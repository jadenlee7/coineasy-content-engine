from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.batch.settings import BatchSettings
from core.batch.dispatcher import BatchDispatchSummary
import scripts.run_batch_dispatcher as batch_script
from scripts.run_batch_dispatcher import _experiment_phase


def _live_env(**overrides):
    values = {
        "BATCH_EXPERIMENT_MODE": "live",
        "BATCH_ALLOWED_CLIENTS": "squid",
        "BATCH_DAILY_CAP_USD": "6.00",
        "BATCH_EXPERIMENT_START_AT": "2026-08-01T00:00:00+09:00",
        "BATCH_EXPERIMENT_END_AT": "2026-08-15T00:00:00+09:00",
        "SUPABASE_URL": "https://project-ref.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "s" * 64,
        "CONTENT_STUDIO_WORKSPACE_ID": (
            "00000000-0000-4000-8000-000000000001"
        ),
        "OPENAI_API_KEY": "sk-test-key-that-is-long-enough",
    }
    values.update(overrides)
    return values


def test_first_experiment_is_squid_dry_run_by_default():
    settings = BatchSettings.from_env({})

    assert settings.mode == "dry_run"
    assert settings.allowed_clients == frozenset({"squid"})
    assert settings.daily_cap_usd == Decimal("0.50")
    assert settings.max_claims == 1
    assert settings.max_requests_per_batch == 1
    assert settings.openai_api_key is None
    assert settings.public_summary()["sync_fallback"] == "manual_only"
    assert settings.public_summary()["auto_publish"] is False


def test_live_mode_requires_separate_provider_and_ledger_credentials():
    settings = BatchSettings.from_env(_live_env())

    assert settings.mode == "live"
    assert settings.openai_api_key is not None
    assert settings.supabase_service_role_key is not None
    assert settings.workspace_id == "00000000-0000-4000-8000-000000000001"
    assert settings.experiment_start_at == datetime(
        2026,
        7,
        31,
        15,
        tzinfo=timezone.utc,
    )
    assert settings.experiment_end_at == datetime(
        2026,
        8,
        14,
        15,
        tzinfo=timezone.utc,
    )


def test_live_producer_parses_without_loading_openai_api_key():
    env = _live_env()
    env.pop("OPENAI_API_KEY")

    settings = BatchSettings.from_env(
        env,
        require_openai_api_key=False,
    )

    assert settings.mode == "live"
    assert settings.openai_api_key is None
    assert settings.supabase_service_role_key is not None
    assert settings.experiment_phase(datetime(
        2026,
        8,
        1,
        tzinfo=timezone.utc,
    )) == "active"
    assert settings.budget_key(
        date(2026, 8, 1)
    ) == "batch-general:2026-08-01"
    assert settings.budget_window(date(2026, 8, 1)) == (
        datetime(2026, 7, 31, 15, tzinfo=timezone.utc),
        datetime(2026, 8, 1, 15, tzinfo=timezone.utc),
    )


def test_non_live_batch_phase_is_disabled():
    settings = BatchSettings.from_env({})

    assert settings.experiment_phase(datetime.now(timezone.utc)) == "disabled"


@pytest.mark.parametrize(
    "now,expected",
    [
        (datetime(2026, 7, 31, 14, 59, tzinfo=timezone.utc), "not_started"),
        (datetime(2026, 7, 31, 15, 0, tzinfo=timezone.utc), "active"),
        (datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc), "expired"),
    ],
)
def test_live_pilot_window_is_time_bounded(now, expected):
    settings = BatchSettings.from_env(_live_env())

    assert _experiment_phase(settings, now) == expected


@pytest.mark.asyncio
async def test_expired_worker_polls_and_cleans_without_new_submission(
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    settings = BatchSettings(
        mode="live",
        allowed_clients=frozenset({"squid"}),
        daily_cap_usd=Decimal("0.50"),
        max_claims=1,
        max_requests_per_batch=1,
        supabase_url="https://project-ref.supabase.co",
        supabase_service_role_key="s" * 64,
        workspace_id="00000000-0000-4000-8000-000000000001",
        openai_api_key="sk-test-key-that-is-long-enough",
        experiment_start_at=now - timedelta(days=2),
        experiment_end_at=now - timedelta(days=1),
    )
    calls = []

    class Repository:
        async def configure_daily_budget(self, **_kwargs):
            raise AssertionError("expired mode must not configure a budget")

    class Dispatcher:
        async def poll_once(self):
            calls.append("poll")
            return BatchDispatchSummary()

        async def cleanup_expired_once(self, *, summary):
            calls.append("cleanup")
            summary.expired_unsubmitted += 1
            return summary

    monkeypatch.setattr(
        batch_script,
        "SupabaseBatchRepository",
        lambda **_kwargs: Repository(),
    )
    monkeypatch.setattr(
        batch_script,
        "build_batch_dispatcher",
        lambda **_kwargs: Dispatcher(),
    )

    result = await batch_script._run_live(settings)

    assert calls == ["poll", "cleanup"]
    assert result["experiment_phase"] == "expired"
    assert result["submissions_enabled"] is False
    assert result["expired_unsubmitted"] == 1


def test_cli_dry_run_override_never_loads_live_secrets():
    settings = BatchSettings.from_env(
        {"BATCH_EXPERIMENT_MODE": "live"},
        force_dry_run=True,
    )
    assert settings.mode == "dry_run"
    assert settings.openai_api_key is None
    assert settings.public_summary()["provider_calls"] is False


@pytest.mark.parametrize(
    "override, message",
    [
        ({"BATCH_EXPERIMENT_MODE": "auto"}, "off, dry_run, or live"),
        ({"BATCH_ALLOWED_CLIENTS": "squid,unknown"}, "unsupported"),
        ({"BATCH_DAILY_CAP_USD": "0.09"}, "between"),
        ({"BATCH_DAILY_CAP_USD": "6.01"}, "between"),
        ({"BATCH_DAILY_CAP_USD": "nan"}, "between"),
        ({"BATCH_MAX_CLAIMS": "101"}, "between"),
        ({"BATCH_MAX_REQUESTS_PER_BATCH": "0"}, "between"),
        ({"BATCH_MAX_REQUESTS_PER_BATCH": "2"}, "between"),
        ({"BATCH_TIMEZONE": "UTC"}, "Asia/Seoul"),
        ({"BATCH_EXPERIMENT_START_AT": ""}, "START_AT"),
        (
            {"BATCH_EXPERIMENT_START_AT": "2026-08-01T00:01:00+09:00"},
            "exact KST midnights",
        ),
        (
            {"BATCH_EXPERIMENT_END_AT": "2026-08-16T00:00:00+09:00"},
            "at most 14 KST days",
        ),
        ({"OPENAI_API_KEY": "short"}, "OPENAI_API_KEY"),
    ],
)
def test_live_settings_fail_closed(override, message):
    with pytest.raises(ValueError, match=message):
        BatchSettings.from_env(_live_env(**override))
