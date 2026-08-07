from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo

from core.batch.canary import (
    CONFIG_SCHEMA,
    DISPATCH_SCHEMA,
    canonical_sha256,
    dispatch_subject,
)
from core.batch.settings import BatchSettings


ROOT = Path(__file__).resolve().parents[1]


def _stage_batch_image_context(target: Path) -> None:
    dockerfile = (ROOT / "Dockerfile.batch").read_text()
    for raw_line in dockerfile.splitlines():
        line = raw_line.strip()
        if not line.startswith("COPY "):
            continue
        parts = shlex.split(line)
        assert len(parts) == 3, f"unsupported Batch Docker COPY: {line}"
        source = ROOT / parts[1]
        destination = target / parts[2].removeprefix("./")
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def test_batch_dispatcher_is_short_lived_and_uses_small_worker_image():
    config = json.loads((ROOT / "railway.batch-dispatcher.json").read_text())

    assert config["build"]["dockerfilePath"] == "Dockerfile.batch"
    assert config["deploy"] == {
        "startCommand": "python -m scripts.run_batch_dispatcher --submit-once",
        "cronSchedule": "0 * * * *",
        "restartPolicyType": "NEVER",
    }
    assert config["deploy"]["startCommand"].endswith("--submit-once")
    dockerfile = (ROOT / "Dockerfile.batch").read_text().lower()
    assert "requirements-automation.txt" in dockerfile
    assert "copy core ./core" not in dockerfile
    assert "copy scripts ./scripts" not in dockerfile
    assert "core/batch" in dockerfile
    assert "core/automation/content_signals.py" in dockerfile
    assert "scripts/run_batch_dispatcher.py" in dockerfile
    assert "playwright" not in dockerfile
    assert "uvicorn" not in dockerfile
    assert "clients" not in dockerfile
    assert "publishers" not in dockerfile
    assert "run_official_x_daily" not in dockerfile


def test_ci_builds_and_dry_runs_the_real_batch_image():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "docker build" in workflow
    assert "--file Dockerfile.batch" in workflow
    assert "docker run --rm" in workflow
    assert "scripts.run_batch_dispatcher --dry-run" in workflow


def test_batch_plane_has_no_publish_deploy_or_outreach_adapter():
    source = "\n".join(
        path.read_text()
        for path in sorted((ROOT / "core" / "batch").glob("*.py"))
    ).lower()

    assert "core.publishers" not in source
    assert "/publish/" not in source
    assert "telegram_bot_token" not in source
    assert "typefully_api_key" not in source
    assert "git push" not in source
    assert "send_email" not in source


def test_staged_batch_image_context_imports_and_runs_dry_run(tmp_path):
    stage = tmp_path / "batch-image"
    stage.mkdir()
    _stage_batch_image_context(stage)

    staged_files = {
        path.relative_to(stage).as_posix()
        for path in stage.rglob("*")
        if path.is_file()
    }
    assert "core/automation/content_signals.py" in staged_files
    assert "core/publishers/telegram.py" not in staged_files
    assert "core/automation/daily_runner.py" not in staged_files

    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(stage),
        "PYTHONUNBUFFERED": "1",
        "BATCH_EXPERIMENT_MODE": "dry_run",
        "BATCH_ALLOWED_CLIENTS": "origintrail",
        "BATCH_DAILY_CAP_USD": "6.00",
        "BATCH_MAX_CLAIMS": "100",
        "BATCH_MAX_REQUESTS_PER_BATCH": "1",
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.run_batch_dispatcher",
            "--dry-run",
        ],
        cwd=stage,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["mode"] == "dry_run"
    assert result["provider_calls"] is False
    assert result["auto_publish"] is False


def test_staged_batch_image_preflights_live_config_without_external_calls(
    tmp_path,
):
    stage = tmp_path / "batch-image"
    stage.mkdir()
    _stage_batch_image_context(stage)
    now = datetime.now(timezone.utc)
    start = now.astimezone(ZoneInfo("Asia/Seoul")).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    end = start + timedelta(hours=48)
    approved_at = now - timedelta(minutes=5)
    expires_at = now + timedelta(hours=1)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(stage),
        "PYTHONUNBUFFERED": "1",
        "BATCH_EXPERIMENT_MODE": "live",
        "BATCH_ALLOWED_CLIENTS": "origintrail",
        "BATCH_DAILY_CAP_USD": "0.05",
        "BATCH_MAX_CLAIMS": "1",
        "BATCH_MAX_REQUESTS_PER_BATCH": "1",
        "BATCH_TIMEZONE": "Asia/Seoul",
        "BATCH_EXPERIMENT_START_AT": start.isoformat(),
        "BATCH_EXPERIMENT_END_AT": end.isoformat(),
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
    _subject, subject_sha256 = BatchSettings.canary_subject_from_env(env)
    env["BATCH_CANARY_APPROVAL_RECEIPT"] = json.dumps({
        "version": CONFIG_SCHEMA,
        "approval_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "approved_by": "operator:test",
        "approved_at": approved_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "subject_sha256": subject_sha256,
    })
    job_id = "11111111-1111-4111-8111-111111111111"
    input_sha256 = "c" * 64
    request_sha256 = "d" * 64
    dispatch = dispatch_subject(
        config_subject_sha256=subject_sha256,
        config_approval_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        job_id=job_id,
        input_sha256=input_sha256,
        request_sha256=request_sha256,
    )
    env["BATCH_CANARY_DISPATCH_RECEIPT"] = json.dumps({
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

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.run_batch_dispatcher",
            "--preflight-live",
        ],
        cwd=stage,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert env["OPENAI_API_KEY"] not in completed.stdout
    assert env["SUPABASE_SERVICE_ROLE_KEY"] not in completed.stdout
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["config_approval_phase"] == "active"
    assert result["dispatch_phase"] == "active"
    assert result["canary_dispatch"]["request_sha256"] == request_sha256
    assert result["runtime_environment_verified"] is True
    assert result["runtime_release_verified"] is True
    assert result["database_calls"] is False
    assert result["provider_calls"] is False
    assert result["submissions_enabled"] is False
