"""Offline, exact-version operator for an existing quality budget store.

No HTTP, provider, Telegram, initialization, startup hook, or retry path exists.
The CLI paths are fixed to the production image and already mounted volume;
Python dependencies may be injected only by local tests. A successful apply is
one transaction and does not reopen any historical job or delivery owner.
"""
from __future__ import annotations

import argparse
from contextvars import ContextVar
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from uuid import UUID

from core.automation.quality_news_card import (
    _existing_store, service_from_env, TokenRateForecastAuthority,
)
from core.automation.higgsfield_token_forecast import FORECAST_POLICY_SHA256
from core.automation.trial_generation_gate import read_image_stamp
from core.content_ops.image_budget import ESTIMATE_POLICY, TOKEN_RATE_FORECAST_POLICY, budget_snapshot
from core.content_ops.token_forecast_transition import (
    _state, apply_token_forecast_transition, plan_token_forecast_transition,
)

MOUNT_ROOT = Path("/data/coineasy-quality")
CONTRACT = "quality-forecast-operator@1"
OFF_FLAGS = (
    "QUALITY_BANNER_ENABLED", "NEWS_CARD_IDEMPOTENCY_ENABLED",
    "YBO_TRIAL_API_MODE", "YBO_TRIAL_CONTROLLER_ENABLED",
    "YBO_TRIAL_COURIER_ENABLED", "YBO_TRIAL_COURIER_MODE",
    "TELEGRAM_PUBLICATION_ENABLED", "CONTENT_OPS_REVIEW_ENABLED",
    "CONTENT_OPS_BUTTON_CARD_ENABLED", "CONTENT_OPS_DAILY_BUTTON_CARD_ENABLED",
    "TYPEFULLY_DAILY_ENABLED", "TYPEFULLY_DRAFT_ENABLED",
)
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_RELEASE = re.compile(r"[a-f0-9]{40}\Z")
_ZERO_IO = {"network_calls": 0, "provider_calls": 0, "private_send_attempts": 0,
            "public_send_attempts": 0}
_DENY_NETWORK = ContextVar("quality_forecast_operator_no_network", default=False)
_AUDIT_INSTALLED = False


class ForecastOperatorError(ValueError):
    """Only static error codes may leave this operator."""


def _network_audit(event, args):
    if _DENY_NETWORK.get() and (event.startswith("socket.")
                               or event.startswith("http.client.")
                               or event == "urllib.Request"):
        raise ForecastOperatorError("forecast_network_forbidden")


def _need(ok, code):
    if not ok:
        raise ForecastOperatorError(code)


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (TypeError, ValueError, AttributeError):
        return False


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _private(path, *, directory=False, device=None):
    info = path.lstat()
    _need(path.is_absolute() and path.resolve() == path
          and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0
          and (stat.S_ISDIR(info.st_mode) if directory else
               stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
          and (device is None or info.st_dev == device), "forecast_private_store_required")
    return info


def _guard(env, expected_sha, mount_root, mount_checker, stamp_reader):
    _need(type(expected_sha) is str and _RELEASE.fullmatch(expected_sha)
          and env.get("RAILWAY_GIT_COMMIT_SHA") == expected_sha
          and stamp_reader() == expected_sha, "forecast_release_mismatch")
    _need(all(env.get(key, "false") == "false" for key in OFF_FLAGS),
          "forecast_all_functions_must_be_off")
    workspace = env.get("CONTENT_STUDIO_WORKSPACE_ID")
    store = env.get("QUALITY_BANNER_STORE_ID")
    account = env.get("QUALITY_BANNER_PROVIDER_ACCOUNT_SHA256")
    _need(_uuid(workspace) and _uuid(store) and type(account) is str and _SHA.fullmatch(account)
          and env.get("AUTOMATION_WORKSPACE_ID", workspace) == workspace,
          "forecast_identity_binding_required")
    root = mount_root / "journal"
    _need(mount_root.is_absolute() and mount_root.resolve() == mount_root
          and mount_root.is_dir() and mount_checker(mount_root)
          and env.get("NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT") == str(root),
          "forecast_real_volume_mount_required")
    device = mount_root.stat().st_dev
    _private(root, directory=True, device=device)
    _existing_store(root, workspace, store)
    _private(root / "quality-news.sqlite3", device=device)
    _private(root / "news-card-requests.sqlite3", device=device)
    marker = root / "provision-receipt.json"
    _private(marker, device=device)
    binding = json.loads(marker.read_text())
    _need(type(binding) is dict and set(binding) == {
        "schema", "plan_sha256", "workspace_id", "store_id", "provider_account_sha256"}
        and binding["schema"] == "quality-volume-binding@1"
        and type(binding["plan_sha256"]) is str and _SHA.fullmatch(binding["plan_sha256"])
        and binding["workspace_id"] == workspace and binding["store_id"] == store
        and binding["provider_account_sha256"] == account, "forecast_identity_binding_required")
    return root, workspace, store, account, device


def _siblings(root, device):
    """Bind every untouched sibling; never open another owner with rw SQLite."""
    evidence = []
    for path in sorted(root.iterdir()):
        _private(path, device=device)
        if path.name in {"quality-news.sqlite3", "quality-news.sqlite3-wal",
                         "quality-news.sqlite3-shm", "quality-news.sqlite3-journal"}:
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        evidence.append({"name": path.name, "sha256": digest.hexdigest(),
                         "bytes": path.stat().st_size})
    return evidence


def _budget(db, workspace, account, now):
    return asdict(budget_snapshot(db, workspace_id=workspace,
                                 provider_account_sha256=account, now=now))


def _schema(db):
    return {row[1]: row for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}


def _plan(db, root, workspace, store, account, device, expected_sha, now):
    core_plan = plan_token_forecast_transition(db, workspace_id=workspace,
                                               provider_account_sha256=account)
    envelope = {"schema": CONTRACT, "release_sha": expected_sha, "store_id": store,
                "transition": asdict(core_plan), "preserved_files": _siblings(root, device)}
    return {**envelope, "plan_sha256": _digest(envelope),
            "budget": _budget(db, workspace, account, now)}


def _readback(db, root, workspace, store, account, device, expected_sha, now):
    _need(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
          "forecast_store_integrity_failed")
    budget = _budget(db, workspace, account, now)
    names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    audit = []
    if "image_budget_transition_audit" in names:
        cursor = db.execute("SELECT plan_sha256,source_policy,destination_policy,"
            "forecast_policy_sha256,pre_state_sha256,post_state_sha256,transitioned_at "
            "FROM image_budget_transition_audit WHERE workspace_id=? AND provider_account_sha256=?",
            (workspace, account))
        audit = [dict(zip((field[0] for field in cursor.description), row)) for row in cursor]
        _need(len(audit) == 1 and db.execute("SELECT count(*) FROM image_budget_transition_audit").fetchone()[0] == 1,
              "forecast_transition_audit_invalid")
        row = audit[0]
        _need(all(type(row[key]) is str and _SHA.fullmatch(row[key]) for key in (
            "plan_sha256", "forecast_policy_sha256", "pre_state_sha256", "post_state_sha256"))
            and row["source_policy"] == ESTIMATE_POLICY
            and row["destination_policy"] == TOKEN_RATE_FORECAST_POLICY
            and row["forecast_policy_sha256"] == FORECAST_POLICY_SHA256
            and type(row["transitioned_at"]) is str and len(row["transitioned_at"]) == 32,
            "forecast_transition_audit_invalid")
        try:
            stamp = datetime.fromisoformat(row["transitioned_at"])
            _need(stamp.utcoffset() is not None and stamp.astimezone(timezone.utc).isoformat(
                timespec="microseconds") == row["transitioned_at"], "forecast_transition_audit_invalid")
        except (TypeError, ValueError):
            raise ForecastOperatorError("forecast_transition_audit_invalid") from None
    _need(budget["policy_mode"] != TOKEN_RATE_FORECAST_POLICY or len(audit) == 1,
          "forecast_transition_audit_required")
    _, table_rows = _state(db)
    return {"schema": CONTRACT, "release_sha": expected_sha, "store_id": store,
            "budget": budget, "table_rows": table_rows, "transition_audit": audit,
            "preserved_files": _siblings(root, device)}


def _run(action, *, expected_sha, plan_sha256=None, environ=None, now=None,
         mount_root=MOUNT_ROOT, mount_checker=os.path.ismount, stamp_reader=read_image_stamp,
         service_factory=service_from_env):
    """Injectable local test seam; CLI cannot change mount or stamp validators."""
    _need(action in {"plan", "apply", "readback"}, "forecast_action_invalid")
    _need((action == "apply" and type(plan_sha256) is str and _SHA.fullmatch(plan_sha256))
          or (action != "apply" and plan_sha256 is None), "forecast_plan_hash_required")
    env = os.environ if environ is None else environ
    now = now or datetime.now(timezone.utc)
    _need(type(now) is datetime and now.utcoffset() is not None, "forecast_time_invalid")
    root, workspace, store, account, device = _guard(
        env, expected_sha, mount_root, mount_checker, stamp_reader)
    mode = "rw" if action == "apply" else "ro"
    # mode=rw never creates a missing database. No journal constructor is used.
    with sqlite3.connect((root / "quality-news.sqlite3").as_uri() + f"?mode={mode}",
                         uri=True, timeout=5) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("BEGIN IMMEDIATE" if action == "apply" else "BEGIN")
        if action == "plan":
            value = _plan(db, root, workspace, store, account, device, expected_sha, now)
        elif action == "readback":
            value = _readback(db, root, workspace, store, account, device, expected_sha, now)
            _need(value["budget"]["policy_mode"] == TOKEN_RATE_FORECAST_POLICY,
                  "forecast_transition_required")
            # Only this private in-memory copy is ON for constructor validation.
            # No deployment environment is changed and generate is never called.
            validation_env = dict(env)
            validation_env.update(QUALITY_BANNER_ENABLED="true", NEWS_CARD_IDEMPOTENCY_ENABLED="true")
            service = service_factory(validation_env)
            _need(type(service.budget_authority) is TokenRateForecastAuthority
                  and service.budget_authority.account_sha256 == account
                  and service.spend_policy == FORECAST_POLICY_SHA256
                  and service.release == expected_sha, "forecast_factory_validation_failed")
            value["factory_validation"] = {
                "authority": "local_token_rate_forecast", "policy_sha256": FORECAST_POLICY_SHA256,
                "runtime_environment_unchanged": True, "generation_invoked": False,
                "network_denied": True}
        else:
            before = _plan(db, root, workspace, store, account, device, expected_sha, now)
            _need(before["plan_sha256"] == plan_sha256, "forecast_plan_mismatch")
            schema_before = _schema(db)
            policy_before = db.execute("SELECT * FROM image_budget_policy").fetchall()
            receipt = apply_token_forecast_transition(db, workspace_id=workspace,
                provider_account_sha256=account,
                plan_sha256=before["transition"]["plan_sha256"], now=now)
            after = _readback(db, root, workspace, store, account, device, expected_sha, now)
            # OFF/runtime and sibling proofs are repeated immediately before commit.
            _guard(env, expected_sha, mount_root, mount_checker, stamp_reader)
            _need(before["preserved_files"] == after["preserved_files"], "forecast_preservation_failed")
            before_tables = {name: (count, digest) for name, count, digest in before["transition"]["table_rows"]}
            after_tables = {name: (count, digest) for name, count, digest in after["table_rows"]}
            _need(all(after_tables.get(name) == value for name, value in before_tables.items()
                      if name != "image_budget_policy"), "forecast_preservation_failed")
            schema_after = _schema(db)
            _need(all(schema_after.get(name) == value for name, value in schema_before.items())
                  and set(schema_after) - set(schema_before) == {
                      "image_budget_forecast_evidence", "image_budget_transition_audit"},
                  "forecast_preservation_failed")
            expected_policy = [tuple(TOKEN_RATE_FORECAST_POLICY if index == 5 else value
                                     for index, value in enumerate(row)) for row in policy_before]
            _need(db.execute("SELECT * FROM image_budget_policy").fetchall() == expected_policy
                  and db.execute("SELECT count(*) FROM image_budget_forecast_evidence").fetchone()[0] == 0
                  and {key: value for key, value in before["budget"].items() if key != "policy_mode"}
                  == {key: value for key, value in after["budget"].items() if key != "policy_mode"},
                  "forecast_preservation_failed")
            value = {**after, "plan_sha256": plan_sha256, "receipt": asdict(receipt)}
        # Context manager commits apply only after every check. Read modes made no writes.
    return {"status": "applied" if action == "apply" else action + "_ok", **value, **_ZERO_IO}


def run(action, **kwargs):
    """Network-denied wrapper; audit protection ends with this synchronous call."""
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
        result = run(args.action, expected_sha=args.release_sha, plan_sha256=args.plan_sha256)
    except ForecastOperatorError as error:
        result = {"status": "BLOCK", "code": str(error), **_ZERO_IO}
    except Exception:
        result = {"status": "BLOCK", "code": "forecast_operator_unavailable", **_ZERO_IO}
    print(json.dumps(result, sort_keys=True))
    return 1 if result["status"] == "BLOCK" else 0


if __name__ == "__main__":
    raise SystemExit(main())
