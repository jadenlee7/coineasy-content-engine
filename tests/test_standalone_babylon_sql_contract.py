"""Static installation contract; the disposable PG suite proves SQL behavior."""
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = ROOT / 'supabase/proposals/standalone_babylon_trial.rollback.sql'
PREVIOUS = ROOT / 'supabase/proposals/readiness_babylon_trial.rollback.sql'
TRIAL = '7b27e277-0641-4aef-b739-761f86547287'
HASHES = {
    'claim_exact_natural_review_draft_job': '0fc3dfa2ffa4b9e4619996c0faa28b2af5719f8c40f660eceedd48291dd68152',
    'get_quality_banner_generation_context': '57249c5210b32ead5e529a5dad3eab1c9213563550f3e50a09fd1083ff419e2b',
}


def routine(source, name):
    match = re.search(r'create or replace function public\.' + name + r'\([\s\S]*?as \$\$([\s\S]*?)\$\$;', source)
    assert match is not None
    return match.group(1)


def test_preimages_match_actual_previous_bodies_and_rollback_is_default():
    source, previous = PROPOSAL.read_text(), PREVIOUS.read_text()
    for name, expected in HASHES.items():
        assert hashlib.sha256(routine(previous, name).encode()).hexdigest() == expected
        assert "is distinct from '" + expected + "'" in source
    assert source.endswith('rollback; -- LOCAL_ROLLBACK_PROOF; never change to COMMIT here.\n')
    assert "set local lock_timeout='5s'" in source
    assert "set local statement_timeout='60s'" in source
    assert 'insert into' not in re.sub(r'--[^\n]*', '', source).lower()


def test_anchor_has_one_identity_no_fixed_calendar_and_fresh_current_kst_day():
    source = PROPOSAL.read_text()
    header = source.split('\ndo $$begin', 1)[0]
    assert "trial_id='" + TRIAL + "'::uuid" in header
    assert "not_before>='2026-10-16T19:45:03Z'" in header
    assert "kst_date date not null," in header
    assert "new.kst_date is distinct from timezone('Asia/Seoul',observed_at)::date" in header
    assert "new.not_before<observed_at-interval '60 seconds'" in header
    assert "deadline=not_before+interval '2 hours'" in header
    assert "timezone('Asia/Seoul',deadline)::date=kst_date" in header
    assert 'before update or delete or truncate' in header
    assert 'from public,anon,authenticated,service_role' in header
    assert 'enable row level security' in header
    assert 'create policy' not in source.lower()


def test_claim_only_adds_the_new_exact_worker_branch():
    previous = routine(PREVIOUS.read_text(), 'claim_exact_natural_review_draft_job')
    current = routine(PROPOSAL.read_text(), 'claim_exact_natural_review_draft_job')
    prefix = current.index("    if target_worker_id='ybo-trial:" + TRIAL + "'")
    end = current.index("    elsif target_worker_id='ybo-trial:c34dbb8a-ff7c-484e-8e43-757f692ec743'", prefix)
    branch = current[prefix:end]
    assert "and target_client_id='babylon'" in branch
    assert 'anchor.workspace_id=target_workspace_id' in branch
    normalized = current[:prefix] + current[end:].replace('    elsif ', '    if ', 1)
    assert normalized == previous


def test_context_only_adds_new_worker_anchor_and_logo_gate():
    previous = routine(PREVIOUS.read_text(), 'get_quality_banner_generation_context')
    current = routine(PROPOSAL.read_text(), 'get_quality_banner_generation_context')
    start = current.index('      -- Only the new worker is additionally fenced')
    end = current.index('      and job.attempts between', start)
    all_media_gate = current[start:end]
    assert "job.locked_by<>'ybo-trial:" + TRIAL + "'" in all_media_gate
    assert 'job.lease_expires_at<=anchor.deadline' in all_media_gate
    current = current[:start] + current[end:]
    start = current.index("            or (target_client_id='babylon'\n              and job.locked_by='ybo-trial:" + TRIAL + "'")
    end = current.index("            or (target_client_id='babylon'\n              and job.locked_by='ybo-trial:c34dbb8a", start)
    current = current[:start] + current[end:]
    assert current == previous
