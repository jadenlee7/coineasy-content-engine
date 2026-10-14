"""Immutable local manifest for the trusted one-shot controller.

No credentials, network, public routes, owner admission or phase reservation.
The only writer is the hosted controller after generation and machine review;
the HTTP endpoint can only read and recheck existing active owner authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from uuid import UUID

from core.automation.trial_owner import TrialOwner, TrialPlan, canonical_sha256

MAX_BYTES = 16384
ENTRY_KEYS = frozenset({"client_id", "job_id", "request_id", "source_item_id", "source_sha256",
    "content_item_id", "content_version_id", "banner_sha256", "telegram_copy_sha256", "x_copy_sha256"})


class TrialManifestDenied(ValueError):
    pass


class TrialManifestNotReady(TrialManifestDenied):
    pass


def require(value):
    if not value:
        raise TrialManifestDenied("trial_manifest_unavailable")


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, TypeError, AttributeError):
        return False


def _sha(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def validate_manifest(value, *, plan, destination_policy_sha256):
    require(type(plan) is TrialPlan and _sha(destination_policy_sha256))
    require(type(value) is dict and set(value) == {"contract", "trial_id", "plan_sha256", "release_sha",
        "not_before", "deadline", "destination_policy_sha256", "entries"})
    p = plan.payload
    require(value["contract"] == "ybo-trial-courier-manifest@1"
        and value["trial_id"] == plan.trial_id and value["plan_sha256"] == plan.sha256
        and all(value[k] == p[k] for k in ("release_sha", "not_before", "deadline"))
        and value["destination_policy_sha256"] == destination_policy_sha256
        and type(value["entries"]) is list and 1 <= len(value["entries"]) <= 3)
    clients, identities = set(), set()
    for entry in value["entries"]:
        require(type(entry) is dict and set(entry) == ENTRY_KEYS)
        require(entry["client_id"] in p["client_ids"] and entry["client_id"] not in clients)
        ids = [entry[k] for k in ("job_id", "request_id", "source_item_id", "content_version_id")]
        require(all(_uuid(v) for v in ids) and len(set(ids)) == 4 and not identities.intersection(ids)
            and entry["content_item_id"] == entry["request_id"]
            and all(_sha(entry[k]) for k in ("source_sha256", "banner_sha256", "telegram_copy_sha256", "x_copy_sha256")))
        clients.add(entry["client_id"])
        identities.update(ids)
    return value


class TrialManifestStore:
    def __init__(self, *, owner, destination_policy_sha256, clock=None):
        require(type(owner) is TrialOwner and _sha(destination_policy_sha256))
        self.owner, self.policy = owner, destination_policy_sha256
        self.path = owner.path.with_name(f"{owner.plan.trial_id}.trial-manifest.json")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _root(self):
        root = self.path.parent
        info = root.lstat()
        require(root.is_absolute() and root.resolve() == root and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0)

    def publish(self, manifest, admission):
        """One exclusive write. Interrupted/ambiguous writes are never replaced."""
        try:
            self._root()
            validate_manifest(manifest, plan=self.owner.plan, destination_policy_sha256=self.policy)
            require(self.owner.is_active(admission, self.clock())
                and self.owner.delivery_authorized(manifest["entries"], self.clock()))
            raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                             allow_nan=False).encode("ascii")
            require(len(raw) <= MAX_BYTES)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(fd)
            finally:
                os.close(fd)
            parent = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
            return canonical_sha256(manifest)
        except Exception:
            raise TrialManifestDenied("trial_manifest_unavailable") from None

    def resolve(self, *, trial_id, plan_sha256):
        """Authenticated callers select identity, never supply manifest content."""
        try:
            require(trial_id == self.owner.plan.trial_id and plan_sha256 == self.owner.plan.sha256)
            self._root()
            # Reject inactive owners before any manifest file read.
            now = self.clock()
            state = self.owner.inspect(now)
            if state["status"] == "not_started" and now < self.owner.plan.deadline:
                raise TrialManifestNotReady("trial_manifest_not_ready")
            require(state["effective_on"] is True)
            if not self.path.exists() and not self.path.is_symlink():
                raise TrialManifestNotReady("trial_manifest_not_ready")
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_nlink == 1 and info.st_mode & 0o077 == 0 and info.st_size <= MAX_BYTES)
                raw = os.read(fd, MAX_BYTES + 1)
                require(len(raw) == info.st_size)
            finally:
                os.close(fd)
            value = validate_manifest(json.loads(raw), plan=self.owner.plan, destination_policy_sha256=self.policy)
            require(self.owner.delivery_authorized(value["entries"], self.clock()))
            observed = self.clock()
            require(self.owner.inspect(observed)["effective_on"] is True)
            return {"active": True, "observed_at": observed.astimezone(timezone.utc).isoformat(),
                "manifest": value, "manifest_sha256": canonical_sha256(value)}
        except TrialManifestNotReady:
            raise
        except Exception:
            raise TrialManifestDenied("trial_manifest_unavailable") from None
