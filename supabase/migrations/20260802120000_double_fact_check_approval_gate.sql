-- Durable human attestations for the double fact-check publication gate.
--
-- Historical and v1 Studio approvals remain readable, but are deliberately
-- un-attested and therefore cannot authorize a new publication.  The v2 RPC
-- binds both human attestations and the policy version to its idempotency key.

begin;

-- Do not change the final-attempt function beneath a worker that may already
-- have crossed the irreversible Telegram provider boundary. Operators must
-- pause both worker execution planes and reconcile these rows first.
do $$
begin
    if exists (
        select 1
        from public.publications as publication
        where publication.channel = 'telegram'
          and publication.request_payload ->> 'workflow'
                = 'exact_telegram_publication_v1'
          and publication.status = 'publishing'
          and publication.delivery_started_at is not null
    ) or exists (
        select 1
        from public.jobs as job
        where job.job_kind = 'publish'
          and job.input ->> 'workflow' = 'exact_telegram_publication_v1'
          and job.status = 'running'
    ) then
        raise exception
            'pause and reconcile active exact Telegram attempts before double-fact-check migration';
    end if;
end
$$;

alter table public.approvals
    add column fact_check_policy_version text,
    add column source_facts_verified boolean not null default false,
    add column output_claims_verified boolean not null default false,
    add column review_sequence bigint,
    add constraint approvals_fact_check_policy_version_check check (
        fact_check_policy_version is null
        or fact_check_policy_version = 'double-fact-check@1'
    );

create sequence public.approvals_review_sequence_seq;

with ordered_reviews as (
    select approval.id,
           row_number() over (
               order by approval.created_at, approval.id
           )::bigint as review_sequence
    from public.approvals as approval
)
update public.approvals as approval
set review_sequence = ordered.review_sequence
from ordered_reviews as ordered
where ordered.id = approval.id;

select setval(
    'public.approvals_review_sequence_seq'::regclass,
    coalesce((select max(review_sequence) from public.approvals), 1),
    exists (select 1 from public.approvals)
);

alter sequence public.approvals_review_sequence_seq
    owned by public.approvals.review_sequence;
alter table public.approvals
    alter column review_sequence set default nextval(
        'public.approvals_review_sequence_seq'::regclass
    ),
    alter column review_sequence set not null,
    add constraint approvals_review_sequence_key unique (review_sequence);

revoke all on sequence public.approvals_review_sequence_seq
    from public, anon, authenticated, service_role;
grant usage, select on sequence public.approvals_review_sequence_seq
    to service_role;

create or replace function private.has_valid_double_fact_check_report(
    target_generation_meta jsonb
)
returns boolean
language sql
immutable
set search_path = ''
as $$
    select coalesce(
        jsonb_typeof(target_generation_meta) = 'object'
        and jsonb_typeof(target_generation_meta -> 'fact_check') = 'object'
        and target_generation_meta -> 'fact_check' ->> 'schema_version' = '1.0'
        and target_generation_meta -> 'fact_check' ->> 'policy_version'
                = 'double-fact-check@1'
        and target_generation_meta -> 'fact_check' ->> 'content_kind'
                in ('daily_news', 'article', 'tutorial')
        and target_generation_meta -> 'fact_check' -> 'human_review_required'
                = 'true'::jsonb
        and target_generation_meta -> 'fact_check' ->> 'status'
                in ('pass', 'review')
        and target_generation_meta -> 'fact_check' ->> 'input_sha256'
                ~ '^[a-f0-9]{64}$'
        and target_generation_meta -> 'fact_check' ->> 'output_sha256'
                ~ '^[a-f0-9]{64}$'
        and jsonb_typeof(
                target_generation_meta -> 'fact_check' -> 'checks'
            ) = 'array'
        and case
                when jsonb_typeof(
                    target_generation_meta -> 'fact_check' -> 'checks'
                ) = 'array' then jsonb_array_length(
                    target_generation_meta -> 'fact_check' -> 'checks'
                ) = 2
                else false
            end
        and jsonb_typeof(
                target_generation_meta -> 'fact_check' -> 'checks' -> 0
            ) = 'object'
        and jsonb_typeof(
                target_generation_meta -> 'fact_check' -> 'checks' -> 1
            ) = 'object'
        and target_generation_meta -> 'fact_check' -> 'checks' -> 0
                ->> 'id' = 'source_evidence'
        and target_generation_meta -> 'fact_check' -> 'checks' -> 1
                ->> 'id' = 'output_claims'
        and target_generation_meta -> 'fact_check' -> 'checks' -> 0
                ->> 'status' in ('pass', 'review')
        and target_generation_meta -> 'fact_check' -> 'checks' -> 1
                ->> 'status' in ('pass', 'review')
        and jsonb_typeof(
                target_generation_meta -> 'fact_check' -> 'checks' -> 0
                    -> 'label'
            ) = 'string'
        and char_length(btrim(
                target_generation_meta -> 'fact_check' -> 'checks' -> 0
                    ->> 'label'
            )) > 0
        and jsonb_typeof(
                target_generation_meta -> 'fact_check' -> 'checks' -> 1
                    -> 'label'
            ) = 'string'
        and char_length(btrim(
                target_generation_meta -> 'fact_check' -> 'checks' -> 1
                    ->> 'label'
            )) > 0
        and jsonb_typeof(
                target_generation_meta -> 'fact_check' -> 'checks' -> 0
                    -> 'detail'
            ) = 'string'
        and char_length(btrim(
                target_generation_meta -> 'fact_check' -> 'checks' -> 0
                    ->> 'detail'
            )) > 0
        and jsonb_typeof(
                target_generation_meta -> 'fact_check' -> 'checks' -> 1
                    -> 'detail'
            ) = 'string'
        and char_length(btrim(
                target_generation_meta -> 'fact_check' -> 'checks' -> 1
                    ->> 'detail'
            )) > 0
        and jsonb_typeof(
                target_generation_meta -> 'fact_check' -> 'checks' -> 0
                    -> 'metrics'
            ) = 'object'
        and jsonb_typeof(
                target_generation_meta -> 'fact_check' -> 'checks' -> 1
                    -> 'metrics'
            ) = 'object'
        and target_generation_meta -> 'fact_check' ->> 'status'
                = case
                    when target_generation_meta -> 'fact_check' -> 'checks' -> 0
                            ->> 'status' = 'review'
                      or target_generation_meta -> 'fact_check' -> 'checks' -> 1
                            ->> 'status' = 'review'
                        then 'review'
                    else 'pass'
                  end,
        false
    )
$$;

-- Return the latest approval for the exact immutable version, or fail closed.
-- Callers that pin an approval pass its id so a later review cannot be ignored.
create or replace function private.require_double_fact_check_approval(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    expected_approval_id uuid default null
)
returns uuid
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    latest public.approvals%rowtype;
    target_generation_meta jsonb;
    target_content_kind text;
begin
    select review.* into latest
    from public.approvals as review
    where review.workspace_id = target_workspace_id
      and review.content_item_id = target_content_item_id
      and review.content_version_id = target_content_version_id
    order by review.review_sequence desc
    limit 1;

    if not found
       or latest.decision <> 'approved'
       or latest.fact_check_policy_version
            is distinct from 'double-fact-check@1'
       or latest.source_facts_verified is not true
       or latest.output_claims_verified is not true
       or (expected_approval_id is not null
           and latest.id is distinct from expected_approval_id) then
        raise exception
            'publication requires the latest double-fact-check approval'
            using errcode = '23514';
    end if;

    select version.generation_meta, item.content_kind
    into target_generation_meta, target_content_kind
    from public.content_versions as version
    join public.content_items as item
      on item.workspace_id = version.workspace_id
     and item.id = version.content_item_id
    where version.workspace_id = target_workspace_id
      and version.content_item_id = target_content_item_id
      and version.id = target_content_version_id;
    if not found
       or not private.has_valid_double_fact_check_report(
            target_generation_meta
       )
       or target_generation_meta -> 'fact_check' ->> 'content_kind'
            is distinct from target_content_kind then
        raise exception
            'publication requires a valid double-fact-check report'
            using errcode = '23514';
    end if;

    return latest.id;
end;
$$;

create or replace function public.record_studio_content_review_v2(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    review_decision text,
    review_fact_check_policy_version text,
    review_source_facts_verified boolean,
    review_output_claims_verified boolean,
    review_reason_codes text[] default '{}'::text[],
    review_comment text default null,
    review_idempotency_key text default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    item public.content_items%rowtype;
    target_version public.content_versions%rowtype;
    existing public.approvals%rowtype;
    approval_id uuid;
    normalized_codes text[];
    normalized_comment text;
begin
    if target_workspace_id is null
       or target_content_item_id is null
       or target_content_version_id is null then
        raise exception 'studio review identifiers are required'
            using errcode = '22023';
    end if;
    if review_decision not in ('approved', 'rejected') then
        raise exception 'studio review decision is invalid'
            using errcode = '22023';
    end if;
    if review_fact_check_policy_version
            is distinct from 'double-fact-check@1'
       or review_source_facts_verified is null
       or review_output_claims_verified is null then
        raise exception 'studio review fact-check attestation is invalid'
            using errcode = '22023';
    end if;
    if review_idempotency_key is null
       or char_length(review_idempotency_key) not between 8 and 200 then
        raise exception 'studio review idempotency key is invalid'
            using errcode = '22023';
    end if;

    normalized_comment := nullif(btrim(coalesce(review_comment, '')), '');
    if normalized_comment is not null
       and char_length(normalized_comment) > 1000 then
        raise exception 'studio review comment is too long'
            using errcode = '22023';
    end if;

    select coalesce(array_agg(distinct code order by code), '{}'::text[])
    into normalized_codes
    from unnest(coalesce(review_reason_codes, '{}'::text[])) as code;
    if cardinality(normalized_codes) > 5
       or array_position(normalized_codes, null) is not null
       or not (
           normalized_codes <@ array[
               'off_brand_tone',
               'unsupported_claim',
               'awkward_korean',
               'visual_brand_mismatch',
               'duplicate_logo',
               'source_fidelity',
               'channel_fit',
               'other'
           ]::text[]
       ) then
        raise exception 'studio review reason codes are invalid'
            using errcode = '22023';
    end if;
    if review_decision = 'approved'
       and cardinality(normalized_codes) > 0 then
        raise exception 'approved studio review cannot include rejection reasons'
            using errcode = '22023';
    end if;
    if review_decision = 'rejected'
       and cardinality(normalized_codes) = 0
       and normalized_comment is null then
        raise exception 'rejected studio review requires a reason'
            using errcode = '22023';
    end if;

    select * into item
    from public.content_items
    where id = target_content_item_id
      and workspace_id = target_workspace_id
    for update;
    if not found then
        raise exception 'studio review content item not found'
            using errcode = 'P0002';
    end if;

    select * into target_version
    from public.content_versions
    where workspace_id = target_workspace_id
      and content_item_id = target_content_item_id
      and id = target_content_version_id;
    if not found
       or item.current_version_id is distinct from target_content_version_id then
        raise exception 'studio review version is not current'
            using errcode = '23514';
    end if;

    select * into existing
    from public.approvals
    where workspace_id = target_workspace_id
      and content_item_id = target_content_item_id
      and content_version_id = target_content_version_id
      and idempotency_key = review_idempotency_key;
    if found then
        if existing.decision is distinct from review_decision
           or existing.reason_codes is distinct from normalized_codes
           or existing.comment is distinct from normalized_comment
           or existing.fact_check_policy_version
                is distinct from review_fact_check_policy_version
           or existing.source_facts_verified
                is distinct from review_source_facts_verified
           or existing.output_claims_verified
                is distinct from review_output_claims_verified then
            raise exception 'studio review idempotency conflict'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'approval_id', existing.id,
            'content_item_id', existing.content_item_id,
            'content_version_id', existing.content_version_id,
            'decision', existing.decision,
            'status', existing.decision,
            'reason_codes', existing.reason_codes,
            'fact_check_policy_version',
                existing.fact_check_policy_version,
            'source_facts_verified', existing.source_facts_verified,
            'output_claims_verified', existing.output_claims_verified,
            'created_at', existing.created_at,
            'reused', true
        );
    end if;

    if review_decision = 'approved'
       and (review_source_facts_verified is not true
            or review_output_claims_verified is not true) then
        raise exception 'approved studio review requires both fact checks'
            using errcode = '23514';
    end if;

    if item.status <> 'needs_review' then
        raise exception 'content item is not awaiting studio review'
            using errcode = '23514';
    end if;
    if review_decision = 'approved'
       and target_version.generation_meta @> '{"mock_mode":true}'::jsonb then
        raise exception 'mock/test content cannot be approved'
            using errcode = '23514';
    end if;
    if review_decision = 'approved'
       and not private.has_valid_double_fact_check_report(
            target_version.generation_meta
       ) then
        raise exception
            'approved studio review requires a valid double-fact-check report'
            using errcode = '23514';
    end if;
    if review_decision = 'approved'
       and target_version.generation_meta -> 'fact_check' ->> 'content_kind'
            is distinct from item.content_kind then
        raise exception
            'approved studio review fact-check content kind is invalid'
            using errcode = '23514';
    end if;

    insert into public.approvals (
        workspace_id,
        client_id,
        content_item_id,
        content_version_id,
        reviewer_id,
        reviewer_source,
        decision,
        reason_codes,
        comment,
        idempotency_key,
        fact_check_policy_version,
        source_facts_verified,
        output_claims_verified
    ) values (
        item.workspace_id,
        item.client_id,
        item.id,
        target_version.id,
        null,
        'studio_session',
        review_decision,
        normalized_codes,
        normalized_comment,
        review_idempotency_key,
        review_fact_check_policy_version,
        review_source_facts_verified,
        review_output_claims_verified
    ) returning id into approval_id;

    update public.content_items
    set status = review_decision,
        scheduled_for = null
    where id = item.id;

    insert into public.event_log (
        workspace_id,
        actor_id,
        entity_type,
        entity_id,
        event_type,
        data
    ) values (
        item.workspace_id,
        null,
        'content_item',
        item.id,
        'content_' || review_decision,
        jsonb_build_object(
            'approval_id', approval_id,
            'content_version_id', target_version.id,
            'reviewer_source', 'studio_session',
            'reason_codes', to_jsonb(normalized_codes),
            'fact_check_policy_version',
                review_fact_check_policy_version,
            'source_facts_verified', review_source_facts_verified,
            'output_claims_verified', review_output_claims_verified
        )
    );

    return jsonb_build_object(
        'approval_id', approval_id,
        'content_item_id', item.id,
        'content_version_id', target_version.id,
        'decision', review_decision,
        'status', review_decision,
        'reason_codes', normalized_codes,
        'fact_check_policy_version', review_fact_check_policy_version,
        'source_facts_verified', review_source_facts_verified,
        'output_claims_verified', review_output_claims_verified,
        'created_at', statement_timestamp(),
        'reused', false
    );
end;
$$;

create or replace function public.get_content_review_summary(
    target_workspace_id uuid,
    target_content_item_id uuid
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select case
        when latest.id is null then null
        else jsonb_build_object(
            'approval_id', latest.id,
            'content_version_id', latest.content_version_id,
            'decision', latest.decision,
            'reason_codes', latest.reason_codes,
            'comment', latest.comment,
            'reviewer_source', latest.reviewer_source,
            'fact_check_policy_version', latest.fact_check_policy_version,
            'source_facts_verified', latest.source_facts_verified,
            'output_claims_verified', latest.output_claims_verified,
            'created_at', latest.created_at
        )
    end
    from (select 1) as seed
    left join lateral (
        select approval.*
        from public.approvals as approval
        where approval.workspace_id = target_workspace_id
          and approval.content_item_id = target_content_item_id
        order by approval.review_sequence desc
        limit 1
    ) as latest on true
    where exists (
        select 1
        from public.content_items as item
        where item.workspace_id = target_workspace_id
          and item.id = target_content_item_id
    );
$$;

-- Preserve the prior RPC implementations as inaccessible implementation
-- details, then place the fact-check fence around each public entry point.
alter function public.request_content_publication(
    uuid, uuid, text, timestamptz, text
) set schema private;
alter function private.request_content_publication(
    uuid, uuid, text, timestamptz, text
) rename to request_content_publication_before_double_fact_check;

alter function public.record_manual_publication_observation(
    uuid, uuid, uuid, text, text
) set schema private;
alter function private.record_manual_publication_observation(
    uuid, uuid, uuid, text, text
) rename to record_manual_observation_before_double_fact_check;

alter function public.request_studio_telegram_publication(
    uuid, uuid, uuid, text
) set schema private;
alter function private.request_studio_telegram_publication(
    uuid, uuid, uuid, text
) rename to request_studio_telegram_before_double_fact_check;

alter function public.claim_exact_telegram_publication_job(
    uuid, text, integer
) set schema private;
alter function private.claim_exact_telegram_publication_job(
    uuid, text, integer
) rename to claim_exact_telegram_job_before_double_fact_check;

alter function public.mark_exact_telegram_attempt_started(
    uuid, text, text
) set schema private;
alter function private.mark_exact_telegram_attempt_started(
    uuid, text, text
) rename to mark_exact_telegram_attempt_before_double_fact_check;

create or replace function public.request_content_publication(
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_channel text,
    target_scheduled_for timestamptz default null,
    request_idempotency_key text default null
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
    target_workspace_id uuid;
    result uuid;
begin
    result := private.request_content_publication_before_double_fact_check(
        target_content_item_id,
        target_content_version_id,
        target_channel,
        target_scheduled_for,
        request_idempotency_key
    );

    select item.workspace_id into target_workspace_id
    from public.content_items as item
    where item.id = target_content_item_id;
    if not found then
        raise exception 'content item not found' using errcode = 'P0002';
    end if;

    perform private.require_double_fact_check_approval(
        target_workspace_id,
        target_content_item_id,
        target_content_version_id
    );
    return result;
end;
$$;

create or replace function public.request_studio_telegram_publication(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    request_idempotency_key text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    result jsonb;
    pinned_approval_id uuid;
begin
    perform 1
    from public.content_items as item
    where item.workspace_id = target_workspace_id
      and item.id = target_content_item_id
    for update;
    if not found then
        raise exception 'exact Telegram publication content item not found'
            using errcode = 'P0002';
    end if;

    perform private.require_double_fact_check_approval(
        target_workspace_id,
        target_content_item_id,
        target_content_version_id
    );
    result := private.request_studio_telegram_before_double_fact_check(
        target_workspace_id,
        target_content_item_id,
        target_content_version_id,
        request_idempotency_key
    );
    select (publication.request_payload ->> 'approval_id')::uuid
    into pinned_approval_id
    from public.publications as publication
    where publication.id = (result ->> 'publication_id')::uuid
      and publication.workspace_id = target_workspace_id
      and publication.content_item_id = target_content_item_id
      and publication.content_version_id = target_content_version_id
      and publication.channel = 'telegram'
      and publication.request_payload ->> 'workflow'
            = 'exact_telegram_publication_v1';
    if not found or pinned_approval_id is null then
        raise exception 'exact Telegram publication approval pin is invalid'
            using errcode = '23514';
    end if;
    perform private.require_double_fact_check_approval(
        target_workspace_id,
        target_content_item_id,
        target_content_version_id,
        pinned_approval_id
    );
    return result;
end;
$$;

create or replace function public.record_manual_publication_observation(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_channel text,
    target_external_url text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    result jsonb;
begin
    -- The legacy implementation validates and locks the current item before it
    -- writes.  Gate afterward in the same transaction, while that item lock is
    -- still held; any missing attestation rolls the observation back atomically.
    result := private.record_manual_observation_before_double_fact_check(
        target_workspace_id,
        target_content_item_id,
        target_content_version_id,
        target_channel,
        target_external_url
    );
    perform private.require_double_fact_check_approval(
        target_workspace_id,
        target_content_item_id,
        target_content_version_id
    );
    return result;
end;
$$;

create or replace function public.claim_exact_telegram_publication_job(
    target_workspace_id uuid,
    target_worker_id text,
    target_lease_seconds integer default 300
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    result jsonb;
    validation_failed boolean;
    quarantined_count integer := 0;
    quarantine_limit constant integer := 25;
begin
    loop
        result := private.claim_exact_telegram_job_before_double_fact_check(
            target_workspace_id,
            target_worker_id,
            target_lease_seconds
        );
        if result is null then
            return null;
        end if;

        validation_failed := false;
        begin
            perform private.require_double_fact_check_approval(
                target_workspace_id,
                (result ->> 'content_item_id')::uuid,
                (result ->> 'content_version_id')::uuid,
                (result ->> 'approval_id')::uuid
            );
        exception when check_violation then
            validation_failed := true;
        end;
        if not validation_failed then
            return result;
        end if;

        -- The legacy claim acquired item -> job -> publication locks before
        -- returning.  Quarantine under those same locks so an invalid legacy
        -- queue head cannot roll back to queued and starve later valid work.
        update public.jobs
        set status = 'failed',
            locked_by = null,
            locked_at = null,
            lease_expires_at = null,
            last_error_code = 'double_fact_check_approval_invalid',
            last_error_message =
                'The exact Telegram job lacks a current fact-check attestation.',
            finished_at = statement_timestamp()
        where id = (result ->> 'job_id')::uuid
          and workspace_id = target_workspace_id
          and content_item_id = (result ->> 'content_item_id')::uuid
          and job_kind = 'publish'
          and input ->> 'workflow' = 'exact_telegram_publication_v1'
          and status = 'running'
          and locked_by = target_worker_id;
        if not found then
            raise exception 'exact Telegram quarantine job state changed'
                using errcode = '40001';
        end if;

        update public.publications
        set status = 'failed',
            last_error = 'double_fact_check_approval_invalid'
        where id = (result ->> 'publication_id')::uuid
          and workspace_id = target_workspace_id
          and content_item_id = (result ->> 'content_item_id')::uuid
          and content_version_id = (result ->> 'content_version_id')::uuid
          and channel = 'telegram'
          and request_payload ->> 'workflow'
                = 'exact_telegram_publication_v1'
          and status = 'queued'
          and delivery_started_at is null;
        if not found then
            raise exception 'exact Telegram quarantine publication state changed'
                using errcode = '40001';
        end if;

        insert into public.event_log (
            workspace_id, entity_type, entity_id, event_type, data
        ) values (
            target_workspace_id,
            'publication',
            (result ->> 'publication_id')::uuid,
            'exact_telegram_publication_quarantined',
            jsonb_build_object(
                'job_id', result ->> 'job_id',
                'error_code', 'double_fact_check_approval_invalid'
            )
        );

        quarantined_count := quarantined_count + 1;
        if quarantined_count >= quarantine_limit then
            return null;
        end if;
    end loop;
end;
$$;

create or replace function public.mark_exact_telegram_attempt_started(
    target_job_id uuid,
    target_worker_id text,
    target_request_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    pinned public.jobs%rowtype;
begin
    select job.* into pinned
    from public.jobs as job
    join public.content_items as item
      on item.workspace_id = job.workspace_id
     and item.id = job.content_item_id
    where job.id = target_job_id
      and job.job_kind = 'publish'
      and job.input ->> 'workflow' = 'exact_telegram_publication_v1'
    for update of item;
    if not found then
        raise exception 'exact Telegram publication job does not exist'
            using errcode = '23514';
    end if;

    perform private.require_double_fact_check_approval(
        pinned.workspace_id,
        pinned.content_item_id,
        (pinned.input ->> 'content_version_id')::uuid,
        (pinned.input ->> 'approval_id')::uuid
    );
    return private.mark_exact_telegram_attempt_before_double_fact_check(
        target_job_id,
        target_worker_id,
        target_request_sha256
    );
end;
$$;

revoke all on function private.has_valid_double_fact_check_report(jsonb)
    from public, anon, authenticated, service_role;
revoke all on function private.require_double_fact_check_approval(
    uuid, uuid, uuid, uuid
) from public, anon, authenticated, service_role;
revoke all on function private.request_content_publication_before_double_fact_check(
    uuid, uuid, text, timestamptz, text
) from public, anon, authenticated, service_role;
revoke all on function private.record_manual_observation_before_double_fact_check(
    uuid, uuid, uuid, text, text
) from public, anon, authenticated, service_role;
revoke all on function private.request_studio_telegram_before_double_fact_check(
    uuid, uuid, uuid, text
) from public, anon, authenticated, service_role;
revoke all on function private.claim_exact_telegram_job_before_double_fact_check(
    uuid, text, integer
) from public, anon, authenticated, service_role;
revoke all on function private.mark_exact_telegram_attempt_before_double_fact_check(
    uuid, text, text
) from public, anon, authenticated, service_role;

revoke all on function public.record_studio_content_review_v2(
    uuid, uuid, uuid, text, text, boolean, boolean, text[], text, text
) from public, anon, authenticated, service_role;
revoke all on function public.get_content_review_summary(uuid, uuid)
    from public, anon, authenticated, service_role;
revoke all on function public.request_content_publication(
    uuid, uuid, text, timestamptz, text
) from public, anon, authenticated, service_role;
revoke all on function public.record_manual_publication_observation(
    uuid, uuid, uuid, text, text
) from public, anon, authenticated, service_role;
revoke all on function public.request_studio_telegram_publication(
    uuid, uuid, uuid, text
) from public, anon, authenticated, service_role;
revoke all on function public.claim_exact_telegram_publication_job(
    uuid, text, integer
) from public, anon, authenticated, service_role;
revoke all on function public.mark_exact_telegram_attempt_started(
    uuid, text, text
) from public, anon, authenticated, service_role;

-- Legacy review entry points cannot create any new approved row after this
-- migration. Historical calls remain identifiable by their function names,
-- while all runtime review writes use record_studio_content_review_v2.
revoke all on function public.review_content_version(uuid, uuid, text, text)
    from public, anon, authenticated, service_role;
revoke all on function public.record_studio_content_review(
    uuid, uuid, uuid, text, text[], text, text
) from public, anon, authenticated, service_role;

grant execute on function public.record_studio_content_review_v2(
    uuid, uuid, uuid, text, text, boolean, boolean, text[], text, text
) to service_role;
grant execute on function public.get_content_review_summary(uuid, uuid)
    to service_role;
grant execute on function public.request_content_publication(
    uuid, uuid, text, timestamptz, text
) to authenticated;
grant execute on function public.record_manual_publication_observation(
    uuid, uuid, uuid, text, text
) to service_role;
grant execute on function public.request_studio_telegram_publication(
    uuid, uuid, uuid, text
) to service_role;
grant execute on function public.claim_exact_telegram_publication_job(
    uuid, text, integer
) to service_role;
grant execute on function public.mark_exact_telegram_attempt_started(
    uuid, text, text
) to service_role;

notify pgrst, 'reload schema';

commit;
