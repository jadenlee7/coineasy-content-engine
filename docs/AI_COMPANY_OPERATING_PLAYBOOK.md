# CoinEasy AI 회사 운영 플레이북

## 대표가 할 일

대표는 네 가지만 결정합니다.

1. 이번 주 목표
2. 우선순위와 예산
3. 에이전트가 올린 승인 요청
4. 예외와 중단 여부

진행 상황 복사, 반복 조회, 작업자 독촉, 결과 포맷 정리는 에이전트와
Control Plane이 맡습니다.

## 직원 배치

- **Grok Bot — Chief of Staff:** 오늘 상황, 승인할 것, 막힌 것과 추천을
  쉬운 한국어로 설명합니다.
- **Buzz — 운영 감사관:** 전달, 승인, 장애, 완료 영수증을 남깁니다.
- **Devin — 비동기 개발자:** 명세가 확정된 긴 작업을 하나의 브랜치에서
  구현하고 Draft PR로 돌려줍니다.
- **Claude Code — 페어 개발자:** 사람과 함께 탐색해야 하는 디버깅과 빠른
  수정에 사용합니다.
- **Codex — CTO/릴리즈 담당:** 설계, 보안, 독립 리뷰, DB와 배포 검증을
  담당합니다.
- **Grok Build — 프로토타이퍼:** UI 아이디어와 Preview 시제품까지만
  만듭니다.
- **Railway/Supabase — 운영 시스템:** 스케줄, 원장, 비용, 중복 방지와
  상태 전이를 담당합니다.

## 대표 화면

Grok에는 아래 다섯 줄만 보여줍니다.

```text
오늘 회사 상태
내가 결정할 것
진행 중인 고객 업무
막힌 일과 추천 해결책
오늘 비용과 위험
```

각 승인 요청은 다음 형태입니다.

```text
무슨 일이 있었는지
에이전트의 추천
추천 근거
예상 비용과 위험
[승인] [수정] [보류]
```

## 개발 업무 흐름

```text
Grok이 문제와 추천을 설명
→ 사람이 work order 승인
→ 한 명의 코딩 에이전트가 한 브랜치 소유
→ Draft PR와 테스트 결과 제출
→ 다른 에이전트가 읽기 전용 검증
→ 사람의 병합·Production 승인
→ Codex가 exact SHA 배포와 런타임 검증
→ Buzz가 완료 영수증 전달
```

라우팅 기준:

- 긴 비동기 작업: Devin
- 실시간 탐색과 디버깅: Claude Code
- UI 시제품: Grok Build
- 교차 시스템·보안·DB·릴리즈: Codex

## 콘텐츠 업무 흐름

```text
공식 소스 발견
→ 근거 고정
→ 한국 GTM 초안·배너 생성
→ 독립 QA
→ Grok이 대표에게 설명
→ 승인·수정·보류
→ Buzz가 결정 영수증 기록
→ 게시 준비
→ 사람 최종 확인
→ 게시와 성과 기록
```

자동 발행은 충분한 clean run과 별도 승인 전까지 OFF입니다.

## 회사가 스스로 개선되는 방식

무제한 자기 수정이 아니라 제한된 실험 루프를 사용합니다.

```text
관측 → 문제 정의 → 개선 제안 → Preview 실험 → 측정
     → 사람 승인 → 제한 배포 → 유지 또는 롤백
```

에이전트는 비용 한도, Production 권한, credential scope, 승인 정책,
자기 평가 기준을 스스로 바꿀 수 없습니다.

## 대표 KPI

가장 중요한 수치는 **검증된 결과 한 건당 대표 개입 시간**입니다.

함께 봅니다.

- 사람 없이 검토 단계까지 도착한 비율
- 첫 검토 승인율
- 작업 cycle time과 작업당 비용
- 수정·차단·재시도 비율
- 중복 외부 행동 수: 0
- 권한 밖 행동 수: 0
- 영수증 완전성: 100%
- 콘텐츠 성과와 고객별 SLA

## 실행 순서

### Phase 0 — 지금

- 공통 `agent-work-order@1` 계약
- 한 작업에 한 명의 소유자
- Production·발행·paid call 0
- 실행 권한 없는 계획 패킷 세 건으로 범위·충돌·비밀·테스트 가능성 검증

Phase 0는 아직 연결된 24시간 회사가 아닙니다. 계획 형식을 검증하는
단계이며 에이전트는 파일 수정, branch push, PR 생성, Preview, 메시지,
provider 호출을 실행하지 않습니다.

샘플 계획 패킷은 명시한 저장소 루트 안의 일반 파일만 해시 검증합니다.

```text
PYTHONPATH=. .venv/bin/python -m scripts.run_agent_work_order \
  --input examples/agent-work-order-devin-preview.json \
  --repo-root . \
  --validate-only
```

### Phase 1 — 회사 운영실

- Supabase 업무 원장과 이벤트
- 승인·결과·검증·완료 receipt와 active branch unique lease
- 읽기 전용 운영실 대시보드
- Grok `CoinEasy-Ops` MCP
- Buzz 완료·장애 영수증

### Phase 2 — 개발 자동화

- 사람 승인 후 Devin 세션과 Draft PR 자동 생성
- Claude Code/Codex/Grok Build 라우팅
- CI 결과와 독립 리뷰 수집
- Preview까지 자동, Production은 승인식

### Phase 3 — 콘텐츠 폐쇄 루프

- OriginTrail 한 고객에서 후보부터 게시 준비까지 shadow
- 20회 clean run, 중복 0, 근거·영수증 100%
- 정형 저위험 콘텐츠만 제한적 canary

### Phase 4 — 학습 회사

- 매주 개선 제안과 Preview 실험
- 성과가 측정된 변경만 승인 후 확대
- 고객과 업무 유형을 하나씩 추가

회사를 한 번에 완전 자동화하지 않습니다. 검증된 업무 유형을 하나씩
복제하면 대표는 방향과 승인만 맡고 운영 개입 시간은 계속 줄어듭니다.

Phase 0 종료 기준은 계획 패킷 3건, branch 충돌 0, 범위 밖 파일 0,
비밀 노출 0, 누락된 evidence hash 0입니다. 이후 실행 단계에서는 모든
외부 행동의 receipt 완전성이 100%여야 합니다.
