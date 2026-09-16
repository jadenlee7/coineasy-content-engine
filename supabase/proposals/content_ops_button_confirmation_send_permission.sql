-- LOCAL proposal only. Independent operator issuance is NOT implemented.
begin;
create table private.content_ops_button_confirmation_send_permissions (
    permission_id uuid primary key check (permission_id<>'00000000-0000-0000-0000-000000000000'),
    delivery_id uuid not null unique check (delivery_id<>'00000000-0000-0000-0000-000000000000'),
    card_id uuid not null unique references private.content_ops_button_control_evidence(card_id),
    actor_id uuid not null references auth.users(id),
    human_binding text not null check (human_binding ~ '^[a-f0-9]{64}$'),
    target_binding text not null check (target_binding ~ '^[a-f0-9]{64}$'),
    request_sha256 text not null check (request_sha256 ~ '^[a-f0-9]{64}$'),
    action text not null check (action='send_private_confirmation@1'),
    authorized_at timestamptz not null check (isfinite(authorized_at)),
    expires_at timestamptz not null check (isfinite(expires_at)),
    active boolean not null default false,
    recorded_at timestamptz not null default clock_timestamp() check (isfinite(recorded_at)),
    check (authorized_at<=recorded_at and recorded_at<expires_at
        and authorized_at<expires_at and expires_at<=authorized_at+interval '30 minutes')
);
alter table private.content_ops_button_confirmation_send_permissions enable row level security;
alter table private.content_ops_button_confirmation_send_permissions force row level security;
revoke all on private.content_ops_button_confirmation_send_permissions from public,anon,authenticated,service_role;
-- Existing trigger allows only active -> false, with every other field pinned.
create trigger content_ops_button_confirmation_send_permission_immutable
    before update or delete on private.content_ops_button_confirmation_send_permissions
    for each row execute function private.guard_content_ops_button_markup_approval();
commit;
