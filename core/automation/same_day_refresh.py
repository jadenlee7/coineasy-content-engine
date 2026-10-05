"""Exact, manual-only latest-source refresh. No intake, FIFO, batch or delivery."""
from __future__ import annotations

import re
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Mapping, Protocol
from zoneinfo import ZoneInfo

from core.automation.daily_runner import choose_automation_template_style
from core.automation.generation_client import GeneratedCatalogResult
from core.automation.models import ClaimedJob, StyleReferencePack
from core.automation.repository import SupabaseAutomationRepository
from core.automation.settings import AUTOMATION_CLIENTS
from core.sources.x_media_url import normalize_x_media_url


KST = ZoneInfo("Asia/Seoul")
SHA40 = re.compile(r"[a-f0-9]{40}\Z")
SHA64 = re.compile(r"[a-f0-9]{64}\Z")
_HANDLES = {"yellow": "Yellow", "origintrail": "origin_trail",
            "squid": "SquidRouter", "babylon": "babylonlabs_io"}
_QUEUE_KEYS = frozenset({"refresh_id", "job_id", "request_id", "source_item_id",
    "client_id", "kst_date", "release_sha", "expires_at", "status", "reused"})
_QUEUE_STATUSES = frozenset({"queued", "running", "succeeded", "failed", "cancelled", "retrying"})
_CLAIM_KEYS = frozenset({"job_id", "workspace_id", "client_id", "status",
    "attempts", "max_attempts", "locked_by", "lease_expires_at", "input",
    "refresh_id", "release_sha", "execution_plane", "origintrail_batch_eligible",
    "batch_handoff_recovery_only"})
_INPUT_KEYS = frozenset({"workflow", "kst_date", "source_item_ids", "content_kind",
    "request_id", "source_content", "source_url", "source_image_url",
    "manual_only", "same_day_refresh_id"})
_INSPECT_KEYS = frozenset({"status", "refresh_id", "workspace_id", "client_id",
    "kst_date", "job_id", "request_id", "source_item_id", "content_item_id",
    "content_version_id", "banner_asset_id", "banner_sha256",
    "source_published_at", "source_age_seconds", "release_sha", "execution_plane",
    "execution_authorized", "delivery_authorized"})


def canonical_uuid(value: object) -> str:
    if type(value) is not str:
        raise ValueError("same_day_refresh_identity_invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("same_day_refresh_identity_invalid") from exc
    if str(parsed) != value or parsed.version not in {1, 2, 3, 4, 5}:
        raise ValueError("same_day_refresh_identity_invalid")
    return value


def aware_time(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif type(value) is str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("same_day_refresh_time_invalid") from exc
    else:
        raise ValueError("same_day_refresh_time_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("same_day_refresh_time_invalid")
    return parsed


def canonical_day(value: object) -> date:
    if type(value) is date:
        return value
    if type(value) is not str:
        raise ValueError("same_day_refresh_day_invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("same_day_refresh_day_invalid") from exc
    if parsed.isoformat() != value:
        raise ValueError("same_day_refresh_day_invalid")
    return parsed


@dataclass(frozen=True)
class RefreshIdentity:
    workspace_id: str
    client_id: str
    kst_date: date
    refresh_id: str
    request_id: str
    source_item_id: str
    release_sha: str
    expires_at: datetime

    def validate(self, now: datetime) -> None:
        now = aware_time(now)
        for value in (self.workspace_id, self.refresh_id, self.request_id,
                      self.source_item_id):
            canonical_uuid(value)
        if (self.client_id not in AUTOMATION_CLIENTS
            or type(self.release_sha) is not str or SHA40.fullmatch(self.release_sha) is None
            or canonical_day(self.kst_date) != now.astimezone(KST).date()
            or not now < aware_time(self.expires_at) <= now + timedelta(hours=2)
            or len({self.refresh_id, self.request_id, self.source_item_id}) != 3):
            raise ValueError("same_day_refresh_manifest_invalid")

    def safe_identity(self) -> dict[str, object]:
        return {"workspace_id": self.workspace_id, "client_id": self.client_id,
                "kst_date": self.kst_date.isoformat(), "refresh_id": self.refresh_id,
                "request_id": self.request_id, "source_item_id": self.source_item_id,
                "release_sha": self.release_sha, "expires_at": self.expires_at.isoformat()}


@dataclass(frozen=True)
class RefreshQueueManifest(RefreshIdentity):
    predecessor_job_id: str
    predecessor_content_item_id: str
    predecessor_content_version_id: str

    def validate(self, now: datetime) -> None:
        super().validate(now)
        for value in (self.predecessor_job_id, self.predecessor_content_item_id,
                      self.predecessor_content_version_id):
            canonical_uuid(value)
        if self.request_id == self.predecessor_content_item_id:
            raise ValueError("same_day_refresh_new_request_required")


@dataclass(frozen=True)
class RefreshRunManifest(RefreshIdentity):
    job_id: str

    def validate(self, now: datetime) -> None:
        super().validate(now)
        canonical_uuid(self.job_id)
        if self.job_id in {self.request_id, self.refresh_id}:
            raise ValueError("same_day_refresh_manifest_invalid")


class RefreshRepository(Protocol):
    async def queue_refresh(self, manifest: RefreshQueueManifest, *, now: datetime) -> Mapping: ...
    async def claim_refresh(self, manifest: RefreshRunManifest, *, worker_id: str,
                            now: datetime, lease_seconds: int = 900) -> ClaimedJob | None: ...
    async def inspect_refresh(self, *, workspace_id: str, refresh_id: str,
                              content_version_id: str) -> Mapping: ...
    async def get_or_create_style_reference_pack(self, **kwargs) -> StyleReferencePack: ...
    async def complete_job(self, **kwargs) -> None: ...
    async def fail_job(self, **kwargs) -> None: ...


class RefreshGeneration(Protocol):
    async def require_release(self, expected_release_sha: str) -> None: ...
    async def generate(self, **kwargs) -> GeneratedCatalogResult: ...


class _RefreshWindowClosed(ValueError):
    pass


class SupabaseSameDayRefreshRepository(SupabaseAutomationRepository):
    """Only dedicated exact refresh RPCs; shared completion/failure remain unchanged."""

    async def queue_refresh(self, manifest: RefreshQueueManifest, *, now: datetime) -> Mapping:
        manifest.validate(now)
        raw = await self._rpc("queue_official_x_same_day_refresh", {
            "target_workspace_id": manifest.workspace_id,
            "target_client_id": manifest.client_id,
            "target_kst_date": manifest.kst_date.isoformat(),
            "target_predecessor_job_id": manifest.predecessor_job_id,
            "target_predecessor_content_item_id": manifest.predecessor_content_item_id,
            "target_predecessor_content_version_id": manifest.predecessor_content_version_id,
            "target_source_item_id": manifest.source_item_id,
            "target_refresh_id": manifest.refresh_id,
            "target_request_id": manifest.request_id,
            "target_release_sha": manifest.release_sha,
            "target_expires_at": manifest.expires_at.isoformat(),
        })
        if not isinstance(raw, Mapping) or set(raw) != _QUEUE_KEYS:
            raise ValueError("same_day_refresh_queue_receipt_invalid")
        expected = manifest.safe_identity()
        for key in ("refresh_id", "request_id", "source_item_id", "client_id", "kst_date", "release_sha"):
            if raw[key] != expected[key]:
                raise ValueError("same_day_refresh_queue_receipt_invalid")
        canonical_uuid(raw["job_id"])
        if (raw["job_id"] in {manifest.refresh_id, manifest.request_id, manifest.predecessor_job_id}
            or aware_time(raw["expires_at"]) != manifest.expires_at
            or raw["status"] not in _QUEUE_STATUSES
            or type(raw["reused"]) is not bool):
            raise ValueError("same_day_refresh_queue_receipt_invalid")
        return dict(raw)

    async def claim_refresh(self, manifest: RefreshRunManifest, *, worker_id: str,
                            now: datetime, lease_seconds: int = 900) -> ClaimedJob | None:
        manifest.validate(now)
        if type(lease_seconds) is not int or not 60 <= lease_seconds <= 900:
            raise ValueError("same_day_refresh_lease_invalid")
        worker_id = self._worker(worker_id)
        raw = await self._rpc("claim_official_x_same_day_refresh", {
            "target_workspace_id": manifest.workspace_id, "target_refresh_id": manifest.refresh_id,
            "target_job_id": manifest.job_id, "target_request_id": manifest.request_id,
            "target_source_item_id": manifest.source_item_id, "target_release_sha": manifest.release_sha,
            "target_worker_id": worker_id, "target_lease_seconds": lease_seconds,
        })
        if raw is None:
            return None
        if not isinstance(raw, Mapping) or set(raw) != _CLAIM_KEYS:
            raise ValueError("same_day_refresh_claim_receipt_invalid")
        inp = raw.get("input")
        if (not isinstance(inp, Mapping) or set(inp) != _INPUT_KEYS
            or raw["workspace_id"] != manifest.workspace_id
            or raw["job_id"] != manifest.job_id or raw["client_id"] != manifest.client_id
            or raw["refresh_id"] != manifest.refresh_id or raw["release_sha"] != manifest.release_sha
            or raw["status"] != "running" or raw["execution_plane"] != "studio_sync"
            or type(raw["attempts"]) is not int or raw["attempts"] != 1
            or type(raw["max_attempts"]) is not int or raw["max_attempts"] != 1
            or raw["origintrail_batch_eligible"] is not False
            or raw["batch_handoff_recovery_only"] is not False
            or inp["workflow"] != "official_x_review_draft_v1"
            or inp["manual_only"] is not True or inp["same_day_refresh_id"] != manifest.refresh_id
            or inp["content_kind"] != "daily_news" or inp["request_id"] != manifest.request_id
            or inp["source_item_ids"] != [manifest.source_item_id]
            or inp["kst_date"] != manifest.kst_date.isoformat()
            # The SQL lease starts at its statement_timestamp, after this client's
            # observation. Permit only bounded request latency, not a longer lease.
            or not aware_time(now) < aware_time(raw["lease_expires_at"]) <= min(
                manifest.expires_at, aware_time(now) + timedelta(seconds=lease_seconds + 30))):
            raise ValueError("same_day_refresh_claim_receipt_invalid")
        job = self._claimed_job(raw, worker_id=worker_id)
        _validate_claimed_job(job, manifest, worker_id)
        return job

    async def inspect_refresh(self, *, workspace_id: str, refresh_id: str,
                              content_version_id: str) -> Mapping:
        for value in (workspace_id, refresh_id, content_version_id):
            canonical_uuid(value)
        raw = await self._rpc("inspect_official_x_same_day_refresh", {
            "target_workspace_id": workspace_id, "target_refresh_id": refresh_id,
            "target_content_version_id": content_version_id,
        })
        if not isinstance(raw, Mapping) or set(raw) != _INSPECT_KEYS:
            raise ValueError("same_day_refresh_inspection_invalid")
        if (raw["workspace_id"] != workspace_id or raw["refresh_id"] != refresh_id
            or raw["content_version_id"] != content_version_id
            or raw["status"] not in {"refresh_ready", "refresh_not_ready"}
            or raw["client_id"] not in AUTOMATION_CLIENTS
            or raw["content_item_id"] != raw["request_id"]
            or type(raw["release_sha"]) is not str or SHA40.fullmatch(raw["release_sha"]) is None
            or raw["execution_plane"] != "studio_sync"
            or raw["execution_authorized"] is not False or raw["delivery_authorized"] is not False):
            raise ValueError("same_day_refresh_inspection_invalid")
        for key in ("job_id", "request_id", "source_item_id", "content_item_id"):
            canonical_uuid(raw[key])
        canonical_day(raw["kst_date"])
        if raw["banner_asset_id"] is not None:
            canonical_uuid(raw["banner_asset_id"])
        if raw["banner_sha256"] is not None and (
            type(raw["banner_sha256"]) is not str or SHA64.fullmatch(raw["banner_sha256"]) is None):
            raise ValueError("same_day_refresh_inspection_invalid")
        if raw["source_published_at"] is not None:
            aware_time(raw["source_published_at"])
        age = raw["source_age_seconds"]
        if age is not None and (type(age) not in {int, float} or not math.isfinite(age) or not 0 <= age):
            raise ValueError("same_day_refresh_inspection_invalid")
        if raw["status"] == "refresh_ready" and (
            raw["banner_asset_id"] is None or raw["banner_sha256"] is None
            or raw["source_published_at"] is None or age is None or not age < 86400):
            raise ValueError("same_day_refresh_inspection_invalid")
        return dict(raw)


def _validate_claimed_job(job: ClaimedJob, manifest: RefreshRunManifest, worker: str) -> None:
    if (type(job) is not ClaimedJob or job.job_id != manifest.job_id
        or job.client_id != manifest.client_id or job.kst_date != manifest.kst_date
        or job.request_id != manifest.request_id or job.primary_source_item_id != manifest.source_item_id
        or job.content_kind != "daily_news" or job.manual_only is not True
        or type(job.attempts) is not int or job.attempts != 1
        or type(job.max_attempts) is not int or job.max_attempts != 1
        or job.locked_by != worker or job.origintrail_batch_eligible is not False
        or job.batch_handoff_recovery_only is not False or job.failed_draft_recovery_only is not False
        or type(job.source_content) is not str or not 10 <= len(job.source_content.strip()) <= 20000
        or type(job.source_url) is not str
        or re.fullmatch(r"https://x\.com/" + re.escape(_HANDLES[manifest.client_id])
                        + r"/status/[0-9]{1,19}", job.source_url) is None
        or type(job.source_image_url) is not str
        or (job.source_image_url != "" and not normalize_x_media_url(job.source_image_url))):
        raise ValueError("same_day_refresh_claim_receipt_invalid")


class SameDayRefreshRunner:
    """One invocation, one exact claim, one generation; unknown ACK never retries."""

    def __init__(self, *, repository: RefreshRepository, generation: RefreshGeneration | None = None,
                 now_factory: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.repository, self.generation, self.now_factory = repository, generation, now_factory
        self._attempted: set[tuple[str, str]] = set()

    @staticmethod
    def _receipt(manifest: RefreshIdentity, status: str, *, ok: bool = False,
                 attempted: bool = False, **extra) -> dict[str, object]:
        return {"ok": ok, "status": status, **manifest.safe_identity(),
                "generation_attempted": attempted, "private_send_attempted": False,
                "public_send_attempted": False, "automatic_publication": False, **extra}

    async def queue_once(self, manifest: RefreshQueueManifest) -> Mapping:
        now = self.now_factory()
        manifest.validate(now)
        key = ("queue", manifest.refresh_id)
        if key in self._attempted:
            return self._receipt(manifest, "already_attempted")
        self._attempted.add(key)
        try:
            result = await self.repository.queue_refresh(manifest, now=now)
            if (not isinstance(result, Mapping) or set(result) != _QUEUE_KEYS
                or result["status"] not in _QUEUE_STATUSES or type(result["reused"]) is not bool):
                raise ValueError("same_day_refresh_queue_receipt_invalid")
            canonical_uuid(result["job_id"])
        except Exception:
            return self._receipt(manifest, "queue_unknown")
        return self._receipt(manifest, result["status"], ok=True,
                             job_id=result["job_id"], reused=result["reused"])

    async def generate_once(self, manifest: RefreshRunManifest) -> Mapping:
        now = self.now_factory()
        manifest.validate(now)
        key = ("generate", manifest.job_id)
        if key in self._attempted:
            return self._receipt(manifest, "already_attempted", job_id=manifest.job_id)
        # Consume before the first network operation, including an unknown claim ACK.
        self._attempted.add(key)
        try:
            await self.generation.require_release(manifest.release_sha)
        except Exception:
            return self._receipt(manifest, "release_blocked", job_id=manifest.job_id)
        worker = f"same-day-refresh:{manifest.refresh_id}"
        try:
            # Repeat day/expiry validation after release HTTP, before the exact claim.
            claim_started = aware_time(self.now_factory())
            manifest.validate(claim_started)
            job = await self.repository.claim_refresh(manifest, worker_id=worker,
                now=claim_started, lease_seconds=900)
            if job is None:
                return self._receipt(manifest, "already_consumed", job_id=manifest.job_id)
            _validate_claimed_job(job, manifest, worker)
        except Exception:
            return self._receipt(manifest, "claim_unknown", job_id=manifest.job_id)
        try:
            pack = await self.repository.get_or_create_style_reference_pack(
                workspace_id=manifest.workspace_id, client_id=job.client_id,
                request_id=job.request_id, primary_source_item_id=job.primary_source_item_id)
            if (type(pack) is not StyleReferencePack or pack.request_id != manifest.request_id
                or pack.primary_source_item_id != manifest.source_item_id
                or type(pack.reference_pack_hash) is not str
                or re.fullmatch(r"[a-f0-9]{32}", pack.reference_pack_hash) is None):
                raise ValueError("same_day_refresh_style_pack_invalid")
        except Exception:
            return await self._failed(manifest, worker, "preparation_failed", attempted=False)
        try:
            # Claim is consumed, but a slow style-pack request must not start new
            # provider work after the original KST day/expiry/lease budget ends.
            generation_started = aware_time(self.now_factory())
            manifest.validate(generation_started)
            if generation_started >= min(manifest.expires_at, claim_started + timedelta(seconds=900)):
                raise ValueError("same_day_refresh_generation_window_closed")
            source_image_url = normalize_x_media_url(job.source_image_url) if job.source_image_url else ""
        except Exception:
            return await self._failed(manifest, worker, "preparation_expired", attempted=False)
        post_boundary_checked = False

        def before_generation_post() -> None:
            nonlocal post_boundary_checked
            try:
                post_started = aware_time(self.now_factory())
                manifest.validate(post_started)
                if post_started >= min(manifest.expires_at, claim_started + timedelta(seconds=900)):
                    raise ValueError("same_day_refresh_generation_window_closed")
            except Exception as exc:
                raise _RefreshWindowClosed("same_day_refresh_generation_window_closed") from exc
            post_boundary_checked = True

        try:
            result = await self.generation.generate(
                client_id=job.client_id, content_kind="daily_news", request_id=job.request_id,
                source_content=job.source_content, source_url=job.source_url,
                source_image_url=source_image_url,
                template_style=choose_automation_template_style(client_id=job.client_id,
                    content_kind="daily_news", source_image_url=source_image_url),
                style_references=pack.references, style_reference_pack_hash=pack.reference_pack_hash,
                expected_studio_release_sha=manifest.release_sha,
                before_generation_post=before_generation_post)
            if (not post_boundary_checked or type(result) is not GeneratedCatalogResult
                or result.content_item_id != manifest.request_id
                or type(result.reused) is not bool or type(result.asset_ids) is not tuple
                or len(result.asset_ids) != 1):
                raise ValueError("same_day_refresh_generation_receipt_invalid")
            canonical_uuid(result.content_version_id)
            canonical_uuid(result.asset_ids[0])
        except _RefreshWindowClosed:
            return await self._failed(manifest, worker, "preparation_expired", attempted=False)
        except Exception:
            return await self._failed(manifest, worker, "generation_unknown", attempted=True)
        try:
            await self.repository.complete_job(job_id=job.job_id, worker_id=worker,
                content_item_id=result.content_item_id, content_version_id=result.content_version_id)
        except Exception:
            # Durable generation may exist. Leave the lease alone; never regenerate.
            return self._receipt(manifest, "completion_ack_unknown", attempted=True,
                job_id=manifest.job_id, content_item_id=result.content_item_id,
                content_version_id=result.content_version_id)
        return self._receipt(manifest, "needs_review", ok=True, attempted=True,
            job_id=manifest.job_id, content_item_id=result.content_item_id,
            content_version_id=result.content_version_id, reused=result.reused)

    async def _failed(self, manifest: RefreshRunManifest, worker: str,
                      status: str, *, attempted: bool) -> Mapping:
        try:
            await self.repository.fail_job(job_id=manifest.job_id, worker_id=worker,
                error_code=f"same_day_refresh_{status}", retryable=False, retry_at=None)
        except Exception:
            status = "failure_ack_unknown"
        return self._receipt(manifest, status, attempted=attempted, job_id=manifest.job_id)

    async def inspect(self, *, workspace_id: str, refresh_id: str,
                      content_version_id: str) -> Mapping:
        return await self.repository.inspect_refresh(workspace_id=workspace_id,
            refresh_id=refresh_id, content_version_id=content_version_id)
