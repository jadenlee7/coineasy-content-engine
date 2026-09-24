import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("private Typefully daily Railway config stays isolated and default OFF", () => {
  const config = JSON.parse(read("railway.typefully-daily.json"));
  assert.deepEqual(config, {
    $schema: "https://railway.com/railway.schema.json",
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "Dockerfile.typefully-daily",
      watchPatterns: [
        "/.dockerignore",
        "/Dockerfile.typefully-daily",
        "/Dockerfile.typefully-daily.dockerignore",
        "/core/__init__.py",
        "/core/publications/__init__.py",
        "/core/publications/models.py",
        "/core/publications/handoff.py",
        "/core/publications/settings.py",
        "/core/publications/typefully_daily.py",
        "/core/publications/typefully_draft_once.py",
        "/core/publications/typefully_media_once.py",
        "/core/publications/typefully_media_upload.py",
        "/core/publications/typefully_readback.py",
        "/scripts/run_typefully_daily.py",
        "/railway.typefully-daily.json",
      ],
    },
    deploy: {
      preDeployCommand: "python -m scripts.run_typefully_daily --validate-only",
      startCommand: "python -m scripts.run_typefully_daily",
      cronSchedule: "*/15 * * * *",
      restartPolicyType: "NEVER",
    },
  });
  const docker = read("Dockerfile.typefully-daily");
  assert.match(docker, /^ARG RAILWAY_GIT_COMMIT_SHA$/m);
  assert.match(docker, /TYPEFULLY_DAILY_ENABLED=false/);
  assert.match(docker, /USER nobody/);
  assert.match(docker, /typefully-daily-build-sha/);
  assert.doesNotMatch(docker, /TELEGRAM_|TYPEFULLY_DAILY_ENABLED=true/);
  assert.deepEqual(docker.split("\n").filter((line) => line.startsWith("COPY ")), [
    "COPY core/__init__.py ./core/__init__.py",
    "COPY core/publications/__init__.py ./core/publications/__init__.py",
    "COPY core/publications/models.py ./core/publications/models.py",
    "COPY core/publications/handoff.py ./core/publications/handoff.py",
    "COPY core/publications/settings.py ./core/publications/settings.py",
    "COPY core/publications/typefully_daily.py ./core/publications/typefully_daily.py",
    "COPY core/publications/typefully_draft_once.py ./core/publications/typefully_draft_once.py",
    "COPY core/publications/typefully_media_once.py ./core/publications/typefully_media_once.py",
    "COPY core/publications/typefully_media_upload.py ./core/publications/typefully_media_upload.py",
    "COPY core/publications/typefully_readback.py ./core/publications/typefully_readback.py",
    "COPY scripts/run_typefully_daily.py ./scripts/run_typefully_daily.py",
  ]);
  assert.deepEqual(read("Dockerfile.typefully-daily.dockerignore").trimEnd().split("\n"), [
    "**",
    "!Dockerfile.typefully-daily",
    "!core/",
    "!core/__init__.py",
    "!core/publications/",
    "!core/publications/__init__.py",
    "!core/publications/models.py",
    "!core/publications/handoff.py",
    "!core/publications/settings.py",
    "!core/publications/typefully_daily.py",
    "!core/publications/typefully_draft_once.py",
    "!core/publications/typefully_media_once.py",
    "!core/publications/typefully_media_upload.py",
    "!core/publications/typefully_readback.py",
    "!scripts/",
    "!scripts/run_typefully_daily.py",
  ]);
  assert.doesNotMatch(read(".railway/railway.ts"), /Dockerfile\.typefully-daily|TYPEFULLY_DAILY_ENABLED/);
});

test("private Typefully image CI proves zero-I/O OFF and checks build SHA", () => {
  const job = read(".github/workflows/ci.yml")
    .split("  typefully-daily-image:\n")[1]?.split("\n  automation-image:")[0];
  assert.ok(job);
  assert.match(job, /persist-credentials: false/);
  assert.match(job, /--file Dockerfile\.typefully-daily/);
  assert.match(job, /--network none --read-only --cap-drop ALL/);
  assert.match(job, /--validate-only/);
  assert.match(job, /RAILWAY_GIT_COMMIT_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/);
  assert.doesNotMatch(job, /secrets\.|docker (?:push|login)|netlify deploy|railway (?:up|deploy)/);
});
