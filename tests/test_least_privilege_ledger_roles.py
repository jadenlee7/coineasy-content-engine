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
BATCH_REPOSITORY = (ROOT / "core" / "batch" / "repository.py").read_text()

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


def test_every_batch_rpc_call_site_has_a_grant():
    """No ledger routine may reach a call site without a role that can run it."""
    covered = _granted_routines("dispatcher_routines") | _granted_routines(
        "producer_routines"
    )
    ungranted = sorted(_batch_repository_rpcs() - covered)
    assert not ungranted, (
        "SupabaseBatchRepository calls routines no least-privilege role holds: "
        f"{ungranted}. Add them to the matching array in the migration."
    )


def test_dispatcher_holds_every_batch_rpc_except_the_producer_bridge():
    """Pins the dispatcher grant to its call sites in both directions."""
    dispatcher = _granted_routines("dispatcher_routines")
    expected = _batch_repository_rpcs() - PRODUCER_ONLY_BATCH_RPCS
    assert dispatcher == expected, (
        "dispatcher grant drifted from its call sites -- "
        f"missing {sorted(expected - dispatcher)}, "
        f"unused {sorted(dispatcher - expected)}"
    )


def test_dispatcher_cannot_queue_work():
    assert not (_granted_routines("dispatcher_routines") & PRODUCER_ONLY_BATCH_RPCS)


def test_migration_applies_after_every_routine_it_grants():
    """The guard in the migration only has teeth if the routines already exist."""
    migrations = sorted(p.name for p in (ROOT / "supabase" / "migrations").glob("*.sql"))
    least_privilege = [n for n in migrations if "least_privilege_ledger_roles" in n]
    assert len(least_privilege) == 1, least_privilege
    assert migrations[-1] == least_privilege[0], (
        "the least-privilege migration must sort last so its to_regprocedure "
        f"guard sees final signatures; last is {migrations[-1]}"
    )
