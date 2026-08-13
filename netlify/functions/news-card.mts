import type { Config, Context } from "@netlify/functions";
import { createHash, randomUUID } from "node:crypto";
import {
  canonicalXStatusUrl,
  hasVerifiedOfficialSquidXProvenance,
  normalizeXImageUrl,
  resolveSourceInput,
  SourceInputError,
  type ResolvedSource,
} from "./_shared/source-content.mts";
import { buildChannelCopy } from "./_shared/channel-copy.mts";
import { evaluateBrandQuality } from "./_shared/brand-quality.mts";
import {
  evaluateFactCheck,
  validatedFactCheckReport,
} from "./_shared/fact-check.mts";
import {
  hasValidStudioAutomationAccess,
  requireStudioGenerationAccess,
} from "./_shared/studio-session.mts";
import {
  parseStyleReferencePack,
  StyleReferenceInputError,
  styleReferenceAudit,
  type StyleReference,
  type StyleReferencePack,
} from "./_shared/style-references.mts";
import {
  brandReviewGuidanceAudit,
  emptyBrandReviewGuidance,
  getBrandReviewGuidance,
} from "./_shared/content-reviews.mts";
import {
  contentCatalogConfig,
  contentStoragePath,
  ContentCatalogError,
  downloadCatalogPng,
  findGeneratedContent,
  pngDimensions,
  recordGeneratedContent,
  removeContentAssets,
  type ContentCatalogClient,
  type ContentCatalogLookup,
  uploadNewsCard,
  verifyContentStorageScope,
  verifyPrivateContentBucket,
} from "./_shared/content-catalog.mts";

type NewsCardRequest = {
  source_content?: unknown;
  source_type?: unknown;
  source_url?: unknown;
  mock_mode?: unknown;
  template_style?: unknown;
  source_image_url?: unknown;
  style_references?: unknown;
  style_reference_pack_hash?: unknown;
};

type RailwayNewsCardResponse = {
  client_id: string;
  content_type: string;
  spec: Record<string, unknown>;
  png_path: string;
  template_style: string;
  requested_template_style?: string;
  source_image_used?: boolean;
  source_image_url?: unknown;
  source_image_sha256?: unknown;
  source_visual_path?: unknown;
  figma_template?: unknown;
  manifest_path: string;
  duration_ms: number;
};

type FigmaTemplateReference = {
  registry_schema_version: "1.0";
  file_key: "hsRSASQjEMxl5NMLH9y5Wm";
  file_name: "CoinEasy Management";
  page_name: "Daily content";
  node_id: string;
  frame_name: string;
  status: "approved";
  version: string;
};

const ALLOWED_CLIENTS = new Set<ContentCatalogClient>([
  "yellow",
  "origintrail",
  "squid",
  "babylon",
]);
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const SQUID_SOURCE_NATIVE_POLICY = "official_source_native_v1";
export const SQUID_CREATIVE_FAMILY_POLICY_VERSION = "squid-visual-routing@1";
export const SQUID_GENERATED_TEMPLATE_VERSION = "squid-generated-gtm@5";
export const SQUID_VISUAL_REFERENCE_PACK_VERSION = 2;
export const SQUID_GENERATED_DESIGN_PROFILE_ID = "squid/full-bleed-character-type";
export const SQUID_GENERATED_DESIGN_PROFILE_VERSION = 2;
export const NEWS_BRAND_PROFILE_POLICY_VERSION = "client-news-brand-profiles@1";
type StandardNewsBrandClient = Exclude<ContentCatalogClient, "squid">;
type StandardNewsBrandProfile = {
  designProfileId: string;
  designProfileVersion: number;
  brandTokensVersion: string;
  assetPackVersion: string;
  classicTemplateVersion: number;
};
export const NEWS_BRAND_PROFILES: Record<StandardNewsBrandClient, StandardNewsBrandProfile> = {
  yellow: {
    designProfileId: "yellow/institutional-market-infrastructure",
    designProfileVersion: 2,
    brandTokensVersion: "yellow-brand-tokens@1",
    assetPackVersion: "yellow-official-brand-assets@1",
    classicTemplateVersion: 2,
  },
  origintrail: {
    designProfileId: "origintrail/verifiable-knowledge",
    designProfileVersion: 1,
    brandTokensVersion: "origintrail-brand-tokens@1",
    assetPackVersion: "origintrail-official-brand-assets@1",
    classicTemplateVersion: 1,
  },
  babylon: {
    designProfileId: "babylon/bitcoin-native-infrastructure",
    designProfileVersion: 1,
    brandTokensVersion: "babylon-brand-tokens@1",
    assetPackVersion: "babylon-official-brand-assets@1",
    classicTemplateVersion: 1,
  },
};

function standardNewsTemplateVersion(
  clientId: StandardNewsBrandClient,
  templateStyle: string,
): string {
  const version = templateStyle === "classic"
    ? NEWS_BRAND_PROFILES[clientId].classicTemplateVersion
    : 1;
  return `${clientId}-news-${templateStyle}@${version}`;
}
const SQUID_VISUAL_REFERENCE_PACKS = {
  editorial_big_type: {
    id: "squid/editorial-big-type",
    statusUrls: ["https://x.com/squidrouter/status/2079999207956500971"],
  },
  milestone_metric: {
    id: "squid/milestone-metric",
    statusUrls: ["https://x.com/squidrouter/status/2082889008385044897"],
  },
  status_progress: {
    id: "squid/status-progress",
    statusUrls: ["https://x.com/squidrouter/status/2080668216792129968"],
  },
  product_proof: {
    id: "squid/product-proof",
    statusUrls: [
      "https://x.com/squidrouter/status/2079628218403803481",
      "https://x.com/squidrouter/status/2083266484789514640",
    ],
  },
  worldbuilding: {
    id: "squid/worldbuilding",
    statusUrls: [
      "https://x.com/squidrouter/status/2083583547353501977",
      "https://x.com/squidrouter/status/2073032336384356666",
    ],
  },
} as const;
const NETLIFY_REQUEST_BUDGET_MS = 58_000;
const RAILWAY_GENERATION_BUDGET_MS = 38_000;
// A 3 MB PNG expands to roughly 4 MB when embedded as base64 in JSON. Keep the
// binary ceiling comfortably below Netlify's 6 MB synchronous response limit.
export const MAX_NEWS_CARD_BYTES = 3_000_000;
const NEWS_CARD_PERSISTENCE_RESERVE_MS = 18_000;
const FIGMA_TEMPLATE_NODES: Partial<Record<ContentCatalogClient, {
  nodeId: string;
  frameName: string;
  version: string;
}>> = {
  squid: {
    nodeId: "1479:1954",
    frameName: "[KEEP] Banner_Squid_Sample",
    version: "2026-07-30.1",
  },
  yellow: {
    nodeId: "1966:2389",
    frameName: "[KEEP] Banner_Yellow_Sample",
    version: "2026-08-13.1",
  },
};

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}

function cleanBaseUrl(value: string): string {
  return value.replace(/\/+$/, "");
}

export function deadlineSignal(
  deadline: number,
  maximumMs: number,
  reserveMs = 0,
): AbortSignal {
  const remaining = deadline - Date.now() - reserveMs;
  if (remaining <= 250) throw new ContentCatalogError("news_card_deadline_exceeded");
  return AbortSignal.timeout(Math.min(maximumMs, remaining));
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function normalizedFigmaTemplate(
  value: unknown,
  clientId: ContentCatalogClient,
  templateStyle: string,
): FigmaTemplateReference | null {
  if (value === null || value === undefined) return null;
  if (
    templateStyle !== "classic"
    || !value
    || typeof value !== "object"
    || Array.isArray(value)
  ) return null;
  const reference = value as Record<string, unknown>;
  const expected = FIGMA_TEMPLATE_NODES[clientId];
  if (
    !expected
    || Object.keys(reference).sort().join(",")
      !== [
        "file_key",
        "file_name",
        "frame_name",
        "node_id",
        "page_name",
        "registry_schema_version",
        "status",
        "version",
      ].sort().join(",")
    || reference.registry_schema_version !== "1.0"
    || reference.file_key !== "hsRSASQjEMxl5NMLH9y5Wm"
    || reference.file_name !== "CoinEasy Management"
    || reference.page_name !== "Daily content"
    || reference.node_id !== expected.nodeId
    || reference.frame_name !== expected.frameName
    || reference.status !== "approved"
    || reference.version !== expected.version
  ) return null;
  return reference as FigmaTemplateReference;
}

type NewsCardRequestHashInput = {
  clientId: string;
  sourceContent: string;
  sourceType: string;
  sourceUrl: string;
  mockMode: boolean;
  templateStyle: string;
  sourceImageUrl?: string;
  styleReferences?: StyleReference[];
  styleReferencePackHash?: string;
};

function buildNewsCardRequestHash(
  input: NewsCardRequestHashInput,
  includeBrandPolicy: boolean,
): string {
  const payload: Record<string, unknown> = {
    client_id: input.clientId,
    source_content: input.sourceContent,
    source_type: input.sourceType,
    source_url: input.sourceUrl,
    mock_mode: input.mockMode,
    template_style: input.templateStyle,
    style_references: input.styleReferences || [],
    style_reference_pack_hash: input.styleReferencePackHash || "",
  };
  // The Squid generated visual is server-routed from immutable source text.
  // Bind the routing policy to idempotency so a reviewed older family cannot
  // be silently reused after the deterministic policy changes.
  if (input.clientId === "squid" && includeBrandPolicy) {
    payload.creative_family_policy_version = SQUID_CREATIVE_FAMILY_POLICY_VERSION;
    payload.visual_reference_pack_version = SQUID_VISUAL_REFERENCE_PACK_VERSION;
    // A generated-stage geometry change must not replay a durable PNG rendered
    // with an older template. Remix keeps its separate source-native contract.
    if (input.templateStyle !== "remix") {
      payload.template_version = SQUID_GENERATED_TEMPLATE_VERSION;
      payload.visual_design_profile_id = SQUID_GENERATED_DESIGN_PROFILE_ID;
      payload.visual_design_profile_version = SQUID_GENERATED_DESIGN_PROFILE_VERSION;
    }
  } else if (includeBrandPolicy && input.clientId in NEWS_BRAND_PROFILES) {
    const clientId = input.clientId as StandardNewsBrandClient;
    const profile = NEWS_BRAND_PROFILES[clientId];
    payload.brand_profile_policy_version = NEWS_BRAND_PROFILE_POLICY_VERSION;
    payload.brand_tokens_version = profile.brandTokensVersion;
    payload.template_version = standardNewsTemplateVersion(clientId, input.templateStyle);
    payload.asset_pack_version = profile.assetPackVersion;
    payload.visual_design_profile_id = profile.designProfileId;
    payload.visual_design_profile_version = profile.designProfileVersion;
  }
  // The automation-only pinned URL extends the idempotency identity when it
  // is present.
  if (input.sourceImageUrl) payload.source_image_url = input.sourceImageUrl;
  return createHash("sha256").update(JSON.stringify(payload), "utf8").digest("hex");
}

export function newsCardRequestHash(input: NewsCardRequestHashInput): string {
  return buildNewsCardRequestHash(input, true);
}

function legacyNewsCardRequestHash(input: NewsCardRequestHashInput): string {
  return buildNewsCardRequestHash(input, false);
}

function isLegacySquidCreativeRecord(existing: ContentCatalogLookup): boolean {
  const spec = objectValue(existing.content.spec);
  return !Object.hasOwn(spec, "creative_family_policy_version")
    && !Object.hasOwn(spec, "creative_family");
}

function isLegacyStandardBrandRecord(existing: ContentCatalogLookup): boolean {
  const spec = objectValue(existing.content.spec);
  return !Object.hasOwn(spec, "brand_profile_policy_version");
}

export function isOfficialSquidXStatusUrl(value: string): boolean {
  const canonical = canonicalXStatusUrl(value);
  if (!canonical) return false;
  return /^\/squidrouter\/status\/\d+$/i.test(new URL(canonical).pathname);
}

export function validSquidNativeOutputSpec(
  spec: Record<string, unknown>,
  dimensions: { width: number; height: number },
): boolean {
  const sourceWidth = spec.source_image_width;
  const sourceHeight = spec.source_image_height;
  const outputWidth = spec.output_width;
  const outputHeight = spec.output_height;
  if (
    spec.output_policy !== SQUID_SOURCE_NATIVE_POLICY
    || typeof sourceWidth !== "number"
    || !Number.isSafeInteger(sourceWidth)
    || typeof sourceHeight !== "number"
    || !Number.isSafeInteger(sourceHeight)
    || typeof outputWidth !== "number"
    || !Number.isSafeInteger(outputWidth)
    || typeof outputHeight !== "number"
    || !Number.isSafeInteger(outputHeight)
  ) return false;
  const sourceW = sourceWidth;
  const sourceH = sourceHeight;
  const outputW = outputWidth;
  const outputH = outputHeight;
  if (
    sourceW < 1 || sourceW > 1_800
    || sourceH < 1 || sourceH > 1_800
    || outputW < 1 || outputW > 1_200
    || outputH < 1 || outputH > 1_200
  ) return false;
  const scale = Math.min(1, 1_200 / Math.max(sourceW, sourceH));
  return outputW === Math.max(1, Math.round(sourceW * scale))
    && outputH === Math.max(1, Math.round(sourceH * scale))
    && dimensions.width === outputW
    && dimensions.height === outputH;
}

export function validSquidCreativeMetadata(
  spec: Record<string, unknown>,
  templateStyle: string,
): boolean {
  const family = spec.creative_family;
  if (
    typeof family !== "string"
    || !(family in SQUID_VISUAL_REFERENCE_PACKS)
  ) return false;
  const reference = SQUID_VISUAL_REFERENCE_PACKS[
    family as keyof typeof SQUID_VISUAL_REFERENCE_PACKS
  ];
  const expectedStrategy = templateStyle === "remix"
    ? "source_remix"
    : templateStyle === "classic"
    ? "generated_gtm"
    : "";
  const expectedTemplateVersion = templateStyle === "remix"
    ? "squid-source-remix@1"
    : SQUID_GENERATED_TEMPLATE_VERSION;
  const expectedAssetPackVersion = templateStyle === "remix"
    ? "official-source-media@1"
    : "squid-local-approved@1";
  if (
    !expectedStrategy
    || spec.render_strategy !== expectedStrategy
    || spec.creative_family_policy_version !== SQUID_CREATIVE_FAMILY_POLICY_VERSION
    || spec.visual_reference_pack_id !== reference.id
    || spec.visual_reference_pack_version !== SQUID_VISUAL_REFERENCE_PACK_VERSION
    || spec.visual_automatic !== true
    || spec.brand_tokens_version !== "squid-brand-tokens@1"
    || spec.template_version !== expectedTemplateVersion
    || spec.asset_pack_version !== expectedAssetPackVersion
    || ![
      "bagoss_condensed_licensed",
      "pretendard_fallback",
    ].includes(String(spec.font_status || ""))
    || (templateStyle === "classic" && family === "worldbuilding")
  ) return false;
  if (
    !Array.isArray(spec.visual_reference_status_urls)
    || spec.visual_reference_status_urls.length !== reference.statusUrls.length
    || spec.visual_reference_status_urls.some(
      (value, index) => value !== reference.statusUrls[index],
    )
  ) return false;
  const expectedProfile = templateStyle === "remix" ? "source_native" : "x_square";
  if (spec.channel_profile !== expectedProfile) return false;
  const hasDesignProfileId = Object.hasOwn(spec, "visual_design_profile_id");
  const hasDesignProfileVersion = Object.hasOwn(spec, "visual_design_profile_version");
  if (templateStyle === "classic") {
    if (
      spec.visual_design_profile_id !== SQUID_GENERATED_DESIGN_PROFILE_ID
      || spec.visual_design_profile_version !== SQUID_GENERATED_DESIGN_PROFILE_VERSION
    ) return false;
  } else if (hasDesignProfileId || hasDesignProfileVersion) {
    return false;
  }
  if (
    spec.visual_metric !== undefined
    && (
      typeof spec.visual_metric !== "string"
      || !spec.visual_metric.trim()
      || spec.visual_metric.length > 32
      || family !== "milestone_metric"
    )
  ) return false;
  return true;
}

export function validStandardNewsBrandMetadata(
  spec: Record<string, unknown>,
  clientId: string,
  templateStyle: string,
): boolean {
  if (!(clientId in NEWS_BRAND_PROFILES)) return false;
  if (!["classic", "editorial", "signal", "remix"].includes(templateStyle)) {
    return false;
  }
  const standardClientId = clientId as StandardNewsBrandClient;
  const profile = NEWS_BRAND_PROFILES[standardClientId];
  return spec.brand_profile_policy_version === NEWS_BRAND_PROFILE_POLICY_VERSION
    && spec.render_strategy === (templateStyle === "remix" ? "source_remix" : "brand_native")
    && spec.channel_profile === "x_square"
    && spec.brand_tokens_version === profile.brandTokensVersion
    && spec.template_version === standardNewsTemplateVersion(standardClientId, templateStyle)
    && spec.asset_pack_version === profile.assetPackVersion
    && spec.visual_design_profile_id === profile.designProfileId
    && spec.visual_design_profile_version === profile.designProfileVersion;
}

function catalogRequestHash(existing: ContentCatalogLookup): string | null {
  const contentHash = existing.content.request_hash;
  const generationHash = existing.generationMeta.request_hash;
  return typeof contentHash === "string"
    && SHA256_PATTERN.test(contentHash)
    && contentHash === generationHash
    ? contentHash
    : null;
}

function isRailwayNewsCardResponse(
  value: unknown,
  clientId: string,
): value is RailwayNewsCardResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const result = value as Partial<RailwayNewsCardResponse>;
  return result.client_id === clientId
    && result.content_type === "news_card"
    && Boolean(result.spec)
    && typeof result.spec === "object"
    && !Array.isArray(result.spec)
    && typeof result.png_path === "string"
    && typeof result.template_style === "string"
    && (
      result.requested_template_style === undefined
      || typeof result.requested_template_style === "string"
    )
    && typeof result.manifest_path === "string"
    && typeof result.duration_ms === "number"
    && Number.isFinite(result.duration_ms)
    && result.duration_ms >= 0;
}

export function validNewsTemplatePair(
  clientId: string,
  submittedTemplateStyle: string,
  reportedRequestedTemplateStyle: string,
  reportedActualTemplateStyle: string,
): boolean {
  const allowed = new Set(["remix", "classic", "editorial", "signal"]);
  if (
    !allowed.has(submittedTemplateStyle)
    || !allowed.has(reportedRequestedTemplateStyle)
    || !allowed.has(reportedActualTemplateStyle)
    || reportedRequestedTemplateStyle !== submittedTemplateStyle
  ) {
    return false;
  }
  if (submittedTemplateStyle === "remix") {
    return reportedActualTemplateStyle === "remix"
      || (clientId !== "squid" && reportedActualTemplateStyle === "classic");
  }
  if (
    clientId === "squid"
    && (submittedTemplateStyle === "editorial" || submittedTemplateStyle === "signal")
  ) {
    return reportedActualTemplateStyle === "classic";
  }
  return reportedActualTemplateStyle === submittedTemplateStyle;
}

export function storedNewsTemplatePair(
  clientId: string,
  render: Record<string, unknown>,
): { requestedTemplateStyle: string; actualTemplateStyle: string } | null {
  const requestedTemplateStyle = render.requested_template_style;
  const actualTemplateStyle = render.template_style;
  if (
    typeof requestedTemplateStyle !== "string"
    || typeof actualTemplateStyle !== "string"
    || !validNewsTemplatePair(
      clientId,
      requestedTemplateStyle,
      requestedTemplateStyle,
      actualTemplateStyle,
    )
  ) {
    return null;
  }
  return { requestedTemplateStyle, actualTemplateStyle };
}

async function catalogRetryResponse(
  existing: ContentCatalogLookup,
  storageConfig: NonNullable<ReturnType<typeof contentCatalogConfig>>,
  clientId: ContentCatalogClient,
  deadline: number,
  expectedVerifiedSourceImageUrl = "",
): Promise<Record<string, unknown>> {
  if (existing.assets.length !== 1) {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  const factCheck = validatedFactCheckReport(existing.generationMeta.fact_check, "daily_news");
  if (!factCheck) {
    throw new ContentCatalogError("fact_check_regeneration_required");
  }
  const replayAsset = existing.assets[0];
  if (
    typeof replayAsset.byteSize !== "number"
    || !Number.isSafeInteger(replayAsset.byteSize)
    || replayAsset.byteSize < 24
  ) {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  if (replayAsset.byteSize > MAX_NEWS_CARD_BYTES) {
    throw new ContentCatalogError("generated_image_too_large");
  }
  const render = objectValue(existing.content.render);
  const source = objectValue(existing.content.source);
  const spec = objectValue(existing.content.spec);
  const templatePair = storedNewsTemplatePair(clientId, render);
  if (!templatePair) {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  const { actualTemplateStyle, requestedTemplateStyle } = templatePair;
  const storedSourceImageUrl = typeof source.image_url === "string" ? source.image_url : "";
  const storedSourceImageSha256 = typeof source.prepared_sha256 === "string"
    && SHA256_PATTERN.test(source.prepared_sha256)
    ? source.prepared_sha256
    : "";
  const isSquidRemix = clientId === "squid" && actualTemplateStyle === "remix";
  const isLegacySquidSquare = isSquidRemix && !Object.hasOwn(spec, "output_policy");
  if (
    isSquidRemix
    && (
      !expectedVerifiedSourceImageUrl
      || render.source_image_used !== true
      || storedSourceImageUrl !== expectedVerifiedSourceImageUrl
      || (!isLegacySquidSquare && source.media_status !== "present")
      || (!isLegacySquidSquare && !storedSourceImageSha256)
      || (isLegacySquidSquare && source.media_status != null && source.media_status !== "present")
      || (isLegacySquidSquare && source.prepared_sha256 != null && !storedSourceImageSha256)
    )
  ) {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  const figmaTemplate = normalizedFigmaTemplate(
    render.figma_template,
    clientId,
    actualTemplateStyle,
  );
  if (render.figma_template != null && !figmaTemplate) {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  const imageBytes = await downloadCatalogPng(
    storageConfig,
    clientId,
    replayAsset,
    fetch,
    deadlineSignal(deadline, 8_000),
  );
  if (imageBytes.byteLength > MAX_NEWS_CARD_BYTES) {
    throw new ContentCatalogError("generated_image_too_large");
  }
  const replayDimensions = pngDimensions(imageBytes);
  if (!replayDimensions) {
    throw new ContentCatalogError("durable_asset_invalid");
  }
  if (
    isSquidRemix
    && !isLegacySquidSquare
    && !validSquidNativeOutputSpec(spec, replayDimensions)
  ) {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  if (
    isLegacySquidSquare
    && replayDimensions.width !== replayDimensions.height
  ) {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  const duration = existing.generationMeta.duration_ms;
  return {
    client_id: clientId,
    content_type: "news_card",
    spec,
    source_mode: source.mode === "x_import" ? "x_import" : "provided",
    source_image_url: storedSourceImageUrl,
    source_image_sha256: storedSourceImageSha256,
    source_media_status: ["not_requested", "present", "absent", "unavailable"].includes(String(source.media_status))
      ? source.media_status
      : "not_requested",
    source_visual_file: typeof render.source_visual_file === "string" ? render.source_visual_file : null,
    source_image_detected: Boolean(source.image_url),
    source_image_used: render.source_image_used === true,
    figma_template: figmaTemplate,
    requested_template_style: requestedTemplateStyle,
    template_style: actualTemplateStyle,
    duration_ms: typeof duration === "number" && Number.isFinite(duration) ? duration : 0,
    mock_mode: existing.generationMeta.mock_mode === true,
    channel_copy: existing.channelCopy,
    brand_qa: existing.generationMeta.brand_qa || null,
    fact_check: factCheck,
    output_width: replayDimensions.width,
    output_height: replayDimensions.height,
    image_data_url: `data:image/png;base64,${Buffer.from(imageBytes).toString("base64")}`,
    filename: `${clientId}-${actualTemplateStyle}-news-card.png`,
    storage_backend: "supabase",
    content_item_id: existing.contentItemId,
    content_version_id: existing.contentVersionId,
    asset_ids: existing.assets.map((asset) => asset.assetId),
    reused: true,
  };
}

function generatedFilePath(pngPath: string, clientId: string): string | null {
  const marker = `/${clientId}/`;
  const markerIndex = pngPath.lastIndexOf(marker);
  if (markerIndex < 0) return null;
  return pngPath.slice(markerIndex + 1);
}

export function normalizedSourceVisualFile(value: unknown, clientId: string): string | null {
  if (typeof value !== "string" || value.length > 1_024) return null;
  const marker = `/${clientId}/`;
  const markerIndex = value.lastIndexOf(marker);
  const candidate = value.startsWith(`${clientId}/`)
    ? value
    : markerIndex >= 0
      ? value.slice(markerIndex + 1)
      : "";
  const escapedClientId = clientId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(
    `^${escapedClientId}/news_[0-9]+/source_visual_cleaned\\.jpg$`,
  ).test(candidate)
    ? candidate
    : null;
}

function needsCleanedSquidVisual(
  clientId: string,
  templateStyle: string,
  spec: Record<string, unknown>,
): boolean {
  return clientId === "squid"
    && templateStyle === "remix"
    && spec.source_text_visible === true
    && Array.isArray(spec.translation_regions)
    && spec.translation_regions.length > 0;
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "POST") {
    return json({ error: "method_not_allowed" }, 405);
  }
  const requestDeadline = Date.now() + NETLIFY_REQUEST_BUDGET_MS;

  const studioAccessError = requireStudioGenerationAccess(req);
  if (studioAccessError) return studioAccessError;

  const clientParam = context.params.clientId;
  if (!clientParam || !ALLOWED_CLIENTS.has(clientParam as ContentCatalogClient)) {
    return json({ error: "unknown_client" }, 404);
  }
  const clientId = clientParam as ContentCatalogClient;
  const requestId = (req.headers.get("idempotency-key") || "").trim().toLowerCase();
  if (!UUID_PATTERN.test(requestId)) {
    return json({ error: "invalid_news_card_idempotency_key" }, 400);
  }

  const apiSecret = Netlify.env.get("API_SECRET");
  if (!apiSecret) {
    return json({ error: "server_not_configured" }, 503);
  }
  const storageConfig = contentCatalogConfig((name) => Netlify.env.get(name));
  if (!storageConfig) {
    return json({ error: "durable_storage_not_configured" }, 503);
  }

  let body: NewsCardRequest;
  try {
    body = (await req.json()) as NewsCardRequest;
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  const sourceContent = typeof body.source_content === "string" ? body.source_content.trim() : "";
  const sourceType = typeof body.source_type === "string" ? body.source_type : "tweet";
  const sourceUrl = typeof body.source_url === "string" ? body.source_url.trim() : "";
  const templateStyle = typeof body.template_style === "string" ? body.template_style : "classic";
  const allowedSourceTypes = new Set(["tweet", "blog", "article"]);
  const allowedTemplateStyles = new Set(["remix", "classic", "editorial", "signal"]);

  if (!allowedSourceTypes.has(sourceType)) {
    return json({ error: "invalid_source_type" }, 400);
  }
  if (!allowedTemplateStyles.has(templateStyle)) {
    return json({ error: "invalid_template_style" }, 400);
  }
  if (body.source_image_url !== undefined && typeof body.source_image_url !== "string") {
    return json({ error: "invalid_source_image_url" }, 422);
  }
  const sourceImageCandidate = typeof body.source_image_url === "string"
    ? body.source_image_url.trim()
    : "";
  let pinnedSourceImageUrl = "";
  if (sourceImageCandidate) {
    if (!hasValidStudioAutomationAccess(req)) {
      return json({ error: "source_image_url_automation_only" }, 403);
    }
    pinnedSourceImageUrl = normalizeXImageUrl(sourceImageCandidate);
    if (
      !pinnedSourceImageUrl
      || clientId !== "squid"
      || templateStyle !== "remix"
      || !isOfficialSquidXStatusUrl(sourceUrl)
    ) {
      return json({ error: "invalid_source_image_url" }, 422);
    }
  }
  const submittedStyleReferences = body.style_references !== undefined
    || body.style_reference_pack_hash !== undefined;
  if (submittedStyleReferences && !hasValidStudioAutomationAccess(req)) {
    return json({ error: "style_references_automation_only" }, 403);
  }
  let styleReferencePack: StyleReferencePack;
  try {
    styleReferencePack = parseStyleReferencePack(
      body.style_references,
      body.style_reference_pack_hash,
    );
  } catch (error) {
    const code = error instanceof StyleReferenceInputError
      ? error.code
      : "invalid_style_references";
    return json({ error: code }, 422);
  }
  const mockMode = body.mock_mode === true;
  const requiresVerifiedSquidSource = clientId === "squid" && templateStyle === "remix";
  const requestHashInput = {
    clientId,
    sourceContent,
    sourceType,
    sourceUrl,
    mockMode,
    templateStyle,
    sourceImageUrl: pinnedSourceImageUrl,
    styleReferences: styleReferencePack.references,
    styleReferencePackHash: styleReferencePack.packHash,
  };
  const requestHash = newsCardRequestHash(requestHashInput);

  let existingGeneration: ContentCatalogLookup | null;
  try {
    await Promise.all([
      verifyPrivateContentBucket(
        storageConfig,
        fetch,
        deadlineSignal(requestDeadline, 5_000),
      ),
      verifyContentStorageScope(
        storageConfig,
        clientId,
        fetch,
        deadlineSignal(requestDeadline, 5_000),
      ),
    ]);
    existingGeneration = await findGeneratedContent(
      storageConfig,
      requestId,
      clientId,
      "daily_news",
      fetch,
      deadlineSignal(requestDeadline, 4_000),
    );
  } catch (error) {
    const code = error instanceof ContentCatalogError
      ? error.code
      : "durable_storage_unavailable";
    const deadlineExceeded = code === "news_card_deadline_exceeded"
      || Date.now() >= requestDeadline - 100;
    return json({ error: deadlineExceeded ? "news_card_deadline_exceeded" : code }, deadlineExceeded ? 504 : 503);
  }
  if (existingGeneration) {
    const storedRequestHash = catalogRequestHash(existingGeneration);
    const isCompatibleLegacyBrandRetry = (
      clientId === "squid"
        ? isLegacySquidCreativeRecord(existingGeneration)
        : isLegacyStandardBrandRecord(existingGeneration)
    ) && storedRequestHash === legacyNewsCardRequestHash(requestHashInput);
    if (storedRequestHash !== requestHash && !isCompatibleLegacyBrandRetry) {
      return json({ error: "news_card_idempotency_conflict" }, 409);
    }
    if (!requiresVerifiedSquidSource || pinnedSourceImageUrl) {
      try {
        return json(await catalogRetryResponse(
          existingGeneration,
          storageConfig,
          clientId,
          requestDeadline,
          pinnedSourceImageUrl,
        ));
      } catch (error) {
        const code = error instanceof ContentCatalogError
          ? error.code
          : "durable_asset_unavailable";
        const deadlineExceeded = code === "news_card_deadline_exceeded"
          || Date.now() >= requestDeadline - 100;
        const status = deadlineExceeded
          ? 504
          : code === "fact_check_regeneration_required"
            ? 409
          : code === "generated_image_too_large"
            ? 502
            : 503;
        return json({ error: deadlineExceeded ? "news_card_deadline_exceeded" : code }, status);
      }
    }
  }

  let brandReviewGuidance = emptyBrandReviewGuidance();
  let brandReviewGuidanceAvailable = false;
  try {
    brandReviewGuidance = await getBrandReviewGuidance(
      storageConfig,
      clientId,
      "daily_news",
      fetch,
      deadlineSignal(requestDeadline, 4_000),
    );
    brandReviewGuidanceAvailable = true;
  } catch {
    // Human-review guidance improves brand fit but is not factual evidence.
    // A missing projection must not block source-locked generation.
  }

  let resolvedSource: ResolvedSource;
  try {
    resolvedSource = await resolveSourceInput(
      sourceContent,
      sourceUrl,
      fetch,
      templateStyle === "remix",
    );
  } catch (error) {
    if (error instanceof SourceInputError) {
      return json({ error: error.code, detail: error.message }, error.status);
    }
    return json({ error: "source_fetch_failed" }, 422);
  }
  if (requiresVerifiedSquidSource && !hasVerifiedOfficialSquidXProvenance(resolvedSource)) {
    return resolvedSource.mediaStatus === "unavailable"
      ? json({ error: "source_media_unavailable" }, 422)
      : json({ error: "source_not_official_squid" }, 422);
  }
  if (
    pinnedSourceImageUrl
    && !resolvedSource.xProvenance?.mediaUrls.includes(pinnedSourceImageUrl)
  ) {
    return json({ error: "invalid_source_image_url" }, 422);
  }
  if (pinnedSourceImageUrl) {
    resolvedSource = {
      ...resolvedSource,
      imageUrl: pinnedSourceImageUrl,
      mediaStatus: "present",
    };
  }
  if (requiresVerifiedSquidSource && !resolvedSource.imageUrl) {
    return resolvedSource.mediaStatus === "unavailable"
      ? json({ error: "source_media_unavailable" }, 422)
      : json({ error: "source_image_required" }, 422);
  }

  if (existingGeneration) {
    try {
      return json(await catalogRetryResponse(
        existingGeneration,
        storageConfig,
        clientId,
        requestDeadline,
        requiresVerifiedSquidSource ? resolvedSource.imageUrl : "",
      ));
    } catch (error) {
      const code = error instanceof ContentCatalogError
        ? error.code
        : "durable_asset_unavailable";
      const deadlineExceeded = code === "news_card_deadline_exceeded"
        || Date.now() >= requestDeadline - 100;
      const status = deadlineExceeded
        ? 504
        : code === "fact_check_regeneration_required"
          ? 409
        : code === "generated_image_too_large"
          ? 502
          : 503;
      return json({ error: deadlineExceeded ? "news_card_deadline_exceeded" : code }, status);
    }
  }

  const railwayUrl = cleanBaseUrl(
    Netlify.env.get("RAILWAY_API_URL") ||
      "https://coineasy-content-engine-production.up.railway.app",
  );
  const upstreamHeaders = {
    "Content-Type": "application/json",
    "X-API-Key": apiSecret,
  };

  try {
    const generationResponse = await fetch(
      `${railwayUrl}/clients/${encodeURIComponent(clientId)}/generate/news-card`,
      {
        method: "POST",
        headers: upstreamHeaders,
        body: JSON.stringify({
          source_content: resolvedSource.content,
          source_type: sourceType,
          source_url: resolvedSource.url,
          source_image_url: resolvedSource.imageUrl,
          mock_mode: mockMode,
          template_style: templateStyle,
          ...(styleReferencePack.packHash
            ? {
              style_references: styleReferencePack.references,
              style_reference_pack_hash: styleReferencePack.packHash,
            }
            : {}),
          ...(brandReviewGuidanceAvailable
            ? { brand_review_guidance: brandReviewGuidance }
            : {}),
        }),
        signal: deadlineSignal(
          requestDeadline,
          RAILWAY_GENERATION_BUDGET_MS,
          NEWS_CARD_PERSISTENCE_RESERVE_MS,
        ),
      },
    );

    if (!generationResponse.ok) {
      const detail = await generationResponse.text();
      return json(
        {
          error: "generation_failed",
          upstream_status: generationResponse.status,
          detail: detail.slice(0, 500),
        },
        generationResponse.status >= 500 ? 502 : generationResponse.status,
      );
    }

    const rawResult = await generationResponse.json();
    if (!isRailwayNewsCardResponse(rawResult, clientId)) {
      return json({ error: "invalid_generation_response" }, 502);
    }
    const result = rawResult;
    const filePath = generatedFilePath(result.png_path, clientId);
    if (!filePath) {
      return json({ error: "invalid_generated_file_path" }, 502);
    }
    const actualTemplateStyle = result.template_style || templateStyle;
    const reportedRequestedTemplateStyle = result.requested_template_style ?? templateStyle;
    if (
      !allowedTemplateStyles.has(actualTemplateStyle)
      || !validNewsTemplatePair(
        clientId,
        templateStyle,
        reportedRequestedTemplateStyle,
        actualTemplateStyle,
      )
    ) {
      return json({ error: "invalid_generation_response" }, 502);
    }
    const preparedSourceImageSha256 = typeof result.source_image_sha256 === "string"
      && SHA256_PATTERN.test(result.source_image_sha256)
      ? result.source_image_sha256
      : "";
    if (
      clientId === "squid"
      && actualTemplateStyle === "remix"
      && (
        result.source_image_used !== true
        || result.source_image_url !== resolvedSource.imageUrl
        || !preparedSourceImageSha256
      )
    ) {
      return json({ error: "invalid_generation_response" }, 502);
    }
    const sourceVisualFile = normalizedSourceVisualFile(result.source_visual_path, clientId);
    if (needsCleanedSquidVisual(clientId, actualTemplateStyle, result.spec) && !sourceVisualFile) {
      return json({ error: "cleaned_source_unavailable" }, 502);
    }
    if (
      clientId === "squid"
      && !validSquidCreativeMetadata(result.spec, actualTemplateStyle)
    ) {
      return json({ error: "invalid_generation_response" }, 502);
    }
    if (
      clientId !== "squid"
      && !validStandardNewsBrandMetadata(
        result.spec,
        clientId,
        actualTemplateStyle,
      )
    ) {
      return json({ error: "invalid_generation_response" }, 502);
    }
    if (
      clientId === "squid"
      && actualTemplateStyle === "classic"
      && result.figma_template != null
    ) {
      // Family variants are not represented by the one legacy approved
      // Squid frame. Do not label a new layout as Figma-approved by proxy.
      return json({ error: "invalid_generation_response" }, 502);
    }
    const figmaTemplate = normalizedFigmaTemplate(
      result.figma_template,
      clientId,
      actualTemplateStyle,
    );
    if (result.figma_template != null && !figmaTemplate) {
      return json({ error: "invalid_generation_response" }, 502);
    }

    const imageResponse = await fetch(
      `${railwayUrl}/files/${filePath.split("/").map(encodeURIComponent).join("/")}`,
      {
        headers: { "X-API-Key": apiSecret },
        signal: deadlineSignal(requestDeadline, 8_000),
      },
    );

    if (!imageResponse.ok) {
      return json(
        { error: "generated_image_unavailable", upstream_status: imageResponse.status },
        502,
      );
    }

    const declaredBytes = Number(imageResponse.headers.get("content-length"));
    if (Number.isFinite(declaredBytes) && declaredBytes > MAX_NEWS_CARD_BYTES) {
      return json({ error: "generated_image_too_large" }, 502);
    }
    const imageBytes = await imageResponse.arrayBuffer();
    if (imageBytes.byteLength > MAX_NEWS_CARD_BYTES) {
      return json({ error: "generated_image_too_large" }, 502);
    }
    const imageDimensions = pngDimensions(imageBytes);
    if (!imageDimensions) {
      return json({ error: "invalid_generated_png" }, 502);
    }
    if (
      clientId === "squid"
      && actualTemplateStyle === "remix"
      && !validSquidNativeOutputSpec(result.spec, imageDimensions)
    ) {
      return json({ error: "invalid_generation_response" }, 502);
    }
    const resultSpec = result.spec;
    const brandProfileAudit = clientId === "squid"
      ? {
        creative_family: resultSpec.creative_family,
        render_strategy: resultSpec.render_strategy,
        creative_family_policy_version: resultSpec.creative_family_policy_version,
        visual_reference_pack_id: resultSpec.visual_reference_pack_id,
        visual_reference_pack_version: resultSpec.visual_reference_pack_version,
        channel_profile: resultSpec.channel_profile,
        brand_tokens_version: resultSpec.brand_tokens_version,
        template_version: resultSpec.template_version,
        asset_pack_version: resultSpec.asset_pack_version,
        font_status: resultSpec.font_status,
        visual_design_profile_id: resultSpec.visual_design_profile_id,
        visual_design_profile_version: resultSpec.visual_design_profile_version,
      }
      : {
        brand_profile_policy_version: resultSpec.brand_profile_policy_version,
        render_strategy: resultSpec.render_strategy,
        channel_profile: resultSpec.channel_profile,
        brand_tokens_version: resultSpec.brand_tokens_version,
        template_version: resultSpec.template_version,
        asset_pack_version: resultSpec.asset_pack_version,
        visual_design_profile_id: resultSpec.visual_design_profile_id,
        visual_design_profile_version: resultSpec.visual_design_profile_version,
      };
    const channelCopy = buildChannelCopy(
      clientId,
      resultSpec,
      resolvedSource.content,
      resolvedSource.url,
    );
    const brandQa = evaluateBrandQuality({
      clientId,
      contentKind: "daily_news",
      sourceText: resolvedSource.content,
      headline: resultSpec.headline,
      bodyLines: resultSpec.body_lines,
      channelCopy,
      templateStyle: actualTemplateStyle,
      sourceImageUsed: result.source_image_used,
      sourceLogoVisible: resultSpec.source_logo_visible,
      visualLocalizationStatus: resultSpec.visual_localization_status,
    });
    const factCheck = evaluateFactCheck({
      contentKind: "daily_news",
      source: resolvedSource,
      publicText: {
        label: resultSpec.label,
        headline: resultSpec.headline,
        body_lines: resultSpec.body_lines,
        visual_metric: resultSpec.visual_metric,
        date: resultSpec.date,
        source_url: resultSpec.source_url,
        translation_regions: Array.isArray(resultSpec.translation_regions)
          ? resultSpec.translation_regions.map((region) => {
            const value = objectValue(region);
            return { source_text: value.source_text, text: value.text };
          })
          : [],
      },
      channelCopy,
      artifactSha256: [
        createHash("sha256").update(Buffer.from(imageBytes)).digest("hex"),
      ],
      brandQa,
    });
    const referenceAudit = styleReferenceAudit(styleReferencePack);
    const reviewGuidanceAudit = {
      ...brandReviewGuidanceAudit(brandReviewGuidance),
      brand_review_guidance_available: brandReviewGuidanceAvailable,
    };
    const assetId = randomUUID();
    const attemptedStoragePath = contentStoragePath(
      storageConfig.workspaceId,
      clientId,
      assetId,
      "news-card.png",
    );
    if (!attemptedStoragePath) {
      return json({ error: "invalid_durable_asset_path" }, 502);
    }
    let catalogCommitted = false;
    try {
      const storedAsset = await uploadNewsCard(
        storageConfig,
        clientId,
        assetId,
        imageBytes,
        fetch,
        deadlineSignal(requestDeadline, 7_000),
      );
      const catalog = await recordGeneratedContent(storageConfig, {
        requestId,
        clientId,
        contentKind: "daily_news",
        title: typeof resultSpec.headline === "string" && resultSpec.headline.trim()
          ? resultSpec.headline.trim().slice(0, 200)
          : `${clientId} 데일리 뉴스`,
        content: {
          request_hash: requestHash,
          spec: resultSpec,
          source: {
            submitted_content: sourceContent,
            resolved_content: resolvedSource.content,
            type: sourceType,
            url: resolvedSource.url,
            mode: resolvedSource.mode,
            image_url: resolvedSource.imageUrl,
            media_status: resolvedSource.mediaStatus,
            ...(preparedSourceImageSha256
              ? { prepared_sha256: preparedSourceImageSha256 }
              : {}),
          },
          render: {
            requested_template_style: reportedRequestedTemplateStyle,
            template_style: actualTemplateStyle,
            source_image_used: result.source_image_used === true,
            source_visual_file: sourceVisualFile,
            figma_template: figmaTemplate,
            ...brandProfileAudit,
          },
        },
        channelCopy,
        generationMeta: {
          request_hash: requestHash,
          duration_ms: result.duration_ms,
          renderer: "railway",
          storage_backend: "supabase",
          mock_mode: mockMode,
          ...referenceAudit,
          ...reviewGuidanceAudit,
          figma_template_version: figmaTemplate?.version || null,
          ...brandProfileAudit,
          brand_qa: brandQa,
          fact_check: factCheck,
        },
        asset: storedAsset,
        promptVersion: clientId === "squid" ? "news-card@3" : "news-card@2",
      }, fetch, deadlineSignal(requestDeadline, 6_000));
      catalogCommitted = true;
      return json({
        client_id: result.client_id,
        content_type: result.content_type,
        spec: resultSpec,
        source_mode: resolvedSource.mode,
        source_image_url: resolvedSource.imageUrl,
        source_image_sha256: preparedSourceImageSha256,
        source_media_status: resolvedSource.mediaStatus,
        source_visual_file: sourceVisualFile,
        source_image_detected: Boolean(resolvedSource.imageUrl),
        source_image_used: result.source_image_used === true,
        requested_template_style: reportedRequestedTemplateStyle,
        template_style: actualTemplateStyle,
        figma_template: figmaTemplate,
        duration_ms: result.duration_ms,
        mock_mode: mockMode,
        channel_copy: channelCopy,
        brand_qa: brandQa,
        fact_check: factCheck,
        output_width: imageDimensions.width,
        output_height: imageDimensions.height,
        image_data_url: `data:image/png;base64,${Buffer.from(imageBytes).toString("base64")}`,
        filename: `${clientId}-${actualTemplateStyle}-news-card.png`,
        storage_backend: "supabase",
        content_item_id: catalog.contentItemId,
        content_version_id: catalog.contentVersionId,
        asset_ids: catalog.assetIds,
        reused: false,
      });
    } catch (error) {
      if (
        !catalogCommitted
        && (!(error instanceof ContentCatalogError) || error.cleanupSafe)
      ) {
        await removeContentAssets(
          storageConfig,
          [attemptedStoragePath],
          fetch,
          AbortSignal.timeout(2_000),
        );
      }
      throw error;
    }
  } catch (error) {
    if (error instanceof ContentCatalogError) {
      const deadlineExceeded = error.code === "news_card_deadline_exceeded"
        || Date.now() >= requestDeadline - 100;
      if (deadlineExceeded) {
        return json({ error: "news_card_deadline_exceeded" }, 504);
      }
      if (error.code === "content_idempotency_conflict") {
        try {
          const concurrentGeneration = await findGeneratedContent(
            storageConfig,
            requestId,
            clientId,
            "daily_news",
            fetch,
            deadlineSignal(requestDeadline, 4_000),
          );
          if (
            concurrentGeneration
            && catalogRequestHash(concurrentGeneration) === requestHash
          ) {
            return json(await catalogRetryResponse(
              concurrentGeneration,
              storageConfig,
              clientId,
              requestDeadline,
              requiresVerifiedSquidSource ? resolvedSource.imageUrl : "",
            ));
          }
          return json({ error: "news_card_idempotency_conflict" }, 409);
        } catch (lookupError) {
          const code = lookupError instanceof ContentCatalogError
            ? lookupError.code
            : "durable_catalog_lookup_failed";
          const deadlineExceeded = code === "news_card_deadline_exceeded"
            || Date.now() >= requestDeadline - 100;
          const status = deadlineExceeded
            ? 504
            : code === "fact_check_regeneration_required"
              ? 409
            : code === "generated_image_too_large"
              ? 502
              : 503;
          return json({ error: deadlineExceeded ? "news_card_deadline_exceeded" : code }, status);
        }
      }
      const providerResultError = new Set([
        "invalid_generated_png",
        "generated_image_too_large",
      ]).has(error.code);
      return json({ error: error.code }, providerResultError ? 502 : 503);
    }
    const message = error instanceof Error ? error.message : "unknown_error";
    const timedOut = error instanceof Error && error.name === "TimeoutError";
    return json(
      { error: timedOut ? "generation_timeout" : "upstream_unavailable", detail: message },
      timedOut ? 504 : 502,
    );
  }
};

export const config: Config = {
  path: "/api/news-card/:clientId",
};
