-- New OriginTrail publish candidates must fit the exact Telegram caption
-- contract. NOT VALID deliberately leaves historical shadow rows untouched;
-- the v2 review eligibility predicate excludes any legacy value over 1024.

begin;

alter table agent_runtime.batch_jobs
    add constraint origintrail_batch_telegram_copy_publish_limit check (
        not (
            client_id = 'origintrail'
            and workflow_kind = 'official_source_nonurgent_pack'
            and status = 'completed'
            and result_code = 'needs_review'
        )
        or (
            result_payload ? 'telegram_copy_ko'
            and
            jsonb_typeof(result_payload -> 'telegram_copy_ko') = 'string'
            and char_length(result_payload ->> 'telegram_copy_ko')
                between 1 and 1024
            and (result_payload ->> 'telegram_copy_ko') ~ '[^[:space:]]'
        )
    ) not valid;

commit;
