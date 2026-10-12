"""Private durable request barrier before any news-card generator invocation.

Every replica must share the SAME persistent private root and output volume.
This is request idempotency, not spending authority or source/daily deduplication.
Started requests are never reclaimed: errors, cancellation and lost responses
remain held until separately reconciled. No reset/retry/delete API is provided.
Both renderer modes pin their UUID, client and full payload here before any
paid work. Quality may resume its own GET-only ledger, never switch to legacy.
Stable output directories deliberately do not match legacy retention grammar;
future garbage collection must reconcile journal state before deleting assets.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

_CLIENT = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_MAX_JSON = 1_000_000
_MAX_ASSET = 32_000_000
_RESPONSE_FIELDS = frozenset({"client_id", "content_type", "spec", "png_path",
    "template_style", "requested_template_style", "source_image_used",
    "source_image_url", "source_image_sha256", "source_visual_path",
    "figma_template", "manifest_path", "duration_ms"})


class NewsGenerationJournalError(ValueError):
    """Fixed metadata-only error codes; never echo request/provider content."""


def canonical_request_id(value):
    try:
        if type(value) is str and str(UUID(value)) == value and UUID(value).int:
            return value
    except (ValueError, AttributeError):
        pass
    raise NewsGenerationJournalError("news_generation_request_id_invalid")


def _json(value):
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=True,
                          separators=(",", ":"), allow_nan=False)
        if len(text.encode("ascii")) <= _MAX_JSON:
            return text
    except (TypeError, ValueError, OverflowError, RecursionError):
        pass
    raise NewsGenerationJournalError("news_generation_payload_invalid")


def request_digest(client_id, payload):
    if type(client_id) is not str or not _CLIENT.fullmatch(client_id) or type(payload) is not dict:
        raise NewsGenerationJournalError("news_generation_payload_invalid")
    return hashlib.sha256(_json({"client_id": client_id, "request": payload}).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class NewsGenerationReservation:
    request_id: str
    client_id: str
    request_sha256: str
    output_dir: Path
    response: dict | None = field(default=None, repr=False)
    owner_token: str | None = field(default=None, repr=False)


class NewsGenerationJournal:
    def __init__(self, root, *, output_root, initialize=False):
        # Provisioning is a separate explicit operator step, never API startup.
        # Losing a volume must not silently create a fresh paid-attempt ledger.
        if type(initialize) is not bool:
            raise NewsGenerationJournalError("news_generation_journal_unavailable")
        self.root = Path(root)
        self.path = self.root / "news-card-requests.sqlite3"
        self._private_root()
        self.output_root = self._safe_output_root(output_root)
        created = False
        if initialize:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            except OSError:
                raise NewsGenerationJournalError("news_generation_journal_unavailable") from None
            else:
                os.close(fd)
                created = True
        with self._db() as db:
            if created:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""CREATE TABLE news_generation_requests (
                    request_id TEXT PRIMARY KEY, client_id TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL, output_dir TEXT NOT NULL,
                    owner_token TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('started','succeeded')),
                    response_json TEXT, assets_json TEXT, created_at INTEGER NOT NULL)""")
                db.execute("""CREATE TABLE news_generation_modes (
                    request_id TEXT PRIMARY KEY, mode TEXT NOT NULL CHECK(mode IN ('legacy','quality')),
                    client_id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                    output_root TEXT NOT NULL, created_at INTEGER NOT NULL)""")
                db.execute("CREATE TABLE news_generation_schema (version INTEGER PRIMARY KEY CHECK(version=2))")
                db.execute("INSERT INTO news_generation_schema VALUES (2)")
            # Existing v1 stores cannot prove historical quality mode admission.
            # Never upgrade/backfill them at HTTP time, even with initialize=True.
            self._validate_schema(db)

    @staticmethod
    def _validate_schema(db):
        if ([row[0] for row in db.execute("SELECT version FROM news_generation_schema")] != [2]
                or {row[1] for row in db.execute("PRAGMA table_info(news_generation_requests)")}
                != {"request_id", "client_id", "request_sha256", "output_dir", "owner_token",
                    "state", "response_json", "assets_json", "created_at"}
                or {row[1] for row in db.execute("PRAGMA table_info(news_generation_modes)")}
                != {"request_id", "mode", "client_id", "request_sha256", "output_root", "created_at"}):
            raise NewsGenerationJournalError("news_generation_journal_unavailable")

    def _private_root(self):
        try:
            info = self.root.lstat()
            valid = (self.root.is_absolute() and self.root.resolve() == self.root
                     and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                     and info.st_mode & 0o077 == 0)
        except (OSError, ValueError):
            valid = False
        if not valid:
            raise NewsGenerationJournalError("news_generation_private_volume_required")

    @contextmanager
    def _db(self):
        self._private_root()
        try:
            info = self.path.lstat()
            if not (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_mode & 0o077 == 0 and info.st_nlink == 1):
                raise NewsGenerationJournalError("news_generation_private_volume_required")
            db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)
        except (sqlite3.Error, OSError):
            raise NewsGenerationJournalError("news_generation_journal_unavailable") from None
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        except sqlite3.Error:
            raise NewsGenerationJournalError("news_generation_outcome_unknown") from None
        finally:
            db.close()

    def _safe_output_root(self, output_root):
        root = Path(output_root)
        if (not root.is_absolute() or root.resolve() != root
                or root == self.root or self.root.is_relative_to(root)
                or root.is_relative_to(self.root)):
            raise NewsGenerationJournalError("news_generation_output_root_invalid")
        return root

    def _output_dir(self, client_id, request_id):
        root = self._safe_output_root(self.output_root)
        path = root / client_id / ("news_request_" + request_id)
        if path.resolve() != path:
            raise NewsGenerationJournalError("news_generation_output_root_invalid")
        return path

    def _pin_mode(self, db, request_id, client_id, digest, mode):
        self._validate_schema(db)
        if type(mode) is not str or mode not in {"legacy", "quality"}:
            raise NewsGenerationJournalError("news_generation_mode_invalid")
        output = self._output_dir(client_id, request_id)
        # Any existing terminal-barrier reservation is unambiguously legacy,
        # even if imported by a separately reviewed migration without a pin.
        legacy = db.execute("SELECT client_id,request_sha256,output_dir FROM news_generation_requests "
                            "WHERE request_id=?", (request_id,)).fetchone()
        if legacy is not None:
            if mode != "legacy":
                raise NewsGenerationJournalError("news_generation_mode_conflict")
            if (legacy["client_id"] != client_id or legacy["request_sha256"] != digest
                    or legacy["output_dir"] != str(output)):
                raise NewsGenerationJournalError("news_generation_request_conflict")
        row = db.execute("SELECT * FROM news_generation_modes WHERE request_id=?", (request_id,)).fetchone()
        if row is not None:
            if row["mode"] != mode:
                raise NewsGenerationJournalError("news_generation_mode_conflict")
            if (row["client_id"] != client_id or row["request_sha256"] != digest
                    or row["output_root"] != str(self.output_root)):
                raise NewsGenerationJournalError("news_generation_request_conflict")
        else:
            db.execute("INSERT INTO news_generation_modes VALUES (?,?,?,?,?,?)",
                       (request_id, mode, client_id, digest, str(self.output_root), int(time.time())))

    def pin_mode(self, request_id, client_id, payload, mode):
        """Commit an immutable mode binding, not a paid attempt reservation.

        Same-mode quality reentry is allowed so its own durable provider ledger
        can collect a pending request with GET only. This cannot authorize retry.
        """
        request_id = canonical_request_id(request_id)
        digest = request_digest(client_id, payload)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._pin_mode(db, request_id, client_id, digest, mode)

    def reserve(self, request_id, client_id, payload):
        request_id = canonical_request_id(request_id)
        digest = request_digest(client_id, payload)
        output = self._output_dir(client_id, request_id)
        response = None
        owner = None
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._pin_mode(db, request_id, client_id, digest, "legacy")
            row = db.execute("SELECT * FROM news_generation_requests WHERE request_id=?",
                             (request_id,)).fetchone()
            if row is not None:
                if (row["client_id"] != client_id or row["request_sha256"] != digest
                        or row["output_dir"] != str(output)):
                    raise NewsGenerationJournalError("news_generation_request_conflict")
                if row["state"] != "succeeded":
                    raise NewsGenerationJournalError("news_generation_outcome_unknown")
                try:
                    response = json.loads(row["response_json"])
                    assets = json.loads(row["assets_json"])
                    if self._assets(response, output, client_id) != assets:
                        raise ValueError()
                except (TypeError, ValueError, OSError, KeyError):
                    raise NewsGenerationJournalError("news_generation_cached_assets_unavailable") from None
            else:
                # An unowned existing directory must never be overwritten.
                if output.exists():
                    raise NewsGenerationJournalError("news_generation_output_exists")
                owner = secrets.token_hex(32)
                db.execute("INSERT INTO news_generation_requests VALUES (?,?,?,?,?,'started',NULL,NULL,?)",
                    (request_id, client_id, digest, str(output), owner, int(time.time())))
        # COMMIT completes before the only generator invocation is admitted.
        return NewsGenerationReservation(request_id, client_id, digest, output, response, owner)

    @staticmethod
    def _assets(response, output, client_id):
        if (type(response) is not dict or set(response) != _RESPONSE_FIELDS
                or response["client_id"] != client_id or response["content_type"] != "news_card"):
            raise NewsGenerationJournalError("news_generation_response_invalid")
        _json(response)
        assets = {}
        for key in ("png_path", "manifest_path", "source_visual_path"):
            value = response[key]
            if key == "source_visual_path" and value is None:
                continue
            if type(value) is not str or not value:
                raise NewsGenerationJournalError("news_generation_response_invalid")
            path = Path(value)
            if (not path.is_absolute() or path.resolve() != path
                    or not path.is_relative_to(output) or not path.is_file()
                    or not 0 < path.stat().st_size <= _MAX_ASSET):
                raise NewsGenerationJournalError("news_generation_cached_assets_unavailable")
            with path.open("rb") as asset:
                data = asset.read(_MAX_ASSET + 1)
            if not 0 < len(data) <= _MAX_ASSET or (key == "png_path" and not data.startswith(b"\x89PNG\r\n\x1a\n")):
                raise NewsGenerationJournalError("news_generation_cached_assets_unavailable")
            assets[key] = {"path": value, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        return assets

    def record_success(self, reservation, response):
        if (type(reservation) is not NewsGenerationReservation
                or reservation.owner_token is None or reservation.response is not None):
            raise NewsGenerationJournalError("news_generation_reservation_invalid")
        response_json = _json(response)
        assets_json = _json(self._assets(response, reservation.output_dir, reservation.client_id))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM news_generation_requests WHERE request_id=?",
                             (reservation.request_id,)).fetchone()
            if (row is None or row["state"] != "started"
                    or row["client_id"] != reservation.client_id
                    or row["request_sha256"] != reservation.request_sha256
                    or row["output_dir"] != str(reservation.output_dir)
                    or not secrets.compare_digest(row["owner_token"], reservation.owner_token)):
                raise NewsGenerationJournalError("news_generation_request_conflict")
            db.execute("UPDATE news_generation_requests SET state='succeeded',response_json=?,assets_json=? "
                       "WHERE request_id=?", (response_json, assets_json, reservation.request_id))
