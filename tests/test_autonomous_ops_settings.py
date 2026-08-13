from __future__ import annotations

import unittest

from core.autonomous_ops_settings import AutonomousOpsSettings


SHA = "a" * 40
TOKEN = "autonomous-ops-token-that-is-dedicated"


def _env(**overrides: str) -> dict[str, str]:
    return {
        "AUTONOMOUS_OPS_ENABLED": "false",
        "AUTONOMOUS_OPS_RECORD_ENABLED": "false",
        "AUTONOMOUS_OPS_ALLOWED_CLIENTS": "origintrail",
        "AUTONOMOUS_OPS_URL": (
            "https://deploy-preview.example/api/autonomous-ops/origintrail"
        ),
        "AUTONOMOUS_OPS_WORKER_TOKEN": TOKEN,
        "AUTONOMOUS_OPS_EXPECTED_ENVIRONMENT": "staging",
        "AUTONOMOUS_OPS_RELEASE_SHA": SHA,
        "RAILWAY_ENVIRONMENT_NAME": "staging",
        "RAILWAY_GIT_COMMIT_SHA": SHA,
        **overrides,
    }


class AutonomousOpsSettingsTests(unittest.TestCase):
    def test_disabled_validation_is_allowed(self):
        settings = AutonomousOpsSettings.from_env_for_validation(_env())
        self.assertEqual(settings.environment, "staging")

    def test_runtime_requires_both_gates(self):
        with self.assertRaises(ValueError):
            AutonomousOpsSettings.from_env(_env())
        with self.assertRaisesRegex(ValueError, "gates must match"):
            AutonomousOpsSettings.from_env_for_validation(_env(
                AUTONOMOUS_OPS_ENABLED="true",
            ))
        settings = AutonomousOpsSettings.from_env(_env(
            AUTONOMOUS_OPS_ENABLED="true",
            AUTONOMOUS_OPS_RECORD_ENABLED="true",
        ))
        self.assertEqual(settings.release_sha, SHA)

    def test_production_and_release_drift_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "restricted to staging"):
            AutonomousOpsSettings.from_env_for_validation(_env(
                RAILWAY_ENVIRONMENT_NAME="production",
                AUTONOMOUS_OPS_EXPECTED_ENVIRONMENT="production",
            ))
        with self.assertRaisesRegex(ValueError, "release SHA fence"):
            AutonomousOpsSettings.from_env_for_validation(_env(
                AUTONOMOUS_OPS_RELEASE_SHA="b" * 40,
            ))

    def test_token_must_be_dedicated(self):
        with self.assertRaisesRegex(ValueError, "must be dedicated"):
            AutonomousOpsSettings.from_env_for_validation(_env(
                OPENAI_API_KEY=TOKEN,
            ))


if __name__ == "__main__":
    unittest.main()
