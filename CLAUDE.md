# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Run all tests (296 tests)
pytest

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
- `src/git_recap/models.py` — Pydantic/dataclass models (`PRRaw`, `CommitRaw`, `IssueRaw`, `Activity`, `DailyStats`) + JSON/JSONL serialization utilities.
- `src/git_recap/services/fetcher.py` — Searches GHES for PRs (3 axes: author/reviewed-by/commenter), commits, issues. Deduplicates, enriches, filters noise (bots, LGTM). Returns `dict[str, Path]`.
- `src/git_recap/services/normalizer.py` — Converts raw data → `Activity` records + `DailyStats`. Filters by actual timestamp (not search date). Self-reviews excluded.
- `src/git_recap/services/summarizer.py` — Renders Jinja2 prompt templates (`prompts/`), calls LLM, saves markdown.
- `src/git_recap/services/orchestrator.py` — Chains Fetch→Normalize→Summarize. `run_daily(date)` and `run_range(since, until)`.
- `src/git_recap/services/date_utils.py` — `date_range`, `weekly_range`, `monthly_range`, `yearly_range`, `catchup_range`.
- `src/git_recap/cli/main.py` — Typer app. Subcommands: `fetch`, `normalize`, `summarize`, `run`, `ask`.
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
data/state/                         → checkpoints.json, jobs/
```

## Testing patterns

- Tests are in `tests/unit/`. `conftest.py` provides `tmp_data_dir` and `test_config` fixtures (isolated temp directories, dummy env).
- HTTP mocking: `respx` for `httpx` requests (GHESClient tests).
- `unittest.mock.patch` for service-level mocking. Patch targets must be module-level imports (not local imports inside functions).
- `pytest-asyncio` for async tests (API routes).
- All tests use `.env.test` automatically (autouse fixture overrides `AppConfig.model_config`).

## Important conventions

- **Ruff config:** line-length=100, target py312.
- **src layout:** package at `src/git_recap/`, `pythonpath = ["src"]` in pytest config.
- **FastAPI route order:** Static routes (`/run/range`) must be defined BEFORE parameterized routes (`/run/{date}`), otherwise FastAPI matches the literal as a parameter.
- **FastAPI StaticFiles:** `app.mount("/", StaticFiles(...))` must be placed AFTER all `app.include_router()` calls to avoid intercepting API routes.
- **Background tasks + DI:** `dependency_overrides` only works for `Depends()` in route signatures. Background task functions must receive config/store as parameters, not call `get_config()` directly.
- **Exception hierarchy:** `GitRecapError` base → `FetchError`, `NormalizeError`, `SummarizeError`, `StepFailedError`.
