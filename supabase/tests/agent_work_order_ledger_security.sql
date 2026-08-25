-- Transactional security smoke for the planning-only Agent Work Order ledger.
-- Run after both 2026082512* migrations as the database owner.  The worker,
-- result, and verification adapters are intentionally absent in this release;
-- the owner-only fixtures below exist solely to exercise the receipt-chain
-- gates and every fixture is removed by the final rollback.

begin;

create function pg_temp.agent_security_scope(
    target_work_order_id uuid,
    target_objective_id uuid,
    target_causation_id uuid,
    target_idempotency_key text,
    target_repository text,
    target_branch_name text,
    target_allowed_paths jsonb,
    target_title text,
    target_created_at text default '2099-01-01T00:00:00Z',
    target_expires_at text default '2099-01-08T00:00:00Z',
    target_owner text default 'devin',
    target_reviewer text default 'codex'
)
returns jsonb
language sql
immutable
set search_path = ''
as $$
    select pg_catalog.jsonb_build_object(
        'acceptance_criteria', pg_catalog.jsonb_build_array(
            'All gates stay fail closed'
        ),
        'allowed_environment', 'local',
        'allowed_paths', target_allowed_paths,
        'automatic_publication', false,
        'base_sha', pg_catalog.repeat('a', 40),
        'branch_name', target_branch_name,
        'causation_id', target_causation_id::text,
        'client_id', null,
        'created_at', target_created_at,
        'evidence', pg_catalog.jsonb_build_array(
            pg_catalog.jsonb_build_object(
                'sha256', pg_catalog.repeat('e', 64),
                'uri', 'docs/AGENT_LEDGER.md'
            )
        ),
        'expected_artifacts', pg_catalog.jsonb_build_array(
            'Security fixture'
        ),
        'expires_at', target_expires_at,
        'forbidden_actions', pg_catalog.jsonb_build_array(
            'branch_push',
            'draft_pr_create',
            'merge',
            'preview_deploy',
            'production_deploy',
            'production_database_write',
            'credential_change',
            'paid_provider_call',
            'public_message',
            'publication'
        ),
        'idempotency_key', target_idempotency_key,
        'max_cost_microusd', 0,
        'max_external_actions', 0,
        'max_handoffs', 1,
        'max_runtime_seconds', 600,
        'objective',
            'Build a reversible local-only security fixture for the agent ledger.',
        'objective_id', target_objective_id::text,
        'owner', target_owner,
        'parent_work_order_id', null,
        'repository', target_repository,
        'requested_by', 'human_operator',
        'reviewer', target_reviewer,
        'risk_tier', 'R1',
        'schema_version', 'agent-work-order@1',
        'title', target_title,
        'verification_commands', pg_catalog.jsonb_build_array(
            'pytest -q tests/test_agent_work_order.py'
        ),
        'work_order_id', target_work_order_id::text,
        'work_type', 'engineering'
    )
$$;

do $test$
declare
    table_name text;
    relation_record record;
    untrusted_roles constant text[] := array[
        'anon',
        'authenticated',
        'service_role',
        'authenticator',
        'coineasy_agent_dashboard',
        'coineasy_agent_control_plane'
    ];
begin
    if exists (
        select 1
        from pg_catalog.pg_roles as role_record
        where role_record.rolname = any(array[
            'coineasy_agent_dashboard',
            'coineasy_agent_control_plane'
        ])
          and (
              role_record.rolcanlogin
              or role_record.rolinherit
              or role_record.rolsuper
              or role_record.rolcreaterole
              or role_record.rolcreatedb
              or role_record.rolreplication
              or role_record.rolbypassrls
          )
    ) then
        raise exception 'agent role has a privileged role attribute';
    end if;

    foreach table_name in array array[
        'agent_work_orders',
        'agent_work_order_events',
        'agent_runs',
        'agent_dispatch_outbox',
        'agent_action_receipts',
        'agent_incidents'
    ] loop
        select class.oid, class.relowner, class.relacl,
               class.relrowsecurity, class.relforcerowsecurity
        into strict relation_record
        from pg_catalog.pg_class as class
        join pg_catalog.pg_namespace as namespace
          on namespace.oid = class.relnamespace
        where namespace.nspname = 'agent_runtime'
          and class.relname = table_name
          and class.relkind = 'r';

        if not relation_record.relrowsecurity
           or not relation_record.relforcerowsecurity then
            raise exception 'agent_runtime.% is not FORCE RLS', table_name;
        end if;
        if exists (
            select 1
            from pg_catalog.aclexplode(coalesce(
                relation_record.relacl,
                pg_catalog.acldefault('r', relation_record.relowner)
            )) as acl
            left join pg_catalog.pg_roles as grantee
              on grantee.oid = acl.grantee
            where acl.grantee = 0
               or grantee.rolname = any(untrusted_roles)
        ) then
            raise exception 'agent_runtime.% has a direct table grant',
                table_name;
        end if;
    end loop;

    if has_function_privilege(
        'coineasy_agent_dashboard',
        'public.record_agent_operator_decision(uuid,uuid,text,bigint,text,text)',
        'execute'
    ) or has_function_privilege(
        'coineasy_agent_control_plane',
        'public.record_agent_operator_decision(uuid,uuid,text,bigint,text,text)',
        'execute'
    ) then
        raise exception 'non-operator role can self-approve a work order';
    end if;

    -- No claim/result/provider-attempt transition exists yet.  Consequently a
    -- pending packet can neither be duplicate-claimed nor become an ambiguous
    -- delivery_unknown row in this planning-only release.
    if exists (
        select 1
        from pg_catalog.pg_proc as procedure
        join pg_catalog.pg_namespace as namespace
          on namespace.oid = procedure.pronamespace
        where namespace.nspname = 'public'
          and procedure.proname = any(array[
              'claim_agent_dispatch',
              'claim_agent_work_order',
              'mark_agent_dispatch_attempt',
              'complete_agent_dispatch',
              'fail_agent_dispatch',
              'submit_agent_work_result',
              'record_agent_work_result',
              'submit_agent_verification',
              'record_agent_verification'
          ])
    ) then
        raise exception 'an unapproved Agent worker adapter is exposed';
    end if;
end
$test$;

insert into auth.users (id)
values
    ('af000000-0000-4000-8000-000000000001'),
    ('af000000-0000-4000-8000-000000000002'),
    ('af000000-0000-4000-8000-000000000003');

insert into public.workspaces (id, name, slug, created_by)
values (
    'a0000000-0000-4000-8000-000000000001',
    'Agent Ledger Security Test',
    'agent-ledger-security-test',
    'af000000-0000-4000-8000-000000000001'
);

insert into public.workspace_members (
    workspace_id, user_id, role, status, invited_by
) values (
    'a0000000-0000-4000-8000-000000000001',
    'af000000-0000-4000-8000-000000000003',
    'viewer',
    'active',
    'af000000-0000-4000-8000-000000000001'
);

do $test$
begin
    if not exists (
        select 1
        from public.workspace_members
        where workspace_id = 'a0000000-0000-4000-8000-000000000001'
          and user_id = 'af000000-0000-4000-8000-000000000001'
          and role = 'owner'
          and status = 'active'
    ) then
        raise exception 'operator owner membership fixture is missing';
    end if;
end
$test$;

create temp table agent_security_side_effect_baseline as
select
    (select count(*) from public.approvals) as approval_count,
    (select count(*) from public.publications) as publication_count,
    (select count(*) from agent_runtime.batch_jobs) as batch_job_count,
    (select count(*) from agent_runtime.batch_runs) as batch_run_count,
    (select count(*) from agent_runtime.buzz_delivery_receipts)
        as buzz_delivery_count,
    (select count(*) from agent_runtime.buzz_review_decisions)
        as buzz_decision_count,
    (select count(*) from agent_runtime.buzz_review_ack_receipts)
        as buzz_ack_count,
    (select count(*) from private.grok_qa_dispatch_outbox)
        as grok_outbox_count,
    (select count(*) from private.grok_qa_verdict_receipts)
        as grok_receipt_count;

do $test$
declare
    scope jsonb := pg_temp.agent_security_scope(
        'a1000000-0000-4000-8000-000000000001',
        'a2000000-0000-4000-8000-000000000001',
        'a3000000-0000-4000-8000-000000000001',
        'security:ledger:primary',
        'CoinEasy/Content-Engine',
        'Agent/Security-Primary',
        '["Core/Agent_Control"]'::jsonb,
        'Durable agent ledger primary fixture'
    );
begin
    if private.agent_json_sha256(scope)
       <> '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d' then
        raise exception 'recursive canonical JSON digest drifted from golden hash';
    end if;
    if not private.agent_work_order_scope_valid(scope) then
        raise exception 'golden security scope is unexpectedly invalid';
    end if;
end
$test$;

do $test$
declare
    scope jsonb := pg_temp.agent_security_scope(
        'a1000000-0000-4000-8000-000000000010',
        'a2000000-0000-4000-8000-000000000010',
        'a3000000-0000-4000-8000-000000000010',
        'security:ledger:validator-parity',
        'CoinEasy/Content-Engine',
        'Agent/Security-Validator',
        '["Core/Agent_Control"]'::jsonb,
        'Durable agent ledger validator fixture'
    );
begin
    if private.agent_work_order_scope_valid(pg_catalog.jsonb_set(
        scope, '{title}', pg_catalog.to_jsonb(' leading space'::text)
    )) then
        raise exception 'SQL accepted whitespace Python normalizes';
    end if;
    if private.agent_work_order_scope_valid(pg_catalog.jsonb_set(
        scope, '{title}', pg_catalog.to_jsonb(123::integer)
    )) then
        raise exception 'SQL accepted a non-string title';
    end if;
    if private.agent_work_order_scope_valid(pg_catalog.jsonb_set(
        scope,
        '{max_runtime_seconds}',
        pg_catalog.to_jsonb('600'::text)
    )) then
        raise exception 'SQL accepted a string runtime limit';
    end if;
    if private.agent_work_order_scope_valid(pg_catalog.jsonb_set(
        scope,
        '{client_id}',
        pg_catalog.to_jsonb(pg_catalog.repeat('a', 32))
    )) then
        raise exception 'SQL accepted a token-shaped client identifier';
    end if;
    if private.agent_work_order_scope_valid(pg_catalog.jsonb_set(
        scope,
        '{expected_artifacts}',
        '["Repeated artifact","Repeated artifact"]'::jsonb
    )) then
        raise exception 'SQL accepted a duplicate Python rejects';
    end if;
    if private.agent_work_order_scope_valid(pg_catalog.jsonb_set(
        scope,
        '{evidence}',
        '[{"sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","uri":".git/config"}]'::jsonb
    )) then
        raise exception 'SQL accepted forbidden repository evidence';
    end if;
end
$test$;

create temp table agent_terminal_scopes as
select fixture.kind,
       fixture.scope,
       private.agent_json_sha256(fixture.scope) as scope_sha256
from (values
    (
        'cancelled',
        pg_temp.agent_security_scope(
            'a1000000-0000-4000-8000-000000000020',
            'a2000000-0000-4000-8000-000000000020',
            'a3000000-0000-4000-8000-000000000020',
            'security:ledger:cancelled',
            'CoinEasy/Content-Engine',
            'Agent/Security-Cancelled',
            '["Independent/Cancelled"]'::jsonb,
            'Durable agent ledger cancelled fixture'
        )
    ),
    (
        'blocked',
        pg_temp.agent_security_scope(
            'a1000000-0000-4000-8000-000000000021',
            'a2000000-0000-4000-8000-000000000021',
            'a3000000-0000-4000-8000-000000000021',
            'security:ledger:blocked',
            'CoinEasy/Content-Engine',
            'Agent/Security-Blocked',
            '["Independent/Blocked"]'::jsonb,
            'Durable agent ledger blocked fixture'
        )
    )
) as fixture(kind, scope);

grant select on table agent_terminal_scopes to authenticated;

-- auth.uid() is sourced from request.jwt.claim.sub.  A valid JWT-shaped claims
-- document alone must not impersonate an operator, and workspace membership is
-- checked for every write.
select pg_catalog.set_config(
    'request.jwt.claims',
    '{"role":"authenticated","sub":"af000000-0000-4000-8000-000000000001"}',
    true
);
select pg_catalog.set_config('request.jwt.claim.sub', '', true);
set local role authenticated;

do $test$
declare
    scope jsonb := pg_temp.agent_security_scope(
        'a1000000-0000-4000-8000-000000000001',
        'a2000000-0000-4000-8000-000000000001',
        'a3000000-0000-4000-8000-000000000001',
        'security:ledger:primary',
        'CoinEasy/Content-Engine',
        'Agent/Security-Primary',
        '["Core/Agent_Control"]'::jsonb,
        'Durable agent ledger primary fixture'
    );
begin
    begin
        perform public.propose_agent_work_order(
            'a0000000-0000-4000-8000-000000000001',
            scope,
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d'
        );
        raise exception 'claims JSON impersonated an operator without auth.uid';
    exception when insufficient_privilege then null;
    end;
end
$test$;

reset role;
select pg_catalog.set_config(
    'request.jwt.claim.sub',
    'af000000-0000-4000-8000-000000000002',
    true
);
set local role authenticated;

do $test$
declare
    scope jsonb := pg_temp.agent_security_scope(
        'a1000000-0000-4000-8000-000000000001',
        'a2000000-0000-4000-8000-000000000001',
        'a3000000-0000-4000-8000-000000000001',
        'security:ledger:primary',
        'CoinEasy/Content-Engine',
        'Agent/Security-Primary',
        '["Core/Agent_Control"]'::jsonb,
        'Durable agent ledger primary fixture'
    );
begin
    begin
        perform public.propose_agent_work_order(
            'a0000000-0000-4000-8000-000000000001',
            scope,
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d'
        );
        raise exception 'non-member proposed an Agent work order';
    exception when insufficient_privilege then null;
    end;
end
$test$;

reset role;
select pg_catalog.set_config(
    'request.jwt.claim.sub',
    'af000000-0000-4000-8000-000000000003',
    true
);
set local role authenticated;

do $test$
declare
    scope jsonb := pg_temp.agent_security_scope(
        'a1000000-0000-4000-8000-000000000001',
        'a2000000-0000-4000-8000-000000000001',
        'a3000000-0000-4000-8000-000000000001',
        'security:ledger:primary',
        'CoinEasy/Content-Engine',
        'Agent/Security-Primary',
        '["Core/Agent_Control"]'::jsonb,
        'Durable agent ledger primary fixture'
    );
begin
    begin
        perform public.propose_agent_work_order(
            'a0000000-0000-4000-8000-000000000001',
            scope,
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d'
        );
        raise exception 'viewer proposed an Agent work order';
    exception when insufficient_privilege then null;
    end;
end
$test$;

reset role;
select pg_catalog.set_config(
    'request.jwt.claim.sub',
    'af000000-0000-4000-8000-000000000001',
    true
);
select pg_catalog.set_config('request.jwt.claims', '{}', true);
set local role authenticated;

do $test$
declare
    scope jsonb := pg_temp.agent_security_scope(
        'a1000000-0000-4000-8000-000000000001',
        'a2000000-0000-4000-8000-000000000001',
        'a3000000-0000-4000-8000-000000000001',
        'security:ledger:primary',
        'CoinEasy/Content-Engine',
        'Agent/Security-Primary',
        '["Core/Agent_Control"]'::jsonb,
        'Durable agent ledger primary fixture'
    );
    conflict_scope jsonb;
    self_review_scope jsonb;
    result jsonb;
begin
    self_review_scope := pg_temp.agent_security_scope(
        'a1000000-0000-4000-8000-000000000009',
        'a2000000-0000-4000-8000-000000000009',
        'a3000000-0000-4000-8000-000000000009',
        'security:ledger:self-review',
        'CoinEasy/Content-Engine',
        'Agent/Security-Self-Review',
        '["Independent/SelfReview"]'::jsonb,
        'Durable agent ledger self review rejection',
        '2099-01-01T00:00:00Z',
        '2099-01-08T00:00:00Z',
        'codex',
        'codex'
    );
    begin
        perform public.propose_agent_work_order(
            'a0000000-0000-4000-8000-000000000001',
            self_review_scope,
            pg_catalog.repeat('0', 64)
        );
        raise exception 'same agent became owner and reviewer';
    exception when invalid_parameter_value then null;
    end;

    begin
        perform public.propose_agent_work_order(
            'a0000000-0000-4000-8000-000000000001',
            scope,
            pg_catalog.repeat('0', 64)
        );
        raise exception 'non-canonical scope hash was accepted';
    exception when check_violation then null;
    end;

    result := public.propose_agent_work_order(
        'a0000000-0000-4000-8000-000000000001',
        scope,
        '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d'
    );
    if result ->> 'reused' <> 'false'
       or result #>> '{work_order,status}' <> 'proposed'
       or result #>> '{work_order,automatic_publication}' <> 'false'
       or result #>> '{work_order,max_cost_microusd}' <> '0'
       or result #>> '{work_order,max_external_actions}' <> '0' then
        raise exception 'first Agent proposal response is unsafe';
    end if;

    result := public.propose_agent_work_order(
        'a0000000-0000-4000-8000-000000000001',
        scope,
        '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d'
    );
    if result ->> 'reused' <> 'true' then
        raise exception 'exact Agent proposal replay was not reused';
    end if;

    conflict_scope := scope || pg_catalog.jsonb_build_object(
        'title', 'Durable agent ledger conflicting replay'
    );
    begin
        perform public.propose_agent_work_order(
            'a0000000-0000-4000-8000-000000000001',
            conflict_scope,
            'be80ffb991038190ac992319253ceef7c48bf9409b4a20b528957167117f9264'
        );
        raise exception 'conflicting Agent proposal replay was reused';
    exception when unique_violation then null;
    end;

    result := public.authorize_agent_work_order(
        'a0000000-0000-4000-8000-000000000001',
        'a1000000-0000-4000-8000-000000000001',
        '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
        0
    );
    if result ->> 'reused' <> 'false'
       or result ->> 'dispatch_status' <> 'pending'
       or result #>> '{work_order,status}' <> 'authorized' then
        raise exception 'Agent authorization response is unsafe';
    end if;

    result := public.authorize_agent_work_order(
        'a0000000-0000-4000-8000-000000000001',
        'a1000000-0000-4000-8000-000000000001',
        '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
        0
    );
    if result ->> 'reused' <> 'true'
       or result ->> 'dispatch_status' <> 'pending' then
        raise exception 'exact Agent authorization replay duplicated state';
    end if;

    begin
        perform public.record_agent_operator_decision(
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000001',
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
            1,
            'approved',
            'security_verified'
        );
        raise exception 'operator approved before independent verification';
    exception when check_violation then null;
    end;
end
$test$;

do $test$
declare
    cancelled_scope jsonb;
    blocked_scope jsonb;
    cancelled_sha text;
    blocked_sha text;
    result jsonb;
begin
    select fixture.scope, fixture.scope_sha256
    into strict cancelled_scope, cancelled_sha
    from pg_temp.agent_terminal_scopes as fixture
    where fixture.kind = 'cancelled';
    perform public.propose_agent_work_order(
        'a0000000-0000-4000-8000-000000000001',
        cancelled_scope,
        cancelled_sha
    );
    result := public.record_agent_operator_decision(
        'a0000000-0000-4000-8000-000000000001',
        'a1000000-0000-4000-8000-000000000020',
        cancelled_sha,
        0,
        'cancelled',
        'operator_cancelled'
    );
    if result #>> '{work_order,status}' <> 'cancelled' then
        raise exception 'proposed cancellation lacked terminal decision proof';
    end if;

    select fixture.scope, fixture.scope_sha256
    into strict blocked_scope, blocked_sha
    from pg_temp.agent_terminal_scopes as fixture
    where fixture.kind = 'blocked';
    perform public.propose_agent_work_order(
        'a0000000-0000-4000-8000-000000000001',
        blocked_scope,
        blocked_sha
    );
    perform public.authorize_agent_work_order(
        'a0000000-0000-4000-8000-000000000001',
        'a1000000-0000-4000-8000-000000000021',
        blocked_sha,
        0
    );
    result := public.record_agent_operator_decision(
        'a0000000-0000-4000-8000-000000000001',
        'a1000000-0000-4000-8000-000000000021',
        blocked_sha,
        1,
        'blocked',
        'operator_blocked'
    );
    if result #>> '{work_order,status}' <> 'blocked'
       or public.get_agent_work_order(
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000021'
       ) ->> 'dispatch_status' <> 'cancelled' then
        raise exception 'authorized block did not cancel pending dispatch';
    end if;
end
$test$;

reset role;

do $test$
begin
    if (select count(*) from agent_runtime.agent_work_orders
        where workspace_id = 'a0000000-0000-4000-8000-000000000001'
          and work_order_id = 'a1000000-0000-4000-8000-000000000001') <> 1
       or (select count(*) from agent_runtime.agent_work_order_events
           where workspace_id = 'a0000000-0000-4000-8000-000000000001'
             and work_order_id = 'a1000000-0000-4000-8000-000000000001'
             and event_type = 'proposed') <> 1
       or (select count(*) from agent_runtime.agent_work_order_events
           where workspace_id = 'a0000000-0000-4000-8000-000000000001'
             and work_order_id = 'a1000000-0000-4000-8000-000000000001'
             and event_type = 'authorized') <> 1
       or (select count(*) from agent_runtime.agent_action_receipts
           where workspace_id = 'a0000000-0000-4000-8000-000000000001'
             and work_order_id = 'a1000000-0000-4000-8000-000000000001'
             and receipt_kind = 'authorization') <> 1
       or (select count(*) from agent_runtime.agent_dispatch_outbox
           where workspace_id = 'a0000000-0000-4000-8000-000000000001'
             and work_order_id = 'a1000000-0000-4000-8000-000000000001'
             and status = 'pending'
             and attempts = 0
             and request_sha256 is null
             and packet -> 'automatic_publication' = 'false'::jsonb
             and packet -> 'max_cost_microusd' = '0'::jsonb
             and packet -> 'max_external_actions' = '0'::jsonb) <> 1 then
        raise exception 'authorization was not exactly-one and atomic';
    end if;
end
$test$;

select pg_catalog.set_config(
    'request.jwt.claim.sub',
    'af000000-0000-4000-8000-000000000001',
    true
);
set local role authenticated;

do $test$
declare
    result jsonb;
begin
    result := public.propose_agent_work_order(
        'a0000000-0000-4000-8000-000000000001',
        pg_temp.agent_security_scope(
            'a1000000-0000-4000-8000-000000000002',
            'a2000000-0000-4000-8000-000000000002',
            'a3000000-0000-4000-8000-000000000002',
            'security:ledger:branch-conflict',
            'coineasy/content-engine',
            'agent/security-primary',
            '["Another/Path"]'::jsonb,
            'Durable agent ledger branch collision'
        ),
        '020804fe6486d7cd9b4b0ea92a09c9d5ea8450270456497a999dc69929080c96'
    );
    begin
        perform public.authorize_agent_work_order(
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000002',
            '020804fe6486d7cd9b4b0ea92a09c9d5ea8450270456497a999dc69929080c96',
            0
        );
        raise exception 'case-insensitive branch collision was authorized';
    exception when unique_violation then null;
    end;

    result := public.propose_agent_work_order(
        'a0000000-0000-4000-8000-000000000001',
        pg_temp.agent_security_scope(
            'a1000000-0000-4000-8000-000000000003',
            'a2000000-0000-4000-8000-000000000003',
            'a3000000-0000-4000-8000-000000000003',
            'security:ledger:path-conflict',
            'COINEASY/CONTENT-ENGINE',
            'Agent/Security-Path',
            '["core/agent_control/worker"]'::jsonb,
            'Durable agent ledger path collision'
        ),
        'f022106bcd27ed6387e468942cbc9f667fda63b5e0bb8a66898ed1f2d8ba710b'
    );
    begin
        perform public.authorize_agent_work_order(
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000003',
            'f022106bcd27ed6387e468942cbc9f667fda63b5e0bb8a66898ed1f2d8ba710b',
            0
        );
        raise exception 'case-insensitive nested path collision was authorized';
    exception when unique_violation then null;
    end;

    result := public.propose_agent_work_order(
        'a0000000-0000-4000-8000-000000000001',
        pg_temp.agent_security_scope(
            'a1000000-0000-4000-8000-000000000004',
            'a2000000-0000-4000-8000-000000000004',
            'a3000000-0000-4000-8000-000000000004',
            'security:ledger:cas-failure',
            'CoinEasy/Content-Engine',
            'Agent/Security-CAS',
            '["Independent/Cas"]'::jsonb,
            'Durable agent ledger CAS failure'
        ),
        'eca681a718b584bcf272d24bda8d3f7028e1f0e7badee128ad7a913b4a54e457'
    );
    begin
        perform public.authorize_agent_work_order(
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000004',
            'eca681a718b584bcf272d24bda8d3f7028e1f0e7badee128ad7a913b4a54e457',
            1
        );
        raise exception 'stale authorization CAS was accepted';
    exception when object_not_in_prerequisite_state then null;
    end;

    result := public.propose_agent_work_order(
        'a0000000-0000-4000-8000-000000000001',
        pg_temp.agent_security_scope(
            'a1000000-0000-4000-8000-000000000005',
            'a2000000-0000-4000-8000-000000000005',
            'a3000000-0000-4000-8000-000000000005',
            'security:ledger:expired',
            'CoinEasy/Content-Engine',
            'Agent/Security-Expired',
            '["Independent/Expired"]'::jsonb,
            'Durable agent ledger expired scope',
            '2000-01-01T00:00:00Z',
            '2000-01-02T00:00:00Z'
        ),
        '644986f79e2cbf36043451667d1d9863af2a3036f7be7eb1c4061194bbb8df33'
    );
    begin
        perform public.authorize_agent_work_order(
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000005',
            '644986f79e2cbf36043451667d1d9863af2a3036f7be7eb1c4061194bbb8df33',
            0
        );
        raise exception 'expired Agent authorization was accepted';
    exception when object_not_in_prerequisite_state then null;
    end;
end
$test$;

reset role;

do $test$
begin
    if exists (
        select 1
        from agent_runtime.agent_work_orders as work
        where work.workspace_id = 'a0000000-0000-4000-8000-000000000001'
          and work.work_order_id in (
              'a1000000-0000-4000-8000-000000000002',
              'a1000000-0000-4000-8000-000000000003',
              'a1000000-0000-4000-8000-000000000004',
              'a1000000-0000-4000-8000-000000000005'
          )
          and work.status <> 'proposed'
    ) or exists (
        select 1
        from agent_runtime.agent_action_receipts as receipt
        where receipt.workspace_id = 'a0000000-0000-4000-8000-000000000001'
          and receipt.work_order_id in (
              'a1000000-0000-4000-8000-000000000002',
              'a1000000-0000-4000-8000-000000000003',
              'a1000000-0000-4000-8000-000000000004',
              'a1000000-0000-4000-8000-000000000005'
          )
    ) or exists (
        select 1
        from agent_runtime.agent_dispatch_outbox as dispatch
        where dispatch.workspace_id = 'a0000000-0000-4000-8000-000000000001'
          and dispatch.work_order_id in (
              'a1000000-0000-4000-8000-000000000002',
              'a1000000-0000-4000-8000-000000000003',
              'a1000000-0000-4000-8000-000000000004',
              'a1000000-0000-4000-8000-000000000005'
          )
    ) then
        raise exception 'failed conflict/expiry/CAS authorization leaked state';
    end if;

    -- DB-owner-only fixture: the shipped release has no result or verification
    -- RPC.  First emulate only the verified state to prove receipts, not merely
    -- a status label, are required for operator approval.
    update agent_runtime.agent_work_orders
    set status = 'verified', status_version = 4,
        updated_at = statement_timestamp()
    where workspace_id = 'a0000000-0000-4000-8000-000000000001'
      and work_order_id = 'a1000000-0000-4000-8000-000000000001';
end
$test$;

select pg_catalog.set_config(
    'request.jwt.claim.sub',
    'af000000-0000-4000-8000-000000000001',
    true
);
set local role authenticated;

do $test$
begin
    begin
        perform public.record_agent_operator_decision(
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000001',
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
            4,
            'approved',
            'security_verified'
        );
        raise exception 'verified label bypassed the receipt-chain gate';
    exception when check_violation then null;
    end;
    begin
        perform public.complete_agent_work_order(
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000001',
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
            4
        );
        raise exception 'work order completed without operator approval';
    exception when object_not_in_prerequisite_state then null;
    end;
end
$test$;

reset role;

do $fixture$
declare
    result_payload jsonb;
    verification_payload jsonb;
    wrong_result_payload jsonb;
begin
    -- DB-owner-only future-adapter fixture.  No provider or external call is
    -- made; one cost remains NULL so the dashboard must report it as unknown.
    insert into agent_runtime.agent_runs (
        workspace_id, run_id, work_order_id, run_kind, agent_identity,
        status, attempt, locked_by, claimed_at, lease_expires_at,
        started_at, finished_at, result_sha256, actual_cost_microusd,
        external_action_count
    ) values
        (
            'a0000000-0000-4000-8000-000000000001',
            'a4000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000001',
            'owner', 'devin', 'result_submitted', 1,
            'security-owner-fixture',
            statement_timestamp() - interval '3 minutes',
            statement_timestamp() + interval '10 minutes',
            statement_timestamp() - interval '2 minutes',
            statement_timestamp() - interval '1 minute',
            pg_catalog.repeat('b', 64), null, 0
        ),
        (
            'a0000000-0000-4000-8000-000000000001',
            'a4000000-0000-4000-8000-000000000002',
            'a1000000-0000-4000-8000-000000000001',
            'review', 'codex', 'verification_submitted', 1,
            'security-review-fixture',
            statement_timestamp() - interval '3 minutes',
            statement_timestamp() + interval '10 minutes',
            statement_timestamp() - interval '2 minutes',
            statement_timestamp() - interval '1 minute',
            pg_catalog.repeat('b', 64), 0, 0
        );

    result_payload := pg_catalog.jsonb_build_object(
        'actual_cost_microusd', null,
        'automatic_publication', false,
        'external_action_count', 0,
        'result_sha256', pg_catalog.repeat('b', 64),
        'schema_version', 'agent-work-result@1',
        'scope_sha256',
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
        'work_order_id', 'a1000000-0000-4000-8000-000000000001'
    );
    verification_payload := pg_catalog.jsonb_build_object(
        'automatic_publication', false,
        'independent_reviewer', 'codex',
        'passed', true,
        'result_sha256', pg_catalog.repeat('b', 64),
        'schema_version', 'agent-verification-receipt@1',
        'scope_sha256',
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
        'verification_sha256', pg_catalog.repeat('c', 64),
        'work_order_id', 'a1000000-0000-4000-8000-000000000001'
    );
    wrong_result_payload := pg_catalog.jsonb_set(
        result_payload,
        '{scope_sha256}',
        pg_catalog.to_jsonb(pg_catalog.repeat('d', 64))
    );
    begin
        insert into agent_runtime.agent_action_receipts (
            workspace_id, work_order_id, run_id, receipt_kind,
            schema_version, actor_kind, payload, payload_sha256,
            scope_sha256, result_sha256
        ) values (
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000001',
            'a4000000-0000-4000-8000-000000000001',
            'work_result', 'agent-work-result@1', 'devin',
            wrong_result_payload,
            private.agent_json_sha256(wrong_result_payload),
            pg_catalog.repeat('d', 64), pg_catalog.repeat('b', 64)
        );
        insert into agent_runtime.agent_action_receipts (
            workspace_id, work_order_id, run_id, receipt_kind,
            schema_version, actor_kind, payload, payload_sha256,
            scope_sha256, result_sha256, verification_sha256
        ) values (
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000001',
            'a4000000-0000-4000-8000-000000000002',
            'verification', 'agent-verification-receipt@1', 'codex',
            verification_payload,
            private.agent_json_sha256(verification_payload),
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
            pg_catalog.repeat('b', 64), pg_catalog.repeat('c', 64)
        );
        perform public.record_agent_operator_decision(
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000001',
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
            4,
            'approved',
            'security_verified'
        );
        raise exception 'mismatched result scope was approved';
    exception when check_violation then null;
    end;
    insert into agent_runtime.agent_action_receipts (
        workspace_id, work_order_id, run_id, receipt_kind, schema_version,
        actor_kind, payload, payload_sha256, scope_sha256, result_sha256
    ) values (
        'a0000000-0000-4000-8000-000000000001',
        'a1000000-0000-4000-8000-000000000001',
        'a4000000-0000-4000-8000-000000000001',
        'work_result', 'agent-work-result@1', 'devin', result_payload,
        private.agent_json_sha256(result_payload),
        '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
        pg_catalog.repeat('b', 64)
    );
    begin
        insert into agent_runtime.agent_action_receipts (
            workspace_id, work_order_id, run_id, receipt_kind,
            schema_version, actor_kind, payload, payload_sha256,
            scope_sha256, result_sha256, verification_sha256
        ) values (
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000001',
            'a4000000-0000-4000-8000-000000000002',
            'verification', 'agent-verification-receipt@1', 'codex',
            pg_catalog.jsonb_set(
                verification_payload, '{passed}', 'false'::jsonb
            ),
            private.agent_json_sha256(pg_catalog.jsonb_set(
                verification_payload, '{passed}', 'false'::jsonb
            )),
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
            pg_catalog.repeat('b', 64), pg_catalog.repeat('c', 64)
        );
        raise exception 'failed verification receipt was accepted';
    exception when check_violation then null;
    end;
    begin
        insert into agent_runtime.agent_action_receipts (
            workspace_id, work_order_id, run_id, receipt_kind,
            schema_version, actor_kind, payload, payload_sha256,
            scope_sha256, result_sha256, verification_sha256
        ) values (
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000001',
            'a4000000-0000-4000-8000-000000000002',
            'verification', 'agent-verification-receipt@1', 'claude_code',
            verification_payload,
            private.agent_json_sha256(verification_payload),
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
            pg_catalog.repeat('b', 64), pg_catalog.repeat('c', 64)
        );
        perform public.record_agent_operator_decision(
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000001',
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
            4,
            'approved',
            'security_verified'
        );
        raise exception 'non-independent reviewer actor was approved';
    exception when check_violation then null;
    end;
    insert into agent_runtime.agent_action_receipts (
        workspace_id, work_order_id, run_id, receipt_kind, schema_version,
        actor_kind, payload, payload_sha256, scope_sha256, result_sha256,
        verification_sha256
    ) values (
        'a0000000-0000-4000-8000-000000000001',
        'a1000000-0000-4000-8000-000000000001',
        'a4000000-0000-4000-8000-000000000002',
        'verification', 'agent-verification-receipt@1', 'codex',
        verification_payload, private.agent_json_sha256(verification_payload),
        '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
        pg_catalog.repeat('b', 64), pg_catalog.repeat('c', 64)
    );
end
$fixture$;

select pg_catalog.set_config(
    'request.jwt.claim.sub',
    'af000000-0000-4000-8000-000000000001',
    true
);
set local role authenticated;

do $test$
declare
    result jsonb;
begin
    result := public.record_agent_operator_decision(
        'a0000000-0000-4000-8000-000000000001',
        'a1000000-0000-4000-8000-000000000001',
        '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
        4,
        'approved',
        'security_verified'
    );
    if result ->> 'reused' <> 'false'
       or result #>> '{work_order,status}' <> 'approved' then
        raise exception 'receipt-bound operator approval failed';
    end if;

    begin
        perform public.complete_agent_work_order(
            'a0000000-0000-4000-8000-000000000001',
            'a1000000-0000-4000-8000-000000000001',
            '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
            4
        );
        raise exception 'stale completion CAS was accepted';
    exception when object_not_in_prerequisite_state then null;
    end;

    result := public.complete_agent_work_order(
        'a0000000-0000-4000-8000-000000000001',
        'a1000000-0000-4000-8000-000000000001',
        '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
        5
    );
    if result ->> 'reused' <> 'false'
       or result #>> '{work_order,status}' <> 'completed' then
        raise exception 'receipt-bound completion failed';
    end if;

    result := public.complete_agent_work_order(
        'a0000000-0000-4000-8000-000000000001',
        'a1000000-0000-4000-8000-000000000001',
        '3ca4df5a2d7c8f2ee145a473094bfa8e86510dd3bc0342cac12f643d44ab624d',
        5
    );
    if result ->> 'reused' <> 'true' then
        raise exception 'completion replay duplicated its receipt';
    end if;
end
$test$;

reset role;
select pg_catalog.set_config('request.jwt.claim.sub', '', true);
select pg_catalog.set_config(
    'request.jwt.claims',
    '{"role":"coineasy_agent_dashboard","workspace_id":"a0000000-0000-4000-8000-000000000099"}',
    true
);
set local role coineasy_agent_dashboard;

do $test$
begin
    begin
        perform public.get_agent_company_dashboard(
            'a0000000-0000-4000-8000-000000000001'
        );
        raise exception 'dashboard role crossed the workspace claim boundary';
    exception when insufficient_privilege then null;
    end;
end
$test$;

reset role;
select pg_catalog.set_config(
    'request.jwt.claims',
    '{"role":"coineasy_agent_dashboard","workspace_id":"a0000000-0000-4000-8000-000000000001"}',
    true
);
set local role coineasy_agent_dashboard;

do $test$
declare
    dashboard jsonb;
begin
    dashboard := public.get_agent_company_dashboard(
        'a0000000-0000-4000-8000-000000000001'
    );
    if dashboard ->> 'actual_cost_microusd' <> '0'
       or dashboard ->> 'unobserved_run_count' <> '1'
       or dashboard ->> 'cost_observation_complete' <> 'false'
       or dashboard ->> 'max_external_actions' <> '0'
       or dashboard ->> 'automatic_publication' <> 'false' then
        raise exception 'dashboard collapsed unknown cost into observed zero';
    end if;
end
$test$;

reset role;

do $test$
declare
    baseline agent_security_side_effect_baseline%rowtype;
begin
    select * into strict baseline from agent_security_side_effect_baseline;

    if (select count(*) from agent_runtime.agent_action_receipts
        where workspace_id = 'a0000000-0000-4000-8000-000000000001'
          and work_order_id = 'a1000000-0000-4000-8000-000000000001'
          and receipt_kind = 'operator_decision') <> 1
       or (select count(*) from agent_runtime.agent_action_receipts
           where workspace_id = 'a0000000-0000-4000-8000-000000000001'
             and work_order_id = 'a1000000-0000-4000-8000-000000000001'
             and receipt_kind = 'completion'
             and payload -> 'automatic_publication' = 'false'::jsonb
             and payload -> 'max_cost_microusd' = '0'::jsonb
             and payload -> 'max_external_actions' = '0'::jsonb) <> 1
       or exists (
            select 1 from agent_runtime.agent_runs
            where workspace_id = 'a0000000-0000-4000-8000-000000000001'
              and (coalesce(actual_cost_microusd, 0) <> 0
                   or external_action_count <> 0)
       ) then
        raise exception 'decision/completion escaped zero-authority bounds';
    end if;

    if baseline.approval_count <> (select count(*) from public.approvals)
       or baseline.publication_count
            <> (select count(*) from public.publications)
       or baseline.batch_job_count
            <> (select count(*) from agent_runtime.batch_jobs)
       or baseline.batch_run_count
            <> (select count(*) from agent_runtime.batch_runs)
       or baseline.buzz_delivery_count
            <> (select count(*) from agent_runtime.buzz_delivery_receipts)
       or baseline.buzz_decision_count
            <> (select count(*) from agent_runtime.buzz_review_decisions)
       or baseline.buzz_ack_count
            <> (select count(*) from agent_runtime.buzz_review_ack_receipts)
       or baseline.grok_outbox_count
            <> (select count(*) from private.grok_qa_dispatch_outbox)
       or baseline.grok_receipt_count
            <> (select count(*) from private.grok_qa_verdict_receipts) then
        raise exception 'Agent ledger changed approval/publication/Batch/Buzz state';
    end if;
end
$test$;

rollback;
