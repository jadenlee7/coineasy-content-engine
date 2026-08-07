-- Integration security smoke for generated-content catalog RPCs.
-- Run after all migrations as the database owner; every row is rolled back.

begin;

do $test$
declare
    signature text;
begin
    foreach signature in array array[
        'public.record_generated_content(uuid,uuid,text,text,text,jsonb,jsonb,jsonb,jsonb,text)',
        'public.get_generated_content(uuid,uuid,text,text)',
        'public.list_content_library(uuid,text,text,text,integer,timestamp with time zone,uuid)',
        'public.get_content_library_item(uuid,uuid)'
    ]
    loop
        if has_function_privilege('anon', signature, 'execute') then
            raise exception 'generated catalog RPC leaked to anon: %', signature;
        end if;
        if has_function_privilege('authenticated', signature, 'execute') then
            raise exception 'generated catalog RPC leaked to authenticated: %', signature;
        end if;
        if not has_function_privilege('service_role', signature, 'execute') then
            raise exception 'generated catalog RPC unavailable to service_role: %', signature;
        end if;
    end loop;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'a0000000-0000-4000-8000-000000000001',
    'Generated Catalog Security Test',
    'generated-catalog-security-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
)
values (
    'a0000000-0000-4000-8000-000000000001',
    'squid',
    'Squid',
    true,
    null
);

insert into storage.objects (bucket_id, name)
values (
    'content-studio',
    'a0000000-0000-4000-8000-000000000001/squid/a3000000-0000-4000-8000-000000000001/news-card.png'
);

select public.record_generated_content(
    'a1000000-0000-4000-8000-000000000001',
    'a0000000-0000-4000-8000-000000000001',
    'squid',
    'daily_news',
    'Squid 뉴스 카드',
    '{"request_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","spec":{"headline":"뉴스"}}'::jsonb,
    '{"telegram":"텔레그램 문구","x":"X 문구"}'::jsonb,
    '{"request_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","mock_mode":false}'::jsonb,
    '{"asset_id":"a3000000-0000-4000-8000-000000000001","filename":"news-card.png","storage_path":"a0000000-0000-4000-8000-000000000001/squid/a3000000-0000-4000-8000-000000000001/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","width":1080,"height":1080}'::jsonb,
    'news-card@1'
);

-- An exact replay returns the committed version without duplicating history.
select public.record_generated_content(
    'a1000000-0000-4000-8000-000000000001',
    'a0000000-0000-4000-8000-000000000001',
    'squid',
    'daily_news',
    'Squid 뉴스 카드',
    '{"request_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","spec":{"headline":"뉴스"}}'::jsonb,
    '{"telegram":"텔레그램 문구","x":"X 문구"}'::jsonb,
    '{"request_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","mock_mode":false}'::jsonb,
    '{"asset_id":"a3000000-0000-4000-8000-000000000001","filename":"news-card.png","storage_path":"a0000000-0000-4000-8000-000000000001/squid/a3000000-0000-4000-8000-000000000001/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","width":1080,"height":1080}'::jsonb,
    'news-card@1'
);

select public.record_generated_content(
    'a1000000-0000-4000-8000-000000000002',
    'a0000000-0000-4000-8000-000000000001',
    'squid',
    'article',
    'Squid 아티클',
    '{"request_hash":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","markdown":"본문"}'::jsonb,
    '{"telegram":"아티클 안내","x":"요약"}'::jsonb,
    '{"request_hash":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","mock_mode":false}'::jsonb,
    null,
    'article@1'
);

do $test$
declare
    news_lookup jsonb;
    article_lookup jsonb;
    detail_result jsonb;
begin
    if (select count(*) from public.content_items
        where id = 'a1000000-0000-4000-8000-000000000001') <> 1
       or (select count(*) from public.content_versions
        where content_item_id = 'a1000000-0000-4000-8000-000000000001') <> 1
       or (select count(*) from public.assets
        where content_item_id = 'a1000000-0000-4000-8000-000000000001') <> 1
       or (select count(*) from public.event_log
        where entity_id = 'a1000000-0000-4000-8000-000000000001'
          and event_type = 'content_generated') <> 1 then
        raise exception 'generated content record was not exactly idempotent';
    end if;
    if not exists (
        select 1
        from public.content_items
        where id = 'a1000000-0000-4000-8000-000000000001'
          and status = 'needs_review'
          and content_kind = 'daily_news'
    ) then
        raise exception 'generated daily news did not enter needs_review';
    end if;
    if (select count(*) from public.assets
        where content_item_id = 'a1000000-0000-4000-8000-000000000002') <> 0 then
        raise exception 'generated article stored an asset';
    end if;

    news_lookup := public.get_generated_content(
        'a1000000-0000-4000-8000-000000000001',
        'a0000000-0000-4000-8000-000000000001',
        'squid',
        'daily_news'
    );
    if news_lookup ->> 'content_item_id'
       <> 'a1000000-0000-4000-8000-000000000001'
       or news_lookup -> 'asset' ->> 'asset_id'
          <> 'a3000000-0000-4000-8000-000000000001'
       or news_lookup -> 'asset' ->> 'filename' <> 'news-card.png' then
        raise exception 'generated daily news lookup is incomplete';
    end if;

    article_lookup := public.get_generated_content(
        'a1000000-0000-4000-8000-000000000002',
        'a0000000-0000-4000-8000-000000000001',
        'squid',
        'article'
    );
    if article_lookup ->> 'content_item_id'
       <> 'a1000000-0000-4000-8000-000000000002'
       or article_lookup -> 'asset' <> 'null'::jsonb then
        raise exception 'generated article lookup returned an asset';
    end if;

    detail_result := public.get_content_library_item(
        'a0000000-0000-4000-8000-000000000001',
        'a1000000-0000-4000-8000-000000000001'
    );
    if detail_result ->> 'content_item_id'
       <> 'a1000000-0000-4000-8000-000000000001'
       or jsonb_array_length(detail_result -> 'assets') <> 1
       or jsonb_array_length(detail_result -> 'figma_links') <> 0 then
        raise exception 'content library detail is incomplete';
    end if;
end
$test$;

do $test$
begin
    begin
        perform public.record_generated_content(
            'a1000000-0000-4000-8000-000000000003',
            'a0000000-0000-4000-8000-000000000001',
            'squid', 'daily_news', 'wrong path',
            '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'::jsonb,
            '{}'::jsonb,
            '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'::jsonb,
            '{"asset_id":"a3000000-0000-4000-8000-000000000003","filename":"news-card.png","storage_path":"a9999999-0000-4000-8000-000000000999/squid/a3000000-0000-4000-8000-000000000003/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","width":1080,"height":1080}'::jsonb
        );
        raise exception 'daily news asset path escaped its workspace';
    exception when invalid_parameter_value then null;
    end;

    begin
        perform public.record_generated_content(
            'a1000000-0000-4000-8000-000000000004',
            'a0000000-0000-4000-8000-000000000001',
            'squid', 'daily_news', 'missing object',
            '{"request_hash":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}'::jsonb,
            '{}'::jsonb,
            '{"request_hash":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}'::jsonb,
            '{"asset_id":"a3000000-0000-4000-8000-000000000004","filename":"news-card.png","storage_path":"a0000000-0000-4000-8000-000000000001/squid/a3000000-0000-4000-8000-000000000004/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","width":1080,"height":1080}'::jsonb
        );
        raise exception 'daily news without a Storage object was accepted';
    exception when check_violation then null;
    end;

    begin
        perform public.record_generated_content(
            'a1000000-0000-4000-8000-000000000005',
            'a0000000-0000-4000-8000-000000000001',
            'squid', 'article', 'article asset',
            '{"request_hash":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"}'::jsonb,
            '{}'::jsonb,
            '{"request_hash":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"}'::jsonb,
            '{"asset_id":"a3000000-0000-4000-8000-000000000001","filename":"news-card.png","storage_path":"a0000000-0000-4000-8000-000000000001/squid/a3000000-0000-4000-8000-000000000001/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","width":1080,"height":1080}'::jsonb
        );
        raise exception 'article accepted a generated asset';
    exception when invalid_parameter_value then null;
    end;

    begin
        perform public.record_generated_content(
            'a1000000-0000-4000-8000-000000000001',
            'a0000000-0000-4000-8000-000000000001',
            'squid', 'daily_news', 'Squid 뉴스 카드',
            '{"request_hash":"9999999999999999999999999999999999999999999999999999999999999999","spec":{"headline":"뉴스"}}'::jsonb,
            '{"telegram":"텔레그램 문구","x":"X 문구"}'::jsonb,
            '{"request_hash":"9999999999999999999999999999999999999999999999999999999999999999","mock_mode":false}'::jsonb,
            '{"asset_id":"a3000000-0000-4000-8000-000000000001","filename":"news-card.png","storage_path":"a0000000-0000-4000-8000-000000000001/squid/a3000000-0000-4000-8000-000000000001/news-card.png","mime_type":"image/png","byte_size":128,"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","width":1080,"height":1080}'::jsonb,
            'news-card@1'
        );
        raise exception 'generated content retry accepted a different request hash';
    exception when unique_violation then null;
    end;

    begin
        perform set_config('storage.allow_delete_query', 'true', true);
        delete from storage.objects
        where bucket_id = 'content-studio'
          and name = 'a0000000-0000-4000-8000-000000000001/squid/a3000000-0000-4000-8000-000000000001/news-card.png';
        perform public.get_generated_content(
            'a1000000-0000-4000-8000-000000000001',
            'a0000000-0000-4000-8000-000000000001',
            'squid', 'daily_news'
        );
        raise exception 'generated content lookup accepted a missing Storage object';
    exception when check_violation then null;
    end;
end
$test$;

do $test$
declare
    first_page jsonb;
    second_page jsonb;
    first_id uuid;
begin
    first_page := public.list_content_library(
        'a0000000-0000-4000-8000-000000000001',
        null, null, null, 1, null, null
    );
    if jsonb_array_length(first_page -> 'items') <> 1
       or first_page -> 'next_cursor' = 'null'::jsonb then
        raise exception 'catalog list did not return a bounded first page';
    end if;
    first_id := (first_page -> 'items' -> 0 ->> 'content_item_id')::uuid;
    second_page := public.list_content_library(
        'a0000000-0000-4000-8000-000000000001',
        null,
        null,
        null,
        1,
        (first_page -> 'next_cursor' ->> 'created_at')::timestamptz,
        (first_page -> 'next_cursor' ->> 'content_item_id')::uuid
    );
    if jsonb_array_length(second_page -> 'items') <> 1
       or (second_page -> 'items' -> 0 ->> 'content_item_id')::uuid = first_id
       or second_page -> 'next_cursor' <> 'null'::jsonb then
        raise exception 'catalog list cursor repeated or skipped its boundary';
    end if;
end
$test$;

rollback;
