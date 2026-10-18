"""Disposable exact-job fixtures: no provider, production or Telegram calls."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import asyncio
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
import httpx
from PIL import Image

from core.automation import exact_copy_recovery as recovery
from core.automation.higgsfield_banner import BannerReference, QualityBannerError
from core.automation.higgsfield_token_forecast import FORECAST_POLICY_SHA256
from core.automation.news_generation_journal import NewsGenerationJournal, request_digest
from core.automation.quality_news_card import QualityNewsJournal, provision_quality_news_store, _request
from core.automation.trial_generation_gate import TrialGenerationGate
from core.content_ops.image_budget import (TOKEN_RATE_FORECAST_POLICY, provision_image_budget,
    reserve_image_budget, bind_image_budget_request)
from scripts import prepare_exact_copy_recovery as operator

NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
RELEASE = "a" * 40
ACCOUNT = "b" * 64
STORE = "33333333-3333-4333-8333-333333333333"


def png(size):
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), "orange").save(buffer, format="PNG")
    return buffer.getvalue()


REFERENCE = png(64)
REFERENCE_SHA = hashlib.sha256(REFERENCE).hexdigest()


@pytest.fixture
def exact_runtime(tmp_path):
    mount = tmp_path / "volume"
    mount.mkdir(mode=0o700)
    root = mount / "journal"
    root.mkdir(mode=0o700)
    output = tmp_path / "output"
    output.mkdir()
    provision_quality_news_store(root, workspace_id=recovery.WORKSPACE_ID, store_id=STORE)
    path = root / "quality-news.sqlite3"
    source = "Official source text for an existing immutable Babylon copy checkpoint."
    payload = {"source_content": source, "source_type": "tweet", "mock_mode": False,
        "template_style": "remix", "source_url": "https://x.com/babylonlabs_io/status/2110380535927427174",
        "source_image_url": "https://pbs.twimg.com/media/synthetic.png"}
    payload["quality_context"] = {**recovery.IDENTITY, "source_published_at": "2026-10-14T14:41:43Z",
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        **{key: payload[key] for key in ("source_content", "source_url", "source_image_url")}}
    logo = BannerReference("official_logo", "https://assets.example.com/babylon.png", REFERENCE_SHA)
    spec = {"headline": "공식 업데이트 안내", "body_lines": ["원문의 사실만 보존합니다"], "theme": "dark", "label": "안내"}
    request = _request(payload["quality_context"], spec, REFERENCE_SHA, logo)
    checkpoint = {"spec": spec, "request": asdict(request)}
    encoded = recovery.canonical(checkpoint)
    old_input = recovery.input_digest(payload, logo, recovery.OLD_RELEASE, recovery.OLD_SPEND_POLICY)
    with sqlite3.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        provision_image_budget(db, workspace_id=recovery.WORKSPACE_ID,
                              provider_account_sha256=ACCOUNT, policy_mode=TOKEN_RATE_FORECAST_POLICY)
        db.execute("INSERT INTO quality_news_copy VALUES (?,?,?,?,?,?,?,?,?)", (
            recovery.REQUEST_ID, recovery.JOB_ID, recovery.WORKSPACE_ID, recovery.CLIENT_ID,
            recovery.SOURCE_ID, old_input, "2026-10-15", encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    news = NewsGenerationJournal(root, output_root=output, initialize=True)
    news.pin_mode(recovery.REQUEST_ID, recovery.CLIENT_ID, payload, "quality")
    binding = {"schema": "quality-volume-binding@1", "plan_sha256": "e" * 64,
               "workspace_id": recovery.WORKSPACE_ID, "store_id": STORE,
               "provider_account_sha256": ACCOUNT}
    marker = root / "provision-receipt.json"
    marker.write_text(json.dumps(binding))
    marker.chmod(0o600)
    owner_path = root / f"{recovery.OLD_TRIAL_ID}.trial-owner.sqlite3"
    with sqlite3.connect(owner_path) as db:
        db.execute("PRAGMA application_id=1497517873")
        db.execute("CREATE TABLE trial_owner (plan_json TEXT,status TEXT,plan_sha TEXT)")
        db.execute("CREATE TABLE trial_clients (client TEXT,identifiers TEXT)")
        db.execute("CREATE TABLE trial_phases (phase_key TEXT,payload_sha TEXT,state TEXT,client TEXT)")
        old_plan = {"trial_id": recovery.OLD_TRIAL_ID, "release_sha": recovery.OLD_RELEASE}
        db.execute("INSERT INTO trial_owner VALUES (?, 'unknown',?)", (json.dumps(old_plan), recovery.digest(old_plan)))
        db.execute("INSERT INTO trial_clients VALUES (?,?)", (recovery.CLIENT_ID, json.dumps({
            key: recovery.IDENTITY[key] for key in ("job_id", "request_id", "source_item_id")})))
        db.execute("INSERT INTO trial_phases VALUES (?,?,?,?)", ("babylon:generate",
            request_digest(recovery.CLIENT_ID, payload), "reserved", recovery.CLIENT_ID))
    owner_path.chmod(0o600)
    proof = {"schema": "exact-babylon-recovery-db-proof@1", **recovery.IDENTITY,
        "observed_at": NOW.isoformat(), "source_published_at": payload["quality_context"]["source_published_at"],
        "source_sha256": payload["quality_context"]["source_sha256"], "input_sha256": "f" * 64,
        "style_pack_hash": "7" * 32, "job_status": "running", "attempts": 1, "content_item_id": None,
        **{key: 0 for key in recovery.ZERO_COUNTS}, **{key: True for key in recovery.TRUE_PROOFS}}
    env = {"RAILWAY_GIT_COMMIT_SHA": RELEASE, "QUALITY_BANNER_RELEASE_SHA": RELEASE,
        "CONTENT_STUDIO_WORKSPACE_ID": recovery.WORKSPACE_ID, "QUALITY_BANNER_STORE_ID": STORE,
        "QUALITY_BANNER_PROVIDER_ACCOUNT_SHA256": ACCOUNT,
        "NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT": str(root), "OUTPUT_ROOT": str(output),
        "QUALITY_BANNER_BUDGET_MODE": TOKEN_RATE_FORECAST_POLICY,
        "QUALITY_BANNER_SPEND_POLICY_SHA256": FORECAST_POLICY_SHA256,
        "QUALITY_BANNER_LOGO_REFERENCES": json.dumps({recovery.CLIENT_ID: {
            "url": logo.url, "sha256": logo.sha256}})}
    return SimpleNamespace(mount=mount, root=root, path=path, output=output,
        payload=payload, logo=logo, request=request, proof=proof, env=env,
        owner_path=owner_path, old_input=old_input, checkpoint=checkpoint)


def call(runtime, action, **kwargs):
    return operator.run(action, expected_sha=RELEASE,
        bundle={"payload": runtime.payload, "database_proof": runtime.proof},
        environ=runtime.env, now=NOW, mount_root=runtime.mount,
        mount_checker=lambda _: True, stamp_reader=lambda: RELEASE, **kwargs)


def prepare(runtime):
    plan = call(runtime, "plan")["plan"]
    return call(runtime, "apply", plan_sha256=plan["plan_sha256"])


def gate(runtime):
    plan = SimpleNamespace(trial_id=recovery.TRIAL_ID, payload={"release_sha": RELEASE},
        eligible_clients=("babylon", "origintrail"))
    owner = SimpleNamespace(path=runtime.root / f"{recovery.TRIAL_ID}.trial-owner.sqlite3",
                            generation_authorized=lambda *args: True)
    return TrialGenerationGate(plan=plan, owner=owner, clock=lambda: NOW)


def authorize(runtime, **kwargs):
    options = dict(payload=runtime.payload, logo=runtime.logo, release_sha=RELEASE,
        spend_policy_sha256=FORECAST_POLICY_SHA256, provider_account_sha256=ACCOUNT,
        trial_authority=gate(runtime), output_root=runtime.output, now=NOW)
    options.update(kwargs)
    with sqlite3.connect(runtime.path) as db:
        return recovery.authorize_checkpoint(db, **options)


def test_append_once_preserves_every_row_file_and_keeps_private_text_private(exact_runtime):
    rt = exact_runtime
    files = {p.name: p.read_bytes() for p in rt.root.iterdir()}
    with sqlite3.connect(rt.path) as db:
        before = recovery.preserved_state(db)
    plan = call(rt, "plan")
    assert files == {p.name: p.read_bytes() for p in rt.root.iterdir()}
    result = call(rt, "apply", plan_sha256=plan["plan"]["plan_sha256"])
    with sqlite3.connect(rt.path) as db:
        assert recovery.preserved_state(db) == before
        assert db.execute(f"SELECT count(*) FROM {recovery.TABLE}").fetchone()[0] == 1
    assert all(rt.root.joinpath(name).read_bytes() == value for name, value in files.items()
               if name != "quality-news.sqlite3")
    assert call(rt, "readback")["receipt_sha256"] == result["receipt_sha256"]
    assert result["database_registration"]["local_proof_sha256"] == result["receipt_sha256"]
    assert "Official source text" not in json.dumps(result)
    assert "공식 업데이트" not in json.dumps(result, ensure_ascii=False)
    assert "https://" not in json.dumps(result)
    assert all(result[key] == 0 for key in operator._ZERO_IO)
    with pytest.raises(ValueError):
        call(rt, "apply", plan_sha256=plan["plan"]["plan_sha256"])


def test_runtime_receipt_exact_successor_and_unchanged_checkpoint_only(exact_runtime):
    rt = exact_runtime
    prepare(rt)
    new_input = authorize(rt)
    assert new_input != rt.old_input
    journal = QualityNewsJournal(rt.path, workspace_id=recovery.WORKSPACE_ID,
        client_id=recovery.CLIENT_ID, source_item_id=recovery.SOURCE_ID,
        daily_limit=3, require_existing=True)
    with pytest.raises(QualityBannerError, match="quality_news_binding_conflict"):
        journal.start_or_read(rt.payload["quality_context"], new_input, admit_new=lambda: pytest.fail("new copy"))
    result = journal.start_or_read(rt.payload["quality_context"], new_input,
        admit_new=lambda: pytest.fail("new copy"), recover_checkpoint=lambda db: recovery.authorize_checkpoint(
            db, payload=rt.payload, logo=rt.logo, release_sha=RELEASE,
            spend_policy_sha256=FORECAST_POLICY_SHA256, provider_account_sha256=ACCOUNT,
            trial_authority=gate(rt), output_root=rt.output, now=NOW))
    assert result == json.loads(recovery.canonical(rt.checkpoint))
    with sqlite3.connect(rt.path) as db:
        assert db.execute("SELECT input_sha256 FROM quality_news_copy").fetchone()[0] == rt.old_input


@pytest.mark.parametrize("change", ["gate", "trial", "release", "policy", "logo", "payload", "root", "path", "expired"])
def test_runtime_fail_closed_for_wrong_capability_or_binding(exact_runtime, change):
    rt = exact_runtime
    prepare(rt)
    options = {}
    if change == "gate": options["trial_authority"] = None
    elif change == "trial":
        bad = gate(rt); bad.plan.trial_id = recovery.OLD_TRIAL_ID; options["trial_authority"] = bad
    elif change == "release": options["release_sha"] = "c" * 40
    elif change == "policy": options["spend_policy_sha256"] = recovery.OLD_SPEND_POLICY
    elif change == "logo": options["logo"] = BannerReference("official_logo", rt.logo.url, "c" * 64)
    elif change == "payload":
        body = deepcopy(rt.payload); body["brand_review_guidance"] = "changed"; options["payload"] = body
    elif change == "root": options["output_root"] = Path("/other-output")
    elif change == "path":
        bad = gate(rt); bad.owner.path = Path("/other-owner"); options["trial_authority"] = bad
    elif change == "expired": options["now"] = NOW + timedelta(hours=3)
    with pytest.raises(ValueError): authorize(rt, **options)


@pytest.mark.parametrize("field", recovery.ZERO_COUNTS + recovery.TRUE_PROOFS)
def test_database_readback_denials_do_not_create_receipt(exact_runtime, field):
    rt = exact_runtime
    rt.proof[field] = 1 if field in recovery.ZERO_COUNTS else False
    with pytest.raises(ValueError): call(rt, "plan")
    with sqlite3.connect(rt.path) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (recovery.TABLE,)).fetchone() is None


@pytest.mark.parametrize("mutation", ["stale", "future", "checkpoint", "old_input", "owner_active", "owner_missing", "review", "mode", "output", "payload", "logo", "forecast_mode", "function_on"])
def test_operator_preservation_prerequisites_fail_closed(exact_runtime, mutation):
    rt = exact_runtime
    if mutation == "stale": rt.proof["observed_at"] = (NOW - timedelta(minutes=6)).isoformat()
    elif mutation == "future": rt.proof["observed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif mutation in {"checkpoint", "old_input"}:
        with sqlite3.connect(rt.path) as db:
            db.execute("UPDATE quality_news_copy SET " + ("checkpoint=NULL" if mutation == "checkpoint" else "input_sha256='bad'"))
    elif mutation == "owner_active":
        with sqlite3.connect(rt.owner_path) as db: db.execute("UPDATE trial_owner SET status='active'")
    elif mutation == "owner_missing": rt.owner_path.unlink()
    elif mutation == "review":
        with sqlite3.connect(rt.owner_path) as db: db.execute("INSERT INTO trial_phases VALUES ('babylon:review','x','reserved','babylon')")
    elif mutation in {"mode", "output"}:
        with sqlite3.connect(rt.root / "news-card-requests.sqlite3") as db:
            db.execute("UPDATE news_generation_modes SET " + ("mode='legacy'" if mutation == "mode" else "output_root='/wrong'"))
    elif mutation == "payload": rt.payload["brand_review_guidance"] = "Changed public copy prompt"
    elif mutation == "logo": rt.env["QUALITY_BANNER_LOGO_REFERENCES"] = json.dumps({"babylon": {"url": rt.logo.url, "sha256": "c" * 64}})
    elif mutation == "forecast_mode": rt.env["QUALITY_BANNER_BUDGET_MODE"] = "authenticated_provider_estimate@1"
    elif mutation == "function_on": rt.env["YBO_TRIAL_API_MODE"] = "true"
    with pytest.raises(ValueError): call(rt, "plan")


def test_operator_plan_detects_unrelated_historical_drift(exact_runtime):
    rt = exact_runtime
    plan = call(rt, "plan")["plan"]
    with sqlite3.connect(rt.path) as db:
        db.execute("CREATE TABLE historical (record TEXT)")
    with pytest.raises(ValueError, match="plan_mismatch"):
        call(rt, "apply", plan_sha256=plan["plan_sha256"])
    with sqlite3.connect(rt.path) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (recovery.TABLE,)).fetchone() is None


def test_runtime_unknown_paid_intent_is_never_reopened(exact_runtime):
    rt = exact_runtime
    prepare(rt)
    with sqlite3.connect(rt.path) as db:
        db.execute("INSERT INTO higgsfield_initial_banners VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            recovery.REQUEST_ID, rt.request.digest(), recovery.WORKSPACE_ID, recovery.CLIENT_ID,
            recovery.SOURCE_ID, "2026-10-15", "submit_started", None, None, None, int(NOW.timestamp())))
    with pytest.raises(ValueError, match="paid_intent_exists"): authorize(rt)
    with sqlite3.connect(rt.path) as db:
        assert db.execute("SELECT state FROM higgsfield_initial_banners").fetchone()[0] == "submit_started"


def reserve(runtime, *, client="babylon", operation=None, source=None):
    from core.automation.higgsfield_token_forecast import forecast_proof
    operation = operation or recovery.REQUEST_ID
    source = source or recovery.SOURCE_ID
    with sqlite3.connect(runtime.path) as db:
        db.execute("BEGIN IMMEDIATE")
        reserve_image_budget(db, operation_id=operation, request_sha256=runtime.request.digest(),
            workspace_id=recovery.WORKSPACE_ID, client_id=client, source_item_id=source,
            provider_account_sha256=ACCOUNT, now=NOW,
            forecast_proof=forecast_proof(runtime.request, provider_account_sha256=ACCOUNT, now=NOW))


def test_operator_blocks_existing_paid_reservation_before_receipt(exact_runtime):
    reserve(exact_runtime)
    with pytest.raises(ValueError, match="paid_intent_exists"):
        call(exact_runtime, "plan")


def test_unrelated_pending_liability_is_preserved_not_required_zero(exact_runtime):
    from uuid import uuid4
    rt = exact_runtime
    reserve(rt, client="yellow", operation=str(uuid4()), source=str(uuid4()))
    with sqlite3.connect(rt.path) as db:
        before = db.execute("SELECT * FROM image_budget_reservations").fetchall()
    prepare(rt)
    assert authorize(rt)
    with sqlite3.connect(rt.path) as db:
        assert db.execute("SELECT * FROM image_budget_reservations").fetchall() == before


def test_known_submitted_request_can_reuse_copy_for_get_only_collect(exact_runtime):
    rt = exact_runtime
    prepare(rt)
    reserve(rt)
    provider_id = "44444444-4444-4444-8444-444444444444"
    with sqlite3.connect(rt.path) as db:
        db.execute("BEGIN IMMEDIATE")
        bind_image_budget_request(db, operation_id=recovery.REQUEST_ID,
            request_sha256=rt.request.digest(), workspace_id=recovery.WORKSPACE_ID,
            client_id=recovery.CLIENT_ID, source_item_id=recovery.SOURCE_ID,
            provider_account_sha256=ACCOUNT, provider_request_id=provider_id)
        db.execute("INSERT INTO higgsfield_initial_banners VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            recovery.REQUEST_ID, rt.request.digest(), recovery.WORKSPACE_ID, recovery.CLIENT_ID,
            recovery.SOURCE_ID, "2026-10-15", "submitted", provider_id, None, None, int(NOW.timestamp())))
    assert authorize(rt)
    with sqlite3.connect(rt.path) as db:
        db.execute("UPDATE image_budget_reservations SET provider_request_id='55555555-5555-4555-8555-555555555555'")
    with pytest.raises(ValueError, match="paid_intent_exists"):
        authorize(rt)


def test_append_rolls_back_if_preservation_check_fails(exact_runtime, monkeypatch):
    rt = exact_runtime
    plan = call(rt, "plan")["plan"]
    original = recovery.preserved_state
    def changed(db):
        state = original(db)
        if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (recovery.TABLE,)).fetchone():
            state["sha256"] = "0" * 64
        return state
    monkeypatch.setattr(recovery, "preserved_state", changed)
    with pytest.raises(ValueError, match="preservation_failed"):
        call(rt, "apply", plan_sha256=plan["plan_sha256"])
    with sqlite3.connect(rt.path) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (recovery.TABLE,)).fetchone() is None


def test_service_reuses_exact_copy_and_submits_one_image_then_collects_without_repost(exact_runtime, monkeypatch):
    from core.automation import quality_news_card
    rt = exact_runtime
    prepare(rt)
    calls = {"copy": 0, "post": 0, "get": 0}
    provider_id = "44444444-4444-4444-8444-444444444444"
    final = png(2880)
    async def forbidden_copy(*args, **kwargs):
        calls["copy"] += 1
        pytest.fail("preserved checkpoint must not call copy provider")
    monkeypatch.setattr(quality_news_card, "_copy", forbidden_copy)
    def transport(request):
        if request.method == "POST":
            calls["post"] += 1
            return httpx.Response(200, json={"request_id": provider_id, "status": "queued",
                "status_url": f"https://api.higgsfield.ai/requests/{provider_id}/status"})
        calls["get"] += 1
        if request.url.host in {"assets.example.com", "pbs.twimg.com"}:
            return httpx.Response(200, content=REFERENCE)
        if request.url.host == "output.example.com":
            return httpx.Response(200, content=final)
        return httpx.Response(200, json={"request_id": provider_id, "status": "completed",
            "images": [{"url": "https://output.example.com/result.png"}]})
    authority = gate(rt)
    service = quality_news_card.QualityNewsService(journal_root=rt.root,
        key_id="synthetic", key_secret="synthetic", logo_references={"babylon": rt.logo},
        reference_hosts=frozenset({"assets.example.com", "pbs.twimg.com"}),
        output_hosts=frozenset({"output.example.com"}), daily_limit=3,
        release_sha=RELEASE, spend_policy_sha256=FORECAST_POLICY_SHA256, store_id=STORE,
        transport=httpx.MockTransport(transport), clock=lambda: NOW, copy_factory=forbidden_copy,
        budget_authority=quality_news_card.TokenRateForecastAuthority(ACCOUNT), trial_authority=authority)
    def run():
        return asyncio.run(service.generate(client_id="babylon", request_id=recovery.REQUEST_ID,
            payload=rt.payload, output_root=rt.output, before_effect=lambda: None))
    result = run()
    first = dict(calls)
    assert first["copy"] == 0 and first["post"] == 1
    assert run() == result and calls == first
    with sqlite3.connect(rt.path) as db:
        assert db.execute("SELECT input_sha256 FROM quality_news_copy").fetchone()[0] == rt.old_input


def test_operator_network_is_denied_and_cli_never_echoes_private_input(exact_runtime, monkeypatch, capsys):
    import io
    import socket
    original = operator._local_evidence
    monkeypatch.setattr(operator, "_local_evidence", lambda *args: socket.socket())
    with pytest.raises(ValueError, match="network_forbidden"): call(exact_runtime, "plan")
    monkeypatch.setattr(operator, "_local_evidence", original)
    monkeypatch.setattr(operator.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"secret":"never-output"}')))
    assert operator.main(["plan", "--release-sha", RELEASE]) == 1
    captured = capsys.readouterr().out
    assert "never-output" not in captured and '"status": "BLOCK"' in captured
