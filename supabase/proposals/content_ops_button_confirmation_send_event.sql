-- LOCAL only. Existing authenticated ingress ownership required; no runtime grant.
begin;
create table private.content_ops_button_confirmation_send_events (
    bot_binding text not null check (bot_binding ~ '^[a-f0-9]{64}$'),
    message_binding text primary key check (message_binding ~ '^[a-f0-9]{64}$'),
    update_binding text not null unique check (update_binding ~ '^[a-f0-9]{64}$'),
    permission_id uuid not null unique references private.content_ops_button_confirmation_send_permissions(permission_id)
        deferrable initially deferred,
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    received_at timestamptz not null check (isfinite(received_at)),
    recorded_at timestamptz not null default clock_timestamp() check (isfinite(recorded_at)),
    check (received_at<=recorded_at and recorded_at<=received_at+interval '5 seconds')
);
alter table private.content_ops_button_confirmation_send_events enable row level security;
alter table private.content_ops_button_confirmation_send_events force row level security;
revoke all on private.content_ops_button_confirmation_send_events from public,anon,authenticated,service_role;
create trigger content_ops_button_confirmation_send_event_immutable
    before update or delete on private.content_ops_button_confirmation_send_events
    for each row execute function private.guard_content_ops_button_control_evidence();
commit;
