import { STUDIO_BUILD_RELEASE_SHA } from "./studio-release.generated.mts";

const RELEASE_SHA_PATTERN = /^[a-f0-9]{40}$/;

export const STUDIO_EXPECTED_RELEASE_HEADER = "x-studio-expected-release-sha";

export function currentStudioReleaseSha(
  buildReleaseSha: string | null = STUDIO_BUILD_RELEASE_SHA,
): string | null {
  const value = (buildReleaseSha || "").trim();
  return RELEASE_SHA_PATTERN.test(value) ? value : null;
}

export function requireExpectedStudioRelease(
  req: Request,
  buildReleaseSha: string | null = STUDIO_BUILD_RELEASE_SHA,
): Response | null {
  const expected = req.headers.get(STUDIO_EXPECTED_RELEASE_HEADER);
  if (expected === null) return null;
  const actual = currentStudioReleaseSha(buildReleaseSha);
  if (!RELEASE_SHA_PATTERN.test(expected) || actual !== expected) {
    return Response.json({ error: "studio_release_mismatch" }, {
      status: 503,
      headers: {
        "Cache-Control": "no-store",
        "Content-Type": "application/json; charset=utf-8",
      },
    });
  }
  return null;
}
