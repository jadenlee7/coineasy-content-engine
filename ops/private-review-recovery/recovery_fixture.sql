-- Disposable PostgreSQL only. This is a structural harness, never an apply pack.
create schema private;
create schema auth;
create schema extensions;
create schema storage;
create extension if not exists pgcrypto;
create function auth.role() returns text language sql stable as $$
    select current_setting('request.jwt.claim.role',true) $$;
create function extensions.digest(bytea,text) returns bytea language sql immutable as $$
    select public.digest($1,$2) $$;
create table private.ybo_readiness_babylon_trial_anchor (
    workspace_id uuid, trial_id uuid, plan_sha256 text, release_sha text,
    not_before timestamptz, deadline timestamptz, kst_date date);
create table private.content_ops_review_outbox (
    outbox_id uuid primary key, workspace_id uuid, client_id text, kst_date date,
    destination_role text, content_item_id uuid, content_version_id uuid,
    source_item_id uuid, generate_job_id uuid, banner_asset_id uuid,
    banner_sha256 text, source_url text, source_published_at timestamptz,
    status text, claim_token uuid, claimed_at timestamptz, lease_expires_at timestamptz,
    send_started_at timestamptz, packet_sha256 text, message_id bigint,
    finished_at timestamptz, created_at timestamptz);
create table private.content_ops_button_reviews (
    id uuid primary key, workspace_id uuid, client_id text, content_item_id uuid,
    content_version_id uuid, version_fingerprint text, epoch bigint default 0,
    state text default 'active', created_at timestamptz default clock_timestamp(),
    expires_at timestamptz default (clock_timestamp()+interval '30 minutes'),
    unique(workspace_id,content_version_id));
create table private.content_ops_button_cards (
    id uuid primary key, review_id uuid references private.content_ops_button_reviews(id),
    bindings jsonb, parts jsonb);
create table private.content_ops_button_card_outbox_owners (
    review_id uuid primary key references private.content_ops_button_reviews(id),
    outbox_id uuid unique references private.content_ops_review_outbox(outbox_id),
    claim_token uuid unique, packet_sha256 text, bound_at timestamptz default clock_timestamp());
create table private.content_ops_button_card_send_attempts (
    review_id uuid references private.content_ops_button_reviews(id), card_id uuid,
    part_index smallint, payload_sha256 text, state text, message_id bigint,
    message_binding text, response_sha256 text, reserved_at timestamptz,
    confirmed_at timestamptz, primary key(review_id,part_index));
create table public.assets (
    id uuid primary key, workspace_id uuid, content_item_id uuid, content_version_id uuid,
    asset_kind text, mime_type text, storage_bucket text, metadata jsonb,
    storage_path text, sha256 text, byte_size integer, width integer, height integer);
create table storage.objects (bucket_id text,name text,primary key(bucket_id,name));
create table private.fixture_candidate_switch (enabled boolean not null);
insert into private.fixture_candidate_switch values(true);
create function private.content_ops_review_candidate(uuid,uuid,uuid)
returns jsonb language sql volatile as $$
    select case when (select enabled from private.fixture_candidate_switch)
    then jsonb_build_object('client_id',q.client_id,'kst_date',q.kst_date,
        'content_item_id',q.content_item_id,'content_version_id',q.content_version_id,
        'source_item_id',q.source_item_id,'generate_job_id',q.generate_job_id,
        'banner_asset_id',q.banner_asset_id,'banner_sha256',q.banner_sha256,
        'source_url',q.source_url,'source_published_at',q.source_published_at,
        'telegram_copy','fixed telegram','x_copy','fixed x') else null end
    from private.content_ops_review_outbox q
    where q.workspace_id=$1 and q.content_item_id=$2 and q.content_version_id=$3 $$;
create function private.content_ops_review_matches(jsonb,private.content_ops_review_outbox)
returns boolean language sql stable as $$
    select $1 is not null and $1->>'client_id'=$2.client_id
        and $1->>'content_version_id'=$2.content_version_id::text
        and $1->>'banner_sha256'=$2.banner_sha256 $$;
create function private.content_ops_button_version_fingerprint(uuid,uuid,uuid)
returns text language sql stable as $$select repeat('f',64)$$;
create function private.reserve_content_ops_button_card_send(uuid,uuid,smallint,text)
returns jsonb language sql volatile as $$select jsonb_build_object('status','reserved','new_attempt',true,'execution_authorized',false)$$;
create function private.confirm_content_ops_button_card_send(uuid,uuid,smallint,text,bigint,text,text,timestamptz)
returns jsonb language sql volatile as $$select jsonb_build_object('status','confirmed','new_confirmation',true,'execution_authorized',false)$$;
create function private.record_content_ops_button_card(uuid,uuid,text,bigint,jsonb,jsonb,timestamptz,timestamptz)
returns jsonb language sql volatile as $$select jsonb_build_object('status','card_recorded','card_id',$2,'reused',false,'execution_authorized',false)$$;
create function private.content_ops_button_card_outbox_owned(uuid)
returns boolean language sql volatile as $$select false$$;
