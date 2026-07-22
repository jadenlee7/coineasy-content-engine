-- Bootstrap the shared-token Content Studio without inventing a fake auth user.
-- A real owner can be attached later through Supabase Auth; until then only the
-- service role used by Netlify can reach this workspace.

begin;

insert into public.workspaces (name, slug, created_by)
values ('CoinEasy Content Studio', 'coineasy-content-studio', null)
on conflict (slug) do update
set name = excluded.name;

insert into public.workspace_clients (
    workspace_id,
    client_id,
    display_name,
    config_revision,
    active,
    created_by
)
select
    workspace.id,
    client.client_id,
    client.display_name,
    'figma-2026',
    true,
    null
from public.workspaces as workspace
cross join (
    values
        ('yellow', 'Yellow'),
        ('origintrail', 'OriginTrail'),
        ('squid', 'Squid'),
        ('babylon', 'Babylon')
) as client(client_id, display_name)
where workspace.slug = 'coineasy-content-studio'
on conflict (workspace_id, client_id) do update
set display_name = excluded.display_name,
    config_revision = excluded.config_revision,
    active = true;

commit;
