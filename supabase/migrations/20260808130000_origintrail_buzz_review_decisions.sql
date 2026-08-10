-- Immutable OriginTrail publish-intent decisions received from a bounded Buzz
-- thread. These RPCs still cannot publish, regenerate, mutate Batch output, or
-- call a provider. Protocol v2 excludes every pre-cutover receipt and requires
-- the immutable X Article evidence used by the reviewed Batch input.

begin;

create table agent_runtime.buzz_review_decisions (
    workspace_id uuid not null references public.workspaces(id) on delete restrict,
    job_id uuid not null,
    delivery_event_id text not null check (delivery_event_id ~ '^[a-f0-9]{64}$'),
    channel_id uuid not null,
    root_relay_event_id text not null check (root_relay_event_id ~ '^[a-f0-9]{64}$'),
    message_sha256 text not null check (message_sha256 ~ '^[a-f0-9]{64}$'),
    protocol_version text not null check (
        protocol_version = 'origintrail-buzz-review@2'
    ),
    decision_event_id text not null check (
        decision_event_id ~ '^[a-f0-9]{64}$'
        and decision_event_id <> root_relay_event_id
    ),
    reviewer_pubkey text not null check (reviewer_pubkey ~ '^[a-f0-9]{64}$'),
    decision text not null check (decision in ('approved', 'changes_requested')),
    reason text,
    command_sha256 text not null check (command_sha256 ~ '^[a-f0-9]{64}$'),
    command_created_at timestamptz not null,
    recorded_at timestamptz not null default statement_timestamp(),
    primary key (workspace_id, job_id),
    unique (decision_event_id),
    foreign key (workspace_id, job_id)
        references agent_runtime.batch_jobs(workspace_id, job_id)
        on delete restrict,
    foreign key (workspace_id, delivery_event_id)
        references agent_runtime.buzz_delivery_receipts(workspace_id, event_id)
        on delete restrict,
    check (
        (decision = 'approved' and reason is null)
        or (
            decision = 'changes_requested'
            and reason is not null
            and reason = btrim(reason)
            and char_length(reason) between 1 and 500
            and octet_length(reason) <= 1500
            and reason !~ '[[:cntrl:]]'
        )
    )
);

create index buzz_review_decisions_recorded_idx
    on agent_runtime.buzz_review_decisions (workspace_id, recorded_at, job_id);

alter table agent_runtime.buzz_review_decisions enable row level security;
alter table agent_runtime.buzz_review_decisions force row level security;
revoke all on table agent_runtime.buzz_review_decisions
from public, anon, authenticated, service_role;

create or replace function private.reject_buzz_review_decision_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'Buzz review decisions are immutable'
        using errcode = '55000';
end;
$$;

revoke all on function private.reject_buzz_review_decision_mutation()
from public, anon, authenticated, service_role;

create trigger buzz_review_decisions_immutable
before update or delete on agent_runtime.buzz_review_decisions
for each row execute function private.reject_buzz_review_decision_mutation();

-- One shared eligibility predicate prevents delivery, list, and record policy
-- from drifting. The pilot accepts exactly one provider-owned X Article whose
-- full body, standalone marker, and immutable evidence SHA all agree with the
-- Batch input snapshot and original review job.
create or replace function private.origintrail_buzz_review_evidence_ready(
    target_workspace_id uuid,
    target_job_id uuid
)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    batch_job agent_runtime.batch_jobs%rowtype;
    review_job public.jobs%rowtype;
    input_document jsonb;
    source_content text;
    computed_source_sha256 text;
    computed_result_sha256 text;
    source_ids uuid[];
    source_count integer;
    distinct_source_count integer;
    verified_source_count integer;
    review_pack_count integer;
begin
    select current_batch.* into batch_job
    from agent_runtime.batch_jobs as current_batch
    where current_batch.workspace_id = target_workspace_id
      and current_batch.job_id = target_job_id;
    if not found then
        return false;
    end if;

    select current_review.* into review_job
    from public.jobs as current_review
    where current_review.workspace_id = batch_job.workspace_id
      and current_review.id = batch_job.job_id
      and current_review.client_id = batch_job.client_id;
    if not found then
        return false;
    end if;

    if batch_job.client_id is distinct from 'origintrail'
       or batch_job.agent_id is distinct from 'origintrail_client_agent'
       or batch_job.workflow_kind is distinct from 'official_source_nonurgent_pack'
       or batch_job.stage is distinct from 'generate'
       or batch_job.status is distinct from 'completed'
       or batch_job.reservation_state is distinct from 'settled'
       or batch_job.result_code is distinct from 'needs_review'
       or batch_job.input_payload -> 'approval_required' is distinct from 'true'::jsonb
       or batch_job.input_payload -> 'input_immutable' is distinct from 'true'::jsonb
       or batch_job.input_payload -> 'source_snapshot_complete' is distinct from 'true'::jsonb
       or jsonb_typeof(batch_job.input_payload -> 'input') is distinct from 'string'
       or review_job.job_kind is distinct from 'generate'
       or review_job.status is distinct from 'succeeded'
       or review_job.content_item_id is not null
       or review_job.input ->> 'workflow' is distinct from 'official_x_review_draft_v1'
       or review_job.input ->> 'content_kind' is distinct from 'daily_news'
       or review_job.input -> 'manual_only' is distinct from 'false'::jsonb
       or not coalesce((review_job.input ->> 'source_url')
            ~ '^https://x[.]com/origin_trail/status/[0-9]{1,19}$', false)
       or review_job.output is distinct from jsonb_build_object(
            'workflow', 'agent_batch_review_handoff_v1',
            'handoff', 'openai_batch',
            'batch_job_id', batch_job.job_id,
            'input_sha256', batch_job.input_sha256,
            'review_state', 'pending'
       )
       or not (batch_job.result_payload ?& array[
            'headline_ko', 'body_ko', 'x_copy_ko', 'telegram_copy_ko'
       ]::text[])
       or (select count(*) from jsonb_object_keys(batch_job.result_payload)) <> 4
       or jsonb_typeof(batch_job.result_payload -> 'headline_ko') is distinct from 'string'
       or jsonb_typeof(batch_job.result_payload -> 'body_ko') is distinct from 'string'
       or jsonb_typeof(batch_job.result_payload -> 'x_copy_ko') is distinct from 'string'
       or jsonb_typeof(batch_job.result_payload -> 'telegram_copy_ko') is distinct from 'string'
       or char_length(batch_job.result_payload ->> 'headline_ko') not between 1 and 120
       or char_length(batch_job.result_payload ->> 'body_ko') not between 1 and 1800
       or char_length(batch_job.result_payload ->> 'x_copy_ko') not between 1 and 500
       or char_length(batch_job.result_payload ->> 'telegram_copy_ko') not between 1 and 1024
       or not coalesce((batch_job.result_payload ->> 'headline_ko') ~ '[^[:space:]]', false)
       or not coalesce((batch_job.result_payload ->> 'body_ko') ~ '[^[:space:]]', false)
       or not coalesce((batch_job.result_payload ->> 'x_copy_ko') ~ '[^[:space:]]', false)
       or not coalesce((batch_job.result_payload ->> 'telegram_copy_ko') ~ '[^[:space:]]', false) then
        return false;
    end if;

    begin
        input_document := (batch_job.input_payload ->> 'input')::jsonb;
    exception when others then
        return false;
    end;
    source_content := input_document -> 'source' ->> 'content';
    computed_source_sha256 := encode(extensions.digest(
        pg_catalog.convert_to(coalesce(source_content, ''), 'UTF8'), 'sha256'
    ), 'hex');
    computed_result_sha256 := encode(extensions.digest(
        pg_catalog.convert_to(batch_job.result_payload::text, 'UTF8'), 'sha256'
    ), 'hex');
    if jsonb_typeof(input_document) is distinct from 'object'
       or input_document ->> 'client_id' is distinct from 'origintrail'
       or input_document ->> 'content_kind' is distinct from 'daily_news'
       or input_document ->> 'request_id' is distinct from review_job.input ->> 'request_id'
       or input_document -> 'source' ->> 'url' is distinct from review_job.input ->> 'source_url'
       or source_content is null
       or char_length(source_content) not between 10 and 60000
       or not coalesce(regexp_replace(
            source_content, 'https?://[^[:space:]]+', '', 'g'
          ) ~ '[^[:space:]]', false)
       or input_document -> 'source' ->> 'content_sha256'
            is distinct from computed_source_sha256
       or review_job.input ->> 'source_content' is distinct from source_content
       or jsonb_typeof(review_job.input -> 'source_item_ids') is distinct from 'array' then
        return false;
    end if;

    begin
        select array_agg(value::uuid order by ordinal), count(*), count(distinct value)
        into source_ids, source_count, distinct_source_count
        from jsonb_array_elements_text(review_job.input -> 'source_item_ids')
             with ordinality as source_id(value, ordinal);
    exception when others then
        return false;
    end;
    if source_count is distinct from 1
       or distinct_source_count is distinct from source_count then
        return false;
    end if;

    select count(*) into verified_source_count
    from public.source_items as source
    join private.origintrail_standalone_sources as standalone
      on standalone.workspace_id = source.workspace_id
     and standalone.client_id = source.client_id
     and standalone.source_item_id = source.id
     and standalone.is_quote is false
    join private.origintrail_x_article_evidence as evidence
      on evidence.workspace_id = source.workspace_id
     and evidence.client_id = source.client_id
     and evidence.source_item_id = source.id
    where source.workspace_id = target_workspace_id
      and source.client_id = 'origintrail'
      and source.id = any(source_ids)
      and source.body = source_content
      and source.canonical_url = review_job.input ->> 'source_url'
      and position('[X Article]' in source.body) > 0
      and evidence.source_content_sha256 = computed_source_sha256
      and evidence.source_content_sha256 = encode(extensions.digest(
            pg_catalog.convert_to(btrim(source.body), 'UTF8'), 'sha256'
          ), 'hex')
      and evidence.retrieval_method in ('x_api_timeline', 'x_api_post_lookup');
    if verified_source_count <> source_count then
        return false;
    end if;

    select count(*) into review_pack_count
    from agent_runtime.origintrail_batch_review_packs as review_pack
    join public.content_items as content_item
      on content_item.workspace_id = review_pack.workspace_id
     and content_item.id = review_pack.content_item_id
     and content_item.client_id = 'origintrail'
     and content_item.content_kind = 'daily_news'
     and content_item.status = 'needs_review'
     and content_item.current_version_id = review_pack.content_version_id
    join public.content_versions as content_version
      on content_version.workspace_id = review_pack.workspace_id
     and content_version.content_item_id = review_pack.content_item_id
     and content_version.id = review_pack.content_version_id
     and content_version.prompt_version = 'origintrail-batch-review-pack@1'
    join public.assets as asset
      on asset.workspace_id = review_pack.workspace_id
     and asset.content_item_id = review_pack.content_item_id
     and asset.content_version_id = review_pack.content_version_id
     and asset.id = review_pack.asset_id
     and asset.sha256 = review_pack.banner_sha256
     and asset.asset_kind = 'png'
     and asset.mime_type = 'image/png'
     and asset.width = 1200
     and asset.height = 630
    where review_pack.workspace_id = target_workspace_id
      and review_pack.job_id = target_job_id
      and review_pack.content_item_id::text = input_document ->> 'request_id'
      and review_pack.source_item_id = source_ids[1]
      and review_pack.input_sha256 = batch_job.input_sha256
      and review_pack.result_sha256 = computed_result_sha256
      and review_pack.source_content_sha256 = computed_source_sha256
      and review_pack.protocol_version = 'origintrail-review-pack@1'
      and review_pack.review_pack_sha256 = private.origintrail_review_pack_sha256(
            review_pack.workspace_id,
            review_pack.job_id,
            review_pack.content_item_id,
            review_pack.source_item_id,
            review_pack.input_sha256,
            review_pack.result_sha256,
            review_pack.source_content_sha256,
            review_pack.banner_sha256
          );
    return review_pack_count = 1;
exception when others then
    return false;
end;
$$;

revoke all on function private.origintrail_buzz_review_evidence_ready(uuid, uuid)
from public, anon, authenticated, service_role;

create or replace function private.origintrail_buzz_review_command_sha256(
    target_workspace_id uuid,
    target_job_id uuid,
    target_delivery_event_id text,
    target_channel_id uuid,
    target_root_relay_event_id text,
    target_message_sha256 text,
    target_protocol_version text,
    target_decision_event_id text,
    target_reviewer_pubkey text,
    target_decision text,
    target_reason text,
    target_command_created_at_epoch bigint
)
returns text
language sql
immutable
set search_path = ''
as $$
    select encode(extensions.digest(
        convert_to('coineasy-buzz-review-decision', 'UTF8') || decode('00', 'hex')
        || convert_to('2.0', 'UTF8') || decode('00', 'hex')
        || convert_to(target_workspace_id::text, 'UTF8') || decode('00', 'hex')
        || convert_to(target_job_id::text, 'UTF8') || decode('00', 'hex')
        || convert_to(target_delivery_event_id, 'UTF8') || decode('00', 'hex')
        || convert_to(target_channel_id::text, 'UTF8') || decode('00', 'hex')
        || convert_to(target_root_relay_event_id, 'UTF8') || decode('00', 'hex')
        || convert_to(target_message_sha256, 'UTF8') || decode('00', 'hex')
        || convert_to(target_protocol_version, 'UTF8') || decode('00', 'hex')
        || convert_to(target_decision_event_id, 'UTF8') || decode('00', 'hex')
        || convert_to(target_reviewer_pubkey, 'UTF8') || decode('00', 'hex')
        || convert_to(target_decision, 'UTF8') || decode('00', 'hex')
        || convert_to(coalesce(target_reason, ''), 'UTF8') || decode('00', 'hex')
        || convert_to(target_command_created_at_epoch::text, 'UTF8'),
        'sha256'
    ), 'hex')
$$;

revoke all on function private.origintrail_buzz_review_command_sha256(
    uuid, uuid, text, uuid, text, text, text, text, text, text, text, bigint
) from public, anon, authenticated, service_role;

create or replace function public.list_origintrail_buzz_review_targets(
    target_workspace_id uuid,
    target_limit integer,
    target_protocol_start_epoch bigint,
    target_protocol_version text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    result jsonb;
begin
    if target_workspace_id is null
       or target_limit not between 1 and 10
       or target_protocol_start_epoch is null
       or target_protocol_start_epoch not between 1700000000 and 4294967295
       or target_protocol_start_epoch > extract(epoch from statement_timestamp())::bigint + 300
       or target_protocol_version is distinct from 'origintrail-buzz-review@2' then
        raise exception 'OriginTrail Buzz review target request is invalid'
            using errcode = '22023';
    end if;

    with targets as (
        select
            delivery.job_id,
            delivery.event_id as delivery_event_id,
            delivery.channel_id,
            delivery.relay_event_id as root_relay_event_id,
            delivery.message_sha256,
            target_protocol_version as protocol_version,
            extract(epoch from delivery.delivered_at)::bigint as delivered_at_epoch
        from agent_runtime.buzz_delivery_receipts as delivery
        left join agent_runtime.buzz_review_decisions as decision
          on decision.workspace_id = delivery.workspace_id
         and decision.job_id = delivery.job_id
        where delivery.workspace_id = target_workspace_id
          and delivery.status = 'delivered'
          and delivery.relay_event_id is not null
          and delivery.delivered_at is not null
          and delivery.delivered_at
                >= pg_catalog.to_timestamp(target_protocol_start_epoch)
          and delivery.client_id = 'origintrail'
          and delivery.agent_id = 'origintrail_client_agent'
          and delivery.workflow_kind = 'official_source_nonurgent_pack'
          and exists (
              select 1
              from agent_runtime.origintrail_batch_review_packs as review_pack
              where review_pack.workspace_id = delivery.workspace_id
                and review_pack.job_id = delivery.job_id
                and review_pack.banner_sha256 = delivery.attachment_sha256
          )
          and private.origintrail_buzz_review_evidence_ready(
                delivery.workspace_id, delivery.job_id
          )
          and decision.job_id is null
        order by delivery.delivered_at desc, delivery.job_id desc
        limit target_limit
    )
    select jsonb_build_object(
        'schema_version', '2.0',
        'mode', 'publish_intent_review',
        'workspace_id', target_workspace_id,
        'targets', coalesce(jsonb_agg(
            jsonb_build_object(
                'job_id', targets.job_id,
                'delivery_event_id', targets.delivery_event_id,
                'channel_id', targets.channel_id,
                'root_relay_event_id', targets.root_relay_event_id,
                'message_sha256', targets.message_sha256,
                'protocol_version', targets.protocol_version,
                'delivered_at_epoch', targets.delivered_at_epoch
            ) order by targets.delivered_at_epoch desc, targets.job_id desc
        ), '[]'::jsonb)
    ) into result
    from targets;
    return result;
end;
$$;

create or replace function public.record_origintrail_buzz_review_decision(
    target_workspace_id uuid,
    target_job_id uuid,
    target_delivery_event_id text,
    target_channel_id uuid,
    target_root_relay_event_id text,
    target_message_sha256 text,
    target_protocol_version text,
    target_protocol_start_epoch bigint,
    target_decision_event_id text,
    target_reviewer_pubkey text,
    target_decision text,
    target_reason text,
    target_command_sha256 text,
    target_command_created_at_epoch bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    delivery agent_runtime.buzz_delivery_receipts%rowtype;
    existing agent_runtime.buzz_review_decisions%rowtype;
    recorded agent_runtime.buzz_review_decisions%rowtype;
    expected_sha text;
    delivered_at_epoch bigint;
begin
    if target_workspace_id is null
       or target_job_id is null
       or target_delivery_event_id is null
       or target_delivery_event_id !~ '^[a-f0-9]{64}$'
       or target_channel_id is null
       or target_root_relay_event_id is null
       or target_root_relay_event_id !~ '^[a-f0-9]{64}$'
       or target_message_sha256 is null
       or target_message_sha256 !~ '^[a-f0-9]{64}$'
       or target_protocol_version is distinct from 'origintrail-buzz-review@2'
       or target_protocol_start_epoch is null
       or target_protocol_start_epoch not between 1700000000 and 4294967295
       or target_protocol_start_epoch > extract(epoch from statement_timestamp())::bigint + 300
       or target_decision_event_id is null
       or target_decision_event_id !~ '^[a-f0-9]{64}$'
       or target_decision_event_id = target_root_relay_event_id
       or target_reviewer_pubkey is null
       or target_reviewer_pubkey !~ '^[a-f0-9]{64}$'
       or target_decision is null
       or target_decision not in ('approved', 'changes_requested')
       or (target_decision = 'approved' and target_reason is not null)
       or (
            target_decision = 'changes_requested'
            and (
                target_reason is null or target_reason <> btrim(target_reason)
                or char_length(target_reason) not between 1 and 500
                or octet_length(target_reason) > 1500
                or target_reason ~ '[[:cntrl:]]'
            )
       )
       or target_command_sha256 is null
       or target_command_sha256 !~ '^[a-f0-9]{64}$'
       or target_command_created_at_epoch is null
       or target_command_created_at_epoch not between 1 and 4294967295 then
        raise exception 'OriginTrail Buzz review decision is invalid'
            using errcode = '22023';
    end if;

    select receipt.* into delivery
    from agent_runtime.buzz_delivery_receipts as receipt
    where receipt.workspace_id = target_workspace_id
      and receipt.job_id = target_job_id
      and receipt.event_id = target_delivery_event_id
      and receipt.channel_id = target_channel_id
      and receipt.relay_event_id = target_root_relay_event_id
      and receipt.message_sha256 = target_message_sha256
      and receipt.status = 'delivered'
      and receipt.delivered_at is not null
      and receipt.delivered_at
            >= pg_catalog.to_timestamp(target_protocol_start_epoch)
      and receipt.client_id = 'origintrail'
      and receipt.agent_id = 'origintrail_client_agent'
      and receipt.workflow_kind = 'official_source_nonurgent_pack'
      and exists (
          select 1
          from agent_runtime.origintrail_batch_review_packs as review_pack
          where review_pack.workspace_id = receipt.workspace_id
            and review_pack.job_id = receipt.job_id
            and review_pack.banner_sha256 = receipt.attachment_sha256
      )
      and private.origintrail_buzz_review_evidence_ready(
            receipt.workspace_id, receipt.job_id
      )
    for update of receipt;
    if not found then
        raise exception 'OriginTrail Buzz review target is not eligible'
            using errcode = '23514';
    end if;

    delivered_at_epoch := extract(epoch from delivery.delivered_at)::bigint;
    if target_command_created_at_epoch < delivered_at_epoch
       or target_command_created_at_epoch < target_protocol_start_epoch
       or target_command_created_at_epoch > delivered_at_epoch + 604800
       or target_command_created_at_epoch
            > extract(epoch from statement_timestamp())::bigint + 300 then
        raise exception 'OriginTrail Buzz review decision timestamp is invalid'
            using errcode = '23514';
    end if;

    expected_sha := private.origintrail_buzz_review_command_sha256(
        target_workspace_id, target_job_id, target_delivery_event_id,
        target_channel_id, target_root_relay_event_id, target_message_sha256,
        target_protocol_version, target_decision_event_id,
        target_reviewer_pubkey, target_decision, target_reason,
        target_command_created_at_epoch
    );
    if target_command_sha256 <> expected_sha then
        raise exception 'OriginTrail Buzz review command hash does not match'
            using errcode = '23514';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        target_workspace_id::text || ':' || target_job_id::text, 0
    ));
    select decision_row.* into existing
    from agent_runtime.buzz_review_decisions as decision_row
    where decision_row.workspace_id = target_workspace_id
      and decision_row.job_id = target_job_id;
    if found then
        if existing.delivery_event_id <> target_delivery_event_id
           or existing.channel_id <> target_channel_id
           or existing.root_relay_event_id <> target_root_relay_event_id
           or existing.message_sha256 <> target_message_sha256
           or existing.protocol_version <> target_protocol_version
           or existing.decision_event_id <> target_decision_event_id
           or existing.reviewer_pubkey <> target_reviewer_pubkey
           or existing.decision <> target_decision
           or existing.reason is distinct from target_reason
           or existing.command_sha256 <> target_command_sha256
           or extract(epoch from existing.command_created_at)::bigint
                <> target_command_created_at_epoch then
            raise exception 'OriginTrail Buzz review decision conflicts'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'schema_version', '2.0', 'mode', 'publish_intent_review',
            'workspace_id', existing.workspace_id, 'job_id', existing.job_id,
            'delivery_event_id', existing.delivery_event_id,
            'channel_id', existing.channel_id,
            'root_relay_event_id', existing.root_relay_event_id,
            'message_sha256', existing.message_sha256,
            'protocol_version', existing.protocol_version,
            'decision_event_id', existing.decision_event_id,
            'reviewer_pubkey', existing.reviewer_pubkey,
            'decision', existing.decision, 'reason', existing.reason,
            'command_sha256', existing.command_sha256,
            'command_created_at_epoch',
                extract(epoch from existing.command_created_at)::bigint,
            'reused', true
        );
    end if;

    insert into agent_runtime.buzz_review_decisions (
        workspace_id, job_id, delivery_event_id, channel_id,
        root_relay_event_id, message_sha256, protocol_version,
        decision_event_id, reviewer_pubkey, decision, reason,
        command_sha256, command_created_at
    ) values (
        target_workspace_id, target_job_id, target_delivery_event_id,
        target_channel_id, target_root_relay_event_id, target_message_sha256,
        target_protocol_version, target_decision_event_id,
        target_reviewer_pubkey, target_decision, target_reason,
        target_command_sha256, to_timestamp(target_command_created_at_epoch)
    ) returning * into recorded;

    return jsonb_build_object(
        'schema_version', '2.0', 'mode', 'publish_intent_review',
        'workspace_id', recorded.workspace_id, 'job_id', recorded.job_id,
        'delivery_event_id', recorded.delivery_event_id,
        'channel_id', recorded.channel_id,
        'root_relay_event_id', recorded.root_relay_event_id,
        'message_sha256', recorded.message_sha256,
        'protocol_version', recorded.protocol_version,
        'decision_event_id', recorded.decision_event_id,
        'reviewer_pubkey', recorded.reviewer_pubkey,
        'decision', recorded.decision, 'reason', recorded.reason,
        'command_sha256', recorded.command_sha256,
        'command_created_at_epoch',
            extract(epoch from recorded.command_created_at)::bigint,
        'reused', false
    );
end;
$$;

revoke all on function public.list_origintrail_buzz_review_targets(
    uuid, integer, bigint, text
) from public, anon, authenticated;
revoke all on function public.record_origintrail_buzz_review_decision(
    uuid, uuid, text, uuid, text, text, text, bigint, text, text,
    text, text, text, bigint
) from public, anon, authenticated;

grant execute on function public.list_origintrail_buzz_review_targets(
    uuid, integer, bigint, text
) to service_role;
grant execute on function public.record_origintrail_buzz_review_decision(
    uuid, uuid, text, uuid, text, text, text, bigint, text, text,
    text, text, text, bigint
) to service_role;

commit;
