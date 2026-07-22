-- Historical migration imported from the shared coineasy-meme-engine Supabase
-- project. Keep this file byte-for-byte equivalent to the recorded migration
-- statements so this repository can be the single migration owner going forward.

create extension if not exists pgcrypto;

create table if not exists daily_memes (
    id             uuid primary key default gen_random_uuid(),
    job_id         text not null unique,
    weekday        text not null,
    format         text not null,
    tone           text,
    props          jsonb not null,
    social_caption text,
    hashtags       text[],
    drive_id       text,
    status         text not null default 'pending',
    review_note    text,
    tiktok_url     text,
    views_24h      integer,
    likes_24h      integer,
    metrics_at     timestamptz,
    created_at     timestamptz not null default now(),
    approved_at    timestamptz
);

create index if not exists daily_memes_status_idx on daily_memes(status);
create index if not exists daily_memes_created_at_idx on daily_memes(created_at desc);
create index if not exists daily_memes_format_idx on daily_memes(format);

create or replace view memes_awaiting_metrics as
select * from daily_memes
where status = 'approved'
  and metrics_at is null
  and created_at >= now() - interval '24 hours';
