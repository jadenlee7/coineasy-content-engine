-- Durable fixture and exact approval for the two-session resolve race.
-- This runs only in the disposable PostgreSQL CI database after all migrations.

\set ON_ERROR_STOP on
set timezone = 'UTC';

drop schema if exists exact_telegram_resolution_concurrency cascade;
create schema exact_telegram_resolution_concurrency;

create table exact_telegram_resolution_concurrency.fixture (
    singleton boolean primary key default true check (singleton),
    workspace_id uuid not null,
    content_item_id uuid not null,
    content_version_id uuid not null,
    publication_id uuid not null,
    job_id uuid not null,
    resolution_id uuid not null,
    operator_approval_id uuid not null,
    inspected_by text not null,
    approved_by text not null,
    resolved_by text not null,
    expires_at timestamptz not null,
    release_sha text not null,
    public_audit jsonb not null,
    public_audit_sha256 text not null,
    approval_subject_sha256 text,
    item_before jsonb not null,
    version_before jsonb not null,
    publication_before jsonb not null,
    job_before jsonb not null,
    publication_approval_before jsonb not null,
    asset_before jsonb not null,
    resolution_approval_before jsonb,
    second_waited_for_first boolean not null default false,
    publication_count_before bigint not null,
    job_count_before bigint not null,
    deadlock_count_before bigint not null
);

create table exact_telegram_resolution_concurrency.results (
    session_name text primary key check (session_name in ('first', 'second')),
    result jsonb not null,
    recorded_at timestamptz not null default pg_catalog.clock_timestamp()
);

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
                    'detail', 'Concurrency fixture human verification.',
                    'metrics', '{}'::jsonb
                ),
                jsonb_build_object(
                    'id', 'output_claims',
                    'status', 'pass',
                    'label', 'Output claims',
                    'detail', 'Concurrency fixture output.',
                    'metrics', '{}'::jsonb
                )
            )
        )
    )
$$;

create function pg_temp.create_delivery_unknown_fixture(
    target_workspace_id uuid,
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
    target_storage_path := target_workspace_id::text || '/squid/'
        || target_asset_id::text || '/news-card.png';
    generated := public.record_generated_content(
        target_content_item_id,
        target_workspace_id,
        'squid',
        'daily_news',
        'Exact Telegram resolution concurrency fixture',
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
        'exact-telegram-resolution-concurrency@1'
    );
    target_version_id := (generated ->> 'content_version_id')::uuid;

    perform public.record_studio_content_review_v2(
        target_workspace_id,
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
        target_workspace_id,
        target_content_item_id,
        target_version_id,
        target_request_id::text
    );
    claimed := public.claim_exact_telegram_publication_job(
        target_workspace_id,
        target_worker_id,
        300
    );
    if claimed ->> 'job_id' is distinct from requested ->> 'job_id'
       or claimed ->> 'publication_id'
            is distinct from requested ->> 'publication_id' then
        raise exception 'concurrency fixture claimed a different job';
    end if;
    marked := public.mark_exact_telegram_attempt_started(
        (claimed ->> 'job_id')::uuid,
        target_worker_id,
        target_request_sha256
    );
    if marked ->> 'status' <> 'publishing' then
        raise exception 'concurrency fixture did not cross the attempt fence';
    end if;
    failed := public.fail_exact_telegram_publication_job(
        (claimed ->> 'job_id')::uuid,
        target_worker_id,
        'telegram_delivery_unknown',
        false
    );
    if failed ->> 'status' <> 'delivery_unknown'
       or failed ->> 'job_status' <> 'failed' then
        raise exception 'concurrency fixture is not terminal unknown';
    end if;

    update public.publications
    set delivery_started_at = pg_catalog.statement_timestamp()
        - interval '20 minutes'
    where id = (requested ->> 'publication_id')::uuid;

    return jsonb_build_object(
        'content_version_id', target_version_id,
        'publication_id', requested ->> 'publication_id',
        'job_id', requested ->> 'job_id'
    );
end;
$$;

select
    gen_random_uuid() as workspace_id,
    gen_random_uuid() as user_id,
    gen_random_uuid() as content_item_id,
    gen_random_uuid() as asset_id,
    gen_random_uuid() as request_id,
    gen_random_uuid() as resolution_id,
    gen_random_uuid() as operator_approval_id
\gset ids_

insert into public.workspaces (id, name, slug, created_by)
values (
    :'ids_workspace_id'::uuid,
    'Exact Telegram Resolution Concurrency',
    'exact-telegram-resolution-concurrency-'
        || left(:'ids_workspace_id', 8),
    null
);

insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
) values (
    :'ids_workspace_id'::uuid, 'squid', 'Squid', true, null
);

insert into auth.users (id) values (:'ids_user_id'::uuid);

insert into public.workspace_members (
    workspace_id, user_id, role, status, invited_by
) values (
    :'ids_workspace_id'::uuid,
    :'ids_user_id'::uuid,
    'owner',
    'active',
    null
);

insert into storage.objects (bucket_id, name)
values (
    'content-studio',
    :'ids_workspace_id' || '/squid/' || :'ids_asset_id'
        || '/news-card.png'
);

select
    fixture ->> 'content_version_id' as content_version_id,
    fixture ->> 'publication_id' as publication_id,
    fixture ->> 'job_id' as job_id
from (
    select pg_temp.create_delivery_unknown_fixture(
        :'ids_workspace_id'::uuid,
        :'ids_content_item_id'::uuid,
        :'ids_asset_id'::uuid,
        :'ids_request_id'::uuid,
        'telegram-resolution-concurrency-worker',
        repeat('c', 64),
        repeat('1', 64),
        'This exact Telegram result remains unknown and must not be resent.',
        'telegram-resolution-concurrency-review'
    ) as fixture
) as created
\gset fixture_

select
    jsonb_build_object(
        'schema_version', 'telegram-public-channel-audit@1',
        'scan_source', 'public_telegram_web_history',
        'public_channel', 'squid_kor_update',
        'first_message_id', 500,
        'last_message_id', 620,
        'message_count', 121,
        'checked_at', to_char(
            date_trunc('second', pg_catalog.statement_timestamp())
                - interval '1 minute',
            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        ),
        'caption_match_count', 0,
        'png_match_count', 0,
        'snapshot_sha256', repeat('e', 64)
    ) as public_audit,
    date_trunc('second', pg_catalog.statement_timestamp())
        + interval '1 hour' as expires_at
\gset prepared_

insert into exact_telegram_resolution_concurrency.fixture (
    workspace_id,
    content_item_id,
    content_version_id,
    publication_id,
    job_id,
    resolution_id,
    operator_approval_id,
    inspected_by,
    approved_by,
    resolved_by,
    expires_at,
    release_sha,
    public_audit,
    public_audit_sha256,
    item_before,
    version_before,
    publication_before,
    job_before,
    publication_approval_before,
    asset_before,
    publication_count_before,
    job_count_before,
    deadlock_count_before
)
select
    :'ids_workspace_id'::uuid,
    :'ids_content_item_id'::uuid,
    :'fixture_content_version_id'::uuid,
    :'fixture_publication_id'::uuid,
    :'fixture_job_id'::uuid,
    :'ids_resolution_id'::uuid,
    :'ids_operator_approval_id'::uuid,
    'codex:telegram-concurrency-inspect',
    'codex:telegram-concurrency-approve',
    'codex:telegram-concurrency-resolve',
    :'prepared_expires_at'::timestamptz,
    repeat('a', 40),
    :'prepared_public_audit'::jsonb,
    pg_catalog.encode(extensions.digest(
        pg_catalog.convert_to(
            :'prepared_public_audit'::jsonb::text,
            'UTF8'
        ),
        'sha256'
    ), 'hex'),
    pg_catalog.to_jsonb(item),
    pg_catalog.to_jsonb(version),
    pg_catalog.to_jsonb(publication),
    pg_catalog.to_jsonb(job),
    pg_catalog.to_jsonb(publication_approval),
    pg_catalog.to_jsonb(asset),
    (
        select count(*)
        from public.publications as counted
        where counted.workspace_id = :'ids_workspace_id'::uuid
    ),
    (
        select count(*)
        from public.jobs as counted
        where counted.workspace_id = :'ids_workspace_id'::uuid
    ),
    (
        select deadlocks
        from pg_catalog.pg_stat_database
        where datname = pg_catalog.current_database()
    )
from public.publications as publication
join public.jobs as job
  on job.id = :'fixture_job_id'::uuid
 and job.workspace_id = publication.workspace_id
join public.content_items as item
  on item.workspace_id = publication.workspace_id
 and item.id = publication.content_item_id
join public.content_versions as version
  on version.workspace_id = publication.workspace_id
 and version.content_item_id = item.id
 and version.id = publication.content_version_id
join public.approvals as publication_approval
  on publication_approval.workspace_id = publication.workspace_id
 and publication_approval.id =
    (publication.request_payload ->> 'approval_id')::uuid
join public.assets as asset
  on asset.workspace_id = publication.workspace_id
 and asset.id = (publication.request_payload ->> 'asset_id')::uuid
where publication.id = :'fixture_publication_id'::uuid
  and publication.workspace_id = :'ids_workspace_id'::uuid;

select
    workspace_id,
    content_item_id,
    content_version_id,
    publication_id,
    job_id,
    resolution_id,
    operator_approval_id,
    inspected_by,
    approved_by,
    expires_at,
    release_sha,
    public_audit::text as public_audit,
    public_audit_sha256
from exact_telegram_resolution_concurrency.fixture
where singleton
\gset target_

begin;
set local role coineasy_telegram_resolution;
select pg_catalog.set_config(
    'request.jwt.claims',
    pg_catalog.jsonb_build_object(
        'role', 'coineasy_telegram_resolution',
        'workspace_id', :'target_workspace_id',
        'sub', :'target_inspected_by',
        'capability', 'telegram_delivery_unknown_inspect',
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
        'approved_by', :'target_approved_by',
        'expires_at', :'target_expires_at',
        'public_audit_sha256', :'target_public_audit_sha256'
    )::text,
    true
) as inspect_claims_set
\gset

select inspected ->> 'approval_subject_sha256'
    as approval_subject_sha256
from (
    select public.inspect_exact_telegram_delivery_unknown_resolution(
        :'target_workspace_id'::uuid,
        :'target_content_item_id'::uuid,
        :'target_content_version_id'::uuid,
        :'target_publication_id'::uuid,
        :'target_job_id'::uuid,
        :'target_resolution_id'::uuid,
        :'target_operator_approval_id'::uuid,
        :'target_inspected_by',
        :'target_approved_by',
        :'target_expires_at'::timestamptz,
        :'target_release_sha',
        :'target_public_audit'::jsonb
    ) as inspected
) as inspection
\gset inspected_
commit;

begin;
set local role coineasy_telegram_resolution;
select pg_catalog.set_config(
    'request.jwt.claims',
    pg_catalog.jsonb_build_object(
        'role', 'coineasy_telegram_resolution',
        'workspace_id', :'target_workspace_id',
        'sub', :'target_approved_by',
        'capability', 'telegram_delivery_unknown_approve',
        'environment', 'production',
        'release_sha', :'target_release_sha',
        'automatic_publication', false,
        'resend_authorized', false,
        'max_external_actions', 0,
        'jti', :'target_operator_approval_id',
        'content_item_id', :'target_content_item_id',
        'content_version_id', :'target_content_version_id',
        'publication_id', :'target_publication_id',
        'job_id', :'target_job_id',
        'resolution_id', :'target_resolution_id',
        'operator_approval_id', :'target_operator_approval_id',
        'approval_subject_sha256', :'inspected_approval_subject_sha256',
        'expires_at', :'target_expires_at'
    )::text,
    true
) as approve_claims_set
\gset

select public.approve_exact_telegram_delivery_unknown_resolution(
    :'target_workspace_id'::uuid,
    :'target_content_item_id'::uuid,
    :'target_content_version_id'::uuid,
    :'target_publication_id'::uuid,
    :'target_job_id'::uuid,
    :'target_resolution_id'::uuid,
    :'target_operator_approval_id'::uuid,
    :'target_approved_by',
    :'target_expires_at'::timestamptz,
    :'target_release_sha',
    :'target_public_audit'::jsonb,
    :'inspected_approval_subject_sha256'
) as approved
\gset approved_
commit;

update exact_telegram_resolution_concurrency.fixture
set approval_subject_sha256 = :'inspected_approval_subject_sha256',
    resolution_approval_before = (
        select pg_catalog.to_jsonb(approval)
        from private.exact_telegram_delivery_unknown_approvals as approval
        where approval.workspace_id = :'target_workspace_id'::uuid
          and approval.operator_approval_id =
            :'target_operator_approval_id'::uuid
    )
where singleton;

do $verify_setup$
declare
    target exact_telegram_resolution_concurrency.fixture%rowtype;
begin
    select * into strict target
    from exact_telegram_resolution_concurrency.fixture
    where singleton;
    if coalesce(target.approval_subject_sha256, '') !~ '^[a-f0-9]{64}$'
       or target.resolution_approval_before is null then
        raise exception 'concurrency approval subject hash is invalid';
    end if;
    if (select count(*)
        from private.exact_telegram_delivery_unknown_approvals as approval
        where approval.workspace_id = target.workspace_id
          and approval.operator_approval_id = target.operator_approval_id) <> 1
       or (select count(*)
           from public.event_log as event
           where event.workspace_id = target.workspace_id
             and event.entity_id = target.publication_id
             and event.event_type =
                'exact_telegram_delivery_unknown_resolution_approved') <> 1 then
        raise exception 'concurrency durable approval was not recorded once';
    end if;
end
$verify_setup$;
