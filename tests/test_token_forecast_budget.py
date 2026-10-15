"""Offline tests for the opt-in local token-rate forecast, never a billing cap."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3
from uuid import uuid4

import pytest

from core.automation.higgsfield_banner import BannerReference, QualityBannerRequest
from core.automation.higgsfield_token_forecast import FORECAST_POLICY_SHA256, forecast_proof
from core.content_ops.image_budget import (
    ESTIMATE_POLICY, STRICT_POLICY, TOKEN_RATE_FORECAST_POLICY,
    FinalChargeProof, ImageBudgetError, ProviderEstimateProof,
    bind_image_budget_request, budget_snapshot, mark_image_budget_completed,
    provision_image_budget, reserve_image_budget, settle_image_budget,
)


NOW = datetime(2026, 10, 15, 14, 59, tzinfo=timezone.utc)  # 23:59 KST
WORKSPACE = "11111111-1111-4111-8111-111111111111"
ACCOUNT = "a" * 64
PROVIDER_REQUEST = "44444444-4444-4444-8444-444444444444"
SOURCE_HASH = "b" * 64
LOGO_HASH = "c" * 64
HANDLES = {"yellow": "Yellow", "babylon": "babylonlabs_io", "origintrail": "origin_trail"}


def request(*, client="yellow", two_refs=True, operation_id=None, source_item_id=None):
    refs = [BannerReference("official_logo", "https://assets.example.com/logo.png", LOGO_HASH)]
    if two_refs:
        refs.insert(0, BannerReference("official_source", "https://assets.example.com/source.png", SOURCE_HASH))
    return QualityBannerRequest(
        operation_id=operation_id or str(uuid4()), workspace_id=WORKSPACE,
        client_id=client, source_item_id=source_item_id or str(uuid4()),
        source_url=f"https://x.com/{HANDLES[client]}/status/1234",
        source_sha256=SOURCE_HASH, source_published_at="2026-10-15T14:00:00Z",
        headline="공식 업데이트", subtitle="새로운 소식을 확인하세요", design_feedback="넉넉한 행간",
        references=tuple(refs),
    )


def scope(req):
    return dict(operation_id=req.operation_id, request_sha256=req.digest(),
                workspace_id=req.workspace_id, client_id=req.client_id,
                source_item_id=req.source_item_id, provider_account_sha256=ACCOUNT)


def provision(db, mode=TOKEN_RATE_FORECAST_POLICY):
    with db:
        db.execute("BEGIN IMMEDIATE")
        provision_image_budget(db, workspace_id=WORKSPACE,
                               provider_account_sha256=ACCOUNT, policy_mode=mode)


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "forecast.sqlite3"
    db = sqlite3.connect(path)
    provision(db)
    yield db, path
    db.close()


def reserve(db, req, *, now=NOW, proof=None):
    with db:
        db.execute("BEGIN IMMEDIATE")
        return reserve_image_budget(db, **scope(req), now=now,
            forecast_proof=proof if proof is not None else forecast_proof(
                req, provider_account_sha256=ACCOUNT, now=now))


def snapshot(db, now=NOW):
    return budget_snapshot(db, workspace_id=WORKSPACE,
                           provider_account_sha256=ACCOUNT, now=now)


def test_local_forecast_is_distinct_and_exact_for_one_or_two_references():
    one, two = request(two_refs=False), request(two_refs=True)
    first = forecast_proof(one, provider_account_sha256=ACCOUNT, now=NOW)
    second = forecast_proof(two, provider_account_sha256=ACCOUNT, now=NOW)
    assert first.kind == second.kind == "local_token_rate_forecast"
    assert first.policy_sha256 == second.policy_sha256 == FORECAST_POLICY_SHA256
    assert first.request_sha256 == one.digest() and second.request_sha256 == two.digest()
    assert first.provider_account_sha256 == second.provider_account_sha256 == ACCOUNT
    assert str(first.estimated_usd) == "3.391488"
    assert str(second.estimated_usd) == "3.588096"
    assert first.evidence_sha256 != second.evidence_sha256
    assert first.forecast_json != second.forecast_json


@pytest.mark.parametrize("at", [
    datetime(2026, 10, 15, 9, 15, 36, 268000, tzinfo=timezone.utc),
    datetime(2026, 11, 14, 9, 15, 36, 269000, tzinfo=timezone.utc),
])
def test_rate_reference_outside_pinned_freshness_window_fails_closed(at):
    with pytest.raises((ImageBudgetError, ValueError)):
        forecast_proof(request(), provider_account_sha256=ACCOUNT, now=at)


def test_forecast_reservation_keeps_provenance_and_never_claims_ceiling(ledger):
    db, _ = ledger
    req = request()
    proof = forecast_proof(req, provider_account_sha256=ACCOUNT, now=NOW)
    assert reserve(db, req, proof=proof)
    value = snapshot(db)
    assert value.policy_mode == TOKEN_RATE_FORECAST_POLICY
    assert not value.capability_provisioned and not value.capability_breached
    assert (value.reserved_usd_micros, value.remaining_usd_micros) == (3_588_096, 6_411_904)
    row = db.execute("SELECT ceiling_usd_micros,estimated_usd_micros FROM image_budget_reservations").fetchone()
    assert row == (None, 3_588_096)
    evidence = db.execute("SELECT policy_sha256,evidence_sha256,forecast_json "
        "FROM image_budget_forecast_evidence WHERE operation_id=?", (req.operation_id,)).fetchone()
    assert evidence == (FORECAST_POLICY_SHA256, proof.evidence_sha256, proof.forecast_json)


@pytest.mark.parametrize("change", [
    {"request_sha256": "d" * 64},
    {"provider_account_sha256": "d" * 64},
    {"policy_sha256": "d" * 64},
    {"evidence_sha256": "d" * 64},
    {"forecast_json": "{}"},
    {"kind": "authenticated_provider_estimate"},
    {"estimated_usd": "0"},
    {"estimated_usd": "10.000001"},
])
def test_tampered_or_misbound_forecast_cannot_reserve(ledger, change):
    db, _ = ledger
    req = request()
    proof = replace(forecast_proof(req, provider_account_sha256=ACCOUNT, now=NOW), **change)
    with pytest.raises(ImageBudgetError):
        reserve(db, req, proof=proof)
    assert db.execute("SELECT count(*) FROM image_budget_reservations").fetchone()[0] == 0


def test_other_policy_stores_reject_forecast_and_no_store_is_reprovisioned(tmp_path):
    for mode in (STRICT_POLICY, ESTIMATE_POLICY):
        with sqlite3.connect(tmp_path / f"{mode}.sqlite3") as db:
            provision(db, mode)
            req = request()
            with pytest.raises(ImageBudgetError):
                reserve(db, req)
            with pytest.raises(ImageBudgetError, match="already_provisioned"):
                provision(db, TOKEN_RATE_FORECAST_POLICY)
            assert db.execute("SELECT count(*) FROM image_budget_reservations").fetchone()[0] == 0


def test_forecast_mode_rejects_authenticated_quote_and_mixed_proofs(ledger):
    db, _ = ledger
    req = request()
    local = forecast_proof(req, provider_account_sha256=ACCOUNT, now=NOW)
    provider = ProviderEstimateProof(req.digest(), ACCOUNT, "1", NOW,
                                     NOW + timedelta(minutes=5), "e" * 64)
    for extras in ({"estimate_proof": provider},
                   {"forecast_proof": local, "estimate_proof": provider}):
        with pytest.raises(ImageBudgetError), db:
            db.execute("BEGIN IMMEDIATE")
            reserve_image_budget(db, **scope(req), now=NOW, **extras)
    assert db.execute("SELECT count(*) FROM image_budget_reservations").fetchone()[0] == 0


def test_ybo_share_ten_dollars_and_third_forecast_is_rejected(ledger):
    db, _ = ledger
    for client in ("yellow", "babylon"):
        assert reserve(db, request(client=client))
    assert snapshot(db).reserved_usd_micros == 7_176_192
    with pytest.raises(ImageBudgetError, match="daily_limit"):
        reserve(db, request(client="origintrail"))
    assert db.execute("SELECT count(*) FROM image_budget_reservations").fetchone()[0] == 2


def test_concurrent_reservations_cannot_exceed_forecast_budget(ledger):
    db, path = ledger
    requests = [request(client=client) for client in ("yellow", "babylon", "origintrail")]
    def attempt(req):
        with sqlite3.connect(path, timeout=10) as connection:
            try:
                return reserve(connection, req)
            except ImageBudgetError as error:
                assert str(error) == "image_budget_daily_limit"
                return False
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(attempt, requests)).count(True) == 2
    assert snapshot(db).reserved_usd_micros == 7_176_192


def test_forecast_reservation_and_submit_intent_roll_back_together(ledger):
    db, _ = ledger
    req = request()
    db.execute("BEGIN IMMEDIATE")
    assert reserve_image_budget(db, **scope(req), now=NOW,
                                forecast_proof=forecast_proof(
                                    req, provider_account_sha256=ACCOUNT, now=NOW))
    db.execute("CREATE TABLE synthetic_submit_intent (operation_id TEXT)")
    db.execute("INSERT INTO synthetic_submit_intent VALUES (?)", (req.operation_id,))
    db.rollback()
    assert snapshot(db).reserved_usd_micros == 0
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='synthetic_submit_intent'").fetchone() is None
    assert db.execute("SELECT count(*) FROM image_budget_forecast_evidence").fetchone()[0] == 0


def test_unknown_attempt_survives_restart_and_kst_midnight_without_reforecast(ledger):
    db, path = ledger
    first, second = request(client="yellow"), request(client="babylon")
    original = forecast_proof(first, provider_account_sha256=ACCOUNT, now=NOW)
    assert reserve(db, first, proof=original)
    assert reserve(db, second)
    tomorrow = NOW + timedelta(minutes=2)
    with sqlite3.connect(path) as restarted:
        # Existing attempts are read by immutable identity before fresh proof
        # validation; an expired/in-memory-lost forecast never reopens a POST.
        with restarted:
            restarted.execute("BEGIN IMMEDIATE")
            assert reserve_image_budget(restarted, **scope(first), now=tomorrow) is False
        assert snapshot(restarted, tomorrow).reserved_usd_micros == 7_176_192
        with pytest.raises(ImageBudgetError, match="daily_limit"):
            reserve(restarted, request(client="origintrail"), now=tomorrow)
        with pytest.raises(ImageBudgetError, match="already_reserved"):
            reserve(restarted, request(client="yellow", source_item_id=first.source_item_id), now=tomorrow)
    assert db.execute("SELECT count(*) FROM image_budget_reservations").fetchone()[0] == 2


def test_completion_is_provisional_and_final_charge_replaces_it(ledger):
    db, _ = ledger
    req = request()
    assert reserve(db, req)
    with db:
        db.execute("BEGIN IMMEDIATE")
        bind_image_budget_request(db, **scope(req), provider_request_id=PROVIDER_REQUEST)
        mark_image_budget_completed(db, **scope(req), provider_request_id=PROVIDER_REQUEST,
                                    png_sha256=hashlib.sha256(b"fixture png").hexdigest(), now=NOW)
    assert (snapshot(db).actual_usd_micros, snapshot(db).provisional_usd_micros,
            snapshot(db).reserved_usd_micros) == (0, 3_588_096, 0)
    tomorrow = NOW + timedelta(minutes=2)
    assert snapshot(db, tomorrow).remaining_usd_micros == 10_000_000
    with db:
        db.execute("BEGIN IMMEDIATE")
        settle_image_budget(db, **scope(req), now=tomorrow,
            final_charge=FinalChargeProof(PROVIDER_REQUEST, 4_500_000, tomorrow, "f" * 64))
    assert (snapshot(db, tomorrow).actual_usd_micros,
            snapshot(db, tomorrow).provisional_usd_micros,
            snapshot(db, tomorrow).remaining_usd_micros) == (4_500_000, 0, 5_500_000)
    assert reserve(db, req, now=tomorrow, proof=forecast_proof(
        req, provider_account_sha256=ACCOUNT, now=NOW)) is False
