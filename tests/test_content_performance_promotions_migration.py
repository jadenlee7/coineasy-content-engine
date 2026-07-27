"""Static safety contract for exact-link content promotion evidence."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260727143000_content_performance_promotions.sql"
).read_text(encoding="utf-8")
SQL_SMOKE = (
    ROOT
    / "supabase"
    / "tests"
    / "content_performance_promotions_security.sql"
)


def _function(name: str) -> str:
    match = re.search(
        rf"create or replace function public\.{name}\(.*?\n\$\$;",
        MIGRATION,
        re.DOTALL,
    )
    assert match is not None, f"missing {name}"
    return match.group(0)


def test_schema_11_extends_ranking_evidence_without_rewriting_rows():
    assert "schema_version in ('1.0', '1.1')" in MIGRATION
    ranking = _function("record_content_signal_ranking_evidence")
    assert "target_schema_version not in ('1.0', '1.1')" in ranking
    assert "update private.content_signal_ranking_evidence" not in MIGRATION
    assert "delete from private.content_signal_ranking_evidence" not in MIGRATION


def test_private_performance_rows_are_immutable_and_service_cannot_read_tables():
    for table in (
        "content_performance_evidence",
        "content_promotion_candidate_receipts",
        "content_promotion_recommendations",
    ):
        assert f"create table private.{table}" in MIGRATION
        assert f"before update or delete on private.{table}" in MIGRATION
    assert re.search(
        r"revoke all on table\s+"
        r"private\.content_performance_evidence,\s+"
        r"private\.content_promotion_candidate_receipts,\s+"
        r"private\.content_promotion_recommendations\s+"
        r"from public, anon, authenticated, service_role;",
        MIGRATION,
        re.DOTALL,
    )
    assert "unique (evidence_id, target_kind)" in MIGRATION


def test_manual_observation_only_records_an_existing_public_link():
    function = _function("record_manual_publication_observation")
    assert "item.content_kind <> 'daily_news'" in function
    assert (
        "item.current_version_id is distinct from "
        "target_content_version_id"
    ) in function
    assert "version.generation_meta -> 'mock_mode'" in function
    assert "is distinct from 'false'::jsonb" in function
    assert "status,\n                published_at" in function
    assert "'published'" in function
    assert "'external_publish_performed', false" in function
    assert "'manual_publication_observed'" in function
    assert "if not exists (" in function
    assert "event.event_type = 'manual_publication_observed'" in function
    assert "insert into public.jobs" not in function
    assert "insert into public.figma_links" not in function
    assert "update public.content_items" not in function
    assert "publication.status in ('queued', 'publishing')" in function
    assert "job.status in ('queued', 'running', 'retrying')" in function
    assert "job.job_kind = 'publish'" in function
    assert (
        "manual observation conflicts with an active publication request"
        in function
    )
    assert "using errcode = '23505'" in function
    assert "https://x.com/" in function
    assert "https://t.me/" in function
    for handle in (
        "yellow__korea",
        "squidkorea",
        "origin_trail_kr",
        "babylonkorean",
        "yellowkorea_ann",
        "squid_kor_update",
        "origintrailkr",
        "babylonbtc",
    ):
        assert f"'{handle}'" in function


def test_public_telegram_channels_mirror_the_performance_allowlist():
    expected = {
        "yellow": "yellowkorea_ann",
        "origintrail": "origintrailkr",
        "squid": "squid_kor_update",
        "babylon": "babylonbtc",
    }
    for client, expected_handle in expected.items():
        config = (
            ROOT / "clients" / client / "config.yaml"
        ).read_text(encoding="utf-8")
        match = re.search(
            r'public_channel:\s*["\']?(?:https://)?(?:t\.me/|@)'
            r'([A-Za-z][A-Za-z0-9_]{4,31})',
            config,
        )
        assert match is not None, f"missing public Telegram channel for {client}"
        assert match.group(1).lower() == expected_handle


def test_publication_request_cannot_queue_after_manual_observation():
    function = _function("request_content_publication")
    assert "where id = target_content_item_id\n    for update;" in function
    assert "publication.content_version_id = target_content_version_id" in function
    assert "publication.channel = target_channel" in function
    assert "publication.status = 'published'" in function
    assert "event.entity_id = publication.id" in function
    assert "event.event_type = 'manual_publication_observed'" in function
    assert "publication was already observed on the external channel" in function
    assert "using errcode = '23505'" in function
    assert function.index("if found then\n        return existing_publication_id;") < (
        function.index("publication was already observed on the external channel")
    )
    assert "insert into public.publications" in function
    assert "insert into public.jobs" in function
    assert re.search(
        r"revoke all on function public\.request_content_publication\(\s*"
        r"uuid,\s*uuid,\s*text,\s*timestamptz,\s*text\s*\)\s*"
        r"from public, anon, authenticated;",
        MIGRATION,
        re.DOTALL,
    )
    assert re.search(
        r"grant execute on function public\.request_content_publication\(\s*"
        r"uuid,\s*uuid,\s*text,\s*timestamptz,\s*text\s*\)\s*"
        r"to authenticated;",
        MIGRATION,
        re.DOTALL,
    )


def test_candidate_recording_is_exact_match_recommendation_only_and_source_gated():
    function = _function("record_content_promotion_candidates")
    assert "jsonb_array_length(target_candidates) > 5" in function
    assert "community_match_count')::numeric\n                not between 0 and 3" in function
    assert "0.55 * reach_score" in function
    assert "0.35 * interaction_score" in function
    assert "abs(candidate_score - expected_score) > 0.001" in function
    assert "reach_score >= 0.7" in function
    assert "interaction_score >= 0.7" in function
    assert "candidate_score >= 0.75" in function
    assert "candidate_score >= 0.8" in function
    assert "target_client_id in ('yellow', 'squid')" in function
    assert "source_character_count >= 300" in function
    assert "official_howto_signal" in function
    assert "publication.external_url = candidate_url" in function
    assert "publication.channel = candidate_channel" in function
    assert "publication.status = 'published'" in function
    assert "split_part(candidate_url, '/', 4)" in function
    assert "publication_item.content_kind = 'daily_news'" in function
    assert "publication_item.current_version_id" in function
    assert "feed.handle = expected_official_source_handle" in function
    for handle in (
        "@Yellow",
        "@origin_trail",
        "@SquidRouter",
        "@babylonlabs_io",
    ):
        assert f"'{handle}'" in function
    assert "insert into private.content_performance_evidence" in function
    assert "insert into private.content_promotion_candidate_receipts" in function
    assert "insert into private.content_promotion_recommendations" in function
    assert "promotion candidate retry does not match" in function
    for forbidden in (
        "insert into public.publications",
        "insert into public.jobs",
        "insert into public.figma_links",
        "update public.content_items",
    ):
        assert forbidden not in function


def test_list_rpc_returns_only_bounded_recommendation_metadata():
    function = _function("list_content_promotion_recommendations")
    for key in (
        "recommendation_id",
        "content_version_id",
        "target_kind",
        "score",
        "reason_codes",
        "policy_version",
        "source_url",
        "channel",
        "observed_at",
        "source_ready",
    ):
        assert f"'{key}'" in function
    assert "'items'" in function
    assert "'publications'" in function
    assert "item.current_version_id = target_content_version_id" in function
    assert "statement_timestamp() - interval '24 hours'" in function
    assert "statement_timestamp() + interval '5 minutes'" in function
    assert "row_number() over (" in function
    assert "recommendation.recency = 1" in function
    assert "content_versions.content" not in function
    assert "source_items.body" not in function


def test_all_public_rpcs_are_service_role_only_and_sql_smoke_exists():
    signatures = {
        "record_manual_publication_observation": (
            "uuid, uuid, uuid, text, text"
        ),
        "record_content_promotion_candidates": (
            "uuid, text, text, text, timestamptz, timestamptz, "
            "timestamptz, text, jsonb"
        ),
        "list_content_promotion_recommendations": "uuid, uuid, uuid",
    }
    for name, signature in signatures.items():
        escaped = re.escape(signature).replace(r"\ ", r"\s*")
        assert re.search(
            rf"revoke all on function public\.{name}\(\s*"
            + escaped
            + r"\s*\) from public, anon, authenticated, service_role;",
            MIGRATION,
            re.DOTALL,
        )
        assert re.search(
            rf"grant execute on function public\.{name}\(\s*"
            + escaped
            + r"\s*\) to service_role;",
            MIGRATION,
            re.DOTALL,
        )
    assert SQL_SMOKE.exists()
    smoke = SQL_SMOKE.read_text(encoding="utf-8")
    assert "source_ready" in smoke
    assert "manual observation bypassed an active publish" in smoke
    assert "publication queued after a manual observation" in smoke
    assert "rollback;" in smoke
