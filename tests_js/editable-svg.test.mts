import assert from "node:assert/strict";
import test from "node:test";

import { buildEditableSvg, escapeXml, wrapSvgText } from "../netlify/functions/_shared/editable-svg.mts";

const SPEC = {
  label: "인사이트",
  date: "2026.07.19",
  headline: "Yellow가 오프체인 클리어링으로 거래 효율을 높입니다",
  body_lines: ["첫 번째 핵심 내용", "두 번째 핵심 내용", "세 번째 핵심 내용"],
  source_url: "https://x.com/Yellow/status/123?x=1&y=2",
  theme: "dark",
};

test("escapes XML content and wraps long editable text", () => {
  assert.equal(escapeXml('<Yellow & "Squid">'), "&lt;Yellow &amp; &quot;Squid&quot;&gt;");
  const lines = wrapSvgText("아주 긴 한국어 헤드라인을 여러 줄로 안전하게 나눕니다", 12, 3);
  assert.ok(lines.length >= 2);
  assert.ok(lines.length <= 3);
});

test("creates a native-layer classic SVG for Figma without foreignObject", () => {
  const svg = buildEditableSvg("yellow", "classic", SPEC, {
    logoDark: "data:image/svg+xml;base64,PHN2Zy8+",
  });
  assert.match(svg, /<svg[^>]+width="1080"[^>]+height="1080"/);
  assert.match(svg, /id="Brand-Logo"/);
  assert.match(svg, /id="Headline-Line-1"/);
  assert.match(svg, /id="Body-Item-1-Background"/);
  assert.match(svg, /id="Source-URL"/);
  assert.match(svg, /x=1&amp;y=2/);
  assert.doesNotMatch(svg, /foreignObject|<style/);
});

test("creates a Squid official-creative translation layer without extra card chrome", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_logo_visible: true,
    source_text_visible: true,
    source_crop_bottom: 70,
    source_image_width: 1600,
    source_image_height: 900,
    translation_regions: [{
      text: "어디서나 XRP를 사용하세요",
      x: 8,
      y: 12,
      width: 54,
      height: 18,
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
  assert.match(svg, /href="data:image\/jpeg;base64,aW1hZ2U="/);
  assert.match(svg, /id="Source-Visual-Crop"/);
  assert.match(svg, /clip-path="url\(#Source-Visual-Crop\)"/);
  assert.match(svg, /<rect x="0\.00" y="294\.75" width="1080\.00" height="425\.25"\/>/);
  assert.match(svg, /id="Translation-Footer-Background"/);
  assert.match(svg, /id="Korean-Translation-Footer"/);
  assert.match(svg, /id="Korean-Translation-Region-1-Text-Line-1"/);
  assert.match(svg, /어디서나 XRP를/);
  assert.match(svg, /사용하세요/);
  assert.match(svg, /<title id="Title">Squid editable Korean news card<\/title>/);
  assert.doesNotMatch(svg, /Squid Router/);
  assert.doesNotMatch(svg, /paint-order|stroke-opacity|scale\(1\.24 1\)|Blur-Patch|Feather-Mask|feGaussianBlur/);
  assert.doesNotMatch(svg, /Localized-Content-Panel|Official-Logo-Safe-Area|Brand-Logo|Label-Text|CoinEasy|COINEASY/i);
});

test("keeps explicit Squid translation line breaks as separate editable text layers", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    source_crop_bottom: 70,
    source_image_width: 480,
    source_image_height: 320,
    translation_regions: [{
      text: "stack이 곧 사랑,\nstack이 곧 인생.",
      font_role: "display",
      font_size: 6,
      text_color: "#000000",
    }],
  }, {
    sourceImage: "data:image/jpeg;base64,aW1hZ2U=",
  });
  assert.match(svg, /id="Korean-Translation-Region-1-Text-Line-1"[^>]+fill="#FFFFFF"[^>]*font-size="64\.80"[^>]*>stack이 곧 사랑,<\/text>/);
  assert.match(svg, /id="Korean-Translation-Region-1-Text-Line-2"[^>]*>stack이 곧 인생\.<\/text>/);
  assert.doesNotMatch(svg, /paint-order|stroke-width|scale\(/);
});

test("keeps each Squid footer region's own font role and PNG size cap", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    source_crop_bottom: 70,
    translation_regions: [
      { text: "첫 번째 문구", font_role: "display", font_size: 6 },
      { text: "두 번째 문구", font_role: "body", font_size: 4 },
    ],
  });
  assert.match(svg, /id="Korean-Translation-Region-1-Text-Line-1"[^>]+font-family="Bagoss Condensed,[^"]+"[^>]+font-size="56\.00"/);
  assert.match(svg, /id="Korean-Translation-Region-2-Text-Line-1"[^>]+font-family="Pretendard,[^"]+"[^>]+font-size="43\.20"/);
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
  assert.match(svg, /id="Korean-Translation-Footer"><\/g>/);
  assert.doesNotMatch(svg, /Source-Visual-Crop|Translation-Footer-Background|이 문구는 나타나면 안 됩니다|Headline-Line|Label-Text|Brand-Logo/);
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

test("supports all four editable layout styles", () => {
  for (const style of ["classic", "editorial", "signal", "remix"] as const) {
    const svg = buildEditableSvg("babylon", style, SPEC);
    assert.match(svg, new RegExp(`Figma Editable · ${style}`));
    assert.match(svg, /<text id="Headline-Line-1"/);
    assert.doesNotMatch(svg, /CoinEasy|COINEASY/i);
  }
});
