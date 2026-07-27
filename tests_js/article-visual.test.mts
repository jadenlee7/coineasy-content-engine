import assert from "node:assert/strict";
import test from "node:test";

import articleVisualHandler from "../netlify/functions/article-visual.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const ACCESS_TOKEN = "article-visual-studio-access-token";
const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const CONTENT_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";

const ARTICLE_DETAIL = {
  content_item_id: CONTENT_ID,
  content_version_id: VERSION_ID,
  client_id: "origintrail",
  content_kind: "article",
  title: "AI 에이전트에 필요한 검증 가능한 메모리",
  status: "needs_review",
  created_at: "2026-07-27T17:51:02.646Z",
  updated_at: "2026-07-27T17:51:02.646Z",
  current_version: {
    content_version_id: VERSION_ID,
    version_number: 1,
    prompt_version: "article@3",
    locale: "ko-KR",
    title: "AI 에이전트에 필요한 검증 가능한 메모리",
    content: {
      lead: "결제 레일을 넘어 검증 가능한 컨텍스트가 필요합니다.",
      sections: [
        { id: "section-1", heading: "결제 레일의 역할", body: "결제 레일은 값을 이동합니다." },
        { id: "section-2", heading: "검증 가능한 컨텍스트", body: "컨텍스트는 출처에 고정됩니다." },
        { id: "section-3", heading: "세 가지 메모리 계층", body: "작업, 공유, 검증 가능한 메모리로 구성됩니다." },
      ],
      key_takeaways: [
        "에이전트에는 결제 레일이 필요합니다.",
        "검증 가능한 컨텍스트가 필요합니다.",
        "메모리는 세 계층으로 구성됩니다.",
      ],
      source: { url: "https://x.com/origin_trail/status/123" },
    },
    channel_copy: {},
    deliverables: {},
    qa: {},
    generation_meta: {},
    created_at: "2026-07-27T17:51:02.646Z",
  },
  assets: [],
  figma_links: [],
};

async function withEnvironment(run: () => Promise<void>): Promise<void> {
  const originalFetch = globalThis.fetch;
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  globalThis.fetch = async input => {
    const url = String(input);
    if (url.endsWith("/rest/v1/rpc/get_content_library_item")) {
      return Response.json(ARTICLE_DETAIL);
    }
    if (url.endsWith("/assets/brands/origintrail-dark.png")) {
      return new Response(new Uint8Array([1, 2, 3]), {
        status: 200,
        headers: { "content-type": "image/png", "content-length": "3" },
      });
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: {
      env: {
        get(name: string): string | undefined {
          if (name === "STUDIO_ACCESS_TOKEN") return ACCESS_TOKEN;
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
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

function request(visualId: string): Request {
  return new Request(
    `https://console.example/api/article-visual/${CONTENT_ID}/${visualId}`,
    {
      headers: {
        cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue(ACCESS_TOKEN)}`,
      },
    },
  );
}

test("renders stored article hero and inline visuals without a new model call", async () => {
  await withEnvironment(async () => {
    const hero = await articleVisualHandler(request("hero"), {
      params: { contentId: CONTENT_ID, visualId: "hero" },
      site: { url: "https://console.example" },
    } as never);
    assert.equal(hero.status, 200);
    assert.equal(hero.headers.get("content-type"), "image/svg+xml; charset=utf-8");
    assert.match(await hero.text(), /ARTICLE \/ INSIGHT/);

    const inline = await articleVisualHandler(request("visual-2"), {
      params: { contentId: CONTENT_ID, visualId: "visual-2" },
      site: { url: "https://console.example" },
    } as never);
    assert.equal(inline.status, 200);
    assert.match(inline.headers.get("content-disposition") || "", /origintrail-article-visual-2-1200x675\.svg/);
    const svg = await inline.text();
    assert.match(svg, /SOURCE-LOCKED EDITORIAL VISUAL/);
    assert.match(svg, /세 가지 메모리 계층/);
  });
});

test("rejects unknown visual ids before reading storage", async () => {
  await withEnvironment(async () => {
    const response = await articleVisualHandler(request("visual-9"), {
      params: { contentId: CONTENT_ID, visualId: "visual-9" },
      site: { url: "https://console.example" },
    } as never);
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), { error: "invalid_visual_id" });
  });
});
