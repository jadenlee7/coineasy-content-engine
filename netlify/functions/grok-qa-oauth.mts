import type { Config, Context } from "@netlify/functions";

import {
  codeChallenge,
  consumeGrokQaAuthorizationCode,
  grokQaOauthConfig,
  GROK_QA_OAUTH_SCOPE,
  issueGrokQaClientId,
  newGrokQaAuthorizationCode,
  operatorSecretMatches,
  parseGrokQaAuthorizationRequest,
  storeGrokQaAuthorizationCode,
  verifyGrokQaClientId,
} from "./_shared/grok-qa-oauth.mts";

const MAX_JSON_BYTES = 32 * 1024;
const MAX_FORM_BYTES = 64 * 1024;
const OAUTH_PATH = "/api/grok-qa/oauth";

function responseHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return {
    "Cache-Control": "no-store",
    Pragma: "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    ...extra,
  };
}

function json(body: Record<string, unknown>, status = 200): Response {
  return Response.json(body, { status, headers: responseHeaders() });
}

function oauthError(error: string, status = 400): Response {
  return json({ error }, status);
}

function htmlEscape(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character] || character);
}

async function boundedBody(req: Request, maximum: number): Promise<string | null> {
  const declared = Number(req.headers.get("content-length") || 0);
  if (Number.isFinite(declared) && declared > maximum) return null;
  const text = await req.text();
  return Buffer.byteLength(text, "utf8") <= maximum ? text : null;
}

function hasContentType(req: Request, expected: string): boolean {
  return (req.headers.get("content-type") || "")
    .split(";", 1)[0]
    ?.trim()
    .toLowerCase() === expected;
}

function redirectWith(
  redirectUri: string,
  values: Record<string, string>,
): Response {
  const url = new URL(redirectUri);
  for (const [key, value] of Object.entries(values)) url.searchParams.set(key, value);
  return new Response(null, {
    status: 302,
    headers: responseHeaders({ Location: url.toString() }),
  });
}

function consentPage(
  action: string,
  request: NonNullable<ReturnType<typeof parseGrokQaAuthorizationRequest>>,
  clientName: string,
  error: string | null = null,
): Response {
  const callbackOrigin = new URL(request.redirect_uri).origin;
  const hidden = Object.entries(request).map(([key, value]) => (
    `<input type="hidden" name="${htmlEscape(key)}" value="${htmlEscape(value)}">`
  )).join("");
  const page = `<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>CoinEasy QA 연결 승인</title><style>
body{font-family:ui-sans-serif,system-ui,sans-serif;background:#f5f7fb;color:#101828;margin:0;padding:32px}
main{max-width:560px;margin:8vh auto;background:#fff;border:1px solid #e4e7ec;border-radius:18px;padding:28px;box-shadow:0 12px 36px #10182814}
h1{font-size:24px;margin:0 0 12px}p,li{line-height:1.6}.muted{color:#667085}.error{color:#b42318;font-weight:700}
label{display:block;font-weight:700;margin:20px 0 8px}input[type=password]{box-sizing:border-box;width:100%;padding:12px;border:1px solid #98a2b3;border-radius:10px;font:inherit}
.actions{display:flex;gap:10px;margin-top:22px}button{border:0;border-radius:10px;padding:12px 16px;font-weight:700;cursor:pointer}.approve{background:#101828;color:#fff}.deny{background:#eaecf0;color:#344054}
</style></head><body><main>
<h1>CoinEasy QA 연결 승인</h1>
<p><strong>${htmlEscape(clientName)}</strong>에서 CoinEasy의 내부 QA 도구 연결을 요청했습니다.</p>
<p class="muted">연결 대상: ${htmlEscape(callbackOrigin)}</p>
<ul><li>검토 대기 항목 읽기</li><li>정확한 버전의 QA 패키지 읽기</li><li>자문용 PASS/WARN/BLOCK 전달</li></ul>
<p class="muted">승인·자동 발행·배포·OpenAI·Batch 권한은 포함되지 않습니다.</p>
${error ? `<p class="error">${htmlEscape(error)}</p>` : ""}
<form method="post" action="${htmlEscape(action)}">${hidden}
<label for="operator_secret">CoinEasy QA 운영자 코드</label>
<input id="operator_secret" name="operator_secret" type="password" autocomplete="one-time-code" required minlength="32" maxlength="512">
<div class="actions"><button class="approve" type="submit" name="decision" value="approve">QA 연결 승인</button>
<button class="deny" type="submit" name="decision" value="deny">취소</button></div></form>
</main></body></html>`;
  return new Response(page, {
    status: error ? 403 : 200,
    headers: responseHeaders({
      "Content-Type": "text/html; charset=utf-8",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    }),
  });
}

export default async (req: Request, _context: Context): Promise<Response> => {
  const url = new URL(req.url);
  const oauth = grokQaOauthConfig((name) => Netlify.env.get(name), url.origin);
  if (!oauth) return oauthError("grok_qa_oauth_not_configured", 503);
  const protectedResourcePaths = new Set([
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/api/grok-qa/mcp",
  ]);

  if (protectedResourcePaths.has(url.pathname)) {
    if (req.method !== "GET") return oauthError("method_not_allowed", 405);
    return json({
      resource: oauth.resource,
      authorization_servers: [oauth.issuer],
      scopes_supported: [GROK_QA_OAUTH_SCOPE],
      bearer_methods_supported: ["header"],
    });
  }

  if (url.pathname === "/.well-known/oauth-authorization-server") {
    if (req.method !== "GET") return oauthError("method_not_allowed", 405);
    return json({
      issuer: oauth.issuer,
      authorization_endpoint: `${oauth.issuer}${OAUTH_PATH}/authorize`,
      token_endpoint: `${oauth.issuer}${OAUTH_PATH}/token`,
      registration_endpoint: `${oauth.issuer}${OAUTH_PATH}/register`,
      response_types_supported: ["code"],
      response_modes_supported: ["query"],
      grant_types_supported: ["authorization_code"],
      token_endpoint_auth_methods_supported: ["none"],
      code_challenge_methods_supported: ["S256"],
      scopes_supported: [GROK_QA_OAUTH_SCOPE],
      protected_resources: [oauth.resource],
    });
  }

  if (url.pathname === `${OAUTH_PATH}/register`) {
    if (req.method !== "POST") return oauthError("method_not_allowed", 405);
    if (!hasContentType(req, "application/json")) {
      return oauthError("invalid_client_metadata", 400);
    }
    const raw = await boundedBody(req, MAX_JSON_BYTES);
    if (raw === null) return oauthError("invalid_client_metadata", 400);
    let body: Record<string, unknown>;
    try {
      body = JSON.parse(raw);
    } catch {
      return oauthError("invalid_client_metadata", 400);
    }
    const redirectUris = body.redirect_uris;
    const clientName = body.client_name;
    const tokenMethod = body.token_endpoint_auth_method ?? "none";
    const grantTypes = body.grant_types ?? ["authorization_code"];
    const responseTypes = body.response_types ?? ["code"];
    if (
      !Array.isArray(redirectUris)
      || !redirectUris.every((value) => typeof value === "string")
      || typeof clientName !== "string"
      || tokenMethod !== "none"
      || JSON.stringify(grantTypes) !== JSON.stringify(["authorization_code"])
      || JSON.stringify(responseTypes) !== JSON.stringify(["code"])
    ) return oauthError("invalid_client_metadata", 400);
    try {
      const issued = issueGrokQaClientId(oauth, redirectUris, clientName);
      return json({
        client_id: issued.clientId,
        client_id_issued_at: issued.issuedAt,
        client_id_expires_at: issued.expiresAt,
        client_name: clientName.trim(),
        redirect_uris: redirectUris,
        token_endpoint_auth_method: "none",
        grant_types: ["authorization_code"],
        response_types: ["code"],
        scope: GROK_QA_OAUTH_SCOPE,
      }, 201);
    } catch (error) {
      return oauthError(
        error instanceof Error && error.message === "invalid_redirect_uris"
          ? "invalid_redirect_uri"
          : "invalid_client_metadata",
        400,
      );
    }
  }

  if (url.pathname === `${OAUTH_PATH}/authorize`) {
    if (req.method === "GET") {
      const request = parseGrokQaAuthorizationRequest(oauth, url.searchParams);
      if (!request) return oauthError("invalid_request", 400);
      const client = verifyGrokQaClientId(oauth, request.client_id);
      if (!client) return oauthError("invalid_client", 400);
      return consentPage(`${oauth.issuer}${OAUTH_PATH}/authorize`, request, client.client_name);
    }
    if (req.method !== "POST") return oauthError("method_not_allowed", 405);
    if (!hasContentType(req, "application/x-www-form-urlencoded")) {
      return oauthError("invalid_request", 400);
    }
    const raw = await boundedBody(req, MAX_FORM_BYTES);
    if (raw === null) return oauthError("invalid_request", 400);
    const form = new URLSearchParams(raw);
    const request = parseGrokQaAuthorizationRequest(oauth, form);
    if (!request) return oauthError("invalid_request", 400);
    const client = verifyGrokQaClientId(oauth, request.client_id);
    if (!client) return oauthError("invalid_client", 400);
    if (form.getAll("decision").length !== 1 || form.getAll("operator_secret").length > 1) {
      return oauthError("invalid_request", 400);
    }
    if (form.get("decision") === "deny") {
      return redirectWith(request.redirect_uri, {
        error: "access_denied",
        state: request.state,
      });
    }
    const supplied = form.get("operator_secret") || "";
    if (form.get("decision") !== "approve" || !operatorSecretMatches(oauth, supplied)) {
      return consentPage(
        `${oauth.issuer}${OAUTH_PATH}/authorize`,
        request,
        client.client_name,
        "운영자 코드를 확인할 수 없습니다.",
      );
    }
    const code = newGrokQaAuthorizationCode();
    try {
      await storeGrokQaAuthorizationCode(oauth, code, request);
    } catch {
      return oauthError("temporarily_unavailable", 503);
    }
    return redirectWith(request.redirect_uri, { code, state: request.state });
  }

  if (url.pathname === `${OAUTH_PATH}/token`) {
    if (req.method !== "POST") return oauthError("method_not_allowed", 405);
    if (!hasContentType(req, "application/x-www-form-urlencoded")) {
      return oauthError("invalid_request", 400);
    }
    const raw = await boundedBody(req, MAX_FORM_BYTES);
    if (raw === null) return oauthError("invalid_request", 400);
    const form = new URLSearchParams(raw);
    const singular = [
      "grant_type",
      "client_id",
      "code",
      "redirect_uri",
      "code_verifier",
      "resource",
    ];
    if (
      singular.some((name) => form.getAll(name).length !== 1)
      || form.getAll("scope").length > 1
    ) return oauthError("invalid_grant", 400);
    const grantType = form.get("grant_type");
    const clientId = form.get("client_id") || "";
    const code = form.get("code") || "";
    const redirectUri = form.get("redirect_uri") || "";
    const verifier = form.get("code_verifier") || "";
    const resource = form.get("resource") || "";
    const scope = form.get("scope") || GROK_QA_OAUTH_SCOPE;
    const challenge = codeChallenge(verifier);
    const client = verifyGrokQaClientId(oauth, clientId);
    if (
      grantType !== "authorization_code"
      || !client
      || !client.redirect_uris.includes(redirectUri)
      || code.length < 32
      || code.length > 512
      || resource !== oauth.resource
      || scope !== GROK_QA_OAUTH_SCOPE
      || !challenge
    ) return oauthError("invalid_grant", 400);
    let authorized = false;
    try {
      authorized = await consumeGrokQaAuthorizationCode(oauth, {
        code,
        clientId,
        redirectUri,
        resource,
        scope,
        codeChallenge: challenge,
      });
    } catch {
      return oauthError("temporarily_unavailable", 503);
    }
    if (!authorized) return oauthError("invalid_grant", 400);
    return json({
      access_token: oauth.connectorToken,
      token_type: "Bearer",
      scope: GROK_QA_OAUTH_SCOPE,
    });
  }

  return oauthError("not_found", 404);
};

export const config: Config = {
  path: [
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/api/grok-qa/mcp",
    "/.well-known/oauth-authorization-server",
    "/api/grok-qa/oauth/register",
    "/api/grok-qa/oauth/authorize",
    "/api/grok-qa/oauth/token",
  ],
};
