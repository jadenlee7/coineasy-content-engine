"""Rollback-only successor tests. Production SQL is unchanged on disk.

Positive grant cases map ONLY the two hardcoded copy hashes to the two synthetic
fixture strings. This does not prove real production copy bytes or freshness;
unchanged production SQL installation and rejection of synthetic copy are tested
separately. Existing candidate/ledger fixture limitations remain explicit.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import threading
import unittest
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
PROPOSAL = ROOT / 'supabase/proposals/content_ops_standalone_private_review_recovery.sql'
CONNECT = None  # Set only by the disposable runner after route/version validation.
W = '5cc143cc-b253-4841-8a6c-ab3ac98623a6'
OLD_Q, OLD_V = '94f31aa1-34dd-4220-9c88-34716e760c47', '43e68255-9797-451d-909f-0dce460a6c18'
Q, V = 'e82406d9-dc09-4367-883c-95dc2f6d47b5', '2732080a-91d4-4666-bcdf-c21a60faff17'
I, S, J = '57d6d430-8825-5a40-a4b3-9241eab6f8af', '33b312ba-b80c-4b5e-975a-a8630b6b4601', 'ce42b75b-8938-49a5-a1c2-dbe758e96544'
A, R, TOKEN = '9eb5b931-d6dc-44e3-9404-8571f0602a02', 'ae9a9a51-f1c3-45b4-a027-c495cf68aab2', 'f61db013-73ef-4574-ab78-71924fd51dba'
TRIAL = '7b27e277-0641-4aef-b739-761f86547287'
ORIGINAL_PLAN = '247339a8b6064f3a82589e3d01f7b88275fcc300be9d4b28ffbe826a62271642'
ORIGINAL_RELEASE = 'cb6ac7ff9c0669da277068148a0dc1da24f8c39c'
PNG = '1427fd53385787ebcfddd7131a539ee2963e20f9f979a4e881c8c19aa1059544'
TG = 'b98236484311660575a046b72cef6cc8ba7850e3690512536b10b3c50f83b2fe'
X = '81229be211e10dd035f46d2102fb4371727a2cac0a60c5b43162caac83c67691'
SHA, HEX = '1' * 40, 'a' * 64
digest = lambda value: hashlib.sha256(value.encode()).hexdigest()


def connect():
    assert CONNECT and CONNECT['dbname'].startswith('coineasy_review_recovery_test_')
    return psycopg.connect(**CONNECT)


def source(fixture_copies=False):
    value = PROPOSAL.read_text()
    assert value.count('\nbegin;\n') == 1 and value.endswith('commit;\n')
    value = value.replace('\nbegin;\n', '\n', 1).removesuffix('commit;\n')
    if fixture_copies:
        assert value.count(TG) == value.count(X) == 1
        value = value.replace(TG, digest('fixed telegram')).replace(X, digest('fixed x'))
    return value


def seed(cur):
    now = datetime.now(timezone.utc)
    for q, v, claim in ((OLD_Q, OLD_V, 'c3a59f3e-7a8b-46a4-9847-d0df487271fa'),
                        (Q, V, '64bcd8e2-e3d1-4096-8ff2-9b9212082f78')):
        cur.execute('insert into private.content_ops_review_outbox '
            '(outbox_id,workspace_id,client_id,kst_date,destination_role,content_item_id,content_version_id,'
            'source_item_id,generate_job_id,banner_asset_id,banner_sha256,source_url,source_published_at,'
            'status,claim_token,claimed_at,lease_expires_at,created_at) '
            "values(%s,%s,'babylon','2026-10-17','content_ops_private',%s,%s,%s,%s,%s,%s,"
            "'https://x.com/babylonlabs_io/status/123',%s,'claimed',%s,%s,%s,%s)",
            (q,W,I,v,S,J,A,PNG,now-timedelta(hours=1),claim,now-timedelta(minutes=5),now-timedelta(minutes=1),now-timedelta(minutes=5)))
    cur.execute('insert into private.ybo_standalone_babylon_trial_anchor values(%s,%s,%s,%s,'
        "'2026-10-16T20:23:58Z','2026-10-16T22:23:58Z','2026-10-17')",(W,TRIAL,ORIGINAL_PLAN,ORIGINAL_RELEASE))


def plan(cur, **overrides):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    row_sha = cur.execute('select private.content_ops_recovery_json_sha256(to_jsonb(q)) '
        'from private.content_ops_review_outbox q where outbox_id=%s',(Q,)).fetchone()[0]
    return dict(schema_version='private-review-recovery@1',recovery_id=R,workspace_id=W,client_id='babylon',
        outbox_id=Q,content_item_id=I,content_version_id=V,source_item_id=S,generate_job_id=J,banner_asset_id=A,
        banner_sha256=PNG,telegram_copy_sha256=digest('fixed telegram'),x_copy_sha256=digest('fixed x'),
        original_outbox_sha256=row_sha,original_trial_id=TRIAL,original_plan_sha256=ORIGINAL_PLAN,
        destination_policy_sha256=HEX,release_sha=SHA,not_before=(now-timedelta(seconds=2)).isoformat().replace('+00:00','Z'),
        deadline=(now+timedelta(minutes=10)).isoformat().replace('+00:00','Z'),estimated_daily_limit_usd_micros=10000000,**overrides)


def grant(cur, p):
    hashed = cur.execute('select private.content_ops_recovery_json_sha256(%s::jsonb)',(Jsonb(p),)).fetchone()[0]
    cur.execute('insert into private.content_ops_review_recovery_grants '
        '(recovery_id,workspace_id,outbox_id,content_version_id,plan,plan_sha256,release_sha,original_outbox_sha256,not_before,deadline) '
        'values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (p['recovery_id'],W,p['outbox_id'],p['content_version_id'],Jsonb(p),hashed,p['release_sha'],p['original_outbox_sha256'],p['not_before'],p['deadline']))
    return hashed


def step(cur, hashed, action, args=None):
    return cur.execute('select public.content_ops_recovery_step(%s,%s,%s,%s,%s,%s,%s)',
        (W,V,R,hashed,SHA,action,Jsonb(args or {}))).fetchone()[0]


class SuccessorTests(unittest.TestCase):
    def setUp(self):
        self.db = connect()
        self.cur = self.db.cursor()
        self.cur.execute("set local request.jwt.claim.role='service_role'")
        seed(self.cur)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def install(self, fixture_copies=False):
        self.cur.execute(source(fixture_copies))

    def test_unchanged_production_sql_installs_empty_off_and_rejects_synthetic_copy(self):
        self.install()
        self.assertEqual(self.cur.execute('select count(*) from private.content_ops_review_recovery_grants').fetchone()[0],0)
        self.assertFalse(self.cur.execute("select has_table_privilege('service_role','private.content_ops_review_recovery_grants','INSERT')").fetchone()[0])
        with self.assertRaisesRegex(psycopg.Error,'standalone_scope_invalid'):
            with self.db.transaction(): grant(self.cur,plan(self.cur))

    def test_fixture_copy_mapping_admits_one_exact_grant_claim_once_and_preserves_rows(self):
        self.install(True)
        before = self.cur.execute('select jsonb_agg(to_jsonb(q) order by outbox_id) from private.content_ops_review_outbox q').fetchone()[0]
        hashed = grant(self.cur,plan(self.cur))
        self.assertEqual(step(self.cur,hashed,'status')['plan_sha256'],hashed)
        self.assertEqual(step(self.cur,hashed,'claim',{'claim_token':TOKEN})['outbox_id'],Q)
        self.assertIsNone(step(self.cur,hashed,'claim',{'claim_token':TOKEN}))
        after = self.cur.execute('select jsonb_agg(to_jsonb(q) order by outbox_id) from private.content_ops_review_outbox q').fetchone()[0]
        self.assertEqual(before,after)

    def test_old_exact_branch_still_admitted(self):
        self.install()
        self.cur.execute('insert into private.ybo_readiness_babylon_trial_anchor values(%s,%s,%s,%s,clock_timestamp(),'
            "clock_timestamp()+interval '1 hour','2026-10-16')",(W,'c34dbb8a-ff7c-484e-8e43-757f692ec743',HEX,SHA))
        p=plan(self.cur);p.update(outbox_id=OLD_Q,content_version_id=OLD_V,original_trial_id='c34dbb8a-ff7c-484e-8e43-757f692ec743',original_plan_sha256=HEX)
        p['original_outbox_sha256']=self.cur.execute('select private.content_ops_recovery_json_sha256(to_jsonb(q)) from private.content_ops_review_outbox q where outbox_id=%s',(OLD_Q,)).fetchone()[0]
        grant(self.cur,p)

    def test_exact_tuple_constraint_rejects_cross_pairs_and_unlisted_pairs(self):
        self.install(True)
        # Disable only the fixture trigger to isolate CHECK rejection, not another gate.
        self.cur.execute('alter table private.content_ops_review_recovery_grants disable trigger content_ops_review_recovery_grant_checked')
        for q,v in ((OLD_Q,V),(Q,OLD_V),(Q,'00000000-1111-4111-8111-111111111111')):
            with self.subTest(q=q,v=v),self.assertRaisesRegex(psycopg.Error,'content_ops_recovery_exact_tuple_check'):
                with self.db.transaction():
                    p=plan(self.cur);p.update(outbox_id=q,content_version_id=v);grant(self.cur,p)

    def test_every_new_pin_and_anchor_drift_fails(self):
        self.install(True)
        for key in ('content_item_id','source_item_id','generate_job_id','banner_sha256','telegram_copy_sha256','x_copy_sha256','original_trial_id','original_plan_sha256'):
            with self.subTest(key=key),self.assertRaisesRegex(psycopg.Error,'standalone_scope_invalid'):
                with self.db.transaction():
                    p=plan(self.cur);p[key]='0'*64 if key.endswith('sha256') else '00000000-1111-4111-8111-111111111111';grant(self.cur,p)
        self.cur.execute("update private.ybo_standalone_babylon_trial_anchor set release_sha=repeat('2',40)")
        with self.assertRaisesRegex(psycopg.Error,'standalone_scope_invalid'):
            with self.db.transaction(): grant(self.cur,plan(self.cur))

    def test_no_send_preflight_refuses_any_consumption(self):
        for column,value in (('packet_sha256',HEX),('send_started_at',datetime.now(timezone.utc)),('message_id',1),('finished_at',datetime.now(timezone.utc))):
            with self.subTest(column=column),self.assertRaisesRegex(psycopg.Error,'no_send_preflight_failed'):
                with self.db.transaction():
                    self.cur.execute(f'update private.content_ops_review_outbox set {column}=%s where outbox_id=%s',(value,Q));self.install()

    def test_preimage_and_acl_drift_rejected_atomically(self):
        for command in ("create or replace function private.content_ops_recovery_json_sha256(jsonb) returns text language sql immutable strict set search_path='' as $$select repeat('a',64)$$",
                        'grant select on private.content_ops_review_recovery_grants to service_role'):
            with self.subTest(command=command),self.assertRaises(psycopg.Error):
                with self.db.transaction(): self.cur.execute(command);self.install()
        self.install()

    def test_constraint_names_discovered_not_assumed_and_replay_rejected(self):
        rows=self.cur.execute("select conname from pg_constraint where conrelid='private.content_ops_review_recovery_grants'::regclass and pg_get_constraintdef(oid) like 'CHECK ((outbox_id = %'").fetchall()
        self.assertEqual(len(rows),1)
        from psycopg import sql
        self.cur.execute(sql.SQL('alter table private.content_ops_review_recovery_grants rename constraint {} to fixture_renamed_outbox').format(sql.Identifier(rows[0][0])))
        self.install()
        self.cur.execute('drop table recovery_successor_expected,recovery_successor_before,recovery_successor_guard_hash')
        with self.assertRaisesRegex(psycopg.Error,'recovery_successor_preimage_mismatch'):
            with self.db.transaction(): self.install()

    def test_grant_guard_rechecks_current_candidate_original_row_and_append_only(self):
        self.install(True)
        for change in ("update private.fixture_candidate_switch set enabled=false",
                       "update private.content_ops_review_outbox set lease_expires_at=clock_timestamp()+interval '1 hour' where outbox_id='"+Q+"'"):
            with self.subTest(change=change),self.assertRaises(psycopg.Error):
                with self.db.transaction(): self.cur.execute(change);grant(self.cur,plan(self.cur))
        hashed=grant(self.cur,plan(self.cur))
        for change in ('update private.content_ops_review_recovery_grants set release_sha=release_sha',
                       'delete from private.content_ops_review_recovery_grants',
                       'truncate private.content_ops_review_recovery_grants cascade'):
            with self.subTest(change=change),self.assertRaises(psycopg.Error):
                with self.db.transaction(): self.cur.execute(change)
        self.assertIsNotNone(step(self.cur,hashed,'status'))

    def test_global_single_grant_limit_preserved(self):
        self.install(True);grant(self.cur,plan(self.cur))
        with self.assertRaisesRegex(psycopg.Error,'grant_invalid'):
            with self.db.transaction():
                p=plan(self.cur);p['recovery_id']='00000000-1111-4111-8111-111111111111';grant(self.cur,p)

    def test_zz_concurrent_new_claim_has_one_winner(self):
        self.install(True)
        p=plan(self.cur);hashed=grant(self.cur,p)
        self.db.commit()  # Final case; the runner drops this isolated database.
        barrier=threading.Barrier(2)
        def claim_once(token):
            with connect() as db:
                cur=db.cursor();cur.execute("set local request.jwt.claim.role='service_role'")
                cur.execute("set local statement_timeout='5s'")
                barrier.wait(timeout=5)
                return step(cur,hashed,'claim',{'claim_token':token})
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(claim_once,[TOKEN,'00000000-1111-4111-8111-111111111111']))
        self.assertEqual(sum(value is not None for value in outcomes),1)
        self.assertEqual(self.cur.execute('select count(*) from private.content_ops_review_recovery_claims').fetchone()[0],1)
        self.assertEqual(self.cur.execute('select private.content_ops_recovery_json_sha256(to_jsonb(q)) '
            'from private.content_ops_review_outbox q where outbox_id=%s',(Q,)).fetchone()[0],p['original_outbox_sha256'])


if __name__ == '__main__':
    raise SystemExit('Use run_local.py with an explicit disposable mode')
