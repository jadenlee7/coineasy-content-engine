import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  buildEditableSvg,
  effectiveEditableTemplateStyle,
  escapeXml,
  wrapSvgText,
} from "../netlify/functions/_shared/editable-svg.mts";
import {
  clientScopedSourceVisualFile,
  needsCleanedSquidVisual,
} from "../netlify/functions/editable-card.mts";
import { normalizedSourceVisualFile } from "../netlify/functions/news-card.mts";

const SPEC = {
  label: "인사이트",
  date: "2026.07.19",
  headline: "Yellow가 오프체인 클리어링으로 거래 효율을 높입니다",
  body_lines: ["첫 번째 핵심 내용", "두 번째 핵심 내용", "세 번째 핵심 내용"],
  source_url: "https://x.com/Yellow/status/123?x=1&y=2",
  theme: "dark",
};

const SQUID_ASSETS = {
  logoDark: "data:image/png;base64,ZGFyay1sb2dv",
  logoLight: "data:image/png;base64,bG9nbw==",
  squidFormLanguage: "data:image/png;base64,Zm9ybQ==",
  squidSquib: "data:image/png;base64,c3F1aWI=",
  squidBubbles: "data:image/png;base64,YnViYmxlcw==",
};

const SQUID_GENERATED_PROFILE = {
  template_version: "squid-generated-gtm@5",
  visual_design_profile_id: "squid/full-bleed-character-type",
  visual_design_profile_version: 2,
} as const;

function assertTransparentSquidCaptions(
  svg: string,
  regionCount: number,
  expectedTextColors: string[] = Array.from({ length: regionCount }, () => "#FFFFFF"),
): void {
  assert.equal(expectedTextColors.length, regionCount);
  assert.doesNotMatch(svg, /Source-Text-Cover|Korean-Subtitle-Scrim|fill="#100D16"\/>/);
  for (let index = 1; index <= regionCount; index += 1) {
    assert.match(
      svg,
      new RegExp(
        `<g id="Korean-Translation-Region-${index}"><text id="Korean-Translation-Region-${index}-Text-Line-1"[^>]+fill="${expectedTextColors[index - 1]}"[^>]+stroke="#100D16"[^>]+stroke-opacity="0\\.76"[^>]+stroke-width="1"[^>]+paint-order="stroke fill"`,
      ),
    );
  }
}

function assertNoSquidTranslation(svg: string): void {
  assert.match(svg, /id="Korean-Translation-Layer"><\/g>/);
  assert.doesNotMatch(svg, /Source-Text-Cover/);
}

test("escapes XML content and wraps long editable text", () => {
  assert.equal(escapeXml('<Yellow & "Squid">'), "&lt;Yellow &amp; &quot;Squid&quot;&gt;");
  const lines = wrapSvgText("아주 긴 한국어 헤드라인을 여러 줄로 안전하게 나눕니다", 12, 3);
  assert.ok(lines.length >= 2);
  assert.ok(lines.length <= 3);
});

test("normalizes only the exact Railway cleaned visual path", () => {
  const absolutePath = "/app/output/squid/news_1784567890/source_visual_cleaned.jpg";
  const normalized = "squid/news_1784567890/source_visual_cleaned.jpg";
  assert.equal(normalizedSourceVisualFile(absolutePath, "squid"), normalized);
  assert.equal(normalizedSourceVisualFile(normalized, "squid"), normalized);
  assert.equal(normalizedSourceVisualFile("squid/news_1784567890/news_card_remix.png", "squid"), null);
  assert.equal(normalizedSourceVisualFile("yellow/news_1784567890/source_visual_cleaned.jpg", "squid"), null);
  assert.equal(normalizedSourceVisualFile("squid/news_1784567890/../source_visual_cleaned.jpg", "squid"), null);
});

test("allows the editable endpoint to fetch only the same-client cleaned visual", () => {
  const normalized = "squid/news_1784567890/source_visual_cleaned.jpg";
  assert.equal(clientScopedSourceVisualFile(normalized, "squid"), normalized);
  assert.equal(clientScopedSourceVisualFile(`/app/output/${normalized}`, "squid"), "");
  assert.equal(clientScopedSourceVisualFile("yellow/news_1784567890/source_visual_cleaned.jpg", "squid"), "");
  assert.equal(clientScopedSourceVisualFile("squid/news_latest/source_visual_cleaned.jpg", "squid"), "");
  assert.equal(clientScopedSourceVisualFile("squid/news_1784567890/source_visual_cleaned.png", "squid"), "");
});

test("requires the cleaned source only for a translated Squid remix", () => {
  const translatedSpec = { source_text_visible: true, translation_regions: [{ text: "번역" }] };
  assert.equal(needsCleanedSquidVisual("squid", "remix", translatedSpec), true);
  assert.equal(needsCleanedSquidVisual("squid", "classic", translatedSpec), false);
  assert.equal(needsCleanedSquidVisual("yellow", "remix", translatedSpec), false);
  assert.equal(needsCleanedSquidVisual("squid", "remix", { source_text_visible: false }), false);
});

test("creates the approved Yellow classic SVG as native Figma layers", () => {
  const svg = buildEditableSvg("yellow", "classic", SPEC, {
    logoLight: "data:image/svg+xml;base64,PHN2Zy8+",
  });
  assert.match(svg, /<svg[^>]+width="1080"[^>]+height="1080"/);
  assert.match(svg, /id="Canvas-Background"[^>]+fill="#F7F0E2"/);
  assert.match(svg, /id="Official-Logo-Tile"/);
  assert.match(svg, /id="Logo-Tile-Background"[^>]+fill="#FDDA16"/);
  assert.match(svg, /id="Brand-Logo"/);
  assert.match(svg, /id="Headline-Line-1"/);
  assert.match(svg, /id="Body-Line-1"/);
  assert.match(svg, /id="Accent-Line"/);
  assert.match(svg, /id="Source-URL"/);
  assert.match(svg, /x=1&amp;y=2/);
  assert.doesNotMatch(svg, /Main-Card-Background|Body-Item-1-Background|Bullet/);
  assert.doesNotMatch(svg, /foreignObject|<style/);
});

test("keeps the approved Yellow headline hierarchy in editable SVG", () => {
  const headline = "가".repeat(24);
  const svg = buildEditableSvg("yellow", "classic", {
    ...SPEC,
    headline,
  }, {
    logoLight: "data:image/svg+xml;base64,PHN2Zy8+",
  });
  const rendered = [...svg.matchAll(/id="Headline-Line-[0-9]+"[^>]*>([^<]*)<\/text>/g)]
    .map(match => match[1])
    .join("");
  assert.equal(rendered, headline);
  assert.match(svg, /id="Headline-Line-1"[^>]+font-size="100"/);
  assert.doesNotMatch(rendered, /…/);
});

test("creates the Figma-aligned Squid classic with official layered assets", () => {
  const svg = buildEditableSvg("squid", "classic", {
    ...SPEC,
    label: "CANTON × SQUID",
    headline: "Canton, 아직 안 가봤나요?",
    body_lines: ["Squid로는 쉬워요", "Squid가 지원하는 생태계에 Canton도 있어요"],
  }, {
    logoLight: "data:image/png;base64,bG9nbw==",
    squidFormLanguage: "data:image/png;base64,Zm9ybQ==",
    squidSquib: "data:image/png;base64,c3F1aWI=",
    squidBubbles: "data:image/png;base64,YnViYmxlcw==",
  });

  assert.match(svg, /id="Squid-Figma-Daily-News"/);
  assert.match(svg, /fill="#E6CCFC"/);
  assert.match(svg, /id="Squid-Official-Bubbles"/);
  assert.match(svg, /id="Squid-Official-SQUIB"/);
  assert.match(svg, /id="Lime-Divider"[^>]+width="320"[^>]+height="8"/);
  assert.match(svg, />Canton, 아직 안<\/text>/);
  assert.match(svg, />가봤나요\?<\/text>/);
  assert.match(svg, /Squid로는 쉬워요/);
  assert.match(svg, /COINEASY \/ KOREA/);
  assert.doesNotMatch(svg, /Main-Card-Background|Body-Item-1-Background|Bullet/);
});

test("keeps field-absent Squid classic byte-identical and rejects malformed generated replay", () => {
  const spec = {
    ...SPEC,
    label: "CANTON × SQUID",
    headline: "Canton, 아직 안 가봤나요?",
    body_lines: ["Squid로는 쉬워요", "Squid가 지원하는 생태계에 Canton도 있어요"],
  };
  const legacySvg = buildEditableSvg("squid", "classic", spec, SQUID_ASSETS);
  assert.throws(
    () => buildEditableSvg("squid", "classic", {
      ...spec,
      render_strategy: "generated_gtm",
    }, SQUID_ASSETS),
    /unsupported_squid_generated_profile/,
  );
  assert.equal(
    createHash("sha256").update(legacySvg).digest("hex"),
    "d10717e072ea950a9fde3623a25df3242ccea6478965c55dc6871bb6a1961af5",
  );
  assert.match(legacySvg, /COINEASY \/ KOREA/);
});

test("renders the four approved generated Squid creative families without publisher chrome", () => {
  const familyContracts = {
    editorial_big_type: {
      root: "Squid-Generated-Editorial-Big-Type",
      headline: "체인 사이의 경계를 넘다",
      expectedHeadlineLines: ["체인 사이의", "경계를 넘다"],
      hasMetric: false,
    },
    milestone_metric: {
      root: "Squid-Generated-Milestone-Metric",
      headline: "하나의 흐름으로 이어진 기록",
      expectedHeadlineLines: ["하나의 흐름으로", "이어진 기록"],
      hasMetric: true,
    },
    status_progress: {
      root: "Squid-Generated-Status-Progress",
      headline: "새로운 단계가 열렸어요",
      expectedHeadlineLines: ["새로운 단계가", "열렸어요"],
      hasMetric: false,
    },
    product_proof: {
      root: "Squid-Generated-Product-Proof",
      headline: "한 번의 경로로 더 간단하게",
      expectedHeadlineLines: ["한 번의 경로로", "더 간단하게"],
      hasMetric: false,
    },
  } as const;

  for (const [creativeFamily, contract] of Object.entries(familyContracts)) {
    const svg = buildEditableSvg("squid", "classic", {
      ...SPEC,
      label: creativeFamily === "milestone_metric" ? "MILESTONE" : "SQUID UPDATE",
      headline: contract.headline,
      visual_metric: creativeFamily === "milestone_metric" ? "5M" : undefined,
      body_lines: ["공식 원문에서 확인한 소식이에요", "한국 사용자에게 자연스럽게 전해요"],
      render_strategy: "generated_gtm",
      creative_family: creativeFamily,
      ...SQUID_GENERATED_PROFILE,
    }, SQUID_ASSETS);

    assert.match(svg, new RegExp(`id="${contract.root}"`));
    assert.match(svg, new RegExp(`data-creative-family="${creativeFamily}"`));
    assert.match(svg, /id="Canvas-Background"[^>]+fill="url\(#Squid-Base-Lavender\)"/);
    assert.match(svg, /#BC8EE4/);
    assert.match(svg, /id="Full-Bleed-Lavender-Halo"[^>]+url\(#Squid-Full-Bleed-Lavender-Halo\)/);
    assert.match(svg, /id="Lower-White-Bloom"[^>]+url\(#Squid-Lower-White-Bloom\)/);
    assert.doesNotMatch(svg, /White-Oval|Stage-Word-Bottom/);
    assert.match(svg, /id="Stage-Word-Top"[^>]+x="180" y="210"[^>]+scale\(1\.22 1\)[^>]+font-size="224"[^>]*>Squid<\/text>/);
    assert.match(svg, /id="Squid-Official-Form-Language" x="-340" y="-360" width="1760" height="1760"[^>]+opacity="\.08"[^>]+rotate\(74 540 520\)/);
    assert.doesNotMatch(svg, /id="Squid-Official-Bubbles"/);
    assert.match(svg, /id="Squid-Official-SQUIB" x="-330" y="-285" width="1500" height="1500"/);
    assert.match(svg, /id="Headline-Line-1"[^>]+x="540" y="840"[^>]+text-anchor="middle"[^>]+font-family="Bagoss Condensed, Pretendard, sans-serif"[^>]+font-weight="900"/);
    assert.doesNotMatch(svg, /Brand-Logo|Eyebrow|Lime-Divider|Support-Line|Public-Source-Metadata|Source-URL|id="Date"/);
    if (contract.hasMetric) {
      assert.match(svg, /id="Metric-Line-1"[^>]+x="1020" y="360"[^>]+text-anchor="end"[^>]+font-size="184"[^>]*>5M<\/text>/);
    } else {
      assert.doesNotMatch(svg, /id="Metric"/);
    }
    const assetLayerIds = [
      "Squid-Official-Form-Language",
      "Squid-Official-SQUIB",
    ];
    const assetLayerPositions = assetLayerIds.map((id) => svg.indexOf(`id="${id}"`));
    assert.ok(assetLayerPositions.every((position) => position >= 0));
    assert.deepEqual(assetLayerPositions, [...assetLayerPositions].sort((left, right) => left - right));
    const renderedHeadlineLines = [...svg.matchAll(/id="Headline-Line-[0-9]+"[^>]*>([^<]+)<\/text>/g)]
      .map((match) => match[1]);
    assert.deepEqual(renderedHeadlineLines, contract.expectedHeadlineLines);
    assert.doesNotMatch(svg, /CoinEasy|COINEASY|KOREA/);
    assert.doesNotMatch(svg, /id="[^"]*(?:Card|Panel|Button|Dashboard|CTA)/);
    assert.doesNotMatch(svg, /foreignObject|<style/);
  }
});

test("keeps a dense Squid headline complete inside the cropped display stack", () => {
  const headline = "한국 이용자를 위한 새로운 연결 소식";
  const svg = buildEditableSvg("squid", "classic", {
    ...SPEC,
    headline,
    body_lines: ["이 문장은 배너에 노출되지 않아요"],
    render_strategy: "generated_gtm",
    creative_family: "editorial_big_type",
    ...SQUID_GENERATED_PROFILE,
  }, SQUID_ASSETS);

  assert.doesNotMatch(svg, /Headline-Line-[^>]+>[^<]*…<\/text>/);
  assert.doesNotMatch(svg, /이 문장은 배너에 노출되지 않아요/);
});

test("keeps the 24-character Squid generated headline boundary complete", () => {
  const headline = "가".repeat(24);
  const svg = buildEditableSvg("squid", "classic", {
    ...SPEC,
    headline,
    render_strategy: "generated_gtm",
    creative_family: "editorial_big_type",
    ...SQUID_GENERATED_PROFILE,
  }, SQUID_ASSETS);

  const rendered = [...svg.matchAll(/id="Headline-Line-[0-9]+"[^>]*>([^<]*)<\/text>/g)]
    .map(match => match[1])
    .join("");
  assert.equal(rendered, headline);
  assert.doesNotMatch(rendered, /…/);
});

test("requires the reviewed official asset pack for every generated Squid family", () => {
  const generatedSpec = {
    ...SPEC,
    render_strategy: "generated_gtm",
    ...SQUID_GENERATED_PROFILE,
  };
  const incompleteAssetSets = [
    {
      logoLight: SQUID_ASSETS.logoLight,
      squidSquib: SQUID_ASSETS.squidSquib,
      squidBubbles: SQUID_ASSETS.squidBubbles,
    },
    {
      logoLight: SQUID_ASSETS.logoLight,
      squidFormLanguage: SQUID_ASSETS.squidFormLanguage,
      squidBubbles: SQUID_ASSETS.squidBubbles,
    },
    {
      logoLight: SQUID_ASSETS.logoLight,
      squidFormLanguage: SQUID_ASSETS.squidFormLanguage,
      squidSquib: SQUID_ASSETS.squidSquib,
    },
  ] as const;

  for (const creativeFamily of [
    "editorial_big_type",
    "milestone_metric",
    "status_progress",
    "product_proof",
  ] as const) {
    const complete = buildEditableSvg("squid", "classic", {
      ...generatedSpec,
      creative_family: creativeFamily,
      visual_metric: creativeFamily === "milestone_metric" ? "5M" : undefined,
    }, SQUID_ASSETS);
    assert.match(complete, /id="Squid-Official-Form-Language"/);
    assert.doesNotMatch(complete, /Brand-Logo/);

    for (const incompleteAssets of incompleteAssetSets) {
      assert.throws(
        () => buildEditableSvg("squid", "classic", {
          ...generatedSpec,
          creative_family: creativeFamily,
          visual_metric: creativeFamily === "milestone_metric" ? "5M" : undefined,
        }, incompleteAssets),
        new RegExp(`official_squid_generated_assets_required:${creativeFamily}`),
      );
    }
  }

  const withoutPublisherLogo = buildEditableSvg("squid", "classic", {
    ...generatedSpec,
    creative_family: "editorial_big_type",
  }, {
    squidFormLanguage: SQUID_ASSETS.squidFormLanguage,
    squidSquib: SQUID_ASSETS.squidSquib,
    squidBubbles: SQUID_ASSETS.squidBubbles,
  });
  assert.match(withoutPublisherLogo, /id="Squid-Official-SQUIB"/);
  assert.doesNotMatch(withoutPublisherLogo, /Brand-Logo/);
});

test("uses official form language for product proof and fails closed for worldbuilding", () => {
  const product = buildEditableSvg("squid", "classic", {
    ...SPEC,
    render_strategy: "generated_gtm",
    creative_family: "product_proof",
    ...SQUID_GENERATED_PROFILE,
  }, SQUID_ASSETS);
  assert.match(product, /id="Squid-Official-Form-Language"/);
  assert.doesNotMatch(product, /Mock|Placeholder|Wireframe|Dashboard/);

  assert.throws(
    () => buildEditableSvg("squid", "classic", {
      ...SPEC,
      render_strategy: "generated_gtm",
      creative_family: "worldbuilding",
      ...SQUID_GENERATED_PROFILE,
    }, SQUID_ASSETS),
    /unsupported_squid_generated_profile/,
  );
});

test("rejects stale Squid generated editable specs instead of replaying them as v5", () => {
  assert.throws(
    () => buildEditableSvg("squid", "classic", {
      ...SPEC,
      render_strategy: "generated_gtm",
      creative_family: "editorial_big_type",
      template_version: "squid-generated-gtm@4",
      visual_design_profile_id: "squid/full-bleed-character-type",
      visual_design_profile_version: 1,
    }, SQUID_ASSETS),
    /unsupported_squid_generated_profile/,
  );
});

test("keeps Squid source-remix SVG output byte-identical and source-native", () => {
  const remixSpec = {
    ...SPEC,
    source_image_width: 1600,
    source_image_height: 900,
    output_width: 1200,
    output_height: 675,
    output_policy: "official_source_native_v1",
  };
  const assets = { sourceImage: "data:image/jpeg;base64,aW1hZ2U=" };
  const beforeRoutingFields = buildEditableSvg("squid", "remix", remixSpec, assets);
  const routedSourceRemix = buildEditableSvg("squid", "remix", {
    ...remixSpec,
    render_strategy: "source_remix",
    creative_family: "product_proof",
  }, assets);

  assert.equal(routedSourceRemix, beforeRoutingFields);
  assert.match(routedSourceRemix, /width="1200" height="675" viewBox="0 0 1200 675"/);
  assert.doesNotMatch(routedSourceRemix, /Squid-Generated-|COINEASY|KOREA/);
});

test("fails closed when Squid classic official assets are incomplete", () => {
  assert.throws(
    () => buildEditableSvg("squid", "classic", SPEC, { logoLight: "data:image/png;base64,bG9nbw==" }),
    /official_squid_classic_assets_required/,
  );
});

test("fits an unbroken Korean Squid headline inside the classic editable safe width", () => {
  const headline = "한국사용자에게정확하고자연스럽게전달하는업데이트입니다";
  const svg = buildEditableSvg("squid", "classic", {
    ...SPEC,
    headline,
    body_lines: ["공식 원문에서 확인한 내용이에요"],
  }, {
    logoLight: "data:image/png;base64,bG9nbw==",
    squidFormLanguage: "data:image/png;base64,Zm9ybQ==",
    squidSquib: "data:image/png;base64,c3F1aWI=",
    squidBubbles: "data:image/png;base64,YnViYmxlcw==",
  });

  const lines = [...svg.matchAll(/id="Headline-Line-[0-9]+"[^>]*>([^<]*)<\/text>/g)]
    .map(match => match[1]);
  assert.deepEqual(lines, ["한국사용자에게정확하고자연스", "럽게전달하는업데이트입니다"]);
  assert.doesNotMatch(lines.join(""), /…/);
  assert.match(svg, /font-size="64"/);
});

test("canonicalizes generic Squid editorial and signal requests to the official classic", () => {
  const assets = {
    logoLight: "data:image/png;base64,bG9nbw==",
    squidFormLanguage: "data:image/png;base64,Zm9ybQ==",
    squidSquib: "data:image/png;base64,c3F1aWI=",
    squidBubbles: "data:image/png;base64,YnViYmxlcw==",
  };
  for (const style of ["editorial", "signal"] as const) {
    assert.equal(effectiveEditableTemplateStyle("squid", style), "classic");
    const svg = buildEditableSvg("squid", style, SPEC, assets);
    assert.match(svg, /Localized News Card · Figma Editable · classic/);
    assert.match(svg, /id="Squid-Figma-Daily-News"/);
    assert.doesNotMatch(svg, /Main-Card-Background|Editorial-Grid|Brand-Rail|Body-Item-1-Background/);
  }
  assert.equal(effectiveEditableTemplateStyle("yellow", "editorial"), "editorial");
});

test("creates a Squid official-creative translation layer without extra card chrome", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_logo_visible: true,
    source_text_visible: true,
    source_crop_bottom: 70,
    source_image_width: 1600,
    source_image_height: 900,
    output_width: 1200,
    output_height: 675,
    output_policy: "official_source_native_v1",
    source_background_color: "#B881DF",
    translation_regions: [{
      source_text: "Need XRP anywhere?",
      text: "어디서나 XRP를 사용하세요",
      x: 8,
      y: 12,
      width: 54,
      height: 18,
      source_x: 8,
      source_y: 12,
      source_width: 54,
      source_height: 18,
      align: "left",
      font_role: "display",
      font_size: 5.5,
      scale_x: 1.24,
      text_color: "#FFFFFF",
    }],
  }, {
    logoDark: "data:image/png;base64,bG9nbw==",
    sourceImage: "data:image/jpeg;base64,aW1hZ2U=",
  });
  assert.match(svg, /id="Source-Visual"/);
  assert.match(svg, /id="Canvas-Background"[^>]+fill="#B881DF"/);
  assert.match(svg, /href="data:image\/jpeg;base64,aW1hZ2U="/);
  assert.match(svg, /<svg[^>]+width="1200" height="675" viewBox="0 0 1200 675"/);
  assert.match(svg, /<image id="Source-Visual" x="0" y="0" width="1200" height="675"/);
  assert.match(svg, /id="Korean-Translation-Layer"/);
  assert.match(svg, /id="Korean-Translation-Region-1-Text-Line-1"/);
  assert.match(svg, /어디서나 XRP를/);
  assert.match(svg, /사용하세요/);
  assert.match(svg, /<title id="Title">Squid editable Korean news card<\/title>/);
  assert.doesNotMatch(svg, /Squid Router/);
  assert.match(svg, /scale\(1\.24 1\)/);
  assertTransparentSquidCaptions(svg, 1);
  assert.doesNotMatch(svg, /Source-Text-Replacement|Source-Text-Clean-Patch|Korean-Translation-Region-1-Clip|clipPath|clip-path|<mask|filter=|feGaussianBlur/);
  assert.doesNotMatch(svg, /Korean-Subtitle-Scrim|Source-Visual-Crop|Translation-Footer|Korean-Translation-Footer|Blur-Patch|Feather-Mask/);
  assert.doesNotMatch(svg, /Localized-Content-Panel|Official-Logo-Safe-Area|Brand-Logo|Label-Text|CoinEasy|COINEASY/i);
});

test("does not trust manipulated Squid source-native SVG dimensions", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_image_width: 1600,
    source_image_height: 900,
    output_width: 1080,
    output_height: 1080,
    output_policy: "official_source_native_v1",
  }, {
    sourceImage: "data:image/jpeg;base64,aW1hZ2U=",
  });
  assert.match(svg, /<svg[^>]+width="1080" height="1080" viewBox="0 0 1080 1080"/);
  assert.match(svg, /<image id="Source-Visual" x="0" y="236\.25" width="1080" height="607\.5"/);
});

test("keeps explicit Squid translation line breaks as separate editable text layers", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    source_crop_bottom: 70,
    source_image_width: 480,
    source_image_height: 320,
    translation_regions: [{
      source_text: "stack is love,\nstack is life.",
      text: "stack이 곧 사랑,\nstack이 곧 인생.",
      x: 28.64,
      y: 72,
      width: 42.72,
      height: 25,
      source_x: 28.64,
      source_y: 72,
      source_width: 42.72,
      source_height: 25,
      align: "center",
      font_role: "display",
      font_size: 6,
      scale_x: 1.24,
      text_color: "#000000",
    }],
  }, {
    sourceImage: "data:image/jpeg;base64,aW1hZ2U=",
  });
  assert.match(svg, /id="Korean-Translation-Region-1-Text-Line-1"[^>]+fill="#000000"[^>]*font-size="46\.66"[^>]*>stack이 곧 사랑,<\/text>/);
  assert.match(svg, /id="Korean-Translation-Region-1-Text-Line-2"[^>]*>stack이 곧 인생\.<\/text>/);
  assertTransparentSquidCaptions(svg, 1, ["#000000"]);
  assert.doesNotMatch(svg, /Source-Text-Replacement|Source-Text-Clean-Patch|clipPath|clip-path|Korean-Subtitle-Scrim|Translation-Footer/);
});

test("calibrates the editable Squid subtitle for standalone SVG font metrics", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    source_image_width: 480,
    source_image_height: 320,
    translation_regions: [{
      source_text: "chillin'",
      text: "여유롭게",
      x: 33,
      y: 84,
      width: 34,
      height: 9,
      source_x: 33,
      source_y: 84,
      source_width: 34,
      source_height: 9,
      align: "center",
      font_role: "display",
      font_size: 6,
      scale_x: 1.35,
      text_color: "#FFFFFF",
    }],
  }, {
    sourceImage: "data:image/jpeg;base64,aW1hZ2U=",
  });

  assert.match(
    svg,
    /<g id="Korean-Translation-Region-1"><text id="Korean-Translation-Region-1-Text-Line-1" x="540" y="850\.33"[^>]+scale\(1\.35 1\)/,
  );
  assertTransparentSquidCaptions(svg, 1);
  assert.match(svg, />여유롭게<\/text>/);
});

test("keeps each Squid in-banner region's own font role and PNG size", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    source_crop_bottom: 70,
    translation_regions: [
      {
        source_text: "First phrase", text: "첫 번째 문구", x: 4, y: 4, width: 40, height: 20,
        source_x: 4, source_y: 4, source_width: 40, source_height: 20,
        font_role: "display", font_size: 6,
      },
      {
        source_text: "Second phrase", text: "두 번째 문구", x: 56, y: 4, width: 40, height: 20,
        source_x: 56, source_y: 4, source_width: 40, source_height: 20,
        font_role: "body", font_size: 4,
      },
    ],
  }, { sourceImage: "data:image/jpeg;base64,aW1hZ2U=" });
  assert.match(svg, /id="Korean-Translation-Region-1-Text-Line-1"[^>]+font-family="Bagoss Condensed,[^"]+"[^>]+font-size="46\.66"/);
  assert.match(svg, /id="Korean-Translation-Region-2-Text-Line-1"[^>]+font-family="Pretendard,[^"]+"[^>]+font-size="31\.10"/);
  assertTransparentSquidCaptions(svg, 2);
});

test("keeps every Squid translation background transparent", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    source_image_width: 480,
    source_image_height: 320,
    translation_regions: [
      {
        source_text: "Small", text: "작게", x: 4, y: 4, width: 40, height: 3,
        source_x: 4, source_y: 4, source_width: 40, source_height: 3,
        font_size: 2,
      },
      {
        source_text: "Large", text: "크게", x: 56, y: 20, width: 40, height: 20,
        source_x: 56, source_y: 20, source_width: 40, source_height: 20,
        font_size: 4, text_color: "#e6fa36",
      },
    ],
  }, { sourceImage: "data:image/jpeg;base64,aW1hZ2U=" });
  assertTransparentSquidCaptions(svg, 2, ["#FFFFFF", "#E6FA36"]);
  assert.doesNotMatch(svg, /<g id="Korean-Translation-Region-[^>]+><rect/);
});

test("rejects a Squid translation whose target differs from the audited source box", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    translation_regions: [{
      source_text: "Original", text: "번역", x: 8, y: 10, width: 40, height: 12,
      source_x: 8, source_y: 12, source_width: 40, source_height: 12,
      font_size: 4,
    }],
  }, { sourceImage: "data:image/jpeg;base64,aW1hZ2U=" });
  assertNoSquidTranslation(svg);
  assert.doesNotMatch(svg, />번역<\/text>/);
});

test("keeps a textless Squid creative free of generated copy", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: false,
    translation_regions: [{ text: "이 문구는 나타나면 안 됩니다" }],
  }, {
    sourceImage: "data:image/jpeg;base64,aW1hZ2U=",
  });
  assert.match(svg, /id="Source-Visual"/);
  assertNoSquidTranslation(svg);
  assert.doesNotMatch(svg, /Korean-Subtitle-Scrim|Source-Visual-Crop|Translation-Footer|이 문구는 나타나면 안 됩니다|Headline-Line|Label-Text|Brand-Logo/);
});

test("drops a Squid subtitle that cannot fit a safe region", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    translation_regions: [{
      source_text: "This cannot fit",
      text: "이 문구는 안전 영역 밖으로 나오면 안 됩니다",
      x: 4,
      y: 4,
      width: 20,
      height: 8,
      source_x: 4,
      source_y: 4,
      source_width: 20,
      source_height: 8,
      font_size: 6,
    }],
  }, {
    sourceImage: "data:image/jpeg;base64,aW1hZ2U=",
  });
  assertNoSquidTranslation(svg);
  assert.doesNotMatch(svg, /이 문구는 안전 영역 밖으로/);
});

test("preserves the original Squid creative when subtitle regions overlap", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    translation_regions: [
      {
        source_text: "First", text: "첫 번째", x: 4, y: 4, width: 30, height: 16,
        source_x: 4, source_y: 4, source_width: 30, source_height: 16, font_size: 4,
      },
      {
        source_text: "Overlap", text: "겹치는 문구", x: 20, y: 8, width: 30, height: 16,
        source_x: 20, source_y: 8, source_width: 30, source_height: 16, font_size: 4,
      },
      {
        source_text: "Third", text: "세 번째", x: 45, y: 8, width: 30, height: 16,
        source_x: 45, source_y: 8, source_width: 30, source_height: 16, font_size: 4,
      },
    ],
  }, {
    sourceImage: "data:image/jpeg;base64,aW1hZ2U=",
  });
  assert.doesNotMatch(svg, />첫 번째<\/text>/);
  assert.doesNotMatch(svg, />겹치는 문구<\/text>/);
  assert.doesNotMatch(svg, />세 번째<\/text>/);
  assertNoSquidTranslation(svg);
});

test("supports a Squid translation at the image edge without a background cover", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    translation_regions: [{
      source_text: "At the edge", text: "가장자리", x: 0, y: 20, width: 24, height: 16,
      source_x: 0, source_y: 20, source_width: 24, source_height: 16, font_size: 4,
    }],
  }, { sourceImage: "data:image/jpeg;base64,aW1hZ2U=" });

  assert.match(svg, />가장자리<\/text>/);
  assertTransparentSquidCaptions(svg, 1);
});

test("keeps nearby non-overlapping Squid translations without padded covers", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    translation_regions: [
      {
        source_text: "First", text: "첫째", x: 20, y: 20, width: 20, height: 16,
        source_x: 20, source_y: 20, source_width: 20, source_height: 16, font_size: 4,
      },
      {
        source_text: "Second", text: "둘째", x: 40.5, y: 20, width: 20, height: 16,
        source_x: 40.5, source_y: 20, source_width: 20, source_height: 16, font_size: 4,
      },
    ],
  }, { sourceImage: "data:image/jpeg;base64,aW1hZ2U=" });

  assert.match(svg, />첫째<\/text>/);
  assert.match(svg, />둘째<\/text>/);
  assertTransparentSquidCaptions(svg, 2);
});

test("preserves the original Squid creative for a three-line subtitle", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    translation_regions: [{
      source_text: "First line\nSecond line\nThird line",
      text: "첫째 줄\n둘째 줄\n셋째 줄",
      x: 4,
      y: 4,
      width: 40,
      height: 24,
      source_x: 4,
      source_y: 4,
      source_width: 40,
      source_height: 24,
      font_size: 4,
    }],
  }, {
    sourceImage: "data:image/jpeg;base64,aW1hZ2U=",
  });
  assertNoSquidTranslation(svg);
  assert.doesNotMatch(svg, /첫째 줄|둘째 줄|셋째 줄/);
});

test("preserves the original Squid creative when any subtitle cannot fit", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    translation_regions: [
      {
        source_text: "Short", text: "짧은 자막", x: 4, y: 4, width: 24, height: 12,
        source_x: 4, source_y: 4, source_width: 24, source_height: 12, font_size: 4,
      },
      {
        source_text: "This cannot fit",
        text: "아주 긴 한국어 자막이 안전 영역 안에서 두 줄로 줄어들지 않아 자동 적용하면 안 되는 경우를 검증합니다",
        x: 70,
        y: 4,
        width: 26,
        height: 12,
        source_x: 70,
        source_y: 4,
        source_width: 26,
        source_height: 12,
        font_size: 6,
      },
    ],
  }, {
    sourceImage: "data:image/jpeg;base64,aW1hZ2U=",
  });
  assertNoSquidTranslation(svg);
  assert.doesNotMatch(svg, /짧은 자막|아주 긴 한국어 자막/);
});

test("places one official logo in the safe area when the source visual lacks it", () => {
  const svg = buildEditableSvg("yellow", "remix", { ...SPEC, source_logo_visible: false }, {
    logoDark: "data:image/svg+xml;base64,bG9nbw==",
    sourceImage: "data:image/jpeg;base64,aW1hZ2U=",
  });
  assert.match(svg, /id="Official-Logo-Safe-Area"/);
  assert.equal((svg.match(/id="Brand-Logo"/g) || []).length, 1);
  assert.doesNotMatch(svg, /CoinEasy|COINEASY/i);
});

test("keeps more canvas for OriginTrail and Babylon official source creatives", () => {
  const assets = { sourceImage: "data:image/jpeg;base64,aW1hZ2U=" };
  for (const clientId of ["origintrail", "babylon"] as const) {
    const svg = buildEditableSvg(clientId, "remix", SPEC, assets);
    assert.match(svg, /id="Source-Visual-Background" x="0" y="0" width="1080" height="780"/);
    assert.match(svg, /id="Source-Visual-Shade"[^>]*fill-opacity="0"/);
    assert.match(svg, /id="Panel-Background" x="0" y="780" width="1080" height="300"/);
    assert.match(svg, /id="Panel-Accent" x="44" y="780"/);
  }

  const yellow = buildEditableSvg("yellow", "remix", SPEC, assets);
  assert.match(yellow, /id="Source-Visual-Background" x="0" y="0" width="1080" height="710"/);
  assert.match(yellow, /id="Panel-Background" x="0" y="710" width="1080" height="370"/);
});

test("supports all four editable layout styles", () => {
  for (const style of ["classic", "editorial", "signal", "remix"] as const) {
    const svg = buildEditableSvg("babylon", style, SPEC);
    assert.match(svg, new RegExp(`Figma Editable · ${style}`));
    assert.match(svg, /<text id="Headline-Line-1"/);
    assert.doesNotMatch(svg, /CoinEasy|COINEASY/i);
  }
});
