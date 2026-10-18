"""Disposable local PostgreSQL proof; no production/operator entry point.

Run after standalone_local_unittest.py in an explicitly isolated database.
The unmodified rollback-only proposal is compiled first. Clock substitution is
only in a second local fixture copy; production exposes no clock override.
"""
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PRIOR = ROOT / 'ops/ybo-standalone-babylon'
spec = importlib.util.spec_from_file_location('exact_local', ROOT / 'ops/ybo-server-trial/exact_claim_local_unittest.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
sql = helper.sql
PROPOSAL = ROOT / 'supabase/proposals/babylon_natural_once.rollback.sql'
PREVIOUS = ROOT / 'supabase/proposals/standalone_babylon_trial.rollback.sql'
HASHES = {
    'claim_exact_natural_review_draft_job': '532ef9755d1909887ad0bf206330ad6b76362433ad1c5d2ec0297df62b989152',
    'get_quality_banner_generation_context': '03cd9ff323cabdbad97cc6f3571b86fb3c1f7dadec97378f3ec8adb3cc6c548e',
}


def catalog():
    result = json.loads(sql((PRIOR / 'operator_catalog.sql').read_text()))
    result.pop('observed_at')
    return result


def invoke(ctx, extra='', changes=None):
    return sql("select set_config('request.jwt.claim.role','service_role',false);" + extra
        + 'select coalesce(babylon_natural_fixture.call_claim(' + helper.literal(ctx) + ','
        + helper.literal(changes or {}) + ")::text,'null');").splitlines()[-1]


class NaturalBabylonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helper.local_env()
        cls.original = PROPOSAL.read_text()
        assert cls.original.count('clock_timestamp()') == 3
        sql(''.join(helper.previous_function(PREVIOUS.read_text(), name, value)
            for name, value in HASHES.items()))
        sql((HERE / 'natural_fixture.sql').read_text())
        cls.before = catalog()
        cls.rows_before = sql('select babylon_natural_fixture.snapshot();')
        sql(cls.original)
        assert catalog() == cls.before
        assert sql('select babylon_natural_fixture.snapshot();') == cls.rows_before
        payload = helper.proposal_statements(cls.original)
        query = (PRIOR / 'operator_catalog.sql').read_text().removeprefix(
            "begin read only;\nset local statement_timeout='15s';\n").removesuffix('\nrollback;\n')
        cls.compiled = json.loads(sql('begin;' + payload + query + 'rollback;'))
        for key in ('existing_security_catalog_sha256', 'existing_structural_catalog_sha256',
                    'dependency_sha256', 'target_dependency_sha256', 'business_rows_sha256', 'anchor_schema'):
            assert cls.compiled[key] == cls.before[key], key
        for name, previous in cls.before['target_functions'].items():
            assert cls.compiled['target_functions'][name]['non_body_catalog_sha256'] == previous['non_body_catalog_sha256']
            assert previous['body_sha256'] == HASHES[name]
        with unittest.TestCase().assertRaisesRegex(AssertionError, 'division by zero'):
            sql('begin;' + payload + 'select 1/0;commit;')
        assert catalog() == cls.before
        assert sql('select babylon_natural_fixture.snapshot();') == cls.rows_before
        # Only disposable installation: do not use this helper for production.
        cls.functions = ''.join(helper.previous_function(cls.original, name,
            cls.compiled['target_functions'][name]['body_sha256']) for name in HASHES)
        cls.install_clock('babylon_natural_fixture')
        assert sql('select babylon_natural_fixture.snapshot();') == cls.rows_before
        print('natural_proposal_sha256=' + hashlib.sha256(cls.original.encode()).hexdigest())
        print('natural_compiled_body_sha256=' + json.dumps({name: value['body_sha256']
            for name, value in cls.compiled['target_functions'].items()}))

    @classmethod
    def install_clock(cls, fixture):
        assert fixture in ('babylon_natural_fixture', 'standalone_trial_fixture')
        sql(cls.functions.replace('clock_timestamp()', fixture + '.clock()')
            .replace('statement_timestamp()', fixture + '.clock()'))

    def test_01_exact_claim_and_logo_context_matrix(self):
        sql((HERE / 'natural_claim_cases.sql').read_text(), timeout=120)
        sql((HERE / 'natural_context_cases.sql').read_text(), timeout=120)

    def test_02_concurrent_exact_claim_and_consumed_replay(self):
        ctx = json.loads(sql('select babylon_natural_fixture.seed();'))
        before = sql('select babylon_natural_fixture.snapshot(' + repr(ctx['job_id']) + ');')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(value != 'null' for value in pool.map(lambda _: invoke(ctx), range(2))), 1)
        self.assertEqual(sql('select babylon_natural_fixture.snapshot(' + repr(ctx['job_id']) + ');'), before)
        self.assertEqual(invoke(ctx), 'null')

    def test_03_time_expiry_during_validation_is_noop(self):
        ctx = json.loads(sql('select babylon_natural_fixture.seed();'))
        before = sql('select babylon_natural_fixture.snapshot();')
        self.assertEqual(invoke(ctx, "select set_config('babylon_natural_fixture.pre_update_now','2026-10-16T22:24:51Z',false);"), 'null')
        self.assertEqual(sql('select babylon_natural_fixture.snapshot();'), before)

    def test_04_owner_binding_and_all_media_fences(self):
        sql((HERE / 'natural_owner_cases.sql').read_text(), timeout=120)

    def test_05_old_anchor_branches_unchanged(self):
        # Re-run the historical worker matrices against both NEW bodies.
        # Their own fixture clocks are substituted only for this local proof.
        self.install_clock('standalone_trial_fixture')
        try:
            sql((PRIOR / 'standalone_claim_cases.sql').read_text(), timeout=120)
            sql((PRIOR / 'standalone_context_cases.sql').read_text(), timeout=120)
        finally:
            self.install_clock('babylon_natural_fixture')

    def test_06_preimage_replay_and_catalog_preserved(self):
        before = sql('select babylon_natural_fixture.snapshot();')
        with self.assertRaisesRegex(AssertionError, 'claim_exact_natural_review_draft_job_previous_body_mismatch'):
            sql(self.original)
        prior_claim = helper.previous_function(PREVIOUS.read_text(),
            'claim_exact_natural_review_draft_job', HASHES['claim_exact_natural_review_draft_job'])
        with self.assertRaisesRegex(AssertionError, 'get_quality_banner_generation_context_previous_body_mismatch'):
            sql('begin;' + prior_claim + helper.proposal_statements(self.original) + 'commit;')
        observed = catalog()
        for name, previous in self.before['target_functions'].items():
            self.assertEqual(observed['target_functions'][name]['non_body_catalog_sha256'], previous['non_body_catalog_sha256'])
        self.assertEqual(observed['existing_security_catalog_sha256'], self.before['existing_security_catalog_sha256'])
        self.assertEqual(sql('select babylon_natural_fixture.snapshot();'), before)
        self.assertEqual(PROPOSAL.read_text(), self.original)


if __name__ == '__main__':
    if sys.argv[1:] != ['--local-only']:
        raise SystemExit('explicit --local-only required')
    unittest.main(argv=[sys.argv[0]], verbosity=2)
