import { createHash, timingSafeEqual } from "node:crypto";
import { contentCatalogConfig, isCatalogUuid } from "./content-catalog.mts";

// This gateway holds the existing database authority. The courier never does.
// It exposes no arbitrary RPC, workspace, destination, approval, or publisher.
const SHA = /^[a-f0-9]{40}$/;
const HASH = /^[a-f0-9]{64}$/;
const MAX_BODY_BYTES = 2048;
const OTHER_PRINCIPALS = [
  "API_SECRET", "STUDIO_ACCESS_TOKEN", "STUDIO_AUTOMATION_TOKEN",
  "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_CONTENT_QA_KEY", "SUPABASE_BUZZ_DELIVERY_KEY",
  "SUPABASE_BUZZ_REVIEW_KEY", "SUPABASE_BUZZ_SHADOW_KEY", "PUBLICATION_WORKER_TOKEN",
  "CONTENT_QA_CONNECTOR_TOKEN", "CONTENT_KPI_SYNC_TOKEN", "GROK_QA_RELAY_TOKEN",
  "GROK_QA_CONNECTOR_TOKEN", "GROK_QA_DISPATCH_TOKEN", "BUZZ_DELIVERY_TOKEN",
  "BUZZ_DELIVERY_WORKER_TOKEN", "BUZZ_REVIEW_TOKEN", "BUZZ_REVIEW_WORKER_TOKEN",
  "BUZZ_SHADOW_ACCESS_TOKEN", "BUZZ_SHADOW_TOKEN", "TYPEFULLY_API_KEY", "XAI_API_KEY",
  "X_BEARER_TOKEN", "TELEGRAM_REVIEW_BOT_TOKEN", "TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN",
];
const HANDLES: Record<string, string> = {
  yellow: "yellow", origintrail: "origin_trail", squid: "squidrouter", babylon: "babylonlabs_io",
};
const CLAIM_KEYS = [
  "outbox_id", "claim_token", "client_id", "kst_date", "content_item_id",
  "content_version_id", "source_item_id", "generate_job_id", "banner_sha256",
  "title", "telegram_copy", "x_copy", "source_url", "source_published_at",
];
type Env = (name: string) => string | undefined;
type Json = Record<string, unknown>;
type ReviewScope = { mode: "canary" | "daily"; content_version_id: string | null;
  packet_mode?: "button_card_v1" };
type Dependencies = { getEnv: Env; releaseSha: () => string | null; fetcher?: typeof fetch };

const json = (value: Json, status = 200) => Response.json(value, {
  status,
  headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
});
const record = (value: unknown): value is Json => Boolean(value)
  && typeof value === "object" && !Array.isArray(value);
const exact = (value: Json, keys: string[]) => Object.keys(value).length === keys.length
  && keys.every((key) => Object.hasOwn(value, key));
const digest = (value: string) => createHash("sha256").update(value).digest();

function configuredScope(getEnv: Env): ReviewScope | null {
  const mode = getEnv("CONTENT_OPS_REVIEW_MODE");
  const version = getEnv("CONTENT_OPS_REVIEW_CANARY_VERSION_ID");
  const packetMode = getEnv("CONTENT_OPS_REVIEW_PACKET_MODE") ?? "link_card";
  // Button cards are a separate default-OFF, exact-version canary. This
  // does not activate the sender, callback owner, approval or publication.
  if (packetMode === "button_card_v1") {
    return getEnv("CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED") === "true"
      && mode === "canary" && isCatalogUuid(version)
      && version === version.toLowerCase()
      ? { mode, content_version_id: version, packet_mode: "button_card_v1" }
      : null;
  }
  // The existing daily-review release exposes link cards only.
  if (packetMode !== "link_card") return null;
  if (mode === "daily" && (version === undefined || version === "")) {
    return { mode, content_version_id: null };
  }
  if (mode === "canary" && isCatalogUuid(version) && version === version.toLowerCase()) {
    return { mode, content_version_id: version };
  }
  // Missing mode must not silently widen a canary into a four-client run.
  return null;
}

async function readBody(req: Request): Promise<Json | null> {
  if (!req.body) return null;
  const reader = req.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      total += next.value.byteLength;
      if (total > MAX_BODY_BYTES) { await reader.cancel(); return null; }
      chunks.push(next.value);
    }
    const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    return record(parsed) ? parsed : null;
  } catch { return null; }
  finally { reader.releaseLock(); }
}

function rpcRequest(body: Json, workspaceId: string, scope: ReviewScope): { name: string; params: Json } | null {
  const params: Json = {
    target_workspace_id: workspaceId,
    target_content_version_id: scope.content_version_id,
  };
  if (body.action === "reconcile" && exact(body, ["action"])) {
    return { name: "content_ops_reconcile_daily", params };
  }
  if (!isCatalogUuid(body.claim_token)) return null;
  params.target_claim_token = body.claim_token;
  if (body.action === "claim" && exact(body, ["action", "claim_token"])) {
    return { name: "content_ops_claim_review", params };
  }
  if (!isCatalogUuid(body.outbox_id)) return null;
  params.target_outbox_id = body.outbox_id;
  if (body.action === "begin" && exact(body, ["action", "outbox_id", "claim_token", "packet_sha256"])
    && typeof body.packet_sha256 === "string" && HASH.test(body.packet_sha256)) {
    params.target_packet_sha256 = body.packet_sha256;
    return { name: "content_ops_begin_review_send", params };
  }
  if (!scope.packet_mode && body.action === "finish" && exact(body, ["action", "outbox_id", "claim_token", "outcome", "message_id"])
    && ["sent", "rejected", "delivery_unknown"].includes(String(body.outcome))
    && (body.outcome === "sent"
      ? Number.isSafeInteger(body.message_id) && Number(body.message_id) > 0
      : body.message_id === null)) {
    params.target_outcome = body.outcome;
    params.target_message_id = body.message_id;
    return { name: "content_ops_finish_review_send", params };
  }
  return null;
}

function claimProjection(value: unknown, claimToken: unknown): Json | null {
  if (!record(value) || !exact(value, CLAIM_KEYS)) return null;
  if (!["outbox_id", "claim_token", "content_item_id", "content_version_id", "source_item_id", "generate_job_id"]
    .every((key) => isCatalogUuid(value[key]))) return null;
  if (value.claim_token !== claimToken || typeof value.client_id !== "string"
    || !Object.hasOwn(HANDLES, value.client_id)
    || typeof value.kst_date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value.kst_date)
    || typeof value.banner_sha256 !== "string" || !HASH.test(value.banner_sha256)) return null;
  for (const [key, maximum] of [["title", 240], ["telegram_copy", 3400], ["x_copy", 1000]] as const) {
    if (typeof value[key] !== "string" || value[key].length > maximum) return null;
  }
  if (typeof value.source_url !== "string"
    || !new RegExp(`^https://x\\.com/${HANDLES[value.client_id]}/status/[0-9]{1,30}$`, "i").test(value.source_url)
    || typeof value.source_published_at !== "string"
    || !/(Z|[+-]\d{2}:\d{2})$/.test(value.source_published_at)
    || !Number.isFinite(Date.parse(value.source_published_at))) return null;
  // Never relay unexpected provider payload, private source fields, or DB error text.
  return Object.fromEntries(CLAIM_KEYS.map((key) => [key, value[key]]));
}

export function createContentOpsReviewHandler(deps: Dependencies) {
  return async (req: Request): Promise<Response> => {
    if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);
    if (deps.getEnv("CONTENT_OPS_GATEWAY_ENABLED") !== "true") {
      return json({ error: "content_ops_disabled" }, 503);
    }
    const host = new URL(req.url).hostname;
    if (host !== "coineasy-newscard.netlify.app" || deps.getEnv("CONTEXT") !== "production") {
      return json({ error: "content_ops_production_host_required" }, 421);
    }
    const release = deps.releaseSha();
    if (!release || !SHA.test(release) || deps.getEnv("CONTENT_OPS_REVIEW_RELEASE_SHA") !== release
      || req.headers.get("x-content-ops-expected-release-sha") !== release) {
      return json({ error: "content_ops_release_mismatch" }, 503);
    }
    const token = deps.getEnv("CONTENT_OPS_GATEWAY_TOKEN") || "";
    if (!/^[A-Za-z0-9_-]{32,256}$/.test(token)
      || OTHER_PRINCIPALS.some((name) => deps.getEnv(name) === token)) {
      return json({ error: "content_ops_token_not_configured" }, 503);
    }
    const provided = req.headers.get("authorization") || "";
    if (!timingSafeEqual(digest(provided), digest(`Bearer ${token}`))) {
      return json({ error: "invalid_token" }, 401);
    }
    const scope = configuredScope(deps.getEnv);
    if (!scope) return json({ error: "content_ops_scope_not_configured" }, 503);
    if (req.headers.get("x-content-ops-mode") !== scope.mode
      || req.headers.get("x-content-ops-version-id") !== scope.content_version_id
      || req.headers.get("x-content-ops-packet-mode") !== (scope.packet_mode ?? null)) {
      return json({ error: "content_ops_scope_mismatch" }, 409);
    }
    if (!/^application\/json(?:\s*;|$)/i.test(req.headers.get("content-type") || "")) {
      return json({ error: "invalid_request" }, 400);
    }
    const body = await readBody(req);
    if (!body) return json({ error: "invalid_request" }, 400);
    const cfg = contentCatalogConfig(deps.getEnv);
    if (!cfg) return json({ error: "content_ops_database_not_configured" }, 503);
    const rpc = rpcRequest(body, cfg.workspaceId, scope);
    if (!rpc) return json({ error: "invalid_request" }, 400);
    try {
      // At most one RPC. A lost acknowledgement must never cause an implicit retry.
      const result = await (deps.fetcher || fetch)(`${cfg.supabaseUrl}/rest/v1/rpc/${rpc.name}`, {
        method: "POST",
        headers: { apikey: cfg.serviceRoleKey, Authorization: `Bearer ${cfg.serviceRoleKey}`, "Content-Type": "application/json" },
        body: JSON.stringify(rpc.params), signal: AbortSignal.timeout(10_000), redirect: "error",
      });
      if (!result.ok) return json({ error: "content_ops_rpc_unavailable" }, 503);
      const value: unknown = await result.json();
      const receipt = { ok: true, release_sha: release, scope };
      if (body.action === "reconcile" && Number.isSafeInteger(value) && Number(value) >= 0
        && Number(value) <= (scope.mode === "canary" ? 1 : 4)) {
        return json({ ...receipt, queued: value });
      }
      if (body.action === "claim") {
        if (value === null) return json({ ...receipt, claim: null });
        const claim = claimProjection(value, body.claim_token);
        if (claim && (scope.mode === "daily" || claim.content_version_id === scope.content_version_id)) {
          return json({ ...receipt, claim });
        }
      }
      if (["begin", "finish"].includes(String(body.action)) && record(value) && typeof value.accepted === "boolean") {
        return json({ ...receipt, accepted: value.accepted });
      }
      return json({ error: "content_ops_invalid_receipt" }, 503);
    } catch { return json({ error: "content_ops_rpc_unavailable" }, 503); }
  };
}
