from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from scripts import run_official_x_daily as runner


@pytest.mark.parametrize(
    "failure_stage",
    ["automation_settings", "batch_settings", "build"],
)
def test_configuration_failure_is_safe_and_does_not_run(
    monkeypatch,
    capsys,
    failure_stage,
):
    secret = "configuration-secret-must-not-leak"

    if failure_stage == "automation_settings":
        monkeypatch.setattr(
            runner.AutomationSettings,
            "from_env",
            lambda: (_ for _ in ()).throw(ValueError(secret)),
        )
        monkeypatch.setattr(
            runner,
            "build_daily_runner",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("builder must not run")
            ),
        )
    else:
        monkeypatch.setattr(
            runner.AutomationSettings,
            "from_env",
            lambda: SimpleNamespace(),
        )
        if failure_stage == "batch_settings":
            monkeypatch.setattr(
                runner.BatchSettings,
                "from_env",
                lambda **_kwargs: (_ for _ in ()).throw(ValueError(secret)),
            )
            monkeypatch.setattr(
                runner,
                "build_daily_runner",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("builder must not run")
                ),
            )
        else:
            monkeypatch.setattr(
                runner.BatchSettings,
                "from_env",
                lambda **_kwargs: SimpleNamespace(),
            )
            monkeypatch.setattr(
                runner,
                "build_daily_runner",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError(secret)
                ),
            )

    monkeypatch.setattr(sys, "argv", ["run_official_x_daily"])

    assert runner.main() == 2
    raw = capsys.readouterr().out.strip()
    assert json.loads(raw) == {
        "ok": False,
        "error": "automation_configuration_invalid",
    }
    assert secret not in raw


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_runtime_failure_has_distinct_safe_error(
    monkeypatch,
    capsys,
    error_type,
):
    secret = "runtime-secret-must-not-leak"

    class FailingRunner:
        async def run(self, *, dry_run):
            assert dry_run is False
            raise error_type(secret)

    monkeypatch.setattr(
        runner.AutomationSettings,
        "from_env",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        runner.BatchSettings,
        "from_env",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        runner,
        "build_daily_runner",
        lambda *_args, **_kwargs: FailingRunner(),
    )
    monkeypatch.setattr(sys, "argv", ["run_official_x_daily"])

    assert runner.main() == 1
    raw = capsys.readouterr().out.strip()
    assert json.loads(raw) == {
        "ok": False,
        "error": "automation_runtime_failed",
    }
    assert secret not in raw


def test_success_prints_summary_and_preserves_dry_run(monkeypatch, capsys):
    settings = SimpleNamespace()
    expected_batch_settings = SimpleNamespace()
    summary = SimpleNamespace(
        errors=0,
        as_dict=lambda: {
            "ok": True,
            "dry_run": True,
            "errors": 0,
        },
    )

    class SuccessfulRunner:
        async def run(self, *, dry_run):
            assert dry_run is True
            return summary

    monkeypatch.setattr(
        runner.AutomationSettings,
        "from_env",
        lambda: settings,
    )

    def load_batch_settings(**kwargs):
        assert kwargs == {
            "force_dry_run": True,
            "require_openai_api_key": False,
        }
        return expected_batch_settings

    monkeypatch.setattr(runner.BatchSettings, "from_env", load_batch_settings)

    def build(configured_settings, *, batch_settings: object):
        assert configured_settings is settings
        assert batch_settings is expected_batch_settings
        return SuccessfulRunner()

    monkeypatch.setattr(runner, "build_daily_runner", build)
    monkeypatch.setattr(sys, "argv", ["run_official_x_daily", "--dry-run"])

    assert runner.main() == 0
    assert json.loads(capsys.readouterr().out) == summary.as_dict()
