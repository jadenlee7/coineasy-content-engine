"""Local image-budget policies, not a provider billing-cap implementation.

The caller owns the private, pre-existing SQLite connection and transaction.
Reserve and submit intent MUST commit together before any paid POST. These
helpers never connect, commit, submit, retry, or read credentials. One immutable
workspace/account policy covers Yellow, Babylon and OriginTrail together.

Strict ceiling evidence is a TRUSTED VERIFIER output, not an estimate. The
separately provisioned estimate policy accepts authenticated provider estimates
and explicitly permits final charges to differ. It is NOT an actual-cost cap.
The separately provisioned local-token-forecast policy uses pinned public
rates and operator assumptions, never calls them an authenticated USD quote,
and persists its sanitized calculation evidence in the same reservation commit.
Pending/unknown estimates carry across days. Completed estimates provisionally
count on completion day until a final billing receipt replaces them with actual.
All proofs are trusted adapter outputs, never request JSON or enable flags.
Neither provisioning nor a digest authenticates the underlying provider data.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, localcontext
from uuid import UUID
from zoneinfo import ZoneInfo

DAILY_LIMIT_USD_MICROS = 10_000_000
_CONTRACT = "image-budget-usd-kst@2"
STRICT_POLICY = "provider_enforced_usd_ceiling@1"
ESTIMATE_POLICY = "authenticated_provider_estimate@1"
TOKEN_RATE_FORECAST_POLICY = "local_token_rate_forecast@1"
ESTIMATE_MAX_AGE = timedelta(minutes=5)
_CLIENTS = frozenset({"yellow", "babylon", "origintrail"})
_KST = ZoneInfo("Asia/Seoul")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_CEILING_KIND = "provider_enforced_usd_ceiling"


class ImageBudgetError(ValueError):
    """Fixed codes only; no provider bodies, credentials, or raw evidence."""


@dataclass(frozen=True)
class EnforcedCeilingProof:
    request_sha256: str
    provider_account_sha256: str
    capability_evidence_sha256: str
    ceiling_usd_micros: int
    evidence_sha256: str
    expires_at: datetime
    kind: str = _CEILING_KIND


@dataclass(frozen=True)
class ProviderEstimateProof:
    request_sha256: str
    provider_account_sha256: str
    estimated_usd: str | Decimal
    estimated_at: datetime
    expires_at: datetime
    evidence_sha256: str
    kind: str = "authenticated_provider_estimate"


@dataclass(frozen=True)
class TokenRateForecastProof:
    request_sha256: str
    provider_account_sha256: str
    estimated_usd: str
    estimated_at: datetime
    expires_at: datetime
    evidence_sha256: str
    policy_sha256: str
    forecast_json: str
    kind: str = "local_token_rate_forecast"


@dataclass(frozen=True)
class FinalChargeProof:
    provider_request_id: str
    actual_usd_micros: int
    charged_at: datetime
    evidence_sha256: str


@dataclass(frozen=True)
class BudgetSnapshot:
    kst_day: str
    limit_usd_micros: int
    actual_usd_micros: int
    reserved_usd_micros: int
    remaining_usd_micros: int
    capability_provisioned: bool
    capability_breached: bool
    policy_mode: str
    actual_overrun_usd_micros: int
    provisional_usd_micros: int


def usd_to_micros(value):
    """Exact, conservative USD conversion; never accept a binary float."""
    _require(type(value) in (str, Decimal), "image_budget_usd_invalid")
    if type(value) is str:
        _require(len(value) <= 100 and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value),
                 "image_budget_usd_invalid")
    try:
        amount = Decimal(value)
        _require(amount.is_finite() and 0 <= amount <= Decimal("9223372036854.775807")
                 and len(amount.as_tuple().digits) <= 100
                 and -1000 <= amount.as_tuple().exponent <= 1000,
                 "image_budget_usd_invalid")
        with localcontext() as context:
            context.prec = 110
            return int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError, OverflowError):
        raise ImageBudgetError("image_budget_usd_invalid") from None


def _require(value, code="image_budget_invalid"):
    if not value:
        raise ImageBudgetError(code)


def _hash(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, TypeError, AttributeError):
        return False


def _time(value):
    _require(type(value) is datetime and value.utcoffset() is not None)
    value = value.astimezone(timezone.utc)
    _require(value.year >= 2000)
    return value.isoformat(timespec="microseconds")


def _day(value):
    _time(value)
    return value.astimezone(_KST).date().isoformat()


def _transaction(db):
    _require(isinstance(db, sqlite3.Connection) and db.in_transaction,
             "image_budget_transaction_required")


def _row(db, query, params=()):
    cursor = db.execute(query, params)
    value = cursor.fetchone()
    return dict(zip((column[0] for column in cursor.description), value)) if value else None


def provision_image_budget(db, *, workspace_id, provider_account_sha256,
                           capability_evidence_sha256=None, policy_mode=STRICT_POLICY):
    """Explicit offline provisioning; no reset/migration/capability toggle.

    Default strict mode without capability evidence is closed. Quote/forecast
    modes must be selected explicitly and cannot carry ceiling evidence.
    Synthetic proofs belong only in disposable tests. Existing tables reject.
    Any prior image attempt requires separate liability reconciliation; never
    initialize a zero ledger over paid, completed, or unknown historical work.
    """
    _transaction(db)
    _require(_uuid(workspace_id) and _hash(provider_account_sha256)
             and (capability_evidence_sha256 is None or _hash(capability_evidence_sha256)))
    _require(policy_mode in (STRICT_POLICY, ESTIMATE_POLICY, TOKEN_RATE_FORECAST_POLICY)
             and (policy_mode == STRICT_POLICY or capability_evidence_sha256 is None),
             "image_budget_policy_invalid")
    _require(db.execute("SELECT 1 FROM sqlite_master WHERE name IN "
                        "('image_budget_policy','image_budget_reservations')").fetchone() is None,
             "image_budget_already_provisioned")
    legacy = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='higgsfield_initial_banners'").fetchone()
    _require(legacy is None or db.execute("SELECT 1 FROM higgsfield_initial_banners LIMIT 1").fetchone() is None,
             "image_budget_prior_liability_reconciliation_required")
    db.execute("""CREATE TABLE image_budget_policy (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), contract TEXT NOT NULL,
        workspace_id TEXT NOT NULL, provider_account_sha256 TEXT NOT NULL,
        capability_evidence_sha256 TEXT, policy_mode TEXT NOT NULL)""")
    db.execute("INSERT INTO image_budget_policy VALUES (1,?,?,?,?,?)",
               (_CONTRACT, workspace_id, provider_account_sha256, capability_evidence_sha256,
                policy_mode))
    db.execute("""CREATE TABLE image_budget_reservations (
        operation_id TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL,
        workspace_id TEXT NOT NULL, client_id TEXT NOT NULL,
        source_item_id TEXT NOT NULL, provider_account_sha256 TEXT NOT NULL,
        reserved_at TEXT NOT NULL, kst_day TEXT NOT NULL,
        ceiling_usd_micros INTEGER CHECK(ceiling_usd_micros BETWEEN 1 AND 10000000),
        ceiling_evidence_sha256 TEXT, provider_request_id TEXT UNIQUE,
        actual_usd_micros INTEGER CHECK(actual_usd_micros>=0),
        charged_at TEXT, charged_kst_day TEXT, final_evidence_sha256 TEXT,
        estimated_usd_micros INTEGER CHECK(estimated_usd_micros BETWEEN 1 AND 10000000),
        estimate_evidence_sha256 TEXT, estimated_at TEXT, estimate_expires_at TEXT,
        completed_at TEXT, completed_kst_day TEXT, completed_png_sha256 TEXT,
        CHECK ((ceiling_usd_micros IS NULL) != (estimated_usd_micros IS NULL)),
        UNIQUE(workspace_id,client_id,source_item_id))""")
    if policy_mode == TOKEN_RATE_FORECAST_POLICY:
        db.execute("""CREATE TABLE image_budget_forecast_evidence (
            operation_id TEXT PRIMARY KEY, policy_sha256 TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL, forecast_json TEXT NOT NULL)""")


def _policy(db, workspace_id, provider_account_sha256, *, lock=False):
    _require(_uuid(workspace_id) and _hash(provider_account_sha256))
    try:
        if lock:
            _transaction(db)
            # Also serializes callers which began a deferred transaction.
            db.execute("UPDATE image_budget_policy SET contract=contract WHERE singleton=1")
        row = _row(db, "SELECT * FROM image_budget_policy WHERE singleton=1")
        _require(row is not None and row["contract"] == _CONTRACT,
                 "image_budget_store_unavailable")
        _require(row.get("policy_mode") in (STRICT_POLICY, ESTIMATE_POLICY, TOKEN_RATE_FORECAST_POLICY),
                 "image_budget_store_unavailable")
        _require(row["workspace_id"] == workspace_id
                 and row["provider_account_sha256"] == provider_account_sha256,
                 "image_budget_binding_conflict")
        return row
    except sqlite3.Error:
        raise ImageBudgetError("image_budget_store_unavailable") from None


def budget_snapshot(db, *, workspace_id, provider_account_sha256, now):
    """Actual charge-day, provisional completion-day, and pending liability.

    Completion is not billing. Estimate mode provisionally books the estimate
    on its immutable completion day; only still-pending estimates carry across
    days. Strict ceilings remain reserved until genuine final billing.
    """
    policy = _policy(db, workspace_id, provider_account_sha256)
    day = _day(now)
    try:
        row = _row(db, """SELECT
            coalesce(sum(CASE WHEN charged_kst_day=? THEN actual_usd_micros ELSE 0 END),0) AS actual,
            coalesce(sum(CASE WHEN actual_usd_micros IS NULL AND
                (? OR completed_at IS NULL) THEN
                coalesce(ceiling_usd_micros,estimated_usd_micros) ELSE 0 END),0) AS reserved,
            coalesce(sum(CASE WHEN actual_usd_micros IS NULL AND completed_kst_day=?
                THEN estimated_usd_micros ELSE 0 END),0) AS provisional,
            coalesce(max(CASE WHEN actual_usd_micros>ceiling_usd_micros THEN 1 ELSE 0 END),0) AS breached
            FROM image_budget_reservations""", (day, policy["policy_mode"] == STRICT_POLICY, day))
    except sqlite3.Error:
        raise ImageBudgetError("image_budget_store_unavailable") from None
    return BudgetSnapshot(day, DAILY_LIMIT_USD_MICROS, row["actual"], row["reserved"],
        max(0, DAILY_LIMIT_USD_MICROS - row["actual"] - row["reserved"] - row["provisional"]),
        policy["policy_mode"] == STRICT_POLICY and _hash(policy["capability_evidence_sha256"]),
        policy["policy_mode"] == STRICT_POLICY and bool(row["breached"]), policy["policy_mode"],
        max(0, row["actual"] - DAILY_LIMIT_USD_MICROS), row["provisional"])


def _identity(operation_id, request_sha256, client_id, source_item_id):
    _require(_uuid(operation_id) and _hash(request_sha256)
             and client_id in _CLIENTS and _uuid(source_item_id))


def _reservation(db, operation_id, request_sha256, workspace_id, client_id,
                 source_item_id, provider_account_sha256):
    _identity(operation_id, request_sha256, client_id, source_item_id)
    _policy(db, workspace_id, provider_account_sha256, lock=True)
    row = _row(db, "SELECT * FROM image_budget_reservations WHERE operation_id=?", (operation_id,))
    _require(row is not None and all(row[key] == value for key, value in (
        ("request_sha256", request_sha256), ("workspace_id", workspace_id),
        ("client_id", client_id), ("source_item_id", source_item_id),
        ("provider_account_sha256", provider_account_sha256))), "image_budget_binding_conflict")
    return row


def reserve_image_budget(db, *, operation_id, request_sha256, workspace_id,
                         client_id, source_item_id, provider_account_sha256,
                         now, ceiling_proof=None, estimate_proof=None, forecast_proof=None):
    """True only for a new reservation, never permission before caller COMMIT."""
    _identity(operation_id, request_sha256, client_id, source_item_id)
    policy = _policy(db, workspace_id, provider_account_sha256, lock=True)
    instant, day = _time(now), _day(now)
    if db.execute("SELECT 1 FROM image_budget_reservations WHERE operation_id=?", (operation_id,)).fetchone():
        _reservation(db, operation_id, request_sha256, workspace_id, client_id,
                     source_item_id, provider_account_sha256)
        return False
    _require(db.execute("SELECT 1 FROM image_budget_reservations WHERE workspace_id=? "
        "AND client_id=? AND source_item_id=?", (workspace_id, client_id, source_item_id)).fetchone() is None,
        "image_budget_already_reserved")
    forecast_mode = policy["policy_mode"] == TOKEN_RATE_FORECAST_POLICY
    estimate_mode = policy["policy_mode"] in (ESTIMATE_POLICY, TOKEN_RATE_FORECAST_POLICY)
    proof = forecast_proof if forecast_mode else (estimate_proof if estimate_mode else ceiling_proof)
    if estimate_mode:
        if forecast_mode:
            _require(ceiling_proof is None and estimate_proof is None
                     and type(proof) is TokenRateForecastProof, "image_budget_evidence_required")
            from core.automation.higgsfield_token_forecast import validate_forecast_proof
            try:
                validate_forecast_proof(proof)
            except ValueError:
                raise ImageBudgetError("image_budget_evidence_required") from None
        else:
            _require(ceiling_proof is None and forecast_proof is None
                     and type(proof) is ProviderEstimateProof
                     and proof.kind == "authenticated_provider_estimate", "image_budget_evidence_required")
        issued, expiry = _time(proof.estimated_at), _time(proof.expires_at)
        _require(issued <= instant < expiry
                 and proof.expires_at.astimezone(timezone.utc)
                    - proof.estimated_at.astimezone(timezone.utc) <= ESTIMATE_MAX_AGE,
                 "image_budget_evidence_expired")
        amount = usd_to_micros(proof.estimated_usd)
    else:
        _require(estimate_proof is None and forecast_proof is None and _hash(policy["capability_evidence_sha256"])
                 and type(proof) is EnforcedCeilingProof and proof.kind == _CEILING_KIND,
                 "image_budget_evidence_required")
        _require(proof.capability_evidence_sha256 == policy["capability_evidence_sha256"],
                 "image_budget_binding_conflict")
        _require(_time(proof.expires_at) > instant, "image_budget_evidence_expired")
        amount = proof.ceiling_usd_micros
    _require(proof.request_sha256 == request_sha256
             and proof.provider_account_sha256 == provider_account_sha256
             and _hash(proof.evidence_sha256), "image_budget_binding_conflict")
    _require(type(amount) is int and amount > 0)
    _require(amount <= DAILY_LIMIT_USD_MICROS, "image_budget_daily_limit")
    summary = budget_snapshot(db, workspace_id=workspace_id,
                              provider_account_sha256=provider_account_sha256, now=now)
    _require(not summary.capability_breached, "image_budget_capability_breached")
    _require(amount <= summary.remaining_usd_micros, "image_budget_daily_limit")
    db.execute("INSERT INTO image_budget_reservations "
        "(operation_id,request_sha256,workspace_id,client_id,source_item_id,provider_account_sha256,"
        "reserved_at,kst_day,ceiling_usd_micros,ceiling_evidence_sha256,estimated_usd_micros,"
        "estimate_evidence_sha256,estimated_at,estimate_expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (operation_id, request_sha256, workspace_id, client_id, source_item_id,
         provider_account_sha256, instant, day, None if estimate_mode else amount,
         None if estimate_mode else proof.evidence_sha256, amount if estimate_mode else None,
         proof.evidence_sha256 if estimate_mode else None,
         issued if estimate_mode else None, expiry if estimate_mode else None))
    if forecast_mode:
        db.execute("INSERT INTO image_budget_forecast_evidence VALUES (?,?,?,?)",
            (operation_id, proof.policy_sha256, proof.evidence_sha256, proof.forecast_json))
    return True


def bind_image_budget_request(db, *, operation_id, request_sha256, workspace_id,
                              client_id, source_item_id, provider_account_sha256,
                              provider_request_id):
    """Bind the known POST receipt atomically with the caller's submit receipt."""
    _require(_uuid(provider_request_id))
    row = _reservation(db, operation_id, request_sha256, workspace_id, client_id,
                       source_item_id, provider_account_sha256)
    if row["provider_request_id"] is not None:
        _require(row["provider_request_id"] == provider_request_id, "image_budget_binding_conflict")
        return
    _require(db.execute("SELECT 1 FROM image_budget_reservations WHERE provider_request_id=?",
                        (provider_request_id,)).fetchone() is None, "image_budget_binding_conflict")
    db.execute("UPDATE image_budget_reservations SET provider_request_id=? WHERE operation_id=?",
               (provider_request_id, operation_id))


def mark_image_budget_completed(db, *, operation_id, request_sha256, workspace_id,
                                client_id, source_item_id, provider_account_sha256,
                                provider_request_id, png_sha256, now):
    """Pair with verified PNG persistence; completion is NOT final billing.

    Exact replays preserve the first completion day, even after midnight.
    The caller verifies canonical PNG bytes before providing this SHA-256.
    Strict-mode ceilings remain fully reserved until a final charge receipt.
    """
    row = _reservation(db, operation_id, request_sha256, workspace_id, client_id,
                       source_item_id, provider_account_sha256)
    _require(_uuid(provider_request_id) and row["provider_request_id"] == provider_request_id
             and _hash(png_sha256), "image_budget_binding_conflict")
    instant = _time(now)
    _require(instant >= row["reserved_at"], "image_budget_completion_time_invalid")
    if row["completed_at"] is not None:
        _require(row["completed_png_sha256"] == png_sha256, "image_budget_completion_conflict")
        _require(instant >= row["completed_at"], "image_budget_completion_time_invalid")
        return
    db.execute("UPDATE image_budget_reservations SET completed_at=?,completed_kst_day=?,"
               "completed_png_sha256=? WHERE operation_id=?", (instant, _day(now), png_sha256, operation_id))


def settle_image_budget(db, *, operation_id, request_sha256, workspace_id,
                        client_id, source_item_id, provider_account_sha256,
                        now, final_charge):
    """Record exact final billing, not output completion. Never reopen a POST.

    Strict ceiling breaches close later reservations across days. Estimate
    differences are accepted; actual plus outstanding estimates controls the
    charge day, not a lifetime halt. Zero never reopens the immutable attempt.
    """
    row = _reservation(db, operation_id, request_sha256, workspace_id, client_id,
                       source_item_id, provider_account_sha256)
    proof = final_charge
    _require(type(proof) is FinalChargeProof and _uuid(proof.provider_request_id)
             and proof.provider_request_id == row["provider_request_id"]
             and _hash(proof.evidence_sha256), "image_budget_binding_conflict")
    _require(type(proof.actual_usd_micros) is int and 0 <= proof.actual_usd_micros < 2**63)
    charged_at, instant = _time(proof.charged_at), _time(now)
    _require(row["reserved_at"] <= charged_at <= instant, "image_budget_charge_time_invalid")
    final = (proof.actual_usd_micros, charged_at, _day(proof.charged_at), proof.evidence_sha256)
    if row["actual_usd_micros"] is not None:
        _require(tuple(row[key] for key in ("actual_usd_micros", "charged_at", "charged_kst_day",
                                          "final_evidence_sha256")) == final,
                 "image_budget_settlement_conflict")
        return
    db.execute("UPDATE image_budget_reservations SET actual_usd_micros=?,charged_at=?,"
               "charged_kst_day=?,final_evidence_sha256=? WHERE operation_id=?", final + (operation_id,))
