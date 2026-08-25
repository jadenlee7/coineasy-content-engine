-- Complete the synthetic Squid Preview stage chain.
--
-- Each stage is append-only, exactly-once, and authorized by a distinct JWT
-- role/capability.  Artifacts are derived from the existing private
-- needs_review version and prior receipts; no content, provider, message,
-- approval, or publication row is created.

begin;

create or replace function private.harmony_preview_round_inputs_current(
    target_workspace_id uuid,
    target_client_id text,
    target_signal_manifest jsonb
)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select (
        select pg_catalog.count(*)
        from pg_catalog.jsonb_array_elements(target_signal_manifest) entry(value)
        join agent_runtime.harmony_signals signal
          on signal.workspace_id = target_workspace_id
         and signal.client_id = target_client_id
         and signal.signal_id = (entry.value ->> 'signal_id')::uuid
         and signal.payload_sha256 = entry.value ->> 'signal_payload_sha256'
         and signal.lane = entry.value ->> 'lane'
         and signal.upstream_receipt_sha256
                = entry.value ->> 'upstream_receipt_sha256'
        join agent_runtime.harmony_connector_attestation_receipts receipt
          on receipt.workspace_id = signal.workspace_id
         and receipt.client_id = signal.client_id
         and receipt.receipt_id = (entry.value ->> 'connector_receipt_id')::uuid
         and receipt.payload_sha256
                = entry.value ->> 'connector_receipt_sha256'
         and receipt.signal_id = signal.signal_id
         and receipt.signal_payload_sha256 = signal.payload_sha256
        where signal.observed_at <= statement_timestamp()
          and signal.expires_at > statement_timestamp()
          and receipt.verified_at <= statement_timestamp()
          and receipt.expires_at > statement_timestamp()
    ) = 4
    and exists (
        select 1
        from agent_runtime.harmony_signals signal
        where signal.workspace_id = target_workspace_id
          and signal.client_id = target_client_id
          and signal.lane = 'content_source'
          and signal.payload_sha256 in (
              select value ->> 'signal_payload_sha256'
              from pg_catalog.jsonb_array_elements(target_signal_manifest)
          )
          and signal.official_source_binding_sha256
                = signal.upstream_receipt_sha256
          and private.harmony_preview_squid_official_source_binding(
                signal.payload
              ) = signal.official_source_binding_sha256
    )
$$;

create or replace function public.append_preview_harmony_squid_stage(
    target_workspace_id uuid,
    target_client_id text,
    target_round_id uuid,
    target_plan_id uuid,
    target_stage text,
    target_receipt_id uuid,
    target_inbox_id uuid default null,
    target_qa_evidence jsonb default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    claims jsonb;
    binding jsonb;
    expected_role text;
    expected_capability text;
    stage_ordinal smallint;
    stage_actor text;
    round_row agent_runtime.harmony_rounds%rowtype;
    previous_row agent_runtime.harmony_stage_receipts%rowtype;
    existing agent_runtime.harmony_stage_receipts%rowtype;
    qa_row agent_runtime.harmony_stage_receipts%rowtype;
    source_signal agent_runtime.harmony_signals%rowtype;
    content_item public.content_items%rowtype;
    content_version public.content_versions%rowtype;
    created_time timestamptz;
    headline text;
    summary text;
    content_snapshot_sha text;
    artifact jsonb;
    artifact_sha text;
    stage_payload jsonb;
    stage_sha text;
    inbox_payload jsonb;
begin
    if target_client_id <> 'squid' or target_stage not in (
        'private_content', 'independent_qa', 'operator_inbox', 'recap'
    ) then
        raise exception 'harmony_preview_stage_scope_invalid';
    end if;
    select lane.role_name, lane.capability_name, lane.ordinal, lane.actor_name
    into expected_role, expected_capability, stage_ordinal, stage_actor
    from (values
        ('private_content', 'coineasy_harmony_content',
            'harmony_prepare_private_content', 2::smallint, 'content_engine'),
        ('independent_qa', 'coineasy_harmony_qa',
            'harmony_independent_qa', 3::smallint, 'codex'),
        ('operator_inbox', 'coineasy_harmony_operator',
            'harmony_operator_inbox', 4::smallint, 'human_operator_inbox'),
        ('recap', 'coineasy_harmony_recap',
            'harmony_recap', 5::smallint, 'coineasy_recap')
    ) lane(stage_name, role_name, capability_name, ordinal, actor_name)
    where lane.stage_name = target_stage;
    if not private.harmony_preview_stage_claims_match(
        target_workspace_id, target_client_id,
        expected_role, expected_capability
    ) or (target_stage = 'recap') <> (target_inbox_id is not null)
      or (target_stage = 'independent_qa')
            <> (target_qa_evidence is not null)
    then
        raise exception 'harmony_preview_stage_claim_invalid';
    end if;
    claims := nullif(
        pg_catalog.current_setting('request.jwt.claims', true), ''
    )::jsonb;
    binding := private.harmony_preview_stage_binding();
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'harmony_preview_stage:' || target_workspace_id::text || ':' ||
        target_client_id || ':' || target_plan_id::text || ':' || target_stage,
        0
    ));
    select * into strict round_row
    from agent_runtime.harmony_rounds candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.round_id = target_round_id
      and candidate.plan_id = target_plan_id
      and candidate.status = 'planned';
    if not private.harmony_preview_round_inputs_current(
        target_workspace_id, target_client_id, round_row.signal_manifest
    ) then
        raise exception 'harmony_preview_stage_input_expired_or_tampered';
    end if;
    select * into existing
    from agent_runtime.harmony_stage_receipts candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.plan_id = target_plan_id
      and candidate.stage = target_stage
    for update;
    if found then
        if existing.receipt_id <> target_receipt_id
           or existing.principal_id <> (claims ->> 'producer_principal_id')::uuid
           or existing.producer_release_sha <> claims ->> 'release_sha'
           or existing.config_sha256 <> claims ->> 'config_sha256'
           or existing.binding_receipt_sha256
                <> binding ->> 'binding_receipt_sha256'
           or (
                target_stage = 'independent_qa'
                and existing.artifact ->> 'evidence_sha256'
                    <> private.agent_json_sha256(target_qa_evidence)
           )
           or (
                target_stage = 'recap'
                and not exists (
                    select 1
                    from agent_runtime.harmony_operator_inbox inbox
                    where inbox.workspace_id = existing.workspace_id
                      and inbox.client_id = existing.client_id
                      and inbox.plan_id = existing.plan_id
                      and inbox.inbox_id = target_inbox_id
                )
           ) then
            raise exception 'harmony_preview_stage_idempotency_conflict';
        end if;
        return pg_catalog.jsonb_build_object(
            'ok', true, 'reused', true, 'stage_receipt', existing.payload,
            'database_calls', true, 'external_calls', false,
            'provider_calls', false, 'publication_calls', false,
            'automatic_publication', false
        );
    end if;
    select * into strict previous_row
    from agent_runtime.harmony_stage_receipts candidate
    where candidate.workspace_id = target_workspace_id
      and candidate.client_id = target_client_id
      and candidate.plan_id = target_plan_id
      and candidate.ordinal = stage_ordinal - 1;
    if (
        select pg_catalog.count(*)
        from pg_catalog.jsonb_array_elements(round_row.signal_manifest) entry(value)
        join agent_runtime.harmony_signals signal
          on signal.workspace_id = target_workspace_id
         and signal.client_id = target_client_id
         and signal.signal_id = (entry.value ->> 'signal_id')::uuid
         and signal.payload_sha256 = entry.value ->> 'signal_payload_sha256'
         and signal.lane = entry.value ->> 'lane'
         and signal.upstream_receipt_sha256
                = entry.value ->> 'upstream_receipt_sha256'
        join agent_runtime.harmony_connector_attestation_receipts receipt
          on receipt.workspace_id = signal.workspace_id
         and receipt.client_id = signal.client_id
         and receipt.receipt_id = (entry.value ->> 'connector_receipt_id')::uuid
         and receipt.payload_sha256
                = entry.value ->> 'connector_receipt_sha256'
         and receipt.signal_id = signal.signal_id
         and receipt.signal_payload_sha256 = signal.payload_sha256
        where signal.observed_at <= statement_timestamp()
          and signal.expires_at > statement_timestamp()
          and receipt.verified_at <= statement_timestamp()
          and receipt.expires_at > statement_timestamp()
    ) <> 4 then
        raise exception 'harmony_preview_stage_input_expired_or_tampered';
    end if;
    if not exists (
        select 1
        from agent_runtime.harmony_signals signal
        where signal.workspace_id = target_workspace_id
          and signal.client_id = target_client_id
          and signal.lane = 'content_source'
          and signal.payload_sha256 in (
              select value ->> 'signal_payload_sha256'
              from pg_catalog.jsonb_array_elements(round_row.signal_manifest)
          )
          and signal.official_source_binding_sha256
                = signal.upstream_receipt_sha256
          and private.harmony_preview_squid_official_source_binding(
                signal.payload
              ) = signal.official_source_binding_sha256
    ) then
        raise exception 'harmony_preview_stage_official_source_stale';
    end if;
    if exists (
        select 1
        from agent_runtime.harmony_stage_receipts later
        where later.workspace_id = target_workspace_id
          and later.client_id = target_client_id
          and later.plan_id = target_plan_id
          and later.ordinal >= stage_ordinal
    ) then
        raise exception 'harmony_preview_stage_order_invalid';
    end if;
    created_time := pg_catalog.date_trunc('second', statement_timestamp());

    if target_stage = 'private_content' then
        select signal.* into strict source_signal
        from agent_runtime.harmony_signals signal
        where signal.workspace_id = target_workspace_id
          and signal.client_id = target_client_id
          and signal.lane = 'content_source'
          and signal.payload_sha256 in (
              select value ->> 'signal_payload_sha256'
              from pg_catalog.jsonb_array_elements(round_row.signal_manifest)
          );
        if source_signal.official_source_binding_sha256 is null
           or source_signal.upstream_receipt_sha256
                <> source_signal.official_source_binding_sha256
           or private.harmony_preview_squid_official_source_binding(
                source_signal.payload
              ) is distinct from source_signal.official_source_binding_sha256
        then
            raise exception 'harmony_preview_private_source_stale';
        end if;
        select item.* into strict content_item
        from public.content_items item
        where item.workspace_id = target_workspace_id
          and item.client_id = target_client_id
          and item.current_version_id = source_signal.official_content_version_id
          and item.status = 'needs_review';
        select version.* into strict content_version
        from public.content_versions version
        where version.workspace_id = content_item.workspace_id
          and version.content_item_id = content_item.id
          and version.id = content_item.current_version_id;
        headline := pg_catalog.btrim(content_version.title);
        summary := pg_catalog.btrim(coalesce(
            content_version.content ->> 'summary_ko',
            content_version.content ->> 'summary',
            content_version.content ->> 'body_ko',
            content_version.content ->> 'body',
            content_version.title
        ));
        if not private.agent_safe_text(headline, 1, 480, true)
           or not private.agent_safe_text(summary, 1, 1800, false)
           or headline !~ '[가-힣]'
           or summary !~ '[가-힣]'
        then
            raise exception 'harmony_preview_private_content_unsafe';
        end if;
        content_snapshot_sha := private.agent_json_sha256(
            pg_catalog.jsonb_build_object(
                'channel_copy', content_version.channel_copy,
                'content', content_version.content,
                'deliverables', content_version.deliverables,
                'generation_meta', content_version.generation_meta,
                'qa', content_version.qa,
                'title', content_version.title
            )
        );
        artifact := pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'content_snapshot_sha256', content_snapshot_sha,
            'content_version_id', content_version.id::text,
            'headline_ko', headline,
            'private_content_only', true,
            'schema_version', 'harmony-private-content@1',
            'source_binding_sha256', source_signal.official_source_binding_sha256,
            'status', 'needs_review',
            'summary_ko', summary,
            'synthetic', true
        );
    elsif target_stage = 'independent_qa' then
        if (claims ->> 'producer_principal_id')::uuid in (
            select principal_id
            from agent_runtime.harmony_stage_receipts candidate
            where candidate.workspace_id = target_workspace_id
              and candidate.client_id = target_client_id
              and candidate.plan_id = target_plan_id
              and candidate.stage in ('plan', 'private_content')
        ) then
            raise exception 'harmony_preview_qa_self_review_forbidden';
        end if;
        if pg_catalog.jsonb_typeof(target_qa_evidence) <> 'object'
           or (select pg_catalog.count(*)
               from pg_catalog.jsonb_object_keys(target_qa_evidence)) <> 6
           or not target_qa_evidence ?& array[
                'schema_version', 'reviewed_output_sha256', 'criteria',
                'findings', 'verdict', 'verifier_version'
           ]
           or target_qa_evidence ->> 'schema_version'
                <> 'harmony-independent-qa-evidence@1'
           or target_qa_evidence ->> 'reviewed_output_sha256'
                <> previous_row.output_sha256
           or target_qa_evidence ->> 'verdict' <> 'passed'
           or target_qa_evidence ->> 'verifier_version'
                <> 'harmony-deterministic-qa@1'
           or target_qa_evidence -> 'findings' <> '[]'::jsonb
           or pg_catalog.jsonb_typeof(target_qa_evidence -> 'criteria')
                <> 'object'
           or (select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(
                target_qa_evidence -> 'criteria'
              )) <> 4
           or target_qa_evidence -> 'criteria' <> pg_catalog.jsonb_build_object(
                'automatic_publication', false,
                'factual_binding', true,
                'no_external_calls', true,
                'private_only', true
              )
        then
            raise exception 'harmony_preview_qa_evidence_invalid';
        end if;
        artifact := pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'criteria_sha256', private.agent_json_sha256(
                target_qa_evidence -> 'criteria'
            ),
            'evidence_sha256', private.agent_json_sha256(target_qa_evidence),
            'reviewed_output_sha256', previous_row.output_sha256,
            'reviewer_principal_id', claims ->> 'producer_principal_id',
            'schema_version', 'harmony-independent-qa@1',
            'synthetic', true,
            'verdict', 'passed',
            'verifier_version', 'harmony-deterministic-qa@1'
        );
    elsif target_stage = 'operator_inbox' then
        if previous_row.stage <> 'independent_qa'
           or previous_row.verdict <> 'passed'
           or previous_row.reviewer_principal_id <> previous_row.principal_id
        then
            raise exception 'harmony_preview_qa_receipt_invalid';
        end if;
        qa_row := previous_row;
        artifact := pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'operator_decision_recorded', false,
            'qa_output_sha256', qa_row.output_sha256,
            'qa_receipt_id', qa_row.receipt_id::text,
            'qa_receipt_sha256', qa_row.receipt_sha256,
            'schema_version', 'harmony-operator-scope@1',
            'status', 'pending',
            'synthetic', true
        );
    else
        select candidate.* into strict qa_row
        from agent_runtime.harmony_stage_receipts candidate
        where candidate.workspace_id = target_workspace_id
          and candidate.client_id = target_client_id
          and candidate.plan_id = target_plan_id
          and candidate.stage = 'independent_qa'
          and candidate.verdict = 'passed'
          and candidate.reviewer_principal_id = candidate.principal_id;
        if previous_row.stage <> 'operator_inbox'
           or previous_row.artifact ->> 'qa_receipt_id'
                <> qa_row.receipt_id::text
           or previous_row.artifact ->> 'qa_receipt_sha256'
                <> qa_row.receipt_sha256
           or previous_row.artifact ->> 'qa_output_sha256'
                <> qa_row.output_sha256
        then
            raise exception 'harmony_preview_operator_scope_invalid';
        end if;
        artifact := pg_catalog.jsonb_build_object(
            'actual_cost_microusd', 0,
            'automatic_publication', false,
            'operator_decision_observed', false,
            'publication_count', 0,
            'schema_version', 'harmony-recap@1',
            'stage_receipt_count', 5,
            'synthetic', true
        );
    end if;
    artifact_sha := private.agent_json_sha256(artifact);
    stage_payload := private.harmony_preview_stage_receipt_payload(
        target_receipt_id, target_workspace_id, target_client_id,
        target_round_id, target_plan_id, target_stage, stage_ordinal,
        stage_actor, previous_row.receipt_sha256,
        previous_row.output_sha256, artifact_sha, created_time,
        case when target_stage = 'independent_qa' then 'passed' end,
        case when target_stage = 'independent_qa'
            then (claims ->> 'producer_principal_id')::uuid end
    );
    stage_sha := stage_payload ->> 'receipt_sha256';
    insert into agent_runtime.harmony_stage_receipts (
        workspace_id, client_id, receipt_id, round_id, plan_id, stage,
        ordinal, actor, principal_id, producer_release_sha, config_sha256,
        capability, binding_receipt_sha256, verdict, reviewer_principal_id,
        previous_receipt_sha256, input_sha256, output_sha256,
        artifact, artifact_sha256, payload, receipt_sha256, created_at
    ) values (
        target_workspace_id, target_client_id, target_receipt_id,
        target_round_id, target_plan_id, target_stage, stage_ordinal,
        stage_actor, (claims ->> 'producer_principal_id')::uuid,
        claims ->> 'release_sha', claims ->> 'config_sha256',
        expected_capability, binding ->> 'binding_receipt_sha256',
        case when target_stage = 'independent_qa' then 'passed' end,
        case when target_stage = 'independent_qa'
            then (claims ->> 'producer_principal_id')::uuid end,
        previous_row.receipt_sha256, previous_row.output_sha256, artifact_sha,
        artifact, artifact_sha, stage_payload, stage_sha, created_time
    );
    if target_stage = 'recap' then
        inbox_payload := pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'client_id', target_client_id,
            'created_at', pg_catalog.to_char(
                created_time at time zone 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS"Z"'
            ),
            'external_delivery_attempted', false,
            'inbox_id', target_inbox_id::text,
            'operator_decision_recorded', false,
            'plan_id', target_plan_id::text,
            'qa_output_sha256', qa_row.output_sha256,
            'qa_receipt_id', qa_row.receipt_id::text,
            'qa_receipt_sha256', qa_row.receipt_sha256,
            'round_id', target_round_id::text,
            'schema_version', 'harmony-operator-inbox@1',
            'scope_sha256', previous_row.output_sha256,
            'stage_receipt_id', previous_row.receipt_id::text,
            'status', 'pending',
            'workspace_id', target_workspace_id::text
        );
        insert into agent_runtime.harmony_operator_inbox (
            workspace_id, client_id, inbox_id, round_id, plan_id,
            stage_receipt_id, scope_sha256, qa_receipt_id,
            qa_receipt_sha256, qa_output_sha256, payload, status, created_at
        ) values (
            target_workspace_id, target_client_id, target_inbox_id,
            target_round_id, target_plan_id, previous_row.receipt_id,
            previous_row.output_sha256,
            qa_row.receipt_id, qa_row.receipt_sha256, qa_row.output_sha256,
            inbox_payload, 'pending', created_time
        );
    end if;
    return pg_catalog.jsonb_build_object(
        'ok', true, 'reused', false, 'stage_receipt', stage_payload,
        'database_calls', true, 'external_calls', false,
        'provider_calls', false, 'publication_calls', false,
        'automatic_publication', false
    );
exception
    when no_data_found then
        raise exception 'harmony_preview_stage_dependency_missing';
end;
$$;

revoke all on function private.harmony_preview_round_inputs_current(
    uuid, text, jsonb
) from public, anon, authenticated, service_role;
revoke all on function public.append_preview_harmony_squid_stage(
    uuid, text, uuid, uuid, text, uuid, uuid, jsonb
) from public, anon, authenticated, service_role;

commit;
