"""Disposable PostgreSQL proof; never accepts a remote DSN or production role.

Explicit --local-only and the existing exact-claim harness connection guards
are required. Controlled clocks are substituted ONLY in this disposable copy.
"""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('exact_local', ROOT / 'ops/ybo-server-trial/exact_claim_local_unittest.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
sql = helper.sql
PROPOSAL = ROOT / 'supabase/proposals/ybo_preserved_babylon_recovery.rollback.sql'
RECEIPT = '14831c91-0bae-45d9-9658-aa225e2a24cb'
JOB = 'e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af'
WORKSPACE = '5cc143cc-b253-4841-8a6c-ab3ac98623a6'
TRIAL = 'd346ceea-4ea9-479f-a5cf-05097b50ce48'
CLAIM = 'public.claim_exact_preserved_babylon_job(uuid,uuid,uuid,uuid,text,text,uuid,text,integer)'
GET = 'public.get_exact_preserved_babylon_context(uuid,uuid,uuid)'
EXISTING_CATALOG = """select coalesce(jsonb_agg(to_jsonb(p) order by p.oid),'[]'::jsonb)::text
  from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname in ('public','private','agent_runtime') and p.proname not in (
    'guard_ybo_preserved_babylon_immutable','guard_ybo_recovery_anchor_insert',
    'exact_preserved_babylon_context','get_exact_preserved_babylon_context','claim_exact_preserved_babylon_job')"""


def invoke(extra='', *, claim=True):
    function = 'preserved_fixture.claim()' if claim else 'preserved_fixture.context()'
    return sql("select set_config('request.jwt.claim.role','service_role',false);"
      + extra + 'select coalesce(' + function + "::text,'null');").splitlines()[-1]


class PreservedBabylonLocalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helper.local_env()
        cls.original = PROPOSAL.read_text()
        assert cls.original.count('clock_timestamp()') == 3
        assert sql("select to_regclass('private.ybo_preserved_babylon_compatibility_receipts') is null") == 't'
        # Additional deployed outbox dependency, not a fake permissive fixture.
        if sql("select to_regclass('private.content_ops_review_outbox') is null") == 't':
            sql((ROOT / 'supabase/migrations/20260906100000_content_ops_review_outbox.sql').read_text())
        cls.prior_catalog = sql(EXISTING_CATALOG)
        # Install original source only inside its mandatory rollback envelope.
        sql(cls.original)
        assert sql("select to_regclass('private.ybo_preserved_babylon_compatibility_receipts') is null") == 't'
        with unittest.TestCase().assertRaisesRegex(AssertionError, 'division by zero'):
            sql('begin;\n' + helper.proposal_statements(cls.original) + '\nselect 1/0; commit;')
        assert sql("select to_regprocedure('" + CLAIM + "') is null") == 't'
        assert sql(EXISTING_CATALOG) == cls.prior_catalog
        sql((Path(__file__).parent / 'recovery_fixture.sql').read_text())
        if sql("select to_regclass('private.ybo_trial_readiness_anchor') is null") == 't':
            prior = (ROOT / 'supabase/proposals/ybo_trial_readiness_anchor.rollback.sql').read_text()
            sql('begin;\n' + helper.proposal_statements(prior).replace('clock_timestamp()', 'preserved_fixture.clock()') + '\ncommit;')
        local = helper.proposal_statements(cls.original).replace('clock_timestamp()', 'preserved_fixture.clock()')
        sql('begin;\n' + local + '\ncommit;')
        # Historical anchor fixture was added after the baseline. All other
        # routines (including production claim/context/security) are byte-exact.
        without_fixture_anchor = EXISTING_CATALOG + " and p.proname<>'guard_ybo_trial_readiness_anchor'"
        before_without_anchor = json.loads(cls.prior_catalog)
        before_without_anchor = [row for row in before_without_anchor if row['proname']!='guard_ybo_trial_readiness_anchor']
        assert json.loads(sql(without_fixture_anchor)) == before_without_anchor
        sql('select preserved_fixture.seed();')
        # Historic anchor stands unchanged; recovery is a separate relation.
        cls.old = sql("select to_jsonb(t)::text from private.ybo_trial_readiness_anchor t")
        cls.old_catalog = sql("select to_jsonb(p)::text from pg_proc p where proname='guard_ybo_trial_readiness_anchor'")

    def setUp(self):
        # Test transactions never commit operational changes. Fixtures remain
        # fixed and each negative condition is applied only in its rollback.
        self.assertEqual(sql('select count(*) from private.ybo_preserved_babylon_handoffs'), '0')

    def test_01_default_off_without_anchor_and_receipt(self):
        self.assertEqual(invoke(claim=False), 'null')
        self.assertEqual(invoke(), 'null')

    def test_02_handoff_only_preserves_rows_and_replay(self):
        result = json.loads(sql("begin; select set_config('request.jwt.claim.role','service_role',false);"
            "select preserved_fixture.register(); select preserved_fixture.assert_handoff(); rollback;").splitlines()[-1])
        self.assertTrue(result['lease_only'])
        self.assertTrue(result['replay_rejected'])
        self.assertTrue(result['other_rows_unchanged'])

    def test_03_negative_mutations_have_zero_write(self):
        cases = [
          "update public.jobs set attempts=2 where id='"+JOB+"';",
          "update public.jobs set locked_by='another-owner' where id='"+JOB+"';",
          "update public.jobs set lease_expires_at='2026-10-15T13:00:00Z' where id='"+JOB+"';",
          "update public.jobs set output='{\"execution_plane\":\"studio_sync\",\"unexpected\":true}' where id='"+JOB+"';",
          "update public.jobs set last_error_code='unknown' where id='"+JOB+"';",
          "update public.jobs set input=input||'{\"manual_only\":true}' where id='"+JOB+"';",
          "update public.source_feeds set active=false where client_id='babylon';",
          "update public.source_feeds set last_polled_at='2026-10-15T10:00:00Z' where client_id='babylon';",
          "update public.source_feeds set poll_interval_minutes=30 where client_id='babylon';",
          "update public.source_feeds set last_polled_at=null where client_id='babylon';",
          "update public.source_feeds set last_polled_at='2026-10-15T13:00:00Z' where client_id='babylon';",
          "update public.source_items set published_at='2026-10-14T11:00:00Z' where id='35ae7c96-993a-4453-9946-3263f4f2842a';",
          "update public.source_items set published_at='2026-10-15T14:00:00Z' where id='35ae7c96-993a-4453-9946-3263f4f2842a';",
          "update public.source_items set media='[]' where id='35ae7c96-993a-4453-9946-3263f4f2842a';",
          "update public.source_items set external_id='2110380535927427175' where id='35ae7c96-993a-4453-9946-3263f4f2842a';",
          "update public.source_items set body='Changed source cannot reuse checkpoint' where id='35ae7c96-993a-4453-9946-3263f4f2842a';",
          "insert into public.source_items(workspace_id,client_id,source_feed_id,external_id,source_type,canonical_url,author_handle,published_at,body,source_hash,media) select workspace_id,client_id,source_feed_id,'2110380535927427175',source_type,'https://x.com/babylonlabs_io/status/2110380535927427175',author_handle,'2026-10-15T11:59:00Z',body,repeat('9',64),media from public.source_items where id='35ae7c96-993a-4453-9946-3263f4f2842a';",
          "insert into public.content_items(id,workspace_id,client_id,content_kind) values('52f95e4e-1f66-549a-baa4-32b0fdf82b30','5cc143cc-b253-4841-8a6c-ab3ac98623a6','babylon','daily_news');",
          "update public.workspace_clients set active=false where client_id='babylon';",
          "delete from private.official_x_daily_slots where client_id='babylon';",
          "delete from private.official_x_source_state where client_id='babylon';",
          "insert into public.jobs(workspace_id,client_id,job_kind,input) select workspace_id,client_id,job_kind,input from public.jobs where id='"+JOB+"';",
          "select set_config('preserved_fixture.now','2026-10-15T14:00:00Z',false);",
          "select set_config('preserved_fixture.now','2026-10-15T11:59:00Z',false);",
          "select set_config('preserved_fixture.advance_after_scan','2026-10-15T14:00:00Z',false);",
        ]
        for case in cases:
            with self.subTest(case=cases.index(case)):
                result = sql("begin; select set_config('request.jwt.claim.role','service_role',false);"
                    "select preserved_fixture.register(); " + case
                    + " select preserved_fixture.assert_no_claim(); rollback;").splitlines()[-1]
                self.assertEqual(result, 't')

    def test_04_acl_and_immutable(self):
        for role in ('anon', 'authenticated'):
            self.assertEqual(sql(f"select has_function_privilege('{role}','{CLAIM}','EXECUTE')"), 'f')
            self.assertEqual(sql(f"select has_function_privilege('{role}','{GET}','EXECUTE')"), 'f')
            with self.assertRaisesRegex(AssertionError, 'permission denied for function'):
                sql(f"set role {role}; select public.get_exact_preserved_babylon_context('{WORKSPACE}','{JOB}','{RECEIPT}');")
        self.assertEqual(sql(f"select has_function_privilege('service_role','{CLAIM}','EXECUTE')"), 't')
        for table in ('ybo_preserved_babylon_compatibility_receipts','ybo_recovery_readiness_anchor','ybo_preserved_babylon_handoffs'):
            for role in ('anon','authenticated','service_role'):
                self.assertEqual(sql(f"select has_table_privilege('{role}','private.{table}','SELECT,INSERT,UPDATE,DELETE,TRUNCATE')"), 'f')
            for operation in ('update private.'+table+' set trial_id=trial_id', 'delete from private.'+table, 'truncate private.'+table+' cascade'):
                with self.subTest(table=table, operation=operation):
                    with self.assertRaisesRegex(AssertionError, 'ybo_preserved_recovery_immutable'):
                        sql('begin; '+operation+'; rollback;')
        with self.assertRaisesRegex(AssertionError, 'preserved_babylon_service_role_required'):
            sql("select set_config('request.jwt.claim.role','authenticated',false); select preserved_fixture.claim();")

    def test_05_anchor_fixed_and_release_bound(self):
        for change in ("release_sha=repeat('b',40)", "not_before=not_before-interval '61 seconds',deadline=deadline-interval '61 seconds'",
                       "not_before=not_before+interval '1 second',deadline=deadline+interval '1 second'",
                       "deadline=deadline+interval '1 second'", "kst_date=kst_date+1"):
            with self.subTest(change=change):
                # Changes to the proposed insert, not a bypass of immutable guard.
                with self.assertRaises(AssertionError):
                    sql("begin; select preserved_fixture.register('"+change.replace("'","''")+"'); rollback;")
        with self.assertRaises(AssertionError):
            sql('begin; select preserved_fixture.register(); select preserved_fixture.register(); rollback;')

    def test_05b_bounded_catalog(self):
        catalog = json.loads(sql((Path(__file__).parent / 'catalog.sql').read_text()))
        self.assertEqual(len(catalog['tables']), 3)
        self.assertEqual(len(catalog['functions']), 5)
        for table in catalog['tables']:
            self.assertTrue(table['rls'])
            self.assertEqual(table['nonowner_acl_count'], 0)
            self.assertEqual(table['policy_count'], 0)
        for function in catalog['functions']:
            self.assertFalse(function['anon_execute'])
            self.assertFalse(function['authenticated_execute'])
            self.assertEqual(function['service_role_execute'], function['schema']=='public')
        self.assertEqual(catalog['receipt_count'], 0)
        self.assertEqual(catalog['anchor_count'], 0)
        self.assertEqual(catalog['handoff_count'], 0)

    def test_05c_exact_checkpoint_and_payload_pin(self):
        for column in ('checkpoint_sha256','api_payload_sha256'):
            with self.subTest(column=column):
                with self.assertRaisesRegex(AssertionError, 'violates check constraint'):
                    sql('begin; select preserved_fixture.register(); '
                        'create temp table changed_receipt as select * from private.ybo_preserved_babylon_compatibility_receipts; '
                        f"update changed_receipt set {column}=repeat('9',64); "
                        'insert into private.ybo_preserved_babylon_compatibility_receipts select * from changed_receipt; rollback;')

    def test_06_concurrent_only_one_handoff(self):
        sql('select preserved_fixture.register();')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(lambda _: invoke(), range(2)))
        self.assertEqual(sum(value != 'null' for value in values), 1)
        self.assertEqual(sql('select count(*) from private.ybo_preserved_babylon_handoffs'), '1')
        self.assertEqual(invoke(), 'null')
        self.assertEqual(sql("select to_jsonb(t)::text from private.ybo_trial_readiness_anchor t"), self.old)
        self.assertEqual(sql("select to_jsonb(p)::text from pg_proc p where proname='guard_ybo_trial_readiness_anchor'"), self.old_catalog)


if __name__ == '__main__':
    if sys.argv[1:] != ['--local-only']:
        raise SystemExit('explicit --local-only required')
    unittest.main(argv=[sys.argv[0]], verbosity=2)
