-- Immutable style-only references for scheduled official-X review drafts.
--
-- Reference posts are never content_source_links and never factual evidence.
-- The worker can only obtain them through the service-role RPC below.

begin;

create table private.official_x_style_reference_packs (
    workspace_id uuid not null,
    client_id text not null check (
        client_id in ('yellow', 'origintrail', 'squid', 'babylon')
    ),
    request_id uuid not null,
    primary_source_item_id uuid not null,
    style_references jsonb not null check (
        jsonb_typeof(style_references) = 'array'
        and jsonb_array_length(style_references) between 0 and 3
        and octet_length(style_references::text) <= 8192
    ),
    reference_pack_hash text not null check (
        reference_pack_hash ~ '^[a-f0-9]{32}$'
    ),
    created_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, client_id, request_id),
    unique (request_id),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id)
        on delete restrict,
    foreign key (workspace_id, client_id, primary_source_item_id)
        references public.source_items(workspace_id, client_id, id)
        on delete restrict
);

create index official_x_style_reference_packs_primary_source_idx
    on private.official_x_style_reference_packs (
        workspace_id,
        client_id,
        primary_source_item_id
    );

revoke all on table private.official_x_style_reference_packs
from public, anon, authenticated, service_role;

create or replace function private.reject_official_x_style_reference_mutation()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    raise exception 'official X style reference packs are immutable'
        using errcode = '55000';
end;
$$;

create trigger official_x_style_reference_packs_immutable
before update or delete on private.official_x_style_reference_packs
for each row
execute function private.reject_official_x_style_reference_mutation();

revoke all on function private.reject_official_x_style_reference_mutation()
from public, anon, authenticated, service_role;

create or replace function public.get_or_create_official_x_style_reference_pack(
    target_workspace_id uuid,
    target_client_id text,
    target_request_id uuid,
    target_primary_source_item_id uuid,
    target_reference_limit integer default 3
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    expected_handle text;
    primary_source public.source_items%rowtype;
    committed private.official_x_style_reference_packs%rowtype;
    selected_references jsonb;
    selected_hash text;
begin
    expected_handle := case target_client_id
        when 'yellow' then '@Yellow'
        when 'origintrail' then '@origin_trail'
        when 'squid' then '@SquidRouter'
        when 'babylon' then '@babylonlabs_io'
        else null
    end;
    if expected_handle is null
       or target_workspace_id is null
       or target_request_id is null
       or target_primary_source_item_id is null
       or target_reference_limit is null
       or target_reference_limit not between 0 and 3 then
        raise exception 'official X style reference request is invalid'
            using errcode = '22023';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'official-x-style-pack:' || target_request_id::text, 0
    ));

    select pack.* into committed
    from private.official_x_style_reference_packs as pack
    where pack.workspace_id = target_workspace_id
      and pack.client_id = target_client_id
      and pack.request_id = target_request_id;
    if found then
        if committed.primary_source_item_id <> target_primary_source_item_id then
            raise exception 'style reference retry changed the primary source'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'request_id', committed.request_id,
            'primary_source_item_id', committed.primary_source_item_id,
            'reference_pack_hash', committed.reference_pack_hash,
            'references', committed.style_references,
            'reused', true
        );
    end if;

    select source.* into primary_source
    from public.source_items as source
    join public.source_feeds as feed
      on feed.workspace_id = source.workspace_id
     and feed.client_id = source.client_id
     and feed.id = source.source_feed_id
    join private.official_x_source_state as state
      on state.workspace_id = source.workspace_id
     and state.client_id = source.client_id
     and state.source_item_id = source.id
    where source.workspace_id = target_workspace_id
      and source.client_id = target_client_id
      and source.id = target_primary_source_item_id
      and source.source_type = 'tweet'
      and source.published_at is not null
      and source.canonical_url is not null
      and feed.provider = 'x'
      and feed.handle = expected_handle
      and feed.active is true;
    if not found then
        raise exception 'primary source is not a recorded active official X post'
            using errcode = '23514';
    end if;

    select coalesce(
        jsonb_agg(
            candidate.payload
            order by candidate.published_at desc, candidate.external_id desc
        ),
        '[]'::jsonb
    )
    into selected_references
    from (
        select
            source.published_at,
            source.external_id,
            jsonb_build_object(
                'source_item_id', source.id,
                'source_url', source.canonical_url,
                'text', pg_catalog.left(source.body, 600),
                'published_at', source.published_at
            ) as payload
        from public.source_items as source
        join public.source_feeds as feed
          on feed.workspace_id = source.workspace_id
         and feed.client_id = source.client_id
         and feed.id = source.source_feed_id
        join private.official_x_source_state as state
          on state.workspace_id = source.workspace_id
         and state.client_id = source.client_id
         and state.source_item_id = source.id
        where source.workspace_id = target_workspace_id
          and source.client_id = target_client_id
          and source.id <> primary_source.id
          and source.source_type = 'tweet'
          and source.published_at is not null
          and source.canonical_url is not null
          and feed.id = primary_source.source_feed_id
          and feed.provider = 'x'
          and feed.handle = expected_handle
          and (
              source.published_at < primary_source.published_at
              or (
                  source.published_at = primary_source.published_at
                  and source.external_id ~ '^[0-9]{1,19}$'
                  and primary_source.external_id ~ '^[0-9]{1,19}$'
                  and source.external_id::numeric < primary_source.external_id::numeric
              )
          )
        order by source.published_at desc, source.external_id desc
        limit target_reference_limit
    ) as candidate;

    selected_hash := pg_catalog.md5(selected_references::text);
    insert into private.official_x_style_reference_packs (
        workspace_id,
        client_id,
        request_id,
        primary_source_item_id,
        style_references,
        reference_pack_hash
    ) values (
        target_workspace_id,
        target_client_id,
        target_request_id,
        target_primary_source_item_id,
        selected_references,
        selected_hash
    )
    returning * into committed;

    return jsonb_build_object(
        'request_id', committed.request_id,
        'primary_source_item_id', committed.primary_source_item_id,
        'reference_pack_hash', committed.reference_pack_hash,
        'references', committed.style_references,
        'reused', false
    );
end;
$$;

revoke all on function public.get_or_create_official_x_style_reference_pack(
    uuid, text, uuid, uuid, integer
) from public, anon, authenticated, service_role;

grant execute on function public.get_or_create_official_x_style_reference_pack(
    uuid, text, uuid, uuid, integer
) to service_role;

commit;
