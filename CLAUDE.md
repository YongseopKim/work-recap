# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Run unit tests (395 tests, integration excluded by default)
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

- `src/git_recap/config.py` — `AppConfig` (pydantic-settings), derives all data paths. Env vars from `.env`.
- `src/git_recap/models.py` — Pydantic/dataclass models (`PRRaw`, `CommitRaw`, `IssueRaw`, `Activity`, `DailyStats`) + JSON/JSONL serialization utilities. `Activity` preserves text context: `body` (PR/commit/issue body), `review_bodies`, `comment_bodies`.
- `src/git_recap/services/fetcher.py` — Searches GHES for PRs (3 axes: author/reviewed-by/commenter), commits, issues. Deduplicates, enriches, filters noise (bots, LGTM). `fetch(date)` returns `dict[str, Path]`. `fetch_range(since, until)` uses monthly chunked range queries for 30x fewer API calls, with skip/force/resilience. Updates `last_fetch_date` checkpoint.
- `src/git_recap/services/normalizer.py` — Converts raw data → `Activity` records + `DailyStats`. Preserves body/review/comment text in Activity fields. Filters by actual timestamp (not search date). Self-reviews excluded. `normalize_range(since, until, force)` with skip/force/resilience. Updates `last_normalize_date` checkpoint.
- `src/git_recap/services/summarizer.py` — Renders Jinja2 prompt templates (`prompts/`), calls LLM, saves markdown. `daily_range(since, until, force)` with skip/force/resilience. Updates `last_summarize_date` checkpoint.
- `src/git_recap/services/orchestrator.py` — Chains Fetch→Normalize→Summarize. `run_daily(date)` and `run_range(since, until)`.
- `src/git_recap/services/date_utils.py` — `date_range`, `weekly_range`, `monthly_range`, `yearly_range`, `catchup_range`, `monthly_chunks`.
- `src/git_recap/cli/main.py` — Typer app. Subcommands: `fetch`, `normalize`, `summarize`, `run`, `ask`. All three data commands support `--force/-f` and checkpoint catch-up mode.
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
data/state/                         → checkpoints.json (last_fetch/normalize/summarize_date), jobs/
```

### Checkpoint & catch-up

All three services (fetch, normalize, summarize daily) share `data/state/checkpoints.json`:
```json
{
  "last_fetch_date": "2026-02-17",
  "last_normalize_date": "2026-02-17",
  "last_summarize_date": "2026-02-17"
}
```

Each service updates only its own key after successful processing. CLI catch-up flow (applies to `fetch`, `normalize`, `summarize daily`):
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
