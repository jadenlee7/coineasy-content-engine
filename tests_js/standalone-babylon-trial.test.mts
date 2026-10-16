import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { STANDALONE_BABYLON_TRIAL_ID as TRIAL, STANDALONE_BABYLON_EARLIEST,
  READY_ANCHORED_TRIAL_ID, FRESH_BABYLON_TRIAL_ID, NEW_BABYLON_TRIAL_ID,
  configuredTrialWindow, trialIdentityPolicy } from "../netlify/functions/_shared/content-ops-trial-window.mts";
import { admitTrialGeneration } from "../netlify/functions/_shared/content-ops-trial-generation.mts";
import { admitTrial, trialManifestHash } from "../netlify/functions/_shared/content-ops-trial.mts";
import { createTrialManifestResolver, createTrialDeliveryConfirmation } from "../netlify/functions/_shared/content-ops-trial-resolver.mts";
import { createTrialStateHandler } from "../netlify/functions/_shared/content-ops-trial-state.mts";
import { createContentOpsReviewHandler } from "../netlify/functions/_shared/content-ops-review.mts";
import { createYboTrialPreflightHandler, YBO_TRIAL_REQUIRED_OFF_FLAGS } from "../netlify/functions/_shared/ybo-trial-preflight.mts";

const START = "2026-10-16T19:45:03Z", END = "2026-10-16T21:45:03Z", DAY = "2026-10-17";
const NOW = Date.parse("2026-10-16T20:00:00Z"), RELEASE = "a".repeat(40), PLAN = "b".repeat(64), POLICY = "c".repeat(64);
const ORIGIN = "https://coineasy-newscard.netlify.app", API = "https://coineasy-content-engine-production.up.railway.app";
const id = (n: number) => `${String(n).padStart(8, "0")}-1111-4111-8111-111111111111`;
const sha = (v: string) => createHash("sha256").update(v).digest("hex");
const SOURCE = "Babylon official fixture source for private review only.", TG = "직원 검수 초안", X = "직원 검수 X 초안";
const OLD = [READY_ANCHORED_TRIAL_ID, FRESH_BABYLON_TRIAL_ID, NEW_BABYLON_TRIAL_ID];
const ENV: Record<string, string> = {
  ...Object.fromEntries(YBO_TRIAL_REQUIRED_OFF_FLAGS.map(k => [k, "false"])),
  YBO_TRIAL_ID: TRIAL, YBO_TRIAL_PLAN_SHA256: PLAN, YBO_TRIAL_RELEASE_SHA: RELEASE,
  YBO_TRIAL_NOT_BEFORE: START, YBO_TRIAL_DEADLINE: END, YBO_TRIAL_GENERATION_GATEWAY_ENABLED: "true",
  CONTENT_OPS_TRIAL_ID: TRIAL, CONTENT_OPS_TRIAL_PLAN_SHA256: PLAN, CONTENT_OPS_REVIEW_RELEASE_SHA: RELEASE,
  CONTENT_OPS_TRIAL_NOT_BEFORE: START, CONTENT_OPS_TRIAL_DEADLINE: END, CONTENT_OPS_TRIAL_GATEWAY_ENABLED: "true",
  CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256: POLICY, API_SECRET: "fixture-api-existing-secret", RAILWAY_API_URL: API,
  CONTENT_OPS_GATEWAY_TOKEN: "fixture-dedicated-gateway-token-32-characters",
  STUDIO_AUTOMATION_TOKEN: "fixture-automation-token-at-least-32-characters",
  SUPABASE_URL: "https://fixture.supabase.co", SUPABASE_SERVICE_ROLE_KEY: "fixture-only",
  CONTENT_STUDIO_WORKSPACE_ID: id(6),
};
const native = { deploy: { context: "production", id: "published-fixture", published: true } };
const entry = { client_id: "babylon", job_id: id(1), request_id: id(2), source_item_id: id(3), source_sha256: sha(SOURCE),
  content_item_id: id(2), content_version_id: id(4), banner_sha256: "d".repeat(64), telegram_copy_sha256: sha(TG), x_copy_sha256: sha(X) };
const manifest = { contract: "ybo-trial-courier-manifest@1", trial_id: TRIAL, plan_sha256: PLAN, release_sha: RELEASE,
  not_before: START, deadline: END, destination_policy_sha256: POLICY, entries: [entry] };
const identity = { trial_id: TRIAL, plan_sha256: PLAN };
const receipt = (m = manifest, now = NOW) => ({ active: true, observed_at: new Date(now).toISOString(), manifest: m,
  manifest_sha256: trialManifestHash(m) });
const payload = { source_content: SOURCE, source_type: "tweet", source_url: "https://x.com/babylonlabs_io/status/1234567890",
  source_image_url: "", mock_mode: false, template_style: "remix", style_references: [], style_reference_pack_hash: "",
  brand_review_guidance: null, quality_context: { client_id: "babylon", request_id: id(2), job_id: id(1) } };
function generationReq(trial = TRIAL, client = "babylon") {
  return new Request(`${ORIGIN}/api/news-card/${client}`, { method: "POST", headers: {
    "x-ybo-trial-id": trial, "x-ybo-trial-plan-sha256": PLAN, "x-studio-expected-release-sha": RELEASE } });
}
function courierReq(m = manifest, body: any = { action: "reconcile" }) {
  return new Request(ORIGIN + "/.netlify/functions/content-ops-review", { method: "POST", body: JSON.stringify(body), headers: {
    "content-type": "application/json", authorization: `Bearer ${ENV.CONTENT_OPS_GATEWAY_TOKEN}`,
    "x-content-ops-expected-release-sha": RELEASE, "x-content-ops-mode": "trial", "x-content-ops-packet-mode": "trial_delivery_only_v1",
    "x-content-ops-version-id": entry.content_version_id, "x-content-ops-trial-id": m.trial_id,
    "x-content-ops-trial-plan-sha256": m.plan_sha256, "x-content-ops-trial-manifest-sha256": trialManifestHash(m) } });
}
const generationOptions = { runtime: native, body: { ...payload, trial_payload: payload }, clientId: "babylon", requestId: id(2),
  automation: true, env: (k: string) => ENV[k], clock: () => NOW, release: RELEASE };
const proof = { schema_version: "ybo-api-readiness@1", ready: true, ...identity, release_sha: RELEASE,
  not_before: START, deadline: END, kst_date: DAY, controller_enabled: true, broad_public_flags_off: true,
  client_ids: ["babylon"], observed_at: new Date(NOW).toISOString() };

test("standalone identity is immutable, one Babylon only, with no calendar-day pin; old policies unchanged", () => {
  assert.equal(TRIAL, "7b27e277-0641-4aef-b739-761f86547287");
  assert.equal(STANDALONE_BABYLON_EARLIEST, START);
  const policy = trialIdentityPolicy(TRIAL)!;
  assert.deepEqual(policy, { clientIds: ["babylon"], maxPerClient: 1, maxTotal: 1, earliest: Date.parse(START) });
  assert(Object.isFrozen(policy)); assert(Object.isFrozen(policy.clientIds));
  assert.deepEqual(trialIdentityPolicy(READY_ANCHORED_TRIAL_ID), { clientIds: ["babylon", "origintrail"], maxPerClient: 1, maxTotal: 2, earliest: 0 });
  for (const [trial, earliest] of [[FRESH_BABYLON_TRIAL_ID, "2026-10-15T15:00:00Z"], [NEW_BABYLON_TRIAL_ID, "2026-10-15T15:47:35Z"]]) {
    assert.deepEqual(trialIdentityPolicy(trial), { clientIds: ["babylon"], maxPerClient: 1, maxTotal: 1, earliest: Date.parse(earliest), kstDate: "2026-10-16" });
  }
  for (const value of [null, undefined, "", id(99), TRIAL.toUpperCase(), TRIAL + "," + TRIAL, "1a2bef47-4827-4bc3-9b4d-5baf2714015d"])
    assert.equal(trialIdentityPolicy(value), null);
});

test("both standalone gateways accept later anchored days, never early, noncanonical, extended or cross-KST bounds", () => {
  for (const prefix of ["YBO_TRIAL", "CONTENT_OPS_TRIAL"] as const) {
    for (const [start, end, day] of [[START, END, DAY], ["2026-11-01T23:10:51Z", "2026-11-02T01:10:51Z", "2026-11-02"]]) {
      const e = { ...ENV, [`${prefix}_NOT_BEFORE`]: start, [`${prefix}_DEADLINE`]: end };
      assert.equal(configuredTrialWindow(k => e[k], prefix)?.kstDate, day);
    }
    for (const [start, end] of [["2026-10-16T19:45:02Z", "2026-10-16T21:45:02Z"],
      [START.replace("Z", ".000Z"), END], [START, END.replace("Z", "+00:00")],
      [START, "2026-10-16T21:45:04Z"], [START, "2026-10-16T21:45:02Z"],
      ["2026-10-17T13:00:00Z", "2026-10-17T15:00:00Z"], ["2026-02-30T19:45:03Z", "2026-02-30T21:45:03Z"]]) {
      const e = { ...ENV, [`${prefix}_NOT_BEFORE`]: start, [`${prefix}_DEADLINE`]: end };
      assert.equal(configuredTrialWindow(k => e[k], prefix), null);
    }
    for (const trial of [FRESH_BABYLON_TRIAL_ID, NEW_BABYLON_TRIAL_ID]) {
      const e = { ...ENV, [`${prefix}_ID`]: trial };
      assert.equal(configuredTrialWindow(k => e[k], prefix), null, "old date restrictions stay closed");
    }
  }
});

test("standalone generation requires the exact existing durable owner before each HTTP call", async () => {
  const calls: any[] = [];
  const admitted = await admitTrialGeneration({ ...generationOptions, req: generationReq(), fetcher: async (url, init) => {
    calls.push({ url, body: init?.body && JSON.parse(String(init.body)) });
    return Response.json({ authorized: true, ...identity, release_sha: RELEASE, client_id: "babylon", request_id: id(2), observed_at: proof.observed_at });
  } });
  assert.equal(admitted.trialId, TRIAL); assert.equal(admitted.collectOnly, false);
  assert.deepEqual(calls, [{ url: API + "/internal/trial/generation-authorize", body: { ...identity, release_sha: RELEASE, client_id: "babylon", request_id: id(2), payload } }]);
  await admitted.assertActive(); assert.equal(calls.length, 2);
});

test("standalone generation rejects wrong or replayed identities, excluded clients and closed windows before owner I/O", async () => {
  const invalid = [...OLD, id(99)].map(trial => ({ req: generationReq(trial) }));
  invalid.push(...["yellow", "origintrail", "squid"].map(client => ({ req: generationReq(TRIAL, client), clientId: client })));
  invalid.push(...[Date.parse(START) - 1, Date.parse(END), NaN].map(now => ({ req: generationReq(), clock: () => now })));
  for (const delta of invalid) {
    let calls = 0;
    await assert.rejects(() => admitTrialGeneration({ ...generationOptions, ...delta, fetcher: async () => { calls++; throw Error("forbidden"); } }));
    assert.equal(calls, 0);
  }
});

test("standalone generation refuses replayed owner authorization and configuration drift without downstream work", async () => {
  for (const returned of [false, ...OLD]) {
    let calls = 0;
    await assert.rejects(() => admitTrialGeneration({ ...generationOptions, req: generationReq(), fetcher: async () => {
      calls++; return Response.json({ authorized: returned !== false, ...identity, trial_id: returned || TRIAL,
        release_sha: RELEASE, client_id: "babylon", request_id: id(2), observed_at: proof.observed_at });
    } })); assert.equal(calls, 1);
  }
  const e = { ...ENV }; let calls = 0;
  const admitted = await admitTrialGeneration({ ...generationOptions, req: generationReq(), env: k => e[k], fetcher: async () => {
    calls++; return Response.json({ authorized: true, ...identity, release_sha: RELEASE, client_id: "babylon", request_id: id(2), observed_at: proof.observed_at });
  } });
  e.YBO_TRIAL_DEADLINE = "2026-10-16T22:45:03Z";
  await assert.rejects(() => admitted.fetcher(API + "/provider-must-not-run")); assert.equal(calls, 1);
});

test("standalone preflight requires exact Babylon-only API identity, plan, release and derived day", async () => {
  for (const delta of [{}, { trial_id: OLD[0] }, { plan_sha256: "e".repeat(64) }, { release_sha: "e".repeat(40) },
    { client_ids: ["babylon", "origintrail"] }, { client_ids: ["yellow"] }, { kst_date: "2026-10-16" },
    { not_before: "2026-10-16T19:46:03Z" }, { deadline: "2026-10-16T21:46:03Z" }, { ready: false }]) {
    let calls = 0;
    const handler = createYboTrialPreflightHandler({ getEnv: k => ENV[k], releaseSha: () => RELEASE, clock: () => NOW,
      fetcher: async (url, init) => { calls++; assert.equal(url, API + "/internal/trial/readiness"); assert.equal(init?.method, "GET"); return Response.json({ ...proof, ...delta }); } });
    const req = new Request(ORIGIN + "/api/ybo-trial-preflight", { headers: { "x-studio-automation-key": ENV.STUDIO_AUTOMATION_TOKEN,
      "x-studio-expected-release-sha": RELEASE, "x-ybo-trial-id": TRIAL, "x-ybo-trial-plan-sha256": PLAN } });
    const response = await handler(req, native as any);
    assert.equal(response.status, Object.keys(delta).length ? 503 : 200); assert.equal(calls, 1);
    if (!Object.keys(delta).length) assert.equal((await response.json()).kst_date, DAY);
  }
});

test("standalone manifest is one Babylon with exact identity, hashes, release and anchored bounds", async () => {
  const admitted = await admitTrial(courierReq(), k => ENV[k], RELEASE, () => NOW, async () => receipt());
  assert.equal(admitted.scope.trial_id, TRIAL); assert.equal(admitted.window.kstDate, DAY);
  const invalid = [...OLD, id(99)].map(trial_id => ({ ...manifest, trial_id }));
  invalid.push(...[{ plan_sha256: "e".repeat(64) }, { release_sha: "e".repeat(40) }, { destination_policy_sha256: "e".repeat(64) },
    { not_before: "2026-10-16T19:46:03Z", deadline: "2026-10-16T21:46:03Z" },
    { entries: [{ ...entry, client_id: "yellow" }] }, { entries: [{ ...entry, client_id: "origintrail" }] },
    { entries: [entry, { ...entry, job_id: id(11), request_id: id(12), content_item_id: id(12), source_item_id: id(13), content_version_id: id(14) }] },
  ].map(delta => ({ ...manifest, ...delta })));
  for (const m of invalid) await assert.rejects(() => admitTrial(courierReq(m), k => ENV[k], RELEASE, () => NOW, async () => receipt(m)));
  for (const delta of [{ active: false }, { manifest_sha256: "e".repeat(64) }, { observed_at: new Date(NOW - 5001).toISOString() }])
    await assert.rejects(() => admitTrial(courierReq(), k => ENV[k], RELEASE, () => NOW, async () => ({ ...receipt(), ...delta })));
});

test("standalone owner closure, manifest substitution and moved anchor cannot reopen an admission", async () => {
  const e = { ...ENV }; let current = receipt(), calls = 0;
  const admitted = await admitTrial(courierReq(), k => e[k], RELEASE, () => NOW, async () => { calls++; return current; });
  current = { ...current, active: false }; await assert.rejects(() => admitted.assertActive());
  current = receipt({ ...manifest, entries: [{ ...entry, source_sha256: "e".repeat(64) }] });
  await assert.rejects(() => admitted.assertActive());
  e.CONTENT_OPS_TRIAL_NOT_BEFORE = "2026-10-16T20:45:03Z"; e.CONTENT_OPS_TRIAL_DEADLINE = "2026-10-16T22:45:03Z";
  await assert.rejects(() => admitted.assertActive()); assert.equal(calls, 3);
});

test("standalone state and close-only confirmation reuse exact trial routes, reject stale identity before HTTP", async () => {
  const calls: any[] = [];
  const resolver = createTrialManifestResolver({ getEnv: k => ENV[k], releaseSha: () => RELEASE, clock: () => NOW,
    fetcher: async (url, init) => { calls.push({ url, body: JSON.parse(String(init?.body)) }); return Response.json(receipt()); } });
  const handler = createTrialStateHandler({ getEnv: k => ENV[k], releaseSha: () => RELEASE, clock: () => NOW, resolver });
  const stateReq = (trial_id = TRIAL) => new Request(ORIGIN + "/.netlify/functions/content-ops-trial-state", { method: "POST",
    body: JSON.stringify({ trial_id, plan_sha256: PLAN }), headers: { "content-type": "application/json",
      authorization: `Bearer ${ENV.CONTENT_OPS_GATEWAY_TOKEN}`, "x-content-ops-expected-release-sha": RELEASE } });
  assert.equal((await handler(stateReq(), native)).status, 200);
  for (const old of [...OLD, id(99)]) assert.equal((await handler(stateReq(old), native)).status, 503);
  assert.deepEqual(calls, [{ url: API + "/internal/trial/manifest", body: identity }]);
  const confirm = createTrialDeliveryConfirmation({ getEnv: k => ENV[k], releaseSha: () => RELEASE, clock: () => NOW,
    fetcher: async (url, init) => { calls.push({ url, body: JSON.parse(String(init?.body)) }); return Response.json({ recorded: true }); } })!;
  const sent = { ...identity, manifest_sha256: trialManifestHash(manifest), content_version_id: id(4), card_id: id(8),
    outbox_id: id(9), confirmed_parts: 4 as const, status: "sent" as const };
  for (const old of [...OLD, id(99)]) await assert.rejects(() => confirm({ ...sent, trial_id: old }));
  await confirm(sent); assert.equal(calls.length, 2);
  assert.deepEqual(calls[1], { url: API + "/internal/trial/delivery-confirm", body: sent });
});

const rows: Record<string, any> = {
  jobs: { id: id(1), client_id: "babylon", job_kind: "generate", status: "succeeded", attempts: 1, max_attempts: 3,
    created_at: "2026-10-16T19:46:03Z", started_at: "2026-10-16T19:47:03Z", finished_at: "2026-10-16T19:48:03Z",
    idempotency_key: `official-x-review:v1:${DAY}:babylon`, content_item_id: null,
    input: { workflow: "official_x_review_draft_v1", content_kind: "daily_news", manual_only: false, kst_date: DAY,
      request_id: id(2), source_item_ids: [id(3)], source_content: SOURCE },
    output: { content_item_id: id(2), content_version_id: id(4), source_item_ids: [id(3)] } },
  content_items: { id: id(2), client_id: "babylon", status: "needs_review", content_kind: "daily_news", current_version_id: id(4) },
  content_versions: { id: id(4), content_item_id: id(2), channel_copy: { telegram: TG, x: X },
    generation_meta: { mock_mode: false, request_id: id(2) }, deliverables: { primary_asset_id: id(7) }, created_at: "2026-10-16T19:48:03Z" },
  assets: { id: id(7), content_item_id: id(2), content_version_id: id(4), asset_kind: "png", mime_type: "image/png",
    sha256: entry.banner_sha256, storage_bucket: "content-studio", storage_path: `${id(6)}/babylon/${id(7)}/news-card.png` },
  source_items: { id: id(3), client_id: "babylon", body: SOURCE },
};
test("standalone review gateway stays on delivery-only trial scope and new anchored daily job", async () => {
  const calls: any[] = [];
  const handler = createContentOpsReviewHandler({ getEnv: k => ENV[k], releaseSha: () => RELEASE, clock: () => NOW,
    trialResolver: async supplied => { assert.deepEqual(supplied, identity); return receipt(); },
    fetcher: async (url, init) => { const path = new URL(String(url)).pathname; calls.push({ path, method: init?.method, body: init?.body && JSON.parse(String(init.body)) });
      return Response.json(path.includes("/rpc/") ? 1 : [rows[path.split("/").pop()!]]); } });
  const response = await handler(courierReq(), native);
  assert.equal(response.status, 200); const body = await response.json();
  assert.deepEqual(body.scope, { mode: "trial", packet_mode: "trial_delivery_only_v1", content_version_id: entry.content_version_id,
    ...identity, manifest_sha256: trialManifestHash(manifest) });
  assert.equal(calls.length, 6); assert.deepEqual(calls.at(-1), { path: "/rest/v1/rpc/content_ops_reconcile_daily", method: "POST",
    body: { target_workspace_id: id(6), target_content_version_id: id(4) } });
});

test("standalone gateway rejects invalid manifest, historical job, expiry and mode bypass before business writes", async () => {
  for (const change of ["wrong-id", "two-entries", "old-job", "expiry", "canary", "off"]) {
    let reads = 0, writes = 0, resolves = 0;
    const handler = createContentOpsReviewHandler({ getEnv: k => change === "off" && k === "CONTENT_OPS_TRIAL_GATEWAY_ENABLED" ? "false" : ENV[k],
      releaseSha: () => RELEASE, clock: () => change === "expiry" ? Date.parse(END) : NOW,
      trialResolver: async () => { resolves++; return receipt(); }, fetcher: async (url, init) => {
        if (init?.method === "POST") { writes++; throw Error("forbidden business RPC"); }
        reads++; const row = rows[new URL(String(url)).pathname.split("/").pop()!];
        return Response.json([change === "old-job" ? { ...row, created_at: "2026-10-15T19:46:03Z" } : row]);
      } });
    const m = change === "wrong-id" ? { ...manifest, trial_id: OLD[0] }
      : change === "two-entries" ? { ...manifest, entries: [entry, entry] } : manifest;
    const req = courierReq(m); if (change === "canary") req.headers.set("x-content-ops-mode", "canary");
    assert.equal((await handler(req, native)).status, 503); assert.equal(writes, 0);
    if (change === "old-job") assert.equal(reads, 1); else assert.equal(reads, 0);
    if (["wrong-id", "expiry", "canary", "off"].includes(change)) assert.equal(resolves, 0);
  }
});
