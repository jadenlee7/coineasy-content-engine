import type { Config } from "@netlify/functions";

import {
  buzzOperationsConfigured,
  BuzzOperationsError,
  buzzOperationsOutboxEnabled,
  buzzOperationsSupabaseConfig,
  executeBuzzOperationsAction,
  hasValidBuzzOperationsAccess,
  parseBuzzOperationsAction,
} from "./_shared/buzz-operations.mts";
import { contentCatalogConfig } from "./_shared/content-catalog.mts";

const MAX_BODY_BYTES = 4_096;

function json(body: unknown, status = 200, headers: HeadersInit = {}): Response {
  return Response.json(body, { status, headers: {
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
    "Vary": "x-coineasy-buzz-operations-key",
    "X-Content-Type-Options": "nosniff",
    ...headers,
  }});
}

async function body(request: Request): Promise<unknown> {
  const declared = Number(request.headers.get("content-length") || 0);
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) {
    throw new BuzzOperationsError("invalid_buzz_operations_request");
  }
  const raw = await request.text();
  if (Buffer.byteLength(raw, "utf8") > MAX_BODY_BYTES) {
    throw new BuzzOperationsError("invalid_buzz_operations_request");
  }
  try { return JSON.parse(raw); } catch {
    throw new BuzzOperationsError("invalid_buzz_operations_request");
  }
}

export default async (request: Request): Promise<Response> => {
  if (request.method !== "POST") {
    return json({ error: "method_not_allowed" }, 405, { Allow: "POST" });
  }
  const getEnv = (name: string) => Netlify.env.get(name);
  if (!buzzOperationsConfigured(getEnv)) {
    return json({ error: "buzz_operations_not_configured" }, 503);
  }
  if (!hasValidBuzzOperationsAccess(request, getEnv)) {
    return json({ error: "buzz_operations_auth_required" }, 401);
  }
  if (!buzzOperationsOutboxEnabled(getEnv)) {
    return json({ error: "buzz_operations_disabled" }, 503);
  }
  const catalog = contentCatalogConfig(getEnv);
  const config = catalog ? buzzOperationsSupabaseConfig(catalog, getEnv) : null;
  if (!config) return json({ error: "buzz_operations_storage_not_configured" }, 503);
  try {
    const action = parseBuzzOperationsAction(await body(request), getEnv);
    return json(await executeBuzzOperationsAction(config, action));
  } catch (error) {
    const code = error instanceof BuzzOperationsError
      ? error.code : "buzz_operations_unavailable";
    return json({ error: code }, code === "invalid_buzz_operations_request"
      ? 400 : code === "buzz_operations_conflict" ? 409 : 502);
  }
};

export const config: Config = { path: "/api/buzz-operations/origintrail" };
