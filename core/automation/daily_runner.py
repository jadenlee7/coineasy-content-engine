from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from core.automation.content_signals import (
    ContentSignalsError,
    ContentSignalsSnapshot,
    EasyFarmContentSignalsClient,
)
from core.automation.generation_client import (
    GenerationRequestError,
    StudioGenerationClient,
)
from core.automation.mode_router import choose_content_mode, select_official_candidate
from core.automation.models import AutomationState, ClaimedJob, QueueResult
from core.automation.repository import (
    AutomationRepositoryError,
    SupabaseAutomationRepository,
)
from core.automation.settings import AUTOMATION_CLIENTS, AutomationSettings
from core.client_config import load_client_config
from core.sources.x_client import (
    XClient,
    XRateLimitError,
    XRequestError,
    XTransientError,
)


_KST = ZoneInfo("Asia/Seoul")
_MAX_CLAIMS_PER_RUN = 8


class AutomationRepository(Protocol):
    async def get_state(self, **kwargs) -> AutomationState: ...
    async def record_ranking_evidence(self, **kwargs) -> str: ...
    async def record_promotion_candidates(self, **kwargs) -> int: ...
    async def record_sources(self, **kwargs) -> AutomationState: ...
    async def get_or_create_style_reference_pack(self, **kwargs): ...
    async def queue_job(self, **kwargs) -> QueueResult: ...
    async def claim_job(self, **kwargs) -> ClaimedJob | None: ...
    async def complete_job(self, **kwargs) -> None: ...
    async def fail_job(self, **kwargs) -> None: ...


class ContentSignalsProvider(Protocol):
    async def fetch(
        self,
        *,
        client_id: str,
        now: datetime,
    ) -> ContentSignalsSnapshot: ...


@dataclass
class DailyRunSummary:
    kst_date: str
    dry_run: bool
    queued: int = 0
    generated: int = 0
    reused: int = 0
    skipped: int = 0
    errors: int = 0
    outcomes: list[dict[str, str]] = field(default_factory=list)

    def add(self, client_id: str, status: str, detail: str = "") -> None:
        item = {"client_id": client_id, "status": status}
        if detail:
            item["detail"] = detail
        self.outcomes.append(item)

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.errors == 0,
            "kst_date": self.kst_date,
            "dry_run": self.dry_run,
            "queued": self.queued,
            "generated": self.generated,
            "reused": self.reused,
            "skipped": self.skipped,
            "errors": self.errors,
            "outcomes": list(self.outcomes),
        }


class OfficialXDailyRunner:
    def __init__(
        self,
        *,
        settings: AutomationSettings,
        repository: AutomationRepository,
        x_client: XClient,
        generation_client: StudioGenerationClient,
        content_signals_client: ContentSignalsProvider | None = None,
        clients_dir: Path = Path("clients"),
        now_factory=lambda: datetime.now(timezone.utc),
    ):
        if settings.enable_tutorials:
            raise ValueError("scheduled tutorial automation is not enabled")
        self.settings = settings
        self.repository = repository
        self.x_client = x_client
        self.generation_client = generation_client
        self.content_signals_client = content_signals_client
        self.clients_dir = clients_dir
        self.now_factory = now_factory

    async def run(self, *, dry_run: bool = False) -> DailyRunSummary:
        now = self._now()
        kst_date = now.astimezone(_KST).date()
        summary = DailyRunSummary(kst_date=kst_date.isoformat(), dry_run=dry_run)
        worker_id = f"official-x:{uuid.uuid4()}"

        if not dry_run:
            await self._drain_jobs(worker_id, summary)

        for client_id in AUTOMATION_CLIENTS:
            try:
                await self._intake_client(
                    client_id=client_id,
                    kst_date=kst_date,
                    now=now,
                    dry_run=dry_run,
                    allow_queue=(
                        dry_run
                        or summary.queued < self.settings.daily_draft_limit
                    ),
                    summary=summary,
                )
            except XRateLimitError:
                self._error(summary, client_id, "x_rate_limited")
            except XRequestError:
                self._error(summary, client_id, "x_request_rejected")
            except XTransientError:
                self._error(summary, client_id, "x_temporarily_unavailable")
            except AutomationRepositoryError as exc:
                self._error(summary, client_id, exc.code)
            except (FileNotFoundError, ValueError):
                self._error(summary, client_id, "automation_client_configuration_invalid")
            except Exception:
                self._error(summary, client_id, "automation_client_failed")

        if not dry_run:
            await self._drain_jobs(worker_id, summary)
        return summary

    async def _intake_client(
        self,
        *,
        client_id: str,
        kst_date,
        now: datetime,
        dry_run: bool,
        allow_queue: bool,
        summary: DailyRunSummary,
    ) -> None:
        config = load_client_config(client_id, clients_dir=self.clients_dir)
        twitter = config.content_sources.twitter
        if not config.active or twitter is None or not twitter.handle:
            raise ValueError("official X source is not configured")

        state = await self.repository.get_state(
            workspace_id=self.settings.workspace_id,
            client_id=client_id,
            kst_date=kst_date,
        )
        fresh_posts: Sequence[Mapping[str, object]] | None
        try:
            fresh_posts = await self._fetch_posts(
                twitter.handle,
                state.last_cursor,
            )
        except (XRateLimitError, XRequestError, XTransientError) as exc:
            if (
                state.draft_reserved_today
                or not allow_queue
                or not state.pending_sources
            ):
                raise
            summary.add(
                client_id,
                "source_refresh_unavailable",
                self._x_error_code(exc),
            )
            fresh_posts = None

        if fresh_posts is not None and not dry_run:
            state = await self.repository.record_sources(
                workspace_id=self.settings.workspace_id,
                client_id=client_id,
                handle=twitter.handle,
                poll_request_id=str(uuid.uuid4()),
                expected_cursor=state.last_cursor,
                next_cursor=self._newest_cursor(
                    fresh_posts,
                    state.last_cursor,
                ),
                source_items=[
                    self._source_payload(post)
                    for post in fresh_posts
                ],
                polled_at=now,
            )

        if state.draft_reserved_today or not allow_queue:
            summary.skipped += 1
            summary.add(
                client_id,
                "already_reserved"
                if state.draft_reserved_today
                else "local_daily_limit",
            )
            return

        demand_terms = await self._demand_terms(
            client_id=client_id,
            now=now,
            persist=not dry_run,
            summary=summary,
        )
        if dry_run and fresh_posts is not None:
            candidate_posts = self._merge_candidate_posts(
                state.pending_sources,
                fresh_posts,
            )
        else:
            candidate_posts = tuple(
                item.routing_post()
                for item in state.pending_sources
            )
        selected = select_official_candidate(
            candidate_posts,
            skip_patterns=config.routing.skip_patterns,
            demand_terms=demand_terms,
        )

        if selected is None:
            summary.skipped += 1
            summary.add(client_id, "no_candidate")
            return

        decision = choose_content_mode(
            client_id,
            selected,
            enable_tutorials=self.settings.enable_tutorials,
        )
        if dry_run:
            summary.add(client_id, "planned", decision.content_kind)
            return

        source_item_id = selected.get("source_item_id")
        source_content = selected.get("text")
        source_url = selected.get("url")
        source_image_url = selected.get("source_image_url", "")
        if (
            not isinstance(source_item_id, str)
            or not isinstance(source_content, str)
            or not isinstance(source_url, str)
            or not isinstance(source_image_url, str)
        ):
            raise ValueError("recorded source is incomplete")
        request_id = self._request_id(
            client_id=client_id,
            source_item_id=source_item_id,
            content_kind=decision.content_kind,
        )
        await self.repository.get_or_create_style_reference_pack(
            workspace_id=self.settings.workspace_id,
            client_id=client_id,
            request_id=request_id,
            primary_source_item_id=source_item_id,
        )
        queued = await self.repository.queue_job(
            workspace_id=self.settings.workspace_id,
            client_id=client_id,
            kst_date=kst_date,
            source_item_ids=[source_item_id],
            content_kind=decision.content_kind,
            request_id=request_id,
            source_content=source_content,
            source_url=source_url,
            source_image_url=source_image_url,
            manual_only=decision.content_kind == "tutorial",
        )
        if queued.job_id is None:
            summary.skipped += 1
            summary.add(client_id, queued.status)
            return
        summary.queued += 1
        summary.add(client_id, "queued", decision.content_kind)

    async def _demand_terms(
        self,
        *,
        client_id: str,
        now: datetime,
        persist: bool,
        summary: DailyRunSummary,
    ) -> tuple[tuple[str, float], ...]:
        if self.content_signals_client is None:
            return ()
        if not persist:
            summary.add(client_id, "signals_skipped_dry_run")
            return ()
        try:
            snapshot = await self.content_signals_client.fetch(
                client_id=client_id,
                now=now,
            )
        except ContentSignalsError:
            summary.add(client_id, "signals_unavailable")
            return ()
        except Exception:
            summary.add(client_id, "signals_unavailable")
            return ()
        try:
            snapshot_hash = await self.repository.record_ranking_evidence(
                workspace_id=self.settings.workspace_id,
                snapshot=snapshot,
                ranking_version="official-x-demand-v1",
            )
        except Exception:
            summary.add(
                client_id,
                "signals_unavailable",
                "ranking_evidence_not_recorded",
            )
            return ()
        if snapshot.promotion_candidates:
            try:
                recommendation_count = (
                    await self.repository.record_promotion_candidates(
                        workspace_id=self.settings.workspace_id,
                        snapshot=snapshot,
                        snapshot_hash=snapshot_hash,
                    )
                )
                summary.add(
                    client_id,
                    "promotion_candidates_recorded",
                    (
                        f"candidate_count={len(snapshot.promotion_candidates)}"
                        f",recommendation_count={recommendation_count}"
                    ),
                )
            except Exception:
                summary.add(
                    client_id,
                    "promotion_candidates_unavailable",
                    "performance_evidence_not_recorded",
                )
        terms = tuple(
            (item.term, item.weight)
            for item in snapshot.demand_terms
            if item.weight > 0
        )
        summary.add(client_id, "signals_used", f"term_count={len(terms)}")
        return terms

    async def _fetch_posts(
        self,
        handle: str,
        last_cursor: str | None,
    ) -> Sequence[Mapping[str, object]]:
        try:
            return await self.x_client.get_recent_tweets(
                handle,
                hours=self.settings.lookback_hours,
                max_results=200,
                since_id=last_cursor,
                require_complete=True,
            )
        except XRequestError as exc:
            if exc.status_code != 400 or last_cursor is None:
                raise
            return await self.x_client.get_recent_tweets(
                handle,
                hours=self.settings.lookback_hours,
                max_results=200,
                since_id=None,
                require_complete=True,
            )

    async def _drain_jobs(self, worker_id: str, summary: DailyRunSummary) -> None:
        for _ in range(_MAX_CLAIMS_PER_RUN):
            try:
                job = await self.repository.claim_job(
                    workspace_id=self.settings.workspace_id,
                    worker_id=worker_id,
                    lease_seconds=900,
                )
            except AutomationRepositoryError as exc:
                self._error(summary, "worker", exc.code)
                return
            if job is None:
                return
            await self._run_claimed_job(job, worker_id, summary)

    async def _run_claimed_job(
        self,
        job: ClaimedJob,
        worker_id: str,
        summary: DailyRunSummary,
    ) -> None:
        try:
            reference_pack = (
                await self.repository.get_or_create_style_reference_pack(
                    workspace_id=self.settings.workspace_id,
                    client_id=job.client_id,
                    request_id=job.request_id,
                    primary_source_item_id=job.primary_source_item_id,
                )
            )
            result = await self.generation_client.generate(
                client_id=job.client_id,
                content_kind=job.content_kind,
                request_id=job.request_id,
                source_content=job.source_content,
                source_url=job.source_url,
                source_image_url=job.source_image_url,
                template_style="classic",
                style_references=reference_pack.references,
                style_reference_pack_hash=reference_pack.reference_pack_hash,
            )
        except AutomationRepositoryError as exc:
            retry_at = self._retry_at(job.attempts) if exc.retryable else None
            await self._mark_failed(job, worker_id, exc.code, retry_at, summary)
            return
        except GenerationRequestError as exc:
            retry_at = self._retry_at(job.attempts) if exc.retryable else None
            await self._mark_failed(job, worker_id, exc.code, retry_at, summary)
            return
        except ValueError:
            await self._mark_failed(
                job,
                worker_id,
                "automation_job_invalid",
                None,
                summary,
            )
            return
        except Exception:
            await self._mark_failed(
                job,
                worker_id,
                "studio_generation_unavailable",
                self._retry_at(job.attempts),
                summary,
            )
            return

        try:
            await self.repository.complete_job(
                job_id=job.job_id,
                worker_id=worker_id,
                content_item_id=result.content_item_id,
                content_version_id=result.content_version_id,
            )
        except AutomationRepositoryError as exc:
            # The generated catalog is already durable. Leave the lease alone;
            # its next claimant uses the same request UUID and receives the
            # idempotently stored result before completing the DB link.
            self._error(summary, job.client_id, exc.code)
            return
        summary.generated += 1
        if result.reused:
            summary.reused += 1
        summary.add(job.client_id, "needs_review", job.content_kind)

    async def _mark_failed(
        self,
        job: ClaimedJob,
        worker_id: str,
        code: str,
        retry_at: datetime | None,
        summary: DailyRunSummary,
    ) -> None:
        try:
            await self.repository.fail_job(
                job_id=job.job_id,
                worker_id=worker_id,
                error_code=code,
                retryable=retry_at is not None,
                retry_at=retry_at,
            )
        except AutomationRepositoryError as exc:
            self._error(summary, job.client_id, exc.code)
            return
        self._error(
            summary,
            job.client_id,
            "generation_retry_scheduled" if retry_at else "generation_failed",
        )

    def _request_id(
        self,
        *,
        client_id: str,
        source_item_id: str,
        content_kind: str,
    ) -> str:
        namespace = uuid.UUID(self.settings.workspace_id)
        return str(uuid.uuid5(
            namespace,
            f"official-x-review:v1:{client_id}:{source_item_id}:{content_kind}",
        ))

    def _retry_at(self, attempts: int) -> datetime:
        minutes = min(30, 5 * (2 ** max(0, min(attempts - 1, 3))))
        return self._now() + timedelta(minutes=minutes)

    def _now(self) -> datetime:
        value = self.now_factory()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("automation clock must return a timezone-aware datetime")
        return value

    @staticmethod
    def _newest_cursor(
        posts: Sequence[Mapping[str, object]],
        previous: str | None,
    ) -> str | None:
        ids = [
            value
            for post in posts
            if isinstance((value := post.get("id")), str) and value.isdigit()
        ]
        if previous:
            ids.append(previous)
        return max(ids, key=int) if ids else None

    @staticmethod
    def _source_payload(post: Mapping[str, object]) -> dict[str, object]:
        return {
            "external_id": post.get("id"),
            "source_content": post.get("text"),
            "source_url": post.get("url"),
            "source_image_url": post.get("source_image_url", ""),
            "published_at": post.get("created_at"),
            "media": post.get("media", []),
            "metrics": post.get("metrics", {}),
            "is_note_tweet": post.get("is_note_tweet") is True,
        }

    @staticmethod
    def _merge_candidate_posts(
        pending_sources,
        fresh_posts: Sequence[Mapping[str, object]],
    ) -> tuple[Mapping[str, object], ...]:
        by_id = {
            item.post_id: item.routing_post()
            for item in pending_sources
        }
        for post in fresh_posts:
            post_id = post.get("id")
            if isinstance(post_id, str) and post_id.isdigit():
                by_id[post_id] = post
        return tuple(
            by_id[post_id]
            for post_id in sorted(by_id, key=int)
        )

    @staticmethod
    def _x_error_code(exc: Exception) -> str:
        if isinstance(exc, XRateLimitError):
            return "x_rate_limited"
        if isinstance(exc, XRequestError):
            return "x_request_rejected"
        return "x_temporarily_unavailable"

    @staticmethod
    def _error(summary: DailyRunSummary, client_id: str, code: str) -> None:
        summary.errors += 1
        summary.add(client_id, "error", code)


def build_daily_runner(settings: AutomationSettings) -> OfficialXDailyRunner:
    signals_client = None
    if (
        settings.easyfarm_content_signals_url is not None
        and settings.easyfarm_content_signals_token is not None
    ):
        signals_client = EasyFarmContentSignalsClient(
            endpoint_url=settings.easyfarm_content_signals_url,
            token=settings.easyfarm_content_signals_token,
            window_days=settings.easyfarm_content_signals_window_days,
        )
    return OfficialXDailyRunner(
        settings=settings,
        repository=SupabaseAutomationRepository(
            supabase_url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
        ),
        x_client=XClient(settings.x_bearer_token),
        generation_client=StudioGenerationClient(
            base_url=settings.studio_base_url,
            automation_token=settings.studio_automation_token,
        ),
        content_signals_client=signals_client,
    )
