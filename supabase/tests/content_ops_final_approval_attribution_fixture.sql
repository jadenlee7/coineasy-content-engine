-- CI-only replica of the hosted signup-free principal relation, which is not
-- yet in this repository's migration history. Never apply to production.
begin;
create table private.content_ops_review_principals (
    id uuid primary key,
    workspace_id uuid not null references public.workspaces(id),
    bot_binding text not null check (bot_binding ~ '^[a-f0-9]{64}$'),
    human_binding text not null check (human_binding ~ '^[a-f0-9]{64}$'),
    created_at timestamptz not null default statement_timestamp(),
    unique (workspace_id, id),
    unique (workspace_id, bot_binding, human_binding)
);
alter table private.content_ops_review_principals enable row level security;
alter table private.content_ops_review_principals force row level security;
revoke all on private.content_ops_review_principals
    from public, anon, authenticated, service_role;
commit;
