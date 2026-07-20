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
  assert.match(svg, /href="data:image\/jpeg;base64,aW1hZ2U="/);
  assert.match(svg, /<image id="Source-Visual" x="0" y="236\.25" width="1080" height="607\.5"/);
  assert.match(svg, /id="Korean-Translation-Layer"/);
  assert.match(svg, /id="Korean-Translation-Region-1-Text-Line-1"/);
  assert.match(svg, /어디서나 XRP를/);
  assert.match(svg, /사용하세요/);
  assert.match(svg, /<title id="Title">Squid editable Korean news card<\/title>/);
  assert.doesNotMatch(svg, /Squid Router/);
  assert.match(svg, /scale\(1\.24 1\)/);
  assert.match(svg, /stroke="#100D16" stroke-width="16\.00" paint-order="stroke fill"/);
  assert.doesNotMatch(svg, /stroke-opacity=/);
  assert.doesNotMatch(svg, /Source-Text-Replacement|Source-Text-Clean-Patch|Korean-Translation-Region-1-Clip|clipPath|clip-path|<mask|filter=|feGaussianBlur/);
  assert.doesNotMatch(svg, /Korean-Subtitle-Scrim|Source-Visual-Crop|Translation-Footer|Korean-Translation-Footer|Blur-Patch|Feather-Mask/);
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
  assert.match(svg, /id="Korean-Translation-Region-1-Text-Line-1"[^>]+fill="#FFFFFF"[^>]*font-size="46\.66"[^>]*>stack이 곧 사랑,<\/text>/);
  assert.match(svg, /id="Korean-Translation-Region-1-Text-Line-2"[^>]*>stack이 곧 인생\.<\/text>/);
  assert.match(svg, /stroke="#100D16" stroke-width="16\.00" paint-order="stroke fill"/);
  assert.doesNotMatch(svg, /Source-Text-Replacement|Source-Text-Clean-Patch|clipPath|clip-path|Korean-Subtitle-Scrim|Translation-Footer/);
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
});

test("uses an opaque dynamic glyph outline between 6px and 16px", () => {
  const svg = buildEditableSvg("squid", "remix", {
    ...SPEC,
    source_text_visible: true,
    translation_regions: [
      {
        source_text: "Small", text: "작게", x: 4, y: 4, width: 40, height: 3,
        source_x: 4, source_y: 4, source_width: 40, source_height: 3,
        font_size: 2,
      },
      {
        source_text: "Large", text: "크게", x: 56, y: 20, width: 40, height: 20,
        source_x: 56, source_y: 20, source_width: 40, source_height: 20,
        font_size: 4,
      },
    ],
  }, { sourceImage: "data:image/jpeg;base64,aW1hZ2U=" });
  assert.match(svg, /id="Korean-Translation-Region-1-Text-Line-1"[^>]+stroke-width="6\.00"/);
  assert.match(svg, /id="Korean-Translation-Region-2-Text-Line-1"[^>]+stroke-width="16\.00"/);
  assert.doesNotMatch(svg, /stroke-opacity=|opacity=/);
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
  assert.match(svg, /id="Korean-Translation-Layer"><\/g>/);
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
  assert.match(svg, /id="Korean-Translation-Layer"><\/g>/);
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
  assert.match(svg, /id="Korean-Translation-Layer"><\/g>/);
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
  assert.match(svg, /id="Korean-Translation-Layer"><\/g>/);
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
  assert.match(svg, /id="Korean-Translation-Layer"><\/g>/);
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
  assert.match(svg, /id="Korean-Translation-Layer"><\/g>/);
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

test("supports all four editable layout styles", () => {
  for (const style of ["classic", "editorial", "signal", "remix"] as const) {
    const svg = buildEditableSvg("babylon", style, SPEC);
    assert.match(svg, new RegExp(`Figma Editable · ${style}`));
    assert.match(svg, /<text id="Headline-Line-1"/);
    assert.doesNotMatch(svg, /CoinEasy|COINEASY/i);
  }
});
