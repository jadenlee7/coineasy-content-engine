-- Transactional smoke for the verified standalone OriginTrail text-post gate.
-- No Batch, Buzz, publication, provider, or external network call is made.

begin;

insert into public.workspaces (id, name, slug, created_by)
values (
    'e0000000-0000-4000-8000-000000000001',
    'OriginTrail standalone text evidence test',
    'origintrail-standalone-text-evidence-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values (
    'e0000000-0000-4000-8000-000000000001',
    'origintrail', 'OriginTrail', true, null
);

insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, active, created_by
)
values (
    'e4000000-0000-4000-8000-000000000001',
    'e0000000-0000-4000-8000-000000000001',
    'origintrail', 'x', 'OriginTrail text evidence source',
    'https://x.com/origin_trail', '@origin_trail', 15, true, null
);

insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, title, body, media,
    raw_payload, source_hash, ingested_by
)
values
(
    'e5000000-0000-4000-8000-000000000001',
    'e0000000-0000-4000-8000-000000000001',
    'origintrail', 'e4000000-0000-4000-8000-000000000001',
    '2083000000000000001', 'tweet',
    'https://x.com/origin_trail/status/2083000000000000001',
    '@origin_trail', statement_timestamp() - interval '1 hour', null,
    'OriginTrail DKG gives AI agents verifiable knowledge with durable provenance.',
    '[]'::jsonb, '{}'::jsonb, pg_catalog.md5('origintrail-text-valid'), null
),
(
    'e5000000-0000-4000-8000-000000000002',
    'e0000000-0000-4000-8000-000000000001',
    'origintrail', 'e4000000-0000-4000-8000-000000000001',
    '2083000000000000002', 'tweet',
    'https://x.com/origin_trail/status/2083000000000000002',
    '@origin_trail', statement_timestamp() - interval '1 hour', null,
    'https://t.co/only-link',
    '[]'::jsonb, '{}'::jsonb, pg_catalog.md5('origintrail-text-url-only'), null
),
(
    'e5000000-0000-4000-8000-000000000003',
    'e0000000-0000-4000-8000-000000000001',
    'origintrail', 'e4000000-0000-4000-8000-000000000001',
    '2083000000000000003', 'tweet',
    'https://x.com/origin_trail/status/2083000000000000003',
    '@origin_trail', statement_timestamp() - interval '1 hour', null,
    'This post has media and must stay outside the text-only review path.',
    jsonb_build_array(jsonb_build_object('type', 'photo')),
    '{}'::jsonb, pg_catalog.md5('origintrail-text-media'), null
);

insert into private.official_x_poll_receipts (
    workspace_id, client_id, poll_request_id, source_feed_id,
    expected_cursor, next_cursor, payload_hash, source_item_ids,
    inserted_count, polled_at
)
values (
    'e0000000-0000-4000-8000-000000000001',
    'origintrail',
    'e6000000-0000-4000-8000-000000000001',
    'e4000000-0000-4000-8000-000000000001',
    null, '2083000000000000003',
    pg_catalog.md5('origintrail-standalone-text-evidence-poll'),
    array[
        'e5000000-0000-4000-8000-000000000001'::uuid,
        'e5000000-0000-4000-8000-000000000002'::uuid,
        'e5000000-0000-4000-8000-000000000003'::uuid
    ],
    3,
    statement_timestamp() - interval '1 hour'
);

insert into private.origintrail_standalone_sources (
    workspace_id, client_id, source_item_id, is_quote,
    first_poll_request_id, verified_at
)
select
    'e0000000-0000-4000-8000-000000000001'::uuid,
    'origintrail',
    source_id,
    false,
    'e6000000-0000-4000-8000-000000000001'::uuid,
    statement_timestamp() - interval '1 hour'
from unnest(array[
    'e5000000-0000-4000-8000-000000000001'::uuid,
    'e5000000-0000-4000-8000-000000000002'::uuid,
    'e5000000-0000-4000-8000-000000000003'::uuid
]) as source(source_id);

do $test$
declare
    test_workspace_id constant uuid :=
        'e0000000-0000-4000-8000-000000000001';
    poll_request_id constant uuid :=
        'e6000000-0000-4000-8000-000000000001';
begin
    if private.origintrail_source_evidence_kind(
        test_workspace_id,
        'e5000000-0000-4000-8000-000000000001',
        poll_request_id
    ) is distinct from 'x_post_text' then
        raise exception 'verified standalone text post was not admitted';
    end if;

    if private.origintrail_source_evidence_kind(
        test_workspace_id,
        'e5000000-0000-4000-8000-000000000002',
        poll_request_id
    ) is not null then
        raise exception 'URL-only post crossed the evidence gate';
    end if;

    if private.origintrail_source_evidence_kind(
        test_workspace_id,
        'e5000000-0000-4000-8000-000000000003',
        poll_request_id
    ) is not null then
        raise exception 'media post crossed the text-only evidence gate';
    end if;

    if private.origintrail_source_evidence_kind(
        test_workspace_id,
        'e5000000-0000-4000-8000-000000000001',
        'e6000000-0000-4000-8000-000000000009'
    ) is not null then
        raise exception 'unmatched poll receipt crossed the evidence gate';
    end if;

    if has_function_privilege(
        'coineasy_batch_reviewer',
        'private.origintrail_source_evidence_kind(uuid,uuid,uuid)',
        'execute'
    ) or has_function_privilege(
        'coineasy_buzz_review_decider',
        'private.origintrail_source_evidence_kind(uuid,uuid,uuid)',
        'execute'
    ) then
        raise exception 'private evidence helper leaked to scoped roles';
    end if;
end
$test$;

rollback;
