import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { FRESH_BABYLON_TRIAL_ID, NEW_BABYLON_TRIAL_ID, NEW_BABYLON_EARLIEST, READY_ANCHORED_TRIAL_ID, configuredTrialWindow,
  trialIdentityPolicy } from "../netlify/functions/_shared/content-ops-trial-window.mts";
import { admitTrialGeneration } from "../netlify/functions/_shared/content-ops-trial-generation.mts";
import { admitTrial, trialManifestHash, verifyTrialProjection } from "../netlify/functions/_shared/content-ops-trial.mts";
import { createTrialManifestResolver, createTrialDeliveryConfirmation } from "../netlify/functions/_shared/content-ops-trial-resolver.mts";
import { createTrialStateHandler } from "../netlify/functions/_shared/content-ops-trial-state.mts";
import { createYboTrialPreflightHandler, YBO_TRIAL_REQUIRED_OFF_FLAGS } from "../netlify/functions/_shared/ybo-trial-preflight.mts";

const TRIAL = FRESH_BABYLON_TRIAL_ID, START = "2026-10-15T15:10:00Z", END = "2026-10-15T17:10:00Z";
const NOW = Date.parse("2026-10-15T15:30:00Z"), RELEASE = "a".repeat(40), PLAN = "b".repeat(64), POLICY = "c".repeat(64);
const ORIGIN = "https://coineasy-newscard.netlify.app", API = "https://coineasy-content-engine-production.up.railway.app";
const id = (n: number) => `${String(n).padStart(8,"0")}-1111-4111-8111-111111111111`;
const sha = (v: string) => createHash("sha256").update(v).digest("hex");
const SOURCE = "Babylon official current natural source for private review only.";
const env: Record<string,string> = {
  ...Object.fromEntries(YBO_TRIAL_REQUIRED_OFF_FLAGS.map(k => [k,"false"])),
  YBO_TRIAL_ID: TRIAL, YBO_TRIAL_PLAN_SHA256: PLAN, YBO_TRIAL_RELEASE_SHA: RELEASE,
  YBO_TRIAL_NOT_BEFORE: START, YBO_TRIAL_DEADLINE: END, YBO_TRIAL_GENERATION_GATEWAY_ENABLED: "true",
  CONTENT_OPS_TRIAL_ID: TRIAL, CONTENT_OPS_TRIAL_PLAN_SHA256: PLAN, CONTENT_OPS_REVIEW_RELEASE_SHA: RELEASE,
  CONTENT_OPS_TRIAL_NOT_BEFORE: START, CONTENT_OPS_TRIAL_DEADLINE: END, CONTENT_OPS_TRIAL_GATEWAY_ENABLED: "true",
  CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256: POLICY, API_SECRET: "fixture-api-existing-secret",
  RAILWAY_API_URL: API, CONTENT_OPS_GATEWAY_TOKEN: "fixture-dedicated-gateway-token-32-characters",
  STUDIO_AUTOMATION_TOKEN: "fixture-automation-token-at-least-32-characters",
};
const native = { deploy: { context: "production", id: "published-fixture", published: true } };
const entry = { client_id: "babylon", job_id: id(1), request_id: id(2), source_item_id: id(3),
  source_sha256: sha(SOURCE), content_item_id: id(2), content_version_id: id(4), banner_sha256: "d".repeat(64),
  telegram_copy_sha256: sha("직원 검수 공지"), x_copy_sha256: sha("직원 검수 X 초안") };
const manifest = { contract: "ybo-trial-courier-manifest@1", trial_id: TRIAL, plan_sha256: PLAN,
  release_sha: RELEASE, not_before: START, deadline: END, destination_policy_sha256: POLICY, entries: [entry] };
const manifestHash = trialManifestHash(manifest);
const value = { active: true, observed_at: new Date(NOW).toISOString(), manifest, manifest_sha256: manifestHash };
const identity = { trial_id: TRIAL, plan_sha256: PLAN };
const payload = { source_content: SOURCE, source_type: "tweet", source_url: "https://x.com/babylonlabs_io/status/1234567890",
  source_image_url: "", mock_mode: false, template_style: "remix", style_references: [], style_reference_pack_hash: "",
  brand_review_guidance: null, quality_context: { client_id: "babylon", request_id: id(2), job_id: id(1) } };
function generationReq(client = "babylon", trial = TRIAL) { return new Request(`${ORIGIN}/api/news-card/${client}`, { method: "POST",
  headers: { "x-ybo-trial-id": trial, "x-ybo-trial-plan-sha256": PLAN, "x-studio-expected-release-sha": RELEASE } }); }
function courierReq(m = manifest) { return new Request(`${ORIGIN}/.netlify/functions/content-ops-review`, { headers: {
  "x-content-ops-mode": "trial", "x-content-ops-packet-mode": "trial_delivery_only_v1",
  "x-content-ops-version-id": m.entries[0].content_version_id, "x-content-ops-trial-id": m.trial_id,
  "x-content-ops-trial-plan-sha256": PLAN, "x-content-ops-trial-manifest-sha256": trialManifestHash(m) } }); }
const resolve = async () => value;

test("fresh fixed identity narrows clients/count/date without changing legacy policy", () => {
  assert.deepEqual(trialIdentityPolicy(TRIAL)?.clientIds,["babylon"]);
  assert.equal(trialIdentityPolicy(TRIAL)?.maxTotal,1);
  assert.deepEqual(trialIdentityPolicy(READY_ANCHORED_TRIAL_ID)?.clientIds,["babylon","origintrail"]);
  assert.equal(trialIdentityPolicy(READY_ANCHORED_TRIAL_ID)?.maxTotal,2);
  for(const v of [undefined,"",id(33),TRIAL.toUpperCase(),TRIAL+","+TRIAL]) assert.equal(trialIdentityPolicy(v),null);
  assert(Object.isFrozen(trialIdentityPolicy(TRIAL))); assert(Object.isFrozen(trialIdentityPolicy(TRIAL)?.clientIds));
  for(const prefix of ["YBO_TRIAL","CONTENT_OPS_TRIAL"] as const) {
    assert.equal(configuredTrialWindow(k=>env[k],prefix)?.kstDate,"2026-10-16");
    for(const [start,end] of [["2026-10-15T12:00:00Z","2026-10-15T14:00:00Z"],
      ["2026-10-16T15:00:00Z","2026-10-16T17:00:00Z"],
      ["2026-10-16T14:00:00Z","2026-10-16T16:00:00Z"]]) {
      const e={...env,[`${prefix}_NOT_BEFORE`]:start,[`${prefix}_DEADLINE`]:end};
      assert.equal(configuredTrialWindow(k=>e[k],prefix),null);
    }
  }
});

test("new Babylon identity is separate, bounded and allowed across both gateways", async () => {
  const start="2026-10-15T15:48:00Z", end="2026-10-15T17:48:00Z", now=Date.parse("2026-10-15T16:00:00Z");
  assert.equal(NEW_BABYLON_EARLIEST,"2026-10-15T15:47:35Z");
  const policy=trialIdentityPolicy(NEW_BABYLON_TRIAL_ID);
  assert.deepEqual(policy?.clientIds,["babylon"]);assert.equal(policy?.maxPerClient,1);assert.equal(policy?.maxTotal,1);
  assert(Object.isFrozen(policy));assert(Object.isFrozen(policy?.clientIds));
  assert.equal(trialIdentityPolicy(FRESH_BABYLON_TRIAL_ID)?.earliest,Date.parse("2026-10-15T15:00:00Z"));
  const e={...env,YBO_TRIAL_ID:NEW_BABYLON_TRIAL_ID,CONTENT_OPS_TRIAL_ID:NEW_BABYLON_TRIAL_ID,
    YBO_TRIAL_NOT_BEFORE:start,YBO_TRIAL_DEADLINE:end,CONTENT_OPS_TRIAL_NOT_BEFORE:start,CONTENT_OPS_TRIAL_DEADLINE:end};
  for(const prefix of ["YBO_TRIAL","CONTENT_OPS_TRIAL"] as const){
    assert.equal(configuredTrialWindow(k=>e[k],prefix)?.kstDate,"2026-10-16");
    for(const [s,d] of [["2026-10-15T15:47:34Z","2026-10-15T17:47:34Z"],
      ["2026-10-16T15:48:00Z","2026-10-16T17:48:00Z"]]){
      const invalid={...e,[`${prefix}_NOT_BEFORE`]:s,[`${prefix}_DEADLINE`]:d};
      assert.equal(configuredTrialWindow(k=>invalid[k],prefix),null);
    }
  }
  let calls=0;
  const freshPayload={...payload,trial_payload:payload};
  const generated=await admitTrialGeneration({req:generationReq("babylon",NEW_BABYLON_TRIAL_ID),runtime:native,
    body:freshPayload,clientId:"babylon",requestId:id(2),automation:true,env:k=>e[k],clock:()=>now,release:RELEASE,
    fetcher:async()=>{calls++;return Response.json({authorized:true,trial_id:NEW_BABYLON_TRIAL_ID,
      plan_sha256:PLAN,release_sha:RELEASE,client_id:"babylon",request_id:id(2),observed_at:new Date(now).toISOString()});}});
  assert.equal(generated.trialId,NEW_BABYLON_TRIAL_ID);assert.equal(calls,1);
  const m={...manifest,trial_id:NEW_BABYLON_TRIAL_ID,not_before:start,deadline:end};
  const admitted=await admitTrial(courierReq(m),k=>e[k],RELEASE,()=>now,
    async()=>({active:true,observed_at:new Date(now).toISOString(),manifest:m,manifest_sha256:trialManifestHash(m)}));
  assert.equal(admitted.entry.client_id,"babylon");
  for(const client of ["origintrail","yellow"]){
    await assert.rejects(()=>admitTrialGeneration({req:generationReq(client,NEW_BABYLON_TRIAL_ID),runtime:native,
      body:freshPayload,clientId:client,requestId:id(2),automation:true,env:k=>e[k],clock:()=>now,release:RELEASE,
      fetcher:async()=>{calls++;throw Error("unexpected HTTP");}}));
  }
  assert.equal(calls,1);
  for(const trialId of [id(99),FRESH_BABYLON_TRIAL_ID,READY_ANCHORED_TRIAL_ID]){
    const wrong={...m,trial_id:trialId};
    await assert.rejects(()=>admitTrial(courierReq(wrong),k=>e[k],RELEASE,()=>now,
      async()=>({active:true,observed_at:new Date(now).toISOString(),manifest:wrong,manifest_sha256:trialManifestHash(wrong)})));
  }
});

test("fresh generation accepts exact Babylon and calls existing durable authorization first", async () => {
  const calls: any[]=[];
  const admitted=await admitTrialGeneration({req:generationReq(),runtime:native,body:{...payload,trial_payload:payload},
    clientId:"babylon",requestId:id(2),automation:true,env:k=>env[k],clock:()=>NOW,release:RELEASE,
    fetcher:async (url,init)=>{calls.push({url,body:JSON.parse(String(init?.body))});return Response.json({authorized:true,
      ...identity,release_sha:RELEASE,client_id:"babylon",request_id:id(2),observed_at:new Date(NOW).toISOString()});}});
  assert.equal(admitted.trialId,TRIAL); assert.equal(calls.length,1);
  assert.equal(calls[0].url,API+"/internal/trial/generation-authorize");
  assert.deepEqual(calls[0].body,{...identity,release_sha:RELEASE,client_id:"babylon",request_id:id(2),payload});
});

test("fresh generation rejects other clients, legacy request identity and early anchor before HTTP", async () => {
  for(const c of ["yellow","origintrail","squid"]) {
    let calls=0; await assert.rejects(()=>admitTrialGeneration({req:generationReq(c),runtime:native,
      body:{...payload,trial_payload:payload},clientId:c,requestId:id(2),automation:true,env:k=>env[k],clock:()=>NOW,release:RELEASE,
      fetcher:async()=>{calls++;throw Error("forbidden");}}));assert.equal(calls,0);
  }
  for(const delta of [{YBO_TRIAL_ID:READY_ANCHORED_TRIAL_ID},
    {YBO_TRIAL_NOT_BEFORE:"2026-10-15T12:00:00Z",YBO_TRIAL_DEADLINE:"2026-10-15T14:00:00Z"}]) {
    let calls=0;const e={...env,...delta};await assert.rejects(()=>admitTrialGeneration({req:generationReq(),runtime:native,
      body:{...payload,trial_payload:payload},clientId:"babylon",requestId:id(2),automation:true,env:k=>e[k],clock:()=>NOW,release:RELEASE,
      fetcher:async()=>{calls++;throw Error("forbidden");}}));assert.equal(calls,0);
  }
});

test("fresh manifest admits exactly one Babylon and rejects extra or substituted clients", async () => {
  const admission=await admitTrial(courierReq(),k=>env[k],RELEASE,()=>NOW,resolve);
  assert.equal(admission.scope.trial_id,TRIAL);assert.deepEqual(admission.entry,entry);
  for(const entries of [[{...entry,client_id:"origintrail"}],[{...entry,client_id:"yellow"}],
    [entry,{...entry,client_id:"origintrail",job_id:id(11),request_id:id(12),content_item_id:id(12),source_item_id:id(13),content_version_id:id(14)}],
    [entry,{...entry,job_id:id(11),request_id:id(12),content_item_id:id(12),source_item_id:id(13),content_version_id:id(14)}]]) {
    const m={...manifest,entries};await assert.rejects(()=>admitTrial(courierReq(m),k=>env[k],RELEASE,()=>NOW,
      async()=>({...value,manifest:m,manifest_sha256:trialManifestHash(m)})));
  }
});

const rows: Record<string,any>={
  jobs:{id:id(1),client_id:"babylon",job_kind:"generate",status:"succeeded",attempts:1,max_attempts:3,
    created_at:"2026-10-15T15:11:00Z",started_at:"2026-10-15T15:12:00Z",finished_at:"2026-10-15T15:13:00Z",
    idempotency_key:"official-x-review:v1:2026-10-16:babylon",content_item_id:null,
    input:{workflow:"official_x_review_draft_v1",content_kind:"daily_news",manual_only:false,kst_date:"2026-10-16",
      request_id:id(2),source_item_ids:[id(3)],source_content:SOURCE},
    output:{content_item_id:id(2),content_version_id:id(4),source_item_ids:[id(3)]}},
  content_items:{id:id(2),client_id:"babylon",status:"needs_review",content_kind:"daily_news",current_version_id:id(4)},
  content_versions:{id:id(4),content_item_id:id(2),channel_copy:{telegram:"직원 검수 공지",x:"직원 검수 X 초안"},
    generation_meta:{mock_mode:false,request_id:id(2)},deliverables:{primary_asset_id:id(7)},created_at:"2026-10-15T15:13:00Z"},
  assets:{id:id(7),content_item_id:id(2),content_version_id:id(4),asset_kind:"png",mime_type:"image/png",
    sha256:entry.banner_sha256,storage_bucket:"content-studio",storage_path:`${id(6)}/babylon/${id(7)}/news-card.png`},
  source_items:{id:id(3),client_id:"babylon",body:SOURCE},
};
test("fresh courier projection allows only a new natural job, never preserved old job or attempt", async () => {
  const admitted=await admitTrial(courierReq(),k=>env[k],RELEASE,()=>NOW,resolve),cfg:any={workspaceId:id(6),
    supabaseUrl:"https://fixture.supabase.co",serviceRoleKey:"fixture-only"};
  const check=async(data:Record<string,any>)=>{let calls=0;await verifyTrialProjection(admitted,cfg,async(url,init)=>{
    calls++;assert.equal(init?.method,"GET");return Response.json([data[new URL(String(url)).pathname.split("/").pop()!]]);
  },()=>NOW);return calls;};
  assert.equal(await check(rows),5);
  for(const delta of [{created_at:"2026-10-14T16:00:00Z"},{started_at:"2026-10-14T16:01:00Z"},
    {attempts:2},{idempotency_key:"official-x-review:v1:2026-10-15:babylon"},
    {input:{...rows.jobs.input,kst_date:"2026-10-15"}},{client_id:"origintrail"}]) {
    await assert.rejects(()=>check({...rows,jobs:{...rows.jobs,...delta}}));
  }
});

test("fresh resolver, state and delivery confirmation preserve existing bounded protocol", async () => {
  const calls:any[]=[];const resolver=createTrialManifestResolver({getEnv:k=>env[k],releaseSha:()=>RELEASE,clock:()=>NOW,
    fetcher:async(url,init)=>{calls.push({url,body:JSON.parse(String(init?.body))});return Response.json(value);}});
  const handler=createTrialStateHandler({getEnv:k=>env[k],releaseSha:()=>RELEASE,clock:()=>NOW,resolver});
  const req=new Request(ORIGIN+"/.netlify/functions/content-ops-trial-state",{method:"POST",body:JSON.stringify(identity),
    headers:{"content-type":"application/json",authorization:`Bearer ${env.CONTENT_OPS_GATEWAY_TOKEN}`,"x-content-ops-expected-release-sha":RELEASE}});
  assert.equal((await handler(req,native)).status,200);assert.equal(calls.length,1);
  assert.deepEqual(calls[0],{url:API+"/internal/trial/manifest",body:identity});
  const confirm=createTrialDeliveryConfirmation({getEnv:k=>env[k],releaseSha:()=>RELEASE,clock:()=>NOW,
    fetcher:async(url,init)=>{calls.push({url,body:JSON.parse(String(init?.body))});return Response.json({recorded:true});}});
  const receipt={...identity,manifest_sha256:manifestHash,content_version_id:id(4),card_id:id(8),outbox_id:id(9),confirmed_parts:4 as const,status:"sent" as const};
  await confirm!(receipt);assert.equal(calls.length,2);assert.deepEqual(calls[1],{url:API+"/internal/trial/delivery-confirm",body:receipt});
});

test("fresh preflight requires upstream Babylon-only readiness and no broader flags", async () => {
  const proof={schema_version:"ybo-api-readiness@1",ready:true,...identity,release_sha:RELEASE,not_before:START,deadline:END,
    kst_date:"2026-10-16",controller_enabled:true,broad_public_flags_off:true,client_ids:["babylon"],observed_at:new Date(NOW).toISOString()};
  for(const clients of [["babylon"],["babylon","origintrail"],["yellow"],[]]) {
    let calls=0;const handler=createYboTrialPreflightHandler({getEnv:k=>env[k],releaseSha:()=>RELEASE,clock:()=>NOW,
      fetcher:async(url,init)=>{calls++;assert.equal(url,API+"/internal/trial/readiness");assert.equal(init?.method,"GET");return Response.json({...proof,client_ids:clients});}});
    const req=new Request(ORIGIN+"/api/ybo-trial-preflight",{headers:{"x-studio-automation-key":env.STUDIO_AUTOMATION_TOKEN,
      "x-studio-expected-release-sha":RELEASE,"x-ybo-trial-id":TRIAL,"x-ybo-trial-plan-sha256":PLAN}});
    const r=await handler(req,native as any);assert.equal(r.status,clients.length===1&&clients[0]==="babylon"?200:503);assert.equal(calls,1);
  }
});
