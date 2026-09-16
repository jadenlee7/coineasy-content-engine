-- LOCAL only. Bounded private original response; no endpoint/runtime grants.
begin;
create table private.content_ops_button_confirmation_sources (
    delivery_id uuid primary key references private.content_ops_button_confirmation_deliveries(delivery_id),
    request_sha256 text not null check (request_sha256 ~ '^[a-f0-9]{64}$'),
    http_status integer not null check (http_status between 100 and 599),
    raw_response bytea not null check (octet_length(raw_response) between 1 and 32768),
    observed_at timestamptz not null check (isfinite(observed_at)),
    response_sha256 text not null check (response_sha256 ~ '^[a-f0-9]{64}$'),
    relay_seal text not null check (relay_seal ~ '^[a-f0-9]{64}$'),
    recorded_at timestamptz not null default clock_timestamp() check (isfinite(recorded_at)),
    check (observed_at<=recorded_at)
);
alter table private.content_ops_button_confirmation_sources enable row level security;
alter table private.content_ops_button_confirmation_sources force row level security;
revoke all on private.content_ops_button_confirmation_sources from public,anon,authenticated,service_role;
create trigger content_ops_button_confirmation_source_immutable
    before update or delete on private.content_ops_button_confirmation_sources
    for each row execute function private.guard_content_ops_button_control_evidence();
commit;
