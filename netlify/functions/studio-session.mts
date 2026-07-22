import type { Config, Context } from "@netlify/functions";
import {
  clearStudioSessionCookie,
  configuredStudioAccessToken,
  constantTimeAccessCodeMatch,
  createStudioSessionCookie,
  hasValidStudioSession,
  studioSessionJson,
} from "./_shared/studio-session.mts";

type Attempt = { failures: number; resetAt: number };

const LOGIN_WINDOW_MS = 10 * 60 * 1_000;
const MAX_LOGIN_FAILURES = 5;
const MAX_TRACKED_IPS = 500;
const attempts = new Map<string, Attempt>();

function clientIp(req: Request, context: Context): string {
  const contextIp = (context as Context & { ip?: string }).ip;
  const value = contextIp || req.headers.get("x-nf-client-connection-ip") || "unknown";
  return value.trim().slice(0, 128) || "unknown";
}

function pruneAttempts(now: number): void {
  for (const [ip, attempt] of attempts) {
    if (attempt.resetAt <= now) attempts.delete(ip);
  }
  while (attempts.size >= MAX_TRACKED_IPS) {
    const oldest = attempts.keys().next().value as string | undefined;
    if (!oldest) break;
    attempts.delete(oldest);
  }
}

function blockedAttempt(ip: string, now: number): Attempt | null {
  pruneAttempts(now);
  const attempt = attempts.get(ip);
  return attempt && attempt.resetAt > now && attempt.failures >= MAX_LOGIN_FAILURES
    ? attempt
    : null;
}

function recordFailure(ip: string, now: number): void {
  const current = attempts.get(ip);
  if (!current || current.resetAt <= now) {
    attempts.set(ip, { failures: 1, resetAt: now + LOGIN_WINDOW_MS });
    return;
  }
  current.failures += 1;
}

export function resetStudioLoginAttemptsForTests(): void {
  attempts.clear();
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method === "DELETE") {
    return studioSessionJson({ authenticated: false }, 200, {
      "Set-Cookie": clearStudioSessionCookie(),
    });
  }

  if (!new Set(["GET", "POST"]).has(req.method)) {
    return studioSessionJson({ error: "method_not_allowed" }, 405, { Allow: "GET, POST, DELETE" });
  }

  const accessToken = configuredStudioAccessToken();
  if (!accessToken) {
    return studioSessionJson({ error: "studio_access_not_configured" }, 503);
  }

  if (req.method === "GET") {
    const authenticated = hasValidStudioSession(req, accessToken);
    return studioSessionJson(
      authenticated ? { authenticated: true } : { authenticated: false, error: "studio_auth_required" },
      authenticated ? 200 : 401,
    );
  }

  const now = Date.now();
  const ip = clientIp(req, context);
  const blocked = blockedAttempt(ip, now);
  if (blocked) {
    const retryAfter = Math.max(1, Math.ceil((blocked.resetAt - now) / 1_000));
    return studioSessionJson({ error: "too_many_attempts" }, 429, {
      "Retry-After": String(retryAfter),
    });
  }

  let body: { access_code?: unknown };
  try {
    body = (await req.json()) as { access_code?: unknown };
  } catch {
    return studioSessionJson({ error: "invalid_json" }, 400);
  }
  const accessCode = typeof body.access_code === "string" && body.access_code.length <= 512
    ? body.access_code
    : "";
  if (!constantTimeAccessCodeMatch(accessCode, accessToken)) {
    recordFailure(ip, now);
    return studioSessionJson({ error: "invalid_access_code" }, 401);
  }

  attempts.delete(ip);
  return studioSessionJson({ authenticated: true }, 200, {
    "Set-Cookie": createStudioSessionCookie(accessToken),
  });
};

export const config: Config = {
  path: "/api/studio-session",
};
