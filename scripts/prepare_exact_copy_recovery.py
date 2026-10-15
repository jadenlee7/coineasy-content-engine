"""OFF-only, network-denied registration of one existing Babylon checkpoint.

Private original payload and a fresh DB readback arrive on stdin, never argv or
stdout. Plan/apply use the same mounted DB; apply appends one compatibility row
and preserves every historical table and sibling byte. No lease, paid call,
provider poll, job reset, trial enablement or Telegram action is performed.
"""
from __future__ import annotations

import argparse
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import NAMESPACE_URL, uuid5

from core.automation.exact_copy_recovery import (
    CLIENT_ID, CONTRACT, IDENTITY, JOB_ID, OLD_RELEASE, OLD_TRIAL_ID, REQUEST_ID,
    TRIAL_ID, WORKSPACE_ID, ExactCopyRecoveryError, apply_recovery, digest, need,
    plan_recovery, preserved_state, read_receipt,
)
from core.automation.higgsfield_banner import BannerReference
from core.automation.higgsfield_token_forecast import FORECAST_POLICY_SHA256
from core.automation.news_generation_journal import NewsGenerationJournal, request_digest
from core.automation.trial_generation_gate import read_image_stamp
from core.content_ops.image_budget import TOKEN_RATE_FORECAST_POLICY
from scripts.transition_quality_forecast import MOUNT_ROOT, _guard, _private, _siblings

_ZERO_IO = {"network_calls": 0, "provider_calls": 0, "private_send_attempts": 0,
            "public_send_attempts": 0, "copy_calls": 0, "historical_rows_modified": 0}
_DENY_NETWORK = ContextVar("exact_copy_recovery_no_network", default=False)
_AUDIT_INSTALLED = False


def _network_audit(event, args):
    if _DENY_NETWORK.get() and (event.startswith("socket.") or event.startswith("http.client.")
                               or event == "urllib.Request"):
        raise ExactCopyRecoveryError("exact_copy_recovery_network_forbidden")


def _ro(path):
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    db.execute("PRAGMA trusted_schema=OFF")
    return db


def _local_evidence(root, device, payload, output_root):
    expected_payload = request_digest(CLIENT_ID, payload)
    # The existing API idempotency pin is independent of release/spend policy.
    # Preserve its complete payload and output owner exactly; do not repin it.
    with _ro(root / "news-card-requests.sqlite3") as db:
        NewsGenerationJournal._validate_schema(db)
        rows = db.execute("SELECT mode,client_id,request_sha256,output_root "
                          "FROM news_generation_modes WHERE request_id=?", (REQUEST_ID,)).fetchall()
        need(rows == [("quality", CLIENT_ID, expected_payload, output_root)]
             and db.execute("SELECT 1 FROM news_generation_requests WHERE request_id=?", (REQUEST_ID,)).fetchone() is None,
             "exact_copy_recovery_news_pin_mismatch")
    owners = []
    saw_original = False
    for path in sorted(root.glob("*.trial-owner.sqlite3")):
        _private(path, device=device)
        # Registration must precede successor activation/admission. Never
        # prepare a copy exception while any new owner could already generate.
        need(path.name != f"{TRIAL_ID}.trial-owner.sqlite3", "exact_copy_recovery_successor_already_exists")
        with _ro(path) as db:
            from core.automation.trial_owner import _APP_ID
            need(db.execute("PRAGMA application_id").fetchone()[0] == _APP_ID
                 and {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                     "AND name NOT LIKE 'sqlite_%'")} == {"trial_owner", "trial_clients", "trial_phases"},
                 "exact_copy_recovery_old_owner_mismatch")
            rows = db.execute("SELECT plan_json,status,plan_sha FROM trial_owner").fetchall()
            if not rows:
                need(db.execute("SELECT count(*) FROM trial_phases").fetchone()[0] == 0,
                     "exact_copy_recovery_old_owner_not_terminal")
                owners.append({"name": path.name, "status": "never_admitted"})
                continue
            need(len(rows) == 1 and rows[0][1] in {
                "completed", "blocked", "unknown", "aborted", "expired", "clock_rollback"},
                "exact_copy_recovery_old_owner_not_terminal")
            plan = json.loads(rows[0][0])
            need(path.name == f"{plan['trial_id']}.trial-owner.sqlite3" and rows[0][2] == digest(plan),
                 "exact_copy_recovery_old_owner_not_terminal")
            owners.append({"name": path.name, "status": rows[0][1], "plan_sha256": digest(plan)})
            if plan["trial_id"] == OLD_TRIAL_ID:
                saw_original = True
                need(plan["release_sha"] == OLD_RELEASE, "exact_copy_recovery_old_owner_mismatch")
                ids = db.execute("SELECT identifiers FROM trial_clients WHERE client=?", (CLIENT_ID,)).fetchone()
                need(ids is not None and json.loads(ids[0]) == {key: IDENTITY[key]
                     for key in ("job_id", "request_id", "source_item_id")}, "exact_copy_recovery_old_owner_mismatch")
                phases = db.execute("SELECT phase_key,payload_sha,state FROM trial_phases WHERE client=?", (CLIENT_ID,)).fetchall()
                generation = [row for row in phases if row[0] == f"{CLIENT_ID}:generate"]
                need(len(generation) == 1 and generation[0][1:] == (expected_payload, "reserved")
                     and not any(row[0] in {f"{CLIENT_ID}:review", f"{CLIENT_ID}:send"} for row in phases),
                     "exact_copy_recovery_old_owner_mismatch")
    need(saw_original, "exact_copy_recovery_original_owner_required")
    return {"api_payload_sha256": expected_payload, "output_root": output_root,
            "all_old_owners_terminal": True, "old_owners": owners,
            "preserved_files": _siblings(root, device)}


def _registration(receipt, receipt_sha256):
    """SHA-only contract for the separately authorized exact DB handoff RPC."""
    return {"receipt_id": str(uuid5(NAMESPACE_URL, CONTRACT + ":" + receipt_sha256)),
        **IDENTITY, "trial_id": TRIAL_ID,
        "old_release_sha": receipt["old_release_sha"], "new_release_sha": receipt["new_release_sha"],
        "old_spend_policy_sha256": receipt["old_spend_policy_sha256"],
        "new_spend_policy_sha256": receipt["new_spend_policy_sha256"],
        "old_copy_input_sha256": receipt["old_input_sha256"],
        "new_copy_input_sha256": receipt["new_input_sha256"],
        "checkpoint_sha256": receipt["checkpoint_sha256"],
        "api_payload_sha256": receipt["api_payload_sha256"],
        "input_sha256": receipt["input_sha256"], "style_pack_hash": receipt["style_pack_hash"],
        "db_proof_sha256": receipt["database_proof_sha256"],
        "local_proof_sha256": receipt_sha256,
        "provider_account_sha256": receipt["provider_account_sha256"],
        "observed_at": receipt["db_observed_at"]}


def _run(action, *, expected_sha, bundle=None, plan_sha256=None, environ=None, now=None,
         mount_root=MOUNT_ROOT, mount_checker=os.path.ismount, stamp_reader=read_image_stamp):
    need(action in {"plan", "apply", "readback"}, "exact_copy_recovery_action_invalid")
    need((action == "apply" and type(plan_sha256) is str and len(plan_sha256) == 64)
         or (action != "apply" and plan_sha256 is None), "exact_copy_recovery_plan_required")
    env = os.environ if environ is None else environ
    now = now or datetime.now(timezone.utc)
    root, workspace, store, account, device = _guard(env, expected_sha, mount_root, mount_checker, stamp_reader)
    need(workspace == WORKSPACE_ID and env.get("QUALITY_BANNER_RELEASE_SHA") == expected_sha
         and env.get("QUALITY_BANNER_BUDGET_MODE") == TOKEN_RATE_FORECAST_POLICY
         and env.get("QUALITY_BANNER_SPEND_POLICY_SHA256") == FORECAST_POLICY_SHA256,
         "exact_copy_recovery_configuration_mismatch")
    kwargs = None
    if action != "readback":
        need(type(bundle) is dict and set(bundle) == {"payload", "database_proof"},
             "exact_copy_recovery_bundle_invalid")
        logos = json.loads(env["QUALITY_BANNER_LOGO_REFERENCES"])
        logo = BannerReference("official_logo", **logos[CLIENT_ID])
        output_root = str(Path(env.get("OUTPUT_ROOT", "/tmp/content_engine_output")).resolve())
        local = _local_evidence(root, device, bundle["payload"], output_root)
        kwargs = dict(payload=bundle["payload"], logo=logo, release_sha=expected_sha,
            provider_account_sha256=account, local_evidence=local,
            database_proof=bundle["database_proof"], now=now)
    mode = "rw" if action == "apply" else "ro"
    with sqlite3.connect((root / "quality-news.sqlite3").as_uri() + f"?mode={mode}", uri=True, timeout=5) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("BEGIN IMMEDIATE" if action == "apply" else "BEGIN")
        if action == "plan":
            result = {"plan": plan_recovery(db, **kwargs)}
        elif action == "apply":
            result = apply_recovery(db, plan_sha256=plan_sha256, **kwargs)
            _guard(env, expected_sha, mount_root, mount_checker, stamp_reader)
            need(_local_evidence(root, device, bundle["payload"], output_root) == local,
                 "exact_copy_recovery_preservation_failed")
            result["database_registration"] = _registration(result["receipt"], result["receipt_sha256"])
        else:
            result = read_receipt(db)
            receipt = result["receipt"]
            need(receipt["new_release_sha"] == expected_sha
                 and receipt["provider_account_sha256"] == account
                 and receipt["preserved_state"] == preserved_state(db)
                 and receipt["local_evidence"]["preserved_files"] == _siblings(root, device),
                 "exact_copy_recovery_readback_mismatch")
            result["database_registration"] = _registration(receipt, result["receipt_sha256"])
    return {"status": action + "_ok", **result, **_ZERO_IO}


def run(action, **kwargs):
    global _AUDIT_INSTALLED
    if not _AUDIT_INSTALLED:
        sys.addaudithook(_network_audit)
        _AUDIT_INSTALLED = True
    token = _DENY_NETWORK.set(True)
    try:
        return _run(action, **kwargs)
    finally:
        _DENY_NETWORK.reset(token)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply", "readback"))
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--plan-sha256")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        bundle = None
        if args.action != "readback":
            raw = sys.stdin.buffer.read(1_000_001)
            need(0 < len(raw) <= 1_000_000, "exact_copy_recovery_bundle_invalid")
            bundle = json.loads(raw)
        result = run(args.action, expected_sha=args.release_sha,
                     plan_sha256=args.plan_sha256, bundle=bundle)
    except ExactCopyRecoveryError as error:
        result = {"status": "BLOCK", "code": str(error), **_ZERO_IO}
    except Exception:
        result = {"status": "BLOCK", "code": "exact_copy_recovery_unavailable", **_ZERO_IO}
    print(json.dumps(result, sort_keys=True))
    return 1 if result["status"] == "BLOCK" else 0


if __name__ == "__main__":
    raise SystemExit(main())
