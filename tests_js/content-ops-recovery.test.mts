import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { createContentOpsReviewHandler } from "../netlify/functions/_shared/content-ops-review.mts";
import { recoveryPlanHash, boundedRecoveryJson } from "../netlify/functions/_shared/content-ops-recovery.mts";

const id = (n: number) => `${String(n).padStart(8, "0")}-1111-4111-8111-111111111111`;
const hash = (v: string | Buffer) => createHash("sha256").update(v).digest("hex");
const RELEASE = "a".repeat(40), NOW = Date.parse("2026-10-16T08:00:00Z");
const PNG = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10, 0]);
const TG = "직원 검수 공지", X = "한국어 X 초안";
const plan = {schema_version: "private-review-recovery@1", recovery_id: id(1), workspace_id: id(2),
  client_id: "babylon", outbox_id: id(3), content_item_id: id(4), content_version_id: id(5),
  source_item_id: id(6), generate_job_id: id(7), banner_asset_id: id(8), banner_sha256: hash(PNG),
  telegram_copy_sha256: hash(TG), x_copy_sha256: hash(X), original_outbox_sha256: "b".repeat(64),
  original_trial_id: id(9), original_plan_sha256: "c".repeat(64), destination_policy_sha256: "d".repeat(64),
  release_sha: RELEASE, not_before: "2026-10-16T07:55:00Z", deadline: "2026-10-16T09:55:00Z",
  estimated_daily_limit_usd_micros: 10_000_000};
const SHA = recoveryPlanHash(plan);
const ENV: Record<string, string> = {CONTENT_OPS_RECOVERY_GATEWAY_ENABLED: "true",
  CONTENT_OPS_GATEWAY_ENABLED: "false", CONTENT_OPS_REVIEW_ENABLED: "false",
  CONTENT_OPS_TRIAL_GATEWAY_ENABLED: "false", CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED: "false",
  CONTENT_OPS_DAILY_BUTTON_CARD_GATEWAY_ENABLED: "false", YBO_TRIAL_GENERATION_GATEWAY_ENABLED: "false",
  TELEGRAM_PUBLICATION_ENABLED: "false", STUDIO_TELEGRAM_PUBLISH_ENABLED: "false",
  CONTENT_OPS_RECOVERY_PLAN_JSON: JSON.stringify(plan), CONTENT_OPS_RECOVERY_PLAN_SHA256: SHA,
  CONTENT_OPS_REVIEW_RELEASE_SHA: RELEASE, CONTENT_OPS_GATEWAY_TOKEN: "fixture-only-dedicated-gateway-token",
  SUPABASE_URL: "https://example.supabase.co", SUPABASE_SERVICE_ROLE_KEY: "fixture-only",
  CONTENT_STUDIO_WORKSPACE_ID: plan.workspace_id};
const runtime = {deploy: {context: "production", id: "fixture", published: true}};
const claim = {outbox_id: plan.outbox_id, claim_token: id(10), client_id: "babylon", kst_date: "2026-10-16",
  content_item_id: plan.content_item_id, content_version_id: plan.content_version_id,
  source_item_id: plan.source_item_id, generate_job_id: plan.generate_job_id, banner_sha256: plan.banner_sha256,
  title: "검수 전용", telegram_copy: TG, x_copy: X,
  source_url: "https://x.com/babylonlabs_io/status/123", source_published_at: "2026-10-15T14:21:00Z"};
const locator = {status: "ready", outbox_id: plan.outbox_id, client_id: plan.client_id,
  content_item_id: plan.content_item_id, content_version_id: plan.content_version_id, asset_id: plan.banner_asset_id,
  bucket: "content-studio", path: `${plan.workspace_id}/babylon/${plan.banner_asset_id}/news-card.png`,
  sha256: plan.banner_sha256, byte_size: PNG.length, execution_authorized: false};
const terminalArgs = {review_id: id(11), card_id: id(12), outbox_id: plan.outbox_id};
const terminal = {status: "recovery_sent", recovery_id: plan.recovery_id, card_id: id(12),
  outbox_id: plan.outbox_id, original_outbox_status: "claimed", execution_authorized: false};
function request(body: any = {action: "reconcile"}) {
  return new Request("https://coineasy-newscard.netlify.app/.netlify/functions/content-ops-review", {
    method: "POST", body: JSON.stringify(body), headers: {"Content-Type": "application/json",
      Authorization: `Bearer ${ENV.CONTENT_OPS_GATEWAY_TOKEN}`, "x-content-ops-expected-release-sha": RELEASE,
      "x-content-ops-mode": "recovery", "x-content-ops-packet-mode": "recovery_delivery_only_v1",
      "x-content-ops-version-id": plan.content_version_id, "x-content-ops-recovery-id": plan.recovery_id,
      "x-content-ops-recovery-plan-sha256": SHA}});
}
function harness(options: any = {}) {
  const calls: any[] = [], env = {...ENV, ...options.env};
  const status = {active: true, observed_at: new Date(NOW).toISOString(), plan, plan_sha256: SHA};
  const handler = createContentOpsReviewHandler({getEnv: k => env[k], releaseSha: () => RELEASE,
    clock: options.clock ?? (() => NOW), fetcher: (async (url: any, init: any) => {
      const body = init?.body && JSON.parse(init.body); calls.push({url: String(url), init, body});
      const override = options.respond?.(body?.target_action, calls.length, url, init);
      if (override) return override;
      if (String(url).includes("/storage/")) return new Response(PNG, {headers: {"content-type": "image/png"}});
      assert.match(String(url), /\/rest\/v1\/rpc\/content_ops_recovery_step$/);
      if (body.target_action === "status") return Response.json(options.status ?? status, {headers: options.rpcHeaders});
      return Response.json(options.result ?? ({claim, image: locator, begin: {accepted: true}, terminal} as any)[body.target_action] ?? 1,
        {headers: options.rpcHeaders});
    }) as typeof fetch});
  return {calls, env, call: (r = request()) => handler(r, options.runtime ?? runtime)};
}

test("OFF without plan has zero network; does not parse plan or credentials", async () => {
  const h = harness({env: {CONTENT_OPS_RECOVERY_GATEWAY_ENABLED: "false", CONTENT_OPS_RECOVERY_PLAN_JSON: "invalid"}});
  assert.equal((await h.call()).status, 503); assert.equal(h.calls.length, 0);
});
for (const key of Object.keys(ENV).filter(k => ENV[k] === "false")) {
  for (const value of ["true", "0", "FALSE"]) test(`competing flag ${key}=${value} blocks before I/O`, async () => {
    const h = harness({env: {[key]: value}}); assert.equal((await h.call()).status, 503); assert.equal(h.calls.length, 0);
  });
}
for (const delta of [{client_id: "yellow"}, {estimated_daily_limit_usd_micros: 10_000_001},
  {release_sha: "f".repeat(40)}, {deadline: "2026-10-16T09:55:01Z"}, {not_before: "2026-10-16T07:55:00+00:00"},
  {outbox_id: "00000000-0000-0000-0000-000000000000"}, {extra: true}, {banner_sha256: "A".repeat(64)}]) {
  test(`invalid plan cannot be enabled: ${JSON.stringify(delta)}`, async () => {
    const next = {...plan, ...delta}; const h = harness({env: {CONTENT_OPS_RECOVERY_PLAN_JSON: JSON.stringify(next),
      CONTENT_OPS_RECOVERY_PLAN_SHA256: recoveryPlanHash(next)}});
    assert.equal((await h.call()).status, 503); assert.equal(h.calls.length, 0);
  });
}
for (const [key, value] of [["x-content-ops-recovery-id", id(99)], ["x-content-ops-version-id", id(99)],
  ["x-content-ops-recovery-plan-sha256", "f".repeat(64)], ["x-content-ops-packet-mode", "button_card_v1"],
  ["x-content-ops-trial-id", id(99)], ["x-content-ops-daily-scope", "ybo_v1"], ["Authorization", "Bearer invalid"]]) {
  test(`mismatched header ${key} blocks before I/O`, async () => {
    const h = harness(), req = request(); req.headers.set(key, value);
    assert.notEqual((await h.call(req)).status, 200); assert.equal(h.calls.length, 0);
  });
}
for (const body of [{action: "finish", outbox_id: plan.outbox_id, claim_token: id(10), outcome: "sent", message_id: 1},
  {action: "status", injected: true}, {action: "image", outbox_id: id(99), claim_token: id(10)},
  {action: "owner", step: "publish", args: {}}, {action: "reconcile", workspace_id: id(99)}]) {
  test(`invalid action zero I/O ${body.action}`, async () => {
    const h = harness(); assert.equal((await h.call(request(body))).status, 400); assert.equal(h.calls.length, 0);
  });
}
for (const now of [Date.parse(plan.not_before) - 1, Date.parse(plan.deadline)]) {
  test(`window boundary ${now} blocks before I/O`, async () => {
    const h = harness({clock: () => now}); assert.equal((await h.call()).status, 503); assert.equal(h.calls.length, 0);
  });
}
test("status has exact authenticated scope, no claim or delivery mutation", async () => {
  const h = harness(); const response = await h.call(request({action: "status"}));
  assert.equal(response.status, 200); const value = await response.json();
  assert.equal(value.scope.recovery_id, plan.recovery_id); assert.deepEqual(value.status.plan, plan);
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].body.target_action, "status");
});
test("reconcile is one current status plus one recovery-only RPC; no legacy requeue", async () => {
  const h = harness(); assert.equal((await h.call()).status, 200);
  assert.deepEqual(h.calls.map(c => c.body.target_action), ["status", "reconcile"]);
  assert.deepEqual(h.calls[1].body, {target_workspace_id: plan.workspace_id,
    target_content_version_id: plan.content_version_id, target_recovery_id: plan.recovery_id,
    target_plan_sha256: SHA, target_release_sha: RELEASE, target_action: "reconcile", target_args: {}});
  for (const c of h.calls) assert.equal(new Headers(c.init.headers).get("accept-encoding"), "identity");
});
for (const delta of [{active: false}, {observed_at: new Date(NOW + 1).toISOString()},
  {observed_at: new Date(NOW - 5001).toISOString()}, {plan_sha256: "f".repeat(64)},
  {plan: {...plan, outbox_id: id(99)}}, {extra: true}]) {
  test(`untrusted status blocks business RPC ${JSON.stringify(delta)}`, async () => {
    const h = harness({status: {active: true, observed_at: new Date(NOW).toISOString(), plan, plan_sha256: SHA, ...delta}});
    assert.equal((await h.call()).status, 503); assert.deepEqual(h.calls.map(c => c.body.target_action), ["status"]);
  });
}
test("claim projection is bound to preserved identity and both exact copy hashes", async () => {
  const body = {action: "claim", claim_token: claim.claim_token};
  assert.equal((await harness().call(request(body))).status, 200);
  for (const delta of [{telegram_copy: TG + "!"}, {x_copy: X + "!"}, {outbox_id: id(99)}, {client_id: "yellow"}]) {
    const h = harness({result: {...claim, ...delta}}); assert.equal((await h.call(request(body))).status, 503);
    assert.equal(h.calls.filter(c => c.body.target_action === "claim").length, 1);
  }
});
test("exact PNG requires status before locator and before canonical storage read", async () => {
  const h = harness(); const r = await h.call(request({action: "image", outbox_id: plan.outbox_id, claim_token: id(10)}));
  assert.equal(r.status, 200); assert.deepEqual(Buffer.from(await r.arrayBuffer()), PNG);
  assert.equal(r.headers.get("x-content-ops-recovery-id"), plan.recovery_id);
  assert.deepEqual(h.calls.map(c => c.body?.target_action ?? "storage"), ["status", "image", "status", "storage"]);
});
for (const range of [undefined, "0-0/*", "0-0/1"]) {
  for (const unit of [undefined, "items"]) {
    test(`scalar RPC item-range metadata accepted across recovery actions: ${range}/${unit}`, async () => {
      const rpcHeaders: Record<string, string> = {};
      if (range !== undefined) rpcHeaders["content-range"] = range;
      if (unit !== undefined) rpcHeaders["range-unit"] = unit;
      const scalar = {active: true};
      assert.deepEqual(await boundedRecoveryJson(Response.json(scalar, {headers: rpcHeaders})), scalar);
      for (const body of [{action: "status"}, {action: "reconcile"}, {action: "claim", claim_token: id(10)},
        {action: "image", outbox_id: plan.outbox_id, claim_token: id(10)},
        {action: "owner", step: "terminal", args: terminalArgs}]) {
        const h = harness({rpcHeaders}); const r = await h.call(request(body));
        assert.equal(r.status, 200, body.action);
        if (body.action === "image") assert.deepEqual(Buffer.from(await r.arrayBuffer()), PNG);
      }
    });
  }
}
for (const headers of [{"content-range": "bytes 0-0/1"}, {"content-range": "0-1/2"},
  {"content-range": "0-0/2"}, {"content-range": "1-1/*"}, {"content-range": "*/0"},
  {"content-range": "0-0/1, 0-0/1"}, {"content-range": "invalid"}, {"range-unit": "bytes"},
  {"content-range": "0-0/1", "range-unit": "bytes"}, {"range-unit": "Items"}]) {
  test(`scalar RPC malformed or non-singleton range blocks before business RPC: ${JSON.stringify(headers)}`, async () => {
    // Use a fully valid body so only the header contract can reject it.
    await assert.rejects(() => boundedRecoveryJson(Response.json({active: true}, {headers})));
    const h = harness({rpcHeaders: headers});
    assert.equal((await h.call()).status, 503);
    assert.deepEqual(h.calls.map(c => c.body.target_action), ["status"]);
  });
}
test("scalar RPC one-item metadata still rejects HTTP 206 and PNG Storage ranges", async () => {
  await assert.rejects(() => boundedRecoveryJson(Response.json({active: true},
    {status: 206, headers: {"content-range": "0-0/1", "range-unit": "items"}})));
  for (const range of ["0-0/*", "0-0/1", "bytes 0-8/9"]) {
    const h = harness({rpcHeaders: {"content-range": "0-0/1", "range-unit": "items"}, respond: (action: string) => {
      if (!action) return new Response(PNG, {headers: {"content-type": "image/png", "content-range": range}});
    }});
    assert.equal((await h.call(request({action: "image", outbox_id: plan.outbox_id, claim_token: id(10)}))).status, 503);
    assert.deepEqual(h.calls.map(c => c.body?.target_action ?? "storage"), ["status", "image", "status", "storage"]);
  }
});
test("image cannot return after expiry during storage response", async () => {
  let now = NOW;
  const h = harness({clock: () => now, respond: (action: string) => {
    if (!action) {now = Date.parse(plan.deadline); return new Response(PNG, {headers: {"content-type": "image/png"}});}
  }});
  assert.equal((await h.call(request({action: "image", outbox_id: plan.outbox_id, claim_token: id(10)}))).status, 503);
});
test("config change after status prevents one business mutation", async () => {
  let h: any; h = harness({respond: (action: string) => {
    if (action === "status") h.env.CONTENT_OPS_RECOVERY_GATEWAY_ENABLED = "false";
  }});
  assert.equal((await h.call()).status, 503); assert.equal(h.calls.length, 1);
});
test("expired terminal reconciles exact recovery receipt without active status or original sent rewrite", async () => {
  const h = harness({clock: () => Date.parse(plan.deadline) + 1});
  const r = await h.call(request({action: "owner", step: "terminal", args: terminalArgs}));
  assert.equal(r.status, 200); assert.deepEqual(h.calls.map(c => c.body.target_action), ["terminal"]);
  const value = await r.json(); assert.equal(value.owner.status, "recovery_sent");
  assert.equal(value.owner.original_outbox_status, "claimed");
});
for (const delta of [{status: "sent"}, {recovery_id: id(99)}, {original_outbox_status: "sent"}, {execution_authorized: true}]) {
  test(`terminal rejects authority or identity drift ${JSON.stringify(delta)}`, async () => {
    const h = harness({result: {...terminal, ...delta}});
    assert.equal((await h.call(request({action: "owner", step: "terminal", args: terminalArgs}))).status, 503);
    assert.equal(h.calls.length, 1);
  });
}
test("ambiguous RPC failure is not retried and raw errors are never reflected", async () => {
  const h = harness({respond: (action: string) => {if (action === "claim") throw Error("SECRET-provider-body");}});
  const r = await h.call(request({action: "claim", claim_token: id(10)}));
  assert.equal(r.status, 503); assert.doesNotMatch(await r.text(), /SECRET/);
  assert.deepEqual(h.calls.map(c => c.body.target_action), ["status", "claim"]);
});
for (const response of [() => new Response("{}", {headers: {"content-type": "application/json", "content-encoding": "gzip"}}),
  () => new Response("{}", {headers: {"content-type": "application/json", "content-length": "1"}}),
  () => new Response("x".repeat(16385), {headers: {"content-type": "application/json"}}),
  () => Response.json({}, {status: 201})]) {
  test("recovery JSON rejects encoding, length, size, or HTTP mismatch", async () => {
    await assert.rejects(() => boundedRecoveryJson(response()));
  });
}
