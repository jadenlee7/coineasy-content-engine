import assert from "node:assert/strict";
import test from "node:test";

import articleHandler from "../netlify/functions/article.mts";
import articleBannerHandler from "../netlify/functions/article-banner.mts";
import articleVisualHandler from "../netlify/functions/article-visual.mts";
import editableCardHandler from "../netlify/functions/editable-card.mts";
import newsCardHandler from "../netlify/functions/news-card.mts";
import studioSessionHandler, {
  resetStudioLoginAttemptsForTests,
} from "../netlify/functions/studio-session.mts";
import tutorialHandler from "../netlify/functions/tutorial.mts";
import {
  createStudioSessionCookie,
  createStudioSessionValue,
  hasValidStudioSession,
  requireStudioGenerationAccess,
  STUDIO_SESSION_COOKIE,
  STUDIO_SESSION_TTL_SECONDS,
  verifyStudioSessionValue,
} from "../netlify/functions/_shared/studio-session.mts";

const ACCESS_TOKEN = "test-studio-access-token-32-bytes";
const AUTOMATION_TOKEN = "test-studio-automation-token-40-bytes-long";

async function withNetlifyEnvironment(
  values: Record<string, string | undefined>,
  run: () => Promise<void>,
): Promise<void> {
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: {
      env: {
        get(name: string): string | undefined {
          return values[name];
        },
      },
    },
  });
  try {
    await run();
  } finally {
    if (originalNetlify) {
      Object.defineProperty(globalThis, "Netlify", originalNetlify);
    } else {
      Reflect.deleteProperty(globalThis, "Netlify");
    }
  }
}

function cookieRequest(value: string): Request {
  return new Request("https://console.example/api/studio-session", {
    headers: { cookie: `${STUDIO_SESSION_COOKIE}=${value}` },
  });
}

test("studio cookie is signed, short-lived, and rejects tampering or expiry", () => {
  const issuedAt = 1_800_000_000;
  const value = createStudioSessionValue(ACCESS_TOKEN, issuedAt);
  const cookie = createStudioSessionCookie(ACCESS_TOKEN, issuedAt);

  assert.match(cookie, new RegExp(`^${STUDIO_SESSION_COOKIE}=`));
  assert.match(cookie, /Path=\//);
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /Secure/);
  assert.match(cookie, /SameSite=Strict/);
  assert.match(cookie, new RegExp(`Max-Age=${STUDIO_SESSION_TTL_SECONDS}`));
  assert.equal(verifyStudioSessionValue(value, ACCESS_TOKEN, issuedAt), true);
  assert.equal(
    verifyStudioSessionValue(value, ACCESS_TOKEN, issuedAt + STUDIO_SESSION_TTL_SECONDS - 1),
    true,
  );
  assert.equal(
    verifyStudioSessionValue(value, ACCESS_TOKEN, issuedAt + STUDIO_SESSION_TTL_SECONDS),
    false,
  );
  const final = value.at(-1) === "A" ? "B" : "A";
  assert.equal(verifyStudioSessionValue(`${value.slice(0, -1)}${final}`, ACCESS_TOKEN, issuedAt), false);
  assert.equal(hasValidStudioSession(cookieRequest(value), ACCESS_TOKEN, issuedAt), true);
});

test("generation accepts a separate server automation key without broadening team sessions", async () => {
  await withNetlifyEnvironment({
    STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
    STUDIO_AUTOMATION_TOKEN: AUTOMATION_TOKEN,
  }, async () => {
    const accepted = requireStudioGenerationAccess(new Request(
      "https://console.example/api/news-card/yellow",
      { headers: { "x-studio-automation-key": AUTOMATION_TOKEN } },
    ));
    assert.equal(accepted, null);

    const rejected = requireStudioGenerationAccess(new Request(
      "https://console.example/api/news-card/yellow",
      { headers: { "x-studio-automation-key": "wrong-automation-key-that-is-long-enough" } },
    ));
    assert.equal(rejected?.status, 401);
    assert.deepEqual(await rejected?.json(), { error: "studio_auth_required" });

    const editingRejected = await editableCardHandler(new Request(
      "https://console.example/api/editable-card/yellow",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-studio-automation-key": AUTOMATION_TOKEN,
        },
        body: "{}",
      },
    ), {
      params: { clientId: "yellow" },
      site: { url: "https://console.example" },
    } as never);
    assert.equal(editingRejected.status, 401);
    assert.deepEqual(await editingRejected.json(), { error: "studio_auth_required" });
  });

  await withNetlifyEnvironment({
    STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
    STUDIO_AUTOMATION_TOKEN: "too-short",
  }, async () => {
    const rejected = requireStudioGenerationAccess(new Request(
      "https://console.example/api/news-card/yellow",
      { headers: { "x-studio-automation-key": "too-short" } },
    ));
    assert.equal(rejected?.status, 401);
  });

  await withNetlifyEnvironment({
    STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
    STUDIO_AUTOMATION_TOKEN: "a".repeat(513),
  }, async () => {
    const rejected = requireStudioGenerationAccess(new Request(
      "https://console.example/api/news-card/yellow",
      { headers: { "x-studio-automation-key": "a".repeat(512) } },
    ));
    assert.equal(rejected?.status, 401);
  });
});

test("recovery generation rejects an expected Netlify release mismatch before work", async () => {
  await withNetlifyEnvironment({
    STUDIO_AUTOMATION_TOKEN: AUTOMATION_TOKEN,
  }, async () => {
    const response = await newsCardHandler(new Request(
      "https://console.example/api/news-card/squid",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "idempotency-key": "11111111-1111-4111-8111-111111111111",
          "x-studio-automation-key": AUTOMATION_TOKEN,
          "x-studio-expected-release-sha": "d".repeat(40),
        },
        body: JSON.stringify({ source_content: "must not be parsed" }),
      },
    ), { params: { clientId: "squid" } } as never);

    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "studio_release_mismatch" });
  });
});

test("session endpoint fails closed when STUDIO_ACCESS_TOKEN is missing", async () => {
  await withNetlifyEnvironment({}, async () => {
    const response = await studioSessionHandler(new Request(
      "https://console.example/api/studio-session",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ access_code: "anything" }),
      },
    ), { ip: "203.0.113.1" } as never);

    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "studio_access_not_configured" });
    assert.equal(response.headers.get("set-cookie"), null);
  });
});

test("valid access code creates a session; invalid and deleted sessions stay closed", async () => {
  resetStudioLoginAttemptsForTests();
  await withNetlifyEnvironment({ STUDIO_ACCESS_TOKEN: ACCESS_TOKEN }, async () => {
    const invalid = await studioSessionHandler(new Request(
      "https://console.example/api/studio-session",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ access_code: "wrong-access-code" }),
      },
    ), { ip: "203.0.113.2" } as never);
    assert.equal(invalid.status, 401);
    assert.deepEqual(await invalid.json(), { error: "invalid_access_code" });

    const login = await studioSessionHandler(new Request(
      "https://console.example/api/studio-session",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ access_code: ACCESS_TOKEN }),
      },
    ), { ip: "203.0.113.2" } as never);
    assert.equal(login.status, 200);
    assert.deepEqual(await login.json(), { authenticated: true });
    const setCookie = login.headers.get("set-cookie") || "";
    assert.match(setCookie, /HttpOnly; Secure; SameSite=Strict/);

    const cookieHeader = setCookie.split(";", 1)[0];
    const status = await studioSessionHandler(new Request(
      "https://console.example/api/studio-session",
      { headers: { cookie: cookieHeader } },
    ), {} as never);
    assert.equal(status.status, 200);
    assert.deepEqual(await status.json(), { authenticated: true });

    const logout = await studioSessionHandler(new Request(
      "https://console.example/api/studio-session",
      { method: "DELETE", headers: { cookie: cookieHeader } },
    ), {} as never);
    assert.equal(logout.status, 200);
    assert.match(logout.headers.get("set-cookie") || "", /Max-Age=0/);
  });
});

test("login throttling blocks repeated failures per IP on a best-effort basis", async () => {
  resetStudioLoginAttemptsForTests();
  await withNetlifyEnvironment({ STUDIO_ACCESS_TOKEN: ACCESS_TOKEN }, async () => {
    const attempt = () => studioSessionHandler(new Request(
      "https://console.example/api/studio-session",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ access_code: "not-the-code" }),
      },
    ), { ip: "198.51.100.8" } as never);

    for (let index = 0; index < 5; index += 1) {
      assert.equal((await attempt()).status, 401);
    }
    const blocked = await attempt();
    assert.equal(blocked.status, 429);
    assert.ok(Number(blocked.headers.get("retry-after")) > 0);
  });
});

test("all privileged relays reject unauthenticated requests before fetching upstream", async () => {
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  globalThis.fetch = async () => {
    fetchCount += 1;
    throw new Error("upstream should not be called");
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      API_SECRET: "railway-api-secret",
    }, async () => {
      const request = (path: string) => new Request(`https://console.example${path}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      });
      const responses = await Promise.all([
        newsCardHandler(request("/api/news-card/yellow"), { params: { clientId: "yellow" } } as never),
        articleHandler(request("/api/article/yellow"), { params: { clientId: "yellow" } } as never),
        articleBannerHandler(request("/api/article-banner/yellow"), {
          params: { clientId: "yellow" }, site: { url: "https://console.example" },
        } as never),
        articleVisualHandler(new Request(
          "https://console.example/api/article-visual/22222222-2222-4222-8222-222222222222/hero",
        ), {
          params: {
            contentId: "22222222-2222-4222-8222-222222222222",
            visualId: "hero",
          },
          site: { url: "https://console.example" },
        } as never),
        tutorialHandler(request("/api/tutorial/yellow"), { params: { clientId: "yellow" } } as never),
        editableCardHandler(request("/api/editable-card/yellow"), {
          params: { clientId: "yellow" }, site: { url: "https://console.example" },
        } as never),
      ]);

      assert.deepEqual(responses.map((response) => response.status), [401, 401, 401, 401, 401, 401]);
      for (const response of responses) {
        assert.deepEqual(await response.json(), { error: "studio_auth_required" });
      }
      assert.equal(fetchCount, 0);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("privileged relays return configuration error before doing work", async () => {
  await withNetlifyEnvironment({ API_SECRET: "railway-api-secret" }, async () => {
    const response = await newsCardHandler(new Request(
      "https://console.example/api/news-card/yellow",
      { method: "POST", body: "{}" },
    ), { params: { clientId: "yellow" } } as never);
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "studio_access_not_configured" });
  });
});
