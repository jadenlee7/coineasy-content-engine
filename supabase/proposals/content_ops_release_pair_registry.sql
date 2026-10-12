-- LOCAL-ONLY, UNMOUNTED. Trusted release-pair registry; no rows or runtime grants.
-- release_sha is always the Content Engine SHA. callback_release_sha identifies
-- the separate callback bot. Neither value may stand in for the other.
begin;
create table private.content_ops_release_pairs (
    release_pair_sha256 text primary key check (release_pair_sha256 ~ '^[a-f0-9]{64}$'),
    release_sha text not null check (release_sha ~ '^[a-f0-9]{40}$'),
    callback_release_sha text not null check (callback_release_sha ~ '^[a-f0-9]{40}$'),
    created_at timestamptz not null default clock_timestamp(),
    unique(release_sha,callback_release_sha,release_pair_sha256),
    check (release_pair_sha256=encode(sha256(convert_to(
        E'content-ops-release-pair@1\nengine='||release_sha||
        E'\ncallback='||callback_release_sha||E'\n','UTF8')),'hex'))
);
create table private.content_ops_current_release_pairs (
    workspace_id uuid not null,
    client_id text not null check (client_id in ('yellow','babylon','squid','origintrail')),
    release_pair_sha256 text not null references private.content_ops_release_pairs(release_pair_sha256),
    selected_at timestamptz not null default clock_timestamp(),
    primary key(workspace_id,client_id),
    foreign key(workspace_id,client_id)
        references public.workspace_clients(workspace_id,client_id) on delete restrict
);
alter table private.content_ops_release_pairs enable row level security;
alter table private.content_ops_release_pairs force row level security;
alter table private.content_ops_current_release_pairs enable row level security;
alter table private.content_ops_current_release_pairs force row level security;
revoke all on private.content_ops_release_pairs,private.content_ops_current_release_pairs
    from public,anon,authenticated,service_role;

create function private.guard_content_ops_release_pair_registry()
returns trigger language plpgsql security invoker set search_path='' as $$
begin
    raise exception 'release_pair_registry_immutable' using errcode='23514';
end $$;
create trigger content_ops_release_pairs_immutable before update or delete
    on private.content_ops_release_pairs for each row
    execute function private.guard_content_ops_release_pair_registry();
create trigger content_ops_release_pairs_no_truncate before truncate
    on private.content_ops_release_pairs for each statement
    execute function private.guard_content_ops_release_pair_registry();

create function private.guard_content_ops_current_release_pair()
returns trigger language plpgsql security invoker set search_path='' as $$
begin
    if tg_op in ('DELETE','TRUNCATE') then
        raise exception 'current_release_pair_removal_forbidden' using errcode='23514';
    end if;
    if tg_op='UPDATE' then
        if new.workspace_id is distinct from old.workspace_id
           or new.client_id is distinct from old.client_id then
            raise exception 'current_release_pair_scope_immutable' using errcode='23514';
        end if;
        new.selected_at:=greatest(clock_timestamp(),old.selected_at+interval '1 microsecond');
    else
        new.selected_at:=clock_timestamp();
    end if;
    return new;
end $$;
create trigger content_ops_current_release_pairs_guard before insert or update or delete
    on private.content_ops_current_release_pairs for each row
    execute function private.guard_content_ops_current_release_pair();
create trigger content_ops_current_release_pairs_no_truncate before truncate
    on private.content_ops_current_release_pairs for each statement
    execute function private.guard_content_ops_current_release_pair();

create function private.require_content_ops_release_pair(
    target_workspace_id uuid,target_client_id text,target_release_sha text,
    target_callback_release_sha text,target_release_pair_sha256 text,
    target_created_at timestamptz default null
) returns void language plpgsql volatile security invoker set search_path='' as $$
declare selected_pair text; selection_time timestamptz;
begin
    if target_workspace_id is null or target_client_id is null
       or target_client_id not in ('yellow','babylon','squid','origintrail')
       or target_release_sha is null or target_release_sha !~ '^[a-f0-9]{40}$'
       or target_callback_release_sha is null or target_callback_release_sha !~ '^[a-f0-9]{40}$'
       or target_release_pair_sha256 is null or target_release_pair_sha256 !~ '^[a-f0-9]{64}$' then
        raise exception 'release_pair_arguments_invalid' using errcode='22023';
    end if;
    -- Lock the scope, not just a matching digest. A concurrent trusted UPDATE
    -- cannot switch the current pair between this check and admission COMMIT.
    -- Callers take their content-item lock before this scoped row lock.
    select s.release_pair_sha256,s.selected_at into selected_pair,selection_time
      from private.content_ops_current_release_pairs s
      where s.workspace_id=target_workspace_id and s.client_id=target_client_id
      for share;
    if not found or selected_pair is distinct from target_release_pair_sha256
       or (target_created_at is not null and selection_time>target_created_at)
       or not exists(select 1 from private.content_ops_release_pairs p
           where p.release_pair_sha256=selected_pair
             and p.release_sha=target_release_sha
             and p.callback_release_sha=target_callback_release_sha) then
        raise exception 'release_pair_missing_or_changed' using errcode='23514';
    end if;
end $$;
revoke all on function private.guard_content_ops_release_pair_registry()
    from public,anon,authenticated,service_role;
revoke all on function private.guard_content_ops_current_release_pair()
    from public,anon,authenticated,service_role;
revoke all on function private.require_content_ops_release_pair(uuid,text,text,text,text,timestamptz)
    from public,anon,authenticated,service_role;
commit;
