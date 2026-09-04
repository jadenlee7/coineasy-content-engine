#!/usr/bin/env node

import { isDeepStrictEqual } from 'node:util';

const MAX_RECEIPT_BYTES = 1024 * 1024;
const PROJECT = {
  id: '43f15c45-4a5c-4cf9-9400-e462cac46bb1',
  name: 'noble-illumination',
};
const ENVIRONMENT = {
  id: '5bf47282-1982-4930-95ad-29230ec0429b',
  name: 'production',
  projectId: PROJECT.id,
};
const SERVICE_KEYS = [
  'builder',
  'dockerfilePath',
  'environmentId',
  'healthcheckPath',
  'healthcheckTimeout',
  'railwayConfigFile',
  'restartPolicyMaxRetries',
  'restartPolicyType',
  'serviceId',
  'serviceName',
  'startCommand',
];
const EXPECTED = {
  web: {
    environmentId: ENVIRONMENT.id,
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
  },
  managed: {
    environmentId: ENVIRONMENT.id,
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
  },
};

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasExactKeys(value, keys) {
  return isObject(value)
    && isDeepStrictEqual(Object.keys(value).sort(), [...keys].sort());
}

function reject() {
  process.stderr.write('railway_iac_owner_receipt_rejected\n');
  process.exitCode = 1;
}

async function readBoundedStdin(maxBytes) {
  const chunks = [];
  let total = 0;
  for await (const chunk of process.stdin) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += bytes.length;
    if (total > maxBytes) throw new Error('bounded');
    chunks.push(bytes);
  }
  return Buffer.concat(chunks, total).toString('utf8');
}

try {
  if (process.argv.length !== 2) throw new Error('arguments');
  const raw = await readBoundedStdin(MAX_RECEIPT_BYTES);
  const receipt = JSON.parse(raw);
  if (!hasExactKeys(receipt, ['data'])) throw new Error('root');
  const data = receipt.data;
  if (!hasExactKeys(data, ['environment', 'managed', 'project', 'web'])) {
    throw new Error('data');
  }
  if (!hasExactKeys(data.project, ['id', 'name'])
      || !isDeepStrictEqual(data.project, PROJECT)) {
    throw new Error('project');
  }
  if (!hasExactKeys(data.environment, ['id', 'name', 'projectId'])
      || !isDeepStrictEqual(data.environment, ENVIRONMENT)) {
    throw new Error('environment');
  }
  for (const key of ['web', 'managed']) {
    if (!hasExactKeys(data[key], SERVICE_KEYS)
        || !isDeepStrictEqual(data[key], EXPECTED[key])) {
      throw new Error(key);
    }
  }

  process.stdout.write(`${JSON.stringify({
    ok: true,
    state: 'owner_settings_converged',
    scope: 'one_time_adoption_settings',
    authority: 'railway.serviceInstance',
    action: 'stop_no_apply',
    projectId: PROJECT.id,
    environmentId: ENVIRONMENT.id,
    services: [EXPECTED.web, EXPECTED.managed].map((service) => ({
      serviceName: service.serviceName,
      serviceId: service.serviceId,
      railwayConfigFile: service.railwayConfigFile,
      dockerfilePath: service.dockerfilePath,
      restartPolicyType: service.restartPolicyType,
      restartPolicyMaxRetries: service.restartPolicyMaxRetries,
      verifiedSettings: true,
    })),
  }, null, 2)}\n`);
} catch {
  reject();
}
