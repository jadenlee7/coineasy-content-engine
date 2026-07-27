import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import articleHandler, {
  articleRequestHash,
  deadlineSignal as articleDeadlineSignal,
  isRailwayArticleResponse,
} from "../netlify/functions/article.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";


const GENERATED_ARTICLE = {
  client_id: "squid",
  content_type: "article",
  title: "Squid가 라우팅 제품군을 업데이트했습니다",
  lead: "Squid는 크로스체인 라우팅 제품군 업데이트를 공개했습니다.",
  sections: [
    { id: "section-1", heading: "발표 내용", body: "App과 API가 업데이트 대상입니다." },
    { id: "section-2", heading: "제품 구성", body: "SDK와 Widget도 같은 스택을 사용합니다." },
    { id: "section-3", heading: "확인 범위", body: "원문에 포함된 사실만 정리합니다." },
  ],
  key_takeaways: ["제품군 업데이트입니다.", "동일한 스택을 사용합니다.", "원문 범위를 유지합니다."],
  visuals: [
    {
      id: "visual-1",
      after_section_id: "section-1",
      role: "overview",
      motif: "flow",
      eyebrow: "WHY IT MATTERS",
      headline: "하나의 라우팅 스택",
      caption: "제품 접점은 동일한 라우팅 기반을 공유합니다.",
      points: ["App과 API가 업데이트 대상입니다.", "SDK와 Widget도 같은 스택을 사용합니다."],
    },
    {
      id: "visual-2",
      after_section_id: "section-3",
      role: "explainer",
      motif: "layers",
      eyebrow: "PRODUCT LAYERS",
      headline: "네 가지 제품 접점",
      caption: "원문에 포함된 제품 범위만 정리합니다.",
      points: ["제품군 업데이트입니다.", "원문 범위를 유지합니다."],
    },
  ],
  source_map: [{ source_url: "https://example.com/source", applies_to: ["title"] }],
  channel_copy: { telegram: "텔레그램 문구", x: "X 문구입니다." },
  markdown: "# Squid가 라우팅 제품군을 업데이트했습니다\n",
  duration_ms: 1200,
};

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const REQUEST_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";

const PASTED_SOURCE = (
  "Squid published a cross-chain routing update for App, API, SDK, and Widget. "
  + "The source explains that integrators use the same routing stack and does not announce "
  + "a token, launch date, Korea availability, performance metric, pricing change, or roadmap. "
  + "The update is limited to the product surfaces and their shared routing system. "
  + "This pasted material is the complete factual boundary for the Korean article draft."
);


async function withArticleEnvironment(
  fetchImpl: typeof fetch,
  run: () => Promise<void>,
): Promise<void> {
  const originalFetch = globalThis.fetch;
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  globalThis.fetch = fetchImpl;
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: {
      env: {
        get(name: string): string | undefined {
          if (name === "API_SECRET") return "article-secret";
          if (name === "RAILWAY_API_URL") return "https://railway.example/";
          if (name === "STUDIO_ACCESS_TOKEN") return "article-studio-access-token";
          if (name === "SUPABASE_URL") return "https://project.supabase.co";
          if (name === "SUPABASE_SERVICE_ROLE_KEY") return "server-only-service-key";
          if (name === "CONTENT_STUDIO_WORKSPACE_ID") return WORKSPACE_ID;
          return undefined;
        },
      },
    },
  });
  try {
    await run();
  } finally {
    globalThis.fetch = originalFetch;
    if (originalNetlify) {
      Object.defineProperty(globalThis, "Netlify", originalNetlify);
    } else {
      Reflect.deleteProperty(globalThis, "Netlify");
    }
  }
}


test("article proxy forwards pasted source to the typed Railway endpoint", async () => {
  let upstreamUrl = "";
  let upstreamApiKey = "";
  let upstreamBody: Record<string, unknown> = {};

  await withArticleEnvironment(async (input, init) => {
    const requestUrl = String(input);
    if (requestUrl.includes("/rest/v1/workspace_clients")) {
      return Response.json([{
        workspace_id: WORKSPACE_ID,
        client_id: "squid",
        active: true,
      }]);
    }
    if (requestUrl.endsWith("/rest/v1/rpc/get_generated_content")) {
      return Response.json(null);
    }
    if (requestUrl.endsWith("/rest/v1/rpc/record_generated_content")) {
      return Response.json({
        content_item_id: REQUEST_ID,
        content_version_id: VERSION_ID,
        asset_ids: [],
      });
    }
    upstreamUrl = requestUrl;
    upstreamApiKey = new Headers(init?.headers).get("X-API-Key") || "";
    upstreamBody = JSON.parse(String(init?.body));
    return Response.json(GENERATED_ARTICLE);
  }, async () => {
    const response = await articleHandler(new Request(
      "https://console.example/api/article/squid",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "Idempotency-Key": REQUEST_ID,
          cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue("article-studio-access-token")}`,
        },
        body: JSON.stringify({
          source_content: PASTED_SOURCE,
          source_type: "article",
          source_url: "https://example.com/source",
        }),
      },
    ), { params: { clientId: "squid" } } as never);

    assert.equal(response.status, 200);
    assert.equal(upstreamUrl, "https://railway.example/clients/squid/generate/article");
    assert.equal(upstreamApiKey, "article-secret");
    assert.deepEqual(upstreamBody, {
      source_content: PASTED_SOURCE,
      source_type: "article",
      source_url: "https://example.com/source",
    });
    assert.deepEqual(await response.json(), {
      ...GENERATED_ARTICLE,
      storage_backend: "supabase",
      content_item_id: REQUEST_ID,
      content_version_id: VERSION_ID,
      asset_ids: [],
      reused: false,
    });
  });
});

test("article Railway deadline preserves the catalog write reserve", () => {
  const now = Date.now();
  assert.throws(
    () => articleDeadlineSignal(now + 8_000, 44_000, 8_000),
    (error: unknown) => error instanceof Error && error.message === "article_deadline_exceeded",
  );
  assert.equal(
    articleDeadlineSignal(now + 8_500, 44_000, 8_000).aborted,
    false,
  );

  const source = readFileSync(
    new URL("../netlify/functions/article.mts", import.meta.url),
    "utf8",
  );
  assert.match(source, /const ARTICLE_PERSISTENCE_RESERVE_MS = 8_000;/);
  assert.match(
    source,
    /signal:\s*deadlineSignal\(\s*requestDeadline,\s*RAILWAY_ARTICLE_BUDGET_MS,\s*ARTICLE_PERSISTENCE_RESERVE_MS,\s*\)/,
  );
});


test("article proxy rejects URL-only or short input without fetching", async () => {
  let fetched = false;
  await withArticleEnvironment(async () => {
    fetched = true;
    return Response.json(GENERATED_ARTICLE);
  }, async () => {
    const response = await articleHandler(new Request(
      "https://console.example/api/article/yellow",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "Idempotency-Key": REQUEST_ID,
          cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue("article-studio-access-token")}`,
        },
        body: JSON.stringify({
          source_content: "",
          source_url: "https://example.com/source",
        }),
      },
    ), { params: { clientId: "yellow" } } as never);

    assert.equal(response.status, 422);
    assert.deepEqual(await response.json(), {
      error: "source_content_must_be_300_to_60000_chars",
    });
    assert.equal(fetched, false);
  });
});

test("browser sessions cannot inject runtime style references", async () => {
  let fetched = false;
  await withArticleEnvironment(async () => {
    fetched = true;
    throw new Error("upstream should not be called");
  }, async () => {
    const response = await articleHandler(new Request(
      "https://console.example/api/article/squid",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "Idempotency-Key": REQUEST_ID,
          cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue("article-studio-access-token")}`,
        },
        body: JSON.stringify({
          source_content: PASTED_SOURCE,
          source_type: "article",
          source_url: "https://example.com/source",
          style_references: [{
            source_item_id: "11111111-1111-4111-8111-111111111111",
            source_url: "https://x.com/SquidRouter/status/123",
            text: "Prior official post.",
            published_at: "2026-07-21T00:00:00Z",
          }],
          style_reference_pack_hash: "a".repeat(32),
        }),
      },
    ), { params: { clientId: "squid" } } as never);

    assert.equal(response.status, 403);
    assert.deepEqual(await response.json(), {
      error: "style_references_automation_only",
    });
    assert.equal(fetched, false);
  });
});


test("article response guard requires the complete three-section contract", () => {
  assert.equal(isRailwayArticleResponse(GENERATED_ARTICLE), true);
  assert.equal(isRailwayArticleResponse({
    ...GENERATED_ARTICLE,
    sections: GENERATED_ARTICLE.sections.slice(0, 2),
  }), false);
  assert.equal(isRailwayArticleResponse({
    ...GENERATED_ARTICLE,
    channel_copy: { telegram: "누락" },
  }), false);
  assert.equal(isRailwayArticleResponse({
    ...GENERATED_ARTICLE,
    title: "   ",
  }), false);
  assert.equal(isRailwayArticleResponse({
    ...GENERATED_ARTICLE,
    duration_ms: Number.NaN,
  }), false);
});


test("an exact article retry returns the immutable catalog without Railway", async () => {
  const requestHash = articleRequestHash({
    clientId: "squid",
    sourceContent: PASTED_SOURCE,
    sourceType: "article",
    sourceUrl: "https://example.com/source",
  });
  let railwayCalls = 0;

  await withArticleEnvironment(async (input) => {
    const url = String(input);
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{
        workspace_id: WORKSPACE_ID,
        client_id: "squid",
        active: true,
      }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) {
      return Response.json({
        content_item_id: REQUEST_ID,
        content_version_id: VERSION_ID,
        client_id: "squid",
        content_kind: "article",
        status: "needs_review",
        title: GENERATED_ARTICLE.title,
        content: {
          request_hash: requestHash,
          lead: GENERATED_ARTICLE.lead,
          sections: GENERATED_ARTICLE.sections,
          key_takeaways: GENERATED_ARTICLE.key_takeaways,
          visuals: GENERATED_ARTICLE.visuals,
          source_map: GENERATED_ARTICLE.source_map,
          markdown: GENERATED_ARTICLE.markdown,
        },
        channel_copy: GENERATED_ARTICLE.channel_copy,
        generation_meta: {
          request_hash: requestHash,
          duration_ms: GENERATED_ARTICLE.duration_ms,
          mock_mode: false,
        },
        assets: [],
      });
    }
    if (url.includes("railway.example")) railwayCalls += 1;
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await articleHandler(new Request(
      "https://console.example/api/article/squid",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "Idempotency-Key": REQUEST_ID,
          cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue("article-studio-access-token")}`,
        },
        body: JSON.stringify({
          source_content: PASTED_SOURCE,
          source_type: "article",
          source_url: "https://example.com/source",
        }),
      },
    ), { params: { clientId: "squid" } } as never);

    assert.equal(response.status, 200, JSON.stringify(await response.clone().json()));
    assert.deepEqual(await response.json(), {
      ...GENERATED_ARTICLE,
      storage_backend: "supabase",
      content_item_id: REQUEST_ID,
      content_version_id: VERSION_ID,
      asset_ids: [],
      reused: true,
    });
    assert.equal(railwayCalls, 0);
  });
});
