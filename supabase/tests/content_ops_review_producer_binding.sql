-- Synthetic-only production-shape regression; all rows are rolled back.
begin;
do $test$
declare
    workspace uuid := private.test_content_ops_seed();
    item uuid;
    version uuid;
    producer public.jobs%rowtype;
    extra_job uuid;
    other_item uuid := gen_random_uuid();
begin
    select id,current_version_id into item,version from public.content_items
    where workspace_id=workspace and client_id='yellow';
    -- complete_review_draft_job binds IDs in input/output, leaving this FK null.
    update public.jobs set content_item_id=null where workspace_id=workspace;
    select * into producer from public.jobs where workspace_id=workspace and client_id='yellow';
    if private.content_ops_review_candidate(workspace,item,version) is null then
        raise exception 'production-shaped completed producer rejected';
    end if;

    update public.jobs set input=input-'request_id' where id=producer.id;
    if private.content_ops_review_candidate(workspace,item,version) is not null then
        raise exception 'missing request binding accepted';
    end if;
    update public.jobs set input=producer.input,output=output||jsonb_build_object('content_item_id',gen_random_uuid())
    where id=producer.id;
    if private.content_ops_review_candidate(workspace,item,version) is not null then
        raise exception 'wrong output item accepted';
    end if;
    update public.jobs set output=producer.output||jsonb_build_object('content_version_id',gen_random_uuid())
    where id=producer.id;
    if private.content_ops_review_candidate(workspace,item,version) is not null then
        raise exception 'wrong output version accepted';
    end if;
    update public.jobs set output=producer.output where id=producer.id;

    insert into public.content_items(id,workspace_id,client_id,content_kind,title,status)
    values(other_item,workspace,'yellow','daily_news','Synthetic conflict','needs_review');
    update public.jobs set content_item_id=other_item where id=producer.id;
    if private.content_ops_review_candidate(workspace,item,version) is not null then
        raise exception 'conflicting nonnull job FK accepted';
    end if;
    update public.jobs set content_item_id=null where id=producer.id;

    extra_job := gen_random_uuid();
    insert into public.jobs(id,workspace_id,client_id,content_item_id,job_kind,status,input,output,finished_at)
    values(extra_job,workspace,'yellow',null,'generate','failed',producer.input,'{}',statement_timestamp());
    if private.content_ops_review_candidate(workspace,item,version) is not null then
        raise exception 'duplicate failed request producer accepted';
    end if;
    update public.jobs set input=producer.input||jsonb_build_object('request_id',gen_random_uuid()),
    output=producer.output,status='succeeded' where id=extra_job;
    if private.content_ops_review_candidate(workspace,item,version) is not null then
        raise exception 'duplicate output-only producer accepted';
    end if;
    delete from public.jobs where id=extra_job;

    update public.jobs set content_item_id=item where id=producer.id;
    if private.content_ops_review_candidate(workspace,item,version) is null then
        raise exception 'matching explicit FK no longer accepted';
    end if;
    update public.jobs set content_item_id=null where id=producer.id;
    update public.content_items set content_kind='article' where id=item;
    if private.content_ops_review_candidate(workspace,item,version) is not null then
        raise exception 'article scope widened';
    end if;
    update public.content_items set content_kind='daily_news' where id=item;
    update public.content_versions set created_at=statement_timestamp()-interval '2 days' where id=version;
    if private.content_ops_review_candidate(workspace,item,version) is not null then
        raise exception 'stale version accepted';
    end if;
    if exists(select 1 from private.content_ops_review_outbox)
       or exists(select 1 from public.approvals) or exists(select 1 from public.publications) then
        raise exception 'candidate inspection changed delivery or approval state';
    end if;
end;
$test$;
rollback;
