-- LOCAL ONLY: independent one-way dispatch consumption. No production grants.
begin;
create table private.content_ops_button_confirmation_dispatches (
    delivery_id uuid primary key references private.content_ops_button_confirmation_deliveries(delivery_id),
    permission_id uuid not null unique references private.content_ops_button_confirmation_send_permissions(permission_id),
    card_id uuid not null unique references private.content_ops_button_control_evidence(card_id),
    request_sha256 text not null check (request_sha256 ~ '^[a-f0-9]{64}$'),
    consumed_at timestamptz not null default clock_timestamp() check (isfinite(consumed_at))
);
alter table private.content_ops_button_confirmation_dispatches enable row level security;
alter table private.content_ops_button_confirmation_dispatches force row level security;
revoke all on private.content_ops_button_confirmation_dispatches from public,anon,authenticated,service_role;
create trigger content_ops_button_confirmation_dispatch_immutable
    before update or delete on private.content_ops_button_confirmation_dispatches
    for each row execute function private.guard_content_ops_button_control_evidence();
commit;
