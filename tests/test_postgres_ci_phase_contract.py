"""Keep proposal security tests mandatory without running before their schema."""
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFERRED = "supabase/tests/four_client_exact_telegram_security.sql"


def postgres_steps():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["postgres"]["steps"]


def test_baseline_smoke_executes_every_migration_security_test_except_explicit_proposal_fixture():
    step = next(s for s in postgres_steps() if s.get("name") == "Run transactional SQL security smoke tests")
    # Enumerate the actual CI shell loop with a no-DB psql stub. Do not run SQL.
    result = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c",
        "psql() { printf '%s\\n' \"$*\"; };\n" + step["run"]], cwd=ROOT,
        capture_output=True, text=True, timeout=10, check=True)
    actual = [line.removeprefix("-X -v ON_ERROR_STOP=1 -f ") for line in result.stdout.splitlines()]
    expected = {str(p.relative_to(ROOT)) for pattern in ("*_security.sql", "*_least_privilege.sql")
                for p in (ROOT / "supabase/tests").glob(pattern)} - {DEFERRED}
    assert set(actual) == expected
    assert len(actual) == len(expected)
    assert DEFERRED not in actual


def test_deferred_fixture_still_runs_after_its_proposal_and_before_channel_queue_checks():
    steps = postgres_steps()
    baseline = next(i for i, s in enumerate(steps) if s.get("name") == "Run transactional SQL security smoke tests")
    owner = next(i for i, s in enumerate(steps) if s.get("name") == "Prove atomic four-client human approval and channel admission")
    assert owner > baseline
    commands = steps[owner]["run"].splitlines()
    proposal = "psql -X -v ON_ERROR_STOP=1 -f supabase/proposals/content_ops_four_client_exact_telegram.sql"
    security = "psql -X -v ON_ERROR_STOP=1 -f " + DEFERRED
    queue = "psql -X -v ON_ERROR_STOP=1 -f supabase/tests/content_ops_final_publication_queue.sql"
    assert commands.count(proposal) == commands.count(security) == commands.count(queue) == 1
    assert commands.index(security) == commands.index(proposal) + 1
    assert commands.index(queue) > commands.index(security)
    assert "private.exact_telegram_allowed_client_subset" in (ROOT / DEFERRED).read_text()
