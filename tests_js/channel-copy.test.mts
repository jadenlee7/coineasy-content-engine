import assert from "node:assert/strict";
import test from "node:test";

import { buildChannelCopy } from "../netlify/functions/_shared/channel-copy.mts";

test("builds a Korean GTM Telegram announcement with CTA, original link, and hashtags", () => {
  const original = "Yellow introduces 24/7 clearing for open finance.";
  const sourceUrl = "https://x.com/Yellow/status/2078087351893414295";
  const copy = buildChannelCopy(
    "yellow",
    {
      label: "인사이트",
      headline: "Yellow가 오픈체인 클리어링으로 거래 효율을 높입니다",
      body_lines: ["마이크로 거래를 오프체인에서 처리", "순차액만 온체인에서 정산"],
    },
    original,
    sourceUrl,
  );

  assert.match(copy.telegram, /Yellow Korea \| 인사이트/);
  assert.match(copy.telegram, /▪️ 마이크로 거래를 오프체인에서 처리/);
  assert.match(copy.telegram, /자세한 내용과 전체 맥락은 원문에서 확인해 주세요/);
  assert.match(copy.telegram, new RegExp(sourceUrl.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(copy.telegram, /#Yellow #YellowNetwork #YellowKorea #Web3/);
});

test("keeps the X copy as source content without added CTA or hashtags", () => {
  const original = "Original post line one.\nOriginal post line two.";
  const copy = buildChannelCopy(
    "babylon",
    { headline: "한국어 카드 헤드라인", body_lines: ["한국 GTM 요약"] },
    `  ${original}  `,
    "https://x.com/babylonlabs_io/status/123",
  );

  assert.equal(copy.x, original);
  assert.doesNotMatch(copy.x, /자세한 내용|#Babylon|한국어 카드/);
});

test("omits the original-link line when no source URL is available", () => {
  const copy = buildChannelCopy(
    "squid",
    { headline: "Squid 업데이트" },
    "Original Squid post.",
    "",
  );

  assert.doesNotMatch(copy.telegram, /🔗 원문:/);
  assert.match(copy.telegram, /#SquidRouter/);
});
