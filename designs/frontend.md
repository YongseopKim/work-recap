# Phase 7: Frontend 상세 설계

## 목적

BE API를 통해 pipeline 실행, summary 조회, 자유 질문을 수행하는 웹 UI.
개인 도구이므로 빌드 도구 없이 **정적 HTML/CSS/JS**로 구성한다.

---

## 기술 선택

| 항목 | 선택 | 이유 |
|------|------|------|
| 프레임워크 | 없음 (Vanilla JS) | 개인 도구, 페이지 3개, 빌드 불필요 |
| CSS | Pico CSS (CDN) | classless CSS, 최소 마크업으로 깔끔한 UI |
| Markdown 렌더링 | marked.js (CDN) | 경량, 널리 사용 |
| 서빙 | FastAPI StaticFiles | 별도 웹서버 불필요 |

---

## 파일 구조

```
frontend/
├── index.html          # SPA — 탭 기반 단일 페이지
├── style.css           # 커스텀 스타일 (Pico CSS 보충)
└── app.js              # API 호출 + UI 로직
```

---

## UI 구조

단일 페이지, 3개 탭으로 구성:

```
┌─────────────────────────────────────────────┐
│  work-recap                                 │
│  [Pipeline]  [Summaries]  [Ask]             │
├─────────────────────────────────────────────┤
│                                             │
│  (탭별 콘텐츠 영역)                           │
│                                             │
└─────────────────────────────────────────────┘
```

### Tab 1: Pipeline

날짜 선택 → 파이프라인 실행 → 결과 확인.

```
┌─ Pipeline ──────────────────────────────────┐
│                                             │
│  Date: [2025-02-16]  [▶ Run]               │
│                                             │
│  ── 또는 ──                                  │
│                                             │
│  Since: [2025-02-01]  Until: [2025-02-16]   │
│  [▶ Run Range]                              │
│                                             │
│  ┌─ Status ──────────────────────────────┐  │
│  │ ⏳ Running... (job: a1b2c3)           │  │
│  │ ✓ Completed → View Summary            │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

**동작:**
1. Run 클릭 → `POST /api/pipeline/run/{date}` → job_id 수신
2. 1초 간격 polling → `GET /api/pipeline/jobs/{job_id}`
3. completed → "View Summary" 링크 표시 (Summaries 탭으로 이동)
4. failed → 에러 메시지 표시

### Tab 2: Summaries

기간별 summary 조회 + markdown 렌더링.

```
┌─ Summaries ─────────────────────────────────┐
│                                             │
│  Type: (•) Daily  ( ) Weekly  ( ) Monthly   │
│        ( ) Yearly                           │
│                                             │
│  [날짜/기간 입력 필드]  [View]               │
│                                             │
│  ┌─ Summary ─────────────────────────────┐  │
│  │                                       │  │
│  │  # 2025-02-16 Daily Summary           │  │
│  │                                       │  │
│  │  ## 주요 활동                          │  │
│  │  - PR #123 작성 ...                   │  │
│  │                                       │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

**타입별 입력:**
- Daily: date input (YYYY-MM-DD)
- Weekly: year input + week number input
- Monthly: year input + month select (1-12)
- Yearly: year input

**동작:**
1. View 클릭 → `GET /api/summary/{type}/...`
2. 200 → `content`를 marked.js로 HTML 변환하여 표시
3. 404 → "Summary not found" 메시지

### Tab 3: Ask

자유 질문 → LLM 응답 표시.

```
┌─ Ask ───────────────────────────────────────┐
│                                             │
│  Question:                                  │
│  [이번 달 주요 성과는?                    ]  │
│                                             │
│  Months: [3]  [▶ Ask]                       │
│                                             │
│  ┌─ Answer ──────────────────────────────┐  │
│  │                                       │  │
│  │  이번 달 주요 성과는 다음과 같습니다...  │  │
│  │                                       │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

**동작:**
1. Ask 클릭 → `POST /api/query` (body: `{question, months}`) → job_id 수신
2. 1초 간격 polling → `GET /api/pipeline/jobs/{job_id}`
3. completed → `result` 필드를 markdown으로 렌더링하여 표시
4. failed → 에러 메시지 표시

---

## app.js 구조

```javascript
// ── API 헬퍼 ──

async function api(method, path, body = null) { ... }

async function pollJob(jobId, onUpdate) {
    // 1초 간격 polling, onUpdate(job) 콜백
    // status가 completed/failed이면 중지
}

// ── Tab 전환 ──

function switchTab(tabName) { ... }

// ── Pipeline 탭 ──

async function runPipeline() { ... }
async function runRange() { ... }

// ── Summaries 탭 ──

function updateSummaryInputs() { ... }  // 타입 변경 시 입력 필드 갱신
async function viewSummary() { ... }

// ── Ask 탭 ──

async function askQuestion() { ... }

// ── 초기화 ──

document.addEventListener('DOMContentLoaded', init);
```

---

## FastAPI 정적 파일 서빙

`app.py`에 StaticFiles 마운트 추가:

```python
from fastapi.staticfiles import StaticFiles

def create_app() -> FastAPI:
    app = FastAPI(...)
    # ... 기존 라우터 등록 ...

    # 정적 파일 서빙 (API 라우터 뒤에 마운트)
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

    return app
```

`html=True` → `index.html`을 기본 문서로 서빙.

**주의:** StaticFiles 마운트는 반드시 API 라우터 `include_router` 뒤에 위치해야
`/api/*` 경로가 먼저 매칭된다.

---

## 테스트 전략

Frontend는 정적 파일이므로 Python 단위 테스트 대상이 아니다.
대신 다음을 검증한다:

### test_api.py에 추가할 테스트

```python
class TestStaticFiles:
    def test_serves_index_html(self, client):
        """GET / → index.html 반환."""

    def test_serves_css(self, client):
        """GET /style.css → CSS 파일 반환."""

    def test_serves_js(self, client):
        """GET /app.js → JS 파일 반환."""
```

### 수동 검증 체크리스트

- [ ] Pipeline: 단일 날짜 실행 → polling → completed 표시
- [ ] Pipeline: 범위 실행 → polling → 결과 표시
- [ ] Pipeline: 실패 시 에러 메시지 표시
- [ ] Summaries: Daily/Weekly/Monthly/Yearly 각각 조회
- [ ] Summaries: 없는 summary → "not found" 메시지
- [ ] Summaries: Markdown 렌더링 정상
- [ ] Ask: 질문 입력 → polling → 응답 표시
- [ ] Ask: 실패 시 에러 메시지 표시
- [ ] 탭 전환 정상 동작

---

## ToDo

| # | 작업 | 테스트 |
|---|------|--------|
| 7.1 | `app.py`에 StaticFiles 마운트 + `frontend/` 디렉토리 생성 | TestStaticFiles (3 tests) |
| 7.2 | `index.html` + `style.css` — 레이아웃, 탭 구조, Pico CSS | 수동 확인 |
| 7.3 | `app.js` — API 헬퍼, polling, Pipeline 탭 로직 | 수동 확인 |
| 7.4 | `app.js` — Summaries 탭 (타입별 입력 + markdown 렌더링) | 수동 확인 |
| 7.5 | `app.js` — Ask 탭 (질문 입력 + async 응답) | 수동 확인 |
