-- LOCAL PROPOSAL ONLY. OFF installation creates no grant, job, send, or retry.
-- Operator must separately insert one exact grant after the protected preflight.
begin;
set local lock_timeout='5s';
set local statement_timeout='60s';
set local timezone='UTC';

-- The transaction is fail-closed on any alteration to the existing owner
-- contract or outbox. The temporary snapshot is never persisted.
create temporary table content_ops_recovery_before on commit drop as
select (select to_jsonb(p)-'prosrc' from pg_proc p where p.oid=
    'private.content_ops_button_card_outbox_owned(uuid)'::regprocedure) owner_catalog,
    (select to_jsonb(q) from private.content_ops_review_outbox q where q.outbox_id=
    '94f31aa1-34dd-4220-9c88-34716e760c47'::uuid) original_outbox;

-- Never replace a drifted legacy ownership gate. The replacement below keeps
-- its original sending branch and appends one exact recovery branch only.
do $$
begin
    if (select encode(extensions.digest(convert_to(p.prosrc,'UTF8'),'sha256'),'hex')
        from pg_proc p where p.oid =
          'private.content_ops_button_card_outbox_owned(uuid)'::regprocedure)
       is distinct from '1fb833251e883058e3f3241f3d2c8708ed15b25dff39e05bcf080ef00ae56823'
    then raise exception 'content_ops_recovery_legacy_owner_body_mismatch'; end if;
end $$;

-- Standalone helpers: the optional agent-work-order schema is not a hosted
-- dependency. Plan keys and values are ASCII identities, integer micros and
-- UTC-second timestamps, matching the JS/Python compact sorted JSON contract.
create function private.content_ops_recovery_json_canonical(target jsonb)
returns text language sql immutable strict set search_path = '' as $$
    select case pg_catalog.jsonb_typeof(target)
        when 'object' then (
            select '{' || coalesce(pg_catalog.string_agg(
                pg_catalog.to_json(pair.key)::text || ':' ||
                private.content_ops_recovery_json_canonical(pair.value),
                ',' order by pg_catalog.convert_to(pair.key,'UTF8')), '') || '}'
            from pg_catalog.jsonb_each(target) as pair(key,value))
        when 'array' then (
            select '[' || coalesce(pg_catalog.string_agg(
                private.content_ops_recovery_json_canonical(element.value),
                ',' order by element.ordinality), '') || ']'
            from pg_catalog.jsonb_array_elements(target) with ordinality as element(value,ordinality))
        else target::text end
$$;
create function private.content_ops_recovery_json_sha256(target jsonb)
returns text language sql immutable strict set search_path = '' as $$
    select pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
        private.content_ops_recovery_json_canonical(target),'UTF8')),'hex')
$$;

create table private.content_ops_review_recovery_grants (
    recovery_id uuid primary key,
    workspace_id uuid not null check (workspace_id = '5cc143cc-b253-4841-8a6c-ab3ac98623a6'::uuid),
    outbox_id uuid not null unique references private.content_ops_review_outbox(outbox_id),
    content_version_id uuid not null unique,
    plan jsonb not null,
    plan_sha256 text not null check (plan_sha256 ~ '^[a-f0-9]{64}$'),
    release_sha text not null check (release_sha ~ '^[a-f0-9]{40}$'),
    original_outbox_sha256 text not null check (original_outbox_sha256 ~ '^[a-f0-9]{64}$'),
    not_before timestamptz not null,
    deadline timestamptz not null,
    created_at timestamptz not null default clock_timestamp(),
    check (outbox_id = '94f31aa1-34dd-4220-9c88-34716e760c47'::uuid),
    check (content_version_id = '43e68255-9797-451d-909f-0dce460a6c18'::uuid),
    check (not_before < deadline and deadline <= not_before + interval '2 hours'),
    check (date_trunc('second', not_before) = not_before and date_trunc('second', deadline) = deadline)
);
create table private.content_ops_review_recovery_claims (
    recovery_id uuid primary key references private.content_ops_review_recovery_grants(recovery_id),
    claim_token uuid not null unique,
    claimed_at timestamptz not null default clock_timestamp()
);
create table private.content_ops_review_recovery_prepared (
    recovery_id uuid primary key references private.content_ops_review_recovery_claims(recovery_id),
    review_id uuid not null unique references private.content_ops_button_reviews(id),
    prepared_at timestamptz not null default clock_timestamp()
);
create table private.content_ops_review_recovery_begins (
    recovery_id uuid primary key references private.content_ops_review_recovery_prepared(recovery_id),
    packet_sha256 text not null check (packet_sha256 ~ '^[a-f0-9]{64}$'),
    begun_at timestamptz not null default clock_timestamp()
);
create table private.content_ops_review_recovery_terminals (
    recovery_id uuid primary key references private.content_ops_review_recovery_begins(recovery_id),
    review_id uuid not null unique references private.content_ops_button_reviews(id),
    card_id uuid not null unique references private.content_ops_button_cards(id),
    recorded_at timestamptz not null default clock_timestamp()
);
revoke all on private.content_ops_review_recovery_grants,
    private.content_ops_review_recovery_claims,
    private.content_ops_review_recovery_prepared,
    private.content_ops_review_recovery_begins,
    private.content_ops_review_recovery_terminals from public, anon, authenticated, service_role;
alter table private.content_ops_review_recovery_grants enable row level security;
alter table private.content_ops_review_recovery_claims enable row level security;
alter table private.content_ops_review_recovery_prepared enable row level security;
alter table private.content_ops_review_recovery_begins enable row level security;
alter table private.content_ops_review_recovery_terminals enable row level security;

create function private.guard_content_ops_recovery_immutable()
returns trigger language plpgsql set search_path = '' as $$
begin
    raise exception 'content_ops_recovery_immutable' using errcode = '55000';
end $$;
create trigger content_ops_review_recovery_grant_immutable
    before update or delete or truncate on private.content_ops_review_recovery_grants
    for each statement execute function private.guard_content_ops_recovery_immutable();
create trigger content_ops_review_recovery_claim_immutable
    before update or delete or truncate on private.content_ops_review_recovery_claims
    for each statement execute function private.guard_content_ops_recovery_immutable();
create trigger content_ops_review_recovery_prepared_immutable
    before update or delete or truncate on private.content_ops_review_recovery_prepared
    for each statement execute function private.guard_content_ops_recovery_immutable();
create trigger content_ops_review_recovery_begin_immutable
    before update or delete or truncate on private.content_ops_review_recovery_begins
    for each statement execute function private.guard_content_ops_recovery_immutable();
create trigger content_ops_review_recovery_terminal_immutable
    before update or delete or truncate on private.content_ops_review_recovery_terminals
    for each statement execute function private.guard_content_ops_recovery_immutable();

-- This is the only INSERT path for the operator-owned grant. Runtime roles have
-- no table grant. A grant cannot be installed with a changed old claim or copy.
create or replace function private.guard_content_ops_recovery_grant()
returns trigger language plpgsql volatile security invoker set search_path = '' as $$
declare
    q private.content_ops_review_outbox;
    candidate jsonb;
    original_anchor private.ybo_readiness_babylon_trial_anchor;
    expected_keys text[] := array['banner_asset_id','banner_sha256','client_id',
        'content_item_id','content_version_id','deadline','destination_policy_sha256',
        'estimated_daily_limit_usd_micros','generate_job_id','not_before',
        'original_outbox_sha256','original_plan_sha256','original_trial_id','outbox_id',
        'recovery_id','release_sha','schema_version','source_item_id',
        'telegram_copy_sha256','workspace_id','x_copy_sha256'];
begin
    if tg_op <> 'INSERT' then raise exception 'content_ops_recovery_immutable' using errcode='55000'; end if;
    if jsonb_typeof(new.plan) is distinct from 'object'
       or not new.plan ?& expected_keys or new.plan - expected_keys <> '{}'::jsonb
       or new.plan->>'schema_version' is distinct from 'private-review-recovery@1'
       or new.plan->>'workspace_id' is distinct from new.workspace_id::text
       or new.plan->>'client_id' is distinct from 'babylon'
       or new.plan->>'outbox_id' is distinct from new.outbox_id::text
       or new.plan->>'content_version_id' is distinct from new.content_version_id::text
       or new.plan->>'recovery_id' is distinct from new.recovery_id::text
       or new.plan->>'release_sha' is distinct from new.release_sha
       or new.plan->>'original_outbox_sha256' is distinct from new.original_outbox_sha256
       or jsonb_typeof(new.plan->'estimated_daily_limit_usd_micros') is distinct from 'number'
       or new.plan->>'estimated_daily_limit_usd_micros' is distinct from '10000000'
       or new.plan->>'not_before' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$'
       or new.plan->>'deadline' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$'
       or (new.plan->>'not_before')::timestamptz is distinct from new.not_before
       or (new.plan->>'deadline')::timestamptz is distinct from new.deadline
       or coalesce(new.plan->>'destination_policy_sha256','') !~ '^[a-f0-9]{64}$'
       or coalesce(new.plan->>'telegram_copy_sha256','') !~ '^[a-f0-9]{64}$'
       or coalesce(new.plan->>'x_copy_sha256','') !~ '^[a-f0-9]{64}$'
       or new.plan_sha256 is distinct from private.content_ops_recovery_json_sha256(new.plan)
       or new.not_before > clock_timestamp()
       or new.deadline <= clock_timestamp()
       or exists(select 1 from private.content_ops_review_recovery_grants)
    then raise exception 'content_ops_recovery_grant_invalid' using errcode='23514'; end if;
    select * into original_anchor from private.ybo_readiness_babylon_trial_anchor
        where workspace_id = new.workspace_id and trial_id =
            'c34dbb8a-ff7c-484e-8e43-757f692ec743'::uuid;
    if not found or new.plan->>'original_trial_id' is distinct from original_anchor.trial_id::text
       or new.plan->>'original_plan_sha256' is distinct from original_anchor.plan_sha256 then
        raise exception 'content_ops_recovery_anchor_invalid' using errcode='23514';
    end if;
    select * into q from private.content_ops_review_outbox
        where outbox_id = new.outbox_id and workspace_id = new.workspace_id for update;
    if not found or q.content_version_id is distinct from new.content_version_id
       or q.client_id is distinct from 'babylon' or q.status is distinct from 'claimed'
       or q.lease_expires_at is null or q.lease_expires_at >= clock_timestamp()
       or q.packet_sha256 is not null or q.send_started_at is not null
       or q.message_id is not null or q.finished_at is not null
       or new.original_outbox_sha256 is distinct from private.content_ops_recovery_json_sha256(to_jsonb(q))
       or new.plan->>'content_item_id' is distinct from q.content_item_id::text
       or new.plan->>'source_item_id' is distinct from q.source_item_id::text
       or new.plan->>'generate_job_id' is distinct from q.generate_job_id::text
       or new.plan->>'banner_asset_id' is distinct from q.banner_asset_id::text
       or new.plan->>'banner_sha256' is distinct from q.banner_sha256
       or exists(select 1 from private.content_ops_button_reviews r
           where r.workspace_id=q.workspace_id and r.content_version_id=q.content_version_id)
       or exists(select 1 from private.content_ops_button_card_outbox_owners b where b.outbox_id=q.outbox_id)
    then raise exception 'content_ops_recovery_original_invalid' using errcode='23514'; end if;
    candidate := private.content_ops_review_candidate(q.workspace_id,q.content_item_id,q.content_version_id);
    if private.content_ops_review_matches(candidate,q) is not true
       or new.plan->>'telegram_copy_sha256' is distinct from
           encode(extensions.digest(convert_to(candidate->>'telegram_copy','UTF8'),'sha256'),'hex')
       or new.plan->>'x_copy_sha256' is distinct from
           encode(extensions.digest(convert_to(candidate->>'x_copy','UTF8'),'sha256'),'hex')
    then raise exception 'content_ops_recovery_candidate_invalid' using errcode='23514'; end if;
    return new;
end $$;
create trigger content_ops_review_recovery_grant_checked before insert
    on private.content_ops_review_recovery_grants for each row
    execute function private.guard_content_ops_recovery_grant();

-- A locked, current capability. No historical-source exception is possible:
-- the existing candidate enforces current version, source/feed/day and PNG.
create function private.content_ops_recovery_candidate(target_recovery_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path = '' as $$
declare
    g private.content_ops_review_recovery_grants;
    q private.content_ops_review_outbox;
    candidate jsonb;
begin
    select * into g from private.content_ops_review_recovery_grants
        where recovery_id = target_recovery_id for share;
    if not found or clock_timestamp() < g.not_before or clock_timestamp() >= g.deadline
       or g.plan_sha256 is distinct from private.content_ops_recovery_json_sha256(g.plan)
       or g.plan->>'release_sha' is distinct from g.release_sha then return null; end if;
    select * into q from private.content_ops_review_outbox
        where outbox_id = g.outbox_id for share;
    if not found or q.workspace_id is distinct from g.workspace_id
       or q.content_version_id is distinct from g.content_version_id
       or q.client_id is distinct from 'babylon'
       or q.status is distinct from 'claimed'
       or q.lease_expires_at is null or q.lease_expires_at >= clock_timestamp()
       or q.packet_sha256 is not null or q.send_started_at is not null
       or q.message_id is not null or q.finished_at is not null
       or private.content_ops_recovery_json_sha256(to_jsonb(q)) is distinct from g.original_outbox_sha256
    then return null; end if;
    candidate := private.content_ops_review_candidate(q.workspace_id,q.content_item_id,q.content_version_id);
    if private.content_ops_review_matches(candidate,q) is not true
       or g.plan->>'telegram_copy_sha256' is distinct from
           encode(extensions.digest(convert_to(candidate->>'telegram_copy','UTF8'),'sha256'),'hex')
       or g.plan->>'x_copy_sha256' is distinct from
           encode(extensions.digest(convert_to(candidate->>'x_copy','UTF8'),'sha256'),'hex')
    then return null; end if;
    return candidate;
end $$;

-- Preserve the ordinary sending/lease branch exactly. Recovery may own ONLY
-- an append-only bound review whose independent claim, begin and frozen old
-- row all still match. The four-part reserve/confirm functions remain intact.
create or replace function private.content_ops_button_card_outbox_owned(target_review_id uuid)
returns boolean language plpgsql volatile security invoker set search_path = '' as $$
declare
    b private.content_ops_button_card_outbox_owners;
    q private.content_ops_review_outbox;
    r private.content_ops_button_reviews;
    recovery_id uuid;
begin
    select * into b from private.content_ops_button_card_outbox_owners
        where review_id = target_review_id;
    if not found then return false; end if;
    select * into q from private.content_ops_review_outbox
        where outbox_id = b.outbox_id for update;
    select * into r from private.content_ops_button_reviews
        where id = target_review_id;
    if q.status = 'sending' and q.claim_token = b.claim_token
        and q.packet_sha256 = b.packet_sha256
        and q.lease_expires_at > clock_timestamp()
        and q.finished_at is null and q.message_id is null
        and q.workspace_id = r.workspace_id and q.client_id = r.client_id
        and q.content_item_id = r.content_item_id
        and q.content_version_id = r.content_version_id then return true; end if;
    select p.recovery_id into recovery_id
        from private.content_ops_review_recovery_prepared p
        join private.content_ops_review_recovery_claims c using(recovery_id)
        join private.content_ops_review_recovery_begins started using(recovery_id)
        join private.content_ops_review_recovery_grants g using(recovery_id)
        where p.review_id = target_review_id and c.claim_token = b.claim_token
          and started.packet_sha256 = b.packet_sha256
          and g.outbox_id = b.outbox_id and g.workspace_id = q.workspace_id
          and g.content_version_id = q.content_version_id;
    return recovery_id is not null
        and q.workspace_id = r.workspace_id and q.client_id = r.client_id
        and q.content_item_id = r.content_item_id and q.content_version_id = r.content_version_id
        and private.content_ops_recovery_candidate(recovery_id) is not null;
end $$;

create function private.content_ops_recovery_image_locator(target_recovery_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path = '' as $$
declare
    g private.content_ops_review_recovery_grants;
    banner public.assets;
begin
    if private.content_ops_recovery_candidate(target_recovery_id) is null then return null; end if;
    select * into g from private.content_ops_review_recovery_grants where recovery_id=target_recovery_id;
    select a.* into banner from public.assets a join storage.objects stored
        on stored.bucket_id = a.storage_bucket and stored.name = a.storage_path
        where a.id = (g.plan->>'banner_asset_id')::uuid
          and a.workspace_id = g.workspace_id
          and a.content_item_id = (g.plan->>'content_item_id')::uuid
          and a.content_version_id = g.content_version_id
          and a.asset_kind = 'png' and a.mime_type = 'image/png'
          and a.storage_bucket = 'content-studio'
          and a.metadata->>'filename' = 'news-card.png'
          and a.storage_path = g.workspace_id::text || '/babylon/' || a.id::text || '/news-card.png'
          and a.sha256 = g.plan->>'banner_sha256'
          and a.byte_size between 9 and 10000000
          and a.width > 0 and a.height > 0 for share of a, stored;
    if not found then return null; end if;
    return jsonb_build_object('status','ready','outbox_id',g.outbox_id,
        'client_id','babylon','content_item_id',g.plan->>'content_item_id',
        'content_version_id',g.content_version_id,'asset_id',banner.id,
        'bucket',banner.storage_bucket,'path',banner.storage_path,
        'sha256',banner.sha256,'byte_size',banner.byte_size,'execution_authorized',false);
end $$;

-- Read-only terminal never checks today's source or the now-expired grant
-- window. A successful terminal is backed by the committed card and all four
-- confirmed parts, not merely a caller-provided card identifier.
create function private.content_ops_recovery_terminal(target_recovery_id uuid,
    target_review_id uuid, target_card_id uuid, target_outbox_id uuid)
returns jsonb language plpgsql stable security invoker set search_path = '' as $$
declare
    g private.content_ops_review_recovery_grants;
    t private.content_ops_review_recovery_terminals;
    q private.content_ops_review_outbox;
    controls private.content_ops_button_card_send_attempts;
    card private.content_ops_button_cards;
begin
    select * into g from private.content_ops_review_recovery_grants where recovery_id=target_recovery_id;
    if not found or target_outbox_id is distinct from g.outbox_id then
        raise exception 'content_ops_recovery_terminal_scope' using errcode='23514'; end if;
    select * into q from private.content_ops_review_outbox where outbox_id=g.outbox_id;
    if not found or private.content_ops_recovery_json_sha256(to_jsonb(q)) is distinct from g.original_outbox_sha256
       or q.status is distinct from 'claimed' then
        raise exception 'content_ops_recovery_original_changed' using errcode='23514'; end if;
    select * into t from private.content_ops_review_recovery_terminals
        where recovery_id=target_recovery_id and review_id=target_review_id and card_id=target_card_id;
    if found then
        select * into card from private.content_ops_button_cards where id=t.card_id and review_id=t.review_id;
        select * into controls from private.content_ops_button_card_send_attempts
            where review_id=t.review_id and card_id=t.card_id and part_index=3 and state='confirmed';
        if card.id=t.card_id and card.bindings->>'message'=controls.message_binding
           and (select count(*) from private.content_ops_button_card_send_attempts
               where review_id=t.review_id and card_id=t.card_id and state='confirmed')=4 then
            return jsonb_build_object('status','recovery_sent','recovery_id',target_recovery_id,
                'card_id',t.card_id,'outbox_id',g.outbox_id,
                'original_outbox_status','claimed','execution_authorized',false);
        end if;
    end if;
    return jsonb_build_object('status','not_confirmed','recovery_id',target_recovery_id,
        'card_id',null,'outbox_id',null,'original_outbox_status','claimed',
        'execution_authorized',false);
end $$;

create or replace function public.content_ops_recovery_step(
    target_workspace_id uuid, target_content_version_id uuid,
    target_recovery_id uuid, target_plan_sha256 text, target_release_sha text,
    target_action text, target_args jsonb
) returns jsonb language plpgsql volatile security definer set search_path = '' as $$
declare
    g private.content_ops_review_recovery_grants;
    q private.content_ops_review_outbox;
    r private.content_ops_button_reviews;
    b private.content_ops_review_recovery_begins;
    claim private.content_ops_review_recovery_claims;
    prepared private.content_ops_review_recovery_prepared;
    candidate jsonb;
    fingerprint text;
    result jsonb;
    selected_card_id uuid;
    chosen_review_id uuid;
    chosen_claim_token uuid;
    allowed text[];
    n integer;
    a private.content_ops_button_card_send_attempts;
    controls_message_id bigint;
begin
    if (select auth.role()) is distinct from 'service_role' then
        raise exception 'content_ops_recovery_service_role_required' using errcode='42501'; end if;
    if target_workspace_id is null or target_content_version_id is null
       or target_recovery_id is null or target_plan_sha256 !~ '^[a-f0-9]{64}$'
       or target_release_sha !~ '^[a-f0-9]{40}$'
       or target_action is null or jsonb_typeof(target_args) is distinct from 'object' then
        raise exception 'content_ops_recovery_arguments_invalid' using errcode='22023'; end if;
    allowed := case target_action
        when 'status' then array[]::text[]
        when 'reconcile' then array[]::text[]
        when 'claim' then array['claim_token']
        when 'image' then array['outbox_id','claim_token']
        when 'begin' then array['outbox_id','claim_token','packet_sha256']
        when 'prepare' then array['outbox_id','claim_token','review_id']
        when 'bind' then array['review_id','outbox_id','claim_token','packet_sha256']
        when 'reserve' then array['review_id','card_id','part_index','payload_sha256']
        when 'confirm' then array['review_id','card_id','part_index','payload_sha256',
            'message_id','message_binding','response_sha256','observed_at']
        when 'register' then array['review_id','card_id','expected_fingerprint','epoch',
            'bindings','parts','controls_payload_sha256','response_sha256s','delivered','expires']
        when 'terminal' then array['review_id','card_id','outbox_id']
        else null end;
    if allowed is null or not target_args ?& allowed or target_args - allowed <> '{}'::jsonb then
        raise exception 'content_ops_recovery_action_denied' using errcode='22023'; end if;
    -- Terminal is historical receipt inspection and cannot authorize another
    -- send, even after the grant expires. It intentionally skips candidate.
    select * into g from private.content_ops_review_recovery_grants
        where recovery_id=target_recovery_id and workspace_id=target_workspace_id
          and content_version_id=target_content_version_id
          and plan_sha256=target_plan_sha256 and release_sha=target_release_sha;
    if not found then return null; end if;
    if target_action='terminal' then
        return private.content_ops_recovery_terminal(target_recovery_id,
            (target_args->>'review_id')::uuid,(target_args->>'card_id')::uuid,
            (target_args->>'outbox_id')::uuid);
    end if;
    -- Acquire the existing owner row before candidate item/feed locks. Two
    -- claims must not both take SHARE first and then deadlock on an upgrade.
    if target_action not in ('status','reconcile') then
        select * into q from private.content_ops_review_outbox where outbox_id=g.outbox_id for update;
        if not found or private.content_ops_recovery_json_sha256(to_jsonb(q)) is distinct from g.original_outbox_sha256 then
            raise exception 'content_ops_recovery_original_changed' using errcode='23514'; end if;
    end if;
    candidate := private.content_ops_recovery_candidate(target_recovery_id);
    if target_action='status' then
        if candidate is null then return null; end if;
        return jsonb_build_object('active',true,'observed_at',clock_timestamp(),
            'plan',g.plan,'plan_sha256',g.plan_sha256);
    end if;
    if target_action='reconcile' then
        if candidate is null or exists(select 1 from private.content_ops_review_recovery_claims
            where recovery_id=target_recovery_id) then return to_jsonb(0); end if;
        return to_jsonb(1);
    end if;
    if candidate is null then raise exception 'content_ops_recovery_inactive' using errcode='23514'; end if;
    if target_action='claim' then
        chosen_claim_token := (target_args->>'claim_token')::uuid;
        if chosen_claim_token is null or chosen_claim_token='00000000-0000-0000-0000-000000000000'::uuid
           or exists(select 1 from private.content_ops_review_outbox old_claim
               where old_claim.claim_token=chosen_claim_token)
           or exists(select 1 from private.content_ops_review_recovery_claims
               where recovery_id=target_recovery_id)
           or exists(select 1 from private.content_ops_button_reviews
               where workspace_id=g.workspace_id and content_version_id=g.content_version_id)
           or exists(select 1 from private.content_ops_button_card_outbox_owners
               where outbox_id=g.outbox_id) then return null; end if;
        insert into private.content_ops_review_recovery_claims(recovery_id,claim_token)
            values(target_recovery_id,chosen_claim_token);
        return (candidate - 'banner_asset_id') || jsonb_build_object(
            'outbox_id',g.outbox_id,'claim_token',chosen_claim_token);
    end if;
    select * into claim from private.content_ops_review_recovery_claims
        where recovery_id=target_recovery_id;
    if not found then raise exception 'content_ops_recovery_not_claimed' using errcode='23514'; end if;
    if target_action in ('image','begin','prepare') then
        if (target_args->>'outbox_id')::uuid is distinct from g.outbox_id
           or (target_args->>'claim_token')::uuid is distinct from claim.claim_token then
            raise exception 'content_ops_recovery_claim_scope' using errcode='23514'; end if;
    end if;
    if target_action='image' then return private.content_ops_recovery_image_locator(target_recovery_id); end if;
    if target_action='prepare' then
        chosen_review_id := (target_args->>'review_id')::uuid;
        if chosen_review_id is null or chosen_review_id='00000000-0000-0000-0000-000000000000'::uuid
           or exists(select 1 from private.content_ops_review_recovery_prepared
               where recovery_id=target_recovery_id)
           or exists(select 1 from private.content_ops_button_reviews
               where workspace_id=g.workspace_id and content_version_id=g.content_version_id) then
            raise exception 'content_ops_recovery_prepare_consumed' using errcode='23514'; end if;
        fingerprint := private.content_ops_button_version_fingerprint(
            q.workspace_id,q.content_item_id,q.content_version_id);
        if fingerprint is null or fingerprint !~ '^[a-f0-9]{64}$' then
            raise exception 'content_ops_recovery_fingerprint_invalid' using errcode='23514'; end if;
        insert into private.content_ops_button_reviews(id,workspace_id,client_id,
            content_item_id,content_version_id,version_fingerprint)
            values(chosen_review_id,q.workspace_id,q.client_id,q.content_item_id,q.content_version_id,fingerprint)
            returning * into r;
        insert into private.content_ops_review_recovery_prepared(recovery_id,review_id)
            values(target_recovery_id,chosen_review_id);
        return jsonb_build_object('status','review_prepared','review_id',r.id,
            'version_fingerprint',r.version_fingerprint,'epoch',r.epoch,
            'state',r.state,'expires_at',r.expires_at,'execution_authorized',false);
    end if;
    select * into prepared from private.content_ops_review_recovery_prepared
        where recovery_id=target_recovery_id;
    if not found then raise exception 'content_ops_recovery_not_prepared' using errcode='23514'; end if;
    if target_action='begin' then
        if exists(select 1 from private.content_ops_review_recovery_begins
            where recovery_id=target_recovery_id)
           or target_args->>'packet_sha256' !~ '^[a-f0-9]{64}$' then
            return jsonb_build_object('accepted',false,'status','not_owned'); end if;
        insert into private.content_ops_review_recovery_begins(recovery_id,packet_sha256)
            values(target_recovery_id,target_args->>'packet_sha256');
        return jsonb_build_object('accepted',true,'status','sending','outbox_id',g.outbox_id);
    end if;
    select * into b from private.content_ops_review_recovery_begins
        where recovery_id=target_recovery_id;
    if not found then raise exception 'content_ops_recovery_not_begun' using errcode='23514'; end if;
    chosen_review_id := (target_args->>'review_id')::uuid;
    if chosen_review_id is distinct from prepared.review_id then
        raise exception 'content_ops_recovery_review_scope' using errcode='23514'; end if;
    if target_action='bind' then
        if (target_args->>'outbox_id')::uuid is distinct from g.outbox_id
           or (target_args->>'claim_token')::uuid is distinct from claim.claim_token
           or target_args->>'packet_sha256' is distinct from b.packet_sha256 then
            raise exception 'content_ops_recovery_bind_scope' using errcode='23514'; end if;
        insert into private.content_ops_button_card_outbox_owners
            (review_id,outbox_id,claim_token,packet_sha256)
            values(chosen_review_id,g.outbox_id,claim.claim_token,b.packet_sha256);
        return jsonb_build_object('status','bound','execution_authorized',false);
    end if;
    if not exists(select 1 from private.content_ops_button_card_outbox_owners owner
        where owner.review_id=chosen_review_id and owner.outbox_id=g.outbox_id
          and owner.claim_token=claim.claim_token and owner.packet_sha256=b.packet_sha256) then
        raise exception 'content_ops_recovery_not_bound' using errcode='23514'; end if;
    if target_action='reserve' then
        return private.reserve_content_ops_button_card_send(chosen_review_id,
            (target_args->>'card_id')::uuid,(target_args->>'part_index')::smallint,
            target_args->>'payload_sha256');
    end if;
    if target_action='confirm' then
        return private.confirm_content_ops_button_card_send(chosen_review_id,
            (target_args->>'card_id')::uuid,(target_args->>'part_index')::smallint,
            target_args->>'payload_sha256',(target_args->>'message_id')::bigint,
            target_args->>'message_binding',target_args->>'response_sha256',
            (target_args->>'observed_at')::timestamptz);
    end if;
    if target_action='register' then
        selected_card_id := (target_args->>'card_id')::uuid;
        if selected_card_id is null or target_args->>'controls_payload_sha256' !~ '^[a-f0-9]{64}$'
           or jsonb_typeof(target_args->'parts') is distinct from 'array'
           or jsonb_array_length(target_args->'parts') <> 3
           or jsonb_typeof(target_args->'response_sha256s') is distinct from 'array'
           or jsonb_array_length(target_args->'response_sha256s') <> 4
           or jsonb_typeof(target_args->'bindings') is distinct from 'object'
           or exists(select 1 from private.content_ops_review_recovery_terminals
               where recovery_id=target_recovery_id) then
            raise exception 'content_ops_recovery_register_invalid' using errcode='23514'; end if;
        select * into r from private.content_ops_button_reviews where id=chosen_review_id for update;
        if r.state is distinct from 'active' or r.epoch is distinct from (target_args->>'epoch')::bigint
           or r.expires_at <= clock_timestamp()
           or r.version_fingerprint is distinct from target_args->>'expected_fingerprint'
           or r.version_fingerprint is distinct from private.content_ops_button_version_fingerprint(
               r.workspace_id,r.content_item_id,r.content_version_id)
           or (select count(*) from private.content_ops_button_card_send_attempts
            where review_id=r.id and card_id=selected_card_id) <> 4 then
            raise exception 'content_ops_recovery_register_ineligible' using errcode='23514'; end if;
        for n in 0..3 loop
            select * into a from private.content_ops_button_card_send_attempts
                where review_id=r.id and card_id=selected_card_id
                  and part_index=n for share;
            if not found or a.state is distinct from 'confirmed'
               or a.response_sha256 is distinct from target_args->'response_sha256s'->>n
               or (n<3 and (a.payload_sha256 is distinct from target_args->'parts'->n->>'payload_sha256'
                   or a.message_binding is distinct from target_args->'parts'->n->>'message_binding'))
               or (n=3 and (a.payload_sha256 is distinct from target_args->>'controls_payload_sha256'
                   or a.message_binding is distinct from target_args->'bindings'->>'message')) then
                raise exception 'content_ops_recovery_send_incomplete' using errcode='23514'; end if;
            if n=3 then controls_message_id := a.message_id; end if;
        end loop;
        if controls_message_id is null then
            raise exception 'content_ops_recovery_send_incomplete' using errcode='23514'; end if;
        result := private.record_content_ops_button_card(chosen_review_id,selected_card_id,
            target_args->>'expected_fingerprint',(target_args->>'epoch')::bigint,
            target_args->'bindings',target_args->'parts',
            (target_args->>'delivered')::timestamptz,(target_args->>'expires')::timestamptz);
        if result->>'status' is distinct from 'card_recorded'
           or result->'reused' is distinct from 'false'::jsonb then
            raise exception 'content_ops_recovery_card_not_new' using errcode='23514'; end if;
        insert into private.content_ops_review_recovery_terminals(recovery_id,review_id,card_id)
            values(target_recovery_id,chosen_review_id,selected_card_id);
        return result;
    end if;
    raise exception 'content_ops_recovery_action_denied' using errcode='22023';
end $$;

revoke all on function private.content_ops_recovery_json_canonical(jsonb),
    private.content_ops_recovery_json_sha256(jsonb),
    private.guard_content_ops_recovery_immutable(),
    private.guard_content_ops_recovery_grant(),
    private.content_ops_recovery_candidate(uuid),
    private.content_ops_recovery_image_locator(uuid),
    private.content_ops_recovery_terminal(uuid,uuid,uuid,uuid),
    private.content_ops_button_card_outbox_owned(uuid)
    from public,anon,authenticated,service_role;
revoke all on function public.content_ops_recovery_step(uuid,uuid,uuid,text,text,text,jsonb)
    from public,anon,authenticated,service_role;
grant execute on function public.content_ops_recovery_step(uuid,uuid,uuid,text,text,text,jsonb)
    to service_role;
do $$
declare name text;
begin
    if (select owner_catalog from pg_temp.content_ops_recovery_before) is distinct from
        (select to_jsonb(p)-'prosrc' from pg_proc p where p.oid=
          'private.content_ops_button_card_outbox_owned(uuid)'::regprocedure)
      or (select original_outbox from pg_temp.content_ops_recovery_before) is distinct from
        (select to_jsonb(q) from private.content_ops_review_outbox q where q.outbox_id=
          '94f31aa1-34dd-4220-9c88-34716e760c47'::uuid) then
        raise exception 'content_ops_recovery_preservation_failed';
    end if;
    foreach name in array array['grants','claims','prepared','begins','terminals'] loop
        if has_table_privilege('service_role','private.content_ops_review_recovery_'||name,
            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
          or has_table_privilege('anon','private.content_ops_review_recovery_'||name,
            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
          or has_table_privilege('authenticated','private.content_ops_review_recovery_'||name,
            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE') then
            raise exception 'content_ops_recovery_acl_failed';
        end if;
    end loop;
    if exists(select 1 from private.content_ops_review_recovery_grants)
      or exists(select 1 from private.content_ops_review_recovery_claims)
      or exists(select 1 from private.content_ops_review_recovery_prepared)
      or exists(select 1 from private.content_ops_review_recovery_begins)
      or exists(select 1 from private.content_ops_review_recovery_terminals)
      or has_function_privilege('anon','public.content_ops_recovery_step(uuid,uuid,uuid,text,text,text,jsonb)','EXECUTE')
      or has_function_privilege('authenticated','public.content_ops_recovery_step(uuid,uuid,uuid,text,text,text,jsonb)','EXECUTE')
      or not has_function_privilege('service_role','public.content_ops_recovery_step(uuid,uuid,uuid,text,text,text,jsonb)','EXECUTE') then
        raise exception 'content_ops_recovery_off_postcondition_failed';
    end if;
end $$;
commit;
