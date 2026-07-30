import type { EditableClientId } from "./editable-svg.mts";

export type OfficialLogoVariant = "dark" | "light";

type ArticleLogoLayout = {
  width: number;
  height: number;
  y: number;
  localMarketName?: string;
};

type OfficialBrandAsset = {
  dark: string;
  light: string;
  article: ArticleLogoLayout;
};

export const OFFICIAL_BRAND_ASSETS: Record<EditableClientId, OfficialBrandAsset> = {
  yellow: {
    dark: "/assets/brands/yellow-dark.svg",
    light: "/assets/brands/yellow-light.svg",
    article: { width: 186, height: 57, y: 39 },
  },
  origintrail: {
    dark: "/assets/brands/origintrail-dark.png",
    light: "/assets/brands/origintrail-light.png",
    // The official Figma PNG intentionally contains vertical transparent
    // padding. A taller image viewport keeps the visible wordmark at the same
    // optical height as the other brands without altering the source asset.
    article: { width: 228, height: 98, y: 20 },
  },
  squid: {
    dark: "/assets/brands/squid-dark.png",
    light: "/assets/brands/squid-light.png",
    article: { width: 146, height: 82, y: 22 },
  },
  babylon: {
    dark: "/assets/brands/babylon-dark.png",
    light: "/assets/brands/babylon-light.png",
    article: {
      width: 60,
      height: 60,
      y: 39,
      // The supplied official asset is the Babylon symbol, not a wordmark.
      // Keep the Korean market name as a separate text layer.
      localMarketName: "Babylon Korea",
    },
  },
};

export function officialBrandLogoPath(
  clientId: EditableClientId,
  variant: OfficialLogoVariant,
): string {
  return OFFICIAL_BRAND_ASSETS[clientId][variant];
}

export async function fetchOfficialBrandLogoDataUrl(
  clientId: EditableClientId,
  variant: OfficialLogoVariant,
  siteOrigin: string,
  fetchImpl: typeof fetch = fetch,
): Promise<string> {
  const logoUrl = new URL(officialBrandLogoPath(clientId, variant), siteOrigin).toString();
  const response = await fetchImpl(logoUrl, {
    signal: AbortSignal.timeout(8_000),
  });
  if (!response.ok) throw new Error("official_logo_fetch_failed");

  const contentType = (response.headers.get("content-type") || "")
    .split(";", 1)[0]
    .toLowerCase();
  if (!/^image\/(?:png|svg\+xml)$/.test(contentType)) {
    throw new Error("unsupported_official_logo_type");
  }

  const declaredSize = Number(response.headers.get("content-length") || 0);
  if (declaredSize > 512_000) throw new Error("official_logo_too_large");
  const bytes = Buffer.from(await response.arrayBuffer());
  if (!bytes.length) throw new Error("official_logo_empty");
  if (bytes.length > 512_000) throw new Error("official_logo_too_large");
  return `data:${contentType};base64,${bytes.toString("base64")}`;
}
