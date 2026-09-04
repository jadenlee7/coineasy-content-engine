import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import test from "node:test";

import oauthHandler from "../netlify/functions/grok-qa-oauth.mts";
import {
  grokQaOauthConfig,
  issueGrokQaClientId,
  verifyGrokQaClientId,
} from "../netlify/functions/_shared/grok-qa-oauth.mts";

const ORIGIN = "https://coineasy-newscard.netlify.app";
const RESOURCE = `${ORIGIN}/api/grok-qa/mcp`;
const CONNECTOR_TOKEN = "connector-token-" + "a".repeat(48);
const OPERATOR_SECRET = "operator-secret-" + "b".repeat(48);
const SIGNING_SECRET = "signing-secret-" + "c".repeat(48);
const PROJECT_KEY = "project-key-" + "d".repeat(48);
const SCOPED_KEY = "scoped-key-" + "e".repeat(48);
const REDIRECT_URI = "http://127.0.0.1:43119/oauth/callback";

const environment: Record<string, string> = {
  GROK_QA_OAUTH_ENABLED: "true",
  GROK_QA_OAUTH_ISSUER: ORIGIN,
  GROK_QA_OAUTH_ALLOWED_REDIRECT_ORIGINS: "http://127.0.0.1,https://grok.com",
  GROK_QA_CONNECTOR_TOKEN: CONNECTOR_TOKEN,
  GROK_QA_OAUTH_OPERATOR_SECRET: OPERATOR_SECRET,
  GROK_QA_OAUTH_SIGNING_SECRET: SIGNING_SECRET,
  SUPABASE_URL: "https://project.supabase.co",
  SUPABASE_SERVICE_ROLE_KEY: PROJECT_KEY,
  SUPABASE_GROK_QA_OAUTH_KEY: SCOPED_KEY,
};

async function withEnvironment(
  values: Record<string, string | undefined>,
  fetcher: typeof fetch,
  run: () => Promise<void>,
): Promise<void> {
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  const originalFetch = Object.getOwnPropertyDescriptor(globalThis, "fetch");
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: { env: { get: (name: string) => values[name] } },
  });
  Object.defineProperty(globalThis, "fetch", { configurable: true, value: fetcher });
  try {
    await run();
  } finally {
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
    if (originalFetch) Object.defineProperty(globalThis, "fetch", originalFetch);
    else Reflect.deleteProperty(globalThis, "fetch");
  }
}

function request(path: string, init?: RequestInit): Request {
  return new Request(`${ORIGIN}${path}`, init);
}

function formRequest(path: string, values: Record<string, string>): Request {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(values).toString(),
  });
}

test("OAuth configuration is exact, scoped, and secret-disjoint", () => {
  const config = grokQaOauthConfig((name) => environment[name], ORIGIN);
  assert.ok(config);
  assert.equal(config.resource, RESOURCE);
  assert.equal(config.authorizationKey, SCOPED_KEY);
  assert.equal(grokQaOauthConfig((name) => ({
    ...environment,
    GROK_QA_OAUTH_ENABLED: "TRUE",
  })[name], ORIGIN), null);
  assert.equal(grokQaOauthConfig((name) => ({
    ...environment,
    GROK_QA_OAUTH_OPERATOR_SECRET: CONNECTOR_TOKEN,
  })[name], ORIGIN), null);
  assert.equal(grokQaOauthConfig((name) => ({
    ...environment,
    SUPABASE_SERVICE_ROLE_KEY: CONNECTOR_TOKEN,
  })[name], ORIGIN), null);
  assert.equal(grokQaOauthConfig((name) => ({
    ...environment,
    GROK_QA_RELAY_TOKEN: CONNECTOR_TOKEN,
  })[name], ORIGIN), null);
  assert.equal(grokQaOauthConfig((name) => environment[name], "https://evil.example"), null);
});

test("signed dynamic clients bind exact redirect URIs and expire", () => {
  const config = grokQaOauthConfig((name) => environment[name], ORIGIN)!;
  const issued = issueGrokQaClientId(config, [REDIRECT_URI], "Grok Bot", 2_000_000_000, "n".repeat(24));
  assert.deepEqual(verifyGrokQaClientId(config, issued.clientId, 2_000_000_001)?.redirect_uris, [REDIRECT_URI]);
  assert.equal(verifyGrokQaClientId(config, `${issued.clientId}x`, 2_000_000_001), null);
  assert.equal(verifyGrokQaClientId(config, issued.clientId, issued.expiresAt), null);
  assert.throws(() => issueGrokQaClientId(
    config,
    ["https://evil.example/callback"],
    "Grok Bot",
  ), /invalid_redirect_uris/);
});

test("Grok OAuth metadata and PKCE flow issue the bounded connector bearer once", async () => {
  const records = new Map<string, Record<string, unknown>>();
  const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
    const outgoing = new Request(input, init);
    assert.equal(outgoing.headers.get("apikey"), PROJECT_KEY);
    assert.equal(outgoing.headers.get("authorization"), `Bearer ${SCOPED_KEY}`);
    const body = await outgoing.json() as Record<string, unknown>;
    if (outgoing.url.endsWith("/rpc/create_grok_qa_oauth_code")) {
      records.set(String(body.target_code_sha256), { ...body, consumed: false });
      return Response.json({ created: true, status: "created" });
    }
    if (outgoing.url.endsWith("/rpc/consume_grok_qa_oauth_code")) {
      const record = records.get(String(body.target_code_sha256));
      const exact = record
        && record.consumed === false
        && record.target_client_id_sha256 === body.target_client_id_sha256
        && record.target_redirect_uri === body.target_redirect_uri
        && record.target_resource === body.target_resource
        && record.target_scope === body.target_scope
        && record.target_code_challenge === body.target_code_challenge;
      if (exact) record!.consumed = true;
      return Response.json({ authorized: Boolean(exact), status: exact ? "consumed" : "invalid" });
    }
    throw new Error(`unexpected request ${outgoing.url}`);
  };

  await withEnvironment(environment, fetcher, async () => {
    const protectedResource = await oauthHandler(
      request("/.well-known/oauth-protected-resource/api/grok-qa/mcp"),
      {} as never,
    );
    assert.equal(protectedResource.status, 200);
    assert.deepEqual(await protectedResource.json(), {
      resource: RESOURCE,
      authorization_servers: [ORIGIN],
      scopes_supported: ["coineasy.qa"],
      bearer_methods_supported: ["header"],
    });

    const metadata = await oauthHandler(
      request("/.well-known/oauth-authorization-server"),
      {} as never,
    );
    const metadataJson = await metadata.json() as Record<string, unknown>;
    assert.equal(metadataJson.issuer, ORIGIN);
    assert.equal(metadataJson.registration_endpoint, `${ORIGIN}/api/grok-qa/oauth/register`);
    assert.deepEqual(metadataJson.code_challenge_methods_supported, ["S256"]);

    const registration = await oauthHandler(request("/api/grok-qa/oauth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_name: "Grok Bot CoinEasy-QA",
        redirect_uris: [REDIRECT_URI],
        token_endpoint_auth_method: "none",
        grant_types: ["authorization_code"],
        response_types: ["code"],
      }),
    }), {} as never);
    assert.equal(registration.status, 201);
    const registered = await registration.json() as Record<string, any>;
    assert.equal(registered.token_endpoint_auth_method, "none");

    const verifier = randomBytes(48).toString("base64url");
    const challenge = createHash("sha256").update(verifier).digest("base64url");
    const state = randomBytes(18).toString("base64url");
    const authorization = {
      response_type: "code",
      client_id: String(registered.client_id),
      redirect_uri: REDIRECT_URI,
      code_challenge: challenge,
      code_challenge_method: "S256",
      resource: RESOURCE,
      scope: "coineasy.qa",
      state,
    };
    const authorizeQuery = new URLSearchParams(authorization).toString();
    const consent = await oauthHandler(
      request(`/api/grok-qa/oauth/authorize?${authorizeQuery}`),
      {} as never,
    );
    assert.equal(consent.status, 200);
    const consentHtml = await consent.text();
    assert.match(consentHtml, /CoinEasy QA 연결 승인/);
    assert.match(consentHtml, /승인·자동 발행·배포·OpenAI·Batch 권한은 포함되지 않습니다/);
    assert.doesNotMatch(consentHtml, /coineasy_publish|queue_agent_batch|OPENAI_API_KEY/);
    assert.match(consent.headers.get("content-security-policy") || "", /frame-ancestors 'none'/);

    const rejected = await oauthHandler(formRequest("/api/grok-qa/oauth/authorize", {
      ...authorization,
      decision: "approve",
      operator_secret: "wrong-secret-that-is-long-enough-xxxxxxxx",
    }), {} as never);
    assert.equal(rejected.status, 403);
    assert.equal(records.size, 0);

    const approved = await oauthHandler(formRequest("/api/grok-qa/oauth/authorize", {
      ...authorization,
      decision: "approve",
      operator_secret: OPERATOR_SECRET,
    }), {} as never);
    assert.equal(approved.status, 302);
    const callback = new URL(approved.headers.get("location")!);
    assert.equal(callback.origin + callback.pathname, REDIRECT_URI);
    assert.equal(callback.searchParams.get("state"), state);
    const code = callback.searchParams.get("code")!;
    assert.ok(code);
    assert.equal(records.size, 1);

    const wrongVerifier = await oauthHandler(formRequest("/api/grok-qa/oauth/token", {
      grant_type: "authorization_code",
      client_id: String(registered.client_id),
      code,
      redirect_uri: REDIRECT_URI,
      code_verifier: "w".repeat(64),
      resource: RESOURCE,
      scope: "coineasy.qa",
    }), {} as never);
    assert.equal(wrongVerifier.status, 400);

    const token = await oauthHandler(formRequest("/api/grok-qa/oauth/token", {
      grant_type: "authorization_code",
      client_id: String(registered.client_id),
      code,
      redirect_uri: REDIRECT_URI,
      code_verifier: verifier,
      resource: RESOURCE,
      scope: "coineasy.qa",
    }), {} as never);
    assert.equal(token.status, 200);
    assert.deepEqual(await token.json(), {
      access_token: CONNECTOR_TOKEN,
      token_type: "Bearer",
      scope: "coineasy.qa",
    });

    const replay = await oauthHandler(formRequest("/api/grok-qa/oauth/token", {
      grant_type: "authorization_code",
      client_id: String(registered.client_id),
      code,
      redirect_uri: REDIRECT_URI,
      code_verifier: verifier,
      resource: RESOURCE,
      scope: "coineasy.qa",
    }), {} as never);
    assert.equal(replay.status, 400);
  });
});

test("dynamic registration rejects an unapproved redirect origin", async () => {
  await withEnvironment(environment, async () => {
    throw new Error("storage must not be called");
  }, async () => {
    const response = await oauthHandler(request("/api/grok-qa/oauth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_name: "attacker",
        redirect_uris: ["https://evil.example/callback"],
      }),
    }), {} as never);
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), { error: "invalid_redirect_uri" });
  });
});

test("OAuth state and content types fail closed before storage", async () => {
  let calls = 0;
  await withEnvironment(environment, async () => {
    calls += 1;
    throw new Error("storage must not be called");
  }, async () => {
    const registration = await oauthHandler(request("/api/grok-qa/oauth/register", {
      method: "POST",
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify({ client_name: "Grok", redirect_uris: [REDIRECT_URI] }),
    }), {} as never);
    assert.equal(registration.status, 400);

    const token = await oauthHandler(request("/api/grok-qa/oauth/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }), {} as never);
    assert.equal(token.status, 400);
    assert.equal(calls, 0);
  });
});
