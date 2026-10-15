"""Offline same-store policy transition tests; no provider or production I/O."""

from datetime import datetime, timedelta, timezone
import sqlite3
from uuid import uuid4

import pytest

from core.content_ops.image_budget import (
    ESTIMATE_POLICY, STRICT_POLICY, TOKEN_RATE_FORECAST_POLICY, FinalChargeProof,
    ProviderEstimateProof, bind_image_budget_request, budget_snapshot,
    mark_image_budget_completed, provision_image_budget, reserve_image_budget,
    settle_image_budget,
)
from core.content_ops.token_forecast_transition import (
    TokenForecastTransitionError, apply_token_forecast_transition,
    plan_token_forecast_transition,
)


NOW = datetime(2026, 10, 15, 14, 59, tzinfo=timezone.utc)
WORKSPACE = "11111111-1111-4111-8111-111111111111"
ACCOUNT = "a" * 64


def _scope(client):
    return dict(operation_id=str(uuid4()), request_sha256="b" * 64,
                workspace_id=WORKSPACE, client_id=client, source_item_id=str(uuid4()),
                provider_account_sha256=ACCOUNT)


def _snapshot(db, now=NOW):
    return budget_snapshot(db, workspace_id=WORKSPACE,
                           provider_account_sha256=ACCOUNT, now=now)


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _historical_rows(db):
    return {table: db.execute(f"SELECT * FROM {table} ORDER BY operation_id").fetchall()
            for table in ("image_budget_reservations", "quality_news_copy",
                          "higgsfield_initial_banners")}


def _store(tmp_path, *, mode=ESTIMATE_POLICY, populated=True):
    db = sqlite3.connect(tmp_path / "same-store.sqlite3")
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE quality_news_copy (
            operation_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, client_id TEXT NOT NULL,
            source_item_id TEXT NOT NULL, input_sha256 TEXT NOT NULL,
            kst_day TEXT NOT NULL, checkpoint TEXT, checkpoint_sha256 TEXT,
            UNIQUE(workspace_id,client_id,source_item_id))""")
        db.execute("""CREATE TABLE higgsfield_initial_banners (
            operation_id TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL,
            workspace_id TEXT NOT NULL, client_id TEXT NOT NULL,
            source_item_id TEXT NOT NULL, kst_day TEXT NOT NULL,
            state TEXT NOT NULL, request_id TEXT UNIQUE, png BLOB,
            png_sha256 TEXT, created_at INTEGER NOT NULL,
            UNIQUE(workspace_id,client_id,source_item_id))""")
        db.execute("CREATE TABLE optional_owner (id INTEGER PRIMARY KEY, private_data BLOB)")
        db.execute("INSERT INTO optional_owner VALUES (1,?)", (b"private owner bytes",))
        provision_image_budget(db, workspace_id=WORKSPACE,
                               provider_account_sha256=ACCOUNT, policy_mode=mode)
    if not populated or mode != ESTIMATE_POLICY:
        return db
    for client, amount, state in (("yellow", "1", "submitted"),
                                  ("babylon", "2", "result_ready"),
                                  ("origintrail", "3", "submit_started")):
        scope = _scope(client)
        provider_id = str(uuid4())
        proof = ProviderEstimateProof(scope["request_sha256"], ACCOUNT, amount,
                                      NOW, NOW + timedelta(minutes=5), "e" * 64)
        with db:
            db.execute("BEGIN IMMEDIATE")
            reserve_image_budget(db, **scope, now=NOW, estimate_proof=proof)
            db.execute("INSERT INTO quality_news_copy VALUES (?,?,?,?,?,?,?,?,?)",
                (scope["operation_id"], str(uuid4()), WORKSPACE, client,
                 scope["source_item_id"], "c" * 64, "2026-10-15",
                 '{"private":"historical checkpoint"}', "d" * 64))
            db.execute("INSERT INTO higgsfield_initial_banners VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (scope["operation_id"], scope["request_sha256"], WORKSPACE,
                 client, scope["source_item_id"], "2026-10-15", state,
                 None if state == "submit_started" else provider_id,
                 b"historical png bytes" if state == "result_ready" else None,
                 "f" * 64 if state == "result_ready" else None, int(NOW.timestamp())))
            if state != "submit_started":
                bind_image_budget_request(db, **scope, provider_request_id=provider_id)
            if state == "result_ready":
                mark_image_budget_completed(db, **scope, provider_request_id=provider_id,
                                            png_sha256="f" * 64, now=NOW)
            if client == "yellow":
                settle_image_budget(db, **scope, now=NOW,
                    final_charge=FinalChargeProof(provider_id, 1_000_000, NOW, "f" * 64))
    return db


@pytest.fixture
def store(tmp_path):
    db = _store(tmp_path)
    yield db
    db.close()


def _plan(db):
    return plan_token_forecast_transition(db, workspace_id=WORKSPACE,
                                           provider_account_sha256=ACCOUNT)


def _apply(db, plan, now=NOW):
    return apply_token_forecast_transition(db, workspace_id=WORKSPACE,
        provider_account_sha256=ACCOUNT, plan_sha256=plan.plan_sha256, now=now)


def test_plan_is_read_only_and_binds_pinned_policy_and_all_historical_tables(store):
    before = _historical_rows(store)
    tables = _tables(store)
    plan = _plan(store)
    assert plan.source_policy == ESTIMATE_POLICY
    assert plan.destination_policy == TOKEN_RATE_FORECAST_POLICY
    assert len(plan.plan_sha256) == len(plan.pre_state_sha256) == len(plan.forecast_policy_sha256) == 64
    assert set(dict((name, count) for name, count, _ in plan.table_rows)) >= {
        "image_budget_reservations", "quality_news_copy", "higgsfield_initial_banners",
        "optional_owner"}
    assert plan.plan_sha256 == _plan(store).plan_sha256
    assert _tables(store) == tables and _historical_rows(store) == before
    assert not store.in_transaction
    assert "historical checkpoint" not in repr(plan)
    assert "private owner bytes" not in repr(plan)


def test_apply_preserves_pending_provisional_actual_and_old_rows(store):
    old_rows = _historical_rows(store)
    before = _snapshot(store)
    next_day = _snapshot(store, NOW + timedelta(minutes=2))
    assert (before.actual_usd_micros, before.provisional_usd_micros,
            before.reserved_usd_micros, before.remaining_usd_micros) == (1_000_000, 2_000_000, 3_000_000, 4_000_000)
    assert next_day.reserved_usd_micros == 3_000_000
    plan = _plan(store)
    with store:
        store.execute("BEGIN IMMEDIATE")
        receipt = _apply(store, plan)
        assert store.in_transaction
    assert receipt.plan_sha256 == plan.plan_sha256
    assert receipt.pre_state_sha256 == plan.pre_state_sha256
    assert receipt.post_state_sha256 != receipt.pre_state_sha256
    assert _historical_rows(store) == old_rows
    assert _snapshot(store).policy_mode == TOKEN_RATE_FORECAST_POLICY
    assert (_snapshot(store).actual_usd_micros, _snapshot(store).provisional_usd_micros,
            _snapshot(store).reserved_usd_micros, _snapshot(store).remaining_usd_micros) == (
                before.actual_usd_micros, before.provisional_usd_micros,
                before.reserved_usd_micros, before.remaining_usd_micros)
    assert _snapshot(store, NOW + timedelta(minutes=2)).reserved_usd_micros == 3_000_000
    assert store.execute("SELECT count(*) FROM image_budget_forecast_evidence").fetchone()[0] == 0
    assert store.execute("SELECT plan_sha256,pre_state_sha256,post_state_sha256 "
                         "FROM image_budget_transition_audit").fetchone() == (
                             plan.plan_sha256, plan.pre_state_sha256, receipt.post_state_sha256)


@pytest.mark.parametrize("table,change", [
    ("quality_news_copy", "UPDATE quality_news_copy SET checkpoint='changed' WHERE client_id='yellow'"),
    ("higgsfield_initial_banners", "UPDATE higgsfield_initial_banners SET png=X'01' WHERE client_id='babylon'"),
    ("image_budget_reservations", "UPDATE image_budget_reservations SET estimated_usd_micros=4 WHERE client_id='origintrail'"),
    ("optional_owner", "UPDATE optional_owner SET private_data=X'00' WHERE id=1"),
])
def test_any_historical_drift_rejects_without_policy_change(store, table, change):
    plan = _plan(store)
    with pytest.raises(TokenForecastTransitionError, match="plan_mismatch"), store:
        store.execute("BEGIN IMMEDIATE")
        store.execute(change)
        _apply(store, plan)
    assert _snapshot(store).policy_mode == ESTIMATE_POLICY
    assert "image_budget_forecast_evidence" not in _tables(store)


def test_apply_requires_transaction_and_exact_plan_sha(store):
    plan = _plan(store)
    with pytest.raises(TokenForecastTransitionError, match="transaction_required"):
        _apply(store, plan)
    with pytest.raises(TokenForecastTransitionError, match="plan_mismatch"), store:
        store.execute("BEGIN IMMEDIATE")
        apply_token_forecast_transition(store, workspace_id=WORKSPACE,
            provider_account_sha256=ACCOUNT, plan_sha256="0" * 64, now=NOW)
    assert _snapshot(store).policy_mode == ESTIMATE_POLICY


def test_schema_drift_rejects_stale_plan(store):
    plan = _plan(store)
    with store:
        store.execute("ALTER TABLE optional_owner ADD COLUMN review_marker TEXT")
    with pytest.raises(TokenForecastTransitionError, match="plan_mismatch"), store:
        store.execute("BEGIN IMMEDIATE")
        _apply(store, plan)
    assert _snapshot(store).policy_mode == ESTIMATE_POLICY
    assert "image_budget_transition_audit" not in _tables(store)


def test_caller_rollback_removes_only_new_transition_changes(store):
    plan = _plan(store)
    old_rows = _historical_rows(store)
    store.execute("BEGIN IMMEDIATE")
    _apply(store, plan)
    store.rollback()
    assert _snapshot(store).policy_mode == ESTIMATE_POLICY
    assert _historical_rows(store) == old_rows
    assert "image_budget_forecast_evidence" not in _tables(store)
    assert "image_budget_transition_audit" not in _tables(store)
    assert _plan(store).plan_sha256 == plan.plan_sha256


def test_replay_never_resets_or_reconverts_old_store(store):
    plan = _plan(store)
    with store:
        store.execute("BEGIN IMMEDIATE")
        _apply(store, plan)
    with pytest.raises(TokenForecastTransitionError), store:
        store.execute("BEGIN IMMEDIATE")
        _apply(store, plan)
    assert store.execute("SELECT count(*) FROM image_budget_transition_audit").fetchone()[0] == 1
    assert store.execute("SELECT count(*) FROM image_budget_reservations").fetchone()[0] == 3


@pytest.mark.parametrize("mode", [STRICT_POLICY, TOKEN_RATE_FORECAST_POLICY])
def test_other_source_policies_cannot_transition(tmp_path, mode):
    with _store(tmp_path, mode=mode, populated=False) as db:
        with pytest.raises(TokenForecastTransitionError):
            _plan(db)


def test_wrong_workspace_or_account_and_orphan_submit_fail_closed(store):
    with pytest.raises(TokenForecastTransitionError, match="binding_conflict"):
        plan_token_forecast_transition(store, workspace_id=str(uuid4()),
                                       provider_account_sha256=ACCOUNT)
    with pytest.raises(TokenForecastTransitionError, match="binding_conflict"):
        plan_token_forecast_transition(store, workspace_id=WORKSPACE,
                                       provider_account_sha256="f" * 64)
    with store:
        store.execute("INSERT INTO higgsfield_initial_banners VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid4()), "b" * 64, WORKSPACE, "yellow", str(uuid4()),
             "2026-10-15", "submit_started", None, None, None, int(NOW.timestamp())))
    with pytest.raises(TokenForecastTransitionError, match="prior_liability"):
        _plan(store)
