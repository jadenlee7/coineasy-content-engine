import type { Config } from "@netlify/functions";
import { contentCatalogConfig } from "./_shared/content-catalog.mts";
import {
  currentKstMonth,
  fetchMonthlyKpi,
  hasValidMonthlyKpiAccess,
  MonthlyKpiError,
  normalizedKpiMonth,
} from "./_shared/monthly-kpi.mts";

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}

export default async (request: Request): Promise<Response> => {
  if (request.method !== "GET") {
    return json({ error: "method_not_allowed" }, 405);
  }
  if (!hasValidMonthlyKpiAccess(request)) {
    return json({ error: "kpi_auth_required" }, 401);
  }
  const config = contentCatalogConfig((name) => Netlify.env.get(name));
  if (!config) return json({ error: "kpi_storage_not_configured" }, 503);

  const url = new URL(request.url);
  const rawMonth = url.searchParams.get("month");
  const month = rawMonth === null ? currentKstMonth() : normalizedKpiMonth(rawMonth);
  if (!month) return json({ error: "invalid_month" }, 400);

  try {
    return json(await fetchMonthlyKpi(config, month));
  } catch (error) {
    const code = error instanceof MonthlyKpiError
      ? error.code
      : "monthly_kpi_unavailable";
    return json(
      { error: code },
      code === "invalid_month"
        ? 400
        : code === "monthly_kpi_invalid_response"
          ? 502
          : 503,
    );
  }
};

export const config: Config = {
  path: "/api/kpi/monthly",
};
