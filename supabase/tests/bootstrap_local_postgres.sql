-- Minimal Supabase-compatible objects for disposable PostgreSQL migration CI.
-- This file is test-only and must never be applied to a Supabase project.

create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;

create extension if not exists pgcrypto;

create schema auth;
create table auth.users (
    id uuid primary key
);
create or replace function auth.uid()
returns uuid
language sql
stable
as $$
    select nullif(
        current_setting('request.jwt.claim.sub', true),
        ''
    )::uuid
$$;

create schema storage;
create table storage.buckets (
    id text primary key,
    name text not null,
    public boolean not null default false,
    file_size_limit bigint,
    allowed_mime_types text[]
);
create table storage.objects (
    id uuid primary key default gen_random_uuid(),
    bucket_id text not null references storage.buckets(id) on delete cascade,
    name text not null,
    created_at timestamptz not null default now(),
    unique (bucket_id, name)
);
alter table storage.objects enable row level security;

grant usage on schema auth to anon, authenticated, service_role;
grant execute on function auth.uid() to anon, authenticated, service_role;
grant usage on schema storage to anon, authenticated, service_role;
grant all on table storage.buckets, storage.objects
    to anon, authenticated, service_role;
