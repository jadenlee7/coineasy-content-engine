import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import tutorialHandler, {
  generatedTutorialSlide,
  tutorialRequestHash,
} from "../netlify/functions/tutorial.mts";
import tutorialSlideHandler, {
  safeTutorialSlidePath,
} from "../netlify/functions/tutorial-slide.mts";
import {
  signTutorialSlide,
  verifyTutorialSlide,
} from "../netlify/functions/_shared/tutorial-slide-token.mts";
import {
  createTutorialSlideSignedUrl,
  findTutorialGeneration,
  pngDimensions,
  recordTutorialGeneration,
  removeTutorialSlides,
  tutorialStorageConfig,
  tutorialStoragePath,
  uploadTutorialSlide,
  verifyPrivateTutorialBucket,
  verifyTutorialStorageScope,
} from "../netlify/functions/_shared/tutorial-storage.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const OBJECT_ID = "22222222-2222-4222-8222-222222222222";
const CATALOG_ID = "33333333-3333-4333-8333-333333333333";
const VERSION_ID = "44444444-4444-4444-8444-444444444444";
const SECOND_OBJECT_ID = "55555555-5555-4555-8555-555555555555";
const ACCESS_TOKEN = "test-studio-access-token-32-bytes";
const REQUEST_HASH = "f".repeat(64);
const TUTORIAL_HANDLER_SOURCE = readFileSync(
  new URL("../netlify/functions/tutorial.mts", import.meta.url),
  "utf8",
);

function minimalPng(width: number, height: number, marker = 0): Uint8Array {
  const bytes = new Uint8Array(25);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  bytes.set([0x49, 0x48, 0x44, 0x52], 12);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width, false);
  view.setUint32(20, height, false);
  bytes[24] = marker;
  return bytes;
}

function tutorialLessons(count: number): Record<string, unknown>[] {
  return Array.from({ length: count }, (_, index) => ({
    lesson_number: index + 1,
    layout: "P2_BULLETS",
    theme: "dark",
    slots: {
      lesson_title_kr: `검증 레슨 ${index + 1}`,
      lesson_body_kr: `원문에 근거한 검증 본문 ${index + 1}`,
      bullets: [{ text_kr: "근거 확인" }],
    },
  }));
}

function validTutorialFactCheck(): Record<string, unknown> {
  return {
    schema_version: "1.0",
    policy_version: "double-fact-check@1",
    content_kind: "tutorial",
    status: "review",
    human_review_required: true,
    input_sha256: "a".repeat(64),
    output_sha256: "b".repeat(64),
    checks: [
      { id: "source_evidence", status: "review", label: "Source evidence", detail: "Human verification required.", metrics: {} },
      { id: "output_claims", status: "pass", label: "Output claims", detail: "Mechanical anchors recorded.", metrics: {} },
    ],
  };
}

async function withNetlifyEnvironment(
  values: Record<string, string | undefined>,
  run: () => Promise<void>,
): Promise<void> {
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: { env: { get: (name: string) => values[name] } },
  });
  try {
    await run();
  } finally {
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

test("accepts only generated tutorial lesson paths for the selected client", () => {
  assert.deepEqual(
    generatedTutorialSlide(
      "/tmp/content_engine_output/yellow/edu_2087200000/lesson_01.png",
      "yellow",
    ),
    { runId: "edu_2087200000", fileName: "lesson_01.png" },
  );
  assert.equal(
    generatedTutorialSlide(
      "/tmp/content_engine_output/squid/edu_2087200000/lesson_01.png",
      "yellow",
    ),
    null,
  );
  assert.equal(generatedTutorialSlide("yellow/edu_1/../manifest.json", "yellow"), null);
});

test("tutorial request hash binds every accepted input before URL-only import", () => {
  const request = {
    clientId: "squid",
    sourceContent: "",
    sourceType: "tweet",
    sourceUrl: "https://x.com/squidrouter/status/123",
    seriesNumber: "14",
    mockMode: false,
  };
  const hash = tutorialRequestHash(request);
  assert.match(hash, /^[a-f0-9]{64}$/);
  assert.equal(tutorialRequestHash({ ...request }), hash);
  assert.notEqual(
    tutorialRequestHash({ ...request, sourceUrl: "https://x.com/squidrouter/status/124" }),
    hash,
  );
  assert.notEqual(tutorialRequestHash({ ...request, mockMode: true }), hash);
  assert.notEqual(tutorialRequestHash({ ...request, clientId: "yellow" }), hash);
});

test("tutorial slide proxy accepts only workspace-scoped durable object paths", () => {
  assert.equal(
    safeTutorialSlidePath(WORKSPACE_ID, "squid", OBJECT_ID, "lesson_08.png"),
    `${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_08.png`,
  );
  assert.equal(safeTutorialSlidePath(WORKSPACE_ID, "babylon", OBJECT_ID, "lesson_01.png"), null);
  assert.equal(safeTutorialSlidePath(WORKSPACE_ID, "yellow", "../edu_1", "lesson_01.png"), null);
  assert.equal(safeTutorialSlidePath(WORKSPACE_ID, "yellow", OBJECT_ID, "source.png"), null);
});

test("tutorial slide URLs require an unexpired server signature", () => {
  const expires = 4_600;
  const token = signTutorialSlide("secret", "yellow", OBJECT_ID, "lesson_01.png", expires);
  assert.equal(
    verifyTutorialSlide("secret", "yellow", OBJECT_ID, "lesson_01.png", expires, token, 1_000),
    true,
  );
  assert.equal(
    verifyTutorialSlide("secret", "yellow", OBJECT_ID, "lesson_02.png", expires, token, 1_000),
    false,
  );
  assert.equal(
    verifyTutorialSlide("secret", "yellow", OBJECT_ID, "lesson_01.png", expires, token, 4_600),
    false,
  );
});

test("durable tutorial storage config is complete, scoped, and HTTPS-only", () => {
  const config = tutorialStorageConfig((name) => ({
    SUPABASE_URL: "https://project.supabase.co/",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  })[name]);
  assert.deepEqual(config, {
    supabaseUrl: "https://project.supabase.co",
    serviceRoleKey: "server-only-service-key",
    workspaceId: WORKSPACE_ID,
  });
  assert.equal(tutorialStorageConfig(() => undefined), null);
  assert.equal(tutorialStorageConfig((name) => ({
    SUPABASE_URL: "http://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  })[name]), null);
  assert.equal(
    tutorialStoragePath(WORKSPACE_ID, "squid", OBJECT_ID, "lesson_01.png"),
    `${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png`,
  );
});

test("PNG metadata validation reads bounded IHDR dimensions", () => {
  assert.deepEqual(pngDimensions(minimalPng(1080, 1350).buffer), {
    width: 1080,
    height: 1350,
  });
  assert.equal(pngDimensions(minimalPng(0, 1350).buffer), null);
  assert.equal(pngDimensions(Uint8Array.from([0x89, 0x50]).buffer), null);
});

test("tutorial catalog RPC is scoped, idempotent, and maps durable IDs", async () => {
  const config = tutorialStorageConfig((name) => ({
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  })[name])!;
  const catalogSlide = {
    number: 1,
    filename: "lesson_01.png",
    storage_path: `${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png`,
    byte_size: 25,
    sha256: "a".repeat(64),
    width: 1080,
    height: 1350,
  };
  let requestBody: Record<string, unknown> = {};
  const result = await recordTutorialGeneration(config, {
    requestId: CATALOG_ID,
    clientId: "squid",
    title: "  한국어 튜토리얼  ",
    content: { request_hash: REQUEST_HASH, lesson_count: 1 },
    generationMeta: { request_hash: REQUEST_HASH, renderer: "railway" },
    slides: [catalogSlide],
  }, async (input, init) => {
    const request = new Request(input, init);
    assert.equal(
      request.url,
      "https://project.supabase.co/rest/v1/rpc/record_tutorial_generation",
    );
    assert.equal(request.method, "POST");
    assert.equal(request.headers.get("authorization"), "Bearer server-only-service-key");
    requestBody = JSON.parse(String(init?.body));
    return Response.json({
      content_item_id: CATALOG_ID,
      content_version_id: VERSION_ID,
      asset_ids: [OBJECT_ID],
    });
  });
  assert.deepEqual(requestBody, {
    target_content_item_id: CATALOG_ID,
    target_workspace_id: WORKSPACE_ID,
    target_client_id: "squid",
    target_title: "한국어 튜토리얼",
    target_content: { request_hash: REQUEST_HASH, lesson_count: 1 },
    target_generation_meta: { request_hash: REQUEST_HASH, renderer: "railway" },
    target_slides: [catalogSlide],
    target_prompt_version: "edu-carousel@1",
  });
  assert.deepEqual(result, {
    contentItemId: CATALOG_ID,
    contentVersionId: VERSION_ID,
    assetIds: [OBJECT_ID],
  });
});

test("tutorial catalog lookup returns the original durable generation for retries", async () => {
  const config = tutorialStorageConfig((name) => ({
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  })[name])!;
  let body: Record<string, unknown> = {};
  const found = await findTutorialGeneration(
    config,
    CATALOG_ID,
    "squid",
    async (input, init) => {
      assert.equal(
        String(input),
        "https://project.supabase.co/rest/v1/rpc/get_tutorial_generation",
      );
      body = JSON.parse(String(init?.body));
      return Response.json({
        content_item_id: CATALOG_ID,
        content_version_id: VERSION_ID,
        title: "기존 튜토리얼",
        content: { series: { series_subtitle_kr: "기존 튜토리얼" } },
        generation_meta: { duration_ms: 123 },
        slides: [{
          asset_id: OBJECT_ID,
          number: 1,
          filename: "lesson_01.png",
          storage_path: `${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png`,
          byte_size: 25,
          sha256: "a".repeat(64),
          width: 1080,
          height: 1350,
        }],
      });
    },
  );
  assert.deepEqual(body, {
    target_content_item_id: CATALOG_ID,
    target_workspace_id: WORKSPACE_ID,
    target_client_id: "squid",
  });
  assert.deepEqual(found, {
    contentItemId: CATALOG_ID,
    contentVersionId: VERSION_ID,
    title: "기존 튜토리얼",
    content: { series: { series_subtitle_kr: "기존 튜토리얼" } },
    generationMeta: { duration_ms: 123 },
    slides: [{
      assetId: OBJECT_ID,
      number: 1,
      filename: "lesson_01.png",
      storagePath: `${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png`,
    }],
  });
  assert.equal(
    await findTutorialGeneration(config, CATALOG_ID, "squid", async () => Response.json(null)),
    null,
  );
});

test("tutorial catalog rejects missing hash binding and surfaces a definite 409", async () => {
  const config = tutorialStorageConfig((name) => ({
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  })[name])!;
  const slide = {
    number: 1,
    filename: "lesson_01.png",
    storage_path: `${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png`,
    byte_size: 25,
    sha256: "a".repeat(64),
    width: 1080,
    height: 1350,
  };
  await assert.rejects(
    () => recordTutorialGeneration(config, {
      requestId: CATALOG_ID,
      clientId: "squid",
      title: "해시 없는 요청",
      content: {},
      generationMeta: {},
      slides: [slide],
    }, async () => {
      throw new Error("invalid payload must fail before fetch");
    }),
    (error: unknown) => (error as { code?: unknown }).code === "invalid_tutorial_catalog_payload",
  );
  await assert.rejects(
    () => recordTutorialGeneration(config, {
      requestId: CATALOG_ID,
      clientId: "squid",
      title: "충돌 요청",
      content: { request_hash: REQUEST_HASH },
      generationMeta: { request_hash: REQUEST_HASH },
      slides: [slide],
    }, async () => Response.json({ message: "duplicate" }, { status: 409 })),
    (error: unknown) => {
      assert.equal((error as { code?: unknown }).code, "tutorial_idempotency_conflict");
      assert.equal((error as { cleanupSafe?: unknown }).cleanupSafe, true);
      return true;
    },
  );
});

test("ambiguous catalog responses retry without marking uploaded files cleanup-safe", async () => {
  const config = tutorialStorageConfig((name) => ({
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  })[name])!;
  let attempts = 0;
  await assert.rejects(
    () => recordTutorialGeneration(config, {
      requestId: CATALOG_ID,
      clientId: "squid",
      title: "한국어 튜토리얼",
      content: { request_hash: REQUEST_HASH },
      generationMeta: { request_hash: REQUEST_HASH },
      slides: [{
        number: 1,
        filename: "lesson_01.png",
        storage_path: `${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png`,
        byte_size: 25,
        sha256: "a".repeat(64),
        width: 1080,
        height: 1350,
      }],
    }, async () => {
      attempts += 1;
      return new Response("not-json", { status: 200 });
    }),
    (error: unknown) => {
      assert.equal(attempts, 2);
      assert.equal((error as { code?: unknown }).code, "durable_catalog_result_unknown");
      assert.equal((error as { cleanupSafe?: unknown }).cleanupSafe, false);
      return true;
    },
  );
});

test("catalog gateway 5xx is treated as an unknown commit outcome", async () => {
  const config = tutorialStorageConfig((name) => ({
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  })[name])!;
  let attempts = 0;
  await assert.rejects(
    () => recordTutorialGeneration(config, {
      requestId: CATALOG_ID,
      clientId: "squid",
      title: "게이트웨이 재시도",
      content: { request_hash: REQUEST_HASH },
      generationMeta: { request_hash: REQUEST_HASH },
      slides: [{
        number: 1,
        filename: "lesson_01.png",
        storage_path: `${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png`,
        byte_size: 25,
        sha256: "a".repeat(64),
        width: 1080,
        height: 1350,
      }],
    }, async () => {
      attempts += 1;
      return Response.json({ message: "gateway timeout" }, { status: 504 });
    }),
    (error: unknown) => {
      assert.equal(attempts, 2);
      assert.equal((error as { code?: unknown }).code, "durable_catalog_result_unknown");
      assert.equal((error as { cleanupSafe?: unknown }).cleanupSafe, false);
      return true;
    },
  );
});

test("tutorial assets upload to a private bucket and resolve through a short signed URL", async () => {
  const config = tutorialStorageConfig((name) => ({
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  })[name])!;
  const path = `${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png`;
  const calls: Array<{ url: string; method: string; authorization: string | null }> = [];
  const png = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2]);
  const fetcher = async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    calls.push({
      url: request.url,
      method: request.method,
      authorization: request.headers.get("authorization"),
    });
    if (request.url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (request.url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid" }]);
    }
    if (request.method === "POST" && request.url.includes("/storage/v1/object/sign/")) {
      return Response.json({
        signedURL: `/object/sign/content-studio/${path}?token=short-lived-token`,
      });
    }
    return Response.json({ Key: `content-studio/${path}` });
  };

  await verifyPrivateTutorialBucket(config, fetcher);
  await verifyTutorialStorageScope(config, "squid", fetcher);
  const stored = await uploadTutorialSlide(
    config,
    "squid",
    OBJECT_ID,
    "lesson_01.png",
    png.buffer,
    fetcher,
  );
  assert.deepEqual(stored, { objectId: OBJECT_ID, storagePath: path, byteSize: 10 });
  const signedUrl = await createTutorialSlideSignedUrl(config, path, 60, fetcher);
  assert.equal(
    signedUrl,
    `https://project.supabase.co/storage/v1/object/sign/content-studio/${path}?token=short-lived-token`,
  );
  assert.ok(calls.every((call) => call.authorization === "Bearer server-only-service-key"));
  assert.ok(calls.some((call) => call.method === "POST" && call.url.includes("/object/content-studio/")));
});

test("tutorial storage rejects an unregistered workspace/client scope", async () => {
  const config = tutorialStorageConfig((name) => ({
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  })[name])!;
  await assert.rejects(
    () => verifyTutorialStorageScope(
      config,
      "squid",
      async () => Response.json([]),
    ),
    /durable_storage_scope_not_ready/,
  );
});

test("partial durable uploads have a best-effort cleanup path", async () => {
  const config = tutorialStorageConfig((name) => ({
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  })[name])!;
  let deleteBody = "";
  await removeTutorialSlides(config, [
    `${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png`,
  ], async (_input, init) => {
    deleteBody = String(init?.body || "");
    return Response.json([]);
  });
  assert.deepEqual(JSON.parse(deleteBody), {
    prefixes: [`${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png`],
  });
});

test("tutorial slide proxy redirects only to the durable Supabase object", async () => {
  const apiSecret = "railway-api-secret";
  const expires = Math.floor(Date.now() / 1_000) + 3_600;
  const token = signTutorialSlide(apiSecret, "squid", OBJECT_ID, "lesson_01.png", expires);
  let fetchedUrl = "";
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    fetchedUrl = String(input);
    return Response.json({
      signedURL: `/object/sign/content-studio/${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png?token=short-lived-token`,
    });
  };
  try {
    await withNetlifyEnvironment({
      API_SECRET: apiSecret,
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const response = await tutorialSlideHandler(new Request(
        `https://console.example/api/tutorial-slide/squid/${OBJECT_ID}/lesson_01.png?expires=${expires}&token=${token}`,
      ), {
        params: { clientId: "squid", objectId: OBJECT_ID, fileName: "lesson_01.png" },
      } as never);
      assert.equal(response.status, 302);
      assert.equal(
        response.headers.get("location"),
        `https://project.supabase.co/storage/v1/object/sign/content-studio/${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png?token=short-lived-token`,
      );
      assert.match(fetchedUrl, /^https:\/\/project\.supabase\.co\/storage\/v1\/object\/sign\//);
      assert.doesNotMatch(fetchedUrl, /railway|\/files\//);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("tutorial generation fails closed before Railway when durable storage is not configured", async () => {
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  let fetchCount = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    fetchCount += 1;
    throw new Error("no upstream request expected");
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      API_SECRET: "railway-api-secret",
    }, async () => {
      const response = await tutorialHandler(new Request(
        "https://console.example/api/tutorial/squid",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
            "idempotency-key": CATALOG_ID,
          },
          body: "{}",
        },
      ), { params: { clientId: "squid" } } as never);
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), { error: "durable_storage_not_configured" });
      assert.equal(fetchCount, 0);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("tutorial generation requires a caller-stable idempotency key", async () => {
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  let fetchCount = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    fetchCount += 1;
    throw new Error("no upstream request expected");
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      API_SECRET: "railway-api-secret",
    }, async () => {
      const response = await tutorialHandler(new Request(
        "https://console.example/api/tutorial/squid",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
          },
          body: JSON.stringify({ source_content: "충분한 원문입니다." }),
        },
      ), { params: { clientId: "squid" } } as never);
      assert.equal(response.status, 400);
      assert.deepEqual(await response.json(), { error: "invalid_tutorial_idempotency_key" });
      assert.equal(fetchCount, 0);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("tutorial relay reserves time for durable persistence below Netlify's limit", () => {
  assert.match(TUTORIAL_HANDLER_SOURCE, /NETLIFY_REQUEST_BUDGET_MS = 55_000/);
  assert.match(TUTORIAL_HANDLER_SOURCE, /RAILWAY_GENERATION_BUDGET_MS = 32_000/);
  assert.match(TUTORIAL_HANDLER_SOURCE, /deadlineSignal\(requestDeadline, 6_000\)/);
  assert.match(TUTORIAL_HANDLER_SOURCE, /AbortSignal\.timeout\(2_000\)/);
});

test("tutorial generation catalogs uploaded PNGs before returning durable slide URLs", async () => {
  const apiSecret = "railway-api-secret";
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  const firstPng = minimalPng(1080, 1350, 1);
  const secondPng = minimalPng(1080, 1350, 2);
  const events: string[] = [];
  const sourceContent = "Squid의 크로스체인 구조를 설명하는 충분한 원문입니다.";
  const expectedRequestHash = tutorialRequestHash({
    clientId: "squid",
    sourceContent,
    sourceType: "article",
    sourceUrl: "",
    mockMode: true,
  });
  let rpcBody: Record<string, any> = {};
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    if (request.url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (request.url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid" }]);
    }
    if (request.url.endsWith("/rest/v1/rpc/get_tutorial_generation")) {
      return Response.json(null);
    }
    if (request.url.endsWith("/clients/squid/generate/edu-carousel")) {
      events.push("generate");
      return Response.json({
        client_id: "squid",
        content_type: "edu_carousel",
        series: {
          series_title_en: "Squid Fundamentals",
          series_subtitle_kr: "스퀴드 핵심 이해하기",
        },
        lessons: tutorialLessons(2),
        lesson_count: 2,
        png_paths: [
          "/tmp/output/squid/edu_2087200000/lesson_01.png",
          "/tmp/output/squid/edu_2087200000/lesson_02.png",
        ],
        manifest_path: "/tmp/output/squid/edu_2087200000/manifest.json",
        duration_ms: 321,
      });
    }
    if (request.url.endsWith("/files/squid/edu_2087200000/lesson_01.png")) {
      events.push("fetch-1");
      return new Response(firstPng, { headers: { "content-length": String(firstPng.length) } });
    }
    if (request.url.endsWith("/files/squid/edu_2087200000/lesson_02.png")) {
      events.push("fetch-2");
      return new Response(secondPng, { headers: { "content-length": String(secondPng.length) } });
    }
    if (request.url.includes("/storage/v1/object/content-studio/")) {
      events.push("upload");
      return Response.json({ Key: request.url });
    }
    if (request.url.endsWith("/rest/v1/rpc/record_tutorial_generation")) {
      events.push("catalog");
      rpcBody = JSON.parse(String(init?.body));
      const assetIds = rpcBody.target_slides.map(
        (slide: { storage_path: string }) => slide.storage_path.split("/")[2],
      );
      return Response.json({
        content_item_id: rpcBody.target_content_item_id,
        content_version_id: VERSION_ID,
        asset_ids: assetIds,
      });
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      API_SECRET: apiSecret,
      RAILWAY_API_URL: "https://railway.example",
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const response = await tutorialHandler(new Request(
        "https://console.example/api/tutorial/squid",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
            "idempotency-key": CATALOG_ID,
          },
          body: JSON.stringify({
            source_content: sourceContent,
            source_type: "article",
            mock_mode: true,
          }),
        },
      ), { params: { clientId: "squid" } } as never);
      assert.equal(response.status, 200);
      const payload = await response.json() as Record<string, any>;
      assert.equal(payload.content_item_id, rpcBody.target_content_item_id);
      assert.equal(payload.content_version_id, VERSION_ID);
      assert.deepEqual(payload.asset_ids, rpcBody.target_slides.map(
        (slide: { storage_path: string }) => slide.storage_path.split("/")[2],
      ));
      assert.equal(payload.slides.length, 2);
      assert.equal(payload.mock_mode, true);
      assert.equal(payload.brand_qa.policy_version, "brand-qa@1");
      assert.equal(payload.brand_qa.client_id, "squid");
      assert.equal(payload.brand_qa.content_kind, "tutorial");
      assert.equal(payload.fact_check.policy_version, "double-fact-check@1");
      assert.equal(payload.fact_check.human_review_required, true);
      assert.equal(payload.fact_check.checks[1].metrics.artifact_count, 2);
      assert.deepEqual(payload.lessons, tutorialLessons(2));
      assert.equal(rpcBody.target_title, "스퀴드 핵심 이해하기");
      assert.deepEqual(rpcBody.target_content.lessons, tutorialLessons(2));
      assert.equal(rpcBody.target_content.source.mode, "provided");
      assert.equal(rpcBody.target_content.request_hash, expectedRequestHash);
      assert.equal(
        rpcBody.target_content.source.content,
        "Squid의 크로스체인 구조를 설명하는 충분한 원문입니다.",
      );
      assert.equal(rpcBody.target_generation_meta.duration_ms, 321);
      assert.equal(rpcBody.target_generation_meta.mock_mode, true);
      assert.equal(rpcBody.target_generation_meta.request_hash, expectedRequestHash);
      assert.deepEqual(rpcBody.target_generation_meta.brand_qa, payload.brand_qa);
      assert.deepEqual(rpcBody.target_generation_meta.fact_check, payload.fact_check);
      assert.deepEqual(
        rpcBody.target_slides.map((slide: Record<string, unknown>) => ({
          number: slide.number,
          filename: slide.filename,
          byte_size: slide.byte_size,
          width: slide.width,
          height: slide.height,
          sha256: slide.sha256,
        })),
        [
          {
            number: 1,
            filename: "lesson_01.png",
            byte_size: firstPng.length,
            width: 1080,
            height: 1350,
            sha256: createHash("sha256").update(firstPng).digest("hex"),
          },
          {
            number: 2,
            filename: "lesson_02.png",
            byte_size: secondPng.length,
            width: 1080,
            height: 1350,
            sha256: createHash("sha256").update(secondPng).digest("hex"),
          },
        ],
      );
      assert.ok(events.lastIndexOf("catalog") > events.lastIndexOf("upload"));
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("a definite tutorial catalog conflict returns 409 and removes every uploaded object", async () => {
  const apiSecret = "railway-api-secret";
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  const png = minimalPng(1080, 1350, 3);
  let deleteBody = "";
  const uploadedPaths: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    if (request.url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (request.url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid" }]);
    }
    if (request.url.endsWith("/rest/v1/rpc/get_tutorial_generation")) {
      return Response.json(null);
    }
    if (request.url.endsWith("/clients/squid/generate/edu-carousel")) {
      return Response.json({
        client_id: "squid",
        content_type: "edu_carousel",
        series: { series_subtitle_kr: "정리 테스트" },
        lessons: tutorialLessons(2),
        lesson_count: 2,
        png_paths: [
          "/tmp/output/squid/edu_2087200001/lesson_01.png",
          "/tmp/output/squid/edu_2087200001/lesson_02.png",
        ],
        manifest_path: "/tmp/output/squid/edu_2087200001/manifest.json",
        duration_ms: 12,
      });
    }
    if (request.url.includes("/files/squid/edu_2087200001/")) return new Response(png);
    if (request.method === "POST" && request.url.includes("/storage/v1/object/content-studio/")) {
      uploadedPaths.push(decodeURIComponent(request.url.split("/content-studio/")[1]));
      return Response.json({ Key: request.url });
    }
    if (request.url.endsWith("/rest/v1/rpc/record_tutorial_generation")) {
      return Response.json({ message: "rejected" }, { status: 409 });
    }
    if (request.method === "DELETE" && request.url.endsWith("/storage/v1/object/content-studio")) {
      deleteBody = String(init?.body || "");
      return Response.json([]);
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      API_SECRET: apiSecret,
      RAILWAY_API_URL: "https://railway.example",
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const response = await tutorialHandler(new Request(
        "https://console.example/api/tutorial/squid",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
            "idempotency-key": CATALOG_ID,
          },
          body: JSON.stringify({
            source_content: "정리 동작을 검증하기 위한 충분한 원문입니다.",
          }),
        },
      ), { params: { clientId: "squid" } } as never);
      assert.equal(response.status, 409);
      assert.deepEqual(await response.json(), { error: "tutorial_idempotency_conflict" });
      assert.deepEqual(JSON.parse(deleteBody), { prefixes: uploadedPaths });
      assert.equal(uploadedPaths.length, 2);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("a same-payload catalog race cleans losing uploads and returns the winner", async () => {
  const apiSecret = "railway-api-secret";
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  const png = minimalPng(1080, 1350, 8);
  const sourceContent = "동시 요청 복구를 검증하기 위한 충분한 원문입니다.";
  const requestHash = tutorialRequestHash({
    clientId: "squid",
    sourceContent,
    sourceType: "article",
    sourceUrl: "",
    mockMode: false,
  });
  let lookupCount = 0;
  let cleanupCount = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    if (request.url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (request.url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid" }]);
    }
    if (request.url.endsWith("/rest/v1/rpc/get_tutorial_generation")) {
      lookupCount += 1;
      if (lookupCount === 1) return Response.json(null);
      return Response.json({
        content_item_id: CATALOG_ID,
        content_version_id: VERSION_ID,
        title: "먼저 저장된 튜토리얼",
        content: {
          request_hash: requestHash,
          series: { series_subtitle_kr: "먼저 저장된 튜토리얼" },
          lessons: tutorialLessons(1),
          source: { mode: "provided", content: sourceContent },
        },
        generation_meta: {
          request_hash: requestHash,
          duration_ms: 99,
          fact_check: validTutorialFactCheck(),
        },
        slides: [{
          asset_id: OBJECT_ID,
          number: 1,
          filename: "lesson_01.png",
          storage_path: `${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png`,
          byte_size: 25,
          sha256: "a".repeat(64),
          width: 1080,
          height: 1350,
        }],
      });
    }
    if (request.url.endsWith("/clients/squid/generate/edu-carousel")) {
      return Response.json({
        client_id: "squid",
        content_type: "edu_carousel",
        series: { series_subtitle_kr: "동시 요청" },
        lessons: tutorialLessons(1),
        lesson_count: 1,
        png_paths: ["/tmp/output/squid/edu_2087200004/lesson_01.png"],
        manifest_path: "/tmp/output/squid/edu_2087200004/manifest.json",
        duration_ms: 15,
      });
    }
    if (request.url.endsWith("/files/squid/edu_2087200004/lesson_01.png")) {
      return new Response(png);
    }
    if (request.method === "POST" && request.url.includes("/storage/v1/object/content-studio/")) {
      return Response.json({ Key: request.url });
    }
    if (request.url.endsWith("/rest/v1/rpc/record_tutorial_generation")) {
      return Response.json({ message: "concurrent request won" }, { status: 409 });
    }
    if (request.method === "DELETE" && request.url.endsWith("/storage/v1/object/content-studio")) {
      cleanupCount += 1;
      return Response.json([]);
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      API_SECRET: apiSecret,
      RAILWAY_API_URL: "https://railway.example",
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const response = await tutorialHandler(new Request(
        "https://console.example/api/tutorial/squid",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
            "idempotency-key": CATALOG_ID,
          },
          body: JSON.stringify({ source_content: sourceContent }),
        },
      ), { params: { clientId: "squid" } } as never);
      assert.equal(response.status, 200);
      const payload = await response.json() as Record<string, any>;
      assert.equal(payload.reused, true);
      assert.equal(payload.content_version_id, VERSION_ID);
      assert.deepEqual(payload.asset_ids, [OBJECT_ID]);
      assert.equal(lookupCount, 2);
      assert.equal(cleanupCount, 1);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("an ambiguous upload failure removes every intended object path", async () => {
  const apiSecret = "railway-api-secret";
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  const png = minimalPng(1080, 1350, 4);
  const attemptedPaths: string[] = [];
  let deleteBody = "";
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    if (request.url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (request.url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid" }]);
    }
    if (request.url.endsWith("/rest/v1/rpc/get_tutorial_generation")) {
      return Response.json(null);
    }
    if (request.url.endsWith("/clients/squid/generate/edu-carousel")) {
      return Response.json({
        client_id: "squid",
        content_type: "edu_carousel",
        series: { series_subtitle_kr: "업로드 정리 테스트" },
        lessons: tutorialLessons(2),
        lesson_count: 2,
        png_paths: [
          "/tmp/output/squid/edu_2087200002/lesson_01.png",
          "/tmp/output/squid/edu_2087200002/lesson_02.png",
        ],
        manifest_path: "/tmp/output/squid/edu_2087200002/manifest.json",
        duration_ms: 13,
      });
    }
    if (request.url.includes("/files/squid/edu_2087200002/")) {
      return new Response(png);
    }
    if (request.method === "POST" && request.url.includes("/storage/v1/object/content-studio/")) {
      attemptedPaths.push(decodeURIComponent(request.url.split("/content-studio/")[1]));
      if (attemptedPaths.length === 2) {
        // Simulate Supabase committing the object before its response is lost.
        throw new Error("upload response lost");
      }
      return Response.json({ Key: request.url });
    }
    if (request.method === "DELETE" && request.url.endsWith("/storage/v1/object/content-studio")) {
      deleteBody = String(init?.body || "");
      return Response.json([]);
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      API_SECRET: apiSecret,
      RAILWAY_API_URL: "https://railway.example",
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const response = await tutorialHandler(new Request(
        "https://console.example/api/tutorial/squid",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
            "idempotency-key": CATALOG_ID,
          },
          body: JSON.stringify({
            source_content: "응답이 사라진 업로드도 정리하는지 검증하는 충분한 원문입니다.",
          }),
        },
      ), { params: { clientId: "squid" } } as never);
      assert.equal(response.status, 502);
      assert.deepEqual(await response.json(), { error: "durable_storage_upload_failed" });
      assert.equal(attemptedPaths.length, 2);
      assert.deepEqual(JSON.parse(deleteBody), { prefixes: attemptedPaths });
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("an unknown catalog outcome preserves every attempted object", async () => {
  const apiSecret = "railway-api-secret";
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  const png = minimalPng(1080, 1350, 5);
  const attemptedPaths: string[] = [];
  let catalogAttempts = 0;
  let deleteAttempts = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    if (request.url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (request.url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid" }]);
    }
    if (request.url.endsWith("/rest/v1/rpc/get_tutorial_generation")) {
      return Response.json(null);
    }
    if (request.url.endsWith("/clients/squid/generate/edu-carousel")) {
      return Response.json({
        client_id: "squid",
        content_type: "edu_carousel",
        series: { series_subtitle_kr: "카탈로그 보존 테스트" },
        lessons: tutorialLessons(1),
        lesson_count: 1,
        png_paths: ["/tmp/output/squid/edu_2087200003/lesson_01.png"],
        manifest_path: "/tmp/output/squid/edu_2087200003/manifest.json",
        duration_ms: 14,
      });
    }
    if (request.url.endsWith("/files/squid/edu_2087200003/lesson_01.png")) {
      return new Response(png);
    }
    if (request.method === "POST" && request.url.includes("/storage/v1/object/content-studio/")) {
      attemptedPaths.push(decodeURIComponent(request.url.split("/content-studio/")[1]));
      return Response.json({ Key: request.url });
    }
    if (request.url.endsWith("/rest/v1/rpc/record_tutorial_generation")) {
      catalogAttempts += 1;
      return Response.json({ message: "gateway timeout" }, { status: 504 });
    }
    if (request.method === "DELETE" && request.url.endsWith("/storage/v1/object/content-studio")) {
      deleteAttempts += 1;
      return Response.json([]);
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      API_SECRET: apiSecret,
      RAILWAY_API_URL: "https://railway.example",
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const response = await tutorialHandler(new Request(
        "https://console.example/api/tutorial/squid",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
            "idempotency-key": CATALOG_ID,
          },
          body: JSON.stringify({
            source_content: "카탈로그 결과가 불명확할 때 파일을 보존하는지 검증하는 충분한 원문입니다.",
          }),
        },
      ), { params: { clientId: "squid" } } as never);
      assert.equal(response.status, 502);
      assert.deepEqual(await response.json(), { error: "durable_catalog_result_unknown" });
      assert.equal(attemptedPaths.length, 1);
      assert.equal(catalogAttempts, 2);
      assert.equal(deleteAttempts, 0);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("a repeated tutorial request returns the committed catalog without Railway", async () => {
  const apiSecret = "railway-api-secret";
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  const sourceContent = "";
  const sourceUrl = "https://x.com/squidrouter/status/123456789";
  const requestHash = tutorialRequestHash({
    clientId: "squid",
    sourceContent,
    sourceType: "tweet",
    sourceUrl,
    mockMode: true,
  });
  let railwayRequests = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    if (request.url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (request.url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid" }]);
    }
    if (request.url.endsWith("/rest/v1/rpc/get_tutorial_generation")) {
      return Response.json({
        content_item_id: CATALOG_ID,
        content_version_id: VERSION_ID,
        title: "이미 저장된 튜토리얼",
        content: {
          request_hash: requestHash,
          series: { series_subtitle_kr: "이미 저장된 튜토리얼" },
          lessons: tutorialLessons(1),
          source: { mode: "x_import", url: sourceUrl, content: "전체 원문" },
        },
        generation_meta: {
          request_hash: requestHash,
          duration_ms: 222,
          mock_mode: true,
          fact_check: validTutorialFactCheck(),
        },
        slides: [{
          asset_id: OBJECT_ID,
          number: 1,
          filename: "lesson_01.png",
          storage_path: `${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png`,
          byte_size: 25,
          sha256: "a".repeat(64),
          width: 1080,
          height: 1350,
        }],
      });
    }
    railwayRequests += 1;
    throw new Error(`Railway must not be called on a catalog retry: ${request.url}`);
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      API_SECRET: apiSecret,
      RAILWAY_API_URL: "https://railway.example",
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const response = await tutorialHandler(new Request(
        "https://console.example/api/tutorial/squid",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
            "idempotency-key": CATALOG_ID,
          },
          body: JSON.stringify({
            source_content: sourceContent,
            source_type: "tweet",
            source_url: sourceUrl,
            mock_mode: true,
          }),
        },
      ), { params: { clientId: "squid" } } as never);
      assert.equal(response.status, 200);
      const payload = await response.json() as Record<string, any>;
      assert.equal(payload.reused, true);
      assert.equal(payload.mock_mode, true);
      assert.equal(payload.content_item_id, CATALOG_ID);
      assert.equal(payload.content_version_id, VERSION_ID);
      assert.equal(payload.source_mode, "x_import");
      assert.deepEqual(payload.asset_ids, [OBJECT_ID]);
      assert.equal(payload.slides.length, 1);
      assert.equal(railwayRequests, 0);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("a reused tutorial UUID with a different payload returns 409 before Railway", async () => {
  const apiSecret = "railway-api-secret";
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  let downstreamRequests = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    if (request.url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (request.url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid" }]);
    }
    if (request.url.endsWith("/rest/v1/rpc/get_tutorial_generation")) {
      return Response.json({
        content_item_id: CATALOG_ID,
        content_version_id: VERSION_ID,
        title: "다른 요청으로 저장된 튜토리얼",
        content: {
          request_hash: "e".repeat(64),
          series: { series_subtitle_kr: "다른 요청" },
          source: { mode: "provided", content: "이전 원문" },
        },
        generation_meta: { request_hash: "e".repeat(64), duration_ms: 111 },
        slides: [{
          asset_id: OBJECT_ID,
          number: 1,
          filename: "lesson_01.png",
          storage_path: `${WORKSPACE_ID}/squid/${OBJECT_ID}/lesson_01.png`,
          byte_size: 25,
          sha256: "a".repeat(64),
          width: 1080,
          height: 1350,
        }],
      });
    }
    downstreamRequests += 1;
    throw new Error(`generation must not run after an idempotency conflict: ${request.url}`);
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      API_SECRET: apiSecret,
      RAILWAY_API_URL: "https://railway.example",
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const response = await tutorialHandler(new Request(
        "https://console.example/api/tutorial/squid",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
            "idempotency-key": CATALOG_ID,
          },
          body: JSON.stringify({ source_content: "새 요청에 사용할 충분한 원문입니다." }),
        },
      ), { params: { clientId: "squid" } } as never);
      assert.equal(response.status, 409);
      assert.deepEqual(await response.json(), { error: "tutorial_idempotency_conflict" });
      assert.equal(downstreamRequests, 0);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
