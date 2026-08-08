import type { BatchReviewDetail } from "./batch-review.mts";

export const ORIGINTRAIL_ARCHIVED_JOB_ID = "b5dc57dc-5565-4d74-834a-3bbd8ec71f30";
export const ORIGINTRAIL_ARCHIVED_SOURCE_SHA256 =
  "82b2348ad23ccc99c17108a59954dae15c12d7e314cf05884ad5512ffd87c041";

const ARCHIVED_REVIEW: BatchReviewDetail = Object.freeze({
  ref: `batch:${ORIGINTRAIL_ARCHIVED_JOB_ID}`,
  job_id: ORIGINTRAIL_ARCHIVED_JOB_ID,
  client_id: "origintrail",
  agent_id: "origintrail_client_agent",
  workflow_kind: "official_source_nonurgent_pack",
  stage: "generate",
  status: "completed",
  model: "gpt-5.6-luna",
  model_tier: "S",
  title: "OriginTrail 7월 업데이트: 검증 가능한 공유 컨텍스트의 확장",
  result_code: "needs_review",
  actual_cost_microusd: 590,
  finished_at: "2026-08-05T09:12:03.003Z",
  source_url: "https://x.com/origin_trail/status/2084283287518798116",
  request_id: null,
  source_item_ids: [],
  result_sha256: null,
  result_payload: Object.freeze({
    headline_ko: "OriginTrail 7월 업데이트: 검증 가능한 공유 컨텍스트의 확장",
    body_ko: "7월 OriginTrail 업데이트 요약\n\n• DKG V10 그리드가 출시를 넘어 운영 단계로 전환됐습니다.\n• Block의 Buzz와 Decentralized Knowledge Graph 통합이 소개됐습니다. Buzz의 각 대화방은 DKG Context Graph와 1:1로 연결되며, 메시지와 워크플로 단계는 서명된 Nostr 이벤트로 기록됩니다.\n• Oxford PharmaGenesis는 임상 지식의 출처를 유지하는 의료 AI 활용 사례를 선보였습니다.\n• DMaaST를 통해 KamstrupGroup과 JPB_SYSTEME가 DKG에 온보딩됐으며, Digital Product Passports를 활용한 제품·부품 데이터 추적 사례가 소개됐습니다.\n• Umanitek의 Guardian Agent Blackbox와 TrueSeal이 공개됐습니다.\n• FIFA World Cup의 경기·결과·선수-클럽 관계가 OriginTrail 기반 공유 컨텍스트 그래프로 실시간 수집·게시됩니다.\n• DKG V10.0.10이 메인넷에 적용돼 Knowledge Assets 검색·검증, 동기화, 게시 및 노드 안정성이 개선됐습니다.\n\n핵심 주제는 에이전트와 산업 데이터가 검증 가능한 공유 컨텍스트를 활용하도록 하는 것입니다.",
    x_copy_ko: "OriginTrail 7월 업데이트\n\nDKG V10이 운영 단계로 전환됐습니다.\n\nBlock의 Buzz 통합, Oxford PharmaGenesis의 의료 AI, DMaaST의 산업·항공우주 Digital Product Passports, Guardian Agent Blackbox와 TrueSeal, FIFA World Cup 실시간 컨텍스트 그래프가 소개됐습니다.\n\nDKG V10.0.10은 Knowledge Assets 검증·동기화와 노드 안정성을 개선했습니다.",
    telegram_copy_ko: "OriginTrail 7월 업데이트입니다.\n\nDKG V10 그리드가 운영 단계로 전환됐고, Block의 Buzz 통합을 통해 서명된 대화와 검증 가능한 지식 연결이 소개됐습니다.\n\nOxford PharmaGenesis의 의료 AI, DMaaST의 산업·항공우주 Digital Product Passports, Umanitek의 Guardian Agent Blackbox와 TrueSeal, FIFA World Cup 실시간 컨텍스트 그래프 등 다양한 사례도 공개됐습니다.\n\n또한 DKG V10.0.10이 메인넷에 적용돼 Knowledge Assets 검색·검증과 동기화, 게시 안정성이 개선됐습니다.",
  }),
  source_content: null,
  source_evidence: Object.freeze({
    storage: "hash_only_archive",
    content_length: 6_661,
    content_sha256: ORIGINTRAIL_ARCHIVED_SOURCE_SHA256,
    verified_at: "2026-08-05T11:28:00.000Z",
  }),
  input_sha256: "845705fbfed21b166e665c3b434eff0cd28870d9655d996c6e567d218a4d9dbd",
  actual_input_tokens: 1_945,
  actual_output_tokens: 658,
});

export function getOriginTrailArchivedReview(jobId: string): BatchReviewDetail | null {
  return jobId.toLowerCase() === ORIGINTRAIL_ARCHIVED_JOB_ID
    ? ARCHIVED_REVIEW
    : null;
}
