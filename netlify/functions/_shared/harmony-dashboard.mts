import { createHash } from "node:crypto";

import { currentStudioReleaseSha } from "./studio-release.mts";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const HASH_PATTERN = /^[a-f0-9]{64}$/;
const GIT_SHA_PATTERN = /^[a-f0-9]{40}$/;
const JWT_PATTERN = /^[A-Za-z0-9_-]{2,2048}\.[A-Za-z0-9_-]{2,8192}\.[A-Za-z0-9_-]{16,8192}$/;
const PROJECT_HOST_PATTERN = /^([a-z0-9-]{8,80})\.supabase\.co$/;
const SECRET_PATTERN = /(?:sk|xai)-[A-Za-z0-9_-]{20,}|(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}|sb_(?:secret|publishable)_[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|Bearer\s+[A-Za-z0-9._~+/-]{16,}/i;
const CONTROL_PATTERN = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;
const MAX_RESPONSE_BYTES = 256 * 1024;
const PREVIEW_CLIENT_ID = "squid";
const RPC_NAME = "get_preview_harmony_dashboard";

const SPECIALIST_CONTRACT = [
  {
    stage: "plan",
    actor: "grok_bot",
    capability: "harmony_plan",
    specialistCode: "squid_planner",
  },
  {
    stage: "private_content",
    actor: "content_engine",
    capability: "harmony_prepare_private_content",
    specialistCode: "squid_private_content_producer",
  },
  {
    stage: "independent_qa",
    actor: "codex",
    capability: "harmony_independent_qa",
    specialistCode: "squid_independent_qa",
  },
  {
    stage: "operator_inbox",
    actor: "human_operator_inbox",
    capability: "harmony_operator_inbox",
    specialistCode: "coineasy_representative_inbox",
  },
  {
    stage: "recap",
    actor: "coineasy_recap",
    capability: "harmony_recap",
    specialistCode: "squid_recap",
  },
] as const;

type SpecialistContract = typeof SPECIALIST_CONTRACT[number];
type PreviewStage = SpecialistContract["stage"];
type PreviewActor = SpecialistContract["actor"];
type PreviewCapability = SpecialistContract["capability"];
type PreviewSpecialistCode = SpecialistContract["specialistCode"];

export type HarmonyDashboardConfig = {
  supabaseUrl: string;
  projectKey: string;
  authorizationKey: string;
  workspaceId: string;
  clientId: typeof PREVIEW_CLIENT_ID;
};

export type HarmonyDashboardRuntimeContext = {
  deploy?: {
    context?: string;
    published?: boolean;
  };
  site?: {
    name?: string;
    url?: string;
  };
};

export type HarmonyDashboardStage = {
  stage: PreviewStage;
  ordinal: number;
  actor: PreviewActor;
  capability: PreviewCapability;
  specialist_code: PreviewSpecialistCode;
  specialist_binding_sha256: string;
  operation_key_sha256: string;
  principal_id: string;
  producer_release_sha: string;
  config_sha256: string;
  receipt_sha256: string;
  input_sha256: string;
  output_sha256: string;
  recorded_at: string;
  verdict: null | "passed";
};

export type HarmonyDashboardRecap = {
  schema_version: "harmony-dashboard-recap@1";
  receipt_sha256: string;
  input_sha256: string;
  output_sha256: string;
  actual_cost_microusd: 0;
  stage_receipt_count: 5;
  operator_decision_observed: false;
  publication_count: 0;
  synthetic: true;
  automatic_publication: false;
};

export type HarmonyDashboardRound = {
  schema_version: "harmony-dashboard-round@2";
  round_id: string;
  plan_id: string;
  input_set_sha256: string;
  round_sha256: string;
  status: "operator_review_pending";
  headline_ko: string;
  summary_ko: string;
  stages: HarmonyDashboardStage[];
  recap: HarmonyDashboardRecap | null;
  automatic_publication: false;
};

export type HarmonyDashboardInboxItem = {
  schema_version: "harmony-dashboard-inbox@2";
  inbox_id: string;
  round_id: string;
  plan_id: string;
  status: "pending";
  scope_sha256: string;
  qa_receipt_id: string;
  qa_receipt_sha256: string;
  qa_output_sha256: string;
  round_sha256: string;
  recap_receipt_sha256: string | null;
  recap_output_sha256: string | null;
  headline_ko: string;
  summary_ko: string;
  created_at: string;
  operator_decision_recorded: false;
  automatic_publication: false;
};

export type HarmonyDashboard = {
  schema_version: "harmony-preview-dashboard@2";
  workspace_id: string;
  client_id: typeof PREVIEW_CLIENT_ID;
  observed_at: string;
  counts: {
    signals: number;
    connector_receipts: number;
    rounds: number;
    plans: number;
    stage_receipts: number;
    pending_operator_inbox: number;
  };
  latest_round: HarmonyDashboardRound | null;
  operator_inbox: HarmonyDashboardInboxItem[];
  trust: {
    environment: "preview";
    client_scope_verified: true;
    portable_trust: false;
  };
  flags: {
    read_only: true;
    external_calls: false;
    provider_calls: false;
    publication_calls: false;
    automatic_publication: false;
  };
};

export class HarmonyDashboardError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "HarmonyDashboardError";
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const sortedExpected = [...expected].sort();
  return actual.length === sortedExpected.length
    && actual.every((key, index) => key === sortedExpected[index]);
}

function uuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

function hash(value: unknown): value is string {
  return typeof value === "string" && HASH_PATTERN.test(value);
}

function count(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0 && Number(value) <= 1_000_000;
}

function timestamp(value: unknown): value is string {
  if (
    typeof value !== "string"
    || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value)
  ) return false;
  return Number.isFinite(Date.parse(value));
}

function safeText(
  value: unknown,
  maximumLength: number,
  singleLine: boolean,
): value is string {
  return typeof value === "string"
    && value === value.trim()
    && value.length > 0
    && value.length <= maximumLength
    && !CONTROL_PATTERN.test(value)
    && (!singleLine || !/[\r\n\t]/.test(value))
    && !SECRET_PATTERN.test(value);
}

function parseJwtPart(value: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(Buffer.from(value, "base64url").toString("utf8"));
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function jwtPayload(value: string): Record<string, unknown> | null {
  if (!JWT_PATTERN.test(value)) return null;
  const [headerPart, payloadPart] = value.split(".");
  const header = parseJwtPart(headerPart);
  const payload = parseJwtPart(payloadPart);
  if (
    !header
    || !payload
    || typeof header.alg !== "string"
    || header.alg.toLowerCase() === "none"
  ) return null;
  return payload;
}

function projectSupabaseUrl(value: string): { url: string; projectRef: string } | null {
  try {
    const url = new URL(value);
    const match = url.hostname.toLowerCase().match(PROJECT_HOST_PATTERN);
    if (
      url.protocol !== "https:"
      || !match
      || url.username
      || url.password
      || (url.pathname !== "" && url.pathname !== "/")
      || url.search
      || url.hash
    ) return null;
    return { url: url.origin, projectRef: match[1] };
  } catch {
    return null;
  }
}

function projectKeyIsPublishable(value: string): boolean {
  if (/^sb_publishable_[A-Za-z0-9_-]{20,2048}$/.test(value)) return true;
  const payload = jwtPayload(value);
  return payload?.role === "anon";
}

function scopedDashboardClaimsMatch(
  value: string,
  workspaceId: string,
  projectRef: string,
  nowSeconds: number,
): boolean {
  const payload = jwtPayload(value);
  const issuedAt = Number(payload?.iat);
  const expiresAt = Number(payload?.exp);
  return Boolean(
    payload
    && payload.iss === "supabase"
    && payload.aud === "authenticated"
    && payload.role === "coineasy_harmony_dashboard"
    && payload.workspace_id === workspaceId
    && payload.client_id === PREVIEW_CLIENT_ID
    && payload.environment === "preview"
    && payload.ref === projectRef
    && Number.isSafeInteger(issuedAt)
    && issuedAt > 0
    && issuedAt <= nowSeconds + 60
    && Number.isSafeInteger(expiresAt)
    && expiresAt > nowSeconds + 60
    && expiresAt - issuedAt >= 1
    && expiresAt - issuedAt <= 2_678_400
    && payload.automatic_publication === false
    && payload.max_cost_microusd === 0
    && payload.max_external_actions === 0,
  );
}

export function harmonyDashboardPreviewEnabled(
  getEnv: (name: string) => string | undefined,
): boolean {
  return getEnv("HARMONY_DASHBOARD_PREVIEW_ENABLED") === "true";
}

function httpsOrigin(value: string | undefined): string | null {
  try {
    const url = new URL((value || "").trim());
    if (
      url.protocol !== "https:"
      || url.username
      || url.password
      || url.port
      || (url.pathname !== "" && url.pathname !== "/")
      || url.search
      || url.hash
    ) return null;
    return url.origin;
  } catch {
    return null;
  }
}

function httpsRequestOrigin(value: string): string | null {
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:"
      || url.username
      || url.password
      || url.port
    ) return null;
    return url.origin;
  } catch {
    return null;
  }
}

function netlifyPreviewHostMatches(
  previewOrigin: string,
  siteName: string | undefined,
): boolean {
  const previewHost = new URL(previewOrigin).hostname.toLowerCase();
  const normalizedSiteName = (siteName || "").trim().toLowerCase();
  if (!/^[a-z0-9-]{1,63}$/.test(normalizedSiteName)) return false;
  const escapedSiteName = normalizedSiteName.replace(
    /[.*+?^${}()|[\]\\]/g,
    "\\$&",
  );
  return new RegExp(
    "^deploy-preview-[1-9][0-9]*--"
      + escapedSiteName
      + "\\.netlify\\.app$",
  ).test(previewHost);
}

export function harmonyDashboardPreviewOrigin(
  requestUrl: string,
  context: HarmonyDashboardRuntimeContext,
): string | null {
  if (
    context.deploy?.context !== "deploy-preview"
    || context.deploy.published !== false
  ) return null;
  const previewOrigin = httpsRequestOrigin(requestUrl);
  const productionOrigin = httpsOrigin(context.site?.url);
  if (
    !previewOrigin
    || !productionOrigin
    || previewOrigin === productionOrigin
    || !netlifyPreviewHostMatches(previewOrigin, context.site?.name)
  ) return null;
  return previewOrigin;
}

export function harmonyDashboardPreviewCommitMatches(
  getEnv: (name: string) => string | undefined,
  buildReleaseSha: string | null = currentStudioReleaseSha(),
): boolean {
  const actual = (buildReleaseSha || "").trim().toLowerCase();
  const expected = (
    getEnv("HARMONY_DASHBOARD_EXPECTED_COMMIT_SHA") || ""
  ).trim().toLowerCase();
  return GIT_SHA_PATTERN.test(actual)
    && GIT_SHA_PATTERN.test(expected)
    && actual === expected;
}

export function harmonyDashboardConfig(
  getEnv: (name: string) => string | undefined,
  nowSeconds = Math.floor(Date.now() / 1_000),
): HarmonyDashboardConfig | null {
  const parsedUrl = projectSupabaseUrl((getEnv("SUPABASE_URL") || "").trim());
  const projectKey = (getEnv("SUPABASE_PUBLISHABLE_KEY") || "").trim();
  const authorizationKey = (getEnv("SUPABASE_HARMONY_DASHBOARD_KEY") || "").trim();
  const workspaceId = (getEnv("CONTENT_STUDIO_WORKSPACE_ID") || "").trim().toLowerCase();
  const serviceRole = (getEnv("SUPABASE_SERVICE_ROLE_KEY") || "").trim();
  if (
    !parsedUrl
    || !uuid(workspaceId)
    || !projectKeyIsPublishable(projectKey)
    || !scopedDashboardClaimsMatch(
      authorizationKey,
      workspaceId,
      parsedUrl.projectRef,
      nowSeconds,
    )
    || authorizationKey === projectKey
    || (serviceRole && (serviceRole === projectKey || serviceRole === authorizationKey))
  ) return null;
  return {
    supabaseUrl: parsedUrl.url,
    projectKey,
    authorizationKey,
    workspaceId,
    clientId: PREVIEW_CLIENT_ID,
  };
}

function normalizeStage(
  raw: unknown,
  expected: SpecialistContract,
  expectedOrdinal: number,
): HarmonyDashboardStage | null {
  if (!isRecord(raw) || !exactKeys(raw, [
    "stage",
    "ordinal",
    "actor",
    "capability",
    "specialist_code",
    "specialist_binding_sha256",
    "operation_key_sha256",
    "principal_id",
    "producer_release_sha",
    "config_sha256",
    "receipt_sha256",
    "input_sha256",
    "output_sha256",
    "recorded_at",
    "verdict",
  ])) return null;
  const expectedVerdict = expected.stage === "independent_qa"
    ? "passed"
    : null;
  if (
    raw.stage !== expected.stage
    || raw.ordinal !== expectedOrdinal
    || raw.actor !== expected.actor
    || raw.capability !== expected.capability
    || raw.specialist_code !== expected.specialistCode
    || !hash(raw.specialist_binding_sha256)
    || !hash(raw.operation_key_sha256)
    || !uuid(raw.principal_id)
    || typeof raw.producer_release_sha !== "string"
    || !GIT_SHA_PATTERN.test(raw.producer_release_sha)
    || !hash(raw.config_sha256)
    || !hash(raw.receipt_sha256)
    || !hash(raw.input_sha256)
    || !hash(raw.output_sha256)
    || !timestamp(raw.recorded_at)
    || raw.verdict !== expectedVerdict
  ) return null;
  return raw as HarmonyDashboardStage;
}

function stageOperationKeySha256(
  config: Pick<HarmonyDashboardConfig, "workspaceId" | "clientId">,
  planId: string,
  stage: HarmonyDashboardStage,
): string {
  const canonical = JSON.stringify({
    client_id: config.clientId,
    input_sha256: stage.input_sha256,
    output_sha256: stage.output_sha256,
    plan_id: planId,
    schema_version: "harmony-stage-operation@1",
    specialist_binding_sha256: stage.specialist_binding_sha256,
    stage: stage.stage,
    workspace_id: config.workspaceId,
  });
  return createHash("sha256").update(canonical, "utf8").digest("hex");
}

function normalizeRound(
  raw: unknown,
  config: Pick<HarmonyDashboardConfig, "workspaceId" | "clientId">,
): HarmonyDashboardRound | null {
  if (!isRecord(raw) || !exactKeys(raw, [
    "schema_version",
    "round_id",
    "plan_id",
    "input_set_sha256",
    "round_sha256",
    "status",
    "headline_ko",
    "summary_ko",
    "stages",
    "recap",
    "automatic_publication",
  ])) return null;
  if (
    raw.schema_version !== "harmony-dashboard-round@2"
    || !uuid(raw.round_id)
    || !uuid(raw.plan_id)
    || !hash(raw.input_set_sha256)
    || !hash(raw.round_sha256)
    || raw.status !== "operator_review_pending"
    || !safeText(raw.headline_ko, 160, true)
    || !safeText(raw.summary_ko, 600, false)
    || !Array.isArray(raw.stages)
    || ![4, SPECIALIST_CONTRACT.length].includes(raw.stages.length)
    || raw.automatic_publication !== false
  ) return null;
  const stages = raw.stages.map((stage, index) =>
    normalizeStage(stage, SPECIALIST_CONTRACT[index], index + 1)
  );
  if (stages.some((stage) => stage === null)) return null;
  const validStages = stages as HarmonyDashboardStage[];
  if (
    new Set(validStages.map((stage) => stage.principal_id)).size
      !== validStages.length
    || validStages.some((stage) =>
      stage.operation_key_sha256
        !== stageOperationKeySha256(config, raw.plan_id as string, stage)
    )
    || validStages[0].input_sha256 !== raw.input_set_sha256
    || validStages.some((stage, index) =>
      index > 0 && stage.input_sha256 !== validStages[index - 1].output_sha256
    )
  ) return null;
  const recap = raw.recap;
  const recapStage = validStages[4];
  if (validStages.length === 4) {
    if (recap !== null) return null;
    return { ...raw, stages: validStages, recap: null } as HarmonyDashboardRound;
  }
  if (!recapStage || !isRecord(recap) || !exactKeys(recap, [
    "schema_version",
    "receipt_sha256",
    "input_sha256",
    "output_sha256",
    "actual_cost_microusd",
    "stage_receipt_count",
    "operator_decision_observed",
    "publication_count",
    "synthetic",
    "automatic_publication",
  ]) || recap.schema_version !== "harmony-dashboard-recap@1"
    || recap.receipt_sha256 !== recapStage.receipt_sha256
    || recap.input_sha256 !== recapStage.input_sha256
    || recap.output_sha256 !== recapStage.output_sha256
    || recap.actual_cost_microusd !== 0
    || recap.stage_receipt_count !== 5
    || recap.operator_decision_observed !== false
    || recap.publication_count !== 0
    || recap.synthetic !== true
    || recap.automatic_publication !== false
  ) return null;
  return { ...raw, stages: validStages, recap } as HarmonyDashboardRound;
}

function normalizeInboxItem(raw: unknown): HarmonyDashboardInboxItem | null {
  if (!isRecord(raw) || !exactKeys(raw, [
    "schema_version",
    "inbox_id",
    "round_id",
    "plan_id",
    "status",
    "scope_sha256",
    "qa_receipt_id",
    "qa_receipt_sha256",
    "qa_output_sha256",
    "round_sha256",
    "recap_receipt_sha256",
    "recap_output_sha256",
    "headline_ko",
    "summary_ko",
    "created_at",
    "operator_decision_recorded",
    "automatic_publication",
  ])) return null;
  if (
    raw.schema_version !== "harmony-dashboard-inbox@2"
    || !uuid(raw.inbox_id)
    || !uuid(raw.round_id)
    || !uuid(raw.plan_id)
    || raw.status !== "pending"
    || !hash(raw.scope_sha256)
    || !uuid(raw.qa_receipt_id)
    || !hash(raw.qa_receipt_sha256)
    || !hash(raw.qa_output_sha256)
    || !hash(raw.round_sha256)
    || !(
      (raw.recap_receipt_sha256 === null && raw.recap_output_sha256 === null)
      || (hash(raw.recap_receipt_sha256) && hash(raw.recap_output_sha256))
    )
    || !safeText(raw.headline_ko, 160, true)
    || !safeText(raw.summary_ko, 600, false)
    || !timestamp(raw.created_at)
    || raw.operator_decision_recorded !== false
    || raw.automatic_publication !== false
  ) return null;
  return raw as HarmonyDashboardInboxItem;
}

export function normalizeHarmonyDashboard(
  raw: unknown,
  config: Pick<HarmonyDashboardConfig, "workspaceId" | "clientId">,
): HarmonyDashboard {
  if (!isRecord(raw) || !exactKeys(raw, [
    "schema_version",
    "workspace_id",
    "client_id",
    "observed_at",
    "counts",
    "latest_round",
    "operator_inbox",
    "trust",
    "flags",
  ])) throw new HarmonyDashboardError("harmony_dashboard_invalid_response");
  const counts = raw.counts;
  const trust = raw.trust;
  const flags = raw.flags;
  if (
    raw.schema_version !== "harmony-preview-dashboard@2"
    || raw.workspace_id !== config.workspaceId
    || raw.client_id !== config.clientId
    || !timestamp(raw.observed_at)
    || !isRecord(counts)
    || !exactKeys(counts, [
      "signals",
      "connector_receipts",
      "rounds",
      "plans",
      "stage_receipts",
      "pending_operator_inbox",
    ])
    || !Object.values(counts).every(count)
    || !Array.isArray(raw.operator_inbox)
    || raw.operator_inbox.length > 25
    || !isRecord(trust)
    || !exactKeys(trust, [
      "environment",
      "client_scope_verified",
      "portable_trust",
    ])
    || trust.environment !== "preview"
    || trust.client_scope_verified !== true
    || trust.portable_trust !== false
    || !isRecord(flags)
    || !exactKeys(flags, [
      "read_only",
      "external_calls",
      "provider_calls",
      "publication_calls",
      "automatic_publication",
    ])
    || flags.read_only !== true
    || flags.external_calls !== false
    || flags.provider_calls !== false
    || flags.publication_calls !== false
    || flags.automatic_publication !== false
  ) throw new HarmonyDashboardError("harmony_dashboard_invalid_response");

  const latestRound = raw.latest_round === null
    ? null
    : normalizeRound(raw.latest_round, config);
  const inbox = raw.operator_inbox.map(normalizeInboxItem);
  const validInbox = inbox.filter(
    (item): item is HarmonyDashboardInboxItem => item !== null,
  );
  const inboxIds = validInbox.map((item) => item.inbox_id);
  const roundInboxPairs = validInbox.map(
    (item) => item.round_id + ":" + item.inbox_id,
  );
  const inboxOrderedNewestFirst = validInbox.every((item, index) =>
    index === 0
    || Date.parse(validInbox[index - 1].created_at) >= Date.parse(item.created_at)
  );
  if (
    (raw.latest_round !== null && latestRound === null)
    || inbox.some((item) => item === null)
    || new Set(inboxIds).size !== inboxIds.length
    || new Set(roundInboxPairs).size !== roundInboxPairs.length
    || !inboxOrderedNewestFirst
    || counts.pending_operator_inbox < validInbox.length
    || (
      counts.pending_operator_inbox <= 25
      && counts.pending_operator_inbox !== validInbox.length
    )
    || (counts.pending_operator_inbox > 25 && validInbox.length !== 25)
    || (latestRound === null && (counts.rounds !== 0 || validInbox.length !== 0))
    || (latestRound !== null && (
      counts.signals < 4
      || counts.connector_receipts < 4
      || counts.rounds < 1
      || counts.plans < 1
      || counts.stage_receipts < 4
    ))
  ) throw new HarmonyDashboardError("harmony_dashboard_invalid_response");

  if (latestRound) {
    const qaStage = latestRound.stages[2];
    const operatorStage = latestRound.stages[3];
    const recapStage = latestRound.stages[4];
    const matchingLatest = validInbox.filter(
      (item) => item.round_id === latestRound.round_id,
    );
    if (
      matchingLatest.length !== 1
      || matchingLatest[0].plan_id !== latestRound.plan_id
      || matchingLatest[0].qa_receipt_sha256 !== qaStage.receipt_sha256
      || matchingLatest[0].qa_output_sha256 !== qaStage.output_sha256
      || matchingLatest[0].scope_sha256 !== operatorStage.output_sha256
      || matchingLatest[0].round_sha256 !== latestRound.round_sha256
      || (
        recapStage
          ? (
            matchingLatest[0].recap_receipt_sha256 !== recapStage.receipt_sha256
            || matchingLatest[0].recap_output_sha256 !== recapStage.output_sha256
          )
          : (
            matchingLatest[0].recap_receipt_sha256 !== null
            || matchingLatest[0].recap_output_sha256 !== null
          )
      )
      || matchingLatest[0].headline_ko !== latestRound.headline_ko
      || matchingLatest[0].summary_ko !== latestRound.summary_ko
    ) {
      throw new HarmonyDashboardError("harmony_dashboard_invalid_response");
    }
  }
  return {
    ...raw,
    latest_round: latestRound,
    operator_inbox: validInbox,
  } as HarmonyDashboard;
}

export async function getHarmonyDashboard(
  config: HarmonyDashboardConfig,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(8_000),
): Promise<HarmonyDashboard> {
  const endpoint = new URL(
    config.supabaseUrl + "/rest/v1/rpc/" + RPC_NAME,
  );
  endpoint.searchParams.set("target_workspace_id", config.workspaceId);
  endpoint.searchParams.set("target_client_id", config.clientId);
  let response: Response;
  try {
    response = await fetcher(endpoint, {
      method: "GET",
      headers: {
        Accept: "application/json",
        apikey: config.projectKey,
        Authorization: "Bearer " + config.authorizationKey,
      },
      signal,
    });
  } catch {
    throw new HarmonyDashboardError("harmony_dashboard_unavailable");
  }
  if (!response.ok) {
    throw new HarmonyDashboardError("harmony_dashboard_unavailable");
  }
  let rawText: string;
  try {
    rawText = await response.text();
  } catch {
    throw new HarmonyDashboardError("harmony_dashboard_unavailable");
  }
  if (
    Buffer.byteLength(rawText, "utf8") < 2
    || Buffer.byteLength(rawText, "utf8") > MAX_RESPONSE_BYTES
  ) throw new HarmonyDashboardError("harmony_dashboard_invalid_response");
  let raw: unknown;
  try {
    raw = JSON.parse(rawText);
  } catch {
    throw new HarmonyDashboardError("harmony_dashboard_invalid_response");
  }
  return normalizeHarmonyDashboard(raw, config);
}
