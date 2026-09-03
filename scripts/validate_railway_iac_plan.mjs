#!/usr/bin/env node

import { readFileSync } from 'node:fs';

const MAX_PLAN_BYTES = 5 * 1024 * 1024;
const PROJECT_ID = '43f15c45-4a5c-4cf9-9400-e462cac46bb1';
const PROJECT = 'noble-illumination';
const ENVIRONMENT_ID = '5bf47282-1982-4930-95ad-29230ec0429b';
const ENVIRONMENT = 'production';
const ADDRESSES = [
  'service.coineasy-content-engine',
  'service.coineasy-managed-inspect',
];
const EXPECTED_CHANGES = [
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

function reject() {
  process.stderr.write('railway_iac_plan_rejected\n');
  process.exitCode = 1;
}

try {
  const raw = readFileSync(0, { encoding: 'utf8' });
  if (Buffer.byteLength(raw, 'utf8') > MAX_PLAN_BYTES) throw new Error('bounded');
  const plan = JSON.parse(raw);
  if (plan?.ok !== true || plan?.command !== 'plan') throw new Error('command');
  if (plan?.currentEnvironment?.projectId !== PROJECT_ID) throw new Error('project_id');
  if (plan?.currentEnvironment?.projectName !== PROJECT) throw new Error('project');
  if (plan?.currentEnvironment?.environmentId !== ENVIRONMENT_ID) throw new Error('environment_id');
  if (plan?.currentEnvironment?.environmentName !== ENVIRONMENT) throw new Error('environment');
  const configEtag = plan?.currentEnvironment?.configEtag;
  if (typeof configEtag !== 'string' || !/^[a-f0-9]{64}$/.test(configEtag)) {
    throw new Error('etag');
  }
  if (plan?.applyResult != null || plan?.deploymentId != null
      || plan?.stagedPatch != null || plan?.stagedPatchId != null) {
    throw new Error('mutation');
  }

  const desiredAddresses = (plan?.desiredGraph?.resources ?? [])
    .map((resource) => resource?.address).sort();
  if (JSON.stringify(desiredAddresses) !== JSON.stringify([...ADDRESSES].sort())) {
    throw new Error('scope');
  }
  if (!Array.isArray(plan?.diagnostics) || plan.diagnostics.length !== 0) {
    throw new Error('diagnostics');
  }

  const changes = plan?.changeSet?.changes;
  if (!Array.isArray(changes)) throw new Error('changes');
  const boundedChanges = changes.map((change) => ({
    summary: change?.summary,
    severity: change?.severity,
    kind: change?.kind,
    details: change?.details,
  }));
  if (JSON.stringify(boundedChanges) !== JSON.stringify(EXPECTED_CHANGES)) {
    throw new Error('unexpected_change_set');
  }
  const summaries = boundedChanges.map((change) => change.summary);

  process.stdout.write(`${JSON.stringify({
    ok: true,
    projectId: PROJECT_ID,
    project: PROJECT,
    environmentId: ENVIRONMENT_ID,
    environment: ENVIRONMENT,
    configEtag,
    desiredAddresses,
    changeCount: summaries.length,
    changes: summaries,
    diagnostics: [],
  }, null, 2)}\n`);
} catch {
  reject();
}
