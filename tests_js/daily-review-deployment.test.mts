import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("daily review CI exercises the existing courier without delivery authority", () => {
  const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
  const job = read(".github/workflows/ci.yml").split("  daily-review-image:\n")[1]?.split("\n  typefully-daily-image:")[0];
  assert.ok(job);
  assert.match(job, /contents: read/);
  assert.match(job, /persist-credentials: false/);
  assert.match(job, /timeout-minutes: 10/);
  assert.match(job, /--file Dockerfile\.content-ops-review/);
  assert.match(job, /--network none --read-only --cap-drop ALL/);
  assert.match(job, /"enabled":false,"claimed":0/);
  assert.match(job, /run\(validate_only=True, environ=env\)/);
  assert.match(job, /'CONTENT_OPS_REVIEW_ENABLED': 'false'/);
  assert.match(job, /'production_proof': False/);
  assert.doesNotMatch(job, /secrets\.|docker (?:push|login)|netlify deploy|railway (?:up|deploy)/);
  assert.doesNotMatch(read(".railway/railway.ts"), /Dockerfile\.content-ops-review|CONTENT_OPS_GATEWAY_ENABLED/);
  const postgres = read(".github/workflows/ci.yml").split("\n  postgres:\n")[1];
  assert.match(postgres, /create function auth\.role\(\)/);
  assert.match(postgres, /-f supabase\/tests\/content_ops_review_outbox\.sql/);
});
