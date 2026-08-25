from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from core.automation.models import FailedDraftRecoveryInspection
from scripts import run_squid_failed_draft_recovery as cli


JOB_ID = "33333333-3333-4333-8333-333333333333"
REQUEST_ID = "44444444-4444-4444-8444-444444444444"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"
RECOVERY_ID = "66666666-6666-4666-8666-666666666666"
APPROVAL_ID = "77777777-7777-4777-8777-777777777777"
SUBJECT_SHA = "5" * 64
RELEASE_SHA = "a" * 40


def test_runbook_uses_packaged_module_entrypoint():
    runbook = (
        Path(__file__).parents[1] / "docs" / "SQUID_FAILED_DRAFT_RECOVERY.md"
    ).read_text(encoding="utf-8")

    assert "scripts/run_squid_failed_draft_recovery.py" not in runbook
    assert runbook.count(
        "python -m scripts.run_squid_failed_draft_recovery"
    ) == 3


def _settings():
    return SimpleNamespace(
        workspace_id="11111111-1111-4111-8111-111111111111",
        supabase_url="https://project-ref.supabase.co",
        supabase_service_role_key="s" * 64,
    )


def _approval_args(command: str) -> list[str]:
    return [
        command,
        "--job-id",
        JOB_ID,
        "--recovery-id",
        RECOVERY_ID,
        "--release-sha",
        RELEASE_SHA,
        "--approval-id",
        APPROVAL_ID,
        "--approved-by",
        "codex:user",
        "--approved-at",
        "2026-08-25T08:00:00Z",
        "--expires-at",
        "2026-08-25T09:00:00Z",
    ]


def _exact_production_release(monkeypatch) -> None:
    monkeypatch.setenv("OFFICIAL_X_RECOVERY_RELEASE_SHA", RELEASE_SHA)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", RELEASE_SHA)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")


def test_inspect_is_read_only_and_does_not_construct_the_generation_runner(
    monkeypatch,
    capsys,
):
    calls = []

    class Repository:
        async def inspect_failed_draft_recovery(self, **kwargs):
            calls.append(kwargs)
            return FailedDraftRecoveryInspection(
                recovery_id=RECOVERY_ID,
                job_id=JOB_ID,
                request_id=REQUEST_ID,
                source_item_id=SOURCE_ID,
                approval_subject={
                    "contract": "squid-failed-draft-recovery@1",
                    "automatic_approval": False,
                    "automatic_publication": False,
                },
                approval_subject_sha256=SUBJECT_SHA,
                authorized=False,
                claims_allowed=1,
                claims_consumed=0,
                expires_at="2026-08-25T09:00:00+00:00",
                release_sha=RELEASE_SHA,
            )

    monkeypatch.setattr(cli.AutomationSettings, "from_env", _settings)
    monkeypatch.setattr(cli, "_repository", lambda _settings: Repository())
    monkeypatch.setattr(
        cli,
        "build_daily_runner",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("generation runner must not be constructed")
        ),
    )
    monkeypatch.setattr(sys, "argv", [
        "run_squid_failed_draft_recovery",
        *_approval_args("inspect"),
    ])

    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "inspect"
    assert result["approval_subject_sha256"] == SUBJECT_SHA
    assert result["automatic_publication"] is False
    assert len(calls) == 1


def test_authorize_records_only_the_exact_grant(monkeypatch, capsys):
    calls = []

    class Repository:
        async def authorize_failed_draft_recovery(self, **kwargs):
            calls.append(kwargs)
            return False

    monkeypatch.setattr(cli.AutomationSettings, "from_env", _settings)
    monkeypatch.setattr(cli, "_repository", lambda _settings: Repository())
    _exact_production_release(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "run_squid_failed_draft_recovery",
        *_approval_args("authorize"),
        "--subject-sha256",
        SUBJECT_SHA,
    ])

    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "ok": True,
        "mode": "authorize",
        "authorized": True,
        "reused": False,
        "recovery_id": RECOVERY_ID,
        "job_id": JOB_ID,
        "approval_subject_sha256": SUBJECT_SHA,
        "release_sha": RELEASE_SHA,
        "automatic_approval": False,
        "automatic_publication": False,
    }
    assert len(calls) == 1


def test_run_once_uses_only_the_targeted_runner_method(monkeypatch, capsys):
    calls = []
    summary = SimpleNamespace(
        errors=0,
        as_dict=lambda: {
            "ok": True,
            "kst_date": "2026-08-25",
            "dry_run": False,
            "queued": 0,
            "generated": 1,
            "reused": 0,
            "skipped": 0,
            "errors": 0,
            "outcomes": [{"client_id": "squid", "status": "needs_review"}],
        },
    )

    class Runner:
        async def recover_failed_draft_once(self, **kwargs):
            calls.append(kwargs)
            return summary

    monkeypatch.setattr(cli.AutomationSettings, "from_env", _settings)
    monkeypatch.setattr(cli, "build_daily_runner", lambda _settings: Runner())
    _exact_production_release(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "run_squid_failed_draft_recovery",
        "run-once",
        "--job-id",
        JOB_ID,
        "--recovery-id",
        RECOVERY_ID,
        "--release-sha",
        RELEASE_SHA,
        "--subject-sha256",
        SUBJECT_SHA,
    ])

    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "run-once"
    assert result["generated"] == 1
    assert result["automatic_approval"] is False
    assert result["automatic_publication"] is False
    assert calls == [{
        "job_id": JOB_ID,
        "recovery_id": RECOVERY_ID,
        "approval_subject_sha256": SUBJECT_SHA,
        "release_sha": RELEASE_SHA,
    }]


def test_authorize_fails_closed_when_exact_release_is_not_deployed(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(cli.AutomationSettings, "from_env", _settings)
    monkeypatch.delenv("OFFICIAL_X_RECOVERY_RELEASE_SHA", raising=False)
    monkeypatch.setattr(sys, "argv", [
        "run_squid_failed_draft_recovery",
        *_approval_args("authorize"),
        "--subject-sha256",
        SUBJECT_SHA,
    ])

    assert cli.main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": "recovery_configuration_invalid",
    }


def test_run_once_fails_closed_on_self_declared_but_wrong_runtime_release(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(cli.AutomationSettings, "from_env", _settings)
    _exact_production_release(monkeypatch)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "b" * 40)
    monkeypatch.setattr(sys, "argv", [
        "run_squid_failed_draft_recovery",
        "run-once",
        "--job-id",
        JOB_ID,
        "--recovery-id",
        RECOVERY_ID,
        "--release-sha",
        RELEASE_SHA,
        "--subject-sha256",
        SUBJECT_SHA,
    ])

    assert cli.main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": "recovery_configuration_invalid",
    }


def test_authorize_fails_closed_outside_production(monkeypatch, capsys):
    monkeypatch.setattr(cli.AutomationSettings, "from_env", _settings)
    _exact_production_release(monkeypatch)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    monkeypatch.setattr(sys, "argv", [
        "run_squid_failed_draft_recovery",
        *_approval_args("authorize"),
        "--subject-sha256",
        SUBJECT_SHA,
    ])

    assert cli.main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": "recovery_configuration_invalid",
    }
