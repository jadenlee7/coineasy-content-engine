-- SYNTHETIC-ONLY fixture schema, NOT production migration or schema proof.
-- Run once in a new isolated local PostgreSQL database, then new migration,
-- then content_ops_review_outbox.sql. No network providers or real identifiers.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
    if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin; end if;
end;
$$;
create schema private;
create schema auth;
create schema storage;
create function auth.role() returns text language sql stable as $$
    select current_setting('request.jwt.claim.role', true)
$$;
create table public.workspaces (id uuid primary key, name text, slug text);
create table public.workspace_clients (
    workspace_id uuid references public.workspaces(id), client_id text,
    display_name text, active boolean default true,
    primary key (workspace_id, client_id)
);
create table public.content_items (
    id uuid primary key, workspace_id uuid, client_id text, content_kind text,
    title text, status text, current_version_id uuid,
    unique(workspace_id, id), unique(workspace_id, client_id, id)
);
create table public.content_versions (
    id uuid primary key, workspace_id uuid, content_item_id uuid,
    version_number integer, prompt_version text, title text,
    generation_meta jsonb, deliverables jsonb, channel_copy jsonb,
    created_at timestamptz default statement_timestamp(),
    unique(workspace_id, content_item_id, id),
    foreign key(workspace_id,content_item_id) references public.content_items(workspace_id,id)
);
create table public.source_feeds (
    id uuid primary key, workspace_id uuid, client_id text, provider text,
    name text, handle text, active boolean, poll_interval_minutes integer,
    last_polled_at timestamptz,
    unique(workspace_id,client_id,id)
);
create table public.source_items (
    id uuid primary key, workspace_id uuid, client_id text, source_feed_id uuid,
    source_type text, author_handle text, canonical_url text, external_id text,
    published_at timestamptz, body text, source_hash text,
    unique(workspace_id,client_id,id),
    foreign key(workspace_id,client_id,source_feed_id)
      references public.source_feeds(workspace_id,client_id,id)
);
create table public.content_source_links (
    workspace_id uuid, client_id text, content_item_id uuid, source_item_id uuid, position smallint,
    primary key(content_item_id,source_item_id),
    foreign key(workspace_id,client_id,content_item_id)
      references public.content_items(workspace_id,client_id,id)
);
create table public.jobs (
    id uuid primary key, workspace_id uuid, client_id text, content_item_id uuid,
    job_kind text, status text, input jsonb, output jsonb, finished_at timestamptz,
    foreign key(workspace_id,client_id,content_item_id)
      references public.content_items(workspace_id,client_id,id)
);
create table private.official_x_daily_slots (
    workspace_id uuid, client_id text, kst_date date, job_id uuid,
    primary key(workspace_id,client_id,kst_date)
);
create table public.assets (
    id uuid primary key, workspace_id uuid, content_item_id uuid, content_version_id uuid,
    asset_kind text, storage_bucket text, storage_path text, mime_type text,
    metadata jsonb, sha256 text, byte_size bigint, width integer, height integer,
    foreign key(workspace_id,content_item_id)
      references public.content_items(workspace_id,id)
);
create table storage.objects (id uuid primary key, bucket_id text, name text, unique(bucket_id,name));
create table public.approvals (workspace_id uuid, content_item_id uuid references public.content_items(id));
create table public.publications (workspace_id uuid, content_item_id uuid references public.content_items(id));

-- Only this synthetic database has the helper; runtime tests detect it and
-- keep all generated rows within the enclosing rollback transaction.
create function private.test_content_ops_seed() returns uuid language plpgsql as $$
declare
    workspace uuid := gen_random_uuid();
    client text;
    item uuid;
    version uuid;
    source uuid;
    feed uuid;
    job uuid;
    banner uuid;
    handle text;
    source_url text;
    storage_path text;
begin
    insert into public.workspaces values(workspace,'Synthetic test',workspace::text);
    foreach client in array array['yellow','origintrail','squid','babylon'] loop
        item := gen_random_uuid(); version := gen_random_uuid(); source := gen_random_uuid();
        feed := gen_random_uuid(); job := gen_random_uuid(); banner := gen_random_uuid();
        handle := case client when 'yellow' then '@Yellow' when 'origintrail' then '@origin_trail'
            when 'squid' then '@SquidRouter' else '@babylonlabs_io' end;
        source_url := 'https://x.com/' || substring(handle from 2) || '/status/123456789';
        storage_path := workspace::text || '/' || client || '/' || banner::text || '/news-card.png';
        insert into public.workspace_clients values(workspace,client,client,true);
        insert into public.content_items values(item,workspace,client,'daily_news','Synthetic','needs_review',version);
        insert into public.content_versions(id,workspace_id,content_item_id,version_number,prompt_version,title,
            generation_meta,deliverables,channel_copy,created_at) values(version,workspace,item,1,'test@1','Synthetic',
            jsonb_build_object('mock_mode',false,'request_id',item::text),
            jsonb_build_object('primary_asset_id',banner::text),
            jsonb_build_object('telegram','Synthetic Korean review','x','Synthetic X review'),statement_timestamp());
        insert into public.source_feeds values(feed,workspace,client,'x','Synthetic',handle,true,15,statement_timestamp());
        insert into public.source_items values(source,workspace,client,feed,'tweet',handle,source_url,'123456789',
            statement_timestamp() - interval '1 hour','Synthetic primary source',repeat('a',64));
        insert into public.content_source_links values(workspace,client,item,source,0);
        insert into public.jobs values(job,workspace,client,item,'generate','succeeded',
            jsonb_build_object('workflow','official_x_review_draft_v1','content_kind','daily_news','manual_only',false,
                'kst_date',pg_catalog.timezone('Asia/Seoul',statement_timestamp())::date,
                'request_id',item::text,'source_item_ids',jsonb_build_array(source::text)),
            jsonb_build_object('content_item_id',item::text,'content_version_id',version::text,
                'source_item_ids',jsonb_build_array(source::text)),statement_timestamp());
        insert into private.official_x_daily_slots values(workspace,client,
            pg_catalog.timezone('Asia/Seoul',statement_timestamp())::date,job);
        insert into public.assets values(banner,workspace,item,version,'png','content-studio',storage_path,
            'image/png',jsonb_build_object('filename','news-card.png'),repeat('a',64),1024,1080,1080);
        insert into storage.objects values(gen_random_uuid(),'content-studio',storage_path);
    end loop;
    return workspace;
end;
$$;
