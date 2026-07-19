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
      text_color: "#FFFFFF",
    }],
  }, {
    logoDark: "data:image/png;base64,bG9nbw==",
    sourceImage: "data:image/jpeg;base64,aW1hZ2U=",
  });
  assert.match(svg, /id="Source-Visual"/);
  assert.match(svg, /href="data:image\/jpeg;base64,aW1hZ2U="/);
  assert.match(svg, /id="Korean-Translation-Layer"/);
  assert.match(svg, /id="Translated-Region-1-Blur-Patch"/);
  assert.match(svg, /id="Translated-Region-1-Feather-Mask"/);
  assert.match(svg, /feGaussianBlur/);
  assert.match(svg, /paint-order="stroke fill"/);
  assert.match(svg, /어디서나 XRP를/);
  assert.match(svg, /사용하세요/);
  assert.match(svg, /<title id="Title">Squid editable Korean news card<\/title>/);
  assert.doesNotMatch(svg, /Squid Router/);
  assert.doesNotMatch(svg, /Translated-Region-1-Cover/);
  assert.doesNotMatch(svg, /Localized-Content-Panel|Official-Logo-Safe-Area|Brand-Logo|Footer|Label-Text|CoinEasy|COINEASY/i);
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
  assert.match(svg, /id="Korean-Translation-Layer"><\/g>/);
  assert.doesNotMatch(svg, /이 문구는 나타나면 안 됩니다|Headline-Line|Footer|Label-Text|Brand-Logo/);
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
