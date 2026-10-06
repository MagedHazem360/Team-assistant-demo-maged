# ADR-0014: Knowledge-base corpus baked into the api image; re-index through a single-flight endpoint

|        |            |
| ------ | ---------- |
| Status | Proposed   |
| Date   | 2026-10-06 |

> **Deferred to phase 2** (`ARCHITECTURE.md` B2, B8 #1): phase 1 indexes `docs/` with the
> template's CLI (`uv run python -m app.ai.ingest`) run by a developer. Decide this ADR, with the
> threat model, when F3 is planned.

## Context

Team Assistant answers from the repository's own Markdown (`ARCHITECTURE.md` B1, B3). The
template's ingestion job (`app/ai/ingest.py`, ADR-0009) is a CLI that reads a local path, chunks,
embeds and uploads to the Azure AI Search index (`team-assistant-docs`). F3 asks for an admin to
re-index "with one command/endpoint". Locally the api can read the repo's files; a deployed
container cannot — the api image today copies only `app/`, `alembic.ini` and `alembic/`
(`apps/api/Dockerfile`). There is no auth (D8), so any endpoint is callable by any internal user,
and each run spends embedding calls on the shared Foundry deployment.

## Decision

We copy the corpus into the api image at `/app/corpus` at build time (locally the api reads the
repo's own folders) and expose `POST /v1/admin/ingest`, which takes no input, returns `202`, runs
`ingest_documents` in the background against that fixed path, and returns `409 ingest_running`
while a run is in progress (single-flight per process).

## Consequences

- Good: the deployed corpus is exactly the docs of the commit that was deployed — versioned,
  reviewed, reproducible; no storage account or sync job.
- Good: no input means no path traversal or SSRF surface; single-flight caps the cost of
  repeated clicks.
- Bad: a docs change reaches the assistant only after a rebuild + redeploy + re-ingest.
- Bad: single-flight is per process — with more than one replica (`apiReplicas.max` is 2–6) two
  runs can overlap; idempotent chunk ids (`sha1(source#index)`) make that wasteful, not corrupt.
- Bad: chunks of a deleted or renamed file stay in the index until a full rebuild — ingestion
  upserts, it does not prune.
- Bad: the endpoint is unauthenticated until Okta; it must stay internal-only, and it is a
  candidate for an operator-only guard (see the threat model).

## Alternatives considered

- **Blob storage container as the corpus + sync job** — rejected for the first release: more
  infrastructure (a container, a grant, an upload step) for a corpus that already lives in git.
- **CLI only (`uv run python -m app.ai.ingest`)** — rejected: there is no shell into the deployed
  container for the team, and F3 asks for one command/endpoint.
- **Ingest on api startup** — rejected: every restart or scale-out would spend embedding calls
  and slow readiness.

## References

- Related ADRs: ADR-0009 (AI runtime, ingestion job) · ADR-0012 (shared AI Search service)
- Docs: `docs/architecture/ARCHITECTURE.md` → B3, B8 #1–#2 · `apps/api/Dockerfile` ·
  `.claude/rules/70-ai.md` (ingestion)
- Threat model: `docs/security/threat-models/team-assistant.md` (to be written)
