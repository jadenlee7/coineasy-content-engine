-- Synthetic-only discovery regression. Natural completion binds the item in
-- jobs.input/output while leaving jobs.content_item_id NULL; testing only the
-- candidate helper does not prove that reconciliation can discover those jobs.
-- Requires the isolated bootstrap and producer/discovery/exact-copy corrections.
-- Every fixture/outbox row is rolled back. Claims are fixture-only; no send,
-- provider or public action. Synthetic copy is never emitted in assertions.
begin;
do $test$
declare
    workspace uuid;
    item public.content_items%rowtype;
    version uuid;
    producer public.jobs%rowtype;
    conflicting_item uuid;
    candidate_count integer;
    inserted_first integer;
    inserted_again integer;
    telegram_length integer;
    x_length integer;
    full_telegram text;
    full_x text;
    full_title text;
    reference_source text;
    reference_source_id uuid;
    claim_token uuid;
    claimed jsonb;
    condition text;
    role_name text;
begin
    foreach role_name in array array['anon', 'authenticated', 'service_role'] loop
        if has_function_privilege(role_name,
               'private.content_ops_review_candidate(uuid,uuid,uuid)', 'EXECUTE')
           or (has_function_privilege(role_name,
                   'public.content_ops_reconcile_daily(uuid,uuid)', 'EXECUTE')
               is distinct from (role_name = 'service_role')) then
            raise exception 'daily review candidate or reconciliation ACL widened';
        end if;
    end loop;
    perform set_config('request.jwt.claim.role', 'service_role', true);
    workspace := private.test_content_ops_seed();
    update public.jobs set content_item_id = null where workspace_id = workspace;
    candidate_count := 0;
    for item in
        select candidate.* from public.content_items as candidate
        where candidate.workspace_id = workspace order by candidate.client_id
    loop
        if private.content_ops_review_candidate(workspace, item.id, item.current_version_id) is null then
            raise exception 'NULL-FK natural candidate rejected before discovery';
        end if;
        candidate_count := candidate_count + 1;
    end loop;
    if candidate_count <> 4 then
        raise exception 'NULL-FK natural candidate fixture is not four-client';
    end if;
    -- Separate volatile RPC calls from assertions: scalar subquery InitPlans
    -- in one IF expression may otherwise observe the pre-insert snapshot.
    inserted_first := public.content_ops_reconcile_daily(workspace);
    inserted_again := public.content_ops_reconcile_daily(workspace);
    if inserted_first <> 4 or inserted_again <> 0 then
        raise exception 'NULL-FK natural completion cannot reconcile four clients once';
    end if;
    if (select count(*) from private.content_ops_review_outbox as queued
        where queued.workspace_id = workspace) <> 4
       or (select count(distinct queued.client_id) from private.content_ops_review_outbox as queued
           where queued.workspace_id = workspace) <> 4
       or exists (
           select 1 from private.content_ops_review_outbox as queued
           join public.content_items as candidate
             on candidate.workspace_id = queued.workspace_id and candidate.id = queued.content_item_id
           where queued.workspace_id = workspace
             and queued.content_version_id is distinct from candidate.current_version_id
       ) then
        raise exception 'NULL-FK reconciliation lost client or exact current version binding';
    end if;

    -- A fixed-version canary still discovers exactly one natural completion.
    workspace := private.test_content_ops_seed();
    update public.jobs set content_item_id = null where workspace_id = workspace;
    select candidate.current_version_id into version from public.content_items as candidate
    where candidate.workspace_id = workspace and candidate.client_id = 'yellow';
    inserted_first := public.content_ops_reconcile_daily(workspace, version);
    inserted_again := public.content_ops_reconcile_daily(workspace, version);
    if inserted_first <> 1 or inserted_again <> 0
       or (select count(*) from private.content_ops_review_outbox as queued
           where queued.workspace_id = workspace) <> 1
       or exists (
           select 1 from private.content_ops_review_outbox as queued
           where queued.workspace_id = workspace
             and (queued.client_id <> 'yellow' or queued.content_version_id <> version)
       ) then
        raise exception 'NULL-FK exact-version discovery widened or duplicated';
    end if;

    -- Request/output ambiguity and unsuccessful producers stay excluded, even
    -- without the optional FK. Malformed text must fail closed, not UUID-cast.
    foreach condition in array array[
        'missing_request', 'wrong_request', 'malformed_request',
        'missing_output_item', 'wrong_output_item',
        'missing_output_version', 'wrong_output_version', 'failed_job', 'wrong_optional_fk'
    ] loop
        workspace := private.test_content_ops_seed();
        update public.jobs set content_item_id = null where workspace_id = workspace;
        select candidate.* into item from public.content_items as candidate
        where candidate.workspace_id = workspace and candidate.client_id = 'yellow';
        select candidate.* into producer from public.jobs as candidate
        where candidate.workspace_id = workspace and candidate.client_id = 'yellow';
        case condition
        when 'missing_request' then
            update public.jobs set input = input - 'request_id' where id = producer.id;
        when 'wrong_request' then
            update public.jobs set input = input || jsonb_build_object('request_id', gen_random_uuid())
            where id = producer.id;
        when 'malformed_request' then
            update public.jobs set input = input || jsonb_build_object('request_id', 'synthetic-not-a-uuid')
            where id = producer.id;
        when 'missing_output_item' then
            update public.jobs set output = output - 'content_item_id' where id = producer.id;
        when 'wrong_output_item' then
            update public.jobs set output = output || jsonb_build_object('content_item_id', gen_random_uuid())
            where id = producer.id;
        when 'missing_output_version' then
            update public.jobs set output = output - 'content_version_id' where id = producer.id;
        when 'wrong_output_version' then
            update public.jobs set output = output || jsonb_build_object('content_version_id', gen_random_uuid())
            where id = producer.id;
        when 'failed_job' then
            update public.jobs set status = 'failed' where id = producer.id;
        when 'wrong_optional_fk' then
            conflicting_item := gen_random_uuid();
            insert into public.content_items(id, workspace_id, client_id, content_kind, title, status)
            values(conflicting_item, workspace, 'yellow', 'daily_news', 'Synthetic FK conflict', 'needs_review');
            update public.jobs set content_item_id = conflicting_item where id = producer.id;
        end case;
        if private.content_ops_review_candidate(workspace, item.id, item.current_version_id) is not null then
            raise exception 'invalid NULL-FK producer accepted by candidate helper';
        end if;
        inserted_first := public.content_ops_reconcile_daily(workspace);
        inserted_again := public.content_ops_reconcile_daily(workspace);
        if inserted_first <> 3 or inserted_again <> 0
           or exists (
               select 1 from private.content_ops_review_outbox as queued
               where queued.workspace_id = workspace and queued.client_id = 'yellow'
           ) then
            raise exception 'invalid NULL-FK producer leaked into discovery or blocked other clients';
        end if;
    end loop;

    -- Complete supported copy must survive the whole discovery/claim boundary,
    -- not merely the private helper. Include the pinned primary reference and
    -- cover both just-beyond-legacy and maximum supported lengths.
    foreach telegram_length in array array[2101, 3400] loop
        workspace := private.test_content_ops_seed();
        update public.jobs set content_item_id = null where workspace_id = workspace;
        select candidate.* into item from public.content_items as candidate
        where candidate.workspace_id = workspace and candidate.client_id = 'yellow';
        select source.id, source.canonical_url into reference_source_id, reference_source
        from public.content_source_links as link
        join public.source_items as source on source.id = link.source_item_id
          and source.workspace_id = link.workspace_id and source.client_id = link.client_id
        where link.workspace_id = workspace and link.content_item_id = item.id and link.position = 0;
        x_length := case telegram_length when 2101 then 601 else 1000 end;
        full_title := repeat('제', 160);
        full_telegram := repeat('검', telegram_length - length(reference_source) - 1)
            || E'\n' || reference_source;
        full_x := repeat('요', x_length - length(reference_source) - 1)
            || E'\n' || reference_source;
        if length(full_telegram) <> telegram_length or length(full_x) <> x_length then
            raise exception 'exact-copy fixture length invalid';
        end if;
        update public.content_versions
        set title = full_title, channel_copy = jsonb_build_object('telegram', full_telegram, 'x', full_x)
        where id = item.current_version_id;
        inserted_first := public.content_ops_reconcile_daily(workspace, item.current_version_id);
        inserted_again := public.content_ops_reconcile_daily(workspace, item.current_version_id);
        if inserted_first <> 1 or inserted_again <> 0 then
            raise exception 'supported exact-copy candidate was not discovered once';
        end if;
        claim_token := gen_random_uuid();
        claimed := public.content_ops_claim_review(workspace, claim_token, item.current_version_id);
        if claimed is null
           or claimed ->> 'title' is distinct from full_title
           or claimed ->> 'telegram_copy' is distinct from full_telegram
           or claimed ->> 'x_copy' is distinct from full_x
           or claimed ->> 'content_item_id' is distinct from item.id::text
           or claimed ->> 'content_version_id' is distinct from item.current_version_id::text
           or claimed ->> 'source_item_id' is distinct from reference_source_id::text
           or claimed ->> 'source_url' is distinct from reference_source
           or claimed ->> 'claim_token' is distinct from claim_token::text then
            raise exception 'discovery claim truncated copy or lost exact source/version binding';
        end if;
        if public.content_ops_claim_review(workspace, claim_token, item.current_version_id) is not null then
            raise exception 'exact-copy claim token replay accepted';
        end if;
    end loop;

    -- Unsupported payloads are held rather than truncated into a different
    -- statement. Other clients remain discoverable in the same workspace.
    foreach condition in array array['oversize_title', 'oversize_telegram', 'oversize_x'] loop
        workspace := private.test_content_ops_seed();
        update public.jobs set content_item_id = null where workspace_id = workspace;
        select candidate.* into item from public.content_items as candidate
        where candidate.workspace_id = workspace and candidate.client_id = 'yellow';
        case condition
        when 'oversize_title' then
            update public.content_versions set title = repeat('제', 161)
            where id = item.current_version_id;
        when 'oversize_telegram' then
            update public.content_versions
            set channel_copy = channel_copy || jsonb_build_object('telegram', repeat('검', 3401))
            where id = item.current_version_id;
        when 'oversize_x' then
            update public.content_versions
            set channel_copy = channel_copy || jsonb_build_object('x', repeat('요', 1001))
            where id = item.current_version_id;
        end case;
        if private.content_ops_review_candidate(workspace, item.id, item.current_version_id) is not null then
            raise exception 'oversize review copy was silently truncated or accepted';
        end if;
        inserted_first := public.content_ops_reconcile_daily(workspace);
        inserted_again := public.content_ops_reconcile_daily(workspace);
        if inserted_first <> 3 or inserted_again <> 0
           or exists (
               select 1 from private.content_ops_review_outbox as queued
               where queued.workspace_id = workspace and queued.client_id = 'yellow'
           ) then
            raise exception 'oversize copy leaked into discovery or blocked other clients';
        end if;
    end loop;
    if exists (select 1 from public.approvals)
       or exists (select 1 from public.publications) then
        raise exception 'discovery regression changed approval or publication state';
    end if;
end;
$test$;
rollback;
