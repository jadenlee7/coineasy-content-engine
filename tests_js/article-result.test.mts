import assert from "node:assert/strict";
import test from "node:test";

import articleResultHandler from "../netlify/functions/article-result.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const ACCESS_TOKEN = "article-result-studio-access-token";
const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const REQUEST_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";
const REQUEST_HASH = "a".repeat(64);

const STORED_ARTICLE = {
  content_item_id: REQUEST_ID,
  content_version_id: VERSION_ID,
  client_id: "squid",
  content_kind: "article",
  status: "needs_review",
  title: "Squid가 Canton 탐색을 더 쉽게 소개합니다",
  content: {
    request_hash: REQUEST_HASH,
    lead: "공식 게시물과 홈페이지에서 확인되는 내용만 정리합니다.",
    sections: [
      { id: "section-1", heading: "공식 메시지", body: "Squid로는 쉽다고 소개합니다." },
      { id: "section-2", heading: "지원 범위", body: "Canton은 지원 생태계 목록에 있습니다." },
      { id: "section-3", heading: "확인할 점", body: "구체적인 경로와 자산은 직접 확인해야 합니다." },
    ],
    key_takeaways: [
      "공식 메시지만 사용합니다.",
      "지원 목록을 확인합니다.",
      "구체적인 이용 조건은 추론하지 않습니다.",
    ],
    visuals: [],
    source_map: [{
      source_url: "https://x.com/SquidRouter/status/2081031728622178334",
      applies_to: ["title", "lead"],
    }],
    markdown: "# Squid가 Canton 탐색을 더 쉽게 소개합니다\n",
  },
  channel_copy: {
    telegram: "Squid 공식 게시물 내용을 확인해 보세요.",
    x: "Squid가 Canton 탐색을 쉽게 소개합니다.",
  },
  generation_meta: {
    request_hash: REQUEST_HASH,
    duration_ms: 28_074,
    mock_mode: false,
    brand_qa: { status: "pass", score: 100 },
  },
  assets: [],
};

async function withArticleResultEnvironment(
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
    if (originalNetlify) {
      Object.defineProperty(globalThis, "Netlify", originalNetlify);
    } else {
      Reflect.deleteProperty(globalThis, "Netlify");
    }
  }
}

function request(): Request {
  return new Request(
    `https://console.example/api/article-result/squid/${REQUEST_ID}`,
    {
      headers: {
        cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue(ACCESS_TOKEN)}`,
      },
    },
  );
}

test("article result polling reports a pending durable item without regenerating", async () => {
  let calls = 0;
  await withArticleResultEnvironment(async (input) => {
    calls += 1;
    assert.match(String(input), /\/rest\/v1\/rpc\/get_generated_content$/);
    return Response.json(null);
  }, async () => {
    const response = await articleResultHandler(request(), {
      params: { clientId: "squid", requestId: REQUEST_ID },
    } as never);
    assert.equal(response.status, 202);
    assert.deepEqual(await response.json(), {
      status: "generating",
      content_item_id: REQUEST_ID,
    });
    assert.equal(calls, 1);
  });
});

test("article result polling returns the immutable stored response", async () => {
  await withArticleResultEnvironment(async () => Response.json(STORED_ARTICLE), async () => {
    const response = await articleResultHandler(request(), {
      params: { clientId: "squid", requestId: REQUEST_ID },
    } as never);
    assert.equal(response.status, 200);
    const payload = await response.json();
    assert.equal(payload.content_item_id, REQUEST_ID);
    assert.equal(payload.content_version_id, VERSION_ID);
    assert.equal(payload.storage_backend, "supabase");
    assert.equal(payload.reused, true);
    assert.equal(payload.title, STORED_ARTICLE.title);
    assert.deepEqual(payload.brand_qa, { status: "pass", score: 100 });
  });
});

test("article result polling still requires Studio generation access", async () => {
  await withArticleResultEnvironment(async () => {
    throw new Error("catalog must not be queried");
  }, async () => {
    const response = await articleResultHandler(new Request(
      `https://console.example/api/article-result/squid/${REQUEST_ID}`,
    ), {
      params: { clientId: "squid", requestId: REQUEST_ID },
    } as never);
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { error: "studio_auth_required" });
  });
});
