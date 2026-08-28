# Harmony disposable Preview 검증 런북

## 목적과 고정 경계

이 런북은 **disposable Supabase Preview branch 한 곳**에서 고객별 typed
signal, RLS 원장, connector attestation, Squid 한 고객의 private 협업
라운드, 그리고 Netlify Deploy Preview의 읽기 전용 운영실을 검증할 때만
사용합니다.

```text
Quiz aggregate ─┐
Community ops ──┼─> registered connector -> signed request receipt
Official source ┤                                      |
Recap aggregate ┘                                      v
             plan -> private_content
                         |
                         v
              Codex-first fixed QA specialist
              prepare -> claim -> start(one execute)
                      -> submit -> verify
                         | pass (same transaction)
                         v
              independent_qa -> operator_inbox(pending) -> recap
                         |
                         + needs_changes/blocked/outcome_unknown -> terminal
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

아홉 번째 durable Codex gate migration과 64-way DB proof 자체는 dashboard를
포함한 어떤 feature flag도 켜지 않습니다. 이 범위에서는 모든 flag가 OFF이며,
아래 Gate 4의 짧은 dashboard 관측은 별도 승인·별도 실행으로만 가능합니다.

## 현재 계약 스냅샷

아래 이름은 2026-08-27의 작업 트리에서 읽은 **미병합 계약**입니다. 실행
직전에 exact Git SHA에서 다시 확인해야 하며, 이름이나 응답 shape가 바뀌면
이 런북보다 코드를 우선하지 말고 검증을 중단해 문서를 함께 갱신합니다.

### Preview 마이그레이션 순서

Preview 전용 원장은 같은 exact commit의 공통 함수와 role closure에
의존하므로 아래 **아홉 파일 전체**를 각각 SHA-256으로 고정해 순서대로
적용합니다. 전체 repository migration을 밀어 넣는 unconstrained `db push`는
사용하지 않습니다.

1. `20260825130000_agent_work_order_ledger.sql`
2. `20260825131000_agent_work_order_roles.sql`
3. `20260825132000_harmony_preview_collaboration.sql`
4. `20260825133000_harmony_preview_vertical_slice.sql`
5. `20260825134000_harmony_preview_stage_chain.sql`
6. `20260825135000_harmony_preview_dashboard_roles.sql`
7. `20260825140000_harmony_preview_fixed_specialist_chain.sql`
8. `20260826210000_harmony_preview_trust_hardening.sql`
9. `20260827220000_harmony_preview_codex_gate_durable.sql`

공개 RPC와 허용 role은 다음과 같습니다.

| RPC | 허용 role | 효과 |
|---|---|---|
| `submit_preview_harmony_signal(uuid,text,uuid,jsonb)` | `coineasy_harmony_connector` | registration·signed request nonce를 검증하고 signal, connector receipt, request receipt를 원자적으로 추가 |
| `create_preview_harmony_squid_plan(uuid,text,uuid,uuid,uuid,text[],text)` | `coineasy_harmony_orchestrator` | 고정 `squid_planner` principal만 Squid plan, round, plan-stage receipt 생성 |
| `append_preview_harmony_squid_stage(uuid,text,uuid,uuid,text,uuid,uuid,jsonb)` | `coineasy_harmony_content`, `coineasy_harmony_operator`, `coineasy_harmony_recap` | 사전 결속된 specialist만 자신의 다음 stage 하나 추가. positive QA의 generic append 권한은 제거되며 stage 4만 pending inbox를 생성 |
| `record_preview_harmony_squid_qa_denial(uuid,text,uuid,uuid,uuid,jsonb)` | `coineasy_harmony_qa` | 유효한 `failed` QA만 append-only denial로 추가하고 inbox/Recap을 만들지 않음 |
| `prepare_preview_harmony_squid_codex_qa(uuid,text,uuid,uuid,bigint)` | `coineasy_harmony_qa` | DB의 현재 signal manifest·source lineage·fixed reviewer binding으로 stable work/request를 준비 |
| `claim_preview_harmony_squid_codex_qa(uuid,text,integer)` | `coineasy_harmony_qa` | 준비된 한 work를 최대 15분 lease로 claim하고 claim fence를 append |
| `start_preview_harmony_squid_codex_qa_attempt(uuid,text,text,text)` | `coineasy_harmony_qa` | 정확한 claim fence에서 첫 호출 한 건만 `execute_authorized=true`; replay는 절대 실행 권한을 주지 않음 |
| `submit_preview_harmony_squid_codex_qa_result(uuid,text,text,text,jsonb,text,text[],text)` | `coineasy_harmony_qa` | exact attempt fence에 typed semantic evidence와 result receipt를 한 번만 append |
| `verify_preview_harmony_squid_codex_qa_result(uuid,text,text)` | `coineasy_harmony_qa` | 결과를 검증하고, `pass`일 때만 verification·positive stage·stage link를 같은 transaction에 생성 |
| `reconcile_preview_harmony_squid_codex_qa_lease(uuid,text,integer)` | `coineasy_harmony_qa` | 실행 전 만료 claim만 최대 3회 범위에서 해제; attempt 이후 무결과는 terminal `outcome_unknown`으로 고정 |
| `get_preview_harmony_round(uuid,text,uuid)` | `coineasy_harmony_orchestrator`, `coineasy_harmony_operator` | 완성된 private round 조회 |
| `get_preview_harmony_dashboard(uuid,text)` | `coineasy_harmony_dashboard` | 읽기 전용 운영실 projection 조회 |

`coineasy_harmony_qa`의 Harmony public RPC execute-set은 위의 durable gate
여섯 함수와 별도 negative receipt용
`record_preview_harmony_squid_qa_denial`뿐입니다. 기존
`append_preview_harmony_squid_stage` execute는 명시적으로 revoke합니다.
`public`, `anon`, `authenticated`, `service_role`과 다른 Harmony role은 durable
gate 여섯 함수를 실행할 수 없습니다. positive `independent_qa` insert에는
verified durable result를 요구하는 table trigger도 적용되어 DB owner를 통한
우회 insert까지 fail-closed합니다.

여섯 durable gate RPC는 tenant row를 읽기 전에 JWT claim만으로
workspace/client/role/capability/environment/zero-authority policy를 preflight하고,
동일한 `workspace_id + client_id` advisory transaction lock을 잡습니다. 그 뒤에만
run/request/round row를 lock하며, immutable request에 고정된 reviewer assignment는
두 번째 gate로 다시 확인합니다. 이 공통 순서는 교차 RPC lock cycle과 다른
고객의 queue 존재 여부를 이용한 oracle을 함께 차단합니다.

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
DB owner가 seed하는 고정 roster는 stage마다 principal 한 명만 허용하고,
principal/release/config/Preview ref/만료 시각을 하나의 immutable binding SHA로
결속합니다. 환경 fence와 specialist binding TTL은 DB constraint에서도 최대
2시간이며, runtime이 모델을 골라 배정하는 경로는 없습니다.
이 one-shot Preview branch에서는 lane, connector ID, producer principal,
attestation key ID가 각각 all-time unique입니다. 새 registration UUID를 이용한
sibling 우회나 암묵적 renewal은 허용하지 않습니다. Connector JWT의 정수 초
`iat`도 registration 생성 초보다 빠를 수 없습니다.
P0의 durable `harmony_independent_qa` harness는 실제 Codex/Grok 모델 호출을
하지 않습니다. Codex-first 고정 QA principal이 DB가 산출한 source lineage와
prior-output SHA에 결속된 typed semantic evidence를 제출하는 계약만
검증합니다. `pass`만 positive stage를 만들며 `needs_changes`와 `blocked`는
verification receipt를 남기고 downstream stage를 만들지 않습니다. attempt가
시작된 뒤 receipt 없이 lease가 만료되면 `outcome_unknown`이고 자동 retry하지
않습니다. 기존 deterministic `failed` denial 원장은 별도 negative 경로로
유지합니다. 잘못된 요청과 인증 실패는 row를 만들지 않습니다.

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
SHA에서 아홉 migration의 disposable PostgreSQL 적용, 기존 Harmony security
suite와 `harmony_preview_codex_gate_security.sql`,
`scripts/probe_harmony_preview_concurrency.py`의 64개 실제 DB connection 결과,
그리고 `scripts/probe_harmony_preview_postgrest.py`의 실제 서명 JWT/PostgREST
64-way connector 결과가 모두 성공해야 합니다. dashboard 응답은
`counts.pending_operator_inbox`,
`trust.client_scope_verified=true`, `trust.portable_trust=false`를 정확히
사용하며, revocation으로 non-current가 된 round는 projection과 count에서
즉시 제외되어야 합니다. 이 중 하나라도 확인되지 않으면 Gate 1에서
`BLOCK`입니다.

현재 문서 시점에는 새 durable gate가 fresh disposable local PostgreSQL 16에서
아홉 migration, 24개 격리 SQL security/least-privilege suite, 기존 Squid
recovery concurrency test, 64-way durable QA 분포, Python 1,970개, Netlify 함수
404개를 통과했습니다. 다만 clean exact-SHA Supabase Preview 성공 receipt는
아직 없으므로 **Preview proof pending**입니다. 로컬 증거를 live Preview 또는
Production 증거로 대체해 주장하지 않습니다.

## 승인 게이트

각 gate는 별도 승인입니다. 이전 gate 승인이 다음 gate 권한을 포함하지
않습니다.

### Gate 1 — 유료 Preview branch

실행 직전에 Supabase의 현재 branch 시간당 비용을 조회해 조직, Production
project ref, 예상 TTL, 최대 예상액과 함께 대표에게 제시합니다. 대표가 해당
금액과 branch 생성을 명시적으로 승인하기 전에는 생성하지 않습니다.
Branch는 clean exact HEAD를 이름과 receipt에 고정한 non-persistent Small
disposable child 한 곳만 만들며 `with_data=false`를 사용합니다. migration과
probe는 그 exact commit의 immutable checkout에서만 읽습니다. 실패한 child를
수리하거나 다른 child로 자동 교체하지 않고 즉시 삭제한 뒤 별도 승인을
받습니다. 2시간 TTL은 비상 상한이며 정상·실패 모두 검증 직후 바로
삭제합니다.

실행기는 create mutation 전에 unique branch name과 생성 시점 기준 110분
absolute-deadline cleanup watchdog을 먼저 arm합니다. create 응답의 id/ref가
유실되어도 exact name으로 child를 찾아 삭제하고, main cleanup이 id/ref 또는
name 부재를 연속 3회 확인하기 전에는 watchdog을 해제하지 않습니다. 또한
create 인자 자체를 증거로 쓰지 않습니다. BranchResponse의
`preview_project_status=ACTIVE_HEALTHY`와 terminal migration lifecycle을 함께
확인하고, `persistent=false`, `with_data=false`를 확인한 뒤 Management API의
exact-child `GET /v1/projects/{child_ref}/billing/addons`에서
`selected_addons[].type=compute_instance`와 exact `variant.id=ci_small`을 실제
확인합니다. scoped PAT은 branch lifecycle 최소 권한과
`infra_add_ons_read`, `api_gateway_keys_read`만 가지며, 비용이 발생하는 create
전에 billing add-ons endpoint를 parent ref로 GET해 compute read 권한과 최소
envelope shape만 preflight합니다. Production API-key metadata나 값은 읽지
않습니다. API-key read 권한은 cleanup watchdog이 보호하는 child가 생성된 뒤
그 exact child에서 처음 확인합니다. Redirect는 전부 거부하고
401/403·unknown variant·중복·shape drift는 즉시 fail-closed합니다.
Child가 ready 직후 일시적으로 404/429/5xx, transport 실패 또는 compute add-on
미노출 상태인 경우에만 기존 readiness deadline 안에서 GET을 재시도하며,
branch를 수리하거나 다시 생성하지 않습니다.

Child credential은 복합 `supabase branches get` 대신 Management API의 세
read-only 요청으로만 가져옵니다. 먼저 exact
`GET /v1/branches/{child_ref}`에서 direct DB password와 Legacy JWT secret을
읽고 ref·ACTIVE_HEALTHY·`db.{child_ref}.supabase.co:5432`·`postgres` principal을
고정 검증합니다. 다음으로 exact
`GET /v1/projects/{child_ref}/api-keys?reveal=false`에서 `type=publishable`,
`name=default`인 canonical UUID 하나만 고른 뒤, 그 ID 하나에만
`GET /v1/projects/{child_ref}/api-keys/{id}?reveal=true`를 호출합니다.
`secret`, `service_role`, legacy anon 값은 선택하거나 reveal하지 않습니다.
Pooler endpoint는 호출하지 않으며 `database_pooling_config_read` 권한도 요구하지
않습니다. 세 raw response는 성공·부분 실패 모두 재귀적으로 즉시 비웁니다.

두 probe와 config는 migration과 함께 exact SHA blob을 메모리에 한 번만
snapshot합니다. Probe는 mutable checkout이나 임시 source file을 다시 열지 않고
`python -I -`의 stdin으로 해당 bytes를 실행하며, PostgREST probe가 사용하는
concurrency module도 같은 메모리 bundle에 결속합니다. 모든 command는 독립
process group으로 실행합니다. Timeout, runner SIGINT/SIGTERM, nonzero 종료 시
그룹 전체에 `TERM`과 `KILL`을 각각 한 번만 보내고 direct child를 wait/drain한
뒤, bounded read-only process snapshot으로 live member가 없음을 확인하기 전에는
cleanup 성공으로 기록하지 않습니다. PGID가 사라졌거나 zombie-only인 경우만
quiescent로 인정하며, live/unknown은 fail-closed합니다. 확인 단계에서는 재사용될
수 있는 PGID에 신호를 다시 보내지 않습니다. `pthread_sigmask`는 호출 thread만
막으므로 foreground command는 callback 전부터 `HANDOFF`, `OWNED`, `FENCING`,
`RESTORING` phase-aware Python handler도 함께 설치합니다. HANDOFF의 첫 interrupt는
bounded slot에만 기록하고, OWNED에서 cleanup으로 이동하며, FENCING/RESTORING의
추가 interrupt는 coalesce합니다. 그룹 fence와 두 handler 복구가 끝난 뒤에만
원래 caller handler를 replay합니다. Watchdog의 Supabase CLI도
별도 session과 signal guard를 가지며, foreground management HOME과 분리된 mode
`0700` control root 및 임시 HOME을 사용합니다.

정상 cleanup 뒤 watchdog 취소는 nonce-bound local socket의
`CANCEL -> CLEAN_READY -> ACK_ACCEPTED` 3단계 handshake로만 확정합니다.
`CLEAN_READY`는 활성 CLI PGID가 `null`이고, 마지막 CLI PGID의 read-only 부재
또는 zombie-only quiescence 확인과 control root 삭제가 끝난 경우에만 유효합니다.
Parent는 그 증거를 직접
재검증하고 ACK한 뒤 watchdog 종료 및 watchdog PGID 부재까지 확인해야
`watchdog_cancelled=true`와 `watchdog_secret_released=true`를 기록합니다.
메시지 누락·nonce/스키마 불일치·활성 child·root 잔존·PGID 확인 실패는 모두
fail-closed이며 secret release를 성공으로 기록하지 않습니다. Create identity가
불명확하면 socket EOF로 취소하지 않고 watchdog이 absolute deadline까지 exact-name
late visibility를 감시합니다. Signal-mask 실패 시에도 unmasked KILL을 시도하고,
process-group 미확인은 mask 복구 오류보다 우선합니다. 각 SHA-256만 redacted
receipt에 남깁니다.

승인 receipt에 다음을 기록합니다.

- exact Git SHA와 아홉 마이그레이션 SHA-256
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
Connector JWT는 추가로 `attestation_registration_id`,
`attestation_key_id`, `request_nonce`(`jti`와 동일),
`request_sha256`를 포함합니다. `request_sha256`는 DB와 동일한
domain/RPC/workspace/client/registration/logical signal/payload binding으로 계산하며,
JWT timestamp나 nonce를 바꿔 동일 논리 요청을 갱신할 수 없습니다.
Dashboard JWT는 role `coineasy_harmony_dashboard`만 사용합니다. Netlify
Deploy Preview에는 scoped dashboard JWT만 bearer로 저장하며
`SUPABASE_SERVICE_ROLE_KEY`로 대체하지 않습니다.

### Gate 3 — Preview DB 적용

별도 승인 후 아홉 마이그레이션을 **Preview branch ref를 재확인한 연결**에만
순서대로 적용합니다. DB owner가 `private.harmony_preview_environment_fence`
에 exact branch ref와 짧은 만료 시각을 한 번 seed합니다. 이 table은
immutable하므로 오류 시 row를 수정/삭제하지 않고 branch 전체를 삭제합니다.

적용 직후, E2E 입력 전 아래를 읽기 전용으로 확인합니다.

- 모든 Harmony 원장, environment fence, specialist roster table에 RLS와
  FORCE RLS 활성
- Harmony role의 모든 직접 table/view/sequence grant 0
- public/anon/authenticated/service_role의 Harmony 원장 table 직접 grant 0
- service_role의 열두 공개 Harmony RPC execute grant 0
- 각 Harmony role의 execute-set이 위 표와 정확히 일치. 특히
  `coineasy_harmony_qa`는 durable gate 여섯 RPC와 denial RPC만 실행 가능하고
  generic stage append는 실행 불가
- Codex gate append-only relation 10개와 mutable run projection에 RLS와 FORCE
  RLS가 켜져 있고 직접 table/view/sequence grant는 0
- unverified `independent_qa` 직접 insert가 table trigger에서 차단되고,
  verified `pass`만 stage receipt와 durable stage link를 만들 수 있음
- role 특권과 상호 role membership 0. 런타임 assume edge는
  `authenticator`의 `SET=true`, `ADMIN=false`, `INHERIT=false` 한 건뿐입니다.
  Supabase PostgreSQL 16이 custom role 생성자 `postgres`에 자동으로 남기는
  `ADMIN=true`, `SET=false`, `INHERIT=false` 관리 edge는 허용하되 런타임
  assume 권한으로 간주하지 않으며, 그 밖의 principal edge는 0이어야 합니다.
- environment fence는 exact Preview ref 한 건, active, 미만료
- 고정 specialist roster는 Squid 5단계와 정확히 일치하고 principal 5개가
  모두 다르며, 각 binding과 fence의 남은 TTL이 2시간 이내
- Harmony signal/receipt/round/plan/stage/inbox 및 Codex gate
  lineage/request/run/transition/claim/attempt/evidence/result/verification/
  reconciliation/stage-link row 모두 0. Connector
  registration은 owner가 exact branch/workspace/client/lane/capability/principal/
  release/config/key-id/expiry를 결속해 seed하며, revocation/request/QA-denial
  receipt는 0
- Production 연결과 Production row delta 0

### Gate 4 — Netlify Deploy Preview

별도 승인 후 exact Git SHA의 Deploy Preview에만 branch URL, publishable
key, scoped dashboard JWT, workspace ID와 exact commit fence를 설정합니다.
이 gate는 durable Codex QA DB proof의 필수 단계나 암묵적 권한이 아닙니다.
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
그 뒤 나머지 세 typed signal을 각각 한 번 기록하고 plan과 private-content를
각각 64-way로 race합니다. QA는 generic stage append가 아니라 다음 고정 순서를
각 단계마다 64개 독립 connection으로 검증합니다.

```text
prepare -> claim -> start -> submit -> verify
```

- prepare: `reused=false` 1, `reused=true` 63
- claim: `claimed=true` 1, `claimed=false` 63
- start: `execute_authorized=true` 1, replay non-authorizing 63;
  동시에 `reused=false` 1, `reused=true` 63
- submit: `reused=false` 1, `reused=true` 63
- verify: `reused=false` 1, `reused=true` 63, positive QA stage 1,
  durable stage link 1

verify의 pass transaction이 stage 3을 만든 뒤 operator-inbox와 Recap을 각각
64-way로 이어갑니다. 각 race는 새 receipt UUID와 새 short-lived JWT `jti`를
쓰되 동일한 specialist binding과 logical input을 사용합니다. `start`의 한
건 실행 허가는 durable authorization proof일 뿐 이 harness에서 실제 Codex나
provider를 호출한다는 뜻이 아닙니다. provider, relay, message, approval,
publication client는 구성하거나 실행하지 않습니다.

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

64-signal race 뒤에는 동일 plan/stage의 concurrent stable replay와 logical
identity conflict를 각각 실행합니다. 새 receipt UUID나 갱신 JWT는 transport
metadata이므로 하나의 기존 receipt로 수렴해야 합니다. 다른 round/plan/topic,
QA evidence 또는 inbox identity는 conflict로 row delta 0 거절되어야 합니다.
이어 connector 경계의 wrong client/workspace/lane/role, future/expired JWT,
service role, quiz signal payload/hash 변조는 실제 signed-JWT PostgREST 경로에서
거절하는지 확인합니다. official source의 content/outbox binding과 source hash
변조는 direct-PG security suite에서 검증하며, 아직 signed-JWT PostgREST 경로의
source mutation proof로 주장하지 않습니다. plan/stage 경계의 self-review,
specialist principal 재사용, stage 순서 위반과 logical identity conflict도 동일
claims를 주입한 direct-PG transaction probe로 검증합니다. 이 결과는 stage RPC의
실제 PostgREST 검증으로 과장하지 않습니다. 현재 harness는 registration
revocation과 별도 content item의 current source-version drift를 모두 runtime으로
재현합니다. 후자는 submit 완료 뒤 version을 supersede하고 64개 reconciler가
`result_not_current` receipt 1개와 typed no-op 63개로 수렴하는지 확인합니다.
Receipt expiry는 후속 disposable Preview 검증 항목이며, 실제로 재현하기 전에는
성공 receipt에 검증 완료로 기록하지 않고 `미관측`으로 남깁니다.

DB race만으로 connector attestation 성공을 주장하지 않습니다. 실제 JWT
runner는 위 exact-child Management API 응답에서 publishable key와 Legacy JWT
secret을 메모리로 한 번 읽고 raw 응답을 즉시 비웁니다. 필요한 값은 단 한 번의
격리된 PostgREST probe process 환경에만 전달하며, 파일·stdout·다른 subprocess로
전달하지 않습니다. 모든 connector write는 child URL의 PostgREST RPC를 통과해야
합니다.

## 관측 가능한 성공 기준

다음 조건을 **모두** 하나의 redacted receipt에 기록해야 성공입니다.

1. signal, plan, private-content, operator-inbox, Recap의 각 64개 호출과
   Codex QA의 prepare/submit/verify가 같은 idempotency 결과로 수렴합니다.
   각 race마다 정확히 하나만 `reused=false`, 63개는 `reused=true`입니다.
   claim은 1 claimed/63 not-claimed, start는 1 execute-authorized/63
   replay-non-authorizing이어야 하며 모든 응답은 같은 stable identity와 fence를
   반환합니다. timeout이나 commit-unknown은 재시도하지 않고 실패로 처리합니다.
2. 첫 `quiz_learning` signal의 64-way race에서 물리 row delta는
   `harmony_signals +1`, `harmony_connector_attestation_receipts +1`,
   `harmony_preview_connector_request_receipts +1`뿐이고
   round/plan/stage/inbox 및 Codex gate delta는 0입니다. 이후 plan race는
   round/plan/stage receipt를 각각 `+1`, private-content는 stage receipt `+1`입니다.
   durable QA success path는 lineage/request/run `+1`, transition `+6`,
   claim/attempt/evidence/result/verification/stage-link `+1`, reconciliation `+0`,
   positive QA stage receipt `+1`로 수렴합니다. operator-inbox race는 stage
   receipt와 inbox를 각각 `+1`, Recap race는 stage receipt만 `+1`이고 inbox
   delta는 0이어야 합니다. 각 phase에서 다른 고객 row delta는 0입니다.
3. 성공 경로가 끝나고 QA-denial 및 revocation 서브테스트를 시작하기 전
   Squid row 수는 signals 4, connector receipts 4, connector request
   receipts 4, rounds 1, plans 1, stage receipts 5, pending operator inbox
   1입니다. Codex gate는 lineage/request/run 1, transition 6,
   claim/attempt/evidence/result/verification/stage-link 각 1,
   reconciliation 0이어야 합니다. 중복 key/group은 0입니다. 이후 denial 서브테스트는 별도의
   signal, connector receipt, request receipt, round, plan을 각각 1개와
   private-content stage 1개, QA denial 1개만 추가해야 합니다.
4. stage order와 ordinal은 정확히
   `plan(1) -> private_content(2) -> independent_qa(3) -> operator_inbox(4)
   -> recap(5)`입니다. 각 input SHA는 직전 output SHA와 같고 receipt hash도
   직전 receipt에 연결됩니다.
5. QA principal은 모든 signal/plan/content principal과 다르고, submitted
   verdict는 `pass`, stage verdict는 `passed`이며, operator inbox는 그 verified
   QA receipt와 output hash에 정확히 묶여 있습니다. positive QA stage는
   verify transaction과 stage-link trigger를 통해 원자적으로 생성되어야 하며
   generic append 경로에서는 생성할 수 없습니다.
   다섯 specialist principal은 모두 다르고 각 stage receipt는 exact
   specialist binding/release/config/operation key를 가집니다. stage 4의 inbox
   delta는 `+1`, stage 5 Recap의 inbox delta는 `0`입니다.
6. private headline/summary에는 비어 있지 않은 한국어가 있고 factual source
   binding이 일치합니다. operator inbox는 `pending`, operator decision은 0,
   publication은 0입니다.
7. recap은 `actual_cost_microusd=0`, `publication_count=0`,
   `operator_decision_observed=false`입니다. `미관측` metric을 0으로 바꾼
   흔적이 없어야 합니다.
8. `get_preview_harmony_dashboard`와 `GET /api/harmony/dashboard`가 같은 exact
   workspace/Squid projection을 반환합니다. response schema는
   `harmony-preview-dashboard@2`, 최신 상태는 `operator_review_pending`, flags는
   read-only true와 네 side-effect false입니다.
   각 stage projection은 specialist code, principal, release/config, binding SHA,
   operation key를 보존합니다. Netlify API는 다섯 principal의 전원 고유성과
   workspace/client/plan/stage/input/output/binding으로부터 operation key를 다시
   계산합니다. binding SHA 자체는 DB roster·fence join이 attestation한 opaque
   evidence이며, 브라우저는 same-origin API가 검증한 exact shape와 principal
   고유성만 다시 확인합니다.
9. 다른 client/workspace JWT, 다른 lane capability, 만료/future JWT,
   service_role, 자기 QA, stage 순서 위반, hash 변조는 모두 fail-closed이고 row
   delta 0입니다. 같은 nonce의 request/claim drift와 새 nonce로 동일
   logical digest를 갱신하는 요청도 typed conflict와 domain delta 0이어야
   합니다. Revocation 이후 신규 signal과 기존 round current projection은
   모두 차단되어야 합니다.
10. 유효한 `failed` QA 64-way race는 denial 하나와 63 reuse로
    수렴하고, passed-QA stage, operator inbox, Recap, approval, publication
    delta는 모두 0이어야 합니다. 거절된 output은 동일 plan에서
    나중에 `passed`로 바뀐 수 없습니다.
11. durable result의 `needs_changes`와 `blocked`는 terminal verification만
    남기고 positive stage/inbox/Recap을 만들지 않아야 합니다. attempt 시작 전
    만료 claim만 최대 3회 claim-release 대상이며, attempt 시작 뒤 result receipt
    없이 lease가 만료된 run은 `outcome_unknown` 한 건으로 고정되고 자동
    재실행·자동 retry·positive stage delta가 모두 0이어야 합니다. submit 뒤
    source version이 non-current가 된 run은 64-way reconcile에서 immutable
    `result_not_current` receipt 1개만 만들고 63개는 no-op이어야 하며,
    verification/stage-link/positive QA stage/operator inbox/Recap은 모두 0이어야
    합니다.
12. Production DB/Netlify/Railway, provider, Buzz, Telegram, X, approval,
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

`secrets_persisted=false`는 foreground management CLI의 token-bearing 임시 HOME
삭제와, 위 socket handshake가 증명한 watchdog 전용 HOME/control root·활성 CLI
live 후손 부재(숫자 PGID 부재 또는 zombie-only)·watchdog process group 부재가
모두 확인된 경우에만 기록합니다. 하나라도
확인되지 않으면 typed 값은 `null`(unconfirmed)이며 성공 receipt가 될 수
없습니다. create 결과에서 immutable child identity를 끝내 얻지 못한 경우에는
foreground가 watchdog을 취소하지 않고 exact-name late visibility 감시를 absolute
deadline까지 유지합니다.

Branch 삭제는 성공·실패와 관계없이 probe의 cleanup/finally 경로에서 즉시
수행하고, branch 목록에서 승인 ref 부재를 연속 확인합니다. 같은 작업 창
안의 2시간 TTL은 삭제 실패를 탐지하는 상한이지 유지 시간 목표가 아닙니다. 이
Preview 성공은 Production 적용, 다른 고객 연결, 실제 승인 또는 발행 권한을
부여하지 않습니다.

## 로컬 사전 검증 체크리스트

- [ ] exact branch/head와 dirty worktree 목록을 receipt에 기록
- [ ] 아홉 마이그레이션과 Netlify adapter의 계약 키가 완전히 일치
- [x] 관련 Python/JavaScript/SQL security 테스트 통과
- [x] 임시 PostgreSQL에서 전체 마이그레이션과 64-connection harness 통과;
      durable QA는 prepare 1/63, claim 1/63, start authorize 1/63,
      submit 1/63, verify 1/63 및 atomic stage/link 1을 확인
- [x] `git diff --check` 통과
- [ ] 자동 발행/provider/Buzz/publication/Production adapter가 코드 경로에 없음
- [ ] Gate 1~5의 별도 승인 문구와 rollback 담당자 준비

체크박스는 실행 receipt가 붙기 전까지 완료 표시하지 않습니다. 특히 이
문서의 durable gate 설명은 구현 계약이며, disposable Preview 실증 완료 선언이
아닙니다.
