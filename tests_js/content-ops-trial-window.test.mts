import assert from "node:assert/strict";
import test from "node:test";
import { READY_ANCHORED_TRIAL_ID, trialWindow, withinTrialWindow } from "../netlify/functions/_shared/content-ops-trial-window.mts";

test("readiness anchor is a fixed identity but accepts operator-pinned dates without clock-derived restart", () => {
  assert.equal(READY_ANCHORED_TRIAL_ID, "1a2bef47-4827-4bc3-9b4d-5baf2714015d");
  for (const [start, end, day] of [
    ["2026-10-14T15:10:51Z", "2026-10-14T17:10:51Z", "2026-10-15"],
    ["2026-11-01T23:10:51Z", "2026-11-02T01:10:51Z", "2026-11-02"],
  ]) {
    const bounds = trialWindow(start, end)!;
    assert.equal(bounds.kstDate, day); assert.equal(bounds.end - bounds.start, 7200_000);
    assert.equal(Object.isFrozen(bounds), true);
    assert.equal(withinTrialWindow(bounds, bounds.start), true);
    for (const now of [NaN, Infinity, bounds.start - 1, bounds.end, bounds.end + 1]) {
      assert.equal(withinTrialWindow(bounds, now), false);
    }
  }
});
for (const [start, end] of [
  [undefined, undefined], ["2026-10-14T15:10:51.000Z", "2026-10-14T17:10:51Z"],
  ["2026-10-15T00:10:51+09:00", "2026-10-14T17:10:51Z"],
  ["2026-10-14T15:10:51Z", "2026-10-14T17:10:51.000Z"],
  ["2026-10-14T15:10:51Z", "2026-10-14T17:10:51+00:00"],
  ["2026-02-30T15:10:51Z", "2026-02-30T17:10:51Z"],
  ["2026-10-14T24:00:00Z", "2026-10-15T02:00:00Z"],
  ["2026-10-14T15:10:51z", "2026-10-14T17:10:51Z"],
  [" 2026-10-14T15:10:51Z", "2026-10-14T17:10:51Z"],
  ["2026-10-14T15:10:51Z", "2026-10-14T17:10:50Z"],
  ["2026-10-14T15:10:51Z", "2026-10-14T17:10:52Z"],
  ["2026-10-14T13:10:51Z", "2026-10-14T15:10:51Z"],
  ["2026-10-14T13:00:00Z", "2026-10-14T15:00:00Z"],
]) test(`noncanonical, non-two-hour or cross-KST-day anchor fails: ${String(start)} / ${String(end)}`, () => {
  assert.equal(trialWindow(start, end), null);
});
