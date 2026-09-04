from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase/migrations/20260813213000_grok_qa_oauth_authorization_codes.sql"
).read_text(encoding="utf-8").lower()
SECURITY_TEST = (
    ROOT / "supabase/tests/grok_qa_oauth_security.sql"
).read_text(encoding="utf-8").lower()


def _function(name: str) -> str:
    match = re.search(
        rf"create or replace function public[.]{name}[(].*?\n[$]function[$];",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, name
    return match.group(0)


def test_authorization_codes_are_private_force_rls_and_relation_grant_free():
    assert "create table private.grok_qa_oauth_codes" in MIGRATION
    assert "code_sha256 text primary key" in MIGRATION
    assert "alter table private.grok_qa_oauth_codes force row level security" in MIGRATION
    assert (
        "revoke all on table private.grok_qa_oauth_codes\n"
        "from public, anon, authenticated, service_role"
    ) in MIGRATION
    assert "revoke all on table private.grok_qa_oauth_codes\nfrom coineasy_grok_qa_oauth" in MIGRATION
    assert "grant select" not in MIGRATION
    assert "create policy" not in MIGRATION


def test_code_creation_is_short_lived_bounded_and_collision_closed():
    create = _function("create_grok_qa_oauth_code")
    assert "security definer\nset search_path = ''" in create
    assert "target_expires_at > observed_at + interval '10 minutes'" in create
    assert "limit 50\n        for update skip locked" in create
    assert "insert into private.grok_qa_oauth_codes" in create
    assert "when unique_violation then" in create
    assert "'created', false, 'status', 'conflict'" in create


def test_code_consumption_binds_pkce_client_redirect_resource_and_scope_once():
    consume = _function("consume_grok_qa_oauth_code")
    assert "security definer\nset search_path = ''" in consume
    assert "for update" in consume
    for binding in (
        "authorization_code.client_id_sha256 <> target_client_id_sha256",
        "authorization_code.redirect_uri <> target_redirect_uri",
        "authorization_code.resource <> target_resource",
        "authorization_code.scope <> target_scope",
        "authorization_code.code_challenge <> target_code_challenge",
    ):
        assert binding in consume
    assert "authorization_code.consumed_at is not null" in consume
    assert "set consumed_at = observed_at" in consume
    assert "and consumed_at is null" in consume
    assert "'authorized', true, 'status', 'consumed'" in consume


def test_scoped_role_has_only_the_two_oauth_rpcs_and_no_bypass():
    grants = re.findall(
        r"grant execute on function public[.]([a-z_]+)[(].*?\) to coineasy_grok_qa_oauth;",
        MIGRATION,
        re.DOTALL,
    )
    assert grants == [
        "create_grok_qa_oauth_code",
        "consume_grok_qa_oauth_code",
    ]
    assert "alter role coineasy_grok_qa_oauth nologin noinherit nobypassrls" in MIGRATION
    assert "grant coineasy_grok_qa_oauth to authenticator" in MIGRATION
    assert "grant usage on schema public to coineasy_grok_qa_oauth" in MIGRATION
    assert ") to service_role;" not in MIGRATION


def test_oauth_migration_has_no_content_or_external_effect_path():
    for forbidden in (
        "public.approvals",
        "public.publications",
        "content_publications",
        "queue_agent_batch_job",
        "openai_api_key",
        "telegram_bot_token",
        "messages send",
        "http_post",
        "net.http",
    ):
        assert forbidden not in MIGRATION


def test_transactional_fixture_proves_one_time_code_and_zero_content_effects():
    assert SECURITY_TEST.startswith("-- transactional security")
    assert SECURITY_TEST.rstrip().endswith("rollback;")
    assert "set local role coineasy_grok_qa_oauth" in SECURITY_TEST
    assert SECURITY_TEST.count("\n    public.consume_grok_qa_oauth_code(") == 3
    assert "oauth code was not consumed exactly once" in SECURITY_TEST
    assert "oauth flow mutated approval, publication, or batch state" in SECURITY_TEST
