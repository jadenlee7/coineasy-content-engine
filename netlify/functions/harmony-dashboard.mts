import type { Config, Context } from "@netlify/functions";

import {
  getHarmonyDashboard,
  harmonyDashboardConfig,
  HarmonyDashboardError,
  harmonyDashboardPreviewCommitMatches,
  harmonyDashboardPreviewEnabled,
  harmonyDashboardPreviewOrigin,
} from "./_shared/harmony-dashboard.mts";
import {
  requireStudioSession,
} from "./_shared/studio-session.mts";

function json(
  body: unknown,
  status = 200,
  extraHeaders: HeadersInit = {},
): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
      "Vary": "Cookie",
      "X-Content-Type-Options": "nosniff",
      ...extraHeaders,
    },
  });
}

export default async (
  request: Request,
  _context?: Context,
): Promise<Response> => {
  if (request.method !== "GET") {
    return json({ error: "method_not_allowed" }, 405, { Allow: "GET" });
  }

  const getEnv = (name: string) => Netlify.env.get(name);
  const previewOrigin = harmonyDashboardPreviewOrigin(getEnv);
  if (!previewOrigin) {
    return json({ error: "harmony_dashboard_preview_only" }, 403);
  }
  if (!harmonyDashboardPreviewCommitMatches(getEnv)) {
    return json({ error: "harmony_dashboard_preview_commit_mismatch" }, 409);
  }
  if (!harmonyDashboardPreviewEnabled(getEnv)) {
    return json({ error: "harmony_dashboard_preview_disabled" }, 503);
  }
  if (new URL(request.url).origin !== previewOrigin) {
    return json({ error: "invalid_harmony_dashboard_preview_host" }, 421);
  }

  const sessionError = requireStudioSession(request);
  if (sessionError) return sessionError;

  const config = harmonyDashboardConfig(getEnv);
  if (!config) {
    return json({ error: "harmony_dashboard_not_configured" }, 503);
  }

  try {
    return json(await getHarmonyDashboard(config, fetch));
  } catch (error) {
    const code = error instanceof HarmonyDashboardError
      ? error.code
      : "harmony_dashboard_unavailable";
    return json({ error: code }, 502);
  }
};

export const config: Config = {
  path: "/api/harmony/dashboard",
};
