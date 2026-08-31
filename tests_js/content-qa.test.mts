import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import handler from "../netlify/functions/content-qa-mcp.mts";
import {
  listContentLibrary,
  type ContentLibraryDetail,
} from "../netlify/functions/_shared/content-catalog.mts";
import { getContentReviewReadiness } from "../netlify/functions/_shared/content-review-readiness.mts";
import {
  buildContentQaPackage, contentQaBannerImage, contentQaConnectorConfig,
  contentQaDatabaseConfig,
  contentQaPassConflictsWithStoredBrandQa, hasContentQaConnectorAccess,
  isEligibleContentQaReadiness, isStoredContentQaSourceSubset,
  recordContentQaVerdict, sameContentQaProvenance, type ContentQaVerdict,
  getContentQaJob,
  isNewContentQaCandidateReadiness,
} from "../netlify/functions/_shared/content-qa.mts";

const TOKEN = "content-qa-test-token-that-is-dedicated-and-long";
const WORKSPACE = "11111111-1111-4111-8111-111111111111";
const ITEM = "22222222-2222-4222-8222-222222222222";
const VERSION = "33333333-3333-4333-8333-333333333333";
const SOURCE = "https://x.com/squidrouter/status/2083266484789514640";
const SHA = "a49f66713560523f1199374322010e9092760c38";
const NOW_SECONDS = 1_788_000_000;
const PROJECT_KEY = `sb_publishable_${"p".repeat(32)}`;
function jwt(payload: Record<string, unknown>): string {
  const part = (value: Record<string, unknown>) => Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${part({ alg: "HS256", typ: "JWT" })}.${part(payload)}.${"s".repeat(32)}`;
}
const QA_KEY = jwt({
  iss: "supabase", aud: "authenticated", role: "coineasy_content_qa",
  workspace_id: WORKSPACE, environment: "production", ref: "projectref",
  sub: "codex:content-qa", capability: "content_qa_review", release_sha: SHA,
  iat: NOW_SECONDS - 60, exp: NOW_SECONDS + 3600,
  automatic_publication: false, max_external_actions: 0,
});
const QA_DB = contentQaDatabaseConfig((name) => ({
  SUPABASE_URL: "https://projectref.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: PROJECT_KEY,
  SUPABASE_CONTENT_QA_KEY: QA_KEY,
  CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE,
}[name]), NOW_SECONDS, SHA)!;
const expectedProvenance = {
  generate_job_id: "66666666-6666-4666-8666-666666666666",
  source_item_id: "77777777-7777-4777-8777-777777777777",
  source_canonical_url: SOURCE,
  source_published_at: "2026-08-29T00:00:00Z",
  banner_sha256: createHash("sha256").update(new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0])).digest("hex"),
};
const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0]);

function detail(): ContentLibraryDetail {
  return {
    content_item_id: ITEM, client_id: "squid", content_kind: "daily_news",
    title: "mutable", status: "needs_review", current_version_id: VERSION,
    created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z",
    current_version: {
      content_version_id: VERSION, version_number: 1, prompt_version: "news@1",
      locale: "ko-KR", title: "검수 제목",
      content: { spec: { headline: "검수" }, source: { url: SOURCE, submitted_content: "raw private" }, render: {} },
      channel_copy: { telegram: "검수 문구", x: "검수 문구" }, deliverables: {}, qa: {},
      generation_meta: { mock_mode: false, brand_qa: { status: "pass" }, fact_check: { status: "review" } },
      created_at: "2026-08-29T00:00:00Z",
    },
    assets: [{
      asset_id: "44444444-4444-4444-8444-444444444444", asset_kind: "png",
      filename: "card.png", mime_type: "image/png", byte_size: png.byteLength,
      sha256: createHash("sha256").update(png).digest("hex"), width: 1080, height: 1080,
      url: "https://project.supabase.co/storage/v1/object/sign/private/card.png?token=secret", expires_in: 60,
    }], figma_links: [],
  };
}

const verdict: ContentQaVerdict = {
  decision: "PASS", summary: "공식 원문과 브랜드 기준을 모두 확인했습니다.",
  fact_check: { status: "PASS", checks: ["공식 source 확인"], source_urls: [SOURCE] },
  brand_check: { status: "PASS", checks: ["브랜드 기준 확인"] }, issues: [],
  next_action: "ready_for_human_approval",
};

async function env(values: Record<string, string | undefined>, fn: () => Promise<void>) {
  const original = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  Object.defineProperty(globalThis, "Netlify", { configurable: true, value: { env: { get: (name: string) => values[name] } } });
  try { await fn(); } finally {
    if (original) Object.defineProperty(globalThis, "Netlify", original);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}
function request(body: Record<string, unknown>, token = TOKEN) {
  return new Request("https://coineasy-newscard.netlify.app/api/content-qa/mcp", {
    method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18" }, body: JSON.stringify(body),
  });
}
async function sse(response: Response): Promise<Record<string, any>> {
  const line = (await response.text()).split("\n").find((value) => value.startsWith("data: "));
  assert.ok(line); return JSON.parse(line.slice(6));
}

test("connector bearer is dedicated and independent of Grok, XAI, and Telegram env", () => {
  assert.deepEqual(contentQaConnectorConfig((name) => name === "CONTENT_QA_CONNECTOR_TOKEN" ? TOKEN : undefined), { token: TOKEN });
  for (const sensitive of ["API_SECRET", "GROK_QA_CONNECTOR_TOKEN", "XAI_API_KEY", "TELEGRAM_REVIEW_BOT_TOKEN"]) {
    assert.equal(contentQaConnectorConfig((name) => name === "CONTENT_QA_CONNECTOR_TOKEN" || name === sensitive ? TOKEN : undefined), null);
  }
  const arbitrary = { GROK_QA_RELAY_TOKEN: "different-relay", XAI_API_KEY: "different-xai", TELEGRAM_REVIEW_BOT_TOKEN: "different-telegram" };
  assert.deepEqual(contentQaConnectorConfig((name) => name === "CONTENT_QA_CONNECTOR_TOKEN" ? TOKEN : arbitrary[name as keyof typeof arbitrary]), { token: TOKEN });
  assert.equal(hasContentQaConnectorAccess(new Request("https://example.com", { headers: { Authorization: `Bearer ${TOKEN}` } }), TOKEN), true);
});

test("database access requires publishable apikey plus exact scoped Content QA JWT", () => {
  assert.deepEqual(QA_DB, {
    supabaseUrl: "https://projectref.supabase.co", projectKey: PROJECT_KEY,
    authorizationKey: QA_KEY, workspaceId: WORKSPACE,
    rpcNames: {
      listLibrary: "list_content_qa_library",
      getLibraryItem: "get_content_qa_library_item",
      getReviewReadiness: "get_content_qa_readiness",
    },
  });
  for (const claims of [
    { role: "service_role" },
    { role: "coineasy_content_qa", workspace_id: ITEM },
    { role: "coineasy_content_qa", workspace_id: WORKSPACE, environment: "preview" },
    { role: "coineasy_content_qa", capability: "content_generate" },
    { role: "coineasy_content_qa", release_sha: "f".repeat(40) },
  ]) {
    const invalid = jwt({
      iss: "supabase", aud: "authenticated", workspace_id: WORKSPACE,
      environment: "production", ref: "projectref", iat: NOW_SECONDS - 60,
      sub: "codex:content-qa", capability: "content_qa_review", release_sha: SHA,
      exp: NOW_SECONDS + 3600, automatic_publication: false,
      max_external_actions: 0, ...claims,
    });
    assert.equal(contentQaDatabaseConfig((name) => ({
      SUPABASE_URL: "https://projectref.supabase.co",
      SUPABASE_PUBLISHABLE_KEY: PROJECT_KEY,
      SUPABASE_CONTENT_QA_KEY: invalid,
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE,
    }[name]), NOW_SECONDS, SHA), null);
  }
});

test("scoped catalog and readiness reads use only QA wrappers and scoped bearer", async () => {
  const calls: Request[] = [];
  const fetcher = async (input: URL | RequestInfo, init?: RequestInit) => {
    const request = new Request(input, init); calls.push(request);
    return request.url.endsWith("/list_content_qa_library")
      ? Response.json({ items: [], next_cursor: null })
      : Response.json(null);
  };
  assert.deepEqual(await listContentLibrary(QA_DB, {
    clientId: "squid", contentKind: "daily_news", status: "needs_review", limit: 5,
  }, fetcher), { items: [], next_cursor: null });
  assert.equal(await getContentReviewReadiness(QA_DB, ITEM, VERSION, fetcher), null);
  assert.deepEqual(calls.map((call) => new URL(call.url).pathname), [
    "/rest/v1/rpc/list_content_qa_library",
    "/rest/v1/rpc/get_content_qa_readiness",
  ]);
  for (const call of calls) {
    assert.equal(call.headers.get("apikey"), PROJECT_KEY);
    assert.equal(call.headers.get("authorization"), `Bearer ${QA_KEY}`);
  }
});

test("MCP exposes exactly three content-QA-only tools and rejects wrong bearer", async () => {
  await env({ CONTENT_QA_CONNECTOR_TOKEN: TOKEN }, async () => {
    assert.equal((await handler(request({ jsonrpc: "2.0", id: 1, method: "tools/list", params: {} }, `${TOKEN}x`), {} as never)).status, 401);
    const payload = await sse(await handler(request({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }), {} as never));
    assert.equal(payload.result.serverInfo?.name ?? "coineasy-content-qa", "coineasy-content-qa");
    assert.deepEqual(payload.result.tools.map((tool: any) => tool.name), [
      "coineasy_list_content_qa_candidates", "coineasy_get_content_qa_package", "coineasy_record_content_qa_verdict",
    ]);
    const serializedTools = JSON.stringify(payload.result.tools);
    assert.doesNotMatch(serializedTools, /origintrail/);
    for (const field of ["generate_job_id", "source_item_id", "source_canonical_url", "source_published_at", "banner_sha256"]) {
      assert.match(serializedTools, new RegExp(field));
    }
  });
});

test("package is sanitized and banner bytes must match stored PNG hash", async () => {
  const item = detail(); const pack = buildContentQaPackage(item);
  assert.deepEqual(pack.source_urls, [SOURCE]);
  assert.doesNotMatch(JSON.stringify(pack), /raw private|object\/sign|token=secret/);
  const image = await contentQaBannerImage(item, async () => new Response(png, { headers: { "Content-Type": "image/png" } }));
  assert.equal(image?.sha256, item.assets[0]!.sha256);
  item.assets[0]!.sha256 = "f".repeat(64);
  assert.equal(await contentQaBannerImage(item, async () => new Response(png, { headers: { "Content-Type": "image/png" } })), null);
});

test("record RPC is DB-only and binds the exact policy, principal, model and release", async () => {
  const calls: Request[] = [];
  const receipt = await recordContentQaVerdict(QA_DB, detail(), verdict, SHA, expectedProvenance, async (input, init) => {
    calls.push(new Request(input, init));
    return Response.json({ recorded: true, status: "reviewed", job_id: "55555555-5555-4555-8555-555555555555", input_sha256: "b".repeat(64), verdict_sha256: "c".repeat(64), decision: "PASS", policy_version: "official-x-content-qa@1", reviewer_principal: "codex:content-qa", reviewer_model: "codex", reviewer_release_sha: SHA });
  });
  assert.equal(receipt.recorded, true); assert.equal(calls.length, 1);
  assert.equal(calls[0]!.headers.get("apikey"), PROJECT_KEY);
  assert.equal(calls[0]!.headers.get("authorization"), `Bearer ${QA_KEY}`);
  const body = await calls[0]!.json();
  assert.deepEqual(body, {
    target_workspace_id: WORKSPACE, target_content_item_id: ITEM, target_content_version_id: VERSION,
    target_policy_version: "official-x-content-qa@1", target_reviewer_principal: "codex:content-qa",
    target_reviewer_model: "codex", target_reviewer_release_sha: SHA, target_verdict: verdict,
    target_expected_generate_job_id: expectedProvenance.generate_job_id,
    target_expected_source_item_id: expectedProvenance.source_item_id,
    target_expected_source_canonical_url: SOURCE,
    target_expected_source_published_at: expectedProvenance.source_published_at,
    target_expected_banner_sha256: expectedProvenance.banner_sha256,
  });
  assert.doesNotMatch(JSON.stringify(body), /telegram|relay|xai/i);
});

test("record parser accepts a stored-decision mismatch only as duplicate_conflict", async () => {
  const conflict = await recordContentQaVerdict(
    QA_DB,
    detail(), { ...verdict, decision: "WARN", next_action: "human_review" }, SHA, expectedProvenance,
    async () => Response.json({
      recorded: false, status: "duplicate_conflict",
      job_id: "55555555-5555-4555-8555-555555555555",
      input_sha256: "b".repeat(64), verdict_sha256: "c".repeat(64), decision: "PASS",
      policy_version: "official-x-content-qa@1", reviewer_principal: "codex:content-qa",
      reviewer_model: "codex", reviewer_release_sha: SHA,
    }),
  );
  assert.equal(conflict.status, "duplicate_conflict");
  assert.equal(conflict.decision, "PASS");
  await assert.rejects(() => recordContentQaVerdict(
    QA_DB,
    detail(), verdict, SHA, expectedProvenance,
    async () => Response.json({
      recorded: false, status: "reviewed",
      job_id: "55555555-5555-4555-8555-555555555555",
      input_sha256: "b".repeat(64), verdict_sha256: "c".repeat(64), decision: "WARN",
      policy_version: "official-x-content-qa@1", reviewer_principal: "codex:content-qa",
      reviewer_model: "codex", reviewer_release_sha: SHA,
    }),
  ), /content_qa_record_invalid_response/);
});

test("existing Content QA job reader validates exact bounded identity and hashes", async () => {
  const config = QA_DB;
  const exact = {
    workspace_id: WORKSPACE, job_id: "55555555-5555-4555-8555-555555555555",
    content_item_id: ITEM, content_version_id: VERSION,
    source_item_id: expectedProvenance.source_item_id,
    banner_sha256: expectedProvenance.banner_sha256,
    input_sha256: "b".repeat(64), policy_version: "official-x-content-qa@1",
    decision: "PASS", verdict_sha256: "c".repeat(64),
    reviewer_principal: "codex:content-qa", reviewer_model: "codex",
    reviewer_release_sha: SHA, status: "reviewed", reviewed_at: "2026-08-29T01:00:00Z",
  };
  const calls: Request[] = [];
  const job = await getContentQaJob(config, ITEM, VERSION, async (input, init) => {
    calls.push(new Request(input, init)); return Response.json(exact);
  });
  assert.deepEqual(job, exact); assert.equal(calls.length, 1);
  assert.deepEqual(await calls[0]!.json(), {
    target_workspace_id: WORKSPACE, target_content_item_id: ITEM,
    target_content_version_id: VERSION, target_policy_version: "official-x-content-qa@1",
  });
  assert.equal(await getContentQaJob(config, ITEM, VERSION, async () => Response.json(null)), null);
  await assert.rejects(
    () => getContentQaJob(config, ITEM, VERSION, async () => Response.json({ ...exact, content_version_id: ITEM })),
    /content_qa_job_invalid_response/,
  );
  await assert.rejects(
    () => getContentQaJob(config, ITEM, VERSION, async () => Response.json({ ...exact, extra: "leak" })),
    /content_qa_job_invalid_response/,
  );
});

test("source, critical brand QA, missing banner and unstamped release fail before record RPC", async () => {
  assert.equal(isStoredContentQaSourceSubset([SOURCE], [SOURCE]), true);
  assert.equal(isStoredContentQaSourceSubset(["https://example.com"], [SOURCE]), false);
  assert.equal(sameContentQaProvenance(expectedProvenance, { ...expectedProvenance }), true);
  assert.equal(sameContentQaProvenance(expectedProvenance, { ...expectedProvenance, banner_sha256: "f".repeat(64) }), false);
  const item = detail();
  item.current_version.generation_meta.brand_qa = { checks: [{ severity: "critical", status: "review" }] };
  assert.equal(contentQaPassConflictsWithStoredBrandQa(item, verdict), true);
  item.assets = [];
  let calls = 0;
  assert.equal(await contentQaBannerImage(item, async () => { calls += 1; return new Response(png); }), null);
  assert.equal(calls, 0);
  const readiness: any = {
    content_item_id: ITEM, content_version_id: VERSION,
    generate_job_id: "66666666-6666-4666-8666-666666666666",
    source_item_id: "77777777-7777-4777-8777-777777777777",
    source_published_at: "2026-08-29T00:00:00Z", source_is_latest: true,
    source_within_24h: true, feed_active: true, feed_poll_interval_minutes: 15,
    feed_last_polled_at: "2026-08-29T01:00:00Z", feed_poll_recent: true,
    banner_sha256: "b".repeat(64), approval_count: 0, publication_count: 0,
    grok_outbox_count: 1, grok_status: "pending", grok_decision: null,
    grok_next_action: null, grok_verdict_sha256: null,
  };
  assert.equal(isEligibleContentQaReadiness(readiness), true);
  assert.equal(isNewContentQaCandidateReadiness(readiness), true);
  assert.equal(isEligibleContentQaReadiness({ ...readiness, source_within_24h: false }), false);
  assert.equal(isEligibleContentQaReadiness({ ...readiness, feed_poll_recent: false }), false);
  assert.equal(isEligibleContentQaReadiness({ ...readiness, approval_count: 1 }), false);
  const terminalLegacy = { ...readiness, grok_status: "sent", grok_decision: "PASS", grok_next_action: "ready_for_human_approval", grok_verdict_sha256: "c".repeat(64) };
  assert.equal(isEligibleContentQaReadiness(terminalLegacy), true);
  assert.equal(isNewContentQaCandidateReadiness(terminalLegacy), false);
  assert.equal(isEligibleContentQaReadiness({ ...readiness, grok_outbox_count: 0, grok_status: null }), true);
  await env({ CONTENT_QA_CONNECTOR_TOKEN: TOKEN }, async () => {
    const result = await handler(request({ jsonrpc: "2.0", id: 9, method: "tools/call", params: { name: "coineasy_record_content_qa_verdict", arguments: { content_item_id: ITEM, content_version_id: VERSION, expected_provenance: expectedProvenance, verdict } } }), {} as never);
    assert.match(await result.text(), /content_qa_release_unstamped/);
  });
});

test("MCP endpoint has no relay, Telegram, XAI, or GROK call surface", async () => {
  const source = await import("node:fs/promises").then((fs) => fs.readFile(new URL("../netlify/functions/content-qa-mcp.mts", import.meta.url), "utf8"));
  const shared = await import("node:fs/promises").then((fs) => fs.readFile(new URL("../netlify/functions/_shared/content-qa.mts", import.meta.url), "utf8"));
  assert.doesNotMatch(source, /sendGrok|Relay|Telegram|XAI_|GROK_/);
  assert.doesNotMatch(source + shared, /serviceRoleKey|contentCatalogConfig/);
  assert.match(shared, /SUPABASE_PUBLISHABLE_KEY/);
  assert.match(shared, /SUPABASE_CONTENT_QA_KEY/);
  assert.match(source, /recordContentQaVerdict/);
});
