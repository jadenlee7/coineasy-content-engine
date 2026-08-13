-- Durable, advisory-only Grok QA receipts.
--
-- The connector claims one immutable Content Studio version before its
-- private Telegram relay call. A claimed receipt is never automatically
-- retried: a timeout may mean Telegram accepted the message even when the
-- caller did not receive the response. This fail-closed rule prevents a bot
-- Routine from duplicating QA verdicts in the team room.

begin;

create table private.grok_qa_verdict_receipts (
    workspace_id uuid not null,
    content_item_id uuid not null,
    content_version_id uuid not null,
    decision text not null check (decision in ('PASS', 'WARN', 'BLOCK')),
    payload jsonb not null check (jsonb_typeof(payload) = 'object'),
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    status text not null default 'claimed'
        check (status in ('claimed', 'sent', 'failed')),
    failure_code text,
    claimed_at timestamptz not null default statement_timestamp(),
    finalized_at timestamptz,
    primary key (workspace_id, content_version_id),
    foreign key (workspace_id, content_item_id)
        references public.content_items(workspace_id, id) on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id)
        on delete restrict,
    check (
        (status = 'claimed' and finalized_at is null and failure_code is null)
        or (status = 'sent' and finalized_at is not null and failure_code is null)
        or (status = 'failed' and finalized_at is not null and failure_code is not null)
    )
);

revoke all on table private.grok_qa_verdict_receipts
from public, anon, authenticated, service_role;

create or replace function public.claim_grok_qa_verdict(
    target_workspace_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_payload jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    receipt private.grok_qa_verdict_receipts%rowtype;
    payload_hash text;
    issue jsonb;
    new_claim boolean := false;
begin
    if target_workspace_id is null
       or target_content_item_id is null
       or target_content_version_id is null
       or jsonb_typeof(target_payload) <> 'object'
       or not target_payload ?& array[
           'decision', 'summary', 'fact_check', 'brand_check', 'issues',
           'next_action'
       ]
       or (select count(*) from jsonb_object_keys(target_payload)) <> 6
       or target_payload ->> 'decision' not in ('PASS', 'WARN', 'BLOCK')
       or jsonb_typeof(target_payload -> 'summary') <> 'string'
       or char_length(target_payload ->> 'summary') not between 10 and 800
       or btrim(target_payload ->> 'summary') = ''
       or target_payload ->> 'next_action' not in (
           'ready_for_human_approval', 'human_review', 'verify_source',
           'revise_copy', 'revise_banner'
       ) then
        raise exception 'Grok QA verdict payload is invalid'
            using errcode = '22023';
    end if;

    if jsonb_typeof(target_payload -> 'fact_check') <> 'object'
       or not (target_payload -> 'fact_check') ?& array[
           'status', 'checks', 'source_urls'
       ]
       or (select count(*) from jsonb_object_keys(
           target_payload -> 'fact_check'
       )) <> 3
       or target_payload -> 'fact_check' ->> 'status'
            not in ('PASS', 'WARN', 'BLOCK')
       or jsonb_typeof(target_payload -> 'fact_check' -> 'checks') <> 'array'
       or jsonb_array_length(target_payload -> 'fact_check' -> 'checks')
            not between 1 and 6
       or exists (
           select 1
           from jsonb_array_elements(target_payload -> 'fact_check' -> 'checks')
                as check_item(value)
           where jsonb_typeof(check_item.value) <> 'string'
              or char_length(check_item.value #>> '{}') not between 3 and 300
              or btrim(check_item.value #>> '{}') = ''
       )
       or jsonb_typeof(target_payload -> 'fact_check' -> 'source_urls') <> 'array'
       or jsonb_array_length(target_payload -> 'fact_check' -> 'source_urls') > 8
       or exists (
           select 1
           from jsonb_array_elements(
               target_payload -> 'fact_check' -> 'source_urls'
           ) as source_url(value)
           where jsonb_typeof(source_url.value) <> 'string'
              or char_length(source_url.value #>> '{}') not between 9 and 2048
              or (source_url.value #>> '{}') !~ '^https://[^[:space:]#]+$'
       ) then
        raise exception 'Grok QA fact check is invalid'
            using errcode = '22023';
    end if;

    if jsonb_typeof(target_payload -> 'brand_check') <> 'object'
       or not (target_payload -> 'brand_check') ?& array['status', 'checks']
       or (select count(*) from jsonb_object_keys(
           target_payload -> 'brand_check'
       )) <> 2
       or target_payload -> 'brand_check' ->> 'status'
            not in ('PASS', 'WARN', 'BLOCK')
       or jsonb_typeof(target_payload -> 'brand_check' -> 'checks') <> 'array'
       or jsonb_array_length(target_payload -> 'brand_check' -> 'checks')
            not between 1 and 6
       or exists (
           select 1
           from jsonb_array_elements(target_payload -> 'brand_check' -> 'checks')
                as check_item(value)
           where jsonb_typeof(check_item.value) <> 'string'
              or char_length(check_item.value #>> '{}') not between 3 and 300
              or btrim(check_item.value #>> '{}') = ''
       ) then
        raise exception 'Grok QA brand check is invalid'
            using errcode = '22023';
    end if;

    if jsonb_typeof(target_payload -> 'issues') <> 'array'
       or jsonb_array_length(target_payload -> 'issues') > 3 then
        raise exception 'Grok QA issues are invalid'
            using errcode = '22023';
    end if;
    for issue in
        select value from jsonb_array_elements(target_payload -> 'issues')
    loop
        if jsonb_typeof(issue) <> 'object'
           or not issue ?& array['severity', 'code', 'message']
           or (select count(*) from jsonb_object_keys(issue))
                not between 3 and 4
           or issue ->> 'severity' not in ('WARN', 'BLOCK')
           or coalesce(issue ->> 'code', '') !~ '^[a-z][a-z0-9_]{2,47}$'
           or char_length(coalesce(issue ->> 'message', '')) not between 3 and 500
           or (issue ? 'evidence_url' and (
               jsonb_typeof(issue -> 'evidence_url') <> 'string'
               or char_length(issue ->> 'evidence_url') not between 9 and 2048
               or (issue ->> 'evidence_url') !~ '^https://[^[:space:]#]+$'
           )) then
            raise exception 'Grok QA issue is invalid'
                using errcode = '22023';
        end if;
    end loop;

    if target_payload ->> 'decision' = 'PASS' and (
        target_payload -> 'fact_check' ->> 'status' <> 'PASS'
        or target_payload -> 'brand_check' ->> 'status' <> 'PASS'
        or jsonb_array_length(target_payload -> 'issues') <> 0
        or target_payload ->> 'next_action' <> 'ready_for_human_approval'
        or jsonb_array_length(
            target_payload -> 'fact_check' -> 'source_urls'
        ) = 0
    ) then
        raise exception 'Grok QA PASS evidence is incomplete'
            using errcode = '22023';
    end if;
    if target_payload ->> 'decision' <> 'PASS'
       and target_payload ->> 'next_action' = 'ready_for_human_approval' then
        raise exception 'Grok QA non-PASS next action is invalid'
            using errcode = '22023';
    end if;
    if target_payload ->> 'decision' = 'BLOCK'
       and target_payload -> 'fact_check' ->> 'status' <> 'BLOCK'
       and target_payload -> 'brand_check' ->> 'status' <> 'BLOCK'
       and not exists (
           select 1 from jsonb_array_elements(target_payload -> 'issues')
                as blocking_issue(value)
           where blocking_issue.value ->> 'severity' = 'BLOCK'
       ) then
        raise exception 'Grok QA BLOCK evidence is incomplete'
            using errcode = '22023';
    end if;

    select current_item.* into item
    from public.content_items as current_item
    where current_item.workspace_id = target_workspace_id
      and current_item.id = target_content_item_id
    for update;

    if not found
       or item.status <> 'needs_review'
       or item.current_version_id is distinct from target_content_version_id then
        raise exception 'Grok QA target is not the current needs_review version'
            using errcode = '23514';
    end if;

    select current_version.* into version
    from public.content_versions as current_version
    where current_version.workspace_id = target_workspace_id
      and current_version.content_item_id = target_content_item_id
      and current_version.id = target_content_version_id;

    if not found
       or version.generation_meta -> 'mock_mode' = 'true'::jsonb then
        raise exception 'Grok QA target version is not eligible'
            using errcode = '23514';
    end if;

    payload_hash := encode(extensions.digest(
        convert_to(target_payload::text, 'UTF8'), 'sha256'
    ), 'hex');

    insert into private.grok_qa_verdict_receipts (
        workspace_id, content_item_id, content_version_id, decision,
        payload, payload_sha256
    ) values (
        target_workspace_id, target_content_item_id, target_content_version_id,
        target_payload ->> 'decision', target_payload, payload_hash
    )
    on conflict (workspace_id, content_version_id) do nothing
    returning * into receipt;

    new_claim := found;
    if not new_claim then
        select current_receipt.* into receipt
        from private.grok_qa_verdict_receipts as current_receipt
        where current_receipt.workspace_id = target_workspace_id
          and current_receipt.content_version_id = target_content_version_id;
    end if;

    if receipt.payload is distinct from target_payload then
        return jsonb_build_object(
            'claimed', false,
            'status', 'duplicate_conflict',
            'payload_sha256', null,
            'decision', receipt.decision
        );
    end if;

    return jsonb_build_object(
        'claimed', new_claim,
        'status', receipt.status,
        'payload_sha256', receipt.payload_sha256,
        'decision', receipt.decision
    );
end;
$$;

create or replace function public.finalize_grok_qa_verdict(
    target_workspace_id uuid,
    target_content_version_id uuid,
    target_payload_sha256 text,
    target_outcome text,
    target_failure_code text default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    receipt private.grok_qa_verdict_receipts%rowtype;
begin
    if target_workspace_id is null
       or target_content_version_id is null
       or coalesce(target_payload_sha256, '') !~ '^[a-f0-9]{64}$'
       or target_outcome not in ('sent', 'failed')
       or (target_outcome = 'sent' and target_failure_code is not null)
       or (target_outcome = 'failed' and coalesce(target_failure_code, '')
            !~ '^[a-z][a-z0-9_]{2,63}$') then
        raise exception 'Grok QA finalization is invalid'
            using errcode = '22023';
    end if;

    select current_receipt.* into receipt
    from private.grok_qa_verdict_receipts as current_receipt
    where current_receipt.workspace_id = target_workspace_id
      and current_receipt.content_version_id = target_content_version_id
    for update;

    if not found
       or receipt.payload_sha256 is distinct from target_payload_sha256 then
        raise exception 'Grok QA receipt does not match'
            using errcode = '23514';
    end if;

    if receipt.status = 'claimed' then
        update private.grok_qa_verdict_receipts
        set status = target_outcome,
            failure_code = target_failure_code,
            finalized_at = statement_timestamp()
        where workspace_id = target_workspace_id
          and content_version_id = target_content_version_id
        returning * into receipt;
    end if;

    return jsonb_build_object(
        'status', receipt.status,
        'payload_sha256', receipt.payload_sha256,
        'decision', receipt.decision
    );
end;
$$;

revoke all on function public.claim_grok_qa_verdict(uuid, uuid, uuid, jsonb)
from public, anon, authenticated, service_role;
revoke all on function public.finalize_grok_qa_verdict(
    uuid, uuid, text, text, text
) from public, anon, authenticated, service_role;

grant execute on function public.claim_grok_qa_verdict(uuid, uuid, uuid, jsonb)
to service_role;
grant execute on function public.finalize_grok_qa_verdict(
    uuid, uuid, text, text, text
) to service_role;

commit;
