# CLAUDE.md

## Virtual environment

- `.venv/`가 공식 가상환경. 명령 실행 전 `source .venv/bin/activate` 필요.
- **Worktree:** `pip install -e .` 금지 → `PYTHONPATH=src pytest` 사용.

## Commands

```bash
pip install -e ".[dev]"              # 최초 1회
pytest                               # unit tests (integration excluded)
pytest -m integration -x -v          # integration tests (.env 필요)
ruff check src/ tests/               # lint
ruff format --check src/ tests/      # format
python -m workrecap --help           # CLI
uvicorn workrecap.api.app:app --reload  # web UI
```

## Architecture

```
CLI (Typer) / API (FastAPI)
  ↓
Orchestrator → Fetcher → Normalizer → Summarizer → StorageService
  ↓
GHESClient (httpx) | LLMRouter (multi-provider) | PostgresClient | VectorDBClient
  ↓
Providers: OpenAI, Anthropic, Custom (OpenAI-compatible)
```

**Pipeline:** GHES Search API → `data/raw/` (JSON) → `data/normalized/` (JSONL+stats) → `data/summaries/` (Markdown)

**Hierarchical summarization:** Daily → Weekly → Monthly → Yearly (each level consumes the level below)

**Storage:** File-first. PostgreSQL + ChromaDB optional (graceful degradation).

### Key modules

| Path | Role |
|------|------|
| `config.py` | `AppConfig` (pydantic-settings). `.env`=GHES creds, `.provider/config.toml`=LLM routing |
| `models.py` | Dataclass models (Raw/Normalized/Stats/Job/TokenUsage) + serialization |
| `exceptions.py` | `WorkRecapError` → `FetchError`, `NormalizeError`, `SummarizeError`, `StepFailedError`, `StorageError` |
| `services/fetcher.py` | GHES search (PRs/commits/issues), dedup, enrich, noise filter. `repos` param for repo filtering |
| `services/normalizer.py` | Raw → Activity + DailyStats. LLM enrichment (`task="enrich"`, `json_mode=True`) |
| `services/summarizer.py` | Jinja2 templates → LLM → markdown. `detailed` param selects `daily_detailed.md` template |
| `services/orchestrator.py` | Chains Fetch→Normalize→Summarize. `run_daily()` / `run_range()` |
| `services/daily_state.py` | Per-date timestamp tracking (fetch/normalize/summarize). Cascade staleness |
| `services/storage.py` | PostgreSQL + ChromaDB integration (async core + sync wrappers) |
| `infra/ghes_client.py` | httpx + resilient retry (rate limit 3-tier wait, server error backoff, jitter) |
| `infra/llm_router.py` | Task-based provider routing. Strategy modes. Batch API. Prompt caching |
| `infra/providers/` | `OpenAIProvider`, `AnthropicProvider`, `CustomProvider`. `base_url` for proxy |
| `infra/provider_config.py` | Parses `.provider/config.toml` (tomllib). `base_url` proxy mode skips api_key check |
| `cli/main.py` | Typer: `fetch`, `normalize`, `summarize`, `run`, `ask`, `models`, `storage` |
| `api/app.py` | FastAPI + SchedulerService lifespan. Routes in `api/routes/` |
| `scheduler/` | APScheduler wrapper. `schedule.toml` config. Telegram notifications |

All paths relative to `src/workrecap/`.

### API endpoints

- **Pipeline:** `POST /api/pipeline/run/{date}`, `POST /api/pipeline/run/range` (body: repos, detailed, force, types, enrich, batch, workers, summarize_*)
- **Fetch:** `POST /api/pipeline/fetch/{date}`, `POST /api/pipeline/fetch/range` (body: repos, force, types, workers)
- **Normalize:** `POST /api/pipeline/normalize/{date}`, `POST /api/pipeline/normalize/range`
- **Summarize:** `POST /api/pipeline/summarize/daily/{date}` (body: detailed), `POST .../daily/range`, `POST .../weekly`, `POST .../monthly`, `POST .../yearly`
- **Read:** `GET /api/summary/{daily,weekly,monthly,yearly}/...`, `GET /api/summaries/available`
- **Jobs:** `GET /api/pipeline/jobs/{id}`, `GET .../jobs/{id}/stream` (SSE)
- **Scheduler:** `GET /api/scheduler/status`, `POST .../trigger/{job}`, `PUT .../pause`, `PUT .../resume`
- **Query:** `POST /api/query`

All mutation endpoints return 202 + `job_id`. Background task functions receive config/store as params (not via DI).

### CLI options

- `--repo/-r` (fetch, run): filter to specific repos (repeatable, e.g. `--repo org/repo1 --repo org/repo2`). Post-fetch filtering by `repository_url`/`repository.full_name`.
- `--detailed` (summarize daily, run): uses `prompts/daily_detailed.md` template for context/intent-rich summaries.
- `--type/-t`, `--source/-s`, `--force/-f`, `--workers/-w`, `--batch/--no-batch`, `--enrich/--no-enrich`, `--weekly/--monthly/--yearly`

### Frontend

Alpine.js + Pico CSS + marked.js (CDN, no build). 4 tabs: Pipeline, Summaries (calendar), Ask (chat), Scheduler.

### Data layout

```
data/raw/{YYYY}/{MM}/{DD}/          → prs.json, commits.json, issues.json
data/normalized/{YYYY}/{MM}/{DD}/   → activities.jsonl, stats.json
data/summaries/{YYYY}/daily/        → {MM}-{DD}.md, {MM}-{DD}.telegram.txt
data/summaries/{YYYY}/weekly/       → W{NN}.md
data/summaries/{YYYY}/monthly/      → {MM}.md
data/summaries/{YYYY}/              → yearly.md
data/state/                         → checkpoints.json, daily_state.json, failed_dates.json, jobs/
```

## Testing

- Unit: `tests/unit/`, `conftest.py` provides `tmp_data_dir`/`test_config` fixtures. `.env.test` autouse.
- Integration: `tests/integration/`, real credentials. `pytest -m integration -x -v`.
- HTTP mocking: `respx`. Service mocking: `unittest.mock.patch` (patch at module-level import target).
- `pytest-asyncio` for async API route tests.

## Important conventions

- **Ruff:** line-length=100, target py312. `src/` layout with `pythonpath = ["src"]`.
- **FastAPI route order:** Static routes (`/run/range`) BEFORE parameterized (`/run/{date}`).
- **FastAPI StaticFiles:** `app.mount("/")` AFTER all `include_router()` calls.
- **Thread safety:** `DailyStateStore`, `LLMRouter`, `UsageTracker`, checkpoint writes all lock-protected. Date comparison guard prevents out-of-order overwrites.
- **Config split:** `.env` = GHES creds only. `.provider/config.toml` = LLM provider/task routing. `schedule.toml` = scheduler.
- **LLM proxy:** `base_url` in config.toml → provider SDK handles routing. `X-Client-ID: work-recap` header auto-added in proxy mode.
- **Prompt caching:** `<!-- SPLIT -->` marker splits static instructions (system, cacheable) from dynamic data (user content). `cache_system_prompt=True` by default.
- **Batch API:** `--batch` flag. `BatchCapable` mixin. Dynamic timeout: `min(300+30*N, 14400)s`.
- **Staleness cascade:** fetch→normalize→summarize (daily: DailyStateStore timestamps, weekly+: file mtime).
- **Checkpoint catch-up:** No args + checkpoint → `catchup_range(last_date)`. `--force` reprocesses all.

## Workflow

1. **Plan** → Design approach, break into sub-plans
2. **Branch** → `git worktree add ../work-recap-claude-<branch> -b <branch>`
3. **TDD** → Test first → implement → verify → full suite pass → next
4. **Check** → All tests pass, ruff clean, CLAUDE.md updated
5. **Commit** → Clear message summarizing work
