const SQUID_SOURCE_NATIVE_POLICY = "official_source_native_v1";
const SQUID_SOURCE_TEMPLATE_VERSION = "squid-source-remix@1";
const SQUID_SOURCE_ASSET_PACK_VERSION = "official-source-media@1";

type SquidSourceNativeInput = {
  clientId: string;
  contentKind?: string;
  sourceUrl: string;
  sourceMediaStatus: unknown;
  sourceImageSha256: unknown;
  templateStyle: unknown;
  sourceImageUsed: unknown;
  spec: Record<string, unknown>;
  render?: Record<string, unknown>;
};

function isOfficialSquidStatusUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:"
      && /^(?:www\.)?x\.com$/i.test(url.hostname)
      && /^\/squidrouter\/status\/\d+$/i.test(url.pathname)
      && !url.username
      && !url.password
      && !url.hash;
  } catch {
    return false;
  }
}

/**
 * Recognize only the server-bound Squid path whose PNG is the official X
 * creative with no generated/localized overlay. This is deliberately stricter
 * than merely seeing `template_style=remix`: inconsistent or legacy records
 * remain fully reviewable and receive no source-native exemption.
 */
export function isVerifiedSquidSourceNativeNoOverlay(
  input: SquidSourceNativeInput,
): boolean {
  const { spec, render } = input;
  const regions = spec.translation_regions;
  if (
    input.clientId !== "squid"
    || (input.contentKind !== undefined && input.contentKind !== "daily_news")
    || !isOfficialSquidStatusUrl(input.sourceUrl)
    || input.sourceMediaStatus !== "present"
    || typeof input.sourceImageSha256 !== "string"
    || !/^[a-f0-9]{64}$/.test(input.sourceImageSha256)
    || input.templateStyle !== "remix"
    || input.sourceImageUsed !== true
    || spec.output_policy !== SQUID_SOURCE_NATIVE_POLICY
    || spec.render_strategy !== "source_remix"
    || spec.channel_profile !== "source_native"
    || spec.template_version !== SQUID_SOURCE_TEMPLATE_VERSION
    || spec.asset_pack_version !== SQUID_SOURCE_ASSET_PACK_VERSION
    || spec.source_text_visible !== false
    || !Array.isArray(regions)
    || regions.length !== 0
    || spec.visual_localization_status !== "no_text"
  ) return false;

  if (!render) return true;
  return render.template_style === "remix"
    && render.requested_template_style === "remix"
    && render.source_image_used === true
    && render.render_strategy === "source_remix"
    && render.channel_profile === "source_native"
    && render.template_version === SQUID_SOURCE_TEMPLATE_VERSION
    && render.asset_pack_version === SQUID_SOURCE_ASSET_PACK_VERSION;
}
