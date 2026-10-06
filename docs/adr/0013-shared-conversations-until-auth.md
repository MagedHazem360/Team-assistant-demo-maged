# ADR-0013: Conversations are shared (no owner) until auth lands

|        |            |
| ------ | ---------- |
| Status | Proposed   |
| Date   | 2026-10-06 |

## Context

Team Assistant stores conversations and messages in Azure SQL (`ARCHITECTURE.md` B4) and lists
them on `/chat` (F2). The project is internal-only with no user auth (team decision D8, B7); Okta
is planned (template roadmap Phase 8) but not scheduled. Without an identity there is nothing to
own a conversation by, so either every visitor sees every conversation, or history is scoped to
something weaker than a user (a browser cookie), or history waits for auth. Messages are free text
typed by people and kept indefinitely (B8 #5), so whatever is visible is visible to every internal
user of the deployment.

## Decision

We store conversations without an owner column and show every conversation to every user of the
deployment until Okta lands; adding ownership then is a new nullable `owner_id` column plus a
filter, recorded in its own ADR with the threat model auth requires.

## Consequences

- Good: F2 ships now with a simple schema (`conversations`, `messages`) and no identity plumbing.
- Good: the later change is additive (a nullable column, a backfill decision, a `WHERE`).
- Bad: anyone who can reach the deployment reads every conversation — users must be told not to
  paste personal or confidential data, and the deployment must stay internal-only.
- Bad: existing conversations will have no owner when auth arrives; the team decides then
  whether they stay shared, are archived, or are deleted.

## Alternatives considered

- **Per-browser scoping (anonymous cookie id)** — rejected: it looks like privacy but is not
  (no authentication, cookies are shareable and lost on a new device) and it would be replaced
  wholesale by auth.
- **Wait for Okta before F2** — rejected: it blocks the conversation-history feature on an
  unscheduled module.
- **Do not persist conversations** — rejected: history is a core feature (B2 F2).

## References

- Related ADRs: ADR-0008 (Azure SQL data layer)
- Docs: `docs/architecture/ARCHITECTURE.md` → B4, B7, B8 · `docs/design/db-design.md` ·
  `.claude/rules/50-security.md` (no auth yet)
- Threat model: `docs/security/threat-models/team-assistant.md` (to be written)
