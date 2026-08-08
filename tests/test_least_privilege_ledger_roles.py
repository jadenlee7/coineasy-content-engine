"""Drift guard for the least-privilege ledger roles.

`supabase/tests/agent_batch_ledger_least_privilege.sql` proves each role holds
EXECUTE on exactly the set the migration grants. That is a closed loop: both
sides are the migration, so adding a routine to the ledger and calling it from
the dispatcher keeps the SQL suite green while the scoped credential silently
lacks the grant it needs.

This module closes the loop against the call sites. It parses the routine names
out of the migration and compares them to the RPC names `SupabaseBatchRepository`
actually issues, so a new ledger routine that reaches a call site without a
matching grant fails the build instead of failing at cutover.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260805090000_least_privilege_ledger_roles.sql"
).read_text()
TERMINAL_REFRESH = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260808130000_origintrail_media_fact_evidence_role_refresh.sql"
)
AUTOMATION_REPOSITORY = (ROOT / "core" / "automation" / "repository.py").read_text()
BATCH_REPOSITORY = (ROOT / "core" / "batch" / "repository.py").read_text()
BUZZ_DELIVERY_ADAPTER = (
    ROOT / "netlify" / "functions" / "_shared" / "buzz-delivery.mts"
).read_text()

# `queue_agent_batch_job` lives in the same repository class but is reached only
# through `BatchQueueBridge`, which runs in the producer. Keeping the dispatcher
# off it is the specific separation ADR-007 buys, so it is named here rather
# than inferred.
PRODUCER_ONLY_BATCH_RPCS = frozenset({"queue_agent_batch_job"})


def _granted_routines(role_array: str) -> frozenset[str]:
    """Routine names in one `<role>_routines constant text[] := array[...]`."""
    block = re.search(
        rf"{role_array} constant text\[\] := array\[(.*?)\n    \];",
        MIGRATION,
        re.DOTALL,
    )
    assert block is not None, f"{role_array} array not found in the migration"
    names = re.findall(r"'public\.([a-z_]+)\(", block.group(1))
    assert names, f"{role_array} array parsed to nothing"
    return frozenset(names)


def _batch_repository_rpcs() -> frozenset[str]:
    names = re.findall(r'_rpc\(\s*"([a-z_]+)"', BATCH_REPOSITORY)
    assert names, "no _rpc call sites parsed from core/batch/repository.py"
    return frozenset(names)


def _refresh_granted_routines(role_name: str) -> frozenset[str]:
    """Explicit forward grants made after the baseline role manifest."""
    refresh = TERMINAL_REFRESH.read_text()
    names = re.findall(
        rf"grant execute on function\s+public\.([a-z_]+)\([^;]+?\)\s+"
        rf"to\s+{role_name}\s*;",
        refresh,
        re.DOTALL,
    )
    return frozenset(names)


def _effective_grants(role_array: str, role_name: str) -> frozenset[str]:
    return _granted_routines(role_array) | _refresh_granted_routines(role_name)


def _automation_repository_rpcs() -> frozenset[str]:
    """Literal RPC call sites reached by the daily-runner producer."""
    names = re.findall(
        r'self\._rpc\(\s*"([a-z_]+)"',
        AUTOMATION_REPOSITORY,
        re.DOTALL,
    )
    assert names, "no _rpc call sites parsed from core/automation/repository.py"
    return frozenset(names)


def test_every_batch_rpc_call_site_has_a_grant():
    """No ledger routine may reach a call site without a role that can run it."""
    covered = _effective_grants(
        "dispatcher_routines", "coineasy_batch_dispatcher"
    ) | _effective_grants("producer_routines", "coineasy_batch_producer")
    ungranted = sorted(_batch_repository_rpcs() - covered)
    assert not ungranted, (
        "SupabaseBatchRepository calls routines no least-privilege role holds: "
        f"{ungranted}. Add them to the matching array in the migration."
    )


def test_dispatcher_holds_every_batch_rpc_except_the_producer_bridge():
    """Pins the dispatcher grant to its call sites in both directions."""
    dispatcher = _effective_grants(
        "dispatcher_routines", "coineasy_batch_dispatcher"
    )
    expected = _batch_repository_rpcs() - PRODUCER_ONLY_BATCH_RPCS
    assert dispatcher == expected, (
        "dispatcher grant drifted from its call sites -- "
        f"missing {sorted(expected - dispatcher)}, "
        f"unused {sorted(dispatcher - expected)}"
    )


def test_dispatcher_cannot_queue_work():
    assert not (
        _effective_grants("dispatcher_routines", "coineasy_batch_dispatcher")
        & PRODUCER_ONLY_BATCH_RPCS
    )


def test_every_literal_automation_rpc_call_site_has_a_producer_grant():
    producer = _effective_grants(
        "producer_routines", "coineasy_batch_producer"
    )
    ungranted = sorted(_automation_repository_rpcs() - producer)
    assert not ungranted, (
        "AutomationRepository calls routines the producer role cannot execute: "
        f"{ungranted}. Add a forward grant and keep its refresh migration last."
    )


def test_buzz_delivery_grant_matches_the_netlify_adapter_exactly():
    """The buzz role covers exactly the RPCs the delivery adapter issues."""
    called = frozenset(
        re.findall(r'name: "([a-z_]+)"', BUZZ_DELIVERY_ADAPTER)
    )
    assert called, "no RPC names parsed from the buzz delivery adapter"
    granted = _effective_grants(
        "buzz_delivery_routines", "coineasy_buzz_delivery"
    )
    assert granted == called, (
        "buzz delivery grant drifted from the adapter's RPCs -- "
        f"missing {sorted(called - granted)}, unused {sorted(granted - called)}"
    )


def test_terminal_refresh_applies_after_every_routine_it_grants():
    """Every post-baseline routine change must finish with its role refresh."""
    migrations = sorted(p.name for p in (ROOT / "supabase" / "migrations").glob("*.sql"))
    assert TERMINAL_REFRESH.exists()
    assert migrations[-1] == TERMINAL_REFRESH.name, (
        "the least-privilege refresh must sort last so its to_regprocedure "
        f"guard sees final signatures; last is {migrations[-1]}"
    )


def test_media_evidence_rpc_is_refreshed_for_producer_only():
    """The new evidence read follows the producer credential, never reviewer."""
    refresh = TERMINAL_REFRESH.read_text()
    assert "get_origintrail_reviewed_source_evidence" in _effective_grants(
        "producer_routines", "coineasy_batch_producer"
    )
    assert re.search(
        r"grant execute on function\s+"
        r"public\.get_origintrail_reviewed_source_evidence\(uuid, uuid, text\)\s+"
        r"to coineasy_batch_producer",
        refresh,
        re.DOTALL,
    )
    assert re.search(
        r"revoke execute on function\s+"
        r"public\.get_origintrail_reviewed_source_evidence\(uuid, uuid, text\)\s+"
        r"from coineasy_batch_dispatcher,\s+"
        r"coineasy_batch_reviewer,\s+"
        r"coineasy_buzz_delivery",
        refresh,
        re.DOTALL,
    )
