import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import test from 'node:test';

import railwayProgram, { partial } from '../.railway/railway.ts';
import { createRailwayContext } from 'railway/iac';

const region = 'asia-southeast1-eqsg3a';
const source = {
  type: 'github',
  repo: 'jadenlee7/coineasy-content-engine',
  branch: 'main',
};

const webVariables = [
  'ANTHROPIC_API_KEY',
  'API_SECRET',
  'CONTENT_STUDIO_WORKSPACE_ID',
  'EASYFARM_CONTENT_SIGNALS_TOKEN',
  'EASYFARM_CONTENT_SIGNALS_URL',
  'FIGMA_TOKEN',
  'GROK_QA_RELAY_TOKEN',
  'PUBLICATION_WORKER_TOKEN',
  'SUPABASE_SERVICE_ROLE_KEY',
  'SUPABASE_URL',
  'TELEGRAM_BOT_TOKEN_SQUID',
  'TELEGRAM_BOT_TOKEN_YELLOW',
  'TELEGRAM_CHANNEL_SQUID',
  'TELEGRAM_CHANNEL_YELLOW',
  'TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN',
  'TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID',
  'TELEGRAM_PUBLICATION_ALLOWED_CLIENTS',
  'TELEGRAM_PUBLICATION_ENABLED',
  'TELEGRAM_PUBLICATION_LEASE_SECONDS',
  'TELEGRAM_PUBLICATION_MAX_CLAIMS',
  'TELEGRAM_PUBLICATION_RECOVERY_LIMIT',
  'TELEGRAM_PUBLICATION_RELEASE_SHA',
  'TELEGRAM_REVIEW_BOT_TOKEN',
  'TELEGRAM_REVIEW_CHAT_ID',
  'TYPEFULLY_API_KEY',
  'TYPEFULLY_SOCIAL_SET_ID',
  'X_BEARER_TOKEN',
];

const managedVariables = [
  'MANAGED_INSPECT_ENABLED',
  'MANAGED_INSPECT_SOURCE_SHA',
  'RAILWAY_DOCKERFILE_PATH',
];

const managedWatchPatterns = [
  '/Dockerfile.managed-inspect',
  '/Dockerfile.managed-inspect.dockerignore',
  '/tools/managed-telegram-inspect/auth.mjs',
  '/tools/managed-telegram-inspect/browser-guard.mjs',
  '/tools/managed-telegram-inspect/config.mjs',
  '/tools/managed-telegram-inspect/server.mjs',
  '/scripts/lib/telegram-resolution-inspect.mjs',
  '/railway.managed-inspect.json',
];

const legacyManifestHashes: Record<string, string> = {
  'railway.batch-dispatcher.json': 'ff8f71eb5bd232c8976463cf6d2e7c70e1e89e464ef3122ca313ec8e098e99c3',
  'railway.buzz-delivery.json': '401985a4509f7297b0561737f78c42c64b94b1251b26e5756ff5e2a5f6ae60ab',
  'railway.buzz-review.json': '542d2f83bc0d6d35abc717d97439b8b67124e846de02bb1bbed029bcfa017058',
  'railway.grok-qa.json': '7bea14fa9a681b1898a33eca0c5caf4cb177c7d0de0c590c2fae887cc987f3e0',
  'railway.managed-inspect.json': 'a2ec55ef1d3f45e271c3d6f393ba6c7b77b6f9a2a62ca1bf45aba436f4fd578c',
  'railway.official-x-cron.json': '6f2c9fa13b0e35fd7fec3f139f0adeba818903bd415be6d40cc652fefeed33de',
  'railway.telegram-publication-worker.json': '4cb5a085a58ef9d37fef5a124a54d9812162414326c999aecc9bb2d5f7014714',
};

function assertPreservedVariables(actual: Record<string, unknown>, expected: string[]) {
  assert.deepEqual(Object.keys(actual).sort(), [...expected].sort());
  for (const name of expected) assert.deepEqual(actual[name], { type: 'preserve' });
}

test('Railway IaC has no auto-discovered root config and owns only two services', async () => {
  assert.equal(existsSync(new URL('../railway.json', import.meta.url)), false);
  assert.equal(existsSync(new URL('../railway.toml', import.meta.url)), false);
  const workflows = readdirSync(new URL('../.github/workflows/', import.meta.url))
    .filter((name) => /\.ya?ml$/.test(name))
    .map((name) => readFileSync(
      new URL(`../.github/workflows/${name}`, import.meta.url), 'utf8',
    )).join('\n');
  assert.doesNotMatch(workflows, /railway\s+config\s+apply/,
    'CI must never apply Railway infrastructure');
  const manifests = readdirSync(new URL('..', import.meta.url))
    .filter((name) => /^railway\..+\.json$/.test(name)).sort();
  assert.deepEqual(manifests, Object.keys(legacyManifestHashes).sort());
  for (const name of manifests) {
    const sha256 = createHash('sha256')
      .update(readFileSync(new URL(`../${name}`, import.meta.url))).digest('hex');
    assert.equal(sha256, legacyManifestHashes[name], `${name} changed outside its migration scope`);
  }

  assert.equal(partial, 'coineasy-content-engine-services');
  const targetContext = {
    command: 'plan',
    projectId: '43f15c45-4a5c-4cf9-9400-e462cac46bb1',
    projectName: 'noble-illumination',
    environmentId: '5bf47282-1982-4930-95ad-29230ec0429b',
    environment: 'production',
  };
  const definition = await railwayProgram(createRailwayContext(targetContext));
  assert.throws(
    () => railwayProgram(createRailwayContext({ ...targetContext, projectName: 'wrong-project' })),
    /railway_iac_target_mismatch/,
  );
  assert.throws(
    () => railwayProgram(createRailwayContext({ ...targetContext, environment: 'staging' })),
    /railway_iac_target_mismatch/,
  );
  assert.throws(
    () => railwayProgram(createRailwayContext({ ...targetContext, command: 'apply' })),
    /railway_iac_target_mismatch/,
  );
  assert.equal(definition.name, 'noble-illumination');
  assert.deepEqual(
    definition.resources.map((resource) => resource.address),
    ['service.coineasy-content-engine', 'service.coineasy-managed-inspect'],
  );

  const [web, managed] = definition.resources;
  assert.equal('configFile' in web, false);
  assert.equal('configFile' in managed, false);

  assert.deepEqual(web.source, source);
  assert.deepEqual(web.build, {
    buildEnvironment: 'V3',
    builder: 'DOCKERFILE',
    dockerfilePath: 'Dockerfile',
  });
  assert.deepEqual(web.deploy, {
    ipv6EgressEnabled: false,
    restartPolicyType: 'ON_FAILURE',
    restartPolicyMaxRetries: 3,
    runtime: 'V2',
    useLegacyStacker: false,
    startCommand: "sh -c 'uvicorn api.server:app --host 0.0.0.0 --port $PORT'",
    healthcheckPath: '/health',
    healthcheckTimeout: 100,
    multiRegionConfig: { [region]: { numReplicas: 1 } },
  });
  assertPreservedVariables(web.variables, webVariables);

  assert.deepEqual(managed.source, source);
  assert.deepEqual(managed.build, {
    buildEnvironment: 'V3',
    builder: 'DOCKERFILE',
    dockerfilePath: 'Dockerfile.managed-inspect',
    watchPatterns: managedWatchPatterns,
  });
  assert.deepEqual(managed.deploy, {
    ipv6EgressEnabled: false,
    restartPolicyType: 'ON_FAILURE',
    restartPolicyMaxRetries: 10,
    runtime: 'V2',
    useLegacyStacker: false,
    multiRegionConfig: { [region]: { numReplicas: 1 } },
  });
  assert.deepEqual(managed.networking, {
    serviceDomains: {
      'coineasy-managed-inspect-production.up.railway.app': { port: 8080 },
    },
  });
  assertPreservedVariables(managed.variables, managedVariables);
});

const legacyMigrationChanges = [
  {
    summary: 'Update coineasy-content-engine build.builder',
    severity: 'safe',
    kind: 'resource.update',
    details: ['build.builder (null → "DOCKERFILE")'],
  },
  {
    summary: 'Update coineasy-content-engine deploy.healthcheckPath, deploy.healthcheckTimeout, deploy.restartPolicyMaxRetries and 2 more',
    severity: 'safe',
    kind: 'resource.update',
    details: [
      'deploy.healthcheckPath (null → "/health")',
      'deploy.healthcheckTimeout (null → 100)',
      'deploy.restartPolicyMaxRetries (null → 3)',
      'deploy.restartPolicyType (null → "ON_FAILURE")',
      'deploy.startCommand (null → "sh -c \'uvicorn api.server:app --host 0.0.0.0 --port $PORT\'")',
    ],
  },
  {
    summary: 'Update coineasy-managed-inspect deploy.restartPolicyMaxRetries, deploy.restartPolicyType',
    severity: 'safe',
    kind: 'resource.update',
    details: [
      'deploy.restartPolicyMaxRetries (null → 10)',
      'deploy.restartPolicyType (null → "ON_FAILURE")',
    ],
  },
];

function planFixture(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    command: 'plan',
    currentEnvironment: {
      projectId: '43f15c45-4a5c-4cf9-9400-e462cac46bb1',
      projectName: 'noble-illumination',
      environmentId: '5bf47282-1982-4930-95ad-29230ec0429b',
      environmentName: 'production',
      configEtag: 'a'.repeat(64),
    },
    changeSet: {
      changes: [],
    },
    diagnostics: [],
    currentGraph: { secret: 'current-graph-secret' },
    desiredGraph: {
      secret: 'desired-graph-secret',
      resources: [
        { address: 'service.coineasy-content-engine' },
        { address: 'service.coineasy-managed-inspect' },
      ],
    },
    applyResult: null,
    deploymentId: null,
    stagedPatchId: null,
    ...overrides,
  };
}

function runPlanGate(plan: Record<string, unknown>) {
  return spawnSync(process.execPath, [
    new URL('../scripts/validate_railway_iac_plan.mjs', import.meta.url).pathname,
  ], {
    encoding: 'utf8',
    input: JSON.stringify(plan),
  });
}

test('Railway convergence gate emits only bounded metadata and rejects any change', () => {
  const accepted = runPlanGate(planFixture());
  assert.equal(accepted.status, 0, accepted.stderr);
  const output = JSON.parse(accepted.stdout);
  assert.deepEqual(output, {
    ok: true,
    state: 'converged',
    action: 'stop_no_apply',
    projectId: '43f15c45-4a5c-4cf9-9400-e462cac46bb1',
    project: 'noble-illumination',
    environmentId: '5bf47282-1982-4930-95ad-29230ec0429b',
    environment: 'production',
    configEtag: 'a'.repeat(64),
    desiredAddresses: [
      'service.coineasy-content-engine',
      'service.coineasy-managed-inspect',
    ],
    changeCount: 0,
    changes: [],
    diagnostics: [],
  });
  assert.doesNotMatch(accepted.stdout, /secret|currentGraph|desiredGraph|details/i);

  const unsafePlans = [
    planFixture({ errors: [] }),
    planFixture({ extensions: {} }),
    planFixture({ changeSet: { changes: legacyMigrationChanges } }),
    planFixture({
      desiredGraph: { resources: [{ address: 'service.coineasy-content-engine' }] },
    }),
    planFixture({
      changeSet: {
        changes: [{
          kind: 'variable.delete',
          severity: 'destructive',
          summary: 'Delete variable coineasy-content-engine.API_SECRET',
        }],
      },
    }),
    planFixture({ diagnostics: [{ severity: 'error', path: 'service' }] }),
    planFixture({ applyResult: { ok: true } }),
    planFixture({
      changeSet: {
        changes: [{
          kind: 'resource.update',
          severity: 'safe',
          summary: 'Update unrelated-service build.builder',
        }],
      },
    }),
    planFixture({
      changeSet: {
        changes: [{
          summary: 'Update coineasy-content-engine build.builder',
          severity: 'safe',
          kind: 'resource.update',
          details: ['build.builder (null → "NIXPACKS")'],
        }],
      },
    }),
  ];
  for (const unsafe of unsafePlans) {
    const rejected = runPlanGate(unsafe);
    assert.notEqual(rejected.status, 0);
    assert.equal(rejected.stdout, '');
    assert.equal(rejected.stderr, 'railway_iac_plan_rejected\n');
  }

  const rejectedArgument = spawnSync(process.execPath, [
    new URL('../scripts/validate_railway_iac_plan.mjs', import.meta.url).pathname,
    '--expect-migration',
  ], {
    encoding: 'utf8',
    input: JSON.stringify(planFixture()),
  });
  assert.notEqual(rejectedArgument.status, 0);
  assert.equal(rejectedArgument.stdout, '');
  assert.equal(rejectedArgument.stderr, 'railway_iac_plan_rejected\n');
});

test('Railway convergence gate rejects oversized input', () => {
  const rejected = spawnSync(process.execPath, [
    new URL('../scripts/validate_railway_iac_plan.mjs', import.meta.url).pathname,
  ], {
    encoding: 'utf8',
    input: JSON.stringify({ data: 'x'.repeat(5 * 1024 * 1024) }),
  });
  assert.notEqual(rejected.status, 0);
  assert.equal(rejected.stdout, '');
  assert.equal(rejected.stderr, 'railway_iac_plan_rejected\n');
});
