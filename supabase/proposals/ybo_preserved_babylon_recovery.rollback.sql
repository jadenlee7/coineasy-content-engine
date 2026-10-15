-- LOCAL_ROLLBACK_PROOF: exact existing Babylon job, never a general retry.
-- Owner-only SHA attestations do not establish remote SQLite facts by themselves.
-- Separate approved OFF operator verifies them before one insert and anchoring.
-- No existing function/table/ACL, original anchor, or job is changed by install.
begin;

create table private.ybo_preserved_babylon_compatibility_receipts (
    receipt_id uuid primary key,
    workspace_id uuid not null check (workspace_id='5cc143cc-b253-4841-8a6c-ab3ac98623a6'),
    trial_id uuid not null unique check (trial_id='d346ceea-4ea9-479f-a5cf-05097b50ce48'),
    job_id uuid not null unique references public.jobs(id) on delete restrict
      check (job_id='e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af'),
    request_id uuid not null check (request_id='52f95e4e-1f66-549a-baa4-32b0fdf82b30'),
    source_item_id uuid not null references public.source_items(id) on delete restrict
      check (source_item_id='35ae7c96-993a-4453-9946-3263f4f2842a'),
    old_release_sha text not null check (old_release_sha='b77a54e12dcdadefd001e4dd8cca255dead96d3c'),
    new_release_sha text not null check (new_release_sha ~ '^[a-f0-9]{40}$' and new_release_sha<>old_release_sha),
    old_spend_policy_sha256 text not null check (old_spend_policy_sha256='000a4243bf92c082db1120b6ccbd6aa8e2ade95ed355aaef124f79a31c6aa0a4'),
    new_spend_policy_sha256 text not null check (new_spend_policy_sha256='2f71160c21ebb2b40d000ccf6cf6d36553c0039766416d52e89d3ab0af653982'),
    old_copy_input_sha256 text not null check (old_copy_input_sha256 ~ '^[a-f0-9]{64}$'),
    new_copy_input_sha256 text not null check (new_copy_input_sha256 ~ '^[a-f0-9]{64}$'),
    checkpoint_sha256 text not null check (checkpoint_sha256='1e27adee8d0b14a8af4af546b19e0bbb38e96b2e93981d0f989bd710fd2bebcd'),
    api_payload_sha256 text not null check (api_payload_sha256='fb3999197f2c07def1aab966a2b7c6a5c48ff4d12c446667632c74e0b188c177'),
    input_sha256 text not null check (input_sha256 ~ '^[a-f0-9]{64}$'),
    style_pack_hash text not null check (style_pack_hash ~ '^[a-f0-9]{32}$'),
    db_proof_sha256 text not null check (db_proof_sha256 ~ '^[a-f0-9]{64}$'),
    local_proof_sha256 text not null check (local_proof_sha256 ~ '^[a-f0-9]{64}$'),
    provider_account_sha256 text not null check (provider_account_sha256 ~ '^[a-f0-9]{64}$'),
    observed_at timestamptz not null check (isfinite(observed_at)),
    client_id text not null default 'babylon' check (client_id='babylon')
);

create table private.ybo_recovery_readiness_anchor (
    workspace_id uuid not null check (workspace_id='5cc143cc-b253-4841-8a6c-ab3ac98623a6'),
    trial_id uuid primary key check (trial_id='d346ceea-4ea9-479f-a5cf-05097b50ce48'),
    receipt_id uuid not null unique references private.ybo_preserved_babylon_compatibility_receipts(receipt_id) on delete restrict,
    plan_sha256 text not null check (plan_sha256 ~ '^[a-f0-9]{64}$'),
    release_sha text not null check (release_sha ~ '^[a-f0-9]{40}$'),
    not_before timestamptz not null,
    deadline timestamptz not null,
    kst_date date not null,
    check (isfinite(not_before) and isfinite(deadline)),
    check (date_trunc('second',not_before)=not_before and date_trunc('second',deadline)=deadline),
    check (deadline=not_before+interval '2 hours'),
    check (timezone('Asia/Seoul',not_before)::date=kst_date
      and timezone('Asia/Seoul',deadline)::date=kst_date)
);

create table private.ybo_preserved_babylon_handoffs (
    receipt_id uuid primary key references private.ybo_preserved_babylon_compatibility_receipts(receipt_id) on delete restrict,
    trial_id uuid not null unique references private.ybo_recovery_readiness_anchor(trial_id) on delete restrict,
    job_id uuid not null unique references public.jobs(id) on delete restrict,
    old_worker_id text not null check (old_worker_id='ybo-trial:1a2bef47-4827-4bc3-9b4d-5baf2714015d'),
    new_worker_id text not null check (new_worker_id='ybo-trial:d346ceea-4ea9-479f-a5cf-05097b50ce48'),
    before_job_sha256 text not null check (before_job_sha256 ~ '^[a-f0-9]{64}$'),
    after_job_sha256 text not null check (after_job_sha256 ~ '^[a-f0-9]{64}$'),
    handed_off_at timestamptz not null,
    lease_expires_at timestamptz not null,
    check (lease_expires_at>handed_off_at and lease_expires_at<=handed_off_at+interval '900 seconds')
);

revoke all on table private.ybo_preserved_babylon_compatibility_receipts,
  private.ybo_recovery_readiness_anchor,private.ybo_preserved_babylon_handoffs
  from public,anon,authenticated,service_role;
alter table private.ybo_preserved_babylon_compatibility_receipts enable row level security;
alter table private.ybo_recovery_readiness_anchor enable row level security;
alter table private.ybo_preserved_babylon_handoffs enable row level security;

create function private.guard_ybo_preserved_babylon_immutable()
returns trigger language plpgsql volatile set search_path='' as $$
begin
    raise exception 'ybo_preserved_recovery_immutable' using errcode='55000';
end $$;
revoke all on function private.guard_ybo_preserved_babylon_immutable() from public,anon,authenticated,service_role;
create trigger preserved_babylon_receipt_immutable before update or delete or truncate
  on private.ybo_preserved_babylon_compatibility_receipts for each statement execute function private.guard_ybo_preserved_babylon_immutable();
create trigger recovery_anchor_immutable before update or delete or truncate
  on private.ybo_recovery_readiness_anchor for each statement execute function private.guard_ybo_preserved_babylon_immutable();
create trigger preserved_babylon_handoff_immutable before update or delete or truncate
  on private.ybo_preserved_babylon_handoffs for each statement execute function private.guard_ybo_preserved_babylon_immutable();

create function private.guard_ybo_recovery_anchor_insert()
returns trigger language plpgsql volatile set search_path='' as $$
declare t timestamptz:=clock_timestamp();
begin
    if new.not_before>t or new.not_before<t-interval '60 seconds' then
      raise exception 'ybo_recovery_anchor_start_not_fresh' using errcode='22023'; end if;
    if not exists(select 1 from private.ybo_preserved_babylon_compatibility_receipts r
      where r.receipt_id=new.receipt_id and r.workspace_id=new.workspace_id and r.trial_id=new.trial_id
        and r.new_release_sha=new.release_sha and r.observed_at<=new.not_before
        and r.observed_at>t-interval '15 minutes') then
      raise exception 'ybo_recovery_anchor_receipt_mismatch' using errcode='22023'; end if;
    return new;
end $$;
revoke all on function private.guard_ybo_recovery_anchor_insert() from public,anon,authenticated,service_role;
create trigger recovery_anchor_insert before insert on private.ybo_recovery_readiness_anchor
  for each row execute function private.guard_ybo_recovery_anchor_insert();

-- Shared no-write predicate. Claim locks the normal slot/feed/source/job/pack
-- plus immutable grant before calling it. Public readback never takes a lease.
create function private.exact_preserved_babylon_context(target_receipt_id uuid)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare r private.ybo_preserved_babylon_compatibility_receipts%rowtype;
  a private.ybo_recovery_readiness_anchor%rowtype;
  j public.jobs%rowtype; s public.source_items%rowtype; f public.source_feeds%rowtype;
  p private.official_x_style_reference_packs%rowtype; t timestamptz;
begin
  select * into r from private.ybo_preserved_babylon_compatibility_receipts where receipt_id=target_receipt_id;
  if not found then return null; end if;
  select * into a from private.ybo_recovery_readiness_anchor
    where trial_id=r.trial_id and workspace_id=r.workspace_id and receipt_id=r.receipt_id and release_sha=r.new_release_sha;
  if not found then return null; end if;
  if exists(select 1 from private.ybo_preserved_babylon_handoffs where receipt_id=r.receipt_id or job_id=r.job_id) then return null; end if;
  select * into j from public.jobs where id=r.job_id and workspace_id=r.workspace_id and client_id='babylon';
  if not found then return null; end if;
  select * into s from public.source_items where id=r.source_item_id and workspace_id=r.workspace_id and client_id='babylon';
  if not found then return null; end if;
  select * into f from public.source_feeds where id=s.source_feed_id and workspace_id=r.workspace_id and client_id='babylon';
  if not found then return null; end if;
  select * into p from private.official_x_style_reference_packs where workspace_id=r.workspace_id and client_id='babylon'
    and request_id=r.request_id and primary_source_item_id=s.id and reference_pack_hash=r.style_pack_hash;
  if not found then return null; end if;
  t:=clock_timestamp();
  if t<a.not_before or t>=a.deadline-interval '60 seconds' or timezone('Asia/Seoul',t)::date<>a.kst_date
    or r.observed_at>a.not_before or r.observed_at<a.not_before-interval '15 minutes'
    or j.job_kind is distinct from 'generate' or j.status is distinct from 'running'
    or j.attempts is distinct from 1 or j.max_attempts is distinct from 3
    or j.content_item_id is not null or j.output is distinct from '{"execution_plane":"studio_sync"}'::jsonb
    or j.locked_by is distinct from 'ybo-trial:1a2bef47-4827-4bc3-9b4d-5baf2714015d'
    or j.locked_at is null or j.lease_expires_at is null or j.lease_expires_at>t
    or j.started_at is null or j.finished_at is not null
    or j.created_at>=a.not_before or j.created_at>t or j.available_at>t
    or j.last_error_code is not null or j.last_error_message is not null
    or j.idempotency_key is distinct from 'official-x-review:v1:'||a.kst_date::text||':babylon'
    or encode(extensions.digest(convert_to(j.input::text,'UTF8'),'sha256'),'hex') is distinct from r.input_sha256
    then return null; end if;
  if not exists(select 1 from private.official_x_daily_slots where workspace_id=r.workspace_id and client_id='babylon'
      and kst_date=a.kst_date and job_id=j.id)
    or not exists(select 1 from private.official_x_source_state where workspace_id=r.workspace_id and client_id='babylon'
      and source_item_id=s.id and queued_job_id=j.id)
    or not exists(select 1 from public.workspace_clients where workspace_id=r.workspace_id and client_id='babylon' and active is true)
    then return null; end if;
  if j.input is distinct from jsonb_build_object('workflow','official_x_review_draft_v1','kst_date',a.kst_date,
      'source_item_ids',jsonb_build_array(s.id::text),'content_kind','daily_news','request_id',r.request_id,
      'source_content',s.body,'source_url',s.canonical_url,'source_image_url',j.input->>'source_image_url','manual_only',false)
    or s.source_type is distinct from 'tweet' or s.author_handle is distinct from '@babylonlabs_io'
    or s.canonical_url is null or s.canonical_url !~ '^https://x\.com/babylonlabs_io/status/[0-9]{1,19}$'
    or s.external_id is distinct from '2110380535927427174'
    or s.external_id is distinct from split_part(s.canonical_url,'/',6)
    or s.body is null or char_length(s.body) not between 10 and 20000
    or s.published_at is null or s.published_at>t or s.published_at<=t-interval '24 hours'
    or f.provider is distinct from 'x' or f.handle is distinct from '@babylonlabs_io'
    or f.active is distinct from true or f.poll_interval_minutes is distinct from 15
    or f.last_polled_at is null or f.last_polled_at>t or f.last_polled_at<=t-interval '30 minutes'
    or jsonb_typeof(s.media) is distinct from 'array' then return null; end if;
  -- This one preserved source has media. No logo-only, quote, or alternate
  -- client exception is introduced by this recovery path.
  if j.input->>'source_image_url' is null or char_length(j.input->>'source_image_url')>2048
    or j.input->>'source_image_url' !~ '^https://pbs\.twimg\.com/[A-Za-z0-9_./?=&%:+-]+$'
    or not exists(select 1 from jsonb_array_elements(s.media) m(value)
      where m.value->>'type' in ('photo','video','animated_gif') and m.value->>'url'=j.input->>'source_image_url')
    or s.id is distinct from (select n.id from public.source_items n where n.workspace_id=r.workspace_id
      and n.client_id='babylon' and n.source_feed_id=f.id and n.source_type='tweet'
      order by n.published_at desc nulls last,n.id desc limit 1) then return null; end if;
  if exists(select 1 from public.content_items where id=r.request_id)
    or exists(select 1 from public.content_versions where content_item_id=r.request_id or generation_meta->>'request_id'=r.request_id::text)
    or exists(select 1 from public.content_source_links where source_item_id=s.id)
    or exists(select 1 from private.grok_qa_dispatch_outbox where source_item_id=s.id or content_item_id=r.request_id)
    or exists(select 1 from private.content_ops_review_outbox where generate_job_id=j.id or source_item_id=s.id or content_item_id=r.request_id)
    or exists(select 1 from public.approvals where content_item_id=r.request_id)
    or exists(select 1 from public.publications where content_item_id=r.request_id)
    or (select count(*) from public.jobs x where x.job_kind='generate'
      and (x.input->>'request_id'=r.request_id::text or (x.workspace_id=r.workspace_id and x.client_id='babylon'
      and x.input->'source_item_ids' @> jsonb_build_array(s.id::text))))<>1 then return null; end if;
  return jsonb_build_object('job_id',j.id,'workspace_id',r.workspace_id,'client_id','babylon',
    'request_id',r.request_id,'source_item_id',s.id,'source_content',s.body,'source_url',s.canonical_url,
    'source_image_url',j.input->>'source_image_url','source_published_at',s.published_at,
    'source_sha256',encode(extensions.digest(convert_to(s.body,'UTF8'),'sha256'),'hex'),
    'kst_date',a.kst_date,'input_sha256',r.input_sha256,'style_pack_hash',r.style_pack_hash,
    'receipt_id',r.receipt_id,'input',j.input,'new_release_sha',r.new_release_sha,
    'style_reference_pack',jsonb_build_object('request_id',r.request_id,'primary_source_item_id',s.id,
      'reference_pack_hash',p.reference_pack_hash,'references',p.style_references,'reused',true));
end $$;
revoke all on function private.exact_preserved_babylon_context(uuid) from public,anon,authenticated,service_role;

create function public.get_exact_preserved_babylon_context(target_workspace_id uuid,target_job_id uuid,target_receipt_id uuid)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
begin
  if (select auth.role()) is distinct from 'service_role' then
    raise exception 'preserved_babylon_service_role_required' using errcode='42501'; end if;
  if target_workspace_id is distinct from '5cc143cc-b253-4841-8a6c-ab3ac98623a6'::uuid
    or target_job_id is distinct from 'e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af'::uuid
    or target_receipt_id is null then return null; end if;
  return private.exact_preserved_babylon_context(target_receipt_id);
end $$;
revoke all on function public.get_exact_preserved_babylon_context(uuid,uuid,uuid) from public,anon,authenticated,service_role;
grant execute on function public.get_exact_preserved_babylon_context(uuid,uuid,uuid) to service_role;

create function public.claim_exact_preserved_babylon_job(
  target_workspace_id uuid,target_job_id uuid,target_request_id uuid,target_source_item_id uuid,
  target_input_sha256 text,target_style_pack_hash text,target_receipt_id uuid,target_worker_id text,
  target_lease_seconds integer default 900
) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare r private.ybo_preserved_babylon_compatibility_receipts%rowtype;
  a private.ybo_recovery_readiness_anchor%rowtype; j public.jobs%rowtype;
  context jsonb; t timestamptz; before_sha text; after_sha text; before_job jsonb;
begin
  if (select auth.role()) is distinct from 'service_role' then
    raise exception 'preserved_babylon_service_role_required' using errcode='42501'; end if;
  if target_workspace_id is distinct from '5cc143cc-b253-4841-8a6c-ab3ac98623a6'::uuid
    or target_job_id is distinct from 'e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af'::uuid
    or target_request_id is distinct from '52f95e4e-1f66-549a-baa4-32b0fdf82b30'::uuid
    or target_source_item_id is distinct from '35ae7c96-993a-4453-9946-3263f4f2842a'::uuid
    or target_worker_id is distinct from 'ybo-trial:d346ceea-4ea9-479f-a5cf-05097b50ce48'
    or target_receipt_id is null or target_input_sha256 is null or target_input_sha256 !~ '^[a-f0-9]{64}$'
    or target_style_pack_hash is null or target_style_pack_hash !~ '^[a-f0-9]{32}$'
    or target_lease_seconds is null or target_lease_seconds not between 60 and 900 then return null; end if;
  select * into r from private.ybo_preserved_babylon_compatibility_receipts
    where receipt_id=target_receipt_id and input_sha256=target_input_sha256 and style_pack_hash=target_style_pack_hash for update;
  if not found then return null; end if;
  select * into a from private.ybo_recovery_readiness_anchor where trial_id=r.trial_id and receipt_id=r.receipt_id for share;
  if not found then return null; end if;
  perform pg_advisory_xact_lock(hashtextextended(target_workspace_id::text||':'||a.kst_date::text,0));
  perform 1 from private.official_x_daily_slots where workspace_id=target_workspace_id and client_id='babylon'
    and kst_date=a.kst_date and job_id=target_job_id for update;
  if not found then return null; end if;
  perform 1 from public.source_feeds where id=(select source_feed_id from public.source_items where id=target_source_item_id) for update;
  if not found then return null; end if;
  perform 1 from public.source_items where id=target_source_item_id for update;
  select * into j from public.jobs where id=target_job_id for update;
  perform 1 from private.official_x_source_state where workspace_id=target_workspace_id and client_id='babylon'
    and source_item_id=target_source_item_id and queued_job_id=target_job_id for share;
  perform 1 from private.official_x_style_reference_packs where workspace_id=target_workspace_id and client_id='babylon'
    and request_id=target_request_id and primary_source_item_id=target_source_item_id for share;
  perform 1 from public.workspace_clients where workspace_id=target_workspace_id and client_id='babylon' for share;
  context:=private.exact_preserved_babylon_context(target_receipt_id);
  if context is null then return null; end if;
  -- Resample after all predicate scans. A stale feed/source/window cannot gain
  -- authority merely because it was fresh before locks or validation work.
  t:=clock_timestamp();
  if t<a.not_before or t>=a.deadline-interval '60 seconds' or timezone('Asia/Seoul',t)::date<>a.kst_date
    or (context->>'source_published_at')::timestamptz>t
    or (context->>'source_published_at')::timestamptz<=t-interval '24 hours'
    or exists(select 1 from public.source_feeds f join public.source_items s on s.source_feed_id=f.id
      where s.id=target_source_item_id and (f.last_polled_at>t or f.last_polled_at<=t-interval '30 minutes')) then return null; end if;
  before_job:=to_jsonb(j);
  before_sha:=encode(extensions.digest(convert_to(before_job::text,'UTF8'),'sha256'),'hex');
  update public.jobs set locked_by=target_worker_id,locked_at=t,
    lease_expires_at=least(a.deadline,t+make_interval(secs=>target_lease_seconds))
    where id=target_job_id and status='running' and attempts=1 and max_attempts=3
      and locked_by='ybo-trial:1a2bef47-4827-4bc3-9b4d-5baf2714015d'
      and lease_expires_at=j.lease_expires_at and lease_expires_at<=t
    returning * into j;
  if not found then return null; end if;
  if (to_jsonb(j)-array['locked_by','locked_at','lease_expires_at','updated_at'])
    is distinct from (before_job-array['locked_by','locked_at','lease_expires_at','updated_at']) then
    raise exception 'preserved_babylon_nonlease_mutation' using errcode='55000'; end if;
  after_sha:=encode(extensions.digest(convert_to(to_jsonb(j)::text,'UTF8'),'sha256'),'hex');
  insert into private.ybo_preserved_babylon_handoffs values(r.receipt_id,r.trial_id,j.id,
    'ybo-trial:1a2bef47-4827-4bc3-9b4d-5baf2714015d',target_worker_id,before_sha,after_sha,t,j.lease_expires_at);
  return jsonb_build_object('job_id',j.id,'workspace_id',j.workspace_id,'client_id',j.client_id,
    'status',j.status,'attempts',j.attempts,'max_attempts',j.max_attempts,'origintrail_batch_eligible',false,
    'batch_handoff_recovery_only',false,'locked_by',j.locked_by,'lease_expires_at',j.lease_expires_at,
    'input',j.input,'receipt_id',r.receipt_id);
end $$;
revoke all on function public.claim_exact_preserved_babylon_job(uuid,uuid,uuid,uuid,text,text,uuid,text,integer)
  from public,anon,authenticated,service_role;
grant execute on function public.claim_exact_preserved_babylon_job(uuid,uuid,uuid,uuid,text,text,uuid,text,integer) to service_role;

rollback; -- LOCAL_ROLLBACK_PROOF; never change to COMMIT here.
