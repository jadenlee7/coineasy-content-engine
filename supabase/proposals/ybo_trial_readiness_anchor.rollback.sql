-- LOCAL_ROLLBACK_PROOF: one code-pinned, owner-inserted readiness anchor.
-- Empty schema only. No runtime grant, anchor row, business operation or send.
-- The separately approved operator may insert once after exact OFF readiness.
begin;

create table private.ybo_trial_readiness_anchor (
    workspace_id uuid not null check (workspace_id='5cc143cc-b253-4841-8a6c-ab3ac98623a6'::uuid),
    trial_id uuid primary key check (trial_id='1a2bef47-4827-4bc3-9b4d-5baf2714015d'::uuid),
    plan_sha256 text not null check (plan_sha256 ~ '^[a-f0-9]{64}$'),
    release_sha text not null check (release_sha ~ '^[a-f0-9]{40}$'),
    not_before timestamptz not null,
    deadline timestamptz not null,
    kst_date date not null,
    check (isfinite(not_before) and isfinite(deadline)),
    check (date_trunc('second',not_before)=not_before and date_trunc('second',deadline)=deadline),
    check (deadline=not_before+interval '2 hours'),
    check (timezone('Asia/Seoul',not_before)::date=kst_date
        and timezone('Asia/Seoul',deadline)::date=kst_date)
);
-- Explicitly remove any inherited default grants on this NEW private table.
-- The owner retains administrative SELECT/INSERT; no application role may write.
revoke all on table private.ybo_trial_readiness_anchor from public,anon,authenticated,service_role;
alter table private.ybo_trial_readiness_anchor enable row level security;

create function private.guard_ybo_trial_readiness_anchor()
returns trigger language plpgsql volatile set search_path='' as $$
declare observed_at timestamptz;
begin
    if TG_OP <> 'INSERT' then
        raise exception 'ybo_readiness_anchor_immutable' using errcode='55000';
    end if;
    observed_at := clock_timestamp();
    if new.not_before is null or new.not_before>observed_at
       or new.not_before<observed_at-interval '60 seconds' then
        raise exception 'ybo_readiness_anchor_start_not_fresh' using errcode='22023';
    end if;
    if exists(select 1 from private.ybo_trial_readiness_anchor) then
        raise exception 'ybo_readiness_anchor_already_fixed' using errcode='55000';
    end if;
    return new;
end $$;
revoke all on function private.guard_ybo_trial_readiness_anchor() from public,anon,authenticated,service_role;
create trigger ybo_readiness_anchor_insert
before insert on private.ybo_trial_readiness_anchor for each row
execute function private.guard_ybo_trial_readiness_anchor();
create trigger ybo_readiness_anchor_immutable
before update or delete or truncate on private.ybo_trial_readiness_anchor for each statement
execute function private.guard_ybo_trial_readiness_anchor();

rollback; -- LOCAL_ROLLBACK_PROOF; never change to COMMIT here.
