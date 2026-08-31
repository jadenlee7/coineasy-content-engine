from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_publication_worker_has_predeploy_release_validation_and_exact_watch_set():
    config = json.loads(
        (ROOT / "railway.telegram-publication-worker.json").read_text()
    )

    assert config == {
        "$schema": "https://railway.com/railway.schema.json",
        "build": {
            "builder": "DOCKERFILE",
            "dockerfilePath": "Dockerfile.publication",
            "watchPatterns": [
                "/.dockerignore",
                "/Dockerfile.publication",
                "/requirements-automation.txt",
                "/core/**",
                "/clients/**",
                "/scripts/**",
                "/railway.telegram-publication-worker.json",
            ],
        },
        "deploy": {
            "preDeployCommand": (
                "python -m scripts.run_telegram_publications --validate-only"
            ),
            "startCommand": "python -m scripts.run_telegram_publications",
            "cronSchedule": "*/5 * * * *",
            "restartPolicyType": "NEVER",
        },
    }


def test_publication_image_does_not_bake_runtime_release_authority():
    dockerfile = (ROOT / "Dockerfile.publication").read_text()
    lowered = dockerfile.lower()

    assert "telegram_publication_enabled=false" in lowered
    assert "telegram_publication_release_sha=" not in lowered
    assert "railway_git_commit_sha=" not in lowered


def test_ci_validates_publication_image_release_fence_without_network():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "publication-image:" in workflow
    assert "--file Dockerfile.publication" in workflow
    assert "--network none" in workflow
    assert (
        "RAILWAY_GIT_COMMIT_SHA=dddddddddddddddddddddddddddddddddddddddd"
        in workflow
    )
    assert (
        "TELEGRAM_PUBLICATION_RELEASE_SHA="
        "dddddddddddddddddddddddddddddddddddddddd"
        in workflow
    )
    assert "scripts.run_telegram_publications --validate-only" in workflow
    assert "runtime_release_verified" in workflow


def test_release_fence_is_documented_but_not_a_secret():
    env_example = (ROOT / ".env.example").read_text()
    runbook = (ROOT / "docs" / "TELEGRAM_PUBLICATION_RUNBOOK.md").read_text()
    adr = (
        ROOT / "docs" / "ADR-009-exact-version-telegram-publication.md"
    ).read_text()

    assert "TELEGRAM_PUBLICATION_RELEASE_SHA=" in env_example
    assert "TELEGRAM_PUBLICATION_RELEASE_SHA" in runbook
    assert "RAILWAY_GIT_COMMIT_SHA" in runbook
    assert "TELEGRAM_PUBLICATION_RELEASE_SHA" in adr
