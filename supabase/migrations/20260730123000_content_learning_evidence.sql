-- Immutable aggregate quiz-learning evidence used only to reorder eligible
-- official how-to sources. It cannot create facts, approve, or publish.

begin;

create table private.content_learning_evidence (
    workspace_id uuid not null,
    client_id text not null check (
        client_id in ('yellow', 'origintrail', 'squid', 'babylon')
    ),
    snapshot_hash text not null check (
        snapshot_hash ~ '^[a-f0-9]{64}$'
    ),
    schema_version text not null check (schema_version = '1.2'),
    generated_at timestamptz not null,
    window_start timestamptz not null,
    window_end timestamptz not null,
    learning_method text not null check (
        learning_method = 'tutorial-priority-v1'
    ),
    sanitized_learning jsonb not null check (
        jsonb_typeof(sanitized_learning) = 'object'
        and octet_length(sanitized_learning::text) <= 16384
    ),
    created_at timestamptz not null default statement_timestamp(),
    primary key (
        workspace_id,
        client_id,
        snapshot_hash,
        learning_method
    ),
    foreign key (workspace_id, client_id)
        references public.workspace_clients(workspace_id, client_id)
        on delete restrict,
    check (window_end > window_start)
);

create index content_learning_evidence_created_idx
    on private.content_learning_evidence (
        workspace_id,
        client_id,
        created_at desc
    );

revoke all on table private.content_learning_evidence
    from public, anon, authenticated, service_role;

create or replace function public.record_content_learning_evidence(
    target_workspace_id uuid,
    target_client_id text,
    target_snapshot_hash text,
    target_schema_version text,
    target_generated_at timestamptz,
    target_window_start timestamptz,
    target_window_end timestamptz,
    target_learning_method text,
    target_learning jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    gap jsonb;
    committed private.content_learning_evidence%rowtype;
    inserted boolean := false;
begin
    if target_client_id is null
       or target_client_id not in (
        'yellow', 'origintrail', 'squid', 'babylon'
       )
       or target_snapshot_hash is null
       or target_snapshot_hash !~ '^[a-f0-9]{64}$'
       or target_schema_version is distinct from '1.2'
       or target_learning_method is distinct from 'tutorial-priority-v1'
       or target_generated_at is null
       or target_generated_at < statement_timestamp() - interval '24 hours'
       or target_generated_at > statement_timestamp() + interval '5 minutes'
       or target_window_start is null
       or target_window_end is null
       or target_window_end - target_window_start < interval '1 day'
       or target_window_end - target_window_start > interval '31 days'
       or target_window_end < statement_timestamp() - interval '24 hours'
       or target_window_end > statement_timestamp() + interval '5 minutes'
       or jsonb_typeof(target_learning) is distinct from 'object'
       or not (target_learning ?& array['tutorial_priority', 'gaps'])
       or target_learning - array['tutorial_priority', 'gaps'] <> '{}'::jsonb
       or jsonb_typeof(target_learning -> 'tutorial_priority')
            is distinct from 'number'
       or (target_learning ->> 'tutorial_priority')::numeric
            not between 0 and 1
       or jsonb_typeof(target_learning -> 'gaps') is distinct from 'array'
       or jsonb_array_length(target_learning -> 'gaps') > 8
       or octet_length(target_learning::text) > 16384 then
        raise exception 'content learning evidence is invalid'
            using errcode = '22023';
    end if;

    if not exists (
        select 1
        from public.workspace_clients as client
        where client.workspace_id = target_workspace_id
          and client.client_id = target_client_id
          and client.active is true
    ) then
        raise exception 'content learning workspace client is not active'
            using errcode = '23514';
    end if;

    for gap in
        select value
        from jsonb_array_elements(target_learning -> 'gaps')
    loop
        if jsonb_typeof(gap) is distinct from 'object'
           or not (
                gap ?& array[
                    'category', 'attempts', 'participants', 'accuracy_pct'
                ]
           )
           or gap - array[
                'category', 'attempts', 'participants', 'accuracy_pct'
           ] <> '{}'::jsonb
           or jsonb_typeof(gap -> 'category') is distinct from 'string'
           or char_length(gap ->> 'category') not between 1 and 80
           or gap ->> 'category' is distinct from btrim(gap ->> 'category')
           or gap ->> 'category' ~ '[[:cntrl:]]'
           or gap ->> 'category' ~* 'https?://'
           or gap ->> 'category' like '%@%'
           or jsonb_typeof(gap -> 'attempts') is distinct from 'number'
           or (gap ->> 'attempts')::integer < 20
           or jsonb_typeof(gap -> 'participants') is distinct from 'number'
           or (gap ->> 'participants')::integer < 5
           or (gap ->> 'participants')::integer
                > (gap ->> 'attempts')::integer
           or jsonb_typeof(gap -> 'accuracy_pct') is distinct from 'number'
           or (gap ->> 'accuracy_pct')::numeric not between 0 and 100 then
            raise exception 'content learning gap is invalid'
                using errcode = '22023';
        end if;
    end loop;

    insert into private.content_learning_evidence (
        workspace_id,
        client_id,
        snapshot_hash,
        schema_version,
        generated_at,
        window_start,
        window_end,
        learning_method,
        sanitized_learning
    ) values (
        target_workspace_id,
        target_client_id,
        target_snapshot_hash,
        target_schema_version,
        target_generated_at,
        target_window_start,
        target_window_end,
        target_learning_method,
        target_learning
    )
    on conflict (
        workspace_id,
        client_id,
        snapshot_hash,
        learning_method
    ) do nothing
    returning true into inserted;
    inserted := coalesce(inserted, false);

    select evidence.* into committed
    from private.content_learning_evidence as evidence
    where evidence.workspace_id = target_workspace_id
      and evidence.client_id = target_client_id
      and evidence.snapshot_hash = target_snapshot_hash
      and evidence.learning_method = target_learning_method;

    if not found
       or committed.schema_version is distinct from target_schema_version
       or committed.generated_at is distinct from target_generated_at
       or committed.window_start is distinct from target_window_start
       or committed.window_end is distinct from target_window_end
       or committed.sanitized_learning is distinct from target_learning then
        raise exception 'content learning evidence retry does not match'
            using errcode = '23505';
    end if;

    return jsonb_build_object(
        'recorded', true,
        'snapshot_hash', target_snapshot_hash,
        'reused', not inserted
    );
end;
$$;

revoke all on function public.record_content_learning_evidence(
    uuid, text, text, text, timestamptz, timestamptz, timestamptz, text, jsonb
) from public, anon, authenticated;
grant execute on function public.record_content_learning_evidence(
    uuid, text, text, text, timestamptz, timestamptz, timestamptz, text, jsonb
) to service_role;

commit;

-- ROLLBACK
-- drop function if exists public.record_content_learning_evidence(
--   uuid, text, text, text, timestamptz, timestamptz, timestamptz, text, jsonb
-- );
-- drop table if exists private.content_learning_evidence;
