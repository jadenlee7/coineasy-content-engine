-- DISPOSABLE TEST ONLY. No production/provider/delivery execution.
begin;
do $$
declare row_value jsonb; changed jsonb; scenario integer; role_name text;
  insert_sql text:='insert into private.ybo_trial_readiness_anchor select * from jsonb_populate_record(null::private.ybo_trial_readiness_anchor,$1)';
begin
  row_value:=jsonb_build_object('workspace_id','5cc143cc-b253-4841-8a6c-ab3ac98623a6',
    'trial_id','1a2bef47-4827-4bc3-9b4d-5baf2714015d','plan_sha256',repeat('b',64),
    'release_sha',repeat('a',40),'not_before','2026-10-14T15:10:51Z',
    'deadline','2026-10-14T17:10:51Z','kst_date','2026-10-15');
  foreach role_name in array array['anon','authenticated','service_role'] loop
    perform exact_claim_fixture.assert_true(not has_table_privilege(role_name,
      'private.ybo_trial_readiness_anchor','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
      'anchor_no_runtime_table_acl_'||role_name);
    perform exact_claim_fixture.assert_true(not has_function_privilege(role_name,
      'private.guard_ybo_trial_readiness_anchor()','EXECUTE'),'anchor_no_runtime_function_acl_'||role_name);
  end loop;
  perform exact_claim_fixture.assert_true(not exists(select 1 from pg_class c,
    lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
    where c.oid='private.ybo_trial_readiness_anchor'::regclass and a.grantee=0),'anchor_no_public_acl');

  for scenario in 1..20 loop
    changed:=row_value;
    case scenario
      when 1 then changed:=changed||jsonb_build_object('trial_id',gen_random_uuid());
      when 2 then changed:=changed||jsonb_build_object('workspace_id',gen_random_uuid());
      when 3 then changed:=changed||'{"plan_sha256":null}';
      when 4 then changed:=changed||'{"plan_sha256":"short"}';
      when 5 then changed:=changed||jsonb_build_object('plan_sha256',repeat('A',64));
      when 6 then changed:=changed||'{"release_sha":null}';
      when 7 then changed:=changed||jsonb_build_object('release_sha',repeat('A',40));
      when 8 then changed:=changed||'{"not_before":null}';
      when 9 then changed:=changed||'{"deadline":null}';
      when 10 then changed:=changed||'{"kst_date":null}';
      when 11 then changed:=changed||'{"not_before":"2026-10-14T15:10:52Z","deadline":"2026-10-14T17:10:52Z"}';
      when 12 then changed:=changed||'{"not_before":"2026-10-14T15:09:50Z","deadline":"2026-10-14T17:09:50Z"}';
      when 13 then changed:=changed||'{"deadline":"2026-10-14T17:10:50Z"}';
      when 14 then changed:=changed||'{"deadline":"2026-10-14T17:10:52Z"}';
      when 15 then changed:=changed||'{"kst_date":"2026-10-14"}';
      when 16 then changed:=changed||'{"not_before":"2026-10-14T15:10:50.5Z","deadline":"2026-10-14T17:10:50.5Z"}';
      when 17 then changed:=changed||'{"not_before":"-infinity","deadline":"infinity"}';
      when 18 then changed:=changed||'{"release_sha":"short"}';
      when 19 then changed:=changed||'{"workspace_id":null}';
      when 20 then changed:=changed||'{"trial_id":null}';
    end case;
    begin
      execute insert_sql using changed;
      raise exception 'anchor_invalid_row_admitted_%',scenario;
    exception when check_violation or not_null_violation or invalid_parameter_value then null; end;
    perform exact_claim_fixture.assert_true(not exists(select 1 from private.ybo_trial_readiness_anchor),
      'anchor_invalid_no_row_'||scenario);
  end loop;
  -- A real start on another day is allowed, but a KST midnight crossing is not.
  perform set_config('exact_claim_fixture.anchor_now','2026-10-20T14:00:00Z',true);
  begin
    execute insert_sql using row_value||'{"not_before":"2026-10-20T14:00:00Z","deadline":"2026-10-20T16:00:00Z","kst_date":"2026-10-20"}';
    raise exception 'anchor_cross_midnight_admitted';
  exception when check_violation then null; end;
  perform set_config('exact_claim_fixture.anchor_now','2026-10-20T09:00:00Z',true);
  execute insert_sql using row_value||'{"not_before":"2026-10-20T08:59:00Z","deadline":"2026-10-20T10:59:00Z","kst_date":"2026-10-20"}';
  perform exact_claim_fixture.assert_true((select count(*)=1 from private.ybo_trial_readiness_anchor),
    'anchor_arbitrary_ready_day_and_exact_60sec_boundary');
  begin update private.ybo_trial_readiness_anchor set deadline=deadline+interval '1 second';
    raise exception 'anchor_update_admitted'; exception when object_not_in_prerequisite_state then null; end;
  begin delete from private.ybo_trial_readiness_anchor;
    raise exception 'anchor_delete_admitted'; exception when object_not_in_prerequisite_state then null; end;
  begin truncate private.ybo_trial_readiness_anchor;
    raise exception 'anchor_truncate_admitted'; exception when object_not_in_prerequisite_state then null; end;
  begin execute insert_sql using row_value;
    raise exception 'anchor_second_insert_admitted'; exception when object_not_in_prerequisite_state or invalid_parameter_value then null; end;
end $$;
rollback;
