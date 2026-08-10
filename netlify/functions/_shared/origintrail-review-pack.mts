import { createHash } from "node:crypto";

import {
  getBatchReviewItem,
  type BatchReviewConfig,
  type BatchReviewDetail,
} from "./batch-review.mts";
import {
  recordGeneratedContent,
  uploadNewsCard,
  type ContentCatalogConfig,
  type ContentCatalogRecord,
  type UploadedNewsCard,
} from "./content-catalog.mts";
import { evaluateFactCheck } from "./fact-check.mts";
import { renderOriginTrailBatchBanner } from "./origintrail-batch-banner.mts";

const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const REVIEW_PACK_PROTOCOL = "origintrail-review-pack@1";
const REVIEW_PACK_DOMAIN = "coineasy-origintrail-review-pack";

export type OriginTrailReviewPack = {
  jobId: string;
  contentItemId: string;
  contentVersionId: string;
  assetId: string;
  sourceItemId: string;
  bannerSha256: string;
  reviewPackSha256: string;
  protocolVersion: typeof REVIEW_PACK_PROTOCOL;
  reused: boolean;
};

export class OriginTrailReviewPackError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "OriginTrailReviewPackError";
    this.code = code;
  }
}
function uuidBytes(value: string): Buffer {
  if (!UUID_PATTERN.test(value)) throw new OriginTrailReviewPackError("origintrail_review_pack_invalid");
  return Buffer.from(value.replaceAll("-", ""), "hex");
}

export function deterministicReviewPackAssetId(jobId: string, bannerSha256: string): string {
  if (!SHA256_PATTERN.test(bannerSha256)) {
    throw new OriginTrailReviewPackError("origintrail_review_pack_invalid");
  }
  const digest = createHash("sha1")
    .update(uuidBytes(jobId))
    .update(`${REVIEW_PACK_PROTOCOL}\0${bannerSha256}`, "utf8")
    .digest()
    .subarray(0, 16);
  digest[6] = (digest[6] & 0x0f) | 0x50;
  digest[8] = (digest[8] & 0x3f) | 0x80;
  const hex = digest.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function originTrailReviewPackSha256(input: {
  workspaceId: string;
  jobId: string;
  contentItemId: string;
  sourceItemId: string;
  inputSha256: string;
  resultSha256: string;
  sourceContentSha256: string;
  bannerSha256: string;
}): string {
  const values = [
    REVIEW_PACK_DOMAIN,
    "1.0",
    input.workspaceId,
    input.jobId,
    input.contentItemId,
    input.sourceItemId,
    input.inputSha256,
    input.resultSha256,
    input.sourceContentSha256,
    input.bannerSha256,
  ];
  if (
    !values.slice(2, 6).every(value => UUID_PATTERN.test(value))
    || !values.slice(6).every(value => SHA256_PATTERN.test(value))
  ) throw new OriginTrailReviewPackError("origintrail_review_pack_invalid");
  return createHash("sha256").update(values.join("\0"), "utf8").digest("hex");
}

function exactMaterializationIdentity(detail: BatchReviewDetail): {
  contentItemId: string;
  sourceItemId: string;
  resultSha256: string;
} {
  if (
    detail.request_id === null
    || !UUID_PATTERN.test(detail.request_id)
    || detail.source_item_ids.length !== 1
    || !UUID_PATTERN.test(detail.source_item_ids[0])
    || detail.result_sha256 === null
    || !SHA256_PATTERN.test(detail.result_sha256)
    || detail.source_content === null
    || !new Set(["x_article", "x_post_text"]).has(detail.source_evidence.kind)
    || detail.source_evidence.storage !== "inline"
  ) throw new OriginTrailReviewPackError("origintrail_review_pack_evidence_required");
  return {
    contentItemId: detail.request_id,
    sourceItemId: detail.source_item_ids[0],
    resultSha256: detail.result_sha256,
  };
}

function catalogPayload(
  detail: BatchReviewDetail,
  asset: UploadedNewsCard,
  reviewPackSha256: string,
): Parameters<typeof recordGeneratedContent>[1] {
  const result = detail.result_payload;
  const channelCopy = {
    telegram: result.telegram_copy_ko,
    x: result.x_copy_ko,
  };
  const factCheck = evaluateFactCheck({
    contentKind: "daily_news",
    source: {
      content: detail.source_content || "",
      url: detail.source_url || "",
      mode: "provided",
    },
    publicText: {
      headline: result.headline_ko,
      body: result.body_ko,
      source_url: detail.source_url,
    },
    channelCopy,
    artifactSha256: [asset.sha256],
  });
  return {
    requestId: detail.request_id || "",
    clientId: "origintrail",
    contentKind: "daily_news",
    title: result.headline_ko.trim(),
    content: {
      request_hash: reviewPackSha256,
      spec: {
        label: "OriginTrail 공식 업데이트",
        headline: result.headline_ko,
        body_lines: [result.body_ko],
        source_url: detail.source_url,
      },
      source: {
        resolved_content: detail.source_content,
        type: detail.source_evidence.kind,
        url: detail.source_url,
        mode: "provided",
        content_sha256: detail.source_evidence.content_sha256,
      },
      batch: {
        job_id: detail.job_id,
        input_sha256: detail.input_sha256,
        result_sha256: detail.result_sha256,
        workflow: detail.workflow_kind,
      },
      render: {
        renderer: "origintrail-deterministic-svg",
        template_version: "origintrail-batch-banner@1",
        motif: "network",
        width: asset.width,
        height: asset.height,
        banner_sha256: asset.sha256,
      },
    },
    channelCopy,
    generationMeta: {
      request_hash: reviewPackSha256,
      renderer: "origintrail-deterministic-svg",
      storage_backend: "supabase",
      mock_mode: false,
      batch_job_id: detail.job_id,
      batch_input_sha256: detail.input_sha256,
      batch_result_sha256: detail.result_sha256,
      source_content_sha256: detail.source_evidence.content_sha256,
      source_evidence_kind: detail.source_evidence.kind,
      banner_sha256: asset.sha256,
      review_pack_sha256: reviewPackSha256,
      review_pack_protocol: REVIEW_PACK_PROTOCOL,
      fact_check: factCheck,
    },
    asset,
    promptVersion: "origintrail-batch-review-pack@1",
  };
}

async function bindReviewPack(
  config: ContentCatalogConfig,
  detail: BatchReviewDetail,
  identity: ReturnType<typeof exactMaterializationIdentity>,
  catalog: ContentCatalogRecord,
  asset: UploadedNewsCard,
  reviewPackSha256: string,
  fetcher: typeof fetch,
  signal: AbortSignal,
): Promise<OriginTrailReviewPack> {
  const body = JSON.stringify({
    target_workspace_id: config.workspaceId,
    target_job_id: detail.job_id,
    target_content_item_id: identity.contentItemId,
    target_content_version_id: catalog.contentVersionId,
    target_asset_id: asset.assetId,
    target_source_item_id: identity.sourceItemId,
    target_input_sha256: detail.input_sha256,
    target_result_sha256: identity.resultSha256,
    target_source_content_sha256: detail.source_evidence.content_sha256,
    target_banner_sha256: asset.sha256,
    target_review_pack_sha256: reviewPackSha256,
  });
  let response: Response | null = null;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      response = await fetcher(
        `${config.supabaseUrl}/rest/v1/rpc/bind_origintrail_batch_review_pack`,
        {
          method: "POST",
          headers: {
            apikey: config.serviceRoleKey,
            Authorization: `Bearer ${config.serviceRoleKey}`,
            "Content-Type": "application/json",
          },
          body,
          signal,
        },
      );
    } catch {
      response = null;
      continue;
    }
    if (response.ok) break;
    if (response.status < 500) {
      throw new OriginTrailReviewPackError(
        response.status === 409
          ? "origintrail_review_pack_conflict"
          : "origintrail_review_pack_storage_unavailable",
      );
    }
    response = null;
  }
  if (!response?.ok) {
    throw new OriginTrailReviewPackError("origintrail_review_pack_storage_unavailable");
  }
  let raw: unknown;
  try {
    raw = await response.json();
  } catch {
    throw new OriginTrailReviewPackError("origintrail_review_pack_invalid_response");
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new OriginTrailReviewPackError("origintrail_review_pack_invalid_response");
  }
  const value = raw as Record<string, unknown>;
  if (
    value.job_id !== detail.job_id
    || value.content_item_id !== identity.contentItemId
    || value.content_version_id !== catalog.contentVersionId
    || value.asset_id !== asset.assetId
    || value.source_item_id !== identity.sourceItemId
    || value.banner_sha256 !== asset.sha256
    || value.review_pack_sha256 !== reviewPackSha256
    || value.protocol_version !== REVIEW_PACK_PROTOCOL
    || typeof value.reused !== "boolean"
  ) throw new OriginTrailReviewPackError("origintrail_review_pack_invalid_response");
  return {
    jobId: detail.job_id,
    contentItemId: identity.contentItemId,
    contentVersionId: catalog.contentVersionId,
    assetId: asset.assetId,
    sourceItemId: identity.sourceItemId,
    bannerSha256: asset.sha256,
    reviewPackSha256,
    protocolVersion: REVIEW_PACK_PROTOCOL,
    reused: value.reused,
  };
}

export async function materializeOriginTrailReviewPack(
  config: BatchReviewConfig,
  jobId: string,
  siteOrigin: string,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(25_000),
): Promise<OriginTrailReviewPack> {
  const detail = await getBatchReviewItem(config, jobId, fetcher, signal);
  if (!detail) throw new OriginTrailReviewPackError("origintrail_review_pack_evidence_required");
  const identity = exactMaterializationIdentity(detail);
  const banner = await renderOriginTrailBatchBanner(detail, siteOrigin, fetcher);
  const reviewPackSha256 = originTrailReviewPackSha256({
    workspaceId: config.workspaceId,
    jobId: detail.job_id,
    contentItemId: identity.contentItemId,
    sourceItemId: identity.sourceItemId,
    inputSha256: detail.input_sha256,
    resultSha256: identity.resultSha256,
    sourceContentSha256: detail.source_evidence.content_sha256,
    bannerSha256: banner.sha256,
  });
  const assetId = deterministicReviewPackAssetId(detail.job_id, banner.sha256);
  const asset = await uploadNewsCard(
    config,
    "origintrail",
    assetId,
    Uint8Array.from(banner.bytes).buffer,
    fetcher,
    signal,
  );
  const catalog = await recordGeneratedContent(
    config,
    catalogPayload(detail, asset, reviewPackSha256),
    fetcher,
    signal,
  );
  if (
    catalog.contentItemId !== identity.contentItemId
    || catalog.assetIds.length !== 1
    || catalog.assetIds[0] !== asset.assetId
  ) throw new OriginTrailReviewPackError("origintrail_review_pack_invalid_response");
  return bindReviewPack(
    config,
    detail,
    identity,
    catalog,
    asset,
    reviewPackSha256,
    fetcher,
    signal,
  );
}
