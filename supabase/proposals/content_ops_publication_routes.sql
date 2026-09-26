-- LOCAL-ONLY, ungranted destination registry. Never seed guessed account IDs.
-- A trusted verifier must bind client, provider, exact account/destination,
-- public display label, and allowed action into each opaque route digest.
-- This proposal contains no real routes, credentials, grants or provider I/O.
begin;
create table private.content_ops_publication_routes (
    workspace_id uuid not null,
    client_id text not null check (client_id in ('yellow','origintrail','squid','babylon')),
    channel text not null check (channel in ('telegram','typefully_x')),
    route_binding text not null check (route_binding ~ '^[a-f0-9]{64}$'),
    release_sha text not null check (release_sha ~ '^[a-f0-9]{40}$'),
    verified_at timestamptz not null,
    active boolean not null default false,
    primary key (workspace_id,client_id,channel),
    foreign key (workspace_id,client_id)
        references public.workspace_clients(workspace_id,client_id) on delete restrict
);
alter table private.content_ops_publication_routes enable row level security;
alter table private.content_ops_publication_routes force row level security;
revoke all on private.content_ops_publication_routes
    from public,anon,authenticated,service_role;

create function private.require_content_ops_publication_routes(
    target_workspace_id uuid,target_client_id text,target_release_sha text,
    expected_telegram_binding text,expected_typefully_binding text
) returns void language plpgsql volatile security invoker set search_path='' as $$
declare routes integer;
begin
    if target_workspace_id is null or target_client_id is null
       or target_client_id not in ('yellow','origintrail','squid','babylon')
       or target_release_sha is null or target_release_sha !~ '^[a-f0-9]{40}$'
       or expected_telegram_binding is null or expected_telegram_binding !~ '^[a-f0-9]{64}$'
       or expected_typefully_binding is null or expected_typefully_binding !~ '^[a-f0-9]{64}$' then
        raise exception 'publication_route_arguments_invalid' using errcode='22023';
    end if;
    -- Lock BOTH routes before validation. A change cannot slip between the
    -- route check and approval/intent insertion. Caller locks the item first.
    perform 1 from private.content_ops_publication_routes p
        where p.workspace_id=target_workspace_id and p.client_id=target_client_id
        order by p.channel for share;
    select count(*) into routes from private.content_ops_publication_routes p
        where p.workspace_id=target_workspace_id and p.client_id=target_client_id
          and p.active and p.release_sha=target_release_sha
          and p.verified_at<=clock_timestamp()
          and p.verified_at>clock_timestamp()-interval '15 minutes'
          and p.route_binding=case p.channel when 'telegram'
              then expected_telegram_binding else expected_typefully_binding end;
    if routes<>2 then
        raise exception 'publication_routes_missing_or_changed' using errcode='23514';
    end if;
end $$;
revoke all on function private.require_content_ops_publication_routes(
    uuid,text,text,text,text) from public,anon,authenticated,service_role;
commit;
