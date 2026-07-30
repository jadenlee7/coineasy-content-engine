import { createHash, timingSafeEqual } from "node:crypto";
import type { ContentCatalogConfig } from "./content-catalog.mts";

const CLIENTS = new Set(["yellow", "origintrail", "squid", "babylon"]);
const MONTH_PATTERN = /^[0-9]{4}-(0[1-9]|1[0-2])$/;
const TOKEN_HEADER = "x-content-kpi-key";

export class MonthlyKpiError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "MonthlyKpiError";
    this.code = code;
  }
}

function digest(value: string): Buffer {
  return createHash("sha256").update(value, "utf8").digest();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function boundedCount(value: unknown, maximum = 366): value is number {
  return typeof value === "number"
    && Number.isSafeInteger(value)
    && value >= 0
    && value <= maximum;
}

export function currentKstMonth(now = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
  }).formatToParts(now);
  const year = parts.find((part) => part.type === "year")?.value || "";
  const month = parts.find((part) => part.type === "month")?.value || "";
  return `${year}-${month}`;
}

export function normalizedKpiMonth(value: unknown): string | null {
  if (typeof value !== "string" || !MONTH_PATTERN.test(value)) return null;
  return value;
}

export function hasValidMonthlyKpiAccess(
  request: Request,
  getEnv: (name: string) => string | undefined = (name) => Netlify.env.get(name),
): boolean {
  const expected = (getEnv("CONTENT_KPI_SYNC_TOKEN") || "").trim();
  const candidate = (request.headers.get(TOKEN_HEADER) || "").trim();
  if (
    expected.length < 32
    || expected.length > 512
    || candidate.length < 32
    || candidate.length > 512
  ) return false;
  return timingSafeEqual(digest(candidate), digest(expected));
}

export function validateMonthlyKpiSnapshot(
  value: unknown,
  workspaceId: string,
  month: string,
): Record<string, unknown> {
  if (!isRecord(value)) throw new MonthlyKpiError("monthly_kpi_invalid_response");
  const exactKeys = [
    "ok",
    "schema_version",
    "workspace_id",
    "month",
    "timezone",
    "generated_at",
    "days_in_month",
    "elapsed_days",
    "counting_rule",
    "clients",
  ];
  if (
    Object.keys(value).sort().join(",") !== exactKeys.sort().join(",")
    || value.ok !== true
    || value.schema_version !== "1.0"
    || value.workspace_id !== workspaceId
    || value.month !== month
    || value.timezone !== "Asia/Seoul"
    || value.counting_rule !== "distinct_published_content_item"
    || typeof value.generated_at !== "string"
    || !Number.isFinite(Date.parse(value.generated_at))
    || !boundedCount(value.days_in_month, 31)
    || value.days_in_month < 28
    || !boundedCount(value.elapsed_days, value.days_in_month)
    || !Array.isArray(value.clients)
    || value.clients.length > CLIENTS.size
  ) throw new MonthlyKpiError("monthly_kpi_invalid_response");

  const seen = new Set<string>();
  for (const raw of value.clients) {
    if (!isRecord(raw)) throw new MonthlyKpiError("monthly_kpi_invalid_response");
    const clientId = raw.client_id;
    if (
      typeof clientId !== "string"
      || !CLIENTS.has(clientId)
      || seen.has(clientId)
      || typeof raw.display_name !== "string"
      || raw.display_name.length < 1
      || raw.display_name.length > 120
      || raw.community_event_source !== "notion"
    ) throw new MonthlyKpiError("monthly_kpi_invalid_response");
    seen.add(clientId);

    const targets = isRecord(raw.targets) ? raw.targets : {};
    const expected = isRecord(raw.expected_to_date) ? raw.expected_to_date : {};
    const published = isRecord(raw.published) ? raw.published : {};
    const gaps = isRecord(raw.gaps_to_target) ? raw.gaps_to_target : {};
    if (
      targets.daily_news !== value.days_in_month
      || targets.article !== 2
      || targets.tutorial !== 2
      || targets.community_event !== 2
      || !boundedCount(expected.daily_news, value.days_in_month)
      || !boundedCount(expected.article, 2)
      || !boundedCount(expected.tutorial, 2)
      || !boundedCount(expected.community_event, 2)
      || !boundedCount(published.daily_news)
      || !boundedCount(published.article)
      || !boundedCount(published.tutorial)
      || !boundedCount(gaps.daily_news, value.days_in_month)
      || !boundedCount(gaps.article, 2)
      || !boundedCount(gaps.tutorial, 2)
      || !isRecord(raw.pipeline)
    ) throw new MonthlyKpiError("monthly_kpi_invalid_response");
  }
  return value;
}

export async function fetchMonthlyKpi(
  config: ContentCatalogConfig,
  month: string,
  fetchImpl: typeof fetch = fetch,
): Promise<Record<string, unknown>> {
  if (!normalizedKpiMonth(month)) {
    throw new MonthlyKpiError("invalid_month");
  }
  let response: Response;
  try {
    response = await fetchImpl(
      `${config.supabaseUrl}/rest/v1/rpc/get_monthly_content_kpi`,
      {
        method: "POST",
        headers: {
          apikey: config.serviceRoleKey,
          Authorization: `Bearer ${config.serviceRoleKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          target_workspace_id: config.workspaceId,
          target_month: `${month}-01`,
        }),
        signal: AbortSignal.timeout(8_000),
      },
    );
  } catch {
    throw new MonthlyKpiError("monthly_kpi_unavailable");
  }
  if (!response.ok) throw new MonthlyKpiError("monthly_kpi_unavailable");
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new MonthlyKpiError("monthly_kpi_invalid_response");
  }
  return validateMonthlyKpiSnapshot(body, config.workspaceId, month);
}
