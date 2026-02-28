# work-recap

GitHub Enterprise Server(GHES)의 PR, commit, issue, review, comment 활동 데이터를 수집하여
LLM 기반으로 일/주/월/년 단위 업무 요약을 자동 생성하는 개인 도구.

## 핵심 원칙

- **파일 우선 저장** — 모든 중간 산출물이 파일로 남아 재실행/디버깅 가능. PostgreSQL+ChromaDB 선택적 저장 레이어로 구조화 조회 및 시맨틱 검색 지원 (실패 시 graceful degradation)
- **수치는 스크립트, 서술은 LLM** — PR 수, line count 등은 코드가 계산하고 LLM은 서술만 담당
- **계층적 요약** — daily → weekly → monthly → yearly로 하위 요약을 input으로 사용하여 토큰 관리
- **멱등 파이프라인** — 동일 날짜 재실행 시 파일을 덮어씀
- **범위 최적화** — 다중 날짜 fetch 시 월 단위 range 검색으로 API 호출 30배 절감
- **병렬 실행** — `--workers` 옵션으로 fetch enrichment 및 LLM 호출 병렬화
- **재개 가능** — fetch_range 중단 시 chunk 캐시에서 이어서 실행
- **Batch API** — `--batch` 옵션으로 대량 LLM 호출을 일괄 처리 (50% 비용 절감), 동적 타임아웃 (batch 크기 비례)
- **Prompt Caching** — Anthropic `cache_control: ephemeral`로 system prompt 캐싱 (반복 호출 시 input 비용 90% 절감)
- **Cache-aware 비용 추적** — provider별 cache 할인 반영한 실시간 비용 계산
- **Rate Limit 복원력** — GitHub API rate limit 시 7회 재시도, 3단계 대기 전략 (Retry-After → X-RateLimit-Reset → 지수 백오프), jitter로 thundering herd 방지
- **실패 날짜 자동 재시도** — 일시적 오류(timeout/429/5xx)는 다음 실행 시 자동 재시도 (최대 5회), 영구 오류(404/403/422)는 즉시 제외
- **동적 Batch 타임아웃** — batch 크기에 비례하는 타임아웃 (10건→10분, 100건→55분, 500건+→4시간) + 적응형 폴링

## 요구사항

- Python 3.12+
- GHES 인스턴스 + Personal Access Token
- LLM API 키 (OpenAI, Anthropic, Gemini, 또는 OpenAI-compatible 서버)

## 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

> **Note:** `.venv/`는 프로젝트 루트에 위치하는 로컬 가상환경이다. `.gitignore`에 포함되어 있으므로 커밋되지 않는다. `git worktree`에서는 `pip install -e .`를 실행하지 말 것 — editable install은 단일 경로만 기록하므로 worktree 제거 시 메인 venv의 import가 깨진다. 대신 `PYTHONPATH=src pytest`를 사용한다.

## 설정

`.env.example`을 복사하여 `.env` 파일을 생성하고 값을 채운다.

```bash
cp .env.example .env
```

```env
GHES_URL=https://github.example.com
GHES_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
GHES_USERNAME=your-username
MAX_WORKERS=5                 # 병렬 실행 워커 수 (기본: 5)
```

### LLM Provider 설정

`.provider/config.toml`에서 태스크별로 다른 provider+model을 설정한다.

```toml
# .provider/config.toml

[strategy]
mode = "adaptive"  # economy | standard | premium | adaptive | fixed

[providers.openai]
api_key = "sk-..."

[providers.anthropic]
api_key = "sk-ant-..."

[providers.gemini]
api_key = "AIza..."

[providers.custom]
api_key = ""
base_url = "http://localhost:11434/v1"  # Ollama, vLLM 등

[tasks.enrich]
provider = "anthropic"
model = "claude-haiku-4-5"
escalation_model = "claude-sonnet-4-6"
max_tokens = 1024

[tasks.daily]
provider = "anthropic"
model = "claude-sonnet-4-6"
max_tokens = 4096

[tasks.weekly]
provider = "anthropic"
model = "claude-sonnet-4-6"
max_tokens = 4096

[tasks.monthly]
provider = "anthropic"
model = "claude-opus-4-6"
max_tokens = 4096

[tasks.yearly]
provider = "anthropic"
model = "claude-opus-4-6"
max_tokens = 4096

[tasks.query]
provider = "anthropic"
model = "claude-sonnet-4-6"
max_tokens = 4096
```

**Strategy 모드:**

| 모드 | 동작 |
|------|------|
| `economy` | base_model만, escalation 없음 |
| `standard` | base_model + escalation 가능 |
| `premium` | escalation_model 직접 사용 (있으면) |
| `adaptive` | 경량 모델 → 자체 판단 → 필요시 escalation |
| `fixed` | task config의 model 그대로, escalation 없음 |

## 사용법

### CLI

```bash
# 개별 단계 실행 (단일 날짜)
recap fetch 2025-02-16              # GHES에서 PR/Commit/Issue 데이터 수집
recap normalize 2025-02-16          # Activity + Stats로 변환
recap summarize daily 2025-02-16    # Daily summary 생성
recap summarize weekly 2025 7       # Weekly summary 생성
recap summarize monthly 2025 2      # Monthly summary 생성
recap summarize yearly 2025         # Yearly summary 생성

# 날짜 범위 옵션 (fetch, normalize, summarize daily 공통)
recap fetch --since 2025-02-01 --until 2025-02-16   # 기간 범위
recap fetch --weekly 2025-7                          # ISO 주 단위
recap fetch --monthly 2025-2                         # 월 단위
recap fetch --yearly 2025                            # 연 단위
recap normalize --since 2025-02-01 --until 2025-02-16
recap summarize daily --weekly 2025-7

# fetch 전용 옵션
recap fetch --type prs 2025-02-16   # PR만 수집 (prs, commits, issues)
recap fetch --workers 3             # 병렬 enrichment (기본: 1)

# normalize 전용 옵션
recap normalize --no-enrich 2025-02-16  # LLM enrichment 생략
recap normalize --workers 3             # 병렬 LLM 호출 (기본: config.max_workers)

# catch-up: checkpoint 이후 ~ 오늘 (fetch, normalize, summarize daily, run 공통)
recap fetch                         # last_fetch_date 이후 자동 수집
recap normalize                     # last_normalize_date 이후 자동 변환
recap summarize daily               # last_summarize_date 이후 자동 요약
recap run                           # last_summarize_date 이후 전체 파이프라인

# --force/-f: 기존 데이터 무시하고 재처리 (fetch, normalize, summarize daily 공통)
recap fetch --since 2025-02-01 --until 2025-02-16 --force
recap normalize --since 2025-02-01 --until 2025-02-16 --force
recap summarize daily --weekly 2025-7 --force

# Batch API (normalize, summarize daily, run에서 사용 가능)
recap normalize --batch --since 2025-02-01 --until 2025-02-16  # 50% 비용 절감
recap summarize daily --batch --weekly 2025-7
recap run --batch --since 2025-02-01 --until 2025-02-16

# 전체 파이프라인 (fetch → normalize → summarize)
recap run 2025-02-16                # 단일 날짜
recap run --since 2025-02-01 --until 2025-02-16  # 기간 범위
recap run --weekly 2025-7                          # ISO 주 단위 + weekly summary
recap run --monthly 2025-2                         # 월 단위 + weekly→monthly summary
recap run --yearly 2025                            # 연 단위 + weekly→monthly→yearly summary
recap run --type prs --workers 3    # 타입 필터 + 병렬 실행
recap run --no-enrich               # LLM enrichment 생략
recap run                           # catch-up (last_summarize_date 이후 자동)

# 자유 질문
recap ask "이번 달 주요 성과는?"
recap ask "Q1에 가장 임팩트 있던 작업?" --months 6

# 모델 탐색
recap models                           # 설정된 provider별 모델 목록
```

### 웹 UI

```bash
uvicorn workrecap.api.app:app --reload
```

`http://localhost:8000`에서 웹 UI 사용:

- **Pipeline** 탭 — 날짜 선택 후 파이프라인 실행, job polling으로 진행 상황 확인
- **Summaries** 탭 — 생성된 daily/weekly/monthly/yearly summary 조회 (markdown 렌더링)
- **Ask** 탭 — 자유 질문 입력 후 LLM 응답 확인

### REST API

모든 POST 엔드포인트는 비동기 (202 Accepted + job_id) 방식으로 동작하며 `GET /api/pipeline/jobs/{job_id}`로 폴링한다.

**파이프라인 실행**

| Method | Endpoint | 설명 | Body |
|--------|----------|------|------|
| POST | `/api/pipeline/run/{date}` | 단일 날짜 파이프라인 | force?, types?, enrich? |
| POST | `/api/pipeline/run/range` | 기간 범위 파이프라인 | since, until, force?, types?, max_workers?, enrich?, batch?, summarize_weekly/monthly/yearly? |
| GET | `/api/pipeline/jobs/{job_id}` | Job 상태 조회 | — |

**개별 단계 실행**

| Method | Endpoint | 설명 | Body |
|--------|----------|------|------|
| POST | `/api/pipeline/fetch/{date}` | 단일 날짜 fetch | types?, force? |
| POST | `/api/pipeline/fetch/range` | 기간 범위 fetch | since, until, types?, force?, max_workers? |
| POST | `/api/pipeline/normalize/{date}` | 단일 날짜 normalize | enrich?, force? |
| POST | `/api/pipeline/normalize/range` | 기간 범위 normalize | since, until, force?, enrich?, max_workers?, batch? |
| POST | `/api/pipeline/summarize/daily/{date}` | 단일 날짜 summarize | force? |
| POST | `/api/pipeline/summarize/daily/range` | 기간 범위 summarize | since, until, force?, max_workers?, batch? |
| POST | `/api/pipeline/summarize/weekly` | Weekly summary 생성 | year, week, force? |
| POST | `/api/pipeline/summarize/monthly` | Monthly summary 생성 | year, month, force? |
| POST | `/api/pipeline/summarize/yearly` | Yearly summary 생성 | year, force? |

**조회**

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/summary/daily/{date}` | Daily summary 조회 |
| GET | `/api/summary/weekly/{year}/{week}` | Weekly summary 조회 |
| GET | `/api/summary/monthly/{year}/{month}` | Monthly summary 조회 |
| GET | `/api/summary/yearly/{year}` | Yearly summary 조회 |
| POST | `/api/query` | 자유 질문 (body: question, months?) |

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
    ├── daily_state.json                    # 날짜별 fetch/normalize/summarize 타임스탬프
    ├── failed_dates.json                   # 실패 날짜 자동 재시도 추적
    ├── batch_jobs.json                     # Batch API job 상태 (crash recovery)
    ├── fetch_progress/                     # fetch_range 재개용 chunk 캐시
    └── jobs/{job_id}.json                  # Async job 상태
```

## 프로젝트 구조

```
work-recap/
├── .venv/                     # Python 가상환경 (로컬, .gitignore 대상)
├── src/workrecap/
│   ├── __main__.py            # python -m workrecap 진입점
│   ├── config.py               # AppConfig (pydantic-settings)
│   ├── exceptions.py           # WorkRecapError 계층
│   ├── logging_config.py       # 로깅 설정 (stderr, 서드파티 억제)
│   ├── models.py               # 데이터 모델 + 직렬화
│   ├── infra/
│   │   ├── ghes_client.py      # GHES REST API 클라이언트 (retry, rate limit)
│   │   ├── llm_client.py       # [Deprecated] 레거시 LLM 클라이언트
│   │   ├── llm_router.py       # LLM Router (task-based multi-provider routing)
│   │   ├── provider_config.py  # .provider/config.toml 파싱
│   │   ├── escalation.py       # Adaptive escalation handler
│   │   ├── usage_tracker.py    # Per-model usage tracking + cost estimation
│   │   ├── pricing.py          # Built-in pricing table ($/1M tokens, cache-aware)
│   │   ├── model_discovery.py  # Provider별 모델 목록 탐색
│   │   ├── client_pool.py      # GHESClientPool (병렬 enrichment용 스레드 안전 풀)
│   │   └── providers/
│   │       ├── base.py         # LLMProvider ABC + ModelInfo
│   │       ├── batch_mixin.py  # BatchCapable ABC + BatchRequest/Result/Status
│   │       ├── openai_provider.py   # (+ BatchCapable)
│   │       ├── anthropic_provider.py  # (+ BatchCapable, prompt caching)
│   │       ├── gemini_provider.py   # (+ BatchCapable, cache metrics)
│   │       └── custom_provider.py  # OpenAI-compatible (Ollama, vLLM 등)
│   ├── services/
│   │   ├── date_utils.py       # 날짜 범위 유틸리티 (weekly, monthly, yearly, catch-up)
│   │   ├── fetcher.py          # PR/Commit/Issue 데이터 수집 (검색, dedup, enrich, 병렬)
│   │   ├── normalizer.py       # Activity 변환 + LLM enrichment + 통계 계산
│   │   ├── summarizer.py       # LLM 요약 생성 (Jinja2 템플릿, 계층적 staleness)
│   │   ├── orchestrator.py     # 파이프라인 오케스트레이션
│   │   ├── protocols.py        # DataSourceFetcher/Normalizer Protocol 정의
│   │   ├── source_registry.py  # SourceRegistry (멀티 소스 팩토리 레지스트리)
│   │   ├── daily_state.py      # DailyStateStore (날짜별 cascade staleness 추적)
│   │   ├── batch_state.py      # BatchStateStore (batch job 상태 persist/crash recovery)
│   │   ├── failed_dates.py     # FailedDateStore (실패 날짜 자동 재시도 추적)
│   │   ├── checkpoint.py       # 스레드 안전 체크포인트 업데이트
│   │   └── fetch_progress.py   # FetchProgressStore (fetch 재개용 chunk 캐시)
│   ├── cli/
│   │   └── main.py             # Typer CLI (fetch, normalize, summarize, run, ask, models)
│   └── api/
│       ├── app.py              # FastAPI 앱 (CORS, 정적 파일 서빙)
│       ├── deps.py             # 의존성 주입 (get_config, get_job_store, get_llm_router)
│       ├── job_store.py        # Async job 파일 CRUD
│       └── routes/
│           ├── pipeline.py     # 전체 파이프라인 실행 + job polling
│           ├── fetch.py        # 개별 fetch 엔드포인트
│           ├── normalize.py    # 개별 normalize 엔드포인트
│           ├── summarize_pipeline.py  # 개별 summarize 엔드포인트
│           ├── summary.py      # Summary 조회
│           └── query.py        # 자유 질문
├── frontend/
│   ├── index.html              # SPA (Pico CSS + marked.js)
│   ├── style.css
│   └── app.js
├── prompts/                    # LLM 프롬프트 템플릿 (Jinja2, <!-- SPLIT --> 마커)
│   ├── daily.md
│   ├── weekly.md
│   ├── monthly.md
│   ├── yearly.md
│   ├── enrich.md               # Activity LLM enrichment (intent, change_summary)
│   └── query.md
├── pricing.toml                # LLM 가격표 (USD/1M tokens, 코드 변경 없이 업데이트)
├── designs/                    # 모듈별 상세 설계 문서
├── tests/
│   ├── unit/                   # 1011개 단위 테스트 (42개 파일)
│   └── integration/            # 통합 테스트 (실제 API 호출, -m integration)
├── pyproject.toml
└── .env.example
```

## 테스트

```bash
# 전체 단위 테스트 (1011개)
pytest

# 통합 테스트 (실제 GHES + LLM API 호출, .env 필요)
pytest -m integration -x -v
# 통합 테스트 날짜 지정
INTEGRATION_TEST_DATE=2026-02-14 pytest -m integration -x -v

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
│     ├── FetcherService     (+ GHESClientPool)    │
│     ├── NormalizerService  (+ LLMRouter)          │
│     └── SummarizerService  (+ LLMRouter)          │
│                                                  │
│   DailyStateStore · BatchStateStore · Checkpoint  │
└──────────┬────────────────────┬──────────────────┘
           │                    │
┌──────────▼──────────┐ ┌──────▼───────────────────┐
│   GHESClient        │ │   LLMRouter              │
│   (httpx + retry)   │ │   (task-based routing)   │
└─────────────────────┘ └──────┬───────────────────┘
                               │
                  ┌────────────┼────────────┐
                  ▼            ▼            ▼
             OpenAI      Anthropic     Gemini/Custom
```

- **Interface Layer** (CLI, API)는 Service Layer에 의존
- **Service Layer** 간에는 Orchestrator만 다른 Service를 의존
- 모든 Service는 `AppConfig`를 주입받음
- **병렬 실행**: `GHESClientPool` (fetch enrichment), `ThreadPoolExecutor` (날짜별 처리)
- **Batch 실행**: `--batch` 시 normalizer/summarizer가 전체 날짜를 1개 batch로 제출 → 50% 비용 절감
- **Prompt Caching**: system prompt에 `cache_control: ephemeral` 적용, 반복 호출 시 Anthropic input 비용 90% 절감
- **Cascade staleness**: fetch → normalize → summarize 순서로 상위 단계가 갱신되면 하위 재처리

## 설계 결정사항

| 결정 | 내용 |
|------|------|
| D-1: 활동 날짜 기준 | 활동 발생 시각 기준으로 해당 날짜에 포함 (created_at, submitted_at) |
| D-2: PR body 없을 때 | 변경 파일 경로 기반으로 자동 요약 생성 |
| D-3: reviewed-by 미지원 | GHES 422 응답 시 fallback으로 author/commenter 결과에서 review 필터링 |
| D-4: Search API 날짜 필터링 | Fetcher는 후보 수집, Normalizer에서 실제 timestamp로 필터링 |
| D-5: Sync-over-async | API BackgroundTasks 내에서 동기 서비스 코드 실행 (async 불필요) |
| D-6: LLM enrichment | Normalize 단계에서 intent/change_summary 추출, 실패 시 graceful degradation |
| D-7: 계층적 요약 | weekly/monthly/yearly는 하위 단계 요약을 input으로 사용하여 토큰 효율 확보 |
| D-8: Multi-provider routing | 태스크별(enrich/daily/weekly/monthly/yearly/query) 다른 provider+model 배정. `.provider/config.toml`이 단일 설정 소스 |
| D-9: Adaptive escalation | 경량 모델이 자체 판단(confidence 0.0-1.0)으로 고급 모델에 에스컬레이션. JSON envelope 파싱 실패 시 원본 응답 사용 (graceful fallback) |
| D-10: Auto-logging | `.log/YYYYMMDD_HHMMSS.log`에 DEBUG 레벨 자동 기록. LLM usage report 포함 |
| D-11: Prompt caching | `cache_system_prompt=True` 기본값. Anthropic `cache_control: ephemeral` (5분 TTL, input 90% 할인). OpenAI/Gemini는 자동 캐싱으로 플래그 무시. `<!-- SPLIT -->` 마커로 정적 instructions(캐시) / 동적 data(비캐시) 분리 |
| D-12: max_tokens per task | config.toml `max_tokens`로 태스크별 출력 제한 설정. output format에 바인딩 — escalation 시에도 동일 값 유지. 해상도: explicit kwarg > config.toml > None |
| D-13: Batch API | `--batch` 옵션으로 50% 비용 절감 (기본 off). BatchCapable mixin으로 provider 수준 지원 (Anthropic/OpenAI/Gemini). base_model만 사용 (escalation 없음). crash recovery용 BatchStateStore |
| D-14: Cache-aware pricing | `pricing.toml`에서 코드 변경 없이 가격 업데이트. provider별 cache discount factor 적용 (Anthropic 90% read / 25% write, OpenAI 50% read, Gemini 75% read) |
| D-15: Rate limit resilience | GitHub 문서 기반: rate limit(429/403)과 server error(5xx) 재시도 카운터 분리 (7회/3회). 3단계 대기: Retry-After → X-RateLimit-Reset → 지수 백오프(2^n, 5분 cap). ±25% jitter로 thundering herd 방지. "반복 요청 시 integration 밴 가능" 경고 준수 |
| D-16: Failed date auto-retry | `FailedDateStore`로 실패 날짜 영속화. 영구 오류(404/403 non-rate-limit/422) 즉시 제외, 일시적 오류 max_fetch_retries까지 재시도. 10년 히스토리 실행 시 간헐적 실패에서 자동 복구 |
| D-17: Dynamic batch timeout | `min(300 + 30*N, 14400)` 공식으로 batch 크기 비례 타임아웃. 10건→10분(빠른 피드백), 4000건→4시간(10년 히스토리). 적응형 폴링(5s→60s 선형 증가)으로 불필요한 API 호출 절감 |

## 라이선스

Private project.
