-- Transactional security smoke for the disposable-Preview Squid Codex QA gate.
-- Run as database owner after all Harmony migrations.  No rows persist.
-- The 64-connection exactly-once proof lives in the Python Preview probe; this
-- file deliberately checks the database security and fail-closed contracts.

begin;

do $test$
declare
    table_name text;
    role_name text;
begin
    foreach table_name in array array[
        'private.harmony_preview_codex_source_lineage_receipts',
        'private.harmony_preview_codex_gate_requests',
        'private.harmony_preview_codex_gate_runs',
        'private.harmony_preview_codex_gate_transitions',
        'private.harmony_preview_codex_gate_claim_receipts',
        'private.harmony_preview_codex_gate_attempt_receipts',
        'private.harmony_preview_codex_semantic_qa_evidence',
        'private.harmony_preview_codex_gate_result_receipts',
        'private.harmony_preview_codex_gate_verification_receipts',
        'private.harmony_preview_codex_gate_reconciliation_receipts',
        'private.harmony_preview_codex_gate_stage_links'
    ] loop
        if not exists (
            select 1
            from pg_catalog.pg_class relation
            join pg_catalog.pg_namespace namespace
              on namespace.oid = relation.relnamespace
            where namespace.nspname = pg_catalog.split_part(
                    table_name, '.', 1
                  )
              and relation.relname = pg_catalog.split_part(
                    table_name, '.', 2
                  )
              and relation.relkind = 'r'
              and relation.relrowsecurity
              and relation.relforcerowsecurity
        ) then
            raise exception 'Codex gate table is not FORCE RLS: %',
                table_name;
        end if;
        if exists (
            select 1
            from pg_catalog.pg_policies policy
            where policy.schemaname = pg_catalog.split_part(
                    table_name, '.', 1
                  )
              and policy.tablename = pg_catalog.split_part(
                    table_name, '.', 2
                  )
        ) then
            raise exception 'Codex gate table unexpectedly has an RLS policy: %',
                table_name;
        end if;
        foreach role_name in array array[
            'public', 'anon', 'authenticated', 'service_role',
            'coineasy_harmony_connector', 'coineasy_harmony_orchestrator',
            'coineasy_harmony_content', 'coineasy_harmony_qa',
            'coineasy_harmony_operator', 'coineasy_harmony_recap',
            'coineasy_harmony_dashboard'
        ] loop
            if pg_catalog.has_table_privilege(
                role_name,
                table_name,
                'select,insert,update,delete,truncate,references,trigger'
            ) then
                raise exception 'direct Codex gate table privilege leaked: % -> %',
                    role_name, table_name;
            end if;
        end loop;
    end loop;
end
$test$;

-- The durable gate must reuse the fail-closed JWT contract, keep logical work
-- identity independent of assignment/time/random lineage, and fence the only
-- execute-authorizing transition against an already-recorded denial.
do $test$
declare
    binding_definition text;
    preflight_definition text;
    recovery_actor_definition text;
    work_key_definition text;
    assignment_key_definition text;
    claim_definition text;
    definition text;
    row_source text;
    reconcile_definition text;
    signature text;
    start_definition text;
    tenant_lock_definition text;
begin
    binding_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'private.harmony_preview_codex_qa_binding(uuid,text,timestamptz)'
            ::pg_catalog.regprocedure
    ));
    if binding_definition not like '%claim_scope_valid := coalesce((%'
       or binding_definition not like '%claim_policy_valid := coalesce((%'
       or binding_definition not like '%claim_identity_valid := coalesce(%'
       or binding_definition not like '%claim_time_valid := coalesce((%'
       or binding_definition not like '%if not (%'
       or binding_definition not like '%harmony_preview_stage_claims_match%'
       or binding_definition not like '%coalesce(claims ->> ''capability'', '''')%'
       or binding_definition not like '%claims -> ''max_cost_microusd''%'
       or binding_definition not like '%coalesce(claims ->> ''jti'', '''')%'
       or binding_definition not like
            '%expires_epoch - issued_epoch between 1 and 2678400%'
    then
        raise exception 'Codex QA binding is not fail-closed';
    end if;

    preflight_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'private.harmony_preview_codex_qa_scope_preflight(uuid,text,timestamptz)'
            ::pg_catalog.regprocedure
    ));
    if preflight_definition not like '%claim_scope_valid := coalesce((%'
       or preflight_definition not like '%claim_policy_valid := coalesce((%'
       or preflight_definition not like '%claim_identity_valid := coalesce(%'
       or preflight_definition not like '%claim_time_valid := coalesce((%'
       or preflight_definition not like '%claims -> ''max_external_actions''%'
       or preflight_definition not like '%workspace_id'', '''')%'
       or preflight_definition ~
            '[[:space:]]from[[:space:]]+(agent_runtime|private|public)[.]'
    then
        raise exception 'Codex reconciliation preflight touches tenant rows or is incomplete';
    end if;

    tenant_lock_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'private.harmony_preview_codex_lock_tenant(uuid,text)'
            ::pg_catalog.regprocedure
    ));
    if tenant_lock_definition not like '%pg_advisory_xact_lock%'
       or tenant_lock_definition not like
            '%harmony_preview_codex_gate_tenant:%'
    then
        raise exception 'Codex tenant transition lock drifted';
    end if;

    foreach signature in array array[
        'public.prepare_preview_harmony_squid_codex_qa(uuid,text,uuid,uuid,bigint)',
        'public.claim_preview_harmony_squid_codex_qa(uuid,text,integer)',
        'public.start_preview_harmony_squid_codex_qa_attempt(uuid,text,text,text)',
        'public.submit_preview_harmony_squid_codex_qa_result(uuid,text,text,text,jsonb,text,text[],text)',
        'public.verify_preview_harmony_squid_codex_qa_result(uuid,text,text)',
        'public.reconcile_preview_harmony_squid_codex_qa_lease(uuid,text,integer)'
    ] loop
        definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
            signature::pg_catalog.regprocedure
        ));
        row_source := case
            when signature like
                'public.prepare_preview_harmony_squid_codex_qa(%'
            then 'from agent_runtime.harmony_rounds'
            else 'from private.harmony_preview_codex_gate_runs'
        end;
        if pg_catalog.strpos(
                definition, 'harmony_preview_codex_qa_scope_preflight'
           ) = 0
           or pg_catalog.strpos(
                definition, 'harmony_preview_codex_lock_tenant'
           ) = 0
           or pg_catalog.strpos(
                definition, 'harmony_preview_codex_qa_scope_preflight'
           ) >= pg_catalog.strpos(
                definition, 'harmony_preview_codex_lock_tenant'
           )
           or pg_catalog.strpos(definition, row_source) = 0
           or pg_catalog.strpos(
                definition, 'harmony_preview_codex_lock_tenant'
           ) >= pg_catalog.strpos(definition, row_source)
        then
            raise exception
                'Codex RPC tenant lock order drifted: %', signature;
        end if;
    end loop;

    recovery_actor_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'private.harmony_preview_codex_reconciliation_actor(uuid,text,uuid,timestamptz)'
            ::pg_catalog.regprocedure
    ));
    if recovery_actor_definition not like '%claim_scope_valid := coalesce((%'
       or recovery_actor_definition not like '%claim_policy_valid := coalesce((%'
       or recovery_actor_definition not like '%claim_identity_valid := coalesce(%'
       or recovery_actor_definition not like '%claim_time_valid := coalesce((%'
       or recovery_actor_definition !~
            'candidate[.]binding_sha256[[:space:]]*=[[:space:]]*request_row[.]reviewer_specialist_binding_sha256'
       or recovery_actor_definition !~
            'candidate[.]principal_id[[:space:]]*=[[:space:]]*request_row[.]reviewer_principal_id'
       or recovery_actor_definition !~
            'candidate[.]producer_release_sha[[:space:]]*=[[:space:]]*request_row[.]reviewer_release_sha'
       or recovery_actor_definition !~
            'candidate[.]config_sha256[[:space:]]*=[[:space:]]*request_row[.]reviewer_config_sha256'
       or recovery_actor_definition not like '%candidate.actor = ''codex''%'
       or recovery_actor_definition not like
            '%date_trunc(''second'', specialist.created_at)%'
       or recovery_actor_definition like '%harmony_preview_stage_claims_match%'
       or recovery_actor_definition like '%harmony_preview_environment_fence%'
       or recovery_actor_definition like '%specialist.expires_at%'
    then
        raise exception 'stale Codex reconciliation actor is not assignment-bound';
    end if;

    if pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'private.harmony_preview_codex_build_source_lineage(uuid,text,uuid,uuid,jsonb,timestamptz,uuid)'
            ::pg_catalog.regprocedure
    )) not like '%date_trunc(''second'', fence.created_at)%'
       or pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'private.harmony_preview_codex_build_source_lineage(uuid,text,uuid,uuid,jsonb,timestamptz,uuid)'
            ::pg_catalog.regprocedure
    )) not like '%date_trunc(''second'', private_binding.created_at)%'
    then
        raise exception 'Codex lineage time precision can reject same-second stages';
    end if;

    work_key_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'private.harmony_preview_codex_work_key(jsonb)'
            ::pg_catalog.regprocedure
    ));
    if work_key_definition not like '%''plan_receipt_sha256''%'
       or work_key_definition not like '%''round_id''%'
       or work_key_definition not like '%''signal_input_set_sha256''%'
       or work_key_definition not like '%''signal_manifest_sha256''%'
       or work_key_definition not like '%''squid-codex-gate-work@1''%'
       or work_key_definition like '%''lineage_sha256''%'
       or work_key_definition like '%''lineage_receipt_id''%'
       or work_key_definition like '%''observed_at''%'
       or work_key_definition like '%''reviewer_principal_id''%'
    then
        raise exception 'Codex logical work-key contract drifted';
    end if;

    assignment_key_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'private.harmony_preview_codex_assignment_key(text,jsonb)'
            ::pg_catalog.regprocedure
    ));
    if assignment_key_definition not like '%''reviewer_binding_sha256''%'
       or assignment_key_definition not like
            '%''schema_version'', ''squid-codex-gate-assignment@1''%'
       or assignment_key_definition like '%''reviewer_principal_id''%'
       or assignment_key_definition like '%''reviewer_release_sha''%'
       or assignment_key_definition like '%''reviewer_config_sha256''%'
    then
        raise exception 'Codex assignment-key contract drifted';
    end if;

    claim_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'public.claim_preview_harmony_squid_codex_qa(uuid,text,integer)'
            ::pg_catalog.regprocedure
    ));
    if pg_catalog.strpos(
        claim_definition, 'harmony_preview_codex_request_current'
    ) = 0 or pg_catalog.strpos(
        claim_definition, 'harmony_preview_codex_request_current'
    ) > pg_catalog.strpos(
        claim_definition, 'for update of candidate skip locked'
    )
    then
        raise exception 'Codex claim can be head-of-line blocked by stale pending work';
    end if;

    reconcile_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'public.reconcile_preview_harmony_squid_codex_qa_lease(uuid,text,integer)'
            ::pg_catalog.regprocedure
    ));
    if reconcile_definition not like '%candidate.status = ''pending''%'
       or reconcile_definition not like '%request_not_current%'
       or reconcile_definition not like '%candidate.status = ''result_submitted''%'
       or reconcile_definition not like '%result_not_current%'
       or reconcile_definition not like
            '%harmony_preview_codex_reconciliation_actor%'
       or reconcile_definition not like
            '%harmony_preview_codex_qa_scope_preflight%'
       or pg_catalog.strpos(
            reconcile_definition,
            'harmony_preview_codex_qa_scope_preflight'
       ) > pg_catalog.strpos(
            reconcile_definition, 'select candidate.* into current_run'
       )
       or reconcile_definition not like '%actor_claims := nullif(%'
       or reconcile_definition not like '%actor_principal_id :=%'
       or reconcile_definition not like '%actor_release_sha :=%'
       or reconcile_definition not like '%actor_config_sha256 :=%'
       or reconcile_definition not like '%actor_branch_ref :=%'
       or reconcile_definition not like '%actor_issued_epoch :=%'
       or reconcile_definition not like
            '%join private.harmony_preview_squid_specialist_bindings actor_specialist%'
       or reconcile_definition !~
            'actor_specialist[.]binding_sha256[[:space:]]*=[[:space:]]*queued_request[.]reviewer_specialist_binding_sha256'
       or reconcile_definition !~
            'actor_specialist[.]principal_id[[:space:]]*=[[:space:]]*queued_request[.]reviewer_principal_id'
       or reconcile_definition !~
            'actor_specialist[.]principal_id[[:space:]]*=[[:space:]]*actor_principal_id'
       or reconcile_definition !~
            'actor_specialist[.]producer_release_sha[[:space:]]*=[[:space:]]*actor_release_sha'
       or reconcile_definition !~
            'actor_specialist[.]config_sha256[[:space:]]*=[[:space:]]*actor_config_sha256'
       or reconcile_definition !~
            'actor_specialist[.]branch_ref[[:space:]]*=[[:space:]]*actor_branch_ref'
       or reconcile_definition not like
            '%date_trunc(''second'', actor_specialist.created_at)%'
       or pg_catalog.strpos(
            reconcile_definition,
            'join private.harmony_preview_squid_specialist_bindings actor_specialist'
       ) > pg_catalog.strpos(
            reconcile_definition, 'for update of candidate skip locked'
       )
       or pg_catalog.strpos(
            reconcile_definition,
            'harmony_preview_codex_reconciliation_actor('
       ) < pg_catalog.strpos(
            reconcile_definition, 'for update of candidate skip locked'
       )
       or reconcile_definition like '%harmony_preview_codex_qa_binding(%'
       or pg_catalog.strpos(
            reconcile_definition, 'harmony_preview_codex_lock_plan_dependencies'
       ) = 0
       or pg_catalog.strpos(
            reconcile_definition, 'harmony_preview_qa_outcome:'
       ) <= pg_catalog.strpos(
            reconcile_definition, 'harmony_preview_codex_lock_plan_dependencies'
       )
       or reconcile_definition like
            '%insert into agent_runtime.harmony_stage_receipts%'
       or reconcile_definition like
            '%insert into private.harmony_preview_codex_gate_verification_receipts%'
       or not exists (
            select 1
            from pg_catalog.pg_attribute attribute
            where attribute.attrelid =
                'private.harmony_preview_codex_gate_reconciliation_receipts'
                    ::pg_catalog.regclass
              and attribute.attname = 'claim_receipt_id'
              and not attribute.attnotnull
              and not attribute.attisdropped
       )
       or not exists (
            select 1
            from pg_catalog.pg_attribute attribute
            where attribute.attrelid =
                'private.harmony_preview_codex_gate_reconciliation_receipts'
                    ::pg_catalog.regclass
              and attribute.attname = 'result_receipt_id'
              and not attribute.attnotnull
              and not attribute.attisdropped
       )
       or not exists (
            select 1
            from pg_catalog.pg_constraint constraint_value
            where constraint_value.conrelid =
                'private.harmony_preview_codex_gate_reconciliation_receipts'
                    ::pg_catalog.regclass
              and constraint_value.confrelid =
                'private.harmony_preview_codex_gate_result_receipts'
                    ::pg_catalog.regclass
              and constraint_value.contype = 'f'
              and pg_catalog.lower(pg_catalog.pg_get_constraintdef(
                    constraint_value.oid
                  )) like
                    '%(workspace_id, client_id, request_id, result_receipt_id)%'
       )
       or not exists (
            select 1
            from pg_catalog.pg_constraint constraint_value
            where constraint_value.conrelid =
                'private.harmony_preview_codex_gate_reconciliation_receipts'
                    ::pg_catalog.regclass
              and constraint_value.contype = 'c'
              and pg_catalog.lower(pg_catalog.pg_get_constraintdef(
                    constraint_value.oid
                  )) like '%result_not_current%'
              and pg_catalog.lower(pg_catalog.pg_get_constraintdef(
                    constraint_value.oid
                  )) like '%result_receipt_id is not null%'
       )
    then
        raise exception 'stale Codex work cannot be reconciled durably';
    end if;

    start_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'public.start_preview_harmony_squid_codex_qa_attempt(uuid,text,text,text)'
            ::pg_catalog.regprocedure
    ));
    if pg_catalog.strpos(start_definition, 'harmony_preview_qa_outcome:') = 0
       or pg_catalog.strpos(
            start_definition, 'harmony_preview_qa_denial_receipts'
       ) = 0
       or pg_catalog.strpos(
            start_definition, 'harmony_preview_qa_denial_receipts'
       ) > pg_catalog.strpos(start_definition, 'execute_authorized := true')
    then
        raise exception 'Codex attempt start is not denial-fenced';
    end if;
end
$test$;

-- Every ledger relation except the mutable run projection is append-only.
do $test$
declare
    table_name text;
begin
    foreach table_name in array array[
        'harmony_preview_codex_source_lineage_receipts',
        'harmony_preview_codex_gate_requests',
        'harmony_preview_codex_gate_transitions',
        'harmony_preview_codex_gate_claim_receipts',
        'harmony_preview_codex_gate_attempt_receipts',
        'harmony_preview_codex_semantic_qa_evidence',
        'harmony_preview_codex_gate_result_receipts',
        'harmony_preview_codex_gate_verification_receipts',
        'harmony_preview_codex_gate_reconciliation_receipts',
        'harmony_preview_codex_gate_stage_links'
    ] loop
        if not exists (
            select 1
            from pg_catalog.pg_trigger trigger_value
            join pg_catalog.pg_class relation
              on relation.oid = trigger_value.tgrelid
            join pg_catalog.pg_namespace namespace
              on namespace.oid = relation.relnamespace
            join pg_catalog.pg_proc routine
              on routine.oid = trigger_value.tgfoid
            join pg_catalog.pg_namespace routine_namespace
              on routine_namespace.oid = routine.pronamespace
            where namespace.nspname = 'private'
              and relation.relname = table_name
              and routine_namespace.nspname = 'private'
              and routine.proname = 'agent_immutable_row'
              and not trigger_value.tgisinternal
              and trigger_value.tgenabled <> 'D'
              and (trigger_value.tgtype::integer & 1) = 1
              and (trigger_value.tgtype::integer & 2) = 2
              and (trigger_value.tgtype::integer & 8) = 8
              and (trigger_value.tgtype::integer & 16) = 16
        ) then
            raise exception 'Codex gate ledger is not append-only: private.%',
                table_name;
        end if;
    end loop;
end
$test$;

do $test$
declare
    signature text;
    function_oid pg_catalog.oid;
    definition text;
    role_name text;
begin
    foreach signature in array array[
        'public.prepare_preview_harmony_squid_codex_qa(uuid,text,uuid,uuid,bigint)',
        'public.claim_preview_harmony_squid_codex_qa(uuid,text,integer)',
        'public.start_preview_harmony_squid_codex_qa_attempt(uuid,text,text,text)',
        'public.submit_preview_harmony_squid_codex_qa_result(uuid,text,text,text,jsonb,text,text[],text)',
        'public.verify_preview_harmony_squid_codex_qa_result(uuid,text,text)',
        'public.reconcile_preview_harmony_squid_codex_qa_lease(uuid,text,integer)'
    ] loop
        function_oid := pg_catalog.to_regprocedure(signature);
        if function_oid is null then
            raise exception 'Codex gate RPC missing: %', signature;
        end if;
        if not (
            select routine.prosecdef
            from pg_catalog.pg_proc routine
            where routine.oid = function_oid
        ) then
            raise exception 'Codex gate RPC is not SECURITY DEFINER: %',
                signature;
        end if;
        if not coalesce((
            select routine.proconfig @> array['search_path=""']::text[]
            from pg_catalog.pg_proc routine
            where routine.oid = function_oid
        ), false) then
            raise exception 'Codex gate RPC lacks empty search_path: %',
                signature;
        end if;
        if not pg_catalog.has_function_privilege(
            'coineasy_harmony_qa', signature, 'execute'
        ) then
            raise exception 'QA role cannot execute Codex gate RPC: %',
                signature;
        end if;
        foreach role_name in array array[
            'public', 'anon', 'authenticated', 'service_role',
            'coineasy_harmony_connector', 'coineasy_harmony_orchestrator',
            'coineasy_harmony_content', 'coineasy_harmony_operator',
            'coineasy_harmony_recap', 'coineasy_harmony_dashboard'
        ] loop
            if pg_catalog.has_function_privilege(
                role_name, signature, 'execute'
            ) then
                raise exception 'Codex gate RPC execute leaked: % -> %',
                    role_name, signature;
            end if;
        end loop;
        definition := pg_catalog.lower(
            pg_catalog.pg_get_functiondef(function_oid)
        );
        if definition ~ '(insert|update|delete)[[:space:]]+(into[[:space:]]+)?public[.](approvals|publications)'
           or definition ~ '(insert|update|delete)[[:space:]]+(into[[:space:]]+)?agent_runtime[.]buzz_'
           or definition ~ '(insert|update|delete)[[:space:]]+(into[[:space:]]+)?private[.]grok_qa_'
           or definition ~ '(net[.]http_|extensions[.]http_|http_post[(]|dblink[(]|pg_net)'
        then
            raise exception 'Codex gate RPC contains a forbidden side effect: %',
                signature;
        end if;
    end loop;
end
$test$;

-- Private helpers and trigger functions are not callable through the API
-- roles, and none contains a hidden provider/Buzz/approval/publication path.
do $test$
declare
    routine_row record;
    role_name text;
    definition text;
begin
    for routine_row in
        select routine.oid,
               routine.oid::pg_catalog.regprocedure::text as signature
        from pg_catalog.pg_proc routine
        join pg_catalog.pg_namespace namespace
          on namespace.oid = routine.pronamespace
        where namespace.nspname = 'private'
          and routine.proname like 'harmony_preview_codex_%'
    loop
        foreach role_name in array array[
            'public', 'anon', 'authenticated', 'service_role'
        ] loop
            if pg_catalog.has_function_privilege(
                role_name, routine_row.oid, 'execute'
            ) then
                raise exception 'private Codex helper execute leaked: % -> %',
                    role_name, routine_row.signature;
            end if;
        end loop;
        definition := pg_catalog.lower(
            pg_catalog.pg_get_functiondef(routine_row.oid)
        );
        if definition ~ '(insert|update|delete)[[:space:]]+(into[[:space:]]+)?public[.](approvals|publications)'
           or definition ~ '(insert|update|delete)[[:space:]]+(into[[:space:]]+)?agent_runtime[.]buzz_'
           or definition ~ '(insert|update|delete)[[:space:]]+(into[[:space:]]+)?private[.]grok_qa_'
           or definition ~ '(net[.]http_|extensions[.]http_|http_post[(]|dblink[(]|pg_net)'
        then
            raise exception 'private Codex helper contains a forbidden side effect: %',
                routine_row.signature;
        end if;
    end loop;
end
$test$;

-- A positive independent_qa stage cannot be inserted through the legacy
-- append path (or by the database owner) without a verified durable result.
do $test$
declare
    guard_oid pg_catalog.oid;
    guard_definition text;
begin
    select trigger_value.tgfoid into strict guard_oid
    from pg_catalog.pg_trigger trigger_value
    join pg_catalog.pg_class relation
      on relation.oid = trigger_value.tgrelid
    join pg_catalog.pg_namespace namespace
      on namespace.oid = relation.relnamespace
    where namespace.nspname = 'agent_runtime'
      and relation.relname = 'harmony_stage_receipts'
      and trigger_value.tgname
            = 'harmony_stage_receipts_codex_verified_guard'
      and not trigger_value.tgisinternal
      and trigger_value.tgenabled <> 'D'
      and (trigger_value.tgtype::integer & 1) = 1
      and (trigger_value.tgtype::integer & 2) = 2
      and (trigger_value.tgtype::integer & 4) = 4;
    if not (
        select routine.prosecdef
        from pg_catalog.pg_proc routine
        where routine.oid = guard_oid
    ) or not coalesce((
        select routine.proconfig @> array['search_path=""']::text[]
        from pg_catalog.pg_proc routine
        where routine.oid = guard_oid
    ), false) then
        raise exception 'verified QA stage guard is not a hardened trigger';
    end if;
    guard_definition := pg_catalog.lower(
        pg_catalog.pg_get_functiondef(guard_oid)
    );
    if guard_definition not like '%new.stage <> ''independent_qa''%'
       or guard_definition not like '%status = ''verified''%'
       or guard_definition not like '%verdict = ''pass''%'
       or guard_definition not like '%verification_outcome = ''passed''%'
       or guard_definition not like '%harmony_preview_codex_verified_result_required%'
    then
        raise exception 'verified QA stage guard contract drifted';
    end if;
    if not exists (
        select 1
        from pg_catalog.pg_trigger trigger_value
        join pg_catalog.pg_class relation
          on relation.oid = trigger_value.tgrelid
        join pg_catalog.pg_namespace namespace
          on namespace.oid = relation.relnamespace
        join pg_catalog.pg_proc routine
          on routine.oid = trigger_value.tgfoid
        where namespace.nspname = 'agent_runtime'
          and relation.relname = 'harmony_stage_receipts'
          and trigger_value.tgname
                = 'harmony_stage_receipts_codex_verified_link'
          and routine.proname = 'harmony_preview_codex_link_qa_stage_insert'
          and not trigger_value.tgisinternal
          and trigger_value.tgenabled <> 'D'
          and (trigger_value.tgtype::integer & 1) = 1
          and (trigger_value.tgtype::integer & 4) = 4
          and (trigger_value.tgtype::integer & 2) = 0
    ) then
        raise exception 'verified QA stage link trigger is missing';
    end if;
end
$test$;

do $test$
declare
    before_count bigint;
    after_count bigint;
begin
    select pg_catalog.count(*) into before_count
    from agent_runtime.harmony_stage_receipts receipt
    where receipt.stage = 'independent_qa';
    begin
        insert into agent_runtime.harmony_stage_receipts (
            workspace_id, client_id, receipt_id, round_id, plan_id,
            stage, ordinal, actor, principal_id, producer_release_sha,
            config_sha256, capability, binding_receipt_sha256, verdict,
            reviewer_principal_id, previous_receipt_sha256, input_sha256,
            output_sha256, artifact, artifact_sha256, payload,
            receipt_sha256, created_at, specialist_binding_sha256
        ) values (
            'c0000000-0000-4000-8000-000000000001', 'squid',
            'c0000000-0000-4000-8000-000000000002',
            'c0000000-0000-4000-8000-000000000003',
            'c0000000-0000-4000-8000-000000000004',
            'independent_qa', 3, 'codex',
            'c0000000-0000-4000-8000-000000000005',
            pg_catalog.repeat('1', 40), pg_catalog.repeat('2', 64),
            'harmony_independent_qa', pg_catalog.repeat('3', 64),
            'passed', 'c0000000-0000-4000-8000-000000000005',
            pg_catalog.repeat('4', 64), pg_catalog.repeat('5', 64),
            pg_catalog.repeat('6', 64),
            pg_catalog.jsonb_build_object(
                'schema_version', 'squid-codex-verified-qa-stage@1',
                'automatic_publication', false
            ),
            pg_catalog.repeat('7', 64),
            pg_catalog.jsonb_build_object(
                'synthetic', true, 'aggregate_only', true,
                'external_calls', false, 'provider_calls', false,
                'publication_calls', false,
                'automatic_publication', false
            ),
            pg_catalog.repeat('8', 64), pg_catalog.clock_timestamp(),
            pg_catalog.repeat('9', 64)
        );
        raise exception 'unverified independent_qa stage unexpectedly inserted';
    exception
        when others then
            if sqlerrm not in (
                'harmony_preview_codex_verified_result_required',
                'harmony_preview_fixed_specialist_not_bound'
            ) then
                raise exception 'unverified QA-stage rejection drifted: %',
                    sqlerrm;
            end if;
    end;
    select pg_catalog.count(*) into after_count
    from agent_runtime.harmony_stage_receipts receipt
    where receipt.stage = 'independent_qa';
    if after_count <> before_count then
        raise exception 'unverified QA-stage rejection wrote a stage row';
    end if;
end
$test$;

-- Input/state guards are deterministic, and empty-queue claim/reconcile are
-- no-op receipts rather than synthetic state transitions.
do $test$
declare
    result jsonb;
    before_counts jsonb;
    after_counts jsonb;
begin
    select pg_catalog.jsonb_build_object(
        'lineages', (select pg_catalog.count(*)
                     from private.harmony_preview_codex_source_lineage_receipts),
        'requests', (select pg_catalog.count(*)
                     from private.harmony_preview_codex_gate_requests),
        'runs', (select pg_catalog.count(*)
                 from private.harmony_preview_codex_gate_runs),
        'transitions', (select pg_catalog.count(*)
                        from private.harmony_preview_codex_gate_transitions),
        'claims', (select pg_catalog.count(*)
                   from private.harmony_preview_codex_gate_claim_receipts),
        'attempts', (select pg_catalog.count(*)
                     from private.harmony_preview_codex_gate_attempt_receipts),
        'evidence', (select pg_catalog.count(*)
                     from private.harmony_preview_codex_semantic_qa_evidence),
        'results', (select pg_catalog.count(*)
                    from private.harmony_preview_codex_gate_result_receipts),
        'verifications', (select pg_catalog.count(*)
                          from private.harmony_preview_codex_gate_verification_receipts),
        'reconciliations', (select pg_catalog.count(*)
                            from private.harmony_preview_codex_gate_reconciliation_receipts),
        'stage_links', (select pg_catalog.count(*)
                        from private.harmony_preview_codex_gate_stage_links)
    ) into before_counts;
    if before_counts <> pg_catalog.jsonb_build_object(
        'lineages', 0, 'requests', 0, 'runs', 0, 'transitions', 0,
        'claims', 0, 'attempts', 0, 'evidence', 0, 'results', 0,
        'verifications', 0, 'reconciliations', 0, 'stage_links', 0
    ) then
        raise exception 'durable Codex gate security fixture is not empty: %',
            before_counts;
    end if;

    begin
        perform public.prepare_preview_harmony_squid_codex_qa(
            'c1000000-0000-4000-8000-000000000001', 'yellow',
            'c1000000-0000-4000-8000-000000000002',
            'c1000000-0000-4000-8000-000000000003', 0
        );
        raise exception 'cross-client prepare unexpectedly succeeded';
    exception
        when others then
            if sqlerrm <> 'harmony_preview_codex_gate_scope_invalid' then
                raise exception 'cross-client prepare rejection drifted: %',
                    sqlerrm;
            end if;
    end;
    begin
        perform public.claim_preview_harmony_squid_codex_qa(
            'c1000000-0000-4000-8000-000000000001', 'squid', 0
        );
        raise exception 'invalid lease claim unexpectedly succeeded';
    exception
        when others then
            if sqlerrm <> 'harmony_preview_codex_gate_lease_invalid' then
                raise exception 'invalid lease rejection drifted: %', sqlerrm;
            end if;
    end;
    begin
        perform public.start_preview_harmony_squid_codex_qa_attempt(
            'c1000000-0000-4000-8000-000000000001', 'squid',
            'not-a-work-key', pg_catalog.repeat('a', 64)
        );
        raise exception 'malformed attempt start unexpectedly succeeded';
    exception
        when others then
            if sqlerrm <> 'harmony_preview_codex_gate_scope_invalid' then
                raise exception 'malformed attempt rejection drifted: %',
                    sqlerrm;
            end if;
    end;
    begin
        perform public.submit_preview_harmony_squid_codex_qa_result(
            'c1000000-0000-4000-8000-000000000001', 'yellow',
            pg_catalog.repeat('b', 64), pg_catalog.repeat('c', 64),
            '{}'::jsonb, pg_catalog.repeat('d', 64), array[]::text[], 'pass'
        );
        raise exception 'cross-client result unexpectedly succeeded';
    exception
        when others then
            if sqlerrm <> 'harmony_preview_codex_gate_scope_invalid' then
                raise exception 'cross-client result rejection drifted: %',
                    sqlerrm;
            end if;
    end;
    begin
        perform public.verify_preview_harmony_squid_codex_qa_result(
            'c1000000-0000-4000-8000-000000000001', 'squid',
            'not-a-work-key'
        );
        raise exception 'malformed verification unexpectedly succeeded';
    exception
        when others then
            if sqlerrm <> 'harmony_preview_codex_gate_scope_invalid' then
                raise exception 'malformed verification rejection drifted: %',
                    sqlerrm;
            end if;
    end;
    begin
        perform public.reconcile_preview_harmony_squid_codex_qa_lease(
            'c1000000-0000-4000-8000-000000000001', 'squid', 0
        );
        raise exception 'invalid reconciliation limit unexpectedly succeeded';
    exception
        when others then
            if sqlerrm <> 'harmony_preview_codex_gate_scope_invalid' then
                raise exception 'invalid reconciliation rejection drifted: %',
                    sqlerrm;
            end if;
    end;

    perform pg_catalog.set_config(
        'request.jwt.claims',
        pg_catalog.jsonb_build_object(
            'aud', 'authenticated',
            'automatic_publication', false,
            'capability', 'harmony_independent_qa',
            'client_id', 'squid',
            'config_sha256', pg_catalog.repeat('b', 64),
            'environment', 'preview',
            'exp', pg_catalog.date_part(
                'epoch', pg_catalog.clock_timestamp() + interval '10 minutes'
            )::bigint,
            'iat', pg_catalog.date_part(
                'epoch', pg_catalog.clock_timestamp() - interval '1 minute'
            )::bigint,
            'iss', 'supabase',
            'jti', 'c1000000-0000-4000-8000-000000000006',
            'max_cost_microusd', 0,
            'max_external_actions', 0,
            'producer_principal_id',
                'c1000000-0000-4000-8000-000000000005',
            'ref', 'vllwcbhqdojpjrssidcu',
            'release_sha', pg_catalog.repeat('a', 40),
            'role', 'coineasy_harmony_qa',
            'sub', 'c1000000-0000-4000-8000-000000000005',
            'workspace_id', 'c1000000-0000-4000-8000-000000000001'
        )::text,
        true
    );
    begin
        perform public.reconcile_preview_harmony_squid_codex_qa_lease(
            'c1000000-0000-4000-8000-000000000099', 'squid', 64
        );
        raise exception 'cross-workspace reconciliation unexpectedly succeeded';
    exception
        when others then
            if sqlerrm <>
                'harmony_preview_codex_qa_scope_invalid'
            then
                raise exception 'cross-workspace preflight rejection drifted: %',
                    sqlerrm;
            end if;
    end;

    result := public.claim_preview_harmony_squid_codex_qa(
        'c1000000-0000-4000-8000-000000000001', 'squid', 900
    );
    if result -> 'claimed' is distinct from 'false'::jsonb
       or result -> 'work_key' is distinct from 'null'::jsonb
    then
        raise exception 'empty claim queue did not return a typed no-op: %',
            result;
    end if;
    result := public.reconcile_preview_harmony_squid_codex_qa_lease(
        'c1000000-0000-4000-8000-000000000001', 'squid', 64
    );
    if result -> 'reconciled' is distinct from 'false'::jsonb
       or result -> 'outcome_unknown' is distinct from 'false'::jsonb
       or result -> 'pending' is distinct from 'false'::jsonb
       or result -> 'blocked' is distinct from 'false'::jsonb
    then
        raise exception 'empty reconciliation queue did not return a typed no-op: %',
            result;
    end if;

    select pg_catalog.jsonb_build_object(
        'lineages', (select pg_catalog.count(*)
                     from private.harmony_preview_codex_source_lineage_receipts),
        'requests', (select pg_catalog.count(*)
                     from private.harmony_preview_codex_gate_requests),
        'runs', (select pg_catalog.count(*)
                 from private.harmony_preview_codex_gate_runs),
        'transitions', (select pg_catalog.count(*)
                        from private.harmony_preview_codex_gate_transitions),
        'claims', (select pg_catalog.count(*)
                   from private.harmony_preview_codex_gate_claim_receipts),
        'attempts', (select pg_catalog.count(*)
                     from private.harmony_preview_codex_gate_attempt_receipts),
        'evidence', (select pg_catalog.count(*)
                     from private.harmony_preview_codex_semantic_qa_evidence),
        'results', (select pg_catalog.count(*)
                    from private.harmony_preview_codex_gate_result_receipts),
        'verifications', (select pg_catalog.count(*)
                          from private.harmony_preview_codex_gate_verification_receipts),
        'reconciliations', (select pg_catalog.count(*)
                            from private.harmony_preview_codex_gate_reconciliation_receipts),
        'stage_links', (select pg_catalog.count(*)
                        from private.harmony_preview_codex_gate_stage_links)
    ) into after_counts;
    if after_counts <> before_counts then
        raise exception 'Codex gate negative/no-op checks wrote ledger state: % <> %',
            after_counts, before_counts;
    end if;
end
$test$;

rollback;
