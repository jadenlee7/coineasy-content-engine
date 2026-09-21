"""Real-driver integration on the harness's disposable full-schema DB only.

Requires psycopg 3 in a separate test environment. Never accepts a DSN, URL,
database name, remote host or production role. No real identities or providers.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.content_ops.review_edit_ingress import (  # noqa: E402
    EditBindings, PostgresEditReplyOwner, handle_edit_reply_webhook,
)
from core.content_ops.review_ingress import IngressPolicy, ReviewIngressError  # noqa: E402
from core.content_ops.prompt_receipt import PromptReceiptError  # noqa: E402
from core.content_ops.prompt_receipt_owner import PostgresPromptReceiptOwner  # noqa: E402
from core.content_ops.prompt_attempt import prompt_instruction  # noqa: E402
from core.content_ops.prompt_reservation import card_parent_binding  # noqa: E402
from core.content_ops.review_cancellation import (  # noqa: E402
    CancellationError, CancellationSigner, PostgresCardCancellationOwner,
    cancellation_target, handle_cancellation_webhook,
)
from core.content_ops.cancellation_markup import (  # noqa: E402
    CancellationMarkupError, prepare_cancellation_markup, cancellation_markup_request,
    validate_cancellation_markup_response,
)
from core.content_ops.cancellation_markup_owner import PostgresCancellationMarkupLedger  # noqa: E402
from core.content_ops.cancellation_markup_guard import PostgresCancellationMarkupGuard  # noqa: E402
from core.content_ops.cancellation_markup_courier import run_cancellation_markup_once  # noqa: E402
from core.content_ops.cancellation_markup_authority import ExactCancellationMarkupAuthority, MarkupExecutionApproval  # noqa: E402
from core.content_ops.cancellation_markup_authority_reader import PostgresMarkupAuthorityReader  # noqa: E402
from core.content_ops.cancellation_markup_approval_owner import PostgresMarkupApprovalOwner  # noqa: E402
from core.content_ops.cancellation_control_receipt_owner import (  # noqa: E402
    PostgresOriginalControlReceiptOwner, StoredOriginalControlReceipt,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--local-socket', required=True)
    parser.add_argument('--phase', choices=('initial', 'callbacks', 'banner', 'cancellation', 'guard', 'authority', 'confirmation', 'restart'), required=True)
    args = parser.parse_args()
    path = Path(args.local_socket)
    if (not re.fullmatch(r'/private/tmp/coineasy-button-review-[A-Za-z0-9]+', str(path))
            or path.resolve() != path or not (path / 'PG_VERSION').is_file()
            or not stat.S_ISSOCK((path / '.s.PGSQL.65439').stat().st_mode)
            or any(k.startswith('PG') or k == 'DATABASE_URL' for k in os.environ)):
        raise RuntimeError('disposable local socket required')
    import psycopg
    from psycopg.types.json import Jsonb
    print(json.dumps({'driverPhase':args.phase,'phaseStarted':True}),flush=True)

    def connect():
        return psycopg.connect(host=str(path), port=65439, user='postgres', password='',
            dbname='postgres', connect_timeout=5, sslmode='disable',
            options='-c statement_timeout=10000 -c lock_timeout=5000')

    def read(sql, params=()):
        with connect() as c:
            return c.execute(sql, params).fetchone()[0]

    binding = EditBindings(b'local-fixture-key-no-live-registration-1234')
    cancellation_signer = CancellationSigner(b'local-only-fixture-cancellation-signing-key')
    bot, room, human = 101, -(10**12 + 7), 201

    class CancellationConnection:
        """Count the actual owner's five parameterized DB statements only."""
        def __init__(self, statements):
            self.real = connect()
            self.statements = statements
        @property
        def autocommit(self): return self.real.autocommit
        def __enter__(self): self.real.__enter__(); return self
        def __exit__(self, *args): return self.real.__exit__(*args)
        def cursor(self):
            real, statements = self.real.cursor(), self.statements
            class CountedCursor:
                def __enter__(self): real.__enter__(); return self
                def __exit__(self, *args): return real.__exit__(*args)
                def execute(self, statement, params=()):
                    statements.append(statement)
                    return real.execute(statement,params)
                def fetchone(self): return real.fetchone()
            return CountedCursor()

    def cancellation_fixture(ctx, *, lifetime=60):
        review = read('select to_jsonb(r) from private.content_ops_button_reviews r where id=%s',
                      (ctx['review'],))
        card = card_row(ctx)
        now = int(time.time())
        message_date = int(datetime.fromisoformat(card['delivered_at']).timestamp())
        plan = prepare_cancellation_markup(enabled=True,review=review,card=card,
            bindings=binding,signer=cancellation_signer,bot_id=bot,chat_id=room,
            message_id=ctx['message']+1,thread_id=None,control_text='Synthetic local review',
            message_date=message_date,existing_markup={'inline_keyboard':[]},
            now=now,expires_at=now+lifetime)
        request = cancellation_markup_request(enabled=True,plan=plan,bindings=binding,now=now)
        message = {'message_id':ctx['message']+1,'date':message_date,'edit_date':now,
            'chat':{'id':room,'type':'supergroup'},'from':{'id':bot,'is_bot':True},
            'text':'Synthetic local review','reply_markup':request['body']['reply_markup']}
        receipt = validate_cancellation_markup_response(enabled=True,plan=plan,bindings=binding,
            http_status=200,raw_response=json.dumps({'ok':True,'result':message}).encode(),observed_at=now)
        assert receipt['status']=='markup_response_matched' and receipt['execution_authorized'] is False
        token = message['reply_markup']['inline_keyboard'][-1][0]['callback_data']
        update = {'update_id':8,'callback_query':{'id':'synthetic-cancel-callback',
            'from':{'id':human,'is_bot':False},'data':token,
            'message':message}}
        return {'update':update,'now':now,'expires_at':now+lifetime,'plan':plan,
                'response':json.dumps({'ok':True,'result':message}).encode()}

    def markup_args(ctx, fixture, attempt_id):
        return dict(enabled=True,plan=fixture['plan'],attempt_id=attempt_id,actor_id=ctx['actor'],
                    human_id=human,bindings=binding,signer=cancellation_signer)

    def markup_unknown(operation):
        try:
            operation()
        except CancellationMarkupError as exc:
            assert str(exc)=='cancellation_markup_ledger_unknown'
        else:
            raise AssertionError('expected bounded markup ledger unknown')

    def cancel_callback(ctx, fixture, *, owner=None):
        policy = IngressPolicy('fixture_webhook_'+'z'*32,bot,room,'fixture-room',((human,ctx['actor']),))
        return handle_cancellation_webhook(enabled=True,
            raw_body=json.dumps(fixture['update']).encode(),
            headers=[('Content-Type','application/json'),
                     ('X-Telegram-Bot-Api-Secret-Token',policy.webhook_secret)],
            policy=policy,bindings=binding,signer=cancellation_signer,
            owner=owner or PostgresCardCancellationOwner(connect),now=fixture['now'])

    def cancellation_unknown(ctx, fixture, *, owner=None):
        try:
            cancel_callback(ctx,fixture,owner=owner)
        except CancellationError as exc:
            assert str(exc) == 'review_cancellation_outcome_unknown'
        else:
            raise AssertionError('cancellation must be refused or unknown')

    def deliver(ctx, owner=None, text=None, actor=human, prompt=None):
        now = int(time.time())
        policy = IngressPolicy('fixture_webhook_' + 'z' * 32, bot, room,
                               'fixture-room', ((actor, ctx['actor']),))
        chat = dict(id=room, type='supergroup')
        update = {'update_id': 7, 'message': {
            'message_id': ctx['message'] + 10, 'date': now, 'chat': chat,
            'from': dict(id=actor, is_bot=False),
            'text': text if text is not None else ctx['text'],
            'reply_to_message': {'message_id': prompt or ctx['message'], 'date': now - 1,
                                 'chat': chat, 'from': dict(id=bot, is_bot=True)}}}
        return handle_edit_reply_webhook(enabled=True, raw_body=json.dumps(update).encode(),
            headers=[('Content-Type', 'application/json'),
                     ('X-Telegram-Bot-Api-Secret-Token', policy.webhook_secret)],
            policy=policy, bindings=binding, owner=owner or PostgresEditReplyOwner(connect), now=now)

    def register(ctx, human_id=human):
        return read('select private.register_content_ops_button_edit_prompt(%s,%s)',
                    (ctx['receipt'],binding.digest('human',bot,human_id)))

    def persist_prompt(ctx, owner=None, observed_at=None, human_id=None, attempt_id=None):
        # The fixture stores the ORIGINAL precise observation across retries;
        # retry-time wall clock must never refresh a provider delivery receipt.
        return (owner or PostgresPromptReceiptOwner(connect)).record_prompt_response(
            enabled=True, attempt_id=attempt_id or ctx['attempt'], bindings=binding,
            bot_id=bot, chat_id=room, human_id=human_id or human, thread_id=None,
            http_status=200, raw_response=json.dumps(ctx['provider_response']).encode(),
            observed_at=observed_at or datetime.fromisoformat(ctx['observed_at']),
        )

    def verify_prompt_rows(ctx, expected):
        assert read('select count(*) from private.content_ops_button_prompt_receipts where review_id=%s',
                    (ctx['review'],)) == expected
        assert read('select count(*) from private.content_ops_button_edit_prompts where review_id=%s',
                    (ctx['review'],)) == expected

    def prompt_unknown(ctx, **kwargs):
        try:
            persist_prompt(ctx, **kwargs)
        except PromptReceiptError as exc:
            assert str(exc) == 'prompt_registration_unknown'
        else:
            raise AssertionError('prompt persistence must be refused or unknown')

    def seed(client='squid', channel='x', registered=True, deferred_edit=False):
        assert not (registered and deferred_edit)
        ids = {k: str(uuid4()) for k in ('workspace', 'actor', 'item', 'version', 'review', 'prompt', 'source', 'asset')}
        ids.update(client=client, channel=channel, text="검수용 수정 문안 ' quoted ; -- 😀")
        with connect() as c:
            ids['message'] = c.execute("select nextval('private.test_button_driver_messages')").fetchone()[0]
            w, a, i, v = (ids[k] for k in ('workspace', 'actor', 'item', 'version'))
            c.execute('insert into auth.users(id) values(%s)', (a,))
            c.execute('insert into public.workspaces(id,name,slug) values(%s,%s,%s)', (w, 'Synthetic driver', w))
            c.execute('insert into public.workspace_clients(workspace_id,client_id,display_name) values(%s,%s,%s)', (w, client, client))
            c.execute("insert into public.content_items(id,workspace_id,client_id,content_kind,status) values(%s,%s,%s,'daily_news','draft')", (i,w,client))
            c.execute("""insert into public.content_versions(id,workspace_id,content_item_id,version_number,
                prompt_version,channel_copy,content,deliverables,generation_meta)
                values(%s,%s,%s,1,'driver-fixture@1',%s,%s,%s,%s)""", (v,w,i,
                Jsonb({'telegram':'Original TG', 'x':'Original X'}), Jsonb({'render':{'old':True},'spec':{'old':True}}),
                Jsonb({'primary_asset_id':ids['asset']}), Jsonb({'mock_mode':False,
                    'fact_check':{'status':'pass'}, 'brand_qa':{'status':'pass'}})))
            c.execute("insert into public.assets(id,workspace_id,content_item_id,content_version_id,asset_kind,storage_path,mime_type,sha256) values(%s,%s,%s,%s,'png',%s,'image/png',%s)", (ids['asset'],w,i,v,ids['asset']+'/synthetic-only.png','a'*64))
            c.execute("insert into public.source_items(id,workspace_id,client_id,source_type,body,source_hash,published_at) values(%s,%s,%s,'manual','Synthetic source',%s,now())", (ids['source'],w,client,'b'*64))
            c.execute('insert into public.content_source_links(workspace_id,client_id,content_item_id,source_item_id) values(%s,%s,%s,%s)', (w,client,i,ids['source']))
            c.execute("update public.content_items set status='needs_review',current_version_id=%s where id=%s", (v,i))
            c.execute('insert into private.content_ops_button_reviewers values(%s,%s,%s,true)', (w,client,a))
            c.execute('insert into private.content_ops_button_identities values(%s,%s,%s,%s,true)',
                      (w,binding.digest('bot',bot),binding.digest('human',bot,human),a))
            fp = c.execute('select private.content_ops_button_version_fingerprint(%s,%s,%s)', (w,i,v)).fetchone()[0]
            ids['fingerprint'] = fp
            c.execute('insert into private.content_ops_button_reviews(id,workspace_id,client_id,content_item_id,content_version_id,version_fingerprint) values(%s,%s,%s,%s,%s,%s)', (ids['review'],w,client,i,v,fp))
            ids['receipt'] = ids['prompt']
            if not deferred_edit:
                c.execute('select private.record_content_ops_button_action(%s,%s,%s,%s,%s)',
                          (ids['review'],a,fp,'edit_' + ('telegram' if channel=='telegram' else 'x'),'e'*64))
                c.execute("""insert into private.content_ops_button_prompt_receipts(id,review_id,actor_id,epoch,
                    edit_action_key,bot_binding,room_binding,message_binding,receipt_sha256,delivered_at,outcome)
                    values(%s,%s,%s,1,%s,%s,%s,%s,%s,clock_timestamp(),'sent')""",
                    (ids['prompt'],ids['review'],a,'e'*64,binding.digest('bot',bot),binding.digest('room',bot,room),
                     binding.digest('prompt',bot,room,ids['message']),'f'*64))
            ids['old'] = c.execute('select to_jsonb(v) from public.content_versions v where id=%s', (v,)).fetchone()[0]
        if registered:
            registration = register(ids)
            assert registration['reused'] is False and registration['execution_authorized'] is False
            ids['prompt'] = registration['prompt_id']
            assert register(ids)['reused'] is True
        return ids

    def card_fixture(client='squid', channel='x'):
        ctx = seed(client, channel, registered=False, deferred_edit=True)
        # These minimized records are synthetic owner evidence, not proof of
        # actual card transport, staff authorization or production registration.
        ctx.update(card=str(uuid4()), attempt=str(uuid4()), card_bindings={
            'bot':binding.digest('bot',bot), 'room':binding.digest('room',bot,room),
            'message':binding.digest('card-message@2',bot,room,ctx['message']+1),
            'packet_receipt':'a'*64, 'card_receipt':'b'*64,
            'parent_binding':'c'*64, 'thread_id':None},
            card_parts=[{'kind':kind,
                         'message_binding':binding.digest('card-message@2',bot,room,ctx['message']+index+2),
                         'payload_sha256':'d'*64, 'outcome':'sent'}
                        for index,kind in enumerate(('image','telegram','x'))])
        times = read("""select jsonb_build_array(clock_timestamp(),
            least(expires_at,clock_timestamp()+interval '20 minutes'))
            from private.content_ops_button_reviews where id=%s""", (ctx['review'],))
        ctx['card_delivered'], ctx['card_expires'] = times
        review = read('select to_jsonb(r) from private.content_ops_button_reviews r where id=%s',
                      (ctx['review'],))
        card = {'id':ctx['card'], 'review_id':ctx['review'], 'epoch':0,
                'version_fingerprint':ctx['fingerprint'], 'bindings':ctx['card_bindings'],
                'parts':ctx['card_parts'], 'delivered_at':ctx['card_delivered'],
                'expires_at':ctx['card_expires']}
        ctx['card_bindings']['parent_binding'] = card_parent_binding(binding,review,card)
        return ctx

    def record_card(ctx):
        return read('select private.record_content_ops_button_card(%s,%s,%s,%s,%s,%s,%s,%s)',
            (ctx['review'],ctx['card'],ctx['fingerprint'],0,Jsonb(ctx['card_bindings']),
             Jsonb(ctx['card_parts']),ctx['card_delivered'],ctx['card_expires']))

    def request_card_edit(ctx):
        return read('select private.record_content_ops_button_action(%s,%s,%s,%s,%s)',
                    (ctx['review'],ctx['actor'],ctx['fingerprint'],'edit_'+ctx['channel'],'e'*64))

    def reserve_attempt(ctx, *, attempt_id=None, human_id=human):
        return read('select private.reserve_content_ops_button_prompt_attempt(%s,%s,%s,%s,%s)',
                    (ctx['card'],attempt_id or ctx['attempt'],ctx['actor'],
                     binding.digest('human',bot,human_id),'e'*64))

    def revoke_card(ctx, *, connection=None, review_id=None, fingerprint=None,
                    actor_id=None, bot_binding=None, human_binding=None):
        statement = 'select private.revoke_content_ops_button_card(%s,%s,%s,%s,%s,%s)'
        params = (ctx['card'],review_id or ctx['review'],fingerprint or ctx['fingerprint'],
                  actor_id or ctx['actor'],bot_binding or binding.digest('bot',bot),
                  human_binding or binding.digest('human',bot,human))
        if connection is not None:
            return connection.execute(statement,params).fetchone()[0]
        return read(statement,params)

    def registered_reserved_prompt(client='squid', channel='x'):
        ctx = seed_validated_prompt(client,channel)
        ctx['prompt'] = persist_prompt(ctx)['prompt_id']
        return ctx

    def card_row(ctx):
        return read('select to_jsonb(c) from private.content_ops_button_cards c where id=%s',
                    (ctx['card'],))

    def wait_for_actual_lock(waiting_pid, blocking_pid):
        # Fresh read transactions avoid PostgreSQL activity snapshot caching.
        # A bounded owner-system observation, not an assumed scheduling sleep.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if read('''select exists(select 1 from pg_stat_activity
                    where pid=%s and wait_event_type='Lock'
                      and %s=any(pg_blocking_pids(pid)))''',
                    (waiting_pid,blocking_pid)):
                return
        raise AssertionError('expected real local transaction lock was not observed')

    def attempt_row(ctx):
        return read('select to_jsonb(a) from private.content_ops_button_prompt_attempts a where id=%s',
                    (ctx['attempt'],))

    def sql_refusal(operation, expected='23514'):
        try:
            operation()
        except psycopg.Error as exc:
            assert exc.sqlstate == expected, 'unexpected durable attempt refusal category'
        else:
            raise AssertionError('durable attempt operation must be refused')

    def seed_validated_prompt(client='squid', channel='x', *, bad_parent=False):
        ctx = card_fixture(client,channel)
        if bad_parent:
            ctx['card_bindings']['parent_binding'] = '0'*64
        record_card(ctx)
        request_card_edit(ctx)
        reserve_attempt(ctx)
        validate_prompt_fixture(ctx)
        verify_prompt_rows(ctx, 0)
        return ctx

    def validate_prompt_fixture(ctx):
        # Construct only a synthetic response after reading the actual durable
        # reservation. Provider dates have seconds precision; do not round a
        # pre-reservation date up or alter the original precise DB timestamp.
        started = datetime.fromisoformat(attempt_row(ctx)['started_at'])
        seconds = math.ceil(started.timestamp())
        delay = seconds - time.time()
        if delay > 0:
            assert delay <= 1
            time.sleep(delay)
        observed = datetime.now(timezone.utc)
        response = {'ok':True, 'result':{'message_id':ctx['message'], 'date':seconds,
            'chat':{'id':room, 'type':'supergroup'}, 'from':{'id':bot, 'is_bot':True},
            'text':prompt_instruction('edit_'+ctx['channel'])}}
        ctx.update(provider_response=response, observed_at=observed.isoformat(),receipt=ctx['attempt'])

    def verify(ctx, versions):
        assert read('select count(*) from public.content_versions where content_item_id=%s', (ctx['item'],)) == versions
        assert read('select to_jsonb(v) from public.content_versions v where id=%s', (ctx['version'],)) == ctx['old']
        if versions == 2:
            row = read('''select to_jsonb(v) from public.content_versions v join public.content_items i
                on i.current_version_id=v.id where i.id=%s''', (ctx['item'],))
            assert row['channel_copy'][ctx['channel']] == ctx['text']
            other = 'x' if ctx['channel']=='telegram' else 'telegram'
            assert row['channel_copy'][other] == ctx['old']['channel_copy'][other]
            assert row['deliverables'] == {} and row['content'] == {}
            assert row['generation_meta']['fact_check']['status'] == 'needs_review'
            assert row['generation_meta']['brand_qa']['status'] == 'needs_review'
            assert row['qa']['manual_review_required'] is True
            assert read('select count(*) from public.assets where content_version_id=%s', (row['id'],)) == 0
            assert read('select status from public.content_items where id=%s', (ctx['item'],)) == 'draft'
        for table in ('approvals', 'publications'):
            assert read(f'select count(*) from public.{table} where content_item_id=%s', (ctx['item'],)) == 0

    def rejected(ctx, **kwargs):
        try:
            deliver(ctx, **kwargs)
        except ReviewIngressError as exc:
            assert str(exc) == 'review_edit_outcome_unknown'
        else:
            raise AssertionError('expected refusal or unknown')

    if args.phase == 'banner':
        from scripts.verify_banner_revision_owner_local import run
        print(json.dumps(run(connect,card_fixture,record_card,binding,
                             reserve_attempt,validate_prompt_fixture,persist_prompt)))
        return

    if args.phase == 'callbacks':
        from scripts.verify_private_review_owner_local import run
        print(json.dumps(run(connect,card_fixture,record_card,binding)))
        return

    if args.phase == 'restart':
        assert read("select count(*) from private.content_ops_button_markup_attempts where status='response_matched'")==12
        assert read('select count(*) from private.content_ops_button_control_evidence')==5
        assert read('select count(*) from private.content_ops_button_markup_approvals where not active')==4
        assert read('select count(*) from private.content_ops_button_markup_confirmation_events')==1
        assert read('select count(*) from private.content_ops_button_markup_confirmations where not active')==1
        assert read("select count(*) from private.content_ops_button_confirmation_deliveries where status='response_matched'")==1
        assert read('select count(*) from private.content_ops_button_confirmation_sources')==1
        assert read('select count(*) from private.content_ops_button_confirmation_send_permissions where not active')==1
        assert read('select count(*) from private.content_ops_button_confirmation_send_events')==1
        assert read('select count(*) from private.content_ops_button_confirmation_dispatches')==1
        from core.content_ops.confirmation_response_store import (
            ConfirmationResponseAuthenticator, PostgresConfirmationSourceReader,
        )
        with connect() as c:
            did,request_hash=c.execute('''select delivery_id::text,request_sha256
                from private.content_ops_button_confirmation_sources''').fetchone()
            source=PostgresConfirmationSourceReader(enabled=True,authenticator=
                ConfirmationResponseAuthenticator(b'local-synthetic-response-authentication-key'))(
                    cursor=c.cursor(),delivery_id=did,request_sha256=request_hash)
            assert hashlib.sha256(source.raw_response).hexdigest()==c.execute('''select response_sha256
                from private.content_ops_button_confirmation_deliveries where delivery_id=%s''',(did,)).fetchone()[0]
            assert source.observed_at==c.execute('''select observed_at
                from private.content_ops_button_confirmation_deliveries where delivery_id=%s''',(did,)).fetchone()[0]
        assert read("select count(*) from private.content_ops_button_markup_attempts where status='unknown'")>=2
        ctx = read('select ctx from private.test_button_driver_context')
        assert deliver(ctx)['reused'] is True
        verify(ctx, 2)
        prompt_ctx = read('select ctx from private.test_button_prompt_driver_context')
        registration = persist_prompt(prompt_ctx)
        assert registration['reused'] is True and registration['prompt_id'] == prompt_ctx['prompt']
        assert registration['execution_authorized'] is False
        verify_prompt_rows(prompt_ctx, 1)
        verify(prompt_ctx, 1)
        durable_ctx = read('select ctx from private.test_button_durable_context')
        assert reserve_attempt(durable_ctx)['reused'] is True
        assert attempt_row(durable_ctx) == durable_ctx['attempt_snapshot']
        revoked_ctx = read('select ctx from private.test_button_revoked_context')
        assert card_row(revoked_ctx) == revoked_ctx['revoked_snapshot']
        assert card_row(revoked_ctx)['active'] is False
        assert revoke_card(revoked_ctx)['reused'] is True
        rejected(revoked_ctx)
        verify(revoked_ctx,1)
        print(json.dumps({'pythonDriverRestartDedupe':True,
                         'promptReceiptDriverRestartDedupe':True,
                         'durableAttemptRestartDedupe':True,
                         'revokedCardRestartReplyDenied':True,'markupLedgerRestartRetained':True,'hostedProof':False}))
        return

    if args.phase == 'confirmation':
        from dataclasses import astuple
        from core.content_ops.cancellation_markup_confirmation import (
            MarkupConfirmationTarget, StoredMarkupConfirmation, markup_confirmation_payload,
        )
        from core.content_ops.cancellation_markup_confirmation_owner import PostgresTelegramMarkupDecision
        from core.content_ops.confirmation_delivery_owner import (
            PostgresConfirmationDeliveryOwner, StoredConfirmationSendReceipt,
            ConfirmationDeliveryError, confirmation_send_request,
        )
        from core.content_ops.confirmation_response_store import (
            ConfirmationResponseAuthenticator, PostgresConfirmationSourceStore,
            PostgresConfirmationSourceReader, ConfirmationSourceError,
        )
        ctx=card_fixture('yellow'); record_card(ctx)
        fixture=cancellation_fixture(ctx); plan=fixture['plan']; card=card_row(ctx)
        kwargs=markup_args(ctx,fixture,str(uuid4())); approval_id=str(uuid4())
        original=json.loads(fixture['response']); del original['result']['edit_date']
        original['result']['reply_markup']={'inline_keyboard':[]}
        control=StoredOriginalControlReceipt(ctx['card'],card['bindings']['card_receipt'],200,
            json.dumps(original).encode(),datetime.fromisoformat(card['delivered_at']))
        PostgresOriginalControlReceiptOwner(connect,receipt_loader=lambda **_:control).ingest(**kwargs)
        target=MarkupConfirmationTarget(approval_id,kwargs['attempt_id'],ctx['card'],ctx['actor'],
            binding.digest('human',bot,human),plan.seal,control.receipt_sha256,plan.started_at,plan.expires_at)
        delivery_id=str(uuid4())
        from core.content_ops.confirmation_send_authority import PostgresConfirmationSendGuard
        send_guard=PostgresConfirmationSendGuard(connect,enabled=True,target=target,delivery_id=delivery_id)
        identity={k:v for k,v in kwargs.items() if k!='enabled'}
        def check_send_guard():
            with send_guard.hold(**identity) as lease:
                assert lease.validate(now=datetime.now(timezone.utc),**identity) is True
        def send_guard_refused():
            try:check_send_guard()
            except CancellationMarkupError:pass
            else:raise AssertionError('independent send permission required')
        send_guard_refused()
        # Synthetic authenticated command; no real human or webhook involved.
        from core.content_ops.confirmation_send_registration import (
            COMMAND,operator_command,TelegramConfirmationSendDecision,
            PostgresConfirmationPermissionOwner,ConfirmationPermissionError,
        )
        permission_id=str(uuid4()); command_received=datetime.now(timezone.utc)
        command_policy=IngressPolicy('fixture_webhook_'+'z'*32,bot,room,'fixture-room',((human,ctx['actor']),))
        command_headers=[('Content-Type','application/json'),('X-Telegram-Bot-Api-Secret-Token',command_policy.webhook_secret)]
        command_update={'update_id':1909,'message':{'message_id':ctx['message']+60000,
            'date':int(command_received.timestamp()),'chat':{'id':room,'type':'supergroup'},
            'from':{'id':human,'is_bot':False},'text':operator_command(permission_id=permission_id,
                delivery_id=delivery_id,target=target,plan=plan,bindings=binding),
            'entities':[{'type':'bot_command','offset':0,'length':len(COMMAND)}]}}
        def command_adapter(value=command_update):
            return TelegramConfirmationSendDecision(enabled=True,raw_body=json.dumps(value).encode(),
                headers=command_headers,policy=command_policy,received_at=command_received)
        def register_permission(factory=connect,adapter=None):
            return PostgresConfirmationPermissionOwner(factory,decision=adapter or command_adapter()).register(
                permission_id=permission_id,delivery_id=delivery_id,target=target,**kwargs)
        class PermissionFaultConnection:
            def __init__(self,fault,table='content_ops_button_confirmation_send_permissions'):
                self.real=connect();self.fault=fault;self.table=table;self.insert_seen=False
            def __enter__(self):self.real.__enter__();return self
            def __exit__(self,*exc):
                result=self.real.__exit__(*exc)
                if self.fault=='commit_ack' and exc[0] is None:raise RuntimeError('synthetic lost permission ack')
                if self.fault=='fresh_commit_ack' and self.insert_seen and exc[0] is None:
                    raise RuntimeError('synthetic lost fresh dispatch ack')
                return result
            @property
            def autocommit(self):return self.real.autocommit
            def cursor(self):
                real=self.real.cursor(); fault=self.fault; parent=self
                class Cursor:
                    def __enter__(self):real.__enter__();return self
                    def __exit__(self,*exc):return real.__exit__(*exc)
                    def execute(self,sql,params=None):
                        self.inserted=sql.strip().startswith('insert into private.'+parent.table)
                        parent.insert_seen=parent.insert_seen or self.inserted
                        return real.execute(sql,params)
                    def fetchone(self):
                        row=real.fetchone()
                        return None if fault=='readback' and self.inserted else row
                return Cursor()
        for fault in ('readback',):
            try:register_permission(lambda:PermissionFaultConnection(fault))
            except ConfirmationPermissionError:pass
            else:raise AssertionError('permission fault expected')
        assert read('select count(*) from private.content_ops_button_confirmation_send_events')==0
        assert read('select count(*) from private.content_ops_button_confirmation_send_permissions')==0
        permission_barrier=Barrier(8)
        def register_contender(_):
            permission_barrier.wait(timeout=5);return register_permission()
        with ThreadPoolExecutor(max_workers=8) as pool:permission_results=list(pool.map(register_contender,range(8)))
        assert sum(not result['reused'] for result in permission_results)==1
        try:register_permission(lambda:PermissionFaultConnection('commit_ack'))
        except ConfirmationPermissionError:pass
        else:raise AssertionError('lost permission ack expected')
        assert read('select count(*) from private.content_ops_button_confirmation_send_events')==1
        assert read('select count(*) from private.content_ops_button_confirmation_send_permissions')==1
        changed_command=json.loads(json.dumps(command_update));changed_command['message']['message_id']+=1
        try:register_permission(adapter=command_adapter(changed_command))
        except ConfirmationPermissionError:pass
        else:raise AssertionError('changed command must not reissue permission')
        check_send_guard()
        delivery=PostgresConfirmationDeliveryOwner(connect)
        assert delivery.reserve(target=target,delivery_id=delivery_id,**kwargs)==dict(
            status='unknown',new_attempt=True,execution_authorized=False)
        assert delivery.reserve(target=target,delivery_id=delivery_id,**kwargs)['new_attempt'] is False
        try: delivery.reserve(target=target,delivery_id=str(uuid4()),**kwargs)
        except ConfirmationDeliveryError: pass
        else: raise AssertionError('new delivery ID must not bypass unknown attempt')
        from core.content_ops.confirmation_dispatch_owner import (
            PostgresConfirmationDispatchOwner,ConfirmationDispatchError,
        )
        def consume_dispatch(factory=connect):
            return PostgresConfirmationDispatchOwner(factory).consume(target=target,delivery_id=delivery_id,**kwargs)
        dispatch_table='content_ops_button_confirmation_dispatches'
        try:consume_dispatch(lambda:PermissionFaultConnection('readback',dispatch_table))
        except ConfirmationDispatchError:pass
        else:raise AssertionError('dispatch readback fault expected')
        assert read('select count(*) from private.'+dispatch_table)==0
        # Exactly the first INSERT commits but loses its acknowledgement. All
        # remaining contenders must observe consumed state, never send/reissue.
        dispatch_barrier=Barrier(8)
        def dispatch_contender(_):
            dispatch_barrier.wait(timeout=5)
            try:return consume_dispatch(lambda:PermissionFaultConnection('fresh_commit_ack',dispatch_table))
            except ConfirmationDispatchError:return {'status':'unknown'}
        with ThreadPoolExecutor(max_workers=8) as pool:
            dispatch_results=list(pool.map(dispatch_contender,range(8)))
        assert sum(result['status']=='unknown' for result in dispatch_results)==1
        assert sum(result.get('new_dispatch') is False for result in dispatch_results)==7
        assert read('select count(*) from private.'+dispatch_table)==1
        assert consume_dispatch()['new_dispatch'] is False
        from core.content_ops.confirmation_dispatch_client import PostgresCommittedDispatchReader
        from core.content_ops.confirmation_dispatch_protocol import ConfirmationBridgeError
        dispatch_reader=PostgresCommittedDispatchReader(connect)
        proof_args=dict(delivery_id=delivery_id,card_id=plan.card_id,
            request_sha256=hashlib.sha256(confirmation_send_request(target,plan,binding)).hexdigest())
        dispatch_proof=dispatch_reader.read(**proof_args)
        assert dispatch_proof[:3]==(permission_id,plan.card_id,proof_args['request_sha256'])
        try:dispatch_reader.read(**dict(proof_args,request_sha256='e'*64))
        except ConfirmationBridgeError:pass
        else:raise AssertionError('altered dispatch readback must fail')
        received=datetime.now(timezone.utc)
        receipt=StoredMarkupConfirmation(target,bot,room,ctx['message']+50000,None,
            int(received.timestamp()),received,True)
        payload=markup_confirmation_payload(target,binding)
        update={'update_id':909,'callback_query':{'id':'synthetic-confirmation-click',
            'from':{'id':human,'is_bot':False},
            'data':payload['reply_markup']['inline_keyboard'][0][0]['callback_data'],
            'message':dict(payload,message_id=receipt.message_id,date=receipt.message_date,
                chat={'id':room,'type':'supergroup'},**{'from':{'id':bot,'is_bot':True}})}}
        # Synthetic response signed with a test-only key, not live transport proof.
        original_response=StoredConfirmationSendReceipt(delivery_id,
            hashlib.sha256(confirmation_send_request(target,plan,binding)).hexdigest(),200,
            json.dumps({'ok':True,'result':update['callback_query']['message']}).encode(),received)
        source_auth=ConfirmationResponseAuthenticator(b'local-synthetic-response-authentication-key')
        envelope=source_auth.seal(enabled=True,receipt=original_response)
        source_store=PostgresConfirmationSourceStore(connect,authenticator=source_auth)
        class SourceFaultConnection:
            def __init__(self,fault):self.real=connect();self.fault=fault
            @property
            def autocommit(self):return self.real.autocommit
            def __enter__(self):self.real.__enter__();return self
            def __exit__(self,*values):
                result=self.real.__exit__(*values)
                if values[0] is None and self.fault=='commit_ack':raise RuntimeError('synthetic lost ACK')
                return result
            def cursor(self):
                real=self.real.cursor();fault=self.fault
                class Cursor:
                    inserted=False
                    def __enter__(self):real.__enter__();return self
                    def __exit__(self,*values):return real.__exit__(*values)
                    def execute(self,sql,params=()):
                        self.inserted=sql.strip().startswith('insert into private.content_ops_button_confirmation_sources')
                        return real.execute(sql,params)
                    def fetchone(self):
                        row=real.fetchone()
                        if self.inserted and fault=='readback':return None
                        return row
                return Cursor()
        def source_fault(fault):
            calls=[]
            def factory():calls.append(1);return SourceFaultConnection(fault)
            try:PostgresConfirmationSourceStore(factory,authenticator=source_auth).record(enabled=True,envelope=envelope)
            except ConfirmationSourceError:pass
            else:raise AssertionError('source persistence fault must be unknown')
            assert calls==[1]
        source_fault('readback')
        assert read('select count(*) from private.content_ops_button_confirmation_sources')==0
        source_barrier=Barrier(8)
        def store_source(_):
            source_barrier.wait(timeout=5)
            return source_store.record(enabled=True,envelope=envelope)
        with ThreadPoolExecutor(max_workers=8) as pool:source_results=list(pool.map(store_source,range(8)))
        assert sum(not result['reused'] for result in source_results)==1
        source_fault('commit_ack')
        # Read-only reconciliation, not a write retry after the lost ACK.
        assert read('select count(*) from private.content_ops_button_confirmation_sources')==1
        try:dispatch_reader.read(**proof_args)
        except ConfirmationBridgeError:pass
        else:raise AssertionError('captured response must prevent handoff readback')
        delivery=PostgresConfirmationDeliveryOwner(connect,receipt_loader=
            PostgresConfirmationSourceReader(enabled=True,authenticator=source_auth))
        import_barrier=Barrier(8)
        def import_receipt(_):
            import_barrier.wait(timeout=5)
            return delivery.ingest(target=target,delivery_id=delivery_id,**kwargs)
        with ThreadPoolExecutor(max_workers=8) as pool: imported=list(pool.map(import_receipt,range(8)))
        assert sum(not result['reused'] for result in imported)==1
        assert all(result['execution_authorized'] is False for result in imported)
        assert read('select count(*) from private.content_ops_button_markup_approvals where approval_id=%s',(approval_id,))==0
        # New original callback receipt time, distinct from original send response.
        received=datetime.now(timezone.utc)
        policy=IngressPolicy('fixture_webhook_'+'z'*32,bot,room,'fixture-room',((human,ctx['actor']),))
        headers=[('Content-Type','application/json'),('X-Telegram-Bot-Api-Secret-Token',policy.webhook_secret)]
        def adapter(value=update):
            return PostgresTelegramMarkupDecision(enabled=True,raw_body=json.dumps(value).encode(),
                headers=headers,policy=policy,bindings=binding,received_at=received)
        def refused(operation):
            try: operation()
            except CancellationMarkupError as exc: assert str(exc)=='markup_approval_registration_unknown'
            else: raise AssertionError('expected confirmation registration refusal')
        def fail_after_event(**values):
            adapter()(**values)
            raise ValueError('synthetic rollback after event')
        refused(lambda:PostgresMarkupApprovalOwner(connect,decision_authenticator=fail_after_event).register(
            approval_id=approval_id,**kwargs))
        assert read('select count(*) from private.content_ops_button_markup_confirmation_events')==0
        assert read('select count(*) from private.content_ops_button_markup_approvals where approval_id=%s',(approval_id,))==0
        barrier=Barrier(8)
        def register(_):
            barrier.wait(timeout=5)
            return PostgresMarkupApprovalOwner(connect,decision_authenticator=adapter()).register(
                approval_id=approval_id,**kwargs)
        with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(register,range(8)))
        assert sum(not result['reused'] for result in results)==1
        assert all(result['execution_authorized'] is False for result in results)
        assert read('select count(*) from private.content_ops_button_markup_confirmation_events')==1
        assert read('select approved_at from private.content_ops_button_markup_approvals where approval_id=%s',
                    (approval_id,))==received
        changed=json.loads(json.dumps(update)); changed['callback_query']['id']='different-click'
        refused(lambda:PostgresMarkupApprovalOwner(connect,decision_authenticator=adapter(changed)).register(
            approval_id=approval_id,**kwargs))
        # SQL failure after an event must roll back; committed event fields cannot
        # be modified or deleted to enable a retry with a new timestamp/payload.
        for sql in (
            "update private.content_ops_button_markup_confirmation_events set payload_sha256=repeat('e',64)",
            'delete from private.content_ops_button_markup_confirmation_events',
            "update private.content_ops_button_markup_confirmations set plan_seal=repeat('e',64)",
            'delete from private.content_ops_button_markup_confirmations'):
            sql_refusal(lambda sql=sql:read(sql))
        for sql in (
            "update private.content_ops_button_confirmation_deliveries set status='unknown',response_sha256=null,observed_at=null,recorded_approval_id=null",
            'delete from private.content_ops_button_confirmation_deliveries'):
            sql_refusal(lambda sql=sql:read(sql))
        for sql in (
            "update private.content_ops_button_confirmation_sources set response_sha256=repeat('e',64)",
            'delete from private.content_ops_button_confirmation_sources'):
            sql_refusal(lambda sql=sql:read(sql))
        for sql in (
            "update private.content_ops_button_confirmation_send_permissions set request_sha256=repeat('e',64)",
            'delete from private.content_ops_button_confirmation_send_permissions',
            "update private.content_ops_button_confirmation_send_events set payload_sha256=repeat('e',64)",
            'delete from private.content_ops_button_confirmation_send_events'):
            sql_refusal(lambda sql=sql:read(sql))
        for sql in (
            "update private.content_ops_button_confirmation_dispatches set request_sha256=repeat('e',64)",
            'delete from private.content_ops_button_confirmation_dispatches'):
            sql_refusal(lambda sql=sql:read(sql))
        # Real permission share lock blocks a participating concurrent revoke.
        ready=Event(); revoker_pid=[]
        def revoke_send_permission():
            with connect() as c:
                revoker_pid.append(c.info.backend_pid); ready.set()
                c.execute('update private.content_ops_button_confirmation_send_permissions set active=false')
        with ThreadPoolExecutor(max_workers=1) as pool:
            with send_guard.hold(**identity) as lease:
                assert lease.validate(now=datetime.now(timezone.utc),**identity) is True
                future=pool.submit(revoke_send_permission)
                assert ready.wait(timeout=3)
                wait_for_actual_lock(revoker_pid[0],lease._cursor.connection.info.backend_pid)
                assert not future.done()
            future.result(timeout=5)
        send_guard_refused()
        try:consume_dispatch()
        except ConfirmationDispatchError:pass
        else:raise AssertionError('revoked permission must not consume dispatch')
        sql_refusal(lambda:read('update private.content_ops_button_confirmation_send_permissions set active=true'))
        # Owner lock order matches registrar. Trigger retires the approval in
        # this same transaction; no application loop or provider call involved.
        with connect() as c:
            c.execute('select private.lock_content_ops_button_markup_card(%s,%s,%s,%s)',
                (ctx['card'],ctx['actor'],binding.digest('bot',bot),binding.digest('human',bot,human)))
            c.execute('update private.content_ops_button_markup_confirmations set active=false where approval_id=%s',
                (approval_id,))
            assert c.execute('select active from private.content_ops_button_markup_approvals where approval_id=%s',
                (approval_id,)).fetchone()[0] is False
        refused(lambda:PostgresMarkupApprovalOwner(connect,decision_authenticator=adapter()).register(
            approval_id=approval_id,**kwargs))
        sql_refusal(lambda:read('update private.content_ops_button_markup_confirmations set active=true'))
        for role in ('anon','authenticated','service_role'):
            for table in ('content_ops_button_markup_confirmations','content_ops_button_markup_confirmation_events',
                          'content_ops_button_confirmation_deliveries','content_ops_button_confirmation_sources',
                          'content_ops_button_confirmation_send_permissions','content_ops_button_confirmation_send_events',
                          'content_ops_button_confirmation_dispatches'):
                with connect() as c:
                    c.execute('set local role '+role)
                    try: c.execute('select * from private.'+table)
                    except psycopg.errors.InsufficientPrivilege: pass
                    else: raise AssertionError('runtime confirmation access leaked')
        verify(ctx,1)
        print(json.dumps({'driverPhase':'confirmation','phasePassed':True,'concurrentClicks':8,
            'eventRows':1,'newApprovals':1,'rollbackAfterEvent':True,'changedClickRefused':True,
            'atomicRevocation':True,'immutableRefusals':5,'runtimeRolesDenied':3,
            'confirmationReceiptContenders':8,'newConfirmationReceipts':1,
            'confirmationDeliveryResetRefusals':2,'unknownDeliveryNewIdRefused':True,
            'sourceStoreContenders':8,'sourceRows':1,'sourceRollbackVerified':True,
            'sourceLostAckReadOnlyReconciled':True,'sourceMutationRefusals':2,
            'independentSendPermissionRequired':True,'sendPermissionRevocationBlockedByGuard':True,
            'revokedSendPermissionDenied':True,'sendPermissionMutationRefusals':3,
            'permissionRegistrationContenders':8,'newPermissionRows':1,'permissionCommandEventRows':1,
            'permissionAtomicRollbackVerified':True,'permissionLostAckReuseReadOnlyReconciled':True,
            'changedPermissionCommandRefused':True,'permissionEventMutationRefusals':2,
            'dispatchContenders':8,'dispatchRows':1,'dispatchFreshCommitAckLost':True,
            'dispatchReplayResults':7,'dispatchReadbackRollback':True,'dispatchMutationRefusals':2,
            'committedDispatchBridgeReadback':True,'capturedSourceBlocksBridge':True,
            'hostedProof':False,'externalSends':0}),flush=True)
        return

    if args.phase == 'authority':
        class ReceiptFaultConnection:
            def __init__(self, fault, table='content_ops_button_control_evidence'):
                self.real=connect(); self.fault=fault; self.table=table
            @property
            def autocommit(self): return self.real.autocommit
            def __enter__(self): self.real.__enter__(); return self
            def __exit__(self,*args):
                result=self.real.__exit__(*args)
                if args[0] is None and self.fault=='commit_ack':
                    raise RuntimeError('synthetic lost acknowledgement')
                return result
            def cursor(self):
                real=self.real.cursor(); fault=self.fault; table=self.table
                class Cursor:
                    inserted=False
                    def __enter__(self): real.__enter__(); return self
                    def __exit__(self,*args): return real.__exit__(*args)
                    def execute(self,sql,params=()):
                        self.inserted=sql.strip().startswith('insert into private.'+table)
                        return real.execute(sql,params)
                    def fetchone(self):
                        row=real.fetchone()
                        if self.inserted and fault=='readback':
                            row=list(row); row[1]='0'*64; return tuple(row)
                        return row
                return Cursor()
        def decision_for(card,plan,kwargs,approval_id):
            # Synthetic explicit human decision, never a production auth session.
            return MarkupExecutionApproval(approval_id,kwargs['attempt_id'],plan.card_id,
                kwargs['actor_id'],binding.digest('human',bot,human),plan.seal,
                card['bindings']['card_receipt'],'append_cancellation_markup@1',
                datetime.fromtimestamp(plan.started_at,timezone.utc),
                datetime.fromtimestamp(plan.expires_at,timezone.utc),True)
        def seed_authority(ctx,fixture,kwargs,*,active=True,approve=True,concurrent=False,fault=None):
            # Synthetic trusted-loader seam, NOT authenticated transport proof.
            # Approval registrations also use a synthetic authentication seam.
            card=card_row(ctx); plan=fixture['plan']; approval_id=str(uuid4())
            original=json.loads(fixture['response'])
            del original['result']['edit_date']
            original['result']['reply_markup']={'inline_keyboard':[]}
            stored=StoredOriginalControlReceipt(ctx['card'],card['bindings']['card_receipt'],
                200,json.dumps(original).encode(),datetime.fromisoformat(card['delivered_at']))
            def loader(*,cursor,card_id,receipt_sha256):
                assert card_id==ctx['card'] and receipt_sha256==stored.receipt_sha256
                assert cursor.connection.autocommit is False
                return stored
            # For the fault wrapper use only the same cursor's current transaction.
            def fixture_loader(**values):
                assert values['card_id']==ctx['card'] and values['receipt_sha256']==stored.receipt_sha256
                return stored
            owner=PostgresOriginalControlReceiptOwner(connect,receipt_loader=loader)
            if fault:
                connections=[]
                def factory(): connections.append(1); return ReceiptFaultConnection(fault)
                try:
                    PostgresOriginalControlReceiptOwner(factory,receipt_loader=fixture_loader).ingest(**kwargs)
                except CancellationMarkupError as exc: assert str(exc)=='original_control_receipt_unknown'
                else: raise AssertionError('fault must be unknown')
                assert connections==[1]
                # Separate read-only reconciliation, not an automatic write retry.
                count=read('select count(*) from private.content_ops_button_control_evidence where card_id=%s',
                           (ctx['card'],))
                assert count==(1 if fault=='commit_ack' else 0)
            if concurrent:
                barrier=Barrier(8)
                def ingest(_): barrier.wait(timeout=5); return owner.ingest(**kwargs)
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results=list(pool.map(ingest,range(8)))
                assert sum(not result['reused'] for result in results)==1
                assert all(result['execution_authorized'] is False for result in results)
            elif fault!='commit_ack':
                assert owner.ingest(**kwargs)==dict(status='original_control_recorded',reused=False,
                                                    execution_authorized=False)
                assert owner.ingest(**kwargs)['reused'] is True
            assert read('select count(*) from private.content_ops_button_markup_approvals where card_id=%s',
                        (ctx['card'],))==0
            if approve:
                decision=decision_for(card,plan,kwargs,approval_id)
                def authenticate(*,cursor,approval_id):
                    assert approval_id==decision.approval_id
                    return decision
                registrar=PostgresMarkupApprovalOwner(connect,decision_authenticator=authenticate)
                if fault:
                    registrations=[]
                    def registration_factory():
                        registrations.append(1)
                        return ReceiptFaultConnection(fault,'content_ops_button_markup_approvals')
                    try:
                        PostgresMarkupApprovalOwner(registration_factory,
                            decision_authenticator=authenticate).register(approval_id=approval_id,**kwargs)
                    except CancellationMarkupError as exc:
                        assert str(exc)=='markup_approval_registration_unknown'
                    else: raise AssertionError('registration fault must be unknown')
                    assert registrations==[1]
                    assert read('select count(*) from private.content_ops_button_markup_approvals where approval_id=%s',
                        (approval_id,))==(1 if fault=='commit_ack' else 0)
                if fault=='readback':
                    barrier=Barrier(8)
                    def register(_):
                        barrier.wait(timeout=5)
                        return registrar.register(approval_id=approval_id,**kwargs)
                    with ThreadPoolExecutor(max_workers=8) as pool:
                        results=list(pool.map(register,range(8)))
                    assert sum(not result['reused'] for result in results)==1
                    assert all(result['execution_authorized'] is False for result in results)
                elif fault!='commit_ack':
                    assert registrar.register(approval_id=approval_id,**kwargs)['reused'] is False
                    assert registrar.register(approval_id=approval_id,**kwargs)['reused'] is True
                if not active:
                    # Synthetic revocation: the registrar has no activate/update branch.
                    read('update private.content_ops_button_markup_approvals set active=false where approval_id=%s returning 1',
                         (approval_id,))
            return approval_id
        reader=PostgresMarkupAuthorityReader(enabled=True)
        authority=ExactCancellationMarkupAuthority(reader,enabled=True)
        ledger=PostgresCancellationMarkupLedger(connect)
        now=lambda:datetime.now(timezone.utc)
        pids=[]
        def guard_connect():
            c=connect(); pids.append(c.info.backend_pid); return c
        guard=PostgresCancellationMarkupGuard(guard_connect,enabled=True,authorize_plan=authority)
        ctx=card_fixture(); record_card(ctx); fixture=cancellation_fixture(ctx)
        kwargs=markup_args(ctx,fixture,str(uuid4())); sends=[]
        class Transport:
            def edit_reply_markup(self,*,body):
                sends.append(1); return 200,fixture['response']
        # Neither an existing card nor the original evidence alone authorizes I/O.
        assert run_cancellation_markup_once(**kwargs,ledger=ledger,guard=guard,
            transport=Transport(),clock=now)['status']=='blocked'
        seed_authority(ctx,fixture,kwargs,approve=False,concurrent=True)
        assert run_cancellation_markup_once(**kwargs,ledger=ledger,guard=guard,
            transport=Transport(),clock=now)['status']=='blocked'
        assert sends==[] and read('select count(*) from private.content_ops_button_markup_attempts where card_id=%s',
            (ctx['card'],))==0

        # Evidence plus an already consumed attempt must not gain new authority.
        assert ledger.reserve(**kwargs)['new_attempt'] is True
        consumed_decision=decision_for(card_row(ctx),fixture['plan'],kwargs,str(uuid4()))
        try:
            PostgresMarkupApprovalOwner(connect,decision_authenticator=lambda **_:consumed_decision).register(
                approval_id=consumed_decision.approval_id,**kwargs)
        except CancellationMarkupError: pass
        else: raise AssertionError('consumed attempt gained approval')
        assert read('select count(*) from private.content_ops_button_markup_approvals where card_id=%s',
                    (ctx['card'],))==0

        ctx=card_fixture(); record_card(ctx); fixture=cancellation_fixture(ctx)
        kwargs=markup_args(ctx,fixture,str(uuid4())); aid=seed_authority(ctx,fixture,kwargs,fault='readback')
        waiting=[]; ready=Event(); futures=[]
        def revoke_approval():
            with connect() as c:
                waiting.append(c.info.backend_pid); ready.set()
                c.execute('update private.content_ops_button_markup_approvals set active=false where approval_id=%s',(aid,))
        with ThreadPoolExecutor(max_workers=1) as pool:
            class LockedTransport:
                def edit_reply_markup(self,*,body):
                    sends.append(1); futures.append(pool.submit(revoke_approval)); assert ready.wait(3)
                    wait_for_actual_lock(waiting[0],pids[-1])
                    return 200,fixture['response']
            result=run_cancellation_markup_once(**kwargs,ledger=ledger,guard=guard,
                transport=LockedTransport(),clock=now)
            assert result['status']=='response_matched'
            futures[0].result(timeout=5)
        assert len(sends)==1
        revoked_decision=decision_for(card_row(ctx),fixture['plan'],kwargs,aid)
        try:
            PostgresMarkupApprovalOwner(connect,decision_authenticator=lambda **_:revoked_decision).register(
                approval_id=aid,**kwargs)
        except CancellationMarkupError: pass
        else: raise AssertionError('revoked approval reactivated')
        assert read('select active from private.content_ops_button_markup_approvals where approval_id=%s',(aid,)) is False
        assert run_cancellation_markup_once(**kwargs,ledger=ledger,guard=guard,
            transport=Transport(),clock=now)['status']=='blocked'
        assert len(sends)==1

        # Irreversible evidence and approval rows, including after revocation.
        for statement,params in (
            ('update private.content_ops_button_control_evidence set text_sha256=%s where card_id=%s',('0'*64,ctx['card'])),
            ('delete from private.content_ops_button_control_evidence where card_id=%s',(ctx['card'],)),
            ('update private.content_ops_button_markup_approvals set active=true where approval_id=%s',(aid,)),
            ('update private.content_ops_button_markup_approvals set active=false where approval_id=%s',(aid,)),
            ('update private.content_ops_button_markup_approvals set expires_at=expires_at+interval \'1 minute\' where approval_id=%s',(aid,)),
            ('delete from private.content_ops_button_markup_approvals where approval_id=%s',(aid,)),
        ):
            sql_refusal(lambda:read(statement+' returning 1',params))

        # A stored but inactive approval is not accepted.
        ctx=card_fixture(); record_card(ctx); fixture=cancellation_fixture(ctx)
        kwargs=markup_args(ctx,fixture,str(uuid4())); seed_authority(ctx,fixture,kwargs,active=False)
        sends=[]
        assert run_cancellation_markup_once(**kwargs,ledger=ledger,guard=guard,
            transport=Transport(),clock=now)['status']=='blocked' and sends==[]

        # Revocation committed during the unlocked reserve gap prevents I/O.
        ctx=card_fixture(); record_card(ctx); fixture=cancellation_fixture(ctx)
        kwargs=markup_args(ctx,fixture,str(uuid4())); aid=seed_authority(ctx,fixture,kwargs,fault='commit_ack')
        class GapLedger:
            def reserve(self,**values):
                result=ledger.reserve(**values)
                read('update private.content_ops_button_markup_approvals set active=false where approval_id=%s returning 1',(aid,))
                return result
            def record_response(self,**values): raise AssertionError('must not record')
        assert run_cancellation_markup_once(**kwargs,ledger=GapLedger(),guard=guard,
            transport=Transport(),clock=now)['status']=='unknown' and sends==[]
        assert read('select status from private.content_ops_button_markup_attempts where id=%s',
            (kwargs['attempt_id'],))=='unknown'
        for role in ('anon','authenticated','service_role'):
            for table in ('content_ops_button_control_evidence','content_ops_button_markup_approvals'):
                try:
                    with connect() as c:
                        c.execute(f'set local role {role}')
                        c.execute(f'select * from private.{table}')
                except psycopg.errors.InsufficientPrivilege: pass
                else: raise AssertionError('runtime evidence access leaked')
        print(json.dumps({'driverPhase':'authority','phasePassed':True,
            'storedAuthorityVerified':True,'approvalRevocationLockObserved':True,
            'missingEvidenceOrApprovalDenied':True,'inactiveApprovalDenied':True,
            'gapRevocationDenied':True,'immutableRefusals':6,'runtimeRolesDenied':3,
            'originalReceiptImporterVerified':True,'originalReceiptContenders':8,'newOriginalReceipts':1,
            'originalReceiptRollbackVerified':True,'originalReceiptLostAckReadOnlyReconciled':True,
            'exactApprovalRegistrationVerified':True,'approvalRegistrationContenders':8,'newApprovalRows':1,
            'approvalRollbackVerified':True,'approvalLostAckReadOnlyReconciled':True,
            'revokedApprovalReregistrationDenied':True,'consumedAttemptApprovalDenied':True,
            'productionCalls':0,'providerCalls':0,'hostedProof':False}))
        return

    if args.phase == 'guard':
        # Real row locks/ledger transactions; all authority and transport inputs
        # are synthetic. No production authority or Telegram connection exists.
        ledger = PostgresCancellationMarkupLedger(connect)
        now = lambda: datetime.now(timezone.utc)
        def owner(factory=connect):
            return PostgresCancellationMarkupGuard(factory,enabled=True,
                authorize_plan=lambda **identity: True)

        ctx = card_fixture(); record_card(ctx); fixture = cancellation_fixture(ctx)
        kwargs = markup_args(ctx,fixture,str(uuid4())); pids=[]; waiting=[]; ready=Event()
        def guard_connect():
            c=connect(); pids.append(c.info.backend_pid); return c
        def revoke_waiter():
            with connect() as c:
                waiting.append(c.info.backend_pid); ready.set()
                return revoke_card(ctx,connection=c)
        futures=[]
        with ThreadPoolExecutor(max_workers=1) as pool:
            class LockedTransport:
                def edit_reply_markup(self, *, body):
                    assert body == json.loads(fixture['plan'].request_json)
                    futures.append(pool.submit(revoke_waiter))
                    assert ready.wait(3)
                    wait_for_actual_lock(waiting[0],pids[-1])
                    return 200,fixture['response']
            result=run_cancellation_markup_once(**kwargs,ledger=ledger,guard=owner(guard_connect),
                transport=LockedTransport(),clock=now)
            assert result == {'status':'response_matched','execution_authorized':False}
            assert futures[0].result(timeout=5)['status']=='card_revoked'
        assert card_row(ctx)['active'] is False
        assert read('select status from private.content_ops_button_markup_attempts where id=%s',
            (kwargs['attempt_id'],))=='response_matched'

        # Eight DB-backed contenders: reservation commits outside the row guard.
        ctx=card_fixture(); record_card(ctx); fixture=cancellation_fixture(ctx)
        kwargs=markup_args(ctx,fixture,str(uuid4())); sends=[]; barrier=Barrier(8)
        class FakeTransport:
            def edit_reply_markup(self, *, body):
                sends.append(1); return 200,fixture['response']
        def contend(_):
            barrier.wait(timeout=10)
            return run_cancellation_markup_once(**kwargs,ledger=ledger,guard=owner(),
                transport=FakeTransport(),clock=now)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(contend,range(8)))
        assert len(sends)==1
        assert sum(r['status']=='response_matched' for r in results)==1
        assert sum(r['status'] in ('existing_unknown','existing_response_matched') for r in results)==7

        # The reservation gap is intentionally unlocked: a cancellation that
        # wins it must be visible to the second guard before transport.
        ctx=card_fixture(); record_card(ctx); fixture=cancellation_fixture(ctx)
        kwargs=markup_args(ctx,fixture,str(uuid4())); sends=[]
        class GapLedger:
            def reserve(self,**values):
                result=ledger.reserve(**values); revoke_card(ctx); return result
            def record_response(self,**values): raise AssertionError('must not record')
        result=run_cancellation_markup_once(**kwargs,ledger=GapLedger(),guard=owner(),
            transport=FakeTransport(),clock=now)
        assert result['status']=='unknown' and not sends
        assert read('select status from private.content_ops_button_markup_attempts where id=%s',
            (kwargs['attempt_id'],))=='unknown'

        ctx=card_fixture(); record_card(ctx); fixture=cancellation_fixture(ctx)
        kwargs=markup_args(ctx,fixture,str(uuid4())); sends=[]
        class TimeoutTransport:
            def edit_reply_markup(self, *, body):
                sends.append(1); raise TimeoutError('synthetic-only')
        for expected in ('unknown','existing_unknown'):
            result=run_cancellation_markup_once(**kwargs,ledger=ledger,guard=owner(),
                transport=TimeoutTransport(),clock=now)
            assert result['status']==expected
        assert len(sends)==1

        for table in ('content_ops_button_identities','content_ops_button_reviewers','workspace_clients'):
            ctx=card_fixture(); record_card(ctx); fixture=cancellation_fixture(ctx)
            kwargs=markup_args(ctx,fixture,str(uuid4())); sends=[]
            schema='public' if table=='workspace_clients' else 'private'
            with connect() as c:
                c.execute(f'update {schema}.{table} set active=false where workspace_id=%s',(ctx['workspace'],))
            result=run_cancellation_markup_once(**kwargs,ledger=ledger,guard=owner(),
                transport=FakeTransport(),clock=now)
            assert result['status']=='blocked' and not sends
            assert read('select count(*) from private.content_ops_button_markup_attempts where card_id=%s',
                (ctx['card'],))==0
        print(json.dumps({'driverPhase':'guard','phasePassed':True,
            'guardRevocationWaitObserved':True,'guardGapCancellationDenied':True,
            'guardDbContenders':8,'guardFakeSends':1,'guardAuthorityRefusals':3,
            'guardTimeoutNoRetry':True,'productionCalls':0,'providerCalls':0,'hostedProof':False}))
        return

    if args.phase == 'initial':
        with connect() as c:
            c.execute('create sequence private.test_button_driver_messages start 1000 increment 100')
            c.execute('create table private.test_button_driver_context(ctx jsonb)')
            c.execute('create table private.test_button_prompt_driver_context(ctx jsonb)')
            c.execute('create table private.test_button_durable_context(ctx jsonb)')
            c.execute('create table private.test_button_revoked_context(ctx jsonb)')
        # Registration serializes on the current item/review. Receipt evidence is
        # fixture-only here; no registration function fabricates provider success.
        regctx = seed(registered=False); regbarrier = Barrier(8)
        def reg_race(_): regbarrier.wait(timeout=10); return register(regctx)
        with ThreadPoolExecutor(max_workers=8) as pool:
            registrations = list(pool.map(reg_race, range(8)))
        assert sum(not r['reused'] for r in registrations) == 1
        assert len({r['prompt_id'] for r in registrations}) == 1
        for case in ('unknown','rejected','wrong_human','revoked','stale_epoch','stale_source',
                     'future','before_action','expired','banner','conflicting_receipt'):
            ctx = seed(registered=False)
            with connect() as c:
                if case in ('unknown','rejected'):
                    c.execute('update private.content_ops_button_prompt_receipts set outcome=%s,delivered_at=null where id=%s',
                              ('delivery_unknown' if case=='unknown' else 'rejected',ctx['receipt']))
                if case=='revoked':
                    c.execute('update private.content_ops_button_identities set active=false where actor_id=%s',(ctx['actor'],))
                if case=='stale_epoch':
                    c.execute('update private.content_ops_button_reviews set epoch=epoch+1 where id=%s',(ctx['review'],))
                if case=='stale_source':
                    c.execute("update public.source_items set body='Changed' where id=%s",(ctx['source'],))
                if case=='future':
                    c.execute("update private.content_ops_button_prompt_receipts set delivered_at=clock_timestamp()+interval '1 minute',recorded_at=clock_timestamp()+interval '2 minutes' where id=%s",(ctx['receipt'],))
                if case=='before_action':
                    c.execute("update private.content_ops_button_prompt_receipts set delivered_at=clock_timestamp()-interval '1 minute' where id=%s",(ctx['receipt'],))
                if case=='expired':
                    # One stable fixture timestamp: two wall-clock reads can exceed
                    # the 30-minute lifetime constraint by a single microsecond.
                    c.execute("update private.content_ops_button_reviews set created_at=statement_timestamp()-interval '31 minutes',expires_at=statement_timestamp()-interval '1 minute' where id=%s",(ctx['review'],))
                if case=='banner':
                    c.execute("update private.content_ops_button_actions set action='edit_banner' where review_id=%s",(ctx['review'],))
            if case=='conflicting_receipt':
                register(ctx)
                with connect() as c:
                    new_receipt = str(uuid4())
                    c.execute("""insert into private.content_ops_button_prompt_receipts
                        select %s,review_id,actor_id,epoch,edit_action_key,bot_binding,room_binding,
                        %s,receipt_sha256,outcome,delivered_at,recorded_at,reservation_expires_at
                        from private.content_ops_button_prompt_receipts where id=%s""",
                        (new_receipt,binding.digest('prompt',bot,room,ctx['message']+1),ctx['receipt']))
                    ctx['receipt']=new_receipt
            try:
                register(ctx, human_id=202 if case=='wrong_human' else human)
            except psycopg.Error as exc:
                expected = ('42501' if case in ('wrong_human','revoked') else
                            '23505' if case=='conflicting_receipt' else '23514')
                assert exc.sqlstate == expected, 'unexpected registration rejection category'
            else:
                raise AssertionError('registration must be refused')
            assert read('select count(*) from private.content_ops_button_edit_prompts where review_id=%s',
                        (ctx['review'],)) == (1 if case=='conflicting_receipt' else 0)
            verify(ctx,1)
        for client in ('yellow','babylon','squid','origintrail'):
            for channel in ('telegram','x'):
                ctx = seed(client, channel)
                result = deliver(ctx)
                assert result['reused'] is False and result['execution_authorized'] is False
                assert deliver(ctx)['reused'] is True
                verify(ctx, 2)
                rejected(ctx, text='A different retry body')
                verify(ctx, 2)

        ctx = seed(); barrier = Barrier(8)
        def race(_): barrier.wait(timeout=10); return deliver(ctx)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(race, range(8)))
        assert sum(not r['reused'] for r in results) == 1
        assert len({r['content_version_id'] for r in results}) == 1
        verify(ctx, 2)
        with connect() as c:
            c.execute('insert into private.test_button_driver_context values(%s)', (Jsonb(ctx),))

        for scenario in ('human','prompt','revoked','expired','source'):
            ctx = seed(); kwargs = {}
            if scenario == 'human': kwargs['actor'] = 202
            if scenario == 'prompt': kwargs['prompt'] = ctx['message'] + 1
            with connect() as c:
                if scenario == 'revoked':
                    c.execute('update private.content_ops_button_identities set active=false where actor_id=%s', (ctx['actor'],))
                if scenario == 'expired':
                    c.execute("update private.content_ops_button_edit_prompts set delivered_at=now()-interval '21 minutes',expires_at=now()-interval '1 minute' where id=%s", (ctx['prompt'],))
                if scenario == 'source':
                    c.execute("update public.source_items set body='Changed synthetic source' where id=%s", (ctx['source'],))
            rejected(ctx, **kwargs); verify(ctx, 1)

        # A real transaction with fault injection only at the receipt/ACK boundary.
        class FaultConnection:
            def __init__(self, fault): self.real=connect(); self.fault=fault
            @property
            def autocommit(self): return self.real.autocommit
            def __enter__(self): self.real.__enter__(); return self
            def __exit__(self, *args):
                result = self.real.__exit__(*args)
                if self.fault == 'commit_ack': raise RuntimeError('synthetic lost commit acknowledgement')
                return result
            def cursor(self):
                if self.fault not in ('receipt','prompt_receipt'): return self.real.cursor()
                real = self.real.cursor()
                fault = self.fault
                class FaultCursor:
                    def __enter__(self): real.__enter__(); return self
                    def __exit__(self, *args): return real.__exit__(*args)
                    def execute(self, *args): return real.execute(*args)
                    def fetchone(self):
                        row = real.fetchone()
                        if fault == 'receipt' or (row and type(row[0]) is dict
                                and row[0].get('status') == 'prompt_registered'):
                            row[0]['execution_authorized'] = True
                        return row
                return FaultCursor()
        for fault, expected in (('receipt',1),('commit_ack',2)):
            ctx = seed(); calls = []
            def factory(): calls.append(1); return FaultConnection(fault)
            rejected(ctx, owner=PostgresEditReplyOwner(factory))
            assert calls == [1]
            verify(ctx, expected)  # Separate read-only reconciliation, never auto-retry.

        # Actual card -> edit -> durable reservation -> minimized synthetic response
        # -> atomic registration -> authenticated synthetic reply -> immutable save.
        # Pure-planner behavior remains separate unit coverage, not DB proof.
        for client in ('yellow','babylon','squid','origintrail'):
            for channel in ('telegram','x'):
                ctx = seed_validated_prompt(client,channel)
                registration = persist_prompt(ctx)
                assert registration['reused'] is False and registration['execution_authorized'] is False
                ctx['prompt'] = registration['prompt_id']
                assert persist_prompt(ctx)['reused'] is True
                verify_prompt_rows(ctx, 1)
                assert read('''select p.expires_at=a.expires_at
                        and r.reservation_expires_at=a.expires_at
                    from private.content_ops_button_edit_prompts p
                    join private.content_ops_button_prompt_receipts r on r.id=p.owner_receipt_id
                    join private.content_ops_button_prompt_attempts a on a.id=r.id
                    where p.id=%s and a.id=%s''', (ctx['prompt'],ctx['attempt'])) is True
                assert deliver(ctx)['reused'] is False
                assert deliver(ctx)['reused'] is True  # Active historical card permits exact replay.
                verify(ctx, 2)

        # An exact concurrent replay creates one receipt and one prompt, not eight.
        prompt_ctx = seed_validated_prompt(); prompt_barrier = Barrier(8)
        def prompt_race(_): prompt_barrier.wait(timeout=10); return persist_prompt(prompt_ctx)
        with ThreadPoolExecutor(max_workers=8) as pool:
            prompt_results = list(pool.map(prompt_race, range(8)))
        assert sum(not result['reused'] for result in prompt_results) == 1
        assert len({result['prompt_id'] for result in prompt_results}) == 1
        assert all(result['execution_authorized'] is False for result in prompt_results)
        prompt_ctx['prompt'] = prompt_results[0]['prompt_id']
        verify_prompt_rows(prompt_ctx, 1)
        with connect() as c:
            c.execute('insert into private.test_button_prompt_driver_context values(%s)', (Jsonb(prompt_ctx),))

        # Distinct responses for one edit epoch must serialize before any receipt
        # INSERT obtains its FK locks: one winner, seven fully rolled-back losers.
        distinct_ctx = seed_validated_prompt(); contenders = []
        for number in range(8):
            contender = dict(distinct_ctx, message=distinct_ctx['message'] + 20 * number)
            validate_prompt_fixture(contender)
            contenders.append(contender)
        distinct_barrier = Barrier(8)
        distinct_sqlstates = []
        class DiagnosticConnection(FaultConnection):
            # Test-only capture before the adapter deliberately sanitizes DB errors.
            # A deadlock/timeout must not masquerade as an expected conflict loser.
            def __init__(self): super().__init__('none')
            def __exit__(self, *args):
                if args[1] is not None:
                    distinct_sqlstates.append(getattr(args[1], 'sqlstate', None))
                return super().__exit__(*args)
        def distinct_prompt_race(contender):
            distinct_barrier.wait(timeout=10)
            try:
                return persist_prompt(contender, owner=PostgresPromptReceiptOwner(DiagnosticConnection))
            except PromptReceiptError as exc:
                assert str(exc) == 'prompt_registration_unknown'
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            distinct_results = list(pool.map(distinct_prompt_race, contenders))
        winners = [result for result in distinct_results if result is not None]
        assert len(winners) == 1 and winners[0]['reused'] is False
        assert winners[0]['execution_authorized'] is False
        assert len(distinct_sqlstates) == 7
        assert not ({'40P01','55P03','57014'} & set(distinct_sqlstates)), 'unexpected deadlock/timeout'
        verify_prompt_rows(distinct_ctx, 1)
        verify(distinct_ctx, 1)

        # Original observation is immutable: a retry cannot renew receipt age.
        prompt_unknown(prompt_ctx, observed_at=datetime.now(timezone.utc))
        verify_prompt_rows(prompt_ctx, 1)
        for scenario in ('missing_attempt','parent_mismatch','wrong_human','revoked','source',
                         'same_second','card_inactive','wrong_message'):
            ctx = seed_validated_prompt(bad_parent=scenario=='parent_mismatch')
            kwargs = {}
            if scenario=='missing_attempt':
                kwargs['attempt_id'] = str(uuid4())
            if scenario=='wrong_human':
                kwargs['human_id'] = 202
            if scenario=='same_second':
                # An earlier date in the reservation's second is not upgraded to
                # the precise reservation timestamp, even with a later observation.
                started = datetime.fromisoformat(attempt_row(ctx)['started_at'])
                assert started.microsecond != 0
                ctx['provider_response']['result']['date'] = math.floor(started.timestamp())
            if scenario=='wrong_message':
                ctx['provider_response']['result']['message_id'] = ctx['message']+1
            with connect() as c:
                if scenario=='revoked':
                    c.execute('update private.content_ops_button_identities set active=false where actor_id=%s',
                              (ctx['actor'],))
                if scenario=='source':
                    c.execute("update public.source_items set body='Changed synthetic source' where id=%s",
                              (ctx['source'],))
                if scenario=='card_inactive':
                    c.execute('update private.content_ops_button_cards set active=false where id=%s',
                              (ctx['card'],))
            prompt_unknown(ctx, **kwargs)
            verify_prompt_rows(ctx, 0)
            verify(ctx, 1)

        for fault, expected in (('prompt_receipt',0),('commit_ack',1)):
            ctx = seed_validated_prompt(); calls = []
            def prompt_factory(): calls.append(1); return FaultConnection(fault)
            prompt_unknown(ctx, owner=PostgresPromptReceiptOwner(prompt_factory))
            assert calls == [1]
            # Independent reads reconcile committed state; never retry unknown sends.
            verify_prompt_rows(ctx, expected)
            verify(ctx, 1)

        # Durable pre-send reservation still performs no provider call and grants
        # no send permission. The real local schema owns identity, expiry and dedupe.
        for client in ('yellow','babylon','squid','origintrail'):
            for channel in ('telegram','x'):
                ctx = card_fixture(client, channel)
                recorded = record_card(ctx)
                assert recorded == {'status':'card_recorded','card_id':ctx['card'],
                                    'reused':False,'execution_authorized':False}
                assert record_card(ctx)['reused'] is True
                request_card_edit(ctx)
                reserved = reserve_attempt(ctx)
                assert reserved == {'status':'attempt_recorded','attempt_id':ctx['attempt'],
                                    'reused':False,'execution_authorized':False}
                original = attempt_row(ctx)
                assert original['expected_text_sha256'] == hashlib.sha256(
                    prompt_instruction('edit_'+channel).encode()).hexdigest()
                assert datetime.fromisoformat(original['expires_at']) <= datetime.fromisoformat(ctx['card_expires'])
                assert reserve_attempt(ctx)['reused'] is True
                assert attempt_row(ctx) == original
                verify(ctx,1)
                verify_prompt_rows(ctx,0)

        durable_ctx = card_fixture()
        record_card(durable_ctx); request_card_edit(durable_ctx)
        durable_barrier = Barrier(8)
        def durable_race(_): durable_barrier.wait(timeout=10); return reserve_attempt(durable_ctx)
        with ThreadPoolExecutor(max_workers=8) as pool:
            durable_results = list(pool.map(durable_race,range(8)))
        assert sum(not result['reused'] for result in durable_results) == 1
        assert len({result['attempt_id'] for result in durable_results}) == 1
        durable_ctx['attempt_snapshot'] = attempt_row(durable_ctx)
        with connect() as c:
            c.execute('insert into private.test_button_durable_context values(%s)', (Jsonb(durable_ctx),))

        ctx = card_fixture(); record_card(ctx); request_card_edit(ctx)
        durable_distinct_barrier = Barrier(8)
        def durable_distinct_race(attempt_id):
            durable_distinct_barrier.wait(timeout=10)
            try:
                return reserve_attempt(ctx,attempt_id=attempt_id)
            except psycopg.Error as exc:
                assert exc.sqlstate == '23505'
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            durable_distinct = list(pool.map(durable_distinct_race,[str(uuid4()) for _ in range(8)]))
        assert sum(result is not None for result in durable_distinct) == 1
        assert read('select count(*) from private.content_ops_button_prompt_attempts where review_id=%s',
                    (ctx['review'],)) == 1

        # Mutating an attempt, even without changing its values, cannot refresh age.
        for statement in (
            'update private.content_ops_button_prompt_attempts set started_at=started_at where id=%s',
            'delete from private.content_ops_button_prompt_attempts where id=%s',
        ):
            sql_refusal(lambda: read(statement,(durable_ctx['attempt'],)))
            assert attempt_row(durable_ctx) == durable_ctx['attempt_snapshot']
        sql_refusal(lambda: read('update private.content_ops_button_cards set expires_at=expires_at where id=%s',
                                (durable_ctx['card'],)))
        with connect() as c:
            c.execute('update private.content_ops_button_cards set active=false where id=%s',(durable_ctx['card'],))
        sql_refusal(lambda: read('update private.content_ops_button_cards set active=true where id=%s',
                                (durable_ctx['card'],)))
        # Keep the persisted restart fixture active and unchanged; revoke a separate
        # card below instead, because a revoked card must never revive on restart.
        restart_ctx = card_fixture(); record_card(restart_ctx); request_card_edit(restart_ctx)
        reserve_attempt(restart_ctx); restart_ctx['attempt_snapshot'] = attempt_row(restart_ctx)
        with connect() as c:
            c.execute('update private.test_button_durable_context set ctx=%s',(Jsonb(restart_ctx),))

        for scenario in ('expired','wrong_human','revoked','source','review_epoch','card_inactive'):
            ctx = card_fixture(); record_card(ctx); request_card_edit(ctx)
            with connect() as c:
                if scenario=='expired':
                    c.execute("update private.content_ops_button_reviews set created_at=statement_timestamp()-interval '31 minutes',expires_at=statement_timestamp()-interval '1 minute' where id=%s",(ctx['review'],))
                if scenario=='revoked':
                    c.execute('update private.content_ops_button_identities set active=false where actor_id=%s',(ctx['actor'],))
                if scenario=='source':
                    c.execute("update public.source_items set body='Changed synthetic source' where id=%s",(ctx['source'],))
                if scenario=='review_epoch':
                    c.execute('update private.content_ops_button_reviews set epoch=epoch+1 where id=%s',(ctx['review'],))
                if scenario=='card_inactive':
                    c.execute('update private.content_ops_button_cards set active=false where id=%s',(ctx['card'],))
            sql_refusal(lambda: reserve_attempt(ctx,human_id=202 if scenario=='wrong_human' else human),
                        '42501' if scenario in ('wrong_human','revoked') else '23514')
            assert read('select count(*) from private.content_ops_button_prompt_attempts where review_id=%s',
                        (ctx['review'],)) == 0
            verify(ctx,1)
        ctx = card_fixture()
        ctx['card_parts'][2]['message_binding'] = ctx['card_bindings']['message']
        sql_refusal(lambda: record_card(ctx),'22023')
        assert read('select count(*) from private.content_ops_button_cards where review_id=%s',(ctx['review'],)) == 0

        print(json.dumps({'driverPhase':'initial','phasePassed':True,'hostedProof':False}),flush=True)
        return

    # Revocation is a cancellation, never a publishing action or a revision
    # rollback. Every client/channel loses reply authority after its card stops.
    for client in ('yellow','babylon','squid','origintrail'):
        for channel in ('telegram','x'):
            ctx = registered_reserved_prompt(client,channel)
            before = card_row(ctx)
            fixture = cancellation_fixture(ctx)
            ledger = PostgresCancellationMarkupLedger(connect)
            kwargs = markup_args(ctx,fixture,str(uuid4()))
            reserved = ledger.reserve(**kwargs)
            assert reserved['status']=='unknown' and reserved['new_attempt'] is True
            assert reserved['execution_authorized'] is False
            assert ledger.reserve(**kwargs) == dict(reserved,new_attempt=False)
            observed = datetime.now(timezone.utc)
            receipt_kwargs = dict(kwargs,http_status=200,raw_response=fixture['response'],observed_at=observed)
            matched = ledger.record_response(**receipt_kwargs)
            assert matched['status']=='response_matched' and matched['reused'] is False
            assert ledger.record_response(**receipt_kwargs)==dict(matched,reused=True)
            assert ledger.reserve(**kwargs)['new_attempt'] is False
            statements = []
            owner = PostgresCardCancellationOwner(lambda: CancellationConnection(statements))
            result = cancel_callback(ctx,fixture,owner=owner)
            assert len(statements) == 5
            assert result == {'status':'card_revoked','card_id':ctx['card'],
                              'reused':False,'execution_authorized':False}
            after = card_row(ctx)
            assert after == dict(before,active=False)
            statements.clear()
            assert cancel_callback(ctx,fixture,owner=owner) == dict(result,reused=True)
            assert len(statements) == 5
            assert card_row(ctx) == after
            rejected(ctx)
            verify(ctx,1)
            verify_prompt_rows(ctx,1)
    ctx['revoked_snapshot'] = card_row(ctx)
    with connect() as c:
        c.execute('insert into private.test_button_revoked_context values(%s)',(Jsonb(ctx),))

    # Same-ID racing reservations yield one initial record; another attempt ID
    # cannot overwrite/refresh the card even when the outcome remains unknown.
    ctx = card_fixture(); record_card(ctx); fixture = cancellation_fixture(ctx)
    ledger = PostgresCancellationMarkupLedger(connect)
    kwargs = markup_args(ctx,fixture,str(uuid4())); barrier = Barrier(8)
    def markup_race(_):
        barrier.wait(timeout=10)
        return ledger.reserve(**kwargs)
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(markup_race,range(8)))
    assert sum(r['new_attempt'] for r in rows)==1
    original = read('select to_jsonb(a) from private.content_ops_button_markup_attempts a where card_id=%s',(ctx['card'],))
    markup_unknown(lambda: ledger.reserve(**dict(kwargs,attempt_id=str(uuid4()))))
    markup_unknown(lambda: ledger.record_response(**kwargs,http_status=400,raw_response=b'{}',observed_at=datetime.now(timezone.utc)))
    # Partial/unknown delivery never becomes a matched result or allows retry.
    assert read('select to_jsonb(a) from private.content_ops_button_markup_attempts a where card_id=%s',(ctx['card'],))==original
    for mutation in ("status='response_matched'", "plan_seal=repeat('d',64)", "expires_at=expires_at+interval '1 second'"):
        sql_refusal(lambda mutation=mutation: read('update private.content_ops_button_markup_attempts set '+mutation+' where card_id=%s returning id',(ctx['card'],)))
    sql_refusal(lambda: read('delete from private.content_ops_button_markup_attempts where card_id=%s returning id',(ctx['card'],)))
    # Real rollback and commit-ACK loss: only a fresh separate read can reconcile.
    class MarkupFaultConnection:
        def __init__(self, lose_ack): self.real=connect(); self.lose_ack=lose_ack
        @property
        def autocommit(self): return self.real.autocommit
        def __enter__(self): self.real.__enter__(); return self
        def cursor(self): return self.real.cursor()
        def __exit__(self,*args):
            if args[0] is not None: return self.real.__exit__(*args)
            if self.lose_ack:
                self.real.__exit__(*args)
            else:
                self.real.__exit__(RuntimeError,RuntimeError('synthetic rollback'),None)
            raise RuntimeError('synthetic acknowledgement unavailable')
    ctx = card_fixture(); record_card(ctx); fixture = cancellation_fixture(ctx)
    kwargs = markup_args(ctx,fixture,str(uuid4()))
    markup_unknown(lambda: PostgresCancellationMarkupLedger(lambda:MarkupFaultConnection(False)).reserve(**kwargs))
    assert read('select count(*) from private.content_ops_button_markup_attempts where card_id=%s',(ctx['card'],))==0
    markup_unknown(lambda: PostgresCancellationMarkupLedger(lambda:MarkupFaultConnection(True)).reserve(**kwargs))
    result=ledger.reserve(**kwargs)
    assert result['new_attempt'] is False and result['status']=='unknown'
    observed=datetime.now(timezone.utc)
    receipt_kwargs=dict(kwargs,http_status=200,raw_response=fixture['response'],observed_at=observed)
    markup_unknown(lambda: PostgresCancellationMarkupLedger(lambda:MarkupFaultConnection(False)).record_response(**receipt_kwargs))
    assert ledger.reserve(**kwargs)['status']=='unknown'
    markup_unknown(lambda: PostgresCancellationMarkupLedger(lambda:MarkupFaultConnection(True)).record_response(**receipt_kwargs))
    assert ledger.reserve(**kwargs)['status']=='response_matched'
    assert ledger.record_response(**receipt_kwargs)['reused'] is True
    # Different-ID races serialize on the card: exactly one can reserve it.
    ctx = card_fixture(); record_card(ctx); fixture = cancellation_fixture(ctx); barrier=Barrier(8)
    def distinct_markup_race(_):
        local_kwargs=markup_args(ctx,fixture,str(uuid4())); barrier.wait(timeout=10)
        try: return ledger.reserve(**local_kwargs)['new_attempt']
        except CancellationMarkupError: return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(distinct_markup_race,range(8)))==1
    assert read('select count(*) from private.content_ops_button_markup_attempts where card_id=%s',(ctx['card'],))==1
    # Current identity and card state remain required even for an identical
    # reservation replay. No default role/identity fallback is permitted.
    ctx = card_fixture(); record_card(ctx); fixture = cancellation_fixture(ctx)
    kwargs = markup_args(ctx,fixture,str(uuid4()))
    markup_unknown(lambda: ledger.reserve(**dict(kwargs,human_id=human+1)))
    markup_unknown(lambda: ledger.reserve(**dict(kwargs,actor_id=str(uuid4()))))
    assert read('select count(*) from private.content_ops_button_markup_attempts where card_id=%s',(ctx['card'],))==0
    ledger.reserve(**kwargs)
    with connect() as c:
        c.execute('update private.content_ops_button_identities set active=false where actor_id=%s',(ctx['actor'],))
    markup_unknown(lambda: ledger.reserve(**kwargs))
    assert read("select status from private.content_ops_button_markup_attempts where card_id=%s",(ctx['card'],))=='unknown'
    ctx = card_fixture(); record_card(ctx); fixture = cancellation_fixture(ctx)
    kwargs=markup_args(ctx,fixture,str(uuid4())); revoke_card(ctx)
    markup_unknown(lambda: ledger.reserve(**kwargs))
    assert read('select count(*) from private.content_ops_button_markup_attempts where card_id=%s',(ctx['card'],))==0

    for substituted in ('message','topic'):
        ctx = card_fixture(); record_card(ctx)
        before = card_row(ctx)
        fixture = cancellation_fixture(ctx)
        if substituted=='message':
            fixture['update']['callback_query']['message']['message_id'] += 1
        else:
            fixture['update']['callback_query']['message']['message_thread_id'] = 37
        statements = []
        owner = PostgresCardCancellationOwner(lambda: CancellationConnection(statements))
        cancellation_unknown(ctx,fixture,owner=owner)
        assert len(statements) == (1 if substituted=='message' else 3)
        assert card_row(ctx) == before
        verify(ctx,1)

    # The callback is initially live, but expires while the actual revoke RPC
    # waits on its identity row. Its post-RPC clock check must roll back the
    # already-executed revocation, not report success or leave an inactive card.
    ctx = card_fixture(); record_card(ctx)
    before = card_row(ctx)
    entered = Event(); waiting_pid = []; statements = []
    def expiring_cancellation_factory():
        connection = CancellationConnection(statements)
        waiting_pid.append(connection.real.info.backend_pid)
        entered.set()
        return connection
    with connect() as blocker:
        blocker.execute('select actor_id from private.content_ops_button_identities where actor_id=%s for update',
                        (ctx['actor'],)).fetchone()
        fixture = cancellation_fixture(ctx,lifetime=2)
        owner = PostgresCardCancellationOwner(expiring_cancellation_factory)
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(cancellation_unknown,ctx,fixture,owner=owner)
            try:
                assert entered.wait(timeout=3)
                wait_for_actual_lock(waiting_pid[0],blocker.info.backend_pid)
                assert len(statements) == 5 and not waiting.done()
                deadline = time.monotonic()+3
                while read('select floor(extract(epoch from clock_timestamp()))::bigint') < fixture['expires_at']:
                    assert time.monotonic()<deadline, 'local token expiry was not observed'
            finally:
                blocker.commit()
            waiting.result(timeout=10)
    assert card_row(ctx) == before and before['active'] is True
    verify(ctx,1)

    for scenario in ('wrong_actor','wrong_bot','wrong_human','wrong_review','wrong_fingerprint',
                     'revoked_identity','revoked_reviewer','inactive_client'):
        ctx = card_fixture(); record_card(ctx)
        kwargs = {}
        if scenario=='wrong_actor': kwargs['actor_id'] = str(uuid4())
        if scenario=='wrong_bot': kwargs['bot_binding'] = binding.digest('bot',102)
        if scenario=='wrong_human': kwargs['human_binding'] = binding.digest('human',bot,202)
        if scenario=='wrong_review': kwargs['review_id'] = str(uuid4())
        if scenario=='wrong_fingerprint': kwargs['fingerprint'] = '0'*64
        with connect() as c:
            if scenario=='revoked_identity':
                c.execute('update private.content_ops_button_identities set active=false where actor_id=%s',
                          (ctx['actor'],))
            if scenario=='revoked_reviewer':
                c.execute('update private.content_ops_button_reviewers set active=false where actor_id=%s',
                          (ctx['actor'],))
            if scenario=='inactive_client':
                c.execute('update public.workspace_clients set active=false where workspace_id=%s and client_id=%s',
                          (ctx['workspace'],ctx['client']))
        before = card_row(ctx)
        try:
            revoke_card(ctx,**kwargs)
        except psycopg.Error as exc:
            expected = ('P0002' if scenario=='wrong_review' else
                        '23514' if scenario in ('wrong_bot','wrong_fingerprint') else '42501')
            assert exc.sqlstate == expected, 'unexpected revoke refusal category'
        else:
            raise AssertionError('invalid revocation must be refused')
        assert card_row(ctx) == before
        verify(ctx,1)

    # Expired review state cannot keep an old card active forever. Cancellation
    # still requires the original immutable IDs/fingerprint and active reviewer.
    ctx = card_fixture(); record_card(ctx)
    with connect() as c:
        c.execute("update private.content_ops_button_reviews set created_at=statement_timestamp()-interval '31 minutes',expires_at=statement_timestamp()-interval '1 minute' where id=%s",
                  (ctx['review'],))
    assert revoke_card(ctx)['reused'] is False
    assert card_row(ctx)['active'] is False
    verify(ctx,1)

    # A rolled-back cancellation leaves the previously committed authority as-is.
    ctx = registered_reserved_prompt()
    before = card_row(ctx)
    with connect() as c:
        assert revoke_card(ctx,connection=c)['reused'] is False
        c.rollback()
    assert card_row(ctx) == before and before['active'] is True
    assert deliver(ctx)['reused'] is False
    verify(ctx,2)

    # Reassigning a Telegram identity to the same actor does not authorize that
    # new human to consume or replay a prompt reserved for the original human.
    for consumed in (False,True):
        ctx = registered_reserved_prompt()
        if consumed:
            assert deliver(ctx)['reused'] is False
        with connect() as c:
            c.execute('update private.content_ops_button_identities set human_binding=%s where actor_id=%s',
                      (binding.digest('human',bot,202),ctx['actor']))
        rejected(ctx,actor=202)
        verify(ctx,2 if consumed else 1)

    # A linked review cannot become legacy by dropping receipt/epoch bindings.
    # These direct mutations affect disposable synthetic records only.
    for broken in ('receipt_link','epoch','receipt_expiry'):
        ctx = registered_reserved_prompt()
        with connect() as c:
            if broken=='receipt_link':
                c.execute('update private.content_ops_button_edit_prompts set owner_receipt_id=null where id=%s',
                          (ctx['prompt'],))
            if broken=='epoch':
                c.execute('update private.content_ops_button_edit_prompts set epoch=epoch+1 where id=%s',
                          (ctx['prompt'],))
            if broken=='receipt_expiry':
                c.execute('update private.content_ops_button_prompt_receipts set reservation_expires_at=null where id=%s',
                          (ctx['attempt'],))
        rejected(ctx)
        verify(ctx,1)

    ctx = card_fixture(); record_card(ctx); cancel_barrier = Barrier(8)
    def cancellation_race(_):
        cancel_barrier.wait(timeout=10)
        return revoke_card(ctx)
    with ThreadPoolExecutor(max_workers=8) as pool:
        cancellations = list(pool.map(cancellation_race,range(8)))
    assert sum(not result['reused'] for result in cancellations) == 1
    assert sum(result['reused'] for result in cancellations) == 7
    assert all(result['execution_authorized'] is False for result in cancellations)
    assert card_row(ctx)['active'] is False
    verify(ctx,1)

    # Deterministic two-session ordering: commit cancellation while the save is
    # visibly waiting on its item lock, then assert that no revision is created.
    ctx = registered_reserved_prompt(); entered = Event(); waiting_pid = []
    def waiting_save_factory():
        connection = connect()
        waiting_pid.append(connection.info.backend_pid)
        entered.set()
        return connection
    with connect() as first:
        assert revoke_card(ctx,connection=first)['reused'] is False
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(rejected,ctx,owner=PostgresEditReplyOwner(waiting_save_factory))
            try:
                assert entered.wait(timeout=3)
                wait_for_actual_lock(waiting_pid[0],first.info.backend_pid)
                assert not waiting.done()
            finally:
                first.commit()
            waiting.result(timeout=10)
    rejected(ctx)
    verify(ctx,1)

    # Reverse ordering: a saved revision commits before cancellation; cancelling
    # its consumed card must not undo it, and must reject all later save replays.
    ctx = registered_reserved_prompt(); entered = Event(); waiting_pid = []
    def waiting_cancel():
        with connect() as connection:
            waiting_pid.append(connection.info.backend_pid)
            entered.set()
            return revoke_card(ctx,connection=connection)
    with connect() as first:
        saved = first.execute('select private.save_content_ops_button_edit_reply(%s,%s,%s,%s,%s,%s,%s)',
            (ctx['prompt'],binding.digest('bot',bot),binding.digest('room',bot,room),
             binding.digest('prompt',bot,room,ctx['message']),binding.digest('human',bot,human),
             ctx['text'],binding.digest('reply',bot,room,ctx['message']+10))).fetchone()[0]
        assert saved['reused'] is False and saved['execution_authorized'] is False
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(waiting_cancel)
            try:
                assert entered.wait(timeout=3)
                wait_for_actual_lock(waiting_pid[0],first.info.backend_pid)
                assert not waiting.done()
            finally:
                first.commit()
            assert waiting.result(timeout=10)['reused'] is False
    assert read('select current_version_id::text from public.content_items where id=%s',
                (ctx['item'],)) == saved['content_version_id']
    assert revoke_card(ctx)['reused'] is True
    rejected(ctx)
    verify(ctx,2)

    # Prove actual runtime roles cannot execute, without weakening any ACL.
    from psycopg import sql
    for role in ('anon','authenticated','service_role'):
        for statement in (
            'select private.save_content_ops_button_edit_reply(null,null,null,null,null,null,null)',
            'select private.register_content_ops_button_edit_prompt(null,null)',
            'select private.record_content_ops_button_card(null,null,null,null,null,null,null,null)',
            'select private.reserve_content_ops_button_prompt_attempt(null,null,null,null,null)',
            'select private.revoke_content_ops_button_card(null,null,null,null,null,null)',
            'select private.assert_content_ops_button_prompt_card_active(null,null,null)',
            'select private.reserve_content_ops_button_markup_attempt(null,null,null,null,null,null,null,null,null,null)',
            'select private.record_content_ops_button_markup_response(null,null,null,null,null,null,null,null)',
        ):
            with connect() as c:
                c.execute(sql.SQL('set local role {}').format(sql.Identifier(role)))
                try:
                    c.execute(statement)
                except psycopg.errors.InsufficientPrivilege:
                    c.rollback()
                else:
                    raise AssertionError('runtime ACL leaked')
    print(json.dumps({'pythonDriver':psycopg.__version__, 'fullSchemaEditCases':8,
        'concurrentRegistrations':8,'newRegisteredPrompts':1,'registrationRefusals':11,
        'concurrentReplies':8,'newRaceRevisions':1,'dbRejectedCases':5,
        'realRollbackVerified':True,'lostCommitAckReconciled':True,'runtimeRolesDenied':3,
        'reservedPromptToEditCases':8,
        'concurrentReceiptRegistrations':8,
        'newAtomicReceipts':1,'newAtomicPrompts':1,'atomicPromptRefusals':9,
        'distinctReceiptContenders':8,'distinctReceiptWinners':1,'distinctReceiptRollbacks':7,
        'atomicPromptRollbackVerified':True,'atomicPromptLostCommitAckReconciled':True,
        'durableAttemptClientChannelCases':8,'durableAttemptReplayContenders':8,
        'durableAttemptDistinctContenders':8,'durableAttemptDistinctWinners':1,
        'durableAttemptRefusals':7,'durableMutationRefusals':4,
        'revokedCardClientChannelCases':8,'revocationRefusals':8,
        'authenticatedCancellationCallbackCases':8,'cancellationOwnerStatementsPerSuccess':5,
        'markupLedgerClientChannelCases':8,'markupSameIdContenders':8,'markupDistinctIdContenders':8,
        'markupRollbackVerified':True,'markupLostCommitAckReconciled':True,'markupMutationRefusals':4,
        'markupResponseRollbackAndLostAckVerified':True,'markupIdentityCardRefusals':4,
        'cancellationCallbackBindingRefusals':2,'cancellationExpiryAfterRpcRollback':True,
        'expiredReviewRevocation':True,'revocationRollbackPreservedReply':True,
        'concurrentRevocations':8,'newRevocations':1,
        'cancelBeforeSaveLockObserved':True,'saveBeforeCancelLockObserved':True,
        'consumedCardReplayDenied':True,
        'reservedHumanRemapRefusals':2,
        'linkedPromptDowngradeRefusals':3,'activeReservedConsumedReplays':8,
        'productionCalls':0,'providerCalls':0,'hostedProof':False}))


if __name__ == '__main__':
    main()
