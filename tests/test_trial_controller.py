"""Local hosted scheduling/once-only integration without network or spending."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4
import hashlib
import io

import httpx
from PIL import Image
import pytest

from core.automation.trial_controller import (HostedTrialController, START, run_hosted_trial,
    TrialCatalogReadback, TrialControllerBlocked)
from core.automation.trial_generation_gate import TrialGenerationGate
from core.automation.trial_owner import TrialPlan, TrialOwner, canonical_sha256, POLICY_SHA256


def scenario(tmp_path, *, skipped=(), fail=None, before_start=False):
    tmp_path.chmod(0o700)
    now = [START - timedelta(seconds=10) if before_start else START + timedelta(seconds=1)]
    payload = {"contract": "ybo-server-trial@1", "trial_id": str(uuid4()), "not_before": "2026-10-14T00:00:00Z",
        "deadline": "2026-10-14T02:00:00Z", "kst_date": "2026-10-14",
        "client_ids": ["yellow", "babylon", "origintrail"], "max_per_client": 1, "max_total": 3,
        "estimated_daily_limit_micros": 10_000_000, "estimate_policy_sha256": POLICY_SHA256, "release_sha": "a" * 40}
    plan = TrialPlan.from_mapping(payload, canonical_sha256(payload))
    owner = TrialOwner(tmp_path / f"{plan.trial_id}.trial-owner.sqlite3", plan, clock=lambda: now[0])
    gate = TrialGenerationGate(plan=plan, owner=owner, clock=lambda: now[0])
    calls, entries = [], {}

    class Producer:
        async def intake(self, client, *, before_effect):
            before_effect()
            calls.append(("intake", client, now[0]))
            if fail == "intake":
                raise RuntimeError("synthetic unknown")
            if client in skipped:
                return None
            ids = {k: str(uuid4()) for k in ("job_id", "request_id", "source_item_id")}
            return SimpleNamespace(client_id=client, identifiers=lambda: ids)

        async def claim_and_prepare(self, slot, *, worker_id, before_effect):
            before_effect()
            calls.append(("claim", slot.client_id, now[0]))
            return SimpleNamespace(slot=slot, payload_sha256="1" * 64)

        async def generate_and_complete(self, prepared, *, before_effect):
            before_effect()
            calls.append(("generate", prepared.slot.client_id, now[0]))
            if fail == "generate":
                raise RuntimeError("synthetic unknown")
            return SimpleNamespace(content_item_id=prepared.slot.identifiers()["request_id"],
                                   content_version_id=str(uuid4()))

    class Catalog:
        async def verify(self, prepared, result):
            e = {"client_id": prepared.slot.client_id, **prepared.slot.identifiers(),
                "content_item_id": result.content_item_id, "content_version_id": result.content_version_id,
                "source_sha256": "1" * 64, "banner_sha256": "2" * 64,
                "telegram_copy_sha256": "3" * 64, "x_copy_sha256": "4" * 64}
            entries[e["client_id"]] = e
            if fail == "catalog":
                raise RuntimeError("synthetic invalid canonical image")
            return e

    async def sleep(seconds):
        now[0] += timedelta(seconds=seconds)
        if now[0] < START or not controller.store.path.exists():
            return
        if fail == "delivery":
            now[0] = plan.deadline
            return
        manifest = controller.store.resolve(trial_id=plan.trial_id, plan_sha256=plan.sha256)["manifest"]
        # Simulate trusted gateway's confirmed existing four-part terminal.
        for entry in manifest["entries"]:
            assert owner.confirm_external_delivery(manifest["entries"], entry["content_version_id"],
                canonical_sha256({"version": entry["content_version_id"], "confirmed_parts": 4}), now[0])

    controller = HostedTrialController(gate=gate, producer=Producer(), catalog=Catalog(),
        destination_policy_sha256="5" * 64, sleep=sleep)
    return SimpleNamespace(controller=controller, owner=owner, now=now, calls=calls, entries=entries)


def test_server_clock_wait_then_exact_three_and_final_receipt_off(tmp_path):
    s = scenario(tmp_path, before_start=True)
    result = asyncio.run(s.controller.run())
    assert result["status"] == "completed" and result["effective_on"] is False
    assert result["generated_clients"] == ["yellow", "babylon", "origintrail"]
    assert result["visual_qa_performed"] is False and result["public_send_attempted"] is False
    assert all(when >= START for _, _, when in s.calls)
    assert len([c for c in s.calls if c[0] == "generate"]) == 3
    previous = list(s.calls)
    replay = asyncio.run(s.controller.run())
    assert replay["status"] == "not_admitted" and s.calls == previous


def test_no_new_sources_are_skipped_and_no_manifest_created(tmp_path):
    s = scenario(tmp_path, skipped=("yellow", "babylon", "origintrail"))
    result = asyncio.run(s.controller.run())
    assert result["status"] == "completed_no_new_source" and not s.controller.store.path.exists()
    assert s.owner.inspect(s.now[0])["effective_on"] is False
    assert all(action == "intake" for action, _, _ in s.calls)


@pytest.mark.parametrize("failure", ["intake", "generate", "catalog", "delivery"])
def test_unknown_or_deadline_closes_trial_without_retries(tmp_path, failure):
    s = scenario(tmp_path, fail=failure)
    result = asyncio.run(s.controller.run())
    assert result["effective_on"] is False and result["status"] in {"unknown", "expired"}
    calls = list(s.calls)
    asyncio.run(s.controller.run())
    assert s.calls == calls


def test_factory_off_does_not_read_credentials_or_construct_clients():
    class OffEnv:
        def get(self, key, default=None):
            assert key == "YBO_TRIAL_CONTROLLER_ENABLED"
            return "false"
        def __getitem__(self, key):
            raise AssertionError("OFF accessed credentials")
    assert asyncio.run(run_hosted_trial(OffEnv())) == {
        "status": "disabled", "effective_on": False, "public_send_attempted": False}


@pytest.mark.parametrize("mutation", [None, "current_version", "source_link", "source_body",
    "image_hash", "copy_missing", "fact_check", "quality_context", "asset_path", "multiple_assets"])
def test_catalog_reads_exact_current_version_and_private_png_without_writes(mutation):
    workspace, request, version, source, asset, job = [str(uuid4()) for _ in range(6)]
    raw = io.BytesIO()
    Image.new("RGB", (2880, 2880), "yellow").save(raw, format="PNG")
    png = raw.getvalue()
    context = {"fixture": "bound"}
    path = f"{workspace}/yellow/{asset}/news-card.png"
    rows = {
        "content_items": {"id": request, "client_id": "yellow", "content_kind": "daily_news",
            "status": "needs_review", "current_version_id": version},
        "content_versions": {"id": version, "content_item_id": request,
            "content": {"quality_context": context}, "channel_copy": {"telegram": "공지", "x": "짧은 공지"},
            "generation_meta": {"mock_mode": False, "request_id": request,
                "renderer": "higgsfield-sunburst-max", "fact_check": {"policy_version": "double-fact-check@1",
                    "human_review_required": True, "status": "review"}},
            "deliverables": {"primary_asset_id": asset}},
        "assets": {"id": asset, "content_item_id": request, "content_version_id": version,
            "asset_kind": "png", "mime_type": "image/png", "sha256": hashlib.sha256(png).hexdigest(),
            "storage_bucket": "content-studio", "storage_path": path},
        "content_source_links": {"source_item_id": source, "position": 0},
        "source_items": {"id": source, "client_id": "yellow", "body": "Official fixture."},
    }
    if mutation == "current_version": rows["content_items"]["current_version_id"] = str(uuid4())
    if mutation == "source_link": rows["content_source_links"]["source_item_id"] = str(uuid4())
    if mutation == "source_body": rows["source_items"]["body"] += " tampered"
    if mutation == "image_hash": rows["assets"]["sha256"] = "0" * 64
    if mutation == "copy_missing": rows["content_versions"]["channel_copy"]["x"] = ""
    if mutation == "fact_check": rows["content_versions"]["generation_meta"]["fact_check"]["human_review_required"] = False
    if mutation == "quality_context": rows["content_versions"]["content"]["quality_context"] = {}
    if mutation == "asset_path": rows["assets"]["storage_path"] += "other"
    calls, guards = [], []

    def handle(req):
        calls.append(req)
        assert req.method == "GET"
        if "/rest/v1/" in req.url.path:
            table = req.url.path.rsplit("/", 1)[1]
            assert req.url.params["workspace_id"] == f"eq.{workspace}"
            if table == "content_source_links":
                assert req.url.params["content_item_id"] == f"eq.{request}"
                assert "content_version_id" not in req.url.params  # not a schema column
            return httpx.Response(200, json=[rows[table]])
        assert req.url.path == f"/storage/v1/object/authenticated/content-studio/{path}"
        return httpx.Response(200, content=png)

    repo = SimpleNamespace(supabase_url="https://fixture.invalid", service_role_key="fixture-not-a-secret",
        transport=httpx.MockTransport(handle))
    ids = {"job_id": job, "request_id": request, "source_item_id": source}
    slot = SimpleNamespace(client_id="yellow", request_id=request, source_item_id=source,
        source_content="Official fixture.", identifiers=lambda: ids)
    prepared = SimpleNamespace(slot=slot, payload={"quality_context": context})
    result = SimpleNamespace(content_item_id=request, content_version_id=version,
        asset_ids=[asset, str(uuid4())] if mutation == "multiple_assets" else [asset], reused=False)
    catalog = TrialCatalogReadback(repo, workspace, lambda: guards.append(True))
    if mutation:
        with pytest.raises(TrialControllerBlocked):
            asyncio.run(catalog.verify(prepared, result))
    else:
        entry = asyncio.run(catalog.verify(prepared, result))
        assert entry["banner_sha256"] == rows["assets"]["sha256"]
        assert entry["telegram_copy_sha256"] == hashlib.sha256("공지".encode()).hexdigest()
        assert len(calls) == 6 and len(guards) >= len(calls)
