import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const consoleHtml = readFileSync(
  new URL("../web/console/index.html", import.meta.url),
  "utf8",
);

test("distinguishes missing copy from a rejected Squid subtitle placement", () => {
  assert.match(consoleHtml, /visual_localization_status === "unsafe_placement"/);
  assert.match(consoleHtml, /한국 자막의 안전한 위치를 찾지 못해 원본 비주얼 유지/);
  assert.match(consoleHtml, /번역할 문구가 없어 원본 비주얼 그대로 유지/);
  assert.match(consoleHtml, /배너 안에 한국어 자막 적용/);
});
