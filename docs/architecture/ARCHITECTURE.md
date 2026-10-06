# Architecture — living document

This is the **one architecture file** for a project built on the Team Assistant. It has two
parts with different owners:

| Part                                                               | Owner                                                                  | Rule                                                                                                                                                                                                                                       |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **[Part A — Template baseline](#part-a--template-baseline)**       | The template (Orion Digital Solutions for the Diriyah Company AI team) | **Template-owned. Do not edit in a project.** It changes only when the template itself changes (a template release, recorded in [`docs/template-roadmap.md`](../template-roadmap.md)). Every project architecture is built _on top of_ it. |
| **[Part B — Project architecture](#part-b--project-architecture)** | The project team (admin + developers)                                  | **Team-maintained, by hand.** Claude and the `.claude/` skills **read** it before planning and may **propose** edits as a diff or a `Proposed` ADR — they never rewrite it on their own (ADR-0006).                                        |

If anything here disagrees with the code, **the code wins** — then fix this file (root
`CLAUDE.md`, rule 14). Deep-dives behind Part A: [overview.md](overview.md),
[tracing.md](tracing.md), [observability.md](observability.md), [data.md](data.md).

---

## Part A — Template baseline

> Template-owned. A project does not edit this part.

### A1. What the baseline is

A **use-case-agnostic accelerator** for AI applications on Azure: two independent services
wired with end-to-end request tracing and Azure Monitor observability, a database layer ready
for real models, and a `.claude/` toolkit (rules, agents, skills) so that most of the code is
written by Claude under human approval. It ships **plumbing, not product**: no business
domain, no auth, no AI feature — those are the project's job, built in the slots below.

### A2. Principles (non-negotiable in every project)

1. **One inbound edge.** The browser talks only to `apps/web` (the BFF). Service URLs and
   secrets are server-side only; never `NEXT_PUBLIC_*`.
2. **One backend.** `apps/api` (FastAPI) is the sole backend and the sole owner of data access
   and of the AI runtime. `web` is a pass-through BFF plus UI.
3. **One trace id.** `x-trace-id` = origin(4 hex) + env(1 hex) + random(27 hex); minted at the
   edge, forwarded unchanged on every hop, echoed on every response, present on every log line
   and span attribute. Origins: `web=0eb0`, `api=0c70` (`0a71` retired).
4. **Observability is fail-safe and always on.** OpenTelemetry → Azure Monitor; without a
   connection string the services still run (degraded mode, visible on `/health`). Structured
   JSON logs only; secrets redacted at source; bounded metric attributes.
5. **Every business endpoint is versioned** under `/v1` (ADR-0001). Only `/ping`, `/health`,
   `/info` are exempt.
6. **Databases are Azure SQL, on Azure — never locally.** A project's own database is
   **created by infrastructure first**, then modelled (SQLAlchemy 2 async) and migrated
   (Alembic). Migrations are applied by a gated pipeline job or a named human, never by an
   agent. External databases are accessed **read-only** through a separate engine
   (`app/db/external.py`). Driver: `mssql+aioodbc` on ODBC Driver 18, managed-identity auth when
   deployed (ADR-0008).
7. **Infrastructure is Bicep, in two tiers (ADR-0012).** The **platform tier** — Azure AI
   Foundry with its model deployments, the Container Apps Environment, the Container Registry
   and the AI Search service — pre-exists per environment, is owned by the cloud team and is
   **shared by every use case**; the template only references it (`existing`, names from
   `infra/platform/<env>.json`). The **use-case tier** — this project's container apps,
   database, storage, Key Vault, Application Insights, Search index — is `infra/main.bicep`
   composed from the infra team's **vendored building blocks** (`infra/modules/`, never edited
   here) with names per environment in `main.<env>.bicepparam`. GitHub Actions runs `what-if`
   on PR and deploys on merge in every environment; developers may deploy locally to `dev`.
   Roles a use case needs on shared resources are **requested** (`infra/grant-request.md`),
   never assigned here. Agents may `bicep build`/`lint`; they never deploy.
8. **Tests, docs, and telemetry ship with the change** — a feature without them is not done
   (root `CLAUDE.md` → _Definition of Done_).
9. **Decisions are recorded** as ADRs in `docs/adr/`; the team's design inputs live in
   `docs/design/`; the plan of record lives in `roadmaps/`.

### A3. System context (C4 level 1)

```mermaid
flowchart LR
    user(["Browser user"])

    subgraph platform["Project platform (built on the Team Assistant)"]
        web["apps/web<br/>Next.js BFF + UI"]
        api["apps/api<br/>FastAPI — backend + AI runtime"]
    end

    subgraph azure["Azure — use-case tier (this project's Bicep, per environment)"]
        pg[("Azure SQL Database<br/>project database")]
        ext[("External Azure SQL sources<br/>read-only")]
        vs[("AI Search index<br/>vector / hybrid, on the shared service")]
        kv["Key Vault"]
        azm["Azure Monitor<br/>App Insights + Log Analytics"]
    end

    subgraph shared["Azure — platform tier (cloud team, shared by all use cases)"]
        ai["Azure AI Foundry<br/>model deployments"]
        cae["Container Apps Environment"]
        acr["Container Registry"]
        srch["AI Search service"]
    end

    user -->|"HTTPS, same origin only"| web
    web -->|"server-side HTTP, x-trace-id"| api
    api -->|"SQLAlchemy 2 async"| pg
    api -.->|"read-only engine"| ext
    api -.->|"LangChain / LangGraph"| ai
    api -.->|"retrieval"| vs
    api -.->|"secrets via managed identity"| kv
    web -.->|"telemetry"| azm
    api -.->|"telemetry"| azm
```

Solid arrows exist in the template today. **Dashed arrows are baseline slots**: the template
defines where they attach and the rules they follow, and the project (or the template roadmap)
fills them in. Status per slot:

| Slot                   | In the template today                                                                                                                           | Filled by                                                                                 |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Project database       | **Implemented**: lazy `mssql+aioodbc` engine, session dependency, Alembic wired, `migrate.yml` pipeline; **no models, no migrations** by design | Project models + revisions; DB created by the use-case Bicep first (`sql-database` block) |
| External data sources  | **Implemented**: `app/db/external.py` read-only engine (`EXTERNAL_DATABASE_URL`, non-SELECT refused)                                            | The project's `docs/design/external-systems.md` + the AI query tool (roadmap 4.6)         |
| AI runtime             | **Implemented**: `app/ai/` — client seam, `retrieve → answer` graph, tool registry, prompts, telemetry, SSE streaming, ingestion (ADR-0009)     | The project's routes, prompts, tools and eval cases                                       |
| Vector store           | **Implemented**: Azure AI Search via `tools/retrieve.py` + `ingest.py` (index definition) on the **shared** search service (platform tier)      | The project's index name, corpus and ingestion sources                                    |
| Key Vault / identities | Not present in code; ingestion auth via managed identity is implemented                                                                         | Use-case Bicep (`key-vault`, `user-assigned-identity` blocks; revision 2 Phase 2)         |
| Azure Monitor          | **Implemented** in both services; alerting stack authored in Bicep (`infra/modules/observability.bicep`, `alerts.bicep`)                        | Folded into the use-case `main.bicep` (revision 2 Phase 2)                                |
| App hosting            | Dockerfiles + compose only                                                                                                                      | Container apps in the **shared** Container Apps Environment (use-case Bicep)              |
| Auth                   | Not present — projects are **internal-only** until then                                                                                         | **Okta** OIDC module behind `AUTH_MODE=none\|okta` (roadmap Phase 8)                      |

### A4. Containers (C4 level 2)

```mermaid
flowchart TB
    browser(["Browser — React UI"])

    subgraph W["apps/web — Next.js 16 App Router — :3000 — origin 0eb0"]
        wui["src/app/**/page.tsx<br/>UI (client components only where needed)"]
        wbff["src/app/api/v1/**/route.ts<br/>BFF route handlers (withBff)"]
        wlib["src/lib/<br/>trace · logger · metrics · events · observability"]
    end

    subgraph P["apps/api — FastAPI on Python 3.14 — :8000 — origin 0c70"]
        pops["app/routes.py<br/>/ping /health /info"]
        pv1["app/routers/v1.py → app/routers/<feature>.py<br/>/v1 business surface"]
        pdb["app/db/ · app/models/ · alembic/<br/>project DB (async) + external read-only engine*"]
        pai["app/ai/*<br/>client · graph · tools · prompts · telemetry"]
        pxc["app/tracing.py · observability.py · logging_config.py<br/>cross-cutting"]
    end

    browser -->|"same-origin fetch"| wbff
    wui -.-> browser
    wbff --> wlib
    wlib -->|"API_BASE_URL, x-trace-id"| pxc
    pxc --> pops
    pxc --> pv1
    pv1 --> pdb
    pv1 --> pai
    pai --> pdb
```

`*` = baseline slot, not yet in the template code (see A3).

| App        | Stack                                                         | Port | Origin | OTel service name |
| ---------- | ------------------------------------------------------------- | ---- | ------ | ----------------- |
| `apps/web` | Next.js 16 (App Router), React 19, pino, OTel → Azure Monitor | 3000 | `0eb0` | `<slug>-web`      |
| `apps/api` | Python 3.14, FastAPI, SQLAlchemy 2 async, Alembic, structlog  | 8000 | `0c70` | `<slug>-api`      |

Independent packages: no pnpm workspace, per-app lockfiles and Dockerfiles, independent SemVer.

### A5. Request flows the baseline guarantees

1. **UI → BFF → api**: `withBff` adopts/mints the trace id → `fetchUpstream` forwards it →
   `TraceMiddleware` adopts it → business router → response echoes it. Upstream failure = BFF
   `502` with `trace_id`.
2. **api → project DB**: `Depends(get_session)` per request; ORM constructs; spans from the
   SQLAlchemy instrumentation (both engines). **api → external DB**:
   `Depends(get_external_session)`, read-only by construction.
3. **api → AI model / retrieval / external DB** _(slots)_: through `app/ai/` with per-call
   timeouts, `gen_ai` telemetry, no prompt content in logs, read-only external access.
4. **Long-running AI responses**: streamed over SSE from `/v1` through a pass-through BFF
   variant _(slot)_; non-streaming first.

### A6. Environments and delivery

- `APP_ENV` = `local | dev | staging | prod` drives the trace env nibble and the `env` log
  field. Same code path everywhere; config is env-var driven; secrets come from Key Vault.
- Branch promotion `development → staging → main` is enforced by CI; CODEOWNERS gate merges.
- Three resource groups (`dev`, `staging`, `prod`) host every use case. In each, the **platform
  tier** (Foundry, Container Apps Environment, Container Registry, AI Search service) pre-exists
  and is described by `infra/platform/<env>.json`; this project's **use-case tier** is
  `infra/main.bicep` + `main.<env>.bicepparam`, deployed by `.github/workflows/infra.yml`
  (`what-if` on PR, deploy on merge) or locally to `dev`. Roles on shared resources are
  requested from the cloud team (`infra/grant-request.md`).
- Azure SQL is reachable over public endpoints with firewall rules (Azure services + developer
  IPs) for now; private endpoints are a later hardening step.
- Migrations are applied per environment by a gated pipeline job _(target)_ — never
  automatically on deploy.

### A7. Delivery status of the baseline

Honest state of what exists (see also the root `README.md` → _Scope & delivery status_ and
[`docs/template-roadmap.md`](../template-roadmap.md)):

| Area                                          | State                                                                                                               |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Two services + trace contract + observability | ✅ implemented and unit-tested                                                                                      |
| Data layer (engine, session, Alembic)         | ✅ Azure SQL via `mssql+aioodbc`, `migrate.yml` pipeline · ⏳ no models / migrations by design; first apply pending |
| External read-only data access                | ✅ `app/db/external.py` (unit-tested offline)                                                                       |
| AI runtime (`app/ai/`) + Azure AI Search      | ✅ implemented and unit-tested offline (ADR-0009); runtime proof against a live deployment pending                  |
| Infra as code                                 | ⏳ Bicep: observability/alerting modules exist; use-case `main.bicep` on the shared platform in progress (ADR-0012) |
| Auth                                          | ⛔ not included (internal-only); Okta module planned (roadmap Phase 8)                                              |
| Workflow skills (plan/implement/start)        | ⏳ planned (roadmap Phase 5)                                                                                        |

---

## Part B — Project architecture

> Team-maintained. Fill this in after `/rename-project` (later `/init-project`) and keep it
> current as the project evolves. Everything the planning skills generate starts from here and
> from `docs/design/`. Delete the guidance text in each section once you have written the real
> content.

### B1. Project summary

**Team Assistant** — a simple internal chatbot that helps the team find and use its own
documents: a user opens `/chat`, starts a conversation, asks a question; the answer streams back
and cites the documents it came from (title + path). Follow-up questions work because the api
sends the conversation's last 10 stored messages as history. Conversations and messages are
stored in Azure SQL; the corpus is the repo's `docs/**/*.md`, indexed in Azure AI Search
(`team-assistant-docs`). No login (internal only; Okta later).

- **Environments:** `dev` · `staging` · `prod`. **Team:** Maged Hazem (`@MagedHazem360`) — admin, backend and frontend.
- **Phase 1 (first release):** F1 + F2, the corpus ingested with the template's CLI.
  **Phase 2:** F3 (in-app re-index), the platform deployment (B6).

### B2. Feature map

| Id  | Feature              | User story                                                                                                | Layers                              | Phase |
| --- | -------------------- | --------------------------------------------------------------------------------------------------------- | ----------------------------------- | ----- |
| F1  | Chat with citations  | As a user I ask a question on `/chat` and the answer streams back with citations (document title + path). | ai, endpoint, bff, ui               | 1     |
| F2  | Conversation history | As a user I start a new conversation, see past ones, reopen one and continue it (follow-ups use history). | model, migration, endpoint, bff, ui | 1     |
| F3  | Knowledge base       | As an admin I re-index the corpus from the app (`POST /v1/admin/ingest`).                                 | ai, endpoint                        | 2     |

Phase 1 indexes the corpus with the existing CLI, run by a developer from `apps/api`:
`uv run python -m app.ai.ingest ../../docs` (re-run after the docs change).

### B3. AI components

- The template's `retrieve → answer` graph with `AzureSearchRetriever` on index `team-assistant-docs`
  (`gpt-4.1` chat, `text-embedding-3-large` embeddings, 3072 dimensions).
- History = the last 10 messages of the conversation **before** the current question, loaded from
  Azure SQL by the api (the browser sends only the question).
- Streaming frames `sources → token* → done | error`; `sources` and `done` carry `[{title, path}]`
  (ADR-0015) — ingestion adds a `title` index field (first `# ` heading, else the file name);
  `path` is relative to the corpus root (`docs/`).
- Ingestion (phase 1): the template's CLI over `docs/`. Phase 2: `POST /v1/admin/ingest` → `202`,
  background, single-flight (`409` while one runs), corpus baked into the api image (ADR-0014,
  threat model first).
- Telemetry per the template: no message content in logs, spans, metrics or events; no
  conversation ids in metric attributes.
- Limits: question 1–4,000 characters; `top_k = 5`; timeout 60 s (BFF hop timeout ≥ 60 s, rule 30).
  A model/search failure **before the first frame** → `503 ai_unavailable`; **after it** →
  `event: error {"error": "ai_unavailable"}` and the stream ends (the UI shows the code and the
  `x-trace-id` response header). The user message is kept either way; a disconnect before `done`
  stores no assistant message.

### B4. Data stores (Azure SQL, owned by `apps/api`)

Pre-created dev database (`DATABASE_URL` in `apps/api/.env`); read/write from `apps/api` only.

Table `conversations`:

| Column       | Type               | Rules                                                                                                |
| ------------ | ------------------ | ---------------------------------------------------------------------------------------------------- |
| `id`         | `uniqueidentifier` | PK                                                                                                   |
| `title`      | `nvarchar(200)`    | not null; given on create, else "New conversation" until the first question replaces it (cut to 200) |
| `created_at` | `datetime2(3)`     | not null, server default                                                                             |
| `updated_at` | `datetime2(3)`     | not null, server default; set on every new message (the list is newest first)                        |

Table `messages`:

| Column            | Type               | Rules                                                                                      |
| ----------------- | ------------------ | ------------------------------------------------------------------------------------------ |
| `id`              | `uniqueidentifier` | PK                                                                                         |
| `conversation_id` | `uniqueidentifier` | FK → `conversations.id` (`NO ACTION`); index `(conversation_id, created_at)`               |
| `role`            | `nvarchar(20)`     | named `CHECK (role IN ('user','assistant'))` (rule 25)                                     |
| `content`         | `nvarchar(max)`    | not null                                                                                   |
| `citations`       | `nvarchar(max)`    | nullable, JSON `[{title, path}]`, named `CHECK (citations IS NULL OR ISJSON(citations)=1)` |
| `token_count`     | `int`              | nullable                                                                                   |
| `created_at`      | `datetime2(3)`     | not null, server default                                                                   |

Retention: indefinite in this release. PII: free text typed by users. Conversations are shared
(no owner) until auth lands — ADR-0013.

### B4a. API surface (`/v1`)

| Method | Path                                | Request                                  | Response                                                                                     | Errors                                                              |
| ------ | ----------------------------------- | ---------------------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| POST   | `/v1/conversations`                 | `{ "title"?: string }` (≤ 200)           | `201` `{ id, title, created_at, updated_at }`                                                | `422 validation_error`                                              |
| GET    | `/v1/conversations`                 | `?limit=50` (1–100)                      | `200` `{ items: [{ id, title, updated_at }], count }` newest first; `count` = items returned | `422 validation_error`                                              |
| GET    | `/v1/conversations/{id}`            | —                                        | `200` conversation + `messages: [{ id, role, content, citations, created_at }]`              | `404 not_found`                                                     |
| POST   | `/v1/conversations/{id}/ask`        | `{ "question": string }` (1–4,000 chars) | `200` `{ message_id, answer, citations }`                                                    | `404`, `413 payload_too_large`, `422` (empty), `503 ai_unavailable` |
| POST   | `/v1/conversations/{id}/ask/stream` | same                                     | SSE `sources → token* → done` or `error`                                                     | same, only before the first frame                                   |
| POST   | `/v1/admin/ingest` _(phase 2)_      | —                                        | `202 { "status": "started" }`                                                                | `409 ingest_running`                                                |

Error bodies are the template's `{ "error": code, "trace_id": id }`. The BFF mirrors every route
under `/api/v1/…` with `MOCK_UPSTREAM` fixtures for each. These routes **replace** the template's
`/api/v1/assistant/ask[/stream]` BFF routes (which point at api routes that do not exist).

### B5. Integrations

None (no external database).

### B6. Infrastructure topology

Phase 1 runs locally against pre-created Foundry, AI Search and Azure SQL (`apps/api/.env`); the
platform deployment (Bicep on the cloud team's shared tier) is phase 2 (`roadmaps/infra` 1.1,
deferred). Platform manifests (`infra/platform/<env>.json`) keep template placeholders until then
(resource group `rg-ai-dev` confirmed for dev); owned resources follow
`<prefix>-team-assistant-<env>` (Key Vault `kv-team-assistan-<env>`), Search index
`team-assistant-docs`. Developer IPs: not needed (the dev resources allow all IPs).

- **Migrations:** Maged Hazem applies the reviewed revision to the dev database by hand
  (`alembic upgrade head`); agents never apply migrations.
- **Local prerequisites:** ODBC Driver 18 (the api's Azure SQL driver).

### B7. Security and auth posture

Internal-only, no user auth; **conversations are shared** (every user can read every
conversation) until Okta lands — ADR-0013 (Proposed); users should not paste personal or
confidential data. Prompt injection through the corpus is mitigated by the template's "context is
data" prompt rule; the corpus is git-controlled. Phase 2's ingest endpoint needs the threat model
`docs/security/threat-models/team-assistant.md` and ADR-0014 first.

### B8. Decisions and open questions

| #   | Decision                                                                                         |
| --- | ------------------------------------------------------------------------------------------------ |
| 1   | Corpus: `docs/**/*.md`; phase 1 ingests with the CLI; phase 2 bakes it into the image (ADR-0014) |
| 2   | Ingest endpoint (phase 2): `202`, background, single-flight, `409` when busy                     |
| 3   | Title optional on create; else the first question (cut to 200) replaces "New conversation"       |
| 4   | History sent to the model: the last 10 stored messages, loaded by the api                        |
| 5   | Messages kept indefinitely; no purge job                                                         |
| 6   | Rename and delete: out of scope                                                                  |
| 7   | Shared conversations — ADR-0013 · citation shape `[{title, path}]` — ADR-0015                    |
| 8   | The conversation routes replace the template's `assistant` ask routes                            |

Open: none for phase 1. Phase 2: the guard on the ingest endpoint (threat model).

### Request flow (F1 — ask/stream)

```mermaid
sequenceDiagram
    participant U as Browser /chat
    participant W as web BFF
    participant A as api /v1/conversations/{id}/ask/stream
    participant S as AI Search index
    participant M as Foundry chat deployment
    participant D as Azure SQL

    U->>W: POST {question}
    W->>A: POST (x-trace-id)
    A->>D: SELECT last 10 messages; INSERT messages (user)
    A->>S: hybrid query (top 5)
    A-->>W: event: sources [{title, path}]
    A->>M: history (≤10) + context + question, streamed
    A-->>W: event: token …
    A->>D: INSERT messages (assistant, citations); UPDATE conversations.updated_at
    A-->>W: event: done
    W-->>U: same frames, same trace id
```

### B9. Change log of this document

| Date       | Who         | What changed                                                                                                                                                                         |
| ---------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 2026-10-06 | Maged Hazem | `/init-project`: project named, B1 and B7 recorded                                                                                                                                   |
| 2026-10-06 | Maged Hazem | B1–B8 written from the team design (chat with citations, conversation history, knowledge base, flow)                                                                                 |
| 2026-10-06 | Maged Hazem | Architecture review applied: phase 1 = F1 + F2 (CLI ingest), F3 → phase 2; streaming error rule; citation shape; history from the DB; routes replace `assistant`; ADR-0013/0014/0015 |
