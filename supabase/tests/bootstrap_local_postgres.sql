-- Minimal Supabase-compatible objects for disposable PostgreSQL migration CI.
-- This file is test-only and must never be applied to a Supabase project.

create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
-- PostgREST's role-switching entry point. Supabase grants it membership in the
-- request roles so it can `set role` from a JWT `role` claim; least-privilege
-- ledger roles are granted to it the same way.
create role authenticator login password 'postgres' noinherit;
grant anon, authenticated, service_role to authenticator;

create schema extensions;
create extension if not exists pgcrypto with schema extensions;

create schema auth;
create table auth.users (
    id uuid primary key,
    encrypted_password text,
    recovery_sent_at timestamptz,
    banned_until timestamptz,
    deleted_at timestamptz,
    is_anonymous boolean not null default false,
    role text not null default 'authenticated'
);
-- Minimal supported GoTrue v2.189.0 columns for SQL-only security fixtures.
-- These stubs do not verify JWT signatures/MFA. The separate live Auth harness
-- lets GoTrue create its actual schema before applying application migrations.
create table auth.mfa_factors (
    id uuid primary key,
    user_id uuid not null references auth.users(id),
    factor_type text not null,
    status text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create table auth.sessions (
    id uuid primary key,
    user_id uuid not null references auth.users(id),
    factor_id uuid,
    aal text,
    not_after timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    refreshed_at timestamptz
);
create table auth.mfa_amr_claims (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null references auth.sessions(id) on delete cascade,
    authentication_method text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (session_id, authentication_method)
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
