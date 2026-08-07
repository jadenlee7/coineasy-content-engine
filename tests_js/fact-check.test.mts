import assert from "node:assert/strict";
import test from "node:test";

import {
  evaluateFactCheck,
  factCheckPublicText,
  validatedFactCheckReport,
} from "../netlify/functions/_shared/fact-check.mts";

test("fact-check baseline is deterministic and marks pasted evidence for human verification", () => {
  const input = {
    contentKind: "article" as const,
    source: {
      content: "The source announces 12 supported routes for the product update.",
      url: "https://example.com/update",
      mode: "provided" as const,
    },
    publicText: { title: "Product update", body: ["12 routes are described."] },
    channelCopy: { x: "12 routes", telegram: "Product update" },
    brandQa: { status: "pass", score: 100 },
  };
  const first = evaluateFactCheck(input);
  const second = evaluateFactCheck(input);

  assert.deepEqual(first, second);
  assert.equal(first.schema_version, "1.0");
  assert.equal(first.policy_version, "double-fact-check@1");
  assert.equal(first.status, "review");
  assert.equal(first.human_review_required, true);
  assert.match(first.input_sha256, /^[a-f0-9]{64}$/);
  assert.match(first.output_sha256, /^[a-f0-9]{64}$/);
  assert.deepEqual(first.checks.map((check) => check.id), ["source_evidence", "output_claims"]);
  assert.equal(first.checks[0].status, "review");
  assert.match(first.checks[0].detail, /requires human verification/i);
  assert.equal(first.checks[1].status, "pass");
  assert.equal(first.checks[1].metrics.brand_qa_score, 100);
});

test("fact-check records matching X provenance without claiming semantic truth", () => {
  const report = evaluateFactCheck({
    contentKind: "daily_news",
    source: {
      content: "Official update confirms 2 new routes.",
      url: "https://x.com/example/status/12345",
      mode: "x_import",
      xProvenance: {
        requestedStatusId: "12345",
        payloadStatusId: "12345",
        authorHandle: "example",
        authorUserId: "98765",
        mediaUrls: [],
      },
    },
    publicText: "Official update: 2 new routes.",
    channelCopy: { telegram: "2 new routes", x: "2 new routes" },
  });

  assert.equal(report.status, "pass");
  assert.equal(report.checks[0].status, "pass");
  assert.equal(report.checks[0].metrics.x_provenance_verified, true);
  assert.match(report.checks[1].detail, /do not establish semantic truth/i);
});

test("fact-check flags mechanical output anchor gaps and reserves blocked for missing evidence", () => {
  const numericGap = evaluateFactCheck({
    contentKind: "tutorial",
    source: { content: "The lesson has 3 steps.", url: "", mode: "provided" },
    publicText: "The lesson has 4 steps.",
    channelCopy: {},
  });
  assert.equal(numericGap.status, "review");
  assert.equal(numericGap.checks[1].status, "review");
  assert.equal(numericGap.checks[1].metrics.unmatched_output_number_count, 1);
  assert.equal(numericGap.checks[1].metrics.unmatched_output_numbers, "4");

  const missingEvidence = evaluateFactCheck({
    contentKind: "article",
    source: { content: "", url: "", mode: "provided" },
    publicText: "Draft text",
  });
  assert.equal(missingEvidence.status, "blocked");
  assert.equal(missingEvidence.checks[0].status, "blocked");
  assert.equal(factCheckPublicText({ z: "last", a: ["first", "second"] }), "first\nsecond\nlast");
});

test("fact-check output fingerprint binds immutable public artifacts", () => {
  const base = {
    contentKind: "daily_news" as const,
    source: {
      content: "공식 원문에 근거한 동일한 텍스트입니다.",
      url: "https://example.com/source",
      mode: "provided" as const,
    },
    publicText: { headline: "동일한 공개 문구" },
    artifactSha256: ["a".repeat(64)],
  };
  const first = evaluateFactCheck(base);
  const second = evaluateFactCheck({ ...base, artifactSha256: ["b".repeat(64)] });
  assert.notEqual(first.output_sha256, second.output_sha256);
  assert.equal(first.checks[1].metrics.artifact_count, 1);
});

test("persisted fact-check validation rejects partial, mismatched, and inconsistent reports", () => {
  const report = evaluateFactCheck({
    contentKind: "article",
    source: { content: "A sufficiently detailed official source statement.", url: "", mode: "provided" },
    publicText: "A reviewable output statement.",
  });
  assert.deepEqual(validatedFactCheckReport(report, "article"), report);
  assert.equal(validatedFactCheckReport(report, "tutorial"), null);
  assert.equal(validatedFactCheckReport({ ...report, status: "pass" }, "article"), null);
  assert.equal(validatedFactCheckReport({
    ...report,
    checks: [{ id: "source_evidence" }, { id: "output_claims" }],
  }, "article"), null);
});

test("malformed artifact fingerprints block the report instead of being silently omitted", () => {
  const report = evaluateFactCheck({
    contentKind: "daily_news",
    source: { content: "Official source text long enough for review.", url: "", mode: "provided" },
    publicText: "Review output",
    artifactSha256: ["not-a-sha256"],
  });
  assert.equal(report.status, "blocked");
  assert.equal(report.checks[1].status, "blocked");
  assert.equal(report.checks[1].metrics.artifact_integrity_valid, false);
});
