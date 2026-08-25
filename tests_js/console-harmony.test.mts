import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const consoleHtml = readFileSync(
  new URL("../web/console/index.html", import.meta.url),
  "utf8",
);

function browserDashboard(stageCount: 4 | 5 = 5): Record<string, any> {
  const inputSet = "d".repeat(64);
  const outputs = ["1", "2", "3", "4", "5"].map((value) => value.repeat(64));
  const contracts = [
    {
      stage: "plan", actor: "grok_bot", capability: "harmony_plan",
      specialist_code: "squid_planner",
    },
    {
      stage: "private_content",
      actor: "content_engine",
      capability: "harmony_prepare_private_content",
      specialist_code: "squid_private_content_producer",
    },
    {
      stage: "independent_qa",
      actor: "codex",
      capability: "harmony_independent_qa",
      specialist_code: "squid_independent_qa",
    },
    {
      stage: "operator_inbox",
      actor: "human_operator_inbox",
      capability: "harmony_operator_inbox",
      specialist_code: "coineasy_representative_inbox",
    },
    {
      stage: "recap", actor: "coineasy_recap", capability: "harmony_recap",
      specialist_code: "squid_recap",
    },
  ];
  const stages = contracts.map((contract, index) => ({
    ...contract,
    ordinal: index + 1,
    specialist_binding_sha256: ["b", "c", "d", "e", "f"][index].repeat(64),
    operation_key_sha256: ["0", "1", "2", "3", "4"][index].repeat(64),
    principal_id: `f0000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
    producer_release_sha: "1".repeat(40),
    config_sha256: "2".repeat(64),
    receipt_sha256: ["6", "7", "8", "9", "a"][index].repeat(64),
    input_sha256: index === 0 ? inputSet : outputs[index - 1],
    output_sha256: outputs[index],
    recorded_at: `2026-08-25T10:0${index}:00Z`,
    verdict: index === 2 ? "passed" : null,
  })).slice(0, stageCount);
  const recap = stageCount === 5
    ? {
      schema_version: "harmony-dashboard-recap@1",
      receipt_sha256: stages[4].receipt_sha256,
      input_sha256: stages[4].input_sha256,
      output_sha256: stages[4].output_sha256,
      actual_cost_microusd: 0,
      stage_receipt_count: 5,
      operator_decision_observed: false,
      publication_count: 0,
      synthetic: true,
      automatic_publication: false,
    }
    : null;
  const headline = "Squid 한국 커뮤니티 첫 협업 라운드";
  const summary = "공식 근거와 집계 신호를 분리한 Preview 제안입니다.";
  const roundSha = "e".repeat(64);
  return {
    schema_version: "harmony-preview-dashboard@2",
    workspace_id: "a0000000-0000-4000-8000-000000000001",
    client_id: "squid",
    observed_at: "2026-08-25T10:10:00Z",
    counts: {
      signals: 4,
      connector_receipts: 4,
      rounds: 1,
      plans: 1,
      stage_receipts: stageCount,
      pending_operator_inbox: 1,
    },
    latest_round: {
      schema_version: "harmony-dashboard-round@2",
      round_id: "b0000000-0000-4000-8000-000000000001",
      plan_id: "c0000000-0000-4000-8000-000000000001",
      input_set_sha256: inputSet,
      round_sha256: roundSha,
      status: "operator_review_pending",
      headline_ko: headline,
      summary_ko: summary,
      stages,
      recap,
      automatic_publication: false,
    },
    operator_inbox: [{
      schema_version: "harmony-dashboard-inbox@2",
      inbox_id: "d0000000-0000-4000-8000-000000000001",
      round_id: "b0000000-0000-4000-8000-000000000001",
      plan_id: "c0000000-0000-4000-8000-000000000001",
      status: "pending",
      scope_sha256: stages[3].output_sha256,
      qa_receipt_id: "e0000000-0000-4000-8000-000000000001",
      qa_receipt_sha256: stages[2].receipt_sha256,
      qa_output_sha256: stages[2].output_sha256,
      round_sha256: roundSha,
      recap_receipt_sha256: recap?.receipt_sha256 ?? null,
      recap_output_sha256: recap?.output_sha256 ?? null,
      headline_ko: headline,
      summary_ko: summary,
      created_at: "2026-08-25T10:03:00Z",
      operator_decision_recorded: false,
      automatic_publication: false,
    }],
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
}

test("adds a Preview-only Harmony v2 control room without replacing Studio views", () => {
  assert.match(consoleHtml, /data-studio-view="create"[^>]*>만들기</);
  assert.match(consoleHtml, /data-studio-view="library"[^>]*>팀 보관함</);
  assert.match(consoleHtml, /data-studio-view="harmony"[^>]*>Harmony 운영실</);
  assert.match(consoleHtml, /id="harmony-view"[^>]+hidden[^>]+data-preview-contract="harmony-preview-dashboard@2"/);
  assert.match(consoleHtml, /Preview only · read-only/);
  assert.match(consoleHtml, /자동 발행 OFF/);
  assert.match(consoleHtml, /외부 호출 0/);
  assert.match(consoleHtml, /실행 adapter 0/);
  assert.match(consoleHtml, /실제 Grok·Codex 모델 호출 없이 안전 projection 조회만 수행합니다/);
  assert.match(consoleHtml, /\["create", "library", "harmony"\]\.includes\(nextView\)/);
});

test("keeps only Squid interactive and other clients as disabled static slots", () => {
  const clientIds = [...consoleHtml.matchAll(/data-harmony-client="([a-z]+)"/g)]
    .map((match) => match[1]);
  assert.deepEqual(clientIds, ["squid", "origintrail", "yellow", "babylon"]);
  const squidButton = consoleHtml.match(/<button class="harmony-client-tab active"[^>]+data-harmony-client="squid"[^>]*>/)?.[0] || "";
  assert.doesNotMatch(squidButton, /\bdisabled\b/);
  for (const clientId of ["origintrail", "yellow", "babylon"]) {
    const tag = consoleHtml.match(new RegExp(`<button class="harmony-client-tab"[^>]+data-harmony-client="${clientId}"[^>]*>`))?.[0] || "";
    assert.match(tag, /aria-disabled="true"/);
    assert.match(tag, /\bdisabled\b/);
  }
  assert.match(consoleHtml, /button\.disabled/);
  assert.match(consoleHtml, /button\.dataset\.harmonyClient !== "squid"/);
  assert.match(consoleHtml, /const harmonyState = \{ client: "squid", attempted: false/);
});

test("separates four signal contributors from the exact five specialist receipts", () => {
  const contributorContract = consoleHtml.match(
    /const HARMONY_CONTRIBUTORS = Object\.freeze\(\[[\s\S]*?\n      \]\);(?=\n      const HARMONY_SPECIALISTS)/,
  )?.[0] || "";
  const specialistContract = consoleHtml.match(
    /const HARMONY_SPECIALISTS = Object\.freeze\(\[[\s\S]*?\n      \]\);(?=\n      const HARMONY_QA_CHECKS)/,
  )?.[0] || "";
  for (const role of ["quiz_bot", "community_ops", "content_source", "recap_signal"]) {
    assert.match(contributorContract, new RegExp(`role: "${role}"`));
  }
  for (const signal of ["quiz_learning", "community_demand", "official_source", "recap_metric"]) {
    assert.match(contributorContract, new RegExp(`signal: "${signal}"`));
  }
  const specialistBindings = [
    ["plan", "squid_planner", "grok_bot", "harmony_plan"],
    ["private_content", "squid_private_content_producer", "content_engine", "harmony_prepare_private_content"],
    ["independent_qa", "squid_independent_qa", "codex", "harmony_independent_qa"],
    ["operator_inbox", "coineasy_representative_inbox", "human_operator_inbox", "harmony_operator_inbox"],
    ["recap", "squid_recap", "coineasy_recap", "harmony_recap"],
  ];
  for (const [stage, specialistCode, actor, capability] of specialistBindings) {
    assert.match(
      specialistContract,
      new RegExp(`stage: "${stage}", specialistCode: "${specialistCode}", actor: "${actor}", capability: "${capability}"`),
    );
  }
  assert.match(consoleHtml, /id="harmony-contributors"/);
  assert.match(consoleHtml, /id="harmony-specialists"/);
  assert.match(consoleHtml, /사실 권한이 있는 유일한 lane/);
});

test("keeps representative approval and publication actions disabled", () => {
  assert.match(consoleHtml, /독립 구조 QA gate/);
  assert.match(consoleHtml, /실제 Codex 모델 평가가 아니라 DB가 검증하는 typed receipt/);
  assert.match(consoleHtml, /official_source만 factual authority로 인정/);
  assert.match(consoleHtml, /Runtime 검증/);
  assert.match(consoleHtml, /class="harmony-action" id="harmony-approval-action" type="button" disabled>승인 대상 없음/);
  assert.match(consoleHtml, /class="harmony-action publish" id="harmony-publish-action" type="button" disabled>발행 OFF · 실행 불가/);
  assert.match(consoleHtml, /대표 결정 대기 · 읽기 전용/);
  assert.match(consoleHtml, /runtime-verified attestation과 typed signal 4종이 모이기 전 대표 handoff를 만들지 않습니다/);
});

test("renderer shows contributors, specialist rail, inbox detail, and actual recap artifact", () => {
  const renderSource = consoleHtml.match(
    /function renderHarmonyView\(\) \{[\s\S]*?\n      \}(?=\n\n      async function loadHarmonyDashboard)/,
  )?.[0] || "";
  assert.match(renderSource, /HARMONY_CONTRIBUTORS\.map/);
  assert.match(renderSource, /HARMONY_SPECIALISTS\.map/);
  assert.match(renderSource, /latestInbox\.headline_ko/);
  assert.match(renderSource, /latestInbox\.summary_ko/);
  assert.match(renderSource, /latestInbox\.round_sha256\.slice/);
  assert.match(renderSource, /recap\.receipt_sha256\.slice/);
  assert.match(renderSource, /recap\.actual_cost_microusd/);
  assert.match(renderSource, /recap\.publication_count/);
  assert.match(renderSource, /terminal recap 미생성/);
  assert.doesNotMatch(renderSource, /fetch\s*\(|XMLHttpRequest|sendBeacon|WebSocket|EventSource/);
  assert.match(renderSource, /harmonyApprovalAction\.disabled = true/);
});

test("loads the same-origin Preview dashboard once with GET and keeps failures static", () => {
  const loadSource = consoleHtml.match(
    /async function loadHarmonyDashboard\(\) \{[\s\S]*?\n      \}(?=\n\n      function resetHarmonyDashboard)/,
  )?.[0] || "";
  assert.match(loadSource, /if \(harmonyState\.attempted\) return/);
  assert.match(loadSource, /fetch\("\/api\/harmony\/dashboard", \{/);
  assert.match(loadSource, /method: "GET"/);
  assert.match(loadSource, /credentials: "same-origin"/);
  assert.match(loadSource, /normalizeHarmonyDashboardPreview\(payload\)/);
  assert.match(loadSource, /handleStudioAccessResponse\(response, payload\)/);
  assert.doesNotMatch(loadSource, /method: "POST"|body:|sendBeacon|WebSocket|EventSource/);
  assert.match(consoleHtml, /if \(showingHarmony && load\) loadHarmonyDashboard\(\)/);
});

test("browser projection enforces specialist identity, hash chain, inbox scope, and recap", () => {
  const normalizerSource = consoleHtml.match(
    /function normalizeHarmonyDashboardPreview\(raw\) \{[\s\S]*?\n      \}(?=\n\n      function harmonyContributorStatus)/,
  )?.[0];
  assert.ok(normalizerSource, "normalizeHarmonyDashboardPreview must be present");
  const normalize = Function(
    "\"use strict\"; " + normalizerSource + "; return normalizeHarmonyDashboardPreview;",
  )() as (value: unknown) => Record<string, any> | null;

  const observed = browserDashboard();
  const stageFour = browserDashboard(4);
  assert.deepEqual(normalize(observed), observed);
  assert.deepEqual(normalize(stageFour), stageFour);

  const cases = [
    { ...observed, extra: true },
    { ...observed, client_id: "yellow" },
    { ...observed, flags: { ...observed.flags, automatic_publication: true } },
    {
      ...observed,
      latest_round: {
        ...observed.latest_round,
        stages: observed.latest_round.stages.map((stage: any, index: number) =>
          index === 0 ? { ...stage, actor: "caller_claim" } : stage
        ),
      },
    },
    {
      ...observed,
      latest_round: {
        ...observed.latest_round,
        stages: observed.latest_round.stages.map((stage: any, index: number) =>
          index === 4
            ? { ...stage, principal_id: observed.latest_round.stages[0].principal_id }
            : stage
        ),
      },
    },
    {
      ...observed,
      latest_round: {
        ...observed.latest_round,
        stages: observed.latest_round.stages.map((stage: any, index: number) =>
          index === 0 ? { ...stage, input_sha256: "0".repeat(64) } : stage
        ),
      },
    },
    {
      ...observed,
      latest_round: {
        ...observed.latest_round,
        stages: observed.latest_round.stages.map((stage: any, index: number) =>
          index === 1 ? { ...stage, input_sha256: "0".repeat(64) } : stage
        ),
      },
    },
    {
      ...observed,
      latest_round: {
        ...observed.latest_round,
        stages: observed.latest_round.stages.map((stage: any, index: number) =>
          index === 0 ? { ...stage, verdict: "passed" } : stage
        ),
      },
    },
    {
      ...observed,
      operator_inbox: [{ ...observed.operator_inbox[0], scope_sha256: "0".repeat(64) }],
    },
    {
      ...observed,
      operator_inbox: [{ ...observed.operator_inbox[0], round_sha256: "0".repeat(64) }],
    },
    {
      ...observed,
      latest_round: {
        ...observed.latest_round,
        recap: { ...observed.latest_round.recap, output_sha256: "0".repeat(64) },
      },
    },
    { ...observed, latest_round: { ...observed.latest_round, recap: null } },
    {
      ...stageFour,
      latest_round: { ...stageFour.latest_round, recap: observed.latest_round.recap },
    },
  ];
  for (const value of cases) assert.equal(normalize(value), null);
});
