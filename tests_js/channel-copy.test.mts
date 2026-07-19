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

test("builds a concise Korean GTM X post with source CTA and focused hashtags", () => {
  const original = "Original post line one.\nOriginal post line two.";
  const copy = buildChannelCopy(
    "babylon",
    { headline: "바빌론이 비트코인 스테이킹의 활용 범위를 넓힙니다", body_lines: ["BTC를 브릿지 없이 직접 활용합니다", "비트코인 보안을 다양한 네트워크로 확장합니다"] },
    `  ${original}  `,
    "https://x.com/babylonlabs_io/status/123",
  );

  assert.match(copy.x, /바빌론이 비트코인 스테이킹의 활용 범위를 넓힙니다/);
  assert.match(copy.x, /• BTC를 브릿지 없이 직접 활용합니다/);
  assert.match(copy.x, /🔗 원문 확인: https:\/\/x\.com\/babylonlabs_io\/status\/123/);
  assert.match(copy.x, /#Babylon #BabylonKorea/);
  assert.doesNotMatch(copy.x, /Original post line/);
  assert.ok(copy.x.length <= 280);
});

test("omits the original-link line when no source URL is available", () => {
  const copy = buildChannelCopy(
    "squid",
    { headline: "Squid 업데이트" },
    "Original Squid post.",
    "",
  );

  assert.doesNotMatch(copy.telegram, /🔗 원문:/);
  assert.doesNotMatch(copy.x, /🔗 원문 확인:/);
  assert.match(copy.telegram, /#SquidRouter/);
});
