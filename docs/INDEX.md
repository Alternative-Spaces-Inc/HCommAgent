# Index

Every file in the Human Collections Agent repo, one line each. Agents boot from this repo: start at [agents/hca/HCA_BOOTSTRAP.md](../agents/hca/HCA_BOOTSTRAP.md).

- [.github/gates/check_client_data.py](../.github/gates/check_client_data.py): Gate script run by the CI workflow
- [.github/gates/check_manifest.py](../.github/gates/check_manifest.py): Gate script run by the CI workflow
- [.github/gates/check_no_dashes.py](../.github/gates/check_no_dashes.py): Gate script run by the CI workflow
- [.github/gates/client_data_denylist.txt](../.github/gates/client_data_denylist.txt): Client-data denylist, owner maintained
- [.github/workflows/gates.yml](../.github/workflows/gates.yml): CI: dash, client-data, manifest and OpenAPI gates
- [README.md](../README.md): Repo front page and pointers
- [agents/hca/CURRENT.md](../agents/hca/CURRENT.md): Current state of the seat and repo
- [agents/hca/HCA_BOOTSTRAP.md](../agents/hca/HCA_BOOTSTRAP.md): Seat boot contract, long form, boot sequence
- [agents/hca/IDENTITY.md](../agents/hca/IDENTITY.md): Seat identity: name, values, hard constraints
- [agents/hca/MEMORY_SEED.md](../agents/hca/MEMORY_SEED.md): Day-one durable facts for a fresh clone
- [api/openapi.yaml](../api/openapi.yaml): HCA collection API, OpenAPI 0.2.0 (four endpoints added 2026-09-29)
- [bootstrap/HCA_MANIFEST.yaml](../bootstrap/HCA_MANIFEST.yaml): Seat manifest: skills with sha256, rules, surface files
- [bootstrap/agents-surface/BOOTSTRAP.md](../bootstrap/agents-surface/BOOTSTRAP.md): The .agents/BOOTSTRAP.md installed into the sandbox
- [bootstrap/agents-surface/rules/path_first_owner_instructions.md](../bootstrap/agents-surface/rules/path_first_owner_instructions.md): Seat rule installed into .agents/rules
- [bootstrap/agents-surface/rules/repo_first_results.md](../bootstrap/agents-surface/rules/repo_first_results.md): Seat rule installed into .agents/rules
- [bootstrap/agents-surface/rules/sub_agent_prompts_opus55.md](../bootstrap/agents-surface/rules/sub_agent_prompts_opus55.md): Seat rule installed into .agents/rules
- [bootstrap/sandbox-bootstrap.sh](../bootstrap/sandbox-bootstrap.sh): Thin caller: finds or clones this repo, runs the installer
- [bootstrap/seat_sandbox_bootstrap.sh](../bootstrap/seat_sandbox_bootstrap.sh): Installer: BOOTSTRAP.md, rules, mcps, manifest skills into the agent sandbox
- [bootstrap/skills/agent_selfrecover.sh](../bootstrap/skills/agent_selfrecover.sh): Seat copy of a factory skill (provenance header at line 2)
- [bootstrap/skills/approved_run.py](../bootstrap/skills/approved_run.py): Seat copy of a factory skill (provenance header at line 2)
- [bootstrap/skills/boot_heartbeat.sh](../bootstrap/skills/boot_heartbeat.sh): Seat copy of a factory skill (provenance header at line 2)
- [bootstrap/skills/gh_safe_commit.py](../bootstrap/skills/gh_safe_commit.py): Seat copy of a factory skill (provenance header at line 2)
- [bootstrap/skills/mac_bridge.py](../bootstrap/skills/mac_bridge.py): Seat copy of a factory skill (provenance header at line 2)
- [bootstrap/skills/sandbox_recovery.sh](../bootstrap/skills/sandbox_recovery.sh): Seat copy of a factory skill (provenance header at line 2)
- [bootstrap/skills/secret-parity-check.sh](../bootstrap/skills/secret-parity-check.sh): Seat copy of a factory skill (provenance header at line 2)
- [bootstrap/skills/skill_restore.py](../bootstrap/skills/skill_restore.py): Seat copy of a factory skill (provenance header at line 2)
- [bootstrap/skills/task-journal.sh](../bootstrap/skills/task-journal.sh): Seat copy of a factory skill (provenance header at line 2)
- [bootstrap/skills/typed_facts.py](../bootstrap/skills/typed_facts.py): Seat copy of a factory skill (provenance header at line 2)
- [docs/HCA_CHARTER_v1.md](../docs/HCA_CHARTER_v1.md): Charter: purpose, surfaces ladder, controls, decisions applied
- [docs/decisions/DECISIONS_20260929.md](../docs/decisions/DECISIONS_20260929.md): Al rulings of 2026-09-29: seven design decisions, creation shape
- [docs/plans/HCA_CLOUD_RUN_PLAN_v1.md](../docs/plans/HCA_CLOUD_RUN_PLAN_v1.md): Cloud Run hosting plan, reconciled with the charter and decisions
- [docs/research/UNIVERSAL_INTERFACE_PATTERNS_v1.md](../docs/research/UNIVERSAL_INTERFACE_PATTERNS_v1.md): Prior art on universal human-in-the-loop interfaces
- [platform/about-you/HCA_ABOUT_YOU.md](../platform/about-you/HCA_ABOUT_YOU.md): About you paste text (cap 3,144 characters)
- [platform/soul-renders/hca.md](../platform/soul-renders/hca.md): Soul render for the Base44 Personalization, Identity field
