# CoinEasy Content Engine · Architecture

**설계 목적**: N개 Web3 클라이언트(Yellow, Squid, Wallet V 등)의 한국어 콘텐츠 자동 생성을 단일 서비스에서 처리하는 멀티테넌트 엔진.

---

## 1. Core Principles

1. **Client = Config, not Code**  
   새 클라이언트 추가 시 코드 작성 없음. `clients/<name>/config.yaml` + 로고 파일만 추가.

2. **Core is Client-Agnostic**  
   `core/` 밑의 어떤 파일도 클라이언트 이름을 하드코딩하지 않음.

3. **Templates are Shared, Overridable**  
   기본 템플릿 8개는 `core/templates/`에 공유. 클라이언트가 오버라이드 필요하면 `clients/<name>/templates/`에 같은 파일명으로 배치.

4. **One Deploy, N Clients**  
   Railway 서비스 1개로 모든 클라이언트 처리.

---

## 2. Directory Structure

```
coineasy-content-engine/
├── core/                                # 클라이언트 무관 공통 로직
│   ├── llm/
│   │   ├── edu_carousel_pipeline.py     # 교육 캐러셀 LLM
│   │   └── news_card_pipeline.py        # 뉴스 카드 LLM
│   ├── renderers/
│   │   ├── playwright_renderer.py       # PNG 렌더링
│   │   └── template_resolver.py         # 템플릿 경로 해결 (core→client override)
│   ├── templates/                       # 기본 HTML 템플릿
│   │   ├── edu/                         # 교육 캐러셀 (P1-P8)
│   │   │   ├── edu_p1_3card.html
│   │   │   ├── edu_p2_bullets.html
│   │   │   └── ...
│   │   └── news/                        # 뉴스 카드 (1080×1080 단일 카드)
│   │       └── news_title_card.html     # D3 신규 (공유 카드 스키마)
│   ├── orchestrator.py                  # E2E 파이프라인
│   ├── client_config.py                 # Client 설정 로더
│   └── logging.py                       # 구조화 로깅
│
├── clients/                             # 각 클라이언트 설정 + 에셋
│   ├── yellow/
│   │   ├── config.yaml                  # 브랜드·LLM 파라미터
│   │   ├── assets/
│   │   │   ├── logo_dark.png
│   │   │   └── logo_light.png
│   │   ├── overrides/                   # (옵션) 커스텀 템플릿
│   │   │   └── edu_p1_3card.html       # Yellow만의 특별 버전
│   │   └── locales/
│   │       └── ko.yaml                  # Yellow-specific 용어 사전
│   │
│   ├── squid/                           # 새 클라이언트 추가 예시
│   │   ├── config.yaml
│   │   └── assets/
│   │       └── logo_dark.png
│   │
│   └── wallet_v/
│       └── ...
│
├── api/
│   ├── server.py                        # FastAPI 서버
│   ├── routes/
│   │   ├── generate.py                  # POST /clients/{id}/generate
│   │   ├── preview.py                   # GET /clients/{id}/preview
│   │   └── admin.py                     # 내부 관리 API
│   └── auth.py                          # X-API-Key 인증
│
├── scripts/                             # CLI 도구
│   ├── generate_cli.py                  # 로컬 테스트용 CLI
│   └── new_client.py                    # 새 클라이언트 초기화 scaffolding
│
├── tests/
│   ├── test_multitenancy.py             # 클라이언트 격리 테스트
│   └── test_template_resolver.py        # override 테스트
│
├── docs/
│   ├── ARCHITECTURE.md                  # 이 문서
│   ├── ADDING_A_CLIENT.md               # 새 클라이언트 온보딩 가이드
│   └── API.md                           # API 레퍼런스
│
├── Dockerfile                           # Railway 배포용
├── requirements.txt
├── railway.json
└── README.md
```

---

## 3. Client Config Schema

각 클라이언트는 `clients/<id>/config.yaml`로 완전히 기술됨.

```yaml
# clients/yellow/config.yaml

client_id: yellow
name: Yellow Network
locale: ko-KR
active: true

brand:
  primary_color: "#FFDE00"
  bg_dark: "#000000"
  bg_yellow: "#FFDE00"
  text_primary: "#0A0A0A"
  text_body: "#595959"
  logo_dark: assets/logo_dark.png          # 다크 배경용 (흰색)
  logo_light: assets/logo_light.png        # 밝은 배경용 (검정)
  font_family: "Pretendard Variable"

content_sources:
  twitter:
    handle: "@Yellow"
    poll_interval_min: 30
  blog_rss:
    - "https://medium.com/yellow-blog/feed"
    - "https://yellow.com/feed"

llm:
  edu_carousel:
    model: "claude-opus-4-8"
    temperature: 0.3
    # 클라이언트별 커스텀 프롬프트 fragments
    tone_guidance: "professional but approachable, 경어체"
    preserve_terms:
      - "Nitrolite"
      - "Clearnode"
      - "$YELLOW"
      - "Yellow SDK"
      - "State Channel"
    glossary:
      "chain-agnostic": "체인을 가리지 않는"
      "state channel": "스테이트 채널(state channel)"
      "non-custodial": "논커스터디얼(non-custodial)"

  news_card:
    # model 라인 생략 시 중앙 디폴트(claude-opus-4-8) 상속 — 대부분 클라이언트는 이대로.
    temperature: 0.2

publishing:
  telegram:
    public_channel: "@YellowKorea_ann"
    approval_channel_id: -1001234567890    # 제이든+Kailey 승인 채널
    bot_token_env: TELEGRAM_BOT_TOKEN_YELLOW
  twitter:
    typefully_account_id: "yellow_kr"      # Typefully MCP 연결
  discord:
    enabled: false

feature_flags:
  auto_approve: false                      # 수동 승인 유지
  education_carousel: true
  news_card: true

routing:
  # 1차 필터: 소스가 어느 파이프라인으로 갈지
  skip_patterns:
    - "Day 1"
    - "Day 2"
    - "on the ground"
    - "Premier Sponsor"
  edu_signals:
    - "by design"
    - "chain-agnostic"
    - "protocol"
    - "architecture"
  news_signals:
    - "partnership"
    - "live on"
    - "mainnet"
    - "launch"
```

---

## 4. Execution Flow

### Flow A: 자동 생성 (poller)

```
[Cron 30min]
    ↓
core/pollers/twitter_poller.py
    ↓ fetches new tweets from all active clients
    ↓
for each client:
  for each new tweet:
    ├─ already_processed? → skip
    ├─ client.routing.should_skip(tweet)? → skip
    ├─ client.routing.is_edu_candidate(tweet)?
    │     → core/orchestrator.generate_edu_carousel(client_id, tweet)
    │     → send to client.publishing.telegram.approval_channel
    └─ client.routing.is_news_candidate(tweet)?
          → core/orchestrator.generate_news_card(client_id, tweet)
          → send to client.publishing.telegram.approval_channel
```

### Flow B: 수동 API 호출

```
POST /clients/yellow/generate
{
  "source_content": "Yellow is chain-agnostic...",
  "source_type": "tweet",
  "content_type": "edu_carousel"  // or "news_card"
}
    ↓
core/orchestrator.generate(client_id="yellow", ...)
    ↓
{
  "carousel_id": "...",
  "png_urls": ["https://cdn.../1.png", ...],
  "approval_url": "https://t.me/..."
}
```

---

## 5. Template Resolution

`core/renderers/template_resolver.py`:

```python
def resolve_template(client_id: str, template_name: str) -> Path:
    """
    우선순위:
    1. clients/<client_id>/overrides/<template_name>   (클라이언트 커스텀)
    2. core/templates/<template_name>                   (기본)
    """
    client_override = f"clients/{client_id}/overrides/{template_name}"
    if os.path.exists(client_override):
        return Path(client_override)
    
    core_default = f"core/templates/{template_name}"
    if os.path.exists(core_default):
        return Path(core_default)
    
    raise FileNotFoundError(f"Template not found: {template_name}")
```

**효과**:
- Yellow는 기본 템플릿 사용
- Squid가 특별한 P1 레이아웃 원하면 `clients/squid/overrides/edu/edu_p1_3card.html`만 추가
- 오버라이드 없으면 자동 fallback

---

## 6. Adding a New Client (30-minute workflow)

```bash
# 1. scaffold 생성
python scripts/new_client.py --id squid --name "Squid"

# → clients/squid/ 디렉토리 + 기본 config.yaml 생성

# 2. 에셋 업로드
cp ~/Downloads/squid_logo_dark.png clients/squid/assets/logo_dark.png
cp ~/Downloads/squid_logo_light.png clients/squid/assets/logo_light.png

# 3. config.yaml 편집
vim clients/squid/config.yaml   # brand colors, sources, publishing channels

# 4. 테스트
python scripts/generate_cli.py --client squid --source "Squid supports..."

# 5. active=true → 자동 poller가 잡기 시작
```

---

## 7. Data Model (Supabase)

```sql
-- 모든 클라이언트 공통 테이블, client_id로 구분

CREATE TABLE content_generations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id VARCHAR(50) NOT NULL,           -- 'yellow', 'squid', etc.
    content_type VARCHAR(30) NOT NULL,        -- 'edu_carousel', 'news_card'
    
    source_url TEXT NOT NULL,
    source_type VARCHAR(20),
    source_content_preview TEXT,
    
    -- Output
    output_urls TEXT[],                       -- Supabase Storage URLs
    manifest_json JSONB,
    
    -- Metadata
    llm_model VARCHAR(30),
    llm_cost_cents INT,
    duration_ms INT,
    
    -- Approval workflow
    approval_status VARCHAR(20) DEFAULT 'pending',
    approved_by VARCHAR(100),
    approved_at TIMESTAMPTZ,
    
    -- Publishing
    posted_channels TEXT[],
    posted_at TIMESTAMPTZ,
    
    created_at TIMESTAMPTZ DEFAULT now(),
    
    INDEX(client_id, created_at DESC),
    INDEX(client_id, approval_status)
);

CREATE TABLE content_sources_seen (
    client_id VARCHAR(50) NOT NULL,
    source_url TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ DEFAULT now(),
    processed BOOLEAN DEFAULT FALSE,
    
    PRIMARY KEY(client_id, source_url)
);
```

**핵심**: 모든 테이블에 `client_id` 컬럼. Row-Level Security(RLS)로 선택적 격리 가능하지만 초기에는 application-level 필터만으로도 충분.

---

## 8. Cost Projection

| 시나리오 | 클라이언트 수 | 월 LLM 호출 | 월 비용 |
|---|---|---|---|
| 시작 (Yellow만) | 1 | 30 × $0.03 | **~$6** (Railway $5 + LLM $1) |
| 현재 목표 (Yellow·Squid·Wallet V) | 3 | 90 × $0.03 | **~$8** |
| 6개월 후 | 5-7 | 150-200 × $0.03 | **~$11-12** |
| 10개 클라이언트 | 10 | 300 × $0.03 | **~$14** |

**단일 서비스 디자인이 왜 중요한지**: 복붙하면 클라이언트 10개에 $55/월 → 멀티테넌트면 $14/월. **월 $40 절감**, **유지보수 시간 80% 단축**.

---

## 9. Migration from Existing YellowKR

**기존 `YellowKR` 봇은 건드리지 않음**. 역할 재정의:

- **YellowKR (기존)**: `@Yellow__Korea` 스크래핑 + 유저와 AI 채팅 + 팁 발송 (그대로 유지)
- **coineasy-content-engine (신규)**: `@Yellow` 글로벌 계정 + 블로그 기반 **교육 캐러셀/뉴스 카드 생성**

두 시스템은 **완전 독립적**:
- YellowKR: 한국 커뮤니티 인게이지먼트 (fan-facing)
- content-engine: 공식 콘텐츠 프로덕션 (브랜드 asset 생성)

필요하면 나중에 통합 가능:
```
content-engine이 PNG 생성 → YellowKR 봇 API 호출 → Yellow Korea 텔레그램 채널 포스팅
```

---

## 10. Phase Plan

### Phase 1: Foundation (Week 1)
- [x] 멀티테넌트 구조 설계 (완료)
- [ ] `core/` 모듈 구현 (llm, renderers, orchestrator)
- [ ] Yellow 클라이언트 설정 마이그레이션
- [ ] FastAPI 서버 with `/clients/{id}/generate` 엔드포인트
- [ ] Docker + Railway 배포

### Phase 2: Yellow Production (Week 2)
- [ ] 실제 LLM 호출 검증 (3개 샘플)
- [ ] Telegram 승인 봇 연결
- [ ] Typefully MCP로 X 포스팅
- [ ] Supabase 로깅

### Phase 3: Second Client (Week 3)
- [ ] Squid 클라이언트 30분 안에 추가
- [ ] 오버라이드 시스템 검증 (Squid가 다른 레이아웃 원하면)
- [ ] 멀티 클라이언트 동시 운영 부하 테스트

### Phase 4: Scale (Week 4+)
- [ ] Wallet V 추가
- [ ] 필요 시 새 템플릿 유형 (G-03 Event, G-05 Quote 등)
- [ ] 어드민 대시보드 (모든 클라이언트 통합 뷰)

---

## 11. Anti-Patterns to Avoid

❌ **하드코딩**: `if client == "yellow": ...` 코드가 core/에 있으면 즉시 리팩터링
❌ **클라이언트간 의존**: Squid 설정이 Yellow 변수를 참조하는 것
❌ **templates를 clients/로 복붙**: 오버라이드만 clients/에 두고 공통은 core/
❌ **시크릿을 config.yaml에**: API 키·토큰은 Railway 환경변수로
❌ **비동기 작업 공유 queue 남용**: 클라이언트별 job 격리 유지 (한 곳 막히면 다른 곳도 막힘 방지)

---

## 12. Success Metrics

- **새 클라이언트 온보딩 시간**: 30분 이하 (scaffolding + config + 에셋)
- **코드 중복**: 클라이언트 추가 시 core/ 파일 수정 0건
- **월 운영 비용**: 클라이언트 N개 시 Railway $5 + LLM $0.03 × 월간 캐러셀 수
- **장애 격리**: 한 클라이언트 config 오류가 다른 클라이언트 중단시키지 않음

---

## 13. News Card Pipeline

`edu_carousel`과 대칭 구조를 갖는 두 번째 콘텐츠 파이프라인. 단일 1080×1080 뉴스 카드를 생성.

### Card Schema (fixed)

```
{
  "label":       str,           # 배지 텍스트 (예: "파트너십", "런칭")
  "date":        "YYYY.MM.DD",
  "headline":    str,           # 헤드라인 한 문장 (경어체)
  "body_lines":  list[str],     # 1~3개 요약 문장
  "source_url":  str,           # 원본 URL
  "theme":       "dark" | "yellow"
}
```

LLM 출력이 그대로 Jinja 슬롯이 됨 — 렌더러가 자동 주입하는 브랜드 변수(`brand_primary_color`, `logo_dark_path` 등)와 병합.

### Flow

1. **`core/llm/news_card_pipeline.py::generate_news_card_spec()`**  
   소스 콘텐츠를 위 스키마 dict으로 요약. `mock_mode=True`면 `mock_response`를 그대로 반환(스모크용, LLM 호출 없음).

2. **`core/templates/news/news_title_card.html`**  
   1080×1080 단일 카드 템플릿. 카드 스키마 슬롯 + 브랜드 변수를 렌더.

3. **`core/orchestrator.py::generate_news_card()`**  
   `feature_flags.news_card` 체크 → spec 생성 → `render_png(..., viewport=NEWS_CARD_1x1)` → manifest 기록. 반환하는 `NewsCardResult.png_path`는 **str 단일**(edu의 `png_paths: list`와 대비).

4. **`api/server.py POST /clients/{client_id}/generate/news-card`**  
   orchestrator를 HTTP로 노출. 응답에 `spec`과 `png_path`를 담아 호출자가 manifest 재읽기 없이 카드 내용 확인 가능.

### Viewport

`core/renderers/playwright_renderer.py::NEWS_CARD_1x1 = (1080, 1080)` — DPR2로 실제 PNG는 **2160×2160**. `NEWS_CARD_16x9 = (1200, 675)`는 예약(현재 미사용).

### edu_carousel과의 대칭점

| 축 | `edu_carousel` | `news_card` |
|---|---|---|
| LLM 파이프라인 | `edu_carousel_pipeline` | `news_card_pipeline` |
| 템플릿 | `edu/edu_p{1-8}*.html` | `news/news_title_card.html` |
| Orchestrator | `generate_edu_carousel` | `generate_news_card` |
| API 라우트 | `POST /generate/edu-carousel` | `POST /generate/news-card` |
| 출력 | `png_paths: list[str]` (N slides) | `png_path: str` (단일 카드) |
| Feature flag | `feature_flags.education_carousel` | `feature_flags.news_card` |
| Viewport 상수 | `EDU_CAROUSEL_SIZE = (1080, 1080)` | `NEWS_CARD_1x1 = (1080, 1080)` |

### LLM 모델 상속

클라이언트 config에서 `llm.news_card.model`을 명시하지 않으면 `client_config._resolve_default_model()`이 env `LLM_MODEL` → 폴백 `"claude-opus-4-8"` 순으로 해석. 대부분 클라이언트(예: `origintrail`)는 명시하지 않고 중앙 디폴트를 그대로 상속.

### CLI 지원 상태

`scripts/generate_cli.py`는 현재 **edu-carousel 전용**. news-card CLI 래퍼는 후속 작업. 그 전까지는 `POST /generate/news-card` API 라우트 또는 `core.orchestrator.generate_news_card()` 직접 호출을 사용.
