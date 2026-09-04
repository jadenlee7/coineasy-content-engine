const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const FORBIDDEN = /(?:SECRET|PASSWORD|PRIVATE_KEY|SIGNING_KEY|JWT_|JOSE|SERVICE_ROLE|TOKEN|ACCESS_KEY|API_KEY|DATABASE_URL|PGPASSWORD|PGSERVICE|PGPASSFILE|NODE_OPTIONS|NODE_EXTRA_CA_CERTS|SSLKEYLOGFILE|(?:^|_)PROXY$|NODE_USE_ENV_PROXY)/i;
const SHARED_RUNTIME = /^(?:SUPABASE_|STUDIO_|TELEGRAM_|GROK_|XAI_|OPENAI_|BUZZ_|OFFICIAL_X_|BATCH_|TYPEFULLY_|ANTHROPIC_|AWS_|DATABASE_|PG)/i;

export function loadConfig(env = process.env, buildStamp) {
  // Check names only. Never read or render a disallowed credential's value.
  for (const name of Object.keys(env)) {
    if ((FORBIDDEN.test(name) || SHARED_RUNTIME.test(name)) && name !== 'MANAGED_INSPECT_PUBLISHABLE_KEY') {
      throw new Error('isolated_runtime_required');
    }
  }
  if (env.NODE_TLS_REJECT_UNAUTHORIZED === '0') throw new Error('insecure_runtime');
  if (!['', undefined, 'false', 'true'].includes(env.MANAGED_INSPECT_ENABLED)) throw new Error('invalid_configuration');
  if (env.MANAGED_INSPECT_ENABLED !== 'true') return Object.freeze({ enabled: false });
  let origin;
  let project;
  try { origin = new URL(env.MANAGED_INSPECT_ORIGIN); project = new URL(env.MANAGED_INSPECT_PROJECT_URL); }
  catch { throw new Error('invalid_configuration'); }
  if (origin.protocol !== 'https:' || origin.origin !== env.MANAGED_INSPECT_ORIGIN
      || !/^https:\/\/[a-z]{20}\.supabase\.co$/.test(env.MANAGED_INSPECT_PROJECT_URL)
      || !/^sb_publishable_[A-Za-z0-9_-]{16,200}$/.test(env.MANAGED_INSPECT_PUBLISHABLE_KEY ?? '')
      || !/^[a-f0-9]{40}$/.test(buildStamp ?? '')
      || (env.MANAGED_INSPECT_BUILD_SHA !== undefined && env.MANAGED_INSPECT_BUILD_SHA !== buildStamp)
      || !/^[a-f0-9]{40}$/.test(env.RAILWAY_GIT_COMMIT_SHA ?? '')
      || env.RAILWAY_GIT_COMMIT_SHA !== buildStamp
      || !UUID.test(env.MANAGED_INSPECT_WORKSPACE_ID ?? '')
      || env.MANAGED_INSPECT_WORKSPACE_ID === '00000000-0000-0000-0000-000000000000') {
    throw new Error('invalid_configuration');
  }
  return Object.freeze({ enabled: true, origin: origin.origin, projectUrl: project.origin,
    projectRef: project.hostname.split('.')[0], publishableKey: env.MANAGED_INSPECT_PUBLISHABLE_KEY,
    buildSha: buildStamp, workspaceId: env.MANAGED_INSPECT_WORKSPACE_ID });
}

export function assertRuntimeFlags(flags = process.execArgv) {
  if (flags.some((flag) => /^--(?:inspect|require|import|loader|experimental-loader|tls-keylog|heap-prof|heapsnapshot|report|expose-internals)/.test(flag)
      || flag === '-r')) throw new Error('unsafe_runtime_diagnostics');
}
