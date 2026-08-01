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

const SQUID_ARTICLE_DETAIL = {
  ...ARTICLE_DETAIL,
  client_id: "squid",
  title: "Squid로 Canton Network를 탐색하는 방법",
  current_version: {
    ...ARTICLE_DETAIL.current_version,
    title: "Squid로 Canton Network를 탐색하는 방법",
    content: {
      ...ARTICLE_DETAIL.current_version.content,
      lead: "Canton으로 가는 공식 경로를 한국 이용자 관점에서 정리합니다.",
      sections: [
        { id: "section-1", heading: "Canton으로 가는 경로", body: "Squid 지원 생태계에서 Canton을 탐색할 수 있습니다." },
        { id: "section-2", heading: "공식 지원 상태 확인", body: "실행 전 공식 경로에서 지원 상태를 확인합니다." },
        { id: "section-3", heading: "한국 이용자가 볼 포인트", body: "경로와 네트워크 상태를 함께 확인합니다." },
      ],
      key_takeaways: [
        "Canton Network가 Squid 지원 생태계에 포함됩니다.",
        "공식 경로에서 지원 상태를 확인해야 합니다.",
        "실행 전 네트워크 상태를 함께 확인합니다.",
      ],
      source: { url: "https://www.squidrouter.com/blog/canton" },
    },
  },
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
    const heroSvg = await hero.text();
    assert.match(heroSvg, /ORIGINTRAIL KOREA \/ TRUST BRIEF/);
    assert.match(heroSvg, /id="Hero-OriginTrail-Knowledge-Graph"/);
    assert.match(heroSvg, /결제 레일의 역할/);
    assert.doesNotMatch(heroSvg, /AI 에이전트에 필요한 검증 가능한 메모리/);

    const inline = await articleVisualHandler(request("visual-2"), {
      params: { contentId: CONTENT_ID, visualId: "visual-2" },
      site: { url: "https://console.example" },
    } as never);
    assert.equal(inline.status, 200);
    assert.match(inline.headers.get("content-disposition") || "", /origintrail-article-visual-2-1200x675\.svg/);
    const svg = await inline.text();
    assert.match(svg, /공식 원문 기반 에디토리얼 비주얼/);
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

async function withSquidEnvironment(
  run: (requests: string[]) => Promise<void>,
  missingAsset = "",
): Promise<void> {
  const originalFetch = globalThis.fetch;
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  const requests: string[] = [];
  globalThis.fetch = async input => {
    const url = String(input);
    requests.push(url);
    if (url.endsWith("/rest/v1/rpc/get_content_library_item")) {
      return Response.json(SQUID_ARTICLE_DETAIL);
    }
    if (url.includes("/assets/brands/squid-")) {
      if (missingAsset && url.endsWith(missingAsset)) {
        return new Response("missing", { status: 404 });
      }
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
    await run(requests);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

test("Squid stored inline visuals fetch and embed every official world asset", async () => {
  await withSquidEnvironment(async requests => {
    const response = await articleVisualHandler(request("visual-1"), {
      params: { contentId: CONTENT_ID, visualId: "visual-1" },
      site: { url: "https://console.example" },
    } as never);

    assert.equal(response.status, 200);
    assert.deepEqual(requests.filter(url => url.includes("/assets/brands/")).sort(), [
      "https://console.example/assets/brands/squid-form-language-purple.png",
      "https://console.example/assets/brands/squid-light.png",
      "https://console.example/assets/brands/squid-squib-bubbles.png",
      "https://console.example/assets/brands/squid-squib-token-juggle.png",
    ].sort());
    const svg = await response.text();
    assert.match(svg, /id="Squid-Inline-Canvas"/);
    assert.match(svg, /id="Squid-Official-Form-Language-Inline"/);
    assert.match(svg, /id="Squid-Official-Bubbles-Inline"/);
    assert.match(svg, /id="Squid-Official-SQUIB-Inline"/);
    assert.match(svg, /CANTON × SQUID/);
    assert.doesNotMatch(svg, /id="Diagram-Panel"/);
    assert.equal((svg.match(/data:image\/png;base64,AQID/g) || []).length, 4);
  });
});

test("Squid stored inline visuals fail closed when an official asset is unavailable", async () => {
  await withSquidEnvironment(async () => {
    const response = await articleVisualHandler(request("visual-2"), {
      params: { contentId: CONTENT_ID, visualId: "visual-2" },
      site: { url: "https://console.example" },
    } as never);

    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), {
      error: "official_article_inline_assets_unavailable",
    });
  }, "squid-squib-bubbles.png");
});
