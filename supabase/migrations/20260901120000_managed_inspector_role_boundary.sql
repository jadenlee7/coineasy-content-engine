-- Additive least-privilege boundary for the managed Telegram inspector.
-- The Auth account, JWT claim and PostgREST database role must all use the
-- dedicated role. The JWT audience remains the standard authenticated audience.

begin;

create role coineasy_managed_inspector
    nologin noinherit nosuperuser nocreaterole nocreatedb
    noreplication nobypassrls;

do $role$
begin
    if exists (
        select 1 from pg_catalog.pg_roles
        where rolname = 'coineasy_managed_inspector'
          and (rolsuper or rolcreaterole or rolcreatedb or rolcanlogin
               or rolreplication or rolbypassrls or rolinherit)
    ) then
        raise exception 'managed inspector role is privileged' using errcode = '42501';
    end if;
    if exists (
        select 1 from pg_catalog.pg_auth_members m
        where m.member = 'coineasy_managed_inspector'::regrole
    ) then
        raise exception 'managed inspector role must not inherit another role' using errcode = '42501';
    end if;
end
$role$;

grant usage on schema public to coineasy_managed_inspector;
grant coineasy_managed_inspector to authenticator
    with inherit false, set true, admin false;

create or replace function private.require_managed_telegram_inspect_identity(
    target_workspace_id uuid, target_release_sha text
)
returns jsonb language plpgsql security definer set search_path = '' set timezone = 'UTC' as $$
declare
    claims jsonb;
    actor uuid;
    session_uuid uuid;
    now_at timestamptz := pg_catalog.clock_timestamp();
    session_row auth.sessions%rowtype;
    user_row auth.users%rowtype;
    factor_row auth.mfa_factors%rowtype;
    release_row private.managed_telegram_inspect_releases%rowtype;
    consent_allow private.managed_telegram_inspect_allowlist%rowtype;
    inspect_allow private.managed_telegram_inspect_allowlist%rowtype;
    matched integer;
    totp_claim jsonb;
    totp_at timestamptz;
    fingerprint text;
begin
    if pg_catalog.current_setting('transaction_isolation') <> 'read committed' then
        raise exception 'managed Telegram inspect requires READ COMMITTED' using errcode = '25001';
    end if;
    claims := nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb;
    if pg_catalog.jsonb_typeof(claims) is distinct from 'object'
       or claims ->> 'role' is distinct from 'coineasy_managed_inspector'
       or coalesce(claims ->> 'sub', '') !~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
       or coalesce(claims ->> 'session_id', '') !~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
       or claims ->> 'aal' is distinct from 'aal2'
       or claims -> 'is_anonymous' is distinct from 'false'::jsonb
       or claims ->> 'aud' is distinct from 'authenticated'
       or pg_catalog.jsonb_typeof(claims -> 'amr') is distinct from 'array'
       or pg_catalog.jsonb_typeof(claims -> 'exp') is distinct from 'number'
       or coalesce(claims ->> 'exp', '') !~ '^[0-9]{1,12}$'
       or pg_catalog.jsonb_typeof(claims -> 'iat') is distinct from 'number'
       or coalesce(claims ->> 'iat', '') !~ '^[0-9]{1,12}$'
       or target_workspace_id is null
       or target_release_sha is null or target_release_sha !~ '^[a-f0-9]{40}$' then
        raise exception 'managed Telegram identity denied' using errcode = '42501';
    end if;
    actor := (claims ->> 'sub')::uuid;
    session_uuid := (claims ->> 'session_id')::uuid;
    if actor = '00000000-0000-0000-0000-000000000000'::uuid
       or session_uuid = '00000000-0000-0000-0000-000000000000'::uuid
       or auth.uid() is distinct from actor
       or pg_catalog.to_timestamp((claims ->> 'exp')::double precision) <= now_at
       or pg_catalog.to_timestamp((claims ->> 'iat')::double precision) > now_at
       or (claims ? 'nbf' and (
           pg_catalog.jsonb_typeof(claims -> 'nbf') is distinct from 'number'
           or coalesce(claims ->> 'nbf', '') !~ '^[0-9]{1,12}$'
           or pg_catalog.to_timestamp((claims ->> 'nbf')::double precision) > now_at
       )) then
        raise exception 'managed Telegram identity denied' using errcode = '42501';
    end if;
    select u.* into user_row from auth.users u where u.id = actor;
    if not found or user_row.role is distinct from 'coineasy_managed_inspector'
       or user_row.deleted_at is not null
       or user_row.is_anonymous is distinct from false
       or user_row.banned_until > now_at then
        raise exception 'managed Telegram identity denied' using errcode = '42501';
    end if;
    select s.* into session_row from auth.sessions s where s.id = session_uuid and s.user_id = actor;
    if not found or session_row.aal::text is distinct from 'aal2'
       or (session_row.not_after is not null and session_row.not_after <= now_at)
       or session_row.factor_id is null then
        raise exception 'managed Telegram live session denied' using errcode = '42501';
    end if;
    select f.* into factor_row from auth.mfa_factors f
    where f.id = session_row.factor_id and f.user_id = actor
      and f.factor_type::text = 'totp' and f.status::text = 'verified';
    if not found then
        raise exception 'managed Telegram MFA denied' using errcode = '42501';
    end if;
    select count(*) into matched from pg_catalog.jsonb_array_elements(claims -> 'amr') a
        where a ->> 'method' = 'totp';
    if matched <> 1 then
        raise exception 'managed Telegram MFA denied' using errcode = '42501';
    end if;
    select a into totp_claim from pg_catalog.jsonb_array_elements(claims -> 'amr') a where a ->> 'method' = 'totp';
    if pg_catalog.jsonb_typeof(totp_claim -> 'timestamp') is distinct from 'number'
       or coalesce(totp_claim ->> 'timestamp', '') !~ '^[0-9]{1,12}$' then
        raise exception 'managed Telegram MFA denied' using errcode = '42501';
    end if;
    select a.updated_at into totp_at from auth.mfa_amr_claims a
        where a.session_id = session_uuid and a.authentication_method = 'totp';
    if not found or totp_at is null or totp_at > now_at or totp_at < now_at - interval '10 minutes'
       or (totp_claim ->> 'timestamp')::bigint is distinct from pg_catalog.floor(extract(epoch from totp_at))::bigint then
        raise exception 'managed Telegram MFA denied' using errcode = '42501';
    end if;
    -- No general content-write authority in ANY active workspace membership.
    if exists (select 1 from public.workspace_members m where m.user_id = actor
        and m.status = 'active' and m.role in ('owner', 'admin', 'editor'))
       or exists (select 1 from private.managed_telegram_inspect_revocations r
        where (r.target_type = 'user' and r.target_id = actor)
           or (r.target_type = 'session' and r.target_id = session_uuid)) then
        raise exception 'managed Telegram dedicated operator denied' using errcode = '42501';
    end if;
    select count(*) into matched from private.managed_telegram_inspect_releases r
      where r.workspace_id = target_workspace_id and r.release_sha = target_release_sha
        and r.enabled and r.valid_from <= now_at and r.expires_at > now_at
        and not exists (select 1 from private.managed_telegram_inspect_revocations v where v.target_type = 'release' and v.target_id = r.release_id);
    if matched <> 1 then
        raise exception 'managed Telegram release denied' using errcode = '42501';
    end if;
    select r.* into release_row from private.managed_telegram_inspect_releases r
      where r.workspace_id = target_workspace_id and r.release_sha = target_release_sha
        and r.enabled and r.valid_from <= now_at and r.expires_at > now_at
        and not exists (select 1 from private.managed_telegram_inspect_revocations v where v.target_type = 'release' and v.target_id = r.release_id);
    if claims ->> 'iss' is distinct from 'https://' || release_row.project_ref || '.supabase.co/auth/v1' then
        raise exception 'managed Telegram issuer denied' using errcode = '42501';
    end if;
    select count(*) into matched from private.managed_telegram_inspect_allowlist a
      where a.user_id = actor and a.workspace_id = target_workspace_id
        and a.enabled and a.valid_from <= now_at and a.expires_at > now_at
        and not exists (select 1 from private.managed_telegram_inspect_revocations v where v.target_type = 'allowlist' and v.target_id = a.allowlist_id);
    if matched <> 2 then
        raise exception 'managed Telegram allowlist denied' using errcode = '42501';
    end if;
    select a.* into consent_allow from private.managed_telegram_inspect_allowlist a
      where a.user_id = actor and a.workspace_id = target_workspace_id and a.operation = 'consent_inspect'
        and a.enabled and a.valid_from <= now_at and a.expires_at > now_at
        and not exists (select 1 from private.managed_telegram_inspect_revocations v where v.target_type = 'allowlist' and v.target_id = a.allowlist_id);
    if not found then raise exception 'managed Telegram allowlist denied' using errcode = '42501'; end if;
    select a.* into inspect_allow from private.managed_telegram_inspect_allowlist a
      where a.user_id = actor and a.workspace_id = target_workspace_id and a.operation = 'inspect'
        and a.enabled and a.valid_from <= now_at and a.expires_at > now_at
        and not exists (select 1 from private.managed_telegram_inspect_revocations v where v.target_type = 'allowlist' and v.target_id = a.allowlist_id);
    if not found or inspect_allow.approved_by is distinct from consent_allow.approved_by then
        raise exception 'managed Telegram allowlist denied' using errcode = '42501';
    end if;
    -- The password hash is only an internal digest input, never returned or
    -- stored verbatim. recovery_sent_at conservatively invalidates consent on
    -- a recovery REQUEST too; it is not evidence of recovery completion.
    -- Factor creation/removal invalidates consent; challenge/refresh does not.
    fingerprint := private.managed_telegram_inspect_hash(pg_catalog.jsonb_build_object(
        'password_state', user_row.encrypted_password, 'recovery_requested_at', user_row.recovery_sent_at,
        'session_factor_id', factor_row.id,
        'verified_totp_factors', (select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('id', f.id, 'created_at', f.created_at) order by f.id)
            from auth.mfa_factors f where f.user_id = actor and f.factor_type::text = 'totp' and f.status::text = 'verified')
    ));
    return pg_catalog.jsonb_build_object(
        'user_id', actor, 'session_id', session_uuid, 'workspace_id', target_workspace_id,
        'inspected_by', 'auth:' || actor::text, 'approved_by', inspect_allow.approved_by,
        'project_ref', release_row.project_ref, 'release_id', release_row.release_id,
        'release_sha', release_row.release_sha, 'migration_sha256', release_row.migration_sha256,
        'verified_deployment_reference', release_row.verified_deployment_reference,
        'consent_allowlist_id', consent_allow.allowlist_id, 'inspect_allowlist_id', inspect_allow.allowlist_id,
        'auth_fingerprint_sha256', fingerprint,
        'expires_at', least(consent_allow.expires_at, inspect_allow.expires_at, release_row.expires_at,
            totp_at + interval '10 minutes', session_row.not_after,
            pg_catalog.to_timestamp((claims ->> 'exp')::double precision))
    );
exception
    when invalid_text_representation or numeric_value_out_of_range or invalid_datetime_format or datetime_field_overflow then
        raise exception 'managed Telegram identity denied' using errcode = '42501';
end;
$$;

alter function private.require_managed_telegram_inspect_identity(uuid,text) owner to postgres;
revoke all on function private.require_managed_telegram_inspect_identity(uuid,text)
    from public, anon, authenticated, service_role, coineasy_telegram_resolution, coineasy_managed_inspector;

revoke all on function public.managed_telegram_inspect_context(uuid,text)
    from public, anon, authenticated, service_role, coineasy_telegram_resolution, coineasy_managed_inspector;
revoke all on function public.register_managed_telegram_inspect_consent(uuid,jsonb,text)
    from public, anon, authenticated, service_role, coineasy_telegram_resolution, coineasy_managed_inspector;
revoke all on function public.inspect_managed_telegram_delivery_unknown(uuid)
    from public, anon, authenticated, service_role, coineasy_telegram_resolution, coineasy_managed_inspector;
grant execute on function public.managed_telegram_inspect_context(uuid,text) to coineasy_managed_inspector;
grant execute on function public.register_managed_telegram_inspect_consent(uuid,jsonb,text) to coineasy_managed_inspector;
grant execute on function public.inspect_managed_telegram_delivery_unknown(uuid) to coineasy_managed_inspector;

commit;
