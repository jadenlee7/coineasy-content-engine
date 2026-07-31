import type { EditableClientId } from "./editable-svg.mts";

export type OfficialLogoVariant = "dark" | "light";

type ArticleLogoLayout = {
  width: number;
  height: number;
  y: number;
  localMarketName?: string;
};

type ArticleHeroAssetPaths = {
  formLanguage: string;
  squib: string;
  bubbles: string;
};

export type ArticleHeroBrandAssets = {
  formLanguage: string;
  squib: string;
  bubbles: string;
};

type OfficialBrandAsset = {
  dark: string;
  light: string;
  article: ArticleLogoLayout;
  articleHero?: ArticleHeroAssetPaths;
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
    articleHero: {
      formLanguage: "/assets/brands/squid-form-language-purple.png",
      squib: "/assets/brands/squid-squib-token-juggle.png",
      bubbles: "/assets/brands/squid-squib-bubbles.png",
    },
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

export function articleHeroLogoVariant(
  clientId: EditableClientId,
): OfficialLogoVariant {
  return clientId === "yellow" || clientId === "squid" ? "light" : "dark";
}

async function fetchOfficialImageDataUrl(
  assetPath: string,
  siteOrigin: string,
  maximumBytes: number,
  fetchImpl: typeof fetch = fetch,
): Promise<string> {
  const assetUrl = new URL(assetPath, siteOrigin).toString();
  const response = await fetchImpl(assetUrl, {
    signal: AbortSignal.timeout(8_000),
  });
  if (!response.ok) throw new Error("official_brand_asset_fetch_failed");

  const contentType = (response.headers.get("content-type") || "")
    .split(";", 1)[0]
    .toLowerCase();
  if (!/^image\/(?:png|svg\+xml)$/.test(contentType)) {
    throw new Error("unsupported_official_brand_asset_type");
  }

  const declaredSize = Number(response.headers.get("content-length") || 0);
  if (declaredSize > maximumBytes) throw new Error("official_brand_asset_too_large");
  const bytes = Buffer.from(await response.arrayBuffer());
  if (!bytes.length) throw new Error("official_brand_asset_empty");
  if (bytes.length > maximumBytes) throw new Error("official_brand_asset_too_large");
  return `data:${contentType};base64,${bytes.toString("base64")}`;
}

export async function fetchOfficialBrandLogoDataUrl(
  clientId: EditableClientId,
  variant: OfficialLogoVariant,
  siteOrigin: string,
  fetchImpl: typeof fetch = fetch,
): Promise<string> {
  return fetchOfficialImageDataUrl(
    officialBrandLogoPath(clientId, variant),
    siteOrigin,
    512_000,
    fetchImpl,
  );
}

export async function fetchOfficialArticleHeroAssetDataUrls(
  clientId: EditableClientId,
  siteOrigin: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ArticleHeroBrandAssets | null> {
  const paths = OFFICIAL_BRAND_ASSETS[clientId].articleHero;
  if (!paths) return null;

  const [formLanguage, squib, bubbles] = await Promise.all([
    fetchOfficialImageDataUrl(paths.formLanguage, siteOrigin, 512_000, fetchImpl),
    fetchOfficialImageDataUrl(paths.squib, siteOrigin, 512_000, fetchImpl),
    fetchOfficialImageDataUrl(paths.bubbles, siteOrigin, 512_000, fetchImpl),
  ]);
  return { formLanguage, squib, bubbles };
}
