import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import releaseHandler from "../netlify/functions/content-ops-review-release.mts";
import { CONTENT_OPS_OTHER_PRINCIPALS } from "../netlify/functions/_shared/content-ops-gateway-auth.mts";
import { createContentOpsReviewHandler } from "../netlify/functions/_shared/content-ops-review.mts";
import { CONTENT_OPS_RELEASE_PATH, createContentOpsReviewReleaseHandler } from "../netlify/functions/_shared/content-ops-review-release.mts";
import { STUDIO_BUILD_RELEASE_SHA } from "../netlify/functions/_shared/studio-release.generated.mts";

const SHA = "a".repeat(40);
const TOKEN = "fixture_dedicated_gateway_" + "b".repeat(40);
const URL = "https://coineasy-newscard.netlify.app" + CONTENT_OPS_RELEASE_PATH;
const ENV: Record<string, string | undefined> = {
  CONTEXT: "production", CONTENT_OPS_GATEWAY_TOKEN: TOKEN,
  CONTENT_OPS_REVIEW_RELEASE_SHA: SHA, CONTENT_OPS_GATEWAY_ENABLED: "false",
  CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED: "false", STUDIO_TELEGRAM_PUBLISH_ENABLED: "false",
};
function request(url = URL, init: RequestInit = {}) {
  return new Request(url, { headers: {
    Authorization: `Bearer ${TOKEN}`, "x-content-ops-expected-release-sha": SHA,
  }, ...init });
}
function harness(env = ENV, release: string | null = SHA) {
  const reads: string[] = []; let releaseReads = 0;
  const handler = createContentOpsReviewReleaseHandler({
    getEnv: name => { reads.push(name); return env[name]; },
    releaseSha: () => { releaseReads++; return release; },
  });
  return { handler, reads, releaseReads: () => releaseReads };
}
function expected(release = SHA) {
  return { schema_version: "content-ops-release-readback@1", ok: true, netlify_release_sha: release,
    gateway_enabled: false, button_gateway_enabled: false, public_telegram_publish_enabled: false };
}
function noCache(response: Response) {
  for (const header of ["cache-control", "cdn-cache-control", "netlify-cdn-cache-control"]) {
    assert.equal(response.headers.get(header), "no-store");
  }
  assert.equal(response.headers.get("vary"), "Authorization");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(response.headers.get("set-cookie"), null);
  assert.equal(response.headers.get("access-control-allow-origin"), null);
}

test("authenticated OFF readback returns only immutable SHA and local OFF flags with zero fetch", async () => {
  const before = { ...ENV }; const originalFetch = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => { calls++; throw Error("external_io_forbidden"); };
  try {
    const h = harness(); const response = await h.handler(request());
    assert.equal(response.status, 200); noCache(response);
    assert.deepEqual(await response.json(), expected());
    assert.equal(calls, 0); assert.deepEqual(ENV, before);
    assert.equal(h.reads.includes("CONTENT_STUDIO_WORKSPACE_ID"), false);
    assert.equal(h.reads.includes("SUPABASE_URL"), false);
    assert.equal(h.reads.includes("CONTENT_OPS_REVIEW_CANARY_VERSION_ID"), false);
  } finally { globalThis.fetch = originalFetch; }
});

test("methods including HEAD and OPTIONS cannot become a send or auth bypass", async () => {
  for (const method of ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]) {
    const h = harness(); const response = await h.handler(request(URL, { method }));
    assert.equal(response.status, 405, method); noCache(response);
    assert.deepEqual(h.reads, []); assert.equal(h.releaseReads(), 0);
  }
});

test("wrong origin, scheme, port, path and preview contexts fail before token or release reads", async () => {
  for (const url of [
    "https://preview.example" + CONTENT_OPS_RELEASE_PATH,
    "https://coineasy-newscard.netlify.app.evil.example" + CONTENT_OPS_RELEASE_PATH,
    "http://coineasy-newscard.netlify.app" + CONTENT_OPS_RELEASE_PATH,
    "https://coineasy-newscard.netlify.app:444" + CONTENT_OPS_RELEASE_PATH,
    "https://coineasy-newscard.netlify.app/.netlify/functions/content-ops-review",
  ]) {
    const h = harness(); assert.equal((await h.handler(request(url))).status, 421);
    assert.equal(h.reads.includes("CONTENT_OPS_GATEWAY_TOKEN"), false); assert.equal(h.releaseReads(), 0);
  }
  for (const context of [undefined, "", "deploy-preview", "branch-deploy", "Production"]) {
    const h = harness({ ...ENV, CONTEXT: context });
    assert.equal((await h.handler(request())).status, 421);
    assert.deepEqual(h.reads, ["CONTEXT"]); assert.equal(h.releaseReads(), 0);
  }
});

test("missing or wrong bearer, duplicate auth, Studio key and cookie cannot read SHA or flags", async () => {
  const headers = [
    {}, { Authorization: "Bearer wrong" }, { Authorization: TOKEN },
    { Authorization: `bearer ${TOKEN}` }, { Authorization: `Bearer ${TOKEN}, Bearer ${TOKEN}` },
    { "X-Studio-Automation-Key": TOKEN }, { Cookie: `__Host-coineasy_studio=${TOKEN}` },
  ];
  for (const header of headers) {
    const h = harness(); const response = await h.handler(request(URL, { headers: header }));
    assert.equal(response.status, 401); noCache(response);
    assert.deepEqual(await response.json(), { error: "invalid_token" });
    assert.equal(h.releaseReads(), 0);
    assert.equal(h.reads.includes("CONTENT_OPS_GATEWAY_ENABLED"), false);
  }
});

test("gateway token config boundaries and all other execution principals fail closed", async () => {
  for (const token of [undefined, "", "x".repeat(31), "x".repeat(257), " invalid", "bad.token".repeat(8)]) {
    const h = harness({ ...ENV, CONTENT_OPS_GATEWAY_TOKEN: token });
    assert.equal((await h.handler(request())).status, 503); assert.equal(h.releaseReads(), 0);
  }
  for (const name of CONTENT_OPS_OTHER_PRINCIPALS) {
    const h = harness({ ...ENV, [name]: TOKEN });
    const response = await h.handler(request());
    assert.equal(response.status, 503, name);
    assert.deepEqual(await response.json(), { error: "content_ops_token_not_configured" });
    assert.equal(h.releaseReads(), 0);
  }
  for (const length of [32, 256]) {
    const token = "t".repeat(length); const h = harness({ ...ENV, CONTENT_OPS_GATEWAY_TOKEN: token });
    const req = request(); req.headers.set("authorization", `Bearer ${token}`);
    assert.equal((await h.handler(req)).status, 200);
  }
});

test("query parameters and GET bodies never select an action, version, destination or URL", async () => {
  for (const query of ["?action=claim", "?action=publish", "?version=anything", "?url=https://example.invalid", "?token=anything"]) {
    const h = harness(); const response = await h.handler(request(URL + query));
    assert.equal(response.status, 400); assert.equal(h.releaseReads(), 0);
    assert.deepEqual(await response.json(), { error: "invalid_request" });
  }
  for (const length of ["1", "-1", "invalid"]) {
    const h = harness(); const req = request(); req.headers.set("content-length", length);
    assert.equal((await h.handler(req)).status, 400); assert.equal(h.releaseReads(), 0);
  }
  const req = request(); const body = new ReadableStream<Uint8Array>();
  Object.defineProperty(req, "body", { value: body });
  const h = harness(); assert.equal((await h.handler(req)).status, 400);
  assert.equal(body.locked, false); assert.equal(h.releaseReads(), 0);
});

test("build stamp, configured release and exact request SHA must all match", async () => {
  for (const release of [null, "", "bad", "A".repeat(40), "a".repeat(39), "b".repeat(40)]) {
    const response = await harness(ENV, release).handler(request());
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "content_ops_release_mismatch" });
  }
  for (const release of [undefined, "", "b".repeat(40), "A".repeat(40)]) {
    assert.equal((await harness({ ...ENV, CONTENT_OPS_REVIEW_RELEASE_SHA: release }).handler(request())).status, 503);
  }
  for (const expected of [undefined, "", "b".repeat(40), "A".repeat(40), "a".repeat(39)]) {
    const req = request(); req.headers.delete("x-content-ops-expected-release-sha");
    if (expected !== undefined) req.headers.set("x-content-ops-expected-release-sha", expected);
    const response = await harness().handler(req); assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "content_ops_release_mismatch" });
  }
});

test("ON, missing, empty and malformed flags are not evidence of OFF", async () => {
  for (const key of ["CONTENT_OPS_GATEWAY_ENABLED", "CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED", "STUDIO_TELEGRAM_PUBLISH_ENABLED"]) {
    for (const value of ["true", "TRUE", "FALSE", "0", "", undefined]) {
      const response = await harness({ ...ENV, [key]: value }).handler(request());
      assert.equal(response.status, 503, `${key}:${value}`);
      assert.deepEqual(await response.json(), { error: "content_ops_off_state_required" });
    }
  }
});

test("unexpected environment/build errors cannot reflect sensitive data", async () => {
  for (const failEnv of [true, false]) {
    const h = createContentOpsReviewReleaseHandler({
      getEnv: name => { if (failEnv) throw Error("fixture-private-secret"); return ENV[name]; },
      releaseSha: () => { throw Error("fixture-private-secret"); },
    });
    const response = await h(request()); assert.equal(response.status, 503); noCache(response);
    assert.deepEqual(await response.json(), { error: "content_ops_release_unavailable" });
  }
});

test("successful release GET does not unlock any old mutation path or invoke an RPC", async () => {
  const reads: string[] = []; let rpcCalls = 0;
  const mutation = createContentOpsReviewHandler({
    getEnv: name => { reads.push(name); return ENV[name]; }, releaseSha: () => SHA,
    fetcher: async () => { rpcCalls++; throw Error("must_not_call"); },
  });
  assert.equal((await harness().handler(request())).status, 200);
  for (const action of ["reconcile", "claim", "begin", "finish", "image", "owner", "publish", "release"]) {
    const response = await mutation(new Request(URL.replace("-release", ""), {
      method: "POST", headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    }));
    assert.equal(response.status, 503); assert.deepEqual(await response.json(), { error: "content_ops_disabled" });
  }
  assert.deepEqual(reads, Array(8).fill("CONTENT_OPS_GATEWAY_ENABLED")); assert.equal(rpcCalls, 0);
});

test("production adapter uses the generated build stamp, not caller/config SHA", async () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  const release = STUDIO_BUILD_RELEASE_SHA || SHA;
  Object.defineProperty(globalThis, "Netlify", { configurable: true, value: { env: {
    get: (name: string) => ({ ...ENV, CONTENT_OPS_REVIEW_RELEASE_SHA: release }[name]),
  } } });
  try {
    const req = request(); req.headers.set("x-content-ops-expected-release-sha", release);
    const response = await releaseHandler(req);
    if (STUDIO_BUILD_RELEASE_SHA) {
      assert.equal(response.status, 200); assert.deepEqual(await response.json(), expected(STUDIO_BUILD_RELEASE_SHA));
    } else {
      assert.equal(response.status, 503); assert.deepEqual(await response.json(), { error: "content_ops_release_mismatch" });
    }
    if (process.env.EXPECTED_STUDIO_RELEASE_SHA !== undefined) {
      assert.equal(STUDIO_BUILD_RELEASE_SHA, process.env.EXPECTED_STUDIO_RELEASE_SHA);
      assert.equal(response.status, 200);
    }
  } finally {
    if (original) Object.defineProperty(globalThis, "Netlify", original);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
});

test("release dependency tree stays isolated from catalog, DB and provider modules", async () => {
  for (const file of [
    "../netlify/functions/content-ops-review-release.mts",
    "../netlify/functions/_shared/content-ops-review-release.mts",
    "../netlify/functions/_shared/content-ops-gateway-auth.mts",
  ]) {
    const source = await readFile(new globalThis.URL(file, import.meta.url), "utf8");
    const imports = [...source.matchAll(/from\s+["']([^"']+)["']/g)].map(m => m[1]);
    assert.ok(imports.every(p => ["node:crypto", "./_shared/content-ops-review-release.mts", "./_shared/studio-release.mts", "./content-ops-gateway-auth.mts"].includes(p)), file);
    assert.doesNotMatch(source, /\bfetch\s*\(|\bimport\s*\(/, file);
  }
});
