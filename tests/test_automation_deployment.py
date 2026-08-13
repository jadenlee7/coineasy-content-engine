from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_railway_cron_is_short_lived_and_uses_the_small_worker_image():
    config = json.loads((ROOT / "railway.official-x-cron.json").read_text())

    assert config["build"]["dockerfilePath"] == "Dockerfile.automation"
    assert config["deploy"] == {
        "startCommand": "python -m scripts.run_official_x_daily",
        "cronSchedule": "*/15 * * * *",
        "restartPolicyType": "NEVER",
    }

    dockerfile = (ROOT / "Dockerfile.automation").read_text()
    assert "requirements-automation.txt" in dockerfile
    assert "playwright" not in dockerfile.lower()
    assert "uvicorn" not in dockerfile.lower()


def test_review_draft_worker_has_no_publishing_adapter_or_secret():
    source = "\n".join(
        path.read_text()
        for path in sorted((ROOT / "core" / "automation").glob("*.py"))
    ).lower()

    assert "core.publishers" not in source
    assert "/publish/" not in source
    assert "telegram_bot_token" not in source
    assert "typefully_api_key" not in source
