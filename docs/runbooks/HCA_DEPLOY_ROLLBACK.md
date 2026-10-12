# Candidate deployment and traffic recovery

For an existing service and an explicitly approved tested commit, the pipeline tests, acquires a deployment lease and migrates before creating a candidate with zero production traffic. It validates the exact revision, runs the agent smoke against that revision's tag URL, and promotes that revision with an optimistic concurrency precondition. Source tests simulate these decisions; they do not establish that a production deployment or recovery drill has occurred.

## Exact commit authorization

`_APPROVED_COMMIT` defaults to empty. Before any cloud mutation, the first guard requires a full lowercase 40-character commit SHA equal to this build's `COMMIT_SHA`. An automatic main trigger therefore stops unless the operator explicitly authorizes that exact tested source revision; source publication and owner-reported client validation do not supply rollout approval.

After exact-revision CI passes and cloud prerequisites are verified, an authorized operator supplies `_APPROVED_COMMIT=<TESTED_COMMIT>` for the intended manual build or trigger run. For a manual source submission, also supply `COMMIT_SHA=<TESTED_COMMIT>` and use a clean checkout of that exact revision; inspect the source-upload manifest. Leave the default empty for routine repository integration, and do not configure a moving branch value or automatic approval bypass. The approved SHA labels the immutable image/candidate throughout validation; approval of one SHA does not authorize later commits.

## Identity carried between stages

The pushed image is resolved to `repository@sha256:<64 hex characters>` and checked against this build's repository before any migration or deployment. A commit tag is an output label, never the deployment identity. The migration job is `${_MIGRATE_JOB}-${BUILD_ID}`; its definition cannot be overwritten by a different build. The candidate tag is `candidate-${BUILD_ID}`.

After deployment, the pipeline compares the v1 service/revision descriptions with the v2 service description. Configuration, full commit SHA, build UUID, image digest, candidate revision, zero traffic and HTTPS tag URL must agree. `/workspace/hca-candidate.json` preserves that identity, the service UID/etag, deployment lease, declared capacity and previous traffic. `/workspace` is shared across Cloud Build containers; `/tmp` is not. The snapshot contains deployment metadata, not secret values.

The smoke stage reads the saved URL. It marks the snapshot successful only after the smoke succeeds. The promotion stage checks that the service is still ready for this build and has the same UID/etag, then submits a Cloud Run v2 traffic PATCH with that etag and explicit revision targets. It does not resolve the latest revision or rediscover a shared tag. Other tags are preserved at zero percent and the validated candidate receives 100 percent. The pipeline verifies the resulting traffic, runs the final production-URL smoke and releases its own lease conditionally. It retains `hca-candidate.json`, `hca-promotion.json`, `hca-deployment-lease.json` and `hca-lease-release.json` with the extension build artifacts.

## Overlapping builds

After the immutable image is pushed and validated, the pipeline acquires a lease on the existing Cloud Run Service. A v2 metadata PATCH sets the top-level labels `hca-deployment-build=<BUILD_ID>` and `hca-deployment-until=<UNIX_SECONDS>` with the service's current etag; other labels are preserved and the revision template is omitted. Two builds that read the same unlocked service cannot both acquire it. Acquisition refuses missing services, services without existing production traffic and extra inactive tagged revisions. First-service provisioning requires a separate controlled bootstrap and validation; this upgrade pipeline does not prove zero-traffic first creation.

The lease lasts 45 minutes, exceeding the entire 30-minute Cloud Build timeout. Every migration, deploy and capture stage, plus candidate/live smoke, rechecks ownership, service UID and deadline; promotion checks the same lease and its stricter captured etag. Successful final smoke is followed by an owner/etag-conditional lease release. Builds never automatically clear or steal an existing lease, including an expired lease, or retry a failed CAS with a fresh etag. Keep build and migration-job timeouts below the lease lifetime if changing them.

Build-scoped jobs/tags and a bounded PostgreSQL advisory lock also keep migration definitions and execution separate. A competing deployment, traffic edit, service recreation or changed tag after candidate validation causes promotion to fail closed. These source controls coordinate this pipeline; an operator or another deploy tool can still bypass them. All deployment tooling must respect the lease. Never clear it while its build or migration execution can still be running.

A failed or cancelled build keeps its lease for explicit recovery. Confirm the owner build is terminal and its migration execution/connection is gone; inspect actual traffic, any committed schema upgrade, candidate tags and draining instances. Waiting past the deadline alone does not establish that an external job stopped. Remove unused candidate tags only after confirming they belong to terminal builds. Then fetch a fresh v2 Service, verify UID and both recorded lease labels, and issue `PATCH ...?updateMask=labels` with its exact etag and all other labels preserved, omitting only those two lease keys. A conflict requires renewed operator inspection, not an automatic retry. Record the owner build, operator decision and redacted before/after metadata. The same owner can use the normal `lease-release` helper with its retained lease file while the lease is still valid, after this inspection; expired leases require the explicit conditional metadata recovery described here.

An aborted candidate may remain at zero traffic, so prune it before starting another build. Do not manually promote its old snapshot. Start and validate a new build after inspecting the current service. Operators should prune completed build-scoped jobs and unused candidate tags under a recorded retention policy while preserving recovery evidence. Serving-revision aliases are permitted; any additional inactive tagged revision is refused because tags can retain warm capacity.

## Database connection budget

The source guard checks positive integer settings in the range 1 to 9999 before any cloud mutation:

```
new revision = _MAX_INSTANCES * (_DB_POOL_SIZE + _DB_MAX_OVERFLOW + 1 listener)
total = new revision + _DB_OLD_REVISION_RESERVE + _DB_MIGRATION_RESERVE
total <= _DB_CONNECTION_BUDGET
```

Defaults are max instances 2, pool 2, overflow 1, old/draining reserve 15 and migration reserve 2, within an application budget of 25: `8 + 15 + 2 = 25`. The candidate's resolved environment and revision max-instance setting must match this declaration. The lease permits one new warming candidate at a time; inactive retained tags are refused before acquisition and checked again during capture.

Before the first rollout, derive the application budget from actual Cloud SQL limits after reserving system, operator and other application clients. Set the old/draining reserve to cover **all** existing serving revisions, traffic splits, revision minimum instances, retained serving tags and connections that persist while old revisions drain. Inventory older untagged revisions and any jobs outside this pipeline too. Increase the budget/reserves or reduce pool/instance settings if necessary; an existing `_MAX_INSTANCES=3` override is accepted only with a sufficient declared budget. The source cannot measure actual old-revision connection demand, enforce Cloud SQL's global limit or verify that another deploy path respects the lease. Validate these assumptions and measured load before enabling automatic rollouts.

## Operator prerequisites

Before merging a change that triggers deployment, provision the distinct migration identity described in [migration privileges and recovery](HCA_MIGRATION_RECOVERY.md). `_MIGRATE_SA` defaults to `sa-hca-migrate`; `_MIGRATE_DB_URL_SECRET` defaults to `hca-migration-database-url`. Both must differ from their runtime equivalents. The pipeline refuses the historical `_ALLOW_UNGATED=1` setting. Production requires a Google owner gate, configured Google clients, valid owner configuration and the existing database/admin/sweep/pepper secrets. The running API checks resolved secret lengths and PostgreSQL configuration at startup; `check_config.py` checks secret references without reading their values.

The build identity needs Artifact Registry push access, permission to create/run its migration jobs, deploy/read the service and revisions, update service traffic, read operations, and act as the two intended service accounts. Scope Secret Manager grants separately: the runtime identity must not read the migration database secret. Verify actual grants, service configuration, Cloud SQL connection capacity and backup readiness; checked-in configuration cannot verify those accounts. Provision and validate the first service through a separate controlled workflow before enabling this existing-service upgrade pipeline.

Routine deployment updates required environment values and secret references while preserving other settings, including optional encryption keyrings, current key IDs and previous-pepper mounts. It explicitly removes `HCA_WEBHOOK_ALLOW_INSECURE`; preserved development switches still fail candidate validation. Optional keyring and previous-pepper material must be Secret Manager references, and a keyring/current key ID must be present together. Runtime startup validates their resolved contents.

No encryption-key rotation is performed by this pipeline. For a legacy installation whose optional keyring settings are already unset, leaving them unset and the existing pepper unchanged preserves compatibility for a code-only rollout. Separate cryptographic rotation requires its own validated migration and recovery plan, and narrows the safe rollback targets described below. Do not remove overlap material through deployment cleanup. Retirement is an explicit operator change only after data/credential migration and backup dependencies have been verified; maintain all required keys and peppers across routine upgrades and compatible rollback.

## Failure and rollback

Failures before promotion leave existing traffic unchanged, although successful additive database migrations may already have committed. Migration jobs stop after a bounded wait; the pipeline does not terminate unrelated sessions or automatically downgrade a schema. Failures after promotion, including the final production-URL agent smoke, need operator inspection and may require rollback.

1. Inspect the current serving revision and build labels; compare them with the retained promotion/snapshot metadata. If another build changed the service, coordinate recovery before changing traffic.
2. Select the previously tested revision from the recorded `previous_traffic`. If it was a split, preserve the intended split rather than guessing a single revision.
3. Verify the previous revision remains compatible with the current additive database schema and available secrets. After any `aesgcm2` write or rewrap, a pre-dual-read revision cannot decrypt the new rows. After an API key is automatically upgraded under a new pepper, a revision accepting only the old pepper can reject that key. A safe rollback target must retain dual-read support and all required overlap encryption keys and peppers. Keeping optional keyring settings unset and the pepper unchanged preserves compatibility for a code-only rollout; do not assume that after a rotation.
4. Use an explicit revision traffic assignment, then confirm the serving set and repeat the agent smoke. Run the approved owner-answer/signed-receiver checks when validating production recovery.

An operator command for one previously verified revision is:

```
gcloud run services update-traffic <SERVICE> --region <REGION> --project <PROJECT_ID> --to-revisions <VERIFIED_REVISION>=100
```

This command is intentionally manual and is not executed by source tests. Do not use a moving latest-revision assignment for rollback. Destructive migrations need a staged compatibility plan and a tested restore; reversing a release does not reverse its data changes.

## Evidence and limits

The pre-promotion smoke exercises key issuance, ask creation/read and cancellation on the candidate. Local HTTP smoke and regression tests exercise a signed human answer and signed webhook without operator accounts. They do not replace the owner's recorded manual client validation or establish a production receiver, backup restore, IAM review, load/soak result or database recovery drill. The warm candidate and old/draining revisions consume capacity alongside migration jobs; the declared connection guard must be reconciled with the actual database and revisions as described above.

Cloud Run API behavior is documented in the [Service etag reference](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services), [traffic PATCH reference](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services/patch), [no-traffic deployment reference](https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy) and [job deploy/wait reference](https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/deploy).
