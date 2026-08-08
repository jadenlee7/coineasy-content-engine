from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260808120000_origintrail_media_fact_evidence.sql"
).read_text(encoding="utf-8")


def _function(schema: str, name: str) -> str:
    match = re.search(
        rf"create or replace function\s+{re.escape(schema)}\.{re.escape(name)}\b"
        rf"(?P<body>.*?)\$\$;",
        MIGRATION,
        re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, f"missing function {schema}.{name}"
    return match.group(0)


def test_registry_is_private_rls_forced_and_append_only() -> None:
    assert (
        "create table private.origintrail_reviewed_source_evidence"
        in MIGRATION
    )
    assert (
        "alter table private.origintrail_reviewed_source_evidence\n"
        "    enable row level security;"
    ) in MIGRATION
    assert (
        "alter table private.origintrail_reviewed_source_evidence\n"
        "    force row level security;"
    ) in MIGRATION
    assert (
        "revoke all on table private.origintrail_reviewed_source_evidence\n"
        "from public, anon, authenticated, service_role;"
    ) in MIGRATION
    assert "create policy" not in MIGRATION.lower()
    assert "before update or delete on private.origintrail_reviewed_source_evidence" in MIGRATION
    assert "before truncate on private.origintrail_reviewed_source_evidence" in MIGRATION
    reject = _function(
        "private", "reject_origintrail_reviewed_source_evidence_mutation"
    )
    assert "append-only" in reject
    assert "errcode = '55000'" in reject
    assert not re.search(
        r"grant\s+(?:select|insert|update|delete|all).*"
        r"origintrail_reviewed_source_evidence",
        MIGRATION,
        re.IGNORECASE | re.DOTALL,
    )


def test_seed_binds_the_exact_source_and_nonfactual_media() -> None:
    for exact in (
        "2085782218815775024",
        "https://x.com/origin_trail/status/2085782218815775024",
        "aa1676bb2f98b8f35ee7de430c161c9a4ba39a8d4a9c728b8abd93dba3655d74",
        "13_2085781578374860800",
        "https://pbs.twimg.com/amplify_video_thumb/2085781578374860800/img/vH2LVZnApTMbJhq2.jpg",
        "https://pbs.twimg.com/amplify_video_thumb/2085781578374860800/img/vH2LVZnApTMbJhq2.jpg?name=orig",
        "2aa9f90988186014fb262877beb9c7566b81a7a006829b959e6fe0ae105b3d90",
    ):
        assert exact in MIGRATION
    assert "'type', 'video'" in MIGRATION
    assert "'width', 1920" in MIGRATION
    assert "'height', 1920" in MIGRATION
    assert "'factual_evidence', false" in MIGRATION
    assert "preview_media_url = raw_media_url || '?name=orig'" in MIGRATION


def test_seed_uses_the_closed_fact_check_contract_and_qualified_claims() -> None:
    assert "'schema_version', '1.0'" in MIGRATION
    assert (
        "'policy_version', 'origintrail-media-fact-evidence@1'"
        in MIGRATION
    )
    assert "'review_status', 'qualified'" in MIGRATION
    assert "'human_review_required', true" in MIGRATION
    for media_key in (
        "'type'",
        "'media_key'",
        "'recorded_url'",
        "'preview_url'",
        "'preview_url_sha256'",
        "'width'",
        "'height'",
        "'factual_evidence'",
    ):
        assert media_key in MIGRATION
    assert (
        "jsonb_array_length(\n"
        "            registry.evidence_payload -> 'review_notes_ko'\n"
        "          ) between 1 and 8"
    ) in MIGRATION
    assert "독립 검증 또는 인간 능가의 증거로 표현하지 않습니다" in MIGRATION
    assert "Stage 1 전송·연결 계층" in MIGRATION
    assert "공유 메모리 훅과 Python DKG 스킬은 후속 단계" in MIGRATION
    assert "95.23982017078089" in MIGRATION
    assert "self-reported/default" in MIGRATION
    assert "관찰 시점에 404" in MIGRATION


def test_seed_pins_the_exact_official_reference_set() -> None:
    for kind in (
        "origintrail_implementation",
        "prime_intellect_announcement",
        "prime_agent_release",
        "arc_community_leaderboard",
        "arc_methodology",
        "scorecard_source",
    ):
        assert f"'kind', '{kind}'" in MIGRATION
    for reference_key in (
        "kind",
        "label_ko",
        "url",
        "observed_at",
        "snapshot_sha256",
        "availability",
        "finding_ko",
    ):
        assert f"'{reference_key}'" in MIGRATION
    for exact in (
        "https://github.com/OriginTrail/dkg/blob/075e87d881260a1aad2d86b53fa250d5d3f67d40/packages/adapter-prime-agent/README.md",
        "d7a3ec333d26feae1a90f51d6770858541b6c9134799d79397d1601ede42a51b",
        "https://www.primeintellect.ai/blog/prime-agent",
        "https://github.com/PrimeIntellect-ai/prime-agent/commit/be9e2fa0714e7cd1c6bd9bdb1b554d2cc6550387",
        "be9e2fa0714e7cd1c6bd9bdb1b554d2cc6550387",
        "https://arcprize.org/api/leaderboards",
        "2f37594d945680d310a35b3959c84f12c17c14c629ee7c68ae70ede8c5306623",
        "https://arcprize.org/media/ARC_AGI_3_Technical_Report.pdf",
        "https://github.com/PrimeIntellect-ai/arc-agi-3-prime-agent-scorecard/commit/aaee22436235de6f784df7b89302e1258aae9ab9",
    ):
        assert exact in MIGRATION
    assert (
        "jsonb_array_length(\n"
        "            registry.evidence_payload -> 'official_references'\n"
        "          ) = 6"
    ) in MIGRATION
    assert "count(distinct reference.value ->> 'kind')" in MIGRATION
    assert "not in ('available', 'unavailable')" in MIGRATION


def test_evidence_hash_uses_python_compatible_canonical_json() -> None:
    canonical = _function("agent_runtime", "canonical_json_text")
    assert "immutable" in canonical.lower()
    assert "jsonb_each(target)" in canonical
    assert 'order by entry.key collate "C"' in canonical
    assert "jsonb_array_elements(target)" in canonical
    assert "order by entry.ordinal" in canonical
    assert "','" in canonical
    assert "evidence_canonical_json =\n            agent_runtime.canonical_json_text(evidence_payload)" in MIGRATION
    assert "pg_catalog.convert_to(evidence_canonical_json, 'UTF8')" in MIGRATION
    assert "evidence_sha256 = encode(" in MIGRATION
    assert "select payload, payload::text as canonical_json" not in MIGRATION
    assert (
        "agent_runtime.canonical_json_text(payload) as canonical_json"
        in MIGRATION
    )


def test_legacy_predicate_accepts_text_only_or_exact_reviewed_media() -> None:
    wrapper = _function("agent_runtime", "origintrail_review_is_text_only")
    exact = _function(
        "agent_runtime", "origintrail_reviewed_media_evidence"
    )
    assert "origintrail_reviewed_media_evidence(target_job_id)" in wrapper
    assert "is not null then\n        return true" in wrapper
    assert "jsonb_array_length(source.media) = 0" in wrapper
    assert "private.origintrail_standalone_sources" in wrapper
    assert "source_count <> distinct_source_count" in wrapper
    assert "jsonb_array_length(review_job.input -> 'source_item_ids') <> 1" in exact
    assert "private.origintrail_reviewed_source_evidence" in exact
    assert "private.origintrail_standalone_sources" in exact
    assert "registry.source_external_id = source.external_id" in exact
    assert "registry.source_url = source.canonical_url" in exact
    assert "review_job.input ->> 'source_content' = source.body" in exact
    assert "pg_catalog.convert_to(source.body, 'UTF8')" in exact
    assert "jsonb_array_length(source.media) = 1" in exact
    assert "media.item ->> 'media_key' = registry.media_key" in exact
    assert "media.item ->> 'type' = registry.media_type" in exact
    assert "media.item ->> 'url' = registry.raw_media_url" in exact
    assert (
        "review_job.input ->> 'source_image_url' = registry.raw_media_url"
        in exact
    )
    assert "registry.preview_media_url" in exact
    assert "(media.item ->> 'width')::integer = registry.media_width" in exact
    assert "(media.item ->> 'height')::integer = registry.media_height" in exact


def test_evidence_rpc_is_lease_fenced_and_not_publicly_executable() -> None:
    rpc = _function("public", "get_origintrail_reviewed_source_evidence")
    lowered = rpc.lower()
    assert "\nstable\nsecurity definer" in lowered
    assert "set search_path = ''" in lowered
    assert "review_job.status <> 'running'" in rpc
    assert "review_job.locked_by is distinct from target_worker_id" in rpc
    assert "review_job.lease_expires_at <= statement_timestamp()" in rpc
    assert "return null" in rpc
    assert "origintrail_reviewed_media_evidence(target_job_id)" in rpc
    assert re.search(
        r"revoke all on function public\.get_origintrail_reviewed_source_evidence\(\s*"
        r"uuid,\s*uuid,\s*text\s*\) from public, anon, authenticated, service_role;",
        MIGRATION,
        re.DOTALL,
    )
    assert re.search(
        r"grant execute on function public\.get_origintrail_reviewed_source_evidence\(\s*"
        r"uuid,\s*uuid,\s*text\s*\) to service_role;",
        MIGRATION,
        re.DOTALL,
    )
    assert "to anon" not in MIGRATION.lower()
    assert "to authenticated" not in MIGRATION.lower()


def test_insert_trigger_requires_the_exact_sidecar_and_provider_snapshot() -> None:
    trigger = _function(
        "agent_runtime", "enforce_origintrail_media_fact_evidence"
    )
    assert "before insert on agent_runtime.batch_jobs" in MIGRATION
    assert "before update on agent_runtime.batch_jobs" not in MIGRATION
    assert "new.client_id <> 'origintrail'" in trigger
    assert "new.workflow_kind <> 'official_source_nonurgent_pack'" in trigger
    assert "review_job.input ->> 'source_image_url'" in trigger
    assert "origintrail_reviewed_media_evidence(new.job_id)" in trigger
    assert "new.input_payload -> 'fact_check_evidence'" not in trigger
    assert (
        "jsonb_object_keys(\n"
        "            provider_input -> 'fact_check_evidence'"
    ) in trigger
    assert ") <> 2" in trigger
    assert "'payload', 'evidence_sha256'" in trigger
    assert "(new.input_payload ->> 'input')::jsonb" in trigger
    assert "provider_input -> 'fact_check_evidence'" in trigger
    assert "is distinct from expected_evidence" in trigger
    assert "provider_input -> 'source' ->> 'content'" in trigger
    assert "provider_input -> 'source' ->> 'url'" in trigger
    assert "provider_input -> 'source' ->> 'image_url'" in trigger
    assert "provider_input -> 'source' ->> 'content_sha256'" in trigger
    assert "provider_input ? 'fact_check_evidence'" in trigger
    assert "jsonb_object_keys(provider_input)) <> 6" in trigger
    assert "'style_reference_pack'" in trigger
    assert "OriginTrail provider input identity is invalid" in trigger
    assert "OriginTrail text-only input cannot carry fact evidence" in trigger
    assert (
        trigger.index(
            "coalesce(btrim(review_job.input ->> 'source_image_url'), '') = ''"
        ) < trigger.index("OriginTrail provider input identity is invalid")
    )
    assert "return new" in trigger


def test_review_detail_preserves_existing_guards_and_adds_optional_evidence() -> None:
    detail = _function("public", "get_agent_batch_review_item")
    for existing_guard in (
        "batch_job.client_id = 'origintrail'",
        "batch_job.agent_id = 'origintrail_client_agent'",
        "batch_job.workflow_kind = 'official_source_nonurgent_pack'",
        "batch_job.stage = 'generate'",
        "batch_job.status = 'completed'",
        "batch_job.reservation_state = 'settled'",
        "batch_job.result_code = 'needs_review'",
        "batch_job.input_payload -> 'approval_required' = 'true'::jsonb",
        "batch_job.input_payload -> 'input_immutable' = 'true'::jsonb",
        "batch_job.input_payload -> 'source_snapshot_complete' = 'true'::jsonb",
        "(batch_job.input_payload ->> 'input')::jsonb",
        "-> 'source' ->> 'content'",
        "between 1 and 60000",
        "-> 'source' ->> 'url'",
        "= review_job.input ->> 'source_url'",
        "from jsonb_object_keys(batch_job.result_payload)",
        ") = 4",
        "review_job.status = 'succeeded'",
        "review_job.content_item_id is null",
        "review_job.input ->> 'workflow' = 'official_x_review_draft_v1'",
        "review_job.input ->> 'content_kind' = 'daily_news'",
        "review_job.input -> 'manual_only' = 'false'::jsonb",
        "'workflow', 'agent_batch_review_handoff_v1'",
        "'handoff', 'openai_batch'",
        "'review_state', 'pending'",
    ):
        assert existing_guard in detail
    assert "'fact_check_evidence'" in detail
    assert "review_job.input ->> 'source_image_url'" in detail
    assert "else null" in detail
    assert (
        "origintrail_reviewed_media_evidence(\n"
        "                    batch_job.job_id"
    ) in detail
    assert re.search(
        r"revoke all on function public\.get_agent_batch_review_item\(\s*"
        r"uuid,\s*uuid\s*\) from public, anon, authenticated, service_role;",
        MIGRATION,
        re.DOTALL,
    )
    assert re.search(
        r"grant execute on function public\.get_agent_batch_review_item\(\s*"
        r"uuid,\s*uuid\s*\) to service_role;",
        MIGRATION,
        re.DOTALL,
    )


def test_migration_cannot_publish_or_create_external_side_effects() -> None:
    lowered = MIGRATION.lower()
    for forbidden in (
        "insert into public.publications",
        "update public.publications",
        "api.telegram.org",
        "/sendmessage",
        "http_post",
        "net.http",
    ):
        assert forbidden not in lowered
