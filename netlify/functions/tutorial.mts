import type { Config, Context } from "@netlify/functions";
import { createHash, randomUUID } from "node:crypto";
import {
  resolveSourceInput,
  SourceInputError,
  type ResolvedSource,
} from "./_shared/source-content.mts";
import {
  evaluateBrandQuality,
  type BrandQaClient,
} from "./_shared/brand-quality.mts";
import {
  evaluateFactCheck,
  validatedFactCheckReport,
} from "./_shared/fact-check.mts";
import { signTutorialSlide } from "./_shared/tutorial-slide-token.mts";
import { requireStudioGenerationAccess } from "./_shared/studio-session.mts";
import {
  brandReviewGuidanceAudit,
  emptyBrandReviewGuidance,
  getBrandReviewGuidance,
} from "./_shared/content-reviews.mts";
import {
  findTutorialGeneration,
  pngDimensions,
  recordTutorialGeneration,
  removeTutorialSlides,
  type StoredTutorialSlide,
  type TutorialCatalogSlide,
  type TutorialCatalogLookup,
  tutorialStorageConfig,
  tutorialStoragePath,
  TutorialStorageError,
  uploadTutorialSlide,
  verifyPrivateTutorialBucket,
  verifyTutorialStorageScope,
} from "./_shared/tutorial-storage.mts";

type TutorialRequest = {
  source_content?: unknown;
  source_type?: unknown;
  source_url?: unknown;
  series_number?: unknown;
  mock_mode?: unknown;
};

type RailwayTutorialResponse = {
  client_id: string;
  content_type: string;
  series: Record<string, unknown>;
  lessons: unknown[];
  lesson_count: number;
  png_paths: string[];
  manifest_path: string;
  duration_ms: number;
};

type FetchedTutorialPng = {
  bytes: ArrayBuffer;
  width: number;
  height: number;
  sha256: string;
};

export type GeneratedTutorialSlide = {
  runId: string;
  fileName: string;
};

const ALLOWED_CLIENTS = new Set(["yellow", "origintrail", "squid", "babylon"]);
const TUTORIAL_CLIENTS = new Set(["yellow", "squid"]);
const MAX_SLIDES = 12;
const MAX_SLIDE_BYTES = 25 * 1024 * 1024;
const MAX_TUTORIAL_BYTES = 50 * 1024 * 1024;
const NETLIFY_REQUEST_BUDGET_MS = 55_000;
const RAILWAY_GENERATION_BUDGET_MS = 32_000;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const TUTORIAL_LAYOUTS = new Set([
  "P1_3CARD",
  "P2_BULLETS",
  "P3_BEFORE_AFTER",
  "P4_SUMMARY",
  "P5_COVER",
  "P6_STEP",
  "P7_DEFINITION",
  "P8_DIAGRAM",
]);

function tutorialClaimValue(value: unknown, depth = 0): unknown {
  if (depth > 6) throw new Error("invalid_tutorial_claims");
  if (typeof value === "string") {
    if (value.length > 2_000) throw new Error("invalid_tutorial_claims");
    return value.normalize("NFC").trim();
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("invalid_tutorial_claims");
    return value;
  }
  if (typeof value === "boolean" || value === null) return value;
  if (Array.isArray(value)) {
    if (value.length > 20) throw new Error("invalid_tutorial_claims");
    return value.map((item) => tutorialClaimValue(item, depth + 1));
  }
  if (!value || typeof value !== "object") throw new Error("invalid_tutorial_claims");
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length > 30) throw new Error("invalid_tutorial_claims");
  return Object.fromEntries(entries.map(([key, item]) => {
    if (
      !/^[a-z][a-z0-9_]{0,49}$/.test(key)
      || key.startsWith("_")
      || key.endsWith("_svg")
    ) throw new Error("invalid_tutorial_claims");
    return [key, tutorialClaimValue(item, depth + 1)];
  }));
}

export function normalizedTutorialLessons(value: unknown, lessonCount: number): Record<string, unknown>[] | null {
  if (!Array.isArray(value) || value.length !== lessonCount || value.length > MAX_SLIDES) return null;
  try {
    const lessons = value.map((rawLesson, index) => {
      const lesson = rawLesson && typeof rawLesson === "object" && !Array.isArray(rawLesson)
        ? rawLesson as Record<string, unknown>
        : {};
      if (
        !Object.keys(lesson).every((key) => ["lesson_number", "layout", "theme", "slots"].includes(key))
        || lesson.lesson_number !== index + 1
        || typeof lesson.layout !== "string"
        || !TUTORIAL_LAYOUTS.has(lesson.layout)
        || !["dark", "yellow"].includes(String(lesson.theme || ""))
        || !lesson.slots
        || typeof lesson.slots !== "object"
        || Array.isArray(lesson.slots)
      ) throw new Error("invalid_tutorial_claims");
      return {
        lesson_number: lesson.lesson_number,
        layout: lesson.layout,
        theme: lesson.theme,
        slots: tutorialClaimValue(lesson.slots),
      } as Record<string, unknown>;
    });
    return JSON.stringify(lessons).length <= 50_000 ? lessons : null;
  } catch {
    return null;
  }
}

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

function deadlineSignal(deadline: number, maximumMs: number): AbortSignal {
  const remaining = deadline - Date.now();
  if (remaining <= 250) throw new TutorialStorageError("tutorial_deadline_exceeded");
  return AbortSignal.timeout(Math.min(maximumMs, remaining));
}

export function tutorialRequestHash(input: {
  clientId: string;
  sourceContent: string;
  sourceType: string;
  sourceUrl: string;
  seriesNumber?: string;
  mockMode: boolean;
}): string {
  // Bind the idempotency key to the normalized request submitted by the caller,
  // not to content fetched from X. A URL-only source can change between retries;
  // the same submitted URL must still retrieve the immutable first result without
  // another network fetch, while a different URL must conflict.
  const canonicalRequest = JSON.stringify({
    client_id: input.clientId,
    source_content: input.sourceContent,
    source_type: input.sourceType,
    source_url: input.sourceUrl,
    series_number: input.seriesNumber || null,
    mock_mode: input.mockMode,
  });
  return createHash("sha256").update(canonicalRequest, "utf8").digest("hex");
}

function catalogRequestHash(existing: TutorialCatalogLookup): string | null {
  const contentHash = existing.content.request_hash;
  const generationHash = existing.generationMeta.request_hash;
  return typeof contentHash === "string"
    && SHA256_PATTERN.test(contentHash)
    && contentHash === generationHash
    ? contentHash
    : null;
}

export function generatedTutorialSlide(
  pngPath: unknown,
  clientId: string,
): GeneratedTutorialSlide | null {
  if (typeof pngPath !== "string" || pngPath.length > 2_048) return null;
  const marker = `/${clientId}/`;
  const markerIndex = pngPath.lastIndexOf(marker);
  const candidate = pngPath.startsWith(`${clientId}/`)
    ? pngPath
    : markerIndex >= 0
      ? pngPath.slice(markerIndex + 1)
      : "";
  const escapedClientId = clientId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = candidate.match(
    new RegExp(`^${escapedClientId}/(edu_[0-9]+)/(lesson_[0-9]{2}\\.png)$`),
  );
  if (!match) return null;
  return { runId: match[1], fileName: match[2] };
}

async function fetchGeneratedTutorialPng(
  railwayUrl: string,
  apiSecret: string,
  clientId: string,
  runId: string,
  fileName: string,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(20_000),
): Promise<FetchedTutorialPng> {
  let response: Response;
  try {
    const filePath = [clientId, runId, fileName].map(encodeURIComponent).join("/");
    response = await fetcher(`${railwayUrl}/files/${filePath}`, {
      headers: { "X-API-Key": apiSecret },
      signal,
    });
  } catch {
    throw new TutorialStorageError("generated_slide_unavailable");
  }
  if (!response.ok) throw new TutorialStorageError("generated_slide_unavailable");
  const declaredBytes = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredBytes) && declaredBytes > MAX_SLIDE_BYTES) {
    throw new TutorialStorageError("generated_slide_too_large");
  }
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength > MAX_SLIDE_BYTES) {
    throw new TutorialStorageError("generated_slide_too_large");
  }
  const dimensions = pngDimensions(bytes);
  if (!dimensions) throw new TutorialStorageError("invalid_generated_png");
  return {
    bytes,
    width: dimensions.width,
    height: dimensions.height,
    sha256: createHash("sha256").update(new Uint8Array(bytes)).digest("hex"),
  };
}

function tutorialCatalogTitle(series: Record<string, unknown>, clientId: string): string {
  for (const key of ["series_subtitle_kr", "series_title_en"]) {
    const value = series[key];
    if (typeof value === "string" && value.trim()) return value.trim().slice(0, 200);
  }
  return `${clientId === "squid" ? "Squid" : "Yellow"} 튜토리얼`;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function signedSlides(
  apiSecret: string,
  clientId: string,
  slides: Array<{ number: number; objectId: string; fileName: string }>,
): Array<{ number: number; filename: string; url: string }> {
  const slideExpiry = Math.floor(Date.now() / 1_000) + 3_600;
  return slides.map((slide) => ({
    number: slide.number,
    filename: slide.fileName,
    url: `/api/tutorial-slide/${encodeURIComponent(clientId)}/${encodeURIComponent(slide.objectId)}/${encodeURIComponent(slide.fileName)}?expires=${slideExpiry}&token=${signTutorialSlide(
      apiSecret,
      clientId,
      slide.objectId,
      slide.fileName,
      slideExpiry,
    )}`,
  }));
}

function catalogRetryResponse(
  existing: TutorialCatalogLookup,
  apiSecret: string,
  clientId: string,
): Record<string, unknown> {
  const series = objectValue(existing.content.series);
  const source = objectValue(existing.content.source);
  const duration = existing.generationMeta.duration_ms;
  const mockMode = existing.generationMeta.mock_mode === true;
  const lessons = normalizedTutorialLessons(existing.content.lessons, existing.slides.length);
  const factCheck = validatedFactCheckReport(existing.generationMeta.fact_check, "tutorial");
  if (!lessons || !factCheck) {
    throw new TutorialStorageError("fact_check_regeneration_required");
  }
  return {
    client_id: clientId,
    content_type: "edu_carousel",
    series,
    lessons,
    lesson_count: existing.slides.length,
    duration_ms: typeof duration === "number" && Number.isFinite(duration) ? duration : 0,
    source_mode: source.mode === "x_import" ? "x_import" : "provided",
    storage_backend: "supabase",
    content_item_id: existing.contentItemId,
    content_version_id: existing.contentVersionId,
    asset_ids: existing.slides.map((slide) => slide.assetId),
    brand_qa: existing.generationMeta.brand_qa || null,
    fact_check: factCheck,
    mock_mode: mockMode,
    reused: true,
    slides: signedSlides(
      apiSecret,
      clientId,
      existing.slides.map((slide) => ({
        number: slide.number,
        objectId: slide.assetId,
        fileName: slide.filename,
      })),
    ),
  };
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);
  const requestDeadline = Date.now() + NETLIFY_REQUEST_BUDGET_MS;

  const studioAccessError = requireStudioGenerationAccess(req);
  if (studioAccessError) return studioAccessError;

  const clientId = context.params.clientId;
  if (!clientId || !ALLOWED_CLIENTS.has(clientId)) {
    return json({ error: "unknown_client" }, 404);
  }
  if (!TUTORIAL_CLIENTS.has(clientId)) {
    return json({ error: "tutorial_not_enabled" }, 409);
  }
  const requestId = (req.headers.get("idempotency-key") || "").trim().toLowerCase();
  if (!UUID_PATTERN.test(requestId)) {
    return json({ error: "invalid_tutorial_idempotency_key" }, 400);
  }

  const apiSecret = Netlify.env.get("API_SECRET");
  if (!apiSecret) return json({ error: "server_not_configured" }, 503);
  const storageConfig = tutorialStorageConfig((name) => Netlify.env.get(name));
  if (!storageConfig) {
    return json({ error: "durable_storage_not_configured" }, 503);
  }

  let body: TutorialRequest;
  try {
    body = (await req.json()) as TutorialRequest;
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  const sourceContent = typeof body.source_content === "string" ? body.source_content.trim() : "";
  const sourceType = typeof body.source_type === "string" ? body.source_type : "article";
  const sourceUrl = typeof body.source_url === "string" ? body.source_url.trim() : "";
  const seriesNumber = typeof body.series_number === "string"
    ? body.series_number.trim().slice(0, 32) || undefined
    : undefined;
  const mockMode = body.mock_mode === true;
  if (!new Set(["tweet", "blog", "article"]).has(sourceType)) {
    return json({ error: "invalid_source_type" }, 400);
  }
  const requestHash = tutorialRequestHash({
    clientId,
    sourceContent,
    sourceType,
    sourceUrl,
    seriesNumber,
    mockMode,
  });

  let existingGeneration: TutorialCatalogLookup | null;
  try {
    await Promise.all([
      verifyPrivateTutorialBucket(
        storageConfig,
        fetch,
        deadlineSignal(requestDeadline, 6_000),
      ),
      verifyTutorialStorageScope(
        storageConfig,
        clientId,
        fetch,
        deadlineSignal(requestDeadline, 6_000),
      ),
    ]);
    existingGeneration = await findTutorialGeneration(
      storageConfig,
      requestId,
      clientId,
      fetch,
      deadlineSignal(requestDeadline, 4_000),
    );
  } catch (error) {
    const code = error instanceof TutorialStorageError
      ? error.code
      : "durable_storage_unavailable";
    const deadlineExceeded = code === "tutorial_deadline_exceeded"
      || Date.now() >= requestDeadline - 100;
    return json({ error: deadlineExceeded ? "tutorial_deadline_exceeded" : code }, deadlineExceeded ? 504 : 503);
  }
  if (existingGeneration) {
    if (catalogRequestHash(existingGeneration) !== requestHash) {
      return json({ error: "tutorial_idempotency_conflict" }, 409);
    }
    try {
      return json(catalogRetryResponse(existingGeneration, apiSecret, clientId));
    } catch (error) {
      const code = error instanceof TutorialStorageError
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
      "tutorial",
      fetch,
      deadlineSignal(requestDeadline, 4_000),
    );
    brandReviewGuidanceAvailable = true;
  } catch {
    // Review guidance is optional style context, never source evidence.
  }

  let resolvedSource: ResolvedSource;
  try {
    resolvedSource = await resolveSourceInput(sourceContent, sourceUrl, fetch, false);
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

  try {
    const generationResponse = await fetch(
      `${railwayUrl}/clients/${encodeURIComponent(clientId)}/generate/edu-carousel`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiSecret,
        },
        body: JSON.stringify({
          source_content: resolvedSource.content,
          source_type: sourceType,
          source_url: resolvedSource.url,
          series_number: seriesNumber,
          mock_llm: mockMode,
          ...(brandReviewGuidanceAvailable
            ? { brand_review_guidance: brandReviewGuidance }
            : {}),
        }),
        signal: deadlineSignal(requestDeadline, RAILWAY_GENERATION_BUDGET_MS),
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

    const result = (await generationResponse.json()) as RailwayTutorialResponse;
    if (
      !result
      || typeof result !== "object"
      || result.client_id !== clientId
      || result.content_type !== "edu_carousel"
      || !result.series
      || typeof result.series !== "object"
      || Array.isArray(result.series)
      || !Array.isArray(result.lessons)
      || !Number.isSafeInteger(result.lesson_count)
      || typeof result.duration_ms !== "number"
      || !Number.isFinite(result.duration_ms)
      || result.duration_ms < 0
    ) {
      return json({ error: "invalid_generation_response" }, 502);
    }
    const pngPaths = Array.isArray(result.png_paths) ? result.png_paths : [];
    const tutorialLessons = normalizedTutorialLessons(result.lessons, result.lesson_count);
    if (
      !pngPaths.length
      || pngPaths.length > MAX_SLIDES
      || result.lesson_count !== pngPaths.length
      || !tutorialLessons
    ) {
      return json({ error: "invalid_generated_file_count" }, 502);
    }
    const parsedSlides = pngPaths.map((path) => generatedTutorialSlide(path, clientId));
    if (parsedSlides.some((slide) => slide === null)) {
      return json({ error: "invalid_generated_file_path" }, 502);
    }
    const expectedRunId = parsedSlides[0]!.runId;
    if (parsedSlides.some((slide, index) => (
      slide!.runId !== expectedRunId
      || slide!.fileName !== `lesson_${String(index + 1).padStart(2, "0")}.png`
    ))) {
      return json({ error: "invalid_generated_file_sequence" }, 502);
    }
    const generatedPngs: FetchedTutorialPng[] = [];
    let tutorialBytes = 0;
    for (const slide of parsedSlides) {
      const generatedPng = await fetchGeneratedTutorialPng(
        railwayUrl,
        apiSecret,
        clientId,
        slide!.runId,
        slide!.fileName,
        fetch,
        deadlineSignal(requestDeadline, 8_000),
      );
      tutorialBytes += generatedPng.bytes.byteLength;
      if (tutorialBytes > MAX_TUTORIAL_BYTES) {
        return json({ error: "generated_tutorial_too_large" }, 502);
      }
      generatedPngs.push(generatedPng);
    }
    const brandQa = evaluateBrandQuality({
      clientId: clientId as BrandQaClient,
      contentKind: "tutorial",
      sourceText: resolvedSource.content,
      series: result.series,
      lessonCount: result.lesson_count,
    });
    const factCheck = evaluateFactCheck({
      contentKind: "tutorial",
      source: resolvedSource,
      publicText: {
        series: result.series,
        lessons: tutorialLessons,
        lesson_count: String(result.lesson_count),
      },
      artifactSha256: generatedPngs.map((slide) => slide.sha256),
      brandQa,
    });

    const storedSlides: StoredTutorialSlide[] = [];
    const attemptedStoragePaths: string[] = [];
    const catalogRequestId = requestId;
    let catalogCommitted = false;
    try {
      for (let index = 0; index < generatedPngs.length; index += 1) {
        const parsed = parsedSlides[index]!;
        const objectId = randomUUID();
        const intendedStoragePath = tutorialStoragePath(
          storageConfig.workspaceId,
          clientId,
          objectId,
          parsed.fileName,
        );
        if (!intendedStoragePath) {
          throw new TutorialStorageError("invalid_durable_slide_path");
        }
        // Track the path before the request starts: the object may have been
        // persisted even when the upload response times out or disconnects.
        attemptedStoragePaths.push(intendedStoragePath);
        storedSlides.push(await uploadTutorialSlide(
          storageConfig,
          clientId,
          objectId,
          parsed.fileName,
          generatedPngs[index].bytes,
          fetch,
          deadlineSignal(requestDeadline, 8_000),
        ));
      }

      const catalogSlides: TutorialCatalogSlide[] = storedSlides.map((slide, index) => ({
        number: index + 1,
        filename: parsedSlides[index]!.fileName,
        storage_path: slide.storagePath,
        byte_size: slide.byteSize,
        sha256: generatedPngs[index].sha256,
        width: generatedPngs[index].width,
        height: generatedPngs[index].height,
      }));
      const catalog = await recordTutorialGeneration(storageConfig, {
        requestId: catalogRequestId,
        clientId,
        title: tutorialCatalogTitle(result.series, clientId),
        content: {
          request_hash: requestHash,
          series: result.series,
          lessons: tutorialLessons,
          lesson_count: result.lesson_count,
          source: {
            type: sourceType,
            url: resolvedSource.url,
            mode: resolvedSource.mode,
            content: resolvedSource.content,
          },
        },
        generationMeta: {
          request_hash: requestHash,
          duration_ms: result.duration_ms,
          renderer: "railway",
          storage_backend: "supabase",
          mock_mode: mockMode,
          ...brandReviewGuidanceAudit(brandReviewGuidance),
          brand_review_guidance_available: brandReviewGuidanceAvailable,
          brand_qa: brandQa,
          fact_check: factCheck,
        },
        slides: catalogSlides,
      }, fetch, deadlineSignal(requestDeadline, 6_000));
      catalogCommitted = true;

      const slides = signedSlides(apiSecret, clientId, storedSlides.map((slide, index) => ({
        number: index + 1,
        objectId: slide.objectId,
        fileName: parsedSlides[index]!.fileName,
      })));

      return json({
        client_id: result.client_id,
        content_type: result.content_type,
        series: result.series,
        lessons: tutorialLessons,
        lesson_count: result.lesson_count,
        duration_ms: result.duration_ms,
        source_mode: resolvedSource.mode,
        storage_backend: "supabase",
        content_item_id: catalog.contentItemId,
        content_version_id: catalog.contentVersionId,
        asset_ids: catalog.assetIds,
        brand_qa: brandQa,
        fact_check: factCheck,
        mock_mode: body.mock_mode === true,
        reused: false,
        slides,
      });
    } catch (error) {
      if (
        !catalogCommitted
        && (!(error instanceof TutorialStorageError) || error.cleanupSafe)
      ) {
        await removeTutorialSlides(
          storageConfig,
          attemptedStoragePaths,
          fetch,
          AbortSignal.timeout(2_000),
        );
      }
      throw error;
    }
  } catch (error) {
    const deadlineExceeded = error instanceof TutorialStorageError
      ? error.code === "tutorial_deadline_exceeded" || Date.now() >= requestDeadline - 100
      : Date.now() >= requestDeadline - 100;
    if (deadlineExceeded) {
      return json({ error: "tutorial_deadline_exceeded" }, 504);
    }
    if (error instanceof TutorialStorageError) {
      if (error.code === "tutorial_idempotency_conflict") {
        try {
          // A same-payload request can race between the initial lookup and the
          // atomic catalog write. Its losing uploads were already rolled back;
          // return the winner only when the stored request hash proves equality.
          const concurrentGeneration = await findTutorialGeneration(
            storageConfig,
            requestId,
            clientId,
            fetch,
            deadlineSignal(requestDeadline, 4_000),
          );
          if (
            concurrentGeneration
            && catalogRequestHash(concurrentGeneration) === requestHash
          ) {
            return json(catalogRetryResponse(
              concurrentGeneration,
              apiSecret,
              clientId,
            ));
          }
          return json({ error: "tutorial_idempotency_conflict" }, 409);
        } catch (lookupError) {
          const lookupCode = lookupError instanceof TutorialStorageError
            ? lookupError.code
            : "durable_catalog_lookup_failed";
          const lookupDeadlineExceeded = lookupCode === "tutorial_deadline_exceeded"
            || Date.now() >= requestDeadline - 100;
          return json(
            { error: lookupDeadlineExceeded ? "tutorial_deadline_exceeded" : lookupCode },
            lookupDeadlineExceeded
              ? 504
              : lookupCode === "fact_check_regeneration_required" ? 409 : 503,
          );
        }
      }
      return json(
        { error: error.code },
        502,
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
  path: "/api/tutorial/:clientId",
};
