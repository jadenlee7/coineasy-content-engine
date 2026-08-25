# CoinEasy AI 회사 Harmony 운영 가이드

## 한 문장으로 이해하기

각 고객 퀴즈봇·커뮤니티·콘텐츠·Recap 시스템은 자기 고객의 안전한
관측 claim만 공통 운영실에 제출합니다. 별도 신뢰 검증이 그 claim의
고객·역할·release·receipt를 확인한 뒤에만, 여섯 역할의 구조화 rehearsal이
대표에게 실행 전 기획안을 보여줄 수 있습니다.

```text
Quiz 학습 claim ─┐
Community claim ─┼─> attestation -> 고객별 6턴 rehearsal -> 대표 승인함
공식 소스 claim ─┤                                         |
Recap claim ─────┘                                         v
                                     별도 승인 뒤에만 실제 업무 원장
```

## 지금 보이는 네 고객

- Babylon
- OriginTrail
- Squid
- Yellow

중요: 이 저장소에서 확인된 것은 네 고객의 콘텐츠 설정과 익명 집계형 Quiz
신호 계약입니다. 외부 퀴즈봇의 실제 API, 토큰, 채팅방, 서비스 ID는 아직
Harmony에 연결하지 않았습니다. 운영실의 `*_quiz_bot`은 현재
`contract_only` 논리 역할입니다. 다만 scoped JWT claim을 DB가 직접 검증해
connector receipt를 만드는 Preview 계약과 보안 테스트는 로컬에서 구현되어
있습니다. 즉 **attestation 경로는 준비됐지만 실제 외부 봇 inventory는 아직**
입니다.

## 각 역할이 말할 수 있는 것

| 역할 | 운영실에 제출하는 내용 | 제출할 수 없는 내용 |
|---|---|---|
| Quiz | 20회/5명 이상 익명 학습 격차 | 질문·답·사용자·세션 원문 |
| Community Ops | 집계 관심도와 운영 관측 | DM, 사용자명, 지갑, 사실 주장 |
| Content Engine | 검증된 공식 소스 ID와 해시 | 출처 없는 카피 |
| Recap | 관측된 지표 또는 `미관측` | 미관측을 0으로 변환 |
| Grok Bot | 네 신호의 구조화 종합 | 실행·승인·발행 |
| Codex/QA | 계약·근거·격리 검증 | 자기 작업 승인 |
| 대표 | 방향 설정과 scope 채택 | 반복 로그 조사 |

## 대표가 보는 다섯 화면

1. 참여 고객과 역할, 신호 freshness
2. 각 에이전트 연결 상태
3. 고객별 여섯 턴 rehearsal과 차단 사유
4. 대표가 채택할 private handoff
5. 교차 고객 학습, 비용, 완료 상태

교차 고객 학습은 “이 주제를 여러 고객이 함께 어려워한다” 같은 운영 관행만
공유합니다. 고객 A의 카피, 공식 근거, 이미지, 브랜드 표현을 고객 B에게
복사하지 않습니다.

## 로컬 화면 실행

현재 live adapter 없이 네 고객의 연결 준비 상태를 확인합니다.

```bash
PYTHONPATH=. python -m scripts.run_agent_harmony \
  --input examples/agent-harmony-empty-input.json \
  --clients-dir clients \
  --observed-at 2026-08-25T12:00:00Z \
  --dashboard
```

로컬 CLI의 trust registry는 항상 비어 있습니다. 신호 claim이 없어도
`신호 대기`, claim이 있어도 attestation 없이는 `attestation 대기`이며
대표 승인 대상은 0건입니다. 이것이 정상입니다. producer/client/release/
receipt 값을 JSON에 적었다는 이유만으로 실제 봇 발언으로 믿지 않습니다.
화면에 보이는 snapshot과 handoff도 서명된 명령이 아니라 render-only
Preview 자료입니다. 다른 프로세스에서 실행 입력으로 사용할 수 없고,
향후 승격 시 trusted registry가 attestation을 다시 검증해야 합니다.

## 자동 회사로 가는 순서

로컬 P0는 다음까지 통과했습니다.

- 네 고객의 typed signal 계약과 고객별 scope
- DB가 scoped JWT에서 직접 만드는 connector attestation receipt
- FORCE RLS, 직접 table grant 0, 역할·capability별 RPC 분리
- 64개 독립 DB 연결의 동일 입력 exactly-once: 신규 1, exact reuse 63
- Squid 한 곳의 `기획 -> private 콘텐츠 -> 독립 QA -> 대표 승인함 -> Recap`
- CoinEasy News Card의 GET-only Harmony 운영실 화면
- 원장상 external/provider 비용 0, 외부/provider/Buzz/승인/publication 호출 0,
  자동 발행 OFF. 실제 disposable Preview 인프라 비용은 아직 미관측이며 별도
  비용 승인·영수증으로 관리합니다.

여기서 `독립 QA`는 현재 **별도 role/JWT와 고정 조건을 쓰는 구조 QA**입니다.
실제 Codex·Grok 모델이 콘텐츠 의미를 평가한 것이 아니며, 실패 verdict를
보존하는 별도 원장도 Production 전에 추가해야 합니다. 화면도 이를 구조 QA
receipt와 실행 불가능 Preview 제안으로만 표시합니다.

다음 순서는 아래와 같습니다.

1. 시간당 비용·조직·TTL을 다시 확인하고 disposable Supabase Preview 한 곳을
   별도 승인으로 생성합니다.
2. 이미 로컬에서 통과한 마이그레이션·RLS·64동시성·Squid 수직 테스트를 실제
   Preview auth 경로에서 재현합니다.
3. exact SHA의 Netlify Deploy Preview에서 Harmony 탭을 읽기 전용으로 확인한
   뒤 즉시 flag OFF, 비밀 제거, Preview branch 삭제까지 한 창에서 끝냅니다.
4. 외부 Quiz 봇 네 개의 실제 connector inventory를 확정하고 Squid 한 곳의
   aggregate-only connector부터 교체합니다.
5. 20회 clean run 후 다음 고객을 한 명씩 연결합니다. 공개 발행은 기존
   exact-version human gate를 계속 사용합니다.

현재 단계에서 Production, DB, provider, Buzz, 외부 봇 메시지, publication은
변경하거나 호출하지 않습니다. 자동 발행은 계속 OFF입니다.
