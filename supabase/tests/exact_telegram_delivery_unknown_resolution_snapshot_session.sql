-- The snapshot predates a normal resolution committed by another connection.
-- The service role must not bypass forensic freeze through that stale view,
-- and no phase-specific resolution JWT may operate at this isolation level.
\set ON_ERROR_STOP on
set timezone = 'UTC';
begin isolation level :isolation;

select pg_catalog.set_config(
    'test.telegram_resolution_snapshot_fixture',
    pg_catalog.to_jsonb(fixture)::text,
    true
) as snapshot_fixture_set
from exact_telegram_resolution_concurrency.fixture as fixture
where singleton
\gset

do $snapshot_before_resolution$
declare
    target jsonb := pg_catalog.current_setting(
        'test.telegram_resolution_snapshot_fixture'
    )::jsonb;
begin
    if exists (
        select 1
        from private.exact_telegram_delivery_unknown_resolutions
        where workspace_id = (target ->> 'workspace_id')::uuid
          and resolution_id = (target ->> 'resolution_id')::uuid
    ) then
        raise exception 'snapshot fixture was already resolved';
    end if;
end
$snapshot_before_resolution$;

select pg_catalog.pg_advisory_lock(20260831, 171);
do $wait_for_committed_resolution_signal$
declare
    attempt integer;
begin
    for attempt in 1..200 loop
        if not pg_catalog.pg_try_advisory_lock(20260831, 172) then
            return;
        end if;
        perform pg_catalog.pg_advisory_unlock(20260831, 172);
        perform pg_catalog.pg_sleep(0.05);
    end loop;
    raise exception 'normal resolve did not commit before stale-snapshot checks';
end
$wait_for_committed_resolution_signal$;

-- Deliberately prove the receipt is invisible in this transaction even though
-- the other session committed it. This is the condition that broke the old
-- receipt-dependent BEFORE UPDATE trigger.
do $verify_snapshot_stays_stale$
declare
    target jsonb := pg_catalog.current_setting(
        'test.telegram_resolution_snapshot_fixture'
    )::jsonb;
begin
    if exists (
        select 1
        from private.exact_telegram_delivery_unknown_resolutions
        where workspace_id = (target ->> 'workspace_id')::uuid
          and resolution_id = (target ->> 'resolution_id')::uuid
    ) then
        raise exception 'snapshot refreshed unexpectedly';
    end if;
end
$verify_snapshot_stays_stale$;

set local role service_role;
do $stale_original_mutations_fail_closed$
declare
    target jsonb := pg_catalog.current_setting(
        'test.telegram_resolution_snapshot_fixture'
    )::jsonb;
begin
    begin
        update public.jobs
        set last_error_message = 'snapshot mutation must never commit'
        where workspace_id = (target ->> 'workspace_id')::uuid
          and id = (target ->> 'job_id')::uuid;
        raise exception 'stale snapshot unexpectedly updated original job';
    exception when sqlstate '25001' then
        if sqlerrm <> 'exact Telegram unknown-row mutation requires READ COMMITTED' then
            raise;
        end if;
    end;
    begin
        update public.publications
        set last_error = 'snapshot mutation must never commit'
        where workspace_id = (target ->> 'workspace_id')::uuid
          and id = (target ->> 'publication_id')::uuid;
        raise exception 'stale snapshot unexpectedly updated original publication';
    exception when sqlstate '25001' then
        if sqlerrm <> 'exact Telegram unknown-row mutation requires READ COMMITTED' then
            raise;
        end if;
    end;
    begin
        delete from public.jobs
        where workspace_id = (target ->> 'workspace_id')::uuid
          and id = (target ->> 'job_id')::uuid;
        raise exception 'stale snapshot unexpectedly deleted original job';
    exception when sqlstate '25001' then
        if sqlerrm <> 'exact Telegram unknown-row mutation requires READ COMMITTED' then
            raise;
        end if;
    end;
    begin
        delete from public.publications
        where workspace_id = (target ->> 'workspace_id')::uuid
          and id = (target ->> 'publication_id')::uuid;
        raise exception 'stale snapshot unexpectedly deleted original publication';
    exception when sqlstate '25001' then
        if sqlerrm <> 'exact Telegram unknown-row mutation requires READ COMMITTED' then
            raise;
        end if;
    end;
end
$stale_original_mutations_fail_closed$;
reset role;

set local role coineasy_telegram_resolution;
do $stale_phase_credentials_fail_closed$
declare
    target jsonb := pg_catalog.current_setting(
        'test.telegram_resolution_snapshot_fixture'
    )::jsonb;
    claims jsonb;
    phase text;
    principal text;
begin
    foreach phase in array array['inspect', 'approve', 'resolve'] loop
        principal := case phase
            when 'inspect' then target ->> 'inspected_by'
            when 'approve' then target ->> 'approved_by'
            else target ->> 'resolved_by' end;
        claims := pg_catalog.jsonb_build_object(
            'role', 'coineasy_telegram_resolution',
            'workspace_id', target ->> 'workspace_id',
            'sub', principal,
            'capability', 'telegram_delivery_unknown_' || phase,
            'environment', 'production',
            'release_sha', target ->> 'release_sha',
            'automatic_publication', false,
            'resend_authorized', false,
            'max_external_actions', 0,
            'jti', case when phase = 'approve'
                then target ->> 'operator_approval_id'
                else target ->> 'resolution_id' end,
            'content_item_id', target ->> 'content_item_id',
            'content_version_id', target ->> 'content_version_id',
            'publication_id', target ->> 'publication_id',
            'job_id', target ->> 'job_id',
            'resolution_id', target ->> 'resolution_id',
            'operator_approval_id', target ->> 'operator_approval_id',
            'approved_by', target ->> 'approved_by',
            'expires_at', target ->> 'expires_at',
            'public_audit_sha256', target ->> 'public_audit_sha256',
            'approval_subject_sha256', target ->> 'approval_subject_sha256'
        );
        perform pg_catalog.set_config('request.jwt.claims', claims::text, true);
        begin
            case phase
            when 'inspect' then
                perform public.inspect_exact_telegram_delivery_unknown_resolution(
                    (target ->> 'workspace_id')::uuid,
                    (target ->> 'content_item_id')::uuid,
                    (target ->> 'content_version_id')::uuid,
                    (target ->> 'publication_id')::uuid,
                    (target ->> 'job_id')::uuid,
                    (target ->> 'resolution_id')::uuid,
                    (target ->> 'operator_approval_id')::uuid,
                    target ->> 'inspected_by',
                    target ->> 'approved_by',
                    (target ->> 'expires_at')::timestamptz,
                    target ->> 'release_sha',
                    target -> 'public_audit'
                );
            when 'approve' then
                perform public.approve_exact_telegram_delivery_unknown_resolution(
                    (target ->> 'workspace_id')::uuid,
                    (target ->> 'content_item_id')::uuid,
                    (target ->> 'content_version_id')::uuid,
                    (target ->> 'publication_id')::uuid,
                    (target ->> 'job_id')::uuid,
                    (target ->> 'resolution_id')::uuid,
                    (target ->> 'operator_approval_id')::uuid,
                    target ->> 'approved_by',
                    (target ->> 'expires_at')::timestamptz,
                    target ->> 'release_sha',
                    target -> 'public_audit',
                    target ->> 'approval_subject_sha256'
                );
            else
                perform public.resolve_exact_telegram_delivery_unknown_without_resend(
                    (target ->> 'workspace_id')::uuid,
                    (target ->> 'content_item_id')::uuid,
                    (target ->> 'content_version_id')::uuid,
                    (target ->> 'publication_id')::uuid,
                    (target ->> 'job_id')::uuid,
                    (target ->> 'resolution_id')::uuid,
                    (target ->> 'operator_approval_id')::uuid,
                    target ->> 'resolved_by',
                    target ->> 'release_sha',
                    target -> 'public_audit',
                    target ->> 'approval_subject_sha256'
                );
            end case;
            raise exception 'stale snapshot unexpectedly accepted % credential', phase;
        exception when sqlstate '25001' then
            if sqlerrm <> 'Telegram resolution requires READ COMMITTED' then
                raise;
            end if;
        end;
    end loop;
end
$stale_phase_credentials_fail_closed$;
reset role;
select pg_catalog.pg_advisory_unlock(20260831, 171);
rollback;
