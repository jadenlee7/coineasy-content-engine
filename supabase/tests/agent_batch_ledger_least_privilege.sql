-- Transactional assertion suite for the least-privilege ledger roles created by
-- 20260805090000_least_privilege_ledger_roles.sql. Run as the database owner
-- after that migration. Everything rolls back; no row is written.
--
-- What this proves:
--   1. each role can execute exactly its expected routine set, no more
--   2. no role holds any table privilege in `public`
--   3. no role holds any privilege on `agent_runtime`
--   4. no role is a member of service_role and none has BYPASSRLS
--   5. the dispatcher specifically cannot queue work or reach the review-draft
--      family or the review inbox -- the separation ADR-007 exists to buy

begin;

do $test$
declare
    expected constant jsonb := jsonb_build_object(
        'coineasy_batch_dispatcher', jsonb_build_array(
            'configure_agent_batch_budget',
            'claim_agent_batch_jobs',
            'expire_agent_batch_jobs',
            'register_agent_batch',
            'list_active_agent_batches',
            'update_agent_batch_poll',
            'complete_agent_batch_job',
            'fail_agent_batch_job',
            'finalize_agent_batch',
            'configure_origintrail_batch_canary_grant',
            'peek_origintrail_batch_shadow_candidate',
            'configure_origintrail_batch_shadow_day',
            'claim_origintrail_batch_canary_job',
            'authorize_origintrail_batch_provider_create',
            'register_origintrail_batch_provider_create'
        ),
        'coineasy_batch_producer', jsonb_build_array(
            'get_official_x_automation_state',
            'record_content_signal_ranking_evidence',
            'record_content_learning_evidence',
            'record_content_promotion_candidates',
            'record_origintrail_nonquote_sources',
            'record_official_x_sources',
            'queue_review_draft_job',
            'get_or_create_official_x_style_reference_pack',
            'claim_review_draft_job',
            'bind_review_draft_execution_plane',
            'complete_review_draft_job',
            'complete_review_draft_batch_handoff',
            'recover_review_draft_batch_handoff',
            'fail_review_draft_job',
            'get_origintrail_reviewed_source_evidence',
            'configure_agent_batch_budget',
            'queue_agent_batch_job'
        ),
        'coineasy_batch_reviewer', jsonb_build_array(
            'list_agent_batch_review_inbox',
            'get_agent_batch_review_item'
        ),
        'coineasy_buzz_delivery', jsonb_build_array(
            'claim_origintrail_buzz_delivery',
            'mark_origintrail_buzz_delivery_attempt',
            'complete_origintrail_buzz_delivery',
            'fail_origintrail_buzz_delivery',
            'reconcile_origintrail_buzz_delivery_leases'
        )
    );
    role_name text;
    want text[];
    got text[];
    missing text[];
    extra text[];
    leaked text;
begin
    for role_name in select jsonb_object_keys(expected)
    loop
        if not exists (select 1 from pg_roles where rolname = role_name) then
            raise exception 'role % does not exist', role_name;
        end if;

        -- (4) no privilege escalation through role membership or BYPASSRLS
        if exists (
            select 1
            from pg_auth_members m
            join pg_roles child on child.oid = m.member
            join pg_roles parent on parent.oid = m.roleid
            where child.rolname = role_name
              and parent.rolname in (
                  'service_role', 'postgres', 'supabase_admin', 'authenticated'
              )
        ) then
            raise exception '% must not be a member of a privileged role', role_name;
        end if;

        if exists (
            select 1 from pg_roles
            where rolname = role_name and (rolbypassrls or rolcanlogin)
        ) then
            raise exception '% must be nologin and nobypassrls', role_name;
        end if;

        -- authenticator must be able to switch into it, or PostgREST cannot
        -- use the token at all.
        if not exists (
            select 1
            from pg_auth_members m
            join pg_roles child on child.oid = m.member
            join pg_roles parent on parent.oid = m.roleid
            where parent.rolname = role_name and child.rolname = 'authenticator'
        ) then
            raise exception '% must be granted to authenticator', role_name;
        end if;

        -- (1) exact routine set
        select array_agg(value order by value)
          into want
          from jsonb_array_elements_text(expected -> role_name);

        select coalesce(array_agg(distinct p.proname order by p.proname), '{}')
          into got
          from pg_proc p
          join pg_namespace n on n.oid = p.pronamespace
         where n.nspname = 'public'
           and has_function_privilege(role_name, p.oid, 'EXECUTE')
           -- has_function_privilege() is true for everything when PUBLIC holds
           -- EXECUTE, so compare only routines this project actually restricts.
           and p.proacl is not null;

        select coalesce(array_agg(x), '{}') into missing
          from unnest(want) as x where x <> all(got);
        select coalesce(array_agg(x), '{}') into extra
          from unnest(got) as x where x <> all(want);

        if array_length(missing, 1) is not null then
            raise exception '% is missing EXECUTE on: %', role_name, missing;
        end if;
        if array_length(extra, 1) is not null then
            raise exception '% holds unexpected EXECUTE on: %', role_name, extra;
        end if;

        -- (2) no table privilege anywhere in public
        select string_agg(distinct table_name, ', ')
          into leaked
          from information_schema.role_table_grants
         where grantee = role_name and table_schema = 'public';
        if leaked is not null then
            raise exception '% must hold no public table grant, has: %',
                role_name, leaked;
        end if;

        -- (3) agent_runtime stays reachable only through SECURITY DEFINER
        if has_schema_privilege(role_name, 'agent_runtime', 'USAGE') then
            raise exception '% must not have USAGE on agent_runtime', role_name;
        end if;

        select string_agg(distinct table_name, ', ')
          into leaked
          from information_schema.role_table_grants
         where grantee = role_name and table_schema = 'agent_runtime';
        if leaked is not null then
            raise exception '% must hold no agent_runtime grant, has: %',
                role_name, leaked;
        end if;
    end loop;

    -- (5) the specific separation this ADR buys, asserted directly rather than
    -- inferred from the set comparison above.
    foreach leaked in array array[
        'public.queue_agent_batch_job(uuid,text,uuid,text,text,text,text,smallint,text,text,text,timestamp with time zone,jsonb,text,bigint,integer,bigint,text,text,boolean)',
        'public.bind_review_draft_execution_plane(uuid,text,text)',
        'public.complete_review_draft_batch_handoff(uuid,text,uuid,text)',
        'public.recover_review_draft_batch_handoff(uuid,text)',
        'public.claim_review_draft_job(uuid,text,integer)',
        'public.record_origintrail_nonquote_sources(uuid,text,text,uuid,text,text,jsonb,timestamp with time zone)',
        'public.list_agent_batch_review_inbox(uuid,integer,timestamp with time zone,uuid)',
        'public.get_agent_batch_review_item(uuid,uuid)'
    ]
    loop
        if to_regprocedure(leaked) is null then
            raise exception 'assertion target does not exist: %', leaked;
        end if;
        if has_function_privilege(
            'coineasy_batch_dispatcher', to_regprocedure(leaked), 'EXECUTE'
        ) then
            raise exception
                'dispatcher must not be able to execute %', leaked;
        end if;
    end loop;

    -- The reviewer role is read-only: it must not reach any write routine.
    foreach leaked in array array[
        'public.queue_agent_batch_job(uuid,text,uuid,text,text,text,text,smallint,text,text,text,timestamp with time zone,jsonb,text,bigint,integer,bigint,text,text,boolean)',
        'public.complete_agent_batch_job(uuid,uuid,text,text,jsonb,bigint,bigint,bigint)',
        'public.claim_agent_batch_jobs(uuid,text,text[],integer,integer)',
        'public.configure_agent_batch_budget(uuid,text,timestamp with time zone,timestamp with time zone,bigint)',
        'public.claim_origintrail_batch_canary_job(uuid,text,text,uuid,text,uuid,uuid,text,text,timestamp with time zone,bigint,integer,integer)',
        'public.register_origintrail_batch_provider_create(uuid,text,uuid,text,uuid,text,text,text,text,text,text)',
        'public.claim_origintrail_buzz_delivery(uuid,uuid,text,uuid,text,text,text,integer)',
        'public.complete_origintrail_buzz_delivery(uuid,text,text,text,text)'
    ]
    loop
        if has_function_privilege(
            'coineasy_batch_reviewer', to_regprocedure(leaked), 'EXECUTE'
        ) then
            raise exception 'reviewer must stay read-only, but can execute %',
                leaked;
        end if;
    end loop;

    -- The Buzz delivery role owns receipt transitions and nothing else: no
    -- Batch ledger claim, no queueing, and no review-inbox read.
    foreach leaked in array array[
        'public.claim_agent_batch_jobs(uuid,text,text[],integer,integer)',
        'public.queue_agent_batch_job(uuid,text,uuid,text,text,text,text,smallint,text,text,text,timestamp with time zone,jsonb,text,bigint,integer,bigint,text,text,boolean)',
        'public.list_agent_batch_review_inbox(uuid,integer,timestamp with time zone,uuid)',
        'public.get_agent_batch_review_item(uuid,uuid)'
    ]
    loop
        if has_function_privilege(
            'coineasy_buzz_delivery', to_regprocedure(leaked), 'EXECUTE'
        ) then
            raise exception
                'buzz delivery role must stay receipt-only, but can execute %',
                leaked;
        end if;
    end loop;
end;
$test$;

rollback;
