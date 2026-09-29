import { createHash, timingSafeEqual } from "node:crypto";

type Env = (name: string) => string | undefined;

// Shared by the mutation gateway and the isolated release readback. Neither
// route may accept a Studio/admin/provider credential as the courier token.
export const CONTENT_OPS_OTHER_PRINCIPALS = [
  "API_SECRET", "STUDIO_ACCESS_TOKEN", "STUDIO_AUTOMATION_TOKEN",
  "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_CONTENT_QA_KEY", "SUPABASE_BUZZ_DELIVERY_KEY",
  "SUPABASE_BUZZ_REVIEW_KEY", "SUPABASE_BUZZ_SHADOW_KEY", "PUBLICATION_WORKER_TOKEN",
  "CONTENT_QA_CONNECTOR_TOKEN", "CONTENT_KPI_SYNC_TOKEN", "GROK_QA_RELAY_TOKEN",
  "GROK_QA_CONNECTOR_TOKEN", "GROK_QA_DISPATCH_TOKEN", "BUZZ_DELIVERY_TOKEN",
  "BUZZ_DELIVERY_WORKER_TOKEN", "BUZZ_REVIEW_TOKEN", "BUZZ_REVIEW_WORKER_TOKEN",
  "BUZZ_SHADOW_ACCESS_TOKEN", "BUZZ_SHADOW_TOKEN", "TYPEFULLY_API_KEY", "XAI_API_KEY",
  "X_BEARER_TOKEN", "TELEGRAM_REVIEW_BOT_TOKEN", "TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN",
] as const;

export function configuredContentOpsGatewayToken(getEnv: Env): string | null {
  const token = getEnv("CONTENT_OPS_GATEWAY_TOKEN") || "";
  if (!/^[A-Za-z0-9_-]{32,256}$/.test(token)
    || CONTENT_OPS_OTHER_PRINCIPALS.some((name) => getEnv(name) === token)) return null;
  return token;
}

export function hasContentOpsGatewayAccess(req: Request, token: string): boolean {
  const digest = (value: string) => createHash("sha256").update(value).digest();
  return timingSafeEqual(digest(req.headers.get("authorization") || ""), digest(`Bearer ${token}`));
}
