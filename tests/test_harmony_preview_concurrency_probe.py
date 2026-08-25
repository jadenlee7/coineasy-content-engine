from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "probe_harmony_preview_concurrency.py"
SPEC = importlib.util.spec_from_file_location("harmony_preview_probe", SCRIPT)
assert SPEC and SPEC.loader
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def test_probe_accepts_only_local_hosts() -> None:
    assert PROBE._is_local_host("localhost")
    assert PROBE._is_local_host("127.0.0.1")
    assert PROBE._is_local_host("/private/tmp/harmony-pg")
    assert not PROBE._is_local_host("db.example.supabase.co")


def test_preview_probe_requires_a_direct_child_branch_host() -> None:
    child = "vllwcbhqdojpjrssidcu"
    parent = "isuqcqwxpojgzevxfdwr"
    assert PROBE._validated_disposable_preview_ref(
        f"db.{child}.supabase.co", 5432, child, parent
    ) == child
    for host, port, expected, parent_ref in (
        (f"db.{parent}.supabase.co", 5432, parent, parent),
        (f"db.{child}.supabase.co", 5432, parent, parent),
        ("aws-0-us-east-1.pooler.supabase.com", 5432, child, parent),
        (f"db.{child}.supabase.co", 6543, child, parent),
    ):
        try:
            PROBE._validated_disposable_preview_ref(
                host, port, expected, parent_ref
            )
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe Preview target was accepted")


def test_rpc_runs_as_exact_scoped_role() -> None:
    claims = {
        "role": "coineasy_harmony_connector",
        "client_id": "squid",
    }
    sql = PROBE._rpc_sql(claims, "'{}'::jsonb")
    assert "set local role coineasy_harmony_connector" in sql
    assert "request.jwt.claims" in sql
    assert "service_role" not in sql


def test_probe_contract_is_closed_and_fail_closed() -> None:
    source = SCRIPT.read_text()
    assert 'topic = "official_update"' in source
    assert "CONCURRENCY = 64" in source
    assert "new_count, reused_count" in source
    assert '"signals": 1, "connector_receipts": 1' in source
    assert "side_effect_baseline_unchanged" in source
    assert "confirm_disposable_local" in source
    assert "confirm_disposable_preview" in source
    assert "expected_branch_ref" in source
    assert "parent_project_ref" in source
    assert "release_sha" in source
    assert "config_sha256" in source
    assert "command_timeout_seconds" in source
    assert "fence_ttl_minutes" in source
    assert "max_connections" in source
    assert "psql_timeout_commit_state_unknown_no_retry" in source
    assert "refusing to run the disposable Preview probe against Production" in source
    assert "import requests" not in source
    assert "urllib.request" not in source
