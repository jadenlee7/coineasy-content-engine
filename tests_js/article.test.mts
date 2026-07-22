import assert from "node:assert/strict";
import test from "node:test";

import articleHandler, {
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
  source_map: [{ source_url: "https://example.com/source", applies_to: ["title"] }],
  channel_copy: { telegram: "텔레그램 문구", x: "X 문구입니다." },
  markdown: "# Squid가 라우팅 제품군을 업데이트했습니다\n",
  duration_ms: 1200,
};

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
    upstreamUrl = String(input);
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
    assert.deepEqual(await response.json(), GENERATED_ARTICLE);
  });
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
