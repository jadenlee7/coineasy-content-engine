#!/usr/bin/env node

const MAX_PLAN_BYTES = 5 * 1024 * 1024;
const PROJECT_ID = '43f15c45-4a5c-4cf9-9400-e462cac46bb1';
const PROJECT = 'noble-illumination';
const ENVIRONMENT_ID = '5bf47282-1982-4930-95ad-29230ec0429b';
const ENVIRONMENT = 'production';
const ADDRESSES = [
  'service.coineasy-content-engine',
  'service.coineasy-managed-inspect',
];
function reject() {
  process.stderr.write('railway_iac_plan_rejected\n');
  process.exitCode = 1;
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
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
  const raw = await readBoundedStdin(MAX_PLAN_BYTES);
  const plan = JSON.parse(raw);
  if (!isObject(plan)
      || Object.hasOwn(plan, 'errors')
      || Object.hasOwn(plan, 'extensions')) {
    throw new Error('root');
  }
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
  if (changes.length !== 0) throw new Error('not_converged');

  process.stdout.write(`${JSON.stringify({
    ok: true,
    state: 'converged',
    action: 'stop_no_apply',
    projectId: PROJECT_ID,
    project: PROJECT,
    environmentId: ENVIRONMENT_ID,
    environment: ENVIRONMENT,
    configEtag,
    desiredAddresses,
    changeCount: 0,
    changes: [],
    diagnostics: [],
  }, null, 2)}\n`);
} catch {
  reject();
}
