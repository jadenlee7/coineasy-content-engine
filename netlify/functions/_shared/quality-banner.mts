/** Natural initial generation only. No provider calls, grants or publication. */
import { createHash } from "node:crypto";
import { ContentCatalogError, type ContentCatalogConfig } from "./content-catalog.mts";
import { normalizeXImageUrl } from "./source-content.mts";

export const QUALITY_BANNER_POLICY = "official-reference-sunburst-max@2";
export const MAX_QUALITY_BANNER_BYTES = 10_000_000;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const SHA = /^[a-f0-9]{64}$/;
const HANDLES: Record<string, string> = {
  yellow: "Yellow", babylon: "babylonlabs_io", origintrail: "origin_trail",
};
export type QualityGenerationContext = {
  job_id: string; request_id: string; workspace_id: string; client_id: string;
  source_item_id: string; source_published_at: string; source_sha256: string;
  source_content: string; source_url: string; source_image_url: string;
};
const KEYS = ["job_id", "request_id", "workspace_id", "client_id", "source_item_id",
  "source_published_at", "source_sha256", "source_content", "source_url", "source_image_url"] as const;

export function qualityBannerEnabled(value: string | undefined): boolean {
  if (value !== undefined && value !== "true" && value !== "false") {
    throw new ContentCatalogError("quality_banner_configuration_invalid");
  }
  return value === "true";
}

export function qualityBannerClient(clientId: string): boolean {
  return Object.hasOwn(HANDLES, clientId);
}

export function qualityUpstreamError(text: string): { error: string; status: number } {
  let code: unknown;
  try { code = (JSON.parse(text) as { detail?: unknown }).detail; } catch { /* fixed unknown below */ }
  if (typeof code !== "string" || code.length > 80
    || !/^(?:quality_banner_|quality_news_|news_generation_)[a-z0-9_]+$/.test(code)) {
    return { error: "quality_banner_upstream_unknown", status: 409 };
  }
  const recoverable = code === "quality_banner_pending" || code === "quality_banner_collect_unknown"
    || /(?:configuration|config|release)/.test(code);
  return { error: code, status: recoverable ? 503 : 409 };
}

/** Replay validates immutable identities but never re-opens freshness or spend. */
export function parseQualityGenerationContext(value: unknown, expected: {
  workspaceId: string; clientId: string; requestId: string;
  sourceContent: string; sourceUrl: string; sourceImageUrl: string;
  allowLogoOnly?: boolean;
}): QualityGenerationContext {
  const v = value as Partial<QualityGenerationContext> | null;
  if (!v || typeof v !== "object" || Array.isArray(v)
    || Object.keys(v).sort().join() !== [...KEYS].sort().join()
    || KEYS.some((key) => typeof v[key] !== "string")
    || !qualityBannerClient(expected.clientId)
    || [v.job_id, v.request_id, v.workspace_id, v.source_item_id].some((id) => !UUID.test(id!))
    || v.workspace_id !== expected.workspaceId || v.client_id !== expected.clientId
    || v.request_id !== expected.requestId || v.source_content !== expected.sourceContent
    || v.source_content!.length < 10 || v.source_content!.length > 20_000
    || v.source_url !== expected.sourceUrl
    || !new RegExp(`^https://x\\.com/${HANDLES[expected.clientId]}/status/[0-9]{1,19}$`).test(v.source_url!)
    || !SHA.test(v.source_sha256!)
    || v.source_sha256 !== createHash("sha256").update(v.source_content!, "utf8").digest("hex")
    || !/(?:Z|[+-][0-9]{2}:[0-9]{2})$/.test(v.source_published_at!)
    || !Number.isFinite(Date.parse(v.source_published_at!))
    || !((expected.allowLogoOnly === true && v.source_image_url === "" && expected.sourceImageUrl === "")
      || (Boolean(normalizeXImageUrl(v.source_image_url!))
        && normalizeXImageUrl(v.source_image_url!) === expected.sourceImageUrl))) {
    throw new ContentCatalogError("quality_banner_context_invalid");
  }
  // Stable, canonical field order; DB media spelling is verified above before
  // canonicalizing the exact URL sent to the renderer.
  return {
    job_id: v.job_id!, request_id: v.request_id!, workspace_id: v.workspace_id!,
    client_id: v.client_id!, source_item_id: v.source_item_id!,
    source_published_at: v.source_published_at!, source_sha256: v.source_sha256!,
    source_content: v.source_content!, source_url: v.source_url!,
    source_image_url: expected.sourceImageUrl,
  };
}

export async function getQualityGenerationContext(config: ContentCatalogConfig,
  expected: Parameters<typeof parseQualityGenerationContext>[1],
  fetcher: typeof fetch, signal: AbortSignal): Promise<QualityGenerationContext> {
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/get_quality_banner_generation_context`, {
      method: "POST", headers: { apikey: config.serviceRoleKey,
        Authorization: `Bearer ${config.serviceRoleKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({ target_workspace_id: expected.workspaceId,
        target_client_id: expected.clientId, target_request_id: expected.requestId }), signal,
    });
  } catch { throw new ContentCatalogError("quality_banner_context_unavailable"); }
  if (!response.ok) throw new ContentCatalogError("quality_banner_context_unavailable");
  let value: unknown;
  try { value = await response.json(); }
  catch { throw new ContentCatalogError("quality_banner_context_unavailable"); }
  return parseQualityGenerationContext(value, expected);
}

export function qualityContextHash(context: QualityGenerationContext): string {
  return createHash("sha256").update(JSON.stringify(KEYS.map((key) => [key, context[key]])), "utf8").digest("hex");
}

export function validQualityBannerMetadata(spec: Record<string, unknown>, allowLogoOnly = false): boolean {
  const q = spec.quality_banner as Record<string, unknown> | undefined;
  const logoOnly = allowLogoOnly === true && q?.reference_mode === "official_logo_only";
  const keys = ["policy", "request_sha256", "png_sha256", "width", "height"];
  if (logoOnly) keys.push("reference_mode", "official_logo_sha256");
  return !!q && typeof q === "object" && !Array.isArray(q)
    && Object.keys(q).sort().join() === keys.sort().join()
    && (!logoOnly || (typeof q.official_logo_sha256 === "string" && SHA.test(q.official_logo_sha256)))
    && q.policy === QUALITY_BANNER_POLICY
    && typeof q.request_sha256 === "string" && SHA.test(q.request_sha256)
    && typeof q.png_sha256 === "string" && SHA.test(q.png_sha256)
    && Number.isSafeInteger(q.width) && Number(q.width) >= 2880 && Number(q.width) <= 4096
    && q.height === q.width
    && spec.render_strategy === (logoOnly ? "official_logo_announcement" : "reference_first_quality")
    && spec.template_version === QUALITY_BANNER_POLICY
    && spec.asset_pack_version === (logoOnly ? "official-logo-only@1" : "official-reference-source-logo@1")
    && spec.brand_profile_policy_version === "official-reference-quality@1"
    && spec.channel_profile === "x_square"
    && !Object.hasOwn(spec, "visual_design_profile_id")
    && !Object.hasOwn(spec, "visual_design_profile_version");
}

export function validQualityBannerPng(spec: Record<string, unknown>, bytes: ArrayBuffer,
  dimensions: { width: number; height: number }, allowLogoOnly = false): boolean {
  const q = spec.quality_banner as Record<string, unknown>;
  return validQualityBannerMetadata(spec, allowLogoOnly) && bytes.byteLength <= MAX_QUALITY_BANNER_BYTES
    && q.width === dimensions.width && q.height === dimensions.height
    && q.png_sha256 === createHash("sha256").update(Buffer.from(bytes)).digest("hex");
}

export async function validQualityBannerPixels(bytes: ArrayBuffer): Promise<boolean> {
  try {
    const { default: sharp } = await import("sharp");
    const image = sharp(Buffer.from(bytes), { failOn: "error", limitInputPixels: 4096 * 4096 });
    const meta = await image.metadata();
    if (meta.format !== "png" || (meta.pages !== undefined && meta.pages !== 1)
      || !meta.width || meta.width < 2880 || meta.width > 4096 || meta.height !== meta.width) return false;
    // stats decodes every pixel; neither a valid IHDR nor a provider hash alone
    // proves this is complete, non-transparent PNG artwork.
    return (await image.stats()).isOpaque === true;
  } catch { return false; }
}
