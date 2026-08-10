import type { Config, Context } from "@netlify/functions";

import {
  buzzDeliveryAccessConfigured,
  BuzzDeliveryError,
  buzzDeliverySupabaseConfig,
  executeBuzzDeliveryAction,
  hasValidBuzzDeliveryAccess,
  parseBuzzDeliveryAction,
} from "./_shared/buzz-delivery.mts";
import { contentCatalogConfig } from "./_shared/content-catalog.mts";
import {
  materializeOriginTrailReviewPack,
  OriginTrailReviewPackError,
} from "./_shared/origintrail-review-pack.mts";

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

function reviewPackMaterializationEnabled(
  getEnv: (name: string) => string | undefined,
): boolean {
  const value = getEnv("BUZZ_REVIEW_PACK_MATERIALIZATION_ENABLED") || "false";
  if (value === "true") return true;
  if (value === "false" || value === "") return false;
  throw new BuzzDeliveryError("invalid_buzz_delivery_request");
}

export default async (request: Request, context: Context): Promise<Response> => {
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
    const requireReviewPack = reviewPackMaterializationEnabled(getEnv);
    if (action.action === "claim" && requireReviewPack) {
      if (action.attachment_sha256 === null) {
        throw new BuzzDeliveryError("invalid_buzz_delivery_request");
      }
      const pack = await materializeOriginTrailReviewPack(
        config,
        action.job_id,
        context.site.url,
      );
      if (pack.bannerSha256 !== action.attachment_sha256) {
        throw new BuzzDeliveryError("buzz_delivery_receipt_conflict");
      }
    }
    return json(await executeBuzzDeliveryAction(
      buzzDeliverySupabaseConfig(config, getEnv),
      action,
      fetch,
      AbortSignal.timeout(10_000),
      requireReviewPack,
    ));
  } catch (error) {
    const code = error instanceof BuzzDeliveryError || error instanceof OriginTrailReviewPackError
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
