-- Transactional security smoke for durable double fact-check attestations.
-- Run after all migrations as the database owner; every row is rolled back.

begin;

create function pg_temp.fact_check_meta(target_status text)
returns jsonb
language sql
immutable
as $$
    select jsonb_build_object(
        'mock_mode', false,
        'fact_check', jsonb_build_object(
            'schema_version', '1.0',
            'policy_version', 'double-fact-check@1',
            'content_kind', 'article',
            'status', target_status,
            'human_review_required', true,
            'input_sha256', repeat('a', 64),
            'output_sha256', repeat('b', 64),
            'checks', jsonb_build_array(
                jsonb_build_object(
                    'id', 'source_evidence',
                    'status', target_status,
                    'label', 'Source evidence',
                    'detail', 'Human verification fixture.',
                    'metrics', '{}'::jsonb
                ),
                jsonb_build_object(
                    'id', 'output_claims',
                    'status', target_status,
                    'label', 'Output claims',
                    'detail', 'Output fixture.',
                    'metrics', '{}'::jsonb
                )
            )
        )
    )
$$;

do $test$
begin
    if has_function_privilege(
        'anon',
        'public.record_studio_content_review_v2(uuid,uuid,uuid,text,text,boolean,boolean,text[],text,text)',
        'execute'
    ) or has_function_privilege(
        'authenticated',
        'public.record_studio_content_review_v2(uuid,uuid,uuid,text,text,boolean,boolean,text[],text,text)',
        'execute'
    ) or not has_function_privilege(
        'service_role',
        'public.record_studio_content_review_v2(uuid,uuid,uuid,text,text,boolean,boolean,text[],text,text)',
        'execute'
    ) then
        raise exception 'v2 Studio review RPC privileges are unsafe';
    end if;

    if has_function_privilege(
        'service_role',
        'private.require_double_fact_check_approval(uuid,uuid,uuid,uuid)',
        'execute'
    ) then
        raise exception 'private double fact-check gate leaked to service_role';
    end if;

    if has_function_privilege(
        'authenticated',
        'public.review_content_version(uuid,uuid,text,text)',
        'execute'
    ) or has_function_privilege(
        'service_role',
        'public.record_studio_content_review(uuid,uuid,uuid,text,text[],text,text)',
        'execute'
    ) then
        raise exception 'legacy approval RPC retained runtime execution authority';
    end if;

    if not private.has_valid_double_fact_check_report(
        pg_temp.fact_check_meta('pass')
    ) or not private.has_valid_double_fact_check_report(
        pg_temp.fact_check_meta('review')
    ) or private.has_valid_double_fact_check_report(
        pg_temp.fact_check_meta('blocked')
    ) or private.has_valid_double_fact_check_report('{}'::jsonb)
       or private.has_valid_double_fact_check_report(
            '{"fact_check":{"schema_version":"1.0"}}'::jsonb
       ) then
        raise exception 'double fact-check report validator is not fail-closed';
    end if;
end
$test$;

insert into public.workspaces (id, name, slug, created_by)
values (
    'e0000000-0000-4000-8000-000000000001',
    'Double Fact Check Security Test',
    'double-fact-check-security-test',
    null
);
insert into public.workspace_clients (
    workspace_id, client_id, display_name, active, created_by
) values (
    'e0000000-0000-4000-8000-000000000001',
    'squid',
    'Squid',
    true,
    null
);
insert into auth.users (id)
values ('e9000000-0000-4000-8000-000000000001');
insert into public.workspace_members (
    workspace_id, user_id, role, status, invited_by
) values (
    'e0000000-0000-4000-8000-000000000001',
    'e9000000-0000-4000-8000-000000000001',
    'editor',
    'active',
    null
);

insert into public.content_items (
    id, workspace_id, client_id, content_kind, title, status
) values
    (
        'e1000000-0000-4000-8000-000000000001',
        'e0000000-0000-4000-8000-000000000001',
        'squid', 'daily_news', 'Legacy approval', 'needs_review'
    ),
    (
        'e1000000-0000-4000-8000-000000000002',
        'e0000000-0000-4000-8000-000000000001',
        'squid', 'article', 'Missing report', 'needs_review'
    ),
    (
        'e1000000-0000-4000-8000-000000000003',
        'e0000000-0000-4000-8000-000000000001',
        'squid', 'article', 'Blocked report', 'needs_review'
    ),
    (
        'e1000000-0000-4000-8000-000000000004',
        'e0000000-0000-4000-8000-000000000001',
        'squid', 'article', 'Human-reviewed report', 'needs_review'
    );

insert into public.content_versions (
    id, workspace_id, content_item_id, version_number, prompt_version,
    title, content, channel_copy, generation_meta
) values
    (
        'e2000000-0000-4000-8000-000000000001',
        'e0000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000001',
        1, 'security@1', 'Legacy approval', '{}'::jsonb,
        '{"telegram":"legacy must not publish"}'::jsonb,
        '{"mock_mode":false}'::jsonb
    ),
    (
        'e2000000-0000-4000-8000-000000000002',
        'e0000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000002',
        1, 'security@1', 'Missing report', '{}'::jsonb, '{}'::jsonb,
        '{"mock_mode":false}'::jsonb
    ),
    (
        'e2000000-0000-4000-8000-000000000003',
        'e0000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000003',
        1, 'security@1', 'Blocked report', '{}'::jsonb, '{}'::jsonb,
        pg_temp.fact_check_meta('blocked')
    ),
    (
        'e2000000-0000-4000-8000-000000000004',
        'e0000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000004',
        1, 'security@1', 'Human-reviewed report', '{}'::jsonb, '{}'::jsonb,
        pg_temp.fact_check_meta('review')
    );

update public.content_items as item
set current_version_id = version.id
from public.content_versions as version
where version.content_item_id = item.id
  and version.workspace_id = item.workspace_id;

select public.record_studio_content_review(
    'e0000000-0000-4000-8000-000000000001',
    'e1000000-0000-4000-8000-000000000001',
    'e2000000-0000-4000-8000-000000000001',
    'approved', '{}'::text[], null, 'legacy-review-security'
);

do $test$
declare
    summary jsonb;
begin
    summary := public.get_content_review_summary(
        'e0000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000001'
    );
    if summary -> 'fact_check_policy_version' <> 'null'::jsonb
       or summary ->> 'source_facts_verified' <> 'false'
       or summary ->> 'output_claims_verified' <> 'false' then
        raise exception 'legacy approval was not conservatively un-attested';
    end if;

    begin
        perform public.request_studio_telegram_publication(
            'e0000000-0000-4000-8000-000000000001',
            'e1000000-0000-4000-8000-000000000001',
            'e2000000-0000-4000-8000-000000000001',
            'e4000000-0000-4000-8000-000000000001'
        );
        raise exception 'legacy approval authorized publication';
    exception when check_violation then null;
    end;

    begin
        perform public.record_manual_publication_observation(
            'e0000000-0000-4000-8000-000000000001',
            'e1000000-0000-4000-8000-000000000001',
            'e2000000-0000-4000-8000-000000000001',
            'x',
            'https://x.com/squidkorea/status/987654321'
        );
        raise exception 'legacy approval authorized manual publication observation';
    exception when check_violation then null;
    end;
    if exists (
        select 1
        from public.publications as publication
        where publication.content_item_id
                = 'e1000000-0000-4000-8000-000000000001'
    ) or exists (
        select 1
        from public.event_log as event
        where event.event_type = 'manual_publication_observed'
          and event.data ->> 'content_item_id'
                = 'e1000000-0000-4000-8000-000000000001'
    ) then
        raise exception 'failed manual observation leaked into publication ledger';
    end if;

    begin
        perform public.record_studio_content_review_v2(
            'e0000000-0000-4000-8000-000000000001',
            'e1000000-0000-4000-8000-000000000002',
            'e2000000-0000-4000-8000-000000000002',
            'approved', 'double-fact-check@1', true, true,
            '{}'::text[], null, 'missing-report-security'
        );
        raise exception 'missing fact-check report was approved';
    exception when check_violation then null;
    end;

    begin
        perform public.record_studio_content_review_v2(
            'e0000000-0000-4000-8000-000000000001',
            'e1000000-0000-4000-8000-000000000003',
            'e2000000-0000-4000-8000-000000000003',
            'approved', 'double-fact-check@1', true, true,
            '{}'::text[], null, 'blocked-report-security'
        );
        raise exception 'blocked fact-check report was approved';
    exception when check_violation then null;
    end;

    begin
        perform public.record_studio_content_review_v2(
            'e0000000-0000-4000-8000-000000000001',
            'e1000000-0000-4000-8000-000000000004',
            'e2000000-0000-4000-8000-000000000004',
            'approved', 'double-fact-check@1', true, false,
            '{}'::text[], null, 'one-sided-security'
        );
        raise exception 'one-sided human attestation was approved';
    exception when check_violation then null;
    end;
end
$test$;

do $test$
declare
    first_review jsonb;
    replay jsonb;
    summary jsonb;
begin
    first_review := public.record_studio_content_review_v2(
        'e0000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000004',
        'e2000000-0000-4000-8000-000000000004',
        'approved', 'double-fact-check@1', true, true,
        '{}'::text[], null, 'valid-review-security'
    );
    replay := public.record_studio_content_review_v2(
        'e0000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000004',
        'e2000000-0000-4000-8000-000000000004',
        'approved', 'double-fact-check@1', true, true,
        '{}'::text[], null, 'valid-review-security'
    );
    if replay ->> 'approval_id' is distinct from first_review ->> 'approval_id'
       or replay ->> 'reused' <> 'true' then
        raise exception 'review-status fact check could not be human-approved';
    end if;

    begin
        perform public.record_studio_content_review_v2(
            'e0000000-0000-4000-8000-000000000001',
            'e1000000-0000-4000-8000-000000000004',
            'e2000000-0000-4000-8000-000000000004',
            'approved', 'double-fact-check@1', true, false,
            '{}'::text[], null, 'valid-review-security'
        );
        raise exception 'v2 review idempotency did not bind attestations';
    exception when unique_violation then null;
    end;

    summary := public.get_content_review_summary(
        'e0000000-0000-4000-8000-000000000001',
        'e1000000-0000-4000-8000-000000000004'
    );
    if summary ->> 'fact_check_policy_version'
            <> 'double-fact-check@1'
       or summary ->> 'source_facts_verified' <> 'true'
       or summary ->> 'output_claims_verified' <> 'true' then
        raise exception 'review summary omitted double fact-check attestations';
    end if;
end
$test$;

set local role authenticated;
select set_config(
    'request.jwt.claim.sub',
    'e9000000-0000-4000-8000-000000000001',
    true
);

do $test$
begin
    begin
        perform public.request_content_publication(
            'e1000000-0000-4000-8000-000000000001',
            'e2000000-0000-4000-8000-000000000001',
            'web', null, 'legacy-generic-security'
        );
        raise exception 'legacy approval authorized publication';
    exception when check_violation then null;
    end;

    perform public.request_content_publication(
        'e1000000-0000-4000-8000-000000000004',
        'e2000000-0000-4000-8000-000000000004',
        'x', null, 'attested-generic-security'
    );
end
$test$;

reset role;
update public.content_items
set status = 'needs_review'
where id = 'e1000000-0000-4000-8000-000000000004';
select public.record_studio_content_review(
    'e0000000-0000-4000-8000-000000000001',
    'e1000000-0000-4000-8000-000000000004',
    'e2000000-0000-4000-8000-000000000004',
    'approved', '{}'::text[], null, 'later-legacy-security'
);

set local role authenticated;
select set_config(
    'request.jwt.claim.sub',
    'e9000000-0000-4000-8000-000000000001',
    true
);
do $test$
begin
    begin
        perform public.request_content_publication(
            'e1000000-0000-4000-8000-000000000004',
            'e2000000-0000-4000-8000-000000000004',
            'web', null, 'revoked-generic-security'
        );
        raise exception
            'later un-attested review did not revoke publication authority';
    exception when check_violation then null;
    end;
end
$test$;

rollback;
