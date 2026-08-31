-- Transactional security and immutability smoke for audited closure of one
-- exact Telegram delivery_unknown result. Run after all migrations as the
-- database owner; every fixture and attempted mutation is rolled back.

begin;
set local time zone 'UTC';

create function pg_temp.double_fact_check_meta(target_request_hash text)
returns jsonb
language sql
immutable
as $$
    select jsonb_build_object(
        'request_hash', target_request_hash,
        'mock_mode', false,
        'fact_check', jsonb_build_object(
            'schema_version', '1.0',
            'policy_version', 'double-fact-check@1',
            'content_kind', 'daily_news',
            'status', 'review',
            'human_review_required', true,
            'input_sha256', repeat('a', 64),
            'output_sha256', repeat('b', 64),
            'checks', jsonb_build_array(
                jsonb_build_object(
                    'id', 'source_evidence',
                    'status', 'review',
                    'label', 'Source evidence',
                    'detail', 'Human verification fixture.',
                    'metrics', '{}'::jsonb
                ),
                jsonb_build_object(
                    'id', 'output_claims',
                    'status', 'pass',
                    'label', 'Output claims',
                    'detail', 'Output fixture.',
                    'metrics', '{}'::jsonb
                )
            )
        )
    )
$$;

create function pg_temp.public_channel_audit(target_checked_at timestamptz)
returns jsonb
language sql
stable
as $$
    select jsonb_build_object(
        'schema_version', 'telegram-public-channel-audit@1',
        'scan_source', 'public_telegram_web_history',
        'public_channel', 'squid_kor_update',
        'first_message_id', 299,
        'last_message_id', 405,
        'message_count', 107,
        'checked_at', to_char(
            date_trunc('second', target_checked_at) at time zone 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        ),
        'caption_match_count', 0,
        'png_match_count', 0,
        'snapshot_sha256', repeat('e', 64)
    )
$$;

do $test$
declare
    inspect_signature text :=
        'public.inspect_exact_telegram_delivery_unknown_resolution('
        'uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,text,'
        'timestamp with time zone,text,jsonb)';
    approve_signature text :=
        'public.approve_exact_telegram_delivery_unknown_resolution('
        'uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,'
        'timestamp with time zone,text,jsonb,text)';
    resolve_signature text :=
        'public.resolve_exact_telegram_delivery_unknown_without_resend('
        'uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,text,jsonb,text)';
    runtime_role text;
    table_privilege text;
    private_table text;
begin
    if has_function_privilege('anon', inspect_signature, 'execute')
       or has_function_privilege('authenticated', inspect_signature, 'execute')
       or has_function_privilege('service_role', inspect_signature, 'execute')
       or has_function_privilege('anon', approve_signature, 'execute')
       or has_function_privilege('authenticated', approve_signature, 'execute')
       or has_function_privilege('service_role', approve_signature, 'execute')
       or has_function_privilege('anon', resolve_signature, 'execute')
       or has_function_privilege('authenticated', resolve_signature, 'execute')
       or has_function_privilege('service_role', resolve_signature, 'execute') then
        raise exception 'exact Telegram resolution RPC leaked to a broad role';
    end if;
    if not has_function_privilege(
        'coineasy_telegram_resolution', inspect_signature, 'execute'
    ) or not has_function_privilege(
        'coineasy_telegram_resolution', approve_signature, 'execute'
    ) or not has_function_privilege(
        'coineasy_telegram_resolution', resolve_signature, 'execute'
    ) then
        raise exception 'exact Telegram resolution role cannot execute its RPCs';
    end if;
    if exists (
        select 1
        from pg_catalog.pg_proc as procedure
        join pg_catalog.pg_namespace as namespace
          on namespace.oid = procedure.pronamespace
        where namespace.nspname = 'public'
          and pg_catalog.has_function_privilege(
              'coineasy_telegram_resolution', procedure.oid, 'execute'
          )
          and procedure.oid <> pg_catalog.to_regprocedure(inspect_signature)::oid
          and procedure.oid <> pg_catalog.to_regprocedure(approve_signature)::oid
          and procedure.oid <> pg_catalog.to_regprocedure(resolve_signature)::oid
    ) then
        raise exception
            'exact Telegram resolution role can execute an unrelated public RPC';
    end if;
    foreach runtime_role in array array[
        'anon', 'authenticated', 'service_role',
        'coineasy_telegram_resolution'
    ] loop
        foreach private_table in array array[
            'private.exact_telegram_delivery_unknown_approvals',
            'private.exact_telegram_delivery_unknown_resolutions'
        ] loop
            foreach table_privilege in array array[
                'select', 'insert', 'update', 'delete'
            ] loop
                if has_table_privilege(
                    runtime_role, private_table, table_privilege
                ) then
                    raise exception
                        'exact Telegram private table % leaked % to %',
                        private_table, table_privilege, runtime_role;
                end if;
            end loop;
        end loop;
    end loop;
    if exists (
        select 1
        from pg_catalog.pg_class as relation
        join pg_catalog.pg_namespace as namespace
          on namespace.oid = relation.relnamespace
        where namespace.nspname = 'private'
          and relation.relname in (
              'exact_telegram_delivery_unknown_approvals',
              'exact_telegram_delivery_unknown_resolutions'
          )
          and (not relation.relrowsecurity or not relation.relforcerowsecurity)
    ) then
        raise exception 'exact Telegram private table is not FORCE RLS';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    '9e000000-0000-4000-8000-000000000001',
    'Exact Telegram Unknown Resolution Security Test',
    'exact-telegram-unknown-resolution-security-test',
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
) values (
    '9e000000-0000-4000-8000-000000000001',
    'squid',
    'Squid',
    true,
    null
);

insert into auth.users (id)
values ('9e900000-0000-4000-8000-000000000001');

insert into public.workspace_members (
    workspace_id, user_id, role, status, invited_by
) values (
    '9e000000-0000-4000-8000-000000000001',
    '9e900000-0000-4000-8000-000000000001',
    'owner',
    'active',
    null
);

insert into storage.objects (bucket_id, name)
values
    (
        'content-studio',
        '9e000000-0000-4000-8000-000000000001/squid/'
        '9e300000-0000-4000-8000-000000000001/news-card.png'
    ),
    (
        'content-studio',
        '9e000000-0000-4000-8000-000000000001/squid/'
        '9e300000-0000-4000-8000-000000000002/news-card.png'
    ),
    (
        'content-studio',
        '9e000000-0000-4000-8000-000000000001/squid/'
        '9e300000-0000-4000-8000-000000000003/news-card.png'
    );

create function pg_temp.create_delivery_unknown_fixture(
    target_content_item_id uuid,
    target_asset_id uuid,
    target_request_id uuid,
    target_worker_id text,
    target_request_sha256 text,
    target_asset_sha256 text,
    target_caption text,
    target_review_key text
)
returns jsonb
language plpgsql
as $$
declare
    generated jsonb;
    requested jsonb;
    claimed jsonb;
    marked jsonb;
    failed jsonb;
    target_version_id uuid;
    target_storage_path text;
begin
    target_storage_path :=
        '9e000000-0000-4000-8000-000000000001/squid/'
        || target_asset_id::text || '/news-card.png';
    generated := public.record_generated_content(
        target_content_item_id,
        '9e000000-0000-4000-8000-000000000001',
        'squid',
        'daily_news',
        'Exact Telegram delivery unknown resolution fixture',
        jsonb_build_object('request_hash', target_asset_sha256),
        jsonb_build_object('telegram', target_caption),
        pg_temp.double_fact_check_meta(target_asset_sha256),
        jsonb_build_object(
            'asset_id', target_asset_id,
            'filename', 'news-card.png',
            'storage_path', target_storage_path,
            'mime_type', 'image/png',
            'byte_size', 128,
            'sha256', target_asset_sha256,
            'width', 1080,
            'height', 1080
        ),
        'exact-telegram-resolution-smoke@1'
    );
    target_version_id := (generated ->> 'content_version_id')::uuid;

    perform public.record_studio_content_review_v2(
        '9e000000-0000-4000-8000-000000000001',
        target_content_item_id,
        target_version_id,
        'approved',
        'double-fact-check@1',
        true,
        true,
        '{}'::text[],
        null,
        target_review_key
    );
    requested := public.request_studio_telegram_publication(
        '9e000000-0000-4000-8000-000000000001',
        target_content_item_id,
        target_version_id,
        target_request_id::text
    );
    claimed := public.claim_exact_telegram_publication_job(
        '9e000000-0000-4000-8000-000000000001',
        target_worker_id,
        300
    );
    if claimed ->> 'job_id' is distinct from requested ->> 'job_id'
       or claimed ->> 'publication_id'
            is distinct from requested ->> 'publication_id' then
        raise exception 'fixture claimed a different exact Telegram job';
    end if;
    marked := public.mark_exact_telegram_attempt_started(
        (claimed ->> 'job_id')::uuid,
        target_worker_id,
        target_request_sha256
    );
    if marked ->> 'status' <> 'publishing' then
        raise exception 'fixture did not cross the exact Telegram attempt fence';
    end if;
    failed := public.fail_exact_telegram_publication_job(
        (claimed ->> 'job_id')::uuid,
        target_worker_id,
        'telegram_delivery_unknown',
        false
    );
    if failed ->> 'status' <> 'delivery_unknown'
       or failed ->> 'job_status' <> 'failed' then
        raise exception 'fixture did not become terminal delivery_unknown';
    end if;

    -- The public-history audit is required to occur at least ten minutes after
    -- the delivery fence. Age this synthetic attempt before taking snapshots.
    update public.publications
    set delivery_started_at = statement_timestamp() - interval '20 minutes'
    where id = (requested ->> 'publication_id')::uuid;

    return jsonb_build_object(
        'content_item_id', target_content_item_id,
        'content_version_id', target_version_id,
        'publication_id', requested ->> 'publication_id',
        'job_id', requested ->> 'job_id'
    );
end;
$$;

-- All test helpers below are SECURITY INVOKER. Switching the actual database
-- role therefore exercises table/RPC ACLs, not merely a spoofed JWT GUC.
create function pg_temp.resolution_claims(context jsonb, phase text)
returns jsonb language sql stable as $$
    select jsonb_build_object(
        'role', 'coineasy_telegram_resolution',
        'workspace_id', context ->> 'workspace_id',
        'sub', context ->> case phase
            when 'inspect' then 'inspected_by'
            when 'approve' then 'approved_by' else 'resolved_by' end,
        'capability', 'telegram_delivery_unknown_' || phase,
        'environment', 'production',
        'release_sha', context ->> 'release_sha',
        'automatic_publication', false,
        'resend_authorized', false,
        'max_external_actions', 0,
        'jti', context ->> case phase
            when 'approve' then 'operator_approval_id'
            else 'resolution_id' end,
        'content_item_id', context ->> 'content_item_id',
        'content_version_id', context ->> 'content_version_id',
        'publication_id', context ->> 'publication_id',
        'job_id', context ->> 'job_id',
        'resolution_id', context ->> 'resolution_id',
        'operator_approval_id', context ->> 'operator_approval_id',
        'approved_by', context ->> 'approved_by',
        'expires_at', context ->> 'expires_at',
        'public_audit_sha256', context ->> 'public_audit_sha256',
        'approval_subject_sha256', context ->> 'approval_subject_sha256'
    )
$$;

create function pg_temp.inspect_resolution(context jsonb)
returns jsonb language sql as $$
    select public.inspect_exact_telegram_delivery_unknown_resolution(
        (context ->> 'workspace_id')::uuid,
        (context ->> 'content_item_id')::uuid,
        (context ->> 'content_version_id')::uuid,
        (context ->> 'publication_id')::uuid,
        (context ->> 'job_id')::uuid,
        (context ->> 'resolution_id')::uuid,
        (context ->> 'operator_approval_id')::uuid,
        context ->> 'inspected_by',
        context ->> 'approved_by',
        (context ->> 'expires_at')::timestamptz,
        context ->> 'release_sha',
        context -> 'public_audit'
    )
$$;

create function pg_temp.approve_resolution(context jsonb)
returns jsonb language sql as $$
    select public.approve_exact_telegram_delivery_unknown_resolution(
        (context ->> 'workspace_id')::uuid,
        (context ->> 'content_item_id')::uuid,
        (context ->> 'content_version_id')::uuid,
        (context ->> 'publication_id')::uuid,
        (context ->> 'job_id')::uuid,
        (context ->> 'resolution_id')::uuid,
        (context ->> 'operator_approval_id')::uuid,
        context ->> 'approved_by',
        (context ->> 'expires_at')::timestamptz,
        context ->> 'release_sha',
        context -> 'public_audit',
        context ->> 'approval_subject_sha256'
    )
$$;

create function pg_temp.resolve_resolution(context jsonb)
returns jsonb language sql as $$
    select public.resolve_exact_telegram_delivery_unknown_without_resend(
        (context ->> 'workspace_id')::uuid,
        (context ->> 'content_item_id')::uuid,
        (context ->> 'content_version_id')::uuid,
        (context ->> 'publication_id')::uuid,
        (context ->> 'job_id')::uuid,
        (context ->> 'resolution_id')::uuid,
        (context ->> 'operator_approval_id')::uuid,
        context ->> 'resolved_by',
        context ->> 'release_sha',
        context -> 'public_audit',
        context ->> 'approval_subject_sha256'
    )
$$;

create function pg_temp.expect_sqlstate(
    statement text, expected_state text, label text
)
returns void language plpgsql as $$
begin
    begin
        execute statement;
    exception when others then
        if sqlstate = expected_state then return; end if;
        raise exception '%: expected SQLSTATE %, got % (%)',
            label, expected_state, sqlstate, sqlerrm;
    end;
    raise exception '%: statement unexpectedly succeeded', label;
end;
$$;

create function pg_temp.resolution_context(fixture jsonb, suffix text)
returns jsonb language sql stable as $$
    select fixture || jsonb_build_object(
        'workspace_id', '9e000000-0000-4000-8000-000000000001',
        'resolution_id', '9e500000-0000-4000-8000-' || lpad(suffix, 12, '0'),
        'operator_approval_id',
            '9e600000-0000-4000-8000-' || lpad(suffix, 12, '0'),
        'inspected_by', 'codex:resolution-inspector',
        'approved_by', 'operator:resolution-approver',
        'resolved_by', 'codex:resolution-resolver',
        'release_sha', repeat('a', 40),
        'expires_at', date_trunc('second', clock_timestamp()) + interval '1 hour',
        'public_audit', audit,
        'public_audit_sha256', encode(
            extensions.digest(convert_to(audit::text, 'UTF8'), 'sha256'), 'hex'
        )
    )
    from (select pg_temp.public_channel_audit(
        clock_timestamp() - interval '1 minute'
    ) as audit) as bounded_audit
$$;

do $test$
#variable_conflict use_variable
declare
    context jsonb;
    wrong_context jsonb;
    inspected jsonb;
    approved jsonb;
    resolved jsonb;
    replayed jsonb;
    observed jsonb;
    publication_before jsonb;
    job_before jsonb;
    approval_before jsonb;
    resolution_before jsonb;
    claims jsonb;
    field_name text;
    statement text;
    digest_name text;
    forensic_update text;
    inspect_sql text;
    approve_sql text;
    resolve_sql text;
    publication_id uuid;
    job_id uuid;
    asset_id uuid;
    publication_approval_id uuid;
    publication_count_before integer;
    job_count_before integer;
    approval_call_started timestamptz;
begin
    context := pg_temp.resolution_context(
        pg_temp.create_delivery_unknown_fixture(
            '9e100000-0000-4000-8000-000000000001',
            '9e300000-0000-4000-8000-000000000001',
            '9e400000-0000-4000-8000-000000000001',
            'resolution-worker-01', repeat('c', 64), repeat('1', 64),
            'This synthetic exact Telegram result must never be resent.',
            'resolution-security-review-01'
        ), '1'
    );
    publication_id := (context ->> 'publication_id')::uuid;
    job_id := (context ->> 'job_id')::uuid;
    select to_jsonb(p.*),
        (p.request_payload ->> 'asset_id')::uuid,
        (p.request_payload ->> 'approval_id')::uuid
    into publication_before, asset_id, publication_approval_id
    from public.publications p where p.id = publication_id;
    select to_jsonb(j.*) into job_before
    from public.jobs j where j.id = job_id;
    select count(*) into publication_count_before
    from public.publications p
    where p.content_item_id = (context ->> 'content_item_id')::uuid;
    select count(*) into job_count_before
    from public.jobs j
    where j.content_item_id = (context ->> 'content_item_id')::uuid;

    inspect_sql := format(
        'select pg_temp.inspect_resolution(%L::jsonb)', context
    );
    execute 'set local role coineasy_telegram_resolution';
    if current_user <> 'coineasy_telegram_resolution' then
        raise exception 'runtime smoke did not change the real database role';
    end if;
    perform set_config('request.jwt.claims', '', true);
    perform pg_temp.expect_sqlstate(
        inspect_sql, '42501', 'missing scoped JWT'
    );
    perform pg_temp.expect_sqlstate(
        'select * from private.exact_telegram_delivery_unknown_approvals',
        '42501', 'runtime approval table read'
    );
    perform pg_temp.expect_sqlstate(
        'insert into private.exact_telegram_delivery_unknown_resolutions '
        'default values', '42501', 'runtime resolution table insert'
    );
    perform pg_temp.expect_sqlstate(
        'update private.exact_telegram_delivery_unknown_approvals '
        'set approved_by = approved_by', '42501', 'runtime approval table update'
    );
    perform pg_temp.expect_sqlstate(
        'delete from private.exact_telegram_delivery_unknown_resolutions',
        '42501', 'runtime resolution table delete'
    );

    claims := pg_temp.resolution_claims(context, 'inspect');
    foreach field_name in array array[
        'jti', 'workspace_id', 'content_item_id', 'content_version_id',
        'publication_id', 'job_id', 'resolution_id', 'operator_approval_id',
        'approved_by', 'expires_at', 'public_audit_sha256', 'release_sha'
    ] loop
        perform set_config(
            'request.jwt.claims', (claims - field_name)::text, true
        );
        perform pg_temp.expect_sqlstate(
            inspect_sql, '42501', 'inspect JWT missing ' || field_name
        );
    end loop;
    perform set_config('request.jwt.claims', claims::text, true);
    wrong_context := context || jsonb_build_object(
        'job_id', '9e700000-0000-4000-8000-000000000009'
    );
    perform pg_temp.expect_sqlstate(
        format('select pg_temp.inspect_resolution(%L::jsonb)', wrong_context),
        '42501', 'inspect JWT cannot select another exact job'
    );
    wrong_context := context || jsonb_build_object(
        'expires_at', clock_timestamp() - interval '1 minute'
    );
    perform set_config(
        'request.jwt.claims',
        pg_temp.resolution_claims(wrong_context, 'inspect')::text, true
    );
    perform pg_temp.expect_sqlstate(
        format('select pg_temp.inspect_resolution(%L::jsonb)', wrong_context),
        '22023', 'inspection cannot preapprove an expired resolution'
    );
    perform set_config('request.jwt.claims', claims::text, true);
    execute 'set local time zone ''Asia/Seoul''';
    inspected := pg_temp.inspect_resolution(context);
    if inspected ->> 'eligible' <> 'true'
       or inspected ->> 'resolved' <> 'false'
       or inspected ->> 'approved' <> 'false'
       or inspected ->> 'reused' <> 'false'
       or inspected ->> 'resend_authorized' <> 'false'
       or inspected ->> 'approval_subject_sha256' !~ '^[a-f0-9]{64}$'
       or inspected -> 'approval_subject' ->> 'publication_state_changed'
            <> 'false'
       or inspected -> 'approval_subject' ->> 'job_state_changed' <> 'false'
       or inspected -> 'approval_subject' ->> 'provider_calls' <> '0'
       or inspected -> 'approval_subject' ->> 'database_claims' <> '0' then
        raise exception 'inspection did not return an unapproved zero-action subject';
    end if;
    foreach digest_name in array array[
        'content_item_row_sha256', 'content_version_row_sha256',
        'publication_row_sha256', 'job_row_sha256',
        'publication_approval_row_sha256', 'asset_row_sha256'
    ] loop
        if coalesce(inspected -> 'approval_subject' ->> digest_name, '')
                !~ '^[a-f0-9]{64}$' then
            raise exception 'forensic digest % is absent', digest_name;
        end if;
    end loop;
    context := context || jsonb_build_object(
        'approval_subject_sha256', inspected ->> 'approval_subject_sha256'
    );
    approve_sql := format(
        'select pg_temp.approve_resolution(%L::jsonb)', context
    );
    resolve_sql := format(
        'select pg_temp.resolve_resolution(%L::jsonb)', context
    );
    perform pg_temp.expect_sqlstate(
        approve_sql, '42501', 'inspect JWT cannot approve'
    );
    perform pg_temp.expect_sqlstate(
        resolve_sql, '42501', 'inspect JWT cannot resolve'
    );

    perform set_config(
        'request.jwt.claims',
        pg_temp.resolution_claims(context, 'resolve')::text, true
    );
    perform pg_temp.expect_sqlstate(
        resolve_sql, '23514', 'resolve requires a durable exact approval'
    );
    perform pg_temp.expect_sqlstate(
        approve_sql, '42501', 'resolve JWT cannot create its approval'
    );

    claims := pg_temp.resolution_claims(context, 'approve');
    foreach field_name in array array[
        'jti', 'content_item_id', 'content_version_id', 'publication_id',
        'job_id', 'resolution_id', 'operator_approval_id',
        'approval_subject_sha256', 'expires_at'
    ] loop
        perform set_config(
            'request.jwt.claims', (claims - field_name)::text, true
        );
        perform pg_temp.expect_sqlstate(
            approve_sql, '42501', 'approval JWT missing ' || field_name
        );
    end loop;
    wrong_context := context || jsonb_build_object(
        'approval_subject_sha256', repeat('0', 64)
    );
    perform set_config(
        'request.jwt.claims',
        pg_temp.resolution_claims(wrong_context, 'approve')::text, true
    );
    perform pg_temp.expect_sqlstate(
        format('select pg_temp.approve_resolution(%L::jsonb)', wrong_context),
        '23514', 'exact approval JWT with a stale subject'
    );
    perform set_config('request.jwt.claims', claims::text, true);
    perform pg_temp.expect_sqlstate(
        resolve_sql, '42501', 'approval JWT cannot resolve'
    );
    approval_call_started := clock_timestamp();
    execute 'set local time zone ''America/New_York''';
    approved := pg_temp.approve_resolution(context);
    replayed := pg_temp.approve_resolution(context);
    if approved ->> 'approved' <> 'true'
       or approved ->> 'reused' <> 'false'
       or replayed ->> 'reused' <> 'true'
       or (approved ->> 'approved_at')::timestamptz < approval_call_started
       or (approved ->> 'approved_at')::timestamptz > clock_timestamp()
       or approved ->> 'provider_calls' <> '0'
       or approved ->> 'database_claims' <> '0' then
        raise exception 'approval did not persist server-timed idempotent receipt';
    end if;
    wrong_context := context || jsonb_build_object(
        'public_audit', context -> 'public_audit' || jsonb_build_object(
            'snapshot_sha256', repeat('0', 64)
        )
    );
    perform pg_temp.expect_sqlstate(
        format('select pg_temp.approve_resolution(%L::jsonb)', wrong_context),
        '23505', 'approval replay cannot substitute public audit evidence'
    );
    execute 'set local time zone ''UTC''';
    execute 'reset role';
    if exists (
        select 1 from private.exact_telegram_delivery_unknown_resolutions r
        where r.publication_id = publication_id
    ) then
        raise exception 'inspection/approval/failed resolves leaked resolution';
    end if;
    select to_jsonb(a.*) into approval_before
    from private.exact_telegram_delivery_unknown_approvals a
    where a.operator_approval_id =
        (context ->> 'operator_approval_id')::uuid;

    -- Each subtransaction deliberately rolls its forensic drift back. Full-row
    -- binding must catch changes outside the previously hashed input/output.
    foreach forensic_update in array array[
        format('update public.content_items set title = title || %L '
               'where id = %L::uuid', ' drift', context ->> 'content_item_id'),
        format('update public.content_versions set title = title || %L '
               'where id = %L::uuid', ' drift', context ->> 'content_version_id'),
        format('update public.publications set last_error = %L '
               'where id = %L::uuid', 'drift', publication_id),
        format('update public.jobs set last_error_message = %L '
               'where id = %L::uuid', 'drift', job_id),
        format('update public.approvals set comment = %L '
               'where id = %L::uuid', 'drift', publication_approval_id),
        format('update public.assets set metadata = metadata || %L::jsonb '
               'where id = %L::uuid', '{"drift":true}', asset_id)
    ] loop
        begin
            execute forensic_update;
            execute 'set local role coineasy_telegram_resolution';
            perform set_config(
                'request.jwt.claims',
                pg_temp.resolution_claims(context, 'resolve')::text, true
            );
            perform pg_temp.expect_sqlstate(
                resolve_sql, '23514', 'full forensic row drift'
            );
            execute 'reset role';
            raise exception 'rollback synthetic drift' using errcode = 'P0002';
        exception when no_data_found then null;
        end;
    end loop;

    execute 'set local role coineasy_telegram_resolution';
    claims := pg_temp.resolution_claims(context, 'resolve');
    foreach field_name in array array[
        'jti', 'content_item_id', 'content_version_id', 'publication_id',
        'job_id', 'resolution_id', 'operator_approval_id',
        'approval_subject_sha256'
    ] loop
        perform set_config(
            'request.jwt.claims', (claims - field_name)::text, true
        );
        perform pg_temp.expect_sqlstate(
            resolve_sql, '42501', 'resolve JWT missing ' || field_name
        );
    end loop;
    perform set_config('request.jwt.claims', claims::text, true);
    perform pg_temp.expect_sqlstate(
        format('select pg_temp.resolve_resolution(%L::jsonb)',
            context || jsonb_build_object(
                'approval_subject_sha256', repeat('0', 64)
            )
        ),
        '42501', 'resolve JWT cannot substitute the approval subject'
    );
    resolved := pg_temp.resolve_resolution(context);
    replayed := pg_temp.resolve_resolution(context);
    if resolved ->> 'resolved' <> 'true'
       or resolved ->> 'reused' <> 'false'
       or replayed ->> 'reused' <> 'true'
       or resolved ->> 'publication_status' <> 'delivery_unknown'
       or resolved ->> 'job_status' <> 'failed'
       or resolved ->> 'delivery_outcome' <> 'unknown'
       or resolved ->> 'disposition' <> 'operator_closed_without_resend'
       or resolved ->> 'resend_authorized' <> 'false'
       or resolved ->> 'provider_calls' <> '0'
       or resolved ->> 'database_claims' <> '0' then
        raise exception 'resolution did not return idempotent zero-action state';
    end if;
    wrong_context := context || jsonb_build_object(
        'resolution_id', '9e500000-0000-4000-8000-000000000009'
    );
    perform set_config(
        'request.jwt.claims',
        pg_temp.resolution_claims(wrong_context, 'resolve')::text, true
    );
    perform pg_temp.expect_sqlstate(
        format('select pg_temp.resolve_resolution(%L::jsonb)', wrong_context),
        '23505', 'conflicting exact resolution replay'
    );
    perform set_config(
        'request.jwt.claims',
        pg_temp.resolution_claims(context, 'inspect')::text, true
    );
    replayed := pg_temp.inspect_resolution(context);
    if replayed ->> 'approved' <> 'true'
       or replayed ->> 'resolved' <> 'true'
       or replayed ->> 'reused' <> 'true' then
        raise exception 'inspect did not read back committed resolution';
    end if;
    execute 'reset role';

    if (select to_jsonb(p.*) from public.publications p
        where p.id = publication_id) is distinct from publication_before
       or (select to_jsonb(j.*) from public.jobs j where j.id = job_id)
            is distinct from job_before then
        raise exception 'resolution changed the original forensic publication/job';
    end if;
    if (select count(*) from public.publications p
        where p.content_item_id = (context ->> 'content_item_id')::uuid)
            <> publication_count_before
       or (select count(*) from public.jobs j
           where j.content_item_id = (context ->> 'content_item_id')::uuid)
            <> job_count_before then
        raise exception 'resolution created a publication or job';
    end if;
    if (select count(*) from private.exact_telegram_delivery_unknown_approvals a
        where a.publication_id = publication_id) <> 1
       or (select count(*)
           from private.exact_telegram_delivery_unknown_resolutions r
           where r.publication_id = publication_id) <> 1 then
        raise exception 'receipt replay was not exactly once';
    end if;
    foreach statement in array array[
        'exact_telegram_delivery_unknown_resolution_approved',
        'exact_telegram_delivery_unknown_resolved_without_resend'
    ] loop
        if (select count(*) from public.event_log e
            where e.entity_id = publication_id and e.event_type = statement
              and e.data ->> 'approval_subject_sha256'
                    = context ->> 'approval_subject_sha256'
              and e.data ->> 'provider_calls' = '0'
              and e.data ->> 'database_claims' = '0'
              and e.data ->> 'automatic_publication' = 'false'
              and e.data ->> 'resend_authorized' = 'false') <> 1 then
            raise exception 'receipt event % was not exactly once', statement;
        end if;
    end loop;

    select to_jsonb(r.*) into resolution_before
    from private.exact_telegram_delivery_unknown_resolutions r
    where r.publication_id = publication_id;
    foreach statement in array array[
        format('update private.exact_telegram_delivery_unknown_approvals '
               'set approved_by = approved_by where publication_id = %L::uuid',
               publication_id),
        format('delete from private.exact_telegram_delivery_unknown_approvals '
               'where publication_id = %L::uuid', publication_id),
        format('update private.exact_telegram_delivery_unknown_resolutions '
               'set approved_by = approved_by where publication_id = %L::uuid',
               publication_id),
        format('delete from private.exact_telegram_delivery_unknown_resolutions '
               'where publication_id = %L::uuid', publication_id),
        format('update public.publications set last_error = last_error '
               'where id = %L::uuid', publication_id),
        format('delete from public.publications where id = %L::uuid',
               publication_id),
        format('update public.jobs set last_error_message = last_error_message '
               'where id = %L::uuid', job_id),
        format('delete from public.jobs where id = %L::uuid', job_id)
    ] loop
        perform pg_temp.expect_sqlstate(
            statement, '55000', 'immutable receipt/original forensic row'
        );
    end loop;

    -- A later positive public receipt is distinct evidence, not a resend or
    -- rewrite of the unknown attempt. Existing RPC requires the version current.
    observed := public.record_manual_publication_observation(
        (context ->> 'workspace_id')::uuid,
        (context ->> 'content_item_id')::uuid,
        (context ->> 'content_version_id')::uuid,
        'telegram', 'https://t.me/squid_kor_update/987654320'
    );
    if observed ->> 'external_url'
            <> 'https://t.me/squid_kor_update/987654320'
       or (select to_jsonb(p.*) from public.publications p
           where p.id = publication_id) is distinct from publication_before
       or (select to_jsonb(j.*) from public.jobs j where j.id = job_id)
            is distinct from job_before
       or (select to_jsonb(a.*)
           from private.exact_telegram_delivery_unknown_approvals a
           where a.publication_id = publication_id)
            is distinct from approval_before
       or (select to_jsonb(r.*)
           from private.exact_telegram_delivery_unknown_resolutions r
           where r.publication_id = publication_id)
            is distinct from resolution_before then
        raise exception 'late observation rewrote unknown attempt or resolution';
    end if;
end
$test$;

do $test$
#variable_conflict use_variable
declare
    context jsonb;
    inspected jsonb;
    observed jsonb;
    statement text;
    publication_id uuid;
    job_id uuid;
begin
    context := pg_temp.resolution_context(
        pg_temp.create_delivery_unknown_fixture(
            '9e100000-0000-4000-8000-000000000002',
            '9e300000-0000-4000-8000-000000000002',
            '9e400000-0000-4000-8000-000000000002',
            'resolution-worker-02', repeat('d', 64), repeat('2', 64),
            'A synthetic canonical receipt uses observation, never resolution.',
            'resolution-security-review-02'
        ), '2'
    );
    publication_id := (context ->> 'publication_id')::uuid;
    job_id := (context ->> 'job_id')::uuid;

    -- Freeze triggers must pass NEW for unrelated rows, not silently return OLD.
    update public.publications set last_error = 'unresolved-passthrough'
    where id = publication_id;
    update public.jobs set last_error_message = 'unresolved-passthrough'
    where id = job_id;
    if (select p.last_error from public.publications p
        where p.id = publication_id) <> 'unresolved-passthrough'
       or (select j.last_error_message from public.jobs j
           where j.id = job_id) <> 'unresolved-passthrough' then
        raise exception 'global freeze trigger swallowed unrelated row update';
    end if;

    execute 'set local role coineasy_telegram_resolution';
    perform set_config(
        'request.jwt.claims',
        pg_temp.resolution_claims(context, 'inspect')::text, true
    );
    inspected := pg_temp.inspect_resolution(context);
    context := context || jsonb_build_object(
        'approval_subject_sha256', inspected ->> 'approval_subject_sha256'
    );
    perform set_config(
        'request.jwt.claims',
        pg_temp.resolution_claims(context, 'approve')::text, true
    );
    perform pg_temp.approve_resolution(context);
    execute 'reset role';

    -- A positive observation arriving after inspection/approval must still
    -- reject resolution under the same item lock and must not erase approval.
    observed := public.record_manual_publication_observation(
        (context ->> 'workspace_id')::uuid,
        (context ->> 'content_item_id')::uuid,
        (context ->> 'content_version_id')::uuid,
        'telegram', 'https://t.me/squid_kor_update/987654321'
    );
    if observed ->> 'external_url'
            <> 'https://t.me/squid_kor_update/987654321' then
        raise exception 'manual observation fixture did not persist its receipt';
    end if;
    execute 'set local role coineasy_telegram_resolution';
    perform set_config(
        'request.jwt.claims',
        pg_temp.resolution_claims(context, 'inspect')::text, true
    );
    perform pg_temp.expect_sqlstate(
        format('select pg_temp.inspect_resolution(%L::jsonb)', context),
        '23505', 'manual observation before unknown resolution'
    );
    perform set_config(
        'request.jwt.claims',
        pg_temp.resolution_claims(context, 'resolve')::text, true
    );
    perform pg_temp.expect_sqlstate(
        format('select pg_temp.resolve_resolution(%L::jsonb)', context),
        '23505', 'positive observation invalidates an earlier exact approval'
    );
    execute 'reset role';
    if (select count(*)
        from private.exact_telegram_delivery_unknown_approvals a
        where a.publication_id = publication_id) <> 1 or exists (
        select 1 from private.exact_telegram_delivery_unknown_resolutions r
        where r.publication_id = publication_id
    ) then
        raise exception 'manual observation rewrote approval or leaked resolution';
    end if;
end
$test$;

rollback;
