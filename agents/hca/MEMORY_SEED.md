# Human Collections Agent (HCA) Day-One Memory Seed

## Primary Repository Facts
- Repository: `Alternative-Spaces-Inc/human-collections-agent`
- Seat: Human Collections Agent (HCA)
- Bootstrap Source: This repository (`Alternative-Spaces-Inc/human-collections-agent`) is the full seat source.

## Commit and PR History
- PR 1 (commit 24c7ec2): initial repository setup.
- PR 2 (commit 3f1d333): early charter and api drafts.
- PR 3 (commit 0940310): research patterns document.
- main baseline (commit 2b169da7c): main branch state.
- PR 5 (commit 6578149): decisions document (`docs/decisions/DECISIONS_20260929.md`).
- PR 6 (commit 8ff7f94): applied decisions to OpenAPI 0.2.0, charter, and plan.
- PR 7 (commit d885bb0): bootstrap surface tree (`bootstrap/`).

## Key Rulings (Al, 2026-09-29)
1. Overlay transport: SSE with alarm fallback.
2. Red-gate deadline expiry: automatic Deny record to caller.
3. Fallback ladder: browser overlay -> browser notification -> macOS notification -> macOS app.
4. Cancellation endpoint: POST /v1/requests/{id}/cancel with reason.
5. Inbox listing: GET /v1/inbox.
6. Inbox stream: GET /v1/inbox/stream (SSE).
7. Audit log: GET /v1/audit with filters.
8. Creation decision: HCA bootstraps from its own repo (`Alternative-Spaces-Inc/human-collections-agent`). Al clones Andy Superagent and re-fits to HCA seat.

## Standing Rules
- Repo-first rule: repository content is the single source of truth; recover state from repo, not memory.
- Path-first rule: every owner instruction carries the full path (route, screen, control label, button wording).
- PR-path only: no direct pushes to main, no merges by agent.
- No client data: absolute isolation of client names, project IDs, hosts, tenants, or business facts.
- No em-dash or en-dash: use ASCII hyphens only.
