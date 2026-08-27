-- Transactional smoke for the exact one-shot Squid failed-draft recovery.
-- No provider, Telegram, Grok, approval, or publication call is made.

begin;

do $test$
declare
    inspect_signature constant text :=
        'public.inspect_squid_failed_draft_recovery(uuid,uuid,uuid,uuid,text,timestamp with time zone,timestamp with time zone,text)';
    authorize_signature constant text :=
        'public.authorize_squid_failed_draft_recovery(uuid,uuid,uuid,uuid,text,timestamp with time zone,timestamp with time zone,text,text)';
    claim_signature constant text :=
        'public.claim_squid_failed_draft_recovery(uuid,uuid,uuid,text,text,text,integer)';
begin
    if has_function_privilege('anon', inspect_signature, 'execute')
       or has_function_privilege('authenticated', inspect_signature, 'execute')
       or has_function_privilege('anon', authorize_signature, 'execute')
       or has_function_privilege('authenticated', authorize_signature, 'execute')
       or has_function_privilege('anon', claim_signature, 'execute')
       or has_function_privilege('authenticated', claim_signature, 'execute') then
        raise exception 'Squid failed-draft recovery RPC leaked to a browser role';
    end if;
    if not has_function_privilege('service_role', inspect_signature, 'execute')
       or not has_function_privilege('service_role', authorize_signature, 'execute')
       or not has_function_privilege('service_role', claim_signature, 'execute') then
        raise exception 'Squid failed-draft recovery RPC unavailable to service role';
    end if;
    if has_table_privilege(
        'service_role',
        'private.official_x_failed_draft_recovery_grants',
        'select'
    ) or has_table_privilege(
        'service_role',
        'private.official_x_failed_draft_recovery_grants',
        'insert'
    ) or has_table_privilege(
        'service_role',
        'private.official_x_failed_draft_recovery_grants',
        'update'
    ) or has_table_privilege(
        'authenticated',
        'private.official_x_failed_draft_recovery_grants',
        'select'
    ) then
        raise exception 'Squid failed-draft recovery grant leaked direct access';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'f0000000-0000-4000-8000-000000000001',
    'Squid Failed Draft Recovery Test',
    'squid-failed-draft-recovery-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
) values (
    'f0000000-0000-4000-8000-000000000001',
    'squid',
    'Squid',
    true,
    null
);

insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, source_url, handle,
    poll_interval_minutes, last_cursor, last_polled_at, active
) values (
    'f1000000-0000-4000-8000-000000000001',
    'f0000000-0000-4000-8000-000000000001',
    'squid',
    'x',
    'Squid official X',
    'https://x.com/SquidRouter',
    '@SquidRouter',
    15,
    '2091935028565459431',
    clock_timestamp(),
    true
);

insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, external_id, source_type,
    canonical_url, author_handle, published_at, body, media, raw_payload,
    source_hash
) values (
    'f2000000-0000-4000-8000-000000000001',
    'f0000000-0000-4000-8000-000000000001',
    'squid',
    'f1000000-0000-4000-8000-000000000001',
    '2091935028565459431',
    'tweet',
    'https://x.com/SquidRouter/status/2091935028565459431',
    '@SquidRouter',
    statement_timestamp() - interval '1 hour',
    'Squid has moved an official amount across one pinned network.',
    jsonb_build_array(jsonb_build_object(
        'type', 'photo',
        'url', 'https://pbs.twimg.com/media/recovery_test.jpg'
    )),
    '{}'::jsonb,
    repeat('a', 64)
);

insert into public.jobs (
    id, workspace_id, client_id, job_kind, status, priority, input, output,
    idempotency_key, attempts, max_attempts, available_at,
    last_error_code, last_error_message, started_at, finished_at
) values (
    'f3000000-0000-4000-8000-000000000001',
    'f0000000-0000-4000-8000-000000000001',
    'squid',
    'generate',
    'failed',
    0,
    jsonb_build_object(
        'workflow', 'official_x_review_draft_v1',
        'kst_date',
            pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date,
        'source_item_ids', jsonb_build_array(
            'f2000000-0000-4000-8000-000000000001'
        ),
        'content_kind', 'daily_news',
        'request_id', 'f4000000-0000-4000-8000-000000000001',
        'source_content',
            'Squid has moved an official amount across one pinned network.',
        'source_url',
            'https://x.com/SquidRouter/status/2091935028565459431',
        'source_image_url',
            'https://pbs.twimg.com/media/recovery_test.jpg',
        'manual_only', false
    ),
    jsonb_build_object(
        'execution_plane', 'studio_sync',
        'last_failure', jsonb_build_object(
            'worker_id', 'official-x:failed-worker',
            'error_code', 'squid_copy_discovery_unavailable',
            'error_message', 'squid_copy_discovery_unavailable',
            'retryable', false,
            'failed_at', statement_timestamp() - interval '5 minutes',
            'retry_at', 'null'::jsonb
        )
    ),
    'official-x-review:v1:recovery-test:squid',
    3,
    3,
    statement_timestamp(),
    'squid_copy_discovery_unavailable',
    'squid_copy_discovery_unavailable',
    statement_timestamp() - interval '20 minutes',
    statement_timestamp() - interval '5 minutes'
);

insert into private.official_x_source_state (
    workspace_id, client_id, source_item_id, queued_job_id,
    discovered_at, queued_at
) values (
    'f0000000-0000-4000-8000-000000000001',
    'squid',
    'f2000000-0000-4000-8000-000000000001',
    'f3000000-0000-4000-8000-000000000001',
    statement_timestamp() - interval '10 minutes',
    statement_timestamp() - interval '9 minutes'
);

insert into private.official_x_daily_slots (
    workspace_id, kst_date, client_id, slot, job_id
) values (
    'f0000000-0000-4000-8000-000000000001',
    pg_catalog.timezone('Asia/Seoul', statement_timestamp())::date,
    'squid',
    1,
    'f3000000-0000-4000-8000-000000000001'
);

insert into private.official_x_style_reference_packs (
    workspace_id, client_id, request_id, primary_source_item_id,
    style_references, reference_pack_hash
) values (
    'f0000000-0000-4000-8000-000000000001',
    'squid',
    'f4000000-0000-4000-8000-000000000001',
    'f2000000-0000-4000-8000-000000000001',
    '[]'::jsonb,
    md5('[]'::jsonb::text)
);

-- A broader or unrelated failure code must not enter the one-shot path.  The
-- fixture is restored before the positive inspect/authorize/claim flow.
do $test$
declare
    rejected boolean := false;
begin
    update public.jobs
    set last_error_code = 'squid_placement_audit_unavailable',
        last_error_message = 'squid_placement_audit_unavailable',
        output = jsonb_set(
            output,
            '{last_failure,error_code}',
            to_jsonb('squid_placement_audit_unavailable'::text)
        )
    where id = 'f3000000-0000-4000-8000-000000000001';

    begin
        perform public.inspect_squid_failed_draft_recovery(
            'f0000000-0000-4000-8000-000000000001',
            'f3000000-0000-4000-8000-000000000001',
            'f7000000-0000-4000-8000-000000000001',
            'f8000000-0000-4000-8000-000000000001',
            'sql-test',
            statement_timestamp(),
            statement_timestamp() + interval '1 hour',
            repeat('b', 40)
        );
    exception
        when check_violation then
            if sqlerrm <> 'Squid failed draft is not recovery eligible' then
                raise;
            end if;
            rejected := true;
    end;
    if not rejected then
        raise exception 'Unallowlisted Squid failure entered recovery';
    end if;

    update public.jobs
    set last_error_code = 'squid_copy_discovery_unavailable',
        last_error_message = 'squid_copy_discovery_unavailable',
        output = jsonb_set(
            output,
            '{last_failure,error_code}',
            to_jsonb('squid_copy_discovery_unavailable'::text)
        )
    where id = 'f3000000-0000-4000-8000-000000000001';
end
$test$;

do $test$
declare
    test_workspace_id constant uuid :=
        'f0000000-0000-4000-8000-000000000001';
    test_job_id constant uuid :=
        'f3000000-0000-4000-8000-000000000001';
    test_request_id constant uuid :=
        'f4000000-0000-4000-8000-000000000001';
    test_recovery_id constant uuid :=
        'f5000000-0000-4000-8000-000000000001';
    test_approval_id constant uuid :=
        'f6000000-0000-4000-8000-000000000001';
    approved_at timestamptz := statement_timestamp();
    expires_at timestamptz := statement_timestamp() + interval '1 hour';
    test_release_sha constant text := repeat('b', 40);
    test_worker_id constant text := 'squid-recovery:sql-test';
    inspected jsonb;
    authorized jsonb;
    claimed jsonb;
    replay jsonb;
    before_job jsonb;
    after_job jsonb;
    failed_job jsonb;
    subject_sha text;
    grant_count integer;
    content_count integer;
    outbox_count integer;
    approval_count integer;
    publication_count integer;
begin
    select to_jsonb(job) into before_job
    from public.jobs as job where job.id = test_job_id;

    inspected := public.inspect_squid_failed_draft_recovery(
        test_workspace_id,
        test_job_id,
        test_recovery_id,
        test_approval_id,
        'sql-test',
        approved_at,
        expires_at,
        test_release_sha
    );
    if inspected -> 'eligible' is distinct from 'true'::jsonb
       or inspected -> 'authorized' is distinct from 'false'::jsonb
       or inspected ->> 'job_id' <> test_job_id::text
       or inspected ->> 'request_id' <> test_request_id::text
       or inspected -> 'approval_subject' ?| array[
           'source_content', 'source_url', 'source_image_url', 'provider_body'
       ] then
        raise exception 'Squid recovery inspection was not bounded';
    end if;
    subject_sha := inspected ->> 'approval_subject_sha256';

    authorized := public.authorize_squid_failed_draft_recovery(
        test_workspace_id,
        test_job_id,
        test_recovery_id,
        test_approval_id,
        'sql-test',
        approved_at,
        expires_at,
        test_release_sha,
        subject_sha
    );
    select count(*) into grant_count
    from private.official_x_failed_draft_recovery_grants as grant_row
    where grant_row.workspace_id = test_workspace_id
      and grant_row.job_id = test_job_id;
    select to_jsonb(job) into after_job
    from public.jobs as job where job.id = test_job_id;
    if authorized -> 'authorized' is distinct from 'true'::jsonb
       or authorized -> 'reused' is distinct from 'false'::jsonb
       or grant_count <> 1
       or after_job is distinct from before_job then
        raise exception 'Squid recovery authorization mutated the job';
    end if;

    claimed := public.claim_squid_failed_draft_recovery(
        test_workspace_id,
        test_job_id,
        test_recovery_id,
        subject_sha,
        test_release_sha,
        test_worker_id,
        900
    );
    select to_jsonb(job) into after_job
    from public.jobs as job where job.id = test_job_id;
    if claimed -> 'claim_granted' is distinct from 'true'::jsonb
       or claimed -> 'generation_allowed' is distinct from 'true'::jsonb
       or claimed -> 'failed_draft_recovery_only' is distinct from 'true'::jsonb
       or claimed ->> 'request_id' <> test_request_id::text
       or after_job ->> 'status' <> 'running'
       or after_job ->> 'locked_by' <> test_worker_id
       or (after_job ->> 'attempts')::integer <> 3
       or after_job -> 'input' is distinct from before_job -> 'input'
       or after_job -> 'output' is distinct from before_job -> 'output'
       or after_job ->> 'last_error_code'
            is distinct from before_job ->> 'last_error_code'
       or after_job ->> 'last_error_message'
            is distinct from before_job ->> 'last_error_message'
       or after_job ->> 'finished_at'
            is distinct from before_job ->> 'finished_at' then
        raise exception 'Squid recovery claim changed immutable job evidence';
    end if;

    replay := public.claim_squid_failed_draft_recovery(
        test_workspace_id,
        test_job_id,
        test_recovery_id,
        subject_sha,
        test_release_sha,
        test_worker_id,
        900
    );
    if replay -> 'claim_granted' is distinct from 'false'::jsonb
       or replay -> 'generation_allowed' is distinct from 'false'::jsonb then
        raise exception 'Squid recovery replay authorized duplicate generation';
    end if;

    perform public.fail_review_draft_job(
        test_job_id,
        test_worker_id,
        'studio_generation_unavailable',
        'studio_generation_unavailable',
        true,
        null
    );
    select to_jsonb(job) into failed_job
    from public.jobs as job where job.id = test_job_id;
    if failed_job ->> 'status' <> 'failed'
       or (failed_job ->> 'attempts')::integer <> 3
       or failed_job -> 'output' -> 'last_failure'
            is distinct from before_job -> 'output' -> 'last_failure'
       or failed_job -> 'output' -> 'recovery_failure' ->> 'error_code'
            <> 'studio_generation_unavailable' then
        raise exception 'Squid recovery failure was not terminal and preserved';
    end if;

    select count(*) into content_count
    from public.content_items as item
    where item.workspace_id = test_workspace_id;
    select count(*) into outbox_count
    from private.grok_qa_dispatch_outbox as dispatch
    where dispatch.workspace_id = test_workspace_id;
    select count(*) into approval_count
    from public.approvals as approval
    where approval.workspace_id = test_workspace_id;
    select count(*) into publication_count
    from public.publications as publication
    where publication.workspace_id = test_workspace_id;
    if content_count <> 0
       or outbox_count <> 0
       or approval_count <> 0
       or publication_count <> 0 then
        raise exception 'Squid recovery created unauthorized downstream state';
    end if;
end
$test$;

rollback;
