-- Least-privilege PostgREST roles for the agent Batch ledger.
--
-- Additive only. This migration creates three NOLOGIN roles, grants each
-- EXECUTE on exactly the routines its component provably calls, and grants the
-- roles to `authenticator` so PostgREST can switch into them from a JWT `role`
-- claim. It revokes nothing and changes no existing privilege, so applying it
-- alters no behavior. Adoption happens later, per component, by swapping a
-- credential value. See docs/ADR-007-least-privilege-ledger-credentials.md.
--
-- Deliberately NOT granted to any of these roles:
--   * any privilege on `agent_runtime` (direct access is revoked even from
--     service_role; the SECURITY DEFINER routines are the only path)
--   * any table privilege in `public`
--   * BYPASSRLS
--   * membership in service_role

begin;

do $roles$
declare
    dispatcher_routines constant text[] := array[
        'public.configure_agent_batch_budget(uuid,text,timestamp with time zone,timestamp with time zone,bigint)',
        'public.claim_agent_batch_jobs(uuid,text,text[],integer,integer)',
        'public.expire_agent_batch_jobs(uuid,text[])',
        'public.register_agent_batch(uuid,text,text,text,uuid[])',
        'public.list_active_agent_batches(uuid,integer)',
        'public.update_agent_batch_poll(uuid,text,text,text,text)',
        'public.complete_agent_batch_job(uuid,uuid,text,text,jsonb,bigint,bigint,bigint)',
        'public.fail_agent_batch_job(uuid,uuid,text,text,boolean,timestamp with time zone,bigint,bigint,bigint,boolean)',
        'public.finalize_agent_batch(uuid,text)'
    ];
    -- The producer keeps a single role: daily_runner requires its automation and
    -- batch credentials to be byte-identical, so these cannot be split without a
    -- code change (see ADR-007 "Consequences").
    producer_routines constant text[] := array[
        'public.get_official_x_automation_state(uuid,text,date,integer)',
        'public.record_content_signal_ranking_evidence(uuid,text,text,text,timestamp with time zone,timestamp with time zone,timestamp with time zone,text,jsonb)',
        'public.record_content_learning_evidence(uuid,text,text,text,timestamp with time zone,timestamp with time zone,timestamp with time zone,text,jsonb)',
        'public.record_content_promotion_candidates(uuid,text,text,text,timestamp with time zone,timestamp with time zone,timestamp with time zone,text,jsonb)',
        -- name resolved at runtime by client; both are required
        'public.record_origintrail_nonquote_sources(uuid,text,text,uuid,text,text,jsonb,timestamp with time zone)',
        'public.record_official_x_sources(uuid,text,text,uuid,text,text,jsonb,timestamp with time zone)',
        'public.queue_review_draft_job(uuid,text,date,jsonb,text,uuid,text,text,text,boolean)',
        'public.get_or_create_official_x_style_reference_pack(uuid,text,uuid,uuid,integer)',
        'public.claim_review_draft_job(uuid,text,integer)',
        'public.bind_review_draft_execution_plane(uuid,text,text)',
        'public.complete_review_draft_job(uuid,text,uuid,uuid)',
        'public.complete_review_draft_batch_handoff(uuid,text,uuid,text)',
        'public.recover_review_draft_batch_handoff(uuid,text)',
        'public.fail_review_draft_job(uuid,text,text,text,boolean,timestamp with time zone)',
        -- ledger routines reached through BatchQueueBridge
        'public.configure_agent_batch_budget(uuid,text,timestamp with time zone,timestamp with time zone,bigint)',
        'public.queue_agent_batch_job(uuid,text,uuid,text,text,text,text,smallint,text,text,text,timestamp with time zone,jsonb,text,bigint,integer,bigint,text,text,boolean)'
    ];
    -- Read-only. Defined here but NOT adopted: the Netlify site shares one
    -- credential across non-Batch functions that need more. See ADR-007.
    reviewer_routines constant text[] := array[
        'public.list_agent_batch_review_inbox(uuid,integer,timestamp with time zone,uuid)',
        'public.get_agent_batch_review_item(uuid,uuid)'
    ];
    role_name text;
    routine text;
    routines text[];
begin
    foreach role_name in array array[
        'coineasy_batch_dispatcher',
        'coineasy_batch_producer',
        'coineasy_batch_reviewer'
    ]
    loop
        if not exists (select 1 from pg_roles where rolname = role_name) then
            execute format('create role %I nologin noinherit', role_name);
        end if;

        -- Never let these roles inherit the broad grants of the built-ins.
        execute format('alter role %I nologin noinherit nobypassrls', role_name);
        execute format('grant usage on schema public to %I', role_name);
        -- authenticator must be able to `set role` into it for PostgREST.
        execute format('grant %I to authenticator', role_name);
    end loop;

    foreach role_name in array array[
        'coineasy_batch_dispatcher',
        'coineasy_batch_producer',
        'coineasy_batch_reviewer'
    ]
    loop
        routines := case role_name
            when 'coineasy_batch_dispatcher' then dispatcher_routines
            when 'coineasy_batch_producer' then producer_routines
            else reviewer_routines
        end;

        foreach routine in array routines
        loop
            -- Fail loudly rather than silently granting nothing if a signature
            -- drifts from the migration that created it.
            if to_regprocedure(routine) is null then
                raise exception
                    'least-privilege grant target does not exist: %', routine;
            end if;
            execute format(
                'grant execute on function %s to %I', routine, role_name
            );
        end loop;
    end loop;
end;
$roles$;

commit;
