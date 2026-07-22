-- Persist generated daily-news cards and articles in the Content Studio catalog.
--
-- These RPCs are intentionally service-role only. The shared Studio browser
-- session never receives database credentials or raw Storage paths; Netlify is
-- responsible for enforcing the configured workspace and signing asset URLs.

begin;

create index content_items_workspace_created_cursor_idx
    on public.content_items (workspace_id, created_at desc, id desc);

create or replace function public.record_generated_content(
    target_content_item_id uuid,
    target_workspace_id uuid,
    target_client_id text,
    target_content_kind text,
    target_title text,
    target_content jsonb,
    target_channel_copy jsonb,
    target_generation_meta jsonb,
    target_asset jsonb default null,
    target_prompt_version text default 'content-studio@1'
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    content_version_id uuid := gen_random_uuid();
    asset_id uuid;
    file_name text;
    storage_path text;
    path_parts text[];
    normalized_asset jsonb;
    asset_ids jsonb := '[]'::jsonb;
    expected_deliverables jsonb;
    expected_qa jsonb := jsonb_build_object(
        'source_fidelity', 'needs_review',
        'brand_alignment', 'needs_review',
        'manual_review_required', true
    );
    effective_generation_meta jsonb;
    existing_item public.content_items%rowtype;
    existing_version public.content_versions%rowtype;
    existing_asset_count integer;
begin
    if target_content_item_id is null then
        raise exception 'content request id is required'
            using errcode = '22023';
    end if;
    if target_content_kind is null
       or target_content_kind not in ('daily_news', 'article') then
        raise exception 'content kind must be daily_news or article'
            using errcode = '22023';
    end if;
    if not exists (
        select 1
        from public.workspace_clients
        where workspace_id = target_workspace_id
          and client_id = target_client_id
          and active is true
    ) then
        raise exception 'workspace client is not ready for generated content storage'
            using errcode = '23514';
    end if;
    if target_title is null
       or char_length(btrim(target_title)) not between 1 and 200 then
        raise exception 'content title must be 1 to 200 characters'
            using errcode = '22023';
    end if;
    if target_prompt_version is null
       or char_length(btrim(target_prompt_version)) not between 1 and 120 then
        raise exception 'prompt version must be 1 to 120 characters'
            using errcode = '22023';
    end if;
    if jsonb_typeof(target_content) is distinct from 'object'
       or octet_length(target_content::text) > 1048576 then
        raise exception 'generated content must be an object no larger than 1MB'
            using errcode = '22023';
    end if;
    if jsonb_typeof(target_channel_copy) is distinct from 'object'
       or octet_length(target_channel_copy::text) > 65536 then
        raise exception 'channel copy must be an object no larger than 64KB'
            using errcode = '22023';
    end if;
    if jsonb_typeof(target_generation_meta) is distinct from 'object'
       or octet_length(target_generation_meta::text) > 65536 then
        raise exception 'generation metadata must be an object no larger than 64KB'
            using errcode = '22023';
    end if;
    if not coalesce(
           (target_content ->> 'request_hash') ~ '^[a-f0-9]{64}$',
           false
       )
       or target_generation_meta ->> 'request_hash'
          is distinct from target_content ->> 'request_hash' then
        raise exception 'generated content request hash is missing or inconsistent'
            using errcode = '22023';
    end if;

    effective_generation_meta := target_generation_meta || jsonb_build_object(
        'request_id', target_content_item_id::text
    );

    if target_content_kind = 'daily_news' then
        if jsonb_typeof(target_asset) is distinct from 'object'
           or not (target_asset ?& array[
               'asset_id', 'filename', 'storage_path', 'mime_type',
               'byte_size', 'sha256', 'width', 'height'
           ])
           or target_asset - array[
               'asset_id', 'filename', 'storage_path', 'mime_type',
               'byte_size', 'sha256', 'width', 'height'
           ] <> '{}'::jsonb then
            raise exception 'daily news requires exactly one complete PNG asset'
                using errcode = '22023';
        end if;
        if not coalesce(
            (target_asset ->> 'asset_id')
                ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
            false
        ) then
            raise exception 'daily news asset id is invalid'
                using errcode = '22023';
        end if;
        asset_id := (target_asset ->> 'asset_id')::uuid;
        file_name := target_asset ->> 'filename';
        storage_path := target_asset ->> 'storage_path';
        if file_name is distinct from 'news-card.png'
           or target_asset ->> 'mime_type' is distinct from 'image/png'
           or storage_path is null
           or char_length(storage_path) > 500 then
            raise exception 'daily news asset must be news-card.png with image/png MIME type'
                using errcode = '22023';
        end if;
        path_parts := string_to_array(storage_path, '/');
        if array_length(path_parts, 1) <> 4
           or path_parts[1] <> target_workspace_id::text
           or path_parts[2] <> target_client_id
           or path_parts[3] <> asset_id::text
           or path_parts[4] <> file_name then
            raise exception 'daily news asset is outside its workspace scope'
                using errcode = '22023';
        end if;
        if not coalesce((target_asset ->> 'byte_size') ~ '^[0-9]+$', false)
           or not coalesce((target_asset ->> 'sha256') ~ '^[a-f0-9]{64}$', false)
           or not coalesce((target_asset ->> 'width') ~ '^[0-9]+$', false)
           or not coalesce((target_asset ->> 'height') ~ '^[0-9]+$', false)
           or (target_asset ->> 'byte_size')::bigint not between 8 and 26214400
           or (target_asset ->> 'width')::integer not between 1 and 10000
           or (target_asset ->> 'height')::integer not between 1 and 10000 then
            raise exception 'daily news asset dimensions or digest are invalid'
                using errcode = '22023';
        end if;
        perform 1
        from storage.objects
        where bucket_id = 'content-studio'
          and name = storage_path
        for key share;
        if not found then
            raise exception 'daily news asset is not present in private storage'
                using errcode = '23514';
        end if;

        normalized_asset := jsonb_build_object(
            'asset_id', asset_id,
            'filename', file_name,
            'storage_path', storage_path,
            'mime_type', 'image/png',
            'byte_size', (target_asset ->> 'byte_size')::bigint,
            'sha256', target_asset ->> 'sha256',
            'width', (target_asset ->> 'width')::integer,
            'height', (target_asset ->> 'height')::integer
        );
        asset_ids := jsonb_build_array(asset_id::text);
        expected_deliverables := jsonb_build_object(
            'primary_asset_id', asset_id::text,
            'asset_ids', asset_ids
        );
    else
        if target_asset is not null and target_asset <> 'null'::jsonb then
            raise exception 'articles cannot include generated assets'
                using errcode = '22023';
        end if;
        expected_deliverables := jsonb_build_object('asset_ids', asset_ids);
    end if;

    -- The caller supplies a stable UUID for one generation request. Serializing
    -- on it makes retries after an uncertain network response exactly-once.
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(target_content_item_id::text, 0)
    );

    select * into existing_item
    from public.content_items
    where id = target_content_item_id;
    if found then
        if existing_item.workspace_id <> target_workspace_id
           or existing_item.client_id <> target_client_id
           or existing_item.content_kind <> target_content_kind then
            raise exception 'content request id belongs to another catalog entry'
                using errcode = '23505';
        end if;

        select * into existing_version
        from public.content_versions
        where workspace_id = target_workspace_id
          and content_item_id = target_content_item_id
          and version_number = 1;
        if not found
           or existing_version.prompt_version <> btrim(target_prompt_version)
           or existing_version.locale <> 'ko-KR'
           or existing_version.title <> btrim(target_title)
           or existing_version.content is distinct from target_content
           or existing_version.channel_copy is distinct from target_channel_copy
           or existing_version.deliverables is distinct from expected_deliverables
           or existing_version.qa is distinct from expected_qa
           or existing_version.generation_meta is distinct from effective_generation_meta then
            raise exception 'content request retry does not match committed content'
                using errcode = '23505';
        end if;

        select count(*) into existing_asset_count
        from public.assets as a
        where a.workspace_id = target_workspace_id
          and a.content_item_id = target_content_item_id
          and a.content_version_id = existing_version.id;
        if target_content_kind = 'daily_news' then
            if existing_asset_count <> 1
               or not exists (
                   select 1
                   from public.assets as a
                   join storage.objects as stored
                     on stored.bucket_id = a.storage_bucket
                    and stored.name = a.storage_path
                   where a.id = asset_id
                     and a.workspace_id = target_workspace_id
                     and a.content_item_id = target_content_item_id
                     and a.content_version_id = existing_version.id
                     and a.asset_kind = 'png'
                     and a.storage_bucket = 'content-studio'
                     and a.storage_path = normalized_asset ->> 'storage_path'
                     and a.mime_type = 'image/png'
                     and a.byte_size = (normalized_asset ->> 'byte_size')::bigint
                     and a.sha256 = normalized_asset ->> 'sha256'
                     and a.width = (normalized_asset ->> 'width')::integer
                     and a.height = (normalized_asset ->> 'height')::integer
                     and a.metadata = jsonb_build_object(
                         'filename', normalized_asset ->> 'filename'
                     )
               ) then
                raise exception 'content request retry does not match committed assets'
                    using errcode = '23505';
            end if;
        elsif existing_asset_count <> 0 then
            raise exception 'content request retry does not match committed assets'
                using errcode = '23505';
        end if;

        return jsonb_build_object(
            'content_item_id', target_content_item_id,
            'content_version_id', existing_version.id,
            'asset_ids', asset_ids
        );
    end if;

    insert into public.content_items (
        id, workspace_id, client_id, content_kind, title, status
    ) values (
        target_content_item_id,
        target_workspace_id,
        target_client_id,
        target_content_kind,
        btrim(target_title),
        'needs_review'
    );

    insert into public.content_versions (
        id, workspace_id, content_item_id, version_number, prompt_version,
        locale, title, content, channel_copy, deliverables, qa, generation_meta
    ) values (
        content_version_id,
        target_workspace_id,
        target_content_item_id,
        1,
        btrim(target_prompt_version),
        'ko-KR',
        btrim(target_title),
        target_content,
        target_channel_copy,
        expected_deliverables,
        expected_qa,
        effective_generation_meta
    );

    if target_content_kind = 'daily_news' then
        insert into public.assets (
            id, workspace_id, content_item_id, content_version_id, asset_kind,
            storage_bucket, storage_path, mime_type, byte_size, sha256, width,
            height, metadata
        ) values (
            asset_id,
            target_workspace_id,
            target_content_item_id,
            content_version_id,
            'png',
            'content-studio',
            normalized_asset ->> 'storage_path',
            'image/png',
            (normalized_asset ->> 'byte_size')::bigint,
            normalized_asset ->> 'sha256',
            (normalized_asset ->> 'width')::integer,
            (normalized_asset ->> 'height')::integer,
            jsonb_build_object('filename', normalized_asset ->> 'filename')
        );
    end if;

    update public.content_items
    set current_version_id = content_version_id
    where id = target_content_item_id;

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        target_workspace_id,
        'content_item',
        target_content_item_id,
        'content_generated',
        jsonb_build_object(
            'content_kind', target_content_kind,
            'content_version_id', content_version_id,
            'asset_ids', asset_ids,
            'request_hash', target_content ->> 'request_hash'
        )
    );

    return jsonb_build_object(
        'content_item_id', target_content_item_id,
        'content_version_id', content_version_id,
        'asset_ids', asset_ids
    );
end;
$$;

create or replace function public.get_generated_content(
    target_content_item_id uuid,
    target_workspace_id uuid,
    target_client_id text,
    target_content_kind text
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    asset_row jsonb;
    catalog_asset_count integer;
    resolved_asset_count integer;
begin
    if target_content_item_id is null then
        raise exception 'content request id is required'
            using errcode = '22023';
    end if;
    if target_content_kind is null
       or target_content_kind not in ('daily_news', 'article') then
        raise exception 'content kind must be daily_news or article'
            using errcode = '22023';
    end if;
    if not exists (
        select 1
        from public.workspace_clients
        where workspace_id = target_workspace_id
          and client_id = target_client_id
          and active is true
    ) then
        raise exception 'workspace client is not ready for generated content storage'
            using errcode = '23514';
    end if;

    select * into item
    from public.content_items
    where id = target_content_item_id
      and workspace_id = target_workspace_id
      and client_id = target_client_id
      and content_kind = target_content_kind;
    if not found then
        return null;
    end if;

    select * into version
    from public.content_versions
    where workspace_id = target_workspace_id
      and content_item_id = target_content_item_id
      and version_number = 1;
    if not found then
        raise exception 'committed generated content has no initial version'
            using errcode = '23514';
    end if;
    if not coalesce((version.content ->> 'request_hash') ~ '^[a-f0-9]{64}$', false)
       or version.generation_meta ->> 'request_hash'
          is distinct from version.content ->> 'request_hash'
       or version.generation_meta ->> 'request_id'
          is distinct from target_content_item_id::text then
        raise exception 'committed generated content has invalid request metadata'
            using errcode = '23514';
    end if;

    select count(*) into catalog_asset_count
    from public.assets
    where workspace_id = target_workspace_id
      and content_item_id = target_content_item_id
      and content_version_id = version.id;

    if target_content_kind = 'daily_news' then
        if jsonb_typeof(version.deliverables -> 'asset_ids') is distinct from 'array'
           or jsonb_array_length(version.deliverables -> 'asset_ids') <> 1
           or version.deliverables ->> 'primary_asset_id'
              is distinct from version.deliverables -> 'asset_ids' ->> 0
           or catalog_asset_count <> 1 then
            raise exception 'committed daily news has invalid deliverables'
                using errcode = '23514';
        end if;

        select jsonb_build_object(
            'asset_id', a.id,
            'filename', a.metadata ->> 'filename',
            'storage_path', a.storage_path,
            'mime_type', a.mime_type,
            'byte_size', a.byte_size,
            'sha256', a.sha256,
            'width', a.width,
            'height', a.height
        ), count(*) over ()
        into asset_row, resolved_asset_count
        from public.assets as a
        join storage.objects as stored
          on stored.bucket_id = a.storage_bucket
         and stored.name = a.storage_path
        where a.id = (version.deliverables ->> 'primary_asset_id')::uuid
          and a.workspace_id = target_workspace_id
          and a.content_item_id = target_content_item_id
          and a.content_version_id = version.id
          and a.asset_kind = 'png'
          and a.storage_bucket = 'content-studio'
          and a.mime_type = 'image/png'
          and a.metadata ->> 'filename' = 'news-card.png'
          and a.storage_path = target_workspace_id::text || '/' || target_client_id
              || '/' || a.id::text || '/news-card.png';
        if not found or resolved_asset_count <> 1 then
            raise exception 'committed daily news asset is invalid or missing'
                using errcode = '23514';
        end if;
    else
        if version.deliverables is distinct from jsonb_build_object(
               'asset_ids', '[]'::jsonb
           )
           or catalog_asset_count <> 0 then
            raise exception 'committed article has invalid deliverables'
                using errcode = '23514';
        end if;
        asset_row := null;
    end if;

    return jsonb_build_object(
        'content_item_id', item.id,
        'content_version_id', version.id,
        'client_id', item.client_id,
        'content_kind', item.content_kind,
        'status', item.status,
        'title', version.title,
        'prompt_version', version.prompt_version,
        'content', version.content,
        'channel_copy', version.channel_copy,
        'generation_meta', version.generation_meta,
        'asset', asset_row,
        'created_at', item.created_at,
        'updated_at', item.updated_at
    );
end;
$$;

create or replace function public.list_content_library(
    target_workspace_id uuid,
    target_client_id text default null,
    target_content_kind text default null,
    target_status text default null,
    target_limit integer default 24,
    target_before_created_at timestamptz default null,
    target_before_id uuid default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    result jsonb;
begin
    if target_workspace_id is null
       or not exists (
           select 1 from public.workspaces where id = target_workspace_id
       ) then
        raise exception 'content library workspace does not exist'
            using errcode = '23514';
    end if;
    if target_client_id is not null
       and not exists (
           select 1
           from public.workspace_clients
           where workspace_id = target_workspace_id
             and client_id = target_client_id
       ) then
        raise exception 'content library client does not exist in workspace'
            using errcode = '23514';
    end if;
    if target_content_kind is not null
       and target_content_kind not in ('daily_news', 'article', 'tutorial') then
        raise exception 'content library kind is invalid'
            using errcode = '22023';
    end if;
    if target_status is not null
       and target_status not in (
           'draft', 'queued', 'generating', 'needs_review', 'approved',
           'rejected', 'scheduled', 'published', 'failed', 'archived'
       ) then
        raise exception 'content library status is invalid'
            using errcode = '22023';
    end if;
    if target_limit is null or target_limit not between 1 and 100 then
        raise exception 'content library limit must be 1 to 100'
            using errcode = '22023';
    end if;
    if (target_before_created_at is null) <> (target_before_id is null) then
        raise exception 'content library cursor is incomplete'
            using errcode = '22023';
    end if;

    with candidates as materialized (
        select
            item.id as content_item_id,
            version.id as content_version_id,
            item.client_id,
            item.content_kind,
            version.title,
            item.status,
            item.created_at,
            item.updated_at,
            (
                select count(*)
                from public.assets as a
                where a.workspace_id = item.workspace_id
                  and a.content_item_id = item.id
                  and a.content_version_id = version.id
            ) as asset_count,
            version.deliverables ->> 'primary_asset_id' as primary_asset_id,
            case
                when version.generation_meta ->> 'mock_mode' in ('true', 'false')
                    then (version.generation_meta ->> 'mock_mode')::boolean
                else false
            end as mock_mode
        from public.content_items as item
        join public.content_versions as version
          on version.id = item.current_version_id
         and version.workspace_id = item.workspace_id
         and version.content_item_id = item.id
        where item.workspace_id = target_workspace_id
          and (target_client_id is null or item.client_id = target_client_id)
          and (target_content_kind is null or item.content_kind = target_content_kind)
          and (target_status is null or item.status = target_status)
          and (
              target_before_created_at is null
              or (item.created_at, item.id)
                 < (target_before_created_at, target_before_id)
          )
        order by item.created_at desc, item.id desc
        limit target_limit + 1
    ), page_rows as materialized (
        select *
        from candidates
        order by created_at desc, content_item_id desc
        limit target_limit
    ), last_row as (
        select created_at, content_item_id
        from page_rows
        order by created_at asc, content_item_id asc
        limit 1
    )
    select jsonb_build_object(
        'items', coalesce(
            (
                select jsonb_agg(
                    jsonb_build_object(
                        'content_item_id', content_item_id,
                        'content_version_id', content_version_id,
                        'client_id', client_id,
                        'content_kind', content_kind,
                        'title', title,
                        'status', status,
                        'created_at', created_at,
                        'updated_at', updated_at,
                        'asset_count', asset_count,
                        'primary_asset_id', primary_asset_id,
                        'mock_mode', mock_mode
                    ) order by created_at desc, content_item_id desc
                )
                from page_rows
            ),
            '[]'::jsonb
        ),
        'next_cursor', case
            when (select count(*) from candidates) > target_limit then (
                select jsonb_build_object(
                    'created_at', created_at,
                    'content_item_id', content_item_id
                )
                from last_row
            )
            else null
        end
    ) into result;

    return result;
end;
$$;

create or replace function public.get_content_library_item(
    target_workspace_id uuid,
    target_content_item_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    asset_rows jsonb;
    catalog_asset_count integer;
    resolved_asset_count integer;
    figma_rows jsonb;
begin
    if target_workspace_id is null
       or not exists (
           select 1 from public.workspaces where id = target_workspace_id
       ) then
        raise exception 'content library workspace does not exist'
            using errcode = '23514';
    end if;
    if target_content_item_id is null then
        raise exception 'content library item id is required'
            using errcode = '22023';
    end if;

    select * into item
    from public.content_items
    where id = target_content_item_id
      and workspace_id = target_workspace_id;
    if not found then
        return null;
    end if;
    if item.current_version_id is null then
        raise exception 'content library item has no current version'
            using errcode = '23514';
    end if;

    select * into version
    from public.content_versions
    where id = item.current_version_id
      and workspace_id = target_workspace_id
      and content_item_id = target_content_item_id;
    if not found then
        raise exception 'content library item has an invalid current version'
            using errcode = '23514';
    end if;

    select count(*) into catalog_asset_count
    from public.assets
    where workspace_id = target_workspace_id
      and content_item_id = target_content_item_id
      and content_version_id = version.id;

    select coalesce(
        jsonb_agg(
            jsonb_build_object(
                'asset_id', a.id,
                'asset_kind', a.asset_kind,
                'storage_bucket', a.storage_bucket,
                'storage_path', a.storage_path,
                'mime_type', a.mime_type,
                'byte_size', a.byte_size,
                'sha256', a.sha256,
                'width', a.width,
                'height', a.height,
                'metadata', a.metadata,
                'created_at', a.created_at
            ) order by a.created_at, a.id
        ),
        '[]'::jsonb
    ), count(*)
    into asset_rows, resolved_asset_count
    from public.assets as a
    join storage.objects as stored
      on stored.bucket_id = a.storage_bucket
     and stored.name = a.storage_path
    where a.workspace_id = target_workspace_id
      and a.content_item_id = target_content_item_id
      and a.content_version_id = version.id;
    if resolved_asset_count <> catalog_asset_count then
        raise exception 'content library item has missing Storage objects'
            using errcode = '23514';
    end if;

    select coalesce(
        jsonb_agg(
            jsonb_build_object(
                'figma_link_id', link.id,
                'file_key', link.file_key,
                'node_id', link.node_id,
                'page_name', link.page_name,
                'section_name', link.section_name,
                'sync_status', link.sync_status,
                'last_synced_at', link.last_synced_at,
                'metadata', link.metadata,
                'updated_at', link.updated_at
            ) order by link.updated_at desc, link.id desc
        ),
        '[]'::jsonb
    ) into figma_rows
    from public.figma_links as link
    where link.workspace_id = target_workspace_id
      and link.content_item_id = target_content_item_id
      and link.content_version_id = version.id;

    return jsonb_build_object(
        'content_item_id', item.id,
        'content_version_id', version.id,
        'client_id', item.client_id,
        'content_kind', item.content_kind,
        'status', item.status,
        'title', version.title,
        'version_number', version.version_number,
        'prompt_version', version.prompt_version,
        'locale', version.locale,
        'content', version.content,
        'channel_copy', version.channel_copy,
        'deliverables', version.deliverables,
        'qa', version.qa,
        'generation_meta', version.generation_meta,
        'assets', asset_rows,
        'figma_links', figma_rows,
        'created_at', item.created_at,
        'updated_at', item.updated_at
    );
end;
$$;

revoke all on function public.record_generated_content(
    uuid, uuid, text, text, text, jsonb, jsonb, jsonb, jsonb, text
) from public, anon, authenticated, service_role;
revoke all on function public.get_generated_content(uuid, uuid, text, text)
    from public, anon, authenticated, service_role;
revoke all on function public.list_content_library(
    uuid, text, text, text, integer, timestamptz, uuid
) from public, anon, authenticated, service_role;
revoke all on function public.get_content_library_item(uuid, uuid)
    from public, anon, authenticated, service_role;

grant execute on function public.record_generated_content(
    uuid, uuid, text, text, text, jsonb, jsonb, jsonb, jsonb, text
) to service_role;
grant execute on function public.get_generated_content(uuid, uuid, text, text)
    to service_role;
grant execute on function public.list_content_library(
    uuid, text, text, text, integer, timestamptz, uuid
) to service_role;
grant execute on function public.get_content_library_item(uuid, uuid)
    to service_role;

commit;
