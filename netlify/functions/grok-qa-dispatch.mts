import type { Config, Context } from "@netlify/functions";

import { contentCatalogConfig } from "./_shared/content-catalog.mts";
import {
  executeGrokQaDispatchAction,
  grokQaDispatchAccessConfigured,
  GrokQaDispatchError,
  hasGrokQaDispatchAccess,
  parseGrokQaDispatchAction,
} from "./_shared/grok-qa-dispatch.mts";

const PRODUCTION_HOST = "coineasy-newscard.netlify.app";
const MAX_BODY_BYTES = 32 * 1024;

function json(body: unknown, status = 200, extraHeaders: HeadersInit = {}): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
      "Vary": "Authorization",
      "X-Content-Type-Options": "nosniff",
      ...extraHeaders,
    },
  });
}

async function requestBody(request: Request): Promise<unknown> {
  const declared = Number(request.headers.get("content-length") || 0);
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) {
    throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
  }
  const raw = await request.text();
  if (Buffer.byteLength(raw, "utf8") > MAX_BODY_BYTES) {
    throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
  }
  try {
    return JSON.parse(raw);
  } catch {
    throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
  }
}

export default async (request: Request, _context?: Context): Promise<Response> => {
  if (request.method !== "POST") {
    return json({ error: "method_not_allowed" }, 405, { Allow: "POST" });
  }
  const host = new URL(request.url).hostname.toLowerCase();
  if (host !== PRODUCTION_HOST && host !== "localhost" && host !== "127.0.0.1") {
    return json({ error: "invalid_grok_qa_dispatch_host" }, 421);
  }
  const getEnv = (name: string) => Netlify.env.get(name);
  if (!grokQaDispatchAccessConfigured(getEnv)) {
    return json({ error: "grok_qa_dispatch_not_configured" }, 503);
  }
  if (!hasGrokQaDispatchAccess(request, getEnv)) {
    return json({ error: "grok_qa_dispatch_auth_required" }, 401, {
      "WWW-Authenticate": 'Bearer realm="coineasy-grok-qa-dispatch"',
    });
  }
  const config = contentCatalogConfig(getEnv);
  if (!config) return json({ error: "grok_qa_dispatch_storage_not_configured" }, 503);

  try {
    const action = parseGrokQaDispatchAction(await requestBody(request));
    return json(await executeGrokQaDispatchAction(config, action, getEnv, fetch));
  } catch (error) {
    const code = error instanceof GrokQaDispatchError
      ? error.code
      : "grok_qa_dispatch_unavailable";
    const status = code === "invalid_grok_qa_dispatch_request"
      ? 400
      : code.includes("conflict")
        ? 409
        : 502;
    return json({ error: code }, status);
  }
};

export const config: Config = {
  path: "/api/grok-qa/dispatch",
};
