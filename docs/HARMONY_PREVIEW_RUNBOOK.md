# Harmony disposable Preview 검증 런북

## 목적과 고정 경계

이 런북은 **disposable Supabase Preview branch 한 곳**에서 고객별 typed
signal, RLS 원장, connector attestation, Squid 한 고객의 private 협업
라운드, 그리고 Netlify Deploy Preview의 읽기 전용 운영실을 검증할 때만
사용합니다.

```text
Quiz aggregate ─┐
Community ops ──┼─> client-scoped JWT attestation -> immutable typed ledger
Official source ┤                                      |
Recap aggregate ┘                                      v
             plan -> private_content -> independent_qa
                  -> operator_inbox(pending) -> recap
                                      |
                                      v
                         GET-only Deploy Preview dashboard
```

다음은 이 검증의 일부가 아닙니다.

- Production Supabase, Netlify Production, Railway Production 변경
- provider/OpenAI/Grok 호출, Buzz/Telegram/X 메시지 또는 다른 외부 행동
- 승인 결정 기록, publication 생성, 자동 발행
- 다른 고객 vertical slice, backfill, 실제 사용자 원문 또는 개인정보

모든 기능은 기본 `OFF`입니다. `HARMONY_DASHBOARD_PREVIEW_ENABLED=true`는
별도 승인을 받은 Deploy Preview 관측 시간에만 허용하고, 검증 직후 다시
`false`로 되돌립니다. `automatic_publication`, `provider_calls`,
`publication_calls`, `external_calls`는 모든 receipt와 projection에서 계속
`false`여야 합니다.

## 현재 계약 스냅샷

아래 이름은 2026-08-25의 작업 트리에서 읽은 **미병합 계약**입니다. 실행
직전에 exact Git SHA에서 다시 확인해야 하며, 이름이나 응답 shape가 바뀌면
이 런북보다 코드를 우선하지 말고 검증을 중단해 문서를 함께 갱신합니다.

### Preview 마이그레이션 순서

Preview 전용 네 파일은 같은 exact commit의 공통 원장 함수와 role closure에
의존하므로 아래 **여섯 파일 전체**를 각각 SHA-256으로 고정해 순서대로
적용합니다. 전체 repository migration을 밀어 넣는 unconstrained `db push`는
사용하지 않습니다.

1. `20260825130000_agent_work_order_ledger.sql`
2. `20260825131000_agent_work_order_roles.sql`
3. `20260825132000_harmony_preview_collaboration.sql`
4. `20260825133000_harmony_preview_vertical_slice.sql`
5. `20260825134000_harmony_preview_stage_chain.sql`
6. `20260825135000_harmony_preview_dashboard_roles.sql`

공개 RPC와 허용 role은 다음과 같습니다.

| RPC | 허용 role | 효과 |
|---|---|---|
| `submit_preview_harmony_signal(uuid,text,uuid,jsonb)` | `coineasy_harmony_connector` | signal과 DB-검증 connector receipt를 원자적으로 추가 |
| `create_preview_harmony_squid_plan(uuid,text,uuid,uuid,uuid,text[],text)` | `coineasy_harmony_orchestrator` | Squid plan, round, plan-stage receipt 생성 |
| `append_preview_harmony_squid_stage(uuid,text,uuid,uuid,text,uuid,uuid,jsonb)` | `coineasy_harmony_content`, `coineasy_harmony_qa`, `coineasy_harmony_operator`, `coineasy_harmony_recap` | role별 다음 stage 하나 추가. QA 단계만 typed evidence JSON을 요구 |
| `get_preview_harmony_round(uuid,text,uuid)` | `coineasy_harmony_orchestrator`, `coineasy_harmony_operator` | 완성된 private round 조회 |
| `get_preview_harmony_dashboard(uuid,text)` | `coineasy_harmony_dashboard` | 읽기 전용 운영실 projection 조회 |

Connector capability는 lane에 고정합니다.

- `quiz_bot` -> `harmony_submit_quiz_bot`
- `community_ops` -> `harmony_submit_community_ops`
- `content_source` -> `harmony_submit_content_source`
- `recap` -> `harmony_submit_recap`

Stage capability는 순서대로 `harmony_plan`,
`harmony_prepare_private_content`, `harmony_independent_qa`,
`harmony_operator_inbox`, `harmony_recap`입니다. 모든 role은 `NOLOGIN`,
`NOINHERIT`, `NOBYPASSRLS`, `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`,
`NOREPLICATION`이어야 하고, Harmony 테이블 직접 grant는 0이어야 합니다.
P0의 `harmony_independent_qa`는 실제 Codex/Grok 모델 호출이 아니라 별도
principal이 prior-output SHA와 고정된 구조 조건을 attestation하는
deterministic gate입니다. 의미 기반 QA나 실패 verdict 원장은 이 검증의
성공으로 주장하지 않습니다.

Netlify 계약은 다음과 같습니다.

- route: `GET /api/harmony/dashboard`
- Preview flag: `HARMONY_DASHBOARD_PREVIEW_ENABLED` (기본 OFF)
- scoped bearer: `SUPABASE_HARMONY_DASHBOARD_KEY`
- project `apikey`: `SUPABASE_PUBLISHABLE_KEY`
- branch URL: `SUPABASE_URL`
- workspace scope: `CONTENT_STUDIO_WORKSPACE_ID`
- host fence: function `Context.deploy.context=deploy-preview`,
  `Context.deploy.published=false`, and request host
  `deploy-preview-<PR>--<Context.site.name>.netlify.app`
- commit fence: build-stamped `STUDIO_BUILD_RELEASE_SHA` equals
  `HARMONY_DASHBOARD_EXPECTED_COMMIT_SHA` (40-hex); runtime `COMMIT_REF` is not
  trusted or required
- Studio session 인증 필수, `Cache-Control: no-store`
- service-role fallback 없음

### Preview 실행 전 남은 검증 차단점

계약 구현 파일이 존재하는 것만으로 Gate 1을 통과하지 않습니다. exact Git
SHA에서 여섯 migration의 disposable PostgreSQL 적용, security smoke,
`scripts/probe_harmony_preview_concurrency.py`의 64개 실제 DB connection 결과,
그리고 `scripts/probe_harmony_preview_postgrest.py`의 실제 서명 JWT/PostgREST
64-way connector 결과가 모두 성공해야 합니다. dashboard 응답은
`counts.pending_operator_inbox`,
`trust.client_scope_verified=true`, `trust.portable_trust=false`를 정확히
사용하며, 만료되거나 현재 official source와 달라진 round는 projection과
count에서 즉시 제외되어야 합니다. 이 중 하나라도 확인되지 않으면
Gate 1에서 `BLOCK`입니다.

## 승인 게이트

각 gate는 별도 승인입니다. 이전 gate 승인이 다음 gate 권한을 포함하지
않습니다.

### Gate 1 — 유료 Preview branch

실행 직전에 Supabase의 현재 branch 시간당 비용을 조회해 조직, Production
project ref, 예상 TTL, 최대 예상액과 함께 대표에게 제시합니다. 대표가 해당
금액과 branch 생성을 명시적으로 승인하기 전에는 생성하지 않습니다.

승인 receipt에 다음을 기록합니다.

- exact Git SHA와 여섯 마이그레이션 SHA-256
- `examples/harmony-preview-squid-config.json`의 canonical byte SHA-256
- Supabase 조직과 parent project ref
- branch 이름, 현재 시간당 비용, 자동 삭제 예정 UTC 시각
- `Preview only`, `max_cost_microusd=0`, `max_external_actions=0`

### Gate 2 — Preview 비밀과 scoped JWT

Branch가 ready인 뒤 별도 승인으로 Preview 비밀을 설정합니다. Legacy JWT
secret이 필요하면 Keychain 또는 승인된 관리 화면에서 **메모리로만** 읽고
로그, 셸 기록, 파일, 채팅에 남기지 않습니다.

각 JWT는 서로 다른 `jti`와 최소 수명을 사용하고 다음 공통 claim을
고정합니다.

```text
iss=supabase, aud=authenticated, ref=<Preview branch ref>
workspace_id=<approved UUID>, client_id=squid, environment=preview
automatic_publication=false, max_cost_microusd=0, max_external_actions=0
iat=<current integer epoch>, exp=<short-lived integer epoch>
```

Connector/stage JWT는 exact role, capability, `sub`,
`producer_principal_id`, `release_sha`, `config_sha256`, `jti`를 추가합니다.
Dashboard JWT는 role `coineasy_harmony_dashboard`만 사용합니다. Netlify
Deploy Preview에는 scoped dashboard JWT만 bearer로 저장하며
`SUPABASE_SERVICE_ROLE_KEY`로 대체하지 않습니다.

### Gate 3 — Preview DB 적용

별도 승인 후 여섯 마이그레이션을 **Preview branch ref를 재확인한 연결**에만
순서대로 적용합니다. DB owner가 `private.harmony_preview_environment_fence`
에 exact branch ref와 짧은 만료 시각을 한 번 seed합니다. 이 table은
immutable하므로 오류 시 row를 수정/삭제하지 않고 branch 전체를 삭제합니다.

적용 직후, E2E 입력 전 아래를 읽기 전용으로 확인합니다.

- 여섯 원장 table 모두 RLS와 FORCE RLS 활성
- Harmony role의 모든 직접 table/view/sequence grant 0
- public/anon/authenticated/service_role의 Harmony 원장 table 직접 grant 0
- service_role의 다섯 공개 RPC execute grant 0
- 각 Harmony role의 execute-set이 위 표와 정확히 일치
- role 특권과 상호 role membership 0. 런타임 assume edge는
  `authenticator`의 `SET=true`, `ADMIN=false`, `INHERIT=false` 한 건뿐입니다.
  Supabase PostgreSQL 16이 custom role 생성자 `postgres`에 자동으로 남기는
  `ADMIN=true`, `SET=false`, `INHERIT=false` 관리 edge는 허용하되 런타임
  assume 권한으로 간주하지 않으며, 그 밖의 principal edge는 0이어야 합니다.
- environment fence는 exact Preview ref 한 건, active, 미만료
- Harmony signal/receipt/round/plan/stage/inbox row 모두 0
- Production 연결과 Production row delta 0

### Gate 4 — Netlify Deploy Preview

별도 승인 후 exact Git SHA의 Deploy Preview에만 branch URL, publishable
key, scoped dashboard JWT, workspace ID와 exact commit fence를 설정합니다.
먼저 flag가 없거나
`false`인 OFF deploy를 확인한 뒤, 승인된 관측 시간에만
`HARMONY_DASHBOARD_PREVIEW_ENABLED=true`로 다시 배포합니다.

성공 전제는 Netlify Deploy API의 state `ready`, context `deploy-preview`,
deploy `commit_ref`가 승인 SHA와 exact 일치하는 것입니다. 함수 런타임은
`Context.deploy.context/published`, `Context.site.name/url`, request URL을
함께 검사하고, build 시 생성된 release SHA가
`HARMONY_DASHBOARD_EXPECTED_COMMIT_SHA`와 같은지 검증합니다. Production
context에서 같은 route는 403, flag OFF는 503, 비인증 요청은 거절되어야
합니다.

### Gate 5 — 64 동시성 및 Squid E2E

별도 승인 후 synthetic/aggregate 입력만 사용합니다. 먼저 64개의 독립 DB
connection/transaction이 동일한 exact signal request를 동시에 제출합니다.
그 뒤 나머지 세 typed signal을 각각 한 번 기록하고, plan과 네 후속 stage를
순서대로 한 번씩 호출합니다. provider, relay, message, publication client는
구성하거나 실행하지 않습니다.

실행기는 Management API에서 직전에 읽기 전용 확인한 parent Production ref와
child Preview branch ref를 각각 `--parent-project-ref`,
`--expected-branch-ref`로 받아야 합니다. direct host는 정확히
`db.<child-ref>.supabase.co:5432`여야 하고, host ref가 parent ref와 같거나
승인된 child ref와 다르면 environment fence를 seed하기 전에 즉시
중단합니다. pooler·별칭 host나 ref를 DB에서 스스로 추정하는 실행은
허용하지 않습니다.

Squid `content_source`는 Preview에 복제된 다음 자연 원장과 exact hash가
일치해야 합니다.

- current `needs_review` content version, `daily_news`
- 자연 `official_x_review_draft_completed` Grok QA outbox row
- canonical `https://x.com/SquidRouter/status/<id>`
- `x_post_text`, media 없음, 비어 있지 않은 본문
- source body SHA-256과 immutable content/outbox binding 일치

64-signal race 뒤에는 동일 plan/stage의 concurrent exact replay와 서로 다른
receipt ID conflict도 각각 실행합니다. exact replay는 하나의 동일 receipt로
수렴해야 하고 conflict는 row delta 0으로 거절되어야 합니다. 이어 wrong
client/workspace/lane/role, future/expired JWT, service role, self-review, stage
순서 위반, signal/hash/source 변조를 실제 signed-JWT PostgREST 경로에서 모두
거절하는지 확인합니다. 마지막으로 receipt 만료와 current source-version
변경을 각각 재현해 round·inbox·dashboard count가 즉시 projection에서
제외되는지 검증합니다.

DB race만으로 connector attestation 성공을 주장하지 않습니다. 실제 JWT
runner는 child publishable key와 Legacy JWT secret을 고정된 환경변수에서 한 번
읽은 즉시 환경에서 제거하고, secret을 파일·stdout·subprocess에 전달하지
않습니다. 모든 connector write는 child URL의 PostgREST RPC를 통과해야 합니다.

## 관측 가능한 성공 기준

다음 조건을 **모두** 하나의 redacted receipt에 기록해야 성공입니다.

1. 64개 호출이 모두 같은 idempotency 결과로 수렴합니다. 정확히 하나만
   `reused=false`, 63개는 `reused=true`이며 signal/connector receipt ID와
   SHA-256이 같습니다. timeout이나 commit-unknown은 재시도하지 않고 실패로
   처리합니다.
2. 64-call phase의 물리 row delta는 `harmony_signals +1`,
   `harmony_connector_attestation_receipts +1`뿐입니다. 다른 고객 row delta와
   round/plan/stage/inbox delta는 0입니다.
3. 최종 Squid row 수는 signals 4, connector receipts 4, rounds 1, plans 1,
   stage receipts 5, pending operator inbox 1입니다. 중복 key/group은 0입니다.
4. stage order와 ordinal은 정확히
   `plan(1) -> private_content(2) -> independent_qa(3) -> operator_inbox(4)
   -> recap(5)`입니다. 각 input SHA는 직전 output SHA와 같고 receipt hash도
   직전 receipt에 연결됩니다.
5. QA principal은 plan/content principal과 다르고, verdict는 `passed`이며,
   operator inbox는 그 QA receipt와 output hash에 정확히 묶여 있습니다.
6. private headline/summary에는 비어 있지 않은 한국어가 있고 factual source
   binding이 일치합니다. operator inbox는 `pending`, operator decision은 0,
   publication은 0입니다.
7. recap은 `actual_cost_microusd=0`, `publication_count=0`,
   `operator_decision_observed=false`입니다. `미관측` metric을 0으로 바꾼
   흔적이 없어야 합니다.
8. `get_preview_harmony_dashboard`와 `GET /api/harmony/dashboard`가 같은 exact
   workspace/Squid projection을 반환합니다. response schema는
   `harmony-preview-dashboard@1`, 최신 상태는 `operator_review_pending`, flags는
   read-only true와 네 side-effect false입니다.
9. 다른 client/workspace JWT, 다른 lane capability, 만료/future JWT,
   service_role, 자기 QA, stage 순서 위반, hash 변조는 모두 fail-closed이고 row
   delta 0입니다.
10. Production DB/Netlify/Railway, provider, Buzz, Telegram, X, approval,
    publication의 delta는 모두 0입니다. 관측 불가능한 항목은 0으로 쓰지 않고
    `미관측`으로 기록합니다.

## 중단과 롤백

다음 중 하나면 즉시 중단합니다: cross-client read/write, 64-call 불수렴,
unexpected row, dashboard 계약 불일치, 401/403 이외의 auth 우회, provider 또는
message client 접근, Production SHA/config 변화, publication/approval 생성,
비밀 노출.

롤백 순서는 고정합니다.

1. Deploy Preview의 `HARMONY_DASHBOARD_PREVIEW_ENABLED=false`를 적용하고 OFF
   deploy가 ready인지 확인합니다.
2. `SUPABASE_HARMONY_DASHBOARD_KEY`와 이번 검증에서 새로 추가한 branch 전용
   환경변수만 Deploy Preview context에서 제거합니다. 기존 Production 변수는
   건드리지 않습니다.
3. Draft PR은 별도 close 승인이 없으면 그대로 두고, route가 데이터를
   반환하지 않는 OFF 상태인지 확인합니다.
4. row-by-row 정리 대신 disposable Supabase Preview branch 전체를 삭제합니다.
5. branch 목록에서 ref가 사라지고 추가 과금이 멈췄는지 확인합니다.
6. Production exact SHA/config, publication/approval/Buzz/provider delta 0을
   읽기 전용으로 재확인합니다.
7. 비밀 없는 최종 receipt에 branch ref, 삭제 시각, 64-call 분포, row counts,
   round/plan/inbox ID, receipt SHA-256, 비용, 모든 side-effect delta를 기록합니다.

Branch 삭제는 성공·실패와 관계없이 같은 작업 창 안에서 수행합니다. 이
Preview 성공은 Production 적용, 다른 고객 연결, 실제 승인 또는 발행 권한을
부여하지 않습니다.

## 로컬 사전 검증 체크리스트

- [ ] exact branch/head와 dirty worktree 목록을 receipt에 기록
- [ ] 여섯 마이그레이션과 Netlify adapter의 계약 키가 완전히 일치
- [ ] 관련 Python/JavaScript/SQL security 테스트 통과
- [ ] 임시 PostgreSQL에서 전체 마이그레이션과 64-connection harness 통과
- [ ] `git diff --check` 통과
- [ ] 자동 발행/provider/Buzz/publication/Production adapter가 코드 경로에 없음
- [ ] Gate 1~5의 별도 승인 문구와 rollback 담당자 준비
