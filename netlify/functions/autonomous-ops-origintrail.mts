import type { Config } from "@netlify/functions";

import {
  autonomousOpsConfigured,
  AutonomousOpsError,
  autonomousOpsLedgerEnabled,
  autonomousOpsSupabaseConfig,
  executeAutonomousOpsAction,
  hasAutonomousOpsAccess,
  parseAutonomousOpsAction,
} from "./_shared/autonomous-ops.mts";
import { contentCatalogConfig } from "./_shared/content-catalog.mts";

const MAX_BODY_BYTES = 4_096;

function json(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: {
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
    "Vary": "x-coineasy-autonomous-ops-key",
    "X-Content-Type-Options": "nosniff",
  }});
}

async function body(request: Request): Promise<unknown> {
  const raw = await request.text();
  if (Buffer.byteLength(raw, "utf8") > MAX_BODY_BYTES) {
    throw new AutonomousOpsError("invalid_autonomous_ops_request");
  }
  try { return JSON.parse(raw); } catch {
    throw new AutonomousOpsError("invalid_autonomous_ops_request");
  }
}

export default async (request: Request): Promise<Response> => {
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
  const getEnv = (name: string) => Netlify.env.get(name);
  if (!autonomousOpsConfigured(getEnv)) {
    return json({ error: "autonomous_ops_not_configured" }, 503);
  }
  if (!hasAutonomousOpsAccess(request, getEnv)) {
    return json({ error: "autonomous_ops_auth_required" }, 401);
  }
  if (!autonomousOpsLedgerEnabled(getEnv)) {
    return json({ error: "autonomous_ops_disabled" }, 503);
  }
  const catalog = contentCatalogConfig(getEnv);
  if (!catalog) return json({ error: "autonomous_ops_storage_not_configured" }, 503);
  try {
    const action = parseAutonomousOpsAction(await body(request));
    return json(await executeAutonomousOpsAction(
      autonomousOpsSupabaseConfig(catalog, getEnv), action,
    ));
  } catch (error) {
    const code = error instanceof AutonomousOpsError
      ? error.code : "autonomous_ops_unavailable";
    return json({ error: code }, code === "invalid_autonomous_ops_request"
      ? 400 : code === "autonomous_ops_conflict" ? 409 : 502);
  }
};

export const config: Config = { path: "/api/autonomous-ops/origintrail" };
