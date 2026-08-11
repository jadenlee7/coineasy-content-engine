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


def test_buzz_review_is_a_short_lived_fast_disabled_by_default_cron():
    config = json.loads((ROOT / "railway.buzz-review.json").read_text())
    assert config == {
        "$schema": "https://railway.com/railway.schema.json",
        "build": {
            "builder": "DOCKERFILE",
            "dockerfilePath": "Dockerfile.buzz-review",
        },
        "deploy": {
            "preDeployCommand": (
                "python -m scripts.run_origintrail_buzz_review --validate-only"
            ),
            "startCommand": (
                "python -m scripts.run_origintrail_buzz_review --scan-once"
            ),
            "cronSchedule": "*/5 * * * *",
            "restartPolicyType": "NEVER",
        },
    }


def test_buzz_review_image_is_pinned_and_has_no_agent_or_publish_plane():
    dockerfile = (ROOT / "Dockerfile.buzz-review").read_text()
    lowered = dockerfile.lower()
    assert BUZZ_RELEASE in dockerfile
    assert f"--checksum=sha256:{BUZZ_DEB_SHA256}" in dockerfile
    assert "scripts/run_origintrail_buzz_review.py" in dockerfile
    assert "user 10001:10001" in lowered
    assert "buzz_review_enabled=false" in lowered
    assert "buzz_review_ack_enabled=false" in lowered
    assert "buzz_review_allowed_clients=origintrail" in lowered
    assert "core/batch" not in lowered
    assert "core/publishers" not in lowered
    assert "openai_api_key" not in lowered
    assert "supabase_service_role_key" not in lowered
    for fence in (
        "BUZZ_SERVICE_PUBKEY",
        "BUZZ_REVIEWER_PUBKEYS",
        "RAILWAY_ENVIRONMENT_NAME",
        "BUZZ_REVIEW_EXPECTED_ENVIRONMENT",
        "RAILWAY_GIT_COMMIT_SHA",
        "BUZZ_REVIEW_RELEASE_SHA",
        "BUZZ_REVIEW_PROTOCOL_START_EPOCH",
    ):
        assert f"{fence}=" not in dockerfile


def test_ci_builds_and_holds_the_review_image_without_network():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "buzz-review-image:" in workflow
    assert "--file Dockerfile.buzz-review" in workflow
    assert "BUZZ_REVIEW_ENABLED=false" in workflow
    assert "buzz_review_disabled" in workflow
    assert "scripts.run_origintrail_buzz_review --validate-only" in workflow
    for fence in (
        "BUZZ_SERVICE_PUBKEY",
        "RAILWAY_ENVIRONMENT_NAME",
        "BUZZ_REVIEW_EXPECTED_ENVIRONMENT",
        "RAILWAY_GIT_COMMIT_SHA",
        "BUZZ_REVIEW_RELEASE_SHA",
        "BUZZ_REVIEW_PROTOCOL_START_EPOCH",
    ):
        assert f"--env {fence}=" in workflow


def test_buzz_review_adr_number_and_decision_only_boundary_are_current():
    adr = (ROOT / "docs" / "ADR-013-origintrail-buzz-review-action-loop.md")
    assert adr.is_file()
    assert not (
        ROOT / "docs" / "ADR-012-origintrail-buzz-review-action-loop.md"
    ).exists()
    content = adr.read_text()
    assert content.startswith(
        "# ADR-013: OriginTrail Buzz publish-intent review decision loop"
    )
    assert "게시 승인: 원문·최종물 확인" in content
    assert "BUZZ_REVIEW_PROTOCOL_START_EPOCH" in content
    assert "signature-stripped" in content
    assert "cannot authorize publication" in content


def test_review_pack_delivery_bundles_the_deterministic_hangul_font():
    config = (ROOT / "netlify.toml").read_text()
    assert (
        '[functions."buzz-delivery-origintrail"]\n'
        '  included_files = ["netlify/functions/_assets/fonts/**"]'
    ) in config
