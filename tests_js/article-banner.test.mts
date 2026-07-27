import assert from "node:assert/strict";
import test from "node:test";

import {
  buildArticleBannerSvg,
} from "../netlify/functions/_shared/article-banner-svg.mts";
import articleBannerHandler from "../netlify/functions/article-banner.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const ACCESS_TOKEN = "article-banner-studio-access-token";

async function withStudioEnvironment(run: () => Promise<void>): Promise<void> {
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: {
      env: {
        get(name: string): string | undefined {
          return name === "STUDIO_ACCESS_TOKEN" ? ACCESS_TOKEN : undefined;
        },
      },
    },
  });
  try {
    await run();
  } finally {
    if (originalNetlify) {
      Object.defineProperty(globalThis, "Netlify", originalNetlify);
    } else {
      Reflect.deleteProperty(globalThis, "Netlify");
    }
  }
}

test("builds a layered 1200 by 630 Figma-editable article banner", () => {
  const svg = buildArticleBannerSvg("squid", {
    title: "스퀴드가 여는 새로운 크로스체인 유동성 경험",
    lead: "공식 발표를 바탕으로 핵심 변화와 이용자 영향을 짚어봅니다.",
    sourceUrl: "https://www.squidrouter.com/blog/update",
    date: "2026.07.27",
  }, "data:image/png;base64,AQ==");

  assert.match(svg, /width="1200" height="630" viewBox="0 0 1200 630"/);
  assert.match(svg, /id="Article-Title-Line-1"/);
  assert.match(svg, /id="Article-Lead-Line-1"/);
  assert.match(svg, /id="Brand-Logo"/);
  assert.match(svg, /SQUIDROUTER\.COM/);
  assert.match(svg, /#E6FA36/);
  assert.match(svg, /Bagoss Condensed/);
  assert.doesNotMatch(svg, /foreignObject/);
});

test("escapes banner copy and truncates long headlines to three editable lines", () => {
  const svg = buildArticleBannerSvg("yellow", {
    title: `<새 소식> ${"아주 긴 제목 ".repeat(30)}`,
    lead: "A & B를 함께 설명합니다.",
  });

  assert.match(svg, /&lt;새 소식&gt;/);
  assert.match(svg, /A &amp; B/);
  assert.equal((svg.match(/id="Article-Title-Line-/g) || []).length, 3);
  assert.match(svg, /…/);
  assert.match(svg, /Brand-Logo-Fallback/);
});

test("article banner endpoint embeds the client logo behind a Studio session", async () => {
  const originalFetch = globalThis.fetch;
  let logoRequest = "";
  globalThis.fetch = async input => {
    logoRequest = String(input);
    return new Response(new Uint8Array([1, 2, 3]), {
      status: 200,
      headers: { "content-type": "image/png", "content-length": "3" },
    });
  };
  try {
    await withStudioEnvironment(async () => {
      const response = await articleBannerHandler(new Request(
        "https://console.example/api/article-banner/babylon",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue(ACCESS_TOKEN)}`,
          },
          body: JSON.stringify({
            title: "비트코인 스테이킹의 다음 단계",
            lead: "바빌론의 공식 발표에서 확인한 핵심 내용을 정리합니다.",
            source_url: "https://babylonlabs.io/blog/update",
            date: "2026.07.27",
          }),
        },
      ), {
        params: { clientId: "babylon" },
        site: { url: "https://console.example" },
      } as never);

      assert.equal(response.status, 200);
      assert.equal(response.headers.get("content-type"), "image/svg+xml; charset=utf-8");
      assert.match(response.headers.get("content-disposition") || "", /babylon-article-banner-1200x630\.svg/);
      assert.equal(logoRequest, "https://console.example/assets/brands/babylon-dark.png");
      const svg = await response.text();
      assert.match(svg, /data:image\/png;base64,AQID/);
      assert.match(svg, /비트코인 스테이킹의 다음 단계/);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("article banner endpoint rejects malformed copy and unsafe source URLs", async () => {
  await withStudioEnvironment(async () => {
    const request = (body: Record<string, unknown>) => articleBannerHandler(new Request(
      "https://console.example/api/article-banner/yellow",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue(ACCESS_TOKEN)}`,
        },
        body: JSON.stringify(body),
      },
    ), {
      params: { clientId: "yellow" },
      site: { url: "https://console.example" },
    } as never);

    const missingTitle = await request({ title: "", lead: "충분한 리드" });
    assert.equal(missingTitle.status, 400);
    assert.deepEqual(await missingTitle.json(), { error: "invalid_title" });

    const unsafeSource = await request({
      title: "정상 제목",
      lead: "충분한 리드",
      source_url: "javascript:alert(1)",
    });
    assert.equal(unsafeSource.status, 400);
    assert.deepEqual(await unsafeSource.json(), { error: "invalid_source_url" });
  });
});
