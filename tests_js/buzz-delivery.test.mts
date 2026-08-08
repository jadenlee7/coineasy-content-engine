import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import buzzDeliveryHandler from "../netlify/functions/buzz-delivery-origintrail.mts";

const TOKEN = "buzz-delivery-worker-token-that-is-dedicated";
const SHADOW_TOKEN = "buzz-shadow-reader-token-that-is-dedicated";
const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const JOB_ID = "22222222-2222-4222-8222-222222222222";
const CHANNEL_ID = "33333333-3333-4333-8333-333333333333";
const EVENT_ID = "a".repeat(64);
const MESSAGE_SHA = "b".repeat(64);
const REQUEST_SHA = "c".repeat(64);
const WORKER_ID = "origintrail-buzz:test-1234";

function env(overrides: Record<string, string | undefined> = {}) {
  const values: Record<string, string | undefined> = {
    BUZZ_DELIVERY_WORKER_TOKEN: TOKEN,
    BUZZ_SHADOW_ACCESS_TOKEN: SHADOW_TOKEN,
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

function claimBody(extra: Record<string, unknown> = {}) {
  return {
    action: "claim",
    event_id: EVENT_ID,
    job_id: JOB_ID,
    channel_id: CHANNEL_ID,
    message_sha256: MESSAGE_SHA,
    request_sha256: REQUEST_SHA,
    worker_id: WORKER_ID,
    lease_seconds: 180,
    ...extra,
  };
}

function request(body: object, token = TOKEN): Request {
  return new Request("https://console.example/api/buzz-delivery/origintrail", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-coineasy-buzz-delivery-key": token,
    },
    body: JSON.stringify(body),
  });
}

test("delivery endpoint authenticates before parsing or storage", async () => {
  let calls = 0;
  await withEnvironment(env(), async () => {
    calls += 1;
    throw new Error("storage must not be called");
  }, async () => {
    const response = await buzzDeliveryHandler(request(claimBody(), "wrong-token-that-is-still-long-enough"));
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { error: "buzz_delivery_auth_required" });
  });
  assert.equal(calls, 0);
});

test("delivery token must be configured and distinct from read/admin secrets", async () => {
  const forbidden = async () => { throw new Error("storage must not be called"); };
  await withEnvironment(env({ BUZZ_DELIVERY_WORKER_TOKEN: undefined }), forbidden, async () => {
    assert.equal((await buzzDeliveryHandler(request(claimBody()))).status, 503);
  });
  await withEnvironment(env({ BUZZ_DELIVERY_WORKER_TOKEN: SHADOW_TOKEN }), forbidden, async () => {
    assert.equal((await buzzDeliveryHandler(request(claimBody(), SHADOW_TOKEN))).status, 503);
  });
});

test("claim calls only the narrow RPC and returns its exact receipt", async () => {
  let capturedUrl = "";
  let capturedBody: unknown;
  await withEnvironment(env(), async (url, init) => {
    capturedUrl = String(url);
    capturedBody = JSON.parse(String(init?.body));
    assert.equal(new Headers(init?.headers).get("authorization"), `Bearer ${"s".repeat(40)}`);
    return Response.json({
      event_id: EVENT_ID,
      job_id: JOB_ID,
      channel_id: CHANNEL_ID,
      message_sha256: MESSAGE_SHA,
      request_sha256: REQUEST_SHA,
      status: "claimed",
      lease_expires_at: "2026-08-03T12:03:00Z",
      claim_granted: true,
      reused: false,
    });
  }, async () => {
    const response = await buzzDeliveryHandler(request(claimBody()));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).claim_granted, true);
  });
  assert.match(capturedUrl, /\/rest\/v1\/rpc\/claim_origintrail_buzz_delivery$/);
  assert.deepEqual(capturedBody, {
    target_workspace_id: WORKSPACE_ID,
    target_job_id: JOB_ID,
    target_event_id: EVENT_ID,
    target_channel_id: CHANNEL_ID,
    target_message_sha256: MESSAGE_SHA,
    target_request_sha256: REQUEST_SHA,
    target_worker_id: WORKER_ID,
    target_lease_seconds: 180,
  });
});

test("invalid or expanded requests fail before storage", async () => {
  let calls = 0;
  await withEnvironment(env(), async () => {
    calls += 1;
    return Response.json({});
  }, async () => {
    const response = await buzzDeliveryHandler(request(claimBody({ broadcast: true })));
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), { error: "invalid_buzz_delivery_request" });
  });
  assert.equal(calls, 0);
});

test("attempt response must freshly authorize exactly one send", async () => {
  await withEnvironment(env(), async (url) => {
    assert.match(String(url), /mark_origintrail_buzz_delivery_attempt$/);
    return Response.json({
      event_id: EVENT_ID,
      job_id: JOB_ID,
      channel_id: CHANNEL_ID,
      request_sha256: REQUEST_SHA,
      status: "attempt_started",
      authorized_once: true,
      reused: false,
    });
  }, async () => {
    const response = await buzzDeliveryHandler(request({
      action: "attempt",
      event_id: EVENT_ID,
      worker_id: WORKER_ID,
      request_sha256: REQUEST_SHA,
    }));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).authorized_once, true);
  });
});

test("adapter has no relay process, provider, publish, or deployment path", () => {
  const source = [
    "../netlify/functions/_shared/buzz-delivery.mts",
    "../netlify/functions/buzz-delivery-origintrail.mts",
  ].map((path) => readFileSync(new URL(path, import.meta.url), "utf8")).join("\n");
  assert.doesNotMatch(
    source,
    /node:child_process|spawn\(|execFile|BUZZ_PRIVATE_KEY|OPENAI_API_KEY|TELEGRAM_BOT_TOKEN|git push|messages send/i,
  );
  assert.match(source, /claim_origintrail_buzz_delivery/);
  assert.match(source, /mark_origintrail_buzz_delivery_attempt/);
});

test("scoped buzz delivery key is the bearer while the project API key stays unchanged", async () => {
  const SCOPED = "scoped-buzz-delivery-role-jwt-value";
  let bearer: string | null = null;
  await withEnvironment(
    env({ SUPABASE_BUZZ_DELIVERY_KEY: SCOPED }),
    async (url, init) => {
      bearer = new Headers(init?.headers).get("authorization");
      assert.equal(new Headers(init?.headers).get("apikey"), "s".repeat(40));
      return Response.json({
        event_id: EVENT_ID,
        job_id: JOB_ID,
        channel_id: CHANNEL_ID,
        message_sha256: MESSAGE_SHA,
        request_sha256: REQUEST_SHA,
        status: "claimed",
        lease_expires_at: "2026-08-03T12:03:00Z",
        claim_granted: true,
        reused: false,
      });
    },
    async () => {
      const response = await buzzDeliveryHandler(request(claimBody()));
      assert.equal(response.status, 200);
    },
  );
  assert.equal(bearer, `Bearer ${SCOPED}`);
});

test("reconcile accepts only its own workspace echo", async () => {
  const counts = {
    reconciled_count: 1,
    pending_count: 0,
    failed_count: 0,
    delivery_unknown_count: 1,
  };
  await withEnvironment(env(), async () =>
    Response.json({ workspace_id: WORKSPACE_ID, ...counts }),
  async () => {
    const response = await buzzDeliveryHandler(request({ action: "reconcile", limit: 25 }));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { workspace_id: WORKSPACE_ID, ...counts });
  });
  await withEnvironment(env(), async () =>
    Response.json({ workspace_id: JOB_ID, ...counts }),
  async () => {
    const response = await buzzDeliveryHandler(request({ action: "reconcile", limit: 25 }));
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { error: "buzz_delivery_invalid_response" });
  });
});

test("delivery token equal to a scoped supabase key is not configured", async () => {
  const forbidden = async () => { throw new Error("storage must not be called"); };
  await withEnvironment(
    env({ SUPABASE_BUZZ_DELIVERY_KEY: TOKEN }),
    forbidden,
    async () => {
      assert.equal((await buzzDeliveryHandler(request(claimBody()))).status, 503);
    },
  );
});
