"""Disposable SQLite + synthetic verifier fixtures; no genuine provider proof."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import sqlite3
from uuid import uuid4

import pytest

from core.content_ops.image_budget import (
    DAILY_LIMIT_USD_MICROS, ESTIMATE_POLICY, STRICT_POLICY, EnforcedCeilingProof,
    FinalChargeProof, ImageBudgetError, ProviderEstimateProof,
    bind_image_budget_request, budget_snapshot, mark_image_budget_completed, provision_image_budget,
    reserve_image_budget, settle_image_budget, usd_to_micros,
)

WORKSPACE = "11111111-1111-4111-8111-111111111111"
OP = "22222222-2222-4222-8222-222222222222"
SOURCE = "33333333-3333-4333-8333-333333333333"
PROVIDER = "44444444-4444-4444-8444-444444444444"
ACCOUNT, CAPABILITY, DIGEST, EVIDENCE = (letter * 64 for letter in "abcd")
NOW = datetime(2026, 10, 12, 14, 59, tzinfo=timezone.utc)  # 23:59 KST


def scope(**changes):
    return dict(operation_id=OP, request_sha256=DIGEST, workspace_id=WORKSPACE,
        client_id="yellow", source_item_id=SOURCE, provider_account_sha256=ACCOUNT) | changes


def proof(amount=3_000_000, now=NOW, **changes):
    return replace(EnforcedCeilingProof(DIGEST, ACCOUNT, CAPABILITY, amount,
        EVIDENCE, now + timedelta(minutes=5)), **changes)


def final(amount=2_000_000, charged_at=NOW, **changes):
    return replace(FinalChargeProof(PROVIDER, amount, charged_at, "e" * 64), **changes)


def provision(db, capability=CAPABILITY, policy_mode=STRICT_POLICY):
    with db:
        db.execute("BEGIN IMMEDIATE")
        provision_image_budget(db, workspace_id=WORKSPACE,
            provider_account_sha256=ACCOUNT, capability_evidence_sha256=capability,
            policy_mode=policy_mode)


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "synthetic-budget.sqlite3"
    db = sqlite3.connect(path)
    provision(db)
    yield db, path
    db.close()


def reserve(db, *, amount=3_000_000, now=NOW, ceiling=None, **changes):
    with db:
        db.execute("BEGIN IMMEDIATE")
        return reserve_image_budget(db, **scope(**changes), now=now,
                                    ceiling_proof=ceiling if ceiling is not None else proof(amount, now))


def bind(db, **changes):
    with db:
        db.execute("BEGIN IMMEDIATE")
        bind_image_budget_request(db, **scope(), provider_request_id=changes.get("provider_request_id", PROVIDER))


def settle(db, *, receipt=None, now=NOW, **changes):
    with db:
        db.execute("BEGIN IMMEDIATE")
        settle_image_budget(db, **scope(**changes), now=now,
                            final_charge=receipt if receipt is not None else final())


def snapshot(db, now=NOW):
    return budget_snapshot(db, workspace_id=WORKSPACE, provider_account_sha256=ACCOUNT, now=now)


def complete(db, *, now=NOW, provider_request_id=PROVIDER, png_sha256="f" * 64, **changes):
    with db:
        db.execute("BEGIN IMMEDIATE")
        mark_image_budget_completed(db, **scope(**changes), provider_request_id=provider_request_id,
                                    png_sha256=png_sha256, now=now)


def test_capability_default_closed_and_no_runtime_provisioning(tmp_path):
    with sqlite3.connect(tmp_path / "closed.sqlite3") as db:
        with pytest.raises(ImageBudgetError, match="store_unavailable"):
            snapshot(db)
        assert db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
        provision(db, None)
        assert not snapshot(db).capability_provisioned
        with pytest.raises(ImageBudgetError, match="evidence_required"):
            reserve(db)
        assert db.execute("SELECT count(*) FROM image_budget_reservations").fetchone()[0] == 0
        with pytest.raises(ImageBudgetError, match="already_provisioned"):
            provision(db)


def test_helpers_require_caller_transaction_and_never_commit(ledger):
    db, _ = ledger
    with pytest.raises(ImageBudgetError, match="transaction_required"):
        reserve_image_budget(db, **scope(), now=NOW, ceiling_proof=proof())
    db.execute("BEGIN IMMEDIATE")
    db.execute("CREATE TABLE synthetic_submit_intent (operation_id TEXT)")
    assert reserve_image_budget(db, **scope(), now=NOW, ceiling_proof=proof())
    db.execute("INSERT INTO synthetic_submit_intent VALUES (?)", (OP,))
    assert db.in_transaction
    db.rollback()
    assert snapshot(db).reserved_usd_micros == 0
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='synthetic_submit_intent'").fetchone() is None
    assert reserve(db)


@pytest.mark.parametrize("prior_state", ["submit_started", "submitted", "result_ready"])
def test_nonempty_legacy_journal_requires_separate_reconciliation(tmp_path, prior_state):
    with sqlite3.connect(tmp_path / "existing.sqlite3") as db:
        db.execute("CREATE TABLE higgsfield_initial_banners (state TEXT)")
        db.execute("INSERT INTO higgsfield_initial_banners VALUES (?)", (prior_state,))
        db.commit()
        with pytest.raises(ImageBudgetError, match="prior_liability_reconciliation_required"):
            provision(db)
        assert not db.in_transaction
        assert db.execute("SELECT state FROM higgsfield_initial_banners").fetchall() == [(prior_state,)]
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'image_budget_%'").fetchone() is None


def test_empty_legacy_journal_can_be_provisioned_explicitly(tmp_path):
    with sqlite3.connect(tmp_path / "empty-existing.sqlite3") as db:
        db.execute("CREATE TABLE higgsfield_initial_banners (state TEXT)")
        provision(db)
        assert snapshot(db).reserved_usd_micros == snapshot(db).actual_usd_micros == 0
        assert db.execute("SELECT count(*) FROM higgsfield_initial_banners").fetchone()[0] == 0


@pytest.mark.parametrize("bad", [None, {}, proof(kind="estimate"), proof(kind="provider_estimate")])
def test_estimates_and_missing_proof_never_reserve(ledger, bad):
    db, _ = ledger
    with pytest.raises(ImageBudgetError, match="evidence_required"), db:
        db.execute("BEGIN IMMEDIATE")
        reserve_image_budget(db, **scope(), now=NOW, ceiling_proof=bad)
    assert snapshot(db).reserved_usd_micros == 0


@pytest.mark.parametrize("changes", [
    {"provider_account_sha256": "f" * 64}, {"request_sha256": "f" * 64},
    {"capability_evidence_sha256": "f" * 64}, {"evidence_sha256": "not-proof"},
    {"ceiling_usd_micros": True}, {"ceiling_usd_micros": 1.2},
    {"ceiling_usd_micros": 0}, {"ceiling_usd_micros": 10_000_001},
    {"expires_at": NOW}, {"expires_at": NOW.replace(tzinfo=None)},
])
def test_ceiling_proof_exact_binding_types_and_expiry(ledger, changes):
    db, _ = ledger
    with pytest.raises(ImageBudgetError):
        reserve(db, ceiling=proof(**changes))
    assert snapshot(db).reserved_usd_micros == 0


@pytest.mark.parametrize("changes", [
    {"workspace_id": str(uuid4())}, {"provider_account_sha256": "f" * 64},
    {"client_id": "squid"},
])
def test_global_policy_cannot_reset_by_switching_scope(ledger, changes):
    db, _ = ledger
    with pytest.raises(ImageBudgetError):
        reserve(db, **changes)
    assert snapshot(db).reserved_usd_micros == 0


def test_all_three_clients_share_exact_ten_dollar_limit(ledger):
    db, _ = ledger
    assert reserve(db, amount=4_000_000)
    for client in ("babylon", "origintrail"):
        assert reserve(db, client_id=client, source_item_id=str(uuid4()), operation_id=str(uuid4()))
    result = snapshot(db)
    assert result.limit_usd_micros == DAILY_LIMIT_USD_MICROS == 10_000_000
    assert (result.actual_usd_micros, result.reserved_usd_micros, result.remaining_usd_micros) == (0, 10_000_000, 0)
    with pytest.raises(ImageBudgetError, match="daily_limit"):
        reserve(db, amount=1, source_item_id=str(uuid4()), operation_id=str(uuid4()))


def test_concurrent_total_and_same_operation_single_winner(ledger):
    db, path = ledger
    def attempt(index):
        with sqlite3.connect(path, timeout=10) as connection:
            try:
                return reserve(connection, operation_id=str(uuid4()), source_item_id=str(uuid4()),
                    client_id=("yellow", "babylon", "origintrail")[index % 3])
            except ImageBudgetError as exc:
                assert str(exc) == "image_budget_daily_limit"
                return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(attempt, range(8))).count(True) == 3
    assert snapshot(db).reserved_usd_micros == 9_000_000
    def same(_):
        with sqlite3.connect(path, timeout=10) as connection:
            return reserve(connection, amount=1_000_000)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(same, range(8))).count(True) == 1
    assert snapshot(db).reserved_usd_micros == 10_000_000


def test_replay_and_changed_ids_never_grant_more_authority(ledger):
    db, path = ledger
    assert reserve(db)
    with sqlite3.connect(path) as restart:
        assert reserve(restart) is False
        assert reserve(restart, now=NOW + timedelta(days=1)) is False
        with pytest.raises(ImageBudgetError, match="binding_conflict"):
            reserve(restart, request_sha256="f" * 64)
        with pytest.raises(ImageBudgetError, match="already_reserved"):
            reserve(restart, operation_id=str(uuid4()))


def test_deferred_transactions_are_serialized_before_budget_read(ledger):
    db, path = ledger
    def attempt(_):
        with sqlite3.connect(path, timeout=10) as connection:
            connection.execute("BEGIN")
            try:
                return reserve_image_budget(connection, **scope(operation_id=str(uuid4()),
                    source_item_id=str(uuid4())), now=NOW, ceiling_proof=proof(6_000_000))
            except ImageBudgetError as exc:
                assert str(exc) == "image_budget_daily_limit"
                return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(attempt, range(4))).count(True) == 1
    assert snapshot(db).reserved_usd_micros == 6_000_000


def test_row_factory_compatibility_and_distinct_provider_receipts(ledger):
    db, _ = ledger
    db.row_factory = sqlite3.Row  # Existing Higgsfield owner uses this factory.
    assert reserve(db)
    bind(db)
    second = scope(operation_id=str(uuid4()), source_item_id=str(uuid4()), client_id="babylon")
    assert reserve(db, **{key: value for key, value in second.items() if key not in {"workspace_id", "provider_account_sha256"}})
    with pytest.raises(ImageBudgetError, match="binding_conflict"), db:
        db.execute("BEGIN IMMEDIATE")
        bind_image_budget_request(db, **second, provider_request_id=PROVIDER)
    settle(db)
    assert snapshot(db).actual_usd_micros == 2_000_000


def test_unknown_carries_across_midnight_until_exact_late_billing(ledger):
    db, _ = ledger
    tomorrow = NOW + timedelta(minutes=2)
    assert reserve(db, amount=10_000_000)
    assert snapshot(db, tomorrow).kst_day == "2026-10-13"
    assert snapshot(db, tomorrow).reserved_usd_micros == 10_000_000
    with pytest.raises(ImageBudgetError, match="daily_limit"):
        reserve(db, now=tomorrow, amount=1, operation_id=str(uuid4()), source_item_id=str(uuid4()))
    bind(db)
    settle(db, now=tomorrow, receipt=final(charged_at=tomorrow, amount=4_000_000))
    result = snapshot(db, tomorrow)
    assert (result.actual_usd_micros, result.reserved_usd_micros, result.remaining_usd_micros) == (4_000_000, 0, 6_000_000)
    assert reserve(db, now=tomorrow) is False


def test_late_receipt_uses_charge_day_not_reconciliation_day(ledger):
    db, _ = ledger
    assert reserve(db)
    bind(db)
    tomorrow = NOW + timedelta(days=1)
    settle(db, now=tomorrow, receipt=final(charged_at=NOW))
    assert snapshot(db, NOW).actual_usd_micros == 2_000_000
    assert snapshot(db, tomorrow).actual_usd_micros == 0
    assert snapshot(db, tomorrow).reserved_usd_micros == 0


def test_final_receipt_requires_bound_provider_and_exact_idempotency(ledger):
    db, _ = ledger
    assert reserve(db)
    with pytest.raises(ImageBudgetError, match="binding_conflict"):
        settle(db)
    bind(db)
    bind(db)
    with pytest.raises(ImageBudgetError, match="binding_conflict"):
        bind(db, provider_request_id=str(uuid4()))
    with pytest.raises(ImageBudgetError, match="binding_conflict"):
        settle(db, receipt=final(provider_request_id=str(uuid4())))
    settle(db)
    settle(db)
    with pytest.raises(ImageBudgetError, match="settlement_conflict"):
        settle(db, receipt=final(evidence_sha256="f" * 64))
    with pytest.raises(ImageBudgetError, match="settlement_conflict"):
        settle(db, receipt=final(amount=1))
    assert reserve(db) is False
    assert snapshot(db).actual_usd_micros == 2_000_000


@pytest.mark.parametrize("receipt", [final(amount=True), final(amount=-1),
    final(amount=1.5), final(charged_at=NOW - timedelta(seconds=1)),
    final(charged_at=NOW + timedelta(seconds=1)), final(evidence_sha256="not-proof")])
def test_invalid_settlement_keeps_liability(ledger, receipt):
    db, _ = ledger
    assert reserve(db)
    bind(db)
    with pytest.raises(ImageBudgetError):
        settle(db, receipt=receipt)
    assert snapshot(db).reserved_usd_micros == 3_000_000


def test_zero_charge_does_not_allow_resubmit(ledger):
    db, _ = ledger
    assert reserve(db)
    bind(db)
    settle(db, receipt=final(amount=0))
    assert snapshot(db).remaining_usd_micros == 10_000_000
    assert reserve(db) is False
    with pytest.raises(ImageBudgetError, match="already_reserved"):
        reserve(db, operation_id=str(uuid4()))


def test_breach_is_recorded_honestly_and_closes_future_days(ledger):
    db, _ = ledger
    assert reserve(db)
    bind(db)
    settle(db, receipt=final(amount=11_000_000))
    summary = snapshot(db)
    assert summary.actual_usd_micros == 11_000_000 and summary.remaining_usd_micros == 0
    assert summary.capability_breached
    with pytest.raises(ImageBudgetError, match="capability_breached"):
        reserve(db, now=NOW + timedelta(days=1), operation_id=str(uuid4()), source_item_id=str(uuid4()))


@pytest.fixture
def estimates(tmp_path):
    path = tmp_path / "synthetic-estimates.sqlite3"
    db = sqlite3.connect(path)
    provision(db, None, ESTIMATE_POLICY)
    yield db, path
    db.close()


def estimate(usd="3", now=NOW, **changes):
    return replace(ProviderEstimateProof(DIGEST, ACCOUNT, usd, now,
        now + timedelta(minutes=5), EVIDENCE), **changes)


def reserve_estimate(db, *, usd="3", now=NOW, proof=None, **changes):
    with db:
        db.execute("BEGIN IMMEDIATE")
        return reserve_image_budget(db, **scope(**changes), now=now,
            estimate_proof=proof if proof is not None else estimate(usd, now))


@pytest.mark.parametrize("usd,micros", [("0", 0), ("1", 1_000_000),
    ("1.234567", 1_234_567), ("1.2345671", 1_234_568), ("0.000000001", 1),
    (Decimal("1.0000000000000000000000000000000001"), 1_000_001),
    ("9223372036854.775807", 2**63 - 1)])
def test_usd_conversion_is_exact_and_rounds_up_independent_of_context(usd, micros):
    with localcontext() as context:
        context.prec = 2
        assert usd_to_micros(usd) == micros


@pytest.mark.parametrize("usd", [1.1, 1, True, None, "NaN", "Infinity", "-1", "1e2", " 1",
    "1,000", "1.0usd", Decimal("NaN"), Decimal("Infinity"), Decimal("-1"),
    "9223372036854.7758071", Decimal("1e-1001")])
def test_invalid_usd_never_uses_float_or_silently_truncates(usd):
    with pytest.raises(ImageBudgetError, match="usd_invalid"):
        usd_to_micros(usd)


def test_estimate_policy_is_explicit_and_never_labels_estimates_as_ceilings(estimates):
    db, _ = estimates
    assert reserve_estimate(db, usd="3.00000001")
    value = snapshot(db)
    assert value.policy_mode == ESTIMATE_POLICY
    assert not value.capability_provisioned and not value.capability_breached
    assert value.reserved_usd_micros == 3_000_001
    assert value.actual_overrun_usd_micros == 0
    assert db.execute("SELECT ceiling_usd_micros,ceiling_evidence_sha256,estimated_usd_micros,"
        "estimate_evidence_sha256,estimated_at,estimate_expires_at FROM image_budget_reservations"
        ).fetchone() == (None, None, 3_000_001, EVIDENCE,
                        NOW.isoformat(timespec="microseconds"),
                        (NOW + timedelta(minutes=5)).isoformat(timespec="microseconds"))
    with pytest.raises(ImageBudgetError, match="already_provisioned"):
        provision(db)


def test_modes_cannot_accept_each_others_proofs(ledger, estimates):
    strict, _ = ledger
    estimated, _ = estimates
    with pytest.raises(ImageBudgetError, match="evidence_required"):
        reserve_estimate(strict)
    with pytest.raises(ImageBudgetError, match="evidence_required"):
        reserve(estimated)
    with pytest.raises(ImageBudgetError, match="evidence_required"), estimated:
        estimated.execute("BEGIN IMMEDIATE")
        reserve_image_budget(estimated, **scope(), now=NOW, estimate_proof=estimate(), ceiling_proof=proof())
    assert snapshot(strict).reserved_usd_micros == snapshot(estimated).reserved_usd_micros == 0


@pytest.mark.parametrize("changes", [
    {"provider_account_sha256": "f" * 64}, {"request_sha256": "f" * 64},
    {"evidence_sha256": "bad"}, {"kind": "caller_estimate"},
    {"estimated_at": NOW + timedelta(seconds=1)},
    {"estimated_at": NOW - timedelta(minutes=6)},
    {"expires_at": NOW}, {"expires_at": NOW + timedelta(minutes=6)},
    {"estimated_at": NOW.replace(tzinfo=None)}, {"expires_at": NOW.replace(tzinfo=None)},
    {"estimated_usd": "0"}, {"estimated_usd": "10.0000000001"},
])
def test_estimate_requires_exact_authenticated_binding_and_freshness(estimates, changes):
    db, _ = estimates
    with pytest.raises(ImageBudgetError):
        reserve_estimate(db, proof=estimate(**changes))
    assert snapshot(db).reserved_usd_micros == 0


def test_estimate_all_ybo_clients_share_ten_dollars_and_atomic_rollback(estimates):
    db, _ = estimates
    assert reserve_estimate(db, usd="4")
    for client in ("babylon", "origintrail"):
        assert reserve_estimate(db, client_id=client, source_item_id=str(uuid4()), operation_id=str(uuid4()))
    assert snapshot(db).reserved_usd_micros == 10_000_000
    with pytest.raises(ImageBudgetError, match="daily_limit"):
        reserve_estimate(db, usd="0.00000001", source_item_id=str(uuid4()), operation_id=str(uuid4()))
    assert db.execute("SELECT count(*) FROM image_budget_reservations").fetchone()[0] == 3


def test_valid_forecast_above_total_budget_is_daily_limit_not_bad_evidence(estimates):
    db, _ = estimates
    with pytest.raises(ImageBudgetError, match="^image_budget_daily_limit$"):
        reserve_estimate(db, usd="10.000001")
    assert snapshot(db).reserved_usd_micros == 0
    assert db.execute("SELECT count(*) FROM image_budget_reservations").fetchone()[0] == 0


def test_estimate_and_submit_intent_rollback_in_caller_transaction(estimates):
    db, _ = estimates
    db.execute("BEGIN IMMEDIATE")
    assert reserve_image_budget(db, **scope(), now=NOW, estimate_proof=estimate())
    db.execute("CREATE TABLE synthetic_submit_intent (operation_id TEXT)")
    db.execute("INSERT INTO synthetic_submit_intent VALUES (?)", (OP,))
    db.rollback()
    assert snapshot(db).reserved_usd_micros == 0
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='synthetic_submit_intent'").fetchone() is None


def test_estimate_concurrent_reservations_never_exceed_known_budget(estimates):
    db, path = estimates
    def attempt(_):
        with sqlite3.connect(path, timeout=10) as connection:
            connection.execute("BEGIN")
            try:
                return reserve_image_budget(connection, **scope(operation_id=str(uuid4()),
                    source_item_id=str(uuid4())), now=NOW, estimate_proof=estimate())
            except ImageBudgetError as exc:
                assert str(exc) == "image_budget_daily_limit"
                return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(attempt, range(8))).count(True) == 3
    assert snapshot(db).reserved_usd_micros == 9_000_000


def test_estimate_unknown_restart_and_midnight_never_reset_reservation(estimates):
    db, path = estimates
    assert reserve_estimate(db, usd="10")
    tomorrow = NOW + timedelta(minutes=2)
    with sqlite3.connect(path) as restarted:
        assert reserve_estimate(restarted, now=tomorrow) is False
        assert snapshot(restarted, tomorrow).reserved_usd_micros == 10_000_000
        with pytest.raises(ImageBudgetError, match="daily_limit"):
            reserve_estimate(restarted, now=tomorrow, source_item_id=str(uuid4()), operation_id=str(uuid4()))
        with pytest.raises(ImageBudgetError, match="already_reserved"):
            reserve_estimate(restarted, now=tomorrow, operation_id=str(uuid4()))


def test_estimate_difference_is_honest_and_not_a_lifetime_ceiling_breach(estimates):
    db, _ = estimates
    assert reserve_estimate(db, usd="1")
    bind(db)
    settle(db, receipt=final(amount=11_000_000))
    settle(db, receipt=final(amount=11_000_000))
    value = snapshot(db)
    assert value.actual_usd_micros == 11_000_000 and value.actual_overrun_usd_micros == 1_000_000
    assert value.remaining_usd_micros == value.reserved_usd_micros == 0
    assert not value.capability_breached
    with pytest.raises(ImageBudgetError, match="daily_limit"):
        reserve_estimate(db, source_item_id=str(uuid4()), operation_id=str(uuid4()))
    tomorrow = NOW + timedelta(days=1)
    assert reserve_estimate(db, now=tomorrow) is False
    assert reserve_estimate(db, now=tomorrow, source_item_id=str(uuid4()), operation_id=str(uuid4()))


def test_estimate_actual_plus_all_pending_controls_admission_and_late_charge_day(estimates):
    db, _ = estimates
    assert reserve_estimate(db, usd="1")
    bind(db)
    assert reserve_estimate(db, usd="4", client_id="babylon", source_item_id=str(uuid4()), operation_id=str(uuid4()))
    tomorrow = NOW + timedelta(minutes=2)
    settle(db, now=tomorrow, receipt=final(amount=7_000_000, charged_at=tomorrow))
    value = snapshot(db, tomorrow)
    assert (value.actual_usd_micros, value.reserved_usd_micros, value.remaining_usd_micros) == (7_000_000, 4_000_000, 0)
    assert snapshot(db, NOW).actual_usd_micros == 0
    with pytest.raises(ImageBudgetError, match="daily_limit"):
        reserve_estimate(db, now=tomorrow, usd="0.1", source_item_id=str(uuid4()), operation_id=str(uuid4()))


def test_estimate_provision_refuses_old_schema_and_prior_unbudgeted_work(tmp_path):
    with sqlite3.connect(tmp_path / "legacy-policy.sqlite3") as db:
        db.execute("CREATE TABLE image_budget_policy (singleton INTEGER,contract TEXT)")
        db.execute("INSERT INTO image_budget_policy VALUES (1,'image-budget-usd-kst@1')")
        db.commit()
        with pytest.raises(ImageBudgetError, match="already_provisioned"):
            provision(db, None, ESTIMATE_POLICY)
        with pytest.raises(ImageBudgetError, match="store_unavailable"):
            snapshot(db)
        assert db.execute("SELECT contract FROM image_budget_policy").fetchone()[0] == "image-budget-usd-kst@1"
    with sqlite3.connect(tmp_path / "legacy-paid.sqlite3") as db:
        db.execute("CREATE TABLE higgsfield_initial_banners (state TEXT)")
        db.execute("INSERT INTO higgsfield_initial_banners VALUES ('submit_started')")
        db.commit()
        with pytest.raises(ImageBudgetError, match="prior_liability_reconciliation_required"):
            provision(db, None, ESTIMATE_POLICY)
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'image_budget_%'").fetchone() is None


@pytest.mark.parametrize("mode,capability", [(ESTIMATE_POLICY, CAPABILITY), ("estimate", None)])
def test_no_ambiguous_estimate_policy_or_fake_ceiling_capability(tmp_path, mode, capability):
    with sqlite3.connect(tmp_path / "invalid-policy.sqlite3") as db:
        with pytest.raises(ImageBudgetError, match="policy_invalid"):
            provision(db, capability, mode)
        assert db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0


def test_completed_estimate_is_provisional_not_actual_and_does_not_stall_future_days(estimates):
    db, _ = estimates
    assert reserve_estimate(db, usd="10")
    bind(db)
    complete(db)
    value = snapshot(db)
    assert (value.actual_usd_micros, value.provisional_usd_micros, value.reserved_usd_micros,
            value.remaining_usd_micros) == (0, 10_000_000, 0, 0)
    with pytest.raises(ImageBudgetError, match="daily_limit"):
        reserve_estimate(db, source_item_id=str(uuid4()), operation_id=str(uuid4()))
    tomorrow = NOW + timedelta(minutes=2)
    value = snapshot(db, tomorrow)
    assert value.actual_usd_micros == value.provisional_usd_micros == value.reserved_usd_micros == 0
    assert value.remaining_usd_micros == 10_000_000
    assert reserve_estimate(db, usd="10", now=tomorrow,
                            source_item_id=str(uuid4()), operation_id=str(uuid4()))
    assert db.execute("SELECT actual_usd_micros FROM image_budget_reservations WHERE operation_id=?",
                      (OP,)).fetchone()[0] is None


def test_completed_estimate_replay_never_moves_day_or_reopens_source(estimates):
    db, path = estimates
    assert reserve_estimate(db)
    bind(db)
    complete(db)
    tomorrow = NOW + timedelta(days=1)
    with sqlite3.connect(path) as restarted:
        complete(restarted, now=tomorrow)
        assert snapshot(restarted, NOW).provisional_usd_micros == 3_000_000
        assert snapshot(restarted, tomorrow).provisional_usd_micros == 0
        assert reserve_estimate(restarted, now=tomorrow) is False
        with pytest.raises(ImageBudgetError, match="already_reserved"):
            reserve_estimate(restarted, now=tomorrow, operation_id=str(uuid4()))
        with pytest.raises(ImageBudgetError, match="completion_conflict"):
            complete(restarted, now=tomorrow, png_sha256="e" * 64)
        with pytest.raises(ImageBudgetError, match="binding_conflict"):
            complete(restarted, now=tomorrow, provider_request_id=str(uuid4()))


def test_completed_estimate_requires_bound_provider_and_valid_time_hash(estimates):
    db, _ = estimates
    assert reserve_estimate(db)
    with pytest.raises(ImageBudgetError, match="binding_conflict"):
        complete(db)
    bind(db)
    with pytest.raises(ImageBudgetError, match="completion_time_invalid"):
        complete(db, now=NOW - timedelta(microseconds=1))
    with pytest.raises(ImageBudgetError, match="binding_conflict"):
        complete(db, png_sha256="invalid")
    assert snapshot(db).reserved_usd_micros == 3_000_000
    assert snapshot(db).provisional_usd_micros == 0


def test_completion_and_png_commit_rollback_together(estimates):
    db, _ = estimates
    assert reserve_estimate(db)
    bind(db)
    db.execute("BEGIN IMMEDIATE")
    mark_image_budget_completed(db, **scope(), provider_request_id=PROVIDER, png_sha256="f" * 64, now=NOW)
    db.execute("CREATE TABLE synthetic_png (digest TEXT)")
    db.execute("INSERT INTO synthetic_png VALUES (?)", ("f" * 64,))
    db.rollback()
    assert snapshot(db).reserved_usd_micros == 3_000_000
    assert snapshot(db).provisional_usd_micros == 0
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='synthetic_png'").fetchone() is None


def test_strict_completion_still_retains_unsettled_ceiling_across_days(ledger):
    db, _ = ledger
    assert reserve(db)
    bind(db)
    complete(db)
    assert snapshot(db).reserved_usd_micros == 3_000_000
    assert snapshot(db).provisional_usd_micros == 0
    assert snapshot(db, NOW + timedelta(days=1)).reserved_usd_micros == 3_000_000
    settle(db)
    assert snapshot(db).reserved_usd_micros == 0


def test_final_billing_replaces_completion_estimate_on_verified_charge_day(estimates):
    db, _ = estimates
    assert reserve_estimate(db)
    bind(db)
    complete(db)
    tomorrow = NOW + timedelta(minutes=2)
    settle(db, now=tomorrow, receipt=final(amount=4_000_000, charged_at=tomorrow))
    assert snapshot(db, NOW).provisional_usd_micros == 0
    value = snapshot(db, tomorrow)
    assert (value.actual_usd_micros, value.provisional_usd_micros, value.reserved_usd_micros,
            value.remaining_usd_micros) == (4_000_000, 0, 0, 6_000_000)
    complete(db, now=tomorrow)
    settle(db, now=tomorrow, receipt=final(amount=4_000_000, charged_at=tomorrow))
    assert snapshot(db, tomorrow) == value


def test_actual_provisional_and_unknown_estimates_share_one_daily_budget(estimates):
    db, _ = estimates
    assert reserve_estimate(db, usd="1")
    bind(db)
    settle(db, receipt=final(amount=4_000_000))
    second = {"operation_id": str(uuid4()), "source_item_id": str(uuid4()), "client_id": "babylon"}
    assert reserve_estimate(db, usd="4", **second)
    with db:
        db.execute("BEGIN IMMEDIATE")
        bind_image_budget_request(db, **scope(**second), provider_request_id="55555555-5555-4555-8555-555555555555")
    complete(db, **second, provider_request_id="55555555-5555-4555-8555-555555555555")
    assert reserve_estimate(db, usd="2", operation_id=str(uuid4()), source_item_id=str(uuid4()), client_id="origintrail")
    value = snapshot(db)
    assert (value.actual_usd_micros, value.provisional_usd_micros, value.reserved_usd_micros,
            value.remaining_usd_micros) == (4_000_000, 4_000_000, 2_000_000, 0)
    with pytest.raises(ImageBudgetError, match="daily_limit"):
        reserve_estimate(db, usd="0.01", operation_id=str(uuid4()), source_item_id=str(uuid4()))
