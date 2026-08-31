-- LOCAL DISPOSABLE AUTH STACK ONLY. GoTrue owns/migrates auth before this file.
\set ON_ERROR_STOP on
do $$ begin
  if pg_catalog.to_regclass('auth.mfa_amr_claims') is null
     or pg_catalog.to_regclass('auth.sessions') is null
     or pg_catalog.to_regprocedure('auth.uid()') is null then
    raise exception 'real GoTrue migrations must complete first';
  end if;
end $$;
create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
create role authenticator login noinherit;
grant anon, authenticated, service_role to authenticator;
create schema extensions;
create extension if not exists pgcrypto with schema extensions;
-- Deliberately retain GoTrue's real auth.users/session/factor schemas + auth.uid.
create schema storage;
create table storage.buckets (
 id text primary key, name text not null, public boolean not null default false,
 file_size_limit bigint, allowed_mime_types text[]
);
create table storage.objects (
 id uuid primary key default gen_random_uuid(), bucket_id text not null references storage.buckets(id) on delete cascade,
 name text not null, created_at timestamptz not null default now(), unique(bucket_id,name)
);
alter table storage.objects enable row level security;
grant usage on schema auth to anon,authenticated,service_role;
grant execute on function auth.uid() to anon,authenticated,service_role;
grant usage on schema storage to anon,authenticated,service_role;
grant all on storage.buckets,storage.objects to anon,authenticated,service_role;
