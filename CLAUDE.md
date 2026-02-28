# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Virtual environment

- 프로젝트 루트의 `.venv/`가 공식 가상환경. 명령 실행 전 `source .venv/bin/activate` 필요.
- `.venv`에 이미 editable install 완료 상태 — 재설치 불필요.
- **Worktree 주의:** `git worktree`에서 `pip install -e .` 금지. editable install은 단일 경로만 기록하므로 worktree 제거 시 메인 venv import가 깨짐. 대신 `PYTHONPATH=src pytest` 사용.

## Commands

```bash
# Install (editable, with dev deps) — 최초 1회만
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
python -m workrecap --help

# Start web UI (dev)
uvicorn workrecap.api.app:app --reload
```

## Architecture

Three-layer pipeline that collects GitHub activity data and generates LLM-based summaries:

```
Interface Layer (CLI: Typer, API: FastAPI)
    ↓
Service Layer (Orchestrator → Fetcher → Normalizer → Summarizer → StorageService)
    ↓
Infrastructure (GHESClient: httpx, LLMRouter: multi-provider routing,
                PostgresClient: asyncpg, VectorDBClient: chromadb, EmbeddingClient: httpx→TEI)
    ↓
Providers (OpenAI, Anthropic, Gemini, Custom/OpenAI-compatible)
```

**Pipeline flow:** GHES Search API → `data/raw/` (JSON) → `data/normalized/` (JSONL+stats) → `data/summaries/` (Markdown)

**Optional storage layer:** File outputs are the primary storage (file-first). PostgreSQL + ChromaDB provide optional structured query and semantic search capabilities. Storage failures never break the pipeline (graceful degradation).

**Hierarchical summarization:** Daily → Weekly → Monthly → Yearly. Each level consumes the level below as LLM input, keeping token usage bounded.

### Key modules

- `src/workrecap/config.py` — `AppConfig` (pydantic-settings), derives all data paths. Env vars from `.env` (GHES connection + file paths only; LLM settings live in `.provider/config.toml`). `extra="ignore"` so leftover `LLM_*` vars in `.env` are harmless. `max_workers: int = 5` controls parallel LLM execution (override via `MAX_WORKERS` env var). `max_fetch_retries: int = 5` controls how many times a failed date is retried before marking it exhausted (override via `MAX_FETCH_RETRIES` env var). `enabled_sources: list[str] = ["github"]` for multi-source support. `provider_config_path` property returns `Path(".provider/config.toml")`. **Storage settings:** `postgres_url: str` (default `postgresql+asyncpg://pkb_test:pkb_test@192.168.0.2:5433/work_recap`), `chroma_host: str` (default `192.168.0.2`), `chroma_port: int` (default `9000`), `chroma_collection: str` (default `work_recap_summaries`), `tei_url: str` (default `http://192.168.0.2:8090`).
- `src/workrecap/models.py` — Dataclass models + JSON/JSONL serialization utilities. Raw models: `FileChange`, `Comment`, `Review`, `PRRaw`, `CommitRaw`, `IssueRaw`. Normalized: `ActivityKind` (enum, includes GitHub + Confluence/Jira stubs), `Activity` (with `source: str = "github"` and `external_id: int`), `GitHubStats`, `ConfluenceStats` (stub), `JiraStats` (stub), `DailyStats` (nested: `date` + `github: GitHubStats` + `confluence: ConfluenceStats` + `jira: JiraStats`). Async job: `JobStatus` (enum), `Job`. LLM tracking: `TokenUsage` (supports `+` operator, includes `cache_read_tokens` and `cache_write_tokens` fields), `ModelUsage` (per-provider/model breakdown with cost, includes cache token fields). Factory functions: `pr_raw_from_dict()`, `commit_raw_from_dict()`, `issue_raw_from_dict()`, `activity_from_dict()`, `github_stats_from_dict()`, `confluence_stats_from_dict()`, `jira_stats_from_dict()`, `daily_stats_from_dict()` (backward-compat: flat→nested migration). `Activity` preserves text context: `body` (PR/commit/issue body), `review_bodies`, `comment_bodies`.
- `src/workrecap/exceptions.py` — Exception hierarchy: `WorkRecapError` base → `FetchError`, `NormalizeError`, `SummarizeError`, `StepFailedError`, `StorageError`.
- `src/workrecap/logging_config.py` — `setup_logging(level)`: stderr handler, `HH:MM:SS LEVEL [module] message` format, silences noisy third-party loggers (httpx, openai, anthropic, google). Idempotent. `reset_logging()` for tests. `setup_file_logging(log_dir)`: creates `.log/YYYYMMDD_HHMMSS.log` with DEBUG level FileHandler. Also attaches handler to `workrecap.cli.output` logger (`propagate=False`) for file-only CLI output logging.
- `src/workrecap/services/daily_state.py` — `DailyStateStore`: per-date timestamp tracking for fetch/normalize/summarize. Thread-safe (`threading.RLock`). Staleness rules: fetch stale if `fetched_at.date() <= target_date`; normalize stale if `fetch_ts > normalize_ts` (cascade); summarize stale if `normalize_ts > summarize_ts` (cascade). Persists to `data/state/daily_state.json`.
- `src/workrecap/services/failed_dates.py` — `FailedDateStore`: persistent failed-date tracking for auto-retry on subsequent runs. JSON file at `data/state/failed_dates.json`, thread-safe (`threading.RLock`). `record_failure(date, phase, error)`, `record_success(date, phase)`, `retryable_dates(dates, max_retries)`, `exhausted_dates()`. `_is_permanent_error(error_msg)` classifies 404/403-non-rate-limit/422 as permanent (no retry). CLI reports exhausted dates after range operations.
- `src/workrecap/services/fetcher.py` — `source_name` property → `"github"`. Searches GHES for PRs (3 axes: author/reviewed-by/commenter), commits, issues. Deduplicates, enriches, filters noise (bots, LGTM). `fetch(date)` returns `dict[str, Path]`. `fetch_range(since, until)` uses monthly chunked range queries for 30x fewer API calls, with skip/force/resilience. When `DailyStateStore` injected, uses `stale_dates()` to narrow API range. When `FailedDateStore` injected, merges retryable failed dates into processing and records success/failure per date. Updates `last_fetch_date` checkpoint + daily state. **Parallel mode:** `max_workers > 1` enables ThreadPoolExecutor for date-level parallel enrichment with `GHESClientPool`. **Resumable:** `FetchProgressStore` caches per-chunk search results so interrupted runs can resume without re-executing search API calls.
- `src/workrecap/services/protocols.py` — `DataSourceFetcher` and `DataSourceNormalizer` Protocols (`@runtime_checkable`). Define the interface that any data source fetcher/normalizer must satisfy. `source_name` property + `fetch()`/`fetch_range()` and `normalize()`/`normalize_range()` methods.
- `src/workrecap/services/source_registry.py` — `SourceRegistry`: registers source name → fetcher/normalizer factory pairs. `register()`, `get_fetcher()`, `get_normalizer()`, `available_sources()`.
- `src/workrecap/services/normalizer.py` — Converts raw data → `Activity` records + `DailyStats` (nested `GitHubStats`). `source_name` property → `"github"`. Preserves body/review/comment text in Activity fields. Filters by actual timestamp (not search date). Self-reviews excluded. LLM enrichment uses `json_mode=True` and `cache_system_prompt=True` for reliable structured output with prompt caching. Enrich prompt split via `<!-- SPLIT -->` marker: static instructions → system prompt (cacheable), dynamic activity data → user content. `normalize_range(since, until, force, max_workers=1, batch=False)` with skip/force/resilience and optional parallel execution via `ThreadPoolExecutor`. **Batch mode:** `batch=True` → normalizes all dates without enrichment first, then batch-enriches all at once via `submit_batch/wait_for_batch` (with `cache_system_prompt=True`). Thread-safe checkpoint updates via `checkpoint.py` with date comparison guard. When `DailyStateStore` injected, uses cascade staleness (re-normalizes if fetch is newer). Updates `last_normalize_date` checkpoint + daily state.
- `src/workrecap/services/summarizer.py` — Renders Jinja2 prompt templates (`prompts/`), calls LLM, saves markdown. `_format_activities` includes `intent`, `change_summary`, `body` (1000 char), `review_bodies`/`comment_bodies` (각 500 char truncate) as text context for LLM. Enriched fields (`intent`, `change_summary`) appear after the header line and before Files, enabling richer summaries especially for commits. `_render_split_prompt()` splits templates on `<!-- SPLIT -->` marker: static instructions → system prompt (cacheable), dynamic data → prepended to user content. All LLM calls (`daily`, `weekly`, `monthly`, `yearly`, `query`) pass `cache_system_prompt=True`. `daily_range(since, until, force, max_workers=1, batch=False)` with skip/force/resilience and optional parallel execution via `ThreadPoolExecutor`. **Batch mode:** `batch=True` → prepares all date prompts, submits single batch, distributes results to per-date markdown files. Thread-safe checkpoint updates via `checkpoint.py` with date comparison guard. `weekly(year, week, force)`, `monthly(year, month, force)`, `yearly(year, force)` use mtime-based cascade staleness: weekly regenerates if any daily summary is newer, monthly if any weekly is newer, yearly if any monthly is newer. `--force` bypasses staleness check. When `DailyStateStore` injected, uses cascade staleness for daily (re-summarizes if normalize is newer). Updates `last_summarize_date` checkpoint + daily state.
- `src/workrecap/__main__.py` — `python -m workrecap` entry point.
- `src/workrecap/infra/ghes_client.py` — HTTP client with resilient retry logic. **Rate limit handling (429 + 403):** separate retry counter (max 7), three-tier wait strategy: `Retry-After` header → `X-RateLimit-Reset` header → exponential backoff (2^n, cap 5 min), with ±25% jitter to prevent thundering herd. **Server errors (5xx):** separate counter (max 3), standard exponential backoff. Search API throttle (`search_interval=2.0s`), pagination, adaptive rate limiting (warns <100, waits <10 remaining). Thread-safe: `_throttle_search()` protected by `threading.Lock`, rate limit state by separate lock. `search_interval` kwarg controls delay between Search API calls (30 req/min limit). Context manager support.
- `src/workrecap/infra/llm_client.py` — **Deprecated.** Legacy `LLMClient` (OpenAI/Anthropic only). Replaced by `LLMRouter`. Kept for reference.
- `src/workrecap/infra/llm_router.py` — `LLMRouter`: drop-in replacement for `LLMClient`. Task-based provider+model routing via `chat(system, user, task="daily")`. Strategy modes: economy, standard, premium, adaptive, fixed. Lazy provider creation with double-checked locking. `usage` property (backward compat) + `usage_tracker` property (per-model breakdown). Integrates `EscalationHandler` for adaptive mode. `chat()` accepts `json_mode`, `max_tokens`, `cache_system_prompt` kwargs; `cache_system_prompt` defaults to `True` (Anthropic: ephemeral cache_control, OpenAI/Gemini: auto-caching, flag ignored); `max_tokens` resolution: explicit kwarg > task config (config.toml) > None (bound to output format, not model — same value on escalation). **Batch methods:** `submit_batch(requests, task)`, `get_batch_status(batch_id, task)`, `get_batch_results(batch_id, task)`, `wait_for_batch(batch_id, task, timeout, poll_interval, batch_size, progress)`. **Dynamic batch timeout:** `batch_size` auto-computes timeout (base 5min + 30s/request, cap 4h); `poll_interval=None` uses adaptive polling (5s→60s linear ramp). Batch uses base_model only (no escalation).
- `src/workrecap/infra/providers/` — Provider abstraction layer. `base.py`: `LLMProvider` ABC + `ModelInfo` dataclass. `chat()` signature: `json_mode` (structured JSON output), `max_tokens` (output limit), `cache_system_prompt` (enable prompt caching). `batch_mixin.py`: `BatchRequest`, `BatchResult`, `BatchStatus`, `BatchCapable` ABC (mixin for providers supporting batch). Concrete: `openai_provider.py` (uses `response_format`, maps `max_tokens` → `max_completion_tokens` for SDK compatibility; reasoning models (gpt-5/o3/o4) skip `max_completion_tokens` since it includes thinking tokens — `_REASONING_PREFIXES` list, `_is_reasoning_model()` check; extracts `cached_tokens` from usage; `BatchCapable` via JSONL file upload), `anthropic_provider.py` (JSON via assistant prefill `[`, caching via `cache_control: {"type": "ephemeral"}`, extracts `cache_read/creation_input_tokens`; `BatchCapable` via `client.messages.batches`), `gemini_provider.py` (JSON via `response_mime_type`, google-genai SDK, extracts `cached_content_token_count` → `cache_read_tokens`; `BatchCapable` via `client.batches`), `custom_provider.py` (OpenAI SDK + base_url for Ollama/vLLM, no batch support).
- `src/workrecap/infra/provider_config.py` — `ProviderConfig`: parses `.provider/config.toml` (TOML, stdlib `tomllib`) for multi-provider task routing. Requires config.toml to exist (no fallback). `TaskConfig` (includes `max_tokens: int | None`), `ProviderEntry` dataclasses. `validate()` checks consistency.
- `src/workrecap/infra/escalation.py` — `EscalationHandler`: adaptive escalation for strategy mode. Uses lean fixed system prompt + original instructions merged into user content (avoids 2x token overhead). Base model self-assesses with `json_mode=True`; escalates to a more capable model if `confidence < 0.7`. Forwards `json_mode`, `max_tokens`, `cache_system_prompt` to escalation call. Graceful fallback on JSON parse failure.
- `src/workrecap/infra/usage_tracker.py` — `UsageTracker`: thread-safe per-provider/model usage tracking with estimated cost via `PricingTable`. `record()` accumulates cache tokens. `model_usages`, `total_usage`, `format_report()` (shows cache read/write stats when present).
- `src/workrecap/infra/pricing.py` — `PricingTable(path=)`: loads $/1M token rates from `pricing.toml` (repo root, git-tracked). `_load_pricing(path)` parses TOML → `{provider: {model: (input, output)}}`. `_normalize_model_name()` strips date suffixes. `get_rate(provider, model)` returns `(input_rate, output_rate)`. `estimate_cost()` supports cache-aware pricing via `_CACHE_FACTORS` per-provider discounts (Anthropic: 90% read discount/25% write surcharge, OpenAI: 50% read discount, Gemini: 75% read discount). Missing file → warning + empty dict (all costs $0).
- `pricing.toml` — LLM pricing data (USD/1M tokens) for OpenAI, Anthropic, Gemini. Format: `[provider]` sections with `"model" = { input = X, output = Y }`. Edit this file to update prices without touching Python code.
- `src/workrecap/infra/model_discovery.py` — `discover_models(providers)`: aggregates `list_models()` across all configured providers. Sorted by (provider, id). Resilient to individual provider failures.
- `src/workrecap/infra/client_pool.py` — `GHESClientPool`: `queue.Queue`-based thread-safe pool of `GHESClient` instances. `acquire(timeout)`/`release(client)`/`client()` context manager/`close()`. Used for parallel enrichment.
- `src/workrecap/infra/postgres_client.py` — `PostgresClient`: async PostgreSQL client using SQLModel + asyncpg. `init_db()` creates tables (`ActivityDB`, `StatsDB`, `SummaryDB`). Write: `save_activities(date, activities)`, `save_stats(stats)`, `save_summary(level, date_key, content, metadata)`. Read: `get_activities(date_str)`, `get_stats(date_str)`, `get_summary(level, date_key)`. All errors wrapped in `StorageError`.
- `src/workrecap/infra/vector_client.py` — `VectorDBClient`: ChromaDB HTTP client for vector similarity search. `add_documents(ids, embeddings, documents, metadatas)`, `search(query_embeddings, n_results)`, `delete_by_metadata(where)`, `close()`. Errors wrapped in `StorageError`.
- `src/workrecap/infra/embedding_client.py` — `EmbeddingClient`: TEI (Text Embeddings Inference) HTTP client via httpx. `embed_queries(texts)`, `embed_documents(texts)` → `list[list[float]]`. Calls `POST {tei_url}/embed`. Connection errors → `StorageError`.
- `src/workrecap/services/storage.py` — `StorageService`: PostgreSQL + ChromaDB integration service. Async core: `save_activities()`, `save_summary()`, `search_summaries()`, `init_db()`, `close()`. Sync wrappers: `*_sync()` methods using `asyncio.run()` (orchestrator is sync). Graceful degradation: all operations catch exceptions and log warnings without raising.
- `src/workrecap/services/batch_state.py` — `BatchStateStore`: persists batch job state to `data/state/batch_jobs.json` for crash recovery. Thread-safe (`threading.Lock`). `save_job()`, `get_job()`, `get_active_jobs()`, `update_status()`, `remove_job()`. Terminal statuses (completed/failed/expired) excluded from `get_active_jobs()`.
- `src/workrecap/services/checkpoint.py` — Thread-safe `update_checkpoint(cp_path, key, value)` utility with module-level `threading.Lock` and date comparison guard. Used by fetcher/normalizer/summarizer.
- `src/workrecap/services/fetch_progress.py` — `FetchProgressStore`: chunk search result caching in `data/state/fetch_progress/` for resumable `fetch_range()`. `save_chunk_search(key, buckets)`/`load_chunk_search(key)`/`clear_chunk(key)`/`clear_all()`.
- `src/workrecap/services/orchestrator.py` — Chains Fetch→Normalize→Summarize. Constructor accepts single fetcher/normalizer (backward compat → wrapped as `{"github": ...}`) or `dict[str, DataSourceFetcher/Normalizer]` for multi-source. Optional `storage: StorageService` kwarg for DB+Vector integration. `run_daily(date, types=None)` for single date — when storage is provided, saves activities/stats/summary to DB after each pipeline step via `_safe_storage_call` (graceful degradation). `run_range(since, until, force=False, types=None, max_workers=1, batch=False)` uses bulk `fetch_range`→`normalize_range`→`daily_range` for significantly fewer API calls. Passes `force`, `types`, `max_workers`, and `batch` through to services. Accepts optional `config` kwarg for path derivation.
- `src/workrecap/services/date_utils.py` — `date_range`, `weekly_range`, `monthly_range`, `yearly_range`, `catchup_range`, `monthly_chunks`.
- `src/workrecap/cli/main.py` — Typer app. Subcommands: `fetch`, `normalize`, `summarize`, `run`, `ask`, `models`, `storage` (`init-db`, `sync`, `search`). `run` command creates optional `StorageService` via `_get_storage_service()` and passes it to orchestrator (graceful degradation on init failure). `storage init-db` initializes PostgreSQL tables. `storage sync` backfills existing file data to DB+Vector with `--since`/`--until` filters. `storage search` performs semantic search via ChromaDB. All user-facing output uses `_echo()` wrapper (calls `typer.echo` + logs to `workrecap.cli.output` file-only logger, avoiding stderr duplication). All four commands support checkpoint catch-up and `--weekly/--monthly/--yearly` options. All four commands support `--force/-f`. `summarize weekly/monthly/yearly` also support `--force/-f` (skip-if-exists). `fetch` and `run` support `--type/-t` for type filtering and `--source/-s` for source selection (default: all enabled). `SOURCE_TYPES` maps source → valid types. `fetch` and `run` support `--workers/-w` for parallel fetch (default: 1 for fetch, config.max_workers for run). `normalize`, `summarize daily`, `run` support `--workers/-w` for parallel LLM execution (default: `config.max_workers=5`). `normalize`, `summarize daily`, `run` support `--batch/--no-batch` (default: `False`) for batch API LLM calls. `normalize` and `run` support `--enrich/--no-enrich` (default: `True`). All inject `DailyStateStore` + `FetchProgressStore` into services. `run --weekly/--monthly/--yearly` triggers hierarchical summarization after the daily pipeline: `--weekly` generates weekly summary, `--monthly` cascades weekly→monthly, `--yearly` cascades weekly→monthly→yearly. Skipped when daily pipeline has failures. `SummarizeError` from intermediate steps is handled gracefully.
- `src/workrecap/api/app.py` — FastAPI app. Routes in `api/routes/`. `StaticFiles` mount for `frontend/`. Registers routers: pipeline, fetch, normalize, summarize_pipeline, summary, summaries_available, query. CORS middleware.
- `src/workrecap/api/deps.py` — FastAPI dependency injection: `get_config()` (lru_cache), `get_job_store()`, `get_llm_router(config)` (creates `ProviderConfig` + `UsageTracker` + `LLMRouter`).
- `src/workrecap/api/job_store.py` — `JobStore`: async job CRUD via `data/state/jobs/{job_id}.json`. `create()`, `get(job_id)`, `update(job_id, status, result, error)`.
- `src/workrecap/api/routes/pipeline.py` — Full pipeline endpoints. `POST /api/pipeline/run/{date}` (optional body: force, types, enrich), `POST /api/pipeline/run/range` (body: since, until, force, types, max_workers, enrich, batch, summarize_weekly/monthly/yearly). `GET /api/pipeline/jobs/{id}`. All async via BackgroundTasks (202 + job_id). Hierarchical summarization via `_run_hierarchical` helper when `summarize_weekly/monthly/yearly` set. Injects `FetchProgressStore`, `DailyStateStore`, `GHESClientPool` (when workers>1). `ghes.close()` and `pool.close()` in finally.
- `src/workrecap/api/routes/fetch.py` — Individual fetch endpoints. `POST /api/pipeline/fetch/{date}` (optional body: types, force), `POST /api/pipeline/fetch/range` (body: since, until, types, force, max_workers). `GHESClientPool` when workers>1. Resource cleanup in finally.
- `src/workrecap/api/routes/normalize.py` — Individual normalize endpoints. `POST /api/pipeline/normalize/{date}` (optional body: enrich, force), `POST /api/pipeline/normalize/range` (body: since, until, force, enrich, max_workers, batch). `enrich=False` skips LLM creation.
- `src/workrecap/api/routes/summarize_pipeline.py` — Summarize trigger endpoints. `POST /api/pipeline/summarize/daily/{date}`, `POST /api/pipeline/summarize/daily/range` (body: since, until, force, max_workers, batch), `POST /api/pipeline/summarize/weekly` (body: year, week, force), `POST /api/pipeline/summarize/monthly` (body: year, month, force), `POST /api/pipeline/summarize/yearly` (body: year, force).
- `src/workrecap/api/routes/summary.py` — Read-only summary endpoints. `GET /api/summary/daily/{date}`, `GET /api/summary/weekly/{year}/{week}`, `GET /api/summary/monthly/{year}/{month}`, `GET /api/summary/yearly/{year}`.
- `src/workrecap/api/routes/summaries_available.py` — Summary availability endpoint. `GET /api/summaries/available?year=&month=` returns `{daily: [dd,...], weekly: [ww,...], monthly: [mm,...], yearly: bool}` by scanning `data/summaries/` directory tree.
- `src/workrecap/api/routes/query.py` — Free-form query endpoint. `POST /api/query` (body: question, months).

### Frontend (Alpine.js)

Web UI in `frontend/` served via FastAPI `StaticFiles`. Alpine.js + Pico CSS + marked.js, all CDN-loaded. No build tools.

```
frontend/
├── index.html          ← Alpine.js + marked.js CDN, ES module entry
├── style.css           ← Pico CSS extensions (calendar, chat, dark mode)
└── js/
    ├── app.js          ← Alpine init (alpine:init), component registration, dark mode store
    ├── api.js          ← fetch helpers: api(), pollJob(maxErrors=30), escapeHtml(), copyToClipboard()
    ├── pipeline.js     ← Pipeline tab: single/range run, options (force/batch/workers/enrich/hierarchical)
    ├── summaries.js    ← Summaries tab: calendar view, hierarchy navigation (daily→weekly→monthly→yearly)
    └── ask.js          ← Ask tab: chat history, quick questions, markdown rendering
```

**3 tabs:** Pipeline, Summaries (calendar view), Ask (chat history). Dark mode toggle via `data-theme` + `localStorage`.

### Data directory layout

All outputs go under `data/` (file-based, no DB):
```
data/raw/{YYYY}/{MM}/{DD}/          → prs.json, commits.json, issues.json
data/normalized/{YYYY}/{MM}/{DD}/   → activities.jsonl, stats.json
data/summaries/{YYYY}/daily/        → {MM}-{DD}.md
data/summaries/{YYYY}/weekly/       → W{NN}.md
data/summaries/{YYYY}/monthly/      → {MM}.md
data/summaries/{YYYY}/              → yearly.md
data/state/                         → checkpoints.json (last_fetch/normalize/summarize_date), daily_state.json (per-date timestamps), failed_dates.json (failed date tracking for auto-retry), fetch_progress/ (chunk search cache), jobs/
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
- **Integration tests** are in `tests/integration/`. They use real `.env` credentials to call GitHub API and LLM API. Excluded from default `pytest` runs via `addopts = "-m 'not integration'"`. Run with `pytest -m integration -x -v`. Tests share a class-scoped temp `data_dir` to avoid polluting real `data/`. Use `INTEGRATION_TEST_DATE=YYYY-MM-DD` to override the default test date (3 days ago). `TestPromptCaching` verifies Anthropic prompt caching (cache_write on 1st call, cache_read on 2nd call with same system prompt). **Note:** Anthropic's empirical minimum cacheable prompt is ~2048 tokens for Sonnet 4.6 (docs say 1024); test uses ~3000 diverse tokens to safely exceed the threshold. `test_config_and_providers.py`: `TestConfigConsistency` (config.toml validates, all task models exist in pricing.toml), `TestProviderConnectivity` (OpenAI/Anthropic/Gemini cheapest-model chat connectivity), `TestEnrichTask` (enrich task JSON response via LLMRouter).
- HTTP mocking: `respx` for `httpx` requests (GHESClient tests).
- `unittest.mock.patch` for service-level mocking. Patch targets must be module-level imports (not local imports inside functions).
- `pytest-asyncio` for async tests (API routes).

## Important conventions

- **Ruff config:** line-length=100, target py312.
- **src layout:** package at `src/workrecap/`, `pythonpath = ["src"]` in pytest config.
- **FastAPI route order:** Static routes (`/run/range`) must be defined BEFORE parameterized routes (`/run/{date}`), otherwise FastAPI matches the literal as a parameter.
- **FastAPI StaticFiles:** `app.mount("/", StaticFiles(...))` must be placed AFTER all `app.include_router()` calls to avoid intercepting API routes.
- **Background tasks + DI:** `dependency_overrides` only works for `Depends()` in route signatures. Background task functions must receive config/store as parameters, not call `get_config()` directly.
- **Exception hierarchy:** See `src/workrecap/exceptions.py`.
- **Thread safety for parallel execution:** `DailyStateStore` (get/set_timestamp), `LLMRouter` (_usage + provider cache via `_provider_lock`), `UsageTracker` (record via `_lock`), and service checkpoint updates are protected by `threading.Lock`. Checkpoint writes use date comparison guard (`target_date > existing`) to prevent out-of-order overwrites. Date-specific file paths (raw/normalized/summaries) don't conflict across threads.
- **Multi-provider config:** `.provider/config.toml` is the single source for all LLM provider/task routing configuration. `.env` contains only GHES credentials and file paths. Auto-logging to `.log/` directory.
- **Provider task routing:** Services pass `task=` kwarg to `LLMRouter.chat()`: normalizer uses `task="enrich"`, summarizer uses `task="daily"/"weekly"/"monthly"/"yearly"/"query"`. Router maps each task to its configured provider+model.
- **LLM API optimization features:** `json_mode=True` for structured output (provider-native JSON constraints), `max_tokens` for per-task output limits (config.toml or explicit, bound to output format not model — same value on escalation; OpenAI provider maps to `max_completion_tokens` for SDK compatibility; **reasoning models** (gpt-5/o3/o4) skip `max_completion_tokens` entirely since it includes thinking tokens and a low limit starves visible output), `cache_system_prompt` defaults to `True` in `LLMRouter.chat()` — Anthropic uses `cache_control: {"type": "ephemeral"}`, OpenAI/Gemini use automatic implicit caching (flag ignored).
- **Prompt template split convention:** Templates use `<!-- SPLIT -->` marker to separate static instructions (→ system prompt, cacheable) from dynamic data (→ user content). `_render_split_prompt()` in summarizer, manual split in normalizer.
- **Cache-aware cost tracking:** `TokenUsage` carries `cache_read_tokens`/`cache_write_tokens`; providers extract these from response metadata. `PricingTable.estimate_cost()` applies provider-specific discount factors.
- **Batch API:** Default off (`--batch` / `batch=False`). When enabled, normalizer and summarizer submit all date prompts as a single batch via `LLMRouter.submit_batch()` → `wait_for_batch()`. 50% cost reduction. `BatchCapable` mixin on providers (Anthropic, OpenAI, Gemini); `isinstance(provider, BatchCapable)` for runtime capability check. Batch uses base_model only (no escalation). `custom_id` convention: `enrich-{date}` for normalizer, `daily-{date}` for summarizer. `BatchStateStore` persists job state for crash recovery. **Dynamic batch timeout:** `wait_for_batch(batch_size=N)` auto-computes timeout as `min(300 + 30*N, 14400)` seconds; adaptive polling ramps from 5s to 60s based on elapsed/total ratio.
- **Rate limit resilience:** `GHESClient._request_with_retry()` uses separate counters for rate limits (max 7) and server errors (max 3). Rate limit wait uses three-tier strategy: `Retry-After` → `X-RateLimit-Reset` → exponential backoff (2^n, cap 5 min), with ±25% jitter. This follows GitHub's documented best practices and prevents integration bans from aggressive retrying.
- **Failed date auto-retry:** `FailedDateStore` persists per-date failure records to `data/state/failed_dates.json`. On subsequent runs, dates with retryable errors (timeout, 429, 5xx) are automatically re-attempted up to `max_fetch_retries` (default 5). Permanent errors (404, 403 non-rate-limit, 422) are never retried. CLI reports exhausted dates after range operations.

## Workflow rules

Every task follows this workflow:

### 1. Plan first (always start in PLAN mode)
- Enter PLAN mode to design the overall approach.
- Review the design for correctness and completeness.
- Break the design into small **sub-plans** (incremental steps).

### 2. Branch & worktree
- Create a topic branch based on the task subject.
- Use `git worktree` to isolate work (`git worktree add ../work-recap-claude-<branch> -b <branch>`).
- **Do NOT run `pip install -e .` in worktrees.** Editable install records a single path per package — running it in a worktree overwrites the main venv's path, and when the worktree is removed the import breaks. Instead, use `PYTHONPATH=src pytest` to run tests in worktrees without touching the shared `.venv`.

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
