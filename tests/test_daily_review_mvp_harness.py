"""The integration runner refuses accidental non-local invocation before I/O."""
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/verify_daily_review_mvp_local.mjs"


@pytest.mark.parametrize("arguments", [[], ["--url", "https://example.invalid"],
    ["--local-only", "--database", "existing"], ["--production"]])
def test_daily_review_harness_requires_exact_opt_in(arguments):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    result = subprocess.run([node, str(SCRIPT), *arguments], cwd=ROOT,
        text=True, capture_output=True, timeout=10, check=False)
    assert result.returncode != 0
    assert "explicit --local-only from repository root required" in result.stderr
    assert "freshLocalMigrationFiles" not in result.stdout


def test_daily_review_harness_does_not_inherit_credentials():
    source = SCRIPT.read_text()
    assert "...process.env" not in source
    assert "-h '' -k ${dir}" in source
    assert "restartUnknownNeverRetry: true" in source
    assert "freshSchemaAclPassed: true" in source
