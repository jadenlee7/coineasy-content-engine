# Deployment Guide · coineasy-content-engine

**목표**: 로컬 검증 → GitHub push → Railway 배포 → 첫 캐러셀 생성까지.  
**예상 소요**: 30분 ~ 1시간

---

## Phase 1 · 로컬 검증 (15분)

```bash
# 1. ZIP 압축 풀기
cd ~/coineasy
unzip coineasy-content-engine.zip
cd coineasy-content-engine

# 2. Python 환경
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 3. API 키 설정
export ANTHROPIC_API_KEY="sk-ant-..."

# 4. Yellow 클라이언트로 mock 테스트 (API 호출 없음)
python scripts/generate_cli.py --client yellow --mock \
  --source "Yellow is chain-agnostic by design."

# 5. 실제 LLM 호출 테스트
python scripts/generate_cli.py --client yellow \
  --source "Yellow is chain-agnostic by design. One example is our integration with the XRPL EVM sidechain, bringing real-world asset trading onto faster, lower-cost, non-custodial rails. The goal is a single connected market, not pervasive isolation." \
  --source-url "https://x.com/Yellow/status/2046509996834206186"

# → 결과: output/yellow/<timestamp>/ 폴더에 PNG 3-5장
```

**체크포인트**:
- [ ] 3-5개 PNG 생성됨
- [ ] 한국어 번역이 자연스러운가?
- [ ] preserve_terms (Yellow SDK, $YELLOW, Nitrolite 등) 영어 그대로 남아있는가?
- [ ] 레이아웃 선택이 적절한가?

---

## Phase 2 · GitHub Repo 생성 (5분)

```bash
cd ~/coineasy/coineasy-content-engine

# Git 초기화
git init
git add .
git commit -m "Initial: multi-tenant content engine (yellow + squid)"

# GitHub에서 새 레포 생성 (https://github.com/new)
# 이름: coineasy-content-engine
# Private 또는 Public

# Push
git remote add origin https://github.com/jadenlee7/coineasy-content-engine.git
git branch -M main
git push -u origin main
```

---

## Phase 3 · Railway 배포 (10분)

### 3.1 Railway 새 프로젝트 생성

```bash
# Railway CLI (이미 있으심)
railway login  # 필요시

# 현재 디렉토리에서 새 프로젝트 생성
cd ~/coineasy/coineasy-content-engine
railway init coineasy-content-engine

# GitHub 연결 (Railway 대시보드에서)
# → railway.app 접속
# → New Project → Deploy from GitHub repo → coineasy-content-engine 선택
```

### 3.2 환경변수 설정

```bash
# 필수
railway variables --set ANTHROPIC_API_KEY="sk-ant-..."
railway variables --set API_SECRET="$(openssl rand -hex 32)"

# Yellow 텔레그램 봇 (옵션 - 나중에 승인봇 붙일 때)
# railway variables --set TELEGRAM_BOT_TOKEN_YELLOW="..."
```

### 3.3 첫 배포

Railway가 `Dockerfile`을 자동 감지해서 빌드 시작합니다.

**예상 빌드 시간**: 4-6분 (Playwright + Chromium + 한글 폰트 다운로드)

```bash
# 배포 상태 확인
railway logs

# 도메인 확인
railway domain
# → https://coineasy-content-engine-production.up.railway.app
```

### 3.4 배포 검증

```bash
export API_URL="https://coineasy-content-engine-production.up.railway.app"
export API_SECRET="[방금 설정한 secret]"

# Health check
curl $API_URL/health
# → {"ok":true,"ts":...}

# 로드된 클라이언트 확인
curl $API_URL/clients -H "X-API-Key: $API_SECRET"
# → [{"client_id":"yellow",...}, {"client_id":"squid",...}]

# 실제 캐러셀 생성
curl -X POST $API_URL/clients/yellow/generate/edu-carousel \
  -H "X-API-Key: $API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "source_content": "Yellow is chain-agnostic by design.",
    "source_type": "tweet",
    "source_url": "https://x.com/Yellow/status/xxx"
  }'
```

---

## Phase 4 · 새 클라이언트 추가 (30분/각)

예: Wallet V 추가

```bash
cd ~/coineasy/coineasy-content-engine

# 1. Scaffold (30초)
python scripts/new_client.py --id wallet_v --name "Wallet V"

# 2. 로고 추가 (2분)
cp ~/Downloads/walletv_logo_white.png clients/wallet_v/assets/logo_dark.png
cp ~/Downloads/walletv_logo_black.png clients/wallet_v/assets/logo_light.png

# 3. config.yaml 편집 (15분)
vim clients/wallet_v/config.yaml
# → brand.primary_color, preserve_terms, glossary, telegram.public_channel 등

# 4. 로컬 테스트 (2분)
python scripts/generate_cli.py --client wallet_v --mock \
  --source "Wallet V now supports..."

# 5. active: true 로 변경 + commit
vim clients/wallet_v/config.yaml  # active: true

git add clients/wallet_v/
git commit -m "Add Wallet V client"
git push
# → Railway 자동 재배포 → wallet_v 클라이언트 새 서비스에 로드됨
```

---

## 트러블슈팅

### Playwright 설치 실패 (로컬)

```bash
# macOS
brew install --cask chromium  # 대체 옵션

# 또는 시스템 의존성만 설치
playwright install-deps chromium
playwright install chromium
```

### Railway 빌드 실패 (Playwright)

Dockerfile에 이미 모든 system deps가 들어있지만, 빌드 로그에서 에러 확인:

```bash
railway logs --deployment
```

흔한 원인: 메모리 부족. Railway Hobby 플랜은 기본 512MB-8GB. Playwright는 빌드 시 많이 먹음.

### 한글이 깨져서 렌더링됨

Dockerfile에 `fonts-noto-cjk` 포함되어 있지만, Playwright 런타임에서 안 쓰는 경우:

```bash
# 로컬에서 확인
docker build -t test-engine .
docker run -it test-engine fc-list | grep -i noto
```

---

## 운영 운영 팁

### 실제 LLM 비용 모니터링

Anthropic Console (https://console.anthropic.com/usage) 에서 매일 확인.

**예상 비용**:
- 1 carousel ≈ $0.03 (Claude Sonnet 4.6, 4000 output tokens)
- 월 30 carousels/client × 3 clients = 월 $3
- 월 200 carousels/client × 10 clients = 월 $60

### 로그 모니터링

```bash
# 실시간 로그
railway logs --follow

# 특정 클라이언트만 필터
railway logs --follow | grep "\[yellow\]"
```

### 캐러셀 생성 실패 시 디버그

1. Railway 로그에서 에러 찾기
2. 로컬에서 같은 input으로 재현:
   ```bash
   python scripts/generate_cli.py --client yellow \
     --source "[실패한 input]"
   ```
3. LLM 응답 확인 (manifest.json 열어서 `spec` 필드)

---

## 다음 단계 (Phase 5+)

이 가이드에서 커버 안 한 것들:

1. **Telegram 승인 봇** — 생성된 캐러셀을 제이든+Kailey 채널에 보내고 버튼으로 승인/수정/거절
2. **Typefully MCP 연결** — 승인된 캐러셀을 X에 자동 포스팅
3. **Content Studio 다음 단계** — 생성 히스토리와 팀 보관함은 적용됨. 다음은 승인/발행 화면과 Supabase Auth 전환
4. **검토형 자동 트리거** — 공식 X 새 게시물 감지 → 초안 생성 → 사람이 승인한 뒤 게시
5. **Figma 플러그인** — 승인된 불변 SVG만 가져오고 연결 정보는 서버에서 기록

이것들은 별도 phase로 순차 진행하면 됩니다.

---

## 요약

```
[Phase 1] 로컬 검증       15분  → mock 테스트 + 실제 LLM 1회
[Phase 2] GitHub push     5분   → jadenlee7/coineasy-content-engine
[Phase 3] Railway 배포    10분  → 새 서비스 + 환경변수 + 첫 호출
[Phase 4] 신규 클라 추가  30분  → Squid, Wallet V 등 추가
─────────────────────────────
총             1시간 ~ 1.5시간  → 프로덕션 가동 완료
```
