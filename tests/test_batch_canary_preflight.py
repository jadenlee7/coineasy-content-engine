from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from core.batch.canary import (
    CONFIG_SCHEMA,
    DISPATCH_SCHEMA,
    canonical_sha256,
    dispatch_subject,
)
from core.batch.settings import BatchSettings
import scripts.run_batch_dispatcher as batch_script


def _live_env(now: datetime) -> dict[str, str]:
    kst = ZoneInfo("Asia/Seoul")
    start_kst = now.astimezone(kst).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    end_kst = start_kst + timedelta(hours=48)
    values = {
        "BATCH_EXPERIMENT_MODE": "live",
        "BATCH_ALLOWED_CLIENTS": "origintrail",
        "BATCH_DAILY_CAP_USD": "0.05",
        "BATCH_MAX_CLAIMS": "1",
        "BATCH_MAX_REQUESTS_PER_BATCH": "1",
        "BATCH_TIMEZONE": "Asia/Seoul",
        "BATCH_EXPERIMENT_START_AT": start_kst.isoformat(),
        "BATCH_EXPERIMENT_END_AT": end_kst.isoformat(),
        "BATCH_CANARY_ENABLED": "true",
        "BATCH_CANARY_ENVIRONMENT": "staging",
        "RAILWAY_ENVIRONMENT_NAME": "staging",
        "BATCH_CANARY_RELEASE_SHA": "a" * 40,
        "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
        "SUPABASE_URL": "https://project-ref.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "service-secret-" + "s" * 48,
        "CONTENT_STUDIO_WORKSPACE_ID": (
            "00000000-0000-4000-8000-000000000001"
        ),
        "OPENAI_API_KEY": "sk-provider-secret-that-is-long-enough",
    }
    _subject, config_digest = BatchSettings.canary_subject_from_env(values)
    approval_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    approved_at = now - timedelta(minutes=5)
    expires_at = now + timedelta(hours=1)
    values["BATCH_CANARY_APPROVAL_RECEIPT"] = json.dumps({
        "version": CONFIG_SCHEMA,
        "approval_id": approval_id,
        "approved_by": "operator:test",
        "approved_at": approved_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "subject_sha256": config_digest,
    })
    job_id = "11111111-1111-4111-8111-111111111111"
    input_sha256 = "c" * 64
    request_sha256 = "d" * 64
    dispatch = dispatch_subject(
        config_subject_sha256=config_digest,
        config_approval_id=approval_id,
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
        "subject_sha256": canonical_sha256(dispatch),
    })
    return values


def _install_env(monkeypatch, values):
    for name in tuple(os.environ):
        if not name.startswith("BATCH_"):
            continue
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize(
    ("arguments", "expected_poll_only"),
    [
        ([], True),
        (["--poll-only"], True),
        (["--submit-once"], False),
    ],
)
def test_live_cli_requires_explicit_submit_once(
    monkeypatch,
    capsys,
    arguments,
    expected_poll_only,
):
    captured = {}

    class SettingsFactory:
        @staticmethod
        def from_env(**_kwargs):
            return SimpleNamespace(mode="live")

    async def run_live(_settings, *, poll_only):
        captured["poll_only"] = poll_only
        return {"ok": True, "mode": "live"}

    monkeypatch.setattr(batch_script, "BatchSettings", SettingsFactory)
    monkeypatch.setattr(batch_script, "_run_live", run_live)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_batch_dispatcher", *arguments],
    )

    assert batch_script.main() == 0
    assert captured["poll_only"] is expected_poll_only
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_live_preflight_constructs_no_database_or_provider_client(
    monkeypatch,
    capsys,
):
    values = _live_env(datetime.now(timezone.utc))
    _install_env(monkeypatch, values)
    monkeypatch.setattr(sys, "argv", ["run_batch_dispatcher", "--preflight-live"])
    monkeypatch.setattr(
        batch_script,
        "SupabaseBatchRepository",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("database constructor must not run")
        ),
    )
    monkeypatch.setattr(
        batch_script,
        "build_batch_dispatcher",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider constructor must not run")
        ),
    )

    result_code = batch_script.main()
    output = capsys.readouterr().out
    result = json.loads(output)

    assert result_code == 0
    assert result["ok"] is True
    assert result["ready_to_submit"] is True
    assert result["database_calls"] is False
    assert result["provider_calls"] is False
    assert result["submissions_enabled"] is False
    assert result["canary_dispatch"]["job_id"] == (
        "11111111-1111-4111-8111-111111111111"
    )
    assert result["canary_dispatch"]["input_sha256"] == "c" * 64
    assert result["canary_dispatch"]["request_sha256"] == "d" * 64
    assert result["runtime_environment_verified"] is True
    assert result["runtime_release_verified"] is True
    assert values["OPENAI_API_KEY"] not in output
    assert values["SUPABASE_SERVICE_ROLE_KEY"] not in output


def test_seven_day_shadow_preflight_needs_no_exact_dispatch_receipt(
    monkeypatch,
    capsys,
):
    now = datetime.now(timezone.utc)
    values = _live_env(now)
    start = now.astimezone(ZoneInfo("Asia/Seoul")).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    values.update({
        "BATCH_EXPERIMENT_START_AT": start.isoformat(),
        "BATCH_EXPERIMENT_END_AT": (start + timedelta(days=7)).isoformat(),
        "BATCH_PRODUCTION_SHADOW_AUTO_DISPATCH": "true",
        "BATCH_CANARY_APPROVAL_RECEIPT": "",
        "BATCH_CANARY_DISPATCH_RECEIPT": "",
    })
    _subject, digest = BatchSettings.canary_subject_from_env(values)
    values["BATCH_CANARY_APPROVAL_RECEIPT"] = json.dumps({
        "version": CONFIG_SCHEMA,
        "approval_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "approved_by": "coineasy-owner:test",
        "approved_at": (now - timedelta(minutes=5)).isoformat(),
        "expires_at": (start + timedelta(days=7)).isoformat(),
        "subject_sha256": digest,
    })
    _install_env(monkeypatch, values)
    monkeypatch.setattr(sys, "argv", ["run_batch_dispatcher", "--preflight-live"])

    assert batch_script.main() == 0
    result = json.loads(capsys.readouterr().out)

    assert result["ready_to_submit"] is True
    assert result["dispatch_phase"] == "active"
    assert result["canary_dispatch_configured"] is False
    assert result["production_shadow_auto_dispatch"] is True
    assert result["authorized_provider_batches"] == 7
    assert result["auto_publish"] is False
    assert result["database_calls"] is False
    assert result["provider_calls"] is False


def test_approval_subject_is_a_hold_receipt_and_never_loads_openai_key(
    monkeypatch,
    capsys,
):
    values = _live_env(datetime.now(timezone.utc))
    values.pop("BATCH_CANARY_APPROVAL_RECEIPT")
    values.pop("BATCH_CANARY_DISPATCH_RECEIPT")
    values.pop("OPENAI_API_KEY")
    _install_env(monkeypatch, values)
    monkeypatch.setattr(sys, "argv", ["run_batch_dispatcher", "--approval-subject"])

    result_code = batch_script.main()
    result = json.loads(capsys.readouterr().out)

    assert result_code == 0
    assert result["mode"] == "hold"
    assert result["database_calls"] is False
    assert result["provider_calls"] is False
    assert result["submissions_enabled"] is False
    assert result["detail"] == "approval_subject_only_not_authorization"


def test_dispatch_subject_is_exact_job_hold_with_zero_external_calls(
    monkeypatch,
    capsys,
):
    values = _live_env(datetime.now(timezone.utc))
    values.pop("BATCH_CANARY_DISPATCH_RECEIPT")
    values.pop("OPENAI_API_KEY")
    _install_env(monkeypatch, values)
    job_id = "11111111-1111-4111-8111-111111111111"
    input_sha256 = "c" * 64
    request_sha256 = "d" * 64
    monkeypatch.setattr(sys, "argv", [
        "run_batch_dispatcher",
        "--dispatch-subject",
        job_id,
        input_sha256,
        request_sha256,
    ])

    result_code = batch_script.main()
    result = json.loads(capsys.readouterr().out)

    assert result_code == 0
    assert result["mode"] == "hold"
    assert result["subject"]["job_id"] == job_id
    assert result["subject"]["input_sha256"] == input_sha256
    assert result["subject"]["request_sha256"] == request_sha256
    assert result["subject"]["authorized_provider_batches"] == 1
    assert result["subject"]["authorized_total_usd"] == "0.05"
    assert result["database_calls"] is False
    assert result["provider_calls"] is False
    assert result["submissions_enabled"] is False
