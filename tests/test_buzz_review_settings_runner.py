from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from core.buzz.cli import BuzzCliPublisher
from core.buzz.review import build_origintrail_buzz_review_worker
from core.buzz.settings import BuzzReviewSettings
from scripts import run_origintrail_buzz_review as runner


def _env(
    *,
    enabled: str = "false",
    acknowledgement_enabled: str = "false",
    durable_acknowledgement_enabled: str = "false",
    deployment_environment: str = "staging",
) -> dict[str, str]:
    return {
        "BUZZ_REVIEW_ENABLED": enabled,
        "BUZZ_REVIEW_ACK_ENABLED": acknowledgement_enabled,
        "BUZZ_REVIEW_DURABLE_ACK_ENABLED": durable_acknowledgement_enabled,
        "BUZZ_REVIEW_ACK_LEASE_SECONDS": "180",
        "BUZZ_REVIEW_ALLOWED_CLIENTS": "origintrail",
        "BUZZ_REVIEW_URL": "https://console.example/api/buzz-review/origintrail",
        "BUZZ_REVIEW_WORKER_TOKEN": "review-token-that-is-dedicated-and-long-enough",
        "BUZZ_RELAY_URL": "https://buzz.example",
        "BUZZ_PRIVATE_KEY": "1" * 64,
        "BUZZ_CHANNEL_ID": "33333333-3333-4333-8333-333333333333",
        "BUZZ_CLI_PATH": "/opt/coineasy/bin/buzz",
        "BUZZ_REVIEWER_PUBKEYS": ("a" * 64) + "," + ("b" * 64),
        "BUZZ_SERVICE_PUBKEY": "c" * 64,
        "RAILWAY_ENVIRONMENT_NAME": deployment_environment,
        "BUZZ_REVIEW_EXPECTED_ENVIRONMENT": deployment_environment,
        "RAILWAY_GIT_COMMIT_SHA": "d" * 40,
        "BUZZ_REVIEW_RELEASE_SHA": "d" * 40,
        "BUZZ_REVIEW_PROTOCOL_START_EPOCH": "1786100000",
    }


class BuzzReviewSettingsRunnerTests(unittest.TestCase):
    def test_validation_mode_builds_no_io_worker(self):
        with patch.dict(os.environ, _env(), clear=True), patch.object(
            runner, "_build_worker", side_effect=AssertionError("must not build")
        ), patch("builtins.print") as output:
            self.assertEqual(runner.main(["--validate-only"]), 0)
        self.assertEqual(json.loads(output.call_args.args[0]), {
            "ok": True,
            "mode": "validate_only",
            "enabled": False,
            "client_id": "origintrail",
            "channel_id": "33333333-3333-4333-8333-333333333333",
            "reviewer_count": 2,
            "acknowledgement_enabled": False,
            "durable_acknowledgement_enabled": False,
            "provider_calls": False,
            "publication_calls": False,
            "database_calls": False,
            "relay_calls": False,
        })

    def test_default_and_disabled_scan_are_no_io_holds(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            runner, "_build_worker", side_effect=AssertionError("must not build")
        ), patch("builtins.print") as output:
            self.assertEqual(runner.main([]), 0)
        self.assertEqual(json.loads(output.call_args.args[0])["mode"], "hold")

        with patch.dict(os.environ, _env(), clear=True), patch.object(
            runner, "_build_worker", side_effect=AssertionError("must not build")
        ), patch("builtins.print") as output:
            self.assertEqual(runner.main(["--scan-once"]), 0)
        self.assertEqual(
            json.loads(output.call_args.args[0])["reason"], "buzz_review_disabled"
        )

    def test_settings_require_exact_origintrail_scope_and_reviewers(self):
        values = _env()
        values["BUZZ_REVIEW_ALLOWED_CLIENTS"] = "origintrail,squid"
        with self.assertRaisesRegex(ValueError, "must be origintrail"):
            BuzzReviewSettings.from_env_for_validation(values)
        for reviewers in ("", "A" * 64, ("a" * 64) + "," + ("a" * 64)):
            values = _env()
            values["BUZZ_REVIEWER_PUBKEYS"] = reviewers
            with self.subTest(reviewers=reviewers[:4]), self.assertRaisesRegex(
                ValueError, "BUZZ_REVIEWER_PUBKEYS"
            ):
                BuzzReviewSettings.from_env_for_validation(values)

    def test_review_token_cannot_reuse_delivery_or_database_secrets(self):
        for name in (
            "BUZZ_DELIVERY_WORKER_TOKEN",
            "BUZZ_SHADOW_ACCESS_TOKEN",
            "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_BUZZ_REVIEW_KEY",
            "OPENAI_API_KEY",
        ):
            values = _env()
            values[name] = values["BUZZ_REVIEW_WORKER_TOKEN"]
            with self.subTest(name=name), self.assertRaisesRegex(
                ValueError, "dedicated secret"
            ):
                BuzzReviewSettings.from_env_for_validation(values)

    def test_review_token_cannot_reuse_buzz_signing_credentials(self):
        values = _env()
        values["BUZZ_REVIEW_WORKER_TOKEN"] = values["BUZZ_PRIVATE_KEY"]
        with self.assertRaisesRegex(ValueError, "dedicated secret"):
            BuzzReviewSettings.from_env_for_validation(values)

    def test_service_identity_and_environment_release_fences_are_required(self):
        values = _env()
        values["BUZZ_SERVICE_PUBKEY"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "cannot approve"):
            BuzzReviewSettings.from_env_for_validation(values)
        for name, value in (
            ("BUZZ_REVIEW_EXPECTED_ENVIRONMENT", "production"),
            ("BUZZ_REVIEW_RELEASE_SHA", "e" * 40),
        ):
            values = _env()
            values[name] = value
            with self.subTest(name=name), self.assertRaisesRegex(
                ValueError, "fence does not match"
            ):
                BuzzReviewSettings.from_env_for_validation(values)

    def test_acknowledgement_is_literal_default_off_and_staging_only(self):
        self.assertFalse(
            BuzzReviewSettings.from_env_for_validation(_env())
            .acknowledgement_enabled
        )
        self.assertTrue(
            BuzzReviewSettings.from_env_for_validation(
                _env(
                    acknowledgement_enabled="true",
                    durable_acknowledgement_enabled="true",
                )
            ).acknowledgement_enabled
        )
        with self.assertRaisesRegex(ValueError, "restricted to staging"):
            BuzzReviewSettings.from_env_for_validation(
                _env(
                    acknowledgement_enabled="true",
                    durable_acknowledgement_enabled="true",
                    deployment_environment="production",
                )
            )
        values = _env()
        values["BUZZ_REVIEW_ACK_ENABLED"] = "1"
        with self.assertRaisesRegex(ValueError, "literal true or false"):
            BuzzReviewSettings.from_env_for_validation(values)

        for acknowledgement_enabled, durable_enabled in (
            ("true", "false"),
            ("false", "true"),
        ):
            with self.subTest(
                acknowledgement_enabled=acknowledgement_enabled,
                durable_enabled=durable_enabled,
            ), self.assertRaisesRegex(ValueError, "enabled together"):
                BuzzReviewSettings.from_env_for_validation(_env(
                    acknowledgement_enabled=acknowledgement_enabled,
                    durable_acknowledgement_enabled=durable_enabled,
                ))

        values = _env()
        values["BUZZ_REVIEW_DURABLE_ACK_ENABLED"] = "1"
        with self.assertRaisesRegex(ValueError, "literal true or false"):
            BuzzReviewSettings.from_env_for_validation(values)

        values = _env()
        values["BUZZ_REVIEW_ACK_LEASE_SECONDS"] = "179"
        with self.assertRaisesRegex(ValueError, "between 180 and 600"):
            BuzzReviewSettings.from_env_for_validation(values)

    def test_factory_wires_publisher_only_for_explicit_staging_ack(self):
        disabled = build_origintrail_buzz_review_worker(
            BuzzReviewSettings.from_env_for_validation(_env())
        )
        self.assertIsNone(disabled.acknowledger)

        enabled = build_origintrail_buzz_review_worker(
            BuzzReviewSettings.from_env_for_validation(
                _env(
                    acknowledgement_enabled="true",
                    durable_acknowledgement_enabled="true",
                )
            )
        )
        self.assertIsInstance(enabled.acknowledger, BuzzCliPublisher)


if __name__ == "__main__":
    unittest.main()
