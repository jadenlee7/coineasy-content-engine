from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping
from zoneinfo import ZoneInfo

from core.automation.settings import _supabase_url
from core.batch.canary import (
    CanaryConfigApproval,
    CanaryDispatchApproval,
    PRODUCTION_SHADOW_APPROVAL_TTL,
    canonical_sha256,
    config_subject,
)


_CLIENTS = frozenset({"yellow", "origintrail", "squid", "babylon"})
_MODES = frozenset({"off", "dry_run", "live"})
_KST = ZoneInfo("Asia/Seoul")


def _bounded_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(env.get(name, str(default)).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _secret(
    env: Mapping[str, str],
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> str:
    value = env.get(name, "").strip()
    if not minimum <= len(value) <= maximum:
        raise ValueError(f"{name} is required in live mode")
    return value


def _pilot_timestamp(env: Mapping[str, str], name: str) -> datetime:
    value = _secret(env, name, minimum=20, maximum=35)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class BatchSettings:
    mode: str
    allowed_clients: frozenset[str]
    daily_cap_usd: Decimal
    max_claims: int
    max_requests_per_batch: int
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    workspace_id: str | None = None
    openai_api_key: str | None = None
    timezone: str = "Asia/Seoul"
    experiment_start_at: datetime | None = None
    experiment_end_at: datetime | None = None
    canary_environment: str | None = None
    runtime_environment: str | None = None
    canary_release_sha: str | None = None
    runtime_release_sha: str | None = None
    canary_subject_sha256: str | None = None
    canary_approval: CanaryConfigApproval | None = None
    canary_dispatch_approval: CanaryDispatchApproval | None = None
    production_shadow_auto_dispatch: bool = False

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        force_dry_run: bool = False,
        require_openai_api_key: bool = True,
        _require_canary_receipt: bool = True,
    ) -> "BatchSettings":
        env = os.environ if environ is None else environ
        configured_mode = env.get(
            "BATCH_EXPERIMENT_MODE",
            "dry_run",
        ).strip().lower()
        if configured_mode not in _MODES:
            raise ValueError("BATCH_EXPERIMENT_MODE must be off, dry_run, or live")
        mode = "dry_run" if force_dry_run else configured_mode

        raw_clients = env.get("BATCH_ALLOWED_CLIENTS", "origintrail")
        allowed_clients = frozenset(
            value.strip().lower()
            for value in raw_clients.split(",")
            if value.strip()
        )
        if not allowed_clients or not allowed_clients <= _CLIENTS:
            raise ValueError("BATCH_ALLOWED_CLIENTS contains an unsupported client")

        try:
            daily_cap = Decimal(
                env.get("BATCH_DAILY_CAP_USD", "0.50").strip()
            )
        except InvalidOperation as exc:
            raise ValueError("BATCH_DAILY_CAP_USD must be a decimal") from exc
        if not daily_cap.is_finite():
            raise ValueError(
                "BATCH_DAILY_CAP_USD must be exact cents between 0.05 and 6.00"
            )
        normalized_daily_cap = daily_cap.quantize(Decimal("0.01"))
        if (
            daily_cap != normalized_daily_cap
            or daily_cap < Decimal("0.05")
            or daily_cap > Decimal("6.00")
        ):
            raise ValueError(
                "BATCH_DAILY_CAP_USD must be exact cents between 0.05 and 6.00"
            )

        values: dict[str, object] = {
            "mode": mode,
            "allowed_clients": allowed_clients,
            "daily_cap_usd": normalized_daily_cap,
            "max_claims": _bounded_int(
                env,
                "BATCH_MAX_CLAIMS",
                1,
                minimum=1,
                maximum=100,
            ),
            "max_requests_per_batch": _bounded_int(
                env,
                "BATCH_MAX_REQUESTS_PER_BATCH",
                1,
                minimum=1,
                maximum=1,
            ),
        }
        timezone_name = env.get("BATCH_TIMEZONE", "Asia/Seoul").strip()
        if timezone_name != "Asia/Seoul":
            raise ValueError("BATCH_TIMEZONE must be Asia/Seoul")
        values["timezone"] = timezone_name

        raw_auto_dispatch = env.get(
            "BATCH_PRODUCTION_SHADOW_AUTO_DISPATCH",
            "false",
        )
        if raw_auto_dispatch not in {"true", "false"}:
            raise ValueError(
                "BATCH_PRODUCTION_SHADOW_AUTO_DISPATCH must be true or false"
            )
        production_shadow_auto_dispatch = raw_auto_dispatch == "true"
        values["production_shadow_auto_dispatch"] = (
            production_shadow_auto_dispatch
        )

        if mode == "live":
            if env.get("BATCH_CANARY_ENABLED", "false") != "true":
                raise ValueError("BATCH_CANARY_ENABLED must be true in live mode")
            experiment_start_at = _pilot_timestamp(
                env,
                "BATCH_EXPERIMENT_START_AT",
            )
            experiment_end_at = _pilot_timestamp(
                env,
                "BATCH_EXPERIMENT_END_AT",
            )
            start_kst = experiment_start_at.astimezone(_KST)
            end_kst = experiment_end_at.astimezone(_KST)
            if (
                experiment_end_at <= experiment_start_at
                or experiment_end_at - experiment_start_at
                > timedelta(days=14)
                or any((
                    start_kst.hour,
                    start_kst.minute,
                    start_kst.second,
                    start_kst.microsecond,
                    end_kst.hour,
                    end_kst.minute,
                    end_kst.second,
                    end_kst.microsecond,
                ))
            ):
                raise ValueError(
                    "Batch experiment window must span at most 14 KST days "
                    "between exact KST midnights"
                )
            workspace_id = _secret(
                env,
                "CONTENT_STUDIO_WORKSPACE_ID",
                minimum=36,
                maximum=36,
            )
            try:
                workspace_id = str(uuid.UUID(workspace_id))
            except ValueError as exc:
                raise ValueError(
                    "CONTENT_STUDIO_WORKSPACE_ID must be a UUID"
                ) from exc
            values.update({
                "supabase_url": _supabase_url(
                    _secret(
                        env,
                        "SUPABASE_URL",
                        minimum=8,
                        maximum=2_048,
                    )
                ),
                "supabase_service_role_key": _secret(
                    env,
                    "SUPABASE_SERVICE_ROLE_KEY",
                    minimum=32,
                    maximum=8_192,
                ),
                "workspace_id": workspace_id,
                "experiment_start_at": experiment_start_at,
                "experiment_end_at": experiment_end_at,
            })
            if require_openai_api_key:
                values["openai_api_key"] = _secret(
                    env,
                    "OPENAI_API_KEY",
                    minimum=20,
                    maximum=512,
                )
            canary_environment = _secret(
                env,
                "BATCH_CANARY_ENVIRONMENT",
                minimum=3,
                maximum=32,
            )
            if canary_environment not in {"staging", "production"}:
                raise ValueError(
                    "BATCH_CANARY_ENVIRONMENT must be staging or production"
                )
            runtime_environment = _secret(
                env,
                "RAILWAY_ENVIRONMENT_NAME",
                minimum=3,
                maximum=32,
            )
            if runtime_environment != canary_environment:
                raise ValueError(
                    "BATCH_CANARY_ENVIRONMENT must match "
                    "RAILWAY_ENVIRONMENT_NAME"
                )
            canary_release_sha = _secret(
                env,
                "BATCH_CANARY_RELEASE_SHA",
                minimum=40,
                maximum=40,
            )
            runtime_release_sha = _secret(
                env,
                "RAILWAY_GIT_COMMIT_SHA",
                minimum=40,
                maximum=40,
            )
            if runtime_release_sha != canary_release_sha:
                raise ValueError(
                    "BATCH_CANARY_RELEASE_SHA must match "
                    "RAILWAY_GIT_COMMIT_SHA"
                )
            subject = config_subject(
                environment=canary_environment,
                release_sha=canary_release_sha,
                supabase_url=values["supabase_url"],
                workspace_id=workspace_id,
                allowed_clients=allowed_clients,
                daily_cap_usd=normalized_daily_cap,
                max_claims=values["max_claims"],
                max_requests_per_batch=values["max_requests_per_batch"],
                experiment_start_at=experiment_start_at,
                experiment_end_at=experiment_end_at,
                timezone_name=timezone_name,
                production_shadow_auto_dispatch=(
                    production_shadow_auto_dispatch
                ),
            )
            subject_sha256 = canonical_sha256(subject)
            approval = None
            if _require_canary_receipt:
                approval = CanaryConfigApproval.from_json(
                    _secret(
                        env,
                        "BATCH_CANARY_APPROVAL_RECEIPT",
                        minimum=64,
                        maximum=8_192,
                    ),
                    expected_subject_sha256=subject_sha256,
                    maximum_ttl=(
                        PRODUCTION_SHADOW_APPROVAL_TTL
                        if production_shadow_auto_dispatch
                        else timedelta(hours=2)
                    ),
                )
            raw_dispatch_receipt = env.get(
                "BATCH_CANARY_DISPATCH_RECEIPT",
                "",
            ).strip()
            dispatch_approval = None
            if raw_dispatch_receipt:
                if approval is None:
                    raise ValueError(
                        "Batch canary dispatch receipt requires config approval"
                    )
                dispatch_approval = CanaryDispatchApproval.from_json(
                    raw_dispatch_receipt,
                    config_subject_sha256=subject_sha256,
                    config_approval_id=approval.approval_id,
                )
            values.update({
                "canary_environment": canary_environment,
                "runtime_environment": runtime_environment,
                "canary_release_sha": canary_release_sha,
                "runtime_release_sha": runtime_release_sha,
                "canary_subject_sha256": subject_sha256,
                "canary_approval": approval,
                "canary_dispatch_approval": dispatch_approval,
            })
        return cls(**values)

    @classmethod
    def canary_subject_from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> tuple[dict[str, object], str]:
        settings = cls.from_env(
            environ,
            require_openai_api_key=False,
            _require_canary_receipt=False,
        )
        subject = settings.canary_subject()
        return subject, canonical_sha256(subject)

    def canary_subject(self) -> dict[str, object]:
        if (
            self.mode != "live"
            or self.supabase_url is None
            or self.workspace_id is None
            or self.experiment_start_at is None
            or self.experiment_end_at is None
            or self.canary_environment is None
            or self.runtime_environment != self.canary_environment
            or self.canary_release_sha is None
            or self.runtime_release_sha != self.canary_release_sha
        ):
            raise ValueError("live Batch canary settings are incomplete")
        return config_subject(
            environment=self.canary_environment,
            release_sha=self.canary_release_sha,
            supabase_url=self.supabase_url,
            workspace_id=self.workspace_id,
            allowed_clients=self.allowed_clients,
            daily_cap_usd=self.daily_cap_usd,
            max_claims=self.max_claims,
            max_requests_per_batch=self.max_requests_per_batch,
            experiment_start_at=self.experiment_start_at,
            experiment_end_at=self.experiment_end_at,
            timezone_name=self.timezone,
            production_shadow_auto_dispatch=(
                self.production_shadow_auto_dispatch
            ),
        )

    def assert_canary_config_authorized(self) -> None:
        if self.mode != "live":
            return
        expected = canonical_sha256(self.canary_subject())
        if (
            self.canary_approval is None
            or self.canary_subject_sha256 is None
            or self.canary_subject_sha256 != expected
            or self.canary_approval.subject_sha256 != expected
        ):
            raise ValueError("live Batch canary approval is missing or invalid")

    def experiment_window_phase(self, now: datetime) -> str:
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("Batch experiment clock must be timezone-aware")
        if self.mode != "live":
            return "disabled"
        if (
            self.experiment_start_at is None
            or self.experiment_end_at is None
        ):
            raise ValueError("live Batch experiment window is incomplete")
        if now < self.experiment_start_at:
            return "not_started"
        if now >= self.experiment_end_at:
            return "expired"
        return "active"

    def experiment_phase(self, now: datetime) -> str:
        window_phase = self.experiment_window_phase(now)
        if window_phase != "active":
            return window_phase
        self.assert_canary_config_authorized()
        approval_phase = self.canary_approval.phase(now)
        return (
            "active"
            if approval_phase == "active"
            else f"authorization_{approval_phase}"
        )

    def dispatch_phase(self, now: datetime) -> str:
        window_phase = self.experiment_window_phase(now)
        if window_phase != "active":
            return window_phase
        self.assert_canary_config_authorized()
        config_phase = self.canary_approval.phase(now)
        if config_phase != "active":
            return f"config_authorization_{config_phase}"
        if self.canary_dispatch_approval is None:
            return (
                "active"
                if self.production_shadow_auto_dispatch
                else "not_configured"
            )
        approval_phase = self.canary_dispatch_approval.phase(now)
        return (
            "active"
            if approval_phase == "active"
            else f"authorization_{approval_phase}"
        )

    def submission_not_after(self) -> datetime:
        """Return the earliest hard boundary for creating a provider Batch."""
        self.assert_canary_config_authorized()
        if self.experiment_end_at is None or self.canary_approval is None:
            raise ValueError("live Batch dispatch approval is incomplete")
        if (
            self.production_shadow_auto_dispatch
            and self.canary_dispatch_approval is None
        ):
            return min(
                self.experiment_end_at,
                self.canary_approval.expires_at,
            )
        if self.canary_dispatch_approval is None:
            raise ValueError("live Batch dispatch approval is incomplete")
        return min(
            self.experiment_end_at - timedelta(hours=26),
            self.canary_approval.expires_at,
            self.canary_dispatch_approval.expires_at,
        )

    def submission_deadline_safe(self, now: datetime) -> bool:
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("Batch submission clock must be timezone-aware")
        if self.experiment_end_at is None:
            raise ValueError("live Batch experiment window is incomplete")
        required_slack = (
            timedelta(0)
            if self.production_shadow_auto_dispatch
            else timedelta(hours=26)
        )
        return self.experiment_end_at - now > required_slack

    @staticmethod
    def budget_key(kst_date: date) -> str:
        if not isinstance(kst_date, date) or isinstance(kst_date, datetime):
            raise ValueError("kst_date must be a date")
        return f"batch-general:{kst_date.isoformat()}"

    @staticmethod
    def budget_window(kst_date: date) -> tuple[datetime, datetime]:
        if not isinstance(kst_date, date) or isinstance(kst_date, datetime):
            raise ValueError("kst_date must be a date")
        start = datetime.combine(kst_date, time.min, tzinfo=_KST)
        end = start + timedelta(days=1)
        return (
            start.astimezone(timezone.utc),
            end.astimezone(timezone.utc),
        )

    def public_summary(self) -> dict[str, object]:
        summary = {
            "mode": self.mode,
            "allowed_clients": sorted(self.allowed_clients),
            "daily_cap_usd": str(self.daily_cap_usd),
            "max_claims": self.max_claims,
            "max_requests_per_batch": self.max_requests_per_batch,
            "sync_fallback": "manual_only",
            "auto_publish": False,
            "provider_calls": self.mode == "live",
        }
        if self.mode == "live":
            self.assert_canary_config_authorized()
            summary.update({
                "canary_environment": self.canary_environment,
                "runtime_environment_verified": (
                    self.runtime_environment == self.canary_environment
                ),
                "canary_release_sha": self.canary_release_sha,
                "runtime_release_verified": (
                    self.runtime_release_sha == self.canary_release_sha
                ),
                "canary_subject_sha256": self.canary_subject_sha256,
                "canary_approval_id": self.canary_approval.approval_id,
                "canary_dispatch_configured": (
                    self.canary_dispatch_approval is not None
                ),
                "production_shadow_auto_dispatch": (
                    self.production_shadow_auto_dispatch
                ),
                "max_provider_batches_per_kst_day": 1,
                "authorized_provider_batches": (
                    7 if self.production_shadow_auto_dispatch else 1
                ),
            })
        return summary
