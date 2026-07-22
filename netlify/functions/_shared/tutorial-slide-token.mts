import { createHmac, timingSafeEqual } from "node:crypto";

function signature(
  secret: string,
  clientId: string,
  objectId: string,
  fileName: string,
  expires: number,
): string {
  return createHmac("sha256", secret)
    .update(`${clientId}/${objectId}/${fileName}:${expires}`)
    .digest("hex");
}

export function signTutorialSlide(
  secret: string,
  clientId: string,
  objectId: string,
  fileName: string,
  expires: number,
): string {
  return signature(secret, clientId, objectId, fileName, expires);
}

export function verifyTutorialSlide(
  secret: string,
  clientId: string,
  objectId: string,
  fileName: string,
  expires: number,
  token: string,
  now = Math.floor(Date.now() / 1_000),
): boolean {
  if (!Number.isSafeInteger(expires) || expires <= now || expires > now + 3_700) return false;
  if (!/^[a-f0-9]{64}$/.test(token)) return false;
  const expected = Buffer.from(signature(secret, clientId, objectId, fileName, expires), "hex");
  const actual = Buffer.from(token, "hex");
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}
