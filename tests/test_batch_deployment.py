from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


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
        "startCommand": "python -m scripts.run_batch_dispatcher",
        "cronSchedule": "*/10 * * * *",
        "restartPolicyType": "NEVER",
    }
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
