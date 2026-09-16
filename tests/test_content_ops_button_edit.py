"""Edit proposal guards; real local PostgreSQL tests exercise transactions."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / 'supabase/proposals/content_ops_button_edit_reply.sql').read_text()
TESTS = (ROOT / 'supabase/tests/content_ops_button_edit_reply.sql').read_text()
HARNESS = (ROOT / 'scripts/verify_button_review_state_local.mjs').read_text()


def test_no_runtime_grant_or_publisher_capability():
    assert 'security invoker' in SQL and 'security definer' not in SQL
    assert not re.search(r'\bgrant\s+\w+', SQL, re.I)
    assert SQL.count('enable row level security') == 2
    assert not re.search(r'\b(insert into|update|delete from) public\.(approvals|publications|assets|jobs)\b', SQL, re.I)
    assert not re.search(r'\bupdate public\.content_versions\b', SQL, re.I)


def test_exact_bound_reply_and_active_identity_are_rechecked():
    for guard in ('p.bot_binding is distinct from verified_bot_binding',
                  'p.room_binding is distinct from verified_room_binding',
                  'p.message_binding is distinct from verified_message_binding',
                  'identity_actor is distinct from p.actor_id',
                  'act.actor_id is distinct from identity_actor',
                  'r.epoch is distinct from p.epoch', 'and x.active for share',
                  'and a.actor_id=identity_actor and a.active for share'):
        assert guard in SQL


def test_old_version_assets_and_qa_cannot_be_silently_reused():
    assert 'insert into public.content_versions' in SQL
    assert "'button-edit@1'" in SQL
    assert "old_version.content-'render'-'spec'" in SQL
    assert "'fact_check',jsonb_build_object('status','needs_review')" in SQL
    assert "'brand_qa',jsonb_build_object('status','needs_review')" in SQL
    assert "'canonical_banner_required',true" in SQL
    assert "current_version_id=new_id,status='draft',scheduled_for=null" in SQL
    assert "'execution_authorized',false" in SQL


def test_retry_must_match_consumed_text_operation_and_current_revision():
    for guard in ('p.consumed_key is distinct from operation_key',
                  'p.reply_sha256 is distinct from reply_hash',
                  'item.current_version_id is distinct from p.new_version_id'):
        assert guard in SQL
    assert SQL.index('if p.consumed_key is not null') < SQL.index('insert into public.content_versions')


def test_utf16_and_private_content_boundaries():
    assert 'ascii(c)>65535 then 2 else 1' in SQL
    assert "when 'edit_telegram' then 3700 else 1000" in SQL
    assert 'button_edit_no_change' in SQL
    assert 'oversize_utf16' in TESTS and 'private_invite' in TESTS


def test_harness_checks_edit_race_and_restart():
    assert 'new Set(edits.map(r => r.content_version_id)).size !== 1' in HARNESS
    assert 'edit restart lost receipt' in HARNESS
    assert 'wrong_message' in TESTS and 'reviewer_revoked' in TESTS
    assert 'old immutable version untouched' in TESTS
