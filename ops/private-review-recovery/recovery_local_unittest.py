"""Explicit disposable-PG recovery proof, excluded from normal pytest discovery.

Use run_local.py --local-only, which prepares a fresh disposable database.
Every test mutation is rolled back; the installed proposal remains grant-empty.
"""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import time
import threading
import unittest
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


WORKSPACE = UUID('5cc143cc-b253-4841-8a6c-ab3ac98623a6')
OUTBOX = UUID('94f31aa1-34dd-4220-9c88-34716e760c47')
VERSION = UUID('43e68255-9797-451d-909f-0dce460a6c18')
TRIAL = UUID('c34dbb8a-ff7c-484e-8e43-757f692ec743')
RECOVERY = UUID('ae9a9a51-f1c3-45b4-a027-c495cf68aab2')
ITEM = UUID('5bf86d0b-cd8d-4683-827c-a34531ee9305')
SOURCE = UUID('5b7209a5-20bc-4976-891d-d66defa3c1d7')
JOB = UUID('22f0f020-f69e-43a5-862f-642b2461cadd')
ASSET = UUID('9eb5b931-d6dc-44e3-9404-8571f0602a02')
CLAIM = UUID('f61db013-73ef-4574-ab78-71924fd51dba')
REVIEW = UUID('16a74ba6-5c75-4c22-ae75-31a16920a920')
CARD = UUID('79e84471-6288-41fd-b6ad-a149341b49e9')
RELEASE = '1' * 40
HEX = 'a' * 64


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def connection():
    local = os.environ.get('PGHOST') == '/private/tmp'
    ci = os.environ.get('PGHOST') == '127.0.0.1' and os.environ.get('GITHUB_ACTIONS') == 'true'
    if not (local or ci) or os.environ.get('PGPORT') != '5432':
        raise RuntimeError('disposable local socket required')
    database = os.environ.get('PGDATABASE', '')
    if not database.startswith('coineasy_review_recovery_test_') or not database.removeprefix('coineasy_review_recovery_test_').isalnum():
        raise RuntimeError('exact disposable database required')
    if os.environ.get('PGSERVICE') or os.environ.get('PGHOSTADDR'):
        raise RuntimeError('alternate database route forbidden')
    return psycopg.connect(host=os.environ['PGHOST'],port=5432,
        dbname=database,user=os.environ['PGUSER'])


def seed(cur, *, deadline_seconds=120, lease_seconds=-60, wrong_copy=False):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    not_before, deadline = now - timedelta(seconds=2), now + timedelta(seconds=deadline_seconds)
    cur.execute("insert into private.ybo_readiness_babylon_trial_anchor "
        "(workspace_id,trial_id,plan_sha256,release_sha,not_before,deadline,kst_date) "
        "values(%s,%s,%s,%s,%s,%s,'2026-10-16')",
        (WORKSPACE,TRIAL,HEX,RELEASE,not_before,deadline))
    cur.execute("insert into private.content_ops_review_outbox "
        "(outbox_id,workspace_id,client_id,kst_date,destination_role,content_item_id,"
        "content_version_id,source_item_id,generate_job_id,banner_asset_id,banner_sha256,"
        "source_url,source_published_at,status,claim_token,claimed_at,lease_expires_at,created_at) "
        "values(%s,%s,'babylon','2026-10-16','content_ops_private',%s,%s,%s,%s,%s,%s,"
        "'https://x.com/Coiniseasy/status/123456789',%s,'claimed',%s,%s,%s,%s)",
        (OUTBOX,WORKSPACE,ITEM,VERSION,SOURCE,JOB,ASSET,HEX,
         now-timedelta(hours=1),UUID('c3a59f3e-7a8b-46a4-9847-d0df487271fa'),
         now-timedelta(hours=1),now+timedelta(seconds=lease_seconds),now-timedelta(hours=1)))
    cur.execute("insert into public.assets values(%s,%s,%s,%s,'png','image/png',"
        "'content-studio',%s,%s,%s,1000,100,100)",
        (ASSET,WORKSPACE,ITEM,VERSION,Jsonb({'filename':'news-card.png'}),
         f'{WORKSPACE}/babylon/{ASSET}/news-card.png',HEX))
    cur.execute("insert into storage.objects values('content-studio',%s)",
        (f'{WORKSPACE}/babylon/{ASSET}/news-card.png',))
    original = cur.execute("select private.content_ops_recovery_json_sha256(to_jsonb(q)) "
        "from private.content_ops_review_outbox q where outbox_id=%s",(OUTBOX,)).fetchone()[0]
    plan = dict(schema_version='private-review-recovery@1', recovery_id=str(RECOVERY),
        workspace_id=str(WORKSPACE),client_id='babylon',outbox_id=str(OUTBOX),
        content_item_id=str(ITEM),content_version_id=str(VERSION),source_item_id=str(SOURCE),
        generate_job_id=str(JOB),banner_asset_id=str(ASSET),banner_sha256=HEX,
        telegram_copy_sha256=(HEX if wrong_copy else digest('fixed telegram')),
        x_copy_sha256=digest('fixed x'),
        original_outbox_sha256=original,original_trial_id=str(TRIAL),
        original_plan_sha256=HEX,destination_policy_sha256=HEX,release_sha=RELEASE,
        not_before=not_before.isoformat().replace('+00:00','Z'),
        deadline=deadline.isoformat().replace('+00:00','Z'),
        estimated_daily_limit_usd_micros=10000000)
    plan_sha = cur.execute("select private.content_ops_recovery_json_sha256(%s::jsonb)",(Jsonb(plan),)).fetchone()[0]
    cur.execute("insert into private.content_ops_review_recovery_grants "
        "(recovery_id,workspace_id,outbox_id,content_version_id,plan,plan_sha256,"
        "release_sha,original_outbox_sha256,not_before,deadline) "
        "values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (RECOVERY,WORKSPACE,OUTBOX,VERSION,Jsonb(plan),plan_sha,RELEASE,
         original,not_before,deadline))
    return plan_sha,original


def step(cur, plan_sha, action, args=None):
    return cur.execute("select public.content_ops_recovery_step(%s,%s,%s,%s,%s,%s,%s)",
        (WORKSPACE,VERSION,RECOVERY,plan_sha,RELEASE,action,Jsonb(args or {}))).fetchone()[0]


class RecoveryLocalTests(unittest.TestCase):
    def setUp(self):
        self.db = connection()
        self.cur = self.db.cursor()
        self.cur.execute("set local request.jwt.claim.role='service_role'")

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_empty_off_and_acl(self):
        self.assertIsNone(step(self.cur,HEX,'status'))
        self.assertEqual(self.cur.execute("select count(*) from private.content_ops_review_recovery_grants").fetchone()[0],0)
        self.assertFalse(self.cur.execute("select has_table_privilege('service_role',"
            "'private.content_ops_review_recovery_grants','INSERT')").fetchone()[0])
        self.assertTrue(self.cur.execute("select has_function_privilege('service_role',"
            "'public.content_ops_recovery_step(uuid,uuid,uuid,text,text,text,jsonb)','EXECUTE')").fetchone()[0])

    def test_canonical_json_matches_python_and_does_not_require_agent_schema(self):
        value = {'z': None, 'a': {'integer': 10000000, 'text': 'ascii'}, 'array': [False, 2, 'x']}
        expected = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        actual = self.cur.execute('select private.content_ops_recovery_json_sha256(%s::jsonb)', (Jsonb(value),)).fetchone()[0]
        self.assertEqual(actual, expected)
        self.assertIsNone(self.cur.execute("select to_regprocedure('private.agent_json_sha256(jsonb)')").fetchone()[0])

    def test_zz_concurrent_claim_has_one_winner_and_no_old_row_update(self):
        # Last test in a freshly created disposable database, dropped by runner.
        plan_sha, original = seed(self.cur)
        self.db.commit()
        barrier = threading.Barrier(2)
        def claim_once(token):
            with connection() as db:
                cur = db.cursor()
                cur.execute("set local request.jwt.claim.role='service_role'")
                cur.execute("set local statement_timeout='5s'")
                barrier.wait(timeout=5)
                return step(cur, plan_sha, 'claim', {'claim_token': token})
        tokens = [str(CLAIM), '64bcd8e2-e3d1-4096-8ff2-9b9212082f78']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim_once, tokens))
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertEqual(self.cur.execute('select count(*) from private.content_ops_review_recovery_claims').fetchone()[0], 1)
        self.assertEqual(self.cur.execute('select private.content_ops_recovery_json_sha256(to_jsonb(q)) from private.content_ops_review_outbox q where outbox_id=%s', (OUTBOX,)).fetchone()[0], original)

    def test_exact_claim_replay_original_preserved_and_current_candidate(self):
        plan_sha,original=seed(self.cur)
        self.assertEqual(step(self.cur,plan_sha,'status')['plan_sha256'],plan_sha)
        self.assertEqual(step(self.cur,plan_sha,'reconcile'),1)
        claimed=step(self.cur,plan_sha,'claim',{'claim_token':str(CLAIM)})
        self.assertEqual(claimed['outbox_id'],str(OUTBOX))
        self.assertIsNone(step(self.cur,plan_sha,'claim',{'claim_token':str(CLAIM)}))
        self.assertEqual(step(self.cur,plan_sha,'reconcile'),0)
        locator=step(self.cur,plan_sha,'image',{'outbox_id':str(OUTBOX),'claim_token':str(CLAIM)})
        self.assertEqual(locator['status'],'ready')
        self.assertEqual(locator['byte_size'],1000)
        self.assertEqual(self.cur.execute("select private.content_ops_recovery_json_sha256(to_jsonb(q)) "
            "from private.content_ops_review_outbox q where outbox_id=%s",(OUTBOX,)).fetchone()[0],original)
        self.assertEqual(self.cur.execute("select status from private.content_ops_review_outbox "
            "where outbox_id=%s",(OUTBOX,)).fetchone()[0],'claimed')
        self.cur.execute("update private.fixture_candidate_switch set enabled=false")
        self.assertIsNone(step(self.cur,plan_sha,'status'))
        self.assertEqual(step(self.cur,plan_sha,'reconcile'),0)

    def test_prepared_begin_bound_and_original_drift_denied(self):
        plan_sha,_=seed(self.cur)
        step(self.cur,plan_sha,'claim',{'claim_token':str(CLAIM)})
        prepared=step(self.cur,plan_sha,'prepare',{'outbox_id':str(OUTBOX),
            'claim_token':str(CLAIM),'review_id':str(REVIEW)})
        self.assertEqual(prepared['status'],'review_prepared')
        begun=step(self.cur,plan_sha,'begin',{'outbox_id':str(OUTBOX),
            'claim_token':str(CLAIM),'packet_sha256':HEX})
        self.assertTrue(begun['accepted'])
        self.assertFalse(step(self.cur,plan_sha,'begin',{'outbox_id':str(OUTBOX),
            'claim_token':str(CLAIM),'packet_sha256':HEX})['accepted'])
        self.assertEqual(step(self.cur,plan_sha,'bind',{'review_id':str(REVIEW),
            'outbox_id':str(OUTBOX),'claim_token':str(CLAIM),'packet_sha256':HEX})['status'],'bound')
        self.assertTrue(self.cur.execute("select private.content_ops_button_card_outbox_owned(%s)",
            (REVIEW,)).fetchone()[0])
        self.cur.execute("update private.content_ops_review_outbox set source_url="
            "'https://x.com/Coiniseasy/status/999' where outbox_id=%s",(OUTBOX,))
        self.assertFalse(self.cur.execute("select private.content_ops_button_card_outbox_owned(%s)",
            (REVIEW,)).fetchone()[0])
        self.assertIsNone(step(self.cur,plan_sha,'status'))

    def test_actual_four_part_ledger_first_reservation_only(self):
        # Replace only local fixture stubs with the byte-identical current
        # reserve/confirm function bodies, inside this rolled-back test.
        ledger=(Path(__file__).resolve().parents[2] /
            'supabase/proposals/content_ops_button_card_send_ledger.sql').read_text()
        self.cur.execute("alter table private.content_ops_button_card_send_attempts "
            "alter column state set default 'reserved', "
            "alter column reserved_at set default clock_timestamp()")
        for name in ('reserve_content_ops_button_card_send',
                     'confirm_content_ops_button_card_send'):
            start=ledger.index(f'create function private.{name}(')
            end=ledger.index('end $$;',start)+len('end $$;')
            self.cur.execute(ledger[start:end].replace('create function',
                'create or replace function',1))
        plan_sha,_=seed(self.cur)
        step(self.cur,plan_sha,'claim',{'claim_token':str(CLAIM)})
        step(self.cur,plan_sha,'prepare',{'outbox_id':str(OUTBOX),
            'claim_token':str(CLAIM),'review_id':str(REVIEW)})
        step(self.cur,plan_sha,'begin',{'outbox_id':str(OUTBOX),
            'claim_token':str(CLAIM),'packet_sha256':HEX})
        step(self.cur,plan_sha,'bind',{'review_id':str(REVIEW),
            'outbox_id':str(OUTBOX),'claim_token':str(CLAIM),'packet_sha256':HEX})
        reservation={'review_id':str(REVIEW),'card_id':str(CARD),
            'part_index':0,'payload_sha256':HEX}
        self.assertTrue(step(self.cur,plan_sha,'reserve',reservation)['new_attempt'])
        self.assertFalse(step(self.cur,plan_sha,'reserve',reservation)['new_attempt'])
        self.assertEqual(self.cur.execute("select count(*) from "
            "private.content_ops_button_card_send_attempts").fetchone()[0],1)
        confirmation={**reservation,'message_id':1,'message_binding':'b'*64,
            'response_sha256':'c'*64,
            'observed_at':datetime.now(timezone.utc).isoformat()}
        self.assertTrue(step(self.cur,plan_sha,'confirm',confirmation)['new_confirmation'])
        self.assertFalse(step(self.cur,plan_sha,'confirm',confirmation)['new_confirmation'])
        self.cur.execute("update private.fixture_candidate_switch set enabled=false")
        with self.assertRaises(psycopg.Error):
            with self.db.transaction():
                step(self.cur,plan_sha,'reserve',{**reservation,'part_index':1})
        self.assertEqual(self.cur.execute("select status from private.content_ops_review_outbox "
            "where outbox_id=%s",(OUTBOX,)).fetchone()[0],'claimed')

    def test_terminal_not_confirmed_after_candidate_loss(self):
        plan_sha,_=seed(self.cur)
        self.cur.execute("update private.fixture_candidate_switch set enabled=false")
        receipt=step(self.cur,plan_sha,'terminal',{'review_id':str(REVIEW),
            'card_id':str(CARD),'outbox_id':str(OUTBOX)})
        self.assertEqual(receipt['status'],'not_confirmed')
        self.assertFalse(receipt['execution_authorized'])

    def test_grant_rejects_stale_or_mutated_original(self):
        with self.assertRaises(psycopg.Error):
            seed(self.cur,lease_seconds=60)
        self.db.rollback()
        self.assertEqual(self.cur.execute("select count(*) from private.content_ops_review_recovery_grants").fetchone()[0],0)

    def test_grant_rejects_copy_drift(self):
        with self.assertRaises(psycopg.Error):
            seed(self.cur,wrong_copy=True)
        self.db.rollback()
        self.assertEqual(self.cur.execute("select count(*) from private.content_ops_review_recovery_grants").fetchone()[0],0)

    def test_immutable_grant_and_unauthenticated_rpc(self):
        plan_sha,_=seed(self.cur)
        with self.assertRaises(psycopg.Error):
            with self.db.transaction():
                self.cur.execute("update private.content_ops_review_recovery_grants "
                    "set release_sha=%s where recovery_id=%s",('2'*40,RECOVERY))
        self.cur.execute("set local request.jwt.claim.role='anon'")
        with self.assertRaises(psycopg.Error):
            with self.db.transaction():
                step(self.cur,plan_sha,'status')

    def test_terminal_read_after_deadline_and_candidate_loss(self):
        plan_sha,_=seed(self.cur,deadline_seconds=2)
        self.cur.execute("insert into private.content_ops_review_recovery_claims values(%s,%s,clock_timestamp())",
            (RECOVERY,CLAIM))
        self.cur.execute("insert into private.content_ops_button_reviews "
            "(id,workspace_id,client_id,content_item_id,content_version_id,version_fingerprint) "
            "values(%s,%s,'babylon',%s,%s,%s)",(REVIEW,WORKSPACE,ITEM,VERSION,'f'*64))
        self.cur.execute("insert into private.content_ops_review_recovery_prepared "
            "values(%s,%s,clock_timestamp())",(RECOVERY,REVIEW))
        self.cur.execute("insert into private.content_ops_review_recovery_begins "
            "values(%s,%s,clock_timestamp())",(RECOVERY,HEX))
        self.cur.execute("insert into private.content_ops_button_cards values(%s,%s,%s,%s)",
            (CARD,REVIEW,Jsonb({'message':'b'*64}),Jsonb([])))
        for index in range(4):
            self.cur.execute("insert into private.content_ops_button_card_send_attempts "
                "(review_id,card_id,part_index,payload_sha256,state,message_id,message_binding,"
                "response_sha256,reserved_at,confirmed_at) "
                "values(%s,%s,%s,%s,'confirmed',%s,%s,%s,clock_timestamp(),clock_timestamp())",
                (REVIEW,CARD,index,HEX,index+1,('b'*64 if index==3 else chr(99+index)*64),HEX))
        self.cur.execute("insert into private.content_ops_review_recovery_terminals "
            "values(%s,%s,%s,clock_timestamp())",(RECOVERY,REVIEW,CARD))
        self.cur.execute("update private.fixture_candidate_switch set enabled=false")
        time.sleep(2.2)
        self.assertIsNone(step(self.cur,plan_sha,'status'))
        receipt=step(self.cur,plan_sha,'terminal',{'review_id':str(REVIEW),
            'card_id':str(CARD),'outbox_id':str(OUTBOX)})
        self.assertEqual(receipt['status'],'recovery_sent')
        self.assertFalse(receipt['execution_authorized'])
        self.assertEqual(self.cur.execute("select status from private.content_ops_review_outbox "
            "where outbox_id=%s",(OUTBOX,)).fetchone()[0],'claimed')

    def test_four_confirmed_parts_register_without_old_outbox_update(self):
        # The fixture card writer is structural; the real four-part ledger is
        # exercised independently above. This checks the recovery-only atomic
        # terminal insertion and preservation of the original outbox.
        self.cur.execute("""create or replace function private.record_content_ops_button_card(
            uuid,uuid,text,bigint,jsonb,jsonb,timestamptz,timestamptz)
            returns jsonb language plpgsql volatile as $$
            begin
                insert into private.content_ops_button_cards(id,review_id,bindings,parts)
                    values($2,$1,$5,$6);
                return jsonb_build_object('status','card_recorded','card_id',$2,
                    'reused',false,'execution_authorized',false);
            end $$""")
        plan_sha,original=seed(self.cur)
        step(self.cur,plan_sha,'claim',{'claim_token':str(CLAIM)})
        step(self.cur,plan_sha,'prepare',{'outbox_id':str(OUTBOX),
            'claim_token':str(CLAIM),'review_id':str(REVIEW)})
        step(self.cur,plan_sha,'begin',{'outbox_id':str(OUTBOX),
            'claim_token':str(CLAIM),'packet_sha256':HEX})
        step(self.cur,plan_sha,'bind',{'review_id':str(REVIEW),
            'outbox_id':str(OUTBOX),'claim_token':str(CLAIM),'packet_sha256':HEX})
        parts=[]
        for index in range(4):
            binding=chr(98+index)*64
            self.cur.execute("insert into private.content_ops_button_card_send_attempts "
                "(review_id,card_id,part_index,payload_sha256,state,message_id,message_binding,"
                "response_sha256,reserved_at,confirmed_at) "
                "values(%s,%s,%s,%s,'confirmed',%s,%s,%s,clock_timestamp(),clock_timestamp())",
                (REVIEW,CARD,index,HEX,index+1,binding,HEX))
            if index<3:
                parts.append({'payload_sha256':HEX,'message_binding':binding})
        observed=datetime.now(timezone.utc)
        args={'review_id':str(REVIEW),'card_id':str(CARD),
            'expected_fingerprint':'f'*64,'epoch':0,
            'bindings':{'message':'e'*64},'parts':parts,
            'controls_payload_sha256':HEX,'response_sha256s':[HEX]*4,
            'delivered':observed.isoformat(),
            'expires':(observed+timedelta(minutes=5)).isoformat()}
        self.assertEqual(step(self.cur,plan_sha,'register',args)['status'],'card_recorded')
        self.assertEqual(step(self.cur,plan_sha,'terminal',{'review_id':str(REVIEW),
            'card_id':str(CARD),'outbox_id':str(OUTBOX)})['status'],'recovery_sent')
        self.assertEqual(self.cur.execute("select private.content_ops_recovery_json_sha256(to_jsonb(q)) "
            "from private.content_ops_review_outbox q where outbox_id=%s",(OUTBOX,)).fetchone()[0],original)


if __name__ == '__main__':
    unittest.main()
