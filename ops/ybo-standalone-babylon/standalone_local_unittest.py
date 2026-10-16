"""Disposable PostgreSQL proof, never a production/operator entry point.

Run after the existing readiness suite. Production SQL is first compiled
unchanged with rollback, then only fixture clocks are substituted locally.
"""
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('exact_local', ROOT / 'ops/ybo-server-trial/exact_claim_local_unittest.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
sql = helper.sql
CLAIM = helper.SIGNATURE
CONTEXT = 'public.get_quality_banner_generation_context(uuid,text,uuid)'
TABLE = 'private.ybo_standalone_babylon_trial_anchor'
GUARD = 'private.guard_ybo_standalone_babylon_trial_anchor()'
TRIAL = '7b27e277-0641-4aef-b739-761f86547287'
WORKSPACE = '5cc143cc-b253-4841-8a6c-ab3ac98623a6'
PROPOSAL = ROOT / 'supabase/proposals/standalone_babylon_trial.rollback.sql'
PREVIOUS = ROOT / 'supabase/proposals/readiness_babylon_trial.rollback.sql'
HASHES = {'claim_exact_natural_review_draft_job':
    '0fc3dfa2ffa4b9e4619996c0faa28b2af5719f8c40f660eceedd48291dd68152',
    'get_quality_banner_generation_context':
    '57249c5210b32ead5e529a5dad3eab1c9213563550f3e50a09fd1083ff419e2b'}


def catalog():
    return json.loads(sql((HERE / 'operator_catalog.sql').read_text()))


def invoke(ctx, extra='', changes=None):
    return sql("select set_config('request.jwt.claim.role','service_role',false);" + extra
        + 'select coalesce(standalone_trial_fixture.call_claim(' + helper.literal(ctx) + ','
        + helper.literal(changes or {}) + ")::text,'null');").splitlines()[-1]


def anchor(**changes):
    values = dict(workspace_id=WORKSPACE, trial_id=TRIAL, plan_sha256='b'*64,
        release_sha='a'*40, not_before='2026-10-16T19:55:51Z',
        deadline='2026-10-16T21:55:51Z', kst_date='2026-10-17')
    values.update(changes)
    return 'insert into ' + TABLE + ' values (' + ','.join(repr(v) for v in values.values()) + ');'


class StandaloneBabylonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helper.local_env()
        cls.original = PROPOSAL.read_text()
        assert cls.original.count('clock_timestamp()') == 4
        assert sql("select to_regclass('" + TABLE + "') is null") == 't'
        # Restore exact hosted preimages after the preceding fixture suite.
        sql(''.join(helper.previous_function(PREVIOUS.read_text(), name, value)
            for name, value in HASHES.items()))
        sql((HERE / 'standalone_fixture.sql').read_text())
        cls.before = catalog()
        cls.rows_before = sql('select standalone_trial_fixture.snapshot();')
        sql(cls.original)
        rolled_back = catalog()
        for key in ('existing_security_catalog_sha256','existing_structural_catalog_sha256',
                    'dependency_sha256','target_dependency_sha256','business_rows_sha256'):
            assert rolled_back[key] == cls.before[key], key
        assert sql('select standalone_trial_fixture.snapshot();') == cls.rows_before
        payload = helper.proposal_statements(cls.original)
        with unittest.TestCase().assertRaisesRegex(AssertionError, 'division by zero'):
            sql('begin;' + payload + 'select 1/0;commit;')
        assert catalog()['anchor_schema']['exists'] is False
        query = (HERE / 'operator_catalog.sql').read_text().removeprefix(
            "begin read only;\nset local statement_timeout='15s';\n").removesuffix('\nrollback;\n')
        cls.compiled = json.loads(sql('begin;' + payload + query + 'rollback;'))
        for key in ('existing_security_catalog_sha256','existing_structural_catalog_sha256',
                    'dependency_sha256','target_dependency_sha256','business_rows_sha256'):
            assert cls.compiled[key] == cls.before[key], key
        for name, previous in cls.before['target_functions'].items():
            assert cls.compiled['target_functions'][name]['non_body_catalog_sha256'] == previous['non_body_catalog_sha256']
            assert previous['body_sha256'] == HASHES[name]
        local = payload.replace('clock_timestamp()', 'standalone_trial_fixture.anchor_clock()', 1)
        local = local.replace('clock_timestamp()', 'standalone_trial_fixture.clock()')
        local = local.replace('statement_timestamp()', 'standalone_trial_fixture.clock()')
        sql('begin;' + local + 'commit;')
        assert sql('select standalone_trial_fixture.snapshot()-' + repr(TABLE) + ';') == cls.rows_before
        print('standalone_proposal_sha256=' + hashlib.sha256(cls.original.encode()).hexdigest())
        print('standalone_compiled_body_sha256=' + json.dumps({name: value['body_sha256']
            for name, value in cls.compiled['target_functions'].items()}))
        print('standalone_anchor_guard_sha256=' + cls.compiled['anchor_schema']['guard_body_sha256'])
        receipt_path = os.environ.get('STANDALONE_TRIAL_PROOF_RECEIPT')
        if receipt_path:
            destination = Path(receipt_path)
            assert str(destination).startswith(('/tmp/', '/private/tmp/')) and destination.is_absolute()
            receipt = {'schema': 'standalone-babylon-disposable-pg-proof@1',
                'proposal_sha256': hashlib.sha256(cls.original.encode()).hexdigest(),
                'catalog_sha256': hashlib.sha256((HERE / 'operator_catalog.sql').read_bytes()).hexdigest(),
                'before': cls.before, 'compiled': cls.compiled,
                'production_calls': 0, 'provider_calls': 0, 'telegram_calls': 0}
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.write(fd, (json.dumps(receipt, sort_keys=True) + '\n').encode())
                os.fsync(fd)
            finally:
                os.close(fd)

    def test_01_no_anchor_means_no_claim(self):
        ctx = json.loads(sql('select standalone_trial_fixture.seed();'))
        before = sql('select standalone_trial_fixture.snapshot();')
        self.assertEqual(invoke(ctx), 'null')
        self.assertEqual(sql('select standalone_trial_fixture.snapshot();'), before)

    def test_02_current_day_freshness_and_future_calendar(self):
        # No fixed calendar restriction: another actual current KST day works.
        sql("begin;select set_config('standalone_trial_fixture.anchor_now','2026-11-03T19:55:51Z',true);"
            + anchor(not_before='2026-11-03T19:55:51Z', deadline='2026-11-03T21:55:51Z',
                     kst_date='2026-11-04') + 'rollback;')
        cases = [dict(trial_id='11111111-1111-4111-8111-111111111111'),
            dict(workspace_id='11111111-1111-4111-8111-111111111111'),
            dict(kst_date='2026-10-16'), dict(deadline='2026-10-16T21:55:50Z'),
            dict(not_before='2026-10-16T19:55:51.5Z', deadline='2026-10-16T21:55:51.5Z'),
            dict(not_before='2026-10-16T19:45:02Z', deadline='2026-10-16T21:45:02Z'),
            dict(not_before='2026-10-16T19:54:50Z', deadline='2026-10-16T21:54:50Z'),
            dict(not_before='2026-10-16T19:55:52Z', deadline='2026-10-16T21:55:52Z'),
            dict(not_before='2026-10-17T14:00:00Z', deadline='2026-10-17T16:00:00Z'),
            dict(plan_sha256='A'*64), dict(release_sha='x'*40)]
        for values in cases:
            when = values.get('not_before') if values.get('not_before') in (
                '2026-10-16T19:45:02Z','2026-10-17T14:00:00Z') else '2026-10-16T19:55:51Z'
            with self.subTest(values=values), self.assertRaises(AssertionError):
                sql("begin;select set_config('standalone_trial_fixture.anchor_now'," + repr(when)
                    + ',true);' + anchor(**values) + 'rollback;')
        def insert(_):
            try:
                sql(anchor()); return True
            except AssertionError as error:
                self.assertRegex(str(error), 'already_fixed|duplicate key'); return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(insert, range(2))), 1)

    def test_03_claim_and_context_cases(self):
        sql((HERE / 'standalone_claim_cases.sql').read_text(), timeout=120)
        sql((HERE / 'standalone_context_cases.sql').read_text(), timeout=120)

    def test_04_concurrent_claim_exactly_one(self):
        ctx = json.loads(sql('select standalone_trial_fixture.seed();'))
        before = sql('select standalone_trial_fixture.snapshot(' + repr(ctx['job_id']) + ');')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(v != 'null' for v in pool.map(lambda _: invoke(ctx), range(2))), 1)
        self.assertEqual(sql('select standalone_trial_fixture.snapshot(' + repr(ctx['job_id']) + ');'), before)
        self.assertEqual(invoke(ctx), 'null')

    def test_05_acl_immutability_and_catalog(self):
        observed = catalog()
        info = observed['anchor_schema']
        for key in ('runtime_roles_have_no_access','owner_matches_existing_routines','rls_enabled'):
            self.assertTrue(info[key], key)
        for key in ('policy_count','non_owner_table_acl_count','non_owner_guard_acl_count'):
            self.assertEqual(info[key], 0, key)
        self.assertEqual(len(info['columns']), 7)
        self.assertEqual(len(info['triggers']), 2)
        for operation in ('update '+TABLE+' set plan_sha256=plan_sha256','delete from '+TABLE,'truncate '+TABLE):
            with self.assertRaisesRegex(AssertionError, 'immutable'):
                sql('begin;' + operation + ';rollback;')
        self.assertEqual(observed['existing_security_catalog_sha256'], self.before['existing_security_catalog_sha256'])
        for name in (CLAIM, CONTEXT):
            for role in ('anon','authenticated'):
                self.assertEqual(sql(f"select has_function_privilege('{role}','{name}','EXECUTE')"), 'f')
            self.assertEqual(sql(f"select has_function_privilege('service_role','{name}','EXECUTE')"), 't')

    def test_06_validation_scans_cannot_extend_window(self):
        ctx = json.loads(sql('select standalone_trial_fixture.seed();'))
        before = sql('select standalone_trial_fixture.snapshot();')
        self.assertEqual(invoke(ctx, "select set_config('standalone_trial_fixture.pre_update_now','2026-10-16T21:55:51Z',false);"), 'null')
        self.assertEqual(sql('select standalone_trial_fixture.snapshot();'), before)

    def test_07_old_readiness_worker_and_anchors_preserved(self):
        ctx = json.loads(sql('select readiness_trial_fixture.seed();'))
        result = invoke(ctx, "select set_config('standalone_trial_fixture.now','2026-10-15T16:25:51Z',false);",
            {'worker':'ybo-trial:c34dbb8a-ff7c-484e-8e43-757f692ec743'})
        self.assertNotEqual(result, 'null')
        for table in ('ybo_trial_readiness_anchor','ybo_fresh_babylon_trial_anchor','ybo_readiness_babylon_trial_anchor'):
            self.assertEqual(sql('select count(*) from private.' + table), '1')
        self.assertEqual(sql("select count(*) from public.jobs where id='e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af' and status='running' and attempts=1"), '1')

    def test_08_replay_and_preimage_guards(self):
        before = sql('select standalone_trial_fixture.snapshot();')
        with self.assertRaisesRegex(AssertionError, 'already exists'):
            sql(self.original)
        reset = 'drop table ' + TABLE + ';drop function ' + GUARD + ';'
        for restore_claim, message in ((False,'exact_claim_previous_body_mismatch'),
                                      (True,'quality_context_previous_body_mismatch')):
            prior = helper.previous_function(PREVIOUS.read_text(), 'claim_exact_natural_review_draft_job',
                HASHES['claim_exact_natural_review_draft_job']) if restore_claim else ''
            with self.assertRaisesRegex(AssertionError, message):
                sql('begin;' + reset + prior + helper.proposal_statements(self.original) + 'commit;')
        self.assertEqual(sql('select standalone_trial_fixture.snapshot();'), before)
        self.assertEqual(PROPOSAL.read_text(), self.original)


if __name__ == '__main__':
    if sys.argv[1:] != ['--local-only']:
        raise SystemExit('explicit --local-only required')
    unittest.main(argv=[sys.argv[0]], verbosity=2)
