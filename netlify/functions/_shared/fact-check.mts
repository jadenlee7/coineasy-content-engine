import { createHash } from "node:crypto";
import { canonicalXStatusUrl, type ResolvedSource } from "./source-content.mts";

export type FactCheckContentKind = "daily_news" | "article" | "tutorial";
export type FactCheckStatus = "pass" | "review" | "blocked";

export type FactCheckCheck = {
  id: "source_evidence" | "output_claims";
  status: FactCheckStatus;
  label: string;
  detail: string;
  metrics: Record<string, boolean | number | string | null>;
};

export type FactCheckReport = {
  schema_version: "1.0";
  policy_version: "double-fact-check@1";
  content_kind: FactCheckContentKind;
  status: FactCheckStatus;
  human_review_required: true;
  input_sha256: string;
  output_sha256: string;
  checks: [FactCheckCheck, FactCheckCheck];
};

export type FactCheckSource = Pick<ResolvedSource, "content" | "url" | "mode" | "xProvenance">;

export type FactCheckInput = {
  contentKind: FactCheckContentKind;
  source: FactCheckSource;
  publicText: unknown;
  channelCopy?: unknown;
  artifactSha256?: string[];
  brandQa?: { status?: unknown; score?: unknown } | null;
};

const SHA256_PATTERN = /^[a-f0-9]{64}$/;

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

/**
 * Validate a persisted report before replaying it across the generation API.
 * Approval remains stricter: blocked reports are structurally valid evidence,
 * but they can never satisfy the publication gate.
 */
export function validatedFactCheckReport(
  value: unknown,
  expectedKind: FactCheckContentKind,
): FactCheckReport | null {
  const report = record(value);
  if (!report) return null;
  const checks = Array.isArray(report.checks) ? report.checks : [];
  if (checks.length !== 2) return null;
  const expectedIds = ["source_evidence", "output_claims"] as const;
  const statuses: FactCheckStatus[] = [];
  for (let index = 0; index < expectedIds.length; index += 1) {
    const check = record(checks[index]);
    if (
      !check
      || check.id !== expectedIds[index]
      || !["pass", "review", "blocked"].includes(String(check.status))
      || typeof check.label !== "string"
      || !check.label.trim()
      || typeof check.detail !== "string"
      || !check.detail.trim()
      || !record(check.metrics)
    ) return null;
    statuses.push(check.status as FactCheckStatus);
  }
  const aggregate = maxStatus(...statuses);
  if (
    report.schema_version !== "1.0"
    || report.policy_version !== "double-fact-check@1"
    || report.content_kind !== expectedKind
    || report.status !== aggregate
    || report.human_review_required !== true
    || typeof report.input_sha256 !== "string"
    || !SHA256_PATTERN.test(report.input_sha256)
    || typeof report.output_sha256 !== "string"
    || !SHA256_PATTERN.test(report.output_sha256)
  ) return null;
  return report as FactCheckReport;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.normalize("NFC").trim() : "";
}

function stableValue(value: unknown): unknown {
  if (typeof value === "string") return value.normalize("NFC").trim();
  if (typeof value === "number" || typeof value === "boolean" || value === null) return value;
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value as Record<string, unknown>).sort().map((key) => [
      key,
      stableValue((value as Record<string, unknown>)[key]),
    ]));
  }
  return "";
}

function sha256(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(stableValue(value)), "utf8").digest("hex");
}

/** Collect only public string fields in a stable order for output fingerprinting. */
export function factCheckPublicText(value: unknown): string {
  if (typeof value === "string") return text(value);
  if (Array.isArray(value)) return value.map(factCheckPublicText).filter(Boolean).join("\n");
  if (!value || typeof value !== "object") return "";
  return Object.keys(value as Record<string, unknown>).sort()
    .map((key) => factCheckPublicText((value as Record<string, unknown>)[key]))
    .filter(Boolean)
    .join("\n");
}

function numbers(value: string): string[] {
  return value.match(/\d+(?:[,.]\d+)?/g)?.map((item) => item.replace(/,/g, "")) || [];
}

function terms(value: string): Set<string> {
  return new Set((value.toLowerCase().match(/[\p{L}\p{N}_-]{2,}/gu) || []));
}

function maxStatus(...statuses: FactCheckStatus[]): FactCheckStatus {
  return statuses.includes("blocked") ? "blocked" : statuses.includes("review") ? "review" : "pass";
}

function sourceEvidenceCheck(source: FactCheckSource): FactCheckCheck {
  const sourceText = text(source.content);
  const xUrl = canonicalXStatusUrl(text(source.url));
  const provenance = source.xProvenance;
  const statusId = xUrl?.match(/\/status\/(\d+)/)?.[1] || "";
  const xProvenanceMatches = Boolean(
    statusId
    && provenance
    && provenance.requestedStatusId === statusId
    && provenance.payloadStatusId === statusId
    && text(provenance.authorHandle)
    && text(provenance.authorUserId),
  );
  if (sourceText.length < 10) {
    return {
      id: "source_evidence",
      status: "blocked",
      label: "Source evidence",
      detail: "No usable source text was available for the deterministic baseline; no semantic conclusion is made.",
      metrics: { source_characters: sourceText.length, source_mode: source.mode, x_provenance_verified: false },
    };
  }
  if (source.mode === "provided") {
    return {
      id: "source_evidence",
      status: "review",
      label: "Source evidence",
      detail: "Provided text is retained as evidence, but requires human verification; this check does not verify semantic truth.",
      metrics: { source_characters: sourceText.length, source_mode: source.mode, x_provenance_verified: xProvenanceMatches },
    };
  }
  return {
    id: "source_evidence",
    status: xProvenanceMatches ? "pass" : "review",
    label: "Source evidence",
    detail: xProvenanceMatches
      ? "Resolved X text has matching machine-recorded post and author identifiers; human verification is still required and semantic truth is not certified."
      : "Resolved X text has no matching machine-recorded post provenance; human verification is required and semantic truth is not certified.",
    metrics: { source_characters: sourceText.length, source_mode: source.mode, x_provenance_verified: xProvenanceMatches },
  };
}

function outputClaimsCheck(
  sourceText: string,
  publicText: string,
  channelCopy: unknown,
  artifactSha256: string[],
  artifactIntegrityValid: boolean,
  brandQa: FactCheckInput["brandQa"],
): FactCheckCheck {
  const copyText = factCheckPublicText(channelCopy);
  const sourceNumbers = numbers(sourceText);
  const outputNumbers = numbers(`${publicText}\n${copyText}`);
  const sourceNumberSet = new Set(sourceNumbers);
  const unmatchedNumbers = outputNumbers.filter((value) => !sourceNumberSet.has(value));
  const unmatchedNumberSample = [...new Set(unmatchedNumbers)].slice(0, 10).join(", ");
  const sourceTerms = terms(sourceText);
  const outputTerms = terms(`${publicText}\n${copyText}`);
  const sharedTerms = [...outputTerms].filter((term) => sourceTerms.has(term)).length;
  if (!artifactIntegrityValid) {
    return {
      id: "output_claims",
      status: "blocked",
      label: "Output claim anchors",
      detail: "One or more public artifact fingerprints were malformed, so the output cannot be bound to this report.",
      metrics: { public_characters: publicText.length, channel_copy_characters: copyText.length, artifact_count: artifactSha256.length, artifact_integrity_valid: false, source_number_count: sourceNumbers.length, output_number_count: outputNumbers.length, unmatched_output_number_count: unmatchedNumbers.length, unmatched_output_numbers: unmatchedNumberSample, shared_term_count: sharedTerms, brand_qa_status: null, brand_qa_score: null },
    };
  }
  if (!publicText && !copyText) {
    return {
      id: "output_claims",
      status: "review",
      label: "Output claim anchors",
      detail: "No public output text was available for anchoring. Human verification is required; this is not a semantic truth determination.",
      metrics: { public_characters: 0, channel_copy_characters: 0, artifact_count: artifactSha256.length, artifact_integrity_valid: true, source_number_count: sourceNumbers.length, output_number_count: 0, unmatched_output_number_count: 0, unmatched_output_numbers: "", shared_term_count: 0, brand_qa_status: null, brand_qa_score: null },
    };
  }
  const brandStatus = brandQa?.status === "pass" || brandQa?.status === "review" ? brandQa.status : null;
  const brandScore = typeof brandQa?.score === "number" && Number.isFinite(brandQa.score)
    ? brandQa.score
    : null;
  return {
    id: "output_claims",
    status: unmatchedNumbers.length ? "review" : "pass",
    label: "Output claim anchors",
    detail: unmatchedNumbers.length
      ? "Output contains numeric tokens absent from the source-text snapshot. This mechanical signal requires human review and does not decide semantic truth."
      : "Output fingerprint and lexical/numeric anchor metrics were recorded. They are mechanical signals only and do not establish semantic truth.",
    metrics: {
      public_characters: publicText.length,
      channel_copy_characters: copyText.length,
      artifact_count: artifactSha256.length,
      artifact_integrity_valid: true,
      source_number_count: sourceNumbers.length,
      output_number_count: outputNumbers.length,
      unmatched_output_number_count: unmatchedNumbers.length,
      unmatched_output_numbers: unmatchedNumberSample,
      shared_term_count: sharedTerms,
      brand_qa_status: brandStatus,
      brand_qa_score: brandScore,
    },
  };
}

/**
 * Build a deterministic, immutable-at-persistence baseline. It records evidence
 * and mechanical anchors only; it deliberately makes no semantic truth claim.
 */
export function evaluateFactCheck(input: FactCheckInput): FactCheckReport {
  const sourceText = text(input.source.content);
  const publicText = factCheckPublicText(input.publicText);
  const sourceSnapshot = {
    content: sourceText,
    url: text(input.source.url),
    mode: input.source.mode,
    x_provenance: input.source.xProvenance
      ? {
        requested_status_id: text(input.source.xProvenance.requestedStatusId),
        payload_status_id: text(input.source.xProvenance.payloadStatusId),
        author_handle: text(input.source.xProvenance.authorHandle),
        author_user_id: text(input.source.xProvenance.authorUserId),
      }
      : null,
  };
  const rawArtifactSha256 = Array.isArray(input.artifactSha256) ? input.artifactSha256 : [];
  const artifactIntegrityValid = rawArtifactSha256.every((value) => (
    typeof value === "string" && SHA256_PATTERN.test(value)
  ));
  const artifactSha256 = rawArtifactSha256.filter((value): value is string => (
    typeof value === "string" && SHA256_PATTERN.test(value)
  ));
  const outputSnapshot = {
    public_text: publicText,
    channel_copy: input.channelCopy ?? null,
    artifact_sha256: artifactSha256,
  };
  const sourceCheck = sourceEvidenceCheck(input.source);
  const outputCheck = outputClaimsCheck(
    sourceText,
    publicText,
    input.channelCopy,
    artifactSha256,
    artifactIntegrityValid,
    input.brandQa,
  );
  return {
    schema_version: "1.0",
    policy_version: "double-fact-check@1",
    content_kind: input.contentKind,
    status: maxStatus(sourceCheck.status, outputCheck.status),
    human_review_required: true,
    input_sha256: sha256(sourceSnapshot),
    output_sha256: sha256(outputSnapshot),
    checks: [sourceCheck, outputCheck],
  };
}
