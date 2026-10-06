# Infra roadmap — Team Assistant

Use-case-tier Bicep (`infra/main.bicep` + `main.<env>.bicepparam`, ADR-0012): blocks called,
param files, grants requested, deploy steps. Schema: [`../README.md`](../README.md).
Written by `/plan-roadmap infra`; executed by `/implement-next infra`.

## Phase 1 — Foundation

### 1.1 — First dev apply

- **status:** blocked
- **depends_on:** []
- **layers:** [infra]
- **acceptance:**
  - `infra/platform/dev.json` holds the cloud team's real dev manifest (no `00000000-…` / `rg-ai-dev` placeholders) and `main.dev.bicepparam` the real SQL Entra admin group
  - step 1 (`deployContainerApps = false`) is deployed to `dev`: SQL server + database, Key Vault, storage, App Insights and the Search index exist
  - the grants in `infra/grant-request.md` are granted by the cloud team and the contained SQL user exists
  - step 2 (`deployContainerApps = true`) is deployed; `ca-team-assistant-dev-api` `/health` answers
- **how_to_test:**
- **needs_human:**
  - the cloud team's real `dev` platform manifest and the SQL Entra admin group (login + object id)
  - `az login`, then `make infra-whatif ENV=dev` and `make infra-deploy ENV=dev` (step 1)
  - `node scripts/grant-request.mjs` → send to the cloud team → wait for the grants
  - create the contained SQL user for the api identity
  - flip `deployContainerApps = true` in `main.dev.bicepparam` and deploy again (step 2)
- **notes:**
  - 2026-10-06 — created by `/init-project`
  - 2026-10-06 — blocked (deferred): the dev resources (Foundry, AI Search, Azure SQL) are pre-created and the first release runs locally against `apps/api/.env` (ARCHITECTURE.md B6); the platform deployment is the second exercise — unblock then, with the real manifests
