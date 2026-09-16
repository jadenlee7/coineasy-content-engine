-- LOCAL proposal: pre-send uncertainty ledger. No sender or runtime grants.
begin;
create table private.content_ops_button_confirmation_deliveries (
    delivery_id uuid primary key check (delivery_id<>'00000000-0000-0000-0000-000000000000'),
    approval_id uuid not null unique,
    card_id uuid not null unique references private.content_ops_button_control_evidence(card_id),
    actor_id uuid not null references auth.users(id),
    target_binding text not null check (target_binding ~ '^[a-f0-9]{64}$'),
    request_sha256 text not null check (request_sha256 ~ '^[a-f0-9]{64}$'),
    expires_at timestamptz not null check (isfinite(expires_at)),
    reserved_at timestamptz not null default clock_timestamp() check (isfinite(reserved_at)),
    status text not null default 'unknown' check (status in ('unknown','response_matched')),
    response_sha256 text check (response_sha256 ~ '^[a-f0-9]{64}$'),
    observed_at timestamptz check (isfinite(observed_at)),
    recorded_approval_id uuid references private.content_ops_button_markup_confirmations(approval_id),
    check (reserved_at<expires_at and expires_at<=reserved_at+interval '30 minutes'),
    check ((status='unknown' and response_sha256 is null and observed_at is null and recorded_approval_id is null)
        or (status='response_matched' and response_sha256 is not null and observed_at is not null
            and observed_at>=reserved_at and observed_at<expires_at
            and recorded_approval_id is not null and recorded_approval_id=approval_id))
);
alter table private.content_ops_button_confirmation_deliveries enable row level security;
alter table private.content_ops_button_confirmation_deliveries force row level security;
revoke all on private.content_ops_button_confirmation_deliveries from public,anon,authenticated,service_role;
create function private.guard_content_ops_button_confirmation_delivery()
returns trigger language plpgsql security invoker set search_path='' as $$
begin
    if tg_op='UPDATE' and old.status='unknown' and new.status='response_matched'
        and (to_jsonb(old)-array['status','response_sha256','observed_at','recorded_approval_id'])=
            (to_jsonb(new)-array['status','response_sha256','observed_at','recorded_approval_id'])
        then return new;end if;
    raise exception 'button_confirmation_delivery_immutable' using errcode='23514';
end $$;
create trigger content_ops_button_confirmation_delivery_immutable
    before update or delete on private.content_ops_button_confirmation_deliveries
    for each row execute function private.guard_content_ops_button_confirmation_delivery();
revoke all on function private.guard_content_ops_button_confirmation_delivery()
    from public,anon,authenticated,service_role;
commit;
