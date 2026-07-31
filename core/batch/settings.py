from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping
from zoneinfo import ZoneInfo

from core.automation.settings import _supabase_url


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

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        force_dry_run: bool = False,
        require_openai_api_key: bool = True,
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
        if (
            not daily_cap.is_finite()
            or daily_cap < Decimal("0.10")
            or daily_cap > Decimal("6.00")
        ):
            raise ValueError(
                "BATCH_DAILY_CAP_USD must be between 0.10 and 6.00"
            )

        values: dict[str, object] = {
            "mode": mode,
            "allowed_clients": allowed_clients,
            "daily_cap_usd": daily_cap.quantize(Decimal("0.01")),
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
        if mode == "live":
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
        timezone = env.get("BATCH_TIMEZONE", "Asia/Seoul").strip()
        if timezone != "Asia/Seoul":
            raise ValueError("BATCH_TIMEZONE must be Asia/Seoul")
        values["timezone"] = timezone
        return cls(**values)

    def experiment_phase(self, now: datetime) -> str:
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
        return {
            "mode": self.mode,
            "allowed_clients": sorted(self.allowed_clients),
            "daily_cap_usd": str(self.daily_cap_usd),
            "max_claims": self.max_claims,
            "max_requests_per_batch": self.max_requests_per_batch,
            "sync_fallback": "manual_only",
            "auto_publish": False,
            "provider_calls": self.mode == "live",
        }
