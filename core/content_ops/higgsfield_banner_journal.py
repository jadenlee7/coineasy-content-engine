"""Persistent initial-banner submit barrier; no provider, approval or publication.

All replicas must share this ONE journal on a durable private volume. A trusted
owner constructs each instance with its exact workspace/client/source. This is
not a public enqueue API and does not admit edited versions or authorize spend.
An initial source gets one attempt forever, even across KST dates or new UUIDs.
After an uncertain POST only an already recorded request ID may be polled.
There is deliberately no reset, reclaim, retry, cancellation or approval API.

``daily_limit`` is an explicit aggregate REQUEST count, not a dollar guarantee.
Provider token-based final charges cannot be capped by a local estimate. The
operator must separately authorize a budget before mounting a paid worker.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import sqlite3
import stat
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from PIL import Image

_HASH = re.compile(r"[a-f0-9]{64}\Z")
_CLIENTS = frozenset({"yellow", "babylon", "origintrail", "squid"})
_KST = ZoneInfo("Asia/Seoul")
_MAX_PNG = 10_000_000


class HiggsfieldJournalError(ValueError):
    """Fixed codes only; no source, credentials, provider body or asset echo."""


def _require(value, code="higgsfield_journal_invalid"):
    if not value:
        raise HiggsfieldJournalError(code)


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


def _png(value):
    _require(type(value) is bytes and 45 <= len(value) <= _MAX_PNG,
             "higgsfield_journal_image_invalid")
    try:
        with Image.open(io.BytesIO(value)) as image:
            _require(image.format == "PNG" and getattr(image, "n_frames", 1) == 1
                     and 2880 <= image.width == image.height <= 4096)
            image.verify()
        with Image.open(io.BytesIO(value)) as image:
            image.load()
            _require(image.convert("RGBA").getchannel("A").getextrema() == (255, 255))
    except Exception:
        raise HiggsfieldJournalError("higgsfield_journal_image_invalid") from None


class HiggsfieldBannerJournal:
    def __init__(self, path, *, workspace_id, client_id, source_item_id,
                 daily_limit, clock=time.time, require_existing=False):
        _require(_uuid(workspace_id) and _uuid(source_item_id)
                 and client_id in _CLIENTS and type(daily_limit) is int
                 and 1 <= daily_limit <= 4 and callable(clock) and type(require_existing) is bool)
        self.path = Path(path)
        _require(self.path.is_absolute() and self.path.parent.is_dir()
                 and self.path.parent.resolve() == self.path.parent
                 and self.path.parent.stat().st_uid == os.getuid()
                 and self.path.parent.stat().st_mode & 0o077 == 0,
                 "higgsfield_journal_private_volume_required")
        self.workspace_id, self.client_id = workspace_id, client_id
        self.source_item_id, self.daily_limit = source_item_id, daily_limit
        self._clock = clock
        if not require_existing:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
        self._check_path()
        with self._db() as db:
            if require_existing:
                _require(db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                    "AND name='higgsfield_initial_banners'").fetchone() is not None,
                         "higgsfield_journal_private_volume_required")
                return
            db.execute("""CREATE TABLE IF NOT EXISTS higgsfield_initial_banners (
                operation_id TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL,
                workspace_id TEXT NOT NULL, client_id TEXT NOT NULL,
                source_item_id TEXT NOT NULL, kst_day TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('submit_started','submitted','result_ready')),
                request_id TEXT UNIQUE, png BLOB, png_sha256 TEXT,
                created_at INTEGER NOT NULL,
                UNIQUE(workspace_id,client_id,source_item_id))""")

    def _check_path(self):
        info = self.path.lstat()
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                 and info.st_mode & 0o077 == 0 and info.st_nlink == 1,
                 "higgsfield_journal_private_volume_required")

    @contextmanager
    def _db(self):
        self._check_path()
        # Even after a successful lstat, SQLite must never recreate a volume
        # removed between that check and connect (e.g. a broken runtime mount).
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        except sqlite3.Error:
            raise HiggsfieldJournalError("higgsfield_journal_outcome_unknown") from None
        finally:
            db.close()

    def _identity(self, operation_id, request_sha256):
        _require(_uuid(operation_id) and type(request_sha256) is str
                 and _HASH.fullmatch(request_sha256))

    def _row(self, db, operation_id, request_sha256):
        self._identity(operation_id, request_sha256)
        row = db.execute("SELECT * FROM higgsfield_initial_banners WHERE operation_id=?",
                         (operation_id,)).fetchone()
        _require(row is not None and row["request_sha256"] == request_sha256
                 and row["workspace_id"] == self.workspace_id
                 and row["client_id"] == self.client_id
                 and row["source_item_id"] == self.source_item_id,
                 "higgsfield_journal_binding_conflict")
        return row

    def begin_submit(self, operation_id, request_sha256, *, workspace_id,
                     client_id, source_item_id):
        self._identity(operation_id, request_sha256)
        # A valid request for another client/source must not consume this
        # owner's quota or obtain submit authority under the wrong identity.
        # This is checked before opening a transaction or creating a row.
        _require(_uuid(workspace_id) and _uuid(source_item_id)
                 and client_id in _CLIENTS
                 and workspace_id == self.workspace_id
                 and client_id == self.client_id
                 and source_item_id == self.source_item_id,
                 "higgsfield_journal_binding_conflict")
        now = self._clock()
        _require(type(now) in (int, float) and 0 < now < 253402300000)
        day = datetime.fromtimestamp(now, _KST).date().isoformat()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            # Conflicts are never a fresh attempt, including across clients,
            # processes, calendar days, restarts and changed request digests.
            existing = db.execute("SELECT 1 FROM higgsfield_initial_banners WHERE operation_id=? "
                "OR (workspace_id=? AND client_id=? AND source_item_id=?) LIMIT 1",
                (operation_id, self.workspace_id, self.client_id, self.source_item_id)).fetchone()
            if existing:
                return False
            counts = db.execute("SELECT count(*),coalesce(sum(client_id=?),0) "
                "FROM higgsfield_initial_banners WHERE workspace_id=? AND kst_day=?",
                (self.client_id, self.workspace_id, day)).fetchone()
            if counts[0] >= self.daily_limit or counts[1] >= 1:
                return False
            if not self._reserve_image_budget(db, operation_id, request_sha256, now):
                return False
            db.execute("INSERT INTO higgsfield_initial_banners "
                "(operation_id,request_sha256,workspace_id,client_id,source_item_id,kst_day,state,created_at) "
                "VALUES (?,?,?,?,?,?,'submit_started',?)",
                (operation_id, request_sha256, self.workspace_id, self.client_id,
                 self.source_item_id, day, int(now)))
        # Context manager COMMIT completes before sole submit permission returns.
        return True

    def _reserve_image_budget(self, db, operation_id, request_sha256, now):
        """Extended by the strict-budget quality owner, in this transaction.

        This base journal only enforces request counts. It is not a monetary
        authority; the quality runtime must use its strict-budget subclass.
        """
        return True

    def record_submitted(self, operation_id, request_sha256, request_id):
        _require(_uuid(request_id))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, operation_id, request_sha256)
            if row["state"] in {"submitted", "result_ready"}:
                _require(row["request_id"] == request_id, "higgsfield_journal_binding_conflict")
                return
            _require(row["state"] == "submit_started" and row["request_id"] is None)
            self._bind_image_budget_request(db, operation_id, request_sha256, request_id)
            db.execute("UPDATE higgsfield_initial_banners SET state='submitted',request_id=? "
                       "WHERE operation_id=?", (request_id, operation_id))

    def _bind_image_budget_request(self, db, operation_id, request_sha256, request_id):
        """Strict-budget subclass binds billing to the sole provider request."""
        return None

    def read_request_id(self, operation_id, request_sha256):
        with self._db() as db:
            row = self._row(db, operation_id, request_sha256)
            _require(row["state"] in {"submitted", "result_ready"}
                     and _uuid(row["request_id"]), "higgsfield_journal_submit_unknown")
            return row["request_id"]

    def attempt_state(self, operation_id, request_sha256):
        """Read exact attempt state; absence is not an authorization to retry."""
        self._identity(operation_id, request_sha256)
        with self._db() as db:
            present = db.execute(
                "SELECT 1 FROM higgsfield_initial_banners WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if present is None:
                return None
            return self._row(db, operation_id, request_sha256)["state"]

    def record_result(self, operation_id, request_sha256, request_id, png):
        _require(_uuid(request_id))
        _png(png)
        digest = hashlib.sha256(png).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, operation_id, request_sha256)
            _require(row["state"] in {"submitted", "result_ready"}
                     and row["request_id"] == request_id, "higgsfield_journal_binding_conflict")
            if row["state"] == "result_ready":
                _require(row["png_sha256"] == digest and bytes(row["png"]) == png,
                         "higgsfield_journal_result_conflict")
                return
            self._mark_image_budget_completed(db, operation_id, request_sha256, request_id, digest)
            db.execute("UPDATE higgsfield_initial_banners SET state='result_ready',png=?,png_sha256=? "
                       "WHERE operation_id=?", (png, digest, operation_id))

    def _mark_image_budget_completed(self, db, operation_id, request_sha256, request_id, png_sha256):
        """Quality owner records completion separately from actual billing."""
        return None

    def read_result(self, operation_id, request_sha256):
        """Local staging only; not canonical asset admission or content approval."""
        with self._db() as db:
            row = self._row(db, operation_id, request_sha256)
            if row["state"] != "result_ready":
                return None
            png = bytes(row["png"])
            _require(hashlib.sha256(png).hexdigest() == row["png_sha256"],
                     "higgsfield_journal_result_conflict")
            _png(png)
            return png
