-- Transactional security smoke for advisory-only Grok QA receipts.

begin;

insert into public.workspaces (id, name, slug)
values (
    'a8100000-0000-4000-8000-000000000001',
    'Grok QA Security',
    'grok-qa-security'
);

insert into public.workspace_clients (workspace_id, client_id, display_name)
values (
    'a8100000-0000-4000-8000-000000000001',
    'squid',
    'Squid'
);

insert into public.content_items (
    id, workspace_id, client_id, content_kind, title, status
) values (
    'a8200000-0000-4000-8000-000000000001',
    'a8100000-0000-4000-8000-000000000001',
    'squid',
    'daily_news',
    'Squid Grok QA canary',
    'needs_review'
);

insert into public.content_versions (
    id, workspace_id, content_item_id, version_number, prompt_version,
    locale, title, content, channel_copy, generation_meta
) values (
    'a8300000-0000-4000-8000-000000000001',
    'a8100000-0000-4000-8000-000000000001',
    'a8200000-0000-4000-8000-000000000001',
    1,
    'grok-qa-security@1',
    'ko-KR',
    'Squid Grok QA canary',
    '{"spec":{"headline":"Squid Telegram"}}'::jsonb,
    '{"telegram":"검토 문구","x":"검토 문구"}'::jsonb,
    '{"mock_mode":false}'::jsonb
);

update public.content_items
set current_version_id = 'a8300000-0000-4000-8000-000000000001'
where id = 'a8200000-0000-4000-8000-000000000001';

do $test$
begin
    if has_function_privilege(
        'anon',
        'public.claim_grok_qa_verdict(uuid,uuid,uuid,jsonb)',
        'EXECUTE'
    ) or has_function_privilege(
        'authenticated',
        'public.claim_grok_qa_verdict(uuid,uuid,uuid,jsonb)',
        'EXECUTE'
    ) or not has_function_privilege(
        'service_role',
        'public.claim_grok_qa_verdict(uuid,uuid,uuid,jsonb)',
        'EXECUTE'
    ) then
        raise exception 'Grok QA claim privilege boundary is invalid';
    end if;
    if has_table_privilege(
        'service_role',
        'private.grok_qa_verdict_receipts',
        'SELECT'
    ) then
        raise exception 'service_role gained direct Grok QA receipt access';
    end if;
end
$test$;

set local role service_role;

do $test$
declare
    payload constant jsonb := '{
      "decision":"PASS",
      "summary":"공식 원문과 Squid 브랜드 표현이 모두 일치합니다.",
      "fact_check":{
        "status":"PASS",
        "checks":["공식 X 원문의 핵심 사실을 확인했습니다."],
        "source_urls":["https://x.com/squidrouter/status/2083266484789514640"]
      },
      "brand_check":{
        "status":"PASS",
        "checks":["Squid 공식 명칭과 브랜드 톤을 확인했습니다."]
      },
      "issues":[],
      "next_action":"ready_for_human_approval"
    }'::jsonb;
    result jsonb;
    payload_hash text;
begin
    result := public.claim_grok_qa_verdict(
        'a8100000-0000-4000-8000-000000000001',
        'a8200000-0000-4000-8000-000000000001',
        'a8300000-0000-4000-8000-000000000001',
        payload
    );
    if result -> 'claimed' <> 'true'::jsonb
       or result ->> 'status' <> 'claimed'
       or result ->> 'decision' <> 'PASS' then
        raise exception 'first Grok QA claim did not acquire the version: %', result;
    end if;
    payload_hash := result ->> 'payload_sha256';

    result := public.claim_grok_qa_verdict(
        'a8100000-0000-4000-8000-000000000001',
        'a8200000-0000-4000-8000-000000000001',
        'a8300000-0000-4000-8000-000000000001',
        payload
    );
    if result -> 'claimed' <> 'false'::jsonb
       or result ->> 'status' <> 'claimed' then
        raise exception 'duplicate Grok QA claim was not suppressed: %', result;
    end if;

    result := public.claim_grok_qa_verdict(
        'a8100000-0000-4000-8000-000000000001',
        'a8200000-0000-4000-8000-000000000001',
        'a8300000-0000-4000-8000-000000000001',
        jsonb_set(payload, '{summary}', '"다른 판정은 충돌해야 합니다."')
    );
    if result ->> 'status' <> 'duplicate_conflict' then
        raise exception 'conflicting Grok QA verdict was accepted: %', result;
    end if;

    result := public.finalize_grok_qa_verdict(
        'a8100000-0000-4000-8000-000000000001',
        'a8300000-0000-4000-8000-000000000001',
        payload_hash,
        'sent',
        null
    );
    if result ->> 'status' <> 'sent' then
        raise exception 'Grok QA receipt did not finalize: %', result;
    end if;

    result := public.claim_grok_qa_verdict(
        'a8100000-0000-4000-8000-000000000001',
        'a8200000-0000-4000-8000-000000000001',
        'a8300000-0000-4000-8000-000000000001',
        payload
    );
    if result -> 'claimed' <> 'false'::jsonb
       or result ->> 'status' <> 'sent' then
        raise exception 'sent Grok QA verdict became retryable: %', result;
    end if;
end
$test$;

reset role;

do $test$
begin
    if exists (
        select 1 from public.approvals
        where content_item_id = 'a8200000-0000-4000-8000-000000000001'
    ) then
        raise exception 'Grok QA advisory verdict created a human approval';
    end if;
    if (
        select status from public.content_items
        where id = 'a8200000-0000-4000-8000-000000000001'
    ) <> 'needs_review' then
        raise exception 'Grok QA advisory verdict changed Studio status';
    end if;
end
$test$;

rollback;

