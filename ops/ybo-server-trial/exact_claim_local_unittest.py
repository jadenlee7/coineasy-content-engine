"""LOCAL FIXTURE ONLY: disposable DB, no remote/production DSN.

Run explicitly with --local-only; not discovered by the general pytest suite.
Use an explicit /tmp UNIX socket, or CI's loopback Docker service only when
EXACT_CLAIM_LOCAL_CI=true. Confirm EXACT_CLAIM_DISPOSABLE_DATABASE in either case.
This harness never starts/stops servers, deletes resources or creates roles.
It installs the exact prior bodies locally, checks the replacement and rollback. A second
LOCAL-ONLY copy replaces exactly three clock_timestamp() calls with the fixture
clock to exercise the fixed future window without widening the candidate.
"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unittest

HERE = Path(__file__).resolve().parent
SIGNATURE = 'public.claim_exact_natural_review_draft_job(uuid,text,uuid,uuid,uuid,text,text,text,integer)'
PG_BIN = Path(os.environ.get('EXACT_CLAIM_PSQL', 'psql'))


def local_env():
    host = os.environ.get('PGHOST', '')
    database = os.environ.get('PGDATABASE', '')
    local_socket = host.startswith('/private/tmp/') or host.startswith('/tmp/')
    local_ci = (os.environ.get('EXACT_CLAIM_LOCAL_CI') == 'true'
        and host == '127.0.0.1' and os.environ.get('PGPORT') == '5432'
        and os.environ.get('PGUSER') == 'postgres'
        and database == 'postgres')
    if not (local_socket or local_ci):
        raise RuntimeError('explicit disposable socket or loopback CI service required')
    if not database or os.environ.get('EXACT_CLAIM_DISPOSABLE_DATABASE') != database:
        raise RuntimeError('explicit disposable database confirmation required')
    if os.environ.get('PGSERVICE') or os.environ.get('PGHOSTADDR'):
        raise RuntimeError('alternate PostgreSQL connection routes forbidden')
    result = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C', 'TZ': 'UTC',
        'PGHOST': host, 'PGPORT': os.environ.get('PGPORT', '5432'),
        'PGDATABASE': database, 'PGUSER': os.environ.get('PGUSER', 'postgres'),
        'PGPASSFILE': '/dev/null', 'PGCONNECT_TIMEOUT': '3'}
    if local_ci:
        if os.environ.get('PGPASSWORD') != 'postgres':
            raise RuntimeError('disposable CI password required')
        result['PGPASSWORD'] = 'postgres'
    return result


def sql(query, *, timeout=45):
    result = subprocess.run([str(PG_BIN), '-X', '--no-password', '-qAt',
        '-v', 'ON_ERROR_STOP=1'], input=query, text=True, capture_output=True,
        timeout=timeout, env=local_env(), check=False)
    if result.returncode:
        # Synthetic disposable fixture output only; no credential input exists.
        raise AssertionError(result.stderr[-5000:])
    return result.stdout.strip()


def literal(value):
    return "'" + json.dumps(value).replace("'", "''") + "'::jsonb"


def invoke(ctx, extra=''):
    return sql("select set_config('request.jwt.claim.role','service_role',false);"
        + extra + 'select coalesce(exact_claim_fixture.call_claim('
        + literal(ctx) + ")::text,'null');").splitlines()[-1]


def previous_function(candidate, name, expected_sha256):
    """Local-only reconstruction, pinned to the exact previously hosted body.

    Reversing only the two timestamps and KST day must reproduce its digest.
    Any non-window body edit fails before disposable installation.
    """
    match = re.search(r'create or replace function public\.' + re.escape(name)
        + r'[\s\S]*? as \$\$([\s\S]*?end )\$\$;', candidate)
    if match is None:
        raise AssertionError('candidate_function_not_found')
    previous = match.group(0).replace('2026-10-14T15:10:51Z', '2026-10-14T12:44:51Z') \
        .replace('2026-10-14T17:10:51Z', '2026-10-14T14:44:51Z') \
        .replace('2026-10-15', '2026-10-14')
    body = previous.split(' as $$', 1)[1].rsplit('$$;', 1)[0]
    if hashlib.sha256(body.encode()).hexdigest() != expected_sha256:
        raise AssertionError('candidate_not_time_day_only')
    return previous


class ExactNaturalClaimLocalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        local_env()
        cls.original = (HERE / 'exact-natural-claim.rollback.sql').read_text()
        assert 'LOCAL_ROLLBACK_PROOF' in cls.original
        assert cls.original.count('clock_timestamp()') == 3
        assert "'2026-10-14T15:10:51Z'" in cls.original
        assert "'2026-10-14T17:10:51Z'" in cls.original
        assert re.search(r'rollback;[^\n]*\s*$', cls.original, re.I)
        assert sql("select to_regprocedure('" + SIGNATURE + "') is null") == 't'
        assert sql("select to_regnamespace('exact_claim_fixture') is null") == 't'
        # Only the disposable bootstrap installs the prior exact routine/ACL.
        # The candidate is replacement-only and cannot grant/revoke privileges.
        previous = previous_function(cls.original, 'claim_exact_natural_review_draft_job',
            '6c306a539f0476eb7acb0cf4430d96d2a2783919a725de17c181fef5d3f1e477')
        sql(previous + ' revoke all on function ' + SIGNATURE
            + ' from public,anon,authenticated,service_role; grant execute on function '
            + SIGNATURE + ' to service_role;')
        query = "select to_jsonb(p)::text from pg_proc p where oid='" + SIGNATURE + "'::regprocedure"
        before = sql(query)
        # This parses/compiles the ORIGINAL fixed-date body and verifies its ACL
        # while inside the original transaction, then rolls the entire DDL back.
        proof = """
do $$begin
  if has_function_privilege('anon','SIGNATURE','EXECUTE')
    or has_function_privilege('authenticated','SIGNATURE','EXECUTE')
    or not has_function_privilege('service_role','SIGNATURE','EXECUTE')
    then raise exception 'candidate_acl_invalid'; end if;
end $$;
rollback;
""".replace('SIGNATURE', SIGNATURE)
        sql(re.sub(r'rollback;[^\n]*\s*$', lambda _: proof, cls.original, flags=re.I))
        assert sql(query) == before
        # The only body modification is clock substitution in the disposable DB.
        fixture = (HERE / 'exact_claim_fixture.sql').read_text()
        body = cls.original.replace('clock_timestamp()', 'exact_claim_fixture.clock()')
        body = re.sub(r'rollback;[^\n]*\s*$', 'commit;\n', body, flags=re.I)
        # Helpers reference the candidate; PL/pgSQL resolves it at invocation.
        sql(fixture)
        sql(body)
        after = json.loads(sql(query))
        expected = json.loads(before)
        assert after.pop('prosrc') != expected.pop('prosrc')
        assert after == expected
        try:
            sql(cls.original)
        except AssertionError as error:
            assert 'exact_claim_previous_body_mismatch' in str(error)
        else:
            raise AssertionError('changed_claim_body_not_rejected')

    @classmethod
    def tearDownClass(cls):
        # Only objects created by this test; fixture data uses distinct workspaces
        # and remains in the disposable DB for operator inspection until teardown.
        sql('drop function ' + SIGNATURE + '; drop schema exact_claim_fixture cascade;')

    def test_01_positive_negative_acl_no_other_rows(self):
        sql((HERE / 'exact_claim_cases.sql').read_text(), timeout=90)

    def test_02_actual_anon_and_authenticated_acl(self):
        for role in ('anon', 'authenticated'):
            with self.subTest(role=role):
                query = "begin; set local role " + role + "; select " + SIGNATURE.split('(')[0] + "(" \
                    "null,'yellow',null,null,null,null,null,'fixture-worker',900); rollback;"
                with self.assertRaisesRegex(AssertionError, 'permission denied for function'):
                    sql(query)

    def test_03_same_job_two_connections_one_winner(self):
        ctx = json.loads(sql('select exact_claim_fixture.seed();'))
        baseline = sql('select exact_claim_fixture.snapshot(' + repr(ctx['job_id']) + '::uuid);')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: invoke(ctx), range(2)))
        self.assertEqual(sum(result != 'null' for result in results), 1)
        self.assertEqual(sql('select attempts from public.jobs where id=' + repr(ctx['job_id'])), '1')
        self.assertEqual(baseline, sql('select exact_claim_fixture.snapshot(' + repr(ctx['job_id']) + '::uuid);'))

    def test_04_lock_wait_crosses_expiry_no_claim(self):
        ctx = json.loads(sql('select exact_claim_fixture.seed();'))
        sql("update public.source_feeds set last_polled_at='2026-10-14T17:05:51Z' where id="
            + repr(ctx['feed_id']))
        baseline = sql('select exact_claim_fixture.snapshot();')
        # One backend holds the exact workspace/day lock, matching natural intake.
        lock = "begin; select pg_advisory_xact_lock(hashtextextended(" + repr(ctx['workspace_id'] + ':2026-10-15') \
            + ",0)); select pg_sleep(2); commit;"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            holder = pool.submit(sql, "set application_name='exact-claim-fixture-lock';" + lock)
            for _ in range(50):
                if sql("select exists(select 1 from pg_stat_activity where application_name='exact-claim-fixture-lock' "
                    "and wait_event='PgSleep')") == 't':
                    break
                time.sleep(.02)
            else:
                self.fail('fixture_lock_not_observed')
            result = invoke(ctx, "select set_config('exact_claim_fixture.now','2026-10-14T17:09:50.5Z',false);"
                "select set_config('exact_claim_fixture.advance','true',false);")
            holder.result()
        self.assertEqual(result, 'null')
        self.assertEqual(baseline, sql('select exact_claim_fixture.snapshot();'))

    def test_05_candidate_file_not_rewritten(self):
        self.assertEqual((HERE / 'exact-natural-claim.rollback.sql').read_text(), self.original)
        print('candidate_sha256=' + hashlib.sha256(self.original.encode()).hexdigest())

    def test_07_time_consumed_by_validation_rechecked_before_write(self):
        for final_now, setup in (
            ('2026-10-14T17:10:51Z', ''),
            ('2026-10-14T15:41:51Z', "update public.source_feeds set last_polled_at='2026-10-14T15:11:51Z' where id=FEED;"),
            ('2026-10-14T15:41:51Z', "update public.source_items set published_at='2026-10-13T15:41:51Z' where id=SOURCE;"),
        ):
            with self.subTest(final_now=final_now, boundary=setup):
                ctx = json.loads(sql('select exact_claim_fixture.seed();'))
                if setup:
                    sql(setup.replace('FEED', repr(ctx['feed_id'])).replace('SOURCE', repr(ctx['source_item_id'])))
                before = sql('select exact_claim_fixture.snapshot();')
                result = invoke(ctx, "select set_config('exact_claim_fixture.pre_update_now'," + repr(final_now) + ',false);')
                self.assertEqual(result, 'null')
                self.assertEqual(before, sql('select exact_claim_fixture.snapshot();'))

    def test_06_invalid_arguments_no_mutation(self):
        ctx = json.loads(sql('select exact_claim_fixture.seed();'))
        args = [repr(ctx['workspace_id']) + '::uuid', repr(ctx['client_id']),
            repr(ctx['job_id']) + '::uuid', repr(ctx['request_id']) + '::uuid',
            repr(ctx['source_item_id']) + '::uuid', repr(ctx['input_sha256']),
            repr(ctx['style_pack_hash']), "'fixture-worker'", '900']
        cases = [(index, 'null') for index in range(9)] + [
            (1, "'squid'"), (1, "'unknown'"), (5, "'bad'"),
            (5, "repeat('A',64)"), (6, "'bad'"), (6, "repeat('B',32)"),
            (7, "''"), (7, "'not a worker'"), (7, "repeat('w',129)"),
            (8, '59'), (8, '901')]
        for index, value in cases:
            with self.subTest(argument=index, invalid=value):
                changed = args.copy()
                changed[index] = value
                sql("begin; select set_config('request.jwt.claim.role','service_role',true);"
                    "do $$declare before_state jsonb:=exact_claim_fixture.snapshot(); begin begin perform "
                    + SIGNATURE.split('(')[0] + '(' + ','.join(changed) + ');'
                    "raise exception 'invalid_argument_admitted'; exception when invalid_parameter_value then null; end;"
                    "perform exact_claim_fixture.assert_true(before_state=exact_claim_fixture.snapshot(),"
                    "'invalid_argument_unchanged'); end $$; rollback;")


    def test_08_python_nine_key_hash_matches_postgresql_jsonb_unicode(self):
        from core.automation.trial_producer_adapter import fixed_natural_input_sha256
        value = {'workflow': 'official_x_review_draft_v1', 'kst_date': '2026-10-15',
            'source_item_ids': ['11111111-1111-4111-8111-111111111111'],
            'content_kind': 'daily_news',
            'request_id': '22222222-2222-4222-8222-222222222222',
            'source_content': 'Yellow 업데이트: official line "quoted" and emoji 🚀',
            'source_url': 'https://x.com/Yellow/status/123456789',
            'source_image_url': 'https://pbs.twimg.com/media/fixture.jpg?name=orig',
            'manual_only': False}
        pg_text = sql('select ' + literal(value) + '::text;')
        self.assertEqual(fixed_natural_input_sha256(value),
            hashlib.sha256(pg_text.encode('utf8')).hexdigest())

    def test_09_python_hash_matches_seeded_natural_job(self):
        from core.automation.trial_producer_adapter import fixed_natural_input_sha256
        ctx = json.loads(sql('select exact_claim_fixture.seed();'))
        actual = json.loads(sql('select input::text from public.jobs where id='
            + repr(ctx['job_id']) + '::uuid;'))
        self.assertEqual(fixed_natural_input_sha256(actual), ctx['input_sha256'])

    def test_10_quality_logo_proposal_rolls_back_and_preserves_acl_catalog(self):
        root = HERE.parent.parent
        signature = 'public.get_quality_banner_generation_context(uuid,text,uuid)'
        historical = (root / 'supabase/proposals/quality_banner_generation_context.sql').read_text()
        candidate_path = root / 'supabase/proposals/quality_banner_generation_context_ybo_logo.rollback.sql'
        candidate = candidate_path.read_text()
        self.assertIn('LOCAL_ROLLBACK_PROOF', candidate)
        self.assertRegex(candidate, r'rollback;[^\n]*\s*$')
        self.assertIn("'2026-10-14T15:10:51Z'", candidate)
        self.assertIn("'2026-10-14T17:10:51Z'", candidate)
        self.assertEqual(sql("select to_regprocedure('" + signature + "') is null"), 't')
        sql(historical)
        sql(previous_function(candidate, 'get_quality_banner_generation_context',
            '52bee7b65bf683b958b38a8017cc14faea2246e301ac957de548692c99a5746e'))
        try:
            query = "select to_jsonb(p)::text from pg_proc p where oid='" + signature + "'::regprocedure"
            before = sql(query)
            # Untouched proposal must compile and roll back, preserving its
            # exact prior hosted prosrc and all non-body catalog fields/ACL.
            sql(candidate)
            self.assertEqual(before, sql(query))
            local = candidate.replace('statement_timestamp()', 'exact_claim_fixture.clock()')
            local = re.sub(r'rollback;[^\n]*\s*$', 'commit;\n', local, flags=re.I)
            sql(local)
            after = json.loads(sql(query))
            expected = json.loads(before)
            self.assertNotEqual(after.pop('prosrc'), expected.pop('prosrc'))
            self.assertEqual(after, expected)
            for role, allowed in [('anon', 'f'), ('authenticated', 'f'), ('service_role', 't')]:
                self.assertEqual(sql("select has_function_privilege('" + role + "','" + signature
                    + "','EXECUTE')"), allowed)
            sql((HERE / 'quality_logo_context_cases.sql').read_text(), timeout=90)
            # Reapplying against any altered body must fail closed.
            with self.assertRaisesRegex(AssertionError, 'quality_context_previous_body_mismatch'):
                sql(candidate)
            self.assertEqual(candidate_path.read_text(), candidate)
            print('quality_logo_candidate_sha256=' + hashlib.sha256(candidate.encode()).hexdigest())
        finally:
            sql('drop function ' + signature + ';')


if __name__ == '__main__':
    if sys.argv[1:] != ['--local-only']:
        raise SystemExit('explicit --local-only required')
    unittest.main(argv=[sys.argv[0]], verbosity=2)
