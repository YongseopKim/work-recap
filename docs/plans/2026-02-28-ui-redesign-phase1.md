# UI Redesign Phase 1: 기반 + 즉시 가치 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 기존 바닐라 JS 프론트엔드를 Alpine.js로 마이그레이션하고, Summaries 캘린더 뷰 + 다크 모드 + 복사 버튼을 추가한다.

**Architecture:** Alpine.js CDN으로 반응형 UI 전환. ES modules로 탭별 파일 분할. 새 API 1개 (`GET /api/summaries/available`) 추가. 기존 API 변경 없음.

**Tech Stack:** Alpine.js (CDN), Pico CSS (기존), marked.js (기존), ES modules (`<script type="module">`)

**Design doc:** `docs/plans/2026-02-28-ui-redesign-design.md`

**Worktree:** `git worktree add ../work-recap-claude-feat/ui-phase1 -b feat/ui-phase1`
**Tests:** `PYTHONPATH=src pytest` (worktree에서는 pip install -e . 금지)

---

## Task 1: Summaries Available API 엔드포인트

신규 API: `GET /api/summaries/available?year=2025&month=2` — 해당 월에 존재하는 summary 파일 목록 반환.

**Files:**
- Create: `src/workrecap/api/routes/summaries_available.py`
- Modify: `src/workrecap/api/app.py:28-37` (라우터 등록 추가)
- Test: `tests/unit/test_api_summaries_available.py`

### Step 1: Write the failing test

```python
# tests/unit/test_api_summaries_available.py
"""Summaries available API 테스트."""

import pytest
from starlette.testclient import TestClient

from workrecap.api.app import create_app
from workrecap.api.deps import get_config, get_job_store
from workrecap.api.job_store import JobStore
from workrecap.config import AppConfig


@pytest.fixture()
def test_config(tmp_path):
    data_dir = tmp_path / "data"
    for sub in ["state/jobs", "raw", "normalized", "summaries"]:
        (data_dir / sub).mkdir(parents=True)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    return AppConfig(
        ghes_url="https://github.example.com",
        ghes_token="test-token",
        username="testuser",
        data_dir=data_dir,
        prompts_dir=prompts_dir,
    )


@pytest.fixture()
def client(test_config):
    app = create_app()
    store = JobStore(test_config)
    app.dependency_overrides[get_config] = lambda: test_config
    app.dependency_overrides[get_job_store] = lambda: store
    return TestClient(app)


class TestSummariesAvailable:
    def test_empty_month(self, client):
        """데이터 없는 월 조회 시 모든 리스트가 비어있다."""
        resp = client.get("/api/summaries/available?year=2025&month=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["daily"] == []
        assert data["weekly"] == []
        assert data["monthly"] == []
        assert data["yearly"] is False

    def test_with_daily_summaries(self, client, test_config):
        """daily summary 파일이 있으면 해당 날짜가 리스트에 포함된다."""
        daily_dir = test_config.summaries_dir / "2025" / "daily"
        daily_dir.mkdir(parents=True)
        (daily_dir / "02-10.md").write_text("summary", encoding="utf-8")
        (daily_dir / "02-14.md").write_text("summary", encoding="utf-8")
        (daily_dir / "03-01.md").write_text("other month", encoding="utf-8")

        resp = client.get("/api/summaries/available?year=2025&month=2")
        data = resp.json()
        assert sorted(data["daily"]) == ["02-10", "02-14"]

    def test_with_weekly_summaries(self, client, test_config):
        """weekly summary 파일이 있으면 해당 주차가 리스트에 포함된다."""
        weekly_dir = test_config.summaries_dir / "2025" / "weekly"
        weekly_dir.mkdir(parents=True)
        (weekly_dir / "W06.md").write_text("summary", encoding="utf-8")
        (weekly_dir / "W07.md").write_text("summary", encoding="utf-8")

        resp = client.get("/api/summaries/available?year=2025&month=2")
        data = resp.json()
        # W06 = 2/3~2/9, W07 = 2/10~2/16 — both overlap with Feb
        assert "W06" in data["weekly"]
        assert "W07" in data["weekly"]

    def test_with_monthly_summary(self, client, test_config):
        """monthly summary 파일이 있으면 리스트에 포함된다."""
        monthly_dir = test_config.summaries_dir / "2025" / "monthly"
        monthly_dir.mkdir(parents=True)
        (monthly_dir / "02.md").write_text("summary", encoding="utf-8")

        resp = client.get("/api/summaries/available?year=2025&month=2")
        data = resp.json()
        assert data["monthly"] == ["02"]

    def test_with_yearly_summary(self, client, test_config):
        """yearly summary 파일이 있으면 True를 반환한다."""
        yearly_dir = test_config.summaries_dir / "2025"
        yearly_dir.mkdir(parents=True)
        (yearly_dir / "yearly.md").write_text("summary", encoding="utf-8")

        resp = client.get("/api/summaries/available?year=2025&month=2")
        data = resp.json()
        assert data["yearly"] is True

    def test_missing_year_param(self, client):
        """year 파라미터 누락 시 422."""
        resp = client.get("/api/summaries/available?month=2")
        assert resp.status_code == 422

    def test_missing_month_param(self, client):
        """month 파라미터 누락 시 422."""
        resp = client.get("/api/summaries/available?year=2025")
        assert resp.status_code == 422
```

### Step 2: Run test to verify it fails

Run: `PYTHONPATH=src pytest tests/unit/test_api_summaries_available.py -v`
Expected: FAIL — `404 Not Found` (route doesn't exist yet)

### Step 3: Write the implementation

```python
# src/workrecap/api/routes/summaries_available.py
"""Summary 파일 존재 여부 조회 — 캘린더 뷰에서 사용."""

import calendar
from datetime import date

from fastapi import APIRouter, Depends, Query

from workrecap.api.deps import get_config
from workrecap.config import AppConfig

router = APIRouter()


def _weeks_overlapping_month(year: int, month: int) -> set[str]:
    """해당 월과 겹치는 모든 ISO week 번호(W06 형식)를 반환."""
    seen: set[str] = set()
    num_days = calendar.monthrange(year, month)[1]
    for day in range(1, num_days + 1):
        iso_y, iso_w, _ = date(year, month, day).isocalendar()
        if iso_y == year:
            seen.add(f"W{iso_w:02d}")
    return seen


@router.get("/available")
def get_available_summaries(
    year: int = Query(...),
    month: int = Query(...),
    config: AppConfig = Depends(get_config),
):
    summaries_year_dir = config.summaries_dir / str(year)
    month_str = f"{month:02d}"

    # Daily: data/summaries/{year}/daily/{MM}-{DD}.md
    daily: list[str] = []
    daily_dir = summaries_year_dir / "daily"
    if daily_dir.exists():
        for f in sorted(daily_dir.glob(f"{month_str}-*.md")):
            daily.append(f.stem)  # "02-10"

    # Weekly: data/summaries/{year}/weekly/W{NN}.md — 해당 월과 겹치는 주차만
    weekly: list[str] = []
    weekly_dir = summaries_year_dir / "weekly"
    overlapping = _weeks_overlapping_month(year, month)
    if weekly_dir.exists():
        for f in sorted(weekly_dir.glob("W*.md")):
            if f.stem in overlapping:
                weekly.append(f.stem)  # "W07"

    # Monthly: data/summaries/{year}/monthly/{MM}.md
    monthly: list[str] = []
    monthly_path = summaries_year_dir / "monthly" / f"{month_str}.md"
    if monthly_path.exists():
        monthly.append(month_str)

    # Yearly: data/summaries/{year}/yearly.md
    yearly = (summaries_year_dir / "yearly.md").exists()

    return {
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "yearly": yearly,
    }
```

Register the router in `src/workrecap/api/app.py` — add after the existing `summary.router` line:

```python
from workrecap.api.routes import (
    fetch, normalize, pipeline, query, summaries_available, summarize_pipeline, summary,
)
# ... in create_app():
    app.include_router(
        summaries_available.router, prefix="/api/summaries", tags=["summaries"]
    )
```

### Step 4: Run test to verify it passes

Run: `PYTHONPATH=src pytest tests/unit/test_api_summaries_available.py -v`
Expected: ALL PASS

### Step 5: Run full test suite

Run: `PYTHONPATH=src pytest`
Expected: ALL PASS (1011+ tests)

### Step 6: Commit

```bash
git add src/workrecap/api/routes/summaries_available.py tests/unit/test_api_summaries_available.py src/workrecap/api/app.py
git commit -m "feat(api): add GET /api/summaries/available endpoint for calendar view"
```

---

## Task 2: Alpine.js 마이그레이션 — 프론트엔드 파일 구조 전환

기존 `frontend/app.js`를 ES module 기반 구조로 분할하고, Alpine.js CDN을 추가한다.

**Files:**
- Modify: `frontend/index.html` (CDN 추가, `<script type="module">` 전환)
- Create: `frontend/js/app.js` (Alpine 초기화 + 탭 라우팅)
- Create: `frontend/js/api.js` (fetch 헬퍼 + job polling — 기존 로직 추출)
- Delete: `frontend/app.js` (기존 단일 파일 — 새 구조로 대체)
- Test: 수동 브라우저 테스트 (프론트엔드 전환이므로)

### Step 1: Create `frontend/js/api.js` — 기존 API 헬퍼 추출

```js
// frontend/js/api.js
// API 헬퍼 + job polling — 기존 app.js에서 추출

export async function api(method, path, body = null) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);

  const resp = await fetch(`/api${path}`, opts);
  if (!resp.ok && resp.status !== 404) {
    const err = await resp.json().catch(() => ({ error: resp.statusText }));
    throw new Error(err.error || err.detail || resp.statusText);
  }
  return resp;
}

export function pollJob(jobId, onUpdate) {
  const poll = async () => {
    const resp = await api("GET", `/pipeline/jobs/${jobId}`);
    const job = await resp.json();
    onUpdate(job);
    if (job.status === "completed" || job.status === "failed") return;
    setTimeout(poll, 1000);
  };
  poll();
}

export function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

export async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
```

### Step 2: Create `frontend/js/app.js` — Alpine 초기화 + 전역 상태

```js
// frontend/js/app.js
// Alpine.js 초기화 + 탭 라우팅

import Alpine from "https://cdn.jsdelivr.net/npm/alpinejs@3/dist/module.esm.js";

// 탭별 컴포넌트 등록
import { pipelineComponent } from "./pipeline.js";
import { summariesComponent } from "./summaries.js";
import { askComponent } from "./ask.js";

// 전역 탭 상태
Alpine.data("tabs", () => ({
  current: "pipeline",
  switch(tab) {
    this.current = tab;
  },
}));

// 탭별 Alpine 컴포넌트 등록
Alpine.data("pipeline", pipelineComponent);
Alpine.data("summaries", summariesComponent);
Alpine.data("ask", askComponent);

// 다크 모드
Alpine.data("theme", () => ({
  dark: localStorage.getItem("theme") === "dark",
  toggle() {
    this.dark = !this.dark;
    localStorage.setItem("theme", this.dark ? "dark" : "light");
    document.documentElement.setAttribute(
      "data-theme",
      this.dark ? "dark" : "light"
    );
  },
  init() {
    document.documentElement.setAttribute(
      "data-theme",
      this.dark ? "dark" : "light"
    );
  },
}));

Alpine.start();
```

### Step 3: Create `frontend/js/pipeline.js` — Pipeline 탭 Alpine 컴포넌트

```js
// frontend/js/pipeline.js
// Pipeline 탭 — 기존 runPipeline/runRange 로직을 Alpine 컴포넌트로 전환

import { api, pollJob, escapeHtml } from "./api.js";

const STATUS_ICONS = {
  accepted: "\u23f3",
  running: "\u23f3",
  completed: "\u2713",
  failed: "\u2717",
};

function renderJobStatus(job) {
  const icon = STATUS_ICONS[job.status] || "";
  let html = `<span class="status-icon">${icon}</span>`;
  html += `<span class="status-${job.status}">`;
  if (job.status === "accepted" || job.status === "running") {
    html += `Running... <small>(job: ${job.job_id})</small>`;
  } else if (job.status === "completed") {
    html += "Completed";
    if (job.result) html += ` &mdash; ${escapeHtml(job.result)}`;
  } else if (job.status === "failed") {
    html += "Failed";
    if (job.error) html += ` &mdash; ${escapeHtml(job.error)}`;
  }
  html += "</span>";
  return html;
}

export function pipelineComponent() {
  return {
    // Single date
    date: new Date().toISOString().slice(0, 10),
    // Range
    since: "",
    until: new Date().toISOString().slice(0, 10),
    // Status
    busy: false,
    statusVisible: false,
    statusHtml: "",
    completedDate: null,

    async runSingle() {
      if (!this.date) return alert("Please select a date.");
      this.busy = true;
      this.statusVisible = true;
      this.completedDate = null;
      this.statusHtml = renderJobStatus({ status: "accepted", job_id: "..." });
      try {
        const resp = await api("POST", `/pipeline/run/${this.date}`);
        const { job_id } = await resp.json();
        pollJob(job_id, (job) => {
          this.statusHtml = renderJobStatus(job);
          if (job.status === "completed") this.completedDate = this.date;
          if (job.status === "completed" || job.status === "failed") this.busy = false;
        });
      } catch (e) {
        this.statusHtml = `<span class="status-failed">${escapeHtml(e.message)}</span>`;
        this.busy = false;
      }
    },

    async runRange() {
      if (!this.since || !this.until) return alert("Please select both dates.");
      this.busy = true;
      this.statusVisible = true;
      this.completedDate = null;
      this.statusHtml = renderJobStatus({ status: "accepted", job_id: "..." });
      try {
        const resp = await api("POST", "/pipeline/run/range", {
          since: this.since,
          until: this.until,
        });
        const { job_id } = await resp.json();
        pollJob(job_id, (job) => {
          this.statusHtml = renderJobStatus(job);
          if (job.status === "completed" || job.status === "failed") this.busy = false;
        });
      } catch (e) {
        this.statusHtml = `<span class="status-failed">${escapeHtml(e.message)}</span>`;
        this.busy = false;
      }
    },
  };
}
```

### Step 4: Create `frontend/js/summaries.js` — Summaries 탭 Alpine 컴포넌트 (캘린더 뷰 포함)

```js
// frontend/js/summaries.js
// Summaries 탭 — 캘린더 뷰 + 계층 네비게이션

import { api, copyToClipboard } from "./api.js";

export function summariesComponent() {
  const now = new Date();
  return {
    type: "daily",
    year: now.getFullYear(),
    month: now.getMonth() + 1,
    week: null,
    // Calendar state
    available: { daily: [], weekly: [], monthly: [], yearly: false },
    calendarDays: [],
    selectedDate: null,
    // Content
    content: "",
    contentRaw: "",
    error: "",
    loading: false,
    copySuccess: false,

    async init() {
      await this.loadAvailable();
      this.buildCalendar();
    },

    async loadAvailable() {
      try {
        const resp = await api("GET", `/summaries/available?year=${this.year}&month=${this.month}`);
        this.available = await resp.json();
      } catch {
        this.available = { daily: [], weekly: [], monthly: [], yearly: false };
      }
    },

    buildCalendar() {
      const days = [];
      const firstDay = new Date(this.year, this.month - 1, 1);
      // Monday = 0 기반 (ISO 기준)
      let startDow = firstDay.getDay();
      startDow = startDow === 0 ? 6 : startDow - 1; // Sun=6, Mon=0, ...

      // 빈 칸 채우기
      for (let i = 0; i < startDow; i++) {
        days.push({ day: null, hasData: false, date: null });
      }

      const daysInMonth = new Date(this.year, this.month, 0).getDate();
      const mm = String(this.month).padStart(2, "0");

      for (let d = 1; d <= daysInMonth; d++) {
        const dd = String(d).padStart(2, "0");
        const dateKey = `${mm}-${dd}`;
        const fullDate = `${this.year}-${mm}-${dd}`;
        days.push({
          day: d,
          hasData: this.available.daily.includes(dateKey),
          date: fullDate,
        });
      }
      this.calendarDays = days;
    },

    async prevMonth() {
      this.month--;
      if (this.month < 1) { this.month = 12; this.year--; }
      await this.loadAvailable();
      this.buildCalendar();
      this.content = "";
      this.selectedDate = null;
    },

    async nextMonth() {
      this.month++;
      if (this.month > 12) { this.month = 1; this.year++; }
      await this.loadAvailable();
      this.buildCalendar();
      this.content = "";
      this.selectedDate = null;
    },

    async selectDate(date) {
      if (!date) return;
      this.selectedDate = date;
      this.type = "daily";
      await this.loadSummary(`/summary/daily/${date}`);
    },

    async selectWeekly(week) {
      this.type = "weekly";
      await this.loadSummary(`/summary/weekly/${this.year}/${week.replace("W", "")}`);
    },

    async selectMonthly(month) {
      this.type = "monthly";
      await this.loadSummary(`/summary/monthly/${this.year}/${parseInt(month, 10)}`);
    },

    async selectYearly() {
      this.type = "yearly";
      await this.loadSummary(`/summary/yearly/${this.year}`);
    },

    async loadSummary(path) {
      this.loading = true;
      this.error = "";
      this.content = "";
      this.contentRaw = "";
      try {
        const resp = await api("GET", path);
        if (resp.status === 404) {
          this.error = "Summary not found.";
          return;
        }
        const data = await resp.json();
        this.contentRaw = data.content;
        this.content = marked.parse(data.content);
      } catch (e) {
        this.error = e.message;
      } finally {
        this.loading = false;
      }
    },

    async copy() {
      const ok = await copyToClipboard(this.contentRaw);
      if (ok) {
        this.copySuccess = true;
        setTimeout(() => (this.copySuccess = false), 2000);
      }
    },
  };
}
```

### Step 5: Create `frontend/js/ask.js` — Ask 탭 Alpine 컴포넌트 (대화 히스토리)

```js
// frontend/js/ask.js
// Ask 탭 — 빠른 질문 + 대화 히스토리

import { api, pollJob, escapeHtml, copyToClipboard } from "./api.js";

const QUICK_QUESTIONS = [
  { label: "이번 주 요약", question: "이번 주 활동을 3줄로 요약해줘", months: 1 },
  { label: "이번 달 핵심 성과", question: "이번 달 가장 임팩트 있는 활동 3가지는?", months: 1 },
  { label: "가장 리뷰 많은 PR", question: "최근 가장 많은 리뷰 코멘트가 달린 PR은?", months: 3 },
];

export function askComponent() {
  return {
    question: "",
    months: 3,
    busy: false,
    messages: [],
    quickQuestions: QUICK_QUESTIONS,

    useQuick(q) {
      this.question = q.question;
      this.months = q.months;
    },

    async ask() {
      const q = this.question.trim();
      if (!q) return alert("Please enter a question.");

      this.messages.push({ role: "user", text: q });
      this.busy = true;
      const months = this.months;
      this.question = "";

      try {
        const resp = await api("POST", "/query", { question: q, months });
        const { job_id } = await resp.json();
        pollJob(job_id, (job) => {
          if (job.status === "completed" && job.result) {
            this.messages.push({
              role: "assistant",
              text: job.result,
              html: marked.parse(job.result),
            });
            this.busy = false;
          } else if (job.status === "failed") {
            this.messages.push({
              role: "assistant",
              text: `Error: ${job.error}`,
              html: `<span class="status-failed">${escapeHtml(job.error)}</span>`,
            });
            this.busy = false;
          }
        });
      } catch (e) {
        this.messages.push({
          role: "assistant",
          text: `Error: ${e.message}`,
          html: `<span class="status-failed">${escapeHtml(e.message)}</span>`,
        });
        this.busy = false;
      }
    },

    async copyMessage(msg) {
      await copyToClipboard(msg.text);
    },
  };
}
```

### Step 6: Rewrite `frontend/index.html` — Alpine.js 기반 전환

기존 `frontend/index.html`을 완전히 새로 작성한다. CDN으로 Alpine.js, Chart.js, marked.js를 로드하고, 탭별 Alpine 컴포넌트를 바인딩한다. 전체 HTML은 구현 시 작성 — 핵심 구조:

```html
<!DOCTYPE html>
<html lang="ko" data-theme="light">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>work-recap</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <header class="container" x-data="theme">
    <nav>
      <ul>
        <li><strong>work-recap</strong></li>
      </ul>
      <ul x-data="tabs">
        <li><a href="#" class="tab-link" :class="current==='pipeline' && 'active'" @click.prevent="switch('pipeline')">Pipeline</a></li>
        <li><a href="#" class="tab-link" :class="current==='summaries' && 'active'" @click.prevent="switch('summaries')">Summaries</a></li>
        <li><a href="#" class="tab-link" :class="current==='ask' && 'active'" @click.prevent="switch('ask')">Ask</a></li>
      </ul>
      <ul>
        <li><a href="#" @click.prevent="toggle" x-text="dark ? '☀' : '🌙'"></a></li>
      </ul>
    </nav>
  </header>

  <main class="container" x-data="tabs">
    <!-- Pipeline Tab -->
    <section x-show="current==='pipeline'" x-data="pipeline">
      <!-- ... pipeline UI with x-model, @click, x-show ... -->
    </section>

    <!-- Summaries Tab -->
    <section x-show="current==='summaries'" x-data="summaries" x-init="init()">
      <!-- ... calendar grid with x-for, summary viewer ... -->
    </section>

    <!-- Ask Tab -->
    <section x-show="current==='ask'" x-data="ask">
      <!-- ... quick questions, chat history with x-for ... -->
    </section>
  </main>

  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script type="module" src="/js/app.js"></script>
</body>
</html>
```

### Step 7: Update `frontend/style.css` — 캘린더 + 다크 모드 스타일 추가

기존 스타일 유지하면서 추가:

```css
/* Calendar grid */
.calendar-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 2px;
}
.calendar-header {
  text-align: center;
  font-weight: 600;
  font-size: 0.85em;
  padding: 0.25rem;
  color: var(--pico-muted-color);
}
.calendar-day {
  text-align: center;
  padding: 0.4rem 0.2rem;
  border-radius: var(--pico-border-radius);
  font-size: 0.9em;
  cursor: default;
}
.calendar-day.has-data {
  background: var(--pico-primary-background);
  color: var(--pico-primary-inverse);
  cursor: pointer;
}
.calendar-day.has-data:hover {
  opacity: 0.8;
}
.calendar-day.selected {
  outline: 2px solid var(--pico-primary);
  outline-offset: -2px;
}
.calendar-day.empty {
  visibility: hidden;
}

/* Calendar navigation */
.calendar-nav {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.5rem;
}

/* Copy button */
.copy-btn {
  background: none;
  border: 1px solid var(--pico-muted-border-color);
  padding: 0.25rem 0.5rem;
  border-radius: var(--pico-border-radius);
  cursor: pointer;
  font-size: 0.85em;
}
.copy-btn:hover {
  background: var(--pico-secondary-background);
}

/* Chat messages */
.chat-messages {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.chat-msg {
  padding: 0.75rem 1rem;
  border-radius: var(--pico-border-radius);
}
.chat-msg.user {
  background: var(--pico-primary-background);
  color: var(--pico-primary-inverse);
  align-self: flex-end;
  max-width: 80%;
}
.chat-msg.assistant {
  background: var(--pico-card-background-color);
  border: 1px solid var(--pico-muted-border-color);
}

/* Quick question chips */
.quick-questions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 1rem;
}
.quick-btn {
  background: var(--pico-secondary-background);
  border: 1px solid var(--pico-muted-border-color);
  padding: 0.25rem 0.75rem;
  border-radius: 999px;
  font-size: 0.85em;
  cursor: pointer;
}
.quick-btn:hover {
  background: var(--pico-primary-background);
  color: var(--pico-primary-inverse);
}

/* Summary type pills */
.type-pills {
  display: flex;
  gap: 0.25rem;
  margin-bottom: 1rem;
}
.type-pill {
  padding: 0.3rem 0.8rem;
  border-radius: 999px;
  border: 1px solid var(--pico-muted-border-color);
  background: none;
  cursor: pointer;
  font-size: 0.85em;
}
.type-pill.active {
  background: var(--pico-primary);
  color: var(--pico-primary-inverse);
  border-color: var(--pico-primary);
}

/* Weekly/Monthly list items */
.summary-list-item {
  display: flex;
  justify-content: space-between;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--pico-muted-border-color);
  cursor: pointer;
}
.summary-list-item:hover {
  background: var(--pico-secondary-background);
}
.summary-list-item .badge {
  font-size: 0.8em;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
}
.summary-list-item .badge.exists {
  background: #2e7d32;
  color: white;
}
```

### Step 8: Delete old `frontend/app.js`

Remove the old monolithic file since all logic has been moved to `frontend/js/` modules.

### Step 9: Verify manually

Run: `uvicorn workrecap.api.app:app --reload`
Open: `http://localhost:8000`
Verify:
- 3탭(Pipeline/Summaries/Ask) 전환 동작
- Pipeline: 날짜 입력 + Run 버튼 동작 (202 응답 + polling)
- Summaries: 캘린더 표시 + 날짜 클릭 시 summary 로드
- Ask: 빠른 질문 버튼 + 대화 히스토리 누적
- 다크 모드 토글 + localStorage 유지
- 복사 버튼 동작

### Step 10: Commit

```bash
git rm frontend/app.js
git add frontend/index.html frontend/style.css frontend/js/
git commit -m "feat(frontend): migrate to Alpine.js with calendar view, dark mode, copy buttons"
```

---

## Task 3: 기존 API 테스트 + 전체 검증

마이그레이션 후 기존 백엔드 테스트가 모두 통과하는지 확인.

**Files:**
- No changes — verification only

### Step 1: Run full backend test suite

Run: `PYTHONPATH=src pytest -v`
Expected: ALL PASS (1011+ tests including new test_api_summaries_available.py)

### Step 2: Run lint

Run: `ruff check src/ tests/`
Expected: No errors

Run: `ruff format --check src/ tests/`
Expected: No formatting issues

### Step 3: Commit (if any lint fixes needed)

```bash
git add -A
git commit -m "fix: lint fixes after Phase 1 migration"
```

---

## Task 4: CLAUDE.md 업데이트

Phase 1 변경사항을 문서에 반영.

**Files:**
- Modify: `CLAUDE.md` (프론트엔드 구조, 새 API 추가)

### Step 1: Update CLAUDE.md

추가할 내용:
- `summaries_available.py` 라우터 설명
- 프론트엔드 파일 구조 변경 (js/ 디렉토리, ES modules)
- Alpine.js + Chart.js CDN 의존성
- 다크 모드 (`data-theme` + localStorage)

### Step 2: Commit

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for Phase 1 UI changes"
```

---

## Summary

| Task | 내용 | 새 파일 | 테스트 |
|---|---|---|---|
| 1 | Summaries Available API | 2 (route + test) | 7 tests |
| 2 | Alpine.js 마이그레이션 | 5 JS files + HTML/CSS rewrite | 수동 브라우저 |
| 3 | 전체 검증 | 없음 | 기존 1011+ tests |
| 4 | 문서 업데이트 | 없음 | 없음 |
