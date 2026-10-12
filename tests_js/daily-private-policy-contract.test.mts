import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { QUALITY_BANNER_POLICY } from "../netlify/functions/_shared/quality-banner.mts";

const read = (path: string) => readFileSync(new URL("../" + path, import.meta.url), "utf8");

test("actual Python renderer, Netlify catalog persistence and SQL daily admission share one policy", () => {
  const python = read("core/automation/higgsfield_banner.py");
  const marker = python.match(/^POLICY = ("[^"\r\n]+")$/m);
  assert.ok(marker, "actual renderer POLICY literal missing");
  const rendererPolicy = JSON.parse(marker[1]);
  assert.equal(QUALITY_BANNER_POLICY, rendererPolicy);
  assert.notEqual(rendererPolicy, "official-reference-sunburst-max@1", "designer-feedback policy must not regress");
  const persistence = read("netlify/functions/news-card.mts");
  assert.match(persistence, /promptVersion:\s*qualityMode\s*\?\s*QUALITY_BANNER_POLICY\s*:/);
  assert.match(persistence, /quality_banner:\s*resultSpec\.quality_banner/);
  const sql = read("supabase/proposals/content_ops_daily_private_scope.sql");
  const prompt = sql.match(/v\.prompt_version\s*=\s*'([^']+)'/);
  const metadata = sql.match(/v\.generation_meta\s*->\s*'quality_banner'\s*->>\s*'policy'\s*=\s*'([^']+)'/);
  assert.ok(prompt && metadata, "both persisted policy gates required");
  assert.equal(prompt[1], rendererPolicy, "SQL prompt policy drift blocks every real banner");
  assert.equal(metadata[1], rendererPolicy, "SQL metadata policy drift blocks every real banner");
  const fixture = read("supabase/tests/content_ops_daily_private_scope.sql");
  assert.match(fixture, /:'quality_banner_policy'/);
  assert.match(fixture, /obsolete_policy_claimed_or_mutated/);
  const harness = read("scripts/verify_daily_private_scope_local.mjs");
  assert.match(harness, /core\/automation\/higgsfield_banner\.py/);
  assert.match(harness, /quality_banner_policy=\s*['"]?\+policy/);
});
