-- Parameterized resolve session. The first caller holds its transaction open
-- after resolving so the second caller must wait on the same forensic tuple.

\set ON_ERROR_STOP on
set timezone = 'UTC';

select
    workspace_id,
    content_item_id,
    content_version_id,
    publication_id,
    job_id,
    resolution_id,
    operator_approval_id,
    resolved_by,
    expires_at,
    release_sha,
    public_audit::text as public_audit,
    public_audit_sha256,
    approval_subject_sha256
from exact_telegram_resolution_concurrency.fixture
where singleton
\gset target_

begin;
select pg_catalog.set_config(
    'application_name', 'exact_telegram_resolution_' || :'session_name', true
) as application_name_set
\gset
set local role coineasy_telegram_resolution;
select pg_catalog.set_config(
    'request.jwt.claims',
    pg_catalog.jsonb_build_object(
        'role', 'coineasy_telegram_resolution',
        'workspace_id', :'target_workspace_id',
        'sub', :'target_resolved_by',
        'capability', 'telegram_delivery_unknown_resolve',
        'environment', 'production',
        'release_sha', :'target_release_sha',
        'automatic_publication', false,
        'resend_authorized', false,
        'max_external_actions', 0,
        'jti', :'target_resolution_id',
        'content_item_id', :'target_content_item_id',
        'content_version_id', :'target_content_version_id',
        'publication_id', :'target_publication_id',
        'job_id', :'target_job_id',
        'resolution_id', :'target_resolution_id',
        'operator_approval_id', :'target_operator_approval_id',
        'approval_subject_sha256', :'target_approval_subject_sha256',
        'approved_by', 'codex:telegram-concurrency-approve',
        'expires_at', :'target_expires_at',
        'public_audit_sha256', :'target_public_audit_sha256'
    )::text,
    true
) as resolve_claims_set
\gset

select public.resolve_exact_telegram_delivery_unknown_without_resend(
    :'target_workspace_id'::uuid,
    :'target_content_item_id'::uuid,
    :'target_content_version_id'::uuid,
    :'target_publication_id'::uuid,
    :'target_job_id'::uuid,
    :'target_resolution_id'::uuid,
    :'target_operator_approval_id'::uuid,
    :'target_resolved_by',
    :'target_release_sha',
    :'target_public_audit'::jsonb,
    :'target_approval_subject_sha256'
)::text as resolve_result
\gset

-- The RPC ran under the real dedicated role. Only the test barrier below
-- returns to the disposable database owner to inspect both session states.
reset role;
select pg_catalog.pg_advisory_lock(20260831, 170)
where :'hold_lock'::boolean;
\if :hold_lock
do $wait_for_competing_resolve$
declare
    attempt integer;
begin
    for attempt in 1..200 loop
        if exists (
            select 1
            from pg_catalog.pg_stat_activity as blocked
            where blocked.datname = pg_catalog.current_database()
              and blocked.application_name =
                'exact_telegram_resolution_second'
              and blocked.wait_event_type = 'Lock'
              and pg_catalog.pg_backend_pid() = any (
                  pg_catalog.pg_blocking_pids(blocked.pid)
              )
        ) then
            update exact_telegram_resolution_concurrency.fixture
            set second_waited_for_first = true
            where singleton;
            return;
        end if;
        perform pg_catalog.pg_sleep(0.05);
        -- Stats views can cache a snapshot within this transaction.
        perform pg_catalog.pg_stat_clear_snapshot();
    end loop;
    raise exception 'second resolve never waited on the first transaction';
end
$wait_for_competing_resolve$;
\endif
select pg_catalog.pg_advisory_unlock(20260831, 170)
where :'hold_lock'::boolean;
commit;

insert into exact_telegram_resolution_concurrency.results (
    session_name, result
) values (
    :'session_name', :'resolve_result'::jsonb
);
