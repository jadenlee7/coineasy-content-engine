import { createHash } from "node:crypto";

import sharp from "sharp";

import {
  buildArticleBannerSvg,
  type ArticleBannerInput,
} from "./article-banner-svg.mts";
import type { BatchReviewDetail } from "./batch-review.mts";
import {
  ORIGINTRAIL_ARCHIVED_JOB_ID,
  ORIGINTRAIL_ARCHIVED_SOURCE_SHA256,
} from "./origintrail-archived-review.mts";
import {
  articleHeroLogoVariant,
  fetchOfficialBrandLogoDataUrl,
} from "./official-brand-assets.mts";

const BANNER_WIDTH = 1_200;
const BANNER_HEIGHT = 630;
const MAX_BANNER_BYTES = 4 * 1_024 * 1_024;

export type OriginTrailBatchBanner = {
  bytes: Buffer;
  sha256: string;
  width: 1_200;
  height: 630;
};

export class OriginTrailBatchBannerError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "OriginTrailBatchBannerError";
    this.code = code;
  }
}

export function hasStandaloneOriginTrailEvidence(sourceContent: string): boolean {
  return sourceContent.replace(/https?:\/\/\S+/giu, "").trim().length > 0;
}

function dateLabel(finishedAt: string): string {
  const date = new Date(finishedAt);
  if (!Number.isFinite(date.getTime())) {
    throw new OriginTrailBatchBannerError("origintrail_batch_banner_invalid_input");
  }
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function exactOriginTrailReview(detail: BatchReviewDetail): boolean {
  const evidence = detail.source_evidence;
  const sourceVerified = typeof detail.source_content === "string"
    ? hasStandaloneOriginTrailEvidence(detail.source_content)
      && evidence.storage === "inline"
      && evidence.content_length === detail.source_content.length
      && createHash("sha256").update(detail.source_content, "utf8").digest("hex")
        === evidence.content_sha256
    : detail.source_content === null
      && evidence.storage === "hash_only_archive"
      && detail.job_id === ORIGINTRAIL_ARCHIVED_JOB_ID
      && detail.input_sha256
        === "845705fbfed21b166e665c3b434eff0cd28870d9655d996c6e567d218a4d9dbd"
      && evidence.content_length === 6_661
      && evidence.content_sha256 === ORIGINTRAIL_ARCHIVED_SOURCE_SHA256
      && Number.isFinite(Date.parse(evidence.verified_at));
  return detail.client_id === "origintrail"
    && detail.agent_id === "origintrail_client_agent"
    && detail.workflow_kind === "official_source_nonurgent_pack"
    && detail.stage === "generate"
    && detail.status === "completed"
    && detail.result_code === "needs_review"
    && detail.source_url !== null
    && /^https:\/\/x[.]com\/origin_trail\/status\/[0-9]{1,19}$/.test(detail.source_url)
    && sourceVerified;
}

export async function renderOriginTrailBatchBanner(
  detail: BatchReviewDetail,
  siteOrigin: string,
  fetcher: typeof fetch = fetch,
): Promise<OriginTrailBatchBanner> {
  if (!exactOriginTrailReview(detail)) {
    throw new OriginTrailBatchBannerError("origintrail_batch_banner_evidence_required");
  }

  let origin: string;
  try {
    origin = new URL(siteOrigin).origin;
  } catch {
    throw new OriginTrailBatchBannerError("origintrail_batch_banner_invalid_origin");
  }
  if (!origin.startsWith("https://") && !origin.startsWith("http://localhost")) {
    throw new OriginTrailBatchBannerError("origintrail_batch_banner_invalid_origin");
  }

  let logoDataUrl: string;
  try {
    logoDataUrl = await fetchOfficialBrandLogoDataUrl(
      "origintrail",
      articleHeroLogoVariant("origintrail"),
      origin,
      fetcher,
    );
  } catch {
    throw new OriginTrailBatchBannerError("origintrail_batch_banner_logo_unavailable");
  }

  const input: ArticleBannerInput = {
    title: detail.result_payload.headline_ko,
    lead: detail.result_payload.telegram_copy_ko,
    sourceUrl: detail.source_url,
    date: dateLabel(detail.finished_at),
    motif: "network",
  };
  const svg = buildArticleBannerSvg("origintrail", input, logoDataUrl, null);

  let bytes: Buffer;
  try {
    bytes = await sharp(Buffer.from(svg, "utf8"), {
      density: 144,
      limitInputPixels: BANNER_WIDTH * BANNER_HEIGHT * 4,
    })
      .resize(BANNER_WIDTH, BANNER_HEIGHT, { fit: "fill" })
      .png({ compressionLevel: 9, progressive: false })
      .toBuffer();
  } catch {
    throw new OriginTrailBatchBannerError("origintrail_batch_banner_render_failed");
  }

  if (
    bytes.length < 24
    || bytes.length > MAX_BANNER_BYTES
    || !bytes.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex"))
    || bytes.readUInt32BE(16) !== BANNER_WIDTH
    || bytes.readUInt32BE(20) !== BANNER_HEIGHT
  ) {
    throw new OriginTrailBatchBannerError("origintrail_batch_banner_render_invalid");
  }

  return {
    bytes,
    sha256: createHash("sha256").update(bytes).digest("hex"),
    width: BANNER_WIDTH,
    height: BANNER_HEIGHT,
  };
}

export function originTrailBatchBannerResponse(
  banner: OriginTrailBatchBanner,
  jobId: string,
): Response {
  return new Response(banner.bytes, {
    status: 200,
    headers: {
      "Cache-Control": "no-store",
      "Content-Disposition": `inline; filename="origintrail-review-${jobId}.png"`,
      "Content-Length": String(banner.bytes.length),
      "Content-Type": "image/png",
      "X-CoinEasy-Content-SHA256": banner.sha256,
      "X-Content-Type-Options": "nosniff",
    },
  });
}
