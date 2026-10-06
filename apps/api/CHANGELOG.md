# Changelog — `apps/api`

All notable changes to the **api** service (FastAPI). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/) from the consumer's perspective (root `CLAUDE.md`, rule 15).
The two services version independently.

## [Unreleased]

## [0.6.0] — 2026-10-06

### Added

- **`/v1/conversations`** (roadmap api 1.2, `ARCHITECTURE.md` B4a): `POST` starts a conversation
  (`201`; optional `title` ≤ 200, missing or blank → "New conversation"), `GET` lists them newest
  first (`limit` 1–100, default 50; `{items, count}`), `GET /{id}` returns one with its messages
  oldest first and `citations` as `[{title, path}]` (`404 not_found` for an unknown id).
  Timestamps are UTC (`…Z`). Conversations are shared — no owner until auth (ADR-0013).
- `app/repositories/conversations.py` — the queries (ORM only), kept out of the router.
- Titles are trimmed, then bounded at 200 **UTF-16 units** (as `nvarchar(200)` counts them), so an
  over-long or emoji-heavy title is a `422`, never a truncation error. A stored `citations` value
  that is valid JSON but not `[{title, path}]` reads as `null` for that message (logged, no
  content) instead of a `500` for the whole conversation. `role` is typed `"user" | "assistant"`.
- `docs/reference/openapi.json` and the web's generated `api-types.ts` include the new routes.

## [0.5.0] — 2026-10-06

### Added

- **Citation titles** (roadmap api 2.1, ADR-0015): the search index gains a searchable `title`
  field — a document's first `# ` heading (code fences skipped), else its file name. The
  retriever returns it; `graph.unique_citations()` turns retrieved chunks into unique
  `[{title, path}]` (also on `Answer.citations`). Adding the field to an existing index is
  non-breaking; chunks ingested earlier read as their file name until re-ingested.
- **`--prune`** on `python -m app.ai.ingest` finds every chunk in the index that the run did not
  produce (deleted or renamed documents, older path formats) and prints `stale=`. It is a **dry
  run** unless `--yes` is added; it needs the corpus folder (not a file), never runs after a failed
  or empty upload, and refuses (exit 2) to delete more chunks than the run keeps unless `--force`.
  The run logs its target (search host + index) before writing; an incomplete delete exits 1.
- `alembic` logs the target database **host** (never the URL) before connecting, since the URL may
  now come from `apps/api/.env`.

### Changed

- Ingestion stores `source` **relative to the ingested folder** with POSIX separators
  (`architecture/tracing.md`, not `../../docs/architecture/tracing.md`), so chunk ids no longer
  depend on where the job runs. Existing chunks get new ids on the next ingest — run it with
  `--prune --yes` once to remove the old ones (add `--force` if the dry run shows more stale chunks
  than kept).
- **Deploy order:** the retriever now selects `title`, which an index created before 0.5.0 lacks
  (Azure Search answers 400). Run the ingest (it adds the field) **before** rolling out 0.5.0.
- The ingest CLI quiets Azure SDK / httpx / openai request logging to warnings; only its own
  counts print.

## [0.4.1] — 2026-10-06

### Fixed

- **`alembic` and `python -m app.ai.ingest` now read `apps/api/.env`** (`load_local_env()`, as
  the service already did). Before, both fell back to placeholders from a plain terminal —
  Alembic tried `your-server.database.windows.net` and ingest raised `AINotConfigured` — unless
  every variable was exported in the shell. Values set in the shell still win; pipelines have no
  `.env` and are unaffected.
- **The test suite no longer reads a developer's `.env`.** `tests/conftest.py` points
  `load_local_env()` at a file that never exists (importing `app.main` used to load the real
  one, so a local database host showed up in test logs). `app.config.SERVICE_ENV_FILE` keeps the
  real path.

## [0.4.0] — 2026-10-06

### Added

- **Chat-history models** `app/models/conversation.py` (roadmap api 1.1, `ARCHITECTURE.md` B4):
  `Conversation` (`conversations`: UUID id, `nvarchar(200)` title defaulting to "New conversation",
  `datetime2(3)` UTC `created_at`/`updated_at`) and `Message` (`messages`: FK to
  `conversations.id` with `NO ACTION`, `role` restricted to `user`/`assistant`, `nvarchar(max)`
  content and JSON `citations`, nullable `token_count`, index `(conversation_id, created_at)`).
  No owner column — conversations are shared until auth lands (ADR-0013).
- **First Alembic revision** `3f1c2a9b7d10` creating both tables, with a full `downgrade()`.
  Hand-written; a test checks it renders exactly the model DDL offline. Not applied by this
  change — a named human applies it to the dev database.

### Changed

- `tests/test_alembic.py` now expects one linear revision history (it asserted an empty
  `alembic/versions/`) and also renders the downgrade offline.

## [0.3.0] — 2026-10-04

### Added

- **Error contract** `app/errors.py`: every non-2xx response body is `{"error": <snake_case code>,
  "trace_id": <id>}` with the trace id also echoed in `x-trace-id`. `ApiError` (+ `NotFound`,
  `Conflict`, `AIUnavailable`) for routes and repositories; handlers for `HTTPException`
  (a snake_case `detail` becomes the code, prose maps to a default per status; `Allow` and other
  protocol headers kept), `RequestValidationError` (`422 validation_error` — field locations are
  logged, never returned) and `AINotConfigured` (`503 ai_unavailable`); `UnhandledErrorMiddleware`
  turns anything else into `500 internal_error` logging only the exception kind and location.
  `v1_router` documents 422/500 as `ErrorBody` in OpenAPI (`docs/reference/openapi.json`
  regenerated). Promoted from the first project dry run (`docs/development/walkthrough-team-assistant.md`).

### Changed

- Comments and docstrings that described the Terraform landing zone now describe the use-case
  Bicep deployment (ADR-0012). No behaviour change.

## [0.2.0] — 2026-09-28

### Added

- **AI runtime** `app/ai/` (ADR-0009): LangChain + LangGraph on Azure AI Foundry — client
  factory (`get_chat_model` / `get_embeddings`, managed identity when deployed), a compiled
  `retrieve → answer` graph with an optional tool loop (`build_graph`, `ask`), SSE streaming
  (`stream_answer`, `sse_response`), a tool registry with the allow-listed read-only
  `query_external_db` tool, prompt files (`load_prompt`), content-free `gen_ai` telemetry
  (`model_call_span`), Azure AI Search retrieval (`AzureSearchRetriever`) and the ingestion job
  (`python -m app.ai.ingest`). No route or use case is shipped.
- New optional env vars: `AZURE_AI_ENDPOINT`, `AZURE_AI_DEPLOYMENT`,
  `AZURE_AI_EMBEDDING_DEPLOYMENT`, `AZURE_AI_EMBEDDING_DIMENSIONS`, `AZURE_AI_API_VERSION`,
  `AZURE_AI_AUTH_MODE`, `AZURE_AI_API_KEY`, `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX`,
  `AZURE_SEARCH_API_KEY`, `AI_REQUEST_TIMEOUT_SECONDS`, `AI_MAX_RETRIES`, `AI_ALLOW_TEXT_TO_SQL`.
- Dependencies: `langchain-core`, `langgraph`, `langchain-openai`, `azure-search-documents`
  (OTel pins unchanged). Tests: `tests/ai/` + the `tests/evals/` prompt regression harness.
- `python -m app.openapi_export` writes the API contract to `docs/reference/openapi.json`
  (`make openapi` also regenerates the web's TS types); `tests/test_openapi_export.py` fails
  when the committed contract is stale.

## [0.1.0] — 2026-09-28

### Changed — **BREAKING** (consumer-facing configuration)

- **Database engine is Azure SQL Database**, not Postgres (ADR-0008, supersedes ADR-0004):
  `DATABASE_URL` must now be an `mssql+aioodbc://…?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no`
  URL. `postgresql+asyncpg://…?ssl=require` URLs are no longer accepted.
- Dependencies: `asyncpg` and `opentelemetry-instrumentation-asyncpg` removed; `aioodbc`,
  `pyodbc` and `opentelemetry-instrumentation-sqlalchemy` (0.61b0) added. The runtime image now
  installs Microsoft ODBC Driver 18 for SQL Server.
- DB dependency spans come from the SQLAlchemy instrumentation (registered without an engine, so
  every lazily created engine is covered) instead of the asyncpg driver instrumentation.

### Added

- `EXTERNAL_DATABASE_URL` (optional) + `app/db/external.py`: a second, lazy, **read-only**
  engine for an external Azure SQL database (`get_external_session()` dependency); non-`SELECT`
  statements are refused before they reach the driver.

### Internal

- Engine factories resolve `create_async_engine` at call time (`sa_asyncio.create_async_engine`)
  so the OTel wrapper applies regardless of import order.
- Offline tests now guard on `pyodbc.connect`; Alembic offline mode renders T-SQL.

## [0.0.0] — 2026-09-20

- Template baseline. No features shipped.
