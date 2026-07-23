-- Official-X intake and review-draft worker contract.
--
-- This migration deliberately stops at `needs_review`. It creates no
-- publication rows and gives the worker no publishing or Figma-export path.

begin;

-- One active X feed per workspace/client and one immutable copy of each X post.
create unique index source_feeds_active_x_client_idx
    on public.source_feeds (workspace_id, client_id)
    where provider = 'x' and active is true;

create unique index source_items_x_external_id_idx
    on public.source_items (workspace_id, client_id, source_feed_id, external_id);

-- Private receipts make polling exactly idempotent after an uncertain response.
create table private.official_x_poll_receipts (
    workspace_id uuid not null,
    client_id text not null,
    poll_request_id uuid not null,
    source_feed_id uuid not null,
    expected_cursor text,
    next_cursor text,
    payload_hash text not null check (payload_hash ~ '^[a-f0-9]{32}$'),
    source_item_ids uuid[] not null,
    inserted_count integer not null check (inserted_count >= 0),
    polled_at timestamptz not null,
    created_at timestamptz not null default now(),
    primary key (workspace_id, client_id, poll_request_id),
    foreign key (workspace_id, client_id, source_feed_id)
        references public.source_feeds(workspace_id, client_id, id) on delete restrict
);

-- This ledger closes the record-before-queue crash window. A later run can read
-- every official source that was committed but never reserved by a draft job.
create table private.official_x_source_state (
    workspace_id uuid not null,
    client_id text not null,
    source_item_id uuid primary key,
    queued_job_id uuid references public.jobs(id) on delete restrict,
    discovered_at timestamptz not null default now(),
    queued_at timestamptz,
    foreign key (workspace_id, client_id, source_item_id)
        references public.source_items(workspace_id, client_id, id) on delete restrict,
    check ((queued_job_id is null) = (queued_at is null))
);

create index official_x_source_state_pending_idx
    on private.official_x_source_state (workspace_id, client_id, discovered_at desc)
    where queued_job_id is null;

-- A row is both the per-client reservation and one of four global KST-day slots.
-- Unique constraints are the concurrency backstop; the queue RPC also takes a
-- transaction advisory lock so it can choose the smallest free slot atomically.
create table private.official_x_daily_slots (
    workspace_id uuid not null references public.workspaces(id) on delete restrict,
    kst_date date not null,
    client_id text not null,
    slot smallint not null check (slot between 1 and 4),
    job_id uuid not null unique references public.jobs(id) on delete restrict,
    created_at timestamptz not null default now(),
    primary key (workspace_id, kst_date, client_id),
    unique (workspace_id, kst_date, slot),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id) on delete restrict
);

revoke all on table
    private.official_x_poll_receipts,
    private.official_x_source_state,
    private.official_x_daily_slots
from public, anon, authenticated, service_role;

-- Existing cursor state is intentionally preserved during an idempotent deploy.
insert into public.source_feeds (
    workspace_id, client_id, provider, name, source_url, handle,
    credential_ref, poll_interval_minutes, active, created_by
)
select
    workspace.id,
    official.client_id,
    'x',
    official.display_name || ' official X',
    'https://x.com/' || ltrim(official.handle, '@'),
    official.handle,
    null,
    15,
    true,
    null
from public.workspaces as workspace
join public.workspace_clients as client
  on client.workspace_id = workspace.id
 and client.active is true
join (
    values
        ('yellow', 'Yellow', '@Yellow'),
        ('origintrail', 'OriginTrail', '@origin_trail'),
        ('squid', 'Squid', '@SquidRouter'),
        ('babylon', 'Babylon', '@babylonlabs_io')
) as official(client_id, display_name, handle)
  on official.client_id = client.client_id
where workspace.slug = 'coineasy-content-studio'
on conflict (workspace_id, client_id)
    where provider = 'x' and active is true
do update set
    name = excluded.name,
    source_url = excluded.source_url,
    handle = excluded.handle,
    poll_interval_minutes = excluded.poll_interval_minutes;

-- Only sanitized routing fields are returned. Arbitrary provider payloads stay
-- out of the worker response and cannot influence mode selection after a crash.
create or replace function private.official_x_pending_sources(
    target_workspace_id uuid,
    target_client_id text,
    target_limit integer
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce(jsonb_agg(p.payload order by p.sort_at desc, p.source_item_id desc), '[]'::jsonb)
    from (
        select
            source.id as source_item_id,
            coalesce(source.published_at, source.ingested_at) as sort_at,
            jsonb_build_object(
                'source_item_id', source.id,
                'external_id', source.external_id,
                'source_content', source.body,
                'source_url', source.canonical_url,
                'source_image_url', coalesce((
                    select media_item ->> 'url'
                    from jsonb_array_elements(source.media) as media(media_item)
                    where media_item ->> 'type' = 'photo'
                    order by media_item ->> 'media_key'
                    limit 1
                ), ''),
                'published_at', source.published_at,
                'media', source.media,
                'is_note_tweet', coalesce((source.raw_payload ->> 'is_note_tweet')::boolean, false),
                'metrics', coalesce(source.raw_payload -> 'metrics', '{}'::jsonb)
            ) as payload
        from private.official_x_source_state as state
        join public.source_items as source
          on source.id = state.source_item_id
         and source.workspace_id = state.workspace_id
         and source.client_id = state.client_id
        where state.workspace_id = target_workspace_id
          and state.client_id = target_client_id
          and state.queued_job_id is null
        order by coalesce(source.published_at, source.ingested_at) desc, source.id desc
        limit greatest(1, least(coalesce(target_limit, 8), 20))
    ) as p
$$;

revoke all on function private.official_x_pending_sources(uuid, text, integer)
    from public, anon, authenticated, service_role;

create or replace function public.record_official_x_sources(
    target_workspace_id uuid,
    target_client_id text,
    target_handle text,
    target_poll_request_id uuid,
    target_expected_cursor text,
    target_next_cursor text,
    target_items jsonb,
    target_polled_at timestamptz default now()
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    feed public.source_feeds%rowtype;
    receipt private.official_x_poll_receipts%rowtype;
    expected_handle text;
    canonical_username text;
    normalized_expected_cursor text := nullif(btrim(target_expected_cursor), '');
    normalized_next_cursor text := nullif(btrim(target_next_cursor), '');
    request_payload_hash text;
    item jsonb;
    media_item jsonb;
    item_external_id text;
    body text;
    canonical_url text;
    published_at timestamptz;
    media jsonb;
    normalized_media jsonb;
    metrics jsonb;
    is_note_tweet boolean;
    expected_raw_payload jsonb;
    expected_source_hash text;
    committed_source public.source_items%rowtype;
    recorded_source_item_id uuid;
    source_item_ids uuid[] := '{}'::uuid[];
    inserted_count integer := 0;
    maximum_external_id numeric;
    pending_sources jsonb;
begin
    expected_handle := case target_client_id
        when 'yellow' then '@Yellow'
        when 'origintrail' then '@origin_trail'
        when 'squid' then '@SquidRouter'
        when 'babylon' then '@babylonlabs_io'
        else null
    end;
    if expected_handle is null
       or target_handle is null
       or ('@' || ltrim(btrim(target_handle), '@')) <> expected_handle then
        raise exception 'official X client or handle is invalid'
            using errcode = '22023';
    end if;
    if target_poll_request_id is null then
        raise exception 'poll request id is required'
            using errcode = '22023';
    end if;
    if jsonb_typeof(target_items) is distinct from 'array'
       or jsonb_array_length(target_items) > 100
       or octet_length(target_items::text) > 1048576 then
        raise exception 'official X items must be an array of at most 100 items and 1MB'
            using errcode = '22023';
    end if;
    if normalized_expected_cursor is not null
       and normalized_expected_cursor !~ '^[0-9]{1,19}$' then
        raise exception 'expected X cursor is invalid'
            using errcode = '22023';
    end if;
    if normalized_next_cursor is not null
       and normalized_next_cursor !~ '^[0-9]{1,19}$' then
        raise exception 'next X cursor is invalid'
            using errcode = '22023';
    end if;

    canonical_username := ltrim(expected_handle, '@');
    request_payload_hash := pg_catalog.md5(target_items::text);

    select source_feed.* into feed
    from public.source_feeds as source_feed
    where source_feed.workspace_id = target_workspace_id
      and source_feed.client_id = target_client_id
      and source_feed.provider = 'x'
      and source_feed.handle = expected_handle
      and source_feed.active is true
    for update;
    if not found then
        raise exception 'official X feed is not active'
            using errcode = '23514';
    end if;

    select poll.* into receipt
    from private.official_x_poll_receipts as poll
    where poll.workspace_id = target_workspace_id
      and poll.client_id = target_client_id
      and poll.poll_request_id = target_poll_request_id;
    if found then
        if receipt.source_feed_id <> feed.id
           or receipt.expected_cursor is distinct from normalized_expected_cursor
           or receipt.next_cursor is distinct from normalized_next_cursor
           or receipt.payload_hash <> request_payload_hash then
            raise exception 'poll request retry does not match the committed intake'
                using errcode = '23505';
        end if;
        pending_sources := private.official_x_pending_sources(
            target_workspace_id, target_client_id, 20
        );
        return jsonb_build_object(
            'poll_request_id', target_poll_request_id,
            'source_feed_id', feed.id,
            'source_item_ids', to_jsonb(receipt.source_item_ids),
            'inserted_count', receipt.inserted_count,
            'last_cursor', receipt.next_cursor,
            'last_polled_at', receipt.polled_at,
            'draft_reserved_today', exists (
                select 1 from private.official_x_daily_slots as slot
                where slot.workspace_id = target_workspace_id
                  and slot.client_id = target_client_id
                  and slot.kst_date = pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date
            ),
            'pending_sources', pending_sources,
            'reused', true
        );
    end if;

    if target_polled_at is null
       or target_polled_at < statement_timestamp() - interval '24 hours'
       or target_polled_at > statement_timestamp() + interval '5 minutes' then
        raise exception 'official X poll time is outside the accepted window'
            using errcode = '22023';
    end if;
    if feed.last_cursor is distinct from normalized_expected_cursor then
        raise exception 'official X cursor changed before intake commit'
            using errcode = '40001';
    end if;
    if normalized_next_cursor is not null
       and normalized_expected_cursor is not null
       and normalized_next_cursor::numeric < normalized_expected_cursor::numeric then
        raise exception 'next X cursor cannot move backwards'
            using errcode = '22023';
    end if;
    if jsonb_array_length(target_items) = 0
       and normalized_next_cursor is distinct from normalized_expected_cursor then
        raise exception 'an empty X poll cannot advance the cursor'
            using errcode = '22023';
    end if;

    for item in select value from jsonb_array_elements(target_items)
    loop
        if jsonb_typeof(item) is distinct from 'object' then
            raise exception 'every official X item must be an object'
                using errcode = '22023';
        end if;
        item_external_id := coalesce(item ->> 'external_id', item ->> 'id');
        body := coalesce(item ->> 'body', item ->> 'text', item ->> 'source_content');
        canonical_url := coalesce(
            item ->> 'canonical_url', item ->> 'url', item ->> 'source_url'
        );
        if item_external_id is null or item_external_id !~ '^[0-9]{1,19}$' then
            raise exception 'official X item id is invalid'
                using errcode = '22023';
        end if;
        if body is null or char_length(btrim(body)) not between 1 and 60000 then
            raise exception 'official X item text must be 1 to 60000 characters'
                using errcode = '22023';
        end if;
        if canonical_url is distinct from
           'https://x.com/' || canonical_username || '/status/' || item_external_id then
            raise exception 'official X item URL does not match its feed'
                using errcode = '22023';
        end if;
        if item @> '{"is_retweet":true}'::jsonb
           or item @> '{"is_reply":true}'::jsonb then
            raise exception 'replies and retweets are not official draft sources'
                using errcode = '22023';
        end if;
        begin
            published_at := coalesce(item ->> 'published_at', item ->> 'created_at')::timestamptz;
        exception when others then
            raise exception 'official X item published_at is invalid'
                using errcode = '22023';
        end;
        if published_at is null
           or published_at < statement_timestamp() - interval '30 days'
           or published_at > statement_timestamp() + interval '5 minutes' then
            raise exception 'official X item published_at is outside the accepted window'
                using errcode = '22023';
        end if;
        media := coalesce(item -> 'media', '[]'::jsonb);
        if jsonb_typeof(media) is distinct from 'array'
           or jsonb_array_length(media) > 12
           or octet_length(media::text) > 65536 then
            raise exception 'official X media is invalid or too large'
                using errcode = '22023';
        end if;
        normalized_media := '[]'::jsonb;
        for media_item in select value from jsonb_array_elements(media)
        loop
            if jsonb_typeof(media_item) is distinct from 'object'
               or (media_item ->> 'type' in ('photo', 'video', 'animated_gif')) is not true
               or char_length(coalesce(media_item ->> 'url', '')) > 2048
               or not coalesce(
                   (media_item ->> 'url') ~ '^https://pbs\.twimg\.com/[A-Za-z0-9_./?=&%:+-]+$',
                   false
               ) then
                raise exception 'official X media entry is outside the allowlist'
                    using errcode = '22023';
            end if;
            if (media_item ? 'width' and not coalesce((media_item ->> 'width') ~ '^[0-9]{1,5}$', false))
               or (media_item ? 'height' and not coalesce((media_item ->> 'height') ~ '^[0-9]{1,5}$', false)) then
                raise exception 'official X media dimensions are invalid'
                    using errcode = '22023';
            end if;
            if coalesce((media_item ->> 'width')::integer, 1) not between 1 and 20000
               or coalesce((media_item ->> 'height')::integer, 1) not between 1 and 20000 then
                raise exception 'official X media dimensions are invalid'
                    using errcode = '22023';
            end if;
            normalized_media := normalized_media || jsonb_build_array(
                jsonb_strip_nulls(jsonb_build_object(
                    'media_key', nullif(media_item ->> 'media_key', ''),
                    'type', media_item ->> 'type',
                    'url', media_item ->> 'url',
                    'width', case when media_item ? 'width'
                                  then (media_item ->> 'width')::integer else null end,
                    'height', case when media_item ? 'height'
                                   then (media_item ->> 'height')::integer else null end
                ))
            );
        end loop;
        metrics := coalesce(item -> 'metrics', '{}'::jsonb);
        if jsonb_typeof(metrics) is distinct from 'object'
           or octet_length(metrics::text) > 8192 then
            raise exception 'official X metrics are invalid or too large'
                using errcode = '22023';
        end if;
        if exists (
            select 1
            from jsonb_each(metrics) as metric(name, value)
            where name in ('like_count', 'retweet_count', 'reply_count', 'quote_count')
              and (
                  jsonb_typeof(value) <> 'number'
                  or value::text !~ '^[0-9]{1,10}$'
                  or value::text::bigint > 2147483647
              )
        ) then
            raise exception 'official X routing metrics are invalid'
                using errcode = '22023';
        end if;
        metrics := jsonb_strip_nulls(jsonb_build_object(
            'like_count', case when metrics ? 'like_count'
                               then (metrics ->> 'like_count')::integer else null end,
            'retweet_count', case when metrics ? 'retweet_count'
                                  then (metrics ->> 'retweet_count')::integer else null end,
            'reply_count', case when metrics ? 'reply_count'
                                then (metrics ->> 'reply_count')::integer else null end,
            'quote_count', case when metrics ? 'quote_count'
                                then (metrics ->> 'quote_count')::integer else null end
        ));
        if item ? 'is_note_tweet'
           and jsonb_typeof(item -> 'is_note_tweet') <> 'boolean' then
            raise exception 'official X note flag is invalid'
                using errcode = '22023';
        end if;
        is_note_tweet := coalesce((item ->> 'is_note_tweet')::boolean, false);
        expected_raw_payload := jsonb_build_object(
            'is_note_tweet', is_note_tweet,
            'metrics', metrics
        );
        expected_source_hash := pg_catalog.md5(
            'official-x:' || lower(expected_handle) || ':' || item_external_id
        );
        if exists (
            select 1
            from unnest(source_item_ids) as already_seen(existing_id)
            join public.source_items as existing_source on existing_source.id = already_seen.existing_id
            where existing_source.external_id = item_external_id
        ) then
            raise exception 'official X poll contains duplicate item ids'
                using errcode = '22023';
        end if;

        recorded_source_item_id := null;
        insert into public.source_items (
            workspace_id, client_id, source_feed_id, external_id, source_type,
            canonical_url, author_handle, published_at, body, media, raw_payload,
            source_hash, ingested_by
        ) values (
            target_workspace_id,
            target_client_id,
            feed.id,
            item_external_id,
            'tweet',
            canonical_url,
            expected_handle,
            published_at,
            btrim(body),
            normalized_media,
            expected_raw_payload,
            expected_source_hash,
            null
        )
        on conflict (workspace_id, client_id, source_feed_id, external_id)
        do nothing
        returning id into recorded_source_item_id;
        if recorded_source_item_id is null then
            select existing_source.* into committed_source
            from public.source_items as existing_source
            where existing_source.workspace_id = target_workspace_id
              and existing_source.client_id = target_client_id
              and existing_source.source_feed_id = feed.id
              and existing_source.external_id = item_external_id;
            if not found
               or committed_source.source_type is distinct from 'tweet'
               or committed_source.canonical_url is distinct from canonical_url
               or committed_source.author_handle is distinct from expected_handle
               or committed_source.published_at is distinct from published_at
               or committed_source.body is distinct from btrim(body)
               or committed_source.media is distinct from normalized_media
               or committed_source.raw_payload is distinct from expected_raw_payload
               or committed_source.source_hash is distinct from expected_source_hash
               or committed_source.ingested_by is not null then
                raise exception 'existing official X source does not match normalized intake'
                    using errcode = '23514';
            end if;
            recorded_source_item_id := committed_source.id;
        else
            inserted_count := inserted_count + 1;
        end if;

        insert into private.official_x_source_state (
            workspace_id, client_id, source_item_id
        ) values (
            target_workspace_id, target_client_id, recorded_source_item_id
        ) on conflict (source_item_id) do nothing;

        source_item_ids := array_append(source_item_ids, recorded_source_item_id);
        if maximum_external_id is null or item_external_id::numeric > maximum_external_id then
            maximum_external_id := item_external_id::numeric;
        end if;
    end loop;

    if maximum_external_id is not null
       and (normalized_next_cursor is null
            or normalized_next_cursor::numeric <> maximum_external_id) then
        raise exception 'next X cursor must equal the newest committed item id'
            using errcode = '22023';
    end if;

    update public.source_feeds
    set last_cursor = normalized_next_cursor,
        last_polled_at = target_polled_at
    where id = feed.id;

    insert into private.official_x_poll_receipts (
        workspace_id, client_id, poll_request_id, source_feed_id,
        expected_cursor, next_cursor, payload_hash, source_item_ids,
        inserted_count, polled_at
    ) values (
        target_workspace_id, target_client_id, target_poll_request_id, feed.id,
        normalized_expected_cursor, normalized_next_cursor, request_payload_hash,
        source_item_ids, inserted_count, target_polled_at
    );

    pending_sources := private.official_x_pending_sources(
        target_workspace_id, target_client_id, 20
    );
    return jsonb_build_object(
        'poll_request_id', target_poll_request_id,
        'source_feed_id', feed.id,
        'source_item_ids', to_jsonb(source_item_ids),
        'inserted_count', inserted_count,
        'last_cursor', normalized_next_cursor,
        'last_polled_at', target_polled_at,
        'draft_reserved_today', exists (
            select 1 from private.official_x_daily_slots as slot
            where slot.workspace_id = target_workspace_id
              and slot.client_id = target_client_id
              and slot.kst_date = pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date
        ),
        'pending_sources', pending_sources,
        'reused', false
    );
end;
$$;

create or replace function public.get_official_x_automation_state(
    target_workspace_id uuid,
    target_client_id text,
    target_kst_date date default null,
    target_pending_limit integer default 8
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    feed public.source_feeds%rowtype;
    reservation private.official_x_daily_slots%rowtype;
    expected_handle text;
    effective_date date := coalesce(
        target_kst_date,
        pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date
    );
    pending_sources jsonb;
    pending_count integer;
    reservation_status text;
begin
    expected_handle := case target_client_id
        when 'yellow' then '@Yellow'
        when 'origintrail' then '@origin_trail'
        when 'squid' then '@SquidRouter'
        when 'babylon' then '@babylonlabs_io'
        else null
    end;
    if expected_handle is null
       or effective_date <> pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date
       or target_pending_limit not between 1 and 20 then
        raise exception 'official X automation state request is invalid'
            using errcode = '22023';
    end if;
    select source_feed.* into feed
    from public.source_feeds as source_feed
    where source_feed.workspace_id = target_workspace_id
      and source_feed.client_id = target_client_id
      and source_feed.provider = 'x'
      and source_feed.handle = expected_handle
      and source_feed.active is true;
    if not found then
        raise exception 'official X feed is not active'
            using errcode = '23514';
    end if;
    select slot.* into reservation
    from private.official_x_daily_slots as slot
    where slot.workspace_id = target_workspace_id
      and slot.client_id = target_client_id
      and slot.kst_date = effective_date;
    if found then
        select job.status into reservation_status
        from public.jobs as job where job.id = reservation.job_id;
    end if;
    select count(*) into pending_count
    from private.official_x_source_state as state
    where state.workspace_id = target_workspace_id
      and state.client_id = target_client_id
      and state.queued_job_id is null;
    pending_sources := private.official_x_pending_sources(
        target_workspace_id, target_client_id, target_pending_limit
    );
    return jsonb_build_object(
        'client_id', target_client_id,
        'handle', expected_handle,
        'source_feed_id', feed.id,
        'last_cursor', feed.last_cursor,
        'last_polled_at', feed.last_polled_at,
        'kst_date', effective_date,
        'draft_reserved_today', reservation.job_id is not null,
        'reserved_job_id', reservation.job_id,
        'reserved_job_status', reservation_status,
        'pending_count', pending_count,
        'pending_sources', pending_sources
    );
end;
$$;

create or replace function public.queue_review_draft_job(
    target_workspace_id uuid,
    target_client_id text,
    target_kst_date date,
    target_source_item_ids jsonb,
    target_content_kind text,
    target_request_id uuid,
    target_source_content text,
    target_source_url text,
    target_source_image_url text default '',
    target_manual_only boolean default false
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    expected_handle text;
    source_item_ids uuid[];
    source_count integer;
    distinct_source_count integer;
    requested_input jsonb;
    existing_slot private.official_x_daily_slots%rowtype;
    existing_job public.jobs%rowtype;
    selected_slot smallint;
    new_job_id uuid := gen_random_uuid();
    normalized_source_content text := btrim(target_source_content);
    normalized_image_url text := coalesce(btrim(target_source_image_url), '');
begin
    expected_handle := case target_client_id
        when 'yellow' then '@Yellow'
        when 'origintrail' then '@origin_trail'
        when 'squid' then '@SquidRouter'
        when 'babylon' then '@babylonlabs_io'
        else null
    end;
    if expected_handle is null
       or target_kst_date is distinct from
          pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date
       or target_request_id is null
       or target_manual_only is null then
        raise exception 'review draft queue request is invalid'
            using errcode = '22023';
    end if;
    if target_content_kind is null
       or target_content_kind not in ('daily_news', 'article', 'tutorial')
       or (target_content_kind = 'tutorial' and target_manual_only is not true) then
        raise exception 'tutorials must remain manual-only review recommendations'
            using errcode = '22023';
    end if;
    if normalized_source_content is null
       or char_length(normalized_source_content) > 60000
       or (target_content_kind = 'article' and char_length(normalized_source_content) < 300)
       or (target_content_kind <> 'article' and char_length(normalized_source_content) < 10) then
        raise exception 'review draft source content length is invalid'
            using errcode = '22023';
    end if;
    if target_source_url is null
       or target_source_url !~ '^https://x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]{1,19}$'
       or char_length(target_source_url) > 200 then
        raise exception 'review draft source URL is invalid'
            using errcode = '22023';
    end if;
    if normalized_image_url <> ''
       and (char_length(normalized_image_url) > 2048
            or normalized_image_url !~ '^https://pbs\.twimg\.com/[A-Za-z0-9_./?=&%:+-]+$') then
        raise exception 'review draft source image URL is outside the allowlist'
            using errcode = '22023';
    end if;
    if jsonb_typeof(target_source_item_ids) is distinct from 'array'
       or jsonb_array_length(target_source_item_ids) not between 1 and 20
       or exists (
           select 1 from jsonb_array_elements_text(target_source_item_ids) as value(item_id)
           where item_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       ) then
        raise exception 'review draft source_item_ids are invalid'
            using errcode = '22023';
    end if;
    select
        array_agg(item_id::uuid order by ordinal),
        count(*),
        count(distinct item_id)
    into source_item_ids, source_count, distinct_source_count
    from jsonb_array_elements_text(target_source_item_ids)
         with ordinality as source_id(item_id, ordinal);
    if source_count <> distinct_source_count then
        raise exception 'review draft source_item_ids must be distinct'
            using errcode = '22023';
    end if;

    requested_input := jsonb_build_object(
        'workflow', 'official_x_review_draft_v1',
        'kst_date', target_kst_date,
        'source_item_ids', to_jsonb(source_item_ids),
        'content_kind', target_content_kind,
        'request_id', target_request_id,
        'source_content', normalized_source_content,
        'source_url', target_source_url,
        'source_image_url', normalized_image_url,
        'manual_only', target_manual_only
    );

    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        target_workspace_id::text || ':' || target_kst_date::text, 0
    ));
    select slot.* into existing_slot
    from private.official_x_daily_slots as slot
    where slot.workspace_id = target_workspace_id
      and slot.kst_date = target_kst_date
      and slot.client_id = target_client_id
    for update;
    if found then
        select job.* into existing_job from public.jobs as job
        where job.id = existing_slot.job_id;
        if existing_job.job_kind <> 'generate'
           or existing_job.input is distinct from requested_input then
            raise exception 'client already has a different KST-day draft reservation'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'job_id', existing_job.id,
            'status', existing_job.status,
            'slot', existing_slot.slot,
            'input', existing_job.input,
            'reused', true
        );
    end if;

    if exists (select 1 from public.content_items where id = target_request_id) then
        raise exception 'review draft request id already belongs to catalog content'
            using errcode = '23505';
    end if;
    if (
        select count(*)
        from public.source_items as source
        join public.source_feeds as feed
          on feed.id = source.source_feed_id
         and feed.workspace_id = source.workspace_id
         and feed.client_id = source.client_id
        where source.id = any(source_item_ids)
          and source.workspace_id = target_workspace_id
          and source.client_id = target_client_id
          and source.source_type = 'tweet'
          and feed.provider = 'x'
          and feed.handle = expected_handle
          and feed.active is true
    ) <> source_count then
        raise exception 'review draft sources are outside the official client feed'
            using errcode = '23514';
    end if;
    if not exists (
        select 1 from public.source_items as source
        where source.id = any(source_item_ids)
          and source.canonical_url = target_source_url
    ) then
        raise exception 'review draft source URL is not one of its pinned sources'
            using errcode = '23514';
    end if;
    if source_count = 1 and not exists (
        select 1 from public.source_items as source
        where source.id = source_item_ids[1]
          and source.body = normalized_source_content
    ) then
        raise exception 'review draft source content does not match its pinned source'
            using errcode = '23514';
    end if;
    if normalized_image_url <> '' and not exists (
        select 1
        from public.source_items as source
        cross join lateral jsonb_array_elements(source.media) as media(media_item)
        where source.id = any(source_item_ids)
          and media_item ->> 'url' = normalized_image_url
    ) then
        raise exception 'review draft image is not attached to a pinned source'
            using errcode = '23514';
    end if;

    insert into private.official_x_source_state (
        workspace_id, client_id, source_item_id
    )
    select target_workspace_id, target_client_id, source_id
    from unnest(source_item_ids) as source(source_id)
    on conflict (source_item_id) do nothing;
    if exists (
        select 1 from private.official_x_source_state as state
        where state.source_item_id = any(source_item_ids)
          and state.queued_job_id is not null
    ) then
        raise exception 'an official X source is already reserved by another draft'
            using errcode = '23505';
    end if;

    select free.slot::smallint into selected_slot
    from generate_series(1, 4) as free(slot)
    where not exists (
        select 1 from private.official_x_daily_slots as occupied
        where occupied.workspace_id = target_workspace_id
          and occupied.kst_date = target_kst_date
          and occupied.slot = free.slot
    )
    order by free.slot
    limit 1;
    if selected_slot is null then
        raise exception 'global KST-day review draft limit of four is reached'
            using errcode = 'P0001';
    end if;

    insert into public.jobs (
        id, workspace_id, client_id, job_kind, status, priority, input, output,
        idempotency_key, attempts, max_attempts, available_at
    ) values (
        new_job_id,
        target_workspace_id,
        target_client_id,
        'generate',
        'queued',
        0,
        requested_input,
        '{}'::jsonb,
        'official-x-review:v1:' || target_kst_date::text || ':' || target_client_id,
        0,
        3,
        statement_timestamp()
    );
    insert into private.official_x_daily_slots (
        workspace_id, kst_date, client_id, slot, job_id
    ) values (
        target_workspace_id, target_kst_date, target_client_id,
        selected_slot, new_job_id
    );
    update private.official_x_source_state
    set queued_job_id = new_job_id,
        queued_at = statement_timestamp()
    where source_item_id = any(source_item_ids);

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        target_workspace_id,
        'job',
        new_job_id,
        'official_x_review_draft_queued',
        jsonb_build_object(
            'client_id', target_client_id,
            'content_kind', target_content_kind,
            'kst_date', target_kst_date,
            'slot', selected_slot,
            'manual_only', target_manual_only
        )
    );
    return jsonb_build_object(
        'job_id', new_job_id,
        'status', 'queued',
        'slot', selected_slot,
        'input', requested_input,
        'reused', false
    );
end;
$$;

create or replace function public.claim_review_draft_job(
    target_workspace_id uuid,
    target_worker_id text,
    target_lease_seconds integer default 900
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    candidate public.jobs%rowtype;
begin
    if target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       or target_lease_seconds not between 60 and 3600 then
        raise exception 'review draft worker lease request is invalid'
            using errcode = '22023';
    end if;
    if not exists (select 1 from public.workspaces where id = target_workspace_id) then
        raise exception 'review draft workspace does not exist'
            using errcode = '23514';
    end if;

    update public.jobs as job
    set status = case when job.attempts >= job.max_attempts then 'failed' else 'retrying' end,
        available_at = statement_timestamp(),
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        last_error_code = 'lease_expired',
        last_error_message = 'The previous review-draft worker lease expired.',
        finished_at = case when job.attempts >= job.max_attempts
                           then statement_timestamp() else null end
    where job.workspace_id = target_workspace_id
      and job.job_kind = 'generate'
      and job.input ->> 'workflow' = 'official_x_review_draft_v1'
      and job.input -> 'manual_only' = 'false'::jsonb
      and job.status = 'running'
      and job.lease_expires_at <= statement_timestamp();

    select job.* into candidate
    from public.jobs as job
    where job.workspace_id = target_workspace_id
      and job.job_kind = 'generate'
      and job.input ->> 'workflow' = 'official_x_review_draft_v1'
      and job.input -> 'manual_only' = 'false'::jsonb
      and job.status in ('queued', 'retrying')
      and job.attempts < job.max_attempts
      and job.available_at <= statement_timestamp()
    order by job.priority desc, job.available_at, job.created_at, job.id
    for update skip locked
    limit 1;
    if not found then
        return null;
    end if;

    update public.jobs
    set status = 'running',
        attempts = attempts + 1,
        locked_by = target_worker_id,
        locked_at = statement_timestamp(),
        lease_expires_at = statement_timestamp() + make_interval(secs => target_lease_seconds),
        started_at = coalesce(started_at, statement_timestamp()),
        finished_at = null,
        last_error_code = null,
        last_error_message = null
    where id = candidate.id
    returning * into candidate;

    return jsonb_build_object(
        'job_id', candidate.id,
        'workspace_id', candidate.workspace_id,
        'client_id', candidate.client_id,
        'status', candidate.status,
        'attempts', candidate.attempts,
        'max_attempts', candidate.max_attempts,
        'locked_by', candidate.locked_by,
        'lease_expires_at', candidate.lease_expires_at,
        'input', candidate.input
    );
end;
$$;

create or replace function public.complete_review_draft_job(
    target_job_id uuid,
    target_worker_id text,
    target_content_item_id uuid,
    target_content_version_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    job public.jobs%rowtype;
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    source_item_ids uuid[];
    source_count integer;
    linked_count integer;
    expected_handle text;
begin
    if target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       or target_content_item_id is null
       or target_content_version_id is null then
        raise exception 'review draft completion request is invalid'
            using errcode = '22023';
    end if;
    select queued_job.* into job from public.jobs as queued_job
    where queued_job.id = target_job_id
    for update;
    if not found
       or job.job_kind <> 'generate'
       or job.input ->> 'workflow' is distinct from 'official_x_review_draft_v1' then
        raise exception 'review draft job does not exist'
            using errcode = '23514';
    end if;
    select array_agg(item_id::uuid order by ordinal), count(*)
    into source_item_ids, source_count
    from jsonb_array_elements_text(job.input -> 'source_item_ids')
         with ordinality as source_id(item_id, ordinal);
    if source_item_ids is null or source_count not between 1 and 20 then
        raise exception 'review draft job has invalid pinned source ids'
            using errcode = '23514';
    end if;
    expected_handle := case job.client_id
        when 'yellow' then '@Yellow'
        when 'origintrail' then '@origin_trail'
        when 'squid' then '@SquidRouter'
        when 'babylon' then '@babylonlabs_io'
        else null
    end;

    if job.status = 'succeeded' then
        if job.output ->> 'completed_by' <> target_worker_id
           or job.output ->> 'content_item_id' <> target_content_item_id::text
           or job.output ->> 'content_version_id' <> target_content_version_id::text
           or job.output -> 'source_item_ids' is distinct from to_jsonb(source_item_ids) then
            raise exception 'review draft completion retry does not match committed output'
                using errcode = '23505';
        end if;
        select count(*) into linked_count
        from public.content_source_links as link
        where link.content_item_id = target_content_item_id;
        if linked_count <> source_count or exists (
            select 1
            from unnest(source_item_ids) with ordinality as expected(source_id, ordinal)
            left join public.content_source_links as link
              on link.content_item_id = target_content_item_id
             and link.source_item_id = expected.source_id
             and link.position = (expected.ordinal - 1)::smallint
            where link.source_item_id is null
        ) then
            raise exception 'committed review draft source links are incomplete'
                using errcode = '23514';
        end if;
        return jsonb_build_object(
            'job_id', job.id,
            'status', job.status,
            'content_item_id', target_content_item_id,
            'content_version_id', target_content_version_id,
            'source_item_ids', to_jsonb(source_item_ids),
            'reused', true
        );
    end if;
    if job.status <> 'running'
       or job.locked_by is distinct from target_worker_id
       or job.lease_expires_at is null
       or job.lease_expires_at <= statement_timestamp() then
        raise exception 'review draft lease is not owned by this worker'
            using errcode = '55000';
    end if;
    if job.input ->> 'request_id' is distinct from target_content_item_id::text then
        raise exception 'generated content does not match the queued request id'
            using errcode = '23514';
    end if;
    select content.* into item from public.content_items as content
    where content.id = target_content_item_id
    for update;
    if not found
       or item.workspace_id <> job.workspace_id
       or item.client_id <> job.client_id
       or item.content_kind is distinct from job.input ->> 'content_kind'
       or item.status <> 'needs_review'
       or item.current_version_id <> target_content_version_id then
        raise exception 'generated content is not the queued needs_review catalog item'
            using errcode = '23514';
    end if;
    select content_version.* into version
    from public.content_versions as content_version
    where content_version.id = target_content_version_id
      and content_version.workspace_id = job.workspace_id
      and content_version.content_item_id = target_content_item_id
    for key share;
    if not found
       or version.generation_meta ->> 'request_id' is distinct from target_content_item_id::text
       or version.generation_meta @> '{"mock_mode":true}'::jsonb then
        raise exception 'generated version is not a production review draft'
            using errcode = '23514';
    end if;
    if (
        select count(*) from public.source_items as source
        join public.source_feeds as feed
          on feed.id = source.source_feed_id
         and feed.workspace_id = source.workspace_id
         and feed.client_id = source.client_id
        where source.id = any(source_item_ids)
          and source.workspace_id = job.workspace_id
          and source.client_id = job.client_id
          and feed.provider = 'x'
          and feed.handle = expected_handle
          and feed.active is true
    ) <> source_count then
        raise exception 'queued official X sources are no longer valid'
            using errcode = '23514';
    end if;

    insert into public.content_source_links (
        workspace_id, client_id, content_item_id, source_item_id, position
    )
    select
        job.workspace_id,
        job.client_id,
        target_content_item_id,
        source_id,
        (ordinal - 1)::smallint
    from unnest(source_item_ids) with ordinality as source(source_id, ordinal)
    on conflict (content_item_id, source_item_id) do nothing;
    select count(*) into linked_count
    from public.content_source_links as link
    where link.content_item_id = target_content_item_id;
    if linked_count <> source_count or exists (
        select 1
        from unnest(source_item_ids) with ordinality as expected(source_id, ordinal)
        left join public.content_source_links as link
          on link.content_item_id = target_content_item_id
         and link.source_item_id = expected.source_id
         and link.position = (expected.ordinal - 1)::smallint
        where link.source_item_id is null
    ) then
        raise exception 'content source links do not match the queued source order'
            using errcode = '23514';
    end if;

    update public.jobs
    set status = 'succeeded',
        output = jsonb_build_object(
            'content_item_id', target_content_item_id,
            'content_version_id', target_content_version_id,
            'source_item_ids', to_jsonb(source_item_ids),
            'completed_by', target_worker_id
        ),
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        finished_at = statement_timestamp(),
        last_error_code = null,
        last_error_message = null
    where id = job.id;
    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        job.workspace_id,
        'content_item',
        target_content_item_id,
        'official_x_review_draft_completed',
        jsonb_build_object(
            'job_id', job.id,
            'content_version_id', target_content_version_id,
            'source_item_ids', to_jsonb(source_item_ids)
        )
    );
    return jsonb_build_object(
        'job_id', job.id,
        'status', 'succeeded',
        'content_item_id', target_content_item_id,
        'content_version_id', target_content_version_id,
        'source_item_ids', to_jsonb(source_item_ids),
        'reused', false
    );
end;
$$;

create or replace function public.fail_review_draft_job(
    target_job_id uuid,
    target_worker_id text,
    target_error_code text,
    target_error_message text,
    target_retryable boolean default true,
    target_retry_at timestamptz default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    job public.jobs%rowtype;
    next_status text;
    effective_retry_at timestamptz;
    failure jsonb;
begin
    if target_worker_id is null
       or target_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       or target_error_code is null
       or target_error_code !~ '^[a-z][a-z0-9_]{0,119}$'
       or target_error_message is null
       or char_length(target_error_message) not between 1 and 1000
       or target_retryable is null then
        raise exception 'review draft failure request is invalid'
            using errcode = '22023';
    end if;
    select queued_job.* into job from public.jobs as queued_job
    where queued_job.id = target_job_id
    for update;
    if not found
       or job.job_kind <> 'generate'
       or job.input ->> 'workflow' is distinct from 'official_x_review_draft_v1' then
        raise exception 'review draft job does not exist'
            using errcode = '23514';
    end if;

    if job.status in ('retrying', 'failed')
       and job.output -> 'last_failure' ->> 'worker_id' = target_worker_id
       and job.output -> 'last_failure' ->> 'error_code' = target_error_code
       and job.output -> 'last_failure' ->> 'error_message' = target_error_message
       and (job.output -> 'last_failure' ->> 'retryable')::boolean = target_retryable
       and (
           target_retry_at is null
           or (job.output -> 'last_failure' ->> 'retry_at')::timestamptz = target_retry_at
       ) then
        return jsonb_build_object(
            'job_id', job.id,
            'status', job.status,
            'attempts', job.attempts,
            'max_attempts', job.max_attempts,
            'available_at', job.available_at,
            'reused', true
        );
    end if;
    if job.status <> 'running'
       or job.locked_by is distinct from target_worker_id
       or job.lease_expires_at is null
       or job.lease_expires_at <= statement_timestamp() then
        raise exception 'review draft lease is not owned by this worker'
            using errcode = '55000';
    end if;
    if target_retry_at is not null
       and (target_retry_at < statement_timestamp()
            or target_retry_at > statement_timestamp() + interval '24 hours') then
        raise exception 'review draft retry time is outside the accepted window'
            using errcode = '22023';
    end if;

    if target_retryable and job.attempts < job.max_attempts then
        next_status := 'retrying';
        effective_retry_at := coalesce(
            target_retry_at,
            statement_timestamp() + case
                when job.attempts <= 1 then interval '1 minute'
                else interval '5 minutes'
            end
        );
    else
        next_status := 'failed';
        effective_retry_at := statement_timestamp();
    end if;
    failure := jsonb_build_object(
        'worker_id', target_worker_id,
        'error_code', target_error_code,
        'error_message', target_error_message,
        'retryable', target_retryable,
        'failed_at', statement_timestamp(),
        'retry_at', case when next_status = 'retrying'
                         then to_jsonb(effective_retry_at) else 'null'::jsonb end
    );
    update public.jobs
    set status = next_status,
        output = job.output || jsonb_build_object('last_failure', failure),
        available_at = effective_retry_at,
        locked_by = null,
        locked_at = null,
        lease_expires_at = null,
        last_error_code = target_error_code,
        last_error_message = target_error_message,
        finished_at = case when next_status = 'failed'
                           then statement_timestamp() else null end
    where id = job.id;
    return jsonb_build_object(
        'job_id', job.id,
        'status', next_status,
        'attempts', job.attempts,
        'max_attempts', job.max_attempts,
        'available_at', effective_retry_at,
        'reused', false
    );
end;
$$;

revoke all on function public.record_official_x_sources(
    uuid, text, text, uuid, text, text, jsonb, timestamptz
) from public, anon, authenticated, service_role;
revoke all on function public.get_official_x_automation_state(uuid, text, date, integer)
    from public, anon, authenticated, service_role;
revoke all on function public.queue_review_draft_job(
    uuid, text, date, jsonb, text, uuid, text, text, text, boolean
) from public, anon, authenticated, service_role;
revoke all on function public.claim_review_draft_job(uuid, text, integer)
    from public, anon, authenticated, service_role;
revoke all on function public.complete_review_draft_job(uuid, text, uuid, uuid)
    from public, anon, authenticated, service_role;
revoke all on function public.fail_review_draft_job(
    uuid, text, text, text, boolean, timestamptz
) from public, anon, authenticated, service_role;

grant execute on function public.record_official_x_sources(
    uuid, text, text, uuid, text, text, jsonb, timestamptz
) to service_role;
grant execute on function public.get_official_x_automation_state(uuid, text, date, integer)
    to service_role;
grant execute on function public.queue_review_draft_job(
    uuid, text, date, jsonb, text, uuid, text, text, text, boolean
) to service_role;
grant execute on function public.claim_review_draft_job(uuid, text, integer)
    to service_role;
grant execute on function public.complete_review_draft_job(uuid, text, uuid, uuid)
    to service_role;
grant execute on function public.fail_review_draft_job(
    uuid, text, text, text, boolean, timestamptz
) to service_role;

commit;
