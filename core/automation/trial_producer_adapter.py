"""Exact natural YBO one-shot producer; no scheduler, FIFO or retry path.

The trusted host controller owns the TrialOwner admission and phase lifecycle.
This adapter uses only existing service-role RPCs and the installed exact claim.
Every ambiguous write is terminal for this invocation; callers must not retry
or mark_failed/requeue the job. Netlify is the sole catalog/generation owner.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Awaitable, Callable
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

import httpx

from core.automation.daily_runner import OfficialXDailyRunner, _pinned_source_image_url
from core.automation.generation_client import (GeneratedCatalogResult, GenerationRequestError,
    StudioGenerationClient)
from core.automation.mode_router import select_daily_review_candidate
from core.automation.models import ClaimedJob, StyleReferencePack
from core.automation.news_generation_journal import request_digest
from core.automation.repository import AutomationRepositoryError, SupabaseAutomationRepository
from core.automation.trial_owner import CLIENTS, TrialOwner
from core.client_config import load_client_config
from core.sources.x_client import XClient
from core.sources.x_media_url import normalize_x_media_url

KST = ZoneInfo("Asia/Seoul")
START = datetime(2026, 10, 14, 6, 22, 17, tzinfo=timezone.utc)
END = datetime(2026, 10, 14, 8, 22, 17, tzinfo=timezone.utc)
DAY = date(2026, 10, 14)
HEX32 = re.compile(r"[a-f0-9]{32}\Z")
WORKER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class TrialProducerBlocked(ValueError):
    """Fixed, non-secret fail-closed reason; never a retry instruction."""


def _require(value, code="trial_producer_blocked"):
    if not value:
        raise TrialProducerBlocked(code)


def _uuid(value):
    try:
        _require(type(value) is str and str(UUID(value)) == value and UUID(value).int)
    except (ValueError, TypeError, AttributeError):
        raise TrialProducerBlocked("trial_producer_identity_invalid") from None
    return value


def _window(now):
    _require(type(now) is datetime and now.utcoffset() is not None)
    current = now.astimezone(timezone.utc)
    _require(START <= current < END and current.astimezone(KST).date() == DAY,
             "trial_producer_window_closed")
    return current


def _jsonb_string(value):
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), allow_nan=False)


def fixed_natural_input_sha256(value):
    """PostgreSQL jsonb::text for the fixed nine-key natural queue payload.

    PostgreSQL jsonb stores these ASCII keys by byte length then byte order;
    its text output has comma-space and colon-space separators. Only this exact
    string/UUID/bool shape is supported. The installed claim independently
    computes the server digest and returns NULL on any mismatch.
    """
    keys = ("kst_date", "workflow", "request_id", "source_url", "manual_only",
            "content_kind", "source_content", "source_item_ids", "source_image_url")
    _require(type(value) is dict and set(value) == set(keys))
    _require(value["kst_date"] == DAY.isoformat()
             and value["workflow"] == "official_x_review_draft_v1"
             and value["content_kind"] == "daily_news" and value["manual_only"] is False
             and type(value["source_item_ids"]) is list and len(value["source_item_ids"]) == 1)
    _uuid(value["request_id"])
    _uuid(value["source_item_ids"][0])
    for key in ("source_url", "source_content", "source_image_url"):
        _require(type(value[key]) is str)
    text = "{" + ", ".join(f"{_jsonb_string(key)}: {_jsonb_string(value[key])}" for key in keys) + "}"
    return hashlib.sha256(text.encode("utf8")).hexdigest()


@dataclass(frozen=True)
class NaturalSlot:
    workspace_id: str
    client_id: str
    job_id: str
    request_id: str
    source_item_id: str
    source_content: str
    source_url: str
    source_image_url: str
    style_pack: StyleReferencePack

    def job_input(self):
        return {"workflow": "official_x_review_draft_v1", "kst_date": DAY.isoformat(),
            "source_item_ids": [self.source_item_id], "content_kind": "daily_news",
            "request_id": self.request_id, "source_content": self.source_content.strip(),
            "source_url": self.source_url, "source_image_url": self.source_image_url.strip(),
            "manual_only": False}

    def identifiers(self):
        return {"job_id": self.job_id, "request_id": self.request_id,
                "source_item_id": self.source_item_id}


@dataclass(frozen=True)
class ClaimedTrialGeneration:
    slot: NaturalSlot
    job: ClaimedJob
    payload: dict
    payload_sha256: str
    worker_id: str


class TrialNaturalProducerAdapter:
    def __init__(self, *, repository: SupabaseAutomationRepository, x_client: XClient,
                 studio: StudioGenerationClient, owner: TrialOwner, workspace_id: str,
                 trial_id: str, clock: Callable[[], datetime] | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        _uuid(workspace_id); _uuid(trial_id)
        _require(type(owner) is TrialOwner and owner.plan.trial_id == trial_id
                 and callable(sleep))
        self.repository, self.x_client, self.studio = repository, x_client, studio
        self.owner, self.workspace_id, self.trial_id = owner, workspace_id, trial_id
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep

    def _before_effect(self, callback: Callable[[], None]) -> None:
        _window(self.clock())
        callback()
        _window(self.clock())

    async def intake(self, client_id: str, *, before_effect: Callable[[], None]) -> NaturalSlot | None:
        """Poll and queue only the latest eligible official YBO source."""
        _require(client_id in CLIENTS and callable(before_effect))
        now = _window(self.clock())
        config = load_client_config(client_id)
        source = config.content_sources.twitter
        _require(config.active and source is not None and source.handle)
        self._before_effect(before_effect)
        state = await self.repository.get_state(workspace_id=self.workspace_id,
            client_id=client_id, kst_date=DAY)
        if state.draft_reserved_today:
            return None
        self._before_effect(before_effect)
        posts = await self.x_client.get_recent_tweets(source.handle, hours=24,
            max_results=100, since_id=state.last_cursor, require_complete=True)
        eligible = tuple(post for post in posts if post.get("is_retweet") is False
                         and post.get("is_reply") is False
                         and (client_id != "origintrail" or post.get("is_quote") is False))
        # An ineligible newest result cannot make an older post the trial's
        # "latest" official source. No backlog fallback is admitted.
        observed_ids = [int(post["id"]) for post in posts if isinstance(post.get("id"), str)
                        and post["id"].isdigit()]
        eligible_ids = [int(post["id"]) for post in eligible if isinstance(post.get("id"), str)
                        and post["id"].isdigit()]
        if observed_ids and (not eligible_ids or max(observed_ids) != max(eligible_ids)):
            return None
        latest_cursor = OfficialXDailyRunner._newest_cursor(eligible, state.last_cursor)
        self._before_effect(before_effect)
        state = await self.repository.record_sources(workspace_id=self.workspace_id,
            client_id=client_id, handle=source.handle, poll_request_id=str(uuid4()),
            expected_cursor=state.last_cursor, next_cursor=latest_cursor,
            source_items=[OfficialXDailyRunner._source_payload(post,
                include_standalone_signals=client_id == "origintrail") for post in eligible],
            polled_at=_window(self.clock()))
        if state.draft_reserved_today:
            return None
        selected = select_daily_review_candidate((item.routing_post() for item in state.pending_sources),
            latest_cursor=state.last_cursor, now=_window(self.clock()),
            skip_patterns=config.routing.skip_patterns)
        if selected is None:
            return None
        image = _pinned_source_image_url(selected)
        _require(image and type(selected.get("source_item_id")) is str,
                 "trial_producer_official_image_required")
        source_id = _uuid(selected["source_item_id"])
        namespace = UUID(self.workspace_id)
        policy = ("yellow-source-visual-routing@1" if client_id == "yellow"
                  else "standard-source-visual-routing@1")
        request_id = str(uuid5(namespace, f"official-x-review:v3:double-fact-check@1:"
            f"{policy}:{client_id}:{source_id}:daily_news"))
        self._before_effect(before_effect)
        pack = await self.repository.get_or_create_style_reference_pack(
            workspace_id=self.workspace_id, client_id=client_id, request_id=request_id,
            primary_source_item_id=source_id)
        _require(type(pack) is StyleReferencePack and HEX32.fullmatch(pack.reference_pack_hash))
        self._before_effect(before_effect)
        queued = await self.repository.queue_job(workspace_id=self.workspace_id,
            client_id=client_id, kst_date=DAY, source_item_ids=[source_id],
            content_kind="daily_news", request_id=request_id,
            source_content=str(selected["text"]), source_url=str(selected["url"]),
            source_image_url=image, manual_only=False)
        _require(queued.job_id is not None and queued.status == "queued" and not queued.reused,
                 "trial_producer_slot_not_new")
        return NaturalSlot(self.workspace_id, client_id, _uuid(queued.job_id), request_id,
            source_id, str(selected["text"]).strip(), str(selected["url"]), image, pack)

    async def claim_and_prepare(self, slot: NaturalSlot, *, worker_id: str,
                                before_effect: Callable[[], None]) -> ClaimedTrialGeneration:
        """One exact initial claim, then read current DB context and pin API body."""
        _window(self.clock())
        _require(type(slot) is NaturalSlot and slot.workspace_id == self.workspace_id
                 and slot.client_id in CLIENTS and WORKER.fullmatch(worker_id or "")
                 and callable(before_effect))
        for value in slot.identifiers().values(): _uuid(value)
        _require(type(slot.style_pack) is StyleReferencePack
                 and slot.style_pack.request_id == slot.request_id
                 and slot.style_pack.primary_source_item_id == slot.source_item_id
                 and HEX32.fullmatch(slot.style_pack.reference_pack_hash))
        self._before_effect(before_effect)
        raw = await self.repository._rpc("claim_exact_natural_review_draft_job", {
            "target_workspace_id": slot.workspace_id, "target_client_id": slot.client_id,
            "target_job_id": slot.job_id, "target_request_id": slot.request_id,
            "target_source_item_id": slot.source_item_id,
            "target_input_sha256": fixed_natural_input_sha256(slot.job_input()),
            "target_style_pack_hash": slot.style_pack.reference_pack_hash,
            "target_worker_id": worker_id, "target_lease_seconds": 900})
        _require(raw is not None, "trial_producer_exact_claim_denied")
        job = self.repository._claimed_job(raw, worker_id=worker_id)
        _require(job.job_id == slot.job_id and job.client_id == slot.client_id
                 and job.request_id == slot.request_id
                 and job.primary_source_item_id == slot.source_item_id
                 and job.kst_date == DAY and job.content_kind == "daily_news"
                 and job.attempts == 1 and job.max_attempts == 3
                 and not job.manual_only and not job.origintrail_batch_eligible
                 and not job.batch_handoff_recovery_only and not job.failed_draft_recovery_only
                 and job.source_content == slot.source_content
                 and job.source_url == slot.source_url
                 and job.source_image_url == slot.source_image_url,
                 "trial_producer_claim_mismatch")
        self._before_effect(before_effect)
        plane = await self.repository.bind_execution_plane(job_id=job.job_id,
            worker_id=worker_id, requested_plane="studio_sync")
        _require(plane == "studio_sync", "trial_producer_plane_mismatch")
        self._before_effect(before_effect)
        context = await self.repository._rpc("get_quality_banner_generation_context", {
            "target_workspace_id": slot.workspace_id,
            "target_client_id": slot.client_id, "target_request_id": slot.request_id})
        _require(type(context) is dict and context.get("job_id") == slot.job_id
                 and context.get("request_id") == slot.request_id
                 and context.get("workspace_id") == slot.workspace_id
                 and context.get("client_id") == slot.client_id
                 and context.get("source_item_id") == slot.source_item_id
                 and context.get("source_content") == slot.source_content
                 and context.get("source_url") == slot.source_url
                 and context.get("source_image_url") == slot.source_image_url,
                 "trial_producer_context_mismatch")
        context_keys = {"job_id", "request_id", "workspace_id", "client_id", "source_item_id",
            "source_published_at", "source_sha256", "source_content", "source_url",
            "source_image_url"}
        _require(set(context) == context_keys and all(type(v) is str for v in context.values())
                 and context["source_sha256"] == hashlib.sha256(
                     slot.source_content.encode("utf8")).hexdigest(),
                 "trial_producer_context_mismatch")
        try:
            published = datetime.fromisoformat(context["source_published_at"].replace("Z", "+00:00"))
        except ValueError:
            raise TrialProducerBlocked("trial_producer_context_mismatch") from None
        now = _window(self.clock())
        _require(published.utcoffset() is not None and 0 < (now - published).total_seconds() < 86_400,
                 "trial_producer_source_stale")
        image = normalize_x_media_url(slot.source_image_url)
        _require(image, "trial_producer_context_mismatch")
        context = {**context, "source_image_url": image}
        # The API request model is the digest authority. Import here so a
        # future API-host controller import does not introduce a module cycle.
        from api.server import NewsCardRequest
        payload = NewsCardRequest(source_content=slot.source_content, source_type="tweet",
            source_url=slot.source_url, source_image_url=image,
            mock_mode=False, template_style="remix",
            style_references=[reference.generation_payload() for reference in slot.style_pack.references],
            style_reference_pack_hash=slot.style_pack.reference_pack_hash,
            brand_review_guidance=None, quality_context=context).model_dump(mode="json")
        digest = request_digest(slot.client_id, payload)
        return ClaimedTrialGeneration(slot, job, payload, digest, worker_id)

    async def generate_and_complete(self, prepared: ClaimedTrialGeneration, *,
                                    before_effect: Callable[[], None]) -> GeneratedCatalogResult:
        """One generation POST; pending responses permit bounded GET-only continuation."""
        _require(type(prepared) is ClaimedTrialGeneration and callable(before_effect))
        slot = prepared.slot
        started = _window(self.clock())
        _require(request_digest(slot.client_id, prepared.payload) == prepared.payload_sha256,
                 "trial_producer_payload_changed")
        _require(self.owner.generation_authorized(slot.client_id, slot.identifiers(),
            prepared.payload_sha256, self.clock()), "trial_producer_owner_not_reserved")
        self._before_effect(before_effect)
        async with httpx.AsyncClient(timeout=self.studio.timeout_seconds,
                follow_redirects=False, transport=self.studio.transport) as client:
            # Contract GET has no generation side effect; still expires closed.
            release = await self.studio._require_generation_contract(client,
                expected_release_sha=self.owner.plan.payload["release_sha"])
            _require(release == self.owner.plan.payload["release_sha"],
                     "trial_producer_release_mismatch")
            attempts = 0
            while True:
                # The initial call may submit. Every later call is explicitly
                # collect-only and is allowed only after an exact pending ACK.
                collect_only = attempts > 0
                self._before_effect(before_effect)
                _require(self.owner.generation_authorized(slot.client_id, slot.identifiers(),
                    prepared.payload_sha256, self.clock()), "trial_producer_owner_not_reserved")
                self._before_effect(before_effect)
                _require(request_digest(slot.client_id, prepared.payload) == prepared.payload_sha256,
                         "trial_producer_payload_changed")
                try:
                    published = datetime.fromisoformat(prepared.payload["quality_context"]["source_published_at"]
                        .replace("Z", "+00:00"))
                except (KeyError, TypeError, ValueError, AttributeError):
                    raise TrialProducerBlocked("trial_producer_payload_changed") from None
                _require(published.utcoffset() is not None and 0 < (
                    _window(self.clock()) - published).total_seconds() < 86_400,
                    "trial_producer_source_stale")
                headers = {"Content-Type": "application/json",
                    "Idempotency-Key": slot.request_id,
                    "X-Studio-Automation-Key": self.studio.automation_token,
                    "X-Studio-Expected-Release-Sha": release,
                    "X-YBO-Trial-ID": self.trial_id,
                    "X-YBO-Trial-Plan-Sha256": self.owner.plan.sha256}
                if collect_only:
                    headers["X-YBO-Trial-Collect-Only"] = "true"
                try:
                    response = await client.post(f"{self.studio.base_url}/api/news-card/{slot.client_id}",
                        headers=headers,
                        json={**{key: prepared.payload[key] for key in (
                            "source_content", "source_type", "source_url", "source_image_url",
                            "mock_mode", "template_style", "style_references",
                            "style_reference_pack_hash")},
                            "trial_payload": prepared.payload})
                except (httpx.TimeoutException, httpx.TransportError):
                    raise TrialProducerBlocked("trial_producer_generation_outcome_unknown") from None
                attempts += 1
                try:
                    pending = (response.status_code == 503 and len(response.content) <= 256
                        and response.json() == {"error": "quality_banner_pending"})
                except ValueError:
                    pending = False
                if not pending:
                    break
                _require(attempts < 30 and _window(self.clock()) < started + timedelta(minutes=8),
                         "trial_producer_collect_deadline")
                await self.sleep(15)
        try:
            result = self.studio._validated_generation_result(response,
                client_id=slot.client_id, content_kind="daily_news", request_id=slot.request_id,
                source_image_url=prepared.payload["source_image_url"], template_style="remix")
        except GenerationRequestError:
            # Even a retryable ordinary-client response cannot reopen a
            # one-shot trial whose upstream catalog/provider outcome is opaque.
            raise TrialProducerBlocked("trial_producer_generation_outcome_unknown") from None
        _require(not result.reused, "trial_producer_generation_reused")
        self._before_effect(before_effect)
        _require(self.owner.generation_authorized(slot.client_id, slot.identifiers(),
            prepared.payload_sha256, self.clock()), "trial_producer_owner_not_reserved")
        try:
            await self.repository.complete_job(job_id=slot.job_id, worker_id=prepared.worker_id,
                content_item_id=result.content_item_id,
                content_version_id=result.content_version_id)
        except AutomationRepositoryError:
            raise TrialProducerBlocked("trial_producer_completion_unknown") from None
        return result
