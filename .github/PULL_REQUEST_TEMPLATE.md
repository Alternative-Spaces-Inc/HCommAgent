## Summary

Briefly describe the changes in this pull request for HCommAgent, a human control plane for agentic retail, marketing, merchandising and creative operations.

## Checklist

### Quality Gates (CI Alignment)
- [ ] Check No Dash Characters: `python3 .github/gates/check_no_dashes.py` passed (no U+2014 or U+2013)
- [ ] Check Client Data Hygiene: `python3 .github/gates/check_client_data.py` passed
- [ ] Check Bootstrap Manifest Integrity: `python3 .github/gates/check_manifest.py` passed (if bootstrap/ changed)
- [ ] Validate OpenAPI Specification: `openapi-spec-validator api/openapi.yaml` passed (if routes changed)
- [ ] API Tests (Postgres): `cd api && pytest -v` passed with tests added for new behavior
- [ ] Extension Tests (Node 20): `cd extension && npm test` passed (if extension changed)
- [ ] Python Lint (ruff): `ruff check api .github/gates scripts` clean
- [ ] Python Dependency Audit (pip-audit): `cd api && pip-audit -r requirements.txt` passed

### Governance and Hygiene Rules
- [ ] Schema / Model changes: migration added and `python3 scripts/check_migrated.py` passes
- [ ] OpenAPI specification: `api/openapi.yaml` updated if API routes changed
- [ ] Documentation: updated in `docs/` and listed in `docs/INDEX.md` if applicable
- [ ] Secret hygiene: no secrets, credentials, API tokens, project IDs, or hostnames in code or commits
