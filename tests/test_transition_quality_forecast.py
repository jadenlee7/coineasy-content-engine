"""Synthetic, offline operator tests; no production mount or credentials."""
from datetime import datetime, timedelta, timezone
import json
import socket
import sqlite3
from uuid import uuid4

import pytest

from core.automation.higgsfield_token_forecast import FORECAST_POLICY_SHA256
from core.automation.quality_news_card import provision_quality_news_store
from core.content_ops.image_budget import (
    ESTIMATE_POLICY, TOKEN_RATE_FORECAST_POLICY, ProviderEstimateProof,
    reserve_image_budget,
)
from scripts import transition_quality_forecast as operator


NOW = datetime(2026, 10, 15, 11, tzinfo=timezone.utc)
RELEASE = "a" * 40
WORKSPACE = "11111111-1111-4111-8111-111111111111"
STORE = "22222222-2222-4222-8222-222222222222"
ACCOUNT = "b" * 64


@pytest.fixture
def runtime(tmp_path):
    mount = tmp_path / "volume"
    mount.mkdir(mode=0o700)
    root = mount / "journal"
    root.mkdir(mode=0o700)
    provision_quality_news_store(root, workspace_id=WORKSPACE, store_id=STORE,
                                 provider_account_sha256=ACCOUNT)
    db_path = root / "quality-news.sqlite3"
    db_path.chmod(0o600)
    operation, source = str(uuid4()), str(uuid4())
    with sqlite3.connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        reserve_image_budget(db, operation_id=operation, request_sha256="c" * 64,
            workspace_id=WORKSPACE, client_id="yellow", source_item_id=source,
            provider_account_sha256=ACCOUNT, now=NOW,
            estimate_proof=ProviderEstimateProof("c" * 64, ACCOUNT, "1.2", NOW,
                                                 NOW + timedelta(minutes=5), "d" * 64))
        db.execute("INSERT INTO quality_news_copy VALUES (?,?,?,?,?,?,?,?,?)",
            (operation, str(uuid4()), WORKSPACE, "yellow", source, "e" * 64,
             "2026-10-15", '{"copy":"private historical copy"}', "f" * 64))
        db.execute("INSERT INTO higgsfield_initial_banners VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (operation, "c" * 64, WORKSPACE, "yellow", source, "2026-10-15",
             "submit_started", None, None, None, int(NOW.timestamp())))
    binding = {"schema": "quality-volume-binding@1", "plan_sha256": "f" * 64,
               "workspace_id": WORKSPACE, "store_id": STORE,
               "provider_account_sha256": ACCOUNT}
    files = {"provision-receipt.json": json.dumps(binding).encode(),
             "news-card-requests.sqlite3": b"synthetic unchanged legacy journal",
             str(uuid4()) + ".trial-owner.sqlite3": b"unknown owner preserved"}
    for name, data in files.items():
        (root / name).write_bytes(data)
        (root / name).chmod(0o600)
    env = {"RAILWAY_GIT_COMMIT_SHA": RELEASE, "QUALITY_BANNER_RELEASE_SHA": RELEASE,
        "CONTENT_STUDIO_WORKSPACE_ID": WORKSPACE, "QUALITY_BANNER_STORE_ID": STORE,
        "QUALITY_BANNER_PROVIDER_ACCOUNT_SHA256": ACCOUNT,
        "NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT": str(root),
        "QUALITY_BANNER_BUDGET_MODE": TOKEN_RATE_FORECAST_POLICY,
        "QUALITY_BANNER_SPEND_POLICY_SHA256": FORECAST_POLICY_SHA256,
        "QUALITY_BANNER_DAILY_REQUEST_LIMIT": "3",
        "QUALITY_BANNER_REFERENCE_HOSTS": "assets.example.com",
        "QUALITY_BANNER_OUTPUT_HOSTS": "output.example.com",
        "HIGGSFIELD_API_KEY_ID": "synthetic-id", "HIGGSFIELD_API_KEY_SECRET": "synthetic-secret",
        "QUALITY_BANNER_LOGO_REFERENCES": json.dumps({client: {
            "url": "https://assets.example.com/logo.png", "sha256": "d" * 64}
            for client in ("yellow", "babylon", "origintrail")})}
    return {"mount": mount, "root": root, "db_path": db_path, "env": env}


def call(runtime, action, **kwargs):
    defaults = dict(expected_sha=RELEASE, environ=runtime["env"], now=NOW,
        mount_root=runtime["mount"], mount_checker=lambda _: True, stamp_reader=lambda: RELEASE)
    return operator.run(action, **{**defaults, **kwargs})


def policy(runtime):
    with sqlite3.connect(runtime["db_path"]) as db:
        return db.execute("SELECT policy_mode FROM image_budget_policy").fetchone()[0]


def historical(runtime):
    with sqlite3.connect(runtime["db_path"]) as db:
        return {name: db.execute("SELECT * FROM " + name).fetchall() for name in (
            "image_budget_reservations", "quality_news_copy", "higgsfield_initial_banners")}


def test_plan_reads_only_and_never_exposes_content_or_credentials(runtime):
    original = {path.name: path.read_bytes() for path in runtime["root"].iterdir()}
    result = call(runtime, "plan")
    assert result["status"] == "plan_ok"
    assert result["transition"]["source_policy"] == ESTIMATE_POLICY
    assert result["transition"]["destination_policy"] == TOKEN_RATE_FORECAST_POLICY
    assert result["budget"]["reserved_usd_micros"] == 1_200_000
    assert result["budget"]["remaining_usd_micros"] == 8_800_000
    assert result["plan_sha256"] == call(runtime, "plan")["plan_sha256"]
    assert original == {path.name: path.read_bytes() for path in runtime["root"].iterdir()}
    assert all(result[key] == 0 for key in operator._ZERO_IO)
    encoded = json.dumps(result)
    assert "private historical copy" not in encoded and "synthetic-secret" not in encoded
    assert "unknown owner preserved" not in encoded


def test_apply_once_preserves_history_liability_siblings_and_factory_off(runtime):
    before = historical(runtime)
    env = dict(runtime["env"])
    plan = call(runtime, "plan")
    result = call(runtime, "apply", plan_sha256=plan["plan_sha256"])
    assert result["status"] == "applied" and policy(runtime) == TOKEN_RATE_FORECAST_POLICY
    assert result["receipt"]["plan_sha256"] == plan["transition"]["plan_sha256"]
    assert result["preserved_files"] == plan["preserved_files"]
    assert result["budget"]["reserved_usd_micros"] == 1_200_000
    assert historical(runtime) == before
    readback = call(runtime, "readback")
    assert readback["status"] == "readback_ok" and len(readback["transition_audit"]) == 1
    assert readback["factory_validation"] == {
        "authority": "local_token_rate_forecast", "policy_sha256": FORECAST_POLICY_SHA256,
        "runtime_environment_unchanged": True, "generation_invoked": False, "network_denied": True}
    assert runtime["env"] == env
    with pytest.raises(ValueError):
        call(runtime, "apply", plan_sha256=plan["plan_sha256"])
    assert historical(runtime) == before


@pytest.mark.parametrize("flag", operator.OFF_FLAGS)
@pytest.mark.parametrize("value", ["true", "False", "", "1"])
def test_flags_are_literal_or_default_false(runtime, flag, value):
    runtime["env"][flag] = value
    with pytest.raises(operator.ForecastOperatorError, match="must_be_off"):
        call(runtime, "plan")
    assert policy(runtime) == ESTIMATE_POLICY


@pytest.mark.parametrize("change", [
    {"expected_sha": "a" * 39}, {"expected_sha": "A" * 40},
    {"stamp_reader": lambda: "b" * 40}, {"mount_checker": lambda _: False},
])
def test_runtime_fences_fail_closed(runtime, change):
    with pytest.raises(ValueError):
        call(runtime, "plan", **change)
    assert policy(runtime) == ESTIMATE_POLICY


@pytest.mark.parametrize("key,value", [
    ("RAILWAY_GIT_COMMIT_SHA", "b" * 40), ("CONTENT_STUDIO_WORKSPACE_ID", str(uuid4())),
    ("QUALITY_BANNER_STORE_ID", str(uuid4())), ("QUALITY_BANNER_PROVIDER_ACCOUNT_SHA256", "c" * 64),
    ("AUTOMATION_WORKSPACE_ID", str(uuid4())), ("NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT", "/tmp/new-store"),
])
def test_environment_identity_drift_rejected(runtime, key, value):
    runtime["env"][key] = value
    with pytest.raises(ValueError):
        call(runtime, "plan")
    assert policy(runtime) == ESTIMATE_POLICY


@pytest.mark.parametrize("path", ["quality-news.sqlite3", "news-card-requests.sqlite3", "provision-receipt.json"])
def test_private_file_permissions_required(runtime, path):
    (runtime["root"] / path).chmod(0o644)
    with pytest.raises(ValueError):
        call(runtime, "plan")


def test_missing_store_never_initialized(runtime):
    runtime["db_path"].unlink()
    with pytest.raises(ValueError):
        call(runtime, "apply", plan_sha256="c" * 64)
    assert not runtime["db_path"].exists()


def test_symlink_or_hardlink_legacy_file_rejected(runtime):
    file = runtime["root"] / "news-card-requests.sqlite3"
    file.unlink()
    file.symlink_to(runtime["db_path"])
    with pytest.raises(ValueError):
        call(runtime, "plan")


@pytest.mark.parametrize("target", ["db", "owner", "new_file"])
def test_apply_hash_binds_history_and_all_existing_siblings(runtime, target):
    plan = call(runtime, "plan")
    if target == "db":
        with sqlite3.connect(runtime["db_path"]) as db:
            db.execute("UPDATE quality_news_copy SET checkpoint='edited'")
    else:
        path = (next(runtime["root"].glob("*.trial-owner.sqlite3")) if target == "owner"
                else runtime["root"] / "new-evidence.json")
        path.write_text("edited")
        path.chmod(0o600)
    with pytest.raises(operator.ForecastOperatorError, match="plan_mismatch"):
        call(runtime, "apply", plan_sha256=plan["plan_sha256"])
    assert policy(runtime) == ESTIMATE_POLICY


def test_unexpected_trigger_mutation_rolls_back_every_change(runtime):
    with sqlite3.connect(runtime["db_path"]) as db:
        db.execute("CREATE TRIGGER unapproved_edit AFTER UPDATE ON image_budget_policy "
                   "BEGIN UPDATE quality_news_copy SET checkpoint='corrupt'; END")
    before = historical(runtime)
    plan = call(runtime, "plan")
    with pytest.raises(operator.ForecastOperatorError, match="preservation_failed"):
        call(runtime, "apply", plan_sha256=plan["plan_sha256"])
    assert policy(runtime) == ESTIMATE_POLICY and historical(runtime) == before
    with sqlite3.connect(runtime["db_path"]) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='image_budget_transition_audit'").fetchall()


def test_release_rechecked_before_commit(runtime):
    plan = call(runtime, "plan")
    stamps = iter((RELEASE, "b" * 40))
    with pytest.raises(operator.ForecastOperatorError, match="release_mismatch"):
        call(runtime, "apply", plan_sha256=plan["plan_sha256"], stamp_reader=lambda: next(stamps))
    assert policy(runtime) == ESTIMATE_POLICY


def test_readback_blocks_network_even_from_factory_and_never_modifies_env(runtime):
    plan = call(runtime, "plan")
    call(runtime, "apply", plan_sha256=plan["plan_sha256"])
    before = dict(runtime["env"])
    def unsafe_factory(env):
        assert env["QUALITY_BANNER_ENABLED"] == "true"
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(operator.ForecastOperatorError, match="network_forbidden"):
        call(runtime, "readback", service_factory=unsafe_factory)
    assert runtime["env"] == before
    assert operator._DENY_NETWORK.get() is False


def test_readback_requires_transition_and_correct_selected_policy(runtime):
    with pytest.raises(operator.ForecastOperatorError, match="transition_required"):
        call(runtime, "readback")
    plan = call(runtime, "plan")
    call(runtime, "apply", plan_sha256=plan["plan_sha256"])
    runtime["env"]["QUALITY_BANNER_SPEND_POLICY_SHA256"] = "0" * 64
    with pytest.raises(ValueError):
        call(runtime, "readback")


def test_readback_never_echoes_untrusted_audit_content(runtime):
    plan = call(runtime, "plan")
    call(runtime, "apply", plan_sha256=plan["plan_sha256"])
    with sqlite3.connect(runtime["db_path"]) as db:
        db.execute("UPDATE image_budget_transition_audit SET source_policy='private source content'")
    with pytest.raises(operator.ForecastOperatorError, match="forecast_transition_audit_invalid") as caught:
        call(runtime, "readback")
    assert "private source content" not in str(caught.value)


def test_cli_masks_all_unexpected_errors_and_does_not_echo_args(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("secret raw provider body")
    monkeypatch.setattr(operator, "run", fail)
    assert operator.main(["plan", "--release-sha", RELEASE]) == 1
    value = json.loads(capsys.readouterr().out)
    assert value == {"status": "BLOCK", "code": "forecast_operator_unavailable", **operator._ZERO_IO}


def test_image_contains_operator_source():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert "COPY . ." in (root / "Dockerfile").read_text()
    assert "scripts" not in (root / ".dockerignore").read_text().splitlines()
