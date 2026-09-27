import {
  createHash,
  createHmac,
  randomBytes,
  timingSafeEqual,
} from "node:crypto";

const SECRET_MIN_BYTES = 32;
const SECRET_MAX_BYTES = 512;
const MAX_CLIENT_ID_BYTES = 8_192;
const MAX_REDIRECT_URIS = 5;
const CLIENT_LIFETIME_SECONDS = 30 * 24 * 60 * 60;
const CODE_LIFETIME_SECONDS = 5 * 60;
const SCOPE = "coineasy.qa";
const CLIENT_PREFIX = "cqa1";
const BASE64URL = /^[A-Za-z0-9_-]+$/;
const CODE_CHALLENGE = /^[A-Za-z0-9_-]{43,128}$/;
const CODE_VERIFIER = /^[A-Za-z0-9._~-]{43,128}$/;
const SAFE_STATE = /^[\u0021-\u007e]{8,512}$/;
const CLIENT_NONCE = /^[A-Za-z0-9_-]{24,64}$/;
const RESERVED_SECRETS = [
  "STUDIO_ACCESS_TOKEN",
  "STUDIO_AUTOMATION_TOKEN",
  "API_SECRET",
  "PUBLICATION_WORKER_TOKEN",
  "SUPABASE_SERVICE_ROLE_KEY",
  "GROK_QA_CONNECTOR_TOKEN",
  "GROK_QA_RELAY_TOKEN",
  "GROK_QA_DISPATCH_TOKEN",
  "XAI_API_KEY",
] as const;

export type GrokQaOauthConfig = {
  issuer: string;
  resource: string;
  connectorToken: string;
  operatorSecret: string;
  signingSecret: string;
  supabaseUrl: string;
  projectApiKey: string;
  authorizationKey: string;
  allowedRedirectOrigins: ReadonlySet<string>;
};

type ClientPayload = {
  v: 1;
  redirect_uris: string[];
  client_name: string;
  iat: number;
  exp: number;
  nonce: string;
};

export type GrokQaAuthorizationRequest = {
  response_type: "code";
  client_id: string;
  redirect_uri: string;
  code_challenge: string;
  code_challenge_method: "S256";
  resource: string;
  scope: typeof SCOPE;
  state: string;
};

function digest(value: string): Buffer {
  return createHash("sha256").update(value, "utf8").digest();
}

function digestHex(value: string): string {
  return digest(value).toString("hex");
}

function secret(value: string): boolean {
  const bytes = Buffer.byteLength(value, "utf8");
  return bytes >= SECRET_MIN_BYTES
    && bytes <= SECRET_MAX_BYTES
    && value.trim() === value
    && !/[\u0000-\u0020\u007f]/.test(value);
}

function normalizedOrigin(value: string): string | null {
  try {
    const url = new URL(value);
    if (
      !["https:", "http:"].includes(url.protocol)
      || !url.hostname
      || url.username
      || url.password
      || url.pathname !== "/"
      || url.search
      || url.hash
    ) return null;
    if (url.protocol === "http:" && !["127.0.0.1", "localhost"].includes(url.hostname)) {
      return null;
    }
    return url.origin;
  } catch {
    return null;
  }
}

function parseAllowedOrigins(value: string): ReadonlySet<string> | null {
  const entries = value.split(",").map((item) => item.trim()).filter(Boolean);
  if (entries.length < 1 || entries.length > 10) return null;
  const origins = entries.map(normalizedOrigin);
  if (origins.some((origin) => origin === null)) return null;
  const unique = new Set(origins as string[]);
  return unique.size === entries.length ? unique : null;
}

export function grokQaOauthConfig(
  getEnv: (name: string) => string | undefined,
  requestOrigin: string,
): GrokQaOauthConfig | null {
  if (getEnv("GROK_QA_OAUTH_ENABLED") !== "true") return null;
  const issuer = normalizedOrigin((getEnv("GROK_QA_OAUTH_ISSUER") || "").trim());
  const actualOrigin = normalizedOrigin(requestOrigin);
  const connectorToken = (getEnv("GROK_QA_CONNECTOR_TOKEN") || "").trim();
  const operatorSecret = (getEnv("GROK_QA_OAUTH_OPERATOR_SECRET") || "").trim();
  const signingSecret = (getEnv("GROK_QA_OAUTH_SIGNING_SECRET") || "").trim();
  const supabaseUrl = (getEnv("SUPABASE_URL") || "").trim().replace(/\/+$/, "");
  const projectApiKey = (getEnv("SUPABASE_SERVICE_ROLE_KEY") || "").trim();
  const authorizationKey = (getEnv("SUPABASE_GROK_QA_OAUTH_KEY") || "").trim();
  const allowedRedirectOrigins = parseAllowedOrigins(
    getEnv("GROK_QA_OAUTH_ALLOWED_REDIRECT_ORIGINS") || "",
  );
  const values = [
    connectorToken,
    operatorSecret,
    signingSecret,
    projectApiKey,
    authorizationKey,
  ];
  const distinct = new Set(values).size === values.length;
  const connectorConflict = RESERVED_SECRETS.some((name) => {
    if (name === "GROK_QA_CONNECTOR_TOKEN") return false;
    const reserved = (getEnv(name) || "").trim();
    return Boolean(reserved) && reserved === connectorToken;
  });
  const conflicts = [operatorSecret, signingSecret, authorizationKey].some((value) => (
    RESERVED_SECRETS.some((name) => {
      const reserved = (getEnv(name) || "").trim();
      return Boolean(reserved) && reserved === value;
    })
  ));
  let validSupabase = false;
  try {
    const url = new URL(supabaseUrl);
    validSupabase = url.protocol === "https:"
      && !url.username
      && !url.password
      && url.pathname === "/"
      && !url.search
      && !url.hash;
  } catch {
    validSupabase = false;
  }
  if (
    !issuer
    || !actualOrigin
    || issuer !== actualOrigin
    || !secret(connectorToken)
    || !secret(operatorSecret)
    || !secret(signingSecret)
    || !secret(projectApiKey)
    || !secret(authorizationKey)
    || projectApiKey === authorizationKey
    || !distinct
    || connectorConflict
    || conflicts
    || !validSupabase
    || !allowedRedirectOrigins
  ) return null;
  return {
    issuer,
    resource: `${issuer}/api/grok-qa/mcp`,
    connectorToken,
    operatorSecret,
    signingSecret,
    supabaseUrl,
    projectApiKey,
    authorizationKey,
    allowedRedirectOrigins,
  };
}

function b64url(value: Buffer | string): string {
  return Buffer.from(value).toString("base64url");
}

function signature(secretValue: string, encoded: string): string {
  return b64url(createHmac("sha256", secretValue).update(encoded, "utf8").digest());
}

function exactKeys(value: Record<string, unknown>, expected: string[]): boolean {
  const actual = Object.keys(value).sort();
  const sorted = [...expected].sort();
  return actual.length === sorted.length
    && actual.every((key, index) => key === sorted[index]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function validGrokQaRedirectUri(
  value: string,
  allowedOrigins: ReadonlySet<string>,
): boolean {
  if (
    value.length < 8
    || value.length > 2_048
    || /[\u0000-\u0020\u007f]/.test(value)
  ) return false;
  try {
    const url = new URL(value);
    if (url.username || url.password || url.hash) return false;
    if (url.protocol === "http:") {
      return ["127.0.0.1", "localhost"].includes(url.hostname)
        && allowedOrigins.has(`${url.protocol}//${url.hostname}`);
    }
    return url.protocol === "https:" && allowedOrigins.has(url.origin);
  } catch {
    return false;
  }
}

export function issueGrokQaClientId(
  config: GrokQaOauthConfig,
  redirectUris: string[],
  clientName: string,
  nowEpoch = Math.floor(Date.now() / 1_000),
  nonce = b64url(randomBytes(18)),
): { clientId: string; issuedAt: number; expiresAt: number } {
  if (
    redirectUris.length < 1
    || redirectUris.length > MAX_REDIRECT_URIS
    || new Set(redirectUris).size !== redirectUris.length
    || !redirectUris.every((uri) => validGrokQaRedirectUri(uri, config.allowedRedirectOrigins))
  ) throw new Error("invalid_redirect_uris");
  const name = clientName.trim();
  if (
    !name
    || name.length > 120
    || /[\u0000-\u001f\u007f]/.test(name)
    || Buffer.from(name, "utf8").toString("utf8") !== name
    || !Number.isInteger(nowEpoch)
    || nowEpoch < 0
    || !CLIENT_NONCE.test(nonce)
  ) throw new Error("invalid_client_name");
  const payload: ClientPayload = {
    v: 1,
    redirect_uris: [...redirectUris],
    client_name: name,
    iat: nowEpoch,
    exp: nowEpoch + CLIENT_LIFETIME_SECONDS,
    nonce,
  };
  const encoded = b64url(JSON.stringify(payload));
  const clientId = `${CLIENT_PREFIX}.${encoded}.${signature(config.signingSecret, encoded)}`;
  if (Buffer.byteLength(clientId, "utf8") > MAX_CLIENT_ID_BYTES) {
    throw new Error("client_id_too_large");
  }
  return { clientId, issuedAt: payload.iat, expiresAt: payload.exp };
}

export function verifyGrokQaClientId(
  config: GrokQaOauthConfig,
  clientId: string,
  nowEpoch = Math.floor(Date.now() / 1_000),
): ClientPayload | null {
  if (Buffer.byteLength(clientId, "utf8") > MAX_CLIENT_ID_BYTES) return null;
  const parts = clientId.split(".");
  if (parts.length !== 3 || parts[0] !== CLIENT_PREFIX || !BASE64URL.test(parts[1] || "")) {
    return null;
  }
  const expected = signature(config.signingSecret, parts[1]);
  const supplied = parts[2] || "";
  if (expected.length !== supplied.length) return null;
  if (!timingSafeEqual(Buffer.from(expected), Buffer.from(supplied))) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
  } catch {
    return null;
  }
  if (
    !isRecord(payload)
    || !exactKeys(payload, ["v", "redirect_uris", "client_name", "iat", "exp", "nonce"])
    || payload.v !== 1
    || !Array.isArray(payload.redirect_uris)
    || payload.redirect_uris.length < 1
    || payload.redirect_uris.length > MAX_REDIRECT_URIS
    || !payload.redirect_uris.every((uri) => (
      typeof uri === "string" && validGrokQaRedirectUri(uri, config.allowedRedirectOrigins)
    ))
    || typeof payload.client_name !== "string"
    || payload.client_name.length < 1
    || payload.client_name.length > 120
    || payload.client_name.trim() !== payload.client_name
    || /[\u0000-\u001f\u007f]/.test(payload.client_name)
    || typeof payload.iat !== "number"
    || typeof payload.exp !== "number"
    || !Number.isInteger(payload.iat)
    || !Number.isInteger(payload.exp)
    || payload.iat > nowEpoch + 300
    || payload.exp <= nowEpoch
    || payload.exp - payload.iat !== CLIENT_LIFETIME_SECONDS
    || typeof payload.nonce !== "string"
    || !CLIENT_NONCE.test(payload.nonce)
  ) return null;
  return payload as unknown as ClientPayload;
}

export function parseGrokQaAuthorizationRequest(
  config: GrokQaOauthConfig,
  params: URLSearchParams,
  nowEpoch = Math.floor(Date.now() / 1_000),
): GrokQaAuthorizationRequest | null {
  const required = [
    "response_type",
    "client_id",
    "redirect_uri",
    "code_challenge",
    "code_challenge_method",
    "resource",
    "state",
  ];
  if (
    required.some((name) => params.getAll(name).length !== 1)
    || params.getAll("scope").length > 1
  ) return null;
  const request = {
    response_type: params.get("response_type"),
    client_id: params.get("client_id"),
    redirect_uri: params.get("redirect_uri"),
    code_challenge: params.get("code_challenge"),
    code_challenge_method: params.get("code_challenge_method"),
    resource: params.get("resource"),
    scope: params.get("scope") || SCOPE,
    state: params.get("state"),
  };
  if (
    request.response_type !== "code"
    || typeof request.client_id !== "string"
    || typeof request.redirect_uri !== "string"
    || typeof request.code_challenge !== "string"
    || request.code_challenge_method !== "S256"
    || request.resource !== config.resource
    || request.scope !== SCOPE
    || typeof request.state !== "string"
    || !SAFE_STATE.test(request.state)
    || !CODE_CHALLENGE.test(request.code_challenge)
  ) return null;
  const client = verifyGrokQaClientId(config, request.client_id, nowEpoch);
  if (!client || !client.redirect_uris.includes(request.redirect_uri)) return null;
  return request as GrokQaAuthorizationRequest;
}

export function operatorSecretMatches(config: GrokQaOauthConfig, supplied: string): boolean {
  if (!secret(supplied)) return false;
  return timingSafeEqual(digest(supplied), digest(config.operatorSecret));
}

export function codeChallenge(verifier: string): string | null {
  return CODE_VERIFIER.test(verifier) ? b64url(digest(verifier)) : null;
}

export function newGrokQaAuthorizationCode(): string {
  return b64url(randomBytes(32));
}

export function grokQaAuthorizationCodeExpiresAt(now = new Date()): string {
  return new Date(now.getTime() + CODE_LIFETIME_SECONDS * 1_000).toISOString();
}

function supabaseHeaders(config: GrokQaOauthConfig): Record<string, string> {
  return {
    apikey: config.projectApiKey,
    Authorization: `Bearer ${config.authorizationKey}`,
    "Content-Type": "application/json",
  };
}

async function oauthRpc(
  config: GrokQaOauthConfig,
  name: string,
  body: Record<string, unknown>,
  fetcher: typeof fetch,
): Promise<Record<string, unknown>> {
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/${name}`, {
      method: "POST",
      headers: supabaseHeaders(config),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    throw new Error("oauth_storage_unavailable");
  }
  if (!response.ok) throw new Error("oauth_storage_unavailable");
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    throw new Error("oauth_storage_invalid_response");
  }
  if (!isRecord(value)) throw new Error("oauth_storage_invalid_response");
  return value;
}

export async function storeGrokQaAuthorizationCode(
  config: GrokQaOauthConfig,
  code: string,
  request: GrokQaAuthorizationRequest,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const result = await oauthRpc(config, "create_grok_qa_oauth_code", {
    target_code_sha256: digestHex(code),
    target_client_id_sha256: digestHex(request.client_id),
    target_redirect_uri: request.redirect_uri,
    target_resource: request.resource,
    target_scope: request.scope,
    target_code_challenge: request.code_challenge,
    target_expires_at: grokQaAuthorizationCodeExpiresAt(),
  }, fetcher);
  if (
    !exactKeys(result, ["created", "status"])
    || result.created !== true
    || result.status !== "created"
  ) {
    throw new Error("oauth_storage_invalid_response");
  }
}

export async function consumeGrokQaAuthorizationCode(
  config: GrokQaOauthConfig,
  input: {
    code: string;
    clientId: string;
    redirectUri: string;
    resource: string;
    scope: string;
    codeChallenge: string;
  },
  fetcher: typeof fetch = fetch,
): Promise<boolean> {
  const result = await oauthRpc(config, "consume_grok_qa_oauth_code", {
    target_code_sha256: digestHex(input.code),
    target_client_id_sha256: digestHex(input.clientId),
    target_redirect_uri: input.redirectUri,
    target_resource: input.resource,
    target_scope: input.scope,
    target_code_challenge: input.codeChallenge,
  }, fetcher);
  return exactKeys(result, ["authorized", "status"])
    && result.authorized === true
    && result.status === "consumed";
}

export const GROK_QA_OAUTH_SCOPE = SCOPE;
