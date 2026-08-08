-- Bind one verified OriginTrail Batch result to one immutable Content Studio
-- version and its exact deterministic PNG. Rows are created only by the
-- materialization RPC added after the Buzz review evidence gate.

begin;

-- The asset table does not originally expose a composite unique constraint
-- including its id; add the exact identity required by the review-pack FK.
alter table public.assets
    add constraint assets_workspace_item_version_id_key
    unique (workspace_id, content_item_id, content_version_id, id);

alter table agent_runtime.buzz_delivery_receipts
    add column attachment_sha256 text check (
        attachment_sha256 is null or attachment_sha256 ~ '^[a-f0-9]{64}$'
    );

create table agent_runtime.origintrail_batch_review_packs (
    workspace_id uuid not null references public.workspaces(id) on delete restrict,
    job_id uuid not null,
    client_id text not null default 'origintrail' check (client_id = 'origintrail'),
    content_item_id uuid not null,
    content_version_id uuid not null,
    asset_id uuid not null,
    source_item_id uuid not null,
    input_sha256 text not null check (input_sha256 ~ '^[a-f0-9]{64}$'),
    result_sha256 text not null check (result_sha256 ~ '^[a-f0-9]{64}$'),
    source_content_sha256 text not null check (
        source_content_sha256 ~ '^[a-f0-9]{64}$'
    ),
    banner_sha256 text not null check (banner_sha256 ~ '^[a-f0-9]{64}$'),
    review_pack_sha256 text not null check (
        review_pack_sha256 ~ '^[a-f0-9]{64}$'
    ),
    protocol_version text not null check (
        protocol_version = 'origintrail-review-pack@1'
    ),
    created_at timestamptz not null default now(),
    primary key (workspace_id, job_id),
    unique (workspace_id, content_item_id),
    unique (workspace_id, content_version_id),
    unique (workspace_id, asset_id),
    foreign key (workspace_id, job_id)
        references agent_runtime.batch_jobs(workspace_id, job_id)
        on delete restrict,
    foreign key (workspace_id, content_item_id)
        references public.content_items(workspace_id, id)
        on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id)
        references public.content_versions(workspace_id, content_item_id, id)
        on delete restrict,
    foreign key (workspace_id, content_item_id, content_version_id, asset_id)
        references public.assets(workspace_id, content_item_id, content_version_id, id)
        on delete restrict,
    foreign key (workspace_id, client_id, source_item_id)
        references public.source_items(workspace_id, client_id, id)
        on delete restrict
);

create or replace function private.origintrail_review_pack_sha256(
    target_workspace_id uuid,
    target_job_id uuid,
    target_content_item_id uuid,
    target_source_item_id uuid,
    target_input_sha256 text,
    target_result_sha256 text,
    target_source_content_sha256 text,
    target_banner_sha256 text
)
returns text
language sql
immutable
strict
set search_path = ''
as $$
    select encode(extensions.digest(
        convert_to('coineasy-origintrail-review-pack', 'UTF8')
        || decode('00', 'hex')
        || convert_to('1.0', 'UTF8') || decode('00', 'hex')
        || convert_to(target_workspace_id::text, 'UTF8') || decode('00', 'hex')
        || convert_to(target_job_id::text, 'UTF8') || decode('00', 'hex')
        || convert_to(target_content_item_id::text, 'UTF8') || decode('00', 'hex')
        || convert_to(target_source_item_id::text, 'UTF8') || decode('00', 'hex')
        || convert_to(target_input_sha256, 'UTF8') || decode('00', 'hex')
        || convert_to(target_result_sha256, 'UTF8') || decode('00', 'hex')
        || convert_to(target_source_content_sha256, 'UTF8') || decode('00', 'hex')
        || convert_to(target_banner_sha256, 'UTF8'),
        'sha256'
    ), 'hex')
$$;

create or replace function private.reject_origintrail_batch_review_pack_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'OriginTrail Batch review packs are immutable'
        using errcode = '55000';
end;
$$;

create trigger origintrail_batch_review_packs_immutable
before update or delete on agent_runtime.origintrail_batch_review_packs
for each row execute function
    private.reject_origintrail_batch_review_pack_mutation();

alter table agent_runtime.origintrail_batch_review_packs enable row level security;
alter table agent_runtime.origintrail_batch_review_packs force row level security;

revoke all on table agent_runtime.origintrail_batch_review_packs
from public, anon, authenticated, service_role;
revoke all on function private.origintrail_review_pack_sha256(
    uuid, uuid, uuid, uuid, text, text, text, text
) from public, anon, authenticated, service_role;
revoke all on function private.reject_origintrail_batch_review_pack_mutation()
from public, anon, authenticated, service_role;

commit;
