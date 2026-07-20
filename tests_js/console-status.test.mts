import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const consoleHtml = readFileSync(
  new URL("../web/console/index.html", import.meta.url),
  "utf8",
);

test("distinguishes missing copy from a rejected Squid subtitle placement", () => {
  assert.match(consoleHtml, /visual_localization_status === "unsafe_placement"/);
  assert.match(consoleHtml, /원문 자막을 자연스럽게 교체하기 어려워 원본 비주얼 유지/);
  assert.match(consoleHtml, /원문 자막을 같은 위치의 한국어로 교체/);
  assert.match(consoleHtml, /번역할 문구가 없어 원본 비주얼 그대로 유지/);
});

test("passes the Railway-cleaned source file to the Figma editable endpoint", () => {
  assert.match(consoleHtml, /source_visual_file: payload\.source_visual_file \|\| ""/);
});
