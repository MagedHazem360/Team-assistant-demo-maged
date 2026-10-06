# Changelog — `apps/web`

All notable changes to the **web** service (Next.js BFF + UI). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/) from the consumer's perspective (root `CLAUDE.md`, rule 15).
The two services version independently.

## [Unreleased]

## [0.2.0] — 2026-10-06

### Added

- **Conversation BFF routes** (roadmap web 1.1): `POST /api/v1/conversations`,
  `GET /api/v1/conversations?limit=`, `GET /api/v1/conversations/{id}` → the api's
  `/v1/conversations…`, carrying and echoing `x-trace-id`; api responses (and its
  `{error, trace_id}` errors) pass through with their status; an unreachable api is `502
upstream_unreachable`. The BFF forwards only `title` / `limit`, checks `{id}` as a UUID and caps
  request bodies at 4 KB (`413 payload_too_large`), so a client can never steer the upstream path
  or query. Calls are bounded at 35 s (serverless DB cold start). Shared helper
  `src/lib/conversations.ts`.
- `MOCK_UPSTREAM=true` fixtures `src/mocks/conversations.ts` (list, empty list, a conversation
  with messages and citations, created, and a `404` for any other id), typed from
  `src/lib/api-types.ts`.

## [0.1.0] — 2026-09-28

### Added

- **UI foundation** (ADR-0010): Tailwind CSS v4 (`@tailwindcss/postcss`), chat components
  (`src/components/chat/`: `ChatThread`, `MessageInput`, `ChatPanel`), the browser streaming
  client `src/lib/chat-client.ts` (`askStream`), SSE helpers `src/lib/sse.ts`, and the example
  page `/chat`.
- **Streaming pass-through** for the BFF: `streamUpstream` + `proxyStream` (`src/lib/stream.ts`,
  5-minute whole-answer bound, `x-trace-id` forwarded, no proxy buffering).
- **Mock-upstream mode**: `MOCK_UPSTREAM=true` (server-side, refused in `prod`) makes a BFF
  route serve fixtures from `src/mocks/` (`src/lib/mocks.ts`, responses marked
  `x-mock-upstream: true`).
- Example business routes `POST /api/v1/assistant/ask` and `POST /api/v1/assistant/ask/stream`
  (mock or proxy to the api's `/v1/assistant/…`, which a project adds on `app/ai/`).
- **API contract types**: `src/lib/api-types.ts` generated from `docs/reference/openapi.json`
  (`pnpm api-types` / `make openapi`).
- **Component tests** (ADR-0011): React Testing Library in Vitest with per-file jsdom; coverage
  now includes `src/components/**/*.tsx`. New scripts: `typecheck`, `api-types`.

## [0.0.0] — 2026-09-20

- Template baseline. No features shipped.
