import type { Config } from "@netlify/functions";
import { hasValidStudioAutomationAccess } from "./_shared/studio-session.mts";

const BODY = {
  schema_version: "1.0",
  generation_contract: "double-fact-check@1",
  generated_content_kinds: ["daily_news", "article", "tutorial"],
  tutorial_claims_contract: "lessons@1",
} as const;

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
      "Vary": "X-Studio-Automation-Key",
    },
  });
}

export default async (req: Request): Promise<Response> => {
  if (req.method !== "GET") return json({ error: "method_not_allowed" }, 405);
  if (!hasValidStudioAutomationAccess(req)) {
    return json({ error: "studio_automation_auth_required" }, 401);
  }
  return json(BODY);
};

export const config: Config = {
  path: "/api/studio-capabilities",
};
