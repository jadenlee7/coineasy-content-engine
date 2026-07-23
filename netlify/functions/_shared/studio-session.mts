import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const STUDIO_SESSION_COOKIE = "__Host-coineasy_studio";
export const STUDIO_SESSION_TTL_SECONDS = 4 * 60 * 60;

const SESSION_VERSION = "v1";
const CLOCK_SKEW_SECONDS = 60;
const MINIMUM_ACCESS_TOKEN_LENGTH = 16;
const MINIMUM_AUTOMATION_TOKEN_LENGTH = 32;
const MAXIMUM_AUTOMATION_TOKEN_LENGTH = 512;

function json(body: unknown, status: number, extraHeaders: HeadersInit = {}): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
      "Vary": "Cookie",
      ...extraHeaders,
    },
  });
}

function studioAccessToken(): string | null {
  const value = Netlify.env.get("STUDIO_ACCESS_TOKEN")?.trim() || "";
  return value.length >= MINIMUM_ACCESS_TOKEN_LENGTH ? value : null;
}

function studioAutomationToken(): string | null {
  const value = Netlify.env.get("STUDIO_AUTOMATION_TOKEN")?.trim() || "";
  return value.length >= MINIMUM_AUTOMATION_TOKEN_LENGTH
    && value.length <= MAXIMUM_AUTOMATION_TOKEN_LENGTH
    ? value
    : null;
}

function digest(value: string): Buffer {
  return createHash("sha256").update(value, "utf8").digest();
}

export function constantTimeAccessCodeMatch(candidate: string, expected: string): boolean {
  return timingSafeEqual(digest(candidate), digest(expected));
}

function signPayload(payload: string, accessToken: string): string {
  return createHmac("sha256", accessToken)
    .update(`coineasy-studio-session:${payload}`, "utf8")
    .digest("base64url");
}

export function createStudioSessionValue(
  accessToken: string,
  nowSeconds = Math.floor(Date.now() / 1_000),
): string {
  const expiresAt = nowSeconds + STUDIO_SESSION_TTL_SECONDS;
  const nonce = randomBytes(16).toString("base64url");
  const payload = `${SESSION_VERSION}.${nowSeconds}.${expiresAt}.${nonce}`;
  return `${payload}.${signPayload(payload, accessToken)}`;
}

export function createStudioSessionCookie(
  accessToken: string,
  nowSeconds = Math.floor(Date.now() / 1_000),
): string {
  const value = createStudioSessionValue(accessToken, nowSeconds);
  return `${STUDIO_SESSION_COOKIE}=${value}; Path=/; Max-Age=${STUDIO_SESSION_TTL_SECONDS}; HttpOnly; Secure; SameSite=Strict`;
}

export function clearStudioSessionCookie(): string {
  return `${STUDIO_SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict`;
}

function readCookie(req: Request, name: string): string {
  const rawCookie = req.headers.get("cookie") || "";
  for (const item of rawCookie.split(";")) {
    const separator = item.indexOf("=");
    if (separator < 0) continue;
    const key = item.slice(0, separator).trim();
    if (key === name) return item.slice(separator + 1).trim();
  }
  return "";
}

export function verifyStudioSessionValue(
  value: string,
  accessToken: string,
  nowSeconds = Math.floor(Date.now() / 1_000),
): boolean {
  const match = value.match(/^v1\.(\d{10})\.(\d{10})\.([A-Za-z0-9_-]{20,32})\.([A-Za-z0-9_-]{43})$/);
  if (!match) return false;

  const issuedAt = Number(match[1]);
  const expiresAt = Number(match[2]);
  if (!Number.isSafeInteger(issuedAt) || !Number.isSafeInteger(expiresAt)) return false;
  if (issuedAt > nowSeconds + CLOCK_SKEW_SECONDS || expiresAt <= nowSeconds) return false;
  if (expiresAt <= issuedAt || expiresAt - issuedAt > STUDIO_SESSION_TTL_SECONDS) return false;

  const payload = `${SESSION_VERSION}.${match[1]}.${match[2]}.${match[3]}`;
  const expectedSignature = signPayload(payload, accessToken);
  return timingSafeEqual(Buffer.from(match[4]), Buffer.from(expectedSignature));
}

export function hasValidStudioSession(
  req: Request,
  accessToken: string,
  nowSeconds = Math.floor(Date.now() / 1_000),
): boolean {
  return verifyStudioSessionValue(
    readCookie(req, STUDIO_SESSION_COOKIE),
    accessToken,
    nowSeconds,
  );
}

export function requireStudioSession(req: Request): Response | null {
  const accessToken = studioAccessToken();
  if (!accessToken) {
    return json({ error: "studio_access_not_configured" }, 503);
  }
  if (!hasValidStudioSession(req, accessToken)) {
    return json({ error: "studio_auth_required" }, 401);
  }
  return null;
}

export function requireStudioGenerationAccess(req: Request): Response | null {
  const accessToken = studioAccessToken();
  if (accessToken && hasValidStudioSession(req, accessToken)) return null;

  const automationToken = studioAutomationToken();
  const candidate = (req.headers.get("x-studio-automation-key") || "").trim();
  if (
    automationToken
    && candidate.length <= 512
    && constantTimeAccessCodeMatch(candidate, automationToken)
  ) return null;

  if (!accessToken) {
    return json({ error: "studio_access_not_configured" }, 503);
  }
  return json({ error: "studio_auth_required" }, 401);
}

export function configuredStudioAccessToken(): string | null {
  return studioAccessToken();
}

export function studioSessionJson(
  body: unknown,
  status: number,
  extraHeaders: HeadersInit = {},
): Response {
  return json(body, status, extraHeaders);
}
