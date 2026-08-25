import assert from "node:assert/strict";
import test from "node:test";

import capabilitiesHandler from "../netlify/functions/studio-capabilities.mts";
import { STUDIO_BUILD_RELEASE_SHA } from "../netlify/functions/_shared/studio-release.generated.mts";

const AUTOMATION_TOKEN = "test-studio-automation-token-that-is-long-enough";

async function withAutomationToken(run: () => Promise<void>): Promise<void> {
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: { env: { get: (name: string) => ({
      STUDIO_AUTOMATION_TOKEN: AUTOMATION_TOKEN,
    } as Record<string, string>)[name] } },
  });
  try {
    await run();
  } finally {
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

test("automation capability preflight is authenticated and declares the exact generation contract", async () => {
  await withAutomationToken(async () => {
    const denied = await capabilitiesHandler(new Request("https://console.example/api/studio-capabilities"));
    assert.equal(denied.status, 401);

    const response = await capabilitiesHandler(new Request(
      "https://console.example/api/studio-capabilities",
      { headers: { "X-Studio-Automation-Key": AUTOMATION_TOKEN } },
    ));
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(await response.json(), {
      schema_version: "1.0",
      generation_contract: "double-fact-check@1",
      generated_content_kinds: ["daily_news", "article", "tutorial"],
      tutorial_claims_contract: "lessons@1",
      article_reconciliation_contract: "request-bound-readback@1",
      netlify_release_sha: STUDIO_BUILD_RELEASE_SHA,
    });

    const wrongMethod = await capabilitiesHandler(new Request(
      "https://console.example/api/studio-capabilities",
      { method: "POST", headers: { "X-Studio-Automation-Key": AUTOMATION_TOKEN } },
    ));
    assert.equal(wrongMethod.status, 405);
  });
});
