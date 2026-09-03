import { defineRailway, github, preserve, project, service } from "railway/iac";

// This Railway project contains services sourced from several repositories.
// Keep this stable partial name so this repository owns only the two resources
// declared below instead of treating every omitted project resource as delete.
export const partial = "coineasy-content-engine-services";

const TARGET = {
  projectId: "43f15c45-4a5c-4cf9-9400-e462cac46bb1",
  projectName: "noble-illumination",
  environmentId: "5bf47282-1982-4930-95ad-29230ec0429b",
  environmentName: "production",
};

export default defineRailway((context) => {
  if (context.command !== "plan"
      || context.projectId !== TARGET.projectId
      || context.projectName !== TARGET.projectName
      || context.environmentId !== TARGET.environmentId
      || !context.isEnvironment(TARGET.environmentName)) {
    throw new Error("railway_iac_target_mismatch");
  }
  const source = github("jadenlee7/coineasy-content-engine");

  const web = service("coineasy-content-engine", {
    source,
    build: {
      buildEnvironment: "V3",
      builder: "DOCKERFILE",
      dockerfilePath: "Dockerfile",
    },
    start: "sh -c 'uvicorn api.server:app --host 0.0.0.0 --port $PORT'",
    healthcheck: "/health",
    healthcheckTimeout: 100,
    replicas: { "asia-southeast1-eqsg3a": 1 },
    deploy: {
      ipv6EgressEnabled: false,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 3,
      runtime: "V2",
      useLegacyStacker: false,
    },
    env: {
      ANTHROPIC_API_KEY: preserve(),
      API_SECRET: preserve(),
      CONTENT_STUDIO_WORKSPACE_ID: preserve(),
      EASYFARM_CONTENT_SIGNALS_TOKEN: preserve(),
      EASYFARM_CONTENT_SIGNALS_URL: preserve(),
      FIGMA_TOKEN: preserve(),
      GROK_QA_RELAY_TOKEN: preserve(),
      PUBLICATION_WORKER_TOKEN: preserve(),
      SUPABASE_SERVICE_ROLE_KEY: preserve(),
      SUPABASE_URL: preserve(),
      TELEGRAM_BOT_TOKEN_SQUID: preserve(),
      TELEGRAM_BOT_TOKEN_YELLOW: preserve(),
      TELEGRAM_CHANNEL_SQUID: preserve(),
      TELEGRAM_CHANNEL_YELLOW: preserve(),
      TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN: preserve(),
      TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID: preserve(),
      TELEGRAM_PUBLICATION_ALLOWED_CLIENTS: preserve(),
      TELEGRAM_PUBLICATION_ENABLED: preserve(),
      TELEGRAM_PUBLICATION_LEASE_SECONDS: preserve(),
      TELEGRAM_PUBLICATION_MAX_CLAIMS: preserve(),
      TELEGRAM_PUBLICATION_RECOVERY_LIMIT: preserve(),
      TELEGRAM_PUBLICATION_RELEASE_SHA: preserve(),
      TELEGRAM_REVIEW_BOT_TOKEN: preserve(),
      TELEGRAM_REVIEW_CHAT_ID: preserve(),
      TYPEFULLY_API_KEY: preserve(),
      TYPEFULLY_SOCIAL_SET_ID: preserve(),
      X_BEARER_TOKEN: preserve(),
    },
  });

  const managedInspect = service("coineasy-managed-inspect", {
    source,
    build: {
      buildEnvironment: "V3",
      builder: "DOCKERFILE",
      dockerfilePath: "Dockerfile.managed-inspect",
      watchPatterns: [
        "/Dockerfile.managed-inspect",
        "/Dockerfile.managed-inspect.dockerignore",
        "/tools/managed-telegram-inspect/auth.mjs",
        "/tools/managed-telegram-inspect/browser-guard.mjs",
        "/tools/managed-telegram-inspect/config.mjs",
        "/tools/managed-telegram-inspect/server.mjs",
        "/scripts/lib/telegram-resolution-inspect.mjs",
        "/railway.managed-inspect.json",
      ],
    },
    replicas: { "asia-southeast1-eqsg3a": 1 },
    deploy: {
      ipv6EgressEnabled: false,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
      runtime: "V2",
      useLegacyStacker: false,
    },
    networking: {
      serviceDomains: {
        "coineasy-managed-inspect-production.up.railway.app": { port: 8080 },
      },
    },
    env: {
      MANAGED_INSPECT_ENABLED: preserve(),
      MANAGED_INSPECT_SOURCE_SHA: preserve(),
      RAILWAY_DOCKERFILE_PATH: preserve(),
    },
  });

  return project(TARGET.projectName, { resources: [web, managedInspect] });
});
