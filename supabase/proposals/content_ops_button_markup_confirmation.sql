-- LOCAL proposal only. No delivery importer, public RPC or runtime grants.
begin;
create table private.content_ops_button_markup_confirmations (
    approval_id uuid primary key check (approval_id<>'00000000-0000-0000-0000-000000000000'),
    attempt_id uuid not null unique check (attempt_id<>'00000000-0000-0000-0000-000000000000'),
    card_id uuid not null unique,
    actor_id uuid not null references auth.users(id),
    human_binding text not null check (human_binding ~ '^[a-f0-9]{64}$'),
    plan_seal text not null check (plan_seal ~ '^[a-f0-9]{64}$'),
    original_receipt_sha256 text not null,
    started_at bigint not null check (started_at>0),
    expires_at bigint not null check (expires_at<4294967296),
    bot_id bigint not null check (bot_id>0 and bot_id<4503599627370496),
    chat_id bigint not null check (chat_id<0 and chat_id> -4503599627370496),
    message_id bigint not null check (message_id>0 and message_id<4503599627370496),
    thread_id bigint check (thread_id>0 and thread_id<4503599627370496),
    message_date bigint not null,
    delivered_at timestamptz not null check (isfinite(delivered_at)),
    active boolean not null default false,
    recorded_at timestamptz not null default clock_timestamp() check (isfinite(recorded_at)),
    check (started_at<expires_at and expires_at-started_at<=1800),
    check (started_at<=message_date and to_timestamp(message_date)<=delivered_at
        and delivered_at<=recorded_at and recorded_at<to_timestamp(expires_at)),
    foreign key(card_id,original_receipt_sha256)
        references private.content_ops_button_control_evidence(card_id,receipt_sha256),
    unique(bot_id,chat_id,message_id)
);

create table private.content_ops_button_markup_confirmation_events (
    bot_binding text not null check (bot_binding ~ '^[a-f0-9]{64}$'),
    callback_binding text primary key check (callback_binding ~ '^[a-f0-9]{64}$'),
    update_binding text not null unique check (update_binding ~ '^[a-f0-9]{64}$'),
    approval_id uuid not null unique references private.content_ops_button_markup_confirmations(approval_id),
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    received_at timestamptz not null check (isfinite(received_at)),
    recorded_at timestamptz not null default clock_timestamp() check (isfinite(recorded_at)),
    check (received_at<=recorded_at and recorded_at<=received_at+interval '5 seconds')
);

alter table private.content_ops_button_markup_confirmations enable row level security;
alter table private.content_ops_button_markup_confirmations force row level security;
alter table private.content_ops_button_markup_confirmation_events enable row level security;
alter table private.content_ops_button_markup_confirmation_events force row level security;
revoke all on private.content_ops_button_markup_confirmations,
    private.content_ops_button_markup_confirmation_events from public,anon,authenticated,service_role;

create function private.guard_content_ops_button_markup_confirmation()
returns trigger language plpgsql security invoker set search_path='' as $$
begin
    if tg_op='UPDATE' and old.active and not new.active
        and (to_jsonb(old)-'active')=(to_jsonb(new)-'active') then
        -- Atomic retirement of already registered authority. Owners acquire the
        -- card lock first, then confirmation, event, approval, as the registrar.
        update private.content_ops_button_markup_approvals set active=false
            where approval_id=old.approval_id and card_id=old.card_id and active;
        return new;
    end if;
    raise exception 'button_markup_confirmation_immutable' using errcode='23514';
end $$;
create trigger content_ops_button_markup_confirmation_immutable
    before update or delete on private.content_ops_button_markup_confirmations
    for each row execute function private.guard_content_ops_button_markup_confirmation();
create trigger content_ops_button_markup_confirmation_event_immutable
    before update or delete on private.content_ops_button_markup_confirmation_events
    for each row execute function private.guard_content_ops_button_control_evidence();
revoke all on function private.guard_content_ops_button_markup_confirmation()
    from public,anon,authenticated,service_role;
commit;
