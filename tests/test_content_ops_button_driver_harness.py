"""Safety/coverage guards; real-driver behavior is exercised by the local harness."""
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/verify_button_edit_driver_local.py'
TEXT = SCRIPT.read_text()


@pytest.mark.parametrize('socket', ['https://not-a-database.invalid', 'localhost', '/tmp', '/private/tmp/unrelated'])
def test_driver_harness_rejects_non_disposable_targets_before_driver_import(socket):
    result = subprocess.run([sys.executable, str(SCRIPT), '--local-socket', socket,
                             '--phase', 'initial'], cwd=ROOT, capture_output=True, timeout=5)
    assert result.returncode != 0
    assert b'disposable local socket required' in result.stderr
    assert b'No module named' not in result.stderr


def test_driver_connection_is_explicit_local_and_bounded():
    assert "path.resolve() != path" in TEXT and 'stat.S_ISSOCK' in TEXT
    assert "k.startswith('PG') or k == 'DATABASE_URL'" in TEXT
    assert "dbname='postgres', connect_timeout=5" in TEXT
    assert 'statement_timeout=10000 -c lock_timeout=5000' in TEXT
    assert 'host=str(path)' in TEXT


def test_full_schema_integration_covers_transactions_clients_and_acl():
    for marker in ("('yellow','babylon','squid','origintrail')", "('telegram','x')",
                   'Barrier(8)', "args.phase == 'restart'", "('receipt',1),('commit_ack',2)",
                   "('anon','authenticated','service_role')", 'psycopg.errors.InsufficientPrivilege'):
        assert marker in TEXT
    harness = (ROOT / 'scripts/verify_button_review_state_local.mjs').read_text()
    assert "driverTest('initial')" in harness and "driverTest('restart')" in harness
    assert 'timeout: 60000' in harness
