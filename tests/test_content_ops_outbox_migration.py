"""Offline structural contracts; these do not apply or execute the migration."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "supabase/migrations/20260906100000_content_ops_review_outbox.sql").read_text()
SMOKE = (ROOT / "supabase/tests/content_ops_review_outbox.sql").read_text()
RPCS = {
    "content_ops_reconcile_daily": "uuid, uuid",
    "content_ops_claim_review": "uuid, uuid, uuid",
    "content_ops_begin_review_send": "uuid, uuid, uuid, text, uuid",
    "content_ops_finish_review_send": "uuid, uuid, uuid, text, bigint, uuid",
}


def function(name, schema="public"):
    match = re.search(
        rf"create or replace function {schema}\.{name}\(.*?\n\$\$;",
        MIGRATION, re.S,
    )
    assert match, name
    return match.group()


def test_private_outbox_is_fixed_destination_and_twice_unique():
    assert "unique (workspace_id, client_id, kst_date, destination_role)" in MIGRATION
    assert "unique (workspace_id, content_version_id, destination_role)" in MIGRATION
    assert "check (destination_role = 'content_ops_private')" in MIGRATION
    assert "client_id in ('yellow', 'origintrail', 'squid', 'babylon')" in MIGRATION
    assert "claim_token uuid unique" in MIGRATION
    assert "enable row level security" in MIGRATION
    assert "force row level security" in MIGRATION
    assert re.search(r"revoke all on table private.content_ops_review_outbox\s+from public, anon, authenticated, service_role;", MIGRATION)
    table = MIGRATION.split("create index", 1)[0]
    for forbidden in ("telegram_copy", "x_copy", "chat_id", "bot_token", "invite", "payload jsonb", "title text"):
        assert forbidden not in table


def test_only_explicit_rpc_can_enqueue_and_no_other_system_is_mutated():
    assert "create trigger" not in MIGRATION.lower()
    assert "cron.schedule" not in MIGRATION.lower()
    assert "OFF by default" in MIGRATION
    writes = re.findall(r"(?:insert into|update|delete from)\s+((?:public|private)\.[a-z_]+)", MIGRATION)
    assert writes and set(writes) == {"private.content_ops_review_outbox"}
    assert "on conflict do nothing" in function("content_ops_reconcile_daily")
    assert "order by slot.client_id limit 4" in function("content_ops_reconcile_daily")
    for forbidden in ("http_post", "net.http", "typefully", "grant select on", "coineasy_content_qa"):
        assert forbidden not in MIGRATION.lower()


def test_public_rpcs_have_service_only_acl_and_explicit_role_guard():
    for name, signature in RPCS.items():
        body = function(name)
        assert "security definer\nset search_path = ''" in body
        assert "auth.role()) is distinct from 'service_role'" in body
        assert "target_workspace_id is null" in body
        qualified = f"public.{name}({signature})"
        assert re.search(rf"revoke all on function {re.escape(qualified)}\s+from public, anon, authenticated, service_role;", MIGRATION)
        assert f"grant execute on function {qualified} to service_role;" in MIGRATION
    assert "revoke all on function private.content_ops_review_candidate(uuid, uuid, uuid)" in MIGRATION
    assert "grant execute on function private." not in MIGRATION


def test_candidate_requires_exact_current_nonmock_unapproved_draft():
    body = function("content_ops_review_candidate", "private")
    for condition in (
        "item.content_kind is distinct from 'daily_news'",
        "item.status is distinct from 'needs_review'",
        "item.current_version_id is distinct from target_content_version_id",
        "version.generation_meta -> 'mock_mode' is distinct from 'false'::jsonb",
        "client.active is true for share",
        "public.approvals", "public.publications",
        "version.generation_meta ->> 'request_id' is distinct from item.id::text",
    ):
        assert condition in body
    assert "for update;" in body
    assert "version.created_at)::date <> effective_date" in body


def test_candidate_requires_latest_official_source_and_fifteen_minute_poll():
    body = function("content_ops_review_candidate", "private")
    for condition in (
        "link.position = 0) <> 1", "source.source_type is distinct from 'tweet'",
        "source.author_handle is distinct from expected_handle",
        "split_part(source.canonical_url, '/', 6)",
        "interval '24 hours'", "interval '15 minutes'",
        "source.published_at > clock_timestamp()",
        "feed.last_polled_at > clock_timestamp()",
        "feed.active is not true", "feed.poll_interval_minutes is distinct from 15",
        "feed.provider is distinct from 'x'", "feed.handle is distinct from expected_handle",
        "order by candidate.published_at desc nulls last, candidate.id desc limit 1",
    ):
        assert condition in body
    for handle in ("@Yellow", "@origin_trail", "@SquidRouter", "@babylonlabs_io"):
        assert handle in body


def test_generation_is_exact_same_day_natural_job_and_daily_slot():
    body = function("content_ops_review_candidate", "private")
    for condition in (
        "candidate.status = 'succeeded'", "candidate.job_kind = 'generate'",
        "candidate.input ->> 'workflow' = 'official_x_review_draft_v1'",
        "candidate.input ->> 'content_kind' = 'daily_news'",
        "candidate.input -> 'manual_only' = 'false'::jsonb",
        "candidate.input ->> 'kst_date' = effective_date::text",
        "candidate.input -> 'source_item_ids' = jsonb_build_array(source.id::text)",
        "candidate.output -> 'source_item_ids' = jsonb_build_array(source.id::text)",
        "candidate.output ->> 'content_version_id' = version.id::text",
        "candidate.finished_at)::date = effective_date",
        "slot.kst_date = effective_date and slot.job_id = generation.id for share",
    ):
        assert condition in body


def test_canonical_png_is_hash_bound_and_present_in_storage():
    body = function("content_ops_review_candidate", "private")
    for condition in (
        "candidate.asset_kind = 'png') <> 1", "join storage.objects as stored",
        "version.deliverables ->> 'primary_asset_id'", "candidate.mime_type = 'image/png'",
        "candidate.storage_bucket = 'content-studio'", "candidate.metadata ->> 'filename' = 'news-card.png'",
        "candidate.sha256 ~ '^[a-f0-9]{64}$'", "candidate.byte_size > 0",
        "candidate.width > 0 and candidate.height > 0", "for share of candidate, stored",
    ):
        assert condition in body
    assert "left(version.title, 160)" in body
    assert "left(telegram_copy, 2000)" in body
    assert "left(x_copy, 600)" in body
    assert "'source_published_at', source.published_at" in body


def test_claim_and_begin_revalidate_full_immutable_identity():
    for name in ("content_ops_claim_review", "content_ops_begin_review_send"):
        body = function(name)
        assert "private.content_ops_review_candidate(" in body
        assert "private.content_ops_review_matches(card, queued) is not true" in body
        assert "set status = 'obsolete', finished_at = clock_timestamp()" in body
    matches = function("content_ops_review_matches", "private")
    for field in ("client_id", "kst_date", "content_item_id", "content_version_id", "source_item_id", "generate_job_id", "banner_asset_id", "banner_sha256", "source_url", "source_published_at"):
        assert f"queued.{field}" in matches


def test_claims_are_one_shot_with_bounded_lifetime_and_no_reclaim():
    claim = function("content_ops_claim_review")
    assert "existing.claim_token = target_claim_token) then return null" in claim
    assert "pg_advisory_xact_lock" in claim
    assert "pending.status = 'pending'" in claim
    assert "limit 4 for update skip locked" in claim
    assert "interval '120 seconds'" in claim
    assert "return (card - 'banner_asset_id') || jsonb_build_object" in claim
    assert "destination_role" not in claim[claim.index("return (card -"):]
    for name in RPCS:
        body = function(name)
        assert "set status = 'delivery_unknown', finished_at = clock_timestamp()" in body
        assert "lease_expires_at <= clock_timestamp()" in body
    assert "set status = 'pending'" not in MIGRATION
    assert "attempts" not in MIGRATION


def test_begin_is_nonreplayable_and_records_hash_before_send():
    begin = function("content_ops_begin_review_send")
    assert "if queued.status <> 'claimed' then" in begin
    assert "target_packet_sha256 !~ '^[a-f0-9]{64}$'" in begin
    assert "set status = 'sending'" in begin
    assert "packet_sha256 = target_packet_sha256" in begin
    assert begin.index("private.content_ops_review_candidate(") < begin.index("set status = 'sending'")
    assert "'accepted', false, 'status', queued.status" in begin
    assert "'accepted', true, 'status', 'sending'" in begin


def test_finish_is_terminal_idempotent_only_for_exact_outcome_and_message_id():
    finish = function("content_ops_finish_review_send")
    assert "target_message_id bigint default null" in finish
    assert "target_outcome not in ('sent', 'rejected', 'delivery_unknown')" in finish
    assert "target_message_id not between 1 and 9007199254740991" in finish
    assert "queued.status = target_outcome" in finish
    assert "queued.message_id is not distinct from target_message_id" in finish
    assert "if queued.status <> 'sending' then" in finish
    assert "message_id = target_message_id" in finish
    assert finish.index("queued.status in ('sent', 'rejected', 'delivery_unknown', 'obsolete')") < finish.index("set status = target_outcome")


def test_transactional_sql_smoke_is_rollback_only_and_checks_acl():
    assert SMOKE.lstrip().startswith("--")
    assert "begin;" in SMOKE and SMOKE.rstrip().endswith("rollback;")
    assert "relrowsecurity" in SMOKE and "relforcerowsecurity" in SMOKE
    assert "aclexplode" in SMOKE
    assert "has_function_privilege" in SMOKE
    assert "content_ops_service_role_required" in SMOKE
    assert "prosecdef" in SMOKE and "search_path" in SMOKE


def test_final_freshness_check_occurs_after_all_lock_waits():
    body = function("content_ops_review_candidate", "private")
    assert body.index("decision_now := clock_timestamp()") > body.index("for share of candidate, stored")
    for guard in ("source.published_at <= decision_now - interval '24 hours'",
                  "feed.last_polled_at < decision_now - interval '15 minutes'",
                  "source.published_at > decision_now", "feed.last_polled_at > decision_now",
                  "pg_catalog.timezone('Asia/Seoul', decision_now)::date <> effective_date"):
        assert guard in body


def test_optional_exact_version_scopes_every_rpc_and_mutation():
    for name in RPCS:
        body = function(name)
        assert "target_content_version_id uuid default null" in body
        for update in re.findall(r"update private\.content_ops_review_outbox.*?;", body, re.S):
            assert re.search(r"target_content_version_id is null or (?:[a-z_]+\.)?content_version_id = target_content_version_id", update)
    reconcile = function("content_ops_reconcile_daily")
    assert "item.current_version_id = target_content_version_id" in reconcile
    assert "card ->> 'content_version_id' is distinct from target_content_version_id::text" in reconcile
    claim = function("content_ops_claim_review")
    assert "pending.content_version_id = target_content_version_id" in claim
    assert claim.index("existing.claim_token = target_claim_token") < claim.index("update private.content_ops_review_outbox")
    for name in ("content_ops_begin_review_send", "content_ops_finish_review_send"):
        body = function(name)
        assert "candidate.content_version_id = target_content_version_id" in body
        assert body.index("candidate.content_version_id = target_content_version_id") < body.index("if not found then")
        assert "'accepted', false, 'status', 'not_owned'" in body
