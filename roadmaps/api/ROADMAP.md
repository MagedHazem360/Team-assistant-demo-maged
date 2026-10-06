# API roadmap — Team Assistant

Backend (`apps/api`): models, migrations, `/v1` endpoints, AI graphs/tools, ingestion.
Schema: [`../README.md`](../README.md). Written by `/plan-roadmap api`; executed by
`/implement-next api`.

Sources: `docs/architecture/ARCHITECTURE.md` Part B (B2–B8), `docs/design/db-design.md`,
`docs/design/wireframes/chat.md`, ADR-0013 (shared conversations), ADR-0015 (citation shape).
Product phase 1 = F1 + F2 (phases 1–2 below); F3 is product phase 2 (phase 3 below, deferred).
The dev database, Foundry and AI Search are pre-created (B6), so nothing here waits on
`infra:1.1`.

## Phase 1 — Foundation: conversations

### 1.1 — Conversation and message models + first migration

- **status:** done
- **depends_on:** []
- **layers:** [model, migration]
- **acceptance:**
  - `app/models/conversation.py` defines `Conversation` (`conversations`) and `Message` (`messages`) exactly as `docs/design/db-design.md`: UUID PKs, `nvarchar(200)` title, `nvarchar(20)` role, `nvarchar(max)` content and citations, nullable `int` token_count, `datetime2(3)` timestamps with server defaults; both imported in `app/models/__init__.py`
  - named CHECK constraints: `role IN ('user','assistant')` and `citations IS NULL OR ISJSON(citations)=1`; FK `messages.conversation_id → conversations.id` `ON DELETE NO ACTION`; index `(conversation_id, created_at)`
  - one Alembic revision creating both tables with a working `downgrade()` (drops both); `alembic upgrade head --sql` renders valid T-SQL for SQL Server offline
  - no conversation owner column (ADR-0013)
- **how_to_test:**
  - `uv run --directory apps/api pytest tests/test_models_conversation.py tests/test_alembic.py -q` → 12 passed (columns, named CHECKs, FK without `ON DELETE`, index, no owner column, and the drift check: the revision renders exactly the model DDL)
  - `just test` → web 136 passed, api 210 passed
  - offline SQL, no database needed (Git Bash, from `apps/api`): `DATABASE_URL='mssql+aioodbc://u:p@db.invalid:1433/app?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no' uv run alembic upgrade head --sql` → `CREATE TABLE conversations`, `CREATE TABLE messages` (with `ck_messages_role`, `ck_messages_citations_json`, `fk_messages_conversation_id_conversations`), `CREATE INDEX ix_messages_conversation_id ON messages (conversation_id, created_at)`
  - `uv run alembic heads` → `3f1c2a9b7d10 (head)`
  - after you apply it to dev: `uv run alembic current` → `3f1c2a9b7d10 (head)`, and `uv run alembic check` → `No new upgrade operations detected.`
- **needs_human:**
  - install ODBC Driver 18 (admin PowerShell: `winget install --id Microsoft.msodbcsql.18 -e`) — needed to reach the dev database from your machine
  - review the offline SQL (`alembic upgrade head --sql`), then apply it to the dev database yourself: `uv run --directory apps/api alembic upgrade head` (B6 — agents never apply migrations); then `uv run alembic check`
  - decide the primary-key strategy before data exists: app-generated random `uuid4` (current) or a sequential `NEWSEQUENTIALID()` server default (less index fragmentation on `messages`; changes this revision)
- **notes:**
  - 2026-10-06 — planned by `/plan-roadmap api`
  - 2026-10-06 — started by Claude
  - 2026-10-06 — implemented; awaiting test by Maged Hazem. Revision hand-written (dev DB unreachable: no ODBC Driver 18). Files: `app/models/conversation.py` (new), `app/models/__init__.py`, `alembic/versions/20261006_1200-3f1c2a9b7d10_create_conversations_and_messages.py` (new), `tests/test_models_conversation.py` (new), `tests/test_alembic.py`, `CHANGELOG.md` + `pyproject.toml`/`uv.lock` (0.4.0), doc sync (CLAUDE.md ×2, README ×2, `docs/architecture/{data,overview}.md`, `docs/design/db-design.md`, `docs/development/testing.md`, `docs/security/README.md`, `docs/reference/commands.md`, rules 00/25/35, write-migration skill/command, migration-author, db-introspector). Reviews: code-reviewer (nothing blocking; fixed a doc that claimed the migration was applied, removed CONCURRENTLY boilerplate), security-reviewer (nothing blocking; destructive-downgrade warning added to `downgrade()`)
  - 2026-10-06 — carried to later items: 1.2/2.2 validate the citations shape `[{title, path}]` before writing (the CHECK only enforces valid JSON), set `updated_at` on every new message and test it; web 2.2 renders citation paths as text, never as a raw link (security review)
  - 2026-10-06 — confirmed by Maged Hazem (tests green). Still open on the human side: apply `3f1c2a9b7d10` to dev (`alembic upgrade head`, then `alembic check`) before 1.2's live check; primary keys stay app-generated `uuid4` unless decided otherwise before data exists
  - 2026-10-06 — migration applied to the dev database by Maged Hazem (`sqldb-sandbox-test-maged`): `alembic current` → `3f1c2a9b7d10 (head)`, `alembic check` → no drift. Note: `alembic/env.py` does not load `apps/api/.env` (set `$env:DATABASE_URL` in the shell); a serverless DB waking from auto-pause times out the first logins (error 258) — retry after ~30 s
  - 2026-10-06 — follow-up fix (api 0.4.1): `alembic` and `python -m app.ai.ingest` now read `apps/api/.env` themselves — the `$env:DATABASE_URL` workaround above is no longer needed; tests never read the developer's `.env`

### 1.2 — Conversations endpoints: create, list, get

- **status:** done
- **depends_on:** [1.1]
- **layers:** [endpoint]
- **acceptance:**
  - `POST /v1/conversations` with `{ "title"?: string }` (≤ 200) → `201 { id, title, created_at, updated_at }`; no title → `"New conversation"`; a longer title → `422 validation_error`
  - `GET /v1/conversations?limit=50` (1–100, default 50) → `200 { items: [{ id, title, updated_at }], count }`, newest `updated_at` first, `count` = items returned; out-of-range limit → `422`
  - `GET /v1/conversations/{id}` → `200` the conversation + `messages: [{ id, role, content, citations, created_at }]` oldest first, `citations` as a JSON array or `null`; unknown id → `404 not_found`
  - every response echoes `x-trace-id`; errors use `{ "error": code, "trace_id": id }`; message content and titles never appear in logs, spans or metric attributes
  - `docs/reference/openapi.json` and `docs/reference/http-api.md` list the three routes
- **how_to_test:**
  - `uv run --directory apps/api pytest tests/test_conversations.py -q` → 28 passed (shapes, validation incl. trimmed/UTF-16 title bounds, limit 1–100, 404/422 contract, `x-trace-id`, malformed stored citations → `null`, a failing repository is a 500 that leaks no user text, compiled T-SQL `TOP`/`ORDER BY`)
  - `just test` → web 136, api 265 passed
  - live, against the dev database — terminal 1, from `apps/api`: `uv run uvicorn app.main:app --port 8000` (the first request after a pause may take ~30 s while the serverless DB wakes)
  - terminal 2 (PowerShell): `curl.exe -s -X POST localhost:8000/v1/conversations -H "content-type: application/json" -d "{}"` → `{"id":"…","title":"New conversation","created_at":"…Z","updated_at":"…Z"}`
  - `curl.exe -s "localhost:8000/v1/conversations?limit=5"` → `{"items":[{…the one above…}],"count":1}`
  - `curl.exe -s localhost:8000/v1/conversations/<id from the POST>` → the conversation with `"messages":[]`
  - `curl.exe -s -i localhost:8000/v1/conversations/00000000-0000-0000-0000-000000000000` → `404`, body `{"error":"not_found","trace_id":"…"}`, same `x-trace-id` header
  - `curl.exe -s "localhost:8000/v1/conversations?limit=0"` → `{"error":"validation_error",…}`
- **needs_human:**
  - run the live smoke above against the dev database (it creates a test conversation; deleting is out of scope, so it stays)
- **notes:**
  - 2026-10-06 — planned by `/plan-roadmap api`; tests run against a fake session — the live check needs 1.1 applied
  - 2026-10-06 — started by Claude (branch `feat/api-1.2-conversation-endpoints`, stacked on api 2.1 until the open PRs merge)
  - 2026-10-06 — implemented; awaiting test by Maged Hazem. Files: `app/routers/conversations.py`, `app/repositories/{__init__,conversations}.py` (new), `app/routers/v1.py`, `tests/test_conversations.py` (new), generated `docs/reference/openapi.json` + `apps/web/src/lib/api-types.ts`, `docs/reference/http-api.md`, `apps/api/CLAUDE.md`, api 0.6.0 (CHANGELOG, pyproject, uv.lock, root CLAUDE.md). Reviews: code-reviewer + security-reviewer — nothing blocking; fixed: malformed stored citations → `null` (was a whole-thread 500), title bounded after trimming in UTF-16 units (emoji would have been a 500), `role` typed, tests on real ORM objects + failure-path log-leak test
  - 2026-10-06 — carried forward from the reviews: (api 2.2) validate citations with the `Citation` model before insert; cap message/question length on write; user + assistant rows written in one request can share a `datetime2(3)` millisecond — keep the thread ordered (insertion order or sequential ids); consider paging `GET /{id}` messages. (web 1.1/BFF) cap request bodies, validate `{id}` as a UUID before forwarding, never forward arbitrary query strings; rate-limit at the BFF/ingress before anything beyond internal use (create spam pushes real conversations out of the 100-row list). (web 2.x) render titles/paths as plain text; show a "don't paste personal data" notice. (deployment) a serverless-DB cold start surfaces as `500 internal_error` — consider mapping DB timeouts to `503`. (follow-up) `create_app()` still reports OpenAPI/`/info` version `0.0.0` (template leftover, `app/main.py:77`)
  - 2026-10-06 — confirmed by Maged Hazem (offline tests + live smoke against the dev database)

## Phase 2 — Answers with citations

### 2.1 — Citation titles and corpus-relative paths in the index (ADR-0015)

- **status:** done
- **depends_on:** []
- **layers:** [ai]
- **acceptance:**
  - the index definition (`ingest.build_index`) gains a `title` field; each document's title is its first `# ` heading, else its file name; `ensure_index` adds the field to an existing `team-assistant-docs` index without recreating it
  - ingestion stores `source` relative to the ingested folder, POSIX separators (`uv run python -m app.ai.ingest ../../docs` stores `architecture/tracing.md`, never `../../docs/…`)
  - the retriever returns `title` with each chunk; a citation helper returns unique `[{ "title", "path" }]` in retrieval order
  - `--prune`: reports the chunks in the index this run did not produce (`stale=`); deletes them only with `--yes`; needs the corpus folder (not a file); never prunes after a failed or empty upload; refuses (exit 2) to delete more than it keeps unless `--force`; logs the target search host + index before writing
  - the ingest CLI logs only its own counts — the Azure SDK / HTTP request logging is quieted to warnings
  - ingestion and retrieval telemetry stay counts and durations only — no titles, paths or content
- **how_to_test:**
  - `uv run --directory apps/api pytest tests/ai -q` → green (titles and edge cases, corpus-relative paths, citations, prune: dry run / `--yes` / refusal / `--force` / incomplete delete, `run()` never deletes without `--prune --yes`, unsafe flag combinations rejected)
  - `just test` → web 136, api 237 passed
  - live (from `apps/api`): `uv run python -m app.ai.ingest ../../docs --prune` → `documents=53 chunks=583 uploaded=583 failed=0 stale=582 pruned=0` + a dry-run hint; then `… --prune --yes` → `stale=0 pruned=0` (the first `--yes` run deletes the 582 old `../../docs/…` chunks)
- **needs_human:**
  - run the ingestion with pruning against the dev index (see `how_to_test` → live): a dry run, then `--prune --yes` to delete the 582 pre-2.1 `../../docs/…` chunks; re-run `--prune --yes` whenever `docs/` changes
- **notes:**
  - 2026-10-06 — planned by `/plan-roadmap api`
  - 2026-10-06 — pre-2.1 test ingest by Maged Hazem: 53 documents, 582 chunks, 0 failed (index created; search returns relevant hits). Sources are stored as `../../docs/…` — clear the index before the post-2.1 ingest
  - 2026-10-06 — scope: `--prune` added (replaces deleting the index by hand; also removes chunks of deleted/renamed docs). Started by Claude
  - 2026-10-06 — implemented; awaiting test by Maged Hazem. Files: `app/ai/ingest.py`, `app/ai/tools/retrieve.py`, `app/ai/graph.py`, `alembic/env.py` (logs the target host), tests (`tests/ai/fakes.py`, `test_ai_tools_retrieval_ingest.py`, `test_alembic.py`), api 0.5.0 (CHANGELOG, pyproject, uv.lock), docs (`ai.md`, ARCHITECTURE B2, api README/CLAUDE.md, rule 70, feature-scaffold). Reviews: code-reviewer + security-reviewer — HIGH on unguarded `--prune` fixed (dry run by default, `--yes`, `--force`, folder-only, target logged); deploy-order note added (ingest before rolling out 0.5.0). Carried to web 2.2: render citation titles/paths as plain text, never HTML or links
  - 2026-10-06 — confirmed by Maged Hazem: live ingest with `--prune` (dry run, then `--yes`) on the dev index; the pre-2.1 `../../docs/…` chunks are gone

### 2.2 — Ask a question in a conversation (JSON)

- **status:** done
- **depends_on:** [1.2, 2.1]
- **layers:** [ai, endpoint]
- **acceptance:**
  - `POST /v1/conversations/{id}/ask` with `{ "question": string }` → `200 { message_id, answer, citations: [{ title, path }] }`
  - stores the user message, then the assistant message with its citations; sets `conversations.updated_at`; if the title is still "New conversation", replaces it with the question cut to 200 characters
  - the model receives the last 10 stored messages of the conversation **before** this question as history (oldest first), plus the retrieved context (`top_k = 5`) through the template's `retrieve → answer` graph
  - `token_count` on the assistant message = output tokens when the model reports them, else `null`; `null` on user messages
  - unknown conversation → `404`; empty question → `422 validation_error`; more than 4,000 characters → `413 payload_too_large`; model or search failure/timeout (60 s) → `503 ai_unavailable` with the user message kept and no assistant message stored
  - no question, answer, retrieved text or conversation id in logs, spans, metrics or events; model calls only through `client.py` inside `model_call_span()`
  - an eval case in `tests/evals/cases.json` covers a grounded answer that cites its source
- **how_to_test:**
  - `uv run --directory apps/api pytest tests/test_ask.py tests/evals -q` → green (answer + citations + both messages stored, history as user/assistant turns never system, null token_count, 404/422/413, search failure / timeout / model failure → 503 with the question kept and no answer, no content or id in the service's logs, repository T-SQL `TOP 10` newest-first, title rules)
  - `just test` → web 136, api 287 passed
  - live — terminal 1, from `apps/api`: `uv run uvicorn app.main:app --port 8000`
  - terminal 2: create a conversation (`curl.exe -s -X POST localhost:8000/v1/conversations -H "content-type: application/json" -d "{}"`), then ask: `curl.exe -s -X POST localhost:8000/v1/conversations/<id>/ask -H "content-type: application/json" -d "{\"question\": \"How does trace_id propagation work?\"}"` → `{"message_id":…,"answer":"… [1] …","citations":[{"title":"The `trace_id` contract","path":"architecture/tracing.md"},…]}`
  - a follow-up in the same conversation (`"And on the api side?"`) answers in context; `GET /v1/conversations/<id>` shows 4 messages, the title is now the first question
- **needs_human:**
  - the live check above (Foundry chat + embeddings + the ingested index + the dev database)
  - re-run `/code-review` and `/security-review` on this branch: the agent reviews could not run (usage limit) — a manual review against the same checklist found nothing blocking
- **notes:**
  - 2026-10-06 — planned by `/plan-roadmap api`; the live answer needs 1.1 applied and 2.1's ingestion run
  - 2026-10-06 — started by Claude (branch `feat/api-2.2-ask`, off `development`)
  - 2026-10-06 — implemented; awaiting test by Maged Hazem. Files: `app/routers/conversations.py` (ask route), `app/repositories/conversations.py`, `tests/test_ask.py` (new), `tests/evals/{cases.json,test_evals.py}`, generated `docs/reference/openapi.json` + `apps/web/src/lib/api-types.ts`, `docs/reference/http-api.md`, `apps/api/CLAUDE.md`, api 0.7.0 (CHANGELOG, pyproject, uv.lock, root CLAUDE.md). Reviews: agent reviews failed to start (usage limit); manual review — session not held during the model call, no lazy load after commit, history ordered, 60 s cap wins over client retries, broad except covers only the AI call, no content in logs — nothing blocking
  - 2026-10-06 — confirmed by Maged Hazem (live: answers from the indexed docs with citations, follow-up in context, 422/404). Still owed: the agent `/code-review` + `/security-review` (usage limit)

### 2.3 — Ask with streaming (SSE)

- **status:** todo
- **depends_on:** [2.2]
- **layers:** [ai, endpoint]
- **acceptance:**
  - `POST /v1/conversations/{id}/ask/stream` streams `event: sources` (`{ sources: [{ title, path }], count }`) → `event: token`\* (`{ text }`) → `event: done` (`{ sources, input_tokens, output_tokens }`), same storage rules as 2.2
  - errors **before the first frame** return the JSON error contract (`404`, `413`, `422`, `503 ai_unavailable`); a failure **after** it sends `event: error` with `{ "error": "ai_unavailable" }` and ends the stream — the user message is kept, no assistant message is stored
  - a client disconnect before `done` stores no assistant message
  - the response carries `x-trace-id` and is not buffered by proxies (template `sse_response`)
  - `docs/reference/http-api.md` documents the frames and the before/after-first-frame error rule
- **how_to_test:**
- **needs_human:**
- **notes:**
  - 2026-10-06 — planned by `/plan-roadmap api`; the frame shape change (`string[]` → `[{title, path}]`) is the cross-service change of ADR-0015 — web follows in its own items

## Phase 3 — Re-index from the app (F3, product phase 2 — deferred)

### 3.1 — Threat model for the ingest endpoint

- **status:** blocked
- **depends_on:** []
- **layers:** [docs]
- **acceptance:**
  - `docs/security/threat-models/team-assistant.md` (via `threat-modeler`) covers the unauthenticated ingest trigger, overlapping runs across replicas, cost on the shared Foundry deployment, writes to the shared search service, and shared conversations (ADR-0013)
  - ADR-0014 moves from `Proposed` to a team decision, including whether the endpoint gets a guard before Okta
- **how_to_test:**
- **needs_human:**
  - decide ADR-0014 and the guard question
- **notes:**
  - 2026-10-06 — planned; blocked (deferred): F3 is product phase 2 (`ARCHITECTURE.md` B2)

### 3.2 — `POST /v1/admin/ingest` (background, single-flight)

- **status:** blocked
- **depends_on:** [3.1, 2.1]
- **layers:** [ai, endpoint]
- **acceptance:**
  - `POST /v1/admin/ingest` takes no input, returns `202 { "status": "started" }` and runs ingestion of the fixed corpus path in the background; a second call while one runs → `409 ingest_running`
  - the corpus is copied into the api image at `/app/corpus` (`apps/api/Dockerfile`); locally the api reads the repo's `docs/`
  - outcome logged as counts and duration only
- **how_to_test:**
- **needs_human:**
- **notes:**
  - 2026-10-06 — planned; blocked (deferred) until 3.1 is decided
