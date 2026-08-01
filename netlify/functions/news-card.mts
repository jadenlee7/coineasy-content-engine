import type { Config, Context } from "@netlify/functions";
import { createHash, randomUUID } from "node:crypto";
import {
  resolveSourceInput,
  SourceInputError,
  type ResolvedSource,
} from "./_shared/source-content.mts";
import { buildChannelCopy } from "./_shared/channel-copy.mts";
import { evaluateBrandQuality } from "./_shared/brand-quality.mts";
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
const NETLIFY_REQUEST_BUDGET_MS = 58_000;
const RAILWAY_GENERATION_BUDGET_MS = 38_000;
// A 3 MB PNG expands to roughly 4 MB when embedded as base64 in JSON. Keep the
// binary ceiling comfortably below Netlify's 6 MB synchronous response limit.
export const MAX_NEWS_CARD_BYTES = 3_000_000;
const NEWS_CARD_PERSISTENCE_RESERVE_MS = 18_000;
const FIGMA_TEMPLATE_NODES: Partial<Record<ContentCatalogClient, {
  nodeId: string;
  frameName: string;
}>> = {
  squid: {
    nodeId: "1479:1954",
    frameName: "[KEEP] Banner_Squid_Sample",
  },
  yellow: {
    nodeId: "1507:12",
    frameName: "[KEEP] Banner_Yellow_Sample",
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
    || typeof reference.version !== "string"
    || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}\.[1-9][0-9]*$/.test(reference.version)
  ) return null;
  return reference as FigmaTemplateReference;
}

export function newsCardRequestHash(input: {
  clientId: string;
  sourceContent: string;
  sourceType: string;
  sourceUrl: string;
  mockMode: boolean;
  templateStyle: string;
  styleReferences?: StyleReference[];
  styleReferencePackHash?: string;
}): string {
  return createHash("sha256").update(JSON.stringify({
    client_id: input.clientId,
    source_content: input.sourceContent,
    source_type: input.sourceType,
    source_url: input.sourceUrl,
    mock_mode: input.mockMode,
    template_style: input.templateStyle,
    style_references: input.styleReferences || [],
    style_reference_pack_hash: input.styleReferencePackHash || "",
  }), "utf8").digest("hex");
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
): Promise<Record<string, unknown>> {
  if (existing.assets.length !== 1) {
    throw new ContentCatalogError("durable_storage_invalid_response");
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
  const duration = existing.generationMeta.duration_ms;
  return {
    client_id: clientId,
    content_type: "news_card",
    spec,
    source_mode: source.mode === "x_import" ? "x_import" : "provided",
    source_image_url: typeof source.image_url === "string" ? source.image_url : "",
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
  const requestHash = newsCardRequestHash({
    clientId,
    sourceContent,
    sourceType,
    sourceUrl,
    mockMode,
    templateStyle,
    styleReferences: styleReferencePack.references,
    styleReferencePackHash: styleReferencePack.packHash,
  });

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
    if (catalogRequestHash(existingGeneration) !== requestHash) {
      return json({ error: "news_card_idempotency_conflict" }, 409);
    }
    try {
      return json(await catalogRetryResponse(
        existingGeneration,
        storageConfig,
        clientId,
        requestDeadline,
      ));
    } catch (error) {
      const code = error instanceof ContentCatalogError
        ? error.code
        : "durable_asset_unavailable";
      const deadlineExceeded = code === "news_card_deadline_exceeded"
        || Date.now() >= requestDeadline - 100;
      const status = deadlineExceeded
        ? 504
        : code === "generated_image_too_large"
          ? 502
          : 503;
      return json({ error: deadlineExceeded ? "news_card_deadline_exceeded" : code }, status);
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
    const sourceVisualFile = normalizedSourceVisualFile(result.source_visual_path, clientId);
    if (needsCleanedSquidVisual(clientId, actualTemplateStyle, result.spec) && !sourceVisualFile) {
      return json({ error: "cleaned_source_unavailable" }, 502);
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
    const channelCopy = buildChannelCopy(
      clientId,
      result.spec,
      resolvedSource.content,
      resolvedSource.url,
    );
    const brandQa = evaluateBrandQuality({
      clientId,
      contentKind: "daily_news",
      sourceText: resolvedSource.content,
      headline: result.spec.headline,
      bodyLines: result.spec.body_lines,
      channelCopy,
      templateStyle: actualTemplateStyle,
      sourceImageUsed: result.source_image_used,
      sourceLogoVisible: result.spec.source_logo_visible,
      visualLocalizationStatus: result.spec.visual_localization_status,
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
        title: typeof result.spec.headline === "string" && result.spec.headline.trim()
          ? result.spec.headline.trim().slice(0, 200)
          : `${clientId} 데일리 뉴스`,
        content: {
          request_hash: requestHash,
          spec: result.spec,
          source: {
            submitted_content: sourceContent,
            resolved_content: resolvedSource.content,
            type: sourceType,
            url: resolvedSource.url,
            mode: resolvedSource.mode,
            image_url: resolvedSource.imageUrl,
          },
          render: {
            requested_template_style: reportedRequestedTemplateStyle,
            template_style: actualTemplateStyle,
            source_image_used: result.source_image_used === true,
            source_visual_file: sourceVisualFile,
            figma_template: figmaTemplate,
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
          brand_qa: brandQa,
        },
        asset: storedAsset,
        promptVersion: "news-card@2",
      }, fetch, deadlineSignal(requestDeadline, 6_000));
      catalogCommitted = true;
      return json({
        client_id: result.client_id,
        content_type: result.content_type,
        spec: result.spec,
        source_mode: resolvedSource.mode,
        source_image_url: resolvedSource.imageUrl,
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
