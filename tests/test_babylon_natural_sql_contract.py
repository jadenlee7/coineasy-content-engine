"""Static body-only installation contract; disposable PG proves behavior."""
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = ROOT / 'supabase/proposals/babylon_natural_once.rollback.sql'
PREVIOUS = ROOT / 'supabase/proposals/standalone_babylon_trial.rollback.sql'
HASHES = {
    'claim_exact_natural_review_draft_job': '532ef9755d1909887ad0bf206330ad6b76362433ad1c5d2ec0297df62b989152',
    'get_quality_banner_generation_context': '03cd9ff323cabdbad97cc6f3571b86fb3c1f7dadec97378f3ec8adb3cc6c548e',
}


def routine(source, name):
    match = re.search(r'create or replace function public\.' + name + r'\([\s\S]*?as \$\$([\s\S]*?)\$\$;', source)
    assert match is not None
    return match.group(1)


def test_two_exact_preimages_and_rollback_only_no_privilege_or_object_expansion():
    source, previous = PROPOSAL.read_text(), PREVIOUS.read_text()
    for name, expected in HASHES.items():
        assert hashlib.sha256(routine(previous, name).encode()).hexdigest() == expected
        assert "is distinct from '" + expected + "'" in source
        assert name + '_nonbody_catalog_changed' in source
    assert source.endswith('rollback; -- LOCAL_ROLLBACK_PROOF; never change to COMMIT here.\n')
    executable = re.sub(r'--[^\n]*', '', source).lower()
    assert executable.count('create or replace function') == 2
    assert executable.count('create temporary table') == 2
    assert not re.search(r'\b(grant|revoke|insert|delete|truncate|alter)\b', executable)
    assert 'create table' not in executable
    assert executable.count('update public.jobs set') == 1
    assert "set local lock_timeout='5s'" in source


def test_claim_adds_only_exact_request_bound_fresh_babylon_branch():
    previous = routine(PREVIOUS.read_text(), 'claim_exact_natural_review_draft_job')
    current = routine(PROPOSAL.read_text(), 'claim_exact_natural_review_draft_job')
    start = current.index('    -- A fresh natural Babylon reservation')
    end = current.index("    elsif target_worker_id='ybo-trial:7b27", start)
    branch = current[start:end]
    assert "target_client_id='babylon'" in branch
    assert "target_worker_id='babylon-natural-once:'||target_request_id::text" in branch
    assert "candidate.created_at+interval '2 hours'" in branch
    assert "timezone('Asia/Seoul',candidate.created_at)::date+1" in branch
    assert 'candidate.id=target_job_id and candidate.workspace_id=target_workspace_id' in branch
    old_header = previous[previous.index('    -- The prior readiness'):previous.index("    if target_worker_id='ybo-trial:7b27")]
    current = current[:start] + old_header + current[end:].replace('    elsif ', '    if ', 1)
    current = current.replace('    natural_once boolean := false;\n', '')
    current = current.replace('\n       or (natural_once and job.created_at is distinct from gate_start)', '')
    assert current == previous


def test_context_preserves_old_branches_and_requires_more_than_owner_prefix():
    previous = routine(PREVIOUS.read_text(), 'get_quality_banner_generation_context')
    current = routine(PROPOSAL.read_text(), 'get_quality_banner_generation_context')
    start = current.index('      -- The reusable owner is not trusted')
    end = current.index('      and job.attempts between', start)
    fence = current[start:end]
    for guard in ("target_client_id='babylon'", "job.locked_by='babylon-natural-once:'||target_request_id::text",
                  'job.started_at=job.locked_at', 'job.attempts=1 and job.max_attempts=3',
                  "job.created_at+interval '2 hours'", "job.locked_at+interval '900 seconds'",
                  "pack.reference_pack_hash ~ '^[a-f0-9]{32}$'", 'job.input=jsonb_build_object(',
                  "exact_producer.input->>'request_id'=target_request_id::text"):
        assert guard in fence
    current = current[:start] + current[end:]
    current = current.replace("            (target_client_id='babylon'\n"
        "              and job.locked_by='babylon-natural-once:'||target_request_id::text)\n"
        "            or (job.locked_by='ybo-trial:1a2bef47-4827-4bc3-9b4d-5baf2714015d'",
        "            (job.locked_by='ybo-trial:1a2bef47-4827-4bc3-9b4d-5baf2714015d'")
    assert current == previous


def test_local_harness_is_explicit_isolated_and_preserves_production_sql():
    script = (ROOT / 'scripts/verify_babylon_natural_local.mjs').read_text()
    harness = (ROOT / 'ops/babylon-natural-once/natural_local_unittest.py').read_text()
    assert "--local-only" in script and "--local-only" in harness
    assert "-h '' -k ${directory}" in script
    assert "helper.local_env()" in harness
    assert 'sql(cls.original)' in harness
    assert "sql('select babylon_natural_fixture.snapshot();') == cls.rows_before" in harness
    assert "non_body_catalog_sha256" in harness
    assert 'standalone_claim_cases.sql' in harness and 'standalone_context_cases.sql' in harness
