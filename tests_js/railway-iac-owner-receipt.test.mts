import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const validator = new URL(
  '../scripts/validate_railway_iac_owner_receipt.mjs',
  import.meta.url,
).pathname;

const project = {
  id: '43f15c45-4a5c-4cf9-9400-e462cac46bb1',
  name: 'noble-illumination',
};
const environment = {
  id: '5bf47282-1982-4930-95ad-29230ec0429b',
  name: 'production',
  projectId: project.id,
};
const web = {
  environmentId: environment.id,
  serviceId: '80168b5d-54f5-4684-ab32-d5f3c4f8e483',
  serviceName: 'coineasy-content-engine',
  railwayConfigFile: null,
  builder: 'RAILPACK',
  dockerfilePath: 'Dockerfile',
  healthcheckPath: '/health',
  healthcheckTimeout: 100,
  restartPolicyType: 'ON_FAILURE',
  restartPolicyMaxRetries: 3,
  startCommand: "sh -c 'uvicorn api.server:app --host 0.0.0.0 --port $PORT'",
};
const managed = {
  environmentId: environment.id,
  serviceId: 'b1ab4f39-982f-4c33-9402-b0bc843aac2f',
  serviceName: 'coineasy-managed-inspect',
  railwayConfigFile: null,
  builder: 'RAILPACK',
  dockerfilePath: 'Dockerfile.managed-inspect',
  healthcheckPath: null,
  healthcheckTimeout: null,
  restartPolicyType: 'ON_FAILURE',
  restartPolicyMaxRetries: 10,
  startCommand: null,
};

function receiptFixture(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      project,
      environment,
      web,
      managed,
    },
    ...overrides,
  };
}

function runReceiptGate(receipt: unknown, args: string[] = []) {
  return spawnSync(process.execPath, [validator, ...args], {
    encoding: 'utf8',
    input: JSON.stringify(receipt),
  });
}

test('Railway owner receipt proves scoped owner-settings convergence with bounded output', () => {
  const accepted = runReceiptGate(receiptFixture());
  assert.equal(accepted.status, 0, accepted.stderr);
  const output = JSON.parse(accepted.stdout);
  assert.deepEqual(output, {
    ok: true,
    state: 'owner_settings_converged',
    scope: 'one_time_adoption_settings',
    authority: 'railway.serviceInstance',
    action: 'stop_no_apply',
    projectId: project.id,
    environmentId: environment.id,
    services: [web, managed].map((service) => ({
      serviceName: service.serviceName,
      serviceId: service.serviceId,
      railwayConfigFile: null,
      dockerfilePath: service.dockerfilePath,
      restartPolicyType: service.restartPolicyType,
      restartPolicyMaxRetries: service.restartPolicyMaxRetries,
      verifiedSettings: true,
    })),
  });
  assert.doesNotMatch(
    accepted.stdout,
    /startCommand|healthcheck|builder|variable|secret|token|resolvedFileConfig/i,
  );
});

test('Railway owner receipt query is read-only and allowlisted', () => {
  const query = readFileSync(
    new URL('../scripts/railway_iac_owner_receipt.graphql', import.meta.url),
    'utf8',
  );
  assert.match(query, /^query RailwayIacOwnerReceipt\(/);
  assert.equal((query.match(/serviceInstance\(/g) ?? []).length, 2);
  assert.doesNotMatch(
    query,
    /\b(?:mutation|variables|logs|resolvedFileConfig|meta|deployments?)\b/i,
  );
  for (const field of [
    'environmentId',
    'serviceId',
    'serviceName',
    'railwayConfigFile',
    'builder',
    'dockerfilePath',
    'healthcheckPath',
    'healthcheckTimeout',
    'restartPolicyType',
    'restartPolicyMaxRetries',
    'startCommand',
  ]) assert.match(query, new RegExp(`\\b${field}\\b`));
});

test('Railway owner receipt rejects target, setting, scope, and shape drift', () => {
  const unsafeReceipts = [
    receiptFixture({ errors: [{ message: 'denied' }] }),
    receiptFixture({ data: { project: { ...project, id: 'wrong' }, environment, web, managed } }),
    receiptFixture({ data: { project, environment: { ...environment, name: 'staging' }, web, managed } }),
    receiptFixture({ data: { project, environment, web: { ...web, serviceId: managed.serviceId }, managed } }),
    receiptFixture({ data: { project, environment, web: { ...web, railwayConfigFile: 'railway.json' }, managed } }),
    receiptFixture({ data: { project, environment, web: { ...web, builder: 'DOCKERFILE' }, managed } }),
    receiptFixture({ data: { project, environment, web: { ...web, dockerfilePath: null }, managed } }),
    receiptFixture({ data: { project, environment, web: { ...web, healthcheckPath: null }, managed } }),
    receiptFixture({ data: { project, environment, web: { ...web, restartPolicyType: 'ALWAYS' }, managed } }),
    receiptFixture({ data: { project, environment, web, managed: { ...managed, restartPolicyMaxRetries: 0 } } }),
    receiptFixture({ data: { project, environment, web, managed: { ...managed, variables: [] } } }),
    receiptFixture({ data: { project, environment, web } }),
  ];
  for (const unsafe of unsafeReceipts) {
    const rejected = runReceiptGate(unsafe);
    assert.notEqual(rejected.status, 0);
    assert.equal(rejected.stdout, '');
    assert.equal(rejected.stderr, 'railway_iac_owner_receipt_rejected\n');
  }

  const rejectedArgument = runReceiptGate(receiptFixture(), ['--allow-drift']);
  assert.notEqual(rejectedArgument.status, 0);
  assert.equal(rejectedArgument.stdout, '');
  assert.equal(rejectedArgument.stderr, 'railway_iac_owner_receipt_rejected\n');
});

test('Railway owner receipt rejects oversized input', () => {
  const rejected = spawnSync(process.execPath, [validator], {
    encoding: 'utf8',
    input: JSON.stringify({ data: 'x'.repeat(1024 * 1024) }),
  });
  assert.notEqual(rejected.status, 0);
  assert.equal(rejected.stdout, '');
  assert.equal(rejected.stderr, 'railway_iac_owner_receipt_rejected\n');
});
