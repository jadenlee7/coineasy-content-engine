"""Actual default-OFF Engine dispatcher composition; no second Telegram poller.

Credentials and role grants are separately provisioned. This service receives
only the existing private review bot, final signer, binding key, restricted DB
and Storage read credentials. It never owns official-channel/Typefully tokens.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
from dataclasses import dataclass

from core.content_ops.final_card_preparation import (
    FinalPreparationWorker, PostgresFinalPreparationOwner, PostgresPreparedFinalCardOwner,
)
from core.content_ops.final_card_preparation_adapters import (
    CanonicalPrivatePngReader, PreparationReadStore, TelegramPrivateFinalCardSender,
)
from core.content_ops.final_card_courier import FinalCardCourier
from core.content_ops.final_publication_confirmation import FinalConfirmationSigner
from core.content_ops.private_review_bot_policy import ExistingReviewBotPolicy
from core.content_ops.publication_route_verification import ExpectedPublicationRoutes, validate_expected_publication_routes
from core.content_ops.prompt_receipt import canonical_uuid
from core.content_ops.release_pair import valid_release_pair
from core.content_ops.review_edit_ingress import EditBindings

PREFIX="CONTENT_OPS_FINAL_PREPARATION_"
ROLE="coineasy_final_card_preparation"
PROJECT="isuqcqwxpojgzevxfdwr"
STAMP=Path("/app/content-ops-build-sha")
CA_SHA="700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7"


def enabled(env):
    value=env.get(PREFIX+"ENABLED","false")
    if value not in ("false","true"): raise ValueError("final_preparation_flag_invalid")
    return value=="true"


def _require(condition):
    if not condition: raise ValueError("final_preparation_configuration_invalid")


def _json(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            _require(key not in result); result[key]=value
        return result
    _require(type(raw) is str and 0<len(raw)<=32768)
    return json.loads(raw,object_pairs_hook=unique)


def restricted_factory(dsn, ca_path, *, read_only=False):
    """Validate pure connection policy now; verify actual least-privilege role later."""
    from psycopg.conninfo import conninfo_to_dict
    try:
        fields=conninfo_to_dict(dsn)
        endpoints={(f"db.{PROJECT}.supabase.co",ROLE),
            ("aws-1-ap-northeast-2.pooler.supabase.com",ROLE+"."+PROJECT)}
        _require(type(read_only) is bool and type(ca_path) is str and Path(ca_path).is_absolute()
            and set(fields)<={"host","port","dbname","user","password","sslmode"}
            and (fields.get("host"),fields.get("user")) in endpoints
            and fields.get("port","5432")=="5432" and fields.get("dbname")=="postgres"
            and fields.get("password") and fields.get("sslmode")=="verify-full")
        fields.setdefault("port","5432")
    except Exception:
        raise ValueError("final_preparation_database_config_invalid") from None
    def connect():
        import psycopg
        conn=None
        try:
            # No libpq service file, alternative address or ambient credentials.
            _require(not any(key.startswith("PG") and value for key,value in os.environ.items())
                and hashlib.sha256(Path(ca_path).read_bytes()).hexdigest()==CA_SHA)
            conn=psycopg.connect(**fields,sslrootcert=ca_path,gssencmode="disable",connect_timeout=5,
                autocommit=False,options="-c statement_timeout=5000 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=10000")
            conn.read_only=read_only
            with conn.cursor() as cur:
                cur.execute("""select current_user,session_user,rolsuper,rolcreaterole,rolcreatedb,rolreplication,
                    rolbypassrls,exists(select 1 from pg_auth_members m where m.member=r.oid)
                    from pg_roles r where rolname=current_user""")
                _require(cur.fetchone()==(ROLE,ROLE,False,False,False,False,False,False))
            return conn
        except Exception:
            if conn is not None: conn.close()
            raise ValueError("final_preparation_database_identity_invalid") from None
    return connect


@dataclass(frozen=True,repr=False)
class PreparationSettings:
    workspace: str
    engine: str
    callback: str
    pair: str
    policy: ExistingReviewBotPolicy
    reviewers: dict
    routes: dict
    bot_token: str
    signing_key: bytes
    binding_key: bytes
    dsn: str
    ca_path: str
    project_key: str
    storage_token: str

    @classmethod
    def from_env(cls,env,*,stamp_reader=None):
        try:
            engine=env[PREFIX+"ENGINE_RELEASE_SHA"]; callback=env[PREFIX+"CALLBACK_RELEASE_SHA"]
            pair=env[PREFIX+"RELEASE_PAIR_SHA256"]
            _require(valid_release_pair(engine,callback,pair)
                and env.get("RAILWAY_GIT_COMMIT_SHA")==engine
                and (stamp_reader or STAMP.read_text)()==engine)
            workspace=env[PREFIX+"WORKSPACE_ID"]
            _require(canonical_uuid(workspace))
            policy_raw=env[PREFIX+"BOT_POLICY_JSON"]; p=_json(policy_raw)
            policy=ExistingReviewBotPolicy.from_json(policy_raw,bot_id=p["bot_id"],chat_id=p["chat_id"])
            reviewers=_json(env[PREFIX+"REVIEWERS_JSON"])
            _require(type(reviewers) is dict and 1<=len(reviewers)<=100
                and all(canonical_uuid(k) and type(v) is int and 0<v<2**52 and v!=policy.bot_id for k,v in reviewers.items())
                and len(set(reviewers.values()))==len(reviewers))
            raw_routes=_json(env[PREFIX+"ROUTES_JSON"])
            _require(type(raw_routes) is list and 1<=len(raw_routes)<=4)
            routes={}
            for row in raw_routes:
                expected=ExpectedPublicationRoutes(**row); validate_expected_publication_routes(expected)
                _require(expected.workspace_id==workspace and expected.release_sha==engine and expected.client_id not in routes)
                routes[expected.client_id]=expected
            key=env[PREFIX+"SIGNING_KEY_HEX"]; binding=env[PREFIX+"EDIT_KEY_HEX"]
            _require(all(type(v) is str and re.fullmatch(r"[a-f0-9]{64}",v) for v in (key,binding)) and key!=binding)
            settings=cls(workspace,engine,callback,pair,policy,reviewers,routes,
                env[PREFIX+"BOT_TOKEN"],bytes.fromhex(key),bytes.fromhex(binding),env[PREFIX+"DATABASE_URL"],
                env.get(PREFIX+"DATABASE_CA_PATH","/app/certs/supabase-prod-ca-2021.crt"),
                env[PREFIX+"PROJECT_KEY"],env[PREFIX+"STORAGE_READ_TOKEN"])
            # Reject unrelated authority on this dedicated service. Exact scoped
            # secrets above are not discovery fallbacks to shared credentials.
            forbidden=("SUPABASE_","TELEGRAM_","TYPEFULLY_","OPENAI_","XAI_","AWS_","DATABASE_","PG")
            _require(not any(value and (name.startswith(forbidden) or name in {"API_SECRET","PUBLICATION_WORKER_TOKEN"})
                for name,value in env.items()))
            restricted_factory(settings.dsn,settings.ca_path)
            TelegramPrivateFinalCardSender(bot_token=settings.bot_token,bot_id=policy.bot_id,
                chat_id=policy.chat_id,bot_policy=policy)
            store=PreparationReadStore(lambda:None,workspace_id=workspace,engine=engine,callback=callback,pair=pair,routes=routes)
            CanonicalPrivatePngReader(store,project_key=settings.project_key,storage_read_token=settings.storage_token)
            return settings
        except Exception:
            raise ValueError("final_preparation_configuration_invalid") from None


class PreparationDispatcher:
    def __init__(self,store,worker_factory):
        self.store,self.worker_factory=store,worker_factory

    async def run(self,*,enabled=False):
        result={"status":"disabled","considered":0,"cards_recorded":0,"public_send_attempted":False}
        if enabled is not True: return result
        result["status"]="no_candidate"
        try:
            ids=await self.store.pending()
            for intent in ids:
                result["considered"]+=1
                receipt=await self.worker_factory().run(intent,enabled=True)
                if (type(receipt) is not dict or set(receipt)!={"status","confirmed_parts","public_send_attempted"}
                    or receipt["status"]!="card_registered" or type(receipt["confirmed_parts"]) is not int
                    or receipt["confirmed_parts"]!=4 or receipt["public_send_attempted"] is not False):
                    return {**result,"status":"outcome_unknown"}
                result["cards_recorded"]+=1
            return {**result,"status":"completed" if ids else "no_candidate"}
        except Exception:
            return {**result,"status":"outcome_unknown"}


def build_dispatcher(settings):
    write=restricted_factory(settings.dsn,settings.ca_path)
    read=restricted_factory(settings.dsn,settings.ca_path,read_only=True)
    store=PreparationReadStore(read,workspace_id=settings.workspace,engine=settings.engine,
        callback=settings.callback,pair=settings.pair,routes=settings.routes)
    bindings=EditBindings(settings.binding_key)
    reader=CanonicalPrivatePngReader(store,project_key=settings.project_key,storage_read_token=settings.storage_token)
    def fresh_worker():
        owner=PostgresFinalPreparationOwner(write,release_sha=settings.engine,
            callback_release_sha=settings.callback,release_pair_sha256=settings.pair,enabled=True)
        sender=TelegramPrivateFinalCardSender(bot_token=settings.bot_token,bot_id=settings.policy.bot_id,
            chat_id=settings.policy.chat_id,bot_policy=settings.policy)
        courier=FinalCardCourier(PostgresPreparedFinalCardOwner(write,enabled=True),sender,
            FinalConfirmationSigner(settings.signing_key),bindings,runtime_release_sha=settings.engine)
        return FinalPreparationWorker(owner,courier,bindings=bindings,bot_id=settings.policy.bot_id,
            chat_id=settings.policy.chat_id,reviewers=settings.reviewers,png_reader=reader,
            route_reader=store.verified_routes,runtime_engine_release_sha=settings.engine,
            callback_release_sha=settings.callback,release_pair_sha256=settings.pair)
    return PreparationDispatcher(store,fresh_worker)


def run(*,environ=None,validate_only=False,stamp_reader=None,dispatcher_factory=build_dispatcher):
    env=os.environ if environ is None else environ
    base={"mode":"validate_only" if validate_only else "run","public_send_attempted":False}
    zero={"network_calls":False,"database_calls":False,"telegram_calls":False,
          "runtime_acl_verified":False,"release_pair_registered":False}
    try:
        if not enabled(env): return {**base,**zero,"ok":True,"enabled":False,"status":"disabled"}
        settings=PreparationSettings.from_env(env,stamp_reader=stamp_reader)
        if validate_only: return {**base,**zero,"ok":True,"enabled":True,"configuration_validated":True}
        receipt=asyncio.run(dispatcher_factory(settings).run(enabled=True))
        return {**base,**receipt,"ok":receipt["status"] in {"completed","no_candidate"},"enabled":True}
    except Exception:
        return {**base,**(zero if validate_only else {}),"ok":False,"error":"final_preparation_failed"}
