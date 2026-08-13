from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from core.buzz.settings import BuzzOperationsSettings
from scripts import run_origintrail_buzz_operations as runner


def _env(**overrides: str) -> dict[str, str]:
    values = {
        "BUZZ_OPERATIONS_ENABLED": "false",
        "BUZZ_OPERATIONS_RESPONSE_ENABLED": "false",
        "BUZZ_OPERATIONS_ALLOWED_CLIENTS": "origintrail",
        "BUZZ_OPERATIONS_URL": "https://console.example/api/buzz-operations/origintrail",
        "BUZZ_OPERATIONS_WORKER_TOKEN": "operations-token-that-is-dedicated-and-long",
        "BUZZ_OPERATIONS_REVIEWER_PUBKEYS": "a" * 64,
        "BUZZ_OPERATIONS_EXPECTED_ENVIRONMENT": "staging",
        "BUZZ_OPERATIONS_RELEASE_SHA": "b" * 40,
        "BUZZ_OPERATIONS_PROTOCOL_START_EPOCH": "1786000000",
        "BUZZ_OPERATIONS_RESPONSE_LEASE_SECONDS": "180",
        "BUZZ_RELAY_URL": "https://buzz.example",
        "BUZZ_PRIVATE_KEY": "c" * 64,
        "BUZZ_CHANNEL_ID": "33333333-3333-4333-8333-333333333333",
        "BUZZ_SERVICE_PUBKEY": "d" * 64,
        "BUZZ_CLI_PATH": "/opt/coineasy/bin/buzz",
        "RAILWAY_ENVIRONMENT_NAME": "staging",
        "RAILWAY_GIT_COMMIT_SHA": "b" * 40,
    }
    values.update(overrides)
    return values


class BuzzOperationsSettingsTests(unittest.TestCase):
    def test_disabled_validation_is_safe_and_fully_bound(self):
        settings = BuzzOperationsSettings.from_env_for_validation(_env())
        self.assertEqual(settings.deployment_environment, "staging")
        self.assertFalse(settings.response_enabled)
        self.assertEqual(settings.protocol_start_epoch, 1_786_000_000)

    def test_enabled_scanner_requires_response_gate_and_staging(self):
        with self.assertRaisesRegex(ValueError, "gates must match"):
            BuzzOperationsSettings.from_env_for_validation(_env(
                BUZZ_OPERATIONS_ENABLED="true",
            ))
        with self.assertRaisesRegex(ValueError, "restricted to staging"):
            BuzzOperationsSettings.from_env_for_validation(_env(
                BUZZ_OPERATIONS_ENABLED="true",
                BUZZ_OPERATIONS_RESPONSE_ENABLED="true",
                RAILWAY_ENVIRONMENT_NAME="production",
                BUZZ_OPERATIONS_EXPECTED_ENVIRONMENT="production",
            ))

    def test_operations_token_must_be_dedicated(self):
        token = _env()["BUZZ_OPERATIONS_WORKER_TOKEN"]
        with self.assertRaisesRegex(ValueError, "dedicated secret"):
            BuzzOperationsSettings.from_env_for_validation(_env(
                BUZZ_REVIEW_WORKER_TOKEN=token,
            ))


class BuzzOperationsRunnerTests(unittest.TestCase):
    def _run(self, arguments: list[str], environ: dict[str, str]):
        output = io.StringIO()
        with patch.dict(os.environ, environ, clear=True), redirect_stdout(output):
            code = runner.main(arguments)
        return code, json.loads(output.getvalue())

    def test_default_and_disabled_scan_are_zero_io_hold(self):
        for arguments in ([], ["--scan-once"]):
            code, result = self._run(arguments, _env())
            self.assertEqual(code, 0)
            self.assertEqual(result["mode"], "hold")
            for key in (
                "database_calls", "relay_calls", "openai_calls",
                "batch_calls", "publication_calls",
            ):
                self.assertFalse(result[key])

    def test_validate_only_reports_every_external_plane_false(self):
        code, result = self._run(["--validate-only"], _env())
        self.assertEqual(code, 0)
        self.assertEqual(result["mode"], "validate_only")
        self.assertFalse(result["enabled"])
        self.assertFalse(result["response_enabled"])
        self.assertFalse(result["openai_calls"])
        self.assertFalse(result["batch_calls"])
        self.assertFalse(result["publication_calls"])


if __name__ == "__main__":
    unittest.main()
