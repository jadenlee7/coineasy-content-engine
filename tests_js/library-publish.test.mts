import assert from "node:assert/strict";
import test from "node:test";

import publicationHandler from "../netlify/functions/library-publish.mts";
import publicationResolutionHandler from "../netlify/functions/library-publish-resolution.mts";
import {
  cancelStudioTelegramDeliveryUnknown,
  getStudioTelegramPublication,
  kickTelegramPublicationWorker,
  publicationWorkerConfig,
  requestStudioTelegramPublication,
  studioTelegramPublishAllowedClients,
  studioTelegramPublishClientAllowed,
  studioTelegramPublishEnabled,
} from "../netlify/functions/_shared/content-publications.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const ACCESS_TOKEN = "test-studio-access-token-32-bytes";
const AUTOMATION_TOKEN = "test-studio-automation-token-40-bytes-long";
const WORKER_TOKEN = "test-publication-worker-token-40-bytes-long";
const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const ITEM_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";
const PUBLICATION_ID = "44444444-4444-4444-8444-444444444444";
const JOB_ID = "55555555-5555-4555-8555-555555555555";
const IDEMPOTENCY_KEY = "66666666-6666-4666-8666-666666666666";
const CREATED_AT = "2026-08-01T12:00:00.000Z";
const DELIVERY_STARTED_AT = "2026-08-01T12:34:56.123456+00:00";
const NORMALIZED_DELIVERY_STARTED_AT = "2026-08-01T12:34:56.123Z";

function catalogConfig() {
  return {
    supabaseUrl: "https://project.supabase.co",
    serviceRoleKey: "server-only-service-role",
    workspaceId: WORKSPACE_ID,
  };
}

function publication(status = "queued", reused = false) {
  return {
    publication_id: PUBLICATION_ID,
    job_id: JOB_ID,
    content_item_id: ITEM_ID,
    content_version_id: VERSION_ID,
    channel: "telegram",
    status,
    delivery_started_at: ["publishing", "published", "delivery_unknown"].includes(status)
      ? DELIVERY_STARTED_AT
      : null,
    external_url: status === "published" ? "https://t.me/squid_kor_update/123" : null,
    last_error_code: status === "delivery_unknown" ? "telegram_delivery_unknown" : null,
    reused,
  };
}

function detailRpcResult(clientId = "squid") {
  return {
    content_item_id: ITEM_ID,
    content_version_id: VERSION_ID,
    client_id: clientId,
    content_kind: "daily_news",
    title: `${clientId} daily news`,
    status: "approved",
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    version_number: 1,
    prompt_version: "content-studio@1",
    locale: "ko-KR",
    content: {},
    channel_copy: { telegram: "승인된 Telegram 문구" },
    deliverables: {},
    qa: {},
    generation_meta: { mock_mode: false },
    assets: [],
    figma_links: [],
  };
}

async function withEnvironment(
  values: Record<string, string | undefined>,
  fetcher: typeof fetch,
  run: () => Promise<void>,
): Promise<void> {
  const originalFetch = globalThis.fetch;
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  globalThis.fetch = fetcher;
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: { env: { get: (name: string) => values[name] } },
  });
  try {
    await run();
  } finally {
    globalThis.fetch = originalFetch;
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

function sessionHeaders(): Record<string, string> {
  return {
    cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue(ACCESS_TOKEN)}`,
  };
}

function postRequest(
  body: Record<string, unknown> = {
    content_version_id: VERSION_ID,
    channel: "telegram",
  },
  headers: Record<string, string> = sessionHeaders(),
): Request {
  return new Request(`https://console.example/api/library/${ITEM_ID}/publish`, {
    method: "POST",
    headers: {
      ...headers,
      "Content-Type": "application/json",
      "Idempotency-Key": IDEMPOTENCY_KEY,
    },
    body: JSON.stringify(body),
  });
}

function resolutionRequest(
  body: Record<string, unknown> = {
    content_version_id: VERSION_ID,
    publication_id: PUBLICATION_ID,
    delivery_started_at: NORMALIZED_DELIVERY_STARTED_AT,
    resolution: "confirmed_not_observed_cancelled",
    public_channel: "squid_kor_update",
    channel_checked: true,
    caption_checked: true,
    png_checked: true,
  },
  headers: Record<string, string> = sessionHeaders(),
): Request {
  return new Request(`https://console.example/api/library/${ITEM_ID}/publish-resolution`, {
    method: "POST",
    headers: {
      ...headers,
      "Content-Type": "application/json",
      "Idempotency-Key": IDEMPOTENCY_KEY,
    },
    body: JSON.stringify(body),
  });
}

test("exact feature flag is closed unless its value is exactly true", () => {
  for (const value of [undefined, "", "TRUE", "1", " true ", "false"]) {
    assert.equal(studioTelegramPublishEnabled(() => value), false);
  }
  assert.equal(studioTelegramPublishEnabled(() => "true"), true);
});

test("first-slice client allowlist accepts only the exact Squid value", () => {
  const allowed = studioTelegramPublishAllowedClients((name) => (
    name === "STUDIO_TELEGRAM_PUBLISH_ALLOWED_CLIENTS" ? "squid" : undefined
  ));
  assert.deepEqual([...allowed], ["squid"]);
  assert.equal(studioTelegramPublishClientAllowed("squid", () => "squid"), true);
  for (const value of [
    undefined,
    "",
    "Squid",
    " squid",
    "squid ",
    "yellow",
    "origintrail",
    "babylon",
    "squid,yellow",
    "squid, yellow",
    "squid,",
    "squid,squid",
    "all",
  ]) {
    assert.deepEqual(
      [...studioTelegramPublishAllowedClients(() => value)],
      [],
      `expected ${String(value)} to fail closed`,
    );
  }
});

test("publication helpers bind the service-only RPC to one immutable Telegram version", async () => {
  let requestBody: Record<string, unknown> = {};
  const requested = await requestStudioTelegramPublication(
    catalogConfig(),
    ITEM_ID,
    VERSION_ID,
    IDEMPOTENCY_KEY,
    async (input, init) => {
      const request = new Request(input, init);
      assert.equal(request.url, "https://project.supabase.co/rest/v1/rpc/request_studio_telegram_publication");
      assert.equal(request.headers.get("authorization"), "Bearer server-only-service-role");
      requestBody = JSON.parse(String(init?.body));
      return Response.json(publication());
    },
  );
  assert.deepEqual(requestBody, {
    target_workspace_id: WORKSPACE_ID,
    target_content_item_id: ITEM_ID,
    target_content_version_id: VERSION_ID,
    request_idempotency_key: IDEMPOTENCY_KEY,
  });
  assert.equal(requested.status, "queued");
  assert.equal(requested.reused, false);
  assert.doesNotMatch(JSON.stringify(requested), /job_id|service-role/);

  const current = await getStudioTelegramPublication(
    catalogConfig(),
    ITEM_ID,
    VERSION_ID,
    async (input, init) => {
      assert.equal(
        String(input),
        "https://project.supabase.co/rest/v1/rpc/get_studio_telegram_publication",
      );
      assert.deepEqual(JSON.parse(String(init?.body)), {
        target_workspace_id: WORKSPACE_ID,
        target_content_item_id: ITEM_ID,
        target_content_version_id: VERSION_ID,
      });
      return Response.json(publication("delivery_unknown", true));
    },
  );
  assert.equal(current?.status, "delivery_unknown");
  assert.equal(current?.error_code, "telegram_delivery_unknown");
  assert.equal(current?.delivery_started_at, NORMALIZED_DELIVERY_STARTED_AT);
});

test("delivery-unknown resolution binds all three operator checks and never calls a worker", async () => {
  let requestBody: Record<string, unknown> = {};
  const resolved = await cancelStudioTelegramDeliveryUnknown(
    catalogConfig(),
    {
      contentItemId: ITEM_ID,
      contentVersionId: VERSION_ID,
      publicationId: PUBLICATION_ID,
      deliveryStartedAt: NORMALIZED_DELIVERY_STARTED_AT,
      publicChannel: "squid_kor_update",
      idempotencyKey: IDEMPOTENCY_KEY,
    },
    async (input, init) => {
      assert.equal(
        String(input),
        "https://project.supabase.co/rest/v1/rpc/cancel_unobserved_exact_telegram_publication",
      );
      requestBody = JSON.parse(String(init?.body));
      return Response.json(publication("cancelled", false));
    },
  );
  assert.deepEqual(requestBody, {
    target_workspace_id: WORKSPACE_ID,
    target_content_item_id: ITEM_ID,
    target_content_version_id: VERSION_ID,
    target_publication_id: PUBLICATION_ID,
    target_delivery_started_at: NORMALIZED_DELIVERY_STARTED_AT,
    target_public_channel: "squid_kor_update",
    target_channel_checked: true,
    target_caption_checked: true,
    target_png_checked: true,
    request_idempotency_key: IDEMPOTENCY_KEY,
  });
  assert.equal(resolved.status, "cancelled");
  assert.equal(resolved.external_url, null);
});

test("publication parser requires a canonical URL only for published state", async () => {
  for (const polluted of [
    { ...publication("published", true), external_url: null },
    { ...publication("queued", true), external_url: "https://t.me/squid_kor_update/123" },
    { ...publication("delivery_unknown", true), delivery_started_at: "not-an-iso-timestamp" },
    { ...publication("delivery_unknown", true), delivery_started_at: "2026-02-30T12:00:00Z" },
  ]) {
    await assert.rejects(
      () => getStudioTelegramPublication(
        catalogConfig(),
        ITEM_ID,
        VERSION_ID,
        async () => Response.json(polluted),
      ),
      (error: unknown) => (
        error instanceof Error
        && (error as Error & { code?: string }).code === "telegram_publication_invalid_response"
      ),
    );
  }
});

test("worker kick uses the dedicated secret, empty body, and validated Railway origin", async () => {
  const config = publicationWorkerConfig((name) => ({
    RAILWAY_API_URL: "https://content.example/",
    PUBLICATION_WORKER_TOKEN: WORKER_TOKEN,
  })[name]);
  assert.ok(config);
  const kicked = await kickTelegramPublicationWorker(config!, async (input, init) => {
    const request = new Request(input, init);
    assert.equal(request.url, "https://content.example/internal/publications/telegram/run-once");
    assert.equal(request.method, "POST");
    assert.equal(request.headers.get("x-publication-worker-key"), WORKER_TOKEN);
    assert.equal(request.headers.get("content-type"), null);
    assert.equal(init?.body, undefined);
    return Response.json({ ok: true, accepted: true, status: "scheduled" }, { status: 202 });
  });
  assert.equal(kicked, true);
  assert.equal(publicationWorkerConfig((name) => ({
    RAILWAY_API_URL: "http://content.example",
    PUBLICATION_WORKER_TOKEN: WORKER_TOKEN,
  })[name]), null);
  assert.equal(publicationWorkerConfig((name) => ({
    RAILWAY_API_URL: "https://content.example",
    PUBLICATION_WORKER_TOKEN: ` ${WORKER_TOKEN}`,
  })[name]), null);
  assert.equal(publicationWorkerConfig((name) => ({
    RAILWAY_API_URL: "https://content.example",
    PUBLICATION_WORKER_TOKEN: `${WORKER_TOKEN.slice(0, -1)}한`,
  })[name]), null);
  for (const sharedSecretName of [
    "API_SECRET",
    "STUDIO_ACCESS_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
    "TELEGRAM_BOT_TOKEN_SQUID",
    "TELEGRAM_BOT_TOKEN_YELLOW",
    "TELEGRAM_BOT_TOKEN_ORIGINTRAIL",
    "TELEGRAM_BOT_TOKEN_BABYLON",
  ]) {
    assert.equal(publicationWorkerConfig((name) => ({
      RAILWAY_API_URL: "https://content.example",
      PUBLICATION_WORKER_TOKEN: WORKER_TOKEN,
      [sharedSecretName]: WORKER_TOKEN,
    })[name]), null);
  }
});

test("publish route authenticates before RPC and never grants the automation key", async () => {
  let calls = 0;
  await withEnvironment({
    STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
    STUDIO_AUTOMATION_TOKEN: AUTOMATION_TOKEN,
    STUDIO_TELEGRAM_PUBLISH_ENABLED: "true",
  }, async () => {
    calls += 1;
    throw new Error("upstream must not be called");
  }, async () => {
    const unauthorized = await publicationHandler(postRequest(undefined, {}), {
      params: { contentId: ITEM_ID },
    } as never);
    assert.equal(unauthorized.status, 401);

    const automationOnly = await publicationHandler(postRequest(undefined, {
      "x-studio-automation-key": AUTOMATION_TOKEN,
    }), { params: { contentId: ITEM_ID } } as never);
    assert.equal(automationOnly.status, 401);
    assert.equal(calls, 0);
  });
});

test("POST feature rollback is 503 while GET continues to expose durable state", async () => {
  let calls = 0;
  await withEnvironment({
    STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
    STUDIO_TELEGRAM_PUBLISH_ENABLED: "false",
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  }, async (input) => {
    calls += 1;
    assert.match(String(input), /rpc\/get_studio_telegram_publication$/);
    return Response.json(publication("published", true));
  }, async () => {
    const disabled = await publicationHandler(postRequest(), {
      params: { contentId: ITEM_ID },
    } as never);
    assert.equal(disabled.status, 503);
    assert.deepEqual(await disabled.json(), { error: "telegram_publication_not_enabled" });
    assert.equal(calls, 0);

    const duplicateQuery = await publicationHandler(new Request(
      `https://console.example/api/library/${ITEM_ID}/publish?content_version_id=${VERSION_ID}&channel=telegram&channel=telegram`,
      { headers: sessionHeaders() },
    ), { params: { contentId: ITEM_ID } } as never);
    assert.equal(duplicateQuery.status, 400);
    assert.equal(calls, 0);

    const params = new URLSearchParams({ content_version_id: VERSION_ID, channel: "telegram" });
    const status = await publicationHandler(new Request(
      `https://console.example/api/library/${ITEM_ID}/publish?${params}`,
      { headers: sessionHeaders() },
    ), { params: { contentId: ITEM_ID } } as never);
    assert.equal(status.status, 200);
    assert.equal((await status.json()).status, "published");
    assert.equal(calls, 1);
  });
});

test("new queue dispatches a best-effort background worker and refreshes durable state", async () => {
  const calls: string[] = [];
  await withEnvironment({
    STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
    STUDIO_TELEGRAM_PUBLISH_ENABLED: "true",
    STUDIO_TELEGRAM_PUBLISH_ALLOWED_CLIENTS: "squid",
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    RAILWAY_API_URL: "https://content.example",
    PUBLICATION_WORKER_TOKEN: WORKER_TOKEN,
  }, async (input, init) => {
    const request = new Request(input, init);
    calls.push(request.url);
    if (request.url.endsWith("/rpc/get_content_library_item")) {
      return Response.json(detailRpcResult());
    }
    if (request.url.endsWith("/rpc/request_studio_telegram_publication")) {
      return Response.json(publication("queued", false));
    }
    if (request.url.endsWith("/internal/publications/telegram/run-once")) {
      assert.equal(request.headers.get("x-publication-worker-key"), WORKER_TOKEN);
      assert.equal(init?.body, undefined);
      return Response.json({
        ok: true,
        accepted: true,
        status: "scheduled",
      }, { status: 202 });
    }
    if (request.url.endsWith("/rpc/get_studio_telegram_publication")) {
      return Response.json(publication("published", true));
    }
    throw new Error(`unexpected request ${request.url}`);
  }, async () => {
    const response = await publicationHandler(postRequest(), {
      params: { contentId: ITEM_ID },
    } as never);
    assert.equal(response.status, 200);
    const payload = await response.json() as Record<string, unknown>;
    assert.equal(payload.status, "published");
    assert.equal(payload.delivery_started_at, NORMALIZED_DELIVERY_STARTED_AT);
    assert.equal(payload.external_url, "https://t.me/squid_kor_update/123");
    assert.deepEqual(calls.map((url) => new URL(url).pathname), [
      "/rest/v1/rpc/get_content_library_item",
      "/rest/v1/rpc/request_studio_telegram_publication",
      "/internal/publications/telegram/run-once",
      "/rest/v1/rpc/get_studio_telegram_publication",
    ]);
  });
});

test("kick failure preserves the durable queue and strict body validation never queues", async () => {
  let rpcCalls = 0;
  await withEnvironment({
    STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
    STUDIO_TELEGRAM_PUBLISH_ENABLED: "true",
    STUDIO_TELEGRAM_PUBLISH_ALLOWED_CLIENTS: "squid",
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    RAILWAY_API_URL: "https://content.example",
    PUBLICATION_WORKER_TOKEN: WORKER_TOKEN,
  }, async (input) => {
    const url = String(input);
    if (url.endsWith("/rpc/get_content_library_item")) {
      return Response.json(detailRpcResult());
    }
    if (url.endsWith("/rpc/request_studio_telegram_publication")) {
      rpcCalls += 1;
      return Response.json(publication("queued", false));
    }
    if (url.endsWith("/internal/publications/telegram/run-once")) {
      return Response.json({ error: "worker_unavailable" }, { status: 503 });
    }
    if (url.endsWith("/rpc/get_studio_telegram_publication")) {
      return Response.json(publication("queued", true));
    }
    throw new Error(`unexpected request ${url}`);
  }, async () => {
    const invalid = await publicationHandler(postRequest({
      content_version_id: VERSION_ID,
      channel: "telegram",
      telegram_copy: "browser supplied copy must be rejected",
    }), { params: { contentId: ITEM_ID } } as never);
    assert.equal(invalid.status, 400);
    assert.equal(rpcCalls, 0);

    const queued = await publicationHandler(postRequest(), {
      params: { contentId: ITEM_ID },
    } as never);
    assert.equal(queued.status, 202);
    assert.equal((await queued.json()).status, "queued");
    assert.equal(rpcCalls, 1);
  });
});

test("resolution route requires a signed session and disabled publication flag", async () => {
  let calls = 0;
  await withEnvironment({
    STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
    STUDIO_TELEGRAM_PUBLISH_ENABLED: "true",
  }, async () => {
    calls += 1;
    throw new Error("resolution must fail before storage");
  }, async () => {
    const unauthorized = await publicationResolutionHandler(
      resolutionRequest(undefined, {}),
      { params: { contentId: ITEM_ID } } as never,
    );
    assert.equal(unauthorized.status, 401);

    const enabled = await publicationResolutionHandler(
      resolutionRequest(),
      { params: { contentId: ITEM_ID } } as never,
    );
    assert.equal(enabled.status, 409);
    assert.deepEqual(await enabled.json(), {
      error: "telegram_resolution_requires_publication_disabled",
    });
    assert.equal(calls, 0);
  });
});

test("resolution route cancels only the exact attested unknown delivery", async () => {
  let calls = 0;
  await withEnvironment({
    STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
    STUDIO_TELEGRAM_PUBLISH_ENABLED: "false",
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  }, async (input, init) => {
    calls += 1;
    assert.match(String(input), /rpc\/cancel_unobserved_exact_telegram_publication$/);
    const body = JSON.parse(String(init?.body));
    assert.equal(body.target_channel_checked, true);
    assert.equal(body.target_caption_checked, true);
    assert.equal(body.target_png_checked, true);
    return Response.json(publication("cancelled", false));
  }, async () => {
    const invalid = await publicationResolutionHandler(
      resolutionRequest({
        content_version_id: VERSION_ID,
        publication_id: PUBLICATION_ID,
        delivery_started_at: NORMALIZED_DELIVERY_STARTED_AT,
        resolution: "confirmed_not_observed_cancelled",
        public_channel: "squid_kor_update",
        channel_checked: true,
        caption_checked: false,
        png_checked: true,
      }),
      { params: { contentId: ITEM_ID } } as never,
    );
    assert.equal(invalid.status, 400);
    assert.equal(calls, 0);

    const resolved = await publicationResolutionHandler(
      resolutionRequest(),
      { params: { contentId: ITEM_ID } } as never,
    );
    assert.equal(resolved.status, 201);
    assert.equal((await resolved.json()).status, "cancelled");
    assert.equal(calls, 1);
  });
});

test("POST rejects a non-allowlisted Yellow item before creating a publication", async () => {
  const calls: string[] = [];
  await withEnvironment({
    STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
    STUDIO_TELEGRAM_PUBLISH_ENABLED: "true",
    STUDIO_TELEGRAM_PUBLISH_ALLOWED_CLIENTS: "yellow",
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  }, async (input) => {
    const url = String(input);
    calls.push(url);
    if (url.endsWith("/rpc/get_content_library_item")) {
      return Response.json(detailRpcResult("yellow"));
    }
    throw new Error(`publication must not be queued for Yellow: ${url}`);
  }, async () => {
    const response = await publicationHandler(postRequest(), {
      params: { contentId: ITEM_ID },
    } as never);
    assert.equal(response.status, 403);
    assert.deepEqual(await response.json(), {
      error: "telegram_publication_client_not_allowed",
    });
    assert.deepEqual(calls.map((url) => new URL(url).pathname), [
      "/rest/v1/rpc/get_content_library_item",
    ]);
  });
});

test("POST maps exact migration state failures without making them look retryable", async () => {
  let failureMessage = "";
  await withEnvironment({
    STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
    STUDIO_TELEGRAM_PUBLISH_ENABLED: "true",
    STUDIO_TELEGRAM_PUBLISH_ALLOWED_CLIENTS: "squid",
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  }, async (input) => {
    const url = String(input);
    if (url.endsWith("/rpc/get_content_library_item")) {
      return Response.json(detailRpcResult());
    }
    if (url.endsWith("/rpc/request_studio_telegram_publication")) {
      return Response.json({ message: failureMessage }, { status: 400 });
    }
    throw new Error(`unexpected request ${url}`);
  }, async () => {
    for (const [message, status, code] of [
      ["exact Telegram publication requires a production version", 422, "mock_content_cannot_be_published"],
      ["only exact current approved Squid daily news can be published", 409, "telegram_publication_not_approved"],
      ["exact Telegram publication requires an approval record", 409, "telegram_publication_not_approved"],
      ["double-fact-check approval is required", 409, "telegram_publication_not_approved"],
      ["exact Telegram publication client is not active", 403, "telegram_publication_client_not_allowed"],
      ["exact Telegram caption must be 1 to 1024 characters", 400, "telegram_publication_payload_incomplete"],
    ] as const) {
      failureMessage = message;
      const response = await publicationHandler(postRequest(), {
        params: { contentId: ITEM_ID },
      } as never);
      assert.equal(response.status, status);
      assert.deepEqual(await response.json(), { error: code });
    }
  });
});
