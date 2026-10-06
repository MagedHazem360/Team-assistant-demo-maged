# ADR-0015: Citations carry a title and a path (index field + `sources` frame shape)

|        |            |
| ------ | ---------- |
| Status | Proposed   |
| Date   | 2026-10-06 |

## Context

The template's streaming contract (`app/ai/streaming.py`, ADR-0009/0010) sends
`event: sources` and `event: done` with `sources: string[]` — the `source` field of each
retrieved chunk, i.e. the file path. The web client parses exactly that
(`apps/web/src/lib/chat-client.ts`). Team Assistant's design shows each citation as a readable
title next to its path (`docs/design/wireframes/chat.md`, B3) and stores citations on the
assistant message (`messages.citations`, JSON). The index (`ingest.build_index`) has no title
field today. Changing the frame shape is a cross-service contract change (rule 00).

## Decision

We add a `title` field to the search index (first `# ` heading of the file, else the file name),
return it from the retriever, and change `sources` in the `sources` and `done` frames from
`string[]` to `[{ "title": string, "path": string }]` — the same shape stored in
`messages.citations` and returned by `GET /v1/conversations/{id}`. `path` is relative to the
corpus root, so the same document has the same path locally and in the image.

## Consequences

- Good: one citation shape end to end — index → frame → database → UI.
- Good: adding a field to an existing Azure AI Search index is non-breaking; `ensure_index`
  (create-or-update) applies it, and a re-ingest fills it.
- Bad: the web client, its mocks and tests, and the api streaming tests change in the same
  release (both services bump MINOR; the frame shape change is breaking for any other consumer
  of `/v1/assistant/ask/stream` — none exists).
- Bad: chunks ingested before the change have no title until re-ingested.

## Alternatives considered

- **Keep `string[]` and derive the title in the UI from the path** — rejected: the file name is
  not the document title, and the UI cannot read the document.
- **Look titles up at answer time** — rejected: an extra I/O per answer for data that is static
  per document.

## References

- Related ADRs: ADR-0009 (AI runtime) · ADR-0010 (SSE pass-through, MOCK_UPSTREAM)
- Docs: `docs/architecture/ARCHITECTURE.md` → B3, B4 · `docs/design/wireframes/chat.md`
