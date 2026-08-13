from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANARY_VERSION = "22222222-2222-4222-8222-222222222222"


def test_railway_grok_qa_is_a_short_lived_single_claim_cron():
    config = json.loads((ROOT / "railway.grok-qa.json").read_text())

    assert config["build"] == {
        "builder": "DOCKERFILE",
        "dockerfilePath": "Dockerfile.grok-qa",
    }
    assert config["deploy"] == {
        "startCommand": (
            "python -m scripts.run_grok_qa_dispatch --run-once"
        ),
        "cronSchedule": "*/5 * * * *",
        "restartPolicyType": "NEVER",
    }


def test_grok_qa_image_contains_only_the_advisory_worker_runtime():
    dockerfile = (ROOT / "Dockerfile.grok-qa").read_text()
    assert "FROM python:3.12-slim" in dockerfile
    assert "COPY requirements-grok-qa.txt ." in dockerfile
    assert "COPY core/grok_qa ./core/grok_qa" in dockerfile
    assert (
        "COPY scripts/run_grok_qa_dispatch.py "
        "./scripts/run_grok_qa_dispatch.py"
    ) in dockerfile
    assert "COPY . ." not in dockerfile
    assert "requirements.txt" not in dockerfile
    assert "api/" not in dockerfile
    assert "netlify/" not in dockerfile

    requirements = {
        line.strip()
        for line in (ROOT / "requirements-grok-qa.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert requirements == {"httpx==0.27.2", "pydantic==2.9.2"}

    isolated_source = "\n".join(
        path.read_text()
        for path in sorted((ROOT / "core" / "grok_qa").glob("*.py"))
    )
    assert "from core.publishers" not in isolated_source
    assert "import core.publishers" not in isolated_source
    assert "from core.sources" not in isolated_source
    assert "import core.sources" not in isolated_source
    assert "from core.automation" not in isolated_source
    assert "import core.automation" not in isolated_source


def test_validate_only_reports_canary_state_without_disclosing_target():
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT),
        "GROK_QA_DISPATCH_ENABLED": "false",
        "GROK_QA_CANARY_MODE": "true",
        "GROK_QA_CANARY_CONTENT_VERSION_ID": CANARY_VERSION,
        "GROK_QA_DISPATCH_TOKEN": (
            "dispatch-token-that-is-dedicated-and-long-enough"
        ),
        "XAI_API_KEY": "xai-ci-key-that-is-dedicated-and-long-enough",
        "GROK_QA_MODEL": "grok-4.5",
        "GROK_QA_ALLOWED_CLIENTS": "squid",
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "GROK_QA_EXPECTED_ENVIRONMENT": "production",
        "RAILWAY_GIT_COMMIT_SHA": "d" * 40,
        "GROK_QA_RELEASE_SHA": "d" * 40,
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.run_grok_qa_dispatch",
            "--validate-only",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["ok"] is True
    assert payload["canary_mode"] is True
    assert payload["canary_target_configured"] is True
    assert CANARY_VERSION not in completed.stdout
    assert payload["provider_calls"] is False
    assert payload["publication_calls"] is False


def test_validate_only_rejects_inherited_publication_credentials():
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT),
        "GROK_QA_DISPATCH_ENABLED": "false",
        "GROK_QA_CANARY_MODE": "true",
        "GROK_QA_CANARY_CONTENT_VERSION_ID": CANARY_VERSION,
        "GROK_QA_DISPATCH_TOKEN": (
            "dispatch-token-that-is-dedicated-and-long-enough"
        ),
        "XAI_API_KEY": "xai-ci-key-that-is-dedicated-and-long-enough",
        "GROK_QA_ALLOWED_CLIENTS": "squid",
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "GROK_QA_EXPECTED_ENVIRONMENT": "production",
        "RAILWAY_GIT_COMMIT_SHA": "d" * 40,
        "GROK_QA_RELEASE_SHA": "d" * 40,
        "PUBLICATION_WORKER_TOKEN": "must-never-reach-this-worker",
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.run_grok_qa_dispatch",
            "--validate-only",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert payload == {
        "ok": False,
        "error": "grok_qa_dispatch_configuration_invalid",
        "provider_calls": False,
        "publication_calls": False,
    }
    assert environment["PUBLICATION_WORKER_TOKEN"] not in completed.stdout
