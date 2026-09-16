import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("confirmation CI builds its own image and invokes the isolated real-container harness", () => {
  const workflow = read(".github/workflows/ci.yml");
  const job = workflow.split("  confirmation-image:\n")[1]?.split("\n  automation-image:")[0];
  assert.ok(job, "dedicated confirmation image job must exist");
  assert.match(job, /runs-on: ubuntu-latest/);
  assert.match(job, /timeout-minutes: 10/);
  assert.match(job, /contents: read/);
  assert.match(job, /persist-credentials: false/);
  assert.match(job, /--file Dockerfile\.content-ops-confirmation/);
  assert.match(job, /--build-arg RAILWAY_GIT_COMMIT_SHA=a{40}\n/);
  assert.match(job, /--build-arg RAILWAY_GIT_COMMIT_SHA=invalid/);
  assert.match(job, /grep -F 'test -n'/);
  assert.match(job, /grep -F 'exit code: 1'/);
  assert.match(job, /python scripts\/verify_confirmation_image_local\.py\n\s+--local-only --image coineasy-confirmation-ci/);
  assert.match(job, /synthetic SHA, not deployment proof/);
  assert.doesNotMatch(job, /secrets\.|docker (?:push|login)|railway (?:up|deploy)|netlify deploy|pull_request_target/);
  const harness = read("scripts/verify_confirmation_image_local.py");
  for (const flag of ["--pull=never", "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges"]) {
    assert.ok(harness.includes(flag), flag);
  }
  assert.match(harness, /response\.code == 503/);
  assert.match(harness, /'wrong_sha'/);
  assert.match(harness, /'missing_sha'/);
});

test("confirmation deployment stays an inert, unscheduled validate-only template", () => {
  const config = JSON.parse(read("ops/content-ops/confirmation-railway.json"));
  assert.deepEqual(config.build, { builder: "DOCKERFILE", dockerfilePath: "Dockerfile.content-ops-confirmation" });
  assert.deepEqual(config.deploy, {
    preDeployCommand: "python -m scripts.run_content_ops_confirmation --validate-only",
    startCommand: "python -m scripts.run_content_ops_confirmation",
    restartPolicyType: "NEVER",
  });
  assert.doesNotMatch(read(".railway/railway.ts"), /Dockerfile\.content-ops-confirmation|CONTENT_OPS_CONFIRMATION_ENABLED/);
  assert.doesNotMatch(read("api/server.py"), /confirmation_runtime|confirmation_relay_bridge/);
});

test("confirmation image is non-root, default-OFF and copies only relay code", () => {
  const docker = read("Dockerfile.content-ops-confirmation");
  assert.match(docker, /ARG RAILWAY_GIT_COMMIT_SHA/);
  assert.match(docker, /\$\{#RAILWAY_GIT_COMMIT_SHA\}.*-eq 40/);
  assert.ok(docker.includes("'^[a-f0-9]{40}$'"));
  assert.match(docker, /> \/app\/content-ops-confirmation-build-sha/);
  assert.match(docker, /CONTENT_OPS_CONFIRMATION_ENABLED=false CONTENT_OPS_REVIEW_ENABLED=false/);
  assert.match(docker, /USER 10001:10001/);
  assert.match(docker, /httpx==0\.28\.1 uvicorn==0\.31\.0/);
  assert.deepEqual(docker.split("\n").filter((line) => line.startsWith("COPY ")), [
    "COPY core/__init__.py ./core/__init__.py",
    "COPY core/content_ops ./core/content_ops",
    "COPY scripts/run_content_ops_confirmation.py ./scripts/run_content_ops_confirmation.py",
  ]);
  assert.doesNotMatch(docker, /COPY \. |COPY \.env|SUPABASE|TYPEFULLY|OPENAI|DATABASE_URL/);
  assert.doesNotMatch(docker, /mkdir.*\/data|chmod.*\/data|chown.*\/data/);
});

test("review deployment template is inert, default-OFF and has no live schedule", () => {
  const config = JSON.parse(read("ops/content-ops/railway.json"));
  assert.deepEqual(config.build, { builder: "DOCKERFILE", dockerfilePath: "Dockerfile.content-ops-review" });
  assert.deepEqual(config.deploy, {
    preDeployCommand: "python -m scripts.run_content_ops_review --validate-only",
    startCommand: "python -m scripts.run_content_ops_review",
    restartPolicyType: "NEVER",
  });
  assert.equal(existsSync(new URL("../railway.content-ops-review.json", import.meta.url)), false);
  assert.doesNotMatch(read(".railway/railway.ts"), /Dockerfile\.content-ops-review|CONTENT_OPS_GATEWAY_ENABLED/);
});

test("courier image copies only its code and uses an actual GitHub build stamp", () => {
  const docker = read("Dockerfile.content-ops-review");
  assert.match(docker, /ARG RAILWAY_GIT_COMMIT_SHA/);
  assert.match(docker, /> \/app\/content-ops-build-sha/);
  assert.match(docker, /CONTENT_OPS_REVIEW_ENABLED=false/);
  const copies = docker.split("\n").filter((line) => line.startsWith("COPY "));
  assert.deepEqual(copies, [
    "COPY core/__init__.py ./core/__init__.py",
    "COPY core/content_ops ./core/content_ops",
    "COPY scripts/run_content_ops_review.py ./scripts/run_content_ops_review.py",
  ]);
  assert.match(read("core/content_ops/worker.py"), /Path\("\/app\/content-ops-build-sha"\)/);
  assert.doesNotMatch(docker, /COPY \. |COPY \.env|ANTHROPIC|SUPABASE|TYPEFULLY/);
});
