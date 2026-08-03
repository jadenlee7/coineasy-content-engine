import type { Config } from "@netlify/functions";

import {
  buzzDeliveryAccessConfigured,
  BuzzDeliveryError,
  executeBuzzDeliveryAction,
  hasValidBuzzDeliveryAccess,
  parseBuzzDeliveryAction,
} from "./_shared/buzz-delivery.mts";
import { contentCatalogConfig } from "./_shared/content-catalog.mts";

const MAX_BODY_BYTES = 2_048;

function json(body: unknown, status = 200, extraHeaders: HeadersInit = {}): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
      "Vary": "x-coineasy-buzz-delivery-key",
      "X-Content-Type-Options": "nosniff",
      ...extraHeaders,
    },
  });
}

async function requestBody(request: Request): Promise<unknown> {
  const declared = Number(request.headers.get("content-length") || 0);
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) {
    throw new BuzzDeliveryError("invalid_buzz_delivery_request");
  }
  const raw = await request.text();
  if (Buffer.byteLength(raw, "utf8") > MAX_BODY_BYTES) {
    throw new BuzzDeliveryError("invalid_buzz_delivery_request");
  }
  try {
    return JSON.parse(raw);
  } catch {
    throw new BuzzDeliveryError("invalid_buzz_delivery_request");
  }
}

export default async (request: Request): Promise<Response> => {
  if (request.method !== "POST") {
    return json({ error: "method_not_allowed" }, 405, { Allow: "POST" });
  }
  const getEnv = (name: string) => Netlify.env.get(name);
  if (!buzzDeliveryAccessConfigured(getEnv)) {
    return json({ error: "buzz_delivery_not_configured" }, 503);
  }
  if (!hasValidBuzzDeliveryAccess(request, getEnv)) {
    return json({ error: "buzz_delivery_auth_required" }, 401);
  }
  const config = contentCatalogConfig(getEnv);
  if (!config) return json({ error: "buzz_delivery_storage_not_configured" }, 503);

  try {
    const action = parseBuzzDeliveryAction(await requestBody(request));
    return json(await executeBuzzDeliveryAction(config, action));
  } catch (error) {
    const code = error instanceof BuzzDeliveryError
      ? error.code
      : "buzz_delivery_unavailable";
    const status = code === "invalid_buzz_delivery_request"
      ? 400
      : code === "buzz_delivery_receipt_conflict"
      ? 409
      : 502;
    return json({ error: code }, status);
  }
};

export const config: Config = {
  path: "/api/buzz-delivery/origintrail",
};
