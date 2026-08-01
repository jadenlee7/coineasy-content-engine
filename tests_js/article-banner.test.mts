import assert from "node:assert/strict";
import test from "node:test";

import {
  buildArticleBannerSvg,
  buildArticleInlineVisualSvg,
} from "../netlify/functions/_shared/article-banner-svg.mts";
import {
  deriveArticleVisuals,
} from "../netlify/functions/_shared/article-visual-plan.mts";
import articleBannerHandler from "../netlify/functions/article-banner.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";
import {
  articleHeroLogoVariant,
} from "../netlify/functions/_shared/official-brand-assets.mts";

const ACCESS_TOKEN = "article-banner-studio-access-token";
const SQUID_HERO_ASSETS = {
  formLanguage: "data:image/png;base64,Zm9ybQ==",
  squib: "data:image/png;base64,c3F1aWI=",
  bubbles: "data:image/png;base64,YnViYmxlcw==",
};

test("selects a contrast-safe official logo for each hero canvas", () => {
  assert.equal(articleHeroLogoVariant("yellow"), "light");
  assert.equal(articleHeroLogoVariant("squid"), "light");
  assert.equal(articleHeroLogoVariant("origintrail"), "dark");
  assert.equal(articleHeroLogoVariant("babylon"), "dark");
});

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

test("builds a distinct 1200 by 630 Squid editorial world", () => {
  const svg = buildArticleBannerSvg("squid", {
    title: "스퀴드가 여는 새로운 크로스체인 유동성 경험",
    lead: "공식 발표를 바탕으로 핵심 변화와 이용자 영향을 짚어봅니다.",
    sourceUrl: "https://www.squidrouter.com/blog/update",
    date: "2026.07.27",
    motif: "flow",
  }, "data:image/png;base64,AQ==", SQUID_HERO_ASSETS);

  assert.match(svg, /width="1200" height="630" viewBox="0 0 1200 630"/);
  assert.match(svg, /id="Article-Title-Line-1"/);
  assert.match(svg, /id="Article-Lead-Line-1"/);
  assert.match(svg, /id="Brand-Official-Logo"/);
  assert.match(svg, /SQUIDROUTER\.COM/);
  assert.match(svg, /#EFFF5A/);
  assert.match(svg, /fill="#E6CCFC"/);
  assert.match(svg, /Bagoss Condensed/);
  assert.match(svg, /id="Editorial-Hero-v2"/);
  assert.match(svg, /id="Squid-Hero-Composition-flow"/);
  assert.match(svg, /id="Squid-Official-Form-Language-Hero"/);
  assert.match(svg, /id="Squid-Official-Bubbles-Hero"/);
  assert.match(svg, /id="Squid-Official-SQUIB-Hero"/);
  assert.match(svg, /CROSS-CHAIN × SQUID/);
  assert.match(svg, /id="Squid-Lime-Divider"/);
  assert.doesNotMatch(svg, /SQUID KOREA \/ NOTE 01/);
  assert.doesNotMatch(svg, /CROSS-CHAIN, MADE HUMAN/);
  assert.doesNotMatch(svg, /id="Editorial-Grid"/);
  assert.doesNotMatch(svg, /id="Diagram-Panel"/);
  assert.doesNotMatch(svg, /x="48" y="552" width="1104"/);
  assert.doesNotMatch(svg, /id="Visual-Panel"/);
  assert.doesNotMatch(svg, /foreignObject/);
});

test("fails closed instead of drawing a generic Squid character", () => {
  assert.throws(() => buildArticleBannerSvg("squid", {
    title: "공식 애셋이 필요한 Squid 배너",
    lead: "필수 애셋이 없으면 생성하지 않습니다.",
  }, "data:image/png;base64,AQ=="), /official_squid_hero_assets_required/);
});

test("shrinks and wraps a long unbroken Korean Squid hero before the SQUIB safe area", () => {
  const title = "한국사용자에게정확하고자연스럽게전달하는업데이트입니다";
  const svg = buildArticleBannerSvg("squid", {
    title,
    lead: "공식 원문의 핵심만 짧고 자연스럽게 전합니다.",
    motif: "flow",
  }, "data:image/png;base64,AQ==", SQUID_HERO_ASSETS);

  const lines = [...svg.matchAll(/id="Article-Title-Line-[0-9]+"[^>]*>([^<]*)<\/text>/g)]
    .map(match => match[1]);
  assert.deepEqual(lines, ["한국사용자에게정확하고자연스", "럽게전달하는업데이트입니다"]);
  assert.doesNotMatch(lines.join(""), /…/);
  assert.match(svg, /font-size="56"/);
  assert.match(svg, /id="Squid-Official-SQUIB-Hero" x="810"/);
});

test("uses client-specific hero structures and exact reviewed brand tokens", () => {
  const input = {
    title: "한국 시장을 위한 핵심 업데이트",
    lead: "공식 발표에서 확인된 내용을 간결하게 정리합니다.",
    sourceUrl: "https://x.com/example/status/123",
    date: "2026.07.31",
  };
  const logo = "data:image/png;base64,AQ==";

  const yellow = buildArticleBannerSvg("yellow", input, logo);
  assert.match(yellow, /id="Hero-Yellow-Studio"/);
  assert.match(yellow, /fill="#FDDA16"/);
  assert.match(yellow, /KOREA MARKET INTELLIGENCE/);

  const squid = buildArticleBannerSvg("squid", input, logo, SQUID_HERO_ASSETS);
  assert.match(squid, /id="Squid-Hero-Composition-signal"/);
  assert.match(squid, /fill="#E6CCFC"/);
  assert.match(squid, /SQUID × KOREA/);

  const originTrail = buildArticleBannerSvg("origintrail", input, logo);
  assert.match(originTrail, /id="Hero-OriginTrail-Knowledge-Graph"/);
  assert.match(originTrail, /fill="#0C2246"/);
  assert.match(originTrail, /fill="#6344DF"/);

  const babylon = buildArticleBannerSvg("babylon", input, logo);
  assert.match(babylon, /id="Hero-Babylon-Proof-Panel"/);
  assert.match(babylon, /fill="#12495E"/);
  assert.match(babylon, /fill="#CE6533"/);
});

test("derives two source-locked inline visuals and renders an editable 16:9 figure", () => {
  const visuals = deriveArticleVisuals({
    title: "에이전트에 필요한 검증 가능한 메모리",
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
  });
  assert.equal(visuals.length, 2);
  assert.equal(visuals[0].after_section_id, "section-1");
  assert.equal(visuals[1].after_section_id, "section-3");

  const svg = buildArticleInlineVisualSvg("origintrail", {
    visual: {
      ...visuals[1],
      motif: "layers",
      eyebrow: "SMART MANUFACTURING",
      points: [
        "Kamstrup, 탈중앙 지식 그래프(DKG) 온보딩 진행",
        "제조 밸류체인의 신뢰할 수 있는 AI 기반",
        "산업 데이터 기반의 새로운 서비스",
      ],
    },
    sourceUrl: "https://x.com/origin_trail/status/123",
    date: "2026.07.27",
  }, "data:image/png;base64,AQ==");
  assert.match(svg, /width="1200" height="675"/);
  assert.match(svg, /id="Visual-Headline-Line-1"/);
  assert.match(svg, /id="Visual-Point-1"/);
  assert.match(svg, /id="Visual-Point-1-Line-2"/);
  assert.match(svg, /font-size="11" font-weight="900" letter-spacing="1.2">SMART MANUFACTURING/);
  assert.match(svg, /id="Inline-visual-2-Motif-layers"/);
  assert.match(svg, /공식 원문 기반 에디토리얼 비주얼/);
  assert.match(svg, /id="Brand-Atmosphere-OriginTrail"/);
  assert.doesNotMatch(svg, /foreignObject/);
});

test("renders Squid inline visuals as sparse official worlds, not generic diagram panels", () => {
  const input = {
    visual: {
      id: "visual-1" as const,
      after_section_id: "section-1",
      role: "overview" as const,
      motif: "flow" as const,
      eyebrow: "핵심 맥락",
      headline: "Canton으로 가는 길, Squid로 더 쉽게",
      caption: "지원 생태계를 이동하는 흐름을 한국 이용자 관점에서 짚습니다.",
      points: [
        "Canton Network가 Squid 지원 생태계에 포함됩니다.",
        "공식 경로에서 지원 상태를 먼저 확인하세요.",
        "세 번째 문장은 카드로 쌓이지 않아야 합니다.",
      ],
    },
    sourceUrl: "https://www.squidrouter.com/blog/canton",
    date: "2026.08.01",
  };
  const flow = buildArticleInlineVisualSvg(
    "squid",
    input,
    "data:image/png;base64,AQ==",
    SQUID_HERO_ASSETS,
  );
  const network = buildArticleInlineVisualSvg(
    "squid",
    { ...input, visual: { ...input.visual, motif: "network" } },
    "data:image/png;base64,AQ==",
    SQUID_HERO_ASSETS,
  );

  assert.match(flow, /width="1200" height="675"/);
  assert.match(flow, /id="Squid-Inline-Canvas"/);
  assert.match(flow, /id="Squid-Inline-Composition-flow"/);
  assert.match(network, /id="Squid-Inline-Composition-network"/);
  assert.notEqual(flow, network);
  assert.match(flow, /id="Squid-Official-Form-Language-Inline"/);
  assert.match(flow, /id="Squid-Official-Bubbles-Inline"/);
  assert.match(flow, /id="Squid-Official-SQUIB-Inline"/);
  assert.match(flow, /CANTON × SQUID/);
  assert.match(flow, /id="Squid-Evidence-1"/);
  assert.match(flow, /id="Squid-Evidence-2"/);
  assert.doesNotMatch(flow, /id="Squid-Evidence-3"/);
  assert.doesNotMatch(flow, /id="Grid"/);
  assert.doesNotMatch(flow, /id="Accent-Rail"/);
  assert.doesNotMatch(flow, /id="Diagram-Panel"/);
  assert.doesNotMatch(flow, /id="Visual-Point-1"/);
  assert.doesNotMatch(flow, /foreignObject/);
});

test("fails closed when a Squid inline visual is missing official assets", () => {
  assert.throws(() => buildArticleInlineVisualSvg("squid", {
    visual: {
      id: "visual-1",
      after_section_id: "section-1",
      role: "overview",
      motif: "flow",
      eyebrow: "핵심 맥락",
      headline: "Canton으로 가는 경로",
      caption: "공식 애셋이 없으면 생성하지 않습니다.",
      points: ["공식 소스에 고정합니다."],
    },
  }, "data:image/png;base64,AQ=="), /official_squid_inline_assets_required/);
});

test("escapes banner copy and truncates long headlines to three editable lines", () => {
  const svg = buildArticleBannerSvg("yellow", {
    title: `<새 소식> ${"아주 긴 제목 ".repeat(30)}`,
    lead: "A & B를 함께 설명합니다.",
  }, "data:image/svg+xml;base64,AQ==");

  assert.match(svg, /&lt;새 소식&gt;/);
  assert.match(svg, /A &amp; B/);
  assert.equal((svg.match(/id="Article-Title-Line-/g) || []).length, 3);
  assert.match(svg, /…/);
  assert.match(svg, /Brand-Official-Logo/);
  assert.doesNotMatch(svg, /Brand-Logo-Fallback/);
});

test("keeps the official Babylon symbol separate from its Korean market name", () => {
  const svg = buildArticleBannerSvg("babylon", {
    title: "비트코인 네이티브 보안의 다음 단계",
    lead: "공식 발표의 제품 상태와 작동 방식을 정확히 설명합니다.",
  }, "data:image/png;base64,AQ==");

  assert.match(svg, /id="Brand-Official-Lockup"/);
  assert.match(svg, /id="Brand-Official-Logo"/);
  assert.match(svg, /id="Brand-Local-Market-Name"/);
  assert.match(svg, />Babylon Korea<\/text>/);
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

test("article banner endpoint fails closed when the official logo is unavailable", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("missing", { status: 404 });
  try {
    await withStudioEnvironment(async () => {
      const response = await articleBannerHandler(new Request(
        "https://console.example/api/article-banner/yellow",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue(ACCESS_TOKEN)}`,
          },
          body: JSON.stringify({
            title: "공식 로고가 필요한 배너",
            lead: "공식 자산을 불러오지 못하면 배너 생성을 중단합니다.",
          }),
        },
      ), {
        params: { clientId: "yellow" },
        site: { url: "https://console.example" },
      } as never);

      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), { error: "official_logo_unavailable" });
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Squid article banner embeds every required official hero asset", async () => {
  const originalFetch = globalThis.fetch;
  const requests: string[] = [];
  globalThis.fetch = async input => {
    const url = String(input);
    requests.push(url);
    return new Response(new Uint8Array([1, 2, 3]), {
      status: 200,
      headers: { "content-type": "image/png", "content-length": "3" },
    });
  };
  try {
    await withStudioEnvironment(async () => {
      const response = await articleBannerHandler(new Request(
        "https://console.example/api/article-banner/squid",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue(ACCESS_TOKEN)}`,
          },
          body: JSON.stringify({
            title: "Squid로 Canton Network를 간편하게 탐색하세요",
            lead: "공식 Squid 애셋과 함께 한국 이용자가 이해하기 쉽게 정리합니다.",
          }),
        },
      ), {
        params: { clientId: "squid" },
        site: { url: "https://console.example" },
      } as never);

      assert.equal(response.status, 200);
      assert.deepEqual(requests.sort(), [
        "https://console.example/assets/brands/squid-form-language-purple.png",
        "https://console.example/assets/brands/squid-light.png",
        "https://console.example/assets/brands/squid-squib-bubbles.png",
        "https://console.example/assets/brands/squid-squib-token-juggle.png",
      ].sort());
      const svg = await response.text();
      assert.match(svg, /id="Squid-Official-Form-Language-Hero"/);
      assert.match(svg, /id="Squid-Official-Bubbles-Hero"/);
      assert.match(svg, /id="Squid-Official-SQUIB-Hero"/);
      assert.match(svg, /CANTON × SQUID/);
      assert.equal((svg.match(/data:image\/png;base64,AQID/g) || []).length, 4);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
