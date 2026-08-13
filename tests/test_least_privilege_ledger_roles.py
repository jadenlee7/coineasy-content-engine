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
BUZZ_REVIEW_ROLE_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260808133000_buzz_review_decider_role.sql"
).read_text()
BUZZ_REVIEW_ACK_ROLE_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260811161000_buzz_review_ack_role.sql"
).read_text()
REVIEW_PACK_ROLE_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260808140000_origintrail_review_pack_roles.sql"
).read_text()
BATCH_REPOSITORY = (ROOT / "core" / "batch" / "repository.py").read_text()
BUZZ_DELIVERY_ADAPTER = (
    ROOT / "netlify" / "functions" / "_shared" / "buzz-delivery.mts"
).read_text()
BUZZ_REVIEW_ADAPTER = (
    ROOT / "netlify" / "functions" / "_shared" / "buzz-review.mts"
).read_text()

# `queue_agent_batch_job` lives in the same repository class but is reached only
# through `BatchQueueBridge`, which runs in the producer. Keeping the dispatcher
# off it is the specific separation ADR-007 buys, so it is named here rather
# than inferred.
PRODUCER_ONLY_BATCH_RPCS = frozenset({"queue_agent_batch_job"})


def _granted_routines(
    role_array: str, migration: str = MIGRATION
) -> frozenset[str]:
    """Routine names in one `<role>_routines constant text[] := array[...]`."""
    block = re.search(
        rf"{role_array} constant text\[\] := array\[(.*?)\n    \];",
        migration,
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


def test_buzz_delivery_grant_matches_the_netlify_adapter_exactly():
    """The buzz role covers exactly the RPCs the delivery adapter issues."""
    called = frozenset(
        re.findall(r'name: "([a-z_]+)"', BUZZ_DELIVERY_ADAPTER)
    )
    assert called, "no RPC names parsed from the buzz delivery adapter"
    granted = _granted_routines("buzz_delivery_routines") | frozenset(
        re.findall(r"public[.]([a-z_]+)[(]", REVIEW_PACK_ROLE_MIGRATION)
    )
    assert granted == called, (
        "buzz delivery grant drifted from the adapter's RPCs -- "
        f"missing {sorted(called - granted)}, unused {sorted(granted - called)}"
    )


def test_buzz_review_grant_matches_the_netlify_adapter_exactly():
    called = frozenset(
        re.findall(
            r'"((?:list|record|claim|mark|complete|fail|reconcile)_origintrail_buzz_[a-z_]+)"',
            BUZZ_REVIEW_ADAPTER,
        )
    )
    granted = frozenset(re.findall(
        r"'public[.]([a-z_]+)[(]",
        BUZZ_REVIEW_ACK_ROLE_MIGRATION,
    ))
    assert granted == called, (
        "buzz review grant drifted from the adapter's RPCs -- "
        f"missing {sorted(called - granted)}, unused {sorted(granted - called)}"
    )


def test_migration_applies_after_every_routine_it_grants():
    """The guard in the migration only has teeth if the routines already exist."""
    migrations = sorted(p.name for p in (ROOT / "supabase" / "migrations").glob("*.sql"))
    least_privilege = [n for n in migrations if "least_privilege_ledger_roles" in n]
    review_role = [n for n in migrations if "buzz_review_decider_role" in n]
    final_role = [n for n in migrations if "origintrail_review_pack_roles" in n]
    evidence_role = [n for n in migrations if "origintrail_review_evidence_roles" in n]
    acknowledgement_role = [n for n in migrations if "buzz_review_ack_role" in n]
    assert len(least_privilege) == 1, least_privilege
    assert len(review_role) == 1, review_role
    assert len(final_role) == 1, final_role
    assert len(evidence_role) == 1, evidence_role
    assert len(acknowledgement_role) == 1, acknowledgement_role
    assert least_privilege[0] < review_role[0]
    assert review_role[0] < final_role[0]
    assert final_role[0] < evidence_role[0]
    assert evidence_role[0] < acknowledgement_role[0]
    # Unrelated migrations may follow this final Buzz-role grant. None may
    # redefine a routine whose exact signature the guard already checked;
    # doing so would make the earlier to_regprocedure assertion stale.
    guarded_routines = _granted_routines(
        "review_routines", BUZZ_REVIEW_ACK_ROLE_MIGRATION
    )
    later_migrations = migrations[
        migrations.index(acknowledgement_role[0]) + 1:
    ]
    for migration_name in later_migrations:
        body = (
            ROOT / "supabase" / "migrations" / migration_name
        ).read_text().lower()
        changed = sorted(
            routine
            for routine in guarded_routines
            if re.search(
                rf"(?:create\s+or\s+replace|drop)\s+function\s+"
                rf"public[.]{re.escape(routine)}\s*[(]",
                body,
            )
        )
        assert not changed, (
            f"{migration_name} redefines routines after their final scoped "
            f"role grant: {changed}"
        )
