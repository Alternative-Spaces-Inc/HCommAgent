# Human Collections Agent (HCA) Operational State

## Repository Contents
- `README.md`: overview and pointers to seat bootstrap and documentation index.
- `api/openapi.yaml`: OpenAPI 0.2.0 spec for human collection API.
- `docs/HCA_CHARTER_v1.md`: charter defining the surface ladder and API scope.
- `docs/plans/HCA_CLOUD_RUN_PLAN_v1.md`: deployment plan for HCA service.
- `docs/research/UNIVERSAL_INTERFACE_PATTERNS_v1.md`: research on interface patterns.
- `docs/decisions/DECISIONS_20260929.md`: authoritative rulings (PR 5, commit 6578149).
- `bootstrap/`: sandbox bootstrap scripts, manifest, surface BOOTSTRAP.md, rules, skills.
- `agents/hca/`: seat definition (HCA_BOOTSTRAP.md, CURRENT.md, IDENTITY.md, MEMORY_SEED.md).
- `platform/`: soul render (`platform/soul-renders/hca.md`) and About you text (`platform/about-you/HCA_ABOUT_YOU.md`).
- `.github/`: gate scripts and workflow definitions.

## Commit and PR History
- PR 1 (commit 24c7ec2): initial repository setup.
- PR 2 (commit 3f1d333): early charter and api drafts.
- PR 3 (commit 0940310): research patterns document.
- main (commit 2b169da7c): baseline main commit.
- PR 5 (commit 6578149): decisions document (docs/decisions/DECISIONS_20260929.md).
- PR 6 (commit 8ff7f94): applied decisions to OpenAPI 0.2.0, charter, and plan.
- PR 7 (commit d885bb0): added bootstrap surface tree.

## Authoritative Decisions (2026-09-29)
1. Transport for browser overlay: Server-Sent Events (SSE) with alarm fallback.
2. Red-gate deadline with no answer: automatic Deny record sent to caller on expiry.
3. Fallback ladder: browser overlay -> browser notification -> macOS notification -> dedicated macOS app.
4. Cancellation endpoint: POST /v1/requests/{id}/cancel with reason.
5. Inbox listing: GET /v1/inbox listing pending requests across agents and surfaces.
6. Inbox stream: GET /v1/inbox/stream (SSE) pushing updates to open surfaces.
7. Audit log: GET /v1/audit with filters for agent, time window, surface, and outcome.

## Build Backlog (Not Yet Built)
- Core service implementation (Go on Cloud Run).
- Browser extension overlay client.
- Browser notification client.
- macOS notification client.
- Dedicated macOS app client.

## Next Action
Al clones the Andy Superagent in Base44, then Andy repoints the thin caller to this repository and re-fits from it.
