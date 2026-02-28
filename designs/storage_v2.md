# Design: Database and VectorDB Integration (V2)

## Goal
Extend the current file-based storage to support PostgreSQL (for structured data and statistics) and ChromaDB (for semantic search via VectorDB). This enables rich querying, MCP integration, and advanced UI features like performance analysis.

## Architecture

### 1. Relational Database (PostgreSQL)
Uses SQLModel + asyncpg to interact with PostgreSQL at `192.168.0.2:5433`.

#### Schema
- **`activities` table** (`ActivityDB`):
    - `id`: UUID (Primary Key)
    - `date`: Date
    - `source`: String (e.g., "github")
    - `kind`: String (e.g., "pr", "commit")
    - `external_id`: String
    - `data`: JSON (Full raw/normalized activity data)
    - `created_at`: Timestamp
- **`daily_stats` table** (`StatsDB`):
    - `date`: Date (Primary Key)
    - `github_stats`: JSON
    - `confluence_stats`: JSON
    - `jira_stats`: JSON
    - `updated_at`: Timestamp
- **`summaries` table** (`SummaryDB`):
    - `id`: UUID (Primary Key)
    - `level`: String (daily, weekly, monthly, yearly)
    - `date_key`: String (e.g., "2025-02-23", "2025-W08", "2025-02")
    - `content`: Text (Markdown)
    - `metadata_json`: JSON
    - `created_at`: Timestamp
    - `updated_at`: Timestamp

### 2. Vector Database (ChromaDB)
- **Server**: `192.168.0.2:9000`
- **Collection**: `work_recap_summaries`
- **Content**: Summary documents indexed by `{level}_{date_key}` IDs
- **Metadata**: `level`, `date_key`

### 3. Remote Embedding (TEI + BGE-M3)
- **Server**: TEI (Text Embeddings Inference) at `192.168.0.2:8090`
- **Model**: BGE-M3 (multilingual, 1024-dim)
- **API**: `POST /embed` with `{"inputs": [...]}` → `[[float, ...], ...]`
- **Client**: `EmbeddingClient` uses httpx (sync HTTP client)

### 4. Storage Service
`StorageService` coordinates PostgreSQL + ChromaDB:
1. **File-first**: Normalizer/Summarizer already write files. StorageService only writes to DB+Vector.
2. **Graceful degradation**: All storage errors are caught and logged — pipeline never breaks.
3. **Sync wrappers**: Async core methods + `*_sync()` wrappers via `asyncio.run()` (orchestrator is sync).

#### Integration Points
- **Orchestrator `run_daily()`**: After normalize → `storage.save_activities_sync(date, activities, stats)`. After summarize → `storage.save_summary_sync("daily", date, content)`.
- **CLI `run` command**: Creates `StorageService` via `_get_storage_service()`, passes to orchestrator. Init failure → continue without storage.
- **CLI `storage init-db`**: Creates PostgreSQL tables.
- **CLI `storage sync`**: Backfills existing file data to DB+Vector with `--since`/`--until` date filters.
- **CLI `storage search`**: Semantic search via ChromaDB embeddings.

## Dependencies
- `sqlmodel>=0.0.22` — ORM + table definitions
- `asyncpg>=0.30.0` — PostgreSQL async driver
- `chromadb>=0.6.3` — Vector database client
- `httpx>=0.28` — HTTP client for TEI API (already in project)

## Infrastructure

| Service | Address | Purpose |
|---------|---------|---------|
| PostgreSQL | `192.168.0.2:5433` | Structured data storage |
| ChromaDB | `192.168.0.2:9000` | Vector similarity search |
| TEI (BGE-M3) | `192.168.0.2:8090` | Text embedding generation |

## Config (AppConfig)

| Field | Default | Env var |
|-------|---------|---------|
| `postgres_url` | `postgresql+asyncpg://pkb_test:pkb_test@192.168.0.2:5433/work_recap` | `POSTGRES_URL` |
| `chroma_host` | `192.168.0.2` | `CHROMA_HOST` |
| `chroma_port` | `9000` | `CHROMA_PORT` |
| `chroma_collection` | `work_recap_summaries` | `CHROMA_COLLECTION` |
| `tei_url` | `http://192.168.0.2:8090` | `TEI_URL` |

## Key Design Decisions

1. **File-first philosophy**: DB/Vector is an optional enhancement layer. All existing file-based workflows remain unchanged.
2. **Orchestrator stays sync**: No async propagation into the service layer. StorageService provides sync wrappers.
3. **TEI over fastembed**: Remote embedding via TEI HTTP API instead of local fastembed library. Reduces package weight and leverages shared GPU infrastructure.
4. **Normalizer 4-tuple**: `normalize()` returns `(activities_path, stats_path, activities, stats)` to provide in-memory data for storage without re-reading files.

## Test Coverage (1011 total)
- `test_embedding_client.py` — 6 tests (respx HTTP mock)
- `test_vector_client.py` — 6 tests (chromadb mock)
- `test_postgres_client.py` — 11 tests (AsyncMock)
- `test_storage_service.py` — 10 tests (all clients mocked)
- `test_orchestrator.py` — 3 storage integration tests
- `test_cli.py` — 6 storage CLI tests (init-db, search, run+storage)
