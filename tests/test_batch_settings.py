from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from core.batch.canary import (
    CONFIG_SCHEMA,
    DISPATCH_SCHEMA,
    canonical_sha256,
    dispatch_subject,
    pilot_day_authorization,
)
from core.batch.settings import BatchSettings
from core.batch.dispatcher import BatchDispatchSummary
import scripts.run_batch_dispatcher as batch_script
from scripts.run_batch_dispatcher import _experiment_phase


def _live_env(**overrides):
    values = {
        "BATCH_EXPERIMENT_MODE": "live",
        "BATCH_ALLOWED_CLIENTS": "origintrail",
        "BATCH_DAILY_CAP_USD": "0.05",
        "BATCH_MAX_CLAIMS": "1",
        "BATCH_MAX_REQUESTS_PER_BATCH": "1",
        "BATCH_EXPERIMENT_START_AT": "2026-08-01T00:00:00+09:00",
        "BATCH_EXPERIMENT_END_AT": "2026-08-03T00:00:00+09:00",
        "BATCH_CANARY_ENABLED": "true",
        "BATCH_CANARY_ENVIRONMENT": "staging",
        "RAILWAY_ENVIRONMENT_NAME": "staging",
        "BATCH_CANARY_RELEASE_SHA": "a" * 40,
        "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
        "SUPABASE_URL": "https://project-ref.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "s" * 64,
        "CONTENT_STUDIO_WORKSPACE_ID": (
            "00000000-0000-4000-8000-000000000001"
        ),
        "OPENAI_API_KEY": "sk-test-key-that-is-long-enough",
    }
    values.update(overrides)
    if "BATCH_CANARY_APPROVAL_RECEIPT" not in overrides:
        _subject, digest = BatchSettings.canary_subject_from_env(values)
        values["BATCH_CANARY_APPROVAL_RECEIPT"] = json.dumps({
            "version": CONFIG_SCHEMA,
            "approval_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "approved_by": "operator:test",
            "approved_at": "2026-07-31T23:00:00Z",
            "expires_at": "2026-08-01T01:00:00Z",
            "subject_sha256": digest,
        })
    return values


def _active_live_env(now: datetime) -> dict[str, str]:
    start = now.astimezone(ZoneInfo("Asia/Seoul")).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    values = _live_env(
        BATCH_EXPERIMENT_START_AT=start.isoformat(),
        BATCH_EXPERIMENT_END_AT=(start + timedelta(hours=48)).isoformat(),
        BATCH_CANARY_APPROVAL_RECEIPT="",
    )
    _subject, digest = BatchSettings.canary_subject_from_env(values)
    approved_at = now - timedelta(minutes=5)
    expires_at = now + timedelta(hours=1)
    config_approval_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    values["BATCH_CANARY_APPROVAL_RECEIPT"] = json.dumps({
        "version": CONFIG_SCHEMA,
        "approval_id": config_approval_id,
        "approved_by": "operator:test",
        "approved_at": approved_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "subject_sha256": digest,
    })
    job_id = "11111111-1111-4111-8111-111111111111"
    input_sha256 = "c" * 64
    request_sha256 = "d" * 64
    subject = dispatch_subject(
        config_subject_sha256=digest,
        config_approval_id=config_approval_id,
        job_id=job_id,
        input_sha256=input_sha256,
        request_sha256=request_sha256,
    )
    values["BATCH_CANARY_DISPATCH_RECEIPT"] = json.dumps({
        "version": DISPATCH_SCHEMA,
        "dispatch_approval_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "approved_by": "operator:test",
        "approved_at": approved_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "job_id": job_id,
        "input_sha256": input_sha256,
        "request_sha256": request_sha256,
        "subject_sha256": canonical_sha256(subject),
    })
    return values


def _active_shadow_env(now: datetime) -> dict[str, str]:
    start = now.astimezone(ZoneInfo("Asia/Seoul")).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    values = _live_env(
        BATCH_EXPERIMENT_START_AT=start.isoformat(),
        BATCH_EXPERIMENT_END_AT=(start + timedelta(days=7)).isoformat(),
        BATCH_PRODUCTION_SHADOW_AUTO_DISPATCH="true",
        BATCH_CANARY_APPROVAL_RECEIPT="",
        BATCH_CANARY_DISPATCH_RECEIPT="",
    )
    subject, digest = BatchSettings.canary_subject_from_env(values)
    values["BATCH_CANARY_APPROVAL_RECEIPT"] = json.dumps({
        "version": CONFIG_SCHEMA,
        "approval_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "approved_by": "coineasy-owner:test",
        "approved_at": (now - timedelta(minutes=5)).isoformat(),
        "expires_at": (start + timedelta(days=7)).isoformat(),
        "subject_sha256": digest,
    })
    assert subject["authorized_provider_batches"] == 7
    return values


def test_first_experiment_is_origintrail_dry_run_by_default():
    settings = BatchSettings.from_env({})

    assert settings.mode == "dry_run"
    assert settings.allowed_clients == frozenset({"origintrail"})
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
        2,
        15,
        tzinfo=timezone.utc,
    )


def test_live_mode_accepts_exact_production_runtime_binding():
    settings = BatchSettings.from_env(_live_env(
        BATCH_CANARY_ENVIRONMENT="production",
        RAILWAY_ENVIRONMENT_NAME="production",
    ))

    assert settings.canary_environment == "production"
    assert settings.runtime_environment == "production"
    assert settings.public_summary()["runtime_environment_verified"] is True


def test_seven_day_production_shadow_is_daily_bounded_and_auto_dispatches():
    now = datetime(2026, 8, 1, 1, tzinfo=timezone.utc)
    settings = BatchSettings.from_env(_active_shadow_env(now))

    assert settings.production_shadow_auto_dispatch is True
    assert settings.canary_dispatch_approval is None
    assert settings.dispatch_phase(now) == "active"
    assert settings.submission_deadline_safe(now) is True
    assert settings.submission_not_after() == settings.experiment_end_at
    assert settings.public_summary()["authorized_provider_batches"] == 7
    assert settings.public_summary()["max_provider_batches_per_kst_day"] == 1
    assert settings.public_summary()["auto_publish"] is False


def test_seven_day_shadow_rejects_wrong_window_and_flag_spelling():
    now = datetime(2026, 8, 1, 1, tzinfo=timezone.utc)
    env = _active_shadow_env(now)
    start = datetime.fromisoformat(env["BATCH_EXPERIMENT_START_AT"])
    env["BATCH_EXPERIMENT_END_AT"] = (start + timedelta(days=6)).isoformat()
    with pytest.raises(ValueError, match="exactly 7 days"):
        BatchSettings.canary_subject_from_env(env)

    env = _active_shadow_env(now)
    env["BATCH_PRODUCTION_SHADOW_AUTO_DISPATCH"] = "TRUE"
    with pytest.raises(ValueError, match="must be true or false"):
        BatchSettings.from_env(env)


def test_daily_shadow_authorization_is_deterministic_and_exact():
    arguments = {
        "pilot_subject_sha256": "a" * 64,
        "pilot_approval_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "kst_date": date(2026, 8, 5),
        "job_id": "11111111-1111-4111-8111-111111111111",
        "input_sha256": "c" * 64,
        "request_sha256": "d" * 64,
    }

    first = pilot_day_authorization(**arguments)
    second = pilot_day_authorization(**arguments)

    assert first == second
    assert first["kst_date"] == "2026-08-05"
    assert first["job_id"] == arguments["job_id"]
    assert len(first["config_subject_sha256"]) == 64
    assert len(first["dispatch_subject_sha256"]) == 64
    assert first["config_approval_id"] != first["dispatch_approval_id"]


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
        (datetime(2026, 8, 2, 15, 0, tzinfo=timezone.utc), "expired"),
    ],
)
def test_live_pilot_window_is_time_bounded(now, expected):
    settings = BatchSettings.from_env(_live_env())

    assert _experiment_phase(settings, now) == expected


@pytest.mark.asyncio
async def test_expired_worker_polls_and_cleans_without_new_submission(
    monkeypatch,
):
    settings = BatchSettings.from_env(_live_env(
        BATCH_EXPERIMENT_START_AT="2025-08-01T00:00:00+09:00",
        BATCH_EXPERIMENT_END_AT="2025-08-03T00:00:00+09:00",
    ))
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

    result = await batch_script._run_live(settings, poll_only=False)

    assert calls == ["poll", "cleanup"]
    assert result["experiment_phase"] == "expired"
    assert result["submissions_enabled"] is False
    assert result["expired_unsubmitted"] == 1


@pytest.mark.asyncio
async def test_live_worker_polls_then_registers_exact_grant_before_claim(
    monkeypatch,
):
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    settings = BatchSettings.from_env(_active_live_env(now))
    calls = []
    built = {}

    class Clock:
        @classmethod
        def now(cls, _timezone):
            return now

    class Repository:
        async def configure_daily_budget(self, **kwargs):
            calls.append(("budget", kwargs))

        async def configure_canary_grant(self, **kwargs):
            calls.append(("grant", kwargs))

    class Dispatcher:
        async def poll_once(self):
            calls.append(("poll", {}))
            return BatchDispatchSummary()

        async def submit_once(self, *, summary):
            calls.append(("submit", {}))
            summary.claimed += 1
            return summary

    def build(**kwargs):
        built.update(kwargs)
        return Dispatcher()

    monkeypatch.setattr(batch_script, "datetime", Clock)
    monkeypatch.setattr(
        batch_script,
        "SupabaseBatchRepository",
        lambda **_kwargs: Repository(),
    )
    monkeypatch.setattr(batch_script, "build_batch_dispatcher", build)

    result = await batch_script._run_live(settings, poll_only=False)

    assert [name for name, _kwargs in calls] == [
        "poll",
        "budget",
        "grant",
        "submit",
    ]
    assert built["canary_job_id"] == (
        "11111111-1111-4111-8111-111111111111"
    )
    assert built["canary_input_sha256"] == "c" * 64
    assert built["canary_request_sha256"] == "d" * 64
    assert built["canary_not_after"] == now + timedelta(hours=1)
    assert calls[2][1]["request_sha256"] == "d" * 64
    assert calls[2][1]["hard_limit_usd"] == Decimal("0.05")
    assert result["submissions_enabled"] is True


@pytest.mark.asyncio
async def test_shadow_worker_derives_one_exact_daily_grant_before_claim(
    monkeypatch,
):
    now = datetime(2026, 8, 1, 1, tzinfo=timezone.utc)
    settings = BatchSettings.from_env(_active_shadow_env(now))
    calls = []
    builds = []

    class Clock:
        @classmethod
        def now(cls, _timezone):
            return now

        @classmethod
        def fromisoformat(cls, value):
            return datetime.fromisoformat(value)

    class Repository:
        async def configure_daily_budget(self, **kwargs):
            calls.append(("budget", kwargs))

        async def peek_origintrail_shadow_candidate(self, **kwargs):
            calls.append(("peek", kwargs))
            return {
                "kst_date": "2026-08-01",
                "job_id": "11111111-1111-4111-8111-111111111111",
                "input_sha256": "c" * 64,
                "request_sha256": "d" * 64,
            }

        async def configure_origintrail_shadow_day(self, **kwargs):
            calls.append(("shadow_day", kwargs))

    class Dispatcher:
        def __init__(self, exact):
            self.exact = exact

        async def poll_once(self):
            calls.append(("poll", {}))
            return BatchDispatchSummary()

        async def submit_once(self, *, summary):
            assert self.exact is True
            calls.append(("submit", {}))
            summary.claimed += 1
            summary.submitted += 1
            return summary

    def build(**kwargs):
        builds.append(kwargs)
        return Dispatcher("canary_job_id" in kwargs)

    monkeypatch.setattr(batch_script, "datetime", Clock)
    monkeypatch.setattr(
        batch_script,
        "SupabaseBatchRepository",
        lambda **_kwargs: Repository(),
    )
    monkeypatch.setattr(batch_script, "build_batch_dispatcher", build)

    result = await batch_script._run_live(settings, poll_only=False)

    assert [name for name, _kwargs in calls] == [
        "poll",
        "budget",
        "peek",
        "shadow_day",
        "submit",
    ]
    assert len(builds) == 2
    assert "canary_job_id" not in builds[0]
    assert builds[1]["canary_job_id"] == (
        "11111111-1111-4111-8111-111111111111"
    )
    assert calls[2][1]["pilot_subject_sha256"] == (
        settings.canary_subject_sha256
    )
    assert calls[3][1]["kst_date"] == date(2026, 8, 1)
    assert calls[3][1]["hard_limit_usd"] == Decimal("0.05")
    assert result["pilot_kst_date"] == "2026-08-01"
    assert result["submitted"] == 1
    assert result["submissions_enabled"] is True


@pytest.mark.asyncio
async def test_receipt_expiring_during_poll_blocks_budget_grant_and_claim(
    monkeypatch,
):
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    settings = BatchSettings.from_env(_active_live_env(now))
    cutoff = now + timedelta(hours=1)
    moments = iter((now, cutoff))
    calls = []

    class Clock:
        @classmethod
        def now(cls, _timezone):
            return next(moments)

    class Repository:
        async def configure_daily_budget(self, **_kwargs):
            raise AssertionError("expired approval must not configure budget")

        async def configure_canary_grant(self, **_kwargs):
            raise AssertionError("expired approval must not register grant")

    class Dispatcher:
        async def poll_once(self):
            calls.append("poll")
            return BatchDispatchSummary()

        async def submit_once(self, *, summary):
            raise AssertionError("expired approval must not claim")

    monkeypatch.setattr(batch_script, "datetime", Clock)
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

    result = await batch_script._run_live(settings, poll_only=False)

    assert calls == ["poll"]
    assert result["dispatch_phase"] == "config_authorization_expired"
    assert result["submissions_enabled"] is False
    assert result["detail"] == "batch_dispatch_not_authorized_poll_only"


def test_cli_dry_run_override_never_loads_live_secrets():
    settings = BatchSettings.from_env(
        {"BATCH_EXPERIMENT_MODE": "live"},
        force_dry_run=True,
    )
    assert settings.mode == "dry_run"
    assert settings.openai_api_key is None
    assert settings.public_summary()["provider_calls"] is False


def test_live_receipt_binds_release_and_nonsecret_config_only():
    first = _live_env()
    second = dict(first)
    second["SUPABASE_SERVICE_ROLE_KEY"] = "t" * 64
    second["OPENAI_API_KEY"] = "sk-rotated-key-that-is-long-enough"

    _first_subject, first_digest = BatchSettings.canary_subject_from_env(first)
    _second_subject, second_digest = BatchSettings.canary_subject_from_env(second)

    assert first_digest == second_digest
    changed_release = dict(first)
    changed_release["BATCH_CANARY_RELEASE_SHA"] = "b" * 40
    with pytest.raises(ValueError, match="RAILWAY_GIT_COMMIT_SHA"):
        BatchSettings.from_env(changed_release)
    changed_release["RAILWAY_GIT_COMMIT_SHA"] = "b" * 40
    with pytest.raises(ValueError, match="does not match settings"):
        BatchSettings.from_env(changed_release)


def test_config_approval_expiry_disables_new_producer_admission():
    settings = BatchSettings.from_env(_live_env())

    assert settings.experiment_phase(datetime(
        2026,
        8,
        1,
        2,
        tzinfo=timezone.utc,
    )) == "authorization_expired"


def test_dispatch_receipt_is_bound_to_exact_job_and_input():
    env = _live_env()
    settings = BatchSettings.from_env(env)
    job_id = "11111111-1111-4111-8111-111111111111"
    input_sha256 = "c" * 64
    request_sha256 = "d" * 64
    subject = dispatch_subject(
        config_subject_sha256=settings.canary_subject_sha256,
        config_approval_id=settings.canary_approval.approval_id,
        job_id=job_id,
        input_sha256=input_sha256,
        request_sha256=request_sha256,
    )
    env["BATCH_CANARY_DISPATCH_RECEIPT"] = json.dumps({
        "version": DISPATCH_SCHEMA,
        "dispatch_approval_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "approved_by": "operator:test",
        "approved_at": "2026-07-31T23:00:00Z",
        "expires_at": "2026-08-01T01:00:00Z",
        "job_id": job_id,
        "input_sha256": input_sha256,
        "request_sha256": request_sha256,
        "subject_sha256": canonical_sha256(subject),
    })

    authorized = BatchSettings.from_env(env)

    assert authorized.dispatch_phase(datetime(
        2026,
        8,
        1,
        tzinfo=timezone.utc,
    )) == "active"
    assert authorized.canary_dispatch_approval.job_id == job_id


def test_live_receipt_is_required_even_when_all_credentials_exist():
    with pytest.raises(ValueError, match="APPROVAL_RECEIPT"):
        BatchSettings.from_env(_live_env(BATCH_CANARY_APPROVAL_RECEIPT=""))


@pytest.mark.parametrize(
    "override, message",
    [
        ({"BATCH_EXPERIMENT_MODE": "auto"}, "off, dry_run, or live"),
        ({"BATCH_ALLOWED_CLIENTS": "origintrail,unknown"}, "unsupported"),
        ({"BATCH_DAILY_CAP_USD": "0.049"}, "exact cents"),
        ({"BATCH_DAILY_CAP_USD": "0.06"}, "exactly 0.05"),
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
        ({"BATCH_CANARY_ENABLED": "false"}, "CANARY_ENABLED"),
        ({"BATCH_CANARY_ENABLED": "TRUE"}, "CANARY_ENABLED"),
        ({"BATCH_CANARY_ENVIRONMENT": "preview"}, "staging or production"),
        ({"RAILWAY_ENVIRONMENT_NAME": "production"}, "ENVIRONMENT_NAME"),
        ({"RAILWAY_GIT_COMMIT_SHA": ""}, "RAILWAY_GIT_COMMIT_SHA"),
        ({"OPENAI_API_KEY": "short"}, "OPENAI_API_KEY"),
    ],
)
def test_live_settings_fail_closed(override, message):
    with pytest.raises(ValueError, match=message):
        BatchSettings.from_env(_live_env(**override))
