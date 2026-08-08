-- Finalize a deterministic OriginTrail review pack after the PNG object and
-- Content Studio version have been durably recorded. The public generation job
-- remains an open Batch handoff until a separate, trustworthy release action.

begin;

create or replace function public.bind_origintrail_batch_review_pack(
    target_workspace_id uuid,
    target_job_id uuid,
    target_content_item_id uuid,
    target_content_version_id uuid,
    target_asset_id uuid,
    target_source_item_id uuid,
    target_input_sha256 text,
    target_result_sha256 text,
    target_source_content_sha256 text,
    target_banner_sha256 text,
    target_review_pack_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    review_detail jsonb;
    item public.content_items%rowtype;
    version public.content_versions%rowtype;
    asset public.assets%rowtype;
    source public.source_items%rowtype;
    existing agent_runtime.origintrail_batch_review_packs%rowtype;
    expected_review_pack_sha256 text;
    source_link_count integer;
begin
    if target_workspace_id is null
       or target_job_id is null
       or target_content_item_id is null
       or target_content_version_id is null
       or target_asset_id is null
       or target_source_item_id is null
       or lower(coalesce(target_input_sha256, '')) !~ '^[a-f0-9]{64}$'
       or lower(coalesce(target_result_sha256, '')) !~ '^[a-f0-9]{64}$'
       or lower(coalesce(target_source_content_sha256, '')) !~ '^[a-f0-9]{64}$'
       or lower(coalesce(target_banner_sha256, '')) !~ '^[a-f0-9]{64}$'
       or lower(coalesce(target_review_pack_sha256, '')) !~ '^[a-f0-9]{64}$' then
        raise exception 'OriginTrail review pack binding is invalid'
            using errcode = '22023';
    end if;

    expected_review_pack_sha256 := private.origintrail_review_pack_sha256(
        target_workspace_id,
        target_job_id,
        target_content_item_id,
        target_source_item_id,
        lower(target_input_sha256),
        lower(target_result_sha256),
        lower(target_source_content_sha256),
        lower(target_banner_sha256)
    );
    if expected_review_pack_sha256 <> lower(target_review_pack_sha256) then
        raise exception 'OriginTrail review pack digest does not match'
            using errcode = '23514';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        target_workspace_id::text || ':' || target_job_id::text
            || ':origintrail-review-pack',
        0
    ));

    select review_pack.* into existing
    from agent_runtime.origintrail_batch_review_packs as review_pack
    where review_pack.workspace_id = target_workspace_id
      and review_pack.job_id = target_job_id;
    if found then
        if existing.content_item_id is distinct from target_content_item_id
           or existing.content_version_id is distinct from target_content_version_id
           or existing.asset_id is distinct from target_asset_id
           or existing.source_item_id is distinct from target_source_item_id
           or existing.input_sha256 is distinct from lower(target_input_sha256)
           or existing.result_sha256 is distinct from lower(target_result_sha256)
           or existing.source_content_sha256
                is distinct from lower(target_source_content_sha256)
           or existing.banner_sha256 is distinct from lower(target_banner_sha256)
           or existing.review_pack_sha256
                is distinct from lower(target_review_pack_sha256) then
            raise exception 'OriginTrail review pack retry conflicts with its binding'
                using errcode = '23505';
        end if;
        return jsonb_build_object(
            'job_id', existing.job_id,
            'content_item_id', existing.content_item_id,
            'content_version_id', existing.content_version_id,
            'asset_id', existing.asset_id,
            'source_item_id', existing.source_item_id,
            'banner_sha256', existing.banner_sha256,
            'review_pack_sha256', existing.review_pack_sha256,
            'protocol_version', existing.protocol_version,
            'reused', true
        );
    end if;

    review_detail := public.get_agent_batch_review_item(
        target_workspace_id, target_job_id
    );
    if review_detail is null
       or review_detail ->> 'job_id' is distinct from target_job_id::text
       or review_detail ->> 'request_id' is distinct from target_content_item_id::text
       or review_detail -> 'source_item_ids'
            is distinct from jsonb_build_array(target_source_item_id::text)
       or review_detail ->> 'input_sha256'
            is distinct from lower(target_input_sha256)
       or review_detail ->> 'result_sha256'
            is distinct from lower(target_result_sha256)
       or encode(extensions.digest(
            pg_catalog.convert_to(review_detail ->> 'source_content', 'UTF8'),
            'sha256'
          ), 'hex') is distinct from lower(target_source_content_sha256) then
        raise exception 'OriginTrail Batch evidence is not materialization-ready'
            using errcode = '23514';
    end if;

    select content.* into item
    from public.content_items as content
    where content.workspace_id = target_workspace_id
      and content.id = target_content_item_id
    for update;
    if not found
       or item.client_id is distinct from 'origintrail'
       or item.content_kind is distinct from 'daily_news'
       or item.status is distinct from 'needs_review'
       or item.current_version_id is distinct from target_content_version_id then
        raise exception 'OriginTrail review pack content item is invalid'
            using errcode = '23514';
    end if;

    select content_version.* into version
    from public.content_versions as content_version
    where content_version.workspace_id = target_workspace_id
      and content_version.content_item_id = target_content_item_id
      and content_version.id = target_content_version_id;
    if not found
       or version.version_number <> 1
       or version.prompt_version is distinct from 'origintrail-batch-review-pack@1'
       or version.locale is distinct from 'ko-KR'
       or version.title is distinct from review_detail ->> 'title'
       or version.content ->> 'request_hash'
            is distinct from lower(target_review_pack_sha256)
       or version.generation_meta ->> 'request_hash'
            is distinct from lower(target_review_pack_sha256)
       or version.generation_meta -> 'mock_mode' is distinct from 'false'::jsonb
       or version.generation_meta ->> 'renderer'
            is distinct from 'origintrail-deterministic-svg'
       or version.generation_meta ->> 'batch_job_id'
            is distinct from target_job_id::text
       or version.generation_meta ->> 'batch_input_sha256'
            is distinct from lower(target_input_sha256)
       or version.generation_meta ->> 'batch_result_sha256'
            is distinct from lower(target_result_sha256)
       or version.generation_meta ->> 'banner_sha256'
            is distinct from lower(target_banner_sha256)
       or not private.has_valid_double_fact_check_report(version.generation_meta)
       or version.channel_copy is distinct from jsonb_build_object(
            'telegram', review_detail -> 'result_payload' ->> 'telegram_copy_ko',
            'x', review_detail -> 'result_payload' ->> 'x_copy_ko'
          )
       or version.deliverables is distinct from jsonb_build_object(
            'primary_asset_id', target_asset_id::text,
            'asset_ids', jsonb_build_array(target_asset_id::text)
          ) then
        raise exception 'OriginTrail review pack version is invalid'
            using errcode = '23514';
    end if;

    select generated_asset.* into asset
    from public.assets as generated_asset
    join storage.objects as stored
      on stored.bucket_id = generated_asset.storage_bucket
     and stored.name = generated_asset.storage_path
    where generated_asset.id = target_asset_id
      and generated_asset.workspace_id = target_workspace_id
      and generated_asset.content_item_id = target_content_item_id
      and generated_asset.content_version_id = target_content_version_id
      and generated_asset.asset_kind = 'png'
      and generated_asset.storage_bucket = 'content-studio'
      and generated_asset.storage_path = target_workspace_id::text
            || '/origintrail/' || target_asset_id::text || '/news-card.png'
      and generated_asset.mime_type = 'image/png'
      and generated_asset.byte_size between 24 and 4194304
      and generated_asset.sha256 = lower(target_banner_sha256)
      and generated_asset.width = 1200
      and generated_asset.height = 630
      and generated_asset.metadata = jsonb_build_object(
            'filename', 'news-card.png'
          );
    if not found then
        raise exception 'OriginTrail review pack PNG is invalid or missing'
            using errcode = '23514';
    end if;

    select source_item.* into source
    from public.source_items as source_item
    where source_item.workspace_id = target_workspace_id
      and source_item.client_id = 'origintrail'
      and source_item.id = target_source_item_id;
    if not found
       or source.canonical_url is distinct from review_detail ->> 'source_url'
       or source.body is distinct from review_detail ->> 'source_content'
       or encode(extensions.digest(
            pg_catalog.convert_to(source.body, 'UTF8'), 'sha256'
          ), 'hex') is distinct from lower(target_source_content_sha256) then
        raise exception 'OriginTrail review pack source is invalid'
            using errcode = '23514';
    end if;

    select count(*) into source_link_count
    from public.content_source_links as source_link
    where source_link.workspace_id = target_workspace_id
      and source_link.content_item_id = target_content_item_id;
    if source_link_count > 0 then
        raise exception 'OriginTrail review pack source binding conflicts'
            using errcode = '23505';
    end if;
    insert into public.content_source_links (
        workspace_id, client_id, content_item_id, source_item_id, position
    ) values (
        target_workspace_id, 'origintrail', target_content_item_id,
        target_source_item_id, 0
    );

    insert into agent_runtime.origintrail_batch_review_packs (
        workspace_id, job_id, client_id, content_item_id, content_version_id,
        asset_id, source_item_id, input_sha256, result_sha256,
        source_content_sha256, banner_sha256, review_pack_sha256,
        protocol_version
    ) values (
        target_workspace_id, target_job_id, 'origintrail',
        target_content_item_id, target_content_version_id, target_asset_id,
        target_source_item_id, lower(target_input_sha256),
        lower(target_result_sha256), lower(target_source_content_sha256),
        lower(target_banner_sha256), lower(target_review_pack_sha256),
        'origintrail-review-pack@1'
    ) returning * into existing;

    insert into public.event_log (
        workspace_id, entity_type, entity_id, event_type, data
    ) values (
        target_workspace_id,
        'content_item',
        target_content_item_id,
        'origintrail_batch_review_pack_materialized',
        jsonb_build_object(
            'job_id', target_job_id,
            'content_version_id', target_content_version_id,
            'asset_id', target_asset_id,
            'source_item_id', target_source_item_id,
            'banner_sha256', lower(target_banner_sha256),
            'review_pack_sha256', lower(target_review_pack_sha256),
            'automatic_publication', false
        )
    );

    return jsonb_build_object(
        'job_id', existing.job_id,
        'content_item_id', existing.content_item_id,
        'content_version_id', existing.content_version_id,
        'asset_id', existing.asset_id,
        'source_item_id', existing.source_item_id,
        'banner_sha256', existing.banner_sha256,
        'review_pack_sha256', existing.review_pack_sha256,
        'protocol_version', existing.protocol_version,
        'reused', false
    );
end;
$$;

revoke all on function public.bind_origintrail_batch_review_pack(
    uuid, uuid, uuid, uuid, uuid, uuid, text, text, text, text, text
) from public, anon, authenticated;
grant execute on function public.bind_origintrail_batch_review_pack(
    uuid, uuid, uuid, uuid, uuid, uuid, text, text, text, text, text
) to service_role;

commit;
