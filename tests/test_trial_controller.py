"""Local hosted scheduling/once-only integration without network or spending."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4
import hashlib
import io
from pathlib import Path

import httpx
from PIL import Image
import pytest

from core.automation.trial_controller import (HostedTrialController, START, run_hosted_trial,
    TrialCatalogReadback, TrialControllerBlocked, TrialGenerationPreflight)
from core.automation.trial_generation_gate import TrialGenerationGate
from core.automation.trial_owner import (TrialPlan, TrialOwner, TrialError,
    canonical_sha256, POLICY_SHA256, NOT_BEFORE, LATEST_DEADLINE)


def scenario(tmp_path, *, skipped=(), fail=None, before_start=False):
    tmp_path.chmod(0o700)
    now = [START - timedelta(seconds=10) if before_start else START + timedelta(seconds=1)]
    payload = {"contract": "ybo-server-trial@1", "trial_id": str(uuid4()), "not_before": "2026-10-14T12:44:51Z",
        "deadline": "2026-10-14T14:44:51Z", "kst_date": "2026-10-14",
        "client_ids": ["yellow", "babylon", "origintrail"], "max_per_client": 1, "max_total": 3,
        "estimated_daily_limit_micros": 10_000_000, "estimate_policy_sha256": POLICY_SHA256, "release_sha": "a" * 40}
    plan = TrialPlan.from_mapping(payload, canonical_sha256(payload))
    owner = TrialOwner(tmp_path / f"{plan.trial_id}.trial-owner.sqlite3", plan, clock=lambda: now[0])
    gate = TrialGenerationGate(plan=plan, owner=owner, clock=lambda: now[0])
    calls, entries, preflight_calls = [], {}, []

    async def preflight():
        preflight_calls.append(now[0])
        if fail == "preflight":
            raise TrialControllerBlocked("trial_generation_preflight_blocked")

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
        preflight=preflight, destination_policy_sha256="5" * 64, sleep=sleep)
    return SimpleNamespace(controller=controller, owner=owner, now=now, calls=calls, entries=entries,
        preflight_calls=preflight_calls)


def test_server_clock_wait_then_exact_three_and_final_receipt_off(tmp_path):
    s = scenario(tmp_path, before_start=True)
    result = asyncio.run(s.controller.run())
    assert result["status"] == "completed" and result["effective_on"] is False
    assert result["generated_clients"] == ["yellow", "babylon", "origintrail"]
    assert result["visual_qa_performed"] is False and result["public_send_attempted"] is False
    assert all(when >= START for _, _, when in s.calls)
    assert len([c for c in s.calls if c[0] == "generate"]) == 3
    assert len(s.preflight_calls) == 3
    previous = list(s.calls)
    replay = asyncio.run(s.controller.run())
    assert replay["status"] == "not_admitted" and s.calls == previous


def test_fixed_rewindow_agrees_across_python_sql_and_netlify_and_preserves_gates(tmp_path):
    from core.automation.trial_producer_adapter import START as producer_start, END as producer_end
    from core.content_ops.trial_courier_adapter import START as courier_start, END as courier_end
    start = datetime(2026, 10, 14, 12, 44, 51, tzinfo=timezone.utc)
    end = datetime(2026, 10, 14, 14, 44, 51, tzinfo=timezone.utc)
    assert START == producer_start == courier_start == start
    assert producer_end == courier_end == end
    assert NOT_BEFORE == "2026-10-14T12:44:51Z"
    assert LATEST_DEADLINE == "2026-10-14T14:44:51Z"
    assert end - start == timedelta(hours=2)
    root = Path(__file__).resolve().parents[1]
    for filename in ("content-ops-trial.mts", "content-ops-trial-generation.mts",
                     "content-ops-trial-resolver.mts", "content-ops-trial-state.mts", "ybo-trial-preflight.mts"):
        source = (root / "netlify/functions/_shared" / filename).read_text()
        assert NOT_BEFORE in source and LATEST_DEADLINE in source
        assert "2026-10-14T00:00:00Z" not in source
        assert "2026-10-14T02:00:00Z" not in source
        assert "2026-10-14T06:22:17Z" not in source
        assert "2026-10-14T08:22:17Z" not in source
    sql = (root / "ops/ybo-server-trial/exact-natural-claim.rollback.sql").read_text()
    assert f"gate_start constant timestamptz := '{NOT_BEFORE}'" in sql
    assert f"gate_end constant timestamptz := '{LATEST_DEADLINE}'" in sql
    s = scenario(tmp_path)
    payload = s.owner.plan.payload
    assert payload["kst_date"] == "2026-10-14" and payload["max_total"] == 3
    assert payload["max_per_client"] == 1 and payload["estimated_daily_limit_micros"] == 10_000_000
    for changes in ({"not_before": "2026-10-14T00:00:00Z"},
                    {"not_before": "2026-10-14T06:22:17Z"},
                    {"deadline": "2026-10-14T14:44:52Z"}):
        altered = {**payload, **changes}
        with pytest.raises(TrialError):
            TrialPlan.from_mapping(altered, canonical_sha256(altered))
    assert s.owner.admit(s.owner.plan.trial_id, start - timedelta(seconds=1)) is None
    assert not s.owner.path.exists()
    s.now[0] = start
    admitted = s.owner.admit(s.owner.plan.trial_id, start)
    s.now[0] = end - timedelta(microseconds=1)
    assert admitted is not None and s.owner.is_active(admitted, s.now[0])
    s.now[0] = end
    assert not s.owner.is_active(admitted, end)


def test_no_new_sources_are_skipped_and_no_manifest_created(tmp_path):
    s = scenario(tmp_path, skipped=("yellow", "babylon", "origintrail"))
    result = asyncio.run(s.controller.run())
    assert result["status"] == "completed_no_new_source" and not s.controller.store.path.exists()
    assert s.owner.inspect(s.now[0])["effective_on"] is False
    assert all(action == "intake" for action, _, _ in s.calls)


def test_generation_preflight_failure_precedes_all_intake_job_slot_and_provider_effects(tmp_path):
    s = scenario(tmp_path, fail="preflight")
    result = asyncio.run(s.controller.run())
    assert result["status"] == "unknown" and result["effective_on"] is False
    assert s.calls == [] and len(s.preflight_calls) == 1
    assert s.owner.inspect(s.now[0])["phases"] == 0
    assert not s.controller.store.path.exists()
    assert asyncio.run(s.controller.run())["status"] == "not_admitted"
    assert s.calls == [] and len(s.preflight_calls) == 1


def test_published_missing_off_flag_response_stops_real_preflight_before_intake(tmp_path):
    s = scenario(tmp_path)
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/api/ybo-trial-preflight"
        return httpx.Response(503, json={"error": "trial_preflight_blocked"})

    studio = SimpleNamespace(base_url="https://coineasy-newscard.netlify.app",
        automation_token="fixture-existing-automation-token", transport=httpx.MockTransport(handle))
    s.controller.preflight = TrialGenerationPreflight(studio, s.controller.gate, s.controller.guard).verify
    result = asyncio.run(s.controller.run())
    assert result["status"] == "unknown" and result["effective_on"] is False
    assert len(requests) == 1 and s.calls == []
    assert s.owner.inspect(s.now[0])["phases"] == 0
    assert not s.controller.store.path.exists()


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
