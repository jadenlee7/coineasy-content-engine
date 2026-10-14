"""Hosted one-shot YBO controller; all ordinary producer/courier flags stay OFF.

Mounted explicitly by API startup. The server clock waits for the fixed trial
window; the local computer and Codex do not execute it. No admin/platform token,
callback bot, public publisher, broad queue drain or recurring schedule exists
here. The separate private courier consumes the immutable generated manifest.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import io
import json
import re
from uuid import UUID

import httpx
from PIL import Image

from core.automation.generation_client import StudioGenerationClient
from core.automation.repository import SupabaseAutomationRepository
from core.automation.trial_generation_gate import TrialGenerationGate
from core.automation.trial_manifest import TrialManifestStore
from core.automation.trial_owner import CLIENTS, canonical_sha256
from core.automation.trial_producer_adapter import TrialNaturalProducerAdapter
from core.sources.x_client import XClient

START = datetime(2026, 10, 14, 12, 44, 51, tzinfo=timezone.utc)


class TrialControllerBlocked(ValueError):
    pass


def require(value):
    if not value:
        raise TrialControllerBlocked("trial_controller_blocked")


def sha(value):
    return hashlib.sha256(value.encode("utf8") if isinstance(value, str) else value).hexdigest()


class EffectTransport(httpx.AsyncBaseTransport):
    def __init__(self, backend, guard):
        self.backend, self.guard = backend, guard

    async def handle_async_request(self, request):
        self.guard()
        return await self.backend.handle_async_request(request)

    async def aclose(self):
        pass  # shared backend closes only at the controller's end


class TrialGenerationPreflight:
    """Netlify config/upstream auth proof; no slot, job or provider call."""
    def __init__(self, studio, gate, guard):
        self.studio, self.gate, self.guard = studio, gate, guard

    async def verify(self):
        self.guard()
        studio, plan = self.studio, self.gate.plan
        require(studio.base_url == "https://coineasy-newscard.netlify.app")
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False, transport=studio.transport) as client:
                async with client.stream("GET", studio.base_url + "/api/ybo-trial-preflight", headers={
                    "X-Studio-Automation-Key": studio.automation_token,
                    "X-Studio-Expected-Release-Sha": plan.payload["release_sha"],
                    "X-Ybo-Trial-Id": plan.trial_id, "X-Ybo-Trial-Plan-Sha256": plan.sha256,
                }) as response:
                    require(response.status_code == 200 and not response.history
                        and response.headers.get("content-type", "").split(";", 1)[0] == "application/json")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        require(len(raw) <= 4096)
            body = json.loads(raw)
            require(type(body) is dict and set(body) == {"schema_version", "ready", "netlify_release_sha",
                "trial_id", "plan_sha256", "generation_gateway_enabled", "broad_public_flags_off",
                "upstream_api_auth_verified", "observed_at"}
                and body["schema_version"] == "ybo-trial-preflight@1" and body["ready"] is True
                and body["netlify_release_sha"] == plan.payload["release_sha"]
                and body["trial_id"] == plan.trial_id and body["plan_sha256"] == plan.sha256
                and body["generation_gateway_enabled"] is True and body["broad_public_flags_off"] is True
                and body["upstream_api_auth_verified"] is True
                and type(body["observed_at"]) is str
                and re.search(r"(?:Z|[+-]\d{2}:\d{2})$", body["observed_at"]))
            observed = datetime.fromisoformat(body["observed_at"].replace("Z", "+00:00"))
            require(0 <= (self.guard() - observed).total_seconds() <= 5)
        except Exception:
            # Never log response bodies, upstream error text or credentials.
            raise TrialControllerBlocked("trial_generation_preflight_blocked") from None


class TrialCatalogReadback:
    """Read current immutable catalog and canonical PNG; never publish/write."""
    def __init__(self, repository, workspace_id, guard):
        self.repository, self.workspace, self.guard = repository, workspace_id, guard

    async def _read(self, table, fields, filters):
        self.guard()
        r = self.repository
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, transport=r.transport) as client:
            response = await client.get(f"{r.supabase_url}/rest/v1/{table}",
                headers={"apikey": r.service_role_key, "Authorization": f"Bearer {r.service_role_key}"},
                params={"select": fields, "workspace_id": f"eq.{self.workspace}", "limit": "2", **filters})
        require(response.status_code == 200 and len(response.content) <= 1_000_000)
        data = response.json()
        require(type(data) is list and len(data) == 1 and type(data[0]) is dict)
        return data[0]

    async def verify(self, prepared, result):
        slot = prepared.slot
        require(result.content_item_id == slot.request_id and len(result.asset_ids) == 1 and not result.reused)
        item = await self._read("content_items", "id,client_id,content_kind,status,current_version_id",
            {"id": f"eq.{slot.request_id}"})
        require(item["client_id"] == slot.client_id and item["content_kind"] == "daily_news"
            and item["status"] == "needs_review" and item["current_version_id"] == result.content_version_id)
        version = await self._read("content_versions", "id,content_item_id,content,channel_copy,generation_meta,deliverables",
            {"id": f"eq.{result.content_version_id}", "content_item_id": f"eq.{slot.request_id}"})
        copies, meta = version["channel_copy"], version["generation_meta"]
        require(type(copies) is dict and all(type(copies.get(k)) is str and copies[k].strip() for k in ("telegram", "x")))
        require(type(meta) is dict and meta.get("mock_mode") is False
            and meta.get("request_id") == slot.request_id and meta.get("renderer") == "higgsfield-sunburst-max")
        # The existing gateway independently validates source/copy factual
        # evidence and PNG. This readback does not pretend to be visual approval.
        fact = meta.get("fact_check")
        require(type(fact) is dict and fact.get("policy_version") == "double-fact-check@1"
            and fact.get("human_review_required") is True and fact.get("status") in {"pass", "review"})
        require(version["content"]["quality_context"] == prepared.payload["quality_context"])
        asset_id = version["deliverables"]["primary_asset_id"]
        require(asset_id == result.asset_ids[0])
        asset = await self._read("assets", "id,content_item_id,content_version_id,asset_kind,mime_type,sha256,storage_bucket,storage_path",
            {"id": f"eq.{asset_id}", "content_version_id": f"eq.{result.content_version_id}"})
        require(asset["content_item_id"] == slot.request_id and asset["asset_kind"] == "png"
            and asset["mime_type"] == "image/png" and asset["storage_bucket"] == "content-studio"
            and asset["storage_path"] == f"{self.workspace}/{slot.client_id}/{asset_id}/news-card.png")
        link = await self._read("content_source_links", "source_item_id,position",
            {"content_item_id": f"eq.{slot.request_id}", "position": "eq.0"})
        require(link["source_item_id"] == slot.source_item_id)
        source = await self._read("source_items", "id,client_id,body", {"id": f"eq.{slot.source_item_id}"})
        require(source["client_id"] == slot.client_id and source["body"] == slot.source_content)
        r = self.repository
        self.guard()
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=r.transport) as client:
            async with client.stream("GET", f"{r.supabase_url}/storage/v1/object/authenticated/content-studio/{asset['storage_path']}",
                headers={"apikey": r.service_role_key, "Authorization": f"Bearer {r.service_role_key}"}) as response:
                require(response.status_code == 200)
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    require(len(raw) <= 10_000_000)
        require(sha(raw) == asset["sha256"])
        with Image.open(io.BytesIO(raw)) as image:
            require(image.format == "PNG" and image.size == (2880, 2880))
            image.verify()
        self.guard()
        return {"client_id": slot.client_id, **slot.identifiers(),
            "source_sha256": sha(source["body"]), "content_item_id": result.content_item_id,
            "content_version_id": result.content_version_id, "banner_sha256": asset["sha256"],
            "telegram_copy_sha256": sha(copies["telegram"]), "x_copy_sha256": sha(copies["x"])}


class HostedTrialController:
    def __init__(self, *, gate, producer, catalog, preflight, destination_policy_sha256, sleep=asyncio.sleep):
        self.gate, self.owner, self.producer, self.catalog = gate, gate.owner, producer, catalog
        self.preflight = preflight
        self.sleep, self.admission = sleep, None
        self.store = TrialManifestStore(owner=self.owner, destination_policy_sha256=destination_policy_sha256, clock=gate.clock)

    def guard(self):
        now = self.gate.clock()
        require(START <= now < self.gate.plan.deadline and self.admission is not None
            and self.owner.is_active(self.admission, now))
        return now

    async def run(self):
        result = {"status": "blocked", "generated_clients": [], "skipped_clients": [],
            "effective_on": False, "public_send_attempted": False, "visual_qa_performed": False}
        while self.gate.clock() < START:
            await self.sleep(min(30, max(0.01, (START - self.gate.clock()).total_seconds())))
        self.admission = self.owner.admit(self.gate.plan.trial_id, self.gate.clock())
        if self.admission is None:
            return {**result, "status": "not_admitted", "effective_on": self.owner.inspect(self.gate.clock())["effective_on"]}
        entries = []
        try:
            for client in CLIENTS:
                # Prove the published gateway's exact pins and literal OFF
                # flags before even reserving an intake phase/daily slot/job.
                require(await self.preflight() is None)
                require(self.owner.reserve_phase(self.admission, f"{client}:intake", client,
                    canonical_sha256({"trial_id": self.gate.plan.trial_id, "client_id": client}), self.guard()))
                slot = await self.producer.intake(client, before_effect=self.guard)
                require(self.owner.finish_phase(self.admission, f"{client}:intake",
                    canonical_sha256(None if slot is None else slot.identifiers()), self.guard()))
                if slot is None:
                    result["skipped_clients"].append(client)
                    continue
                prepared = await self.producer.claim_and_prepare(slot,
                    worker_id=f"ybo-trial:{self.gate.plan.trial_id}", before_effect=self.guard)
                require(self.owner.reserve_phase(self.admission, f"{client}:generate", client,
                    prepared.payload_sha256, self.guard(), identifiers=slot.identifiers()))
                # The existing account-bound durable image ledger admits and
                # meters this exact request; no second budget is initialized.
                generated = await self.producer.generate_and_complete(prepared, before_effect=self.guard)
                entry = await self.catalog.verify(prepared, generated)
                artifact = {k: v for k, v in entry.items() if k != "job_id"}
                artifact["status"] = "needs_review"
                artifact_sha = canonical_sha256(artifact)
                require(self.owner.finish_phase(self.admission, f"{client}:generate", artifact_sha, self.guard()))
                require(self.owner.reserve_phase(self.admission, f"{client}:review", client, artifact_sha, self.guard()))
                require(self.owner.finish_phase(self.admission, f"{client}:review", canonical_sha256({
                    "artifact_sha256": artifact_sha, "structural_checks_passed": True,
                    "source_binding_passed": True, "visual_assessment": "not_performed",
                    "label": "needs_review"}), self.guard()))
                entries.append(entry)
                result["generated_clients"].append(client)
            if not entries:
                require(self.owner.terminate(self.admission, "completed", self.guard()))
                return {**result, "status": "completed_no_new_source"}
            for entry in entries:
                artifact = {k: v for k, v in entry.items() if k != "job_id"}
                artifact["status"] = "needs_review"
                require(self.owner.reserve_phase(self.admission, entry["client_id"] + ":send", entry["client_id"],
                    canonical_sha256(artifact), self.guard()))
            p = self.gate.plan.payload
            self.store.publish({"contract": "ybo-trial-courier-manifest@1", "trial_id": self.gate.plan.trial_id,
                "plan_sha256": self.gate.plan.sha256, **{k: p[k] for k in ("release_sha", "not_before", "deadline")},
                "destination_policy_sha256": self.store.policy, "entries": entries}, self.admission)
            while True:
                state = self.owner.inspect(self.gate.clock())
                if not state["effective_on"]:
                    return {**result, "status": state["status"], "effective_on": False}
                await self.sleep(2)
        except asyncio.CancelledError:
            self.owner.terminate(self.admission, "aborted", self.gate.clock())
            raise
        except Exception:
            self.owner.terminate(self.admission, "unknown", self.gate.clock())
            state = self.owner.inspect(self.gate.clock())
            return {**result, "status": state["status"], "effective_on": state["effective_on"]}


async def run_hosted_trial(env, *, clock=None, sleep=asyncio.sleep):
    """Default OFF means zero filesystem/network/provider/DB/Telegram work."""
    if env.get("YBO_TRIAL_CONTROLLER_ENABLED", "false") == "false":
        return {"status": "disabled", "effective_on": False, "public_send_attempted": False}
    backend = None
    try:
        require(env.get("YBO_TRIAL_CONTROLLER_ENABLED") == "true")
        require(all(env.get(k, "false") == "false" for k in (
            "QUALITY_BANNER_ENABLED", "NEWS_CARD_IDEMPOTENCY_ENABLED", "TELEGRAM_PUBLICATION_ENABLED",
            "CONTENT_OPS_REVIEW_ENABLED", "CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED")))
        gate = TrialGenerationGate.from_env(env, clock=clock)
        workspace = env["AUTOMATION_WORKSPACE_ID"]
        require(str(UUID(workspace)) == workspace and UUID(workspace).int)
        # Construction only; no secret/provider client is contacted before due.
        holder = {}
        guard = lambda: holder["controller"].guard()
        backend = httpx.AsyncHTTPTransport(retries=0)
        transport = EffectTransport(backend, guard)
        repository = SupabaseAutomationRepository(supabase_url=env["SUPABASE_URL"],
            service_role_key=env["SUPABASE_SERVICE_ROLE_KEY"], transport=transport)
        studio = StudioGenerationClient(base_url="https://coineasy-newscard.netlify.app",
            automation_token=env["STUDIO_AUTOMATION_TOKEN"], transport=transport)
        producer = TrialNaturalProducerAdapter(repository=repository,
            x_client=XClient(env["X_BEARER_TOKEN"], before_effect=guard), studio=studio,
            owner=gate.owner, workspace_id=workspace, trial_id=gate.plan.trial_id, clock=gate.clock)
        controller = HostedTrialController(gate=gate, producer=producer,
            catalog=TrialCatalogReadback(repository, workspace, guard),
            preflight=TrialGenerationPreflight(studio, gate, guard).verify,
            destination_policy_sha256=env["YBO_TRIAL_DESTINATION_POLICY_SHA256"], sleep=sleep)
        holder["controller"] = controller
        return await controller.run()
    except Exception:
        return {"status": "configuration_or_owner_unavailable", "effective_on": None,
            "public_send_attempted": False}
    finally:
        if backend is not None:
            await backend.aclose()
