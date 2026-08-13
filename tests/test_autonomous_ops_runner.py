from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from scripts import run_origintrail_autonomous_ops as runner


SHA = "a" * 40


def _env() -> dict[str, str]:
    return {
        "AUTONOMOUS_OPS_ENABLED": "false",
        "AUTONOMOUS_OPS_RECORD_ENABLED": "false",
        "AUTONOMOUS_OPS_ALLOWED_CLIENTS": "origintrail",
        "AUTONOMOUS_OPS_URL": (
            "https://preview.example/api/autonomous-ops/origintrail"
        ),
        "AUTONOMOUS_OPS_WORKER_TOKEN": (
            "autonomous-ops-token-that-is-dedicated"
        ),
        "AUTONOMOUS_OPS_EXPECTED_ENVIRONMENT": "staging",
        "AUTONOMOUS_OPS_RELEASE_SHA": SHA,
        "RAILWAY_ENVIRONMENT_NAME": "staging",
        "RAILWAY_GIT_COMMIT_SHA": SHA,
    }


class AutonomousOpsRunnerTests(unittest.TestCase):
    def _run(self, args: list[str], env: dict[str, str]) -> tuple[int, dict]:
        output = StringIO()
        with patch.dict("os.environ", env, clear=True), redirect_stdout(output):
            code = runner.main(args)
        return code, json.loads(output.getvalue())

    def test_default_hold_is_zero_io(self):
        code, result = self._run([], {})
        self.assertEqual(code, 0)
        self.assertEqual(result["mode"], "hold")
        for key in (
            "database_calls", "relay_calls", "openai_calls", "batch_calls",
            "publication_calls", "deployment_calls",
        ):
            self.assertFalse(result[key])

    def test_disabled_validate_only_is_zero_io(self):
        code, result = self._run(["--validate-only"], _env())
        self.assertEqual(code, 0)
        self.assertEqual(result["execution_mode"], "propose_only")
        self.assertFalse(result["enabled"])
        self.assertFalse(result["openai_calls"])

    def test_disabled_run_once_holds_without_constructing_client(self):
        code, result = self._run(["--run-once"], _env())
        self.assertEqual(code, 0)
        self.assertEqual(result["reason"], "autonomous_ops_disabled")
        self.assertFalse(result["database_calls"])


if __name__ == "__main__":
    unittest.main()
