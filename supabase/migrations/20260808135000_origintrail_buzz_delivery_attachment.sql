-- V2 delivery claim: require the worker's exact PNG digest to match the
-- immutable Content Studio review pack before a Buzz relay attempt is leased.

begin;

create or replace function public.claim_origintrail_buzz_delivery_v2(
    target_workspace_id uuid,
    target_job_id uuid,
    target_event_id text,
    target_channel_id uuid,
    target_message_sha256 text,
    target_request_sha256 text,
    target_attachment_sha256 text,
    target_worker_id text,
    target_lease_seconds integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    response jsonb;
    receipt agent_runtime.buzz_delivery_receipts%rowtype;
begin
    if lower(coalesce(target_attachment_sha256, '')) !~ '^[a-f0-9]{64}$'
       or not exists (
           select 1
           from agent_runtime.origintrail_batch_review_packs as review_pack
           where review_pack.workspace_id = target_workspace_id
             and review_pack.job_id = target_job_id
             and review_pack.banner_sha256 = lower(target_attachment_sha256)
             and review_pack.protocol_version = 'origintrail-review-pack@1'
             and review_pack.review_pack_sha256
                  = private.origintrail_review_pack_sha256(
                        review_pack.workspace_id,
                        review_pack.job_id,
                        review_pack.content_item_id,
                        review_pack.source_item_id,
                        review_pack.input_sha256,
                        review_pack.result_sha256,
                        review_pack.source_content_sha256,
                        review_pack.banner_sha256
                    )
       ) then
        raise exception 'OriginTrail Buzz attachment is not review-pack bound'
            using errcode = '23514';
    end if;

    response := public.claim_origintrail_buzz_delivery(
        target_workspace_id,
        target_job_id,
        target_event_id,
        target_channel_id,
        target_message_sha256,
        target_request_sha256,
        target_worker_id,
        target_lease_seconds
    );

    select delivery.* into receipt
    from agent_runtime.buzz_delivery_receipts as delivery
    where delivery.workspace_id = target_workspace_id
      and delivery.event_id = lower(target_event_id)
      and delivery.job_id = target_job_id
    for update;
    if not found then
        raise exception 'OriginTrail Buzz V2 receipt was not created'
            using errcode = '23514';
    end if;
    if receipt.attachment_sha256 is not null
       and receipt.attachment_sha256
            is distinct from lower(target_attachment_sha256) then
        raise exception 'OriginTrail Buzz attachment conflicts with its receipt'
            using errcode = '23505';
    end if;
    if receipt.attachment_sha256 is null then
        update agent_runtime.buzz_delivery_receipts
        set attachment_sha256 = lower(target_attachment_sha256),
            updated_at = statement_timestamp()
        where workspace_id = receipt.workspace_id
          and event_id = receipt.event_id
        returning * into receipt;
    end if;

    return response || jsonb_build_object(
        'attachment_sha256', receipt.attachment_sha256
    );
end;
$$;

revoke all on function public.claim_origintrail_buzz_delivery_v2(
    uuid, uuid, text, uuid, text, text, text, text, integer
) from public, anon, authenticated;
grant execute on function public.claim_origintrail_buzz_delivery_v2(
    uuid, uuid, text, uuid, text, text, text, text, integer
) to service_role;

commit;
