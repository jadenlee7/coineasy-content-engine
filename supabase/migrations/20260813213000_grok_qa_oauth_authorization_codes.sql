-- One-time PKCE authorization codes for the Grok Bot custom MCP OAuth adapter.
-- The connector remains QA-only: these rows cannot approve, publish, dispatch,
-- call a model, or select a destination.  The scoped role receives RPC execute
-- only and no relation privileges.

begin;

create table private.grok_qa_oauth_codes (
    code_sha256 text primary key
        check (code_sha256 ~ '^[a-f0-9]{64}$'),
    client_id_sha256 text not null
        check (client_id_sha256 ~ '^[a-f0-9]{64}$'),
    redirect_uri text not null
        check (
            length(redirect_uri) between 8 and 2048
            and redirect_uri !~ '[[:cntrl:][:space:]]'
        ),
    resource text not null
        check (
            length(resource) between 16 and 2048
            and resource ~ '^https://'
            and resource !~ '[[:cntrl:][:space:]]'
        ),
    scope text not null check (scope = 'coineasy.qa'),
    code_challenge text not null
        check (code_challenge ~ '^[A-Za-z0-9_-]{43,128}$'),
    expires_at timestamptz not null,
    consumed_at timestamptz,
    created_at timestamptz not null default statement_timestamp(),
    check (expires_at > created_at),
    check (consumed_at is null or consumed_at >= created_at)
);

create index grok_qa_oauth_codes_expiry_idx
on private.grok_qa_oauth_codes (expires_at)
where consumed_at is null;

alter table private.grok_qa_oauth_codes enable row level security;
alter table private.grok_qa_oauth_codes force row level security;

revoke all on table private.grok_qa_oauth_codes
from public, anon, authenticated, service_role;

create or replace function public.create_grok_qa_oauth_code(
    target_code_sha256 text,
    target_client_id_sha256 text,
    target_redirect_uri text,
    target_resource text,
    target_scope text,
    target_code_challenge text,
    target_expires_at timestamptz
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    current_time constant timestamptz := clock_timestamp();
begin
    if target_code_sha256 !~ '^[a-f0-9]{64}$'
        or target_client_id_sha256 !~ '^[a-f0-9]{64}$'
        or target_redirect_uri is null
        or length(target_redirect_uri) not between 8 and 2048
        or target_redirect_uri ~ '[[:cntrl:][:space:]]'
        or target_resource is null
        or length(target_resource) not between 16 and 2048
        or target_resource !~ '^https://'
        or target_resource ~ '[[:cntrl:][:space:]]'
        or target_scope <> 'coineasy.qa'
        or target_code_challenge !~ '^[A-Za-z0-9_-]{43,128}$'
        or target_expires_at <= current_time
        or target_expires_at > current_time + interval '10 minutes'
    then
        raise exception 'invalid Grok QA OAuth authorization code request';
    end if;

    delete from private.grok_qa_oauth_codes as expired
    where expired.code_sha256 in (
        select candidate.code_sha256
        from private.grok_qa_oauth_codes as candidate
        where candidate.expires_at < current_time - interval '1 day'
        order by candidate.expires_at
        limit 50
        for update skip locked
    );

    insert into private.grok_qa_oauth_codes (
        code_sha256,
        client_id_sha256,
        redirect_uri,
        resource,
        scope,
        code_challenge,
        expires_at
    ) values (
        target_code_sha256,
        target_client_id_sha256,
        target_redirect_uri,
        target_resource,
        target_scope,
        target_code_challenge,
        target_expires_at
    );

    return jsonb_build_object('created', true, 'status', 'created');
exception
    when unique_violation then
        return jsonb_build_object('created', false, 'status', 'conflict');
end;
$function$;

create or replace function public.consume_grok_qa_oauth_code(
    target_code_sha256 text,
    target_client_id_sha256 text,
    target_redirect_uri text,
    target_resource text,
    target_scope text,
    target_code_challenge text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
    current_time constant timestamptz := clock_timestamp();
    authorization_code private.grok_qa_oauth_codes%rowtype;
begin
    if target_code_sha256 !~ '^[a-f0-9]{64}$'
        or target_client_id_sha256 !~ '^[a-f0-9]{64}$'
        or target_scope <> 'coineasy.qa'
        or target_code_challenge !~ '^[A-Za-z0-9_-]{43,128}$'
    then
        return jsonb_build_object('authorized', false, 'status', 'invalid');
    end if;

    select current_code.*
    into authorization_code
    from private.grok_qa_oauth_codes as current_code
    where current_code.code_sha256 = target_code_sha256
    for update;

    if not found
        or authorization_code.consumed_at is not null
        or authorization_code.expires_at <= current_time
        or authorization_code.client_id_sha256 <> target_client_id_sha256
        or authorization_code.redirect_uri <> target_redirect_uri
        or authorization_code.resource <> target_resource
        or authorization_code.scope <> target_scope
        or authorization_code.code_challenge <> target_code_challenge
    then
        return jsonb_build_object('authorized', false, 'status', 'invalid');
    end if;

    update private.grok_qa_oauth_codes
    set consumed_at = current_time
    where code_sha256 = target_code_sha256
        and consumed_at is null;

    if not found then
        return jsonb_build_object('authorized', false, 'status', 'invalid');
    end if;

    return jsonb_build_object('authorized', true, 'status', 'consumed');
end;
$function$;

revoke all on function public.create_grok_qa_oauth_code(
    text, text, text, text, text, text, timestamptz
) from public, anon, authenticated, service_role;
revoke all on function public.consume_grok_qa_oauth_code(
    text, text, text, text, text, text
) from public, anon, authenticated, service_role;

do $role$
begin
    if not exists (
        select 1 from pg_catalog.pg_roles
        where rolname = 'coineasy_grok_qa_oauth'
    ) then
        create role coineasy_grok_qa_oauth nologin noinherit;
    end if;
    alter role coineasy_grok_qa_oauth nologin noinherit nobypassrls;
    grant usage on schema public to coineasy_grok_qa_oauth;
    grant coineasy_grok_qa_oauth to authenticator;
end;
$role$;

grant execute on function public.create_grok_qa_oauth_code(
    text, text, text, text, text, text, timestamptz
) to coineasy_grok_qa_oauth;
grant execute on function public.consume_grok_qa_oauth_code(
    text, text, text, text, text, text
) to coineasy_grok_qa_oauth;

revoke all on table private.grok_qa_oauth_codes
from coineasy_grok_qa_oauth;

commit;
