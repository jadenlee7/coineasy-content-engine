-- Local proposal only. No importer, approval issuer or runtime grants.
begin;
create table private.content_ops_button_control_evidence (
    card_id uuid primary key references private.content_ops_button_cards(id),
    receipt_sha256 text not null check (receipt_sha256 ~ '^[a-f0-9]{64}$'),
    parent_binding_sha256 text not null check (parent_binding_sha256 ~ '^[a-f0-9]{64}$'),
    bot_id bigint not null check (bot_id>0 and bot_id<4503599627370496),
    chat_id bigint not null check (chat_id<0 and chat_id> -4503599627370496),
    message_id bigint not null check (message_id>0 and message_id<4503599627370496),
    thread_id bigint check (thread_id>0 and thread_id<4503599627370496),
    delivered_at timestamptz not null check (isfinite(delivered_at)),
    message_date bigint not null check (message_date>0 and message_date<4294967296),
    text_sha256 text not null check (text_sha256 ~ '^[a-f0-9]{64}$'),
    entities_json text not null check (octet_length(entities_json) between 2 and 32768
        and jsonb_typeof(entities_json::jsonb)='array'),
    markup_json text not null check (octet_length(markup_json) between 2 and 32768
        and jsonb_typeof(markup_json::jsonb)='object'),
    recorded_at timestamptz not null default clock_timestamp() check (isfinite(recorded_at)),
    check (to_timestamp(message_date)<=delivered_at and delivered_at<=recorded_at),
    unique(card_id,receipt_sha256)
);

create table private.content_ops_button_markup_approvals (
    approval_id uuid primary key check (approval_id<>'00000000-0000-0000-0000-000000000000'),
    attempt_id uuid not null unique check (attempt_id<>'00000000-0000-0000-0000-000000000000'),
    card_id uuid not null unique,
    actor_id uuid not null references auth.users(id),
    human_binding text not null check (human_binding ~ '^[a-f0-9]{64}$'),
    plan_seal text not null check (plan_seal ~ '^[a-f0-9]{64}$'),
    original_receipt_sha256 text not null,
    action text not null check (action='append_cancellation_markup@1'),
    approved_at timestamptz not null check (isfinite(approved_at)),
    expires_at timestamptz not null check (isfinite(expires_at)),
    active boolean not null default false,
    recorded_at timestamptz not null default clock_timestamp() check (isfinite(recorded_at)),
    check (approved_at<=recorded_at and recorded_at<expires_at
        and approved_at<expires_at and expires_at<=approved_at+interval '30 minutes'),
    foreign key(card_id,original_receipt_sha256)
        references private.content_ops_button_control_evidence(card_id,receipt_sha256)
);

alter table private.content_ops_button_control_evidence enable row level security;
alter table private.content_ops_button_control_evidence force row level security;
alter table private.content_ops_button_markup_approvals enable row level security;
alter table private.content_ops_button_markup_approvals force row level security;
revoke all on private.content_ops_button_control_evidence,private.content_ops_button_markup_approvals
    from public,anon,authenticated,service_role;

create function private.guard_content_ops_button_control_evidence()
returns trigger language plpgsql security invoker set search_path='' as $$
begin
    raise exception 'button_control_evidence_immutable' using errcode='23514';
end $$;
create trigger content_ops_button_control_evidence_immutable
    before update or delete on private.content_ops_button_control_evidence
    for each row execute function private.guard_content_ops_button_control_evidence();

create function private.guard_content_ops_button_markup_approval()
returns trigger language plpgsql security invoker set search_path='' as $$
begin
    if tg_op='UPDATE' and old.active and not new.active
        and (to_jsonb(old)-'active')=(to_jsonb(new)-'active') then return new;end if;
    raise exception 'button_markup_approval_immutable' using errcode='23514';
end $$;
create trigger content_ops_button_markup_approval_immutable
    before update or delete on private.content_ops_button_markup_approvals
    for each row execute function private.guard_content_ops_button_markup_approval();
revoke all on function private.guard_content_ops_button_control_evidence(),
    private.guard_content_ops_button_markup_approval() from public,anon,authenticated,service_role;
commit;
