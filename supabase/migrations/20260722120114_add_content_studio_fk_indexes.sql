-- Cover all composite foreign keys used to validate immutable content
-- versions. The earlier two-column indexes remain useful for item/version
-- listings; these four specifically protect parent updates and deletes.

begin;

create index if not exists approvals_workspace_item_version_idx
    on public.approvals (workspace_id, content_item_id, content_version_id);
create index if not exists assets_workspace_item_version_idx
    on public.assets (workspace_id, content_item_id, content_version_id);
create index if not exists figma_links_workspace_item_version_idx
    on public.figma_links (workspace_id, content_item_id, content_version_id);
create index if not exists publications_workspace_item_version_idx
    on public.publications (workspace_id, content_item_id, content_version_id);

commit;
