-- Durable, review-only Grok QA dispatch for immutable Content Studio versions.
--
-- Exactly two official-X completion events enqueue work. The trigger runs in
-- the transaction that commits an eligible Daily News review version, so a
-- worker crash cannot leave that durable needs_review card without a matching
-- QA row. Article and Tutorial stay in manual Studio review until their
-- durable banner contract exists.
-- The dispatcher may record an advisory verdict and relay it through the
-- existing Grok QA receipt, but none of these routines can approve or publish.

begin;

create table private.grok_qa_dispatch_outbox (
    workspace_id uuid not null,
    content_item_id uuid not null,
    content_version_id uuid not null,
    client_id text not null check (
        client_id in ('yellow', 'origintrail', 'squid', 'babylon')
    ),
    content_kind text not null check (content_kind = 'daily_news'),
    source_item_id uuid not null,
    source_url text not null check (
        source_url ~ '^https://x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]{1,19}$'
        and char_length(source_url) <= 200
    ),
    source_author_handle text not null check (
        source_author_handle in (
            '@Yellow', '@origin_trail', '@SquidRouter', '@babylonlabs_io'
        )
    ),
    source_published_at timestamptz not null,
    source_event_id bigint not null check (
        source_event_id between 1 and 9007199254740991
    ),
    source_event_type text not null check (
        source_event_type in (
            'official_x_review_draft_completed',
            'origintrail_batch_review_pack_materialized'
        )
    ),
    status text not null default 'pending' check (
        status in (
            'pending', 'claimed', 'staged', 'sent', 'obsolete', 'failed',
            'provider_unknown', 'delivery_unknown'
        )
    ),
    attempts integer not null default 0 check (attempts between 0 and 3),
    max_attempts integer not null default 3 check (max_attempts = 3),
    available_at timestamptz not null default statement_timestamp(),
    locked_by text check (
        locked_by is null
        or locked_by ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
    ),
    locked_at timestamptz,
    lease_expires_at timestamptz,
    verdict jsonb check (
        verdict is null or jsonb_typeof(verdict) = 'object'
    ),
    verdict_sha256 text check (
        verdict_sha256 is null or verdict_sha256 ~ '^[a-f0-9]{64}$'
    ),
    model text check (
        model is null or model = 'grok-4.5'
    ),
    prompt_version text check (
        prompt_version is null
        or prompt_version in (
            'official-x-grok-qa@1', 'grok-qa-external-receipt@1'
        )
    ),
    provider_input_sha256 text check (
        provider_input_sha256 is null
        or provider_input_sha256 ~ '^[a-f0-9]{64}$'
    ),
    banner_sha256 text check (
        banner_sha256 is null or banner_sha256 ~ '^[a-f0-9]{64}$'
    ),
    provider_attempt_started_at timestamptz,
    provider_response_id text check (
        provider_response_id is null
        or provider_response_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$'
    ),
    cost_in_usd_ticks bigint check (
        cost_in_usd_ticks is null
        or cost_in_usd_ticks between 0 and 5000000000
    ),
    x_search_citations jsonb check (
        x_search_citations is null
        or (
            jsonb_typeof(x_search_citations) = 'array'
            and jsonb_array_length(x_search_citations) between 1 and 8
        )
    ),
    x_search_calls smallint check (
        x_search_calls is null or x_search_calls between 1 and 3
    ),
    error_code text check (
        error_code is null or error_code ~ '^[a-z][a-z0-9_]{0,79}$'
    ),
    enqueued_at timestamptz not null default statement_timestamp(),
    completed_at timestamptz,
    updated_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, content_version_id),
    unique (workspace_id, source_event_id),
    foreign key (workspace_id, content_item_id)
        references public.content_items(workspace_id, id) on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id)
        on delete restrict,
    foreign key (workspace_id, client_id, source_item_id)
        references public.source_items(workspace_id, client_id, id)
        on delete restrict,
    foreign key (source_event_id)
        references public.event_log(id) on delete restrict,
    check (
        (status = 'claimed') = (
            locked_by is not null
            and locked_at is not null
            and lease_expires_at is not null
        )
    ),
    check (
        status <> 'claimed' or lease_expires_at > locked_at
    ),
    check (
        (provider_input_sha256 is null)
            = (provider_attempt_started_at is null)
        and (provider_input_sha256 is null) = (banner_sha256 is null)
    ),
    check (
        provider_attempt_started_at is null
        or status in (
            'claimed', 'staged', 'sent', 'failed', 'provider_unknown',
            'delivery_unknown'
        )
    ),
    check (
        (provider_response_id is null)
            = (cost_in_usd_ticks is null)
        and (provider_response_id is null)
            = (x_search_citations is null)
        and (provider_response_id is null)
            = (x_search_calls is null)
    ),
    check (
        (
            verdict is null
            and verdict_sha256 is null
            and model is null
            and prompt_version is null
            and provider_response_id is null
        )
        or (
            verdict is not null
            and verdict_sha256 is not null
            and prompt_version = 'official-x-grok-qa@1'
            and model = 'grok-4.5'
            and provider_input_sha256 is not null
            and banner_sha256 is not null
            and provider_attempt_started_at is not null
            and provider_response_id is not null
        )
        or (
            verdict is not null
            and verdict_sha256 is not null
            and prompt_version = 'grok-qa-external-receipt@1'
            and model is null
            and provider_input_sha256 is null
            and banner_sha256 is null
            and provider_attempt_started_at is null
            and provider_response_id is null
        )
    ),
    check (
        status not in ('sent', 'delivery_unknown')
        or verdict is not null
    ),
    check (
        status <> 'provider_unknown'
        or (
            provider_attempt_started_at is not null
            and verdict is null
        )
    ),
    check (
        status not in (
            'sent', 'obsolete', 'failed', 'provider_unknown',
            'delivery_unknown'
        )
        or completed_at is not null
    ),
    check (
        status in ('failed', 'provider_unknown', 'delivery_unknown')
        or error_code is null
    ),
    check (
        status not in ('failed', 'provider_unknown', 'delivery_unknown')
        or error_code is not null
    )
);

create index grok_qa_dispatch_claim_idx
    on private.grok_qa_dispatch_outbox (
        workspace_id, status, available_at, enqueued_at, content_version_id
    )
    where status in ('pending', 'staged');
create index grok_qa_dispatch_lease_idx
    on private.grok_qa_dispatch_outbox (
        workspace_id, lease_expires_at, content_version_id
    )
    where status = 'claimed';

alter table private.grok_qa_dispatch_outbox enable row level security;
alter table private.grok_qa_dispatch_outbox force row level security;
revoke all on table private.grok_qa_dispatch_outbox
from public, anon, authenticated, service_role;

create or replace function private.grok_qa_dispatch_verdict_valid(
    target_payload jsonb
)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
    issue jsonb;
begin
    if target_payload is null
       or jsonb_typeof(target_payload) <> 'object'
       or not target_payload ?& array[
           'decision', 'summary', 'fact_check', 'brand_check', 'issues',
           'next_action'
       ]
       or (select count(*) from jsonb_object_keys(target_payload)) <> 6
       or target_payload ->> 'decision' not in ('PASS', 'WARN', 'BLOCK')
       or jsonb_typeof(target_payload -> 'summary') <> 'string'
       or char_length(target_payload ->> 'summary') not between 10 and 800
       or btrim(target_payload ->> 'summary') = ''
       or target_payload ->> 'next_action' not in (
           'ready_for_human_approval', 'human_review', 'verify_source',
           'revise_copy', 'revise_banner'
       ) then
        return false;
    end if;

    if jsonb_typeof(target_payload -> 'fact_check') <> 'object'
       or not (target_payload -> 'fact_check') ?& array[
           'status', 'checks', 'source_urls'
       ]
       or (select count(*) from jsonb_object_keys(
           target_payload -> 'fact_check'
       )) <> 3
       or target_payload -> 'fact_check' ->> 'status'
            not in ('PASS', 'WARN', 'BLOCK')
       or jsonb_typeof(target_payload -> 'fact_check' -> 'checks') <> 'array'
       or jsonb_array_length(target_payload -> 'fact_check' -> 'checks')
            not between 1 and 6
       or exists (
           select 1
           from jsonb_array_elements(target_payload -> 'fact_check' -> 'checks')
                as check_item(value)
           where jsonb_typeof(check_item.value) <> 'string'
              or char_length(check_item.value #>> '{}') not between 3 and 300
              or btrim(check_item.value #>> '{}') = ''
       )
       or jsonb_typeof(
           target_payload -> 'fact_check' -> 'source_urls'
       ) <> 'array'
       or jsonb_array_length(
           target_payload -> 'fact_check' -> 'source_urls'
       ) > 8
       or exists (
           select 1
           from jsonb_array_elements(
               target_payload -> 'fact_check' -> 'source_urls'
           ) as source_url(value)
           where jsonb_typeof(source_url.value) <> 'string'
              or char_length(source_url.value #>> '{}') not between 9 and 2048
              or (source_url.value #>> '{}') !~ '^https://[^[:space:]#]+$'
       ) then
        return false;
    end if;

    if jsonb_typeof(target_payload -> 'brand_check') <> 'object'
       or not (target_payload -> 'brand_check') ?& array['status', 'checks']
       or (select count(*) from jsonb_object_keys(
           target_payload -> 'brand_check'
       )) <> 2
       or target_payload -> 'brand_check' ->> 'status'
            not in ('PASS', 'WARN', 'BLOCK')
       or jsonb_typeof(target_payload -> 'brand_check' -> 'checks') <> 'array'
       or jsonb_array_length(target_payload -> 'brand_check' -> 'checks')
            not between 1 and 6
       or exists (
           select 1
           from jsonb_array_elements(target_payload -> 'brand_check' -> 'checks')
                as check_item(value)
           where jsonb_typeof(check_item.value) <> 'string'
              or char_length(check_item.value #>> '{}') not between 3 and 300
              or btrim(check_item.value #>> '{}') = ''
       ) then
        return false;
    end if;

    if jsonb_typeof(target_payload -> 'issues') <> 'array'
       or jsonb_array_length(target_payload -> 'issues') > 3 then
        return false;
    end if;
    for issue in
        select value from jsonb_array_elements(target_payload -> 'issues')
    loop
        if jsonb_typeof(issue) <> 'object'
           or not issue ?& array['severity', 'code', 'message']
           or not (
               (
                   (select count(*) from jsonb_object_keys(issue)) = 3
                   and not issue ? 'evidence_url'
               )
               or (
                   (select count(*) from jsonb_object_keys(issue)) = 4
                   and issue ? 'evidence_url'
               )
           )
           or issue ->> 'severity' not in ('WARN', 'BLOCK')
           or coalesce(issue ->> 'code', '') !~ '^[a-z][a-z0-9_]{2,47}$'
           or char_length(coalesce(issue ->> 'message', '')) not between 3 and 500
           or btrim(coalesce(issue ->> 'message', '')) = ''
           or (issue ? 'evidence_url' and (
               jsonb_typeof(issue -> 'evidence_url') <> 'string'
               or char_length(issue ->> 'evidence_url') not between 9 and 2048
               or (issue ->> 'evidence_url') !~ '^https://[^[:space:]#]+$'
           )) then
            return false;
        end if;
    end loop;

    if target_payload ->> 'decision' = 'PASS' and (
        target_payload -> 'fact_check' ->> 'status' <> 'PASS'
        or target_payload -> 'brand_check' ->> 'status' <> 'PASS'
        or jsonb_array_length(target_payload -> 'issues') <> 0
        or target_payload ->> 'next_action' <> 'ready_for_human_approval'
        or jsonb_array_length(
            target_payload -> 'fact_check' -> 'source_urls'
        ) = 0
    ) then
        return false;
    end if;
    if target_payload ->> 'decision' <> 'PASS'
       and target_payload ->> 'next_action' = 'ready_for_human_approval' then
        return false;
    end if;
    if target_payload ->> 'decision' = 'BLOCK'
       and target_payload -> 'fact_check' ->> 'status' <> 'BLOCK'
       and target_payload -> 'brand_check' ->> 'status' <> 'BLOCK'
       and not exists (
           select 1 from jsonb_array_elements(target_payload -> 'issues')
                as blocking_issue(value)
           where blocking_issue.value ->> 'severity' = 'BLOCK'
       ) then
        return false;
    end if;

    return true;
end;
$$;

create or replace function private.grok_qa_dispatch_citations_valid(
    target_citations jsonb,
    target_source_url text
)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
    citation text;
    source_post_id text;
begin
    if target_citations is null
       or jsonb_typeof(target_citations) <> 'array'
       or jsonb_array_length(target_citations) not between 1 and 8
       or target_source_url
            !~ '^https://x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]{1,19}$'
       or (
           select count(*)
           from jsonb_array_elements_text(target_citations)
       ) <> (
           select count(distinct value)
           from jsonb_array_elements_text(target_citations) as item(value)
       ) then
        return false;
    end if;

    source_post_id := split_part(target_source_url, '/', 6);
    for citation in
        select value #>> '{}'
        from jsonb_array_elements(target_citations) as item(value)
    loop
        -- Every x_search citation is the same immutable post, represented by
        -- either its exact official-handle URL or X's canonical /i/status URL.
        -- Credentials, ports, queries, fragments, and alternate hosts fail.
        if char_length(citation) not between 9 and 2048
           or (
               lower(rtrim(citation, '/')) is distinct from
                    lower(target_source_url)
               and lower(rtrim(citation, '/')) is distinct from (
                   'https://x.com/i/status/' || lower(source_post_id)
               )
           ) then
            return false;
        end if;
    end loop;

    return true;
exception
    when others then
        return false;
end;
$$;

create or replace function private.grok_qa_dispatch_object(
    target_workspace_id uuid,
    target_content_version_id uuid,
    target_claim_granted boolean
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select jsonb_build_object(
        'content_item_id', dispatch.content_item_id,
        'content_version_id', dispatch.content_version_id,
        'client_id', dispatch.client_id,
        'content_kind', dispatch.content_kind,
        'source_item_id', dispatch.source_item_id,
        'source_url', dispatch.source_url,
        'source_author_handle', dispatch.source_author_handle,
        'source_published_at', dispatch.source_published_at,
        'source_event_id', dispatch.source_event_id,
        'source_event_type', dispatch.source_event_type,
        'status', dispatch.status,
        'attempts', dispatch.attempts,
        'max_attempts', dispatch.max_attempts,
        'lease_expires_at', dispatch.lease_expires_at,
        'verdict', dispatch.verdict,
        'verdict_sha256', dispatch.verdict_sha256,
        'model', dispatch.model,
        'prompt_version', dispatch.prompt_version,
        'input_sha256', dispatch.provider_input_sha256,
        'banner_sha256', dispatch.banner_sha256,
        'provider_attempt_started_at', dispatch.provider_attempt_started_at,
        'provider_response_id', dispatch.provider_response_id,
        'cost_in_usd_ticks', dispatch.cost_in_usd_ticks,
        'x_search_citations', dispatch.x_search_citations,
        'x_search_calls', dispatch.x_search_calls,
        'provider_call_required', dispatch.verdict is null,
        'claim_granted', target_claim_granted
    )
    from private.grok_qa_dispatch_outbox as dispatch
    where dispatch.workspace_id = target_workspace_id
      and dispatch.content_version_id = target_content_version_id
$$;

create or replace function private.enqueue_official_x_grok_qa_dispatch()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    target_content_version_id uuid;
    target_job_id uuid;
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    review_job public.jobs%rowtype;
    primary_source public.source_items%rowtype;
    expected_handle text;
    primary_source_count integer;
begin
    if new.event_type not in (
        'official_x_review_draft_completed',
        'origintrail_batch_review_pack_materialized'
    ) then
        return new;
    end if;
    if new.entity_type is distinct from 'content_item'
       or new.entity_id is null
       or jsonb_typeof(new.data) <> 'object'
       or coalesce(new.data ->> 'content_version_id', '')
            !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then
        raise exception 'Grok QA dispatch source event is invalid'
            using errcode = '23514';
    end if;
    target_content_version_id := (new.data ->> 'content_version_id')::uuid;

    select current_item.* into item
    from public.content_items as current_item
    where current_item.workspace_id = new.workspace_id
      and current_item.id = new.entity_id
    for key share;
    if not found
       or item.status is distinct from 'needs_review'
       or item.current_version_id is distinct from target_content_version_id
       or item.client_id not in ('yellow', 'origintrail', 'squid', 'babylon') then
        raise exception 'Grok QA dispatch target is not current needs_review'
            using errcode = '23514';
    end if;
    -- Article/tutorial records intentionally remain in the manual Studio flow:
    -- their canonical durable banner contract does not exist yet. Do not make
    -- their otherwise-valid completion event fail, and never enqueue them.
    if item.content_kind is distinct from 'daily_news' then
        return new;
    end if;

    select current_version.* into version
    from public.content_versions as current_version
    where current_version.workspace_id = new.workspace_id
      and current_version.content_item_id = item.id
      and current_version.id = target_content_version_id;
    if not found or version.generation_meta -> 'mock_mode' = 'true'::jsonb then
        raise exception 'Grok QA dispatch target version is not eligible'
            using errcode = '23514';
    end if;

    expected_handle := case item.client_id
        when 'yellow' then '@Yellow'
        when 'origintrail' then '@origin_trail'
        when 'squid' then '@SquidRouter'
        when 'babylon' then '@babylonlabs_io'
        else null
    end;
    select count(*) into primary_source_count
        from public.content_source_links as link
        join public.source_items as source
          on source.workspace_id = link.workspace_id
         and source.client_id = link.client_id
         and source.id = link.source_item_id
        join public.source_feeds as feed
          on feed.workspace_id = source.workspace_id
         and feed.client_id = source.client_id
         and feed.id = source.source_feed_id
        where link.workspace_id = item.workspace_id
          and link.client_id = item.client_id
          and link.content_item_id = item.id
          and source.source_type = 'tweet'
          and feed.provider = 'x'
          and feed.handle = expected_handle
          and feed.active is true
          and link.position = 0;
    if primary_source_count <> 1 then
        raise exception 'Grok QA dispatch lacks an active official X source'
            using errcode = '23514';
    end if;
    select source.* into primary_source
    from public.content_source_links as link
    join public.source_items as source
      on source.workspace_id = link.workspace_id
     and source.client_id = link.client_id
     and source.id = link.source_item_id
    join public.source_feeds as feed
      on feed.workspace_id = source.workspace_id
     and feed.client_id = source.client_id
     and feed.id = source.source_feed_id
    where link.workspace_id = item.workspace_id
      and link.client_id = item.client_id
      and link.content_item_id = item.id
      and link.position = 0
      and source.source_type = 'tweet'
      and feed.provider = 'x'
      and feed.handle = expected_handle
      and feed.active is true;
    if not found
       or primary_source.author_handle is distinct from expected_handle
       or primary_source.canonical_url !~ (
           '^https://x\.' || 'com/' ||
           substring(expected_handle from 2) || '/status/[0-9]{1,19}$'
       )
       or primary_source.external_id is distinct from
            split_part(primary_source.canonical_url, '/', 6)
       or primary_source.published_at is null then
        raise exception 'Grok QA dispatch official X identity is invalid'
            using errcode = '23514';
    end if;

    if new.event_type = 'official_x_review_draft_completed' then
        if coalesce(new.data ->> 'job_id', '')
                !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then
            raise exception 'Grok QA review job identity is invalid'
                using errcode = '23514';
        end if;
        target_job_id := (new.data ->> 'job_id')::uuid;
        select queued_job.* into review_job
        from public.jobs as queued_job
        where queued_job.workspace_id = new.workspace_id
          and queued_job.id = target_job_id;
        if not found
           or review_job.client_id is distinct from item.client_id
           or review_job.status is distinct from 'succeeded'
           or review_job.job_kind is distinct from 'generate'
           or review_job.input ->> 'workflow'
                is distinct from 'official_x_review_draft_v1'
           or not coalesce(
               review_job.input -> 'source_item_ids'
                   @> jsonb_build_array(primary_source.id::text),
               false
           )
           or review_job.output ->> 'content_item_id'
                is distinct from item.id::text
           or review_job.output ->> 'content_version_id'
                is distinct from target_content_version_id::text
           or not coalesce(
               new.data -> 'source_item_ids'
                   @> jsonb_build_array(primary_source.id::text),
               false
           ) then
            raise exception 'Grok QA review completion event is not authoritative'
                using errcode = '23514';
        end if;
    elsif item.client_id is distinct from 'origintrail'
       or item.content_kind is distinct from 'daily_news'
       or coalesce(new.data ->> 'job_id', '')
            !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or not exists (
           select 1
           from agent_runtime.origintrail_batch_review_packs as review_pack
           where review_pack.workspace_id = new.workspace_id
             and review_pack.job_id = (new.data ->> 'job_id')::uuid
             and review_pack.content_item_id = item.id
             and review_pack.content_version_id = target_content_version_id
             and review_pack.source_item_id = primary_source.id
       ) then
        raise exception 'Grok QA Batch materialization event is not authoritative'
            using errcode = '23514';
    end if;

    insert into private.grok_qa_dispatch_outbox (
        workspace_id, content_item_id, content_version_id, client_id,
        content_kind, source_item_id, source_url, source_author_handle,
        source_published_at, source_event_id, source_event_type
    ) values (
        new.workspace_id, item.id, target_content_version_id, item.client_id,
        item.content_kind, primary_source.id, primary_source.canonical_url,
        primary_source.author_handle, primary_source.published_at,
        new.id, new.event_type
    ) on conflict (workspace_id, content_version_id) do nothing;

    return new;
end;
$$;

create trigger enqueue_official_x_grok_qa_dispatch
after insert on public.event_log
for each row
when (new.event_type in (
    'official_x_review_draft_completed',
    'origintrail_batch_review_pack_materialized'
))
execute function private.enqueue_official_x_grok_qa_dispatch();

create or replace function public.claim_grok_qa_dispatch_job(
    target_workspace_id uuid,
    target_worker_id text,
    target_lease_seconds integer,
    target_allowed_clients text[],
    target_canary_content_version_id uuid default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    dispatch private.grok_qa_dispatch_outbox%rowtype;
    item public.content_items%rowtype;
    primary_source public.source_items%rowtype;
    receipt private.grok_qa_verdict_receipts%rowtype;
    expected_handle text;
    primary_source_count integer;
    item_found boolean;
    receipt_found boolean;
    scan_count integer := 0;
    provider_call_required boolean;
begin
    if target_workspace_id is null
       or coalesce(target_worker_id, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or target_lease_seconds not between 180 and 600
       or target_allowed_clients is null
       or cardinality(target_allowed_clients) not between 1 and 4
       or exists (
           select 1
           from unnest(target_allowed_clients) as allowed(client_id)
           where allowed.client_id is null
              or allowed.client_id not in (
                  'yellow', 'origintrail', 'squid', 'babylon'
              )
       )
       or cardinality(target_allowed_clients) <> (
           select count(distinct allowed.client_id)
           from unnest(target_allowed_clients) as allowed(client_id)
       ) then
        raise exception 'Grok QA dispatch claim is invalid'
            using errcode = '22023';
    end if;

    -- Drain stale/terminal candidates iteratively. Recursing from a
    -- SECURITY DEFINER claim makes a long stale prefix an unbounded stack and
    -- lets it evade one-call work limits. Thirty-two rows is deliberately
    -- bounded; a later worker tick resumes where this call stopped.
    while scan_count < 32 loop
        dispatch := null;
        select queued.* into dispatch
        from private.grok_qa_dispatch_outbox as queued
        where queued.workspace_id = target_workspace_id
          and queued.content_kind = 'daily_news'
          and queued.client_id = any(target_allowed_clients)
          and (
              target_canary_content_version_id is null
              or queued.content_version_id = target_canary_content_version_id
          )
          and (
              (
                  queued.status = 'pending'
                  and queued.available_at <= statement_timestamp()
                  and queued.attempts < queued.max_attempts
              )
              or queued.status = 'staged'
              or (
                  queued.status = 'claimed'
                  and queued.verdict is not null
                  and queued.lease_expires_at <= statement_timestamp()
              )
          )
        order by
            case when queued.verdict is not null then 0 else 1 end,
            queued.available_at, queued.enqueued_at,
            queued.content_version_id
        limit 1
        for update skip locked;
        if not found then
            return jsonb_build_object(
                'schema_version', '1.0',
                'mode', 'official_x_grok_qa_dispatch',
                'workspace_id', target_workspace_id,
                'job', null
            );
        end if;
        scan_count := scan_count + 1;

        item := null;
        select current_item.* into item
        from public.content_items as current_item
        where current_item.workspace_id = dispatch.workspace_id
          and current_item.id = dispatch.content_item_id
        for key share;
        item_found := found;
        if not item_found
           or item.status is distinct from 'needs_review'
           or item.current_version_id is distinct from dispatch.content_version_id
           or item.client_id is distinct from dispatch.client_id
           or item.content_kind is distinct from 'daily_news'
           or dispatch.content_kind is distinct from 'daily_news' then
            update private.grok_qa_dispatch_outbox
            set status = 'obsolete',
                locked_by = null,
                locked_at = null,
                lease_expires_at = null,
                completed_at = statement_timestamp(),
                updated_at = statement_timestamp()
            where workspace_id = dispatch.workspace_id
              and content_version_id = dispatch.content_version_id;
            continue;
        end if;

        expected_handle := case dispatch.client_id
            when 'yellow' then '@Yellow'
            when 'origintrail' then '@origin_trail'
            when 'squid' then '@SquidRouter'
            when 'babylon' then '@babylonlabs_io'
            else null
        end;
        primary_source := null;
        select count(*) into primary_source_count
        from public.content_source_links as link
        join public.source_items as source
          on source.workspace_id = link.workspace_id
         and source.client_id = link.client_id
         and source.id = link.source_item_id
        join public.source_feeds as feed
          on feed.workspace_id = source.workspace_id
         and feed.client_id = source.client_id
         and feed.id = source.source_feed_id
        where link.workspace_id = dispatch.workspace_id
          and link.client_id = dispatch.client_id
          and link.content_item_id = dispatch.content_item_id
          and link.position = 0
          and source.source_type = 'tweet'
          and feed.provider = 'x'
          and feed.handle = expected_handle
          and feed.active is true;
        if primary_source_count = 1 then
            select source.* into primary_source
            from public.content_source_links as link
            join public.source_items as source
              on source.workspace_id = link.workspace_id
             and source.client_id = link.client_id
             and source.id = link.source_item_id
            join public.source_feeds as feed
              on feed.workspace_id = source.workspace_id
             and feed.client_id = source.client_id
             and feed.id = source.source_feed_id
            where link.workspace_id = dispatch.workspace_id
              and link.client_id = dispatch.client_id
              and link.content_item_id = dispatch.content_item_id
              and link.position = 0
              and source.source_type = 'tweet'
              and feed.provider = 'x'
              and feed.handle = expected_handle
              and feed.active is true;
        end if;
        if primary_source_count <> 1
           or primary_source.id is distinct from dispatch.source_item_id
           or primary_source.author_handle is distinct from expected_handle
           or primary_source.author_handle
                is distinct from dispatch.source_author_handle
           or primary_source.canonical_url is distinct from dispatch.source_url
           or primary_source.canonical_url !~ (
               '^https://x\.' || 'com/' ||
               substring(expected_handle from 2) || '/status/[0-9]{1,19}$'
           )
           or primary_source.external_id is distinct from
                split_part(primary_source.canonical_url, '/', 6)
           or primary_source.published_at
                is distinct from dispatch.source_published_at then
            update private.grok_qa_dispatch_outbox
            set status = 'obsolete',
                locked_by = null,
                locked_at = null,
                lease_expires_at = null,
                completed_at = statement_timestamp(),
                updated_at = statement_timestamp()
            where workspace_id = dispatch.workspace_id
              and content_version_id = dispatch.content_version_id;
            continue;
        end if;

        receipt := null;
        select current_receipt.* into receipt
        from private.grok_qa_verdict_receipts as current_receipt
        where current_receipt.workspace_id = dispatch.workspace_id
          and current_receipt.content_version_id = dispatch.content_version_id;
        receipt_found := found;
        if receipt_found then
            if dispatch.verdict is not null then
                update private.grok_qa_dispatch_outbox
                set status = case
                        when receipt.payload_sha256 is distinct from
                                dispatch.verdict_sha256
                            then 'delivery_unknown'
                        when receipt.status = 'sent' then 'sent'
                        when receipt.status = 'failed' then 'failed'
                        else 'delivery_unknown'
                    end,
                    error_code = case
                        when receipt.payload_sha256 is distinct from
                                dispatch.verdict_sha256
                            then 'grok_qa_receipt_payload_conflict'
                        when receipt.status = 'sent' then null
                        when receipt.status = 'failed' then coalesce(
                            receipt.failure_code, 'grok_qa_receipt_failed'
                        )
                        else 'grok_qa_receipt_claimed'
                    end,
                    locked_by = null,
                    locked_at = null,
                    lease_expires_at = null,
                    completed_at = statement_timestamp(),
                    updated_at = statement_timestamp()
                where workspace_id = dispatch.workspace_id
                  and content_version_id = dispatch.content_version_id;
            else
                update private.grok_qa_dispatch_outbox
                set status = case receipt.status
                        when 'sent' then 'sent'
                        when 'failed' then 'failed'
                        else 'delivery_unknown'
                    end,
                    error_code = case receipt.status
                        when 'sent' then null
                        when 'failed' then coalesce(
                            receipt.failure_code, 'grok_qa_receipt_failed'
                        )
                        else 'grok_qa_receipt_claimed'
                    end,
                    verdict = receipt.payload,
                    verdict_sha256 = receipt.payload_sha256,
                    model = null,
                    prompt_version = 'grok-qa-external-receipt@1',
                    locked_by = null,
                    locked_at = null,
                    lease_expires_at = null,
                    completed_at = statement_timestamp(),
                    updated_at = statement_timestamp()
                where workspace_id = dispatch.workspace_id
                  and content_version_id = dispatch.content_version_id;
            end if;
            continue;
        end if;

        provider_call_required := dispatch.verdict is null;
        update private.grok_qa_dispatch_outbox
        set status = 'claimed',
            attempts = case when provider_call_required
                then attempts + 1 else attempts end,
            locked_by = target_worker_id,
            locked_at = statement_timestamp(),
            lease_expires_at = statement_timestamp()
                + make_interval(secs => target_lease_seconds),
            error_code = null,
            completed_at = null,
            updated_at = statement_timestamp()
        where workspace_id = dispatch.workspace_id
          and content_version_id = dispatch.content_version_id
        returning * into dispatch;

        return jsonb_build_object(
            'schema_version', '1.0',
            'mode', 'official_x_grok_qa_dispatch',
            'workspace_id', target_workspace_id,
            'job', private.grok_qa_dispatch_object(
                dispatch.workspace_id, dispatch.content_version_id, true
            )
        );
    end loop;

    return jsonb_build_object(
        'schema_version', '1.0',
        'mode', 'official_x_grok_qa_dispatch',
        'workspace_id', target_workspace_id,
        'job', null
    );
end;
$$;

create or replace function public.mark_grok_qa_dispatch_provider_attempt(
    target_workspace_id uuid,
    target_content_version_id uuid,
    target_worker_id text,
    target_input_sha256 text,
    target_banner_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    dispatch private.grok_qa_dispatch_outbox%rowtype;
    item public.content_items%rowtype;
    primary_source public.source_items%rowtype;
    version public.content_versions%rowtype;
    banner_asset public.assets%rowtype;
    expected_handle text;
    primary_source_count integer;
    item_found boolean;
    authorized_once boolean := false;
begin
    if target_workspace_id is null
       or target_content_version_id is null
       or coalesce(target_worker_id, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or coalesce(target_input_sha256, '') !~ '^[a-f0-9]{64}$'
       or coalesce(target_banner_sha256, '') !~ '^[a-f0-9]{64}$' then
        raise exception 'Grok QA provider attempt is invalid'
            using errcode = '22023';
    end if;

    select queued.* into dispatch
    from private.grok_qa_dispatch_outbox as queued
    where queued.workspace_id = target_workspace_id
      and queued.content_version_id = target_content_version_id
    for update;
    if not found then
        raise exception 'Grok QA dispatch does not exist'
            using errcode = 'P0002';
    end if;
    if dispatch.status is distinct from 'claimed'
       or dispatch.locked_by is distinct from target_worker_id
       or dispatch.lease_expires_at is null
       or dispatch.lease_expires_at <= statement_timestamp() then
        raise exception 'Grok QA dispatch lease is not owned by this worker'
            using errcode = '55000';
    end if;
    if dispatch.verdict is not null or exists (
        select 1 from private.grok_qa_verdict_receipts as receipt
        where receipt.workspace_id = target_workspace_id
          and receipt.content_version_id = target_content_version_id
    ) then
        raise exception 'Grok QA dispatch already crossed a later fence'
            using errcode = '55000';
    end if;

    -- This is the irreversible pre-provider-call fence. Revalidate under row
    -- locks in the same transaction that writes the fence so a stale Studio
    -- version, moved primary source, or disabled official feed cannot authorize
    -- xAI spend. FOR UPDATE on the content item also serializes new source-link
    -- inserts whose foreign-key check takes a key-share lock on this row. The
    -- exact link/source/feed rows use FOR SHARE (not KEY SHARE), because active,
    -- handle, canonical URL, and position are non-key fields that must not
    -- change between validation and the provider fence commit.
    item := null;
    select current_item.* into item
    from public.content_items as current_item
    where current_item.workspace_id = dispatch.workspace_id
      and current_item.id = dispatch.content_item_id
    for update;
    item_found := found;
    version := null;
    select current_version.* into version
    from public.content_versions as current_version
    where current_version.workspace_id = dispatch.workspace_id
      and current_version.content_item_id = dispatch.content_item_id
      and current_version.id = dispatch.content_version_id
    for share;
    banner_asset := null;
    if found
       and coalesce(
           version.deliverables ->> 'primary_asset_id', ''
       ) ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then
        select candidate.* into banner_asset
        from public.assets as candidate
        join storage.objects as stored
          on stored.bucket_id = candidate.storage_bucket
         and stored.name = candidate.storage_path
        where candidate.workspace_id = dispatch.workspace_id
          and candidate.content_item_id = dispatch.content_item_id
          and candidate.content_version_id = dispatch.content_version_id
          and candidate.id = (
              version.deliverables ->> 'primary_asset_id'
          )::uuid
          and version.deliverables -> 'asset_ids'
                = jsonb_build_array(candidate.id::text)
          and candidate.asset_kind = 'png'
          and candidate.storage_bucket = 'content-studio'
          and candidate.mime_type = 'image/png'
          and candidate.byte_size between 9 and 3000000
          and candidate.sha256 = target_banner_sha256
          and candidate.width between 1 and 10000
          and candidate.height between 1 and 10000
          and candidate.metadata ->> 'filename' = 'news-card.png'
          and candidate.storage_path = dispatch.workspace_id::text || '/'
                || dispatch.client_id || '/' || candidate.id::text
                || '/news-card.png'
        for share of candidate, stored;
    end if;
    expected_handle := case dispatch.client_id
        when 'yellow' then '@Yellow'
        when 'origintrail' then '@origin_trail'
        when 'squid' then '@SquidRouter'
        when 'babylon' then '@babylonlabs_io'
        else null
    end;
    -- Lock the entire source-link dependency set before counting eligible
    -- rows. The item FOR UPDATE above blocks new FK-linked rows, while these
    -- SHARE locks freeze every existing link and its source/feed eligibility.
    -- Counting first and locking only the selected row would leave a gap in
    -- which an existing secondary link or feed could become position-0/active.
    perform 1
    from public.content_source_links as link
    where link.workspace_id = dispatch.workspace_id
      and link.client_id = dispatch.client_id
      and link.content_item_id = dispatch.content_item_id
    order by link.source_item_id
    for share of link;
    perform 1
    from public.source_items as source
    join public.content_source_links as link
      on link.workspace_id = source.workspace_id
     and link.client_id = source.client_id
     and link.source_item_id = source.id
    where link.workspace_id = dispatch.workspace_id
      and link.client_id = dispatch.client_id
      and link.content_item_id = dispatch.content_item_id
    order by source.id
    for share of source;
    perform 1
    from public.source_feeds as feed
    join public.source_items as source
      on source.workspace_id = feed.workspace_id
     and source.client_id = feed.client_id
     and source.source_feed_id = feed.id
    join public.content_source_links as link
      on link.workspace_id = source.workspace_id
     and link.client_id = source.client_id
     and link.source_item_id = source.id
    where link.workspace_id = dispatch.workspace_id
      and link.client_id = dispatch.client_id
      and link.content_item_id = dispatch.content_item_id
    order by feed.id
    for share of feed;
    primary_source := null;
    select count(*) into primary_source_count
    from public.content_source_links as link
    join public.source_items as source
      on source.workspace_id = link.workspace_id
     and source.client_id = link.client_id
     and source.id = link.source_item_id
    join public.source_feeds as feed
      on feed.workspace_id = source.workspace_id
     and feed.client_id = source.client_id
     and feed.id = source.source_feed_id
    where link.workspace_id = dispatch.workspace_id
      and link.client_id = dispatch.client_id
      and link.content_item_id = dispatch.content_item_id
      and link.position = 0
      and source.source_type = 'tweet'
      and feed.provider = 'x'
      and feed.handle = expected_handle
      and feed.active is true;
    if primary_source_count = 1 then
        select source.* into primary_source
        from public.content_source_links as link
        join public.source_items as source
          on source.workspace_id = link.workspace_id
         and source.client_id = link.client_id
         and source.id = link.source_item_id
        join public.source_feeds as feed
          on feed.workspace_id = source.workspace_id
         and feed.client_id = source.client_id
         and feed.id = source.source_feed_id
        where link.workspace_id = dispatch.workspace_id
          and link.client_id = dispatch.client_id
          and link.content_item_id = dispatch.content_item_id
          and link.position = 0
          and source.source_type = 'tweet'
          and feed.provider = 'x'
          and feed.handle = expected_handle
          and feed.active is true
        for share of link, source, feed;
    end if;
    if not item_found
       or item.status is distinct from 'needs_review'
       or item.current_version_id is distinct from dispatch.content_version_id
       or item.client_id is distinct from dispatch.client_id
       or item.content_kind is distinct from 'daily_news'
       or dispatch.content_kind is distinct from 'daily_news'
       or version.id is null
       or banner_asset.id is null
       or primary_source_count <> 1
       or primary_source.id is distinct from dispatch.source_item_id
       or primary_source.author_handle is distinct from expected_handle
       or primary_source.author_handle
            is distinct from dispatch.source_author_handle
       or primary_source.canonical_url is distinct from dispatch.source_url
       or primary_source.canonical_url !~ (
           '^https://x\.' || 'com/' ||
           substring(expected_handle from 2) || '/status/[0-9]{1,19}$'
       )
       or primary_source.external_id is distinct from
            split_part(primary_source.canonical_url, '/', 6)
       or primary_source.published_at
            is distinct from dispatch.source_published_at then
        update private.grok_qa_dispatch_outbox
        set status = 'obsolete',
            locked_by = null,
            locked_at = null,
            lease_expires_at = null,
            completed_at = statement_timestamp(),
            updated_at = statement_timestamp()
        where workspace_id = dispatch.workspace_id
          and content_version_id = dispatch.content_version_id
        returning * into dispatch;
        return jsonb_build_object(
            'schema_version', '1.0',
            'content_item_id', dispatch.content_item_id,
            'content_version_id', dispatch.content_version_id,
            'input_sha256', null,
            'banner_sha256', null,
            'provider_attempt_started_at', null,
            'authorized_once', false
        );
    end if;

    if dispatch.provider_input_sha256 is not null then
        if dispatch.provider_input_sha256 is distinct from target_input_sha256
           or dispatch.banner_sha256 is distinct from target_banner_sha256 then
            raise exception 'Grok QA provider attempt input conflicts'
                using errcode = '23505';
        end if;
    else
        update private.grok_qa_dispatch_outbox
        set provider_input_sha256 = target_input_sha256,
            banner_sha256 = target_banner_sha256,
            provider_attempt_started_at = statement_timestamp(),
            updated_at = statement_timestamp()
        where workspace_id = dispatch.workspace_id
          and content_version_id = dispatch.content_version_id
        returning * into dispatch;
        authorized_once := true;
    end if;

    return jsonb_build_object(
        'schema_version', '1.0',
        'content_item_id', dispatch.content_item_id,
        'content_version_id', dispatch.content_version_id,
        'input_sha256', dispatch.provider_input_sha256,
        'banner_sha256', dispatch.banner_sha256,
        'provider_attempt_started_at', dispatch.provider_attempt_started_at,
        'authorized_once', authorized_once
    );
end;
$$;

create or replace function public.stage_grok_qa_dispatch_verdict(
    target_workspace_id uuid,
    target_content_version_id uuid,
    target_worker_id text,
    target_verdict jsonb,
    target_model text,
    target_prompt_version text,
    target_provider_response_id text,
    target_input_sha256 text,
    target_banner_sha256 text,
    target_cost_in_usd_ticks bigint,
    target_x_search_citations jsonb,
    target_x_search_calls smallint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    dispatch private.grok_qa_dispatch_outbox%rowtype;
    verdict_hash text;
    reused boolean := false;
begin
    if target_workspace_id is null
       or target_content_version_id is null
       or coalesce(target_worker_id, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or target_model is distinct from 'grok-4.5'
       or target_prompt_version is distinct from 'official-x-grok-qa@1'
       or coalesce(target_provider_response_id, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$'
       or target_provider_response_id is distinct from
            btrim(target_provider_response_id)
       or coalesce(target_input_sha256, '') !~ '^[a-f0-9]{64}$'
       or coalesce(target_banner_sha256, '') !~ '^[a-f0-9]{64}$'
       or target_cost_in_usd_ticks is null
       or target_cost_in_usd_ticks not between 0 and 5000000000
       or target_x_search_calls is null
       or target_x_search_calls not between 1 and 3
       or not private.grok_qa_dispatch_verdict_valid(target_verdict) then
        raise exception 'Grok QA dispatch verdict is invalid'
            using errcode = '22023';
    end if;
    verdict_hash := encode(extensions.digest(
        convert_to(target_verdict::text, 'UTF8'), 'sha256'
    ), 'hex');

    select queued.* into dispatch
    from private.grok_qa_dispatch_outbox as queued
    where queued.workspace_id = target_workspace_id
      and queued.content_version_id = target_content_version_id
    for update;
    if not found then
        raise exception 'Grok QA dispatch does not exist'
            using errcode = 'P0002';
    end if;
    if dispatch.status is distinct from 'claimed'
       or dispatch.locked_by is distinct from target_worker_id
       or dispatch.lease_expires_at is null
       or dispatch.lease_expires_at <= statement_timestamp() then
        raise exception 'Grok QA dispatch lease is not owned by this worker'
            using errcode = '55000';
    end if;
    if dispatch.provider_attempt_started_at is null
       or dispatch.provider_input_sha256
            is distinct from target_input_sha256
       or dispatch.banner_sha256
            is distinct from target_banner_sha256 then
        raise exception 'Grok QA dispatch lacks its provider attempt fence'
            using errcode = '55000';
    end if;
    if not private.grok_qa_dispatch_citations_valid(
        target_x_search_citations, dispatch.source_url
    ) then
        raise exception 'Grok QA x_search citations are invalid'
            using errcode = '22023';
    end if;
    if (
        target_verdict ->> 'decision' = 'PASS'
        and target_verdict -> 'fact_check' -> 'source_urls'
            is distinct from jsonb_build_array(dispatch.source_url)
    ) or (
        target_verdict ->> 'decision' <> 'PASS'
        and target_verdict -> 'fact_check' -> 'source_urls'
            not in ('[]'::jsonb, jsonb_build_array(dispatch.source_url))
    ) or exists (
        select 1
        from jsonb_array_elements(target_verdict -> 'issues') as issue(value)
        where issue.value ? 'evidence_url'
          and issue.value ->> 'evidence_url'
                is distinct from dispatch.source_url
    ) then
        raise exception 'Grok QA verdict source evidence is invalid'
            using errcode = '22023';
    end if;
    if dispatch.verdict is not null then
        if dispatch.verdict is distinct from target_verdict
           or dispatch.verdict_sha256 is distinct from verdict_hash
           or dispatch.model is distinct from target_model
           or dispatch.prompt_version is distinct from target_prompt_version
           or dispatch.provider_response_id
                is distinct from target_provider_response_id
           or dispatch.provider_input_sha256
                is distinct from target_input_sha256
           or dispatch.banner_sha256
                is distinct from target_banner_sha256
           or dispatch.cost_in_usd_ticks
                is distinct from target_cost_in_usd_ticks
           or dispatch.x_search_citations
                is distinct from target_x_search_citations
           or dispatch.x_search_calls
                is distinct from target_x_search_calls then
            raise exception 'Grok QA staged verdict conflicts'
                using errcode = '23505';
        end if;
        reused := true;
    else
        update private.grok_qa_dispatch_outbox
        set verdict = target_verdict,
            verdict_sha256 = verdict_hash,
            model = target_model,
            prompt_version = target_prompt_version,
            provider_response_id = target_provider_response_id,
            cost_in_usd_ticks = target_cost_in_usd_ticks,
            x_search_citations = target_x_search_citations,
            x_search_calls = target_x_search_calls,
            updated_at = statement_timestamp()
        where workspace_id = dispatch.workspace_id
          and content_version_id = dispatch.content_version_id
        returning * into dispatch;
    end if;

    return jsonb_build_object(
        'schema_version', '1.0',
        'content_item_id', dispatch.content_item_id,
        'content_version_id', dispatch.content_version_id,
        'status', dispatch.status,
        'verdict_sha256', dispatch.verdict_sha256,
        'model', dispatch.model,
        'prompt_version', dispatch.prompt_version,
        'provider_response_id', dispatch.provider_response_id,
        'input_sha256', dispatch.provider_input_sha256,
        'banner_sha256', dispatch.banner_sha256,
        'cost_in_usd_ticks', dispatch.cost_in_usd_ticks,
        'x_search_citations', dispatch.x_search_citations,
        'x_search_calls', dispatch.x_search_calls,
        'reused', reused
    );
end;
$$;

create or replace function public.complete_grok_qa_dispatch_job(
    target_workspace_id uuid,
    target_content_version_id uuid,
    target_worker_id text,
    target_verdict_sha256 text,
    target_outcome text,
    target_error_code text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    dispatch private.grok_qa_dispatch_outbox%rowtype;
    receipt private.grok_qa_verdict_receipts%rowtype;
    effective_status text;
    effective_error_code text;
    receipt_found boolean := false;
    reused boolean := false;
begin
    if target_workspace_id is null
       or target_content_version_id is null
       or coalesce(target_worker_id, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or coalesce(target_verdict_sha256, '') !~ '^[a-f0-9]{64}$'
       or target_outcome not in ('sent', 'failed', 'delivery_unknown')
       or (target_outcome = 'sent' and target_error_code is not null)
       or (target_outcome <> 'sent' and coalesce(target_error_code, '')
            !~ '^[a-z][a-z0-9_]{0,79}$') then
        raise exception 'Grok QA dispatch completion is invalid'
            using errcode = '22023';
    end if;

    select queued.* into dispatch
    from private.grok_qa_dispatch_outbox as queued
    where queued.workspace_id = target_workspace_id
      and queued.content_version_id = target_content_version_id
    for update;
    if not found then
        raise exception 'Grok QA dispatch does not exist'
            using errcode = 'P0002';
    end if;
    if dispatch.status in ('sent', 'failed', 'delivery_unknown') then
        if dispatch.verdict_sha256 is distinct from target_verdict_sha256
           or (
               target_outcome <> 'delivery_unknown'
               and (
                   dispatch.status is distinct from target_outcome
               )
           ) then
            raise exception 'Grok QA dispatch completion conflicts'
                using errcode = '23505';
        end if;
        reused := true;
        return jsonb_build_object(
            'schema_version', '1.0',
            'content_item_id', dispatch.content_item_id,
            'content_version_id', dispatch.content_version_id,
            'status', dispatch.status,
            'reused', reused
        );
    end if;
    if dispatch.status is distinct from 'claimed'
       or dispatch.locked_by is distinct from target_worker_id
       or dispatch.lease_expires_at is null
       or dispatch.lease_expires_at <= statement_timestamp()
       or dispatch.verdict is null
       or dispatch.verdict_sha256 is distinct from target_verdict_sha256
       or dispatch.model is distinct from 'grok-4.5'
       or dispatch.prompt_version
            is distinct from 'official-x-grok-qa@1'
       or dispatch.provider_input_sha256 is null
       or dispatch.banner_sha256 is null
       or dispatch.provider_attempt_started_at is null
       or dispatch.provider_response_id is null
       or dispatch.cost_in_usd_ticks is null
       or dispatch.x_search_citations is null
       or dispatch.x_search_calls is null then
        raise exception 'Grok QA dispatch completion lacks its staged lease'
            using errcode = '55000';
    end if;

    select current_receipt.* into receipt
    from private.grok_qa_verdict_receipts as current_receipt
    where current_receipt.workspace_id = target_workspace_id
      and current_receipt.content_version_id = target_content_version_id;
    receipt_found := found;
    if receipt_found
       and receipt.payload_sha256 is distinct from target_verdict_sha256 then
        raise exception 'Grok QA dispatch receipt payload conflicts'
            using errcode = '23505';
    end if;

    if target_outcome = 'sent' then
        if not receipt_found or receipt.status is distinct from 'sent' then
            raise exception 'Grok QA dispatch has no sent receipt'
                using errcode = '23514';
        end if;
        effective_status := 'sent';
        effective_error_code := null;
    elsif target_outcome = 'failed' then
        if not receipt_found or receipt.status is distinct from 'failed' then
            raise exception 'Grok QA dispatch has no failed receipt'
                using errcode = '23514';
        end if;
        effective_status := 'failed';
        effective_error_code := coalesce(
            receipt.failure_code, target_error_code
        );
    else
        if receipt_found and receipt.status = 'sent' then
            effective_status := 'sent';
            effective_error_code := null;
        elsif receipt_found and receipt.status = 'failed' then
            effective_status := 'failed';
            effective_error_code := coalesce(
                receipt.failure_code, target_error_code
            );
        else
            effective_status := 'delivery_unknown';
            effective_error_code := target_error_code;
        end if;
    end if;

    update private.grok_qa_dispatch_outbox
    set status = effective_status,
        error_code = effective_error_code,
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        completed_at = statement_timestamp(),
        updated_at = statement_timestamp()
    where workspace_id = dispatch.workspace_id
      and content_version_id = dispatch.content_version_id
    returning * into dispatch;

    return jsonb_build_object(
        'schema_version', '1.0',
        'content_item_id', dispatch.content_item_id,
        'content_version_id', dispatch.content_version_id,
        'status', dispatch.status,
        'reused', reused
    );
end;
$$;

create or replace function public.fail_grok_qa_dispatch_job(
    target_workspace_id uuid,
    target_content_version_id uuid,
    target_worker_id text,
    target_error_code text,
    target_retryable boolean,
    target_retry_at timestamptz
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    dispatch private.grok_qa_dispatch_outbox%rowtype;
    item public.content_items%rowtype;
    next_status text;
    effective_retry_at timestamptz;
begin
    if target_workspace_id is null
       or target_content_version_id is null
       or coalesce(target_worker_id, '')
            !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
       or coalesce(target_error_code, '') !~ '^[a-z][a-z0-9_]{0,79}$'
       or target_retryable is null
       or (target_retry_at is not null and (
           target_retry_at < statement_timestamp()
           or target_retry_at > statement_timestamp() + interval '24 hours'
       )) then
        raise exception 'Grok QA dispatch failure is invalid'
            using errcode = '22023';
    end if;

    select queued.* into dispatch
    from private.grok_qa_dispatch_outbox as queued
    where queued.workspace_id = target_workspace_id
      and queued.content_version_id = target_content_version_id
    for update;
    if not found then
        raise exception 'Grok QA dispatch does not exist'
            using errcode = 'P0002';
    end if;
    if dispatch.status is distinct from 'claimed'
       or dispatch.locked_by is distinct from target_worker_id
       or dispatch.lease_expires_at is null
       or dispatch.lease_expires_at <= statement_timestamp() then
        raise exception 'Grok QA dispatch lease is not owned by this worker'
            using errcode = '55000';
    end if;
    if dispatch.verdict is not null or exists (
        select 1 from private.grok_qa_verdict_receipts as receipt
        where receipt.workspace_id = target_workspace_id
          and receipt.content_version_id = target_content_version_id
    ) then
        raise exception 'Grok QA dispatch attempt already crossed the verdict fence'
            using errcode = '55000';
    end if;

    select current_item.* into item
    from public.content_items as current_item
    where current_item.workspace_id = dispatch.workspace_id
      and current_item.id = dispatch.content_item_id
    for key share;
    if dispatch.provider_attempt_started_at is not null then
        -- xAI Responses does not expose a documented create-idempotency key.
        -- Once the durable pre-call fence is crossed, no code path may put
        -- this version back in pending and risk a duplicate provider call.
        next_status := 'provider_unknown';
        effective_retry_at := statement_timestamp();
    elsif not found
       or item.status is distinct from 'needs_review'
       or item.current_version_id is distinct from dispatch.content_version_id then
        next_status := 'obsolete';
        effective_retry_at := statement_timestamp();
    elsif target_retryable and dispatch.attempts < dispatch.max_attempts then
        next_status := 'pending';
        effective_retry_at := coalesce(
            target_retry_at,
            statement_timestamp() + case
                when dispatch.attempts <= 1 then interval '1 minute'
                else interval '5 minutes'
            end
        );
    else
        next_status := 'failed';
        effective_retry_at := statement_timestamp();
    end if;

    update private.grok_qa_dispatch_outbox
    set status = next_status,
        available_at = effective_retry_at,
        error_code = case when next_status in ('failed', 'provider_unknown')
                          then target_error_code else null end,
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        completed_at = case when next_status in (
                                'obsolete', 'failed', 'provider_unknown'
                            )
                            then statement_timestamp() else null end,
        updated_at = statement_timestamp()
    where workspace_id = dispatch.workspace_id
      and content_version_id = dispatch.content_version_id
    returning * into dispatch;

    return jsonb_build_object(
        'schema_version', '1.0',
        'content_item_id', dispatch.content_item_id,
        'content_version_id', dispatch.content_version_id,
        'status', dispatch.status,
        'attempts', dispatch.attempts,
        'max_attempts', dispatch.max_attempts,
        'available_at', dispatch.available_at,
        'reused', false
    );
end;
$$;

create or replace function public.reconcile_grok_qa_dispatch_leases(
    target_workspace_id uuid,
    target_limit integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    dispatch private.grok_qa_dispatch_outbox%rowtype;
    item public.content_items%rowtype;
    receipt private.grok_qa_verdict_receipts%rowtype;
    next_status text;
    next_error_code text;
    item_found boolean;
    receipt_found boolean;
    reconciled_count integer := 0;
    pending_count integer := 0;
    sent_count integer := 0;
    failed_count integer := 0;
    obsolete_count integer := 0;
    provider_unknown_count integer := 0;
    unknown_count integer := 0;
begin
    if target_workspace_id is null or target_limit not between 1 and 100 then
        raise exception 'Grok QA dispatch reconciliation is invalid'
            using errcode = '22023';
    end if;

    for dispatch in
        select expired.*
        from private.grok_qa_dispatch_outbox as expired
        where expired.workspace_id = target_workspace_id
          and expired.status = 'claimed'
          and expired.lease_expires_at <= statement_timestamp()
        order by expired.lease_expires_at, expired.content_version_id
        limit target_limit
        for update skip locked
    loop
        select current_item.* into item
        from public.content_items as current_item
        where current_item.workspace_id = dispatch.workspace_id
          and current_item.id = dispatch.content_item_id
        for key share;
        item_found := found;
        select current_receipt.* into receipt
        from private.grok_qa_verdict_receipts as current_receipt
        where current_receipt.workspace_id = dispatch.workspace_id
          and current_receipt.content_version_id = dispatch.content_version_id;
        receipt_found := found;

        if receipt_found
           and dispatch.verdict is not null
           and receipt.payload_sha256 is distinct from
                dispatch.verdict_sha256 then
            next_status := 'delivery_unknown';
            next_error_code := 'grok_qa_receipt_payload_conflict';
        elsif receipt_found
           and dispatch.verdict is null
           and dispatch.provider_attempt_started_at is not null then
            -- A provider call was authorized, but its immutable evidence was
            -- never staged. A later relay receipt cannot reconstruct it.
            next_status := 'provider_unknown';
            next_error_code := 'grok_qa_provider_evidence_missing';
        elsif receipt_found then
            next_status := case receipt.status
                when 'sent' then 'sent'
                when 'failed' then 'failed'
                else 'delivery_unknown'
            end;
            next_error_code := case receipt.status
                when 'sent' then null
                when 'failed' then coalesce(
                    receipt.failure_code, 'grok_qa_receipt_failed'
                )
                else 'grok_qa_receipt_claimed'
            end;
        elsif dispatch.verdict is not null then
            -- The immutable provider result is durable and no Telegram receipt
            -- exists, so delivery has not crossed its own submit fence. Make
            -- the exact stored result claimable again without another provider
            -- authorization or attempt increment.
            next_status := 'staged';
            next_error_code := null;
        elsif dispatch.provider_attempt_started_at is not null then
            -- The provider attempt fence is irreversible. An expired lease
            -- without staged evidence is not safe to retry automatically.
            next_status := 'provider_unknown';
            next_error_code := 'grok_qa_provider_state_unknown';
        elsif not item_found
           or item.status is distinct from 'needs_review'
           or item.current_version_id is distinct from dispatch.content_version_id then
            next_status := 'obsolete';
            next_error_code := null;
        elsif dispatch.attempts < dispatch.max_attempts then
            next_status := 'pending';
            next_error_code := null;
        else
            next_status := 'failed';
            next_error_code := 'grok_qa_dispatch_attempts_exhausted';
        end if;

        update private.grok_qa_dispatch_outbox
        set status = next_status,
            verdict = case
                when receipt_found
                     and dispatch.verdict is null
                     and dispatch.provider_attempt_started_at is null
                then receipt.payload
                else verdict
            end,
            verdict_sha256 = case
                when receipt_found
                     and dispatch.verdict is null
                     and dispatch.provider_attempt_started_at is null
                then receipt.payload_sha256
                else verdict_sha256
            end,
            model = case
                when receipt_found
                     and dispatch.verdict is null
                     and dispatch.provider_attempt_started_at is null
                then null
                else model
            end,
            prompt_version = case
                when receipt_found
                     and dispatch.verdict is null
                     and dispatch.provider_attempt_started_at is null
                then 'grok-qa-external-receipt@1'
                else prompt_version
            end,
            available_at = case
                when next_status = 'pending'
                    then statement_timestamp() + interval '1 minute'
                when next_status = 'staged'
                    then statement_timestamp()
                else available_at
            end,
            error_code = next_error_code,
            locked_by = null,
            locked_at = null,
            lease_expires_at = null,
            completed_at = case when next_status in ('pending', 'staged')
                then null else statement_timestamp()
            end,
            updated_at = statement_timestamp()
        where workspace_id = dispatch.workspace_id
          and content_version_id = dispatch.content_version_id;

        reconciled_count := reconciled_count + 1;
        pending_count := pending_count
            + (next_status in ('pending', 'staged'))::integer;
        sent_count := sent_count + (next_status = 'sent')::integer;
        failed_count := failed_count + (next_status = 'failed')::integer;
        obsolete_count := obsolete_count + (next_status = 'obsolete')::integer;
        provider_unknown_count := provider_unknown_count
            + (next_status = 'provider_unknown')::integer;
        unknown_count := unknown_count
            + (next_status = 'delivery_unknown')::integer;
    end loop;

    return jsonb_build_object(
        'schema_version', '1.0',
        'workspace_id', target_workspace_id,
        'reconciled', reconciled_count,
        'pending', pending_count,
        'sent', sent_count,
        'failed', failed_count,
        'obsolete', obsolete_count,
        'provider_unknown', provider_unknown_count,
        'delivery_unknown', unknown_count
    );
end;
$$;

revoke all on function private.grok_qa_dispatch_verdict_valid(jsonb)
from public, anon, authenticated, service_role;
revoke all on function private.grok_qa_dispatch_citations_valid(jsonb, text)
from public, anon, authenticated, service_role;
revoke all on function private.grok_qa_dispatch_object(uuid, uuid, boolean)
from public, anon, authenticated, service_role;
revoke all on function private.enqueue_official_x_grok_qa_dispatch()
from public, anon, authenticated, service_role;

revoke all on function public.claim_grok_qa_dispatch_job(
    uuid, text, integer, text[], uuid
)
from public, anon, authenticated, service_role;
revoke all on function public.mark_grok_qa_dispatch_provider_attempt(
    uuid, uuid, text, text, text
) from public, anon, authenticated, service_role;
revoke all on function public.stage_grok_qa_dispatch_verdict(
    uuid, uuid, text, jsonb, text, text, text, text, text, bigint, jsonb, smallint
) from public, anon, authenticated, service_role;
revoke all on function public.complete_grok_qa_dispatch_job(
    uuid, uuid, text, text, text, text
) from public, anon, authenticated, service_role;
revoke all on function public.fail_grok_qa_dispatch_job(
    uuid, uuid, text, text, boolean, timestamptz
) from public, anon, authenticated, service_role;
revoke all on function public.reconcile_grok_qa_dispatch_leases(uuid, integer)
from public, anon, authenticated, service_role;

grant execute on function public.claim_grok_qa_dispatch_job(
    uuid, text, integer, text[], uuid
)
to service_role;
grant execute on function public.mark_grok_qa_dispatch_provider_attempt(
    uuid, uuid, text, text, text
) to service_role;
grant execute on function public.stage_grok_qa_dispatch_verdict(
    uuid, uuid, text, jsonb, text, text, text, text, text, bigint, jsonb, smallint
) to service_role;
grant execute on function public.complete_grok_qa_dispatch_job(
    uuid, uuid, text, text, text, text
) to service_role;
grant execute on function public.fail_grok_qa_dispatch_job(
    uuid, uuid, text, text, boolean, timestamptz
) to service_role;
grant execute on function public.reconcile_grok_qa_dispatch_leases(uuid, integer)
to service_role;

commit;
