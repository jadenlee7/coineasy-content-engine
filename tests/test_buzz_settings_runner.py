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

    def test_send_once_requires_literal_enabled_before_building_worker(self):
        with patch.dict(os.environ, _env(enabled="false"), clear=True), patch.object(
            runner, "_build_worker", side_effect=AssertionError("must not build")
        ), patch("builtins.print") as output:
            self.assertEqual(runner.main(["--send-once"]), 1)
        self.assertEqual(
            json.loads(output.call_args.args[0])["error"],
            "buzz_delivery_worker_unavailable",
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
