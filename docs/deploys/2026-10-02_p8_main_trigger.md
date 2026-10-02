# 2026-10-02: HCA-P8, deploy on every merge to main

## Why now

P7 showed the manual deploy path is fragile: the link gate is a build
substitution, so a rebuild that forgets `_REQUIRE_GOOGLE_LINK=1` silently
reopens the human routes to unlinked devices. Every deploy also cost the
owner a separate approval on top of the merge. With the trigger, the squash
merge to main is the one red gate and the pinned substitutions make the
gate drop impossible without an explicit `_ALLOW_UNGATED=1`.

## What this change does

- `cloudbuild.yaml`: new first step `guard`. It fails the build when
  `_GOOGLE_CLIENT_ID` is empty or `_REQUIRE_GOOGLE_LINK` is not `1`, unless
  `_ALLOW_UNGATED=1` (fresh project, no OAuth client yet). Default of
  `_REQUIRE_GOOGLE_LINK` becomes `1`. Nothing else in the pipeline changes.
- Runbook step 11 rewritten Terminal first: connection, repository link,
  trigger. The trigger pins `_GOOGLE_CLIENT_ID`, `_OWNER_EMAILS`,
  `_REQUIRE_GOOGLE_LINK=1`, `_CLOUDSQL_INSTANCE`, and runs as the same
  service account the manual builds used (read from the last build), so no
  new IAM grant.
- Teardown block extended with the repository link and the connection.

## Inventory before (2026-10-02 16:0x UTC, read-only)

No triggers, no connections in the project. Build service accounts present:
the Cloud Build legacy account with `cloudbuild.builds.builder`, the
default compute account with `run.admin`, `artifactregistry.writer`,
`logging.logWriter`, `storage.objectViewer` and `editor`.

## Red gates (owner)

1. GitHub connection `github-hca`: installs the Cloud Build GitHub App on the
   organization for this one repository (browser step from the URL gcloud
   prints), and grants `secretmanager.admin` to the Cloud Build service agent
   for the connection token.
2. Trigger `hca-main-deploy` with the pinned values.

## Proof plan

Merge a docs-only commit after the trigger exists; expect one build in
Cloud Build History for that sha, status SUCCESS, guard step output
`guard: gate pinned`, and a new revision whose `commit` label is the merge
sha, with `HCA_REQUIRE_GOOGLE_LINK=1` still set.

## Proof (2026-10-02 17:0x UTC)

- Connection `github-hca`: COMPLETE on the organization installation of the
  Cloud Build GitHub App, limited to this repository. Two false starts: the
  first authorization bound the connection to the owner's personal-account
  installation, and the stored user token could not be repointed, so the
  connection was deleted and recreated and the organization installation
  chosen in the Google install page. gcloud did not grant the Secret Manager
  role to the Cloud Build service agent by itself; it was granted by hand.
- Trigger `hca-main-deploy`, id `60db90df`, branch `^main$`, compute service
  account, substitutions `_REQUIRE_GOOGLE_LINK=1`, client id, owner list,
  Cloud SQL instance.
- PR 26 squash merged as main `1a6a28fd`. The merge fired build `e53eb2f3`
  (trigger name recorded on the build): guard, pytest, build, push, migrate,
  deploy all SUCCESS. Guard log line: `guard: gate pinned`.
- Cloud Run revision `hca-api-service-00007`, label `commit=1a6a28fd`, env
  `HCA_REQUIRE_GOOGLE_LINK=1`, client id and owner list present.

Result: from this merge on, a squash merge to main is the whole deploy.
The manual block in runbook step 8 is the fallback only.
