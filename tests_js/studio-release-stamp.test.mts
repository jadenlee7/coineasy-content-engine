import assert from "node:assert/strict";
import test from "node:test";

import { STUDIO_BUILD_RELEASE_SHA } from "../netlify/functions/_shared/studio-release.generated.mts";
import {
  currentStudioReleaseSha,
  requireExpectedStudioRelease,
} from "../netlify/functions/_shared/studio-release.mts";
import {
  releaseShaFromModule,
  renderReleaseModule,
} from "../scripts/stamp-netlify-release.mjs";

const RELEASE_SHA = "c".repeat(40);

test("release module renderer accepts only an exact immutable Git SHA", () => {
  assert.equal(releaseShaFromModule(renderReleaseModule(RELEASE_SHA)), RELEASE_SHA);
  assert.equal(releaseShaFromModule(renderReleaseModule(null)), null);
  assert.throws(() => renderReleaseModule("C".repeat(40)), /exact lowercase/);
  assert.throws(() => renderReleaseModule("c".repeat(39)), /exact lowercase/);
});

test("runtime release defaults to the build-stamped module", () => {
  assert.equal(currentStudioReleaseSha(), STUDIO_BUILD_RELEASE_SHA);
  assert.equal(currentStudioReleaseSha(RELEASE_SHA), RELEASE_SHA);
  assert.equal(currentStudioReleaseSha("invalid"), null);

  const expectedByCi = process.env.EXPECTED_STUDIO_RELEASE_SHA;
  if (expectedByCi !== undefined) {
    assert.equal(STUDIO_BUILD_RELEASE_SHA, expectedByCi);
  }
});

test("expected release header fails closed on an unstamped or different build", async () => {
  const request = new Request("https://console.example/api/news-card/squid", {
    headers: { "x-studio-expected-release-sha": RELEASE_SHA },
  });
  assert.equal(requireExpectedStudioRelease(request, RELEASE_SHA), null);

  const missing = requireExpectedStudioRelease(request, null);
  assert.equal(missing?.status, 503);
  assert.deepEqual(await missing?.json(), { error: "studio_release_mismatch" });

  const different = requireExpectedStudioRelease(request, "d".repeat(40));
  assert.equal(different?.status, 503);
});
