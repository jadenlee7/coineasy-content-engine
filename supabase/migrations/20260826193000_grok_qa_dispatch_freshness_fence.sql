-- Fail closed on stale official-X work during normal Grok QA FIFO claims.
-- Exact content-version canaries remain operator-scoped and may bypass only an
-- expired age fence. Future-dated source timestamps always fail closed.

begin;

drop function public.claim_grok_qa_dispatch_job(
    uuid, text, integer, text[], uuid
);

create index if not exists grok_qa_dispatch_fresh_claim_idx
    on private.grok_qa_dispatch_outbox (
        workspace_id, client_id, source_published_at desc,
        available_at, enqueued_at
    )
    where status in ('pending', 'staged', 'claimed');

create or replace function public.claim_grok_qa_dispatch_job(
    target_workspace_id uuid,
    target_worker_id text,
    target_lease_seconds integer,
    target_allowed_clients text[],
    target_max_source_age_seconds integer,
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
       or target_max_source_age_seconds is null
       or target_max_source_age_seconds not between 300 and 604800
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

    -- Retire a bounded stale prefix without claiming it or consuming an
    -- attempt. Pending work with no provider reservation becomes obsolete.
    -- Delivery-only work that already has a staged provider result reconciles
    -- an existing exact receipt; without a receipt it becomes a terminal
    -- failure so it can neither call the provider again nor relay stale
    -- content. An exact UUID canary skips this maintenance path and may
    -- deliberately re-open only one expired, unattempted obsolete row after
    -- all source and current-version checks below pass again.
    if target_canary_content_version_id is null then
        with stale_work as (
            select queued.workspace_id, queued.content_version_id,
                durable_receipt.content_version_id as receipt_content_version_id,
                durable_receipt.status as receipt_status,
                durable_receipt.failure_code as receipt_failure_code,
                durable_receipt.payload as receipt_payload,
                durable_receipt.payload_sha256 as receipt_payload_sha256
            from private.grok_qa_dispatch_outbox as queued
            left join private.grok_qa_verdict_receipts as durable_receipt
              on durable_receipt.workspace_id = queued.workspace_id
             and durable_receipt.content_version_id = queued.content_version_id
            where queued.workspace_id = target_workspace_id
              and queued.content_kind = 'daily_news'
              and queued.client_id = any(target_allowed_clients)
              and (
                  (
                      queued.status = 'pending'
                      and queued.available_at <= statement_timestamp()
                      and queued.attempts < queued.max_attempts
                      and queued.provider_attempt_started_at is null
                      and queued.verdict is null
                  )
                  or queued.status = 'staged'
                  or (
                      queued.status = 'claimed'
                      and queued.verdict is not null
                      and queued.lease_expires_at <= statement_timestamp()
                  )
              )
              and (
                  queued.source_published_at < statement_timestamp()
                      - make_interval(secs => target_max_source_age_seconds)
                  or queued.source_published_at > statement_timestamp()
                      + interval '5 minutes'
              )
            order by queued.source_published_at, queued.enqueued_at,
                queued.content_version_id
            limit 32
            for update of queued skip locked
        )
        update private.grok_qa_dispatch_outbox as queued
        set status = case
                when stale_work.receipt_content_version_id is not null then
                    case
                        when queued.verdict is not null
                            and stale_work.receipt_payload_sha256
                                is distinct from queued.verdict_sha256
                            then 'delivery_unknown'
                        when stale_work.receipt_status = 'sent' then 'sent'
                        when stale_work.receipt_status = 'failed' then 'failed'
                        else 'delivery_unknown'
                    end
                when queued.provider_attempt_started_at is null
                    and queued.verdict is null then 'obsolete'
                else 'failed'
            end,
            error_code = case
                when stale_work.receipt_content_version_id is not null then
                    case
                        when queued.verdict is not null
                            and stale_work.receipt_payload_sha256
                                is distinct from queued.verdict_sha256
                            then 'grok_qa_receipt_payload_conflict'
                        when stale_work.receipt_status = 'sent' then null
                        when stale_work.receipt_status = 'failed' then coalesce(
                            stale_work.receipt_failure_code,
                            'grok_qa_receipt_failed'
                        )
                        else 'grok_qa_receipt_claimed'
                    end
                when queued.provider_attempt_started_at is null
                    and queued.verdict is null then null
                else 'grok_qa_source_expired'
            end,
            verdict = case
                when queued.verdict is null
                    and stale_work.receipt_content_version_id is not null
                    then stale_work.receipt_payload
                else queued.verdict
            end,
            verdict_sha256 = case
                when queued.verdict is null
                    and stale_work.receipt_content_version_id is not null
                    then stale_work.receipt_payload_sha256
                else queued.verdict_sha256
            end,
            model = case
                when queued.verdict is null
                    and stale_work.receipt_content_version_id is not null
                    then null
                else queued.model
            end,
            prompt_version = case
                when queued.verdict is null
                    and stale_work.receipt_content_version_id is not null
                    then 'grok-qa-external-receipt@1'
                else queued.prompt_version
            end,
            locked_by = null,
            locked_at = null,
            lease_expires_at = null,
            completed_at = statement_timestamp(),
            updated_at = statement_timestamp()
        from stale_work
        where queued.workspace_id = stale_work.workspace_id
          and queued.content_version_id = stale_work.content_version_id;
    end if;

    -- Drain obsolete/terminal candidates iteratively. Recursing from a
    -- SECURITY DEFINER claim makes a long invalid prefix an unbounded stack and
    -- lets it evade one-call work limits. Thirty-two rows is deliberately
    -- bounded; a later worker tick resumes where this call stopped.
    while scan_count < 32 loop
        dispatch := null;
        select queued.* into dispatch
        from private.grok_qa_dispatch_outbox as queued
        where queued.workspace_id = target_workspace_id
          and queued.content_kind = 'daily_news'
          and queued.client_id = any(target_allowed_clients)
          and queued.source_published_at <= statement_timestamp()
              + interval '5 minutes'
          and (
              target_canary_content_version_id is not null
              or queued.source_published_at >= statement_timestamp()
                  - make_interval(secs => target_max_source_age_seconds)
          )
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
              or (
                  target_canary_content_version_id is not null
                  and queued.status = 'obsolete'
                  and queued.verdict is null
                  and queued.provider_attempt_started_at is null
                  and queued.attempts < queued.max_attempts
                  and queued.source_published_at < statement_timestamp()
                      - make_interval(secs => target_max_source_age_seconds)
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

revoke all on function public.claim_grok_qa_dispatch_job(
    uuid, text, integer, text[], integer, uuid
)
from public, anon, authenticated, service_role;

grant execute on function public.claim_grok_qa_dispatch_job(
    uuid, text, integer, text[], integer, uuid
)
to service_role;

notify pgrst, 'reload schema';

commit;
