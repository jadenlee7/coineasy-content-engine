import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { createContentOpsReviewHandler } from "../netlify/functions/_shared/content-ops-review.mts";
import { trialManifestHash } from "../netlify/functions/_shared/content-ops-trial.mts";

const sha = (text: string) => createHash("sha256").update(text, "utf8").digest("hex");
const id = (n: number) => `${String(n).padStart(8,"0")}-1111-4111-8111-111111111111`;
const RELEASE = "a".repeat(40), NOW = Date.parse("2026-10-14T06:37:17Z");
const TG = "한국어 직원 검수 공지", X = "한국어 X 초안", SOURCE = "Official source text";
const PNG = Buffer.from([137,80,78,71,13,10,26,10,0]);
const entry = { client_id: "yellow", job_id: id(1), request_id: id(2), source_item_id: id(3),
  source_sha256: sha(SOURCE), content_item_id: id(2), content_version_id: id(4), banner_sha256: sha(PNG as any),
  telegram_copy_sha256: sha(TG), x_copy_sha256: sha(X) };
const manifest = { contract: "ybo-trial-courier-manifest@1", trial_id: id(5), plan_sha256: "b".repeat(64),
  release_sha: RELEASE, not_before: "2026-10-14T06:22:17Z", deadline: "2026-10-14T08:22:17Z",
  destination_policy_sha256: "c".repeat(64), entries: [entry] };
const HASH = trialManifestHash(manifest);
const ENV: Record<string,string> = { CONTENT_OPS_GATEWAY_ENABLED: "false", CONTENT_OPS_REVIEW_ENABLED: "false",
  CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED: "false", CONTENT_OPS_DAILY_BUTTON_CARD_GATEWAY_ENABLED: "false",
  CONTENT_OPS_TRIAL_GATEWAY_ENABLED: "true", CONTENT_OPS_TRIAL_ID: manifest.trial_id,
  CONTENT_OPS_TRIAL_PLAN_SHA256: manifest.plan_sha256,
  CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256: manifest.destination_policy_sha256,
  CONTENT_OPS_REVIEW_RELEASE_SHA: RELEASE, CONTENT_OPS_GATEWAY_TOKEN: "fixture-dedicated-token-at-least-32-chars",
  SUPABASE_URL: "https://example.supabase.co", SUPABASE_SERVICE_ROLE_KEY: "fixture-only",
  CONTENT_STUDIO_WORKSPACE_ID: id(6) };
const runtime = { deploy: { context: "production", id: "fixture-only", published: true } };
const rows: Record<string,any> = {
  jobs: { id: entry.job_id, client_id: "yellow", job_kind: "generate", status: "succeeded", attempts: 1,
    max_attempts: 3, created_at: "2026-10-14T06:23:17Z", started_at: "2026-10-14T06:24:17Z",
    finished_at: "2026-10-14T06:25:17Z", idempotency_key: "official-x-review:v1:2026-10-14:yellow",
    content_item_id: null, input: { workflow: "official_x_review_draft_v1", content_kind: "daily_news",
      manual_only: false, kst_date: "2026-10-14", request_id: entry.request_id,
      source_item_ids: [entry.source_item_id], source_content: SOURCE },
    output: { content_item_id: entry.content_item_id, content_version_id: entry.content_version_id,
      source_item_ids: [entry.source_item_id] } },
  content_items: { id: entry.content_item_id, client_id: "yellow", status: "needs_review", content_kind: "daily_news",
    current_version_id: entry.content_version_id },
  content_versions: { id: entry.content_version_id, content_item_id: entry.content_item_id,
    channel_copy: { telegram: TG, x: X }, generation_meta: { mock_mode: false, request_id: entry.request_id },
    deliverables: { primary_asset_id: id(7) }, created_at: "2026-10-14T06:25:17Z" },
  assets: { id: id(7), content_item_id: entry.content_item_id, content_version_id: entry.content_version_id,
    asset_kind: "png", mime_type: "image/png", sha256: entry.banner_sha256, storage_bucket: "content-studio",
    storage_path: `${id(6)}/yellow/${id(7)}/news-card.png` },
  source_items: { id: entry.source_item_id, client_id: "yellow", body: SOURCE },
};
const claim = { outbox_id: id(8), claim_token: id(9), client_id: "yellow", kst_date: "2026-10-14",
  content_item_id: entry.content_item_id, content_version_id: entry.content_version_id,
  source_item_id: entry.source_item_id, generate_job_id: entry.job_id, banner_sha256: entry.banner_sha256,
  title: "검수용", telegram_copy: TG, x_copy: X, source_url: "https://x.com/Yellow/status/123",
  source_published_at: "2026-10-13T23:00:00Z" };
function request(body: any = { action: "reconcile" }) {
  return new Request("https://coineasy-newscard.netlify.app/.netlify/functions/content-ops-review", {
    method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json",
      Authorization: `Bearer ${ENV.CONTENT_OPS_GATEWAY_TOKEN}`, "x-content-ops-expected-release-sha": RELEASE,
      "x-content-ops-mode": "trial", "x-content-ops-packet-mode": "trial_delivery_only_v1",
      "x-content-ops-version-id": entry.content_version_id, "x-content-ops-trial-id": manifest.trial_id,
      "x-content-ops-trial-plan-sha256": manifest.plan_sha256, "x-content-ops-trial-manifest-sha256": HASH } });
}
function harness(options: any = {}) {
  const calls: any[] = [], identities: any[] = []; let resolutions = 0;
  const fetcher = async (url: any, init: any) => {
    const u = new URL(String(url)); calls.push({ url: u, body: init?.body && JSON.parse(init.body), method: init?.method });
    const supplied = options.respond?.(u, init); if (supplied) return supplied;
    if (u.pathname.includes("/rpc/")) return Response.json(options.result ?? 1);
    return Response.json([ (options.rows ?? rows)[u.pathname.split("/").pop()!] ]);
  };
  const deps: any = { getEnv: (k: string) => ({ ...ENV, ...options.env })[k], releaseSha: () => RELEASE,
    clock: options.clock ?? (() => NOW), fetcher, trialDeliveryConfirm: options.confirm };
  if (!options.missing) deps.trialResolver = async (identity: any) => {
    identities.push(identity); resolutions++;
    if (options.throwResolver) throw Error("SECRET-provider-text");
    return options.resolve?.(resolutions) ?? { active: true, observed_at: new Date(NOW).toISOString(),
      manifest: options.manifest ?? manifest, manifest_sha256: HASH };
  };
  const handler = createContentOpsReviewHandler(deps);
  return { call: (r = request()) => handler(r, runtime), calls, identities };
}

test("trial has no installed resolver: headers and date alone give zero database calls", async () => {
  const h = harness({ missing: true }); const r = await h.call();
  assert.equal(r.status, 503); assert.deepEqual(await r.json(), { error: "content_ops_trial_blocked" });
  assert.equal(h.calls.length, 0);
});
test("ordinary OFF and trial OFF never read resolver/database", async () => {
  const h = harness({ env: { CONTENT_OPS_TRIAL_GATEWAY_ENABLED: "false" } });
  assert.equal((await h.call()).status, 503); assert.equal(h.identities.length, 0); assert.equal(h.calls.length, 0);
});
for (const key of ["CONTENT_OPS_GATEWAY_ENABLED", "CONTENT_OPS_REVIEW_ENABLED",
  "CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED", "CONTENT_OPS_DAILY_BUTTON_CARD_GATEWAY_ENABLED"]) {
  test(`trial cannot coexist with broad flag ${key}`, async () => {
    const h = harness({ env: { [key]: "true" } }); assert.equal((await h.call()).status, 503);
    assert.equal(h.calls.length, 0); assert.equal(h.identities.length, 0);
  });
}
for (const now of [Date.parse("2026-10-14T06:22:16Z"), Date.parse("2026-10-14T08:22:17Z")]) {
  test(`trial clock boundary ${now} has no resolver/database IO`, async () => {
    const h = harness({ clock: () => now }); assert.equal((await h.call()).status, 503);
    assert.equal(h.calls.length, 0); assert.equal(h.identities.length, 0);
  });
}
for (const override of [{ active: false }, { observed_at: "2026-10-14T06:37:11Z" },
  { observed_at: "2026-10-14T06:37:18Z" }, { manifest_sha256: "d".repeat(64) },
  { manifest: { ...manifest, deadline: "2026-10-14T06:37:17Z" } }]) {
  test(`trusted membership rejects ${JSON.stringify(override)}`, async () => {
    const h = harness({ resolve: () => ({ active: true, observed_at: new Date(NOW).toISOString(),
      manifest, manifest_sha256: HASH, ...override }) });
    assert.equal((await h.call()).status, 503); assert.equal(h.calls.length, 0);
  });
}
test("caller version/list cannot widen server-owned manifest", async () => {
  const h = harness(); const r = request(); r.headers.set("x-content-ops-version-id", id(55));
  assert.equal((await h.call(r)).status, 503); assert.equal(h.calls.length, 0);
  assert.deepEqual(h.identities, [{ trial_id: manifest.trial_id, plan_sha256: manifest.plan_sha256 }]);
});
test("projection and active owner precede one exact-version reconcile RPC; no daily discovery", async () => {
  const h = harness(); const r = await h.call(); assert.equal(r.status, 200);
  assert.equal(h.calls.length, 6);
  assert.deepEqual(h.calls.at(-1).body, { target_workspace_id: id(6), target_content_version_id: entry.content_version_id });
  assert.match(h.calls.at(-1).url.pathname, /content_ops_reconcile_daily$/);
  assert.equal(h.calls.filter(c => c.method === "POST").length, 1);
  assert.equal((await r.json()).scope.packet_mode, "trial_delivery_only_v1");
});
for (const [table, field, value] of [["jobs", "attempts", 2], ["jobs", "idempotency_key", "unrelated-job"],
  ["jobs", "content_item_id", entry.content_item_id],
  ["jobs", "output", { ...rows.jobs.output, content_item_id: id(77) }],
  ["jobs", "output", { ...rows.jobs.output, content_version_id: id(77) }],
  ["jobs", "input", { ...rows.jobs.input, request_id: id(77) }],
  ["content_items", "current_version_id", id(77)], ["assets", "sha256", "e".repeat(64)],
  ["source_items", "body", "changed source"]]) {
  test(`projection blocks changed ${table}.${field} before mutating RPC`, async () => {
    const changed = structuredClone(rows); changed[table][field] = value;
    const h = harness({ rows: changed }); assert.equal((await h.call()).status, 503);
    assert.equal(h.calls.some(c => c.method === "POST"), false);
  });
}
test("terminal owner after reads prevents next RPC; provider errors never echo", async () => {
  const h = harness({ resolve: (n: number) => ({ active: n < 8, observed_at: new Date(NOW).toISOString(),
    manifest, manifest_sha256: HASH }) });
  assert.equal((await h.call()).status, 503); assert.equal(h.calls.length, 5);
  const err = await harness({ throwResolver: true }).call(); assert.doesNotMatch(await err.text(), /SECRET/);
});
test("claim receipt must preserve exact job/source/version/banner and full Korean copy hashes", async () => {
  const body = { action: "claim", claim_token: claim.claim_token };
  const good = harness({ result: claim }); assert.equal((await good.call(request(body))).status, 200);
  for (const delta of [{ telegram_copy: TG + "!" }, { generate_job_id: id(77) }, { client_id: "babylon" }]) {
    const h = harness({ result: { ...claim, ...delta } }); assert.equal((await h.call(request(body))).status, 503);
    assert.equal(h.calls.filter(c => c.method === "POST").length, 1);
  }
});
test("prepare uses existing exact owner argument validator and immutable version", async () => {
  const body = { action: "owner", step: "prepare", args: { outbox_id: claim.outbox_id,
    claim_token: claim.claim_token, review_id: id(10) } };
  const h = harness({ result: { status: "review_prepared", review_id: id(10), version_fingerprint: "d".repeat(64),
    epoch: 0, state: "active", expires_at: "2026-10-14T07:07:17Z", execution_authorized: false } });
  assert.equal((await h.call(request(body))).status, 200);
  assert.equal(h.calls.at(-1).body.target_content_version_id, entry.content_version_id);
  assert.deepEqual(h.calls.at(-1).body.target_args, body.args);
  const bad = harness(); assert.equal((await bad.call(request({ ...body, args: { ...body.args, public: true } }))).status, 400);
  assert.equal(bad.calls.some(c => c.method === "POST"), false);
});
test("delivery-only four-part registration uses the existing bounded owner contract", async () => {
  const h64 = "e".repeat(64), card = id(11);
  const args = { review_id: id(10), card_id: card, expected_fingerprint: h64, epoch: 0,
    bindings: { bot: h64, room: h64, message: sha("controls-message"), packet_receipt: h64, card_receipt: h64,
      parent_binding: h64, thread_id: null },
    parts: ["image", "telegram", "x"].map(kind => ({ kind, outcome: "sent", message_binding: sha(`${kind}-message`), payload_sha256: h64 })),
    controls_payload_sha256: h64, response_sha256s: [h64,h64,h64,h64],
    delivered: "2026-10-14T06:37:17Z", expires: "2026-10-14T07:07:17Z" };
  const h = harness({ result: { status: "card_recorded", card_id: card, reused: false, execution_authorized: false } });
  assert.equal((await h.call(request({ action: "owner", step: "register", args }))).status, 200);
  assert.deepEqual(h.calls.at(-1).body.target_args, args);
});
test("verified PNG response attests trial identity and static packet mode; per-HTTP admission", async () => {
  const locator = { status: "ready", outbox_id: claim.outbox_id, client_id: "yellow",
    content_item_id: entry.content_item_id, content_version_id: entry.content_version_id,
    asset_id: id(7), bucket: "content-studio", path: rows.assets.storage_path, sha256: entry.banner_sha256,
    byte_size: PNG.length, execution_authorized: false };
  const respond = (u: URL) => u.pathname.includes("image_locator") ? Response.json(locator)
    : u.pathname.includes("/storage/") ? new Response(PNG, { headers: { "Content-Type": "image/png",
      "Content-Length": String(PNG.length) } }) : null;
  const h = harness({ respond }); const body = { action: "image", outbox_id: claim.outbox_id, claim_token: claim.claim_token };
  const r = await h.call(request(body)); assert.equal(r.status, 200);
  assert.equal(r.headers.get("x-content-ops-trial-manifest-sha256"), HASH);
  assert.equal(r.headers.get("x-content-ops-packet-mode"), "trial_delivery_only_v1");
  assert.deepEqual(Buffer.from(await r.arrayBuffer()), PNG);
  const closed = harness({ respond, resolve: (n: number) => ({ active: n < 9,
    observed_at: new Date(NOW).toISOString(), manifest, manifest_sha256: HASH }) });
  assert.equal((await closed.call(request(body))).status, 503);
  assert.equal(closed.calls.some(c => c.url.pathname.includes("/storage/")), false);
});
test("trial never exposes finish/public/Typefully routes", async () => {
  for (const action of ["finish", "publish", "approve", "typefully"]) {
    const h = harness(); assert.equal((await h.call(request({ action }))).status, 400); assert.equal(h.calls.length, 0);
  }
});
test("only verified terminal DB receipt can close server owner; unknown API ACK never retries send", async () => {
  const args={review_id:id(10),card_id:id(11),outbox_id:claim.outbox_id}, confirmations:any[]=[];
  const terminal={status:"sent",card_id:args.card_id,outbox_id:args.outbox_id,execution_authorized:false};
  const body={action:"owner",step:"terminal",args};
  const h=harness({result:terminal,confirm:async receipt=>{confirmations.push(receipt);}});
  assert.equal((await h.call(request(body))).status,200);
  assert.deepEqual(confirmations,[{trial_id:manifest.trial_id,plan_sha256:manifest.plan_sha256,manifest_sha256:HASH,
    content_version_id:entry.content_version_id,card_id:args.card_id,outbox_id:args.outbox_id,confirmed_parts:4,status:"sent"}]);
  const invalid=harness({result:{...terminal,card_id:id(99)},confirm:async()=>{throw Error("must not confirm");}});
  assert.equal((await invalid.call(request(body))).status,503);
  let attempts=0;
  const unknown=harness({result:terminal,confirm:async()=>{attempts++;throw Error("private API body");}});
  const response=await unknown.call(request(body));assert.equal(response.status,503);
  assert.equal(attempts,1);assert.equal(unknown.calls.filter(c=>c.method==="POST").length,1);
  assert.doesNotMatch(await response.text(),/private API body/);
  const missing=harness({result:terminal});assert.equal((await missing.call(request(body))).status,503);
  let unconfirmedCalls=0;
  const unconfirmed=harness({result:{status:"not_confirmed",card_id:null,outbox_id:null,execution_authorized:false},
    confirm:async()=>{unconfirmedCalls++;}});
  assert.equal((await unconfirmed.call(request(body))).status,200);
  assert.equal(unconfirmedCalls,0);
});
test("canonical manifest is insertion-order independent and binds source/copy identities", () => {
  assert.equal(trialManifestHash(Object.fromEntries(Object.entries(manifest).reverse())), HASH);
  assert.notEqual(trialManifestHash({ ...manifest, entries: [{ ...entry, x_copy_sha256: "d".repeat(64) }] }), HASH);
  // Fixed ASCII manifest means Python canonical ensure_ascii and JS UTF-8 have one representation.
  assert.equal(trialManifestHash({ a: [1, "test"], z: true }), "83ab1506dbc1029b70b47536d47ecf1414fa8fe042c95bb53411cf181f99543d");
});
