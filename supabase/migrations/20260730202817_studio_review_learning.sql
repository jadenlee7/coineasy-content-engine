-- Human review from the signed Studio session, plus bounded brand-learning
-- projections. Review comments remain human-only; generation receives approved
-- output excerpts and allowlisted rejection reason codes, never free-form notes.

begin;

alter table public.approvals
    alter column reviewer_id drop not null,
    add column reviewer_source text not null default 'supabase_auth',
    add column reason_codes text[] not null default '{}'::text[],
    add column idempotency_key text;

alter table public.approvals
    add constraint approvals_reviewer_source_check check (
        (reviewer_source = 'supabase_auth' and reviewer_id is not null)
        or (reviewer_source = 'studio_session' and reviewer_id is null)
    ),
    add constraint approvals_reason_codes_check check (
        cardinality(reason_codes) <= 5
        and array_position(reason_codes, null) is null
        and reason_codes <@ array[
            'off_brand_tone',
            'unsupported_claim',
            'awkward_korean',
            'visual_brand_mismatch',
            'duplicate_logo',
            'source_fidelity',
            'channel_fit',
            'other'
        ]::text[]
    ),
    add constraint approvals_idempotency_key_check check (
        idempotency_key is null
        or char_length(idempotency_key) between 8 and 200
    );

create unique index approvals_studio_idempotency_idx
    on public.approvals (
        workspace_id,
        content_item_id,
        content_version_id,
        idempotency_key
    )
    where idempotency_key is not null;

create index approvals_brand_learning_idx
    on public.approvals (
        workspace_id,
        client_id,
        decision,
        created_at desc
    );

create or replace function public.record_studio_content_review(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    review_decision text,
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
           or existing.comment is distinct from normalized_comment then
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
            'created_at', existing.created_at,
            'reused', true
        );
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
        idempotency_key
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
        review_idempotency_key
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
            'reason_codes', to_jsonb(normalized_codes)
        )
    );

    return jsonb_build_object(
        'approval_id', approval_id,
        'content_item_id', item.id,
        'content_version_id', target_version.id,
        'decision', review_decision,
        'status', review_decision,
        'reason_codes', normalized_codes,
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
            'created_at', latest.created_at
        )
    end
    from (select 1) as seed
    left join lateral (
        select approval.*
        from public.approvals as approval
        where approval.workspace_id = target_workspace_id
          and approval.content_item_id = target_content_item_id
        order by approval.created_at desc, approval.id desc
        limit 1
    ) as latest on true
    where exists (
        select 1
        from public.content_items as item
        where item.workspace_id = target_workspace_id
          and item.id = target_content_item_id
    );
$$;

create or replace function public.get_brand_review_guidance(
    target_workspace_id uuid,
    target_client_id text,
    target_content_kind text,
    target_example_limit integer default 3
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    approved_examples jsonb;
    avoid_reasons jsonb;
begin
    if target_workspace_id is null
       or target_client_id not in ('yellow', 'origintrail', 'squid', 'babylon')
       or target_content_kind not in ('daily_news', 'article', 'tutorial')
       or target_example_limit not between 0 and 3 then
        raise exception 'brand review guidance request is invalid'
            using errcode = '22023';
    end if;
    if not exists (
        select 1
        from public.workspace_clients as client
        where client.workspace_id = target_workspace_id
          and client.client_id = target_client_id
          and client.active is true
    ) then
        raise exception 'brand review guidance client is not active'
            using errcode = '23514';
    end if;

    select coalesce(
        jsonb_agg(
            jsonb_build_object(
                'content_item_id', sample.content_item_id,
                'content_version_id', sample.content_version_id,
                'content_kind', sample.content_kind,
                'text', sample.example_text,
                'approved_at', sample.approved_at
            )
            order by sample.approved_at desc, sample.content_item_id desc
        ),
        '[]'::jsonb
    )
    into approved_examples
    from (
        select
            item.id as content_item_id,
            version.id as content_version_id,
            item.content_kind,
            left(
                concat_ws(
                    E'\n',
                    nullif(version.title, ''),
                    nullif(version.content #>> '{spec,headline}', ''),
                    nullif(version.content ->> 'lead', ''),
                    nullif(version.content #>> '{series,title}', ''),
                    nullif(version.content #>> '{series,subtitle}', ''),
                    nullif(version.channel_copy ->> 'telegram', ''),
                    nullif(version.channel_copy ->> 'x', '')
                ),
                1200
            ) as example_text,
            approval.created_at as approved_at
        from public.approvals as approval
        join public.content_items as item
          on item.workspace_id = approval.workspace_id
         and item.client_id = approval.client_id
         and item.id = approval.content_item_id
         and item.current_version_id = approval.content_version_id
        join public.content_versions as version
          on version.workspace_id = item.workspace_id
         and version.content_item_id = item.id
         and version.id = item.current_version_id
        where approval.workspace_id = target_workspace_id
          and approval.client_id = target_client_id
          and approval.decision = 'approved'
          and item.content_kind = target_content_kind
          and item.status in ('approved', 'scheduled', 'published')
          and not (
              version.generation_meta @> '{"mock_mode":true}'::jsonb
          )
        order by approval.created_at desc, approval.id desc
        limit target_example_limit
    ) as sample
    where char_length(sample.example_text) > 0;

    select coalesce(
        jsonb_agg(
            jsonb_build_object(
                'code', reason.code,
                'count', reason.reason_count
            )
            order by reason.reason_count desc, reason.code
        ),
        '[]'::jsonb
    )
    into avoid_reasons
    from (
        select code, count(*)::integer as reason_count
        from public.approvals as approval
        join public.content_items as item
          on item.workspace_id = approval.workspace_id
         and item.client_id = approval.client_id
         and item.id = approval.content_item_id
        cross join lateral unnest(approval.reason_codes) as code
        where approval.workspace_id = target_workspace_id
          and approval.client_id = target_client_id
          and approval.decision = 'rejected'
          and item.content_kind = target_content_kind
          and approval.created_at >= statement_timestamp() - interval '90 days'
        group by code
        order by count(*) desc, code
        limit 5
    ) as reason;

    return jsonb_build_object(
        'policy_version', 'brand-review-learning@1',
        'approved_examples', approved_examples,
        'avoid_reason_codes', avoid_reasons
    );
end;
$$;

revoke all on function public.record_studio_content_review(
    uuid, uuid, uuid, text, text[], text, text
) from public, anon, authenticated, service_role;
revoke all on function public.get_content_review_summary(uuid, uuid)
    from public, anon, authenticated, service_role;
revoke all on function public.get_brand_review_guidance(
    uuid, text, text, integer
) from public, anon, authenticated, service_role;

grant execute on function public.record_studio_content_review(
    uuid, uuid, uuid, text, text[], text, text
) to service_role;
grant execute on function public.get_content_review_summary(uuid, uuid)
    to service_role;
grant execute on function public.get_brand_review_guidance(
    uuid, text, text, integer
) to service_role;

commit;
