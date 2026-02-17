# git-recap

GitHub Enterprise Server(GHES)의 PR, commit, issue, review, comment 활동 데이터를 수집하여
LLM 기반으로 일/주/월/년 단위 업무 요약을 자동 생성하는 개인 도구.

## 핵심 원칙

- **파일 기반 저장** — DB 없이 모든 중간 산출물이 파일로 남아 재실행/디버깅 가능
- **수치는 스크립트, 서술은 LLM** — PR 수, line count 등은 코드가 계산하고 LLM은 서술만 담당
- **계층적 요약** — daily → weekly → monthly → yearly로 하위 요약을 input으로 사용하여 토큰 관리
- **멱등 파이프라인** — 동일 날짜 재실행 시 파일을 덮어씀
- **범위 최적화** — 다중 날짜 fetch 시 월 단위 range 검색으로 API 호출 30배 절감

## 요구사항

- Python 3.12+
- GHES 인스턴스 + Personal Access Token
- LLM API 키 (OpenAI 또는 Anthropic)

## 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 설정

`.env.example`을 복사하여 `.env` 파일을 생성하고 값을 채운다.

```bash
cp .env.example .env
```

```env
GHES_URL=https://github.example.com
GHES_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
GHES_USERNAME=your-username
LLM_PROVIDER=openai          # openai | anthropic
LLM_API_KEY=sk-xxxxxxxxxxxxxxxxxxxx
LLM_MODEL=gpt-4o-mini
```

## 사용법

### CLI

```bash
# 개별 단계 실행 (단일 날짜)
git-recap fetch 2025-02-16              # GHES에서 PR/Commit/Issue 데이터 수집
git-recap normalize 2025-02-16          # Activity + Stats로 변환
git-recap summarize daily 2025-02-16    # Daily summary 생성
git-recap summarize weekly 2025 7       # Weekly summary 생성
git-recap summarize monthly 2025 2      # Monthly summary 생성
git-recap summarize yearly 2025         # Yearly summary 생성

# 날짜 범위 옵션 (fetch, normalize, summarize daily 공통)
git-recap fetch --since 2025-02-01 --until 2025-02-16   # 기간 범위
git-recap fetch --weekly 2025-7                          # ISO 주 단위
git-recap fetch --monthly 2025-2                         # 월 단위
git-recap fetch --yearly 2025                            # 연 단위
git-recap normalize --since 2025-02-01 --until 2025-02-16
git-recap summarize daily --weekly 2025-7

# fetch 전용 옵션
git-recap fetch --type prs 2025-02-16   # PR만 수집 (prs, commits, issues)

# catch-up: checkpoint 이후 ~ 오늘 (fetch, normalize, summarize daily 공통)
git-recap fetch                         # last_fetch_date 이후 자동 수집
git-recap normalize                     # last_normalize_date 이후 자동 변환
git-recap summarize daily               # last_summarize_date 이후 자동 요약

# --force/-f: 기존 데이터 무시하고 재처리 (fetch, normalize, summarize daily 공통)
git-recap fetch --since 2025-02-01 --until 2025-02-16 --force
git-recap normalize --since 2025-02-01 --until 2025-02-16 --force
git-recap summarize daily --weekly 2025-7 --force

# 전체 파이프라인 (fetch → normalize → summarize)
git-recap run 2025-02-16                # 단일 날짜
git-recap run --since 2025-02-01 --until 2025-02-16  # 기간 범위

# 자유 질문
git-recap ask "이번 달 주요 성과는?"
git-recap ask "Q1에 가장 임팩트 있던 작업?" --months 6
```

### 웹 UI

```bash
uvicorn git_recap.api.app:app --reload
```

`http://localhost:8000`에서 웹 UI 사용:

- **Pipeline** 탭 — 날짜 선택 후 파이프라인 실행, job polling으로 진행 상황 확인
- **Summaries** 탭 — 생성된 daily/weekly/monthly/yearly summary 조회 (markdown 렌더링)
- **Ask** 탭 — 자유 질문 입력 후 LLM 응답 확인

### REST API

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/api/pipeline/run/{date}` | 단일 날짜 파이프라인 실행 (async) |
| POST | `/api/pipeline/run/range` | 기간 범위 실행 (async) |
| GET | `/api/pipeline/jobs/{job_id}` | Job 상태 조회 |
| GET | `/api/summary/daily/{date}` | Daily summary 조회 |
| GET | `/api/summary/weekly/{year}/{week}` | Weekly summary 조회 |
| GET | `/api/summary/monthly/{year}/{month}` | Monthly summary 조회 |
| GET | `/api/summary/yearly/{year}` | Yearly summary 조회 |
| POST | `/api/query` | 자유 질문 (async) |

## 데이터 흐름

```
GHES API
   │
   ▼
┌──────────────┐    ┌────────────────┐    ┌─────────────────┐
│   Fetcher    │───▶│   Normalizer   │───▶│   Summarizer    │
│              │    │                │    │                 │
│ raw/         │    │ normalized/    │    │ summaries/      │
│  prs.json    │    │  activities.   │    │  daily/02-16.md │
│  commits.json│    │  jsonl         │    │  weekly/W07.md  │
│  issues.json │    │  stats.json    │    │  monthly/02.md  │
└──────────────┘    └────────────────┘    └─────────────────┘
```

### 산출물 디렉토리

```
data/
├── raw/{YYYY}/{MM}/{DD}/
│   ├── prs.json                           # PR 원시 데이터
│   ├── commits.json                       # Commit 원시 데이터
│   └── issues.json                        # Issue 원시 데이터
├── normalized/{YYYY}/{MM}/{DD}/
│   ├── activities.jsonl                    # Activity 목록
│   └── stats.json                          # 일일 통계
├── summaries/{YYYY}/
│   ├── daily/{MM}-{DD}.md                  # Daily summary
│   ├── weekly/W{NN}.md                     # Weekly summary
│   ├── monthly/{MM}.md                     # Monthly summary
│   └── yearly.md                           # Yearly summary
└── state/
    ├── checkpoints.json                    # 3개 서비스 마지막 성공 날짜 (fetch/normalize/summarize)
    └── jobs/{job_id}.json                  # Async job 상태
```

## 프로젝트 구조

```
git-recap/
├── src/git_recap/
│   ├── __main__.py            # python -m git_recap 진입점
│   ├── config.py               # AppConfig (pydantic-settings)
│   ├── exceptions.py           # GitRecapError 계층
│   ├── models.py               # 데이터 모델 + 직렬화
│   ├── infra/
│   │   ├── ghes_client.py      # GHES REST API 클라이언트 (retry, rate limit)
│   │   └── llm_client.py       # LLM 클라이언트 (OpenAI, Anthropic)
│   ├── services/
│   │   ├── date_utils.py       # 날짜 범위 유틸리티 (weekly, monthly, yearly, catch-up)
│   │   ├── fetcher.py          # PR/Commit/Issue 데이터 수집 (검색, dedup, enrich)
│   │   ├── normalizer.py       # Activity 변환 + 통계 계산
│   │   ├── summarizer.py       # LLM 요약 생성 (Jinja2 템플릿)
│   │   └── orchestrator.py     # 파이프라인 오케스트레이션
│   ├── cli/
│   │   └── main.py             # Typer CLI
│   └── api/
│       ├── app.py              # FastAPI 앱 (CORS, 정적 파일 서빙)
│       ├── deps.py             # 의존성 주입
│       ├── job_store.py        # Async job 파일 CRUD
│       └── routes/
│           ├── pipeline.py     # 파이프라인 실행 + job polling
│           ├── summary.py      # Summary 조회
│           └── query.py        # 자유 질문
├── frontend/
│   ├── index.html              # SPA (Pico CSS + marked.js)
│   ├── style.css
│   └── app.js
├── prompts/                    # LLM 프롬프트 템플릿 (Jinja2)
│   ├── daily.md
│   ├── weekly.md
│   ├── monthly.md
│   ├── yearly.md
│   └── query.md
├── designs/                    # 모듈별 상세 설계 문서
├── tests/unit/                 # 403개 단위 테스트
├── pyproject.toml
└── .env.example
```

## 테스트

```bash
# 전체 테스트
pytest

# 특정 모듈
pytest tests/unit/test_fetcher.py -v

# 커버리지
coverage run -m pytest && coverage report
```

## 아키텍처

```
┌─────────────────────────────────────────────────┐
│              Interface Layer                     │
│   CLI (Typer)          API (FastAPI)             │
└──────────┬────────────────────┬──────────────────┘
           │                    │
┌──────────▼────────────────────▼──────────────────┐
│              Service Layer                        │
│   OrchestratorService                            │
│     ├── FetcherService                           │
│     ├── NormalizerService                        │
│     └── SummarizerService                        │
└──────────┬────────────────────┬──────────────────┘
           │                    │
┌──────────▼──────────┐ ┌──────▼───────────────────┐
│   GHESClient        │ │   LLMClient              │
│   (httpx + retry)   │ │   (OpenAI / Anthropic)   │
└─────────────────────┘ └──────────────────────────┘
```

- **Interface Layer** (CLI, API)는 Service Layer에 의존
- **Service Layer** 간에는 Orchestrator만 다른 Service를 의존
- 모든 Service는 `AppConfig`를 주입받음

## 설계 결정사항

| 결정 | 내용 |
|------|------|
| D-1: 활동 날짜 기준 | 활동 발생 시각 기준으로 해당 날짜에 포함 (created_at, submitted_at) |
| D-2: PR body 없을 때 | 변경 파일 경로 기반으로 자동 요약 생성 |
| D-3: reviewed-by 미지원 | GHES 422 응답 시 fallback으로 author/commenter 결과에서 review 필터링 |
| D-4: Search API 날짜 필터링 | Fetcher는 후보 수집, Normalizer에서 실제 timestamp로 필터링 |

## 라이선스

Private project.
