-- Integration smoke for a database that has applied the Content Studio migration.
-- Run as the database owner (Supabase CLI test database or an isolated staging DB).
-- Every row is rolled back.

begin;

insert into auth.users (id)
values
    ('10000000-0000-0000-0000-000000000001'),
    ('10000000-0000-0000-0000-000000000002'),
    ('10000000-0000-0000-0000-000000000003');

-- A trusted server may bootstrap an ownerless workspace before team Auth is
-- configured. Browser roles remain unable to do this because their INSERT
-- policy requires created_by = auth.uid().
insert into public.workspaces (id, name, slug, created_by)
values (
    '20000000-0000-0000-0000-000000000099',
    'Service Bootstrap Security Test',
    'service-bootstrap-security-test',
    null
);

do $test$
begin
    if exists (
        select 1 from public.workspace_members
        where workspace_id = '20000000-0000-0000-0000-000000000099'
    ) then
        raise exception 'ownerless service workspace gained a fake member';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    '20000000-0000-0000-0000-000000000001',
    'Content Studio Security Test',
    'content-studio-security-test',
    '10000000-0000-0000-0000-000000000001'
);

insert into public.workspace_members (workspace_id, user_id, role, status, invited_by)
values
    (
        '20000000-0000-0000-0000-000000000001',
        '10000000-0000-0000-0000-000000000002',
        'editor',
        'active',
        '10000000-0000-0000-0000-000000000001'
    ),
    (
        '20000000-0000-0000-0000-000000000001',
        '10000000-0000-0000-0000-000000000003',
        'viewer',
        'active',
        '10000000-0000-0000-0000-000000000001'
    );

insert into public.workspace_clients (
    workspace_id, client_id, display_name, created_by
)
values
    (
        '20000000-0000-0000-0000-000000000001',
        'squid',
        'Squid',
        '10000000-0000-0000-0000-000000000001'
    ),
    (
        '20000000-0000-0000-0000-000000000001',
        'yellow',
        'Yellow',
        '10000000-0000-0000-0000-000000000001'
    );

insert into public.source_feeds (
    id, workspace_id, client_id, provider, name, credential_ref, created_by
)
values (
    '50000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    'squid',
    'x',
    'Squid X',
    'server-secret-reference',
    '10000000-0000-0000-0000-000000000001'
);

insert into public.source_items (
    id, workspace_id, client_id, source_feed_id, source_type, canonical_url,
    body, raw_payload, source_hash, ingested_by
)
values (
    '51000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    'squid',
    '50000000-0000-0000-0000-000000000001',
    'tweet',
    'https://x.com/squidrouter/status/security-test',
    'facts',
    '{"provider_token":"must-not-leak"}'::jsonb,
    repeat('a', 64),
    '10000000-0000-0000-0000-000000000001'
);

insert into public.content_items (
    id, workspace_id, client_id, content_kind, title, status, created_by
)
values
    (
        '30000000-0000-0000-0000-000000000001',
        '20000000-0000-0000-0000-000000000001',
        'squid',
        'daily_news',
        'Squid review item',
        'needs_review',
        '10000000-0000-0000-0000-000000000001'
    ),
    (
        '30000000-0000-0000-0000-000000000002',
        '20000000-0000-0000-0000-000000000001',
        'squid',
        'article',
        'Squid draft item',
        'draft',
        '10000000-0000-0000-0000-000000000001'
    ),
    (
        '30000000-0000-0000-0000-000000000003',
        '20000000-0000-0000-0000-000000000001',
        'yellow',
        'tutorial',
        'Yellow draft item',
        'draft',
        '10000000-0000-0000-0000-000000000001'
    ),
    (
        '30000000-0000-0000-0000-000000000005',
        '20000000-0000-0000-0000-000000000001',
        'squid',
        'tutorial',
        'Squid mock tutorial',
        'needs_review',
        '10000000-0000-0000-0000-000000000001'
    );

insert into public.content_versions (
    id, workspace_id, content_item_id, version_number, prompt_version, title,
    content, generation_meta, created_by
)
values
    (
        '40000000-0000-0000-0000-000000000001',
        '20000000-0000-0000-0000-000000000001',
        '30000000-0000-0000-0000-000000000001',
        1,
        'security-test@1',
        'Squid review v1',
        '{"summary":"one"}'::jsonb,
        '{"mock_mode":false,"fact_check":{"schema_version":"1.0","policy_version":"double-fact-check@1","content_kind":"daily_news","status":"review","human_review_required":true,"input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","output_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","checks":[{"id":"source_evidence","status":"review","label":"Source evidence","detail":"Human verification fixture.","metrics":{}},{"id":"output_claims","status":"pass","label":"Output claims","detail":"Output fixture.","metrics":{}}]}}'::jsonb,
        '10000000-0000-0000-0000-000000000001'
    ),
    (
        '40000000-0000-0000-0000-000000000002',
        '20000000-0000-0000-0000-000000000001',
        '30000000-0000-0000-0000-000000000002',
        1,
        'security-test@1',
        'Squid draft v1',
        '{"summary":"two"}'::jsonb,
        '{}'::jsonb,
        '10000000-0000-0000-0000-000000000001'
    ),
    (
        '40000000-0000-0000-0000-000000000003',
        '20000000-0000-0000-0000-000000000001',
        '30000000-0000-0000-0000-000000000003',
        1,
        'security-test@1',
        'Yellow draft v1',
        '{"summary":"three"}'::jsonb,
        '{}'::jsonb,
        '10000000-0000-0000-0000-000000000001'
    ),
    (
        '40000000-0000-0000-0000-000000000005',
        '20000000-0000-0000-0000-000000000001',
        '30000000-0000-0000-0000-000000000005',
        1,
        'edu-carousel@1',
        'Squid mock tutorial v1',
        '{"summary":"mock tutorial"}'::jsonb,
        '{"mock_mode":true}'::jsonb,
        '10000000-0000-0000-0000-000000000001'
    );

update public.content_items
set current_version_id = case id
    when '30000000-0000-0000-0000-000000000001'
        then '40000000-0000-0000-0000-000000000001'::uuid
    when '30000000-0000-0000-0000-000000000002'
        then '40000000-0000-0000-0000-000000000002'::uuid
    when '30000000-0000-0000-0000-000000000003'
        then '40000000-0000-0000-0000-000000000003'::uuid
    else '40000000-0000-0000-0000-000000000005'::uuid
end;

insert into public.jobs (
    id, workspace_id, client_id, content_item_id, job_kind, input,
    idempotency_key, created_by
)
values (
    '60000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    'squid',
    '30000000-0000-0000-0000-000000000002',
    'generate',
    '{"provider_token":"must-not-leak"}'::jsonb,
    'security-seed-job',
    '10000000-0000-0000-0000-000000000001'
);

insert into public.publications (
    id, workspace_id, client_id, content_item_id, content_version_id, channel,
    request_payload, response_payload, requested_by
)
values (
    '70000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    'squid',
    '30000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000001',
    'telegram',
    '{"bot_token":"must-not-leak"}'::jsonb,
    '{"provider_response":"must-not-leak"}'::jsonb,
    '10000000-0000-0000-0000-000000000001'
);

insert into public.assets (
    id, workspace_id, content_item_id, content_version_id, asset_kind,
    storage_path, mime_type, created_by
)
values (
    '80000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    '30000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000001',
    'png',
    '20000000-0000-0000-0000-000000000001/squid/80000000-0000-0000-0000-000000000001/card.png',
    'image/png',
    '10000000-0000-0000-0000-000000000001'
);

insert into storage.objects (bucket_id, name)
values (
    'content-studio',
    '20000000-0000-0000-0000-000000000001/squid/80000000-0000-0000-0000-000000000001/card.png'
);

-- Composite keys reject a valid version belonging to a different item.
do $test$
begin
    begin
        insert into public.assets (
            workspace_id, content_item_id, content_version_id, asset_kind,
            storage_path, mime_type
        ) values (
            '20000000-0000-0000-0000-000000000001',
            '30000000-0000-0000-0000-000000000001',
            '40000000-0000-0000-0000-000000000002',
            'png',
            'cross-item.png',
            'image/png'
        );
        raise exception 'cross-item asset was accepted';
    exception when foreign_key_violation then
        null;
    end;

    begin
        insert into public.publications (
            workspace_id, client_id, content_item_id, content_version_id,
            channel
        ) values (
            '20000000-0000-0000-0000-000000000001',
            'squid',
            '30000000-0000-0000-0000-000000000001',
            '40000000-0000-0000-0000-000000000002',
            'x'
        );
        raise exception 'cross-item publication was accepted';
    exception when foreign_key_violation then
        null;
    end;

    begin
        insert into public.approvals (
            workspace_id, client_id, content_item_id, content_version_id,
            reviewer_id, decision
        ) values (
            '20000000-0000-0000-0000-000000000001',
            'squid',
            '30000000-0000-0000-0000-000000000001',
            '40000000-0000-0000-0000-000000000002',
            '10000000-0000-0000-0000-000000000001',
            'approved'
        );
        raise exception 'cross-item approval was accepted';
    exception when foreign_key_violation then
        null;
    end;

    begin
        insert into public.figma_links (
            workspace_id, content_item_id, content_version_id, file_key, node_id
        ) values (
            '20000000-0000-0000-0000-000000000001',
            '30000000-0000-0000-0000-000000000001',
            '40000000-0000-0000-0000-000000000002',
            'cross-item',
            '1:2'
        );
        raise exception 'cross-item Figma link was accepted';
    exception when foreign_key_violation then
        null;
    end;

    begin
        insert into public.jobs (
            workspace_id, client_id, content_item_id, job_kind
        ) values (
            '20000000-0000-0000-0000-000000000001',
            'yellow',
            '30000000-0000-0000-0000-000000000001',
            'generate'
        );
        raise exception 'cross-client job was accepted';
    exception when foreign_key_violation then
        null;
    end;

    begin
        insert into public.source_items (
            workspace_id, client_id, source_feed_id, source_type, body, source_hash
        ) values (
            '20000000-0000-0000-0000-000000000001',
            'yellow',
            '50000000-0000-0000-0000-000000000001',
            'tweet',
            'wrong client',
            repeat('b', 64)
        );
        raise exception 'cross-client source feed was accepted';
    exception when foreign_key_violation then
        null;
    end;
end
$test$;

set local role authenticated;
select set_config(
    'request.jwt.claim.sub',
    '10000000-0000-0000-0000-000000000002',
    true
);

-- Safe projections remain readable to an editor.
do $test$
begin
    if (select count(*) from public.content_studio_source_feeds) <> 1 then
        raise exception 'safe feed projection is not readable';
    end if;
    if (select count(*) from public.content_studio_source_items) <> 1 then
        raise exception 'safe source projection is not readable';
    end if;
    if (select count(*) from public.content_studio_job_statuses) <> 1 then
        raise exception 'safe job projection is not readable';
    end if;
    if (select count(*) from public.content_studio_publication_statuses) <> 1 then
        raise exception 'safe publication projection is not readable';
    end if;
end
$test$;

-- Raw secrets, queue locks/payloads, provider payloads, and direct transitions
-- remain unavailable even to an editor.
do $test$
declare
    deleted_count integer;
begin
    begin
        perform credential_ref from public.source_feeds;
        raise exception 'credential_ref was exposed';
    exception when insufficient_privilege then null;
    end;
    begin
        perform raw_payload from public.source_items;
        raise exception 'raw source payload was exposed';
    exception when insufficient_privilege then null;
    end;
    begin
        perform input from public.jobs;
        raise exception 'job input was exposed';
    exception when insufficient_privilege then null;
    end;
    begin
        perform request_payload from public.publications;
        raise exception 'publication payload was exposed';
    exception when insufficient_privilege then null;
    end;
    begin
        update public.content_items
        set status = 'published'
        where id = '30000000-0000-0000-0000-000000000002';
        raise exception 'editor directly changed workflow status';
    exception when insufficient_privilege then null;
    end;
    begin
        insert into public.jobs (
            workspace_id, client_id, content_item_id, job_kind
        ) values (
            '20000000-0000-0000-0000-000000000001',
            'squid',
            '30000000-0000-0000-0000-000000000002',
            'publish'
        );
        raise exception 'editor directly inserted a job';
    exception when insufficient_privilege then null;
    end;
    begin
        insert into public.publications (
            workspace_id, client_id, content_item_id, content_version_id, channel
        ) values (
            '20000000-0000-0000-0000-000000000001',
            'squid',
            '30000000-0000-0000-0000-000000000002',
            '40000000-0000-0000-0000-000000000002',
            'x'
        );
        raise exception 'editor directly inserted a publication';
    exception when insufficient_privilege then null;
    end;
    begin
        insert into public.figma_links (
            workspace_id, content_item_id, content_version_id, file_key, node_id,
            created_by
        ) values (
            '20000000-0000-0000-0000-000000000001',
            '30000000-0000-0000-0000-000000000001',
            '40000000-0000-0000-0000-000000000001',
            'direct-editor-write',
            '1:2',
            '10000000-0000-0000-0000-000000000002'
        );
        raise exception 'editor directly inserted a Figma link';
    exception when insufficient_privilege then null;
    end;
    begin
        delete from public.assets
        where id = '80000000-0000-0000-0000-000000000001';
        raise exception 'editor deleted an immutable asset';
    exception when insufficient_privilege then null;
    end;
    begin
        perform public.record_tutorial_generation(
            '90000000-0000-4000-8000-000000000001',
            '20000000-0000-0000-0000-000000000001',
            'squid',
            'editor must not catalog',
            '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'::jsonb,
            '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'::jsonb,
            '[{"number":1,"filename":"lesson_01.png","storage_path":"20000000-0000-0000-0000-000000000001/squid/91000000-0000-4000-8000-000000000001/lesson_01.png","byte_size":25,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","width":1080,"height":1350}]'::jsonb
        );
        raise exception 'editor cataloged a tutorial';
    exception when insufficient_privilege then null;
    end;
    begin
        perform public.get_tutorial_generation(
            '90000000-0000-4000-8000-000000000001',
            '20000000-0000-0000-0000-000000000001',
            'squid'
        );
        raise exception 'editor looked up a server-only tutorial generation';
    exception when insufficient_privilege then null;
    end;
    delete from storage.objects
    where bucket_id = 'content-studio';
    get diagnostics deleted_count = row_count;
    if deleted_count <> 0 then
        raise exception 'editor deleted an immutable storage object';
    end if;
end
$test$;

-- An editor cannot associate an unapproved draft version with Figma.
do $test$
begin
    begin
        perform public.record_approved_figma_link(
            '30000000-0000-0000-0000-000000000002',
            '40000000-0000-0000-0000-000000000002',
            'security-figma-file',
            '10:20'
        );
        raise exception 'unapproved Figma link was accepted';
    exception when check_violation then null;
    end;
end
$test$;

-- Editors use atomic workflow RPCs instead of raw status/job/publication writes.
select public.queue_content_generation(
    '30000000-0000-0000-0000-000000000002',
    '{"template_style":"editorial"}'::jsonb,
    'security-generation-1'
);

-- An identical retry returns the existing job even after the item became queued.
select public.queue_content_generation(
    '30000000-0000-0000-0000-000000000002',
    '{"template_style":"editorial"}'::jsonb,
    'security-generation-1'
);

do $test$
begin
    if (select status from public.content_items
        where id = '30000000-0000-0000-0000-000000000002') <> 'queued' then
        raise exception 'queue RPC did not transition content';
    end if;
end
$test$;

-- Test/sample generations may be rejected, but they cannot cross either the
-- approval or publication boundary even if a caller retries the workflow RPCs.
do $test$
begin
    begin
        perform public.review_content_version(
            '30000000-0000-0000-0000-000000000005',
            '40000000-0000-0000-0000-000000000005',
            'approved',
            'must stay a sample'
        );
        raise exception 'legacy authenticated approval RPC remained executable';
    exception when insufficient_privilege then null;
    end;
end
$test$;

reset role;
do $test$
declare
    failure_message text;
begin
    begin
        perform public.record_studio_content_review_v2(
            '20000000-0000-0000-0000-000000000001',
            '30000000-0000-0000-0000-000000000005',
            '40000000-0000-0000-0000-000000000005',
            'approved',
            'double-fact-check@1',
            true,
            true,
            '{}'::text[],
            'must stay a sample',
            'security-mock-v2-approval'
        );
        raise exception 'mock tutorial was approved';
    exception when check_violation then
        get stacked diagnostics failure_message = message_text;
        if failure_message <> 'mock/test content cannot be approved' then
            raise exception 'mock approval failed for the wrong reason: %',
                failure_message;
        end if;
    end;
    if (select status from public.content_items
        where id = '30000000-0000-0000-0000-000000000005')
       <> 'needs_review' then
        raise exception 'mock approval failure changed workflow status';
    end if;

    perform public.record_studio_content_review_v2(
        '20000000-0000-0000-0000-000000000001',
        '30000000-0000-0000-0000-000000000005',
        '40000000-0000-0000-0000-000000000005',
        'rejected',
        'double-fact-check@1',
        false,
        false,
        '{}'::text[],
        'sample rejection remains allowed',
        'security-mock-v2-rejection'
    );
    if (select status from public.content_items
        where id = '30000000-0000-0000-0000-000000000005')
       <> 'rejected' then
        raise exception 'mock tutorial could not be rejected';
    end if;

end
$test$;

set local role authenticated;
select set_config(
    'request.jwt.claim.sub',
    '10000000-0000-0000-0000-000000000002',
    true
);
do $test$
declare
    failure_message text;
begin
    begin
        perform public.request_content_publication(
            '30000000-0000-0000-0000-000000000005',
            '40000000-0000-0000-0000-000000000005',
            'x',
            null,
            'security-mock-publication'
        );
        raise exception 'mock tutorial was queued for publication';
    exception when check_violation then
        get stacked diagnostics failure_message = message_text;
        if failure_message <> 'mock/test content cannot be published' then
            raise exception 'mock publication failed for the wrong reason: %',
                failure_message;
        end if;
    end;
end
$test$;

reset role;
select public.record_studio_content_review_v2(
    '20000000-0000-0000-0000-000000000001',
    '30000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000001',
    'approved',
    'double-fact-check@1',
    true,
    true,
    '{}'::text[],
    'security smoke approved',
    'security-v2-approval'
);
set local role authenticated;
select set_config(
    'request.jwt.claim.sub',
    '10000000-0000-0000-0000-000000000002',
    true
);

-- Figma links are written only through the validating RPC. Retrying the same
-- frame is an upsert and remains attached to the current approved version.
select public.record_approved_figma_link(
    '30000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000001',
    'security-figma-file',
    '10:20',
    '2026 W30',
    'Squid',
    '{"plugin":"security-smoke"}'::jsonb
);
select public.record_approved_figma_link(
    '30000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000001',
    'security-figma-file',
    '10:20',
    '2026 W30',
    'Squid',
    '{"plugin":"security-smoke-retry"}'::jsonb
);

do $test$
begin
    if (select count(*) from public.figma_links
        where file_key = 'security-figma-file' and node_id = '10:20') <> 1 then
        raise exception 'Figma link RPC retry was not idempotent';
    end if;
    begin
        update public.figma_links
        set page_name = 'direct editor update'
        where file_key = 'security-figma-file';
        raise exception 'editor directly updated a Figma link';
    exception when insufficient_privilege then null;
    end;
    begin
        delete from public.figma_links
        where file_key = 'security-figma-file';
        raise exception 'editor directly deleted a Figma link';
    exception when insufficient_privilege then null;
    end;
end
$test$;

select public.request_content_publication(
    '30000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000001',
    'x',
    now() + interval '1 hour',
    'security-publication-1'
);

-- The idempotency lookup precedes time validation, so a retry remains safe even
-- after the original schedule is in the past.
select public.request_content_publication(
    '30000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000001',
    'x',
    now() - interval '1 hour',
    'security-publication-1'
);

-- Scheduling one channel must not consume the approval or block another.
select public.request_content_publication(
    '30000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000001',
    'telegram',
    now() + interval '2 hours',
    'security-publication-telegram-1'
);

do $test$
begin
    if (select count(*) from public.jobs
        where content_item_id = '30000000-0000-0000-0000-000000000001'
          and job_kind = 'publish') <> 2 then
        raise exception 'multi-channel publication did not queue both channels';
    end if;
    if (select status from public.content_items
        where id = '30000000-0000-0000-0000-000000000001') <> 'scheduled' then
        raise exception 'multi-channel publication lost scheduled status';
    end if;
    if (select current_version_id from public.content_items
        where id = '30000000-0000-0000-0000-000000000001')
       is distinct from '40000000-0000-0000-0000-000000000001'::uuid then
        raise exception 'multi-channel publication changed the approved version';
    end if;
    if (select scheduled_for from public.content_items
        where id = '30000000-0000-0000-0000-000000000001')
       > now() + interval '90 minutes' then
        raise exception 'multi-channel publication did not preserve earliest schedule';
    end if;
end
$test$;

-- An identical retry returns the existing publication after status became scheduled.
select public.request_content_publication(
    '30000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000001',
    'x',
    now() + interval '1 hour',
    'security-publication-1'
);

-- A viewer can read but cannot trigger generation.
select set_config(
    'request.jwt.claim.sub',
    '10000000-0000-0000-0000-000000000003',
    true
);

do $test$
begin
    if (select count(*) from public.content_items) <> 4 then
        raise exception 'viewer cannot read workspace content';
    end if;
    begin
        perform public.queue_content_generation(
            '30000000-0000-0000-0000-000000000003',
            '{}'::jsonb,
            'security-viewer-denied'
        );
        raise exception 'viewer queued generation';
    exception when insufficient_privilege then null;
    end;
end
$test$;

reset role;

-- Workspace deletion is a dedicated retention workflow, never a direct owner
-- table mutation that could cascade through immutable history.
set local role authenticated;
select set_config(
    'request.jwt.claim.sub',
    '10000000-0000-0000-0000-000000000001',
    true
);
do $test$
begin
    begin
        delete from public.workspaces
        where id = '20000000-0000-0000-0000-000000000001';
        raise exception 'owner directly deleted a workspace';
    exception when insufficient_privilege then null;
    end;
end
$test$;

reset role;

-- Even the trusted service role can append, but cannot rewrite or erase the
-- immutable version/review/audit history.
set local role service_role;

-- A server-side tutorial generation is cataloged atomically. The stable request
-- UUID makes retries return the same item/version/assets after an uncertain HTTP
-- response, while each asset UUID stays identical to its Storage path folder.
do $test$
begin
    begin
        perform public.record_tutorial_generation(
            '92000000-0000-4000-8000-000000000000',
            '20000000-0000-0000-0000-000000000001',
            'squid', 'mismatched request hash',
            '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'::jsonb,
            '{"request_hash":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}'::jsonb,
            null::jsonb
        );
        raise exception 'mismatched tutorial request hash was accepted';
    exception when invalid_parameter_value then null;
    end;
    begin
        perform public.record_tutorial_generation(
            '92000000-0000-4000-8000-000000000001',
            '20000000-0000-0000-0000-000000000001',
            'squid', 'null slides',
            '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'::jsonb,
            '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'::jsonb,
            null::jsonb
        );
        raise exception 'null tutorial slides were accepted';
    exception when invalid_parameter_value then null;
    end;
    begin
        perform public.record_tutorial_generation(
            '92000000-0000-4000-8000-000000000002',
            '20000000-0000-0000-0000-000000000001',
            'squid', 'null fields',
            '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'::jsonb,
            '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'::jsonb,
            '[{"number":1,"filename":null,"storage_path":"20000000-0000-0000-0000-000000000001/squid/92000000-0000-4000-8000-000000000012/unexpected.bin","byte_size":null,"sha256":null,"width":null,"height":null}]'::jsonb
        );
        raise exception 'null tutorial slide fields were accepted';
    exception when invalid_parameter_value then null;
    end;
    begin
        perform public.record_tutorial_generation(
            '92000000-0000-4000-8000-000000000003',
            '20000000-0000-0000-0000-000000000001',
            'squid', 'wrong order',
            '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'::jsonb,
            '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'::jsonb,
            '[{"number":2,"filename":"lesson_02.png","storage_path":"20000000-0000-0000-0000-000000000001/squid/92000000-0000-4000-8000-000000000013/lesson_02.png","byte_size":25,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","width":1080,"height":1350},{"number":1,"filename":"lesson_01.png","storage_path":"20000000-0000-0000-0000-000000000001/squid/92000000-0000-4000-8000-000000000014/lesson_01.png","byte_size":25,"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","width":1080,"height":1350}]'::jsonb
        );
        raise exception 'out-of-order tutorial slides were accepted';
    exception when invalid_parameter_value then null;
    end;
    begin
        perform public.record_tutorial_generation(
            '92000000-0000-4000-8000-000000000004',
            '20000000-0000-0000-0000-000000000001',
            'squid', 'missing object',
            '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'::jsonb,
            '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'::jsonb,
            '[{"number":1,"filename":"lesson_01.png","storage_path":"20000000-0000-0000-0000-000000000001/squid/92000000-0000-4000-8000-000000000015/lesson_01.png","byte_size":25,"sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","width":1080,"height":1350}]'::jsonb
        );
        raise exception 'missing tutorial storage object was accepted';
    exception when check_violation then null;
    end;
end
$test$;

insert into storage.objects (bucket_id, name)
values (
    'content-studio',
    '20000000-0000-0000-0000-000000000001/squid/91000000-0000-4000-8000-000000000001/lesson_01.png'
);
select public.record_tutorial_generation(
    '90000000-0000-4000-8000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    'squid',
    'Squid catalog smoke',
    '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","lesson_count":1,"source":{"mode":"provided"}}'::jsonb,
    '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","renderer":"railway"}'::jsonb,
    '[{"number":1,"filename":"lesson_01.png","storage_path":"20000000-0000-0000-0000-000000000001/squid/91000000-0000-4000-8000-000000000001/lesson_01.png","byte_size":25,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","width":1080,"height":1350}]'::jsonb
);
select public.record_tutorial_generation(
    '90000000-0000-4000-8000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    'squid',
    'Squid catalog smoke',
    '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","lesson_count":1,"source":{"mode":"provided"}}'::jsonb,
    '{"request_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","renderer":"railway"}'::jsonb,
    '[{"number":1,"filename":"lesson_01.png","storage_path":"20000000-0000-0000-0000-000000000001/squid/91000000-0000-4000-8000-000000000001/lesson_01.png","byte_size":25,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","width":1080,"height":1350}]'::jsonb
);

do $test$
begin
    begin
        perform public.record_tutorial_generation(
            '90000000-0000-4000-8000-000000000001',
            '20000000-0000-0000-0000-000000000001',
            'squid',
            'Squid catalog smoke',
            '{"request_hash":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","lesson_count":1,"source":{"mode":"provided"}}'::jsonb,
            '{"request_hash":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","renderer":"railway"}'::jsonb,
            '[{"number":1,"filename":"lesson_01.png","storage_path":"20000000-0000-0000-0000-000000000001/squid/91000000-0000-4000-8000-000000000001/lesson_01.png","byte_size":25,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","width":1080,"height":1350}]'::jsonb
        );
        raise exception 'tutorial request UUID accepted a different request hash';
    exception when unique_violation then null;
    end;
end
$test$;

do $test$
declare
    lookup_result jsonb;
begin
    if (select count(*) from public.content_items
        where id = '90000000-0000-4000-8000-000000000001') <> 1
       or (select count(*) from public.content_versions
        where content_item_id = '90000000-0000-4000-8000-000000000001') <> 1
       or (select count(*) from public.event_log
        where entity_id = '90000000-0000-4000-8000-000000000001'
          and event_type = 'tutorial_generated') <> 1 then
        raise exception 'tutorial catalog RPC was not idempotent';
    end if;
    if not exists (
        select 1 from public.assets
        where id = '91000000-0000-4000-8000-000000000001'
          and content_item_id = '90000000-0000-4000-8000-000000000001'
          and storage_path = '20000000-0000-0000-0000-000000000001/squid/91000000-0000-4000-8000-000000000001/lesson_01.png'
    ) then
        raise exception 'tutorial catalog asset path was not aligned';
    end if;
    lookup_result := public.get_tutorial_generation(
        '90000000-0000-4000-8000-000000000001',
        '20000000-0000-0000-0000-000000000001',
        'squid'
    );
    if lookup_result ->> 'content_item_id'
       <> '90000000-0000-4000-8000-000000000001'
       or lookup_result ->> 'content_version_id' is null
       or jsonb_array_length(lookup_result -> 'slides') <> 1
       or lookup_result -> 'slides' -> 0 ->> 'asset_id'
          <> '91000000-0000-4000-8000-000000000001' then
        raise exception 'tutorial catalog lookup did not return the committed generation';
    end if;

    -- The immutable deliverables list is the lookup source of truth. Removing an
    -- expected catalog row or its Storage object must fail closed; either deletion
    -- is rolled back with this inner exception block.
    begin
        delete from public.assets
        where id = '91000000-0000-4000-8000-000000000001';
        perform public.get_tutorial_generation(
            '90000000-0000-4000-8000-000000000001',
            '20000000-0000-0000-0000-000000000001',
            'squid'
        );
        raise exception 'tutorial lookup accepted a missing catalog asset';
    exception when check_violation then null;
    end;
    begin
        delete from storage.objects
        where bucket_id = 'content-studio'
          and name = '20000000-0000-0000-0000-000000000001/squid/91000000-0000-4000-8000-000000000001/lesson_01.png';
        perform public.get_tutorial_generation(
            '90000000-0000-4000-8000-000000000001',
            '20000000-0000-0000-0000-000000000001',
            'squid'
        );
        raise exception 'tutorial lookup accepted a missing Storage object';
    exception when check_violation then null;
    end;

    -- A mutable extra asset is not part of the immutable version deliverables and
    -- must never be introduced into a retry response.
    insert into storage.objects (bucket_id, name)
    values (
        'content-studio',
        '20000000-0000-0000-0000-000000000001/squid/91000000-0000-4000-8000-000000000002/lesson_02.png'
    );
    insert into public.assets (
        id, workspace_id, content_item_id, content_version_id, asset_kind,
        storage_bucket, storage_path, mime_type, byte_size, sha256, width,
        height, metadata
    ) values (
        '91000000-0000-4000-8000-000000000002',
        '20000000-0000-0000-0000-000000000001',
        '90000000-0000-4000-8000-000000000001',
        (lookup_result ->> 'content_version_id')::uuid,
        'carousel_page',
        'content-studio',
        '20000000-0000-0000-0000-000000000001/squid/91000000-0000-4000-8000-000000000002/lesson_02.png',
        'image/png',
        25,
        'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
        1080,
        1350,
        '{"number":2,"filename":"lesson_02.png"}'::jsonb
    );
    lookup_result := public.get_tutorial_generation(
        '90000000-0000-4000-8000-000000000001',
        '20000000-0000-0000-0000-000000000001',
        'squid'
    );
    if jsonb_array_length(lookup_result -> 'slides') <> 1
       or lookup_result -> 'slides' -> 0 ->> 'asset_id'
          <> '91000000-0000-4000-8000-000000000001' then
        raise exception 'tutorial lookup included an asset outside immutable deliverables';
    end if;
    delete from public.assets
    where id = '91000000-0000-4000-8000-000000000002';
    delete from storage.objects
    where bucket_id = 'content-studio'
      and name = '20000000-0000-0000-0000-000000000001/squid/91000000-0000-4000-8000-000000000002/lesson_02.png';
end
$test$;

insert into public.content_versions (
    id, workspace_id, content_item_id, version_number, prompt_version, title,
    content, created_by
) values (
    '40000000-0000-0000-0000-000000000004',
    '20000000-0000-0000-0000-000000000001',
    '30000000-0000-0000-0000-000000000002',
    2,
    'security-test@2',
    'service append',
    '{"summary":"append-only"}'::jsonb,
    '10000000-0000-0000-0000-000000000001'
);
insert into public.approvals (
    workspace_id, client_id, content_item_id, content_version_id,
    reviewer_id, decision, comment
) values (
    '20000000-0000-0000-0000-000000000001',
    'squid',
    '30000000-0000-0000-0000-000000000002',
    '40000000-0000-0000-0000-000000000004',
    '10000000-0000-0000-0000-000000000001',
    'commented',
    'service append smoke'
);
insert into public.event_log (
    workspace_id, actor_id, entity_type, entity_id, event_type, data
) values (
    '20000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    'content_item',
    '30000000-0000-0000-0000-000000000002',
    'service_append_smoke',
    '{}'::jsonb
);

do $test$
begin
    begin
        update public.content_versions
        set title = 'rewritten'
        where id = '40000000-0000-0000-0000-000000000004';
        raise exception 'service role rewrote a content version';
    exception when insufficient_privilege then null;
    end;
    begin
        delete from public.approvals
        where comment = 'service append smoke';
        raise exception 'service role deleted an approval';
    exception when insufficient_privilege then null;
    end;
    begin
        update public.event_log
        set event_type = 'rewritten'
        where event_type = 'service_append_smoke';
        raise exception 'service role rewrote event history';
    exception when insufficient_privilege then null;
    end;
    begin
        delete from public.content_items
        where id = '30000000-0000-0000-0000-000000000002';
        raise exception 'service role deleted a versioned content item';
    exception when insufficient_privilege then null;
    end;
    begin
        delete from public.workspaces
        where id = '20000000-0000-0000-0000-000000000001';
        raise exception 'service role deleted a workspace history';
    exception when insufficient_privilege then null;
    end;
end
$test$;

reset role;

-- The last active owner is protected in ordinary sequential transactions too.
do $test$
begin
    begin
        delete from public.workspace_members
        where workspace_id = '20000000-0000-0000-0000-000000000001'
          and user_id = '10000000-0000-0000-0000-000000000001';
        raise exception 'last workspace owner was deleted';
    exception when check_violation then null;
    end;
end
$test$;

rollback;
