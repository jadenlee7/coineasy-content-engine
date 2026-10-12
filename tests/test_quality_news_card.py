"""Mocked natural-quality boundary; no live source/provider/Telegram calls."""
import asyncio
import hashlib
import io
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from core.automation.higgsfield_banner import BannerReference, QualityBannerError, MODEL
from core.automation.quality_news_card import (QualityNewsService, service_from_env, provision_quality_news_store,
    AuthenticatedEstimateAuthority, ESTIMATE_SPEND_POLICY_SHA256)
from core.content_ops.image_budget import ESTIMATE_POLICY, budget_snapshot
from core.content_ops.image_budget import EnforcedCeilingProof, provision_image_budget

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
WORKSPACE = "296bfec3-7975-4f64-8acb-8f9333e94555"
REQUEST = "173379ea-36aa-4466-87c9-e716b59f59e3"
JOB = "283379ea-36aa-4466-87c9-e716b59f59e3"
SOURCE = "443379ea-36aa-4466-87c9-e716b59f59e3"
PROVIDER = "663b7c4f-e8af-4700-bbe1-7f3d1ff36caa"
STORE = "773b7c4f-e8af-4700-bbe1-7f3d1ff36caa"
ACCOUNT = "e" * 64
CAPABILITY = "f" * 64


class SyntheticBudgetAuthority:
    """Disposable fake billing contract, never a claim about Sunburst."""
    account_sha256 = ACCOUNT

    def proof_for(self, operation_id, request_sha256):
        return EnforcedCeilingProof(request_sha256=request_sha256,
            provider_account_sha256=ACCOUNT, capability_evidence_sha256=CAPABILITY,
            ceiling_usd_micros=1_000_000, evidence_sha256="d" * 64,
            expires_at=NOW + timedelta(hours=2))


def png(size):
    stream = io.BytesIO()
    Image.new("RGB", (size, size), "yellow").save(stream, format="PNG")
    return stream.getvalue()


REFERENCE = png(64)
FINAL = png(2880)


def payload(client="yellow"):
    handle = {"yellow": "Yellow", "babylon": "babylonlabs_io", "origintrail": "origin_trail"}[client]
    text = "Official project update for Korean readers."
    value = {"source_content": text, "source_url": f"https://x.com/{handle}/status/1234",
             "source_image_url": "https://pbs.twimg.com/media/original.png",
             "source_type": "tweet", "mock_mode": False, "template_style": "remix"}
    value["quality_context"] = {"job_id": JOB, "request_id": REQUEST, "workspace_id": WORKSPACE,
        "client_id": client, "source_item_id": SOURCE, "source_published_at": "2026-10-12T09:00:00Z",
        "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
        **{key: value[key] for key in ("source_content", "source_url", "source_image_url")}}
    return value


def setup(tmp_path, *, fail_submit=False, pending=False, fail_copy=False, client="yellow", estimated=False):
    root, output = tmp_path / "private", tmp_path / "output"
    root.mkdir(mode=0o700)
    output.mkdir()
    provision_quality_news_store(root, workspace_id=WORKSPACE, store_id=STORE,
        provider_account_sha256=ACCOUNT if estimated else None)
    if not estimated:
        with sqlite3.connect(root / "quality-news.sqlite3") as db:
            db.execute("BEGIN IMMEDIATE")
            provision_image_budget(db, workspace_id=WORKSPACE,
                provider_account_sha256=ACCOUNT, capability_evidence_sha256=CAPABILITY)
    state = {"posts": 0, "copies": 0, "gets": 0, "pending": pending, "offline": False,
             "estimates": 0, "estimate_usd": "0.728219", "estimate_body": None}
    def http(request):
        if state["offline"]:
            raise AssertionError("No HTTP allowed for stored result replay")
        if request.url.host in {"pbs.twimg.com", "assets.example.com"}:
            assert request.method == "GET" and "authorization" not in request.headers
            state["gets"] += 1
            return httpx.Response(200, content=REFERENCE)
        if request.method == "POST":
            import json
            if request.url.path == "/estimate/" + MODEL:
                assert request.headers["authorization"] == "Key test-key-id:test-key-secret"
                assert "idempotency-key" not in request.headers
                state["estimates"] += 1
                state["estimate_body"] = json.loads(request.content)
                return httpx.Response(200, json={"usd": state["estimate_usd"], "credits": "1.2"})
            if estimated:
                assert json.loads(request.content) == state["estimate_body"]
                assert request.url.path == "/" + MODEL
            state["posts"] += 1
            if fail_submit:
                raise httpx.ReadTimeout("ambiguous provider result")
            return httpx.Response(200, json={"request_id": PROVIDER, "status": "queued",
                "status_url": f"https://api.higgsfield.ai/requests/{PROVIDER}/status"})
        state["gets"] += 1
        if request.url.host == "output.example.com":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=FINAL)
        return httpx.Response(200, json={"request_id": PROVIDER,
            "status": "in_progress" if state["pending"] else "completed",
            "images": [{"url": "https://output.example.com/final.png"}]})
    async def copy(_payload, _client):
        state["copies"] += 1
        if fail_copy:
            raise RuntimeError("unknown LLM response")
        return {"headline": "공식 소식 안내", "body_lines": ["자세한 소식을 확인하세요"],
                "label": "업데이트", "theme": "dark", "approved": True,
                "figma_template": "untrusted", "render_strategy": "fake"}
    kwargs = dict(journal_root=root, key_id="test-key-id", key_secret="test-key-secret",
        logo_references={client: BannerReference("official_logo", "https://assets.example.com/logo.png",
                                                hashlib.sha256(REFERENCE).hexdigest())},
        reference_hosts=frozenset({"pbs.twimg.com", "assets.example.com"}),
        output_hosts=frozenset({"output.example.com"}), daily_limit=3,
        release_sha="a" * 40, spend_policy_sha256="b" * 64, store_id=STORE,
        budget_authority=AuthenticatedEstimateAuthority(ACCOUNT) if estimated else SyntheticBudgetAuthority(),
        transport=httpx.MockTransport(http), clock=lambda: NOW, copy_factory=copy)
    return kwargs, output, state


def invoke(kwargs, output, value=None, client="yellow"):
    value = value or payload(client)
    return asyncio.run(QualityNewsService(**kwargs).generate(
        client_id=client, request_id=value["quality_context"]["request_id"],
        payload=value, output_root=output))


@pytest.mark.parametrize("client", ["yellow", "babylon", "origintrail"])
def test_natural_quality_result_and_restart_no_network(tmp_path, client):
    kwargs, output, state = setup(tmp_path, client=client)
    result = invoke(kwargs, output, client=client)
    assert state["posts"] == state["copies"] == 1
    assert result["spec"]["quality_banner"]["width"] == 2880
    assert result["spec"]["quality_banner"]["png_sha256"] == hashlib.sha256(FINAL).hexdigest()
    assert result["figma_template"] is None
    assert "approved" not in result["spec"] and "figma_template" not in result["spec"]
    assert result["spec"]["render_strategy"] == "reference_first_quality"
    Path(result["png_path"]).unlink()
    state["offline"] = True
    assert invoke(kwargs, output, client=client) == result
    assert Path(result["png_path"]).read_bytes() == FINAL
    assert state["posts"] == state["copies"] == 1


def test_pending_restart_reuses_copy_and_same_provider_request(tmp_path):
    kwargs, output, state = setup(tmp_path, pending=True)
    with pytest.raises(QualityBannerError, match="quality_banner_pending"):
        invoke(kwargs, output)
    state["pending"] = False
    assert invoke(kwargs, output)["content_type"] == "news_card"
    assert state["posts"] == state["copies"] == 1


def test_unknown_submit_and_new_uuid_cannot_repeat(tmp_path):
    kwargs, output, state = setup(tmp_path, fail_submit=True)
    with pytest.raises(QualityBannerError, match="submit_unknown"):
        invoke(kwargs, output)
    with pytest.raises(QualityBannerError, match="submit_unknown"):
        invoke(kwargs, output)
    value = payload()
    value["quality_context"]["request_id"] = str(uuid4())
    with pytest.raises(QualityBannerError, match="binding_conflict"):
        invoke(kwargs, output, value)
    assert state["posts"] == state["copies"] == 1
    with sqlite3.connect(kwargs["journal_root"] / "quality-news.sqlite3") as db:
        assert db.execute("SELECT ceiling_usd_micros,actual_usd_micros,provider_request_id "
            "FROM image_budget_reservations WHERE operation_id=?", (REQUEST,)).fetchone() == (
                1_000_000, None, None)


def test_unknown_copy_never_restarts(tmp_path):
    kwargs, output, state = setup(tmp_path, fail_copy=True)
    with pytest.raises(RuntimeError):
        invoke(kwargs, output)
    with pytest.raises(QualityBannerError, match="copy_unknown"):
        invoke(kwargs, output)
    assert state["copies"] == 1 and state["posts"] == 0


@pytest.mark.parametrize("change", ["source", "job", "release", "logo", "copy_guidance"])
def test_changed_immutable_input_cannot_reuse_result(tmp_path, change):
    kwargs, output, state = setup(tmp_path)
    invoke(kwargs, output)
    value = payload()
    if change == "source":
        value["source_content"] += " changed"
        value["quality_context"]["source_content"] = value["source_content"]
        value["quality_context"]["source_sha256"] = hashlib.sha256(value["source_content"].encode()).hexdigest()
    elif change == "job":
        value["quality_context"]["job_id"] = str(uuid4())
    elif change == "release":
        kwargs["release_sha"] = "c" * 40
    elif change == "logo":
        kwargs["logo_references"]["yellow"] = BannerReference("official_logo", "https://assets.example.com/logo2.png", "c" * 64)
    else:
        value["brand_review_guidance"] = {"different": True}
    with pytest.raises(QualityBannerError, match="binding_conflict"):
        invoke(kwargs, output, value)
    assert state["copies"] == state["posts"] == 1


def test_stale_source_cannot_be_admitted_after_pending(tmp_path):
    kwargs, output, state = setup(tmp_path, pending=True)
    with pytest.raises(QualityBannerError, match="pending"):
        invoke(kwargs, output)
    kwargs["clock"] = lambda: NOW + timedelta(days=1)
    with pytest.raises(QualityBannerError, match="source_stale"):
        invoke(kwargs, output)
    assert state["posts"] == state["copies"] == 1


def test_off_builds_no_client_and_misconfiguration_is_closed():
    assert service_from_env({}) is None
    assert service_from_env({"QUALITY_BANNER_ENABLED": "false"}) is None
    with pytest.raises(QualityBannerError):
        service_from_env({"QUALITY_BANNER_ENABLED": "true"})


def test_runtime_budget_cannot_be_enabled_with_a_self_declared_flag_or_hash():
    class NoCredentialReads(dict):
        def __getitem__(self, name):
            raise AssertionError("No credentials/configuration read before budget evidence")
    env = NoCredentialReads(QUALITY_BANNER_ENABLED="true", NEWS_CARD_IDEMPOTENCY_ENABLED="true",
        RAILWAY_GIT_COMMIT_SHA="a" * 40, QUALITY_BANNER_RELEASE_SHA="a" * 40,
        QUALITY_BANNER_SPEND_POLICY_SHA256="b" * 64,
        QUALITY_BANNER_BUDGET_VERIFIED="true", QUALITY_BANNER_DAILY_BUDGET_USD="10")
    with pytest.raises(QualityBannerError, match="^quality_news_budget_evidence_required$"):
        service_from_env(env)


def test_no_billing_authority_blocks_before_copy_reference_and_reservation(tmp_path):
    kwargs, output, state = setup(tmp_path)
    kwargs.pop("budget_authority")
    with pytest.raises(QualityBannerError, match="^quality_news_budget_evidence_required$"):
        invoke(kwargs, output)
    assert state["posts"] == state["copies"] == state["gets"] == 0
    with sqlite3.connect(kwargs["journal_root"] / "quality-news.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM image_budget_reservations").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM quality_news_copy").fetchone()[0] == 0


def test_image_budget_and_one_shot_submit_share_the_same_durable_receipt(tmp_path):
    kwargs, output, state = setup(tmp_path)
    invoke(kwargs, output)
    with sqlite3.connect(kwargs["journal_root"] / "quality-news.sqlite3") as db:
        row = db.execute("SELECT b.operation_id,b.request_sha256,b.provider_request_id,"
            "b.ceiling_usd_micros,b.actual_usd_micros,h.request_sha256,h.request_id "
            "FROM image_budget_reservations b JOIN higgsfield_initial_banners h "
            "ON h.operation_id=b.operation_id").fetchone()
    assert row[0] == REQUEST and row[1] == row[5] and row[2] == row[6] == PROVIDER
    # Image completion is not a final billing receipt. Keep its full liability.
    assert row[3:5] == (1_000_000, None)
    state["offline"] = True
    invoke(kwargs, output)
    assert state["posts"] == state["copies"] == 1


def test_budget_reservation_and_submit_intent_rollback_together(tmp_path):
    kwargs, output, state = setup(tmp_path)
    with sqlite3.connect(kwargs["journal_root"] / "quality-news.sqlite3") as db:
        db.execute("CREATE TRIGGER fixture_abort_submit BEFORE INSERT ON higgsfield_initial_banners "
            "BEGIN SELECT RAISE(ABORT,'fixture_abort'); END")
    with pytest.raises(QualityBannerError, match="^quality_banner_submit_unknown$"):
        invoke(kwargs, output)
    assert state["posts"] == 0
    with sqlite3.connect(kwargs["journal_root"] / "quality-news.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM image_budget_reservations").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM higgsfield_initial_banners").fetchone()[0] == 0


def test_budget_refusal_cannot_consume_submit_row_or_call_provider(tmp_path):
    kwargs, output, state = setup(tmp_path)
    from dataclasses import replace
    class ExcessiveAuthority(SyntheticBudgetAuthority):
        def proof_for(self, operation_id, request_sha256):
            return replace(super().proof_for(operation_id, request_sha256), ceiling_usd_micros=10_000_001)
    kwargs["budget_authority"] = ExcessiveAuthority()
    with pytest.raises(QualityBannerError, match="^quality_news_daily_budget_limit$"):
        invoke(kwargs, output)
    assert state["posts"] == 0
    with sqlite3.connect(kwargs["journal_root"] / "quality-news.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM image_budget_reservations").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM higgsfield_initial_banners").fetchone()[0] == 0


@pytest.mark.parametrize("client", ["yellow", "babylon", "origintrail"])
def test_authenticated_estimate_provisional_completion_and_restart(tmp_path, client):
    kwargs, output, state = setup(tmp_path, estimated=True, client=client)
    result = invoke(kwargs, output, client=client)
    assert state["estimates"] == state["posts"] == state["copies"] == 1
    assert result["spec"]["quality_banner"]["width"] == 2880
    with sqlite3.connect(kwargs["journal_root"] / "quality-news.sqlite3") as db:
        snapshot = budget_snapshot(db, workspace_id=WORKSPACE, provider_account_sha256=ACCOUNT, now=NOW)
        assert snapshot.policy_mode == ESTIMATE_POLICY
        assert snapshot.actual_usd_micros == snapshot.reserved_usd_micros == 0
        assert snapshot.provisional_usd_micros == 728_219
        assert snapshot.remaining_usd_micros == 9_271_781
        later = budget_snapshot(db, workspace_id=WORKSPACE,
            provider_account_sha256=ACCOUNT, now=NOW + timedelta(days=1))
        assert later.remaining_usd_micros == 10_000_000
    state["offline"] = True
    # A new authority has no cached estimate: persisted result needs none.
    kwargs["budget_authority"] = AuthenticatedEstimateAuthority(ACCOUNT)
    assert invoke(kwargs, output, client=client) == result
    assert state["estimates"] == state["posts"] == state["copies"] == 1


def test_estimate_above_daily_remainder_stops_before_paid_image_submit(tmp_path):
    kwargs, output, state = setup(tmp_path, estimated=True)
    state["estimate_usd"] = "10.000001"
    with pytest.raises(QualityBannerError, match="quality_news_daily_budget_limit"):
        invoke(kwargs, output)
    assert state["estimates"] == 1 and state["posts"] == 0
    with sqlite3.connect(kwargs["journal_root"] / "quality-news.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM higgsfield_initial_banners").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM image_budget_reservations").fetchone()[0] == 0


def test_expired_known_image_reconciles_budget_but_never_admits_content(tmp_path):
    kwargs, output, state = setup(tmp_path, estimated=True, pending=True)
    with pytest.raises(QualityBannerError, match="pending"):
        invoke(kwargs, output)
    later = NOW + timedelta(days=1)
    kwargs["clock"] = lambda: later
    kwargs["budget_authority"] = AuthenticatedEstimateAuthority(ACCOUNT)
    state["pending"] = False
    before = state["gets"]
    with pytest.raises(QualityBannerError, match="source_stale"):
        invoke(kwargs, output)
    assert state["gets"] > before
    assert state["posts"] == state["copies"] == state["estimates"] == 1
    assert not list(output.rglob("news_card_quality.png"))
    with sqlite3.connect(kwargs["journal_root"] / "quality-news.sqlite3") as db:
        snap = budget_snapshot(db, workspace_id=WORKSPACE, provider_account_sha256=ACCOUNT, now=later)
        assert snap.reserved_usd_micros == snap.actual_usd_micros == 0
        assert snap.provisional_usd_micros == 728_219
        next_day = budget_snapshot(db, workspace_id=WORKSPACE,
            provider_account_sha256=ACCOUNT, now=later + timedelta(days=1))
        assert next_day.remaining_usd_micros == 10_000_000
    state["offline"] = True
    with pytest.raises(QualityBannerError, match="source_stale"):
        invoke(kwargs, output)


def test_new_stale_source_cannot_create_paid_intent_or_http(tmp_path):
    kwargs, output, state = setup(tmp_path, estimated=True)
    kwargs["clock"] = lambda: NOW + timedelta(days=1)
    state["offline"] = True
    with pytest.raises(QualityBannerError, match="source_stale"):
        invoke(kwargs, output)
    assert state["posts"] == state["copies"] == state["estimates"] == state["gets"] == 0
    with sqlite3.connect(kwargs["journal_root"] / "quality-news.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM quality_news_copy").fetchone()[0] == 0


@pytest.mark.parametrize("usd", [None, 0.73, "nan", "-0.1", "0", "USD0.73"])
def test_malformed_provider_estimate_is_never_paid_authority(tmp_path, usd):
    kwargs, output, state = setup(tmp_path, estimated=True)
    state["estimate_usd"] = usd
    with pytest.raises(QualityBannerError):
        invoke(kwargs, output)
    assert state["estimates"] == 1 and state["posts"] == 0


def test_estimated_unknown_submit_keeps_liability_and_never_reestimates(tmp_path):
    kwargs, output, state = setup(tmp_path, estimated=True, fail_submit=True)
    with pytest.raises(QualityBannerError, match="submit_unknown"):
        invoke(kwargs, output)
    kwargs["budget_authority"] = AuthenticatedEstimateAuthority(ACCOUNT)
    with pytest.raises(QualityBannerError, match="submit_unknown"):
        invoke(kwargs, output)
    assert state["estimates"] == state["posts"] == state["copies"] == 1
    with sqlite3.connect(kwargs["journal_root"] / "quality-news.sqlite3") as db:
        snapshot = budget_snapshot(db, workspace_id=WORKSPACE,
            provider_account_sha256=ACCOUNT, now=NOW + timedelta(days=1))
        assert snapshot.reserved_usd_micros == 728_219
        assert snapshot.actual_usd_micros == snapshot.provisional_usd_micros == 0


def test_old_strict_store_cannot_be_silently_used_as_an_estimated_store(tmp_path):
    kwargs, output, state = setup(tmp_path)
    kwargs["budget_authority"] = AuthenticatedEstimateAuthority(ACCOUNT)
    with pytest.raises(QualityBannerError, match="quality_news_budget_evidence_required"):
        invoke(kwargs, output)
    assert state["estimates"] == state["copies"] == state["gets"] == state["posts"] == 0


def test_environment_mounts_only_explicit_approved_estimate_policy(tmp_path):
    import json
    env = {"QUALITY_BANNER_ENABLED": "true", "NEWS_CARD_IDEMPOTENCY_ENABLED": "true",
        "RAILWAY_GIT_COMMIT_SHA": "a" * 40, "QUALITY_BANNER_RELEASE_SHA": "a" * 40,
        "QUALITY_BANNER_BUDGET_MODE": ESTIMATE_POLICY,
        "QUALITY_BANNER_SPEND_POLICY_SHA256": ESTIMATE_SPEND_POLICY_SHA256,
        "NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT": str(tmp_path), "QUALITY_BANNER_STORE_ID": STORE,
        "HIGGSFIELD_API_KEY_ID": "test-key-id", "HIGGSFIELD_API_KEY_SECRET": "test-key-secret",
        "QUALITY_BANNER_PROVIDER_ACCOUNT_SHA256": ACCOUNT,
        "QUALITY_BANNER_DAILY_REQUEST_LIMIT": "3", "QUALITY_BANNER_REFERENCE_HOSTS": "assets.example.com",
        "QUALITY_BANNER_OUTPUT_HOSTS": "output.example.com",
        "QUALITY_BANNER_LOGO_REFERENCES": json.dumps({client: {"url": "https://assets.example.com/logo.png",
            "sha256": "d" * 64} for client in ("yellow", "babylon", "origintrail")})}
    service = service_from_env(env)
    assert type(service) is QualityNewsService
    assert type(service.budget_authority) is AuthenticatedEstimateAuthority
    # Construction alone neither provisions the store nor performs HTTP.
    assert not list(tmp_path.iterdir())
    for change in ({"QUALITY_BANNER_BUDGET_MODE": "provider_enforced_usd_ceiling@1"},
                   {"QUALITY_BANNER_SPEND_POLICY_SHA256": "b" * 64}):
        with pytest.raises(QualityBannerError, match="quality_news_budget_evidence_required"):
            service_from_env({**env, **change})


def test_context_mismatch_no_io(tmp_path):
    kwargs, output, state = setup(tmp_path)
    value = payload()
    value["source_url"] = "https://x.com/Yellow/status/9999"
    with pytest.raises(QualityBannerError):
        invoke(kwargs, output, value)
    assert state["gets"] == state["copies"] == state["posts"] == 0


@pytest.mark.parametrize("marker", [
    "https://t.me/+SYNTHETIC_PRIVATE_INVITE",
    "sk-synthetic_fixture_not_a_real_token",
    -1001234567890,
])
@pytest.mark.parametrize("field", ["source_content", "style_references", "brand_review_guidance"])
def test_private_prompt_fields_reject_before_reservation(tmp_path, marker, field):
    kwargs, output, state = setup(tmp_path)
    value = payload()
    if field == "source_content":
        value[field] += " " + str(marker)
        value["quality_context"][field] = value[field]
        value["quality_context"]["source_sha256"] = hashlib.sha256(value[field].encode()).hexdigest()
    else:
        # Exercise mixed nested dict/list values, including a numeric chat ID.
        value[field] = [{"reference": {"observations": ["public fact", marker]}}]
    original = deepcopy(value)
    with pytest.raises(QualityBannerError, match="^quality_news_invalid$"):
        invoke(kwargs, output, value)
    assert value == original  # Do not silently rewrite the pinned source/hash.
    assert state["gets"] == state["copies"] == state["posts"] == 0
    with sqlite3.connect(kwargs["journal_root"] / "quality-news.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM quality_news_copy").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM higgsfield_initial_banners").fetchone()[0] == 0
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("private_fields", [
    {"api_key": "synthetic-fixture-only"},
    {"nested": [{"Authorization": "synthetic-fixture-only"}]},
    {"nested": [{"cookie": "synthetic-fixture-only"}]},
    {"notes": "Bearer synthetic_fixture_only_value"},
    {"notes": "12345678:synthetic_fixture_only_bot_token"},
])
def test_structured_prompt_credentials_reject_before_reservation(tmp_path, private_fields):
    kwargs, output, state = setup(tmp_path)
    value = payload()
    value["brand_review_guidance"] = private_fields
    with pytest.raises(QualityBannerError, match="^quality_news_invalid$"):
        invoke(kwargs, output, value)
    assert state["gets"] == state["copies"] == state["posts"] == 0
    with sqlite3.connect(kwargs["journal_root"] / "quality-news.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM quality_news_copy").fetchone()[0] == 0


def test_copy_entrypoint_checks_privacy_before_model_invocation(monkeypatch):
    from core.automation.quality_news_card import _copy
    from core.llm import news_card_pipeline
    def forbidden(**kwargs):
        raise AssertionError("Private prompt reached model generation")
    monkeypatch.setattr(news_card_pipeline, "generate_news_card_spec", forbidden)
    value = payload()
    value["style_references"] = [{"nested": {"notes": "t.me/joinchat/SYNTHETIC_INVITE"}}]
    with pytest.raises(QualityBannerError, match="^quality_news_invalid$"):
        asyncio.run(_copy(value, "yellow"))


def test_public_nested_prompt_input_is_preserved():
    from core.automation.quality_news_card import _validate_prompt_input, validate_context
    value = payload()
    value["style_references"] = [{"caption": "공식 업데이트", "source_url": value["source_url"]}]
    value["brand_review_guidance"] = {"rules": [{"prefer": ["정확한 사실", "간결한 문장"],
        "weight": 1, "enabled": True, "optional": None}]}
    original = deepcopy(value)
    _validate_prompt_input(value)
    assert validate_context("yellow", REQUEST, value) == original["quality_context"]
    assert value == original


@pytest.mark.parametrize("change", ["missing", "other_store", "workspace"])
def test_lost_or_wrong_persistent_volume_cannot_create_new_ledger(tmp_path, change):
    kwargs, output, state = setup(tmp_path)
    if change == "missing":
        (kwargs["journal_root"] / "quality-news.sqlite3").unlink()
    elif change == "other_store":
        kwargs["store_id"] = str(uuid4())
    value = payload()
    if change == "workspace":
        value["quality_context"]["workspace_id"] = str(uuid4())
    with pytest.raises(QualityBannerError, match="store_unavailable"):
        invoke(kwargs, output, value)
    assert state["posts"] == state["gets"] == state["copies"] == 0


def test_quality_copy_uses_one_sdk_post_after_timeout(monkeypatch):
    import anthropic
    from core.automation.quality_news_card import _copy
    actual = anthropic.Anthropic
    calls = []
    def http(request):
        calls.append(request.method)
        raise httpx.ReadTimeout("synthetic timeout", request=request)
    client = httpx.Client(transport=httpx.MockTransport(http))
    def sdk(**kwargs):
        assert kwargs["max_retries"] == 0
        return actual(api_key="fixture-only-key", http_client=client, **kwargs)
    monkeypatch.setattr(anthropic, "Anthropic", sdk)
    try:
        with pytest.raises(anthropic.APITimeoutError):
            asyncio.run(_copy(payload(), "yellow"))
    finally:
        client.close()
    assert calls == ["POST"]


def test_freshness_rechecked_before_paid_copy(tmp_path):
    kwargs, output, state = setup(tmp_path)
    count = 0
    def clock():
        nonlocal count
        count += 1
        return NOW if count <= 2 else NOW + timedelta(days=1)
    kwargs["clock"] = clock
    with pytest.raises(QualityBannerError, match="source_stale"):
        invoke(kwargs, output)
    assert state["copies"] == state["posts"] == 0


def test_native_banner_can_form_existing_private_review_packet(tmp_path):
    # Boundary integration only: synthetic catalog/review owner receipts, NOT
    # a fabricated production succeeded job, DB admission, or Telegram send.
    from core.content_ops.private_review_card_candidate import build_prepared_card
    from core.content_ops.private_review_card_receipt import prepare_private_card
    from core.content_ops.review_buttons import ButtonSigner
    from core.content_ops.review_edit_ingress import EditBindings
    from core.content_ops.worker import ReviewClaim
    kwargs, output, _state = setup(tmp_path)
    result = invoke(kwargs, output)
    version, card_id, review_id = (str(uuid4()) for _ in range(3))
    banner = Path(result["png_path"]).read_bytes()
    claim = ReviewClaim.parse({"outbox_id": str(uuid4()), "claim_token": str(uuid4()),
        "client_id": "yellow", "kst_date": "2026-10-12", "content_item_id": REQUEST,
        "content_version_id": version, "source_item_id": SOURCE, "generate_job_id": JOB,
        "banner_sha256": hashlib.sha256(banner).hexdigest(), "title": result["spec"]["headline"],
        "telegram_copy": "공식 소식 안내\n자세한 소식을 확인하세요", "x_copy": "공식 소식 안내",
        "source_url": payload()["source_url"], "source_published_at": "2026-10-12T09:00:00Z"}, NOW)
    signer = ButtonSigner(b"s" * 32)
    card = build_prepared_card(claim=claim,
        review_receipt={"status": "review_prepared", "review_id": review_id,
            "version_fingerprint": "a" * 64, "epoch": 0, "state": "active",
            "expires_at": "2026-10-12T12:30:00+00:00", "execution_authorized": False},
        workspace_id=WORKSPACE, card_id=card_id, png=banner, signer=signer,
        bindings=EditBindings(b"e" * 32), bot_id=123456, chat_id=-1001234567890,
        now=int(NOW.timestamp()))
    parts = prepare_private_card(card.snapshot, signer, card.room_binding, now=card.now)
    assert len(parts) == 4 and parts[-1]["kind"] == "controls"
    assert card.snapshot.content_version_id == version
    assert card.snapshot.eligibility == "blocked"  # Staff checks still required.
