import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const html = readFileSync(new URL("../web/console/index.html", import.meta.url), "utf8");
const source = html.match(/function reviewLinkVersionMatches\([\s\S]*?\n      \}/)?.[0];
const loader = html.match(/async function loadLibraryDetail\([\s\S]*?\n      \}/)?.[0];
assert.ok(source);
assert.ok(loader);
const matches = Function(`${source}; return reviewLinkVersionMatches;`)();
const ITEM = "11111111-1111-4111-8111-111111111111";
const VERSION = "22222222-2222-4222-8222-222222222222";

test("staff card opens only its exact current version", () => {
  assert.equal(matches(ITEM, { content_item_id: ITEM, current_version_id: VERSION }, ITEM, VERSION), true);
  assert.equal(matches(ITEM, { content_item_id: ITEM, current_version_id: ITEM }, ITEM, VERSION), false);
  assert.equal(matches(ITEM, { content_item_id: VERSION, current_version_id: VERSION }, ITEM, VERSION), false);
  for (const invalid of ["", "malformed", undefined, 1]) {
    assert.equal(matches(ITEM, {}, ITEM, invalid), false);
  }
  for (const invalid of [null, undefined, [], "wrong"]) {
    assert.equal(matches(ITEM, invalid, ITEM, VERSION), false);
  }
});

test("ordinary library navigation and legacy links remain unchanged", () => {
  assert.equal(matches(ITEM, {}, ITEM, null), true);
  assert.equal(matches(VERSION, {}, ITEM, VERSION), true);
});

test("real detail loader hides stale content before approval controls can render", async () => {
  for (const current of [ITEM, VERSION]) {
    const rendered: unknown[] = [];
    const calls: { url: string; options: { method: string } }[] = [];
    const context = vm.createContext({
      initialReviewContentId: ITEM, initialReviewVersionId: VERSION,
      state: { sessionEpoch: 1 },
      libraryState: { detailRequest: 0, exportRequest: 0, publicationRequest: 0,
        performanceRequest: { x: 0, telegram: 0 }, activeDetail: { stale: true } },
      libraryDetail: { innerHTML: "old controls", hidden: false },
      libraryDetailState: { textContent: "", className: "", hidden: true },
      batchJobIdFromRef: () => null,
      renderLibraryList: () => {},
      handleStudioAccessResponse: () => false,
      renderLibraryDetail: (detail: unknown) => rendered.push(detail),
      fetch: async (url: string, options: { method: string }) => {
        calls.push({ url, options });
        return { ok: true, json: async () => ({ item: { content_item_id: ITEM, current_version_id: current } }) };
      },
    });
    vm.runInContext(`${source}\n${loader}`, context);
    await vm.runInContext(`loadLibraryDetail("${ITEM}")`, context);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, `/api/library/${ITEM}`);
    assert.equal(calls[0].options.method, "GET");
    assert.equal(rendered.length, current === VERSION ? 1 : 0);
    if (current !== VERSION) {
      assert.equal(context.libraryDetail.innerHTML, "");
      assert.equal(context.libraryDetail.hidden, true);
      assert.equal(context.libraryState.activeDetail, null);
      assert.match(context.libraryDetailState.textContent, /현재 버전과 다릅니다/);
    }
  }
});
