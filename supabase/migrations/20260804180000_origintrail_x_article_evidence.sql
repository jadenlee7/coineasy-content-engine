-- Persist provider-owned X Article provenance without introducing a generic
-- URL fetcher. The full immutable article snapshot remains in source_items.body;
-- this sidecar binds it to the canonical X Article URL and a verified SHA-256.

begin;

create table private.origintrail_x_article_evidence (
    workspace_id uuid not null,
    client_id text not null check (client_id = 'origintrail'),
    source_item_id uuid primary key,
    external_id text not null check (external_id ~ '^[0-9]{1,19}$'),
    article_id text not null check (article_id ~ '^[0-9]{1,19}$'),
    article_url text not null,
    title text not null check (char_length(btrim(title)) between 1 and 500),
    source_content_sha256 text not null check (
        source_content_sha256 ~ '^[a-f0-9]{64}$'
    ),
    retrieval_method text not null check (
        retrieval_method in ('x_api_timeline', 'x_api_post_lookup')
    ),
    first_poll_request_id uuid not null,
    recorded_at timestamptz not null default now(),
    foreign key (workspace_id, client_id, source_item_id)
        references public.source_items(workspace_id, client_id, id)
        on delete restrict,
    check (article_url = 'https://x.com/i/article/' || article_id)
);

create index origintrail_x_article_evidence_workspace_idx
    on private.origintrail_x_article_evidence (
        workspace_id,
        source_content_sha256
    );

alter table private.origintrail_x_article_evidence enable row level security;
alter table private.origintrail_x_article_evidence force row level security;

revoke all on table private.origintrail_x_article_evidence
from public, anon, authenticated, service_role;

create or replace function private.reject_origintrail_x_article_evidence_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'OriginTrail X Article evidence is immutable'
        using errcode = '55000';
end;
$$;

revoke all on function
    private.reject_origintrail_x_article_evidence_mutation()
from public, anon, authenticated, service_role;

create trigger origintrail_x_article_evidence_immutable
before update or delete on private.origintrail_x_article_evidence
for each row execute function
    private.reject_origintrail_x_article_evidence_mutation();

create or replace function public.record_origintrail_nonquote_sources(
    target_workspace_id uuid,
    target_client_id text,
    target_handle text,
    target_poll_request_id uuid,
    target_expected_cursor text,
    target_next_cursor text,
    target_items jsonb,
    target_polled_at timestamptz default now()
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    receipt jsonb;
    source_id uuid;
    paired record;
    evidence jsonb;
    source_content text;
    computed_sha256 text;
    committed_evidence private.origintrail_x_article_evidence%rowtype;
begin
    if target_client_id is distinct from 'origintrail'
       or jsonb_typeof(target_items) is distinct from 'array'
       or exists (
           select 1
           from jsonb_array_elements(target_items) as entry(item)
           where jsonb_typeof(item) is distinct from 'object'
              or item -> 'is_quote' is distinct from 'false'::jsonb
              or item -> 'is_retweet' is distinct from 'false'::jsonb
              or item -> 'is_reply' is distinct from 'false'::jsonb
       ) then
        raise exception 'OriginTrail intake requires explicit standalone signals'
            using errcode = '22023';
    end if;

    receipt := public.record_official_x_sources(
        target_workspace_id,
        target_client_id,
        target_handle,
        target_poll_request_id,
        target_expected_cursor,
        target_next_cursor,
        target_items,
        target_polled_at
    );
    if jsonb_typeof(receipt) is distinct from 'object'
       or jsonb_typeof(receipt -> 'source_item_ids') is distinct from 'array'
       or jsonb_array_length(receipt -> 'source_item_ids')
            <> jsonb_array_length(target_items) then
        raise exception 'OriginTrail intake receipt is invalid'
            using errcode = '23514';
    end if;

    begin
        for source_id in
            select value::uuid
            from jsonb_array_elements_text(
                receipt -> 'source_item_ids'
            ) as committed(value)
        loop
            insert into private.origintrail_standalone_sources (
                workspace_id,
                client_id,
                source_item_id,
                is_quote,
                first_poll_request_id,
                verified_at
            )
            select
                source.workspace_id,
                source.client_id,
                source.id,
                false,
                target_poll_request_id,
                statement_timestamp()
            from public.source_items as source
            where source.id = source_id
              and source.workspace_id = target_workspace_id
              and source.client_id = 'origintrail'
            on conflict (source_item_id) do nothing;
        end loop;
    exception when others then
        raise exception 'OriginTrail intake receipt source identity is invalid'
            using errcode = '23514';
    end;

    for paired in
        select
            committed.value::uuid as source_item_id,
            incoming.item
        from jsonb_array_elements_text(
            receipt -> 'source_item_ids'
        ) with ordinality as committed(value, ordinal)
        join jsonb_array_elements(target_items)
            with ordinality as incoming(item, ordinal)
          using (ordinal)
    loop
        evidence := paired.item -> 'article_evidence';
        if evidence is null then
            continue;
        end if;
        source_content := coalesce(
            paired.item ->> 'source_content',
            paired.item ->> 'text',
            paired.item ->> 'body'
        );
        if jsonb_typeof(evidence) is distinct from 'object'
           or (select count(*) from jsonb_object_keys(evidence)) <> 5
           or coalesce(evidence ->> 'article_id', '') !~ '^[0-9]{1,19}$'
           or evidence ->> 'article_url' is distinct from
                'https://x.com/i/article/' || (evidence ->> 'article_id')
           or char_length(btrim(coalesce(evidence ->> 'title', '')))
                not between 1 and 500
           or coalesce(evidence ->> 'source_content_sha256', '')
                !~ '^[a-f0-9]{64}$'
           or coalesce(evidence ->> 'retrieval_method', '') not in (
                'x_api_timeline',
                'x_api_post_lookup'
           )
           or source_content is null
           or position('[X Article]' in source_content) = 0
           or position(
                'Title: ' || (evidence ->> 'title') in source_content
              ) = 0 then
            raise exception 'OriginTrail X Article evidence is invalid'
                using errcode = '22023';
        end if;
        computed_sha256 := encode(
            extensions.digest(
                pg_catalog.convert_to(btrim(source_content), 'UTF8'),
                'sha256'
            ),
            'hex'
        );
        if evidence ->> 'source_content_sha256'
                is distinct from computed_sha256 then
            raise exception 'OriginTrail X Article evidence hash does not match'
                using errcode = '23514';
        end if;

        insert into private.origintrail_x_article_evidence (
            workspace_id,
            client_id,
            source_item_id,
            external_id,
            article_id,
            article_url,
            title,
            source_content_sha256,
            retrieval_method,
            first_poll_request_id
        )
        select
            source.workspace_id,
            source.client_id,
            source.id,
            source.external_id,
            evidence ->> 'article_id',
            evidence ->> 'article_url',
            btrim(evidence ->> 'title'),
            computed_sha256,
            evidence ->> 'retrieval_method',
            target_poll_request_id
        from public.source_items as source
        where source.id = paired.source_item_id
          and source.workspace_id = target_workspace_id
          and source.client_id = 'origintrail'
          and source.body = btrim(source_content)
        on conflict (source_item_id) do nothing;

        select stored.* into committed_evidence
        from private.origintrail_x_article_evidence as stored
        where stored.source_item_id = paired.source_item_id;
        if not found
           or committed_evidence.workspace_id
                is distinct from target_workspace_id
           or committed_evidence.client_id is distinct from 'origintrail'
           or committed_evidence.external_id
                is distinct from (paired.item ->> 'external_id')
           or committed_evidence.article_id
                is distinct from (evidence ->> 'article_id')
           or committed_evidence.article_url
                is distinct from (evidence ->> 'article_url')
           or committed_evidence.title
                is distinct from btrim(evidence ->> 'title')
           or committed_evidence.source_content_sha256
                is distinct from computed_sha256
           or committed_evidence.retrieval_method
                is distinct from (evidence ->> 'retrieval_method')
           or committed_evidence.first_poll_request_id
                is distinct from target_poll_request_id then
            raise exception 'OriginTrail X Article evidence retry does not match'
                using errcode = '23505';
        end if;
    end loop;

    if exists (
        select 1
        from jsonb_array_elements_text(
            receipt -> 'source_item_ids'
        ) as committed(value)
        where not exists (
            select 1
            from private.origintrail_standalone_sources as standalone
            where standalone.source_item_id = committed.value::uuid
              and standalone.workspace_id = target_workspace_id
              and standalone.client_id = 'origintrail'
              and standalone.is_quote is false
        )
    ) then
        raise exception 'OriginTrail source was not durably verified as standalone'
            using errcode = '23514';
    end if;

    return receipt;
end;
$$;

revoke all on function public.record_origintrail_nonquote_sources(
    uuid, text, text, uuid, text, text, jsonb, timestamptz
) from public, anon, authenticated, service_role;

grant execute on function public.record_origintrail_nonquote_sources(
    uuid, text, text, uuid, text, text, jsonb, timestamptz
) to service_role;

commit;
