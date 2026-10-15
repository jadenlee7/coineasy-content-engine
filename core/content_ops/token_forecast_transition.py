"""Explicit, same-store quote-to-local-forecast transition; never startup code.

Planning is read-only. Applying requires a caller-owned SQLite transaction and
an exact, freshly recomputed plan SHA. The caller must use BEGIN IMMEDIATE and
commit only after checking the returned audit; this module never commits,
resets attempts, invokes a provider, or rewrites historical liability.

Hashes are drift detectors, not authentication. They deliberately contain no
source text, copy, PNG bytes, credentials, or account IDs in their payloads.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from core.automation.higgsfield_token_forecast import FORECAST_POLICY_SHA256
from core.content_ops.image_budget import ESTIMATE_POLICY, TOKEN_RATE_FORECAST_POLICY

_CONTRACT = "image-budget-usd-kst@2"
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_AUDIT = "image_budget_transition_audit"
_EVIDENCE = "image_budget_forecast_evidence"
_REQUIRED = {
    "image_budget_policy": {"singleton", "contract", "workspace_id", "provider_account_sha256",
                            "capability_evidence_sha256", "policy_mode"},
    "image_budget_reservations": {"operation_id", "request_sha256", "workspace_id",
                                  "client_id", "source_item_id", "provider_account_sha256",
                                  "estimated_usd_micros", "ceiling_usd_micros",
                                  "actual_usd_micros", "completed_at"},
    "quality_news_copy": {"operation_id", "job_id", "workspace_id", "client_id",
                          "source_item_id", "checkpoint", "checkpoint_sha256"},
    "higgsfield_initial_banners": {"operation_id", "request_sha256", "workspace_id",
                                   "client_id", "source_item_id", "state", "request_id", "png"},
}


class TokenForecastTransitionError(ValueError):
    """Fixed codes only; no stored content or provider data in errors."""


@dataclass(frozen=True)
class TokenForecastTransitionPlan:
    workspace_id: str
    provider_account_sha256: str
    source_policy: str
    destination_policy: str
    forecast_policy_sha256: str
    pre_state_sha256: str
    table_rows: tuple[tuple[str, int, str], ...]
    plan_sha256: str


@dataclass(frozen=True)
class TokenForecastTransitionReceipt:
    plan_sha256: str
    pre_state_sha256: str
    post_state_sha256: str
    forecast_policy_sha256: str
    transitioned_at: str


def _require(value, code="token_forecast_transition_invalid"):
    if not value:
        raise TokenForecastTransitionError(code)


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (TypeError, ValueError, AttributeError):
        return False


def _quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _field(value):
    # Snapshot proofs never contain raw private TEXT or BLOB values.
    if value is None:
        return ["null"]
    if type(value) is bytes:
        return ["blob", len(value), _hash(value)]
    if type(value) is str:
        encoded = value.encode("utf-8")
        return ["text", len(encoded), _hash(encoded)]
    if type(value) is int:
        return ["integer", value]
    if type(value) is float:
        return ["real", repr(value)]
    raise TokenForecastTransitionError("token_forecast_transition_store_unavailable")


def _state(db):
    """Hash every non-internal schema object and table row, order independent."""
    objects = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                         "WHERE name NOT LIKE 'sqlite_%' AND name!=? ORDER BY type,name",
                         (_AUDIT,)).fetchall()
    tables = [row[1] for row in objects if row[0] == "table"]
    digests = []
    for name in tables:
        columns = [row[1] for row in db.execute(f"PRAGMA table_info({_quote(name)})")]
        _require(bool(columns), "token_forecast_transition_store_unavailable")
        row_digests = []
        for row in db.execute(f"SELECT * FROM {_quote(name)}"):
            row_digests.append(_hash(_json([_field(value) for value in row])))
        row_digests.sort()
        digests.append((name, len(row_digests), _hash(_json({"columns": columns,
                                                          "rows": row_digests}))))
    schema = [(kind, name, table, _hash((sql or "").encode("utf-8")))
              for kind, name, table, sql in objects]
    return _hash(_json({"schema": schema, "tables": digests})), tuple(sorted(digests))


def _validate_source(db, workspace_id, provider_account_sha256):
    _require(_uuid(workspace_id) and type(provider_account_sha256) is str
             and _SHA.fullmatch(provider_account_sha256), "token_forecast_transition_binding_conflict")
    names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    _require(set(_REQUIRED) <= names and _EVIDENCE not in names and _AUDIT not in names,
             "token_forecast_transition_store_unavailable")
    for table, required_columns in _REQUIRED.items():
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({_quote(table)})")}
        _require(required_columns <= columns, "token_forecast_transition_store_unavailable")
    rows = db.execute("SELECT singleton,contract,workspace_id,provider_account_sha256,"
                      "capability_evidence_sha256,policy_mode FROM image_budget_policy").fetchall()
    _require(len(rows) == 1 and rows[0][0] == 1 and rows[0][1] == _CONTRACT
             and rows[0][5] == ESTIMATE_POLICY and rows[0][4] is None,
             "token_forecast_transition_source_policy_required")
    _require(rows[0][2] == workspace_id and rows[0][3] == provider_account_sha256,
             "token_forecast_transition_binding_conflict")
    _require(db.execute("SELECT 1 FROM image_budget_reservations "
                        "WHERE estimated_usd_micros IS NULL OR ceiling_usd_micros IS NOT NULL "
                        "LIMIT 1").fetchone() is None,
             "token_forecast_transition_prior_liability_reconciliation_required")
    for table in ("image_budget_reservations", "quality_news_copy", "higgsfield_initial_banners"):
        _require(db.execute(f"SELECT 1 FROM {_quote(table)} WHERE workspace_id!=? LIMIT 1",
                            (workspace_id,)).fetchone() is None,
                 "token_forecast_transition_binding_conflict")
    _require(db.execute("SELECT 1 FROM image_budget_reservations "
                        "WHERE provider_account_sha256!=? LIMIT 1",
                        (provider_account_sha256,)).fetchone() is None,
             "token_forecast_transition_binding_conflict")
    # A paid submit without its ledger reservation is a reconciliation problem,
    # not something a policy transition may silently erase or absorb.
    _require(db.execute("SELECT 1 FROM higgsfield_initial_banners h LEFT JOIN "
                        "image_budget_reservations b ON b.operation_id=h.operation_id "
                        "WHERE b.operation_id IS NULL OR b.request_sha256!=h.request_sha256 "
                        "LIMIT 1").fetchone() is None,
             "token_forecast_transition_prior_liability_reconciliation_required")


def plan_token_forecast_transition(db, *, workspace_id, provider_account_sha256):
    """Return a sanitized read-only plan; never create or alter any table."""
    _require(isinstance(db, sqlite3.Connection), "token_forecast_transition_store_unavailable")
    try:
        _validate_source(db, workspace_id, provider_account_sha256)
        state_sha, table_rows = _state(db)
        payload = {"contract": "image-budget-token-forecast-transition@1",
                   "workspace_id": workspace_id,
                   "provider_account_sha256": provider_account_sha256,
                   "source_policy": ESTIMATE_POLICY,
                   "destination_policy": TOKEN_RATE_FORECAST_POLICY,
                   "forecast_policy_sha256": FORECAST_POLICY_SHA256,
                   "pre_state_sha256": state_sha}
        return TokenForecastTransitionPlan(workspace_id, provider_account_sha256,
            ESTIMATE_POLICY, TOKEN_RATE_FORECAST_POLICY, FORECAST_POLICY_SHA256,
            state_sha, table_rows, _hash(_json(payload)))
    except sqlite3.Error:
        raise TokenForecastTransitionError("token_forecast_transition_store_unavailable") from None


def apply_token_forecast_transition(db, *, workspace_id, provider_account_sha256,
                                    plan_sha256, now):
    """Apply once inside caller's transaction; caller owns COMMIT/ROLLBACK."""
    _require(isinstance(db, sqlite3.Connection) and db.in_transaction,
             "token_forecast_transition_transaction_required")
    _require(type(plan_sha256) is str and _SHA.fullmatch(plan_sha256),
             "token_forecast_transition_plan_mismatch")
    _require(type(now) is datetime and now.utcoffset() is not None,
             "token_forecast_transition_invalid")
    current = plan_token_forecast_transition(db, workspace_id=workspace_id,
                                             provider_account_sha256=provider_account_sha256)
    _require(current.plan_sha256 == plan_sha256,
             "token_forecast_transition_plan_mismatch")
    db.execute("SAVEPOINT token_forecast_transition")
    try:
        updated = db.execute("UPDATE image_budget_policy SET policy_mode=? "
            "WHERE singleton=1 AND contract=? AND workspace_id=? "
            "AND provider_account_sha256=? AND policy_mode=?",
            (TOKEN_RATE_FORECAST_POLICY, _CONTRACT, workspace_id,
             provider_account_sha256, ESTIMATE_POLICY))
        _require(updated.rowcount == 1, "token_forecast_transition_plan_mismatch")
        db.execute("""CREATE TABLE image_budget_forecast_evidence (
            operation_id TEXT PRIMARY KEY, policy_sha256 TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL, forecast_json TEXT NOT NULL)""")
        post_sha, _ = _state(db)
        stamp = now.astimezone(timezone.utc).isoformat(timespec="microseconds")
        db.execute("""CREATE TABLE image_budget_transition_audit (
            plan_sha256 TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
            provider_account_sha256 TEXT NOT NULL, source_policy TEXT NOT NULL,
            destination_policy TEXT NOT NULL, forecast_policy_sha256 TEXT NOT NULL,
            pre_state_sha256 TEXT NOT NULL, post_state_sha256 TEXT NOT NULL,
            transitioned_at TEXT NOT NULL)""")
        db.execute("INSERT INTO image_budget_transition_audit VALUES (?,?,?,?,?,?,?,?,?)",
            (plan_sha256, workspace_id, provider_account_sha256, ESTIMATE_POLICY,
             TOKEN_RATE_FORECAST_POLICY, FORECAST_POLICY_SHA256,
             current.pre_state_sha256, post_sha, stamp))
        db.execute("RELEASE SAVEPOINT token_forecast_transition")
        return TokenForecastTransitionReceipt(plan_sha256, current.pre_state_sha256,
                                               post_sha, FORECAST_POLICY_SHA256, stamp)
    except Exception as error:
        db.execute("ROLLBACK TO SAVEPOINT token_forecast_transition")
        db.execute("RELEASE SAVEPOINT token_forecast_transition")
        if isinstance(error, TokenForecastTransitionError):
            raise
        raise TokenForecastTransitionError("token_forecast_transition_store_unavailable") from None
