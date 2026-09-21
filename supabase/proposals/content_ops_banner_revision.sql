-- LOCAL PROPOSAL ONLY. Apply after button durable-attempt proposal.
-- No runtime grants, publication, scheduling or automatic enrollment.
-- Authenticated feedback registration matches an actor-bound delivered prompt.
-- Briefs must be pinned by a trusted preparation owner, never reply text.
begin;
create table private.content_ops_banner_briefs (
    content_version_id uuid primary key references public.content_versions(id),
    version_fingerprint text not null check(version_fingerprint ~ '^[a-f0-9]{64}$'),
    headline text not null check(length(headline) between 1 and 100),
    subtitle text not null check(length(subtitle) between 1 and 180),
    logo_sha256 text not null check(logo_sha256 ~ '^[a-f0-9]{64}$')
);
create table private.content_ops_banner_requests (
    job_id uuid primary key,
    card_id uuid not null references private.content_ops_button_cards(id),
    review_id uuid not null references private.content_ops_button_reviews(id),
    actor_id uuid not null references auth.users(id),
    human_binding text not null check(human_binding ~ '^[a-f0-9]{64}$'),
    action_key text not null check(action_key ~ '^[a-f0-9]{64}$'),
    request_text text not null check(octet_length(request_text)<=32768),
    request_sha256 text not null check(request_sha256 ~ '^[a-f0-9]{64}$'),
    result_version_id uuid not null unique default gen_random_uuid(),
    result_asset_id uuid not null unique default gen_random_uuid(),
    registered_at timestamptz not null default clock_timestamp(),
    unique(review_id,action_key),
    foreign key(review_id,action_key)
        references private.content_ops_button_actions(review_id,idempotency_key),
    check(request_sha256=encode(sha256(convert_to(request_text,'UTF8')),'hex')),
    check(jsonb_typeof(request_text::jsonb)='object'),
    check((request_text::jsonb->>'job_id')::uuid=job_id)
);
create table private.content_ops_banner_results (
    job_id uuid primary key references private.content_ops_banner_requests(job_id),
    content_version_id uuid not null unique references public.content_versions(id),
    asset_id uuid not null unique references public.assets(id),
    banner_sha256 text not null check(banner_sha256 ~ '^[a-f0-9]{64}$'),
    byte_size integer not null check(byte_size between 45 and 10000000),
    committed_at timestamptz not null default clock_timestamp()
);
create table private.content_ops_banner_feedback (
    prompt_id uuid primary key references private.content_ops_button_edit_prompts(id),
    operation_key text not null unique check(operation_key ~ '^[a-f0-9]{64}$'),
    reply_sha256 text not null check(reply_sha256 ~ '^[a-f0-9]{64}$'),
    job_id uuid not null unique references private.content_ops_banner_requests(job_id)
);
alter table private.content_ops_banner_briefs enable row level security;
alter table private.content_ops_banner_briefs force row level security;
alter table private.content_ops_banner_feedback enable row level security;
alter table private.content_ops_banner_feedback force row level security;
alter table private.content_ops_banner_requests enable row level security;
alter table private.content_ops_banner_requests force row level security;
alter table private.content_ops_banner_results enable row level security;
alter table private.content_ops_banner_results force row level security;
revoke all on private.content_ops_banner_requests,private.content_ops_banner_results,
    private.content_ops_banner_briefs,private.content_ops_banner_feedback
    from public,anon,authenticated,service_role;
-- Reuse the always-reject branch of the immutable durable-record guard.
create trigger content_ops_banner_request_immutable before update or delete
    on private.content_ops_banner_requests for each row
    execute function private.guard_content_ops_button_durable_record();
create trigger content_ops_banner_result_immutable before update or delete
    on private.content_ops_banner_results for each row
    execute function private.guard_content_ops_button_durable_record();
create trigger content_ops_banner_brief_immutable before update or delete
    on private.content_ops_banner_briefs for each row
    execute function private.guard_content_ops_button_durable_record();
create trigger content_ops_banner_feedback_immutable before update or delete
    on private.content_ops_banner_feedback for each row
    execute function private.guard_content_ops_button_durable_record();
commit;
