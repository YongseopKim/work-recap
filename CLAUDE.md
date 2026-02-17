# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Run unit tests (integration excluded by default)
pytest

# Run integration tests (require .env with real credentials)
pytest -m integration -x -v

# Run a single test file
pytest tests/unit/test_fetcher.py -v

# Run a single test by name
pytest tests/unit/test_fetcher.py -k "test_name" -v

# Lint
ruff check src/ tests/

# Format check
ruff format --check src/ tests/

# Coverage
coverage run -m pytest && coverage report

# Run as module
python -m git_recap --help

# Start web UI (dev)
uvicorn git_recap.api.app:app --reload
```

## Architecture

Three-layer pipeline that collects GitHub activity data and generates LLM-based summaries:

```
Interface Layer (CLI: Typer, API: FastAPI)
    ↓
Service Layer (Orchestrator → Fetcher → Normalizer → Summarizer)
    ↓
Infrastructure (GHESClient: httpx, LLMClient: OpenAI/Anthropic)
```

**Pipeline flow:** GHES Search API → `data/raw/` (JSON) → `data/normalized/` (JSONL+stats) → `data/summaries/` (Markdown)

**Hierarchical summarization:** Daily → Weekly → Monthly → Yearly. Each level consumes the level below as LLM input, keeping token usage bounded.

### Key modules

- `src/git_recap/config.py` — `AppConfig` (pydantic-settings), derives all data paths. Env vars from `.env`. `max_workers: int = 5` controls parallel LLM execution (override via `MAX_WORKERS` env var).
- `src/git_recap/models.py` — Pydantic/dataclass models (`PRRaw`, `CommitRaw`, `IssueRaw`, `Activity`, `DailyStats`) + JSON/JSONL serialization utilities. `Activity` preserves text context: `body` (PR/commit/issue body), `review_bodies`, `comment_bodies`.
- `src/git_recap/services/daily_state.py` — `DailyStateStore`: per-date timestamp tracking for fetch/normalize/summarize. Thread-safe (`threading.RLock`). Staleness rules: fetch stale if `fetched_at.date() <= target_date`; normalize stale if `fetch_ts > normalize_ts` (cascade); summarize stale if `normalize_ts > summarize_ts` (cascade). Persists to `data/state/daily_state.json`.
- `src/git_recap/services/fetcher.py` — Searches GHES for PRs (3 axes: author/reviewed-by/commenter), commits, issues. Deduplicates, enriches, filters noise (bots, LGTM). `fetch(date)` returns `dict[str, Path]`. `fetch_range(since, until)` uses monthly chunked range queries for 30x fewer API calls, with skip/force/resilience. When `DailyStateStore` injected, uses `stale_dates()` to narrow API range. Updates `last_fetch_date` checkpoint + daily state. **Parallel mode:** `max_workers > 1` enables ThreadPoolExecutor for date-level parallel enrichment with `GHESClientPool`. **Resumable:** `FetchProgressStore` caches per-chunk search results so interrupted runs can resume without re-executing search API calls.
- `src/git_recap/services/normalizer.py` — Converts raw data → `Activity` records + `DailyStats`. Preserves body/review/comment text in Activity fields. Filters by actual timestamp (not search date). Self-reviews excluded. `normalize_range(since, until, force, max_workers=1)` with skip/force/resilience and optional parallel execution via `ThreadPoolExecutor`. Thread-safe checkpoint updates via `checkpoint.py` with date comparison guard. When `DailyStateStore` injected, uses cascade staleness (re-normalizes if fetch is newer). Updates `last_normalize_date` checkpoint + daily state.
- `src/git_recap/services/summarizer.py` — Renders Jinja2 prompt templates (`prompts/`), calls LLM, saves markdown. `_format_activities` includes `body` (500 char), `review_bodies`/`comment_bodies` (각 200 char truncate) as text context for LLM. `daily_range(since, until, force, max_workers=1)` with skip/force/resilience and optional parallel execution via `ThreadPoolExecutor`. Thread-safe checkpoint updates via `checkpoint.py` with date comparison guard. `weekly(year, week, force)`, `monthly(year, month, force)`, `yearly(year, force)` use mtime-based cascade staleness: weekly regenerates if any daily summary is newer, monthly if any weekly is newer, yearly if any monthly is newer. `--force` bypasses staleness check. When `DailyStateStore` injected, uses cascade staleness for daily (re-summarizes if normalize is newer). Updates `last_summarize_date` checkpoint + daily state.
- `src/git_recap/__main__.py` — `python -m git_recap` entry point.
- `src/git_recap/infra/ghes_client.py` — HTTP client with retry (429 + 403 rate limit + 5xx), search API throttle (`search_interval=2.0s`), pagination, adaptive rate limiting (warns <100, waits <10 remaining). Thread-safe: `_throttle_search()` protected by `threading.Lock`, rate limit state by separate lock. `search_interval` kwarg controls delay between Search API calls (30 req/min limit).
- `src/git_recap/infra/client_pool.py` — `GHESClientPool`: `queue.Queue`-based thread-safe pool of `GHESClient` instances. `acquire(timeout)`/`release(client)`/`client()` context manager/`close()`. Used for parallel enrichment.
- `src/git_recap/services/checkpoint.py` — Thread-safe `update_checkpoint(cp_path, key, value)` utility with module-level `threading.Lock` and date comparison guard. Used by fetcher/normalizer/summarizer.
- `src/git_recap/services/fetch_progress.py` — `FetchProgressStore`: chunk search result caching in `data/state/fetch_progress/` for resumable `fetch_range()`. `save_chunk_search(key, buckets)`/`load_chunk_search(key)`/`clear_chunk(key)`/`clear_all()`.
- `src/git_recap/services/orchestrator.py` — Chains Fetch→Normalize→Summarize. `run_daily(date, types=None)` for single date. `run_range(since, until, force=False, types=None, max_workers=1)` uses bulk `fetch_range`→`normalize_range`→`daily_range` for significantly fewer API calls. Passes `force`, `types`, and `max_workers` through to services. Accepts optional `config` kwarg for path derivation.
- `src/git_recap/services/date_utils.py` — `date_range`, `weekly_range`, `monthly_range`, `yearly_range`, `catchup_range`, `monthly_chunks`.
- `src/git_recap/cli/main.py` — Typer app. Subcommands: `fetch`, `normalize`, `summarize`, `run`, `ask`. All four commands support checkpoint catch-up and `--weekly/--monthly/--yearly` options. All four commands support `--force/-f`. `summarize weekly/monthly/yearly` also support `--force/-f` (skip-if-exists). `fetch` and `run` support `--type/-t` for type filtering. `fetch` and `run` support `--workers/-w` for parallel fetch (default: 1 for fetch, config.max_workers for run). `normalize`, `summarize daily`, `run` support `--workers/-w` for parallel LLM execution (default: `config.max_workers=5`). `normalize` and `run` support `--enrich/--no-enrich` (default: `True`). All inject `DailyStateStore` + `FetchProgressStore` into services.
- `src/git_recap/api/app.py` — FastAPI app. Routes in `api/routes/`. `StaticFiles` mount for `frontend/`.

### Data directory layout

All outputs go under `data/` (file-based, no DB):
```
data/raw/{YYYY}/{MM}/{DD}/          → prs.json, commits.json, issues.json
data/normalized/{YYYY}/{MM}/{DD}/   → activities.jsonl, stats.json
data/summaries/{YYYY}/daily/        → {MM}-{DD}.md
data/summaries/{YYYY}/weekly/       → W{NN}.md
data/summaries/{YYYY}/monthly/      → {MM}.md
data/summaries/{YYYY}/              → yearly.md
data/state/                         → checkpoints.json (last_fetch/normalize/summarize_date), daily_state.json (per-date timestamps), fetch_progress/ (chunk search cache), jobs/
```

### Checkpoint & catch-up

All four commands (fetch, normalize, summarize daily, run) share `data/state/checkpoints.json`:
```json
{
  "last_fetch_date": "2026-02-17",
  "last_normalize_date": "2026-02-17",
  "last_summarize_date": "2026-02-17"
}
```

Per-date timestamps are tracked in `data/state/daily_state.json` via `DailyStateStore`:
```json
{
  "2026-02-17": {
    "fetch": "2026-02-18T08:00:00+00:00",
    "normalize": "2026-02-18T08:01:00+00:00",
    "summarize": "2026-02-18T08:02:00+00:00"
  }
}
```

**Staleness rules (daily — DailyStateStore timestamps):**
- **Fetch**: stale if `fetched_at.date() <= target_date` (same-day fetch may miss evening activity)
- **Normalize (cascade)**: stale if `fetch_ts > normalize_ts` (re-fetched data needs re-normalizing)
- **Summarize (cascade)**: stale if `normalize_ts > summarize_ts` (re-normalized data needs re-summarizing)
- **Range optimization**: `fetch_range` narrows API range to `min(stale)..max(stale)` dates only

**Staleness rules (weekly/monthly/yearly — file mtime comparison):**
- **Weekly**: stale if `max(mtime of daily summaries in week) > mtime of weekly file`
- **Monthly**: stale if `max(mtime of weekly summaries in month) > mtime of monthly file`
- **Yearly**: stale if `max(mtime of monthly summaries in year) > mtime of yearly file`
- Output file absent → always stale. `--force` bypasses staleness check.

Each service updates only its own key after successful processing. CLI catch-up flow (applies to `fetch`, `normalize`, `summarize daily`, `run`):
- No args + checkpoint exists → `catchup_range(last_date)` → range method call
- No args + no checkpoint → run today only
- Empty date list → "Already up to date."
- `--force/-f` flag → skip detection disabled, reprocess all dates
- Range methods (`fetch_range`, `normalize_range`, `daily_range`) return `list[dict]` with `status` per date (success/skipped/failed)

## Testing patterns

- **Unit tests** are in `tests/unit/`. `conftest.py` provides `tmp_data_dir` and `test_config` fixtures (isolated temp directories, dummy env). All unit tests use `.env.test` automatically (autouse fixture overrides `AppConfig.model_config`).
- **Integration tests** are in `tests/integration/`. They use real `.env` credentials to call GitHub API and LLM API. Excluded from default `pytest` runs via `addopts = "-m 'not integration'"`. Run with `pytest -m integration -x -v`. Tests share a class-scoped temp `data_dir` to avoid polluting real `data/`. Use `INTEGRATION_TEST_DATE=YYYY-MM-DD` to override the default test date (3 days ago).
- HTTP mocking: `respx` for `httpx` requests (GHESClient tests).
- `unittest.mock.patch` for service-level mocking. Patch targets must be module-level imports (not local imports inside functions).
- `pytest-asyncio` for async tests (API routes).

## Important conventions

- **Ruff config:** line-length=100, target py312.
- **src layout:** package at `src/git_recap/`, `pythonpath = ["src"]` in pytest config.
- **FastAPI route order:** Static routes (`/run/range`) must be defined BEFORE parameterized routes (`/run/{date}`), otherwise FastAPI matches the literal as a parameter.
- **FastAPI StaticFiles:** `app.mount("/", StaticFiles(...))` must be placed AFTER all `app.include_router()` calls to avoid intercepting API routes.
- **Background tasks + DI:** `dependency_overrides` only works for `Depends()` in route signatures. Background task functions must receive config/store as parameters, not call `get_config()` directly.
- **Exception hierarchy:** `GitRecapError` base → `FetchError`, `NormalizeError`, `SummarizeError`, `StepFailedError`.
- **Thread safety for parallel execution:** `DailyStateStore` (get/set_timestamp), `LLMClient` (_usage accumulation), and service checkpoint updates are protected by `threading.Lock`. Checkpoint writes use date comparison guard (`target_date > existing`) to prevent out-of-order overwrites. Date-specific file paths (raw/normalized/summaries) don't conflict across threads.

## Workflow rules

Every task follows this workflow:

### 1. Plan first (always start in PLAN mode)
- Enter PLAN mode to design the overall approach.
- Review the design for correctness and completeness.
- Break the design into small **sub-plans** (incremental steps).

### 2. Branch & worktree
- Create a topic branch based on the task subject.
- Use `git worktree` to isolate work (`git worktree add ../git-recap-claude-<branch> -b <branch>`).

### 3. Execute sub-plans with TDD
For each sub-plan, follow the TDD cycle:
1. Write a small, focused test first.
2. Implement the minimum code to pass that test.
3. Verify the new test passes.
4. Run the full test suite — **100% pass required** before moving on.
5. Mark the sub-plan as complete and proceed to the next.

### 4. Completion checklist (before finishing the branch)
- All tests pass (`pytest` — 100% pass rate).
- `ruff format --check src/ tests/` and `ruff check src/ tests/` both pass.
- `CLAUDE.md` and `README.md` are updated to reflect any changes.

### 5. Commit
- Create a git commit with a clear message summarizing the work.
