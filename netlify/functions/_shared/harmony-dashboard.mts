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

const STAGE_ORDER = [
  "plan",
  "private_content",
  "independent_qa",
  "operator_inbox",
  "recap",
] as const;

type PreviewStage = typeof STAGE_ORDER[number];

export type HarmonyDashboardConfig = {
  supabaseUrl: string;
  projectKey: string;
  authorizationKey: string;
  workspaceId: string;
  clientId: typeof PREVIEW_CLIENT_ID;
};

export type HarmonyDashboardStage = {
  stage: PreviewStage;
  ordinal: number;
  receipt_sha256: string;
  input_sha256: string;
  output_sha256: string;
  recorded_at: string;
  verdict: null | "passed";
};

export type HarmonyDashboardRound = {
  schema_version: "harmony-dashboard-round@1";
  round_id: string;
  plan_id: string;
  input_set_sha256: string;
  round_sha256: string;
  status: "operator_review_pending";
  headline_ko: string;
  summary_ko: string;
  stages: HarmonyDashboardStage[];
  automatic_publication: false;
};

export type HarmonyDashboardInboxItem = {
  schema_version: "harmony-dashboard-inbox@1";
  inbox_id: string;
  round_id: string;
  plan_id: string;
  status: "pending";
  scope_sha256: string;
  qa_receipt_id: string;
  qa_receipt_sha256: string;
  qa_output_sha256: string;
  created_at: string;
  automatic_publication: false;
};

export type HarmonyDashboard = {
  schema_version: "harmony-preview-dashboard@1";
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
      || (url.pathname !== "" && url.pathname !== "/")
      || url.search
      || url.hash
    ) return null;
    return url.origin;
  } catch {
    return null;
  }
}

export function harmonyDashboardPreviewOrigin(
  getEnv: (name: string) => string | undefined,
): string | null {
  if (getEnv("CONTEXT") !== "deploy-preview") return null;
  const previewOrigin = httpsOrigin(getEnv("DEPLOY_PRIME_URL"));
  const productionOrigin = httpsOrigin(getEnv("URL"));
  if (!previewOrigin || !productionOrigin || previewOrigin === productionOrigin) return null;
  return previewOrigin;
}

export function harmonyDashboardPreviewCommitMatches(
  getEnv: (name: string) => string | undefined,
): boolean {
  const actual = (getEnv("COMMIT_REF") || "").trim().toLowerCase();
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
  expectedStage: PreviewStage,
  expectedOrdinal: number,
): HarmonyDashboardStage | null {
  if (!isRecord(raw) || !exactKeys(raw, [
    "stage",
    "ordinal",
    "receipt_sha256",
    "input_sha256",
    "output_sha256",
    "recorded_at",
    "verdict",
  ])) return null;
  if (
    raw.stage !== expectedStage
    || raw.ordinal !== expectedOrdinal
    || !hash(raw.receipt_sha256)
    || !hash(raw.input_sha256)
    || !hash(raw.output_sha256)
    || !timestamp(raw.recorded_at)
    || (raw.verdict !== null && raw.verdict !== "passed")
  ) return null;
  return raw as HarmonyDashboardStage;
}

function normalizeRound(raw: unknown): HarmonyDashboardRound | null {
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
    "automatic_publication",
  ])) return null;
  if (
    raw.schema_version !== "harmony-dashboard-round@1"
    || !uuid(raw.round_id)
    || !uuid(raw.plan_id)
    || !hash(raw.input_set_sha256)
    || !hash(raw.round_sha256)
    || raw.status !== "operator_review_pending"
    || !safeText(raw.headline_ko, 160, true)
    || !safeText(raw.summary_ko, 600, false)
    || !Array.isArray(raw.stages)
    || raw.stages.length !== STAGE_ORDER.length
    || raw.automatic_publication !== false
  ) return null;
  const stages = raw.stages.map((stage, index) =>
    normalizeStage(stage, STAGE_ORDER[index], index + 1)
  );
  if (stages.some((stage) => stage === null)) return null;
  if (stages[2]?.verdict !== "passed") return null;
  return { ...raw, stages } as HarmonyDashboardRound;
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
    "created_at",
    "automatic_publication",
  ])) return null;
  if (
    raw.schema_version !== "harmony-dashboard-inbox@1"
    || !uuid(raw.inbox_id)
    || !uuid(raw.round_id)
    || !uuid(raw.plan_id)
    || raw.status !== "pending"
    || !hash(raw.scope_sha256)
    || !uuid(raw.qa_receipt_id)
    || !hash(raw.qa_receipt_sha256)
    || !hash(raw.qa_output_sha256)
    || !timestamp(raw.created_at)
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
    raw.schema_version !== "harmony-preview-dashboard@1"
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
    : normalizeRound(raw.latest_round);
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
      || counts.stage_receipts < 5
    ))
  ) throw new HarmonyDashboardError("harmony_dashboard_invalid_response");

  if (latestRound) {
    const qaStage = latestRound.stages[2];
    const matchingLatest = validInbox.find(
      (item) => item.round_id === latestRound.round_id,
    );
    if (matchingLatest && (
      matchingLatest.plan_id !== latestRound.plan_id
      || matchingLatest.qa_receipt_sha256 !== qaStage.receipt_sha256
      || matchingLatest.qa_output_sha256 !== qaStage.output_sha256
    )) {
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
