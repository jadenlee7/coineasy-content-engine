from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUZZ_RELEASE = "desktop-v0.5.4"
BUZZ_DEB_SHA256 = (
    "9c2f0df4589c08698dd940e09d911884a5468c35169d1068fc6ccc93012bfeff"
)


def test_buzz_delivery_is_a_short_lived_disabled_by_default_cron():
    config = json.loads((ROOT / "railway.buzz-delivery.json").read_text())

    assert config == {
        "$schema": "https://railway.com/railway.schema.json",
        "build": {
            "builder": "DOCKERFILE",
            "dockerfilePath": "Dockerfile.buzz-delivery",
        },
        "deploy": {
            "startCommand": (
                "python -m scripts.run_origintrail_buzz_delivery --send-once"
            ),
            "cronSchedule": "15 * * * *",
            "restartPolicyType": "NEVER",
        },
    }


def test_buzz_image_pins_the_reviewed_official_cli_and_is_minimal():
    dockerfile = (ROOT / "Dockerfile.buzz-delivery").read_text()
    lowered = dockerfile.lower()

    assert BUZZ_RELEASE in dockerfile
    assert f"--checksum=sha256:{BUZZ_DEB_SHA256}" in dockerfile
    assert "Buzz_0.5.4_amd64.deb" in dockerfile
    assert "copy --from=buzz-cli" in lowered
    assert "copy core/buzz ./core/buzz" in lowered
    assert "scripts/run_origintrail_buzz_delivery.py" in dockerfile
    assert "user 10001:10001" in lowered
    assert "copy core ./core" not in lowered
    assert "copy scripts ./scripts" not in lowered
    assert "core/batch" not in lowered
    assert "core/publishers" not in lowered
    assert "openai_api_key" not in lowered
    assert "supabase_service_role_key" not in lowered


def test_ci_builds_and_runs_the_real_buzz_image_in_hold_mode():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "buzz-image:" in workflow
    assert "--file Dockerfile.buzz-delivery" in workflow
    assert "--network none" in workflow
    assert "BUZZ_DELIVERY_ENABLED=false" in workflow
    assert "buzz_delivery_disabled" in workflow
