"""Synthetic runtime adapters; no external DB/Telegram/Storage calls."""
import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from core.content_ops import final_card_preparation_runtime as runtime
from core.content_ops.final_card_preparation_adapters import (
    PreparationReadStore, CanonicalPrivatePngReader, TelegramPrivateFinalCardSender,
)
from core.content_ops.final_card_courier import prepare_final_card
from core.content_ops.final_publication_confirmation import FinalConfirmationSigner
from core.content_ops.publication_route_verification import ExpectedPublicationRoutes, verify_publication_routes
import test_content_ops_final_card_courier as f

P=runtime.PREFIX
EXPECTED=ExpectedPublicationRoutes(f.W,"yellow",f.RELEASE,-1009999999999,"yellowkorea_ann",987654,123,"Yellow__Korea")


def env_fixture():
    return {P+"ENABLED":"true",P+"ENGINE_RELEASE_SHA":f.RELEASE,P+"CALLBACK_RELEASE_SHA":f.CALLBACK_RELEASE,
        P+"RELEASE_PAIR_SHA256":f.PAIR,"RAILWAY_GIT_COMMIT_SHA":f.RELEASE,P+"WORKSPACE_ID":f.W,
        P+"BOT_POLICY_JSON":json.dumps({"schema":"existing-review-bot-policy@1","bot_id":f.BOT,"chat_id":f.ROOM,
            "room_binding":"synthetic-room","membership_status":"member"}),
        P+"REVIEWERS_JSON":json.dumps({f.A:f.HUMAN}),P+"ROUTES_JSON":json.dumps([asdict(EXPECTED)]),
        P+"BOT_TOKEN":str(f.BOT)+":"+"t"*32,P+"SIGNING_KEY_HEX":"ab"*32,P+"EDIT_KEY_HEX":"cd"*32,
        P+"DATABASE_URL":f"postgresql://{runtime.ROLE}:synthetic@db.{runtime.PROJECT}.supabase.co/postgres?sslmode=verify-full",
        P+"PROJECT_KEY":"project-fixture-key-"+"a"*32,P+"STORAGE_READ_TOKEN":"scoped-fixture-read-"+"b"*32}


def store():
    return PreparationReadStore(lambda:None,workspace_id=f.W,engine=f.RELEASE,callback=f.CALLBACK_RELEASE,
        pair=f.PAIR,routes={"yellow":EXPECTED})


class RuntimeTests(unittest.TestCase):
    def test_off_only_flag_zero_io_even_validate(self):
        class Off(dict):
            def get(self,key,default=None):
                if key!=P+"ENABLED": raise AssertionError("config read")
                return "false"
        with patch.object(runtime.PreparationSettings,"from_env",side_effect=AssertionError("compose")):
            for validate in (False,True):
                receipt=runtime.run(environ=Off(),validate_only=validate)
                self.assertEqual(receipt["status"],"disabled")
                self.assertFalse(receipt["network_calls"])

    def test_enabled_config_is_lazy_and_pair_is_independent(self):
        import psycopg
        with patch.object(psycopg,"connect",side_effect=AssertionError("DB")), \
             patch.object(httpx.AsyncClient,"__aenter__",side_effect=AssertionError("HTTP")):
            settings=runtime.PreparationSettings.from_env(env_fixture(),stamp_reader=lambda:f.RELEASE)
            dispatcher=runtime.build_dispatcher(settings)
            self.assertIsInstance(dispatcher,runtime.PreparationDispatcher)
            worker=dispatcher.worker_factory()
            self.assertEqual(worker._pair,(f.RELEASE,f.CALLBACK_RELEASE,f.PAIR))
            receipt=runtime.run(environ=env_fixture(),validate_only=True,stamp_reader=lambda:f.RELEASE)
            self.assertTrue(receipt["configuration_validated"])
            self.assertFalse(receipt["runtime_acl_verified"])
            self.assertFalse(receipt["release_pair_registered"])

    def test_bad_config_fails_before_network_and_redacts(self):
        bad=[(P+"ENGINE_RELEASE_SHA",f.CALLBACK_RELEASE),(P+"RELEASE_PAIR_SHA256","d"*64),
            ("RAILWAY_GIT_COMMIT_SHA","b"*40),(P+"EDIT_KEY_HEX","ab"*32),
            (P+"REVIEWERS_JSON",json.dumps({f.A:f.BOT})),(P+"ROUTES_JSON",json.dumps([asdict(EXPECTED)]*2)),
            (P+"DATABASE_URL","SECRET_BAD_DSN"),("TELEGRAM_PUBLIC_BOT_TOKEN","SECRET_PUBLIC"),
            (P+"STORAGE_READ_TOKEN",env_fixture()[P+"PROJECT_KEY"])]
        for key,value in bad:
            env=env_fixture(); env[key]=value
            with self.subTest(key=key):
                receipt=runtime.run(environ=env,validate_only=True,stamp_reader=lambda:f.RELEASE)
                self.assertFalse(receipt["ok"])
                self.assertNotIn("SECRET",json.dumps(receipt))

    def test_dedicated_db_policy_rejects_admin_host_and_ssl_drift(self):
        dsn=env_fixture()[P+"DATABASE_URL"]
        for bad in (dsn.replace(runtime.ROLE,"postgres"),dsn.replace("verify-full","require"),
                    dsn+"&options=-c%20role=postgres",dsn.replace("db.","evil.")):
            with self.assertRaisesRegex(ValueError,"final_preparation_database_config_invalid"):
                runtime.restricted_factory(bad,"/fixture-ca")

    def test_database_connection_rechecks_ca_ambient_and_actual_role(self):
        import psycopg
        factory=runtime.restricted_factory(env_fixture()[P+"DATABASE_URL"],"/fixture-ca",read_only=True)
        with patch.dict(os.environ,{"PGSERVICE":"forbidden"}),patch.object(psycopg,"connect") as connect:
            with self.assertRaisesRegex(ValueError,"final_preparation_database_identity_invalid"): factory()
            connect.assert_not_called()
        with patch.dict(os.environ,{},clear=True),patch.object(runtime.Path,"read_bytes",return_value=b"untrusted"), \
             patch.object(psycopg,"connect") as connect:
            with self.assertRaisesRegex(ValueError,"final_preparation_database_identity_invalid"): factory()
            connect.assert_not_called()
        digest=Mock(); digest.hexdigest.return_value=runtime.CA_SHA
        with patch.dict(os.environ,{},clear=True),patch.object(runtime.Path,"read_bytes",return_value=b"fixture"), \
             patch.object(runtime.hashlib,"sha256",return_value=digest),patch.object(psycopg,"connect") as connect:
            conn=connect.return_value; cur=conn.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value=(runtime.ROLE,runtime.ROLE,False,False,False,False,False,False)
            self.assertIs(factory(),conn); self.assertTrue(conn.read_only)
            cur.fetchone.return_value=("postgres","postgres",True,False,False,False,True,False)
            with self.assertRaisesRegex(ValueError,"final_preparation_database_identity_invalid"): factory()
            conn.close.assert_called_once()

    def test_dispatcher_fresh_worker_per_intent_and_stop_unknown(self):
        s=Mock(); s.pending=AsyncMock(return_value=(f.D,f.C))
        good={"status":"card_registered","confirmed_parts":4,"public_send_attempted":False}
        worker=Mock(); worker.run=AsyncMock(return_value=good); factory=Mock(return_value=worker)
        d=runtime.PreparationDispatcher(s,factory)
        self.assertEqual(asyncio.run(d.run())["status"],"disabled"); s.pending.assert_not_called()
        self.assertEqual(asyncio.run(d.run(enabled=True))["cards_recorded"],2)
        self.assertEqual(factory.call_count,2)
        worker.run=AsyncMock(return_value={"status":"outcome_unknown","public_send_attempted":False})
        self.assertEqual(asyncio.run(d.run(enabled=True))["considered"],1)


class ReadAdaptersTests(unittest.TestCase):
    def test_discovery_bounded_exact_pair_and_no_mutation_sql(self):
        s=store(); s._read=Mock(return_value=[(f.D,)])
        self.assertEqual(asyncio.run(s.pending()),(f.D,))
        query,args=s._read.call_args.args
        self.assertIn("limit 4",query); self.assertIn("p.claimed_at is null",query)
        for guard in ("p.epoch=r.epoch", "selected.selected_at<=p.created_at", "r.state='active'",
                      "i.current_version_id=r.content_version_id", "identity.active"):
            self.assertLess(query.index(guard),query.rindex("limit 4"))
        self.assertNotIn("for update",query.lower()); self.assertNotIn("preflight(",query)
        self.assertEqual(args,(f.W,["yellow"],f.RELEASE,f.CALLBACK_RELEASE,f.PAIR))
        s._read.return_value=[(f.D,)]*2
        with self.assertRaises(ValueError): asyncio.run(s.pending())

    def test_routes_use_recent_attested_digest_not_fabricated_observation(self):
        now=datetime.now(timezone.utc)
        routes=verify_publication_routes(EXPECTED,telegram_bot={"id":EXPECTED.telegram_bot_id,"is_bot":True},
            telegram_channel={"id":EXPECTED.telegram_channel_id,"type":"channel","username":EXPECTED.telegram_username},
            telegram_member={"status":"administrator","can_post_messages":True,"user":{"id":EXPECTED.telegram_bot_id,"is_bot":True}},
            typefully_social_set={"id":123,"platforms":{"x":{"platform":"x","username":EXPECTED.x_username,
                "profile_url":"https://x.com/"+EXPECTED.x_username},"linkedin":None,"mastodon":None,
                "threads":None,"bluesky":None,"substack":None}},observed_at=now,now=now)
        s=store(); s._read=Mock(return_value=[("telegram",routes.telegram_route_binding,now),
            ("typefully_x",routes.typefully_route_binding,now)])
        self.assertEqual(asyncio.run(s.verified_routes(f.W,"yellow",f.RELEASE)),replace(routes,
            typefully_x_destination_label=routes.typefully_x_destination_label.lower()))
        for row in (["telegram","f"*64,now],["telegram",routes.telegram_route_binding,now-timedelta(minutes=16)]):
            s._read.return_value=[row,("typefully_x",routes.typefully_route_binding,now)]
            with self.assertRaises(ValueError): asyncio.run(s.verified_routes(f.W,"yellow",f.RELEASE))

    def test_png_scope_hash_size_private_get_only(self):
        s=store(); path=f"{f.W}/yellow/{f.C}/news-card.png"
        s._read=Mock(return_value=[(len(f.PNG),path)])
        calls=[]
        def handle(request):
            calls.append(request)
            return httpx.Response(200,content=f.PNG,headers={"Content-Type":"image/png"})
        reader=CanonicalPrivatePngReader(s,project_key="k"*32,storage_read_token="t"*32,transport=httpx.MockTransport(handle))
        digest=hashlib.sha256(f.PNG).hexdigest()
        self.assertEqual(asyncio.run(reader(f.W,"yellow",f.V,f.C,digest)),f.PNG)
        self.assertEqual(calls[0].method,"GET"); self.assertEqual(calls[0].url.host,f"{runtime.PROJECT}.supabase.co")
        self.assertTrue(calls[0].url.path.endswith(path))
        with self.assertRaisesRegex(ValueError,"final_preparation_png_unknown"):
            asyncio.run(reader(f.W,"yellow",f.V,f.C,"f"*64))
        before=len(calls)
        with self.assertRaises(ValueError): asyncio.run(reader(f.R,"yellow",f.V,f.C,digest))
        self.assertEqual(len(calls),before)

    def test_png_oversize_redirect_and_raw_error_sanitized(self):
        for response in (httpx.Response(302,headers={"location":"https://evil.invalid/SECRET"}),
                httpx.Response(200,content=f.PNG+b"extra",headers={"Content-Type":"image/png"}),
                httpx.Response(500,content=b"SECRET_PROVIDER")):
            s=store(); s._read=Mock(return_value=[(len(f.PNG),f"{f.W}/yellow/{f.C}/news-card.png")])
            reader=CanonicalPrivatePngReader(s,project_key="k"*32,storage_read_token="t"*32,
                transport=httpx.MockTransport(lambda _:response))
            with self.assertRaisesRegex(ValueError,"^final_preparation_png_unknown$"):
                asyncio.run(reader(f.W,"yellow",f.V,f.C,hashlib.sha256(f.PNG).hexdigest()))


class FinalSenderTests(unittest.TestCase):
    def packet(self):
        return prepare_final_card(replace(f.prepared().snapshot,decision_mode="telegram_and_typefully_draft"),
            FinalConfirmationSigner(b"s"*32),"fixture-room",now=f.NOW)

    def test_private_preflight_exact_final_packet_four_sends_only(self):
        packet=self.packet(); methods=[]; index=0
        def handler(req):
            nonlocal index
            method=req.url.path.rsplit("/",1)[-1]; methods.append(method)
            if method=="getMe": result={"id":f.BOT,"is_bot":True,"username":"coineasy_review_bot"}
            elif method=="getChat": result={"id":f.ROOM,"type":"supergroup"}
            elif method=="getChatMember": result={"status":"member","user":{"id":f.BOT,"is_bot":True}}
            else:
                expected=packet[index]
                result={"message_id":100+index,"date":f.NOW,"chat":{"id":f.ROOM,"type":"supergroup"},"from":{"id":f.BOT,"is_bot":True}}
                result["caption" if index==0 else "text"]=expected["text"]
                if index==0: result["photo"]=[{"file_id":"fixture"}]
                if index==3: result["reply_markup"]=expected["reply_markup"]
                index+=1
            return httpx.Response(200,json={"ok":True,"result":result})
        sender=TelegramPrivateFinalCardSender(bot_token=str(f.BOT)+":"+"t"*32,bot_id=f.BOT,chat_id=f.ROOM,
            transport=httpx.MockTransport(handler),clock=lambda:datetime.fromtimestamp(f.NOW,timezone.utc))
        async def exact():
            await sender.preflight(bot_id=f.BOT,chat_id=f.ROOM)
            for n,request in enumerate(packet): await sender.send_once(request,png=f.PNG if n==0 else None)
            with self.assertRaisesRegex(RuntimeError,"final_card_telegram_unknown"):
                await sender.send_once(packet[-1],png=None)
        asyncio.run(exact())
        self.assertEqual(methods,["getMe","getChat","getChatMember","sendPhoto","sendMessage","sendMessage","sendMessage"])

    def test_ce1_controls_or_unknown_sender_never_retries(self):
        sender=TelegramPrivateFinalCardSender(bot_token=str(f.BOT)+":"+"t"*32,bot_id=f.BOT,chat_id=f.ROOM)
        sender._verified=True; sender._next_index=3; sender._post=AsyncMock(side_effect=TimeoutError("SECRET"))
        packet=self.packet(); bad=json.loads(json.dumps(packet[3]))
        bad["reply_markup"]["inline_keyboard"][0][0]["callback_data"]="ce1:"+"a"*51
        with self.assertRaisesRegex(RuntimeError,"final_card_telegram_unknown"): asyncio.run(sender.send_once(bad,png=None))
        sender._post.assert_not_called()
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError,"^final_card_telegram_unknown$"): asyncio.run(sender.send_once(packet[3],png=None))
        self.assertEqual(sender._post.call_count,1)


if __name__=="__main__": unittest.main()
