"""Static guards only: not proof of executed PostgreSQL registration behavior."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT/'supabase/proposals/content_ops_button_prompt_registration.sql').read_text()


def test_private_inert_no_public_mutation_grants_or_sends():
    assert 'security invoker' in SQL and "set search_path=''" in SQL
    assert 'enable row level security' in SQL
    assert not re.search(r'\bgrant\s+\w+|security definer|create\s+(trigger|policy)',SQL,re.I)
    assert not re.search(r'\b(insert into|update|delete from)\s+public\.',SQL,re.I)
    assert "'execution_authorized',false" in SQL


def test_registration_reads_owner_receipt_not_user_success_claim():
    signature = SQL.split('create function private.register_content_ops_button_edit_prompt(')[1].split(') returns')[0]
    assert 'target_receipt_id uuid, verified_human_binding text' in signature
    assert 'outcome' not in signature and 'receipt_sha256' not in signature
    for marker in ("receipt.outcome is distinct from 'sent'", 'receipt.delivered_at is null',
                   'receipt.delivered_at<action.created_at', 'receipt.recorded_at>observed',
                   'expiry<=observed', "action.action not in ('edit_telegram','edit_x','edit_banner')"):
        assert marker in SQL


def test_banner_registration_never_uses_unreserved_fixture_compatibility():
    assert "action.action='edit_banner' and not exists" in SQL
    for marker in ('private.content_ops_button_prompt_attempts a', 'and c.active',
                   'a.human_binding=verified_human_binding', 'a.expires_at=receipt.reservation_expires_at'):
        assert marker in SQL


def test_exact_identity_current_epoch_and_registration_dedupe():
    for marker in ('actor is distinct from receipt.actor_id', 'i.active for share',
                   'receipt.epoch is distinct from review.epoch',
                   'private.content_ops_button_check_state(review.id,actor)',
                   'prompt.owner_receipt_id is distinct from receipt.id',
                   'prompt.message_binding is distinct from receipt.message_binding',
                   'prompt.receipt_sha256 is distinct from receipt.receipt_sha256',
                   'prompt.consumed_key is not null', "'reused',true"):
        assert marker in SQL
    assert SQL.index('perform 1 from public.content_items') < SQL.index('where id=receipt.review_id for update')


def test_harness_has_registration_race_and_negative_cases():
    driver = (ROOT/'scripts/verify_button_edit_driver_local.py').read_text()
    assert 'regbarrier = Barrier(8)' in driver
    assert "'registrationRefusals':11" in driver
    for case in ('conflicting_receipt','before_action','wrong_human','stale_epoch','stale_source'):
        assert case in driver
    assert 'assert exc.sqlstate == expected' in driver
    assert 'select private.register_content_ops_button_edit_prompt(null,null)' in driver
