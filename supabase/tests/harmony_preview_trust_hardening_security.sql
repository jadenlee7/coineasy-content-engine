-- Transactional security smoke for Preview-only Harmony trust hardening.
-- Run as database owner after all migrations.  No rows persist.

begin;

do $test$
declare
    table_name text;
    role_name text;
begin
    foreach table_name in array array[
        'private.harmony_preview_connector_registrations',
        'private.harmony_preview_connector_registration_revocations',
        'private.harmony_preview_connector_request_receipts',
        'private.harmony_preview_qa_denial_receipts'
    ] loop
        if not exists (
            select 1
            from pg_catalog.pg_class relation
            join pg_catalog.pg_namespace namespace
              on namespace.oid = relation.relnamespace
            where namespace.nspname = pg_catalog.split_part(table_name, '.', 1)
              and relation.relname = pg_catalog.split_part(table_name, '.', 2)
              and relation.relrowsecurity
              and relation.relforcerowsecurity
        ) then
            raise exception 'Harmony trust table is not FORCE RLS: %',
                table_name;
        end if;
        if exists (
            select 1
            from pg_catalog.pg_policies policy
            where policy.schemaname = pg_catalog.split_part(table_name, '.', 1)
              and policy.tablename = pg_catalog.split_part(table_name, '.', 2)
              and policy.cmd <> 'SELECT'
        ) then
            raise exception 'Harmony trust table has a write policy: %',
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
                role_name, table_name,
                'select,insert,update,delete,truncate,references,trigger'
            ) then
                raise exception 'direct Harmony trust table privilege leaked: % -> %',
                    role_name, table_name;
            end if;
        end loop;
    end loop;
end
$test$;

do $test$
declare
    signature text;
    function_oid pg_catalog.oid;
    definition text;
begin
    foreach signature in array array[
        'public.submit_preview_harmony_signal(uuid,text,uuid,jsonb)',
        'public.record_preview_harmony_squid_qa_denial(uuid,text,uuid,uuid,uuid,jsonb)',
        'private.harmony_preview_validate_request_chronology()'
    ] loop
        function_oid := pg_catalog.to_regprocedure(signature);
        if function_oid is null then
            raise exception 'Harmony trust RPC missing: %', signature;
        end if;
        if not (select routine.prosecdef
                from pg_catalog.pg_proc routine
                where routine.oid = function_oid) then
            raise exception 'Harmony trust RPC is not SECURITY DEFINER: %',
                signature;
        end if;
        if not coalesce((
            select routine.proconfig @> array['search_path=""']::text[]
            from pg_catalog.pg_proc routine
            where routine.oid = function_oid
        ), false) then
            raise exception 'Harmony trust RPC lacks empty search_path: %',
                signature;
        end if;
        if pg_catalog.has_function_privilege('public', signature, 'execute')
           or pg_catalog.has_function_privilege('anon', signature, 'execute')
           or pg_catalog.has_function_privilege(
                'authenticated', signature, 'execute'
           )
           or pg_catalog.has_function_privilege(
                'service_role', signature, 'execute'
           )
        then
            raise exception 'broad Harmony trust RPC execute leaked: %',
                signature;
        end if;
        definition := pg_catalog.lower(
            pg_catalog.pg_get_functiondef(function_oid)
        );
        if definition ~ '(insert|update|delete)[[:space:]]+(into[[:space:]]+)?public\.(approvals|publications)'
           or definition ~ '(insert|update|delete)[[:space:]]+(into[[:space:]]+)?agent_runtime\.buzz_'
           or definition ~ '(update|delete)[[:space:]]+private\.grok_qa_dispatch_outbox'
        then
            raise exception 'Harmony trust RPC contains forbidden side-effect SQL: %',
                signature;
        end if;
    end loop;
    if not pg_catalog.has_function_privilege(
        'coineasy_harmony_connector',
        'public.submit_preview_harmony_signal(uuid,text,uuid,jsonb)',
        'execute'
    ) or pg_catalog.has_function_privilege(
        'coineasy_harmony_qa',
        'public.submit_preview_harmony_signal(uuid,text,uuid,jsonb)',
        'execute'
    ) or not pg_catalog.has_function_privilege(
        'coineasy_harmony_qa',
        'public.record_preview_harmony_squid_qa_denial(uuid,text,uuid,uuid,uuid,jsonb)',
        'execute'
    ) or pg_catalog.has_function_privilege(
        'coineasy_harmony_connector',
        'public.record_preview_harmony_squid_qa_denial(uuid,text,uuid,uuid,uuid,jsonb)',
        'execute'
    ) then
        raise exception 'Harmony trust RPC role matrix is invalid';
    end if;
    if pg_catalog.has_function_privilege(
        'coineasy_harmony_connector',
        'private.harmony_preview_submit_signal_legacy(uuid,text,uuid,jsonb)',
        'execute'
    ) or pg_catalog.has_function_privilege(
        'coineasy_harmony_qa',
        'private.harmony_preview_submit_signal_legacy(uuid,text,uuid,jsonb)',
        'execute'
    ) then
        raise exception 'private legacy connector entry point leaked';
    end if;
    definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'public.submit_preview_harmony_signal(uuid,text,uuid,jsonb)'
            ::pg_catalog.regprocedure
    ));
    if pg_catalog.strpos(
        definition,
        'coalesce(target_signal ->> ''observed_at'', '''')'
    ) = 0 or pg_catalog.strpos(
        definition,
        'coalesce(target_signal ->> ''expires_at'', '''')'
    ) = 0 or pg_catalog.strpos(
        definition,
        't[0-9]{2}:[0-9]{2}:[0-9]{2}z$'
    ) = 0 then
        raise exception 'connector wrapper lacks whole-second UTC admission';
    end if;
end
$test$;

do $test$
begin
    if pg_catalog.to_regprocedure(
        'private.harmony_preview_connector_request_sha256(uuid,text,uuid,uuid,jsonb)'
    ) is null
       or pg_catalog.to_regprocedure(
        'private.harmony_preview_lock_connector_registration(uuid,text,uuid)'
       ) is null
       or pg_catalog.to_regprocedure(
        'private.harmony_preview_lock_manifest_registrations(uuid,text,jsonb)'
       ) is null
       or pg_catalog.to_regprocedure(
        'private.harmony_preview_qa_actor_independent(uuid,text,uuid,uuid)'
       ) is null
    then
        raise exception 'Harmony trust helper contract is incomplete';
    end if;
    if not exists (
        select 1
        from pg_catalog.pg_trigger trigger_value
        join pg_catalog.pg_class relation
          on relation.oid = trigger_value.tgrelid
        join pg_catalog.pg_namespace namespace
          on namespace.oid = relation.relnamespace
        where namespace.nspname = 'private'
          and relation.relname
                = 'harmony_preview_connector_registration_revocations'
          and trigger_value.tgname
                = 'harmony_preview_connector_revocation_linearize'
          and not trigger_value.tgisinternal
    ) or not exists (
        select 1
        from pg_catalog.pg_trigger trigger_value
        join pg_catalog.pg_class relation
          on relation.oid = trigger_value.tgrelid
        join pg_catalog.pg_namespace namespace
          on namespace.oid = relation.relnamespace
        where namespace.nspname = 'agent_runtime'
          and relation.relname = 'harmony_rounds'
          and trigger_value.tgname
                = 'harmony_rounds_guard_current_connector_trust'
          and not trigger_value.tgisinternal
    ) or not exists (
        select 1
        from pg_catalog.pg_trigger trigger_value
        join pg_catalog.pg_class relation
          on relation.oid = trigger_value.tgrelid
        join pg_catalog.pg_namespace namespace
          on namespace.oid = relation.relnamespace
        where namespace.nspname = 'agent_runtime'
          and relation.relname = 'harmony_stage_receipts'
          and trigger_value.tgname
                = 'harmony_stage_receipts_guard_current_connector_trust'
          and not trigger_value.tgisinternal
    ) or not exists (
        select 1
        from pg_catalog.pg_trigger trigger_value
        join pg_catalog.pg_class relation
          on relation.oid = trigger_value.tgrelid
        join pg_catalog.pg_namespace namespace
          on namespace.oid = relation.relnamespace
        where namespace.nspname = 'agent_runtime'
          and relation.relname = 'harmony_stage_receipts'
          and trigger_value.tgname = 'harmony_stage_receipts_guard_positive_qa'
          and not trigger_value.tgisinternal
    ) then
        raise exception 'Harmony trust linearization/QA guard trigger missing';
    end if;
end
$test$;

do $test$
declare
    lock_definition text;
    round_definition text;
    stage_definition text;
    denial_trigger_definition text;
    denial_rpc_definition text;
begin
    lock_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'private.harmony_preview_lock_manifest_registrations(uuid,text,jsonb)'
            ::pg_catalog.regprocedure
    ));
    round_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'private.harmony_preview_guard_round_insert_current()'
            ::pg_catalog.regprocedure
    ));
    stage_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'private.harmony_preview_guard_stage_insert_current()'
            ::pg_catalog.regprocedure
    ));
    denial_trigger_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'private.harmony_preview_validate_qa_denial_insert()'
            ::pg_catalog.regprocedure
    ));
    denial_rpc_definition := pg_catalog.lower(pg_catalog.pg_get_functiondef(
        'public.record_preview_harmony_squid_qa_denial(uuid,text,uuid,uuid,uuid,jsonb)'
            ::pg_catalog.regprocedure
    ));
    if pg_catalog.strpos(
        lock_definition,
        'order by request.registration_id'
    ) = 0 or pg_catalog.strpos(
        lock_definition,
        'harmony_preview_lock_connector_registration'
    ) = 0 then
        raise exception 'manifest registration locks are not deterministic';
    end if;
    if pg_catalog.strpos(
        round_definition,
        'harmony_preview_lock_manifest_registrations'
    ) = 0 or pg_catalog.strpos(
        round_definition,
        'harmony_preview_lock_manifest_registrations'
    ) >= pg_catalog.strpos(
        round_definition,
        'harmony_preview_round_inputs_current'
    ) then
        raise exception 'round write path does not lock before currentness';
    end if;
    if pg_catalog.strpos(
        stage_definition,
        'harmony_preview_lock_manifest_registrations'
    ) = 0 or pg_catalog.strpos(
        stage_definition,
        'harmony_preview_lock_manifest_registrations'
    ) >= pg_catalog.strpos(
        stage_definition,
        'harmony_preview_round_inputs_current'
    ) then
        raise exception 'stage write path does not lock before currentness';
    end if;
    if pg_catalog.strpos(
        denial_trigger_definition,
        'harmony_preview_lock_manifest_registrations'
    ) = 0 or pg_catalog.strpos(
        denial_trigger_definition,
        'harmony_preview_lock_manifest_registrations'
    ) >= pg_catalog.strpos(
        denial_trigger_definition,
        'harmony_preview_round_inputs_current'
    ) then
        raise exception 'denial insert does not lock before currentness';
    end if;
    if pg_catalog.strpos(
        denial_rpc_definition,
        'harmony_preview_lock_manifest_registrations'
    ) = 0 or pg_catalog.strpos(
        denial_rpc_definition,
        'harmony_preview_lock_manifest_registrations'
    ) >= pg_catalog.strpos(
        denial_rpc_definition,
        'harmony_preview_round_inputs_current'
    ) then
        raise exception 'denial RPC does not lock before currentness';
    end if;
end
$test$;

insert into private.harmony_preview_environment_fence(
    branch_ref, active, expires_at
) values (
    'trustsecurity0000000', true,
    statement_timestamp() + interval '1 hour'
);

insert into private.harmony_preview_connector_registrations (
    branch_ref, workspace_id, client_id, registration_id, lane,
    capability, connector_id, producer_principal_id,
    producer_release_sha, config_sha256, attestation_key_id, expires_at
)
select
    'trustsecurity0000000', workspace.id, 'squid',
    'b1000000-0000-4000-8000-000000000001', 'quiz_bot',
    'harmony_submit_quiz_bot', 'quiz_bot_security',
    'b2000000-0000-4000-8000-000000000001',
    pg_catalog.repeat('b', 40), pg_catalog.repeat('c', 64),
    'squid.quiz.preview.key-1', statement_timestamp() + interval '30 minutes'
from public.workspaces workspace
where workspace.slug = 'coineasy-content-studio';

insert into private.harmony_preview_connector_registrations (
    branch_ref, workspace_id, client_id, registration_id, lane,
    capability, connector_id, producer_principal_id,
    producer_release_sha, config_sha256, attestation_key_id, expires_at
)
select
    'trustsecurity0000000', workspace.id, 'squid',
    'b1000000-0000-4000-8000-000000000002', 'community_ops',
    'harmony_submit_community_ops', 'community_ops_security',
    'b2000000-0000-4000-8000-000000000002',
    pg_catalog.repeat('d', 40), pg_catalog.repeat('e', 64),
    'squid.community.preview.key-1',
    statement_timestamp() + interval '30 minutes'
from public.workspaces workspace
where workspace.slug = 'coineasy-content-studio';

do $test$
declare
    target_workspace_id uuid;
    violated_constraint text;
    before_counts jsonb;
    after_counts jsonb;
begin
    select workspace.id into strict target_workspace_id
    from public.workspaces workspace
    where workspace.slug = 'coineasy-content-studio';
    select pg_catalog.jsonb_build_object(
        'registrations', (select pg_catalog.count(*)
            from private.harmony_preview_connector_registrations),
        'revocations', (select pg_catalog.count(*)
            from private.harmony_preview_connector_registration_revocations),
        'request_receipts', (select pg_catalog.count(*)
            from private.harmony_preview_connector_request_receipts)
    ) into before_counts;

    begin
        insert into private.harmony_preview_connector_registrations (
            branch_ref, workspace_id, client_id, registration_id, lane,
            capability, connector_id, producer_principal_id,
            producer_release_sha, config_sha256, attestation_key_id, expires_at
        ) values (
            'trustsecurity0000000', target_workspace_id, 'squid',
            'b1000000-0000-4000-8000-000000000003', 'quiz_bot',
            'harmony_submit_quiz_bot', 'lane_duplicate_security',
            'b2000000-0000-4000-8000-000000000003',
            pg_catalog.repeat('1', 40), pg_catalog.repeat('2', 64),
            'squid.lane-duplicate.preview.key-1',
            statement_timestamp() + interval '30 minutes'
        );
        raise exception 'duplicate connector lane unexpectedly succeeded';
    exception
        when unique_violation then
            get stacked diagnostics violated_constraint = constraint_name;
            if violated_constraint
                    <> 'harmony_connector_registration_lane_once' then
                raise exception 'duplicate lane constraint drifted: %',
                    violated_constraint;
            end if;
    end;

    begin
        insert into private.harmony_preview_connector_registrations (
            branch_ref, workspace_id, client_id, registration_id, lane,
            capability, connector_id, producer_principal_id,
            producer_release_sha, config_sha256, attestation_key_id, expires_at
        ) values (
            'trustsecurity0000000', target_workspace_id, 'squid',
            'b1000000-0000-4000-8000-000000000004', 'content_source',
            'harmony_submit_content_source', 'quiz_bot_security',
            'b2000000-0000-4000-8000-000000000004',
            pg_catalog.repeat('3', 40), pg_catalog.repeat('4', 64),
            'squid.connector-duplicate.preview.key-1',
            statement_timestamp() + interval '30 minutes'
        );
        raise exception 'duplicate connector id unexpectedly succeeded';
    exception
        when unique_violation then
            get stacked diagnostics violated_constraint = constraint_name;
            if violated_constraint
                    <> 'harmony_connector_registration_connector_once' then
                raise exception 'duplicate connector constraint drifted: %',
                    violated_constraint;
            end if;
    end;

    begin
        insert into private.harmony_preview_connector_registrations (
            branch_ref, workspace_id, client_id, registration_id, lane,
            capability, connector_id, producer_principal_id,
            producer_release_sha, config_sha256, attestation_key_id, expires_at
        ) values (
            'trustsecurity0000000', target_workspace_id, 'squid',
            'b1000000-0000-4000-8000-000000000005', 'recap',
            'harmony_submit_recap', 'principal_duplicate_security',
            'b2000000-0000-4000-8000-000000000001',
            pg_catalog.repeat('5', 40), pg_catalog.repeat('6', 64),
            'squid.principal-duplicate.preview.key-1',
            statement_timestamp() + interval '30 minutes'
        );
        raise exception 'duplicate connector principal unexpectedly succeeded';
    exception
        when unique_violation then
            get stacked diagnostics violated_constraint = constraint_name;
            if violated_constraint
                    <> 'harmony_connector_registration_principal_once' then
                raise exception 'duplicate principal constraint drifted: %',
                    violated_constraint;
            end if;
    end;

    begin
        insert into private.harmony_preview_connector_registrations (
            branch_ref, workspace_id, client_id, registration_id, lane,
            capability, connector_id, producer_principal_id,
            producer_release_sha, config_sha256, attestation_key_id, expires_at
        ) values (
            'trustsecurity0000000', target_workspace_id, 'squid',
            'b1000000-0000-4000-8000-000000000006', 'content_source',
            'harmony_submit_content_source', 'key_duplicate_security',
            'b2000000-0000-4000-8000-000000000006',
            pg_catalog.repeat('7', 40), pg_catalog.repeat('8', 64),
            'squid.quiz.preview.key-1',
            statement_timestamp() + interval '30 minutes'
        );
        raise exception 'duplicate attestation key unexpectedly succeeded';
    exception
        when unique_violation then
            get stacked diagnostics violated_constraint = constraint_name;
            if violated_constraint
                    <> 'harmony_connector_registration_key_once' then
                raise exception 'duplicate key constraint drifted: %',
                    violated_constraint;
            end if;
    end;

    select pg_catalog.jsonb_build_object(
        'registrations', (select pg_catalog.count(*)
            from private.harmony_preview_connector_registrations),
        'revocations', (select pg_catalog.count(*)
            from private.harmony_preview_connector_registration_revocations),
        'request_receipts', (select pg_catalog.count(*)
            from private.harmony_preview_connector_request_receipts)
    ) into after_counts;
    if after_counts <> before_counts
       or after_counts <> pg_catalog.jsonb_build_object(
            'registrations', 2,
            'revocations', 0,
            'request_receipts', 0
       )
    then
        raise exception 'duplicate registration admission changed trust ledgers';
    end if;
end
$test$;

do $test$
declare
    workspace_id uuid;
    registration private.harmony_preview_connector_registrations%rowtype;
begin
    select workspace.id into strict workspace_id
    from public.workspaces workspace
    where workspace.slug = 'coineasy-content-studio';
    registration := private.harmony_preview_lock_connector_registration(
        workspace_id, 'squid',
        'b1000000-0000-4000-8000-000000000001'
    );
    if registration.registration_sha256 !~ '^[a-f0-9]{64}$'
       or registration.attestation_key_id <> 'squid.quiz.preview.key-1'
    then
        raise exception 'immutable connector registration is malformed';
    end if;
end
$test$;

do $test$
declare
    target_workspace_id uuid;
begin
    select workspace.id into strict target_workspace_id
    from public.workspaces workspace
    where workspace.slug = 'coineasy-content-studio';
    begin
        update private.harmony_preview_connector_registrations
        set connector_id = 'mutated_connector'
        where private.harmony_preview_connector_registrations.workspace_id
                = target_workspace_id
          and registration_id = 'b1000000-0000-4000-8000-000000000001';
        raise exception 'connector registration update unexpectedly succeeded';
    exception
        when sqlstate '55000' then
            null;
    end;
end
$test$;

do $test$
declare
    target_workspace_id uuid;
    signal jsonb;
    digest_one text;
    digest_two text;
begin
    select workspace.id into strict target_workspace_id
    from public.workspaces workspace
    where workspace.slug = 'coineasy-content-studio';
    signal := pg_catalog.jsonb_build_object(
        'signal_id', 'b3000000-0000-4000-8000-000000000001',
        'source_event_id', 'b4000000-0000-4000-8000-000000000001',
        'producer_principal_id', 'b2000000-0000-4000-8000-000000000001',
        'signal_kind', 'quiz_learning', 'lane', 'quiz_bot',
        'payload_sha256', pg_catalog.repeat('f', 64)
    );
    digest_one := private.harmony_preview_connector_request_sha256(
        target_workspace_id, 'squid',
        'b1000000-0000-4000-8000-000000000001',
        'b5000000-0000-4000-8000-000000000001', signal
    );
    digest_two := private.harmony_preview_connector_request_sha256(
        target_workspace_id, 'squid',
        'b1000000-0000-4000-8000-000000000001',
        'b5000000-0000-4000-8000-000000000002', signal
    );
    if digest_one !~ '^[a-f0-9]{64}$'
       or digest_one = digest_two
       or digest_one <> private.harmony_preview_connector_request_sha256(
            target_workspace_id, 'squid',
            'b1000000-0000-4000-8000-000000000001',
            'b5000000-0000-4000-8000-000000000001', signal
       )
    then
        raise exception 'database request digest is not exact/deterministic';
    end if;
end
$test$;

do $test$
declare
    vector_signal jsonb;
    vector_digest text;
begin
    vector_signal := pg_catalog.jsonb_build_object(
        'attempts', 64,
        'automatic_publication', false,
        'client_id', 'squid',
        'lane', 'quiz_bot',
        'payload_sha256',
            'a908c2820db28b21f5ef4caf467c3d0eef274b96a5e35082d021834624b2e8c6',
        'producer_principal_id',
            '66666666-6666-4666-8666-666666666666',
        'schema_version', 'agent-harmony-signal@1',
        'signal_id', '44444444-4444-4444-8444-444444444444',
        'signal_kind', 'quiz_learning',
        'source_event_id', '55555555-5555-4555-8555-555555555555',
        'topic_codes', pg_catalog.jsonb_build_array(
            'official_update', '퀴즈'
        ),
        'workspace_id', '11111111-1111-4111-8111-111111111111'
    );
    vector_digest := private.harmony_preview_connector_request_sha256(
        '11111111-1111-4111-8111-111111111111',
        'squid',
        '22222222-2222-4222-8222-222222222222',
        '33333333-3333-4333-8333-333333333333',
        vector_signal
    );
    if private.agent_json_sha256(vector_signal - 'payload_sha256')
            <> vector_signal ->> 'payload_sha256'
       or vector_digest
            <> 'cfdf90b7d13d375ab4db44d32ab3fd115f5c830ddf99d526251d1c642add9bb9'
    then
        raise exception 'fixed connector request digest vector drifted';
    end if;
end
$test$;

select pg_catalog.set_config(
    'request.jwt.claims',
    pg_catalog.jsonb_build_object(
        'iss', 'supabase', 'aud', 'authenticated',
        'sub', 'b2000000-0000-4000-8000-000000000002',
        'role', 'coineasy_harmony_connector',
        'workspace_id', (
            select workspace.id::text from public.workspaces workspace
            where workspace.slug = 'coineasy-content-studio'
        ),
        'client_id', 'squid', 'environment', 'preview',
        'ref', 'trustsecurity0000000',
        'producer_principal_id', 'b2000000-0000-4000-8000-000000000002',
        'release_sha', pg_catalog.repeat('d', 40),
        'config_sha256', pg_catalog.repeat('e', 64),
        'capability', 'harmony_submit_community_ops',
        'connector_id', 'community_ops_security',
        'attestation_registration_id',
            'b1000000-0000-4000-8000-000000000002',
        'attestation_key_id', 'squid.community.preview.key-1',
        'request_nonce', 'b6000000-0000-4000-8000-000000000001',
        'request_sha256', pg_catalog.repeat('a', 64),
        'jti', 'b6000000-0000-4000-8000-000000000001',
        'iat', extract(epoch from statement_timestamp())::bigint,
        'exp', extract(epoch from statement_timestamp())::bigint + 1200,
        'automatic_publication', false,
        'max_cost_microusd', 0,
        'max_external_actions', 0
    )::text,
    true
);

do $test$
declare
    target_workspace_id uuid;
    registration_created_at timestamptz;
    signal_body jsonb;
    target_signal jsonb;
    request_digest text;
    positive_result jsonb;
    before_counts jsonb;
    during_counts jsonb;
    after_counts jsonb;
begin
    select workspace.id into strict target_workspace_id
    from public.workspaces workspace
    where workspace.slug = 'coineasy-content-studio';
    select registration.created_at into strict registration_created_at
    from private.harmony_preview_connector_registrations registration
    where registration.workspace_id = target_workspace_id
      and registration.client_id = 'squid'
      and registration.registration_id
            = 'b1000000-0000-4000-8000-000000000002';
    signal_body := pg_catalog.jsonb_build_object(
        'schema_version', 'agent-harmony-signal@1',
        'signal_id', 'b3000000-0000-4000-8000-000000000004',
        'workspace_id', target_workspace_id::text,
        'client_id', 'squid',
        'signal_kind', 'community_demand',
        'lane', 'community_ops',
        'source_event_id', 'b4000000-0000-4000-8000-000000000004',
        'producer_principal_id',
            'b2000000-0000-4000-8000-000000000002',
        'producer_release_sha', pg_catalog.repeat('d', 40),
        'config_sha256', pg_catalog.repeat('e', 64),
        'upstream_receipt_sha256', pg_catalog.repeat('7', 64),
        'observed_at', pg_catalog.to_char(
            (statement_timestamp() - interval '5 seconds') at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        ),
        'expires_at', pg_catalog.to_char(
            (statement_timestamp() + interval '10 minutes') at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        ),
        'evidence_sha256', pg_catalog.repeat('8', 64),
        'topic_codes', pg_catalog.jsonb_build_array('official_update'),
        'content_factual_authority', false,
        'raw_messages_included', false,
        'personal_data_included', false,
        'instructions_allowed', false,
        'advisory_only', true,
        'max_cost_microusd', 0,
        'max_external_actions', 0,
        'automatic_publication', false,
        'data_classification', 'aggregate_anonymous',
        'room_mapping_count', 1,
        'sample_size', 32,
        'demand_score_basis_points', 8200
    );
    target_signal := signal_body || pg_catalog.jsonb_build_object(
        'payload_sha256', private.agent_json_sha256(signal_body)
    );
    request_digest := private.harmony_preview_connector_request_sha256(
        target_workspace_id,
        'squid',
        'b1000000-0000-4000-8000-000000000002',
        'b5000000-0000-4000-8000-000000000004',
        target_signal
    );
    perform pg_catalog.set_config(
        'request.jwt.claims',
        (
            pg_catalog.current_setting('request.jwt.claims')::jsonb
            || pg_catalog.jsonb_build_object(
                'iat', extract(epoch from (
                    pg_catalog.date_trunc('second', registration_created_at)
                    - interval '1 second'
                ))::bigint,
                'request_nonce', 'b6000000-0000-4000-8000-000000000004',
                'jti', 'b6000000-0000-4000-8000-000000000004',
                'request_sha256', request_digest
            )
        )::text,
        true
    );
    select pg_catalog.jsonb_build_object(
        'signals', (select pg_catalog.count(*)
                    from agent_runtime.harmony_signals),
        'connector_receipts', (select pg_catalog.count(*)
                    from agent_runtime.harmony_connector_attestation_receipts),
        'request_receipts', (select pg_catalog.count(*)
                    from private.harmony_preview_connector_request_receipts)
    ) into before_counts;
    begin
        perform public.submit_preview_harmony_signal(
            target_workspace_id,
            'squid',
            'b5000000-0000-4000-8000-000000000004',
            target_signal
        );
        raise exception 'pre-registration JWT unexpectedly succeeded';
    exception
        when others then
            if sqlerrm <> 'harmony_preview_connector_registration_invalid' then
                raise exception 'pre-registration JWT rejection drifted: %',
                    sqlerrm;
            end if;
    end;
    select pg_catalog.jsonb_build_object(
        'signals', (select pg_catalog.count(*)
                    from agent_runtime.harmony_signals),
        'connector_receipts', (select pg_catalog.count(*)
                    from agent_runtime.harmony_connector_attestation_receipts),
        'request_receipts', (select pg_catalog.count(*)
                    from private.harmony_preview_connector_request_receipts)
    ) into after_counts;
    if after_counts <> before_counts
       or after_counts <> pg_catalog.jsonb_build_object(
            'signals', 0,
            'connector_receipts', 0,
            'request_receipts', 0
       )
    then
        raise exception 'pre-registration JWT rejection wrote ledger rows';
    end if;

    perform pg_catalog.set_config(
        'request.jwt.claims',
        (
            pg_catalog.current_setting('request.jwt.claims')::jsonb
            || pg_catalog.jsonb_build_object(
                'iat', '-9223372036854775808'::bigint,
                'exp', extract(epoch from statement_timestamp())::bigint + 1200
            )
        )::text,
        true
    );
    begin
        perform public.submit_preview_harmony_signal(
            target_workspace_id,
            'squid',
            'b5000000-0000-4000-8000-000000000004',
            target_signal
        );
        raise exception 'extreme epoch JWT unexpectedly succeeded';
    exception
        when others then
            if sqlerrm <> 'harmony_preview_connector_registration_invalid' then
                raise exception 'extreme epoch typed rejection drifted: %',
                    sqlerrm;
            end if;
    end;
    select pg_catalog.jsonb_build_object(
        'signals', (select pg_catalog.count(*)
                    from agent_runtime.harmony_signals),
        'connector_receipts', (select pg_catalog.count(*)
                    from agent_runtime.harmony_connector_attestation_receipts),
        'request_receipts', (select pg_catalog.count(*)
                    from private.harmony_preview_connector_request_receipts)
    ) into after_counts;
    if after_counts <> before_counts then
        raise exception 'extreme epoch rejection wrote ledger rows';
    end if;

    perform pg_catalog.set_config(
        'request.jwt.claims',
        (
            pg_catalog.current_setting('request.jwt.claims')::jsonb
            || pg_catalog.jsonb_build_object(
                'iat', extract(epoch from pg_catalog.date_trunc(
                    'second', registration_created_at
                ))::bigint
            )
        )::text,
        true
    );
    if not private.harmony_preview_connector_claims_match(
        target_workspace_id, 'squid', target_signal
    ) then
        raise exception 'post-registration JWT positive control was rejected';
    end if;
    begin
        positive_result := public.submit_preview_harmony_signal(
            target_workspace_id,
            'squid',
            'b5000000-0000-4000-8000-000000000004',
            target_signal
        );
        select pg_catalog.jsonb_build_object(
            'signals', (select pg_catalog.count(*)
                        from agent_runtime.harmony_signals),
            'connector_receipts', (select pg_catalog.count(*)
                        from agent_runtime.harmony_connector_attestation_receipts),
            'request_receipts', (select pg_catalog.count(*)
                        from private.harmony_preview_connector_request_receipts)
        ) into during_counts;
        if positive_result -> 'ok' is distinct from 'true'::jsonb
           or positive_result -> 'reused' is distinct from 'false'::jsonb
           or during_counts <> pg_catalog.jsonb_build_object(
                'signals', 1,
                'connector_receipts', 1,
                'request_receipts', 1
           )
        then
            raise exception 'post-registration JWT positive control drifted';
        end if;
        if exists (
            select 1
            from private.harmony_preview_connector_registrations registration
            join agent_runtime.harmony_connector_attestation_receipts receipt
              on receipt.workspace_id = registration.workspace_id
             and receipt.client_id = registration.client_id
            join private.harmony_preview_connector_request_receipts request
              on request.workspace_id = receipt.workspace_id
             and request.client_id = receipt.client_id
             and request.registration_id = registration.registration_id
             and request.connector_receipt_id = receipt.receipt_id
            where registration.workspace_id = target_workspace_id
              and registration.client_id = 'squid'
              and registration.registration_id
                    = 'b1000000-0000-4000-8000-000000000002'
              and (
                    registration.created_at > receipt.verified_at
                    or receipt.verified_at > request.accepted_at
              )
        ) then
            raise exception 'connector receipt chronology drifted';
        end if;
        raise exception using
            errcode = 'ZZ001',
            message = 'rollback_post_registration_positive_control';
    exception
        when sqlstate 'ZZ001' then
            null;
    end;
    select pg_catalog.jsonb_build_object(
        'signals', (select pg_catalog.count(*)
                    from agent_runtime.harmony_signals),
        'connector_receipts', (select pg_catalog.count(*)
                    from agent_runtime.harmony_connector_attestation_receipts),
        'request_receipts', (select pg_catalog.count(*)
                    from private.harmony_preview_connector_request_receipts)
    ) into after_counts;
    if after_counts <> before_counts then
        raise exception 'post-registration positive control was not isolated';
    end if;
end
$test$;

do $test$
declare
    target_workspace_id uuid;
    signal_body jsonb;
    fractional_signal jsonb;
    request_digest text;
    before_counts jsonb;
    after_counts jsonb;
begin
    select workspace.id into strict target_workspace_id
    from public.workspaces workspace
    where workspace.slug = 'coineasy-content-studio';
    signal_body := pg_catalog.jsonb_build_object(
        'schema_version', 'agent-harmony-signal@1',
        'signal_id', 'b3000000-0000-4000-8000-000000000002',
        'workspace_id', target_workspace_id::text,
        'client_id', 'squid',
        'signal_kind', 'community_demand',
        'lane', 'community_ops',
        'source_event_id', 'b4000000-0000-4000-8000-000000000002',
        'producer_principal_id',
            'b2000000-0000-4000-8000-000000000002',
        'producer_release_sha', pg_catalog.repeat('d', 40),
        'config_sha256', pg_catalog.repeat('e', 64),
        'upstream_receipt_sha256', pg_catalog.repeat('f', 64),
        'observed_at', pg_catalog.to_char(
            (statement_timestamp() - interval '5 seconds') at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS'
        ) || '.123Z',
        'expires_at', pg_catalog.to_char(
            (statement_timestamp() + interval '10 minutes') at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS'
        ) || '.123Z',
        'evidence_sha256', pg_catalog.repeat('a', 64),
        'topic_codes', pg_catalog.jsonb_build_array('official_update'),
        'content_factual_authority', false,
        'raw_messages_included', false,
        'personal_data_included', false,
        'instructions_allowed', false,
        'advisory_only', true,
        'max_cost_microusd', 0,
        'max_external_actions', 0,
        'automatic_publication', false,
        'data_classification', 'aggregate_anonymous',
        'room_mapping_count', 1,
        'sample_size', 32,
        'demand_score_basis_points', 8200
    );
    fractional_signal := signal_body || pg_catalog.jsonb_build_object(
        'payload_sha256', private.agent_json_sha256(signal_body)
    );
    if not private.harmony_preview_signal_valid(fractional_signal) then
        raise exception 'fractional timestamp fixture is not base-SQL valid';
    end if;
    request_digest := private.harmony_preview_connector_request_sha256(
        target_workspace_id,
        'squid',
        'b1000000-0000-4000-8000-000000000002',
        'b5000000-0000-4000-8000-000000000003',
        fractional_signal
    );
    perform pg_catalog.set_config(
        'request.jwt.claims',
        (
            pg_catalog.current_setting('request.jwt.claims')::jsonb
            || pg_catalog.jsonb_build_object(
                'request_sha256', request_digest
            )
        )::text,
        true
    );
    select pg_catalog.jsonb_build_object(
        'signals', (select pg_catalog.count(*)
                    from agent_runtime.harmony_signals),
        'connector_receipts', (select pg_catalog.count(*)
                    from agent_runtime.harmony_connector_attestation_receipts),
        'request_receipts', (select pg_catalog.count(*)
                    from private.harmony_preview_connector_request_receipts)
    ) into before_counts;
    begin
        perform public.submit_preview_harmony_signal(
            target_workspace_id,
            'squid',
            'b5000000-0000-4000-8000-000000000003',
            fractional_signal
        );
        raise exception 'fractional timestamp admission unexpectedly succeeded';
    exception
        when others then
            if sqlerrm <> 'harmony_preview_connector_trust_claim_invalid' then
                raise exception 'fractional timestamp rejection drifted: %',
                    sqlerrm;
            end if;
    end;
    select pg_catalog.jsonb_build_object(
        'signals', (select pg_catalog.count(*)
                    from agent_runtime.harmony_signals),
        'connector_receipts', (select pg_catalog.count(*)
                    from agent_runtime.harmony_connector_attestation_receipts),
        'request_receipts', (select pg_catalog.count(*)
                    from private.harmony_preview_connector_request_receipts)
    ) into after_counts;
    if after_counts <> before_counts
       or after_counts <> pg_catalog.jsonb_build_object(
            'signals', 0,
            'connector_receipts', 0,
            'request_receipts', 0
       )
    then
        raise exception 'fractional timestamp rejection wrote ledger rows';
    end if;
end
$test$;

do $test$
declare
    target_workspace_id uuid;
    target_signal jsonb;
begin
    select workspace.id into strict target_workspace_id
    from public.workspaces workspace
    where workspace.slug = 'coineasy-content-studio';
    target_signal := pg_catalog.jsonb_build_object(
        'lane', 'community_ops',
        'producer_principal_id', 'b2000000-0000-4000-8000-000000000002',
        'producer_release_sha', pg_catalog.repeat('d', 40),
        'config_sha256', pg_catalog.repeat('e', 64)
    );
    if not private.harmony_preview_connector_claims_match(
        target_workspace_id, 'squid', target_signal
    ) or private.harmony_preview_connector_verification_reference()
            <> private.harmony_preview_connector_token_claims_sha256()
    then
        raise exception 'registered connector claim binding failed';
    end if;
end
$test$;

insert into private.harmony_preview_connector_registration_revocations (
    workspace_id, client_id, revocation_id, registration_id,
    registration_sha256, reason_code
)
select registration.workspace_id, registration.client_id,
       'b7000000-0000-4000-8000-000000000001',
       registration.registration_id, registration.registration_sha256,
       'connector_disabled'
from private.harmony_preview_connector_registrations registration
where registration.registration_id
    = 'b1000000-0000-4000-8000-000000000002';

do $test$
declare
    target_workspace_id uuid;
    target_signal jsonb;
begin
    select workspace.id into strict target_workspace_id
    from public.workspaces workspace
    where workspace.slug = 'coineasy-content-studio';
    target_signal := pg_catalog.jsonb_build_object(
        'lane', 'community_ops',
        'producer_principal_id', 'b2000000-0000-4000-8000-000000000002',
        'producer_release_sha', pg_catalog.repeat('d', 40),
        'config_sha256', pg_catalog.repeat('e', 64)
    );
    if private.harmony_preview_connector_claims_match(
        target_workspace_id, 'squid', target_signal
    ) then
        raise exception 'revoked connector remained current';
    end if;
    begin
        delete from private.harmony_preview_connector_registration_revocations
        where private.harmony_preview_connector_registration_revocations.workspace_id
                = target_workspace_id;
        raise exception 'connector revocation delete unexpectedly succeeded';
    exception
        when sqlstate '55000' then
            null;
    end;
end
$test$;

do $test$
declare
    output_sha text := pg_catalog.repeat('9', 64);
    valid_evidence jsonb;
    invalid_evidence jsonb;
begin
    valid_evidence := pg_catalog.jsonb_build_object(
        'schema_version', 'harmony-independent-qa-evidence@1',
        'reviewed_output_sha256', output_sha,
        'criteria', pg_catalog.jsonb_build_object(
            'automatic_publication', false,
            'factual_binding', false,
            'no_external_calls', false,
            'private_only', true
        ),
        'findings', pg_catalog.jsonb_build_array(
            'external_call_detected', 'factual_binding_failed'
        ),
        'verdict', 'failed',
        'verifier_version', 'harmony-deterministic-qa@1'
    );
    if not private.harmony_preview_failed_qa_evidence_valid(
        valid_evidence, output_sha
    ) or private.harmony_preview_qa_failed_finding_codes(
        valid_evidence -> 'criteria'
    ) is distinct from array[
        'external_call_detected', 'factual_binding_failed'
    ]::text[] then
        raise exception 'valid closed QA denial evidence was rejected';
    end if;
    invalid_evidence := valid_evidence || pg_catalog.jsonb_build_object(
        'raw_content', 'must never be durable'
    );
    if private.harmony_preview_failed_qa_evidence_valid(
        invalid_evidence, output_sha
    ) or private.harmony_preview_failed_qa_evidence_valid(
        valid_evidence || pg_catalog.jsonb_build_object(
            'findings', pg_catalog.jsonb_build_array('unknown_finding')
        ), output_sha
    ) then
        raise exception 'open/raw QA denial evidence crossed the closed schema';
    end if;
end
$test$;

do $test$
begin
    if exists (select 1 from private.harmony_preview_connector_request_receipts)
       or exists (select 1 from private.harmony_preview_qa_denial_receipts)
       or exists (select 1 from agent_runtime.harmony_operator_inbox)
    then
        raise exception 'security smoke created request, denial, or inbox rows';
    end if;
end
$test$;

rollback;
