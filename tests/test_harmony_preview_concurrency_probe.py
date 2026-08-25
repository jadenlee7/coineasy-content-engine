from __future__ import annotations

import importlib.util
from pathlib import Path
import threading


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
    assert "_seed_specialists_sql" in source
    assert "harmony_preview_squid_specialist_bindings" in source
    assert "_race_exactly_once" in source
    assert '"harmony-preview-concurrency-proof@2"' in source
    assert '"plan": plan_race' in source
    for stage in (
        "private_content",
        "independent_qa",
        "operator_inbox",
        "recap",
    ):
        assert f'operation_races[stage] = stage_race' in source
        assert f'"{stage}"' in source
    assert '"wrong_principal_attempts": 5' in source
    assert '"wrong_principal_preemption_rows"' in source
    assert '"operator_inbox_stage4_delta"' in source
    assert '"recap_operator_inbox_delta"' in source
    assert "plan_expression(uid())" in source
    assert "_stage_expression(ids, stage, uid(), inbox_id, qa_evidence)" in source
    assert "import requests" not in source
    assert "urllib.request" not in source


def test_fixed_specialist_seed_has_five_distinct_stage_owners() -> None:
    ids = {"workspace": "11111111-1111-4111-8111-111111111111"}
    stages = (
        "plan",
        "private_content",
        "independent_qa",
        "operator_inbox",
        "recap",
    )
    principals = {
        stage: f"00000000-0000-4000-8000-{index:012d}"
        for index, stage in enumerate(stages, start=1)
    }
    sql = PROBE._seed_specialists_sql(
        ids,
        principals,
        "vllwcbhqdojpjrssidcu",
        "a" * 40,
        "b" * 64,
        "2026-08-25T18:49:07Z",
    )
    assert "private.harmony_preview_squid_specialist_bindings" in sql
    assert "'specialists', pg_catalog.count(*)" in sql
    assert "'distinct_principals'" in sql
    for stage, principal in principals.items():
        assert f"'{stage}'" in sql
        assert f"'{principal}'::uuid" in sql


def test_operator_and_recap_bind_the_same_existing_inbox() -> None:
    ids = {
        "workspace": "11111111-1111-4111-8111-111111111111",
        "round": "22222222-2222-4222-8222-222222222222",
        "plan": "33333333-3333-4333-8333-333333333333",
    }
    inbox_id = "44444444-4444-4444-8444-444444444444"
    operator = PROBE._stage_expression(
        ids,
        "operator_inbox",
        "55555555-5555-4555-8555-555555555555",
        inbox_id,
    )
    recap = PROBE._stage_expression(
        ids,
        "recap",
        "66666666-6666-4666-8666-666666666666",
        inbox_id,
    )
    private_content = PROBE._stage_expression(
        ids,
        "private_content",
        "77777777-7777-4777-8777-777777777777",
    )
    assert f"'{inbox_id}'::uuid" in operator
    assert f"'{inbox_id}'::uuid" in recap
    assert "null::uuid" in private_content


def test_exactly_once_race_accepts_fresh_transport_ids(monkeypatch) -> None:
    monkeypatch.setattr(PROBE, "CONCURRENCY", 4)
    lock = threading.Lock()
    committed = False

    def invoke(_: int) -> dict[str, object]:
        nonlocal committed
        with lock:
            reused = committed
            committed = True
        return {
            "ok": True,
            "reused": reused,
            "stage_receipt": {
                "receipt_id": "11111111-1111-4111-8111-111111111111",
                "receipt_sha256": "b" * 64,
                "operation_key_sha256": "a" * 64,
            },
            "external_calls": False,
            "provider_calls": False,
            "publication_calls": False,
            "automatic_publication": False,
        }

    row, counts = PROBE._race_exactly_once("plan", invoke)
    assert row["ok"] is True
    assert counts == {"new": 1, "reused": 3}
