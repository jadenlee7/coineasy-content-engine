-- Read-only, publication-backed monthly KPI snapshot for the CoinEasy KPI bot.
--
-- Content Engine remains authoritative for generated content and exact public
-- publication evidence. Community events remain in the CoinEasy operations
-- tracker and are intentionally reported as an external KPI source.

begin;

create or replace function public.get_monthly_content_kpi(
    target_workspace_id uuid,
    target_month date
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    month_start timestamptz;
    month_end timestamptz;
    month_days integer;
    elapsed_days integer;
    client_rows jsonb;
begin
    if target_workspace_id is null
       or not exists (
           select 1
           from public.workspaces
           where id = target_workspace_id
       ) then
        raise exception 'workspace not found' using errcode = 'P0002';
    end if;
    if target_month is null
       or extract(day from target_month) <> 1
       or target_month < (
           date_trunc('month', statement_timestamp() at time zone 'Asia/Seoul')
           - interval '12 months'
       )::date
       or target_month > (
           date_trunc('month', statement_timestamp() at time zone 'Asia/Seoul')
           + interval '3 months'
       )::date then
        raise exception 'target_month must be a bounded first day of month'
            using errcode = '22023';
    end if;

    month_start := target_month::timestamp at time zone 'Asia/Seoul';
    month_end := (target_month + interval '1 month')::timestamp
        at time zone 'Asia/Seoul';
    month_days := extract(day from (target_month + interval '1 month - 1 day'))::integer;
    elapsed_days := case
        when (statement_timestamp() at time zone 'Asia/Seoul')::date < target_month
            then 0
        when (statement_timestamp() at time zone 'Asia/Seoul')::date
             >= (target_month + interval '1 month')::date
            then month_days
        else extract(
            day from statement_timestamp() at time zone 'Asia/Seoul'
        )::integer
    end;

    with clients as (
        select client_id, display_name
        from public.workspace_clients
        where workspace_id = target_workspace_id
          and active is true
          and client_id in ('yellow', 'origintrail', 'squid', 'babylon')
    ),
    published as (
        select
            item.client_id,
            item.content_kind,
            count(distinct publication.content_item_id)::integer as completed
        from public.publications as publication
        join public.content_items as item
          on item.workspace_id = publication.workspace_id
         and item.client_id = publication.client_id
         and item.id = publication.content_item_id
        where publication.workspace_id = target_workspace_id
          and publication.status = 'published'
          and publication.published_at is not null
          and publication.published_at >= month_start
          and publication.published_at < month_end
          and item.content_kind in ('daily_news', 'article', 'tutorial')
        group by item.client_id, item.content_kind
    ),
    pipeline as (
        select
            item.client_id,
            item.content_kind,
            count(*) filter (
                where item.status = 'needs_review'
            )::integer as needs_review,
            count(*) filter (
                where item.status in ('approved', 'scheduled')
            )::integer as ready,
            count(*) filter (
                where item.status = 'failed'
            )::integer as failed
        from public.content_items as item
        where item.workspace_id = target_workspace_id
          and item.created_at >= month_start
          and item.created_at < month_end
          and item.content_kind in ('daily_news', 'article', 'tutorial')
        group by item.client_id, item.content_kind
    ),
    snapshots as (
        select
            client.client_id,
            client.display_name,
            coalesce(news.completed, 0) as daily_news,
            coalesce(article.completed, 0) as article,
            coalesce(tutorial.completed, 0) as tutorial,
            jsonb_build_object(
                'daily_news', jsonb_build_object(
                    'needs_review', coalesce(news_pipe.needs_review, 0),
                    'ready', coalesce(news_pipe.ready, 0),
                    'failed', coalesce(news_pipe.failed, 0)
                ),
                'article', jsonb_build_object(
                    'needs_review', coalesce(article_pipe.needs_review, 0),
                    'ready', coalesce(article_pipe.ready, 0),
                    'failed', coalesce(article_pipe.failed, 0)
                ),
                'tutorial', jsonb_build_object(
                    'needs_review', coalesce(tutorial_pipe.needs_review, 0),
                    'ready', coalesce(tutorial_pipe.ready, 0),
                    'failed', coalesce(tutorial_pipe.failed, 0)
                )
            ) as pipeline
        from clients as client
        left join published as news
          on news.client_id = client.client_id
         and news.content_kind = 'daily_news'
        left join published as article
          on article.client_id = client.client_id
         and article.content_kind = 'article'
        left join published as tutorial
          on tutorial.client_id = client.client_id
         and tutorial.content_kind = 'tutorial'
        left join pipeline as news_pipe
          on news_pipe.client_id = client.client_id
         and news_pipe.content_kind = 'daily_news'
        left join pipeline as article_pipe
          on article_pipe.client_id = client.client_id
         and article_pipe.content_kind = 'article'
        left join pipeline as tutorial_pipe
          on tutorial_pipe.client_id = client.client_id
         and tutorial_pipe.content_kind = 'tutorial'
    )
    select coalesce(
        jsonb_agg(
            jsonb_build_object(
                'client_id', client_id,
                'display_name', display_name,
                'targets', jsonb_build_object(
                    'daily_news', month_days,
                    'article', 2,
                    'tutorial', 2,
                    'community_event', 2
                ),
                'expected_to_date', jsonb_build_object(
                    'daily_news', elapsed_days,
                    'article', least(
                        2,
                        ceil(2.0 * elapsed_days / month_days)::integer
                    ),
                    'tutorial', least(
                        2,
                        ceil(2.0 * elapsed_days / month_days)::integer
                    ),
                    'community_event', least(
                        2,
                        ceil(2.0 * elapsed_days / month_days)::integer
                    )
                ),
                'published', jsonb_build_object(
                    'daily_news', daily_news,
                    'article', article,
                    'tutorial', tutorial
                ),
                'gaps_to_target', jsonb_build_object(
                    'daily_news', greatest(month_days - daily_news, 0),
                    'article', greatest(2 - article, 0),
                    'tutorial', greatest(2 - tutorial, 0)
                ),
                'pipeline', pipeline,
                'community_event_source', 'notion'
            )
            order by client_id
        ),
        '[]'::jsonb
    )
    into client_rows
    from snapshots;

    return jsonb_build_object(
        'ok', true,
        'schema_version', '1.0',
        'workspace_id', target_workspace_id,
        'month', to_char(target_month, 'YYYY-MM'),
        'timezone', 'Asia/Seoul',
        'generated_at', statement_timestamp(),
        'days_in_month', month_days,
        'elapsed_days', elapsed_days,
        'counting_rule', 'distinct_published_content_item',
        'clients', client_rows
    );
end;
$$;

revoke all on function public.get_monthly_content_kpi(uuid, date)
from public, anon, authenticated;
grant execute on function public.get_monthly_content_kpi(uuid, date)
to service_role;

commit;

-- ROLLBACK
-- drop function if exists public.get_monthly_content_kpi(uuid, date);
