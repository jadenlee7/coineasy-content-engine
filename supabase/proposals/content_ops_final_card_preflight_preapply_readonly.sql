-- Hosted catalog compatibility gate for the local-only final-card preflight.
-- No row reads, DDL, grants, approval, publication or provider I/O.
begin transaction read only;
do $$
declare
    relation_name text;
begin
    foreach relation_name in array array[
        'private.content_ops_review_principals',
        'private.content_ops_button_reviewers',
        'private.content_ops_button_reviews',
        'private.content_ops_button_checks',
        'private.content_ops_button_cards'
    ] loop
        if to_regclass(relation_name) is null then
            raise exception 'final_card_preflight_relation_missing';
        end if;
        if not (select relrowsecurity from pg_class where oid=to_regclass(relation_name)) then
            raise exception 'final_card_preflight_rls_missing';
        end if;
    end loop;
    if to_regprocedure('private.content_ops_final_card_preflight(uuid,uuid,uuid,text,text,text,text)')
        is not null then
        raise exception 'final_card_preflight_already_installed';
    end if;
    if to_regprocedure('private.content_ops_button_check_state(uuid,uuid)') is null
       or to_regprocedure('private.content_ops_button_version_fingerprint(uuid,uuid,uuid)') is null
       or to_regprocedure('private.content_ops_review_candidate(uuid,uuid,uuid)') is null then
        raise exception 'final_card_preflight_dependency_missing';
    end if;
    if not exists(select 1 from pg_constraint
        where conrelid='private.content_ops_button_checks'::regclass
          and confrelid='private.content_ops_review_principals'::regclass
          and contype='f')
       or not exists(select 1 from pg_constraint
        where conrelid='private.content_ops_button_reviewers'::regclass
          and confrelid='private.content_ops_review_principals'::regclass
          and contype='f')
       or not exists(select 1 from pg_constraint
        where conrelid='private.content_ops_button_cards'::regclass
          and confrelid='private.content_ops_button_reviews'::regclass
          and contype='f') then
        raise exception 'final_card_preflight_lineage_mismatch';
    end if;
    if exists(select 1 from (values
        ('private.content_ops_review_principals','id','uuid'),
        ('private.content_ops_review_principals','workspace_id','uuid'),
        ('private.content_ops_review_principals','bot_binding','text'),
        ('private.content_ops_review_principals','human_binding','text'),
        ('private.content_ops_button_reviewers','actor_id','uuid'),
        ('private.content_ops_button_reviewers','active','boolean'),
        ('private.content_ops_button_reviews','content_version_id','uuid'),
        ('private.content_ops_button_reviews','version_fingerprint','text'),
        ('private.content_ops_button_reviews','epoch','bigint'),
        ('private.content_ops_button_checks','check_kind','text'),
        ('private.content_ops_button_cards','bindings','jsonb'),
        ('private.content_ops_button_cards','parts','jsonb'),
        ('private.content_ops_button_cards','active','boolean')
    ) as required(relation_name,column_name,type_name)
    where not exists (select 1 from pg_attribute a
        where a.attrelid=to_regclass(required.relation_name)
          and a.attname=required.column_name and a.atttypid=required.type_name::regtype
          and a.attnum>0 and not a.attisdropped)) then
        raise exception 'final_card_preflight_column_mismatch';
    end if;
end $$;
select jsonb_build_object('final_card_preflight_preapply','pass',
    'read_only',true,'changes',0,'provider_calls',0,
    'hosted_runtime_verified',false);
commit;
