import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (name: string) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8');
const docker = read('Dockerfile.same-day-refresh');
const profile = JSON.parse(read('railway.same-day-refresh.json'));
const workflow = read('.github/workflows/ci.yml');
const job = workflow.match(/^  same-day-refresh-off-image:\n([\s\S]*?)(?=^  automation-image:)/m)?.[1];

test('refresh image requires only a canonical native build SHA and a root-owned read-only stamp', () => {
  assert.deepEqual([...docker.matchAll(/^ARG (\w+)/gm)].map(match => match[1]), ['RAILWAY_GIT_COMMIT_SHA']);
  assert.match(docker, /os\.environ\.get\("RAILWAY_GIT_COMMIT_SHA", ""\)/);
  assert.match(docker, /re\.fullmatch\(r"\[a-f0-9\]\{40\}", sha\) is None/);
  assert.match(docker, /\/app\/same-day-refresh-build-sha/);
  assert.match(docker, /path\.write_text\(sha \+ "\\n", encoding="ascii"\); path\.chmod\(0o444\)/);
  assert.doesNotMatch(docker, /ENV[^\n]*RAILWAY_GIT_COMMIT_SHA|MANAGED_INSPECT_SOURCE_SHA|CONTENT_OPS_REVIEW_RELEASE_SHA/);
});

test('refresh package is non-root, defaults OFF, and packages no HTTP or other worker entrypoint', () => {
  assert.match(docker, /^FROM python:3\.12-slim$/m);
  assert.match(docker, /COPY requirements-automation\.txt/);
  assert.match(docker, /COPY core \.\/core/);
  assert.match(docker, /COPY scripts\/run_official_x_same_day_refresh\.py/);
  assert.match(docker, /OFFICIAL_X_SAME_DAY_REFRESH_ENABLED=false/);
  assert.match(docker, /PYTHONDONTWRITEBYTECODE=1/);
  assert.match(docker, /GPG_KEY=""/);
  assert.match(docker, /^USER 10001:10001$/m);
  assert.match(docker, /^CMD \["python", "-m", "scripts\.run_official_x_same_day_refresh", "--validate-runtime-only"\]$/m);
  assert.doesNotMatch(docker, /COPY \. \.|COPY scripts \.|COPY api|EXPOSE|uvicorn|run_official_x_daily|run_private_review_card_canary/);
});

test('Railway profile validates OFF only, never schedules/restarts or invents an autoDeploy config field', () => {
  const command = 'python -m scripts.run_official_x_same_day_refresh --validate-runtime-only';
  assert.deepEqual(profile.build, { builder: 'DOCKERFILE', dockerfilePath: 'Dockerfile.same-day-refresh' });
  assert.deepEqual(profile.deploy, { preDeployCommand: command, startCommand: command, restartPolicyType: 'NEVER' });
  assert.deepEqual(Object.keys(profile).sort(), ['$schema', 'build', 'deploy']);
  assert.doesNotMatch(JSON.stringify(profile), /cronSchedule|autoDeploy|healthcheckPath|PORT|queue-once|generate-once/);
});

test('isolated CI is read-only, uses synthetic inputs, and rejects invalid builds', () => {
  assert.ok(job);
  assert.match(job!, /contents: read/);
  assert.match(job!, /persist-credentials: false/);
  assert.match(job!, /--build-arg RAILWAY_GIT_COMMIT_SHA=a{40}/);
  assert.match(job!, /coineasy-same-day-refresh-missing-ci/);
  assert.match(job!, /--build-arg RAILWAY_GIT_COMMIT_SHA=malformed/);
  assert.doesNotMatch(job!, /\$\{\{\s*secrets\.|railway (?:up|deploy|variable)|netlify deploy|docker push|curl |wget /);
});

test('all runtime CI containers deny network/writes/capabilities and test exact OFF proofs', () => {
  assert.ok(job);
  for (const invocation of job!.matchAll(/docker run([\s\S]*?)(?=coineasy-same-day-refresh-ci)/g)) {
    assert.match(invocation[1], /--network none/);
    assert.match(invocation[1], /--read-only/);
    assert.match(invocation[1], /--cap-drop ALL/);
    assert.match(invocation[1], /--security-opt no-new-privileges/);
  }
  assert.match(job!, /os\.getuid\(\)==10001 and s\.st_uid==0/);
  assert.match(job!, /\.image_attestation == false and \.hosted_provenance_verified == false/);
  assert.match(job!, /\.image_stamp_matches == true and \.workspace_config_valid == true/);
  assert.match(job!, /reject "\$sha" "\$other" false/);
  assert.match(job!, /reject "\$other" "\$sha" false/);
  assert.match(job!, /reject "\$sha" "\$sha" true/);
  for (const key of ['OPENAI_API_KEY', 'TELEGRAM_REVIEW_BOT_TOKEN', 'TYPEFULLY_API_KEY',
    'AWS_ACCESS_KEY_ID', 'GOOGLE_APPLICATION_CREDENTIALS', 'PGSERVICEFILE']) assert.match(job!, new RegExp(key));
  assert.doesNotMatch(job!, /--env (?:PGHOST|DATABASE_URL|TELEGRAM_\w*CHAT_ID|TYPEFULLY_SOCIAL_SET_ID)=/);
});
