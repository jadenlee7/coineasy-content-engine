"""Disposable PG17 proof only. Reuses strict local socket/CI connection guard.

The exact production proposal first compiles and rolls back untouched. Clocks
are substituted only in a second fixture copy, never in the committed proposal.
No production credentials, provider calls, Telegram calls or database DSN.
"""
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
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
TABLE = 'private.ybo_readiness_babylon_trial_anchor'
GUARD = 'private.guard_ybo_readiness_babylon_trial_anchor()'
TRIAL = 'c34dbb8a-ff7c-484e-8e43-757f692ec743'
WORKSPACE = '5cc143cc-b253-4841-8a6c-ab3ac98623a6'
PROPOSAL = ROOT / 'supabase/proposals/readiness_babylon_trial.rollback.sql'


def catalog():
    return json.loads(sql((HERE / 'operator_catalog.sql').read_text()))


def invoke(ctx, extra='', changes=None):
    return sql("select set_config('request.jwt.claim.role','service_role',false);" + extra
        + 'select coalesce(readiness_trial_fixture.call_claim(' + helper.literal(ctx) + ','
        + helper.literal(changes or {}) + ")::text,'null');").splitlines()[-1]


def insert_anchor(**changes):
    values = dict(workspace_id=WORKSPACE, trial_id=TRIAL, plan_sha256='b'*64,
        release_sha='a'*40, not_before='2026-10-15T15:55:51Z',
        deadline='2026-10-15T17:55:51Z', kst_date='2026-10-16')
    values.update(changes)
    return 'insert into ' + TABLE + ' values (' + ','.join(repr(v) for v in values.values()) + ');'


class ReadinessBabylonLocalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helper.local_env()
        cls.original = PROPOSAL.read_text()
        assert cls.original.count('clock_timestamp()') == 4
        assert re.search(r'rollback;[^\n]*\s*$', cls.original, re.I)
        assert sql("select to_regclass('" + TABLE + "') is null") == 't'
        assert sql("select to_regnamespace('readiness_trial_fixture') is null") == 't'
        # Fresh proof committed clock-substituted fixture bodies. Restore its
        # exact original bodies in the disposable DB; OIDs/ACLs and old row stay.
        predecessor = (ROOT / 'supabase/proposals/newfresh_babylon_trial.rollback.sql').read_text()
        claim = helper.previous_function(predecessor,
            'claim_exact_natural_review_draft_job',
            'c62675f07e978d23516bfe533f76b340a3edeff710b6c845e40a7c2b599970c2')
        context = helper.previous_function(predecessor,
            'get_quality_banner_generation_context',
            '559d9b0e2f682b0fae476ad503cb776a481dd642afa01c4693b533241a147873')
        sql(claim + context)
        assert sql("select count(*) from private.ybo_fresh_babylon_trial_anchor where trial_id='9d7865c1-c13b-4b1b-aba1-83124aa8d2cd'") == '1'
        sql((HERE / 'readiness_fixture.sql').read_text())
        assert sql("select encode(extensions.digest(convert_to(prosrc,'UTF8'),'sha256'),'hex') from pg_proc where oid='"+CLAIM+"'::regprocedure") == 'c62675f07e978d23516bfe533f76b340a3edeff710b6c845e40a7c2b599970c2'
        assert sql("select encode(extensions.digest(convert_to(prosrc,'UTF8'),'sha256'),'hex') from pg_proc where oid='"+CONTEXT+"'::regprocedure") == '559d9b0e2f682b0fae476ad503cb776a481dd642afa01c4693b533241a147873'
        cls.before = catalog()
        assert cls.before['anchor_schema']['exists'] is False
        assert cls.before['previous_fresh_anchor'] == {'exists': True, 'row_count': 1, 'trial_id_count': 1}
        sql(cls.original)
        assert catalog()['existing_security_catalog_sha256'] == cls.before['existing_security_catalog_sha256']
        with unittest.TestCase().assertRaisesRegex(AssertionError, 'division by zero'):
            sql('begin;' + helper.proposal_statements(cls.original) + 'select 1/0; commit;')
        assert catalog()['anchor_schema']['exists'] is False
        # Collect the exact original candidate hashes while DDL is rolled back.
        query = (HERE / 'operator_catalog.sql').read_text().removeprefix("begin read only;\nset local statement_timeout='15s';\n").removesuffix('\nrollback;\n')
        cls.compiled = json.loads(sql('begin;' + helper.proposal_statements(cls.original) + query + 'rollback;'))
        for key in ('existing_security_catalog_sha256','existing_structural_catalog_sha256','dependency_sha256','target_dependency_sha256','business_rows_sha256'):
            assert cls.compiled[key] == cls.before[key], key
        for name, value in cls.before['target_functions'].items():
            assert cls.compiled['target_functions'][name]['non_body_catalog_sha256'] == value['non_body_catalog_sha256']
            assert cls.compiled['target_functions'][name]['body_sha256'] != value['body_sha256']
        local = helper.proposal_statements(cls.original)
        local = local.replace('clock_timestamp()', 'readiness_trial_fixture.anchor_clock()', 1)
        local = local.replace('clock_timestamp()', 'readiness_trial_fixture.clock()')
        local = local.replace('statement_timestamp()', 'readiness_trial_fixture.clock()')
        sql('begin;' + local + 'commit;')
        cls.old_anchors = sql("select readiness_trial_fixture.snapshot()-'public.jobs'-'public.source_items'-'public.source_feeds'-'public.workspace_clients'-'private.official_x_daily_slots'-'private.official_x_source_state'-'private.official_x_style_reference_packs'-'public.event_log'-'" + TABLE + "';")
        print('readiness_proposal_sha256=' + hashlib.sha256(cls.original.encode()).hexdigest())
        print('readiness_compiled_body_sha256=' + json.dumps({k:v['body_sha256'] for k,v in cls.compiled['target_functions'].items()}))
        print('readiness_anchor_guard_sha256=' + cls.compiled['anchor_schema']['guard_body_sha256'])
        print('readiness_anchor_metadata=' + json.dumps(cls.compiled['anchor_schema'],sort_keys=True))
        receipt_path = os.environ.get('READINESS_TRIAL_PROOF_RECEIPT')
        if receipt_path:
            path = Path(receipt_path)
            assert str(path).startswith(('/private/tmp/','/tmp/')) and path.is_absolute()
            receipt = {'schema':'readiness-babylon-disposable-pg-proof@1',
                'server_version_num':cls.compiled['server_version_num'],
                'proposal_sha256':hashlib.sha256(cls.original.encode()).hexdigest(),
                'catalog_sha256':hashlib.sha256((HERE/'operator_catalog.sql').read_bytes()).hexdigest(),
                'anchor_schema':cls.compiled['anchor_schema'],
                'target_functions':cls.compiled['target_functions'],
                'production_calls':0,'provider_calls':0,'telegram_calls':0}
            fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            try:
                os.write(fd,(json.dumps(receipt,sort_keys=True,indent=2)+'\n').encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            print('readiness_proof_receipt='+str(path))

    def test_01_missing_anchor_is_off(self):
        ctx = json.loads(sql('select readiness_trial_fixture.seed();'))
        before = sql('select readiness_trial_fixture.snapshot();')
        self.assertEqual(invoke(ctx), 'null')
        self.assertEqual(sql('select readiness_trial_fixture.snapshot();'), before)

    def test_02_anchor_invalid_rows_and_one_insert(self):
        cases = [dict(trial_id='11111111-1111-4111-8111-111111111111'),
            dict(workspace_id='11111111-1111-4111-8111-111111111111'),
            dict(kst_date='2026-10-15'), dict(deadline='2026-10-15T17:10:50Z'),
            dict(not_before='2026-10-15T15:10:51.5Z',deadline='2026-10-15T17:10:51.5Z'),
            dict(not_before='2026-10-15T15:47:34Z',deadline='2026-10-15T17:47:34Z'),
            dict(not_before='2026-10-15T15:54:50Z',deadline='2026-10-15T17:54:50Z'),
            dict(not_before='2026-10-15T15:55:52Z',deadline='2026-10-15T17:55:52Z'),
            dict(not_before='2026-10-15T14:00:00Z',deadline='2026-10-15T16:00:00Z',kst_date='2026-10-15'),
            dict(not_before='2026-10-16T14:00:00Z',deadline='2026-10-16T16:00:00Z'),
            dict(plan_sha256='A'*64), dict(release_sha='x'*40)]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(AssertionError):
                # Matching clock isolates earliest/day checks from freshness.
                when = values.get('not_before','2026-10-15T15:55:51Z') if values.get('not_before','').startswith(('2026-10-15T14','2026-10-15T15:47:34','2026-10-16T14')) else '2026-10-15T15:55:51Z'
                sql("begin; select set_config('readiness_trial_fixture.anchor_now'," + repr(when) + ',true);' + insert_anchor(**values) + 'rollback;')
        self.assertEqual(sql('select count(*) from ' + TABLE), '0')
        def insert(_):
            try:
                sql(insert_anchor()); return True
            except AssertionError as error:
                self.assertRegex(str(error), 'already_fixed|duplicate key'); return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(insert, range(2))), 1)

    def test_03_fresh_claim_positive_negative_and_logo_context(self):
        sql((HERE / 'readiness_claim_cases.sql').read_text(), timeout=120)
        sql((HERE / 'readiness_context_cases.sql').read_text(), timeout=120)

    def test_04_wrong_clients_workers_dates_are_zero_write(self):
        for client in ('yellow','origintrail','babylon'):
            ctx = json.loads(sql('select readiness_trial_fixture.seed(' + repr(client) + ');'))
            for worker in ('wrong-worker','ybo-trial:d346ceea-4ea9-479f-a5cf-05097b50ce48'):
                before = sql('select readiness_trial_fixture.snapshot();')
                self.assertEqual(invoke(ctx, changes={'worker':worker}), 'null')
                self.assertEqual(sql('select readiness_trial_fixture.snapshot();'), before)
            if client != 'babylon':
                before = sql('select readiness_trial_fixture.snapshot();')
                self.assertEqual(invoke(ctx), 'null')
                self.assertEqual(sql('select readiness_trial_fixture.snapshot();'), before)
        ctx = json.loads(sql('select readiness_trial_fixture.seed();'))
        for now in ('2026-10-15T14:59:59Z','2026-10-16T15:40:51Z','2026-10-15T17:55:51Z'):
            before = sql('select readiness_trial_fixture.snapshot();')
            self.assertEqual(invoke(ctx, "select set_config('readiness_trial_fixture.now',"+repr(now)+',false);'), 'null')
            self.assertEqual(sql('select readiness_trial_fixture.snapshot();'), before)

    def test_05_concurrent_claim_only_one_and_existing_rows_preserved(self):
        ctx = json.loads(sql('select readiness_trial_fixture.seed();'))
        before = sql('select readiness_trial_fixture.snapshot(' + repr(ctx['job_id']) + ');')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(v != 'null' for v in pool.map(lambda _:invoke(ctx),range(2))), 1)
        self.assertEqual(sql('select readiness_trial_fixture.snapshot(' + repr(ctx['job_id']) + ');'), before)
        self.assertEqual(invoke(ctx), 'null')

    def test_06_acl_catalog_and_immutability(self):
        observed = catalog()
        anchor = observed['anchor_schema']
        self.assertTrue(anchor['runtime_roles_have_no_access'])
        self.assertTrue(anchor['owner_matches_existing_routines'])
        self.assertTrue(anchor['rls_enabled'])
        self.assertEqual(anchor['policy_count'], 0)
        self.assertEqual(anchor['non_owner_table_acl_count'], 0)
        self.assertEqual(anchor['non_owner_guard_acl_count'], 0)
        self.assertEqual(len(anchor['columns']),7)
        self.assertEqual(len(anchor['triggers']),2)
        for operation in ('update '+TABLE+' set plan_sha256=plan_sha256','delete from '+TABLE,'truncate '+TABLE):
            with self.assertRaisesRegex(AssertionError,'immutable'):
                sql('begin;'+operation+';rollback;')
        for role in ('anon','authenticated','service_role'):
            self.assertEqual(sql(f"select has_table_privilege('{role}','{TABLE}','SELECT,INSERT,UPDATE,DELETE,TRUNCATE')"),'f')
        for name in (CLAIM,CONTEXT):
            for role in ('anon','authenticated'):
                self.assertEqual(sql(f"select has_function_privilege('{role}','{name}','EXECUTE')"),'f')
            self.assertEqual(sql(f"select has_function_privilege('service_role','{name}','EXECUTE')"),'t')
        self.assertEqual(observed['existing_security_catalog_sha256'],self.before['existing_security_catalog_sha256'])

    def test_07_scans_cannot_extend_expired_window(self):
        ctx = json.loads(sql('select readiness_trial_fixture.seed();'))
        before = sql('select readiness_trial_fixture.snapshot();')
        self.assertEqual(invoke(ctx,"select set_config('readiness_trial_fixture.pre_update_now','2026-10-15T17:55:51Z',false);"),'null')
        self.assertEqual(sql('select readiness_trial_fixture.snapshot();'),before)

    def test_08_proposal_replay_and_old_body_guard(self):
        before = sql('select readiness_trial_fixture.snapshot();')
        with self.assertRaisesRegex(AssertionError,'already exists'):
            sql(self.original)
        # Own disposable objects only, inside rollback: verify both exact prior
        # body guards fail before any new transaction can commit.
        reset = 'drop table '+TABLE+'; drop function '+GUARD+';'
        for prior_claim, message in ((False,'exact_claim_previous_body_mismatch'),
                                    (True,'quality_context_previous_body_mismatch')):
            prior = helper.previous_function((ROOT / 'supabase/proposals/newfresh_babylon_trial.rollback.sql').read_text(),
                'claim_exact_natural_review_draft_job',
                'c62675f07e978d23516bfe533f76b340a3edeff710b6c845e40a7c2b599970c2') if prior_claim else ''
            with self.assertRaisesRegex(AssertionError,message):
                sql('begin;'+reset+prior+helper.proposal_statements(self.original)+'commit;')
        self.assertEqual(sql('select readiness_trial_fixture.snapshot();'),before)
        self.assertEqual(PROPOSAL.read_text(),self.original)

    def test_09_preserved_old_job_and_anchor_rows_unchanged(self):
        # The preserved recovery suite ran first in this same disposable DB.
        # Fresh fixture setup never clears previous-day slots or old job keys.
        self.assertEqual(sql("select count(*) from public.jobs where id='e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af' and status='running' and attempts=1 and idempotency_key='official-x-review:v1:2026-10-15:babylon'"),'1')
        self.assertEqual(sql("select count(*) from private.official_x_daily_slots where job_id='e8ff3bbf-9fd1-42f0-ba24-b054d3b3d0af' and kst_date='2026-10-15'"),'1')
        self.assertEqual(sql("select count(*) from private.ybo_trial_readiness_anchor where trial_id='1a2bef47-4827-4bc3-9b4d-5baf2714015d'"),'1')
        self.assertEqual(sql("select count(*) from private.ybo_fresh_babylon_trial_anchor where trial_id='9d7865c1-c13b-4b1b-aba1-83124aa8d2cd'"),'1')
        self.assertEqual(sql("select count(*) from private.ybo_preserved_babylon_handoffs"),'1')

    def test_10_previous_fresh_worker_branch_still_available(self):
        # Run a new synthetic natural slot under the preceding worker/window.
        # The new proposal must not revoke that established route or anchor.
        ctx = json.loads(sql('select fresh_trial_fixture.seed();'))
        result = sql("select set_config('request.jwt.claim.role','service_role',false);"
            + "select set_config('readiness_trial_fixture.now','2026-10-15T15:40:51Z',false);"
            + "select coalesce(public.claim_exact_natural_review_draft_job("
            + repr(ctx['workspace_id']) + "::uuid,'babylon'," + repr(ctx['job_id'])
            + "::uuid," + repr(ctx['request_id']) + "::uuid," + repr(ctx['source_item_id'])
            + "::uuid," + repr(ctx['input_sha256']) + ',' + repr(ctx['style_pack_hash'])
            + ",'ybo-trial:9d7865c1-c13b-4b1b-aba1-83124aa8d2cd',900)::text,'null');").splitlines()[-1]
        self.assertNotEqual(result, 'null')


if __name__ == '__main__':
    if sys.argv[1:] != ['--local-only']:
        raise SystemExit('explicit --local-only required')
    unittest.main(argv=[sys.argv[0]],verbosity=2)
