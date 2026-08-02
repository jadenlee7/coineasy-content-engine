import type { Config, Context } from "@netlify/functions";
import { createHash } from "node:crypto";
import {
  hasValidStudioAutomationAccess,
  requireStudioGenerationAccess,
} from "./_shared/studio-session.mts";
import { evaluateBrandQuality } from "./_shared/brand-quality.mts";
import {
  evaluateFactCheck,
  validatedFactCheckReport,
} from "./_shared/fact-check.mts";
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
  ContentCatalogError,
  findGeneratedContent,
  recordGeneratedContent,
  type ContentCatalogClient,
  type ContentCatalogLookup,
  verifyContentStorageScope,
} from "./_shared/content-catalog.mts";

type ArticleRequest = {
  source_content?: unknown;
  source_type?: unknown;
  source_url?: unknown;
  style_references?: unknown;
  style_reference_pack_hash?: unknown;
};

type RailwayArticleResponse = {
  client_id: string;
  content_type: "article";
  title: string;
  lead: string;
  sections: Array<{ id: string; heading: string; body: string }>;
  key_takeaways: string[];
  visuals?: Array<{
    id: string;
    after_section_id: string;
    role: "overview" | "explainer";
    motif: "network" | "layers" | "flow" | "signal" | "event" | "asset";
    eyebrow: string;
    headline: string;
    caption: string;
    points: string[];
  }>;
  source_map: Array<{ source_url: string; applies_to: string[] }>;
  channel_copy: { telegram: string; x: string };
  markdown: string;
  duration_ms: number;
};

const ALLOWED_CLIENTS = new Set<ContentCatalogClient>(["yellow", "origintrail", "squid", "babylon"]);
const ALLOWED_SOURCE_TYPES = new Set(["tweet", "blog", "article"]);
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const NETLIFY_REQUEST_BUDGET_MS = 58_000;
// Leave enough of Netlify's synchronous request window for response parsing and
// error handling instead of spending the full platform budget upstream.
const RAILWAY_ARTICLE_BUDGET_MS = 44_000;
const ARTICLE_PERSISTENCE_RESERVE_MS = 8_000;

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
  if (remaining <= 250) throw new ContentCatalogError("article_deadline_exceeded");
  return AbortSignal.timeout(Math.min(maximumMs, remaining));
}

export function articleRequestHash(input: {
  clientId: string;
  sourceContent: string;
  sourceType: string;
  sourceUrl: string;
  styleReferences?: StyleReference[];
  styleReferencePackHash?: string;
}): string {
  return createHash("sha256").update(JSON.stringify({
    client_id: input.clientId,
    source_content: input.sourceContent,
    source_type: input.sourceType,
    source_url: input.sourceUrl,
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

export function articleRetryResponse(
  existing: ContentCatalogLookup,
  clientId: ContentCatalogClient,
): RailwayArticleResponse & Record<string, unknown> {
  const content = existing.content;
  const factCheck = validatedFactCheckReport(existing.generationMeta.fact_check, "article");
  if (!factCheck) {
    throw new ContentCatalogError("fact_check_regeneration_required");
  }
  const candidate = {
    client_id: clientId,
    content_type: "article" as const,
    title: existing.title,
    lead: content.lead,
    sections: content.sections,
    key_takeaways: content.key_takeaways,
    visuals: Array.isArray(content.visuals) ? content.visuals : [],
    source_map: content.source_map,
    channel_copy: existing.channelCopy,
    markdown: content.markdown,
    duration_ms: existing.generationMeta.duration_ms,
  };
  if (!isRailwayArticleResponse(candidate)) {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  return {
    ...candidate,
    storage_backend: "supabase",
    content_item_id: existing.contentItemId,
    content_version_id: existing.contentVersionId,
    asset_ids: [],
    brand_qa: existing.generationMeta.brand_qa || null,
    fact_check: factCheck,
    reused: true,
  };
}

function validSourceUrl(value: string): boolean {
  if (!value) return true;
  try {
    const url = new URL(value);
    return (url.protocol === "https:" || url.protocol === "http:") && Boolean(url.hostname);
  } catch {
    return false;
  }
}

export function isRailwayArticleResponse(value: unknown): value is RailwayArticleResponse {
  if (!value || typeof value !== "object") return false;
  const article = value as Partial<RailwayArticleResponse>;
  return typeof article.client_id === "string"
    && article.content_type === "article"
    && typeof article.title === "string"
    && Boolean(article.title.trim())
    && typeof article.lead === "string"
    && Boolean(article.lead.trim())
    && Array.isArray(article.sections)
    && article.sections.length >= 3
    && article.sections.length <= 5
    && article.sections.every((section) => section
      && typeof section.id === "string"
      && Boolean(section.id.trim())
      && typeof section.heading === "string"
      && Boolean(section.heading.trim())
      && typeof section.body === "string"
      && Boolean(section.body.trim()))
    && Array.isArray(article.key_takeaways)
    && article.key_takeaways.length >= 3
    && article.key_takeaways.length <= 5
    && article.key_takeaways.every((item) => typeof item === "string" && Boolean(item.trim()))
    && (
      article.visuals === undefined
      || (
        Array.isArray(article.visuals)
        && (article.visuals.length === 0 || article.visuals.length === 2)
        && article.visuals.every((visual) => visual
          && typeof visual.id === "string"
          && typeof visual.after_section_id === "string"
          && ["overview", "explainer"].includes(visual.role)
          && ["network", "layers", "flow", "signal", "event", "asset"].includes(visual.motif)
          && typeof visual.eyebrow === "string"
          && Boolean(visual.eyebrow.trim())
          && typeof visual.headline === "string"
          && Boolean(visual.headline.trim())
          && typeof visual.caption === "string"
          && Boolean(visual.caption.trim())
          && Array.isArray(visual.points)
          && visual.points.length >= 2
          && visual.points.length <= 4
          && visual.points.every(point => typeof point === "string" && Boolean(point.trim())))
      )
    )
    && Array.isArray(article.source_map)
    && article.source_map.every((source) => source
      && typeof source.source_url === "string"
      && Boolean(source.source_url.trim())
      && validSourceUrl(source.source_url)
      && Array.isArray(source.applies_to)
      && source.applies_to.every((item) => typeof item === "string"))
    && article.channel_copy !== null
    && typeof article.channel_copy === "object"
    && typeof article.channel_copy.telegram === "string"
    && typeof article.channel_copy.x === "string"
    && typeof article.markdown === "string"
    && Boolean(article.markdown.trim())
    && typeof article.duration_ms === "number"
    && Number.isFinite(article.duration_ms)
    && article.duration_ms >= 0;
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);
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
    return json({ error: "invalid_article_idempotency_key" }, 400);
  }

  const apiSecret = Netlify.env.get("API_SECRET");
  if (!apiSecret) return json({ error: "server_not_configured" }, 503);
  const storageConfig = contentCatalogConfig((name) => Netlify.env.get(name));
  if (!storageConfig) return json({ error: "durable_storage_not_configured" }, 503);

  let body: ArticleRequest;
  try {
    body = (await req.json()) as ArticleRequest;
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  const sourceContent = typeof body.source_content === "string" ? body.source_content.trim() : "";
  const sourceType = typeof body.source_type === "string" ? body.source_type : "article";
  const sourceUrl = typeof body.source_url === "string" ? body.source_url.trim() : "";
  if (sourceContent.length < 300 || sourceContent.length > 60_000) {
    return json({ error: "source_content_must_be_300_to_60000_chars" }, 422);
  }
  if (!ALLOWED_SOURCE_TYPES.has(sourceType)) {
    return json({ error: "invalid_source_type" }, 400);
  }
  if (!validSourceUrl(sourceUrl)) {
    return json({ error: "invalid_source_url" }, 400);
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
  const requestHash = articleRequestHash({
    clientId,
    sourceContent,
    sourceType,
    sourceUrl,
    styleReferences: styleReferencePack.references,
    styleReferencePackHash: styleReferencePack.packHash,
  });

  let existingGeneration: ContentCatalogLookup | null;
  try {
    await verifyContentStorageScope(
      storageConfig,
      clientId,
      fetch,
      deadlineSignal(requestDeadline, 5_000),
    );
    existingGeneration = await findGeneratedContent(
      storageConfig,
      requestId,
      clientId,
      "article",
      fetch,
      deadlineSignal(requestDeadline, 4_000),
    );
  } catch (error) {
    const code = error instanceof ContentCatalogError
      ? error.code
      : "durable_catalog_lookup_failed";
    const deadlineExceeded = code === "article_deadline_exceeded"
      || Date.now() >= requestDeadline - 100;
    return json({ error: deadlineExceeded ? "article_deadline_exceeded" : code }, deadlineExceeded ? 504 : 503);
  }
  if (existingGeneration) {
    if (catalogRequestHash(existingGeneration) !== requestHash) {
      return json({ error: "article_idempotency_conflict" }, 409);
    }
    try {
      return json(articleRetryResponse(existingGeneration, clientId));
    } catch (error) {
      const code = error instanceof ContentCatalogError
        ? error.code
        : "durable_storage_invalid_response";
      return json({ error: code }, code === "fact_check_regeneration_required" ? 409 : 503);
    }
  }

  let brandReviewGuidance = emptyBrandReviewGuidance();
  let brandReviewGuidanceAvailable = false;
  try {
    brandReviewGuidance = await getBrandReviewGuidance(
      storageConfig,
      clientId,
      "article",
      fetch,
      deadlineSignal(requestDeadline, 4_000),
    );
    brandReviewGuidanceAvailable = true;
  } catch {
    // Review guidance is optional style context, never source evidence.
  }

  const railwayUrl = cleanBaseUrl(
    Netlify.env.get("RAILWAY_API_URL")
      || "https://coineasy-content-engine-production.up.railway.app",
  );

  try {
    const generationResponse = await fetch(
      `${railwayUrl}/clients/${encodeURIComponent(clientId)}/generate/article`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiSecret,
        },
        body: JSON.stringify({
          source_content: sourceContent,
          source_type: sourceType,
          source_url: sourceUrl,
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
          RAILWAY_ARTICLE_BUDGET_MS,
          ARTICLE_PERSISTENCE_RESERVE_MS,
        ),
      },
    );

    if (!generationResponse.ok) {
      const detail = await generationResponse.text();
      return json({
        error: "generation_failed",
        upstream_status: generationResponse.status,
        detail: detail.slice(0, 500),
      }, generationResponse.status >= 500 ? 502 : generationResponse.status);
    }

    const result = await generationResponse.json();
    if (!isRailwayArticleResponse(result) || result.client_id !== clientId) {
      return json({ error: "invalid_article_response" }, 502);
    }
    const brandQa = evaluateBrandQuality({
      clientId,
      contentKind: "article",
      sourceText: sourceContent,
      title: result.title,
      lead: result.lead,
      sections: result.sections,
      keyTakeaways: result.key_takeaways,
      visuals: result.visuals,
      channelCopy: result.channel_copy,
    });
    const factCheck = evaluateFactCheck({
      contentKind: "article",
      // Article generation intentionally accepts pasted source text without a
      // fetch. Preserve that boundary and mark it for human verification.
      source: { content: sourceContent, url: sourceUrl, mode: "provided" },
      publicText: {
        title: result.title,
        lead: result.lead,
        sections: result.sections.map((section) => ({
          heading: section.heading,
          body: section.body,
        })),
        key_takeaways: result.key_takeaways,
        visuals: (result.visuals || []).map((visual) => ({
          eyebrow: visual.eyebrow,
          headline: visual.headline,
          caption: visual.caption,
          points: visual.points,
        })),
        markdown: result.markdown,
      },
      channelCopy: result.channel_copy,
      brandQa,
    });
    const referenceAudit = styleReferenceAudit(styleReferencePack);
    const reviewGuidanceAudit = {
      ...brandReviewGuidanceAudit(brandReviewGuidance),
      brand_review_guidance_available: brandReviewGuidanceAvailable,
    };
    try {
      const catalog = await recordGeneratedContent(storageConfig, {
        requestId,
        clientId,
        contentKind: "article",
        title: result.title.slice(0, 200),
        content: {
          request_hash: requestHash,
          lead: result.lead,
          sections: result.sections,
          key_takeaways: result.key_takeaways,
          visuals: result.visuals || [],
          source_map: result.source_map,
          markdown: result.markdown,
          source: {
            submitted_content: sourceContent,
            type: sourceType,
            url: sourceUrl,
            mode: "provided",
          },
        },
        channelCopy: result.channel_copy,
        generationMeta: {
          request_hash: requestHash,
          duration_ms: result.duration_ms,
          renderer: "railway",
          storage_backend: "supabase",
          mock_mode: false,
          ...referenceAudit,
          ...reviewGuidanceAudit,
          brand_qa: brandQa,
          fact_check: factCheck,
        },
        asset: null,
        promptVersion: "article@4",
      }, fetch, deadlineSignal(requestDeadline, 6_000));
      return json({
        ...result,
        storage_backend: "supabase",
        content_item_id: catalog.contentItemId,
        content_version_id: catalog.contentVersionId,
        asset_ids: catalog.assetIds,
        brand_qa: brandQa,
        fact_check: factCheck,
        reused: false,
      });
    } catch (error) {
      if (error instanceof ContentCatalogError) {
        if (error.code === "content_idempotency_conflict") {
          try {
            const concurrentGeneration = await findGeneratedContent(
              storageConfig,
              requestId,
              clientId,
              "article",
              fetch,
              deadlineSignal(requestDeadline, 4_000),
            );
            if (
              concurrentGeneration
              && catalogRequestHash(concurrentGeneration) === requestHash
            ) {
              return json(articleRetryResponse(concurrentGeneration, clientId));
            }
            return json({ error: "article_idempotency_conflict" }, 409);
          } catch (lookupError) {
            const code = lookupError instanceof ContentCatalogError
              ? lookupError.code
              : "durable_catalog_lookup_failed";
            return json(
              { error: code },
              Date.now() >= requestDeadline - 100
                ? 504
                : code === "fact_check_regeneration_required" ? 409 : 503,
            );
          }
        }
        const deadlineExceeded = error.code === "article_deadline_exceeded"
          || Date.now() >= requestDeadline - 100;
        return json({ error: deadlineExceeded ? "article_deadline_exceeded" : error.code }, deadlineExceeded ? 504 : 503);
      }
      throw error;
    }
  } catch (error) {
    if (error instanceof ContentCatalogError) {
      const deadlineExceeded = error.code === "article_deadline_exceeded"
        || Date.now() >= requestDeadline - 100;
      return json(
        { error: deadlineExceeded ? "article_deadline_exceeded" : error.code },
        deadlineExceeded ? 504 : 503,
      );
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
  path: "/api/article/:clientId",
};
