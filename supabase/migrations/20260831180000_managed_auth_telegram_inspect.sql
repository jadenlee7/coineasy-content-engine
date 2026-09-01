-- Additive, disabled-by-default managed-user inspect path. No Auth Hook,
-- dedicated-JWT RPC/grant changes, seeds, provider calls or resolution writes.
-- Consent registration is a separate immutable write. Inspect does not consume
-- consent and does NOT claim server-side/global exactly-once execution.

begin;
do $$
begin
    if not exists (select 1 from pg_catalog.pg_roles where rolname = 'postgres' and (rolsuper or rolbypassrls)) then
        raise exception 'managed Telegram definer owner prerequisites missing' using errcode = '42501';
    end if;
end;
$$;

create table private.managed_telegram_inspect_releases (
    release_id uuid primary key,
    workspace_id uuid not null references public.workspaces(id),
    project_ref text not null check (project_ref ~ '^[a-z]{20}$'),
    release_sha text not null check (release_sha ~ '^[a-f0-9]{40}$'),
    migration_sha256 text not null check (migration_sha256 ~ '^[a-f0-9]{64}$'),
    verified_deployment_reference text not null
        check (verified_deployment_reference ~ '^[A-Za-z0-9:._/-]{3,200}$'),
    enabled boolean not null default false,
    valid_from timestamptz not null default pg_catalog.clock_timestamp(),
    expires_at timestamptz not null,
    check (expires_at > valid_from)
);

create table private.managed_telegram_inspect_allowlist (
    allowlist_id uuid primary key,
    user_id uuid not null references auth.users(id),
    workspace_id uuid not null references public.workspaces(id),
    operation text not null check (operation in ('consent_inspect', 'inspect')),
    approved_by text not null check (approved_by ~ '^[A-Za-z0-9@._:-]{3,120}$'),
    enabled boolean not null default false,
    valid_from timestamptz not null default pg_catalog.clock_timestamp(),
    expires_at timestamptz not null,
    check (expires_at > valid_from)
);

create table private.managed_telegram_inspect_consents (
    consent_id uuid primary key,
    user_id uuid not null references auth.users(id),
    session_id uuid not null,
    workspace_id uuid not null references public.workspaces(id),
    release_id uuid not null references private.managed_telegram_inspect_releases(release_id),
    consent_allowlist_id uuid not null references private.managed_telegram_inspect_allowlist(allowlist_id),
    inspect_allowlist_id uuid not null references private.managed_telegram_inspect_allowlist(allowlist_id),
    operation text not null default 'inspect' check (operation = 'inspect'),
    request jsonb not null check (pg_catalog.jsonb_typeof(request) = 'object'),
    request_sha256 text not null check (request_sha256 ~ '^[a-f0-9]{64}$'),
    public_audit_sha256 text not null check (public_audit_sha256 ~ '^[a-f0-9]{64}$'),
    auth_fingerprint_sha256 text not null check (auth_fingerprint_sha256 ~ '^[a-f0-9]{64}$'),
    consented_at timestamptz not null default pg_catalog.clock_timestamp(),
    expires_at timestamptz not null,
    resend_authorized boolean not null default false check (not resend_authorized),
    automatic_publication boolean not null default false check (not automatic_publication),
    max_external_actions integer not null default 0 check (max_external_actions = 0),
    check (expires_at > consented_at and expires_at <= consented_at + interval '10 minutes')
);

create table private.managed_telegram_inspect_revocations (
    revocation_id uuid primary key,
    target_type text not null check (target_type in ('release', 'allowlist', 'consent', 'user', 'session')),
    target_id uuid not null,
    reason_code text not null check (reason_code ~ '^[a-z][a-z0-9_]{2,63}$'),
    revoked_at timestamptz not null default pg_catalog.clock_timestamp(),
    unique (target_type, target_id)
);

create index managed_telegram_inspect_releases_lookup
    on private.managed_telegram_inspect_releases(workspace_id, release_sha);
create index managed_telegram_inspect_allowlist_lookup
    on private.managed_telegram_inspect_allowlist(user_id, workspace_id, operation);

create function private.deny_managed_telegram_inspect_ledger_mutation()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
    raise exception 'managed Telegram inspect ledger is immutable' using errcode = '23514';
end;
$$;

do $$
declare relation_name text;
begin
    foreach relation_name in array array[
        'managed_telegram_inspect_releases', 'managed_telegram_inspect_allowlist',
        'managed_telegram_inspect_consents', 'managed_telegram_inspect_revocations'
    ] loop
        execute pg_catalog.format('alter table private.%I enable row level security', relation_name);
        execute pg_catalog.format('alter table private.%I force row level security', relation_name);
        execute pg_catalog.format('revoke all on table private.%I from public, anon, authenticated, service_role, coineasy_telegram_resolution', relation_name);
        execute pg_catalog.format('create trigger managed_inspect_immutable before update or delete on private.%I for each row execute function private.deny_managed_telegram_inspect_ledger_mutation()', relation_name);
        execute pg_catalog.format('create trigger managed_inspect_no_truncate before truncate on private.%I for each statement execute function private.deny_managed_telegram_inspect_ledger_mutation()', relation_name);
    end loop;
end;
$$;

create function private.managed_telegram_inspect_hash(value jsonb)
returns text language sql immutable set search_path = '' as $$
    select pg_catalog.encode(extensions.digest(pg_catalog.convert_to(value::text, 'UTF8'), 'sha256'), 'hex')
$$;

-- This gate consumes already verified PostgREST claims; it does not mint,
-- rewrite or elevate JWTs. auth.uid must independently agree with the sub.
-- Schema contract: Supabase Auth v2.189.0 users/sessions/mfa_factors/mfa_amr_claims.
create function private.require_managed_telegram_inspect_identity(
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
       or claims ->> 'role' is distinct from 'authenticated'
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
    if not found or user_row.deleted_at is not null
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

create function private.validate_managed_telegram_inspect_request(target_request jsonb, target_now timestamptz)
returns void language plpgsql set search_path = '' set timezone = 'UTC' as $$
declare
    key_name text;
    ids uuid[] := '{}'::uuid[];
    audit jsonb;
    checked_at timestamptz;
    expiry timestamptz;
    first_id bigint;
    last_id bigint;
begin
    if target_request is null or pg_catalog.jsonb_typeof(target_request) <> 'object'
       or pg_catalog.octet_length(target_request::text) > 32768
       or (select pg_catalog.array_agg(k order by k collate "C") from pg_catalog.jsonb_object_keys(target_request) k)
        is distinct from array['approved_by','client_id','content_item_id','content_version_id','environment','expires_at','inspected_by','job_id','operator_approval_id','project_ref','public_audit','publication_id','release_sha','resolution_id','schema_version','workspace_id']::text[]
       or target_request ->> 'schema_version' is distinct from 'telegram-resolution-inspect-request@1'
       or target_request ->> 'environment' is distinct from 'production'
       or target_request ->> 'client_id' is distinct from 'squid'
       or coalesce(target_request ->> 'project_ref', '') !~ '^[a-z]{20}$'
       or coalesce(target_request ->> 'release_sha', '') !~ '^[a-f0-9]{40}$'
       or coalesce(target_request ->> 'inspected_by', '') !~ '^[A-Za-z0-9@._:-]{3,120}$'
       or coalesce(target_request ->> 'approved_by', '') !~ '^[A-Za-z0-9@._:-]{3,120}$'
       or coalesce(target_request ->> 'expires_at', '') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' then
        raise exception 'managed Telegram request invalid' using errcode = '22023';
    end if;
    foreach key_name in array array['schema_version','environment','client_id','project_ref','release_sha','inspected_by','approved_by','expires_at','workspace_id','content_item_id','content_version_id','publication_id','job_id','resolution_id','operator_approval_id'] loop
        if pg_catalog.jsonb_typeof(target_request -> key_name) is distinct from 'string' then
            raise exception 'managed Telegram request invalid' using errcode = '22023';
        end if;
    end loop;
    foreach key_name in array array['workspace_id','content_item_id','content_version_id','publication_id','job_id','resolution_id','operator_approval_id'] loop
        if target_request ->> key_name !~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
           or target_request ->> key_name = '00000000-0000-0000-0000-000000000000' then
            raise exception 'managed Telegram request invalid' using errcode = '22023';
        end if;
        ids := pg_catalog.array_append(ids, (target_request ->> key_name)::uuid);
    end loop;
    if (select count(distinct id) from pg_catalog.unnest(ids) id) <> 7 then
        raise exception 'managed Telegram request invalid' using errcode = '22023';
    end if;
    expiry := (target_request ->> 'expires_at')::timestamptz;
    if pg_catalog.to_char(expiry, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') <> target_request ->> 'expires_at'
       or expiry <= target_now or expiry > target_now + interval '2 hours' then
        raise exception 'managed Telegram request expired' using errcode = '22023';
    end if;
    audit := target_request -> 'public_audit';
    if pg_catalog.jsonb_typeof(audit) is distinct from 'object'
       or pg_catalog.octet_length(audit::text) > 4096
       or (select pg_catalog.array_agg(k order by k collate "C") from pg_catalog.jsonb_object_keys(audit) k)
        is distinct from array['caption_match_count','checked_at','first_message_id','last_message_id','message_count','png_match_count','public_channel','scan_source','schema_version','snapshot_sha256']::text[]
       or audit ->> 'schema_version' is distinct from 'telegram-public-channel-audit@1'
       or audit ->> 'scan_source' is distinct from 'public_telegram_web_history'
       or audit ->> 'public_channel' is distinct from 'squid_kor_update'
       or audit -> 'caption_match_count' is distinct from '0'::jsonb
       or audit -> 'png_match_count' is distinct from '0'::jsonb
       or pg_catalog.jsonb_typeof(audit -> 'message_count') is distinct from 'number'
       or coalesce(audit ->> 'message_count', '') !~ '^[1-9][0-9]{0,3}$'
       or coalesce(audit ->> 'first_message_id', '') !~ '^[1-9][0-9]{0,18}$'
       or coalesce(audit ->> 'last_message_id', '') !~ '^[1-9][0-9]{0,18}$'
       or coalesce(audit ->> 'snapshot_sha256', '') !~ '^[a-f0-9]{64}$'
       or coalesce(audit ->> 'checked_at', '') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' then
        raise exception 'managed Telegram public audit invalid' using errcode = '22023';
    end if;
    foreach key_name in array array['schema_version','scan_source','public_channel','first_message_id','last_message_id','snapshot_sha256','checked_at'] loop
        if pg_catalog.jsonb_typeof(audit -> key_name) is distinct from 'string' then
            raise exception 'managed Telegram public audit invalid' using errcode = '22023';
        end if;
    end loop;
    first_id := (audit ->> 'first_message_id')::bigint;
    last_id := (audit ->> 'last_message_id')::bigint;
    checked_at := (audit ->> 'checked_at')::timestamptz;
    if pg_catalog.to_char(checked_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') <> audit ->> 'checked_at'
       or checked_at > target_now or checked_at < target_now - interval '30 minutes'
       or first_id > last_id or (audit ->> 'message_count')::integer not between 1 and 1000
       or (audit ->> 'message_count')::integer > last_id - first_id + 1 then
        raise exception 'managed Telegram public audit invalid' using errcode = '22023';
    end if;
exception
    when invalid_text_representation or numeric_value_out_of_range or invalid_datetime_format or datetime_field_overflow then
        raise exception 'managed Telegram request invalid' using errcode = '22023';
end;
$$;

create function private.managed_telegram_inspect_fresh_subject(target_request jsonb)
returns jsonb language plpgsql security definer set search_path = '' set timezone = 'UTC' as $$
declare
    subject jsonb;
    workspace_uuid uuid := (target_request ->> 'workspace_id')::uuid;
    item_uuid uuid := (target_request ->> 'content_item_id')::uuid;
    version_uuid uuid := (target_request ->> 'content_version_id')::uuid;
    publication_uuid uuid := (target_request ->> 'publication_id')::uuid;
    job_uuid uuid := (target_request ->> 'job_id')::uuid;
    resolution_uuid uuid := (target_request ->> 'resolution_id')::uuid;
    approval_uuid uuid := (target_request ->> 'operator_approval_id')::uuid;
begin
    if (select count(*) from public.jobs j where j.workspace_id = workspace_uuid
          and j.content_item_id = item_uuid and j.job_kind = 'publish'
          and j.input ->> 'channel' = 'telegram' and j.input ->> 'workflow' = 'exact_telegram_publication_v1'
          and j.input ->> 'content_version_id' = version_uuid::text) <> 1
       or (select count(*) from public.publications p where p.workspace_id = workspace_uuid
          and p.content_item_id = item_uuid and p.content_version_id = version_uuid and p.channel = 'telegram') <> 1 then
        raise exception 'managed Telegram duplicate tuple denied' using errcode = '23505';
    end if;
    subject := private.exact_telegram_delivery_resolution_subject(
        workspace_uuid, item_uuid, version_uuid, publication_uuid, job_uuid,
        resolution_uuid, approval_uuid, target_request ->> 'approved_by',
        (target_request ->> 'expires_at')::timestamptz, pg_catalog.clock_timestamp(),
        target_request ->> 'release_sha', target_request -> 'public_audit'
    );
    -- This path is fresh-only: an existing approval/receipt is not a successful
    -- reuse, regardless of whether the exact payload would match an old RPC.
    if exists (select 1 from private.exact_telegram_delivery_unknown_approvals a
        where a.operator_approval_id = approval_uuid or a.resolution_id = resolution_uuid
           or (a.workspace_id = workspace_uuid and (a.publication_id = publication_uuid or a.job_id = job_uuid)))
       or exists (select 1 from private.exact_telegram_delivery_unknown_resolutions r
        where r.operator_approval_id = approval_uuid or r.resolution_id = resolution_uuid
           or (r.workspace_id = workspace_uuid and (r.publication_id = publication_uuid or r.job_id = job_uuid))) then
        raise exception 'managed Telegram nonfresh inspection denied' using errcode = '23505';
    end if;
    if pg_catalog.octet_length(subject::text) > 16384 then
        raise exception 'managed Telegram subject exceeds bound' using errcode = '22023';
    end if;
    return subject;
end;
$$;

create function public.managed_telegram_inspect_context(target_workspace_id uuid, target_release_sha text)
returns jsonb language plpgsql security definer set search_path = '' set timezone = 'UTC' as $$
declare identity jsonb;
begin
    identity := private.require_managed_telegram_inspect_identity(target_workspace_id, target_release_sha);
    if (identity ->> 'expires_at')::timestamptz <= pg_catalog.clock_timestamp() then
        raise exception 'managed Telegram context expired' using errcode = '42501';
    end if;
    return (identity - array['session_id','consent_allowlist_id','inspect_allowlist_id','auth_fingerprint_sha256'])
        || pg_catalog.jsonb_build_object('schema_version', 'managed-telegram-inspect-context@1');
end;
$$;

create function public.register_managed_telegram_inspect_consent(
    target_consent_id uuid, target_request jsonb, target_request_sha256 text
)
returns jsonb language plpgsql security definer set search_path = '' set timezone = 'UTC' as $$
declare
    identity jsonb;
    final_identity jsonb;
    consent private.managed_telegram_inspect_consents%rowtype;
    now_at timestamptz := pg_catalog.clock_timestamp();
    reused boolean := false;
begin
    perform private.validate_managed_telegram_inspect_request(target_request, now_at);
    identity := private.require_managed_telegram_inspect_identity(
        (target_request ->> 'workspace_id')::uuid, target_request ->> 'release_sha');
    if target_consent_id is null or target_consent_id = '00000000-0000-0000-0000-000000000000'::uuid
       or exists (select 1 from pg_catalog.jsonb_each_text(target_request) e where e.value = target_consent_id::text)
       or target_request_sha256 is null or target_request_sha256 !~ '^[a-f0-9]{64}$'
       or target_request_sha256 is distinct from private.managed_telegram_inspect_hash(target_request)
       or target_request ->> 'project_ref' is distinct from identity ->> 'project_ref'
       or target_request ->> 'inspected_by' is distinct from identity ->> 'inspected_by'
       or target_request ->> 'approved_by' is distinct from identity ->> 'approved_by'
       or exists (select 1 from private.managed_telegram_inspect_revocations r where r.target_type = 'consent' and r.target_id = target_consent_id) then
        raise exception 'managed Telegram consent denied' using errcode = '42501';
    end if;
    -- Register only a currently valid exact candidate. This helper does not
    -- write the source rows or create a resolution approval.
    perform private.managed_telegram_inspect_fresh_subject(target_request);
    final_identity := private.require_managed_telegram_inspect_identity(
        (target_request ->> 'workspace_id')::uuid, target_request ->> 'release_sha');
    now_at := pg_catalog.clock_timestamp();
    perform private.validate_managed_telegram_inspect_request(target_request, now_at);
    if identity - 'expires_at' is distinct from final_identity - 'expires_at'
       or (final_identity ->> 'expires_at')::timestamptz <= now_at
       or exists (select 1 from private.managed_telegram_inspect_revocations r where r.target_type = 'consent' and r.target_id = target_consent_id) then
        raise exception 'managed Telegram consent identity changed' using errcode = '42501';
    end if;
    identity := final_identity;
    insert into private.managed_telegram_inspect_consents (
        consent_id, user_id, session_id, workspace_id, release_id, consent_allowlist_id, inspect_allowlist_id,
        request, request_sha256, public_audit_sha256, auth_fingerprint_sha256, consented_at, expires_at
    ) values (
        target_consent_id, (identity ->> 'user_id')::uuid, (identity ->> 'session_id')::uuid,
        (identity ->> 'workspace_id')::uuid, (identity ->> 'release_id')::uuid,
        (identity ->> 'consent_allowlist_id')::uuid, (identity ->> 'inspect_allowlist_id')::uuid,
        target_request, target_request_sha256, private.managed_telegram_inspect_hash(target_request -> 'public_audit'),
        identity ->> 'auth_fingerprint_sha256', now_at,
        least((target_request ->> 'expires_at')::timestamptz, now_at + interval '10 minutes', (identity ->> 'expires_at')::timestamptz)
    ) on conflict (consent_id) do nothing returning * into consent;
    if not found then
        reused := true;
        select c.* into consent from private.managed_telegram_inspect_consents c where c.consent_id = target_consent_id;
        if not found or consent.user_id::text is distinct from identity ->> 'user_id'
           or consent.session_id::text is distinct from identity ->> 'session_id'
           or consent.request is distinct from target_request or consent.request_sha256 is distinct from target_request_sha256
           or consent.release_id::text is distinct from identity ->> 'release_id'
           or consent.consent_allowlist_id::text is distinct from identity ->> 'consent_allowlist_id'
           or consent.inspect_allowlist_id::text is distinct from identity ->> 'inspect_allowlist_id'
           or consent.auth_fingerprint_sha256 is distinct from identity ->> 'auth_fingerprint_sha256'
           or consent.expires_at <= pg_catalog.clock_timestamp() then
            raise exception 'managed Telegram consent conflict' using errcode = '23505';
        end if;
    end if;
    -- A conflicting registration may have waited on another transaction. It
    -- must not return an expired or newly revoked consent after that wait.
    final_identity := private.require_managed_telegram_inspect_identity(
        (target_request ->> 'workspace_id')::uuid, target_request ->> 'release_sha');
    now_at := pg_catalog.clock_timestamp();
    perform private.validate_managed_telegram_inspect_request(target_request, now_at);
    if identity - 'expires_at' is distinct from final_identity - 'expires_at'
       or consent.expires_at <= now_at or (final_identity ->> 'expires_at')::timestamptz <= now_at
       or exists (select 1 from private.managed_telegram_inspect_revocations r where r.target_type = 'consent' and r.target_id = target_consent_id) then
        raise exception 'managed Telegram consent expired or changed' using errcode = '42501';
    end if;
    return pg_catalog.jsonb_build_object(
        'schema_version', 'managed-telegram-inspect-consent@1', 'consent_id', consent.consent_id,
        'request_sha256', consent.request_sha256, 'public_audit_sha256', consent.public_audit_sha256,
        'consented_at', consent.consented_at, 'expires_at', consent.expires_at, 'reused', reused
    );
end;
$$;

create function public.inspect_managed_telegram_delivery_unknown(target_consent_id uuid)
returns jsonb language plpgsql security definer set search_path = '' set timezone = 'UTC' as $$
declare
    consent private.managed_telegram_inspect_consents%rowtype;
    identity jsonb;
    final_identity jsonb;
    subject jsonb;
    response jsonb;
    now_at timestamptz := pg_catalog.clock_timestamp();
begin
    select c.* into consent from private.managed_telegram_inspect_consents c where c.consent_id = target_consent_id;
    if not found then raise exception 'managed Telegram consent denied' using errcode = '42501'; end if;
    identity := private.require_managed_telegram_inspect_identity(consent.workspace_id, consent.request ->> 'release_sha');
    if consent.user_id::text is distinct from identity ->> 'user_id'
       or consent.session_id::text is distinct from identity ->> 'session_id'
       or consent.release_id::text is distinct from identity ->> 'release_id'
       or consent.consent_allowlist_id::text is distinct from identity ->> 'consent_allowlist_id'
       or consent.inspect_allowlist_id::text is distinct from identity ->> 'inspect_allowlist_id'
       or consent.auth_fingerprint_sha256 is distinct from identity ->> 'auth_fingerprint_sha256'
       or consent.request ->> 'inspected_by' is distinct from identity ->> 'inspected_by'
       or consent.request ->> 'approved_by' is distinct from identity ->> 'approved_by'
       or consent.request ->> 'project_ref' is distinct from identity ->> 'project_ref'
       or consent.consented_at > now_at or consent.expires_at <= now_at
       or consent.request_sha256 is distinct from private.managed_telegram_inspect_hash(consent.request)
       or consent.public_audit_sha256 is distinct from private.managed_telegram_inspect_hash(consent.request -> 'public_audit')
       or exists (select 1 from private.managed_telegram_inspect_revocations r where r.target_type = 'consent' and r.target_id = consent.consent_id) then
        raise exception 'managed Telegram consent denied' using errcode = '42501';
    end if;
    perform private.validate_managed_telegram_inspect_request(consent.request, now_at);
    subject := private.managed_telegram_inspect_fresh_subject(consent.request);
    final_identity := private.require_managed_telegram_inspect_identity(consent.workspace_id, consent.request ->> 'release_sha');
    now_at := pg_catalog.clock_timestamp();
    perform private.validate_managed_telegram_inspect_request(consent.request, now_at);
    if identity - 'expires_at' is distinct from final_identity - 'expires_at'
       or consent.expires_at <= now_at or (final_identity ->> 'expires_at')::timestamptz <= now_at
       or exists (select 1 from private.managed_telegram_inspect_revocations r where r.target_type = 'consent' and r.target_id = consent.consent_id) then
        raise exception 'managed Telegram inspection expired or changed' using errcode = '42501';
    end if;
    response := pg_catalog.jsonb_build_object(
        'eligible', true, 'resolved', false, 'reused', false,
        'resolution_id', consent.request ->> 'resolution_id', 'publication_id', consent.request ->> 'publication_id',
        'job_id', consent.request ->> 'job_id', 'content_item_id', consent.request ->> 'content_item_id',
        'content_version_id', consent.request ->> 'content_version_id', 'delivery_outcome', 'unknown',
        'disposition', 'operator_closed_without_resend', 'public_observation', 'not_observed_at_checked_at',
        'approval_subject', subject, 'approval_subject_sha256', private.managed_telegram_inspect_hash(subject),
        'approved', false, 'approved_at', null, 'resend_authorized', false
    );
    if pg_catalog.octet_length(response::text) > 32768 then
        raise exception 'managed Telegram response exceeds bound' using errcode = '22023';
    end if;
    return response;
end;
$$;

-- Explicit owner and ACLs, including default-PUBLIC EXECUTE removal. No grants
-- on old functions are changed and no ordinary role gets private table DML.
alter function private.deny_managed_telegram_inspect_ledger_mutation() owner to postgres;
alter function private.managed_telegram_inspect_hash(jsonb) owner to postgres;
alter function private.require_managed_telegram_inspect_identity(uuid,text) owner to postgres;
alter function private.validate_managed_telegram_inspect_request(jsonb,timestamptz) owner to postgres;
alter function private.managed_telegram_inspect_fresh_subject(jsonb) owner to postgres;
alter function public.managed_telegram_inspect_context(uuid,text) owner to postgres;
alter function public.register_managed_telegram_inspect_consent(uuid,jsonb,text) owner to postgres;
alter function public.inspect_managed_telegram_delivery_unknown(uuid) owner to postgres;
revoke all on function private.deny_managed_telegram_inspect_ledger_mutation() from public, anon, authenticated, service_role, coineasy_telegram_resolution;
revoke all on function private.managed_telegram_inspect_hash(jsonb) from public, anon, authenticated, service_role, coineasy_telegram_resolution;
revoke all on function private.require_managed_telegram_inspect_identity(uuid,text) from public, anon, authenticated, service_role, coineasy_telegram_resolution;
revoke all on function private.validate_managed_telegram_inspect_request(jsonb,timestamptz) from public, anon, authenticated, service_role, coineasy_telegram_resolution;
revoke all on function private.managed_telegram_inspect_fresh_subject(jsonb) from public, anon, authenticated, service_role, coineasy_telegram_resolution;
revoke all on function public.managed_telegram_inspect_context(uuid,text) from public, anon, authenticated, service_role, coineasy_telegram_resolution;
revoke all on function public.register_managed_telegram_inspect_consent(uuid,jsonb,text) from public, anon, authenticated, service_role, coineasy_telegram_resolution;
revoke all on function public.inspect_managed_telegram_delivery_unknown(uuid) from public, anon, authenticated, service_role, coineasy_telegram_resolution;
grant execute on function public.managed_telegram_inspect_context(uuid,text) to authenticated;
grant execute on function public.register_managed_telegram_inspect_consent(uuid,jsonb,text) to authenticated;
grant execute on function public.inspect_managed_telegram_delivery_unknown(uuid) to authenticated;

commit;
