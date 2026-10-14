"""Local hosted scheduling/once-only integration without network or spending."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4
from trial_test_fixtures import TRIAL_ID, NOT_BEFORE, LATEST_DEADLINE, START, END
import hashlib
import io
from pathlib import Path

import httpx
from PIL import Image
import pytest

from core.automation.trial_controller import (HostedTrialController, run_hosted_trial,
    TrialCatalogReadback, TrialControllerBlocked, TrialGenerationPreflight, TrialPreflightFailure,
    EffectTransport)
from core.automation.trial_generation_gate import TrialGenerationGate
from core.automation.trial_owner import (TrialPlan, TrialOwner, TrialError,
    canonical_sha256, POLICY_SHA256)


def scenario(tmp_path, *, skipped=(), fail=None, before_start=False):
    tmp_path.chmod(0o700)
    now = [START - timedelta(seconds=10) if before_start else START + timedelta(seconds=1)]
    payload = {"contract": "ybo-server-trial@1", "trial_id": TRIAL_ID, "not_before": "2026-10-14T15:10:51Z",
        "deadline": "2026-10-14T17:10:51Z", "kst_date": "2026-10-15",
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

    async def readiness():
        return None

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
        preflight=preflight, readiness=readiness, destination_policy_sha256="5" * 64, sleep=sleep,
        monotonic=lambda: now[0].timestamp())
    return SimpleNamespace(controller=controller, owner=owner, now=now, calls=calls, entries=entries,
        preflight_calls=preflight_calls)


def test_server_clock_wait_then_skip_yellow_and_final_receipt_off(tmp_path):
    s = scenario(tmp_path, before_start=True)
    result = asyncio.run(s.controller.run())
    assert result["status"] == "completed" and result["effective_on"] is False
    assert result["generated_clients"] == ["babylon", "origintrail"]
    assert result["skipped_clients"] == ["yellow"]
    assert not any(client == "yellow" for _, client, _ in s.calls)
    assert result["visual_qa_performed"] is False and result["public_send_attempted"] is False
    assert all(when >= START for _, _, when in s.calls)
    assert len([c for c in s.calls if c[0] == "generate"]) == 2
    assert len(s.preflight_calls) == 2
    previous = list(s.calls)
    replay = asyncio.run(s.controller.run())
    assert replay["status"] == "not_admitted" and s.calls == previous


def test_readiness_anchored_bounds_preserve_scope_and_expire(tmp_path):
    start, end = START + timedelta(days=2), END + timedelta(days=2)
    s = scenario(tmp_path)
    payload = {**s.owner.plan.payload, "not_before": "2026-10-16T15:10:51Z",
               "deadline": "2026-10-16T17:10:51Z", "kst_date": "2026-10-17"}
    plan = TrialPlan.from_mapping(payload, canonical_sha256(payload))
    owner = TrialOwner(s.owner.path, plan, clock=lambda: s.now[0])
    assert plan.not_before == start and plan.deadline == end and payload["max_total"] == 3
    assert payload["max_per_client"] == 1 and payload["estimated_daily_limit_micros"] == 10_000_000
    for changes in ({"trial_id": str(uuid4())}, {"not_before": "2026-10-16T15:10:51.000Z"},
                    {"deadline": "2026-10-16T17:10:52Z"}, {"deadline": "2026-10-16T17:10:50Z"},
                    {"kst_date": "2026-10-16"},
                    {"not_before": "2026-10-16T14:00:00Z", "deadline": "2026-10-16T16:00:00Z"}):
        altered = {**payload, **changes}
        with pytest.raises(TrialError):
            TrialPlan.from_mapping(altered, canonical_sha256(altered))
    assert owner.admit(plan.trial_id, start - timedelta(seconds=1)) is None
    assert not owner.path.exists()
    s.now[0] = start
    admitted = owner.admit(plan.trial_id, start)
    s.now[0] = end - timedelta(microseconds=1)
    assert admitted is not None and owner.is_active(admitted, s.now[0])
    s.now[0] = end
    assert not owner.is_active(admitted, end)


def test_same_trial_id_changed_plan_cannot_reopen_existing_owner(tmp_path):
    s = scenario(tmp_path)
    assert s.owner.admit(TRIAL_ID, s.now[0]) is not None
    before = s.owner.path.read_bytes()
    changed = {**s.owner.plan.payload, "not_before": "2026-10-16T15:10:51Z",
               "deadline": "2026-10-16T17:10:51Z", "kst_date": "2026-10-17"}
    plan = TrialPlan.from_mapping(changed, canonical_sha256(changed))
    owner = TrialOwner(s.owner.path, plan)
    with pytest.raises(TrialError, match="trial_owner_plan_mismatch"):
        owner.admit(TRIAL_ID, plan.not_before)
    assert s.owner.path.read_bytes() == before


def test_fixed_trial_owner_rejects_yellow_phase_before_filesystem(tmp_path, monkeypatch):
    s = scenario(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("Yellow owner I/O")
    monkeypatch.setattr(s.owner, "_transaction", forbidden)
    with pytest.raises(TrialError, match="trial_phase_invalid"):
        s.owner.reserve_phase(None, "yellow:intake", "yellow", "a" * 64, s.now[0])


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


def mount_readiness(s, response):
    calls = []

    def handle(req):
        assert not s.owner.path.exists()
        assert s.calls == [] and req.method == "GET" and not req.content
        assert str(req.url) == "https://coineasy-newscard.netlify.app/api/ybo-trial-preflight"
        calls.append(req)
        return response(req, len(calls))

    studio = SimpleNamespace(base_url="https://coineasy-newscard.netlify.app",
        automation_token="fixture-existing-automation-token", transport=httpx.MockTransport(handle))
    s.controller.readiness = TrialGenerationPreflight(studio, s.controller.gate,
        s.controller.readiness_guard).verify
    return calls


def ready_response(s):
    p = s.owner.plan
    return {"schema_version": "ybo-trial-preflight@1", "ready": True,
        "netlify_release_sha": p.payload["release_sha"], "trial_id": p.trial_id,
        "plan_sha256": p.sha256, "generation_gateway_enabled": True,
        "broad_public_flags_off": True, "upstream_api_auth_verified": True,
        **{key: p.payload[key] for key in ("not_before", "deadline", "kst_date")},
        "observed_at": s.now[0].isoformat()}


def test_startup_route_readiness_precedes_any_owner_io_then_admits_once(tmp_path):
    s = scenario(tmp_path)
    calls = mount_readiness(s, lambda req, n: httpx.Response(502) if n < 3
                           else httpx.Response(200, json=ready_response(s)))
    result = asyncio.run(s.controller.run())
    assert result["status"] == "completed" and len(calls) == 3
    assert len(s.preflight_calls) == 2  # once per non-excluded client, after readiness
    assert len([c for c in s.calls if c[0] == "generate"]) == 2
    assert s.owner.inspect(s.now[0])["phases"] == 8


@pytest.mark.parametrize("failure", ["auth", "config", "malformed", "foreign", "future", "window_drift"])
def test_invalid_readiness_never_admits_or_retries(tmp_path, failure):
    s = scenario(tmp_path)

    def respond(req, n):
        if failure == "auth": return httpx.Response(401, json={"error": "private upstream text"})
        if failure == "config": return httpx.Response(503, json={"error": "trial_preflight_blocked"})
        if failure == "malformed": return httpx.Response(200, content=b"private upstream text",
            headers={"content-type": "application/json"})
        body = ready_response(s)
        if failure == "foreign": body["trial_id"] = str(uuid4())
        if failure == "future": body["observed_at"] = (s.now[0] + timedelta(seconds=1)).isoformat()
        if failure == "window_drift": body["deadline"] = "2026-10-14T17:10:52Z"
        return httpx.Response(200, json=body)

    calls = mount_readiness(s, respond)
    result = asyncio.run(s.controller.run())
    assert result["status"] == "readiness_failed" and result["effective_on"] is False
    assert not s.owner.path.exists() and not s.controller.store.path.exists()
    assert len(calls) == 1 and s.calls == [] and s.preflight_calls == []
    assert result["diagnostic"]["stage"] == "readiness"
    assert "private upstream text" not in json.dumps(result)


@pytest.mark.parametrize("failure", ["timeout", "unavailable", "gateway"])
def test_transient_readiness_is_bounded_six_gets_and_zero_owner_io(tmp_path, failure):
    s = scenario(tmp_path)

    def respond(req, n):
        if failure == "timeout": raise httpx.ReadTimeout("never emit secret", request=req)
        return httpx.Response(504) if failure == "gateway" else httpx.Response(503,
            json={"error": "trial_preflight_unavailable"})

    calls = mount_readiness(s, respond)
    result = asyncio.run(s.controller.run())
    assert result["status"] == "readiness_failed" and len(calls) == 6
    assert result["diagnostic"]["attempts"] == 6
    assert result["diagnostic"]["elapsed_ms"] == 10000
    assert not s.owner.path.exists() and not s.calls
    assert "never emit secret" not in json.dumps(result)


def test_readiness_total_deadline_and_expiry_never_create_owner(tmp_path):
    s = scenario(tmp_path)
    calls = []

    async def slow():
        calls.append(True)
        assert not s.owner.path.exists()
        s.now[0] += timedelta(seconds=30)
        raise TrialPreflightFailure("transport", retryable=True)

    s.controller.readiness = slow
    result = asyncio.run(s.controller.run())
    assert result["status"] == "readiness_failed" and len(calls) == 2
    assert result["diagnostic"]["elapsed_ms"] == 60000
    assert not s.owner.path.exists()

    s.now[0] = s.owner.plan.deadline
    calls.clear()
    result = asyncio.run(s.controller.run())
    assert result["status"] == "readiness_failed" and calls == []
    assert result["diagnostic"]["code"] == "window_closed"
    assert not s.owner.path.exists()


def test_readiness_success_after_expiry_is_rejected_without_admission(tmp_path):
    s = scenario(tmp_path)

    async def expired():
        s.now[0] = s.owner.plan.deadline

    s.controller.readiness = expired
    assert asyncio.run(s.controller.run())["status"] == "readiness_failed"
    assert not s.owner.path.exists() and not s.calls


def test_readiness_cancellation_never_creates_owner(tmp_path):
    s = scenario(tmp_path)

    async def cancelled():
        raise asyncio.CancelledError()

    s.controller.readiness = cancelled
    with pytest.raises(asyncio.CancelledError): asyncio.run(s.controller.run())
    assert not s.owner.path.exists() and not s.calls


def test_failed_existing_owner_cannot_replay_and_bytes_preserved(tmp_path):
    s = scenario(tmp_path, fail="preflight")
    assert asyncio.run(s.controller.run())["status"] == "unknown"
    before = s.owner.path.read_bytes()
    assert asyncio.run(s.controller.run())["status"] == "not_admitted"
    assert s.owner.path.read_bytes() == before and not s.calls
    assert len(s.preflight_calls) == 1


def test_shared_readiness_transport_does_not_close_backend_early():
    closes, requests = [], []

    class Backend(httpx.MockTransport):
        async def aclose(self): closes.append(True)

    backend = Backend(lambda req: (requests.append(req) or httpx.Response(200)))
    transport = EffectTransport(backend, lambda: None)

    async def verify():
        for _ in range(2):
            async with httpx.AsyncClient(transport=transport) as client:
                assert (await client.get("https://fixture.invalid/preflight")).status_code == 200
            assert closes == []
        await backend.aclose()

    asyncio.run(verify())
    assert len(requests) == 2 and closes == [True]


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
