"""Static contracts; behavioral proof is the disposable PostgreSQL fixture."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "supabase/migrations/20261005030000_official_x_same_day_refresh.sql"
SQL = PATH.read_text(encoding="utf-8")
FIXTURE = ROOT / "supabase/tests/official_x_same_day_refresh_security.sql"


def function(name: str, *, schema: str = "public") -> str:
    match = re.search(rf"create (?:or replace )?function {schema}\.{name}\(.*?\n(?:end )?\$\$;", SQL, re.S)
    assert match, name
    return match.group()


def test_additive_isolated_ledger_is_private_rls_and_bounded():
    assert SQL.rstrip().endswith("commit;") and "begin;" in SQL
    assert not re.search(r"\b(drop|truncate|delete\s+from)\b", SQL, re.I)
    for token in ["unique (workspace_id,client_id,kst_date)", "request_id uuid not null unique",
                  "job_id uuid not null unique", "unique (workspace_id,client_id,source_item_id)",
                  "force row level security", "job_input_sha256", "source_snapshot_sha256",
                  "expires_at <= created_at + interval '2 hours'", "same_day_refresh_receipt_immutable"]:
        assert token in SQL
    assert "revoke all on table private.official_x_same_day_refresh_requests" in SQL


def test_queue_derives_source_and_does_not_edit_natural_catalog_or_slot():
    body = function("queue_official_x_same_day_refresh")
    for token in ["target_predecessor_job_id", "target_predecessor_content_item_id",
                  "target_predecessor_content_version_id", "target_source_item_id",
                  "pg_advisory_xact_lock", "for update", "same_day_refresh_source_or_predecessor_ineligible",
                  "'manual_only',true", "'same_day_refresh_id',target_refresh_id",
                  "0,1,statement_timestamp()", "queued_job_id is null", "btrim(source.body)"]:
        assert token in body
    for table in ["public.content_items", "public.content_versions", "private.official_x_daily_slots"]:
        assert f"update {table}" not in body and f"insert into {table}" not in body
    assert "queue_review_draft_job(" not in body
    assert "get_or_create_official_x_style_reference_pack(" not in body


def test_exact_claim_once_no_fifo_reclaim_or_retry():
    body = function("claim_official_x_same_day_refresh")
    assert "refresh_id=target_refresh_id for update" in body
    assert "receipt.claimed_at is not null" in body
    assert "job.status is distinct from 'queued'" in body
    assert "job.attempts is distinct from 0" in body and "job.max_attempts is distinct from 1" in body
    assert "status='running',attempts=1" in body
    assert "output=jsonb_build_object('execution_plane','studio_sync')" in body
    assert "'origintrail_batch_eligible',false,'batch_handoff_recovery_only',false" in body
    assert "receipt.predecessor_content_item_id for share" in body
    assert "order by" not in body.lower() and "skip locked" not in body.lower()
    assert "bind_review_draft_execution_plane" not in body
    assert "'retrying'" not in body and "attempts = attempts + 1" not in body


def test_strict_latest_fresh_official_source_and_poll_checks():
    body = function("official_x_same_day_refresh_source_valid", schema="private")
    for token in ["between 10 and 20000", "interval '15 minutes'", "interval '24 hours'",
                  "source.published_at<=target_now", "feed.last_polled_at<=target_now",
                  "feed.poll_interval_minutes=15", "source.source_type='tweet'",
                  "latest.published_at desc nulls last,latest.id desc", "split_part(source.canonical_url,'/',6)"]:
        assert token in body
    for handle in ["@Yellow", "@SquidRouter", "@babylonlabs_io", "@origin_trail"]:
        assert handle in body


def test_input_and_source_are_full_digest_and_source_derived_bindings():
    body = function("official_x_same_day_refresh_input_valid", schema="private")
    assert "digest(j.input::text,'sha256')" in body
    assert "j.input=jsonb_build_object" in body
    assert "source_snapshot_sha256=private.official_x_same_day_refresh_source_sha256(s.id)" in body
    snapshot = function("official_x_same_day_refresh_source_sha256", schema="private")
    for field in ["id", "source_feed_id", "external_id", "author_handle", "published_at",
                  "canonical_url", "body", "media", "source_hash"]:
        assert f"'{field}'" in snapshot
    for routine in ["claim_official_x_same_day_refresh", "inspect_official_x_same_day_refresh"]:
        assert "private.official_x_same_day_refresh_input_valid(receipt.refresh_id)" in function(routine)


def test_inspection_is_bounded_advisory_and_never_delivery():
    body = function("inspect_official_x_same_day_refresh")
    assert "'execution_authorized',false,'delivery_authorized',false" in body
    assert "'refresh_ready'" in body and "'refresh_not_ready'" in body
    assert "'banner_sha256'" in body and "'source_age_seconds'" in body
    for forbidden in ["'source_content',", "'source_url',", "'telegram_copy',", "'x_copy',", "'storage_path',",
                      "content_ops_reconcile_daily(", "content_ops_claim_review("]:
        assert forbidden not in body
    for token in ["reference_pack_hash=version.generation_meta->>'style_reference_pack_hash'",
                  "submitted_content", "byte_size between 9 and 10000000", "news-card.png",
                  "private.grok_qa_dispatch_outbox", "char_length(version.channel_copy->>'telegram')"]:
        assert token in body
    assert "job.output->>'execution_plane'" not in body


def test_new_rpcs_service_role_only_without_producer_privilege_expansion():
    signatures = {
        "queue_official_x_same_day_refresh": "uuid,text,date,uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz",
        "claim_official_x_same_day_refresh": "uuid,uuid,uuid,uuid,uuid,text,text,integer",
        "inspect_official_x_same_day_refresh": "uuid,uuid,uuid",
    }
    for name, signature in signatures.items():
        body = function(name)
        assert "security definer set search_path = ''" in body
        assert "auth.role()) is distinct from 'service_role'" in body
        assert f"revoke all on function public.{name}({signature})" in SQL
        assert re.search(rf"grant execute on function public\.{name}\({re.escape(signature)}\)\s+to service_role;", SQL)
    assert "coineasy_batch_producer" not in SQL
    for old in ["queue_review_draft_job", "claim_review_draft_job", "complete_review_draft_job",
                "fail_review_draft_job", "content_ops_review_candidate", "content_ops_reconcile_daily"]:
        assert f"create or replace function public.{old}(" not in SQL
        assert f"create or replace function private.{old}(" not in SQL


def test_grok_scope_guard_preserves_original_body_byte_for_byte():
    base = (ROOT / "supabase/migrations/20260813143000_grok_qa_dispatch_outbox.sql").read_text()
    pattern = r"create or replace function private\.enqueue_official_x_grok_qa_dispatch\(\).*?\n\$\$;"
    original = re.search(pattern, base, re.S).group()
    guarded = re.search(pattern, SQL, re.S).group()
    stripped = re.sub(r"    -- BEGIN exact isolated refresh QA exclusion\n.*?    -- END exact isolated refresh QA exclusion\n\n", "", guarded, flags=re.S)
    assert stripped == original
    assert guarded.index("Grok QA review completion event is not authoritative") < guarded.index("-- BEGIN exact")
    for token in ["refresh.claimed_at < refresh.expires_at", "refresh.job_id = review_job.id",
                  "refresh.request_id = item.id", "refresh.source_item_id = primary_source.id",
                  "'same_day_refresh_id'", "private.official_x_same_day_refresh_input_valid(refresh.refresh_id)",
                  "review_job.max_attempts = 1", "'completed_by' = refresh.claimed_by"]:
        assert token in guarded


def test_transactional_fixture_covers_live_behavior_and_rolls_back():
    fixture = FIXTURE.read_text()
    assert "private.test_same_day_refresh_seed()" in fixture
    assert fixture.index("private.test_same_day_refresh_seed()") < fixture.index("begin;")
    assert fixture.rstrip().endswith("rollback;")
    for token in ["natural_rows_unchanged", "consumed_claim_never_replays", "refresh_no_grok_outbox",
                  "natural_grok_outbox_preserved", "fake_marker_not_suppressed", "input_drift_rejected",
                  "source_drift_rejected", "refresh_readiness_requires_canonical_banner", "refresh_not_ready"]:
        assert token in fixture
