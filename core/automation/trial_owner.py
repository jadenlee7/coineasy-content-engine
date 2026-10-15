"""Local-only durable admission prototype; no production adapters or network.

This is NOT the image budget journal and must use its own named SQLite file.
Construction and admit() before the fixed gate perform zero filesystem I/O.
After admission, only this owner file is touched, including when closing for
clock rollback/expiry. A committed start never yields a second admission, even
after a crash. Consequently recovery favors at-most-once over availability.

`now` is a trusted UTC-aware host-clock observation, not request/user input.
Handlers must call is_active immediately before every external side effect;
reservation alone cannot prevent a handler from ignoring a later deadline.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
from uuid import UUID

from core.content_ops.trial_window import (
    ELIGIBLE_CLIENTS, FRESH_TRIAL_ID, KST, TRIAL_ID, trial_clients,
    trial_window as _trial_window,
)

CLIENTS = ("yellow", "babylon", "origintrail")
POLICY_SHA256 = "2f71160c21ebb2b40d000ccf6cf6d36553c0039766416d52e89d3ab0af653982"
LIMIT_MICROS = 10_000_000
PHASES = ("intake", "generate", "review", "send")
TERMINAL = frozenset({"completed", "blocked", "unknown", "aborted", "expired", "clock_rollback"})
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_APP_ID = 0x59424F31
_KEYS = frozenset({"contract", "trial_id", "not_before", "deadline", "kst_date",
    "client_ids", "max_per_client", "max_total", "estimated_daily_limit_micros",
    "estimate_policy_sha256", "release_sha"})


class TrialError(ValueError):
    """A bounded, non-secret error; callers must stop, not retry a handler."""


def _stamp(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TrialError("trial_clock_invalid")
    value = value.astimezone(timezone.utc)
    return value, int(value.timestamp() * 1_000_000)


def _iso(value):
    if type(value) is not str or not value.endswith("Z"):
        raise TrialError("trial_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise TrialError("trial_timestamp_invalid") from None
    if parsed.isoformat(timespec="seconds").replace("+00:00", "Z") != value:
        raise TrialError("trial_timestamp_invalid")
    return parsed


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value or UUID(value).int == 0:
            raise ValueError
    except (ValueError, AttributeError):
        raise TrialError("trial_id_invalid") from None
    return value


def trial_window(not_before, deadline):
    """Pure bounds validation; the operator fixes these once after OFF readiness.

    Runtime never derives a replacement start from its clock or writes the DB
    anchor. The fixed UUID, exact plan hash, and durable owner reject replans.
    """
    try:
        return _trial_window(not_before, deadline)
    except (ValueError, TypeError, OverflowError):
        raise TrialError("trial_plan_scope_invalid") from None


def _digest(value):
    if type(value) is not str or not _HEX.fullmatch(value):
        raise TrialError("trial_digest_invalid")
    return value


def canonical_sha256(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class TrialPlan:
    payload_json: str = field(repr=False)
    sha256: str

    @classmethod
    def from_mapping(cls, payload, expected_sha256):
        _digest(expected_sha256)
        if type(payload) is not dict or set(payload) != _KEYS:
            raise TrialError("trial_plan_shape_invalid")
        _uuid(payload["trial_id"])
        start, _ = trial_window(payload["not_before"], payload["deadline"])
        try:
            clients = trial_clients(payload["trial_id"])
        except ValueError:
            raise TrialError("trial_plan_scope_invalid") from None
        if (payload["contract"] != "ybo-server-trial@1"
            or payload["kst_date"] != start.astimezone(KST).date().isoformat()
            or (payload["trial_id"] == FRESH_TRIAL_ID
                and (start < datetime(2026, 10, 15, 15, tzinfo=timezone.utc)
                     or start.astimezone(KST).date() != date(2026, 10, 16)))
            or payload["client_ids"] != list(clients)
            or any(type(payload[key]) is not int for key in
                   ("max_per_client", "max_total", "estimated_daily_limit_micros"))
            or payload["max_per_client"] != 1 or payload["max_total"] != len(clients)
            or payload["estimated_daily_limit_micros"] != LIMIT_MICROS
            or payload["estimate_policy_sha256"] != POLICY_SHA256
            or type(payload["release_sha"]) is not str
            or re.fullmatch(r"[a-f0-9]{40}", payload["release_sha"]) is None):
            raise TrialError("trial_plan_scope_invalid")
        if not secrets.compare_digest(canonical_sha256(payload), expected_sha256):
            raise TrialError("trial_plan_hash_mismatch")
        return cls(json.dumps(payload, sort_keys=True, separators=(",", ":")), expected_sha256)

    @property
    def payload(self):
        return json.loads(self.payload_json)

    @property
    def plan_sha256(self):
        return self.sha256

    @property
    def trial_id(self):
        return self.payload["trial_id"]

    @property
    def eligible_clients(self):
        return trial_clients(self.trial_id)

    @property
    def not_before(self):
        return _iso(self.payload["not_before"])

    @property
    def kst_date(self):
        return date.fromisoformat(self.payload["kst_date"])

    @property
    def deadline(self):
        return _iso(self.payload["deadline"])


@dataclass(frozen=True)
class Admission:
    trial_id: str
    plan_sha256: str
    nonce: str = field(repr=False)


class TrialOwner:
    def __init__(self, db_path, plan, *, clock=None):
        if type(plan) is not TrialPlan:
            raise TrialError("trial_plan_invalid")
        self.plan = TrialPlan.from_mapping(plan.payload, plan.sha256)
        if clock is not None and not callable(clock):
            raise TrialError("trial_clock_invalid")
        self.clock = clock
        self.path = Path(db_path)
        if self.path.name != f"{plan.trial_id}.trial-owner.sqlite3":
            raise TrialError("trial_separate_owner_path_required")

    @contextmanager
    def _transaction(self, *, create=False):
        # No remote I/O. Refuse a symlink and non-owner database instead of
        # repurposing an existing budget store. Parent directory must exist.
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            if not create:
                yield None
                return
        else:
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or info.st_mode & 0o077):
                raise TrialError("trial_owner_path_invalid")
        conn = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            app_id = conn.execute("PRAGMA application_id").fetchone()[0]
            if not tables and app_id == 0 and create:
                conn.execute(f"PRAGMA application_id={_APP_ID}")
                conn.execute("CREATE TABLE trial_owner (singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
                    "plan_sha TEXT NOT NULL, plan_json TEXT NOT NULL, nonce TEXT NOT NULL,"
                    "status TEXT NOT NULL, started_us INTEGER NOT NULL, last_seen_us INTEGER NOT NULL)")
                conn.execute("CREATE TABLE trial_clients (client TEXT PRIMARY KEY, identifiers TEXT)")
                conn.execute("CREATE TABLE trial_phases (phase_key TEXT PRIMARY KEY, client TEXT NOT NULL,"
                    "payload_sha TEXT NOT NULL, estimate_micros INTEGER NOT NULL, state TEXT NOT NULL,"
                    "receipt_sha TEXT, reserved_us INTEGER NOT NULL, finished_us INTEGER)")
            elif tables != {"trial_owner", "trial_clients", "trial_phases"} or app_id != _APP_ID:
                raise TrialError("trial_owner_database_mismatch")
            os.chmod(self.path, 0o600)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _row(self, conn):
        row = conn.execute("SELECT * FROM trial_owner WHERE singleton=1").fetchone()
        if row and (row["plan_sha"] != self.plan.sha256 or row["plan_json"] != self.plan.payload_json):
            raise TrialError("trial_owner_plan_mismatch")
        return row

    def _active(self, conn, row, now, admission=None):
        if row is None:
            return False
        if admission is not None and (type(admission) is not Admission
            or admission.trial_id != self.plan.trial_id
            or admission.plan_sha256 != self.plan.sha256
            or not secrets.compare_digest(admission.nonce, row["nonce"])):
            return False
        # A request timestamp may predate a concurrent owner transaction or a
        # lock wait. Production supplies a trusted clock sampled AFTER locking.
        current, micros = _stamp(self.clock() if self.clock is not None else now)
        if row["status"] != "active":
            return False
        state = ("clock_rollback" if micros < row["last_seen_us"] else
                 "expired" if current >= self.plan.deadline else
                 "clock_rollback" if current < self.plan.not_before else "active")
        conn.execute("UPDATE trial_owner SET status=?, last_seen_us=? WHERE singleton=1",
                     (state, max(micros, row["last_seen_us"])))
        return state == "active"

    def admit(self, trial_id, now):
        current, micros = _stamp(now)
        if trial_id != self.plan.trial_id:
            raise TrialError("trial_id_mismatch")
        # Absolutely no filesystem operation in this early branch.
        if current < self.plan.not_before:
            return None
        if current >= self.plan.deadline:
            with self._transaction() as conn:
                if conn is not None:
                    self._active(conn, self._row(conn), now)
            return None
        admission = None
        with self._transaction(create=True) as conn:
            current, micros = _stamp(self.clock() if self.clock is not None else now)
            if not self.plan.not_before <= current < self.plan.deadline:
                self._active(conn, self._row(conn), current)
                return None
            row = self._row(conn)
            if row is not None:
                self._active(conn, row, now)
            else:
                nonce = secrets.token_hex(32)
                conn.execute("INSERT INTO trial_owner VALUES (1,?,?,?,?,?,?)",
                    (self.plan.sha256, self.plan.payload_json, nonce, "active", micros, micros))
                admission = Admission(self.plan.trial_id, self.plan.sha256, nonce)
        # COMMIT has completed before any admission is returned to a handler.
        return admission

    def is_active(self, admission, now):
        _stamp(now)
        with self._transaction() as conn:
            return bool(conn is not None and self._active(conn, self._row(conn), now, admission))

    permit = is_active

    def delivery_authorized(self, entries, now):
        """Read exact completed generation/review and reserved delivery phases.

        This never creates admission, identifiers or send permission. It is
        used by the authenticated manifest reader before every gateway effect.
        """
        _stamp(now)
        if type(entries) is not list or not 1 <= len(entries) <= len(self.plan.eligible_clients):
            return False
        with self._transaction() as conn:
            if conn is None or not self._active(conn, self._row(conn), now):
                return False
            for entry in entries:
                client_id = entry.get("client_id")
                if client_id not in self.plan.eligible_clients:
                    return False
                ids = {k: entry.get(k) for k in ("job_id", "request_id", "source_item_id")}
                identity_json = json.dumps(ids, sort_keys=True, separators=(",", ":"))
                bound = conn.execute("SELECT identifiers FROM trial_clients WHERE client=?",
                                     (client_id,)).fetchone()
                if bound is None or bound[0] != identity_json:
                    return False
                artifact = {k: entry.get(k) for k in (
                    "client_id", "request_id", "source_item_id", "source_sha256",
                    "content_item_id", "content_version_id", "banner_sha256",
                    "telegram_copy_sha256", "x_copy_sha256")}
                artifact["status"] = "needs_review"
                artifact_sha = canonical_sha256(artifact)
                phases = {r["phase_key"]: r for r in conn.execute(
                    "SELECT phase_key,state,payload_sha,receipt_sha FROM trial_phases WHERE client=?",
                    (client_id,))}
                generation = phases.get(f"{client_id}:generate")
                review = phases.get(f"{client_id}:review")
                send = phases.get(f"{client_id}:send")
                if (generation is None or generation["state"] != "complete"
                        or generation["receipt_sha"] != artifact_sha
                        or review is None or review["state"] != "complete"
                        or review["payload_sha"] != artifact_sha
                        or send is None or send["state"] not in {"reserved", "complete"}
                        or send["payload_sha"] != artifact_sha):
                    return False
            return True

    def generation_authorized(self, client_id, identifiers, immutable_sha256, now):
        """Read an already reserved exact generation phase; never admit via HTTP.

        The trusted hosted orchestrator must have committed intake and reserved
        this phase with the complete API request digest. A caller's UUID/header
        cannot create or alter that authority. This is deliberately valid only
        while the phase is reserved and the shared owner remains active.
        """
        _stamp(now)
        _digest(immutable_sha256)
        if client_id not in self.plan.eligible_clients or type(identifiers) is not dict or set(identifiers) != {
                "job_id", "request_id", "source_item_id"}:
            return False
        try:
            for value in identifiers.values():
                _uuid(value)
        except TrialError:
            return False
        identity_json = json.dumps(identifiers, sort_keys=True, separators=(",", ":"))
        with self._transaction() as conn:
            if conn is None or not self._active(conn, self._row(conn), now):
                return False
            client = conn.execute("SELECT identifiers FROM trial_clients WHERE client=?", (client_id,)).fetchone()
            intake = conn.execute("SELECT state FROM trial_phases WHERE phase_key=?",
                                  (f"{client_id}:intake",)).fetchone()
            phase = conn.execute("SELECT client,payload_sha,state FROM trial_phases WHERE phase_key=?",
                                 (f"{client_id}:generate",)).fetchone()
            return bool(client is not None and client[0] == identity_json
                        and intake is not None and intake[0] == "complete"
                        and phase is not None and phase[0] == client_id
                        and phase[1] == immutable_sha256 and phase[2] == "reserved")

    def confirm_external_delivery(self, entries, content_version_id, receipt_sha256, now):
        """Close-only acknowledgment from the trusted, authenticated gateway.

        The gateway has already verified its existing four-part DB terminal
        receipt. This cannot admit, reserve, restart, or grant a send. It can
        only complete a pre-existing exact send and turn the entire trial OFF.
        """
        _digest(receipt_sha256)
        if not self.delivery_authorized(entries, now):
            return False
        selected = [e for e in entries if e["content_version_id"] == content_version_id]
        if len(selected) != 1:
            return False
        key = selected[0]["client_id"] + ":send"
        with self._transaction() as conn:
            if conn is None or not self._active(conn, self._row(conn), now):
                return False
            phase = conn.execute("SELECT state,receipt_sha FROM trial_phases WHERE phase_key=?", (key,)).fetchone()
            if phase is None or phase["state"] not in {"reserved", "complete"}:
                return False
            if phase["state"] == "complete" and phase["receipt_sha"] != receipt_sha256:
                conn.execute("UPDATE trial_owner SET status='blocked' WHERE singleton=1")
                return False
            conn.execute("UPDATE trial_phases SET state='complete',receipt_sha=?,finished_us=? WHERE phase_key=?",
                         (receipt_sha256, _stamp(now)[1], key))
            # The immutable manifest lists all generated and reviewed entries.
            # No HTTP caller can append entries or reserve another client.
            complete = all(conn.execute("SELECT state FROM trial_phases WHERE phase_key=?",
                (e["client_id"] + ":send",)).fetchone()[0] == "complete" for e in entries)
            if complete:
                conn.execute("UPDATE trial_owner SET status='completed' WHERE singleton=1")
            return True

    def reserve_phase(self, admission, phase_key, client_id, immutable_sha256, now,
                      identifiers=None, estimated_micros=0):
        _stamp(now)
        _digest(immutable_sha256)
        if (client_id not in self.plan.eligible_clients or phase_key not in {f"{client_id}:{p}" for p in PHASES}
            or type(estimated_micros) is not int or not 0 <= estimated_micros <= LIMIT_MICROS
            or (estimated_micros and phase_key != f"{client_id}:generate")):
            raise TrialError("trial_phase_invalid")
        identity_json = None
        if identifiers is not None:
            if type(identifiers) is not dict or set(identifiers) != {"job_id", "request_id", "source_item_id"}:
                raise TrialError("trial_identifiers_invalid")
            for value in identifiers.values():
                _uuid(value)
            identity_json = json.dumps(identifiers, sort_keys=True, separators=(",", ":"))
        result = False
        with self._transaction() as conn:
            if conn is None or not self._active(conn, self._row(conn), now, admission):
                return False
            existing = conn.execute("SELECT * FROM trial_phases WHERE phase_key=?", (phase_key,)).fetchone()
            if existing:
                return False  # never turn a retry or crash into permission to rerun
            index = PHASES.index(phase_key.split(":")[1])
            if index:
                previous = conn.execute("SELECT state FROM trial_phases WHERE phase_key=?",
                    (f"{client_id}:{PHASES[index-1]}",)).fetchone()
                if previous is None or previous[0] != "complete":
                    conn.execute("UPDATE trial_owner SET status='blocked' WHERE singleton=1")
                    return False
            client = conn.execute("SELECT identifiers FROM trial_clients WHERE client=?", (client_id,)).fetchone()
            bound = client[0] if client else None
            if bound is not None and identity_json is not None and bound != identity_json:
                conn.execute("UPDATE trial_owner SET status='blocked' WHERE singleton=1")
                return False
            if phase_key != f"{client_id}:intake" and bound is None and identity_json is None:
                conn.execute("UPDATE trial_owner SET status='blocked' WHERE singleton=1")
                return False
            if identity_json is not None:
                # Exact IDs cannot silently move between clients in this trial.
                for other in conn.execute("SELECT identifiers FROM trial_clients WHERE client<>? AND identifiers IS NOT NULL", (client_id,)):
                    if set(json.loads(other[0]).values()) & set(identifiers.values()):
                        conn.execute("UPDATE trial_owner SET status='blocked' WHERE singleton=1")
                        return False
            total = conn.execute("SELECT coalesce(sum(estimate_micros),0) FROM trial_phases").fetchone()[0]
            if total + estimated_micros > LIMIT_MICROS:
                conn.execute("UPDATE trial_owner SET status='blocked' WHERE singleton=1")
                return False
            # This optional sum is only an additional plan guard. A zero value
            # does NOT waive the existing external $10 budget reservation.
            conn.execute("INSERT INTO trial_clients VALUES (?,?) ON CONFLICT(client) DO UPDATE SET identifiers=coalesce(trial_clients.identifiers,excluded.identifiers)",
                         (client_id, identity_json))
            conn.execute("INSERT INTO trial_phases VALUES (?,?,?,?, 'reserved',NULL,?,NULL)",
                         (phase_key, client_id, immutable_sha256, estimated_micros, _stamp(now)[1]))
            result = True
        return result

    def finish_phase(self, admission, phase_key, receipt_sha256, now):
        _stamp(now)
        _digest(receipt_sha256)
        with self._transaction() as conn:
            if conn is None or not self._active(conn, self._row(conn), now, admission):
                return False
            row = conn.execute("SELECT * FROM trial_phases WHERE phase_key=?", (phase_key,)).fetchone()
            if row is None:
                return False
            if row["state"] == "complete":
                if row["receipt_sha"] == receipt_sha256:
                    return True
                conn.execute("UPDATE trial_owner SET status='blocked' WHERE singleton=1")
                return False
            conn.execute("UPDATE trial_phases SET state='complete',receipt_sha=?,finished_us=? WHERE phase_key=?",
                         (receipt_sha256, _stamp(now)[1], phase_key))
            return True

    def terminate(self, admission, status, now):
        _stamp(now)
        if status not in TERMINAL - {"expired", "clock_rollback"}:
            raise TrialError("trial_terminal_invalid")
        with self._transaction() as conn:
            if conn is None or not self._active(conn, self._row(conn), now, admission):
                return False
            conn.execute("UPDATE trial_owner SET status=? WHERE singleton=1", (status,))
            return True

    def inspect(self, now):
        """Sanitized local owner state, never the admission nonce or ID bodies."""
        current, _ = _stamp(now)
        if current < self.plan.not_before:
            return {"status": "not_due", "effective_on": False, "phases": 0}
        with self._transaction() as conn:
            if conn is None:
                return {"status": "not_started", "effective_on": False, "phases": 0}
            row = self._row(conn)
            active = self._active(conn, row, now)
            row = self._row(conn)
            return {"status": row["status"] if row else "not_started", "effective_on": active,
                    "phases": conn.execute("SELECT count(*) FROM trial_phases").fetchone()[0]}
