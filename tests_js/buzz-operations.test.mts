import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import handler from "../netlify/functions/buzz-operations-origintrail.mts";


const TOKEN = "buzz-operations-worker-token-that-is-dedicated";
const WORKSPACE = "11111111-1111-4111-8111-111111111111";
const CHANNEL = "33333333-3333-4333-8333-333333333333";
const EVENT = "a".repeat(64);
const REVIEWER = "b".repeat(64);
const CREATED = 1_786_100_000;
const START = 1_786_000_000;
const MESSAGE = "현재 대기 중인 작업이 없습니다.\n자동 발행: OFF";
const MESSAGE_SHA = createHash("sha256").update(MESSAGE).digest("hex");

function env(overrides: Record<string, string | undefined> = {}) {
  const values: Record<string, string | undefined> = {
    BUZZ_OPERATIONS_WORKER_TOKEN: TOKEN,
    BUZZ_OPERATIONS_REVIEWER_PUBKEYS: REVIEWER,
    BUZZ_OPERATIONS_PROTOCOL_START_EPOCH: String(START),
    BUZZ_OPERATIONS_OUTBOX_ENABLED: "true",
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "s".repeat(40),
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE,
    ...overrides,
  };
  return (name: string) => values[name];
}

async function context(
  getEnv: (name: string) => string | undefined,
  fetcher: typeof fetch,
  run: () => Promise<void>,
): Promise<void> {
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  const originalFetch = globalThis.fetch;
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true, value: { env: { get: getEnv } },
  });
  globalThis.fetch = fetcher;
  try { await run(); } finally {
    globalThis.fetch = originalFetch;
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

function request(body: unknown, token = TOKEN): Request {
  return new Request("https://console.example/api/buzz-operations/origintrail", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-coineasy-buzz-operations-key": token,
    },
    body: JSON.stringify(body),
  });
}

function commandHash(command = "next_task", reply: string | null = null): string {
  return createHash("sha256").update([
    "coineasy-buzz-operations-command", "origintrail-buzz-operations@1",
    CHANNEL, EVENT, REVIEWER, command, String(CREATED), reply ?? "",
  ].join("\0"), "utf8").digest("hex");
}

function recordBody(extra: Record<string, unknown> = {}) {
  return {
    action: "record",
    channel_id: CHANNEL,
    command_event_id: EVENT,
    reviewer_pubkey: REVIEWER,
    protocol_version: "origintrail-buzz-operations@1",
    command: "next_task",
    command_sha256: commandHash(),
    command_created_at_epoch: CREATED,
    reply_to_event_id: null,
    ...extra,
  };
}

function responseObject(extra: Record<string, unknown> = {}) {
  return {
    workspace_id: WORKSPACE,
    command_event_id: EVENT,
    channel_id: CHANNEL,
    reply_to_event_id: EVENT,
    thread_root_event_id: EVENT,
    command: "next_task",
    task_id: null,
    message: MESSAGE,
    message_sha256: MESSAGE_SHA,
    status: "pending",
    claim_granted: false,
    reused: false,
    authorized_once: false,
    request_sha256: null,
    delivery_started_at_epoch: null,
    relay_event_id: null,
    ...extra,
  };
}

test("operations endpoint authenticates before storage", async () => {
  let calls = 0;
  await context(env(), async () => { calls += 1; throw new Error("no"); }, async () => {
    const response = await handler(request(recordBody(), "x".repeat(40)));
    assert.equal(response.status, 401);
  });
  assert.equal(calls, 0);
});

test("literal false outbox gate blocks every database call", async () => {
  let calls = 0;
  for (const disabled of [undefined, "", "false", "TRUE", "1"]) {
    await context(env({ BUZZ_OPERATIONS_OUTBOX_ENABLED: disabled }), async () => {
      calls += 1; throw new Error("no");
    }, async () => {
      const response = await handler(request(recordBody()));
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), { error: "buzz_operations_disabled" });
    });
  }
  assert.equal(calls, 0);
});

test("record verifies command hash and calls only bounded RPC", async () => {
  let url = "";
  let body: Record<string, unknown> = {};
  await context(env(), async (input, init) => {
    url = String(input);
    body = JSON.parse(String(init?.body));
    return Response.json(responseObject());
  }, async () => {
    const response = await handler(request(recordBody()));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).status, "pending");
  });
  assert.match(url, /record_origintrail_buzz_operations_command$/);
  assert.equal(body.target_workspace_id, WORKSPACE);
  assert.equal(body.target_command, "next_task");
  assert.equal(body.target_protocol_start_epoch, START);
});

test("scoped bearer keeps project API key in apikey header", async () => {
  let headers = new Headers();
  await context(env({ SUPABASE_BUZZ_OPERATIONS_KEY: "scoped-jwt-value" }), async (_url, init) => {
    headers = new Headers(init?.headers);
    return Response.json(responseObject());
  }, async () => {
    assert.equal((await handler(request(recordBody()))).status, 200);
  });
  assert.equal(headers.get("apikey"), "s".repeat(40));
  assert.equal(headers.get("authorization"), "Bearer scoped-jwt-value");
});

test("hold must bind an exact reply event", async () => {
  for (const invalid of [
    recordBody({ command: "hold", command_sha256: commandHash("hold") }),
    recordBody({ command: "status", reply_to_event_id: "c".repeat(64) }),
  ]) {
    await context(env(), async () => { throw new Error("no"); }, async () => {
      const response = await handler(request(invalid));
      assert.equal(response.status, 400);
    });
  }
});

test("claim validates a null response and unknown list validates envelopes", async () => {
  await context(env(), async () => Response.json(null), async () => {
    const response = await handler(request({
      action: "response_claim",
      command_event_id: null,
      worker_id: "origintrail-operations:staging",
      lease_seconds: 180,
    }));
    assert.equal(response.status, 200);
    assert.equal(await response.json(), null);
  });
  await context(env(), async () => Response.json({
    workspace_id: WORKSPACE,
    items: [responseObject({
      status: "delivery_unknown",
      request_sha256: "d".repeat(64),
      delivery_started_at_epoch: CREATED,
    })],
  }), async () => {
    const response = await handler(request({ action: "response_unknown", limit: 1 }));
    assert.equal(response.status, 200);
  });
});

test("invalid database response fails closed", async () => {
  await context(env(), async () => Response.json({ status: "pending" }), async () => {
    const response = await handler(request(recordBody()));
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { error: "buzz_operations_invalid_response" });
  });
});
