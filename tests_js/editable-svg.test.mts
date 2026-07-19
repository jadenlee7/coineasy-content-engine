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

test("keeps the source visual as a separate embedded image layer without production labels", () => {
  const svg = buildEditableSvg("squid", "remix", SPEC, {
    logoDark: "data:image/png;base64,bG9nbw==",
    sourceImage: "data:image/jpeg;base64,aW1hZ2U=",
  });
  assert.match(svg, /id="Source-Visual"/);
  assert.match(svg, /href="data:image\/jpeg;base64,aW1hZ2U="/);
  assert.match(svg, /id="CoinEasy-Content-Panel"/);
  assert.match(svg, />COINEASY<\/text>/);
  assert.doesNotMatch(svg, /ORIGINAL VISUAL REMIX|KOREA GTM EDIT/i);
});

test("supports all four editable layout styles", () => {
  for (const style of ["classic", "editorial", "signal", "remix"] as const) {
    const svg = buildEditableSvg("babylon", style, SPEC);
    assert.match(svg, new RegExp(`Figma Editable · ${style}`));
    assert.match(svg, /<text id="Headline-Line-1"/);
  }
});
