import assert from "node:assert/strict";
import test from "node:test";
import endpoint, { config } from "../netlify/functions/ybo-trial-preflight.mts";
import { createYboTrialPreflightHandler, YBO_TRIAL_PREFLIGHT_PATH,
  YBO_TRIAL_REQUIRED_OFF_FLAGS } from "../netlify/functions/_shared/ybo-trial-preflight.mts";
import { STUDIO_BUILD_RELEASE_SHA } from "../netlify/functions/_shared/studio-release.generated.mts";

const RELEASE = "a".repeat(40), PLAN = "b".repeat(64), TOKEN = "fixture-automation-" + "c".repeat(32);
const TRIAL = "11111111-1111-4111-8111-111111111111", NOW = Date.parse("2026-10-14T13:04:51Z");
const URL = "https://coineasy-newscard.netlify.app" + YBO_TRIAL_PREFLIGHT_PATH;
const RUNTIME = { deploy: { context: "production", id: "published-fixture", published: true } };
const ENV: Record<string, string | undefined> = { STUDIO_AUTOMATION_TOKEN: TOKEN,
  YBO_TRIAL_RELEASE_SHA: RELEASE, YBO_TRIAL_ID: TRIAL, YBO_TRIAL_PLAN_SHA256: PLAN,
  YBO_TRIAL_GENERATION_GATEWAY_ENABLED: "true", API_SECRET: "fixture-existing-admin",
  RAILWAY_API_URL: "https://coineasy-content-engine-production.up.railway.app",
  ...Object.fromEntries(YBO_TRIAL_REQUIRED_OFF_FLAGS.map(key => [key, "false"])) };
function request(url = URL, headers: Record<string, string> = {}, method = "GET") {
  return new Request(url, { method, headers: { "X-Studio-Automation-Key": TOKEN,
    "X-Studio-Expected-Release-Sha": RELEASE, "X-Ybo-Trial-Id": TRIAL,
    "X-Ybo-Trial-Plan-Sha256": PLAN, ...headers } });
}
function harness(env = ENV, release: string | null = RELEASE, now = NOW) {
  const reads: string[] = [];
  const handler = createYboTrialPreflightHandler({ getEnv: key => { reads.push(key); return env[key]; },
    releaseSha: () => release, clock: () => now });
  return { reads, handler };
}
const expected = (release = RELEASE) => ({ schema_version: "ybo-trial-preflight@1", ready: true,
  netlify_release_sha: release, trial_id: TRIAL, plan_sha256: PLAN,
  generation_gateway_enabled: true, broad_public_flags_off: true, observed_at: new Date(NOW).toISOString() });

test("authenticated published exact trial preflight is bounded local config only, zero network", async () => {
  const previous = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => { calls++; throw Error("network forbidden"); };
  try {
    const h = harness(), response = await h.handler(request(), RUNTIME);
    assert.equal(response.status, 200); assert.deepEqual(await response.json(), expected());
    assert.equal(calls, 0);
    for (const key of ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "CONTENT_STUDIO_WORKSPACE_ID",
      "HIGGSFIELD_API_KEY", "TELEGRAM_BOT_TOKEN"]) assert.equal(h.reads.includes(key), false);
    for (const key of ["cache-control", "cdn-cache-control", "netlify-cdn-cache-control"]) {
      assert.equal(response.headers.get(key), "no-store");
    }
    assert.equal(response.headers.get("vary"), "X-Studio-Automation-Key");
    assert.equal(response.headers.get("set-cookie"), null);
  } finally { globalThis.fetch = previous; }
});

for (const key of YBO_TRIAL_REQUIRED_OFF_FLAGS) {
  test(`literal OFF required for ${key}, especially absent idempotency flag`, async () => {
    for (const value of [undefined, "", "true", "FALSE", "0", " false"]) {
      const h = harness({ ...ENV, [key]: value });
      const response = await h.handler(request(), RUNTIME);
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), { error: "trial_preflight_blocked" });
    }
  });
}
test("OFF generation, wrong/missing pins, malformed release and API config fail closed", async () => {
  for (const changes of [{ YBO_TRIAL_GENERATION_GATEWAY_ENABLED: "false" },
    { YBO_TRIAL_GENERATION_GATEWAY_ENABLED: undefined }, { YBO_TRIAL_GENERATION_GATEWAY_ENABLED: "TRUE" },
    { YBO_TRIAL_ID: undefined }, { YBO_TRIAL_ID: "invalid" }, { YBO_TRIAL_PLAN_SHA256: "c".repeat(64) },
    { YBO_TRIAL_RELEASE_SHA: "b".repeat(40) }, { API_SECRET: "" }, { API_SECRET: " " },
    { RAILWAY_API_URL: "https://other.invalid" }]) {
    assert.equal((await harness({ ...ENV, ...changes }).handler(request(), RUNTIME)).status, 503);
  }
  for (const release of [null, "", "a".repeat(39), "A".repeat(40), "b".repeat(40)]) {
    assert.equal((await harness(ENV, release).handler(request(), RUNTIME)).status, 503);
  }
  for (const headers of [{ "X-Studio-Expected-Release-Sha": "" }, { "X-Ybo-Trial-Id": "" },
    { "X-Ybo-Trial-Id": TRIAL + "," + TRIAL }, { "X-Ybo-Trial-Plan-Sha256": "B".repeat(64) }]) {
    assert.equal((await harness().handler(request(URL, headers), RUNTIME)).status, 503);
  }
});
test("authentication cannot be replaced with cookies, request flags or caller metadata", async () => {
  for (const token of ["", "wrong", TOKEN + "," + TOKEN, "x".repeat(513)]) {
    const h = harness();
    const response = await h.handler(request(URL, { "X-Studio-Automation-Key": token,
      Cookie: "__Host-coineasy_studio=" + TOKEN, "x-netlify-deploy-published": "true" }), RUNTIME);
    assert.equal(response.status, 401);
    assert.deepEqual(h.reads, ["STUDIO_AUTOMATION_TOKEN"]);
  }
  for (const token of [undefined, "", "a".repeat(31), "a".repeat(513)]) {
    assert.equal((await harness({ ...ENV, STUDIO_AUTOMATION_TOKEN: token }).handler(request(), RUNTIME)).status, 503);
  }
});
test("only native published production origin and route before any config read", async () => {
  for (const runtime of [undefined, {}, { deploy: { ...RUNTIME.deploy, context: "deploy-preview" } },
    { deploy: { ...RUNTIME.deploy, published: false } }, { deploy: { ...RUNTIME.deploy, id: " " } }]) {
    const h = harness(); assert.equal((await h.handler(request(), runtime as any)).status, 421);
    assert.deepEqual(h.reads, []);
  }
  for (const url of [URL.replace("coineasy-newscard", "preview"), URL.replace("https:", "http:"),
    URL.replace("/api/", "/.netlify/functions/"), URL.replace(".app/", ".app:444/")]) {
    const h = harness(); assert.equal((await h.handler(request(url), RUNTIME)).status, 421);
    assert.deepEqual(h.reads, []);
  }
});
test("no method/body/query can select an operation or bypass the fixed window", async () => {
  for (const method of ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]) {
    const h = harness(); assert.equal((await h.handler(request(URL, {}, method), RUNTIME)).status, 405);
    assert.deepEqual(h.reads, []);
  }
  for (const url of [URL + "?action=claim", URL + "#publish"]) {
    assert.equal((await harness().handler(request(url), RUNTIME)).status, 400);
  }
  const req = request(); Object.defineProperty(req, "body", { value: new ReadableStream() });
  assert.equal((await harness().handler(req, RUNTIME)).status, 400);
  for (const now of [NaN, Date.parse("2026-10-14T12:44:50.999Z"), Date.parse("2026-10-14T14:44:51Z")]) {
    assert.equal((await harness(ENV, RELEASE, now).handler(request(), RUNTIME)).status, 503);
  }
  assert.equal((await harness(ENV, RELEASE, Date.parse("2026-10-14T12:44:51Z")).handler(request(), RUNTIME)).status, 200);
});
test("real wrapper pins native runtime and build stamp; no mutable env release fallback", async () => {
  assert.equal(config.path, YBO_TRIAL_PREFLIGHT_PATH);
  const oldNetlify = globalThis.Netlify, oldNow = Date.now;
  try {
    globalThis.Netlify = { env: { get: (key: string) => ({ ...ENV, YBO_TRIAL_RELEASE_SHA: STUDIO_BUILD_RELEASE_SHA })[key] } } as any;
    Date.now = () => NOW;
    const response = await endpoint(request(URL, { "X-Studio-Expected-Release-Sha": STUDIO_BUILD_RELEASE_SHA ?? "" }), RUNTIME as any);
    assert.equal(response.status, STUDIO_BUILD_RELEASE_SHA ? 200 : 503);
    if (STUDIO_BUILD_RELEASE_SHA) assert.deepEqual(await response.json(), expected(STUDIO_BUILD_RELEASE_SHA));
  } finally { globalThis.Netlify = oldNetlify; Date.now = oldNow; }
});
