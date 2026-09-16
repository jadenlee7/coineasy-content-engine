"""Proposal shape guards; the local PostgreSQL harness tests actual behavior."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / 'supabase/proposals/content_ops_button_review_state.sql').read_text()
HARNESS = (ROOT / 'scripts/verify_button_review_state_local.mjs').read_text()


def test_no_approval_publication_or_grant_surface():
    assert not re.search(r'\b(insert\s+into|update|delete\s+from)\s+public\.', SQL, re.I)
    assert not re.search(r'\bgrant\s+\w+|create\s+(?:or\s+replace\s+)?(?:trigger|policy)', SQL, re.I)
    assert 'security definer' not in SQL.lower()
    assert "'execution_authorized',false" in SQL
    assert 'approve_and_publish' not in SQL
    assert 'from public,anon,authenticated,service_role' in SQL
    assert SQL.count('enable row level security') == 4


def test_actor_and_version_scoped_durable_uniqueness():
    assert 'primary key (review_id, epoch, actor_id, check_kind)' in SQL
    assert 'primary key (review_id, idempotency_key)' in SQL
    assert 'previous.actor_id is distinct from target_actor_id' in SQL
    assert 'previous.action is distinct from requested_action' in SQL
    assert 'previous.version_fingerprint is distinct from expected_fingerprint' in SQL
    assert "previous.epoch=r.epoch then previous.result_status else 'superseded'" in SQL


def test_lock_order_postlock_expiry_and_reviewer_revocation():
    item = SQL.index('perform 1 from public.content_items')
    review = SQL.index('where id=target_review_id for update')
    actor = SQL.index('and a.client_id=r.client_id and a.actor_id=target_actor_id and a.active for share')
    expiry = SQL.index('if clock_timestamp() >= r.expires_at')
    assert item < review < actor < expiry
    assert "expires_at <= created_at + interval '30 minutes'" in SQL
    assert "set epoch=epoch+1,state=result_status" in SQL
    assert 'button_review_new_card_required' in SQL


def test_fingerprint_comes_from_existing_version_assets_and_sources():
    for marker in ('public.content_versions cv', 'public.assets a', 'public.content_source_links l',
                   'public.source_items s', "'version', to_jsonb(cv)",
                   'item.current_version_id is distinct from r.content_version_id'):
        assert marker in SQL
    assert 'from public.approvals a' in SQL and 'from public.publications p' in SQL
    assert "state:='stale'" in SQL and "state:='expired'" in SQL


def test_harness_is_disposable_socket_only_with_race_and_restart():
    assert 'delete env[k]' in HARNESS and "-h ''" in HARNESS
    assert 'mkdtempSync' in HARNESS and "'--auth=trust'" in HARNESS
    assert 'Promise.all(Array.from({ length: 8 }' in HARNESS
    assert 'restart lost idempotency' in HARNESS
    assert 'localServerStopped' in HARNESS
    assert 'hostedProof: false' in HARNESS


def test_harness_rejects_external_arguments_before_creating_cluster():
    result = subprocess.run(['node', 'scripts/verify_button_review_state_local.mjs',
                             '--local-only', '--database-url=not-allowed'], cwd=ROOT,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert 'local-only repo root required' in result.stderr
