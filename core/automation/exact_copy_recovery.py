"""Append-only compatibility for one preserved Babylon copy checkpoint.

This is not a job retry, provider retry, lease claim or source approval. The
operator supplies a fresh, bounded DB readback; hashes bind that attestation,
not authenticate it. Runtime still requires the trusted successor trial gate
and the normal exact DB lease/source checks. Existing rows are never rewritten.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from core.automation.higgsfield_banner import BannerReference, POLICY
from core.automation.higgsfield_token_forecast import FORECAST_POLICY_SHA256
from core.automation.news_generation_journal import request_digest
from core.content_ops.image_budget import TOKEN_RATE_FORECAST_POLICY

TRIAL_ID = "d346ceea-4ea9-479f-a5cf-05097b50ce48"
OLD_TRIAL_ID = "1a2bef47-4827-4bc3-9b4d-5baf2714015d"
JOB_ID = "e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af"
REQUEST_ID = "52f95e4e-1f66-549a-baa4-32b0fdf82b30"
SOURCE_ID = "35ae7c96-993a-4453-9946-3263f4f2842a"
WORKSPACE_ID = "5cc143cc-b253-4841-8a6c-ab3ac98623a6"
CLIENT_ID = "babylon"
OLD_RELEASE = "b77a54e12dcdadefd001e4dd8cca255dead96d3c"
OLD_SPEND_POLICY = "000a4243bf92c082db1120b6ccbd6aa8e2ade95ed355aaef124f79a31c6aa0a4"
TABLE = "quality_news_exact_copy_recovery"
CONTRACT = "exact-babylon-copy-recovery@1"
IDENTITY = {"job_id": JOB_ID, "request_id": REQUEST_ID, "source_item_id": SOURCE_ID,
            "workspace_id": WORKSPACE_ID, "client_id": CLIENT_ID}
ZERO_COUNTS = ("content_items", "content_versions", "source_links", "grok_outbox",
               "review_outbox", "review_card_parts", "approvals", "publications", "duplicate_jobs")
TRUE_PROOFS = ("latest_source", "feed_active", "feed_poll_recent", "expired_lease", "slot_bound")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_RELEASE = re.compile(r"[a-f0-9]{40}\Z")


class ExactCopyRecoveryError(ValueError):
    """Static metadata-only error codes."""


def need(value, code="exact_copy_recovery_invalid"):
    if not value:
        raise ExactCopyRecoveryError(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        need(parsed.utcoffset() is not None)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ExactCopyRecoveryError("exact_copy_recovery_time_invalid") from None


def _now(now):
    need(type(now) is datetime and now.utcoffset() is not None,
         "exact_copy_recovery_time_invalid")
    return now.astimezone(timezone.utc)


def input_digest(payload, logo, release, spend_policy):
    return digest({"payload": payload, "logo": asdict(logo), "policy": POLICY,
                   "release": release, "spend_policy": spend_policy})


def _context(payload, logo, release_sha, now):
    from core.automation.quality_news_card import validate_context, _validate_prompt_input
    context = validate_context(CLIENT_ID, REQUEST_ID, payload)
    need(all(context.get(k) == v for k, v in IDENTITY.items()), "exact_copy_recovery_identity_mismatch")
    need(type(logo) is BannerReference and logo.role == "official_logo"
         and type(release_sha) is str and _RELEASE.fullmatch(release_sha)
         and release_sha != OLD_RELEASE, "exact_copy_recovery_binding_mismatch")
    _validate_prompt_input(payload)
    need(0 <= (_now(now) - _stamp(context["source_published_at"])).total_seconds() < 86400,
         "exact_copy_recovery_source_expired")
    return context


def _row(db):
    cursor = db.execute("SELECT * FROM quality_news_copy WHERE operation_id=? OR job_id=? "
        "OR (workspace_id=? AND client_id=? AND source_item_id=?)",
        (REQUEST_ID, JOB_ID, WORKSPACE_ID, CLIENT_ID, SOURCE_ID))
    keys = [x[0] for x in cursor.description]
    rows = [dict(zip(keys, row)) for row in cursor]
    need(len(rows) == 1, "exact_copy_recovery_checkpoint_required")
    row = rows[0]
    need(row["operation_id"] == REQUEST_ID and row["job_id"] == JOB_ID
         and row["workspace_id"] == WORKSPACE_ID and row["client_id"] == CLIENT_ID
         and row["source_item_id"] == SOURCE_ID, "exact_copy_recovery_identity_mismatch")
    encoded = row["checkpoint"]
    need(type(encoded) is str and len(encoded.encode()) <= 128000
         and hashlib.sha256(encoded.encode()).hexdigest() == row["checkpoint_sha256"],
         "exact_copy_recovery_checkpoint_required")
    return row


def _policy(db, account):
    need(type(account) is str and _HASH.fullmatch(account), "exact_copy_recovery_budget_mismatch")
    row = db.execute("SELECT workspace_id,provider_account_sha256,policy_mode "
                     "FROM image_budget_policy WHERE singleton=1").fetchone()
    need(row is not None and tuple(row) == (WORKSPACE_ID, account, TOKEN_RATE_FORECAST_POLICY),
         "exact_copy_recovery_budget_mismatch")


def _matching(db, table):
    return db.execute(f'SELECT * FROM "{table}" WHERE operation_id=? '
        'OR (workspace_id=? AND client_id=? AND source_item_id=?)',
        (REQUEST_ID, WORKSPACE_ID, CLIENT_ID, SOURCE_ID)).fetchall()


def no_paid_intent(db):
    need(not _matching(db, "higgsfield_initial_banners")
         and not _matching(db, "image_budget_reservations"), "exact_copy_recovery_paid_intent_exists")


def preserved_state(db):
    """Complete preexisting-schema/data digest excluding only our audit table."""
    from core.content_ops.token_forecast_transition import _field, _quote
    objects = [tuple(row) for row in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' AND name!=? ORDER BY type,name", (TABLE,))]
    tables = []
    for kind, name, _, _ in objects:
        if kind == "table":
            rows = sorted(digest([_field(value) for value in row])
                          for row in db.execute(f"SELECT * FROM {_quote(name)}"))
            tables.append([name, len(rows), digest(rows)])
    return {"sha256": digest({"schema": objects, "tables": tables}), "tables": tables}


def _db_proof(proof, context, now):
    keys = {"schema", "observed_at", "source_published_at", "source_sha256", "input_sha256", "style_pack_hash",
            "job_status", "attempts", "content_item_id", *IDENTITY, *ZERO_COUNTS, *TRUE_PROOFS}
    need(type(proof) is dict and set(proof) == keys
         and proof["schema"] == "exact-babylon-recovery-db-proof@1"
         and all(proof[k] == v for k, v in IDENTITY.items())
         and all(proof[k] is True for k in TRUE_PROOFS)
         and all(type(proof[k]) is int and proof[k] == 0 for k in ZERO_COUNTS)
         and proof["job_status"] == "running" and type(proof["attempts"]) is int
         and proof["attempts"] == 1 and proof["content_item_id"] is None
         and proof["source_published_at"] == context["source_published_at"]
         and proof["source_sha256"] == context["source_sha256"]
         and type(proof["input_sha256"]) is str and _HASH.fullmatch(proof["input_sha256"])
         and type(proof["style_pack_hash"]) is str and re.fullmatch(r"[a-f0-9]{32}", proof["style_pack_hash"]),
         "exact_copy_recovery_database_proof_invalid")
    need(0 <= (_now(now) - _stamp(proof["observed_at"])).total_seconds() <= 300,
         "exact_copy_recovery_database_proof_stale")


def _binding(db, *, payload, logo, release_sha, provider_account_sha256, now):
    context = _context(payload, logo, release_sha, now)
    row = _row(db)
    old_input = input_digest(payload, logo, OLD_RELEASE, OLD_SPEND_POLICY)
    need(row["input_sha256"] == old_input, "exact_copy_recovery_original_input_mismatch")
    _policy(db, provider_account_sha256)
    from core.automation.quality_news_card import _restore_request
    checkpoint = json.loads(row["checkpoint"])
    need(type(checkpoint) is dict and set(checkpoint) == {"request", "spec"},
         "exact_copy_recovery_checkpoint_required")
    request = _restore_request(checkpoint["request"])
    need(request.operation_id == REQUEST_ID and request.client_id == CLIENT_ID
         and request.workspace_id == WORKSPACE_ID and request.source_item_id == SOURCE_ID
         and request.source_url == context["source_url"]
         and request.source_sha256 == context["source_sha256"]
         and request.source_published_at == context["source_published_at"]
         and request.references[-1] == logo,
         "exact_copy_recovery_checkpoint_required")
    return context, row, {
        "schema": CONTRACT, "trial_id": TRIAL_ID, **IDENTITY,
        "old_release_sha": OLD_RELEASE, "new_release_sha": release_sha,
        "old_spend_policy_sha256": OLD_SPEND_POLICY,
        "new_spend_policy_sha256": FORECAST_POLICY_SHA256,
        "old_input_sha256": old_input,
        "new_input_sha256": input_digest(payload, logo, release_sha, FORECAST_POLICY_SHA256),
        "api_payload_sha256": request_digest(CLIENT_ID, payload),
        "logo_sha256": digest(asdict(logo)), "checkpoint_sha256": row["checkpoint_sha256"],
        "image_request_sha256": request.digest(),
        "provider_account_sha256": provider_account_sha256,
    }


def plan_recovery(db, *, payload, logo, release_sha, provider_account_sha256,
                  local_evidence, database_proof, now):
    context, _, binding = _binding(db, payload=payload, logo=logo, release_sha=release_sha,
        provider_account_sha256=provider_account_sha256, now=now)
    need(db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone() is None,
         "exact_copy_recovery_already_prepared")
    no_paid_intent(db)
    _db_proof(database_proof, context, now)
    need(type(local_evidence) is dict and local_evidence.get("all_old_owners_terminal") is True
         and local_evidence.get("api_payload_sha256") == binding["api_payload_sha256"]
         and type(local_evidence.get("preserved_files")) is list,
         "exact_copy_recovery_local_proof_invalid")
    plan = {**binding, "database_proof_sha256": digest(database_proof),
            "input_sha256": database_proof["input_sha256"], "style_pack_hash": database_proof["style_pack_hash"],
            "db_observed_at": database_proof["observed_at"],
            "local_evidence": local_evidence, "preserved_state": preserved_state(db)}
    return {**plan, "plan_sha256": digest(plan)}


def apply_recovery(db, *, plan_sha256, now, **kwargs):
    need(db.in_transaction, "exact_copy_recovery_transaction_required")
    plan = plan_recovery(db, now=now, **kwargs)
    need(plan["plan_sha256"] == plan_sha256, "exact_copy_recovery_plan_mismatch")
    receipt = {**plan, "prepared_at": _now(now).isoformat(timespec="microseconds")}
    db.execute("SAVEPOINT exact_copy_recovery")
    try:
        db.execute(f"CREATE TABLE {TABLE} (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                   "receipt_json TEXT NOT NULL, receipt_sha256 TEXT NOT NULL)")
        db.execute(f"INSERT INTO {TABLE} VALUES (1,?,?)", (canonical(receipt), digest(receipt)))
        need(preserved_state(db) == plan["preserved_state"], "exact_copy_recovery_preservation_failed")
        db.execute("RELEASE exact_copy_recovery")
    except Exception:
        db.execute("ROLLBACK TO exact_copy_recovery")
        db.execute("RELEASE exact_copy_recovery")
        raise
    return {"receipt_sha256": digest(receipt), "receipt": receipt}


def read_receipt(db):
    try:
        rows = db.execute(f"SELECT singleton,receipt_json,receipt_sha256 FROM {TABLE}").fetchall()
        need(len(rows) == 1 and rows[0][0] == 1, "exact_copy_recovery_receipt_required")
        receipt = json.loads(rows[0][1])
        need(digest(receipt) == rows[0][2], "exact_copy_recovery_receipt_mismatch")
        plan = {key: value for key, value in receipt.items() if key not in {"plan_sha256", "prepared_at"}}
        need(digest(plan) == receipt["plan_sha256"], "exact_copy_recovery_receipt_mismatch")
        return {"receipt": receipt, "receipt_sha256": rows[0][2]}
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        raise ExactCopyRecoveryError("exact_copy_recovery_receipt_required") from None


def authorize_checkpoint(db, *, payload, logo, release_sha, spend_policy_sha256,
                         provider_account_sha256, trial_authority, output_root, now):
    from core.automation.trial_generation_gate import TrialGenerationGate
    need(type(trial_authority) is TrialGenerationGate
         and trial_authority.plan.trial_id == TRIAL_ID
         and trial_authority.plan.payload["release_sha"] == release_sha
         and spend_policy_sha256 == FORECAST_POLICY_SHA256,
         "exact_copy_recovery_trial_required")
    paths = [row[2] for row in db.execute("PRAGMA database_list") if row[1] == "main"]
    need(len(paths) == 1 and bool(paths[0])
         and trial_authority.owner.path == Path(paths[0]).parent / f"{TRIAL_ID}.trial-owner.sqlite3",
         "exact_copy_recovery_trial_required")
    trial_authority.require_generation(trial_id=TRIAL_ID, client_id=CLIENT_ID,
                                       request_id=REQUEST_ID, payload=payload)
    _, _, binding = _binding(db, payload=payload, logo=logo, release_sha=release_sha,
        provider_account_sha256=provider_account_sha256, now=now)
    receipt = read_receipt(db)["receipt"]
    need(all(receipt.get(key) == value for key, value in binding.items()),
         "exact_copy_recovery_receipt_mismatch")
    need(str(output_root) == receipt["local_evidence"]["output_root"],
         "exact_copy_recovery_receipt_mismatch")
    # A compatible copy is never new image retry authority. An ambiguous first
    # POST remains held; a known submitted request can only follow the existing
    # journal's GET-only collect path. Absence repeats the atomic admission gate.
    banners = _matching(db, "higgsfield_initial_banners")
    reservations = _matching(db, "image_budget_reservations")
    if not banners:
        need(not reservations, "exact_copy_recovery_paid_intent_exists")
    else:
        cursor = db.execute("SELECT operation_id,request_sha256,state,request_id "
            "FROM higgsfield_initial_banners WHERE operation_id=?", (REQUEST_ID,))
        banner = cursor.fetchone()
        reservation = db.execute("SELECT request_sha256,provider_account_sha256,provider_request_id "
            "FROM image_budget_reservations WHERE operation_id=?", (REQUEST_ID,)).fetchone()
        need(len(banners) == len(reservations) == 1 and banner is not None
             and banner[0] == REQUEST_ID and banner[1] == binding["image_request_sha256"]
             and banner[2] in {"submitted", "result_ready"} and banner[3] is not None,
             "exact_copy_recovery_paid_intent_exists")
        need(reservation is not None and tuple(reservation) == (
            binding["image_request_sha256"], provider_account_sha256, banner[3]),
            "exact_copy_recovery_paid_intent_exists")
    return receipt["new_input_sha256"]
