import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { trustedNaturalOncePolicy, naturalOncePolicyUnchanged, NaturalOnceConfigurationError,
  BABYLON_NATURAL_ONCE_ESTIMATE_POLICY } from "../netlify/functions/_shared/babylon-natural-once.mts";
import { trialIdentityPolicy, configuredTrialWindow, READY_ANCHORED_TRIAL_ID,
  FRESH_BABYLON_TRIAL_ID, NEW_BABYLON_TRIAL_ID, STANDALONE_BABYLON_TRIAL_ID } from "../netlify/functions/_shared/content-ops-trial-window.mts";
import { admitTrialGeneration, canonicalTrialPayload, TrialGenerationError } from "../netlify/functions/_shared/content-ops-trial-generation.mts";
import { createYboTrialPreflightHandler, YBO_TRIAL_REQUIRED_OFF_FLAGS } from "../netlify/functions/_shared/ybo-trial-preflight.mts";
import { admitTrial } from "../netlify/functions/_shared/content-ops-trial.mts";

const id = (n: number) => `${String(n).padStart(8, "0")}-1111-4111-8111-111111111111`;
const START = "2026-10-18T09:00:00Z", END = "2026-10-18T11:00:00Z", NOW = Date.parse("2026-10-18T09:30:00Z");
const RELEASE = "a".repeat(40), API = "https://coineasy-content-engine-production.up.railway.app";
const ORIGIN = "https://coineasy-newscard.netlify.app";
const sha = (value: string) => createHash("sha256").update(value, "utf8").digest("hex");
const plan = { contract: "babylon-natural-once@1", trial_id: id(99), not_before: START, deadline: END,
  kst_date: "2026-10-18", client_ids: ["babylon"], max_per_client: 1, max_total: 1,
  estimated_daily_limit_micros: 10_000_000, estimate_policy_sha256: BABYLON_NATURAL_ONCE_ESTIMATE_POLICY,
  release_sha: RELEASE };
function environment(changes: Record<string, any> = {}) {
  const value = { ...plan, ...changes }, raw = canonicalTrialPayload(value);
  return { ...Object.fromEntries(YBO_TRIAL_REQUIRED_OFF_FLAGS.map(key => [key, "false"])),
    YBO_TRIAL_PLAN_JSON: raw, YBO_TRIAL_PLAN_SHA256: sha(raw), YBO_TRIAL_ID: value.trial_id,
    YBO_TRIAL_RELEASE_SHA: value.release_sha, YBO_TRIAL_NOT_BEFORE: value.not_before,
    YBO_TRIAL_DEADLINE: value.deadline, YBO_TRIAL_GENERATION_GATEWAY_ENABLED: "true",
    API_SECRET: "fixture-existing-api-secret", RAILWAY_API_URL: API,
    STUDIO_AUTOMATION_TOKEN: "fixture-automation-token-at-least-32-chars" };
}
const ENV = environment();
const payload = { source_content: "Babylon official announcement with facts for a staff review draft.", source_type: "tweet",
  source_url: "https://x.com/babylonlabs_io/status/1234567890", source_image_url: "", mock_mode: false,
  template_style: "remix", style_references: [], style_reference_pack_hash: "", brand_review_guidance: null,
  quality_context: { client_id: "babylon", request_id: id(2), job_id: id(1) } };
const body = { ...payload, quality_context: undefined, trial_payload: payload };
const runtime = { deploy: { context: "production", published: true, id: "published-fixture" } };
function request(env: Record<string, string> = ENV, client = "babylon") {
  return new Request(`${ORIGIN}/api/news-card/${client}`, { method: "POST", headers: {
    "X-YBO-Trial-ID": env.YBO_TRIAL_ID, "X-YBO-Trial-Plan-Sha256": env.YBO_TRIAL_PLAN_SHA256,
    "X-Studio-Expected-Release-Sha": RELEASE } });
}
const receipt = (env = ENV, changes = {}) => ({ authorized: true, trial_id: env.YBO_TRIAL_ID,
  plan_sha256: env.YBO_TRIAL_PLAN_SHA256, release_sha: RELEASE, client_id: "babylon",
  request_id: id(2), observed_at: new Date(NOW).toISOString(), ...changes });
async function gate(env: Record<string, string> = ENV, changes: Record<string, any> = {}) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const options = { req: request(env), runtime, body, clientId: "babylon", requestId: id(2), automation: true,
    env: (key: string) => env[key], clock: () => NOW, release: RELEASE,
    fetcher: async (url: any, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return changes.response?.(calls.length) ?? Response.json(receipt(env));
    }, ...changes.args };
  return { calls, admit: () => admitTrialGeneration(options) };
}

test("trusted full natural-once plan is Babylon-only, fixed-budget and isolated from generic trial identities", () => {
  const result = trustedNaturalOncePolicy(key => ENV[key]); assert(result);
  assert.equal(result.trialId, plan.trial_id); assert.equal(result.planSha256, ENV.YBO_TRIAL_PLAN_SHA256);
  assert.deepEqual(result.policy.clientIds, ["babylon"]); assert.equal(result.policy.maxTotal, 1);
  assert(Object.isFrozen(result)); assert(Object.isFrozen(result.policy)); assert(Object.isFrozen(result.policy.clientIds));
  assert(naturalOncePolicyUnchanged(key => ENV[key], result));
  assert.equal(trialIdentityPolicy(plan.trial_id), null);
  assert.equal(configuredTrialWindow(key => ENV[key], "YBO_TRIAL"), null);
  assert.equal(configuredTrialWindow(key => key.replace("CONTENT_OPS_", "YBO_") in ENV ? ENV[key.replace("CONTENT_OPS_", "YBO_")] : undefined,
    "CONTENT_OPS_TRIAL"), null);
  assert.equal(trustedNaturalOncePolicy(() => undefined), null);
  assert.equal(trustedNaturalOncePolicy(key => key === "YBO_TRIAL_PLAN_JSON" ? JSON.stringify({ contract: "ybo-server-trial@1" }) : undefined), null);
});

const invalidPlans: Record<string, any>[] = [
  { contract: "arbitrary-once@1" }, { contract: "" }, { trial_id: "ABCDEF99-1111-4111-8111-111111111111" }, { trial_id: "00000000-0000-0000-0000-000000000000" },
  ...[READY_ANCHORED_TRIAL_ID, FRESH_BABYLON_TRIAL_ID, NEW_BABYLON_TRIAL_ID, STANDALONE_BABYLON_TRIAL_ID].map(trial_id => ({ trial_id })),
  { trial_id: "1a2bef47-4827-4bc3-9b4d-5baf2714015d" },
  { client_ids: ["yellow"] }, { client_ids: ["babylon", "origintrail"] }, { max_per_client: 2 }, { max_total: true },
  { estimated_daily_limit_micros: 10_000_001 }, { estimate_policy_sha256: "b".repeat(64) }, { release_sha: "b".repeat(39) },
  { kst_date: "2026-10-19" }, { deadline: "2026-10-18T11:00:01Z" }, { not_before: "2026-10-18T09:00:00.000Z" },
  { not_before: "2026-10-18T14:00:00Z", deadline: "2026-10-18T16:00:00Z" }, { extra: true },
];
for (const changes of invalidPlans) {
  test(`invalid trusted plan ${JSON.stringify(changes)} blocks before all HTTP`, async () => {
    const env = environment(changes), g = await gate(env);
    assert.throws(() => trustedNaturalOncePolicy(key => env[key]), NaturalOnceConfigurationError);
    await assert.rejects(g.admit, TrialGenerationError); assert.equal(g.calls.length, 0);
  });
}
test("bad raw JSON, duplicate keys, plan hash and mismatching deployed pins block before all HTTP", async () => {
  for (const changes of [{ YBO_TRIAL_PLAN_JSON: "{" }, { YBO_TRIAL_PLAN_JSON: JSON.stringify(plan) },
    { YBO_TRIAL_PLAN_JSON: ENV.YBO_TRIAL_PLAN_JSON.replace('{', '{"contract":"babylon-natural-once@1",') },
    { YBO_TRIAL_PLAN_JSON: undefined }, { YBO_TRIAL_PLAN_SHA256: "b".repeat(64) },
    { YBO_TRIAL_ID: id(98) }, { YBO_TRIAL_RELEASE_SHA: "b".repeat(40) }, { YBO_TRIAL_NOT_BEFORE: "2026-10-18T09:00:01Z" },
    { YBO_TRIAL_DEADLINE: "2026-10-18T11:00:01Z" }, { QUALITY_BANNER_ENABLED: "true" },
    { YBO_TRIAL_GENERATION_GATEWAY_ENABLED: "false" }]) {
    const g = await gate({ ...ENV, ...changes } as Record<string, string>);
    await assert.rejects(g.admit, TrialGenerationError); assert.equal(g.calls.length, 0);
  }
});
test("natural-once still requires exact durable API authorization before every downstream request", async () => {
  const g = await gate(), admission = await g.admit();
  assert.equal(admission.trialId, plan.trial_id); assert.equal(g.calls.length, 1);
  assert.equal(g.calls[0].url, `${API}/internal/trial/generation-authorize`);
  assert.deepEqual(JSON.parse(String(g.calls[0].init?.body)), { trial_id: plan.trial_id,
    plan_sha256: ENV.YBO_TRIAL_PLAN_SHA256, release_sha: RELEASE, client_id: "babylon", request_id: id(2), payload });
  await admission.fetcher("https://fixture.supabase.co/rest/v1/rpc/read", { method: "POST" });
  assert.equal(g.calls.length, 3); assert.equal(g.calls[1].url, g.calls[0].url);
});
test("natural-once valid UUIDs and matching env are never sufficient without owner proof", async () => {
  for (const changes of [{ authorized: false }, { trial_id: STANDALONE_BABYLON_TRIAL_ID }, { request_id: id(77) },
    { plan_sha256: "c".repeat(64) }, { extra: true }]) {
    const g = await gate(ENV, { response: () => Response.json(receipt(ENV, changes)) });
    await assert.rejects(g.admit, TrialGenerationError); assert.equal(g.calls.length, 1);
  }
});
test("natural-once rejects other clients, payload mismatch and unknown old-contract IDs before HTTP", async () => {
  for (const args of [{ clientId: "yellow", req: request(ENV, "yellow") }, { automation: false },
    { body: { ...body, source_content: "Other content" } },
    { body: { ...body, trial_payload: { ...payload, quality_context: { ...payload.quality_context, request_id: id(9) } } } }]) {
    const g = await gate(ENV, { args }); await assert.rejects(g.admit, TrialGenerationError); assert.equal(g.calls.length, 0);
  }
  const legacy = await gate(environment({ contract: "ybo-server-trial@1" }));
  await assert.rejects(legacy.admit, TrialGenerationError); assert.equal(legacy.calls.length, 0);
});
test("natural-once raw plan cannot change after authorization even when the old pins are retained", async () => {
  const env = { ...ENV }, g = await gate(env, { response: () => {
    env.YBO_TRIAL_PLAN_JSON += " "; return Response.json(receipt());
  } });
  await assert.rejects(g.admit, TrialGenerationError); assert.equal(g.calls.length, 1);
});

async function preflight(changes: Record<string, any> = {}) {
  const env = { ...ENV, ...changes.env }; let calls = 0;
  const handler = createYboTrialPreflightHandler({ getEnv: key => env[key], releaseSha: () => RELEASE,
    clock: () => NOW, fetcher: async (url, init) => {
      calls++; assert.equal(url, API + "/internal/trial/readiness"); assert.equal(init?.method, "GET");
      changes.afterRead?.(env);
      return Response.json({ schema_version: "ybo-api-readiness@1", ready: true, trial_id: plan.trial_id,
        plan_sha256: ENV.YBO_TRIAL_PLAN_SHA256, release_sha: RELEASE, not_before: START, deadline: END,
        kst_date: "2026-10-18", controller_enabled: true, broad_public_flags_off: true, client_ids: ["babylon"],
        observed_at: new Date(NOW).toISOString(), ...changes.proof });
    } });
  const response = await handler(new Request(ORIGIN + "/api/ybo-trial-preflight", { headers: {
    "X-Studio-Automation-Key": ENV.STUDIO_AUTOMATION_TOKEN, "X-Studio-Expected-Release-Sha": RELEASE,
    "X-YBO-Trial-ID": plan.trial_id, "X-YBO-Trial-Plan-Sha256": ENV.YBO_TRIAL_PLAN_SHA256 } }), runtime as never);
  return { status: response.status, body: await response.json(), calls };
}
test("generation-only natural plan uses the existing bounded authenticated readiness GET", async () => {
  const result = await preflight(); assert.equal(result.status, 200); assert.equal(result.calls, 1);
  assert.equal(result.body.trial_id, plan.trial_id); assert.equal(result.body.upstream_api_auth_verified, true);
});
test("preflight rejects mismatched owner scope, malformed config and post-I/O raw-plan drift", async () => {
  for (const options of [{ proof: { client_ids: ["babylon", "origintrail"] } }, { proof: { trial_id: STANDALONE_BABYLON_TRIAL_ID } },
    { afterRead: (env: Record<string, string>) => { env.YBO_TRIAL_PLAN_JSON += " "; } }]) {
    const result = await preflight(options); assert.equal(result.status, 503); assert.equal(result.calls, 1);
  }
  const bad = await preflight({ env: { YBO_TRIAL_PLAN_JSON: "{" } }); assert.equal(bad.status, 503); assert.equal(bad.calls, 0);
});
test("new plan cannot widen the historical trial courier admission", async () => {
  const env = { ...ENV, CONTENT_OPS_TRIAL_GATEWAY_ENABLED: "true", CONTENT_OPS_TRIAL_ID: plan.trial_id,
    CONTENT_OPS_TRIAL_PLAN_SHA256: ENV.YBO_TRIAL_PLAN_SHA256, CONTENT_OPS_REVIEW_RELEASE_SHA: RELEASE,
    CONTENT_OPS_TRIAL_NOT_BEFORE: START, CONTENT_OPS_TRIAL_DEADLINE: END,
    CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256: "c".repeat(64) };
  let resolves = 0;
  const req = new Request(ORIGIN + "/.netlify/functions/content-ops-review", { method: "POST", headers: {
    "x-content-ops-mode": "trial", "x-content-ops-packet-mode": "trial_delivery_only_v1",
    "x-content-ops-version-id": id(4), "x-content-ops-trial-id": plan.trial_id,
    "x-content-ops-trial-plan-sha256": ENV.YBO_TRIAL_PLAN_SHA256, "x-content-ops-trial-manifest-sha256": "d".repeat(64) } });
  await assert.rejects(() => admitTrial(req, key => env[key], RELEASE, () => NOW, async () => { resolves++; return {}; }));
  assert.equal(resolves, 0);
});
