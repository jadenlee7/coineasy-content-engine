-- Admit one explicitly reviewed OriginTrail media source to the private Batch
-- canary. The registry is intentionally closed: media is never treated as
-- factual evidence, every supporting reference is pinned in a canonical JSON
-- snapshot, and all public reads remain lease fenced and service-role only.

begin;

-- RFC 8259 JSON with recursively sorted object keys, compact separators and
-- unescaped UTF-8. This is the same representation produced by
-- json.dumps(..., sort_keys=True, separators=(',', ':'), ensure_ascii=False).
create or replace function agent_runtime.canonical_json_text(target jsonb)
returns text
language plpgsql
immutable
strict
set search_path = ''
as $$
declare
    rendered text;
begin
    case jsonb_typeof(target)
        when 'object' then
            select '{' || coalesce(string_agg(
                to_jsonb(entry.key)::text || ':' ||
                    agent_runtime.canonical_json_text(entry.value),
                ',' order by entry.key collate "C"
            ), '') || '}'
            into rendered
            from jsonb_each(target) as entry(key, value);
        when 'array' then
            select '[' || coalesce(string_agg(
                agent_runtime.canonical_json_text(entry.value),
                ',' order by entry.ordinal
            ), '') || ']'
            into rendered
            from jsonb_array_elements(target)
                 with ordinality as entry(value, ordinal);
        else
            rendered := target::text;
    end case;
    return rendered;
end;
$$;

revoke all on function agent_runtime.canonical_json_text(jsonb)
from public, anon, authenticated, service_role;

create table private.origintrail_reviewed_source_evidence (
    source_external_id text primary key check (
        source_external_id ~ '^[0-9]{1,19}$'
    ),
    client_id text not null check (client_id = 'origintrail'),
    source_url text not null unique,
    source_content_sha256 text not null check (
        source_content_sha256 ~ '^[a-f0-9]{64}$'
    ),
    media_key text not null check (media_key ~ '^[0-9]+_[0-9]+$'),
    media_type text not null check (
        media_type in ('photo', 'video', 'animated_gif')
    ),
    raw_media_url text not null unique,
    preview_media_url text not null unique,
    media_url_sha256 text not null check (
        media_url_sha256 ~ '^[a-f0-9]{64}$'
    ),
    media_width integer not null check (media_width between 1 and 20000),
    media_height integer not null check (media_height between 1 and 20000),
    evidence_canonical_json text not null check (
        octet_length(evidence_canonical_json) between 1 and 65536
    ),
    evidence_payload jsonb not null check (
        jsonb_typeof(evidence_payload) = 'object'
        and octet_length(evidence_payload::text) between 1 and 65536
    ),
    evidence_sha256 text not null check (
        evidence_sha256 ~ '^[a-f0-9]{64}$'
    ),
    verified_at timestamptz not null,
    created_at timestamptz not null default now(),
    check (
        source_url = 'https://x.com/origin_trail/status/' || source_external_id
    ),
    check (
        raw_media_url ~ '^https://pbs\.twimg\.com/[A-Za-z0-9_./%:+-]+$'
        and preview_media_url = raw_media_url || '?name=orig'
    ),
    check (
        media_url_sha256 = encode(
            extensions.digest(
                pg_catalog.convert_to(preview_media_url, 'UTF8'),
                'sha256'
            ),
            'hex'
        )
    ),
    check (evidence_payload = evidence_canonical_json::jsonb),
    check (
        evidence_canonical_json =
            agent_runtime.canonical_json_text(evidence_payload)
    ),
    check (
        evidence_sha256 = encode(
            extensions.digest(
                pg_catalog.convert_to(evidence_canonical_json, 'UTF8'),
                'sha256'
            ),
            'hex'
        )
    )
);

alter table private.origintrail_reviewed_source_evidence
    enable row level security;
alter table private.origintrail_reviewed_source_evidence
    force row level security;

revoke all on table private.origintrail_reviewed_source_evidence
from public, anon, authenticated, service_role;

create or replace function
    private.reject_origintrail_reviewed_source_evidence_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'OriginTrail reviewed source evidence is append-only'
        using errcode = '55000';
end;
$$;

revoke all on function
    private.reject_origintrail_reviewed_source_evidence_mutation()
from public, anon, authenticated, service_role;

create trigger origintrail_reviewed_source_evidence_immutable
before update or delete on private.origintrail_reviewed_source_evidence
for each row execute function
    private.reject_origintrail_reviewed_source_evidence_mutation();

create trigger origintrail_reviewed_source_evidence_no_truncate
before truncate on private.origintrail_reviewed_source_evidence
for each statement execute function
    private.reject_origintrail_reviewed_source_evidence_mutation();

with reviewed(payload) as (
    values (
        jsonb_build_object(
            'schema_version', '1.0',
            'policy_version', 'origintrail-media-fact-evidence@1',
            'review_status', 'qualified',
            'human_review_required', true,
            'verified_at', '2026-08-08T11:05:11Z',
            'source_url',
                'https://x.com/origin_trail/status/2085782218815775024',
            'source_content_sha256',
                'aa1676bb2f98b8f35ee7de430c161c9a4ba39a8d4a9c728b8abd93dba3655d74',
            'media', jsonb_build_object(
                'type', 'video',
                'media_key', '13_2085781578374860800',
                'recorded_url',
                    'https://pbs.twimg.com/amplify_video_thumb/2085781578374860800/img/vH2LVZnApTMbJhq2.jpg',
                'preview_url',
                    'https://pbs.twimg.com/amplify_video_thumb/2085781578374860800/img/vH2LVZnApTMbJhq2.jpg?name=orig',
                'preview_url_sha256',
                    '2aa9f90988186014fb262877beb9c7566b81a7a006829b959e6fe0ae105b3d90',
                'width', 1920,
                'height', 1920,
                'factual_evidence', false
            ),
            'review_notes_ko', jsonb_build_array(
                '첨부 영상과 미리보기 이미지는 맥락 확인용이며 사실 근거로 사용하지 않습니다.',
                'OriginTrail DKG Prime Agent 어댑터는 고정된 README 기준 Stage 1 전송·연결 계층입니다. 공유 메모리 훅과 Python DKG 스킬은 후속 단계이므로 현재 기능으로 단정하지 않습니다.',
                '95.5%는 Prime Intellect 발표 수치입니다. 2026-08-08 ARC 커뮤니티 리더보드 API 관찰값은 95.23982017078089였고 self-reported/default 상태이므로 독립 검증 또는 인간 능가의 증거로 표현하지 않습니다.',
                '제시된 scorecard 소스 커밋 링크는 관찰 시점에 404였으므로 독립 근거로 사용하지 않으며 공개 전 사람의 재검토가 필요합니다.'
            ),
            'official_references', jsonb_build_array(
                jsonb_build_object(
                    'kind', 'origintrail_implementation',
                    'label_ko', 'OriginTrail Prime Agent 어댑터 README',
                    'url', 'https://github.com/OriginTrail/dkg/blob/075e87d881260a1aad2d86b53fa250d5d3f67d40/packages/adapter-prime-agent/README.md',
                    'observed_at', '2026-08-08T11:05:11Z',
                    'snapshot_sha256',
                        'd7a3ec333d26feae1a90f51d6770858541b6c9134799d79397d1601ede42a51b',
                    'availability', 'available',
                    'finding_ko', '고정 문서는 현재 범위를 Stage 1 전송·연결로 설명하고 공유 메모리 훅과 Python DKG 스킬을 후속 단계로 둡니다.'
                ),
                jsonb_build_object(
                    'kind', 'prime_intellect_announcement',
                    'label_ko', 'Prime Intellect Prime Agent 발표',
                    'url', 'https://www.primeintellect.ai/blog/prime-agent',
                    'observed_at', '2026-08-08T11:05:11Z',
                    'snapshot_sha256', null,
                    'availability', 'available',
                    'finding_ko', '95.5%와 인간 전문가 95.4% 비교는 Prime Intellect의 1차 발표이며 독립 검증 수치가 아닙니다.'
                ),
                jsonb_build_object(
                    'kind', 'prime_agent_release',
                    'label_ko', 'Prime Agent v0.7.0 불변 코드 커밋',
                    'url', 'https://github.com/PrimeIntellect-ai/prime-agent/commit/be9e2fa0714e7cd1c6bd9bdb1b554d2cc6550387',
                    'observed_at', '2026-08-08T11:05:11Z',
                    'snapshot_sha256', null,
                    'availability', 'available',
                    'finding_ko', 'v0.7.0 릴리스와 코드 커밋 be9e2fa0714e7cd1c6bd9bdb1b554d2cc6550387을 확인했습니다.'
                ),
                jsonb_build_object(
                    'kind', 'arc_community_leaderboard',
                    'label_ko', 'ARC 커뮤니티 리더보드 API 스냅샷',
                    'url', 'https://arcprize.org/api/leaderboards',
                    'observed_at', '2026-08-08T11:05:11Z',
                    'snapshot_sha256',
                        '2f37594d945680d310a35b3959c84f12c17c14c629ee7c68ae70ede8c5306623',
                    'availability', 'available',
                    'finding_ko', 'POST {"game_id":"","ai":true} 관찰값은 95.23982017078089이며 self-reported/default 상태라 독립 검증으로 취급하지 않습니다.'
                ),
                jsonb_build_object(
                    'kind', 'arc_methodology',
                    'label_ko', 'ARC-AGI-3 공식 기술 보고서',
                    'url', 'https://arcprize.org/media/ARC_AGI_3_Technical_Report.pdf',
                    'observed_at', '2026-08-08T11:05:11Z',
                    'snapshot_sha256', null,
                    'availability', 'available',
                    'finding_ko', '평가 설계와 범위를 설명하는 방법론 자료이며 특정 모델의 95.5%를 독립 확인하지 않습니다.'
                ),
                jsonb_build_object(
                    'kind', 'scorecard_source',
                    'label_ko', 'Prime Agent scorecard 소스 커밋',
                    'url', 'https://github.com/PrimeIntellect-ai/arc-agi-3-prime-agent-scorecard/commit/aaee22436235de6f784df7b89302e1258aae9ab9',
                    'observed_at', '2026-08-08T11:05:11Z',
                    'snapshot_sha256', null,
                    'availability', 'unavailable',
                    'finding_ko', '관찰 시점에 404였으므로 인간 능가 또는 재현 가능성의 독립 증거로 사용할 수 없습니다.'
                )
            )
        )
    )
), canonical as (
    select
        payload,
        agent_runtime.canonical_json_text(payload) as canonical_json
    from reviewed
)
insert into private.origintrail_reviewed_source_evidence (
    source_external_id,
    client_id,
    source_url,
    source_content_sha256,
    media_key,
    media_type,
    raw_media_url,
    preview_media_url,
    media_url_sha256,
    media_width,
    media_height,
    evidence_canonical_json,
    evidence_payload,
    evidence_sha256,
    verified_at
)
select
    '2085782218815775024',
    'origintrail',
    'https://x.com/origin_trail/status/2085782218815775024',
    'aa1676bb2f98b8f35ee7de430c161c9a4ba39a8d4a9c728b8abd93dba3655d74',
    '13_2085781578374860800',
    'video',
    'https://pbs.twimg.com/amplify_video_thumb/2085781578374860800/img/vH2LVZnApTMbJhq2.jpg',
    'https://pbs.twimg.com/amplify_video_thumb/2085781578374860800/img/vH2LVZnApTMbJhq2.jpg?name=orig',
    '2aa9f90988186014fb262877beb9c7566b81a7a006829b959e6fe0ae105b3d90',
    1920,
    1920,
    canonical_json,
    payload,
    encode(
        extensions.digest(
            pg_catalog.convert_to(canonical_json, 'UTF8'),
            'sha256'
        ),
        'hex'
    ),
    '2026-08-08T11:05:11Z'::timestamptz
from canonical;

-- Return an evidence envelope only when the public review job, its single
-- stored source, and the curated registry agree byte-for-byte on every source
-- and media identity field. Unknown or changed evidence resolves to NULL.
create or replace function
    agent_runtime.origintrail_reviewed_media_evidence(target_job_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    review_job public.jobs%rowtype;
    pinned_source_item_id uuid;
    evidence jsonb;
begin
    select job.* into review_job
    from public.jobs as job
    where job.id = target_job_id;

    if not found
       or review_job.client_id <> 'origintrail'
       or review_job.job_kind <> 'generate'
       or review_job.input ->> 'workflow'
            is distinct from 'official_x_review_draft_v1'
       or review_job.input ->> 'content_kind' is distinct from 'daily_news'
       or review_job.input -> 'manual_only' is distinct from 'false'::jsonb
       or jsonb_typeof(review_job.input -> 'source_item_ids')
            is distinct from 'array'
       or jsonb_array_length(review_job.input -> 'source_item_ids') <> 1
       or coalesce(btrim(review_job.input ->> 'source_image_url'), '') = ''
       or coalesce(btrim(review_job.input ->> 'source_content'), '') = ''
       or coalesce(btrim(review_job.input ->> 'source_url'), '') = '' then
        return null;
    end if;

    begin
        pinned_source_item_id := (
            review_job.input -> 'source_item_ids' ->> 0
        )::uuid;
    exception when others then
        return null;
    end;

    select jsonb_build_object(
        'payload', registry.evidence_payload,
        'evidence_sha256', registry.evidence_sha256
    )
    into evidence
    from public.source_items as source
    join private.origintrail_standalone_sources as standalone
      on standalone.workspace_id = source.workspace_id
     and standalone.client_id = source.client_id
     and standalone.source_item_id = source.id
     and standalone.is_quote is false
    join private.origintrail_reviewed_source_evidence as registry
      on registry.client_id = source.client_id
     and registry.source_external_id = source.external_id
     and registry.source_url = source.canonical_url
    cross join lateral (
        select source.media -> 0 as item
    ) as media
    where source.id = pinned_source_item_id
      and source.workspace_id = review_job.workspace_id
      and source.client_id = 'origintrail'
      and review_job.input ->> 'source_url' = registry.source_url
      and review_job.input ->> 'source_content' = source.body
      and encode(
            extensions.digest(
                pg_catalog.convert_to(source.body, 'UTF8'),
                'sha256'
            ),
            'hex'
          ) = registry.source_content_sha256
      and jsonb_typeof(source.media) = 'array'
      and jsonb_array_length(source.media) = 1
      and jsonb_typeof(media.item) = 'object'
      and media.item ->> 'media_key' = registry.media_key
      and media.item ->> 'type' = registry.media_type
      and media.item ->> 'url' = registry.raw_media_url
      and review_job.input ->> 'source_image_url' = registry.raw_media_url
      and coalesce(media.item ->> 'width', '') ~ '^[0-9]{1,5}$'
      and coalesce(media.item ->> 'height', '') ~ '^[0-9]{1,5}$'
      and (media.item ->> 'width')::integer = registry.media_width
      and (media.item ->> 'height')::integer = registry.media_height
      and registry.media_url_sha256 = encode(
          extensions.digest(
              pg_catalog.convert_to(registry.preview_media_url, 'UTF8'),
              'sha256'
          ),
          'hex'
      )
      and registry.evidence_payload = registry.evidence_canonical_json::jsonb
      and registry.evidence_sha256 = encode(
          extensions.digest(
              pg_catalog.convert_to(
                  registry.evidence_canonical_json,
                  'UTF8'
              ),
              'sha256'
          ),
          'hex'
      )
      and registry.evidence_payload ->> 'source_url' = registry.source_url
      and (select count(*)
           from jsonb_object_keys(registry.evidence_payload)) = 10
      and registry.evidence_payload ?& array[
          'schema_version',
          'policy_version',
          'review_status',
          'human_review_required',
          'verified_at',
          'source_url',
          'source_content_sha256',
          'media',
          'review_notes_ko',
          'official_references'
      ]::text[]
      and registry.evidence_payload ->> 'schema_version' = '1.0'
      and registry.evidence_payload ->> 'policy_version'
            = 'origintrail-media-fact-evidence@1'
      and registry.evidence_payload ->> 'review_status' = 'qualified'
      and registry.evidence_payload -> 'human_review_required' = 'true'::jsonb
      and registry.evidence_payload ->> 'verified_at'
            = to_char(
                registry.verified_at at time zone 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS"Z"'
              )
      and registry.evidence_payload ->> 'source_content_sha256'
            = registry.source_content_sha256
      and jsonb_typeof(registry.evidence_payload -> 'media') = 'object'
      and (select count(*)
           from jsonb_object_keys(registry.evidence_payload -> 'media')) = 8
      and registry.evidence_payload -> 'media' ?& array[
          'type',
          'media_key',
          'recorded_url',
          'preview_url',
          'preview_url_sha256',
          'width',
          'height',
          'factual_evidence'
      ]::text[]
      and registry.evidence_payload -> 'media' ->> 'media_key'
            = registry.media_key
      and registry.evidence_payload -> 'media' ->> 'type'
            = registry.media_type
      and registry.evidence_payload -> 'media' ->> 'recorded_url'
            = registry.raw_media_url
      and registry.evidence_payload -> 'media' ->> 'preview_url'
            = registry.preview_media_url
      and registry.evidence_payload -> 'media' ->> 'preview_url_sha256'
            = registry.media_url_sha256
      and registry.evidence_payload -> 'media' ->> 'width'
            = registry.media_width::text
      and registry.evidence_payload -> 'media' ->> 'height'
            = registry.media_height::text
      and registry.evidence_payload -> 'media' -> 'factual_evidence'
            = 'false'::jsonb
      and jsonb_typeof(registry.evidence_payload -> 'review_notes_ko')
            = 'array'
      and jsonb_array_length(
            registry.evidence_payload -> 'review_notes_ko'
          ) between 1 and 8
      and not exists (
          select 1
          from jsonb_array_elements(
              registry.evidence_payload -> 'review_notes_ko'
          ) as note(value)
          where jsonb_typeof(note.value) <> 'string'
             or char_length(btrim(note.value #>> '{}')) not between 1 and 1000
      )
      and jsonb_typeof(registry.evidence_payload -> 'official_references')
            = 'array'
      and jsonb_array_length(
            registry.evidence_payload -> 'official_references'
          ) = 6
      and (
          select count(distinct reference.value ->> 'kind')
          from jsonb_array_elements(
              registry.evidence_payload -> 'official_references'
          ) as reference(value)
      ) = 6
      and not exists (
          select 1
          from jsonb_array_elements(
              registry.evidence_payload -> 'official_references'
          ) as reference(value)
          where jsonb_typeof(reference.value) <> 'object'
             or (select count(*)
                 from jsonb_object_keys(reference.value)) <> 7
             or not reference.value ?& array[
                 'kind',
                 'label_ko',
                 'url',
                 'observed_at',
                 'snapshot_sha256',
                 'availability',
                 'finding_ko'
             ]::text[]
             or coalesce(reference.value ->> 'kind', '') not in (
                 'origintrail_implementation',
                 'prime_intellect_announcement',
                 'prime_agent_release',
                 'arc_community_leaderboard',
                 'arc_methodology',
                 'scorecard_source'
             )
             or char_length(btrim(coalesce(
                    reference.value ->> 'label_ko', ''
                ))) not between 1 and 200
             or char_length(btrim(coalesce(
                    reference.value ->> 'url', ''
                ))) not between 1 and 2048
             or reference.value ->> 'observed_at'
                    <> '2026-08-08T11:05:11Z'
             or (
                 reference.value -> 'snapshot_sha256' <> 'null'::jsonb
                 and coalesce(
                     reference.value ->> 'snapshot_sha256', ''
                 ) !~ '^[a-f0-9]{64}$'
             )
             or coalesce(reference.value ->> 'availability', '')
                    not in ('available', 'unavailable')
             or char_length(btrim(coalesce(
                    reference.value ->> 'finding_ko', ''
                ))) not between 1 and 1000
      );

    return evidence;
end;
$$;

revoke all on function
    agent_runtime.origintrail_reviewed_media_evidence(uuid)
from public, anon, authenticated, service_role;

-- Keep the historical symbol because existing admission and recovery RPCs
-- call it. Its meaning is now "safe frozen OriginTrail evidence": either the
-- original text-only standalone set, or the exact single-media registry row.
create or replace function agent_runtime.origintrail_review_is_text_only(
    target_job_id uuid
)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    review_job public.jobs%rowtype;
    source_ids uuid[];
    source_count integer;
    distinct_source_count integer;
    text_only_source_count integer;
begin
    if agent_runtime.origintrail_reviewed_media_evidence(target_job_id)
            is not null then
        return true;
    end if;

    select job.* into review_job
    from public.jobs as job
    where job.id = target_job_id;
    if not found
       or review_job.client_id <> 'origintrail'
       or review_job.job_kind <> 'generate'
       or review_job.input ->> 'workflow'
            is distinct from 'official_x_review_draft_v1'
       or review_job.input ->> 'content_kind'
            is distinct from 'daily_news'
       or review_job.input -> 'manual_only' is distinct from 'false'::jsonb
       or coalesce(btrim(review_job.input ->> 'source_image_url'), '') <> ''
       or jsonb_typeof(review_job.input -> 'source_item_ids')
            is distinct from 'array'
       or jsonb_array_length(review_job.input -> 'source_item_ids')
            not between 1 and 20 then
        return false;
    end if;

    begin
        select
            array_agg(value::uuid order by ordinal),
            count(*),
            count(distinct value)
        into source_ids, source_count, distinct_source_count
        from jsonb_array_elements_text(
                 review_job.input -> 'source_item_ids'
             ) with ordinality as source_id(value, ordinal);
    exception when others then
        return false;
    end;
    if source_ids is null
       or source_count <> distinct_source_count then
        return false;
    end if;

    select count(*) into text_only_source_count
    from public.source_items as source
    where source.id = any(source_ids)
      and source.workspace_id = review_job.workspace_id
      and source.client_id = 'origintrail'
      and jsonb_typeof(source.media) = 'array'
      and jsonb_array_length(source.media) = 0
      and exists (
          select 1
          from private.origintrail_standalone_sources as standalone
          where standalone.workspace_id = source.workspace_id
            and standalone.client_id = source.client_id
            and standalone.source_item_id = source.id
            and standalone.is_quote is false
      );
    return text_only_source_count = source_count;
end;
$$;

revoke all on function agent_runtime.origintrail_review_is_text_only(uuid)
from public, anon, authenticated, service_role;

create or replace function public.get_origintrail_reviewed_source_evidence(
    target_workspace_id uuid,
    target_job_id uuid,
    target_worker_id text
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    review_job public.jobs%rowtype;
begin
    if target_workspace_id is null
       or target_job_id is null
       or char_length(coalesce(target_worker_id, '')) not between 1 and 120 then
        return null;
    end if;

    select job.* into review_job
    from public.jobs as job
    where job.id = target_job_id
      and job.workspace_id = target_workspace_id;

    if not found
       or review_job.status <> 'running'
       or review_job.locked_by is distinct from target_worker_id
       or review_job.lease_expires_at is null
       or review_job.lease_expires_at <= statement_timestamp() then
        return null;
    end if;

    return agent_runtime.origintrail_reviewed_media_evidence(target_job_id);
end;
$$;

revoke all on function public.get_origintrail_reviewed_source_evidence(
    uuid, uuid, text
) from public, anon, authenticated, service_role;

grant execute on function public.get_origintrail_reviewed_source_evidence(
    uuid, uuid, text
) to service_role;

-- The sidecar lives inside the immutable provider input, whose input_sha256 and
-- request_sha256 are already ledger-bound. This trigger prevents a producer
-- from swapping that copy, omitting the registry hash, or adding unreviewed
-- keys. It is INSERT-only so settlement updates and durable replay reads remain
-- untouched.
create or replace function
    agent_runtime.enforce_origintrail_media_fact_evidence()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    review_job public.jobs%rowtype;
    expected_evidence jsonb;
    provider_input jsonb;
begin
    if new.client_id <> 'origintrail'
       or new.agent_id <> 'origintrail_client_agent'
       or new.workflow_kind <> 'official_source_nonurgent_pack'
       or new.stage <> 'generate' then
        return new;
    end if;

    select job.* into review_job
    from public.jobs as job
    where job.id = new.job_id
      and job.workspace_id = new.workspace_id
      and job.client_id = new.client_id;
    if not found then
        raise exception 'OriginTrail Batch job has no exact review source'
            using errcode = '23514';
    end if;

    -- This migration extends the reviewed-media path. Preserve the historical
    -- text-only ledger contract, while refusing to let a text-only job smuggle
    -- a fact-check sidecar into the provider prompt.
    if coalesce(btrim(review_job.input ->> 'source_image_url'), '') = '' then
        begin
            provider_input := (new.input_payload ->> 'input')::jsonb;
        exception when others then
            return new;
        end;
        if jsonb_typeof(provider_input) = 'object'
           and provider_input ? 'fact_check_evidence' then
            raise exception 'OriginTrail text-only input cannot carry fact evidence'
                using errcode = '23514';
        end if;
        return new;
    end if;

    begin
        provider_input := (new.input_payload ->> 'input')::jsonb;
    exception when others then
        raise exception 'OriginTrail provider input is invalid'
            using errcode = '23514';
    end;
    if jsonb_typeof(provider_input) is distinct from 'object'
       or jsonb_typeof(provider_input -> 'source')
            is distinct from 'object'
       or jsonb_typeof(provider_input -> 'style_reference_pack')
            is distinct from 'object'
       or provider_input ->> 'client_id' is distinct from 'origintrail'
       or provider_input ->> 'content_kind'
            is distinct from review_job.input ->> 'content_kind'
       or provider_input ->> 'request_id'
            is distinct from review_job.input ->> 'request_id'
       or provider_input -> 'source' ->> 'content'
            is distinct from review_job.input ->> 'source_content'
       or provider_input -> 'source' ->> 'url'
            is distinct from review_job.input ->> 'source_url' then
        raise exception 'OriginTrail provider input identity is invalid'
            using errcode = '23514';
    end if;

    expected_evidence :=
        agent_runtime.origintrail_reviewed_media_evidence(new.job_id);
    if expected_evidence is null then
        raise exception 'OriginTrail media evidence is not reviewed'
            using errcode = '23514';
    end if;

    if (select count(*) from jsonb_object_keys(provider_input)) <> 6
       or not (provider_input ?& array[
            'client_id',
            'content_kind',
            'request_id',
            'source',
            'style_reference_pack',
            'fact_check_evidence'
          ]::text[])
       or (select count(*) from jsonb_object_keys(
            provider_input -> 'source'
          )) <> 4
       or not ((provider_input -> 'source') ?& array[
            'content', 'content_sha256', 'url', 'image_url'
          ]::text[])
       or jsonb_typeof(provider_input -> 'fact_check_evidence')
            is distinct from 'object'
       or (select count(*) from jsonb_object_keys(
            provider_input -> 'fact_check_evidence'
          )) <> 2
       or not (
           (provider_input -> 'fact_check_evidence') ?& array[
               'payload', 'evidence_sha256'
           ]::text[]
       )
       or provider_input -> 'fact_check_evidence'
            is distinct from expected_evidence
       or provider_input -> 'source' ->> 'content'
            is distinct from review_job.input ->> 'source_content'
       or provider_input -> 'source' ->> 'url'
            is distinct from review_job.input ->> 'source_url'
       or provider_input -> 'source' ->> 'image_url'
            is distinct from review_job.input ->> 'source_image_url'
       or provider_input -> 'source' ->> 'content_sha256'
            is distinct from expected_evidence -> 'payload'
                ->> 'source_content_sha256' then
        raise exception 'OriginTrail media provider evidence is not immutable'
            using errcode = '23514';
    end if;

    return new;
end;
$$;

revoke all on function
    agent_runtime.enforce_origintrail_media_fact_evidence()
from public, anon, authenticated, service_role;

create trigger enforce_origintrail_media_fact_evidence_before_insert
before insert on agent_runtime.batch_jobs
for each row execute function
    agent_runtime.enforce_origintrail_media_fact_evidence();

-- Preserve the existing review-detail contract and add only the immutable
-- evidence envelope used by media-backed work. Text-only rows return JSON null.
create or replace function public.get_agent_batch_review_item(
    target_workspace_id uuid,
    target_job_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    item jsonb;
begin
    if target_workspace_id is null
       or target_job_id is null
       or not exists (
           select 1
           from public.workspaces as workspace
           where workspace.id = target_workspace_id
       ) then
        raise exception 'agent Batch review item request is invalid'
            using errcode = '22023';
    end if;

    select jsonb_build_object(
        'job_id', batch_job.job_id,
        'client_id', batch_job.client_id,
        'agent_id', batch_job.agent_id,
        'workflow_kind', batch_job.workflow_kind,
        'stage', batch_job.stage,
        'status', batch_job.status,
        'model', batch_job.model,
        'model_tier', batch_job.model_tier,
        'title',
            case
                when jsonb_typeof(
                         batch_job.result_payload -> 'headline_ko'
                     ) = 'string'
                 and char_length(
                         btrim(batch_job.result_payload ->> 'headline_ko')
                     )
                     between 1 and 120
                    then btrim(batch_job.result_payload ->> 'headline_ko')
                else 'OriginTrail Batch review draft'
            end,
        'result_code', batch_job.result_code,
        'actual_cost_microusd', batch_job.actual_cost_microusd,
        'finished_at', batch_job.finished_at,
        'source_url', review_job.input ->> 'source_url',
        'source_content',
            (batch_job.input_payload ->> 'input')::jsonb
                -> 'source' ->> 'content',
        'fact_check_evidence',
            case
                when coalesce(
                         btrim(review_job.input ->> 'source_image_url'), ''
                     ) <> ''
                    then (batch_job.input_payload ->> 'input')::jsonb
                        -> 'fact_check_evidence'
                else null
            end,
        'result_payload', batch_job.result_payload,
        'input_sha256', batch_job.input_sha256,
        'actual_input_tokens', batch_job.actual_input_tokens,
        'actual_output_tokens', batch_job.actual_output_tokens
    )
    into item
    from agent_runtime.batch_jobs as batch_job
    join public.jobs as review_job
      on review_job.id = batch_job.job_id
     and review_job.workspace_id = batch_job.workspace_id
     and review_job.client_id = batch_job.client_id
    where batch_job.workspace_id = target_workspace_id
      and batch_job.job_id = target_job_id
      and batch_job.client_id = 'origintrail'
      and batch_job.agent_id = 'origintrail_client_agent'
      and batch_job.workflow_kind = 'official_source_nonurgent_pack'
      and batch_job.stage = 'generate'
      and batch_job.status = 'completed'
      and batch_job.reservation_state = 'settled'
      and batch_job.result_code = 'needs_review'
      and batch_job.input_payload -> 'approval_required' = 'true'::jsonb
      and batch_job.input_payload -> 'input_immutable' = 'true'::jsonb
      and batch_job.input_payload -> 'source_snapshot_complete' = 'true'::jsonb
      and jsonb_typeof(batch_job.input_payload -> 'input') = 'string'
      and jsonb_typeof(
            (batch_job.input_payload ->> 'input')::jsonb
          ) = 'object'
      and jsonb_typeof(
            (batch_job.input_payload ->> 'input')::jsonb -> 'source'
          ) = 'object'
      and jsonb_typeof(
            (batch_job.input_payload ->> 'input')::jsonb
                -> 'source' -> 'content'
          ) = 'string'
      and char_length(
            (batch_job.input_payload ->> 'input')::jsonb
                -> 'source' ->> 'content'
          ) between 1 and 60000
      and (
            (batch_job.input_payload ->> 'input')::jsonb
                -> 'source' ->> 'content'
          ) ~ '[^[:space:]]'
      and (
            (batch_job.input_payload ->> 'input')::jsonb
                -> 'source' ->> 'url'
          ) = review_job.input ->> 'source_url'
      and (
          coalesce(btrim(review_job.input ->> 'source_image_url'), '') = ''
          or (
              (batch_job.input_payload ->> 'input')::jsonb
                  -> 'fact_check_evidence'
              = agent_runtime.origintrail_reviewed_media_evidence(
                    batch_job.job_id
                )
          )
      )
      and batch_job.result_payload ?& array[
          'headline_ko', 'body_ko', 'x_copy_ko', 'telegram_copy_ko'
      ]::text[]
      and (
          select count(*)
          from jsonb_object_keys(batch_job.result_payload)
      ) = 4
      and jsonb_typeof(batch_job.result_payload -> 'headline_ko') = 'string'
      and jsonb_typeof(batch_job.result_payload -> 'body_ko') = 'string'
      and jsonb_typeof(batch_job.result_payload -> 'x_copy_ko') = 'string'
      and jsonb_typeof(batch_job.result_payload -> 'telegram_copy_ko')
            = 'string'
      and char_length(
          batch_job.result_payload ->> 'headline_ko'
      ) between 1 and 120
      and char_length(
          batch_job.result_payload ->> 'body_ko'
      ) between 1 and 1800
      and char_length(
          batch_job.result_payload ->> 'x_copy_ko'
      ) between 1 and 500
      and char_length(
          batch_job.result_payload ->> 'telegram_copy_ko'
      ) between 1 and 1800
      and (batch_job.result_payload ->> 'headline_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'body_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'x_copy_ko') ~ '[^[:space:]]'
      and (batch_job.result_payload ->> 'telegram_copy_ko')
            ~ '[^[:space:]]'
      and review_job.job_kind = 'generate'
      and review_job.status = 'succeeded'
      and review_job.content_item_id is null
      and review_job.input ->> 'workflow' = 'official_x_review_draft_v1'
      and review_job.input ->> 'content_kind' = 'daily_news'
      and review_job.input -> 'manual_only' = 'false'::jsonb
      and review_job.output = jsonb_build_object(
          'workflow', 'agent_batch_review_handoff_v1',
          'handoff', 'openai_batch',
          'batch_job_id', batch_job.job_id,
          'input_sha256', batch_job.input_sha256,
          'review_state', 'pending'
      );

    return item;
end;
$$;

revoke all on function public.get_agent_batch_review_item(
    uuid, uuid
) from public, anon, authenticated, service_role;

grant execute on function public.get_agent_batch_review_item(
    uuid, uuid
) to service_role;

commit;
