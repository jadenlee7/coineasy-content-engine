import type { Config, Context } from "@netlify/functions";
import { verifyTutorialSlide } from "./_shared/tutorial-slide-token.mts";
import {
  createTutorialSlideSignedUrl,
  tutorialStorageConfig,
  tutorialStoragePath,
  TutorialStorageError,
} from "./_shared/tutorial-storage.mts";

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}

export function safeTutorialSlidePath(
  workspaceId: unknown,
  clientId: unknown,
  objectId: unknown,
  fileName: unknown,
): string | null {
  return tutorialStoragePath(workspaceId, clientId, objectId, fileName);
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "GET") return json({ error: "method_not_allowed" }, 405);

  const storageConfig = tutorialStorageConfig((name) => Netlify.env.get(name));
  if (!storageConfig) return json({ error: "durable_storage_not_configured" }, 503);

  const storagePath = safeTutorialSlidePath(
    storageConfig.workspaceId,
    context.params.clientId,
    context.params.objectId,
    context.params.fileName,
  );
  if (!storagePath) return json({ error: "invalid_slide_path" }, 404);

  const apiSecret = Netlify.env.get("API_SECRET");
  if (!apiSecret) return json({ error: "server_not_configured" }, 503);
  const requestUrl = new URL(req.url);
  const expires = Number(requestUrl.searchParams.get("expires"));
  const token = requestUrl.searchParams.get("token") || "";
  if (!verifyTutorialSlide(
    apiSecret,
    context.params.clientId || "",
    context.params.objectId || "",
    context.params.fileName || "",
    expires,
    token,
  )) {
    return json({ error: "invalid_or_expired_slide_token" }, 403);
  }
  try {
    const signedUrl = await createTutorialSlideSignedUrl(storageConfig, storagePath);
    return new Response(null, {
      status: 302,
      headers: {
        "Cache-Control": "private, no-store",
        Location: signedUrl,
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch (error) {
    const code = error instanceof TutorialStorageError
      ? error.code
      : "durable_storage_unavailable";
    return json({ error: code }, 502);
  }
};

export const config: Config = {
  path: "/api/tutorial-slide/:clientId/:objectId/:fileName",
};
