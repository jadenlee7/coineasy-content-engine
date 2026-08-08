import {
  CONTENT_CATALOG_CLIENTS,
  type ContentCatalogConfig,
  type ContentCatalogClient,
  isCatalogUuid,
} from "./content-catalog.mts";

const FEATURE_FLAG = "STUDIO_TELEGRAM_PUBLISH_ENABLED";
const ALLOWED_CLIENTS_ENV = "STUDIO_TELEGRAM_PUBLISH_ALLOWED_CLIENTS";
const FIRST_SLICE_CLIENT: ContentCatalogClient = "squid";
const WORKER_TOKEN_MINIMUM = 32;
const WORKER_TOKEN_MAXIMUM = 512;
const WORKER_TOKEN_FORBIDDEN_PATTERN = /[^\x21-\x7e]/;
const RESERVED_WORKER_SECRET_ENVS = [
  "API_SECRET",
  "STUDIO_ACCESS_TOKEN",
  "SUPABASE_SERVICE_ROLE_KEY",
  "TELEGRAM_BOT_TOKEN_SQUID",
  "TELEGRAM_BOT_TOKEN_YELLOW",
  "TELEGRAM_BOT_TOKEN_ORIGINTRAIL",
  "TELEGRAM_BOT_TOKEN_BABYLON",
] as const;
const PUBLICATION_STATUSES = new Set([
  "queued",
  "publishing",
  "published",
  "failed",
  "delivery_unknown",
  "cancelled",
]);
const ERROR_CODE_PATTERN = /^[a-z][a-z0-9_]{0,79}$/;
const TELEGRAM_URL_PATTERN = /^https:\/\/t\.me\/[A-Za-z][A-Za-z0-9_]{4,31}\/[1-9][0-9]{0,18}$/;
const ISO_TIMESTAMP_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|([+-])(\d{2}):(\d{2}))$/;
const CONTENT_CATALOG_CLIENT_SET = new Set<string>(CONTENT_CATALOG_CLIENTS);

export type StudioTelegramPublicationStatus =
  | "queued"
  | "publishing"
  | "published"
  | "failed"
  | "delivery_unknown"
  | "cancelled";

export type StudioTelegramPublication = {
  publication_id: string;
  content_item_id: string;
  content_version_id: string;
  channel: "telegram";
  status: StudioTelegramPublicationStatus;
  delivery_started_at: string | null;
  external_url: string | null;
  error_code: string | null;
  reused: boolean;
};

export type PublicationWorkerConfig = {
  railwayUrl: string;
  workerToken: string;
};

export type StudioTelegramPublicationTarget = {
  client_id: ContentCatalogClient;
  content_kind: string;
  current_version_id: string;
};

export type StudioTelegramDeliveryResolution = {
  contentItemId: string;
  contentVersionId: string;
  publicationId: string;
  deliveryStartedAt: string;
  publicChannel: "squid_kor_update";
  idempotencyKey: string;
};

export class ContentPublicationError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "ContentPublicationError";
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizedIsoTimestamp(value: unknown): string | null | undefined {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string" || value.length > 40) return undefined;
  const match = value.match(ISO_TIMESTAMP_PATTERN);
  if (!match) return undefined;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const offsetHour = match[8] === undefined ? 0 : Number(match[8]);
  const offsetMinute = match[9] === undefined ? 0 : Number(match[9]);
  const maximumDay = month >= 1 && month <= 12
    ? new Date(Date.UTC(year, month, 0)).getUTCDate()
    : 0;
  if (
    year < 1
    || day < 1
    || day > maximumDay
    || hour > 23
    || minute > 59
    || second > 59
    || offsetHour > 23
    || offsetMinute > 59
  ) return undefined;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : undefined;
}

function publicationError(message: string, status: number): string {
  const normalized = message.toLowerCase();
  if (normalized.includes("delivery resolution")) {
    if (normalized.includes("does not exist")) {
      return "telegram_publication_not_found";
    }
    if (normalized.includes("is invalid")) {
      return "invalid_telegram_delivery_resolution";
    }
    return "telegram_delivery_resolution_conflict";
  }
  if (normalized.includes("delivery was already observed publicly")) {
    return "telegram_delivery_resolution_conflict";
  }
  if (normalized.includes("idempotency")) return "telegram_publication_idempotency_conflict";
  if (
    normalized.includes("mock/test")
    || normalized.includes("mock content")
    || normalized.includes("production version")
  ) {
    return "mock_content_cannot_be_published";
  }
  if (
    normalized.includes("not approved")
    || normalized.includes("current approved")
    || normalized.includes("approval record")
    || normalized.includes("double-fact-check")
    || normalized.includes("double fact check")
    || normalized.includes("fact-check")
    || normalized.includes("fact check")
  ) {
    return "telegram_publication_not_approved";
  }
  if (normalized.includes("client is not active")) {
    return "telegram_publication_client_not_allowed";
  }
  if (normalized.includes("version") && normalized.includes("current")) {
    return "telegram_publication_version_conflict";
  }
  if (normalized.includes("daily_news") || normalized.includes("daily news")) {
    return "telegram_publication_kind_not_supported";
  }
  if (
    normalized.includes("telegram copy")
    || normalized.includes("telegram caption")
    || normalized.includes("png")
  ) {
    return "telegram_publication_payload_incomplete";
  }
  if (normalized.includes("not found") || status === 404) {
    return "telegram_publication_not_found";
  }
  if (status === 409) return "telegram_publication_conflict";
  if (status === 400 || status === 422) return "invalid_telegram_publication";
  return "telegram_publication_storage_unavailable";
}

function normalizedPublication(
  value: unknown,
  expectedItemId: string,
  expectedVersionId: string,
  reusedDefault: boolean,
): StudioTelegramPublication {
  const row = isRecord(value) && isRecord(value.publication)
    ? value.publication
    : value;
  if (!isRecord(row)) {
    throw new ContentPublicationError("telegram_publication_invalid_response");
  }
  const publicationId = String(row.publication_id ?? row.id ?? "").toLowerCase();
  const contentItemId = String(row.content_item_id ?? "").toLowerCase();
  const contentVersionId = String(row.content_version_id ?? "").toLowerCase();
  const status = String(row.status ?? "");
  const deliveryStartedAt = normalizedIsoTimestamp(row.delivery_started_at);
  const externalUrl = row.external_url === null || row.external_url === undefined
    ? null
    : String(row.external_url);
  const rawErrorCode = row.error_code ?? row.last_error_code ?? null;
  const errorCode = rawErrorCode === null || rawErrorCode === undefined
    ? null
    : String(rawErrorCode);
  const reused = typeof row.reused === "boolean" ? row.reused : reusedDefault;
  if (
    !isCatalogUuid(publicationId)
    || contentItemId !== expectedItemId.toLowerCase()
    || contentVersionId !== expectedVersionId.toLowerCase()
    || row.channel !== "telegram"
    || !PUBLICATION_STATUSES.has(status)
    || deliveryStartedAt === undefined
    || !(externalUrl === null || TELEGRAM_URL_PATTERN.test(externalUrl))
    || (status === "published" ? externalUrl === null : externalUrl !== null)
    || !(errorCode === null || ERROR_CODE_PATTERN.test(errorCode))
  ) {
    throw new ContentPublicationError("telegram_publication_invalid_response");
  }
  return {
    publication_id: publicationId,
    content_item_id: contentItemId,
    content_version_id: contentVersionId,
    channel: "telegram",
    status: status as StudioTelegramPublicationStatus,
    delivery_started_at: deliveryStartedAt,
    external_url: externalUrl,
    error_code: errorCode,
    reused,
  };
}

function headers(config: ContentCatalogConfig): Record<string, string> {
  return {
    apikey: config.serviceRoleKey,
    Authorization: `Bearer ${config.serviceRoleKey}`,
    "Content-Type": "application/json",
  };
}

async function rpc(
  config: ContentCatalogConfig,
  functionName: string,
  body: Record<string, unknown>,
  fetcher: typeof fetch,
  signal: AbortSignal,
): Promise<unknown> {
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/${functionName}`, {
      method: "POST",
      headers: headers(config),
      body: JSON.stringify(body),
      signal,
    });
  } catch {
    throw new ContentPublicationError("telegram_publication_storage_unavailable");
  }
  if (!response.ok) {
    const failure = await response.json().catch(() => null);
    const message = isRecord(failure)
      ? String(failure.message ?? failure.error ?? "")
      : "";
    throw new ContentPublicationError(publicationError(message, response.status));
  }
  try {
    return await response.json();
  } catch {
    throw new ContentPublicationError("telegram_publication_invalid_response");
  }
}

export function studioTelegramPublishEnabled(
  getEnv: (name: string) => string | undefined,
): boolean {
  return getEnv(FEATURE_FLAG) === "true";
}

export function studioTelegramPublishAllowedClients(
  getEnv: (name: string) => string | undefined,
): ReadonlySet<ContentCatalogClient> {
  return getEnv(ALLOWED_CLIENTS_ENV) === FIRST_SLICE_CLIENT
    ? new Set([FIRST_SLICE_CLIENT])
    : new Set();
}

export function studioTelegramPublishClientAllowed(
  clientId: unknown,
  getEnv: (name: string) => string | undefined,
): clientId is ContentCatalogClient {
  return typeof clientId === "string"
    && studioTelegramPublishAllowedClients(getEnv).has(clientId as ContentCatalogClient);
}

export function publicationWorkerConfig(
  getEnv: (name: string) => string | undefined,
): PublicationWorkerConfig | null {
  const railwayUrl = (getEnv("RAILWAY_API_URL") || "").trim().replace(/\/+$/, "");
  const workerToken = getEnv("PUBLICATION_WORKER_TOKEN") || "";
  const reusedSecret = RESERVED_WORKER_SECRET_ENVS.some((name) => {
    const reservedValue = getEnv(name) || "";
    return reservedValue.length > 0 && reservedValue === workerToken;
  });
  try {
    const url = new URL(railwayUrl);
    if (
      url.protocol !== "https:"
      || url.username
      || url.password
      || (url.pathname !== "/" && url.pathname !== "")
      || url.search
      || url.hash
      || workerToken.length < WORKER_TOKEN_MINIMUM
      || workerToken.length > WORKER_TOKEN_MAXIMUM
      || WORKER_TOKEN_FORBIDDEN_PATTERN.test(workerToken)
      || reusedSecret
    ) return null;
  } catch {
    return null;
  }
  return { railwayUrl, workerToken };
}

export async function requestStudioTelegramPublication(
  config: ContentCatalogConfig,
  contentItemId: string,
  contentVersionId: string,
  idempotencyKey: string,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<StudioTelegramPublication> {
  const raw = await rpc(config, "request_studio_telegram_publication", {
    target_workspace_id: config.workspaceId,
    target_content_item_id: contentItemId.toLowerCase(),
    target_content_version_id: contentVersionId.toLowerCase(),
    request_idempotency_key: idempotencyKey.toLowerCase(),
  }, fetcher, signal);
  return normalizedPublication(raw, contentItemId, contentVersionId, false);
}

export async function getStudioTelegramPublicationTarget(
  config: ContentCatalogConfig,
  contentItemId: string,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<StudioTelegramPublicationTarget | null> {
  const raw = await rpc(config, "get_content_library_item", {
    target_workspace_id: config.workspaceId,
    target_content_item_id: contentItemId.toLowerCase(),
  }, fetcher, signal);
  if (raw === null) return null;
  if (!isRecord(raw)) {
    throw new ContentPublicationError("telegram_publication_invalid_response");
  }
  const currentVersionId = String(
    raw.current_version_id ?? raw.content_version_id ?? "",
  ).toLowerCase();
  if (
    String(raw.content_item_id ?? "").toLowerCase() !== contentItemId.toLowerCase()
    || !CONTENT_CATALOG_CLIENT_SET.has(String(raw.client_id ?? ""))
    || typeof raw.content_kind !== "string"
    || !isCatalogUuid(currentVersionId)
  ) {
    throw new ContentPublicationError("telegram_publication_invalid_response");
  }
  return {
    client_id: raw.client_id as ContentCatalogClient,
    content_kind: raw.content_kind,
    current_version_id: currentVersionId,
  };
}

export async function getStudioTelegramPublication(
  config: ContentCatalogConfig,
  contentItemId: string,
  contentVersionId: string,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<StudioTelegramPublication | null> {
  const raw = await rpc(config, "get_studio_telegram_publication", {
    target_workspace_id: config.workspaceId,
    target_content_item_id: contentItemId.toLowerCase(),
    target_content_version_id: contentVersionId.toLowerCase(),
  }, fetcher, signal);
  if (raw === null) return null;
  return normalizedPublication(raw, contentItemId, contentVersionId, true);
}

export async function cancelStudioTelegramDeliveryUnknown(
  config: ContentCatalogConfig,
  resolution: StudioTelegramDeliveryResolution,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<StudioTelegramPublication> {
  const raw = await rpc(config, "cancel_unobserved_exact_telegram_publication", {
    target_workspace_id: config.workspaceId,
    target_content_item_id: resolution.contentItemId.toLowerCase(),
    target_content_version_id: resolution.contentVersionId.toLowerCase(),
    target_publication_id: resolution.publicationId.toLowerCase(),
    target_delivery_started_at: resolution.deliveryStartedAt,
    target_public_channel: resolution.publicChannel,
    target_channel_checked: true,
    target_caption_checked: true,
    target_png_checked: true,
    request_idempotency_key: resolution.idempotencyKey.toLowerCase(),
  }, fetcher, signal);
  return normalizedPublication(
    raw,
    resolution.contentItemId,
    resolution.contentVersionId,
    false,
  );
}

export async function kickTelegramPublicationWorker(
  config: PublicationWorkerConfig,
  fetcher: typeof fetch = fetch,
): Promise<boolean> {
  try {
    const response = await fetcher(
      `${config.railwayUrl}/internal/publications/telegram/run-once`,
      {
        method: "POST",
        headers: { "X-Publication-Worker-Key": config.workerToken },
        signal: AbortSignal.timeout(5_000),
      },
    );
    if (!response.ok) return false;
    const result = await response.json().catch(() => null);
    return isRecord(result)
      && result.ok === true
      && result.accepted === true
      && result.status === "scheduled";
  } catch {
    return false;
  }
}
