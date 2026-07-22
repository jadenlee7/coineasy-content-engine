-- The shared legacy meme engine is server-only. Its Railway services use the
-- Supabase service role, so browser roles must not be able to reach the raw
-- table or its helper view. The historical migration checked into this
-- repository guarantees these objects exist before this migration runs.

begin;

alter table public.daily_memes enable row level security;

alter view public.memes_awaiting_metrics
    set (security_invoker = true, security_barrier = true);

revoke all on table public.daily_memes
    from public, anon, authenticated, service_role;
revoke all on table public.memes_awaiting_metrics
    from public, anon, authenticated, service_role;

grant select, insert, update on table public.daily_memes to service_role;
grant select on table public.memes_awaiting_metrics to service_role;

notify pgrst, 'reload schema';

commit;
