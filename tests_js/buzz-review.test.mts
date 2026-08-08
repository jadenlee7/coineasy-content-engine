import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import buzzReviewHandler from "../netlify/functions/buzz-review-origintrail.mts";

const TOKEN = "buzz-review-worker-token-that-is-dedicated";
const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const JOB_ID = "22222222-2222-4222-8222-222222222222";
const CHANNEL_ID = "33333333-3333-4333-8333-333333333333";
const DELIVERY_EVENT_ID = "a".repeat(64);
const ROOT_ID = "b".repeat(64);
const DECISION_EVENT_ID = "c".repeat(64);
const REVIEWER = "d".repeat(64);
const MESSAGE_SHA256 = "e".repeat(64);
const PROTOCOL_VERSION = "origintrail-buzz-review@2";
const CREATED_AT = 1_786_100_000;
const PROTOCOL_START_EPOCH = 1_786_000_000;

function env(overrides: Record<string, string | undefined> = {}) {
  const values: Record<string, string | undefined> = {
    BUZZ_REVIEW_WORKER_TOKEN: TOKEN,
    BUZZ_REVIEWER_PUBKEYS: REVIEWER,
    BUZZ_REVIEW_PROTOCOL_START_EPOCH: String(PROTOCOL_START_EPOCH),
    BUZZ_SHADOW_ACCESS_TOKEN: "shadow-token-that-is-dedicated-and-long-enough",
    BUZZ_DELIVERY_WORKER_TOKEN: "delivery-token-that-is-dedicated-and-long-enough",
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "s".repeat(40),
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    ...overrides,
  };
  return (name: string) => values[name];
}

async function withEnvironment(
  getEnv: (name: string) => string | undefined,
  fetcher: typeof fetch,
  run: () => Promise<void>,
): Promise<void> {
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  const originalFetch = globalThis.fetch;
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: { env: { get: getEnv } },
  });
  globalThis.fetch = fetcher;
  try {
    await run();
  } finally {
    globalThis.fetch = originalFetch;
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

function request(body: object, token = TOKEN): Request {
  return new Request("https://console.example/api/buzz-review/origintrail", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-coineasy-buzz-review-key": token,
    },
    body: JSON.stringify(body),
  });
}

function hashRecord(
  decision = "approved",
  reason: string | null = null,
): string {
  return createHash("sha256").update([
    "coineasy-buzz-review-decision", "2.0", WORKSPACE_ID, JOB_ID,
    DELIVERY_EVENT_ID, CHANNEL_ID, ROOT_ID, MESSAGE_SHA256, PROTOCOL_VERSION,
    DECISION_EVENT_ID, REVIEWER,
    decision, reason ?? "", String(CREATED_AT),
  ].join("\0"), "utf8").digest("hex");
}

function recordBody(extra: Record<string, unknown> = {}) {
  return {
    action: "record",
    job_id: JOB_ID,
    delivery_event_id: DELIVERY_EVENT_ID,
    channel_id: CHANNEL_ID,
    root_relay_event_id: ROOT_ID,
    message_sha256: MESSAGE_SHA256,
    protocol_version: PROTOCOL_VERSION,
    decision_event_id: DECISION_EVENT_ID,
    reviewer_pubkey: REVIEWER,
    decision: "approved",
    reason: null,
    command_sha256: hashRecord(),
    command_created_at_epoch: CREATED_AT,
    ...extra,
  };
}

function recordRpcResponse(reused: boolean): Record<string, unknown> {
  return {
    schema_version: "2.0",
    mode: "publish_intent_review",
    workspace_id: WORKSPACE_ID,
    job_id: JOB_ID,
    delivery_event_id: DELIVERY_EVENT_ID,
    channel_id: CHANNEL_ID,
    root_relay_event_id: ROOT_ID,
    message_sha256: MESSAGE_SHA256,
    protocol_version: PROTOCOL_VERSION,
    decision_event_id: DECISION_EVENT_ID,
    reviewer_pubkey: REVIEWER,
    decision: "approved",
    reason: null,
    command_sha256: hashRecord(),
    command_created_at_epoch: CREATED_AT,
    reused,
  };
}

test("review endpoint authenticates before parsing or storage", async () => {
  let calls = 0;
  await withEnvironment(env(), async () => {
    calls += 1;
    throw new Error("storage must not be called");
  }, async () => {
    const response = await buzzReviewHandler(request(
      { action: "list", limit: 1 },
      "wrong-token-that-is-still-long-enough",
    ));
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { error: "buzz_review_auth_required" });
  });
  assert.equal(calls, 0);
});

test("list calls only the narrow target RPC", async () => {
  let capturedUrl = "";
  let capturedBody: unknown;
  await withEnvironment(env(), async (url, init) => {
    capturedUrl = String(url);
    capturedBody = JSON.parse(String(init?.body));
    return Response.json({
      schema_version: "2.0",
      mode: "publish_intent_review",
      workspace_id: WORKSPACE_ID,
      targets: [{
        job_id: JOB_ID,
        delivery_event_id: DELIVERY_EVENT_ID,
        channel_id: CHANNEL_ID,
        root_relay_event_id: ROOT_ID,
        message_sha256: MESSAGE_SHA256,
        protocol_version: PROTOCOL_VERSION,
        delivered_at_epoch: CREATED_AT - 100,
      }],
    });
  }, async () => {
    const response = await buzzReviewHandler(request({ action: "list", limit: 1 }));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).targets.length, 1);
  });
  assert.match(capturedUrl, /list_origintrail_buzz_review_targets$/);
  assert.deepEqual(capturedBody, {
    target_workspace_id: WORKSPACE_ID,
    target_limit: 1,
    target_protocol_start_epoch: PROTOCOL_START_EPOCH,
    target_protocol_version: PROTOCOL_VERSION,
  });
});

test("record validates canonical hash and calls only immutable decision RPC", async () => {
  let capturedUrl = "";
  let capturedBody: Record<string, unknown> = {};
  await withEnvironment(env(), async (url, init) => {
    capturedUrl = String(url);
    capturedBody = JSON.parse(String(init?.body));
    return Response.json({
      schema_version: "2.0",
      mode: "publish_intent_review",
      workspace_id: WORKSPACE_ID,
      job_id: JOB_ID,
      delivery_event_id: DELIVERY_EVENT_ID,
      channel_id: CHANNEL_ID,
      root_relay_event_id: ROOT_ID,
      message_sha256: MESSAGE_SHA256,
      protocol_version: PROTOCOL_VERSION,
      decision_event_id: DECISION_EVENT_ID,
      reviewer_pubkey: REVIEWER,
      decision: "approved",
      reason: null,
      command_sha256: hashRecord(),
      command_created_at_epoch: CREATED_AT,
      reused: false,
    });
  }, async () => {
    const response = await buzzReviewHandler(request(recordBody()));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).reused, false);
  });
  assert.match(capturedUrl, /record_origintrail_buzz_review_decision$/);
  assert.equal(capturedBody.target_workspace_id, WORKSPACE_ID);
  assert.equal(capturedBody.target_reason, null);
  assert.equal(capturedBody.target_message_sha256, MESSAGE_SHA256);
  assert.equal(capturedBody.target_protocol_start_epoch, PROTOCOL_START_EPOCH);
  assert.equal(capturedBody.target_command_sha256, hashRecord());
});

test("record retries a transport error once with the identical request", async () => {
  const attempts: string[] = [];
  await withEnvironment(env(), async (url, init) => {
    attempts.push(JSON.stringify({
      url: String(url),
      method: init?.method,
      headers: [...new Headers(init?.headers).entries()],
      body: String(init?.body),
    }));
    if (attempts.length === 1) throw new TypeError("commit status unknown");
    return Response.json(recordRpcResponse(true));
  }, async () => {
    const response = await buzzReviewHandler(request(recordBody()));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).reused, true);
  });
  assert.equal(attempts.length, 2);
  assert.equal(attempts[0], attempts[1]);
});

test("record retries 5xx exactly once", async () => {
  const requestBodies: string[] = [];
  await withEnvironment(env(), async (_url, init) => {
    requestBodies.push(String(init?.body));
    return new Response(null, { status: 503 });
  }, async () => {
    const response = await buzzReviewHandler(request(recordBody()));
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), {
      error: "buzz_review_storage_unavailable",
    });
  });
  assert.equal(requestBodies.length, 2);
  assert.equal(requestBodies[0], requestBodies[1]);
});

test("record does not retry 4xx", async () => {
  let calls = 0;
  await withEnvironment(env(), async () => {
    calls += 1;
    return new Response(null, { status: 409 });
  }, async () => {
    const response = await buzzReviewHandler(request(recordBody()));
    assert.equal(response.status, 409);
    assert.deepEqual(await response.json(), {
      error: "buzz_review_decision_conflict",
    });
  });
  assert.equal(calls, 1);
});

test("list does not use the record commit-unknown retry", async () => {
  let calls = 0;
  await withEnvironment(env(), async () => {
    calls += 1;
    return new Response(null, { status: 503 });
  }, async () => {
    const response = await buzzReviewHandler(request({ action: "list", limit: 1 }));
    assert.equal(response.status, 502);
  });
  assert.equal(calls, 1);
});

test("change request preserves one bounded Korean reason", async () => {
  const reason = "원문 수치를 다시 확인해주세요";
  const body = recordBody({
    decision: "changes_requested",
    reason,
    command_sha256: hashRecord("changes_requested", reason),
  });
  await withEnvironment(env(), async (_url, init) => {
    const posted = JSON.parse(String(init?.body));
    assert.equal(posted.target_decision, "changes_requested");
    assert.equal(posted.target_reason, reason);
    return Response.json({
      schema_version: "2.0",
      mode: "publish_intent_review",
      workspace_id: WORKSPACE_ID,
      job_id: JOB_ID,
      delivery_event_id: DELIVERY_EVENT_ID,
      channel_id: CHANNEL_ID,
      root_relay_event_id: ROOT_ID,
      message_sha256: MESSAGE_SHA256,
      protocol_version: PROTOCOL_VERSION,
      decision_event_id: DECISION_EVENT_ID,
      reviewer_pubkey: REVIEWER,
      decision: "changes_requested",
      reason,
      command_sha256: hashRecord("changes_requested", reason),
      command_created_at_epoch: CREATED_AT,
      reused: false,
    });
  }, async () => {
    assert.equal((await buzzReviewHandler(request(body))).status, 200);
  });
});

test("expanded, forged reviewer, or forged hash fails before storage", async () => {
  let calls = 0;
  const forbidden = async () => {
    calls += 1;
    throw new Error("storage must not be called");
  };
  await withEnvironment(env(), forbidden, async () => {
    assert.equal((await buzzReviewHandler(request(recordBody({ publish: true })))).status, 400);
    assert.equal((await buzzReviewHandler(request(recordBody({ reviewer_pubkey: "e".repeat(64) })))).status, 400);
    assert.equal((await buzzReviewHandler(request(recordBody({ command_sha256: "f".repeat(64) })))).status, 400);
  });
  assert.equal(calls, 0);
});

test("scoped review key is bearer while apikey stays the project key", async () => {
  const scoped = "scoped-buzz-review-role-jwt-value";
  await withEnvironment(env({ SUPABASE_BUZZ_REVIEW_KEY: scoped }), async (_url, init) => {
    const headers = new Headers(init?.headers);
    assert.equal(headers.get("authorization"), `Bearer ${scoped}`);
    assert.equal(headers.get("apikey"), "s".repeat(40));
    return Response.json({
      schema_version: "2.0", mode: "publish_intent_review",
      workspace_id: WORKSPACE_ID, targets: [],
    });
  }, async () => {
    assert.equal((await buzzReviewHandler(request({ action: "list", limit: 1 }))).status, 200);
  });
});

test("review adapter has no provider, publication, relay write, or subprocess path", () => {
  const source = [
    "../netlify/functions/_shared/buzz-review.mts",
    "../netlify/functions/buzz-review-origintrail.mts",
  ].map((path) => readFileSync(new URL(path, import.meta.url), "utf8")).join("\n");
  assert.doesNotMatch(
    source,
    /node:child_process|spawn\(|execFile|BUZZ_PRIVATE_KEY|OPENAI_API_KEY|TELEGRAM_BOT_TOKEN|messages send|publish\b/i,
  );
  assert.match(source, /list_origintrail_buzz_review_targets/);
  assert.match(source, /record_origintrail_buzz_review_decision/);
});
