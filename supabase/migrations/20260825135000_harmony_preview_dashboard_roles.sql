-- Read-only Harmony Preview projection and least-privilege role closure.

begin;

create or replace function private.harmony_preview_collaboration_object(
    target_workspace_id uuid,
    target_client_id text,
    target_round_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    round_row agent_runtime.harmony_rounds%rowtype;
    connector_payloads jsonb;
    stage_payloads jsonb;
    inbox_payload jsonb;
    body jsonb;
begin
    select * into strict round_row
    from agent_runtime.harmony_rounds candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.round_id = target_round_id;
    if not private.harmony_preview_round_inputs_current(
        round_row.workspace_id,
        round_row.client_id,
        round_row.signal_manifest
    ) then
        return null;
    end if;
    select pg_catalog.jsonb_agg(receipt.payload order by signal.lane)
    into strict connector_payloads
    from agent_runtime.harmony_signals signal
    join agent_runtime.harmony_connector_attestation_receipts receipt
      on receipt.workspace_id = signal.workspace_id
     and receipt.client_id = signal.client_id
     and receipt.receipt_id = signal.connector_receipt_id
     and receipt.payload_sha256 = signal.connector_receipt_sha256
    where signal.workspace_id = round_row.workspace_id
      and signal.client_id = round_row.client_id
      and signal.payload_sha256 in (
          select value ->> 'signal_payload_sha256'
          from pg_catalog.jsonb_array_elements(round_row.signal_manifest)
      )
    having pg_catalog.count(*) = 4;
    select pg_catalog.jsonb_agg(receipt.payload order by receipt.ordinal)
    into strict stage_payloads
    from agent_runtime.harmony_stage_receipts receipt
    where receipt.workspace_id = round_row.workspace_id
      and receipt.client_id = round_row.client_id
      and receipt.plan_id = round_row.plan_id
    having pg_catalog.count(*) = 5
       and pg_catalog.bool_and(
            receipt.input_sha256 = case receipt.ordinal
                when 1 then round_row.input_set_sha256
                else (
                    select previous.output_sha256
                    from agent_runtime.harmony_stage_receipts previous
                    where previous.workspace_id = receipt.workspace_id
                      and previous.client_id = receipt.client_id
                      and previous.plan_id = receipt.plan_id
                      and previous.ordinal = receipt.ordinal - 1
                )
            end
       )
       and pg_catalog.bool_and(
            receipt.previous_receipt_sha256 is not distinct from
            case receipt.ordinal
                when 1 then null
                else (
                    select previous.receipt_sha256
                    from agent_runtime.harmony_stage_receipts previous
                    where previous.workspace_id = receipt.workspace_id
                      and previous.client_id = receipt.client_id
                      and previous.plan_id = receipt.plan_id
                      and previous.ordinal = receipt.ordinal - 1
                )
            end
       );
    select inbox.payload into strict inbox_payload
    from agent_runtime.harmony_operator_inbox inbox
    join agent_runtime.harmony_stage_receipts operator_stage
      on operator_stage.workspace_id = inbox.workspace_id
     and operator_stage.client_id = inbox.client_id
     and operator_stage.receipt_id = inbox.stage_receipt_id
     and operator_stage.output_sha256 = inbox.scope_sha256
     and operator_stage.stage = 'operator_inbox'
    join agent_runtime.harmony_stage_receipts qa_stage
      on qa_stage.workspace_id = inbox.workspace_id
     and qa_stage.client_id = inbox.client_id
     and qa_stage.receipt_id = inbox.qa_receipt_id
     and qa_stage.receipt_sha256 = inbox.qa_receipt_sha256
     and qa_stage.output_sha256 = inbox.qa_output_sha256
     and qa_stage.stage = 'independent_qa'
     and qa_stage.verdict = 'passed'
     and qa_stage.reviewer_principal_id = qa_stage.principal_id
    where inbox.workspace_id = round_row.workspace_id
      and inbox.client_id = round_row.client_id
      and inbox.plan_id = round_row.plan_id;
    body := pg_catalog.jsonb_build_object(
        'actual_cost_microusd', 0,
        'aggregate_only', true,
        'automatic_publication', false,
        'client_id', round_row.client_id,
        'connector_receipts', connector_payloads,
        'external_calls', false,
        'input_set_sha256', round_row.input_set_sha256,
        'operator_decision_recorded', false,
        'operator_inbox', inbox_payload,
        'plan_id', round_row.plan_id::text,
        'private_content_only', true,
        'provider_calls', false,
        'publication_calls', false,
        'round_id', round_row.round_id::text,
        'schema_version', 'harmony-collaboration-round@1',
        'signal_manifest', round_row.signal_manifest,
        'stage_receipts', stage_payloads,
        'status', 'operator_review_pending',
        'synthetic', true,
        'workspace_id', round_row.workspace_id::text
    );
    return body || pg_catalog.jsonb_build_object(
        'round_sha256', private.agent_json_sha256(body)
    );
exception
    when no_data_found then
        return null;
end;
$$;

create or replace function public.get_preview_harmony_round(
    target_workspace_id uuid,
    target_client_id text,
    target_round_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
    if not private.harmony_preview_scope_matches(
        target_workspace_id, target_client_id,
        array['coineasy_harmony_orchestrator',
              'coineasy_harmony_operator']::text[]
    ) then
        raise exception 'harmony_preview_round_scope_invalid';
    end if;
    return private.harmony_preview_collaboration_object(
        target_workspace_id, target_client_id, target_round_id
    );
end;
$$;

create or replace function public.get_preview_harmony_dashboard(
    target_workspace_id uuid,
    target_client_id text
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    latest_round agent_runtime.harmony_rounds%rowtype;
    latest_payload jsonb;
    inbox_payload jsonb;
    counter jsonb;
begin
    if not private.harmony_preview_scope_matches(
        target_workspace_id, target_client_id,
        array['coineasy_harmony_dashboard']::text[]
    ) then
        raise exception 'harmony_preview_dashboard_scope_invalid';
    end if;
    select candidate.* into latest_round
    from agent_runtime.harmony_rounds candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and private.harmony_preview_collaboration_object(
            candidate.workspace_id, candidate.client_id, candidate.round_id
          ) is not null
    order by candidate.created_at desc, candidate.round_id desc
    limit 1;
    if found then
        select pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'headline_ko', pg_catalog.left(
                content_stage.artifact ->> 'headline_ko', 160
            ),
            'input_set_sha256', latest_round.input_set_sha256,
            'plan_id', latest_round.plan_id::text,
            'round_id', latest_round.round_id::text,
            'round_sha256',
                (private.harmony_preview_collaboration_object(
                    latest_round.workspace_id,
                    latest_round.client_id,
                    latest_round.round_id
                ) ->> 'round_sha256'),
            'schema_version', 'harmony-dashboard-round@1',
            'stages', pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                'input_sha256', stage.input_sha256,
                'ordinal', stage.ordinal,
                'output_sha256', stage.output_sha256,
                'receipt_sha256', stage.receipt_sha256,
                'recorded_at', pg_catalog.to_char(
                    stage.created_at at time zone 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS"Z"'
                ),
                'stage', stage.stage,
                'verdict', stage.verdict
            ) order by stage.ordinal),
            'status', 'operator_review_pending',
            'summary_ko', pg_catalog.left(
                content_stage.artifact ->> 'summary_ko', 600
            )
        ) into latest_payload
        from agent_runtime.harmony_stage_receipts stage
        join agent_runtime.harmony_stage_receipts content_stage
          on content_stage.workspace_id = stage.workspace_id
         and content_stage.client_id = stage.client_id
         and content_stage.plan_id = stage.plan_id
         and content_stage.stage = 'private_content'
        where stage.workspace_id = latest_round.workspace_id
          and stage.client_id = latest_round.client_id
          and stage.plan_id = latest_round.plan_id
        group by content_stage.artifact;
    end if;
    select coalesce(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
        'automatic_publication', false,
        'created_at', pg_catalog.to_char(
            inbox.created_at at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        ),
        'inbox_id', inbox.inbox_id::text,
        'plan_id', inbox.plan_id::text,
        'qa_output_sha256', inbox.qa_output_sha256,
        'qa_receipt_id', inbox.qa_receipt_id::text,
        'qa_receipt_sha256', inbox.qa_receipt_sha256,
        'round_id', inbox.round_id::text,
        'schema_version', 'harmony-dashboard-inbox@1',
        'scope_sha256', inbox.scope_sha256,
        'status', inbox.status
    ) order by inbox.created_at desc, inbox.inbox_id desc), '[]'::jsonb)
    into inbox_payload
    from (
        select *
        from agent_runtime.harmony_operator_inbox candidate
        where candidate.workspace_id = target_workspace_id
          and candidate.client_id = target_client_id
          and candidate.status = 'pending'
          and private.harmony_preview_collaboration_object(
                candidate.workspace_id,
                candidate.client_id,
                candidate.round_id
              ) is not null
        order by candidate.created_at desc, candidate.inbox_id desc
        limit 25
    ) inbox;
    with current_rounds as materialized (
        select candidate.workspace_id, candidate.client_id,
               candidate.round_id, candidate.plan_id,
               candidate.signal_manifest
        from agent_runtime.harmony_rounds candidate
        where candidate.workspace_id = target_workspace_id
          and candidate.client_id = target_client_id
          and private.harmony_preview_collaboration_object(
                candidate.workspace_id,
                candidate.client_id,
                candidate.round_id
              ) is not null
    ), current_signals as materialized (
        select distinct signal.workspace_id, signal.client_id,
               signal.signal_id, signal.connector_receipt_id
        from current_rounds round_value
        cross join lateral pg_catalog.jsonb_array_elements(
            round_value.signal_manifest
        ) entry(value)
        join agent_runtime.harmony_signals signal
          on signal.workspace_id = round_value.workspace_id
         and signal.client_id = round_value.client_id
         and signal.signal_id = (entry.value ->> 'signal_id')::uuid
         and signal.payload_sha256
                = entry.value ->> 'signal_payload_sha256'
    )
    select pg_catalog.jsonb_build_object(
        'connector_receipts', (select pg_catalog.count(distinct connector_receipt_id)
            from current_signals),
        'pending_operator_inbox', (select pg_catalog.count(*)
            from agent_runtime.harmony_operator_inbox row_value
            join current_rounds round_value
              on round_value.workspace_id = row_value.workspace_id
             and round_value.client_id = row_value.client_id
             and round_value.round_id = row_value.round_id
             and round_value.plan_id = row_value.plan_id
            where row_value.status = 'pending'),
        'plans', (select pg_catalog.count(*) from current_rounds),
        'rounds', (select pg_catalog.count(*) from current_rounds),
        'signals', (select pg_catalog.count(*) from current_signals),
        'stage_receipts', (select pg_catalog.count(*)
            from agent_runtime.harmony_stage_receipts row_value
            join current_rounds round_value
              on round_value.workspace_id = row_value.workspace_id
             and round_value.client_id = row_value.client_id
             and round_value.round_id = row_value.round_id
             and round_value.plan_id = row_value.plan_id)
    ) into counter;
    return pg_catalog.jsonb_build_object(
        'client_id', target_client_id,
        'counts', counter,
        'flags', pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'external_calls', false,
            'provider_calls', false,
            'publication_calls', false,
            'read_only', true
        ),
        'latest_round', latest_payload,
        'observed_at', pg_catalog.to_char(
            pg_catalog.date_trunc('second', statement_timestamp()) at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        ),
        'operator_inbox', inbox_payload,
        'schema_version', 'harmony-preview-dashboard@1',
        'trust', pg_catalog.jsonb_build_object(
            'client_scope_verified', true,
            'environment', 'preview',
            'portable_trust', false
        ),
        'workspace_id', target_workspace_id::text
    );
end;
$$;

revoke all on function private.harmony_preview_collaboration_object(
    uuid, text, uuid
) from public, anon, authenticated, service_role;
revoke all on function public.get_preview_harmony_round(uuid, text, uuid)
from public, anon, authenticated, service_role;
revoke all on function public.get_preview_harmony_dashboard(uuid, text)
from public, anon, authenticated, service_role;

do $roles$
declare
    role_name text;
begin
    foreach role_name in array array[
        'coineasy_harmony_connector',
        'coineasy_harmony_orchestrator',
        'coineasy_harmony_content',
        'coineasy_harmony_qa',
        'coineasy_harmony_operator',
        'coineasy_harmony_recap',
        'coineasy_harmony_dashboard'
    ] loop
        if not exists (
            select 1 from pg_catalog.pg_roles where rolname = role_name
        ) then
            execute pg_catalog.format('create role %I', role_name);
        end if;
        execute pg_catalog.format(
            'alter role %I nologin noinherit nobypassrls nosuperuser ' ||
            'nocreatedb nocreaterole noreplication', role_name
        );
        execute pg_catalog.format('grant %I to authenticator', role_name);
    end loop;
end
$roles$;

grant usage on schema public to
    coineasy_harmony_connector,
    coineasy_harmony_orchestrator,
    coineasy_harmony_content,
    coineasy_harmony_qa,
    coineasy_harmony_operator,
    coineasy_harmony_recap,
    coineasy_harmony_dashboard;
grant execute on function public.submit_preview_harmony_signal(
    uuid, text, uuid, jsonb
) to coineasy_harmony_connector;
grant execute on function public.create_preview_harmony_squid_plan(
    uuid, text, uuid, uuid, uuid, text[], text
) to coineasy_harmony_orchestrator;
grant execute on function public.append_preview_harmony_squid_stage(
    uuid, text, uuid, uuid, text, uuid, uuid, jsonb
) to
    coineasy_harmony_content,
    coineasy_harmony_qa,
    coineasy_harmony_operator,
    coineasy_harmony_recap;
grant execute on function public.get_preview_harmony_round(uuid, text, uuid)
to coineasy_harmony_orchestrator, coineasy_harmony_operator;
grant execute on function public.get_preview_harmony_dashboard(uuid, text)
to coineasy_harmony_dashboard;

commit;
