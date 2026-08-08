import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import studioBannerHandler from "../netlify/functions/batch-review-banner.mts";
import buzzBannerHandler from "../netlify/functions/buzz-shadow-origintrail-banner.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";
import { renderOriginTrailBatchBanner } from "../netlify/functions/_shared/origintrail-batch-banner.mts";
import { ORIGINTRAIL_ARCHIVED_JOB_ID } from "../netlify/functions/_shared/origintrail-archived-review.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const JOB_ID = "22222222-2222-4222-8222-222222222222";
const ACCESS_TOKEN = "test-studio-access-token-32-bytes";
const SHADOW_TOKEN = "buzz-shadow-read-token-that-is-long-enough";
const PREVIEW_START_AT = "2026-07-30T00:00:00.000Z";
const FINISHED_AT = "2026-07-31T12:00:00.000Z";
const LOGO = readFileSync(new URL(
  "../web/console/assets/brands/origintrail-dark.png",
  import.meta.url,
));

function detail(sourceContent = "OriginTrail 공식 X Article 본문입니다.") {
  return {
    job_id: JOB_ID,
    client_id: "origintrail",
    agent_id: "origintrail_client_agent",
    workflow_kind: "official_source_nonurgent_pack",
    stage: "generate",
    status: "completed",
    model: "gpt-5.6-luna",
    model_tier: "S",
    title: "OriginTrail 공식 원문 비긴급 팩",
    result_code: "needs_review",
    actual_cost_microusd: 2_200,
    finished_at: FINISHED_AT,
    source_url: "https://x.com/origin_trail/status/2082883998829752783",
    result_payload: {
      headline_ko: "OriginTrail 7월 업데이트",
      body_ko: "검증 가능한 출처와 공유 컨텍스트를 다룹니다.",
      x_copy_ko: "OriginTrail 7월 업데이트를 확인하세요.",
      telegram_copy_ko: "DKG V10과 Buzz 통합의 핵심 내용을 정리했습니다.",
    },
    source_content: sourceContent,
    source_evidence: {
      storage: "inline",
      content_length: sourceContent.length,
      content_sha256: createHash("sha256").update(sourceContent, "utf8").digest("hex"),
      verified_at: FINISHED_AT,
    },
    input_sha256: "a".repeat(64),
    actual_input_tokens: 1_942,
    actual_output_tokens: 595,
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

function context(jobId = JOB_ID) {
  return {
    params: { jobId },
    site: { url: "https://console.example" },
  } as never;
}

function environment() {
  return {
    STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
    BUZZ_SHADOW_ACCESS_TOKEN: SHADOW_TOKEN,
    BUZZ_RESULT_PREVIEW_START_AT: PREVIEW_START_AT,
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  };
}

function pngChunkTypes(bytes: Buffer): string[] {
  const chunks: string[] = [];
  let offset = 8;
  while (offset + 12 <= bytes.length) {
    const length = bytes.readUInt32BE(offset);
    const kind = bytes.subarray(offset + 4, offset + 8).toString("ascii");
    chunks.push(kind);
    offset += 12 + length;
    if (kind === "IEND") break;
  }
  assert.equal(offset, bytes.length);
  return chunks;
}

test("bundled Hangul font renders distinct glyphs and deterministic PNG bytes", async () => {
  const fetcher: typeof fetch = async (input) => {
    const request = new Request(input);
    if (request.url === "https://console.example/assets/brands/origintrail-dark.png") {
      return new Response(LOGO, {
        headers: {
          "content-type": "image/png",
          "content-length": String(LOGO.length),
        },
      });
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  const firstDetail = detail();
  firstDetail.result_payload.headline_ko = "가나다라마바사";
  const secondDetail = detail();
  secondDetail.result_payload.headline_ko = "아자차카타파하";

  const first = await renderOriginTrailBatchBanner(
    firstDetail,
    "https://console.example",
    fetcher,
  );
  const replay = await renderOriginTrailBatchBanner(
    firstDetail,
    "https://console.example",
    fetcher,
  );
  const second = await renderOriginTrailBatchBanner(
    secondDetail,
    "https://console.example",
    fetcher,
  );

  assert.equal(first.sha256, replay.sha256);
  assert.deepEqual(first.bytes, replay.bytes);
  assert.notEqual(first.sha256, second.sha256);
});

test("Studio and Buzz receive the same evidence-bound 1200x630 PNG", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const request = new Request(input);
    if (request.url.endsWith("/rest/v1/rpc/get_agent_batch_review_item")) {
      return Response.json(detail());
    }
    if (request.url === "https://console.example/assets/brands/origintrail-dark.png") {
      return new Response(LOGO, {
        headers: {
          "content-type": "image/png",
          "content-length": String(LOGO.length),
        },
      });
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  try {
    await withNetlifyEnvironment(environment(), async () => {
      const session = createStudioSessionValue(ACCESS_TOKEN);
      const studio = await studioBannerHandler(new Request(
        `https://console.example/api/batch-review/${JOB_ID}/banner.png`,
        { headers: { cookie: `${STUDIO_SESSION_COOKIE}=${session}` } },
      ), context());
      const buzz = await buzzBannerHandler(new Request(
        `https://console.example/api/buzz-shadow/origintrail/batch/${JOB_ID}/banner.png`,
        { headers: { "x-coineasy-buzz-key": SHADOW_TOKEN } },
      ), context());

      assert.equal(studio.status, 200, await studio.clone().text());
      assert.equal(buzz.status, 200, await buzz.clone().text());
      assert.equal(studio.headers.get("content-type"), "image/png");
      assert.equal(studio.headers.get("cache-control"), "no-store");
      assert.equal(buzz.headers.get("vary"), "x-coineasy-buzz-key");
      assert.match(
        studio.headers.get("content-disposition") || "",
        new RegExp(`origintrail-review-${JOB_ID}\\.png`),
      );
      const studioBytes = Buffer.from(await studio.arrayBuffer());
      const buzzBytes = Buffer.from(await buzz.arrayBuffer());
      assert.deepEqual(studioBytes, buzzBytes);
      assert.deepEqual([...studioBytes.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
      assert.equal(studioBytes.readUInt32BE(16), 1_200);
      assert.equal(studioBytes.readUInt32BE(20), 630);
      const chunkTypes = pngChunkTypes(studioBytes);
      assert.equal(chunkTypes[0], "IHDR");
      assert.equal(chunkTypes.at(-1), "IEND");
      assert.equal(chunkTypes.includes("pHYs"), false);
      assert.equal(chunkTypes.some((kind) => ["tEXt", "zTXt", "iTXt", "iCCP", "eXIf"].includes(kind)), false);
      assert.match(studio.headers.get("x-coineasy-content-sha256") || "", /^[a-f0-9]{64}$/);
      assert.equal(
        studio.headers.get("x-coineasy-content-sha256"),
        buzz.headers.get("x-coineasy-content-sha256"),
      );
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("archived Staging evidence renders one authenticated banner without deleted storage", async () => {
  const originalFetch = globalThis.fetch;
  let storageCalls = 0;
  globalThis.fetch = async (input) => {
    const request = new Request(input);
    if (request.url === "https://console.example/assets/brands/origintrail-dark.png") {
      return new Response(LOGO, {
        headers: {
          "content-type": "image/png",
          "content-length": String(LOGO.length),
        },
      });
    }
    storageCalls += 1;
    throw new Error(`unexpected request ${request.url}`);
  };
  try {
    await withNetlifyEnvironment({
      ...environment(),
      BUZZ_RESULT_PREVIEW_START_AT: "2026-08-05T10:00:00.000Z",
    }, async () => {
      const session = createStudioSessionValue(ACCESS_TOKEN);
      const studio = await studioBannerHandler(new Request(
        `https://console.example/api/batch-review/${ORIGINTRAIL_ARCHIVED_JOB_ID}/banner.png`,
        { headers: { cookie: `${STUDIO_SESSION_COOKIE}=${session}` } },
      ), context(ORIGINTRAIL_ARCHIVED_JOB_ID));
      const buzz = await buzzBannerHandler(new Request(
        `https://console.example/api/buzz-shadow/origintrail/batch/${ORIGINTRAIL_ARCHIVED_JOB_ID}/banner.png`,
        { headers: { "x-coineasy-buzz-key": SHADOW_TOKEN } },
      ), context(ORIGINTRAIL_ARCHIVED_JOB_ID));

      assert.equal(studio.status, 200, await studio.clone().text());
      assert.equal(buzz.status, 200, await buzz.clone().text());
      assert.deepEqual(
        Buffer.from(await studio.arrayBuffer()),
        Buffer.from(await buzz.arrayBuffer()),
      );
      assert.equal(storageCalls, 0);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("banner routes authenticate before storage and reject URL-only evidence", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async (input) => {
    calls += 1;
    const request = new Request(input);
    if (request.url.endsWith("/rest/v1/rpc/get_agent_batch_review_item")) {
      return Response.json(detail("https://t.co/source-only"));
    }
    throw new Error("logo must not be fetched for insufficient evidence");
  };
  try {
    await withNetlifyEnvironment(environment(), async () => {
      const unauthorized = await buzzBannerHandler(new Request(
        `https://console.example/api/buzz-shadow/origintrail/batch/${JOB_ID}/banner.png`,
      ), context());
      assert.equal(unauthorized.status, 401);
      assert.equal(calls, 0);

      const session = createStudioSessionValue(ACCESS_TOKEN);
      const insufficient = await studioBannerHandler(new Request(
        `https://console.example/api/batch-review/${JOB_ID}/banner.png`,
        { headers: { cookie: `${STUDIO_SESSION_COOKIE}=${session}` } },
      ), context());
      assert.equal(insufficient.status, 409);
      assert.deepEqual(await insufficient.json(), {
        error: "origintrail_batch_banner_evidence_required",
      });
      assert.equal(calls, 1);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Buzz banner uses the scoped reviewer bearer without replacing the project API key", async () => {
  const scopedKey = "scoped-batch-reviewer-role-jwt-value";
  const originalFetch = globalThis.fetch;
  let reviewCalls = 0;
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    if (request.url.endsWith("/rest/v1/rpc/get_agent_batch_review_item")) {
      reviewCalls += 1;
      const headers = new Headers(init?.headers);
      assert.equal(headers.get("authorization"), `Bearer ${scopedKey}`);
      assert.equal(headers.get("apikey"), "server-only-service-role");
      return Response.json(detail());
    }
    if (request.url === "https://console.example/assets/brands/origintrail-dark.png") {
      return new Response(LOGO, {
        headers: {
          "content-type": "image/png",
          "content-length": String(LOGO.length),
        },
      });
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  try {
    await withNetlifyEnvironment({
      ...environment(),
      SUPABASE_BUZZ_SHADOW_KEY: scopedKey,
    }, async () => {
      const response = await buzzBannerHandler(new Request(
        `https://console.example/api/buzz-shadow/origintrail/batch/${JOB_ID}/banner.png`,
        { headers: { "x-coineasy-buzz-key": SHADOW_TOKEN } },
      ), context());
      assert.equal(response.status, 200, await response.clone().text());
      assert.equal(reviewCalls, 1);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
