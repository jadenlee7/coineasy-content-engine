"""Read-only API fence for a trusted, already admitted one-shot YBO trial.

The API does not provision a plan, admit a trial, claim a job, or reserve a
phase. Its only authority is the shared private TrialOwner file created by the
trusted hosted orchestrator. The existing quality journal and account budget
remain separate and mandatory.
"""
from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from core.automation.news_generation_journal import request_digest
from core.automation.trial_owner import TrialError, TrialOwner, TrialPlan
from core.content_ops.trial_window import TRIAL_ID


class TrialGenerationDenied(ValueError):
    pass


IMAGE_STAMP_PATH = "/app/coineasy-api-build-sha"


def read_image_stamp():
    """Read fixed, immutable build stamp; never accept a caller-selected path."""
    try:
        fd = os.open(IMAGE_STAMP_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            raw = os.read(fd, 41)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                    or info.st_mode & 0o222 or not re.fullmatch(rb"[a-f0-9]{40}", raw)):
                raise TrialGenerationDenied("trial_generation_release_mismatch")
            return raw.decode("ascii")
        finally:
            os.close(fd)
    except (OSError, ValueError):
        raise TrialGenerationDenied("trial_generation_release_mismatch") from None


class TrialGenerationGate:
    def __init__(self, *, plan, owner, clock=None):
        self.plan = plan
        self.owner = owner
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_env(cls, env, *, clock=None, stamp_reader=read_image_stamp):
        """Only deployment configuration identifies the approved plan/owner.

        Invalid/missing configuration is OFF; no owner file is initialized.
        """
        try:
            if env.get("YBO_TRIAL_API_MODE") != "true":
                raise TrialGenerationDenied("trial_generation_disabled")
            payload = json.loads(env["YBO_TRIAL_PLAN_JSON"])
            plan = TrialPlan.from_mapping(payload, env["YBO_TRIAL_PLAN_SHA256"])
            if plan.natural_once and env["YBO_TRIAL_PLAN_JSON"] != plan.payload_json:
                # Reject duplicate JSON keys and alternate serializations for
                # the new trusted contract. Historical configuration is intact.
                raise TrialGenerationDenied("trial_generation_owner_unavailable")
            if plan.trial_id == TRIAL_ID:
                receipt_id = env["YBO_PRESERVED_BABYLON_RECEIPT_ID"]
                if (not isinstance(receipt_id, str) or str(UUID(receipt_id)) != receipt_id
                        or UUID(receipt_id).int == 0):
                    raise TrialGenerationDenied("trial_generation_owner_unavailable")
            elif env.get("YBO_PRESERVED_BABYLON_RECEIPT_ID"):
                raise TrialGenerationDenied("trial_generation_owner_unavailable")
            release = env.get("RAILWAY_GIT_COMMIT_SHA", "")
            if (not re.fullmatch(r"[a-f0-9]{40}", release)
                    or release != plan.payload["release_sha"]
                    or (plan.natural_once and env.get("QUALITY_BANNER_RELEASE_SHA") != release)
                    or stamp_reader() != release):
                raise TrialGenerationDenied("trial_generation_release_mismatch")
            root = Path(env["NEWS_CARD_IDEMPOTENCY_JOURNAL_ROOT"])
            info = root.lstat()
            if (not root.is_absolute() or root.resolve() != root
                    or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077):
                raise TrialGenerationDenied("trial_generation_owner_unavailable")
            trusted_clock = clock or (lambda: datetime.now(timezone.utc))
            return cls(plan=plan, owner=TrialOwner(root / f"{plan.trial_id}.trial-owner.sqlite3",
                plan, clock=trusted_clock), clock=trusted_clock)
        except TrialGenerationDenied:
            raise
        except (KeyError, ValueError, TypeError, OSError, TrialError):
            raise TrialGenerationDenied("trial_generation_owner_unavailable") from None

    def require_generation(self, *, trial_id, client_id, request_id, payload):
        """Check exact durable phase immediately before each external effect.

        `trial_id` is only a request selector, never authority. Full canonical
        request digest and the three DB identifiers must match the durable
        phase that the trusted orchestrator reserved after exact SQL claim.
        """
        try:
            clients = self.plan.eligible_clients
        except ValueError:
            raise TrialGenerationDenied("trial_generation_not_admitted") from None
        if trial_id != self.plan.trial_id or client_id not in clients:
            raise TrialGenerationDenied("trial_generation_not_admitted")
        context = payload.get("quality_context") if type(payload) is dict else None
        if type(context) is not dict or context.get("request_id") != request_id or context.get("client_id") != client_id:
            raise TrialGenerationDenied("trial_generation_not_admitted")
        ids = {name: context.get(name) for name in ("job_id", "request_id", "source_item_id")}
        try:
            digest = request_digest(client_id, payload)
            permitted = self.owner.generation_authorized(client_id, ids, digest, self.clock())
        except Exception:
            raise TrialGenerationDenied("trial_generation_owner_unavailable") from None
        if not permitted:
            raise TrialGenerationDenied("trial_generation_not_admitted")
