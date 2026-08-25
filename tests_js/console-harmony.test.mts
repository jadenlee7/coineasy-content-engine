import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const consoleHtml = readFileSync(
  new URL("../web/console/index.html", import.meta.url),
  "utf8",
);

test("adds a Preview-only Harmony control room without replacing existing Studio views", () => {
  assert.match(consoleHtml, /data-studio-view="create"[^>]*>만들기</);
  assert.match(consoleHtml, /data-studio-view="library"[^>]*>팀 보관함</);
  assert.match(consoleHtml, /data-studio-view="harmony"[^>]*>Harmony 운영실</);
  assert.match(consoleHtml, /id="harmony-view"[^>]+hidden[^>]+data-preview-contract="agent-harmony-round@1"/);
  assert.match(consoleHtml, /Preview only · read-only/);
  assert.match(consoleHtml, /자동 발행 OFF/);
  assert.match(consoleHtml, /외부 호출 0/);
  assert.match(consoleHtml, /실행 adapter 0/);
  assert.match(consoleHtml, /실제 Grok·Codex 모델 호출 없이 안전 projection 조회만 수행합니다/);
  assert.match(consoleHtml, /\["create", "library", "harmony"\]\.includes\(nextView\)/);
});

test("starts with Squid and keeps all four client slots tenant-addressable", () => {
  const clientIds = [...consoleHtml.matchAll(/data-harmony-client="([a-z]+)"/g)].map(match => match[1]);
  assert.deepEqual(clientIds, ["squid", "origintrail", "yellow", "babylon"]);
  assert.match(consoleHtml, /const harmonyState = \{ client: "squid", attempted: false/);
  assert.match(consoleHtml, /const HARMONY_CLIENTS = Object\.freeze\(\{/);
  for (const clientId of ["squid", "origintrail", "yellow", "babylon"]) {
    assert.match(consoleHtml, new RegExp(`${clientId}: Object\\.freeze\\(\\{`));
  }
  assert.match(consoleHtml, /harmonyState\.client = button\.dataset\.harmonyClient/);
  assert.match(consoleHtml, /HARMONY_CLIENTS\[harmonyState\.client\] \|\| HARMONY_CLIENTS\.squid/);
});

test("shows the complete six-role typed round and the four bounded signal kinds", () => {
  const turnContract = consoleHtml.match(
    /const HARMONY_TURNS = Object\.freeze\(\[[\s\S]*?\n      \]\);(?=\n      const HARMONY_QA_CHECKS)/,
  )?.[0] || "";
  for (const role of ["quiz_bot", "community_ops", "content_source", "recap", "coordinator", "independent_qa"]) {
    assert.match(turnContract, new RegExp(`role: "${role}"`));
  }
  for (const signal of ["quiz_learning", "community_demand", "official_source", "recap_metric"]) {
    assert.match(turnContract, new RegExp(`signal: "${signal}"`));
  }
  assert.match(turnContract, /사실 권한이 있는 유일한 lane/);
  assert.match(consoleHtml, /agent-harmony-round@1/);
});

test("keeps structural QA, approval, cost, and recap explicitly fail-closed", () => {
  assert.match(consoleHtml, /Independent Structural QA/);
  assert.match(consoleHtml, /실제 Codex 모델 평가가 아니라 DB가 검증하는 typed receipt/);
  assert.match(consoleHtml, /실행 불가능 Preview 제안/);
  assert.match(consoleHtml, /official_source만 factual authority로 인정/);
  assert.match(consoleHtml, /Runtime 검증/);
  assert.match(consoleHtml, /실제 비용 미관측 · 라운드 상한 0 µUSD/);
  assert.match(consoleHtml, /비용·성과는 0이 아니라 <strong>미관측<\/strong>/);
  assert.match(consoleHtml, /class="harmony-action" id="harmony-approval-action" type="button" disabled>승인 대상 없음</);
  assert.match(consoleHtml, /class="harmony-action publish" id="harmony-publish-action" type="button" disabled>발행 OFF · 실행 불가</);
  assert.match(consoleHtml, /runtime-verified attestation과 typed signal 4종이 모이기 전 대표 handoff를 만들지 않습니다/);
});

test("renders static or observed Harmony without putting network actions in the renderer", () => {
  const renderSource = consoleHtml.match(
    /function renderHarmonyView\(\) \{[\s\S]*?\n      \}(?=\n\n      async function loadHarmonyDashboard)/,
  )?.[0];
  assert.ok(renderSource, "renderHarmonyView must be present");
  assert.match(renderSource, /HARMONY_TURNS\.map/);
  assert.match(renderSource, /HARMONY_QA_CHECKS\.map/);
  assert.match(renderSource, /harmonyRecap\.innerHTML/);
  assert.doesNotMatch(renderSource, /fetch\s*\(|XMLHttpRequest|sendBeacon|WebSocket|EventSource/);
  assert.match(renderSource, /harmonyApprovalAction\.disabled = true/);
});

test("loads the same-origin Preview dashboard once with GET and keeps failures static", () => {
  const loadSource = consoleHtml.match(
    /async function loadHarmonyDashboard\(\) \{[\s\S]*?\n      \}(?=\n\n      function resetHarmonyDashboard)/,
  )?.[0] || "";
  assert.match(loadSource, /if \(harmonyState\.attempted\) return/);
  assert.match(loadSource, /harmonyState\.attempted = true/);
  assert.match(loadSource, /fetch\("\/api\/harmony\/dashboard", \{/);
  assert.match(loadSource, /method: "GET"/);
  assert.match(loadSource, /credentials: "same-origin"/);
  assert.match(loadSource, /normalizeHarmonyDashboardPreview\(payload\)/);
  assert.match(loadSource, /handleStudioAccessResponse\(response, payload\)/);
  assert.match(loadSource, /harmonyState\.dashboard = null/);
  assert.match(loadSource, /harmonyState\.observation = "unobserved"/);
  assert.doesNotMatch(loadSource, /method: "POST"|body:|sendBeacon|WebSocket|EventSource/);
  assert.match(consoleHtml, /if \(showingHarmony && load\) loadHarmonyDashboard\(\)/);
});

test("browser projection rejects unknown keys, wrong tenant, and unsafe flags", () => {
  const normalizerSource = consoleHtml.match(
    /function normalizeHarmonyDashboardPreview\(raw\) \{[\s\S]*?\n      \}(?=\n\n      function harmonyTurnStatus)/,
  )?.[0];
  assert.ok(normalizerSource, "normalizeHarmonyDashboardPreview must be present");
  const normalize = Function(
    "\"use strict\"; " + normalizerSource + "; return normalizeHarmonyDashboardPreview;",
  )() as (value: unknown) => Record<string, any> | null;
  const empty = {
    schema_version: "harmony-preview-dashboard@1",
    workspace_id: "a0000000-0000-4000-8000-000000000001",
    client_id: "squid",
    observed_at: "2026-08-25T10:10:00Z",
    counts: {
      signals: 0,
      connector_receipts: 0,
      rounds: 0,
      plans: 0,
      stage_receipts: 0,
      pending_operator_inbox: 0,
    },
    latest_round: null,
    operator_inbox: [],
    trust: {
      environment: "preview",
      client_scope_verified: true,
      portable_trust: false,
    },
    flags: {
      read_only: true,
      external_calls: false,
      provider_calls: false,
      publication_calls: false,
      automatic_publication: false,
    },
  };
  assert.deepEqual(normalize(empty), empty);
  assert.equal(normalize({ ...empty, extra: true }), null);
  assert.equal(normalize({ ...empty, client_id: "yellow" }), null);
  assert.equal(normalize({
    ...empty,
    flags: { ...empty.flags, automatic_publication: true },
  }), null);

  const stages = [
    "plan",
    "private_content",
    "independent_qa",
    "operator_inbox",
    "recap",
  ].map((stage, index) => ({
    stage,
    ordinal: index + 1,
    receipt_sha256: String(index + 1).repeat(64),
    input_sha256: "a".repeat(64),
    output_sha256: index === 2 ? "c".repeat(64) : "b".repeat(64),
    recorded_at: `2026-08-25T10:0${index}:00Z`,
    verdict: index === 2 ? "passed" : null,
  }));
  const latestRound = {
    schema_version: "harmony-dashboard-round@1",
    round_id: "b0000000-0000-4000-8000-000000000001",
    plan_id: "c0000000-0000-4000-8000-000000000001",
    input_set_sha256: "d".repeat(64),
    round_sha256: "e".repeat(64),
    status: "operator_review_pending",
    headline_ko: "Squid 한국 커뮤니티 첫 협업 라운드",
    summary_ko: "공식 근거와 집계 신호를 분리한 Preview 제안입니다.",
    stages,
    automatic_publication: false,
  };
  const inboxItem = {
    schema_version: "harmony-dashboard-inbox@1",
    inbox_id: "d0000000-0000-4000-8000-000000000001",
    round_id: latestRound.round_id,
    plan_id: latestRound.plan_id,
    status: "pending",
    scope_sha256: "f".repeat(64),
    qa_receipt_id: "e0000000-0000-4000-8000-000000000001",
    qa_receipt_sha256: stages[2].receipt_sha256,
    qa_output_sha256: stages[2].output_sha256,
    created_at: "2026-08-25T10:03:00Z",
    automatic_publication: false,
  };
  const observed = {
    ...empty,
    counts: {
      signals: 4,
      connector_receipts: 4,
      rounds: 1,
      plans: 1,
      stage_receipts: 5,
      pending_operator_inbox: 1,
    },
    latest_round: latestRound,
    operator_inbox: [inboxItem],
  };
  assert.deepEqual(normalize(observed), observed);
  assert.equal(normalize({
    ...observed,
    counts: { ...observed.counts, pending_operator_inbox: 0 },
    operator_inbox: [],
  }), null);
  assert.equal(normalize({
    ...observed,
    operator_inbox: [{
      ...inboxItem,
      qa_receipt_sha256: "0".repeat(64),
    }],
  }), null);
});
