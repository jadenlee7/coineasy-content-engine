from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from core.buzz.settings import BuzzDeliverySettings
from scripts import run_origintrail_buzz_delivery as runner


def _env(*, enabled: str = "false") -> dict[str, str]:
    return {
        "BUZZ_DELIVERY_ENABLED": enabled,
        "BUZZ_DELIVERY_ALLOWED_CLIENTS": "origintrail",
        "BUZZ_SHADOW_URL": "https://console.example/api/buzz-shadow/origintrail/batch",
        "BUZZ_DELIVERY_URL": "https://console.example/api/buzz-delivery/origintrail",
        "BUZZ_SHADOW_ACCESS_TOKEN": "shadow-token-that-is-dedicated-and-long-enough",
        "BUZZ_DELIVERY_WORKER_TOKEN": "delivery-token-that-is-dedicated-and-long-enough",
        "BUZZ_RELAY_URL": "https://buzz.example",
        "BUZZ_PRIVATE_KEY": "1" * 64,
        "BUZZ_CHANNEL_ID": "33333333-3333-4333-8333-333333333333",
        "BUZZ_CLI_PATH": "/opt/coineasy/bin/buzz",
        "BUZZ_DELIVERY_LEASE_SECONDS": "180",
        "RAILWAY_GIT_COMMIT_SHA": "d" * 40,
        "BUZZ_DELIVERY_RELEASE_SHA": "d" * 40,
    }


def _auth_tag(conditions: str = "") -> str:
    return json.dumps(["auth", "a" * 64, conditions, "b" * 128])


class BuzzSettingsRunnerTests(unittest.TestCase):
    def test_validation_mode_constructs_no_io_worker(self):
        with patch.dict(os.environ, _env(), clear=True), patch.object(
            runner, "_build_worker", side_effect=AssertionError("must not build")
        ), patch("builtins.print") as output:
            self.assertEqual(runner.main(["--validate-only"]), 0)
        result = json.loads(output.call_args.args[0])
        self.assertEqual(result, {
            "ok": True,
            "mode": "validate_only",
            "enabled": False,
            "client_id": "origintrail",
            "channel_id": "33333333-3333-4333-8333-333333333333",
            "runtime_release_verified": True,
            "provider_calls": False,
            "database_calls": False,
            "shadow_calls": False,
        })

    def test_default_mode_is_structural_hold_with_no_environment(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            runner, "_build_worker", side_effect=AssertionError("must not build")
        ), patch("builtins.print") as output:
            self.assertEqual(runner.main([]), 0)
        result = json.loads(output.call_args.args[0])
        self.assertEqual(result["mode"], "hold")
        self.assertFalse(result["provider_calls"])
        self.assertFalse(result["database_calls"])
        self.assertFalse(result["shadow_calls"])

    def test_disabled_scheduled_send_is_a_successful_no_io_hold(self):
        with patch.dict(os.environ, _env(enabled="false"), clear=True), patch.object(
            runner, "_build_worker", side_effect=AssertionError("must not build")
        ), patch("builtins.print") as output:
            self.assertEqual(runner.main(["--send-once"]), 0)
        self.assertEqual(json.loads(output.call_args.args[0]), {
            "ok": True,
            "enabled": False,
            "mode": "hold",
            "reason": "buzz_delivery_disabled",
            "provider_calls": False,
            "database_calls": False,
            "shadow_calls": False,
        })

    def test_send_once_rejects_nonliteral_enabled_before_building_worker(self):
        values = _env(enabled="TRUE")
        with patch.dict(os.environ, values, clear=True), patch.object(
            runner, "_build_worker", side_effect=AssertionError("must not build")
        ), patch("builtins.print") as output:
            self.assertEqual(runner.main(["--send-once"]), 1)
        self.assertEqual(
            json.loads(output.call_args.args[0]),
            {
                "ok": False,
                "mode": "send_once",
                "error": "buzz_delivery_configuration_invalid",
                "provider_calls": False,
                "database_calls": False,
                "shadow_calls": False,
            },
        )

    def test_release_fence_is_exact_and_required_in_validation_mode(self):
        cases = (
            ("missing actual", "RAILWAY_GIT_COMMIT_SHA", None),
            ("missing authorized", "BUZZ_DELIVERY_RELEASE_SHA", None),
            ("malformed actual", "RAILWAY_GIT_COMMIT_SHA", "D" * 40),
            ("malformed authorized", "BUZZ_DELIVERY_RELEASE_SHA", "d" * 39),
            ("mismatched", "BUZZ_DELIVERY_RELEASE_SHA", "e" * 40),
        )
        for label, name, value in cases:
            with self.subTest(label=label):
                values = _env()
                if value is None:
                    values.pop(name)
                else:
                    values[name] = value
                with self.assertRaisesRegex(ValueError, "release SHA fence"):
                    BuzzDeliverySettings.from_env_for_validation(values)

    def test_live_release_mismatch_fails_before_worker_with_no_io_proof(self):
        values = _env(enabled="true")
        values["BUZZ_DELIVERY_RELEASE_SHA"] = "e" * 40
        with patch.dict(os.environ, values, clear=True), patch.object(
            runner, "_build_worker", side_effect=AssertionError("must not build")
        ), patch("builtins.print") as output:
            self.assertEqual(runner.main(["--send-once"]), 1)
        self.assertEqual(
            json.loads(output.call_args.args[0]),
            {
                "ok": False,
                "mode": "send_once",
                "error": "buzz_delivery_configuration_invalid",
                "provider_calls": False,
                "database_calls": False,
                "shadow_calls": False,
            },
        )

    def test_validation_release_mismatch_is_a_no_io_failure(self):
        values = _env()
        values["BUZZ_DELIVERY_RELEASE_SHA"] = "e" * 40
        with patch.dict(os.environ, values, clear=True), patch.object(
            runner, "_build_worker", side_effect=AssertionError("must not build")
        ), patch("builtins.print") as output:
            self.assertEqual(runner.main(["--validate-only"]), 1)
        self.assertEqual(
            json.loads(output.call_args.args[0]),
            {
                "ok": False,
                "mode": "validate_only",
                "error": "buzz_delivery_validation_failed",
                "provider_calls": False,
                "database_calls": False,
                "shadow_calls": False,
            },
        )

    def test_settings_reject_cross_origin_control_endpoint(self):
        values = _env()
        values["BUZZ_DELIVERY_URL"] = "https://attacker.example/api/buzz-delivery/origintrail"
        with self.assertRaisesRegex(ValueError, "share one origin"):
            BuzzDeliverySettings.from_env_for_validation(values)

    def test_settings_reject_reused_read_and_delivery_token(self):
        values = _env()
        values["BUZZ_DELIVERY_WORKER_TOKEN"] = values["BUZZ_SHADOW_ACCESS_TOKEN"]
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            BuzzDeliverySettings.from_env_for_validation(values)

    def test_settings_reject_any_client_expansion(self):
        values = _env()
        values["BUZZ_DELIVERY_ALLOWED_CLIENTS"] = "origintrail,squid"
        with self.assertRaisesRegex(ValueError, "must be origintrail"):
            BuzzDeliverySettings.from_env_for_validation(values)

    def test_settings_accept_exact_nip_oa_auth_tag(self):
        values = _env()
        values["BUZZ_AUTH_TAG"] = _auth_tag(
            "kind=65535&created_at<4294967295&created_at>0"
        )
        settings = BuzzDeliverySettings.from_env_for_validation(values)
        self.assertEqual(
            settings.auth_tag,
            '["auth","' + ("a" * 64) + '",'
            '"kind=65535&created_at<4294967295&created_at>0","'
            + ("b" * 128) + '"]',
        )

    def test_settings_reject_legacy_object_auth_tag(self):
        values = _env()
        values["BUZZ_AUTH_TAG"] = json.dumps({"auth": ["a" * 64]})
        with self.assertRaisesRegex(ValueError, "four-string NIP-OA"):
            BuzzDeliverySettings.from_env_for_validation(values)

    def test_settings_reject_malformed_nip_oa_auth_tags(self):
        malformed = (
            json.dumps(["delegation", "a" * 64, "", "b" * 128]),
            json.dumps(["auth", "A" * 64, "", "b" * 128]),
            json.dumps(["auth", "a" * 64, "kind=01", "b" * 128]),
            json.dumps(["auth", "a" * 64, "kind=65536", "b" * 128]),
            json.dumps(["auth", "a" * 64, "created_at<4294967296", "b" * 128]),
            json.dumps(["auth", "a" * 64, "kind=1&", "b" * 128]),
            json.dumps(["auth", "a" * 64, "", "B" * 128]),
        )
        for auth_tag in malformed:
            with self.subTest(auth_tag_length=len(auth_tag)):
                values = _env()
                values["BUZZ_AUTH_TAG"] = auth_tag
                with self.assertRaisesRegex(ValueError, "BUZZ_AUTH_TAG"):
                    BuzzDeliverySettings.from_env_for_validation(values)


if __name__ == "__main__":
    unittest.main()
