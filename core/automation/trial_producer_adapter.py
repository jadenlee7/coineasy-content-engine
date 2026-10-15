"""Exact natural YBO one-shot producer; no scheduler, FIFO or retry path.

The trusted host controller owns the TrialOwner admission and phase lifecycle.
The preserved-job successor uses its one-job Babylon handoff RPC. The distinct
fresh-day trial uses the exact natural initial-claim RPC for one new Babylon
slot. OriginTrail is held before intake in both modes.
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

import httpx

from core.automation.daily_runner import OfficialXDailyRunner, _pinned_source_image_url
from core.automation.generation_client import (GeneratedCatalogResult, GenerationRequestError,
    StudioGenerationClient)
from core.automation.mode_router import select_daily_review_candidate
from core.automation.models import ClaimedJob, StyleReferencePack
from core.automation.news_generation_journal import request_digest
from core.automation.repository import (AutomationRepositoryError, SupabaseAutomationRepository,
    _style_reference_pack)
from core.automation.trial_owner import CLIENTS, KST, TrialOwner
from core.client_config import load_client_config
from core.sources.x_client import XClient
from core.sources.x_media_url import normalize_x_media_url
from core.content_ops.trial_window import TRIAL_ID, trial_identity_policy

HEX32 = re.compile(r"[a-f0-9]{32}\Z")
WORKER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
PRESERVED_BABYLON_JOB_ID = "e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af"
PRESERVED_BABYLON_REQUEST_ID = "52f95e4e-1f66-549a-baa4-32b0fdf82b30"
PRESERVED_BABYLON_SOURCE_ID = "35ae7c96-993a-4453-9946-3263f4f2842a"
PRESERVED_BABYLON_POST_ID = "2110380535927427174"


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


def _window(now, plan):
    _require(type(now) is datetime and now.utcoffset() is not None)
    current = now.astimezone(timezone.utc)
    _require(plan.not_before <= current < plan.deadline and current.astimezone(KST).date() == plan.kst_date,
             "trial_producer_window_closed")
    return current


def _jsonb_string(value):
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), allow_nan=False)


def fixed_natural_input_sha256(value, *, kst_date):
    """PostgreSQL jsonb::text for the fixed nine-key natural queue payload.

    PostgreSQL jsonb stores these ASCII keys by byte length then byte order;
    its text output has comma-space and colon-space separators. Only this exact
    string/UUID/bool shape is supported. The installed claim independently
    computes the server digest and returns NULL on any mismatch.
    """
    keys = ("kst_date", "workflow", "request_id", "source_url", "manual_only",
            "content_kind", "source_content", "source_item_ids", "source_image_url")
    _require(type(value) is dict and set(value) == set(keys))
    _require(type(kst_date) is date and value["kst_date"] == kst_date.isoformat()
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
    kst_date: date
    recovery_receipt_id: str | None = None

    def job_input(self):
        return {"workflow": "official_x_review_draft_v1", "kst_date": self.kst_date.isoformat(),
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
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 recovery_receipt_id: str | None = None):
        _uuid(workspace_id); _uuid(trial_id)
        _require(type(owner) is TrialOwner and owner.plan.trial_id == trial_id
                 and callable(sleep))
        policy = trial_identity_policy(trial_id)
        _require((trial_id == TRIAL_ID and recovery_receipt_id is not None)
                 or (policy.natural and recovery_receipt_id is None),
                 "trial_producer_recovery_configuration_invalid")
        if recovery_receipt_id is not None:
            _uuid(recovery_receipt_id)
        self.repository, self.x_client, self.studio = repository, x_client, studio
        self.owner, self.workspace_id, self.trial_id = owner, workspace_id, trial_id
        self.recovery_receipt_id = recovery_receipt_id
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep

    def _before_effect(self, callback: Callable[[], None]) -> None:
        _window(self.clock(), self.owner.plan)
        callback()
        _window(self.clock(), self.owner.plan)

    async def intake(self, client_id: str, *, before_effect: Callable[[], None]) -> NaturalSlot | None:
        """Poll latest official evidence; only the legacy trial reuses a job."""
        _require(client_id in CLIENTS and callable(before_effect))
        if client_id not in self.owner.plan.eligible_clients:
            return None  # Preserve uncertain prior work before any per-client I/O.
        if client_id == "origintrail" and self.trial_id == TRIAL_ID:
            # The old initial-claim RPC pins the old worker and expired anchor.
            # Do not queue fresh work that this successor cannot safely claim.
            # A future OriginTrail path needs its own bounded claim/context;
            # this release only recovers the one preserved Babylon job.
            return None
        if client_id == "babylon" and self.trial_id == TRIAL_ID:
            return await self._intake_preserved_babylon(before_effect=before_effect)
        now = _window(self.clock(), self.owner.plan)
        config = load_client_config(client_id)
        source = config.content_sources.twitter
        _require(config.active and source is not None and source.handle)
        self._before_effect(before_effect)
        state = await self.repository.get_state(workspace_id=self.workspace_id,
            client_id=client_id, kst_date=self.owner.plan.kst_date)
        if state.draft_reserved_today:
            return None
        self._before_effect(before_effect)
        # The new-day trial must prove the latest live 24-hour feed, even when
        # the persisted cursor already equals the newest post. A since-cursor
        # query alone would not establish that an older pending item is latest.
        posts = await self.x_client.get_recent_tweets(source.handle, hours=24,
            max_results=100, since_id=None, require_complete=True)
        eligible = tuple(post for post in posts if post.get("is_retweet") is False
                         and post.get("is_reply") is False
                         and type(post.get("is_quote")) is bool
                         and (client_id != "origintrail" or post.get("is_quote") is False
                              or (post.get("source_image_url") == ""
                                  and type(post.get("media")) is list and post["media"] == []
                                  and post.get("is_note_tweet") is False)))
        # An ineligible newest result cannot make an older post the trial's
        # "latest" official source. No backlog fallback is admitted.
        observed_ids = [int(post["id"]) for post in posts if isinstance(post.get("id"), str)
                        and post["id"].isdigit()]
        eligible_ids = [int(post["id"]) for post in eligible if isinstance(post.get("id"), str)
                        and post["id"].isdigit()]
        if trial_identity_policy(self.trial_id).natural and not observed_ids:
            return None  # An empty timeline cannot prove the pending source is latest.
        if observed_ids and (not eligible_ids or max(observed_ids) != max(eligible_ids)):
            return None
        latest_cursor = OfficialXDailyRunner._newest_cursor(eligible, state.last_cursor)
        self._before_effect(before_effect)
        state = await self.repository.record_trial_official_sources(workspace_id=self.workspace_id,
            client_id=client_id, handle=source.handle, poll_request_id=str(uuid4()),
            expected_cursor=state.last_cursor, next_cursor=latest_cursor,
            source_items=[OfficialXDailyRunner._source_payload(post,
                include_standalone_signals=True) for post in eligible],
            polled_at=_window(self.clock(), self.owner.plan))
        if state.draft_reserved_today:
            return None
        selected = select_daily_review_candidate((item.routing_post() for item in state.pending_sources),
            latest_cursor=state.last_cursor, now=_window(self.clock(), self.owner.plan),
            skip_patterns=config.routing.skip_patterns)
        if selected is None:
            return None
        try:
            image = _pinned_source_image_url(selected)
        except (TypeError, ValueError):
            raise TrialProducerBlocked("trial_producer_official_image_required") from None
        # No image is an explicit input mode, never an alias for a logo or
        # permission to ignore malformed/unusable source media. The pinned
        # logo is selected later by the existing account-bound renderer.
        _require((bool(image) or (selected.get("source_image_url") == ""
                    and type(selected.get("media")) is list and selected["media"] == []))
                 and type(selected.get("source_item_id")) is str,
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
            client_id=client_id, kst_date=self.owner.plan.kst_date, source_item_ids=[source_id],
            content_kind="daily_news", request_id=request_id,
            source_content=str(selected["text"]), source_url=str(selected["url"]),
            source_image_url=image, manual_only=False)
        _require(queued.job_id is not None and queued.status == "queued" and not queued.reused,
                 "trial_producer_slot_not_new")
        return NaturalSlot(self.workspace_id, client_id, _uuid(queued.job_id), request_id,
            source_id, str(selected["text"]).strip(), str(selected["url"]), image, pack, self.owner.plan.kst_date)

    async def _intake_preserved_babylon(self, *, before_effect: Callable[[], None]) -> NaturalSlot:
        """Refresh official evidence, then read only the original reserved job."""
        _window(self.clock(), self.owner.plan)
        config = load_client_config("babylon")
        source = config.content_sources.twitter
        _require(config.active and source is not None and source.handle)
        self._before_effect(before_effect)
        state = await self.repository.get_state(workspace_id=self.workspace_id,
            client_id="babylon", kst_date=self.owner.plan.kst_date)
        # `draft_reserved_today` is expected for this exact original job; it
        # never authorizes another queue or a different source.
        _require(state.draft_reserved_today, "trial_producer_recovery_slot_missing")
        self._before_effect(before_effect)
        posts = await self.x_client.get_recent_tweets(source.handle, hours=24,
            max_results=100, since_id=None, require_complete=True)
        observed_ids = [int(post["id"]) for post in posts if type(post.get("id")) is str
                        and post["id"].isdigit()]
        expected_post_id = int(PRESERVED_BABYLON_POST_ID)
        _require(observed_ids and max(observed_ids) == expected_post_id,
                 "trial_producer_recovery_not_latest")
        eligible = tuple(post for post in posts if post.get("is_retweet") is False
                         and post.get("is_reply") is False and type(post.get("is_quote")) is bool)
        _require(any(post.get("id") == PRESERVED_BABYLON_POST_ID for post in eligible),
                 "trial_producer_recovery_source_ineligible")
        self._before_effect(before_effect)
        state = await self.repository.record_trial_official_sources(
            workspace_id=self.workspace_id, client_id="babylon", handle=source.handle,
            poll_request_id=str(uuid4()), expected_cursor=state.last_cursor,
            next_cursor=OfficialXDailyRunner._newest_cursor(eligible, state.last_cursor),
            source_items=[OfficialXDailyRunner._source_payload(post,
                include_standalone_signals=True) for post in eligible],
            polled_at=_window(self.clock(), self.owner.plan))
        _require(state.draft_reserved_today, "trial_producer_recovery_slot_missing")
        self._before_effect(before_effect)
        value = await self.repository._rpc("get_exact_preserved_babylon_context", {
            "target_workspace_id": self.workspace_id,
            "target_job_id": PRESERVED_BABYLON_JOB_ID,
            "target_receipt_id": self.recovery_receipt_id,
        })
        _require(type(value) is dict and set(value) == {
            "job_id", "request_id", "source_item_id", "source_content", "source_url",
            "source_image_url", "kst_date", "input_sha256", "style_pack_hash",
            "style_reference_pack", "source_published_at", "source_sha256",
            "receipt_id", "input", "new_release_sha", "workspace_id", "client_id"},
            "trial_producer_recovery_context_mismatch")
        _require(value["job_id"] == PRESERVED_BABYLON_JOB_ID
                 and value["workspace_id"] == self.workspace_id
                 and value["client_id"] == "babylon"
                 and value["request_id"] == PRESERVED_BABYLON_REQUEST_ID
                 and value["source_item_id"] == PRESERVED_BABYLON_SOURCE_ID
                 and value["receipt_id"] == self.recovery_receipt_id
                 and value["new_release_sha"] == self.owner.plan.payload["release_sha"]
                 and type(value["source_content"]) is str and value["source_content"].strip()
                 and value["source_sha256"] == hashlib.sha256(
                     value["source_content"].encode("utf8")).hexdigest()
                 and type(value["source_url"]) is str
                 and value["source_url"] ==
                     "https://x.com/babylonlabs_io/status/" + PRESERVED_BABYLON_POST_ID
                 and type(value["source_image_url"]) is str and value["source_image_url"]
                 and bool(normalize_x_media_url(value["source_image_url"]))
                 and type(value["kst_date"]) is str
                 and value["kst_date"] == self.owner.plan.kst_date.isoformat()
                 and type(value["input_sha256"]) is str and re.fullmatch(r"[a-f0-9]{64}", value["input_sha256"])
                 and type(value["style_pack_hash"]) is str and HEX32.fullmatch(value["style_pack_hash"]),
                 "trial_producer_recovery_context_mismatch")
        try:
            published = datetime.fromisoformat(value["source_published_at"].replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            raise TrialProducerBlocked("trial_producer_recovery_context_mismatch") from None
        _require(published.utcoffset() is not None and 0 < (
            _window(self.clock(), self.owner.plan) - published).total_seconds() < 86_400,
            "trial_producer_recovery_source_stale")
        try:
            pack = _style_reference_pack(value["style_reference_pack"], client_id="babylon",
                request_id=PRESERVED_BABYLON_REQUEST_ID,
                primary_source_item_id=PRESERVED_BABYLON_SOURCE_ID)
        except (AutomationRepositoryError, TypeError, ValueError):
            raise TrialProducerBlocked("trial_producer_recovery_context_mismatch") from None
        _require(pack.reference_pack_hash == value["style_pack_hash"],
                 "trial_producer_recovery_context_mismatch")
        slot = NaturalSlot(self.workspace_id, "babylon", PRESERVED_BABYLON_JOB_ID,
            PRESERVED_BABYLON_REQUEST_ID, PRESERVED_BABYLON_SOURCE_ID,
            value["source_content"], value["source_url"], value["source_image_url"],
            pack, self.owner.plan.kst_date, self.recovery_receipt_id)
        _require(fixed_natural_input_sha256(slot.job_input(), kst_date=slot.kst_date)
                 == value["input_sha256"] and value["input"] == slot.job_input(),
                 "trial_producer_recovery_context_mismatch")
        return slot

    async def claim_and_prepare(self, slot: NaturalSlot, *, worker_id: str,
                                before_effect: Callable[[], None]) -> ClaimedTrialGeneration:
        """One exact claim or handoff, then read DB context and pin API body."""
        _window(self.clock(), self.owner.plan)
        _require(type(slot) is NaturalSlot and slot.workspace_id == self.workspace_id
                 and slot.client_id in self.owner.plan.eligible_clients
                 and slot.kst_date == self.owner.plan.kst_date
                 and WORKER.fullmatch(worker_id or "") and worker_id == f"ybo-trial:{self.trial_id}"
                 and callable(before_effect))
        _require(slot.client_id == "babylon", "trial_producer_successor_client_unsupported")
        for value in slot.identifiers().values(): _uuid(value)
        _require(type(slot.style_pack) is StyleReferencePack
                 and slot.style_pack.request_id == slot.request_id
                 and slot.style_pack.primary_source_item_id == slot.source_item_id
                 and HEX32.fullmatch(slot.style_pack.reference_pack_hash))
        self._before_effect(before_effect)
        recovery = self.trial_id == TRIAL_ID
        _require((recovery and slot.job_id == PRESERVED_BABYLON_JOB_ID
                  and slot.request_id == PRESERVED_BABYLON_REQUEST_ID
                  and slot.source_item_id == PRESERVED_BABYLON_SOURCE_ID
                  and slot.recovery_receipt_id == self.recovery_receipt_id)
                 or (not recovery and slot.recovery_receipt_id is None),
                 "trial_producer_recovery_identity_mismatch")
        claim_args = {
            "target_workspace_id": slot.workspace_id, "target_client_id": slot.client_id,
            "target_job_id": slot.job_id, "target_request_id": slot.request_id,
            "target_source_item_id": slot.source_item_id,
            "target_input_sha256": fixed_natural_input_sha256(slot.job_input(), kst_date=self.owner.plan.kst_date),
            "target_style_pack_hash": slot.style_pack.reference_pack_hash,
            "target_worker_id": worker_id, "target_lease_seconds": 900}
        if recovery:
            _require(bool(slot.source_image_url), "trial_producer_recovery_image_required")
            claim_args.pop("target_client_id")
            claim_args["target_receipt_id"] = slot.recovery_receipt_id
        raw = await self.repository._rpc(
            "claim_exact_preserved_babylon_job" if recovery else "claim_exact_natural_review_draft_job",
            claim_args)
        _require(raw is not None, "trial_producer_exact_claim_denied")
        job = self.repository._claimed_job(raw, worker_id=worker_id)
        _require(job.job_id == slot.job_id and job.client_id == slot.client_id
                 and job.request_id == slot.request_id
                 and job.primary_source_item_id == slot.source_item_id
                 and job.kst_date == self.owner.plan.kst_date and job.content_kind == "daily_news"
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
        now = _window(self.clock(), self.owner.plan)
        _require(published.utcoffset() is not None and 0 < (now - published).total_seconds() < 86_400,
                 "trial_producer_source_stale")
        image = normalize_x_media_url(slot.source_image_url) if slot.source_image_url else ""
        _require(slot.source_image_url == "" or image, "trial_producer_context_mismatch")
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
        started = _window(self.clock(), self.owner.plan)
        _require(slot.client_id == "babylon" and slot.kst_date == self.owner.plan.kst_date
                 and prepared.worker_id == f"ybo-trial:{self.trial_id}")
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
                    _window(self.clock(), self.owner.plan) - published).total_seconds() < 86_400,
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
                _require(attempts < 30 and _window(self.clock(), self.owner.plan) < started + timedelta(minutes=8),
                         "trial_producer_collect_deadline")
                await self.sleep(15)
        try:
            result = self.studio._validated_generation_result(response,
                client_id=slot.client_id, content_kind="daily_news", request_id=slot.request_id,
                source_image_url=prepared.payload["source_image_url"], template_style="remix",
                allow_logo_only=prepared.payload["source_image_url"] == "")
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
