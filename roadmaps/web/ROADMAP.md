# Web roadmap — Team Assistant

Frontend (`apps/web`): pages, BFF routes, components, mocks. Schema:
[`../README.md`](../README.md). Written by `/plan-roadmap web`; executed by
`/implement-next web`.

Sources: `docs/architecture/ARCHITECTURE.md` Part B (B2, B3, B4a, B8), the wireframe
`docs/design/wireframes/chat.md` (+ `chat.html`), ADR-0015 (citation shape), `roadmaps/api`.
The template already ships `/chat`, `ChatPanel`, `ChatThread`, `MessageInput`, the SSE
pass-through (`src/lib/stream.ts`, `sse.ts`), `chat-client.ts` and `MOCK_UPSTREAM` fixtures —
these items adapt them; they do not start from scratch.

## Phase 1 — Foundation: the conversation contract through the BFF

### 1.1 — Conversation BFF routes + typed fixtures

- **status:** todo
- **depends_on:** [api:1.2]
- **layers:** [bff]
- **acceptance:**
  - `POST /api/v1/conversations`, `GET /api/v1/conversations?limit=`, `GET /api/v1/conversations/{id}` forward server-side to the api's `/v1/conversations…` routes via `fetchUpstream`, carrying and echoing `x-trace-id`; api errors pass through as `{ error, trace_id }` with the same status; upstream unreachable → `502` with `trace_id`
  - `src/lib/api-types.ts` regenerated from `docs/reference/openapi.json` (`pnpm -C apps/web api-types`); `MOCK_UPSTREAM=true` fixtures for all three routes (list with a few conversations, an empty list, a conversation with messages and citations, a `404`) are typed against it
  - works against `MOCK_UPSTREAM=true` fixtures until api:1.2 is live
- **how_to_test:**
- **needs_human:**
- **notes:**
  - 2026-10-06 — planned by `/plan-roadmap web`

### 1.2 — Ask routes for a conversation (JSON + SSE), replacing the template's `assistant` routes

- **status:** todo
- **depends_on:** [api:2.3, 1.1]
- **layers:** [bff]
- **acceptance:**
  - `POST /api/v1/conversations/{id}/ask` (JSON) and `POST /api/v1/conversations/{id}/ask/stream` (SSE pass-through, frames unchanged) forward to the api with `x-trace-id`; the BFF hop timeout is ≥ 60 s (B3, rule 30)
  - the template's `/api/v1/assistant/ask` and `/api/v1/assistant/ask/stream` routes, their tests and `src/mocks/assistant.ts` are removed (B8 #8)
  - `chat-client.ts` parses `sources` and `done` as `[{ title, path }]` (ADR-0015) and `event: error` as `{ error }`; a non-2xx before the stream starts surfaces the api's `error` code and the `x-trace-id` header
  - `MOCK_UPSTREAM=true` stream fixtures: a full answer (`sources → token* → done`), a mid-stream `error` (`ai_unavailable`), and a `503` before the first frame
  - works against `MOCK_UPSTREAM=true` fixtures until api:2.3 is live
- **how_to_test:**
- **needs_human:**
- **notes:**
  - 2026-10-06 — planned by `/plan-roadmap web`; the `sources` shape change is ADR-0015's cross-service change (api:2.3 on the other side)

## Phase 2 — The `/chat` page

### 2.1 — Conversation list and the two-column layout

- **status:** todo
- **depends_on:** [1.1]
- **layers:** [ui]
- **acceptance:**
  - `/chat` shows conversations on the left (title + relative time, newest first, selected row highlighted) and the thread on the right, per `docs/design/wireframes/chat.md`; below 768 px the list collapses behind a drawer button
  - empty state "No chats yet"; **+ New chat** calls `POST /api/v1/conversations`, selects the new conversation and focuses the input
  - clicking a row loads it with `GET /api/v1/conversations/{id}` and shows its messages ("reopened" state); a load error shows the error code and trace id
  - new `ConversationList` component with component tests (empty, list, selected, new chat)
  - works against `MOCK_UPSTREAM=true` fixtures until api:1.2 is live
- **how_to_test:**
- **needs_human:**
- **notes:**
  - 2026-10-06 — planned by `/plan-roadmap web`

### 2.2 — Ask in a conversation: streaming answer with sources

- **status:** todo
- **depends_on:** [1.2, 2.1]
- **layers:** [ui]
- **acceptance:**
  - **Send** posts the question to `/api/v1/conversations/{id}/ask/stream`; while streaming, Send and the input are disabled and tokens appear as they arrive
  - a `Sources` block under each assistant message lists citations as title → path, appearing as soon as the `sources` frame arrives; stored messages (reopened conversations) show their saved citations the same way
  - the counter shows `n / 4,000`; Send is disabled for an empty question or over 4,000 characters
  - an error (before or during the stream) shows a red line with the `error` code and the `trace_id` under the last user message, and re-enables the input
  - on `done`, the conversation moves to the top of the list with its new title (the first question replaces "New conversation")
  - new `Sources` component with component tests; `ChatThread`/`ChatPanel` tests updated for the citation shape
  - works against `MOCK_UPSTREAM=true` fixtures until api:2.3 is live
- **how_to_test:**
- **needs_human:**
- **notes:**
  - 2026-10-06 — planned by `/plan-roadmap web`

## Phase 3 — Live against the dev resources

### 3.1 — Mocks off: end-to-end against the local api

- **status:** todo
- **depends_on:** [2.2, api:2.3]
- **layers:** [ui, docs]
- **acceptance:**
  - with `MOCK_UPSTREAM` unset, the web app against the local api (dev Azure SQL, Foundry, AI Search through `apps/api/.env`) creates a conversation, streams a cited answer from `docs/`, lists it, reopens it and answers a follow-up that depends on the previous turn
  - one `x-trace-id` is visible across the browser response, the BFF log line and the api log line for an ask
  - the Playwright spec (`e2e/`, run manually) covers new chat → ask → sources visible → reopen
  - `docs/getting-started.md` (or `apps/web/README.md`) explains running the chatbot locally: env, ingest, `just dev`
- **how_to_test:**
- **needs_human:**
  - the migration from api:1.1 applied to the dev database and the corpus ingested (api:2.1)
  - run both services locally with your `.env` files and do the manual walk-through
- **notes:**
  - 2026-10-06 — planned by `/plan-roadmap web`
