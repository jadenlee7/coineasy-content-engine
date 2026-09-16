import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import sharp from "sharp";

function atLeast(actual: string, minimum: string): boolean {
  if (!/^\d+\.\d+\.\d+$/.test(actual)) return false;
  const a = actual.split(".").map(Number);
  const b = minimum.split(".").map(Number);
  for (let index = 0; index < 3; index++) {
    if (a[index] !== b[index]) return a[index] > b[index];
  }
  return true;
}

test("locked and loaded image dependencies include the libheif security patch", () => {
  // GHSA-rgj7-g3m4-5g8c: test the loaded native library as well as npm metadata.
  const manifest = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url), "utf8"));
  assert.equal(lock.packages[""].dependencies.sharp, manifest.dependencies.sharp);
  assert.ok(atLeast(manifest.dependencies.sharp.replace(/^\^/, ""), "0.35.4"));
  assert.ok(atLeast(lock.packages["node_modules/sharp"].version, "0.35.4"));
  assert.ok(atLeast(sharp.versions.sharp, "0.35.4"));
  assert.ok(atLeast(sharp.versions.heif || "", "1.23.2"), "loaded libheif must include the security patch");
});
