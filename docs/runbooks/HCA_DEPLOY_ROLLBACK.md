# Deploy rollback and traffic recovery

How a deploy reaches production and how to put traffic back on a known good
revision. Every command runs in Terminal on the operator's machine with
`gcloud` signed in to the dedicated project; `<PROJECT_ID>` and `<REGION>`
are the runbook values from the first deploy runbook, the service is
`hca-api-service`.

## How a build promotes

`cloudbuild.yaml` deploys every merge as a candidate revision with
`--no-traffic --tag=candidate`. Production traffic stays on the previous
revision while three steps run against the candidate:

1. `check-config`: `scripts/check_config.py` reads the candidate revision and
   fails closed when a required secret (`DATABASE_URL`, `HCA_ADMIN_TOKEN`,
   `HCA_API_KEY_PEPPER`, `SWEEP_TRIGGER_TOKEN`) is not mounted from Secret
   Manager, when `HCA_GOOGLE_CLIENT_IDS`, `HCA_OWNER_EMAILS` or
   `HCA_REQUIRE_GOOGLE_LINK` is missing or empty, when
   `HCA_REQUIRE_GOOGLE_LINK` is not `1`, when a local only switch
   (`HCA_WEBHOOK_ALLOW_INSECURE`, or any name containing `LOCAL` or `DEV`) is
   present, or when the `commit` label is not the build's commit.
2. `smoke-candidate`: `scripts/smoke_golden_path.py` against the candidate's
   tag URL (`https://candidate---<service hash>.<region>.run.app`).
3. `promote`: `gcloud run services update-traffic --to-latest`, then
   `smoke-live` repeats the smoke on the production URL.

A failure in step 1 or 2 ends the build with production untouched; the
candidate revision stays deployed at 0 percent for inspection.

## Read the current state

```
gcloud run services describe hca-api-service --region <REGION> --project <PROJECT_ID> --format='value(status.traffic[].revisionName,status.traffic[].percent,status.traffic[].tag)'
```

Expected: one line per traffic target; the serving revision shows `100`, the
candidate shows `0` with tag `candidate` until the next build moves the tag.

## Roll back to the previous revision

List revisions newest first:

```
gcloud run revisions list --service hca-api-service --region <REGION> --project <PROJECT_ID> --format='table(metadata.name,metadata.creationTimestamp,metadata.labels.commit)' --limit 5
```

Move all traffic to the known good revision name from that list:

```
gcloud run services update-traffic hca-api-service --region <REGION> --project <PROJECT_ID> --to-revisions <REVISION_NAME>=100
```

Then confirm with the read command above and run the smoke against the
production URL:

```
python3 scripts/smoke_golden_path.py --base "$(gcloud run services describe hca-api-service --region <REGION> --project <PROJECT_ID> --format='value(status.url)')"
```

## Database compatibility

Migrations run before the candidate exists and are additive by policy (new
tables and nullable columns, no drops in the same release as the code that
stops using a column). A rolled back revision therefore runs against the
newer schema. Before merging a migration that removes or renames anything,
the previous revision must already be free of that column; the plan for the
change says so explicitly.

## Known limits

- Two builds started close together are not serialized by Cloud Build; the
  second one's `promote` wins. Merge one PR at a time and wait for the build.
- The candidate keeps `--min-instances=1`, so one idle instance runs between
  deploy and promote; this is the price of a warm smoke.
- The live smoke exercises the agent path (key, ask, read, cancel). A signed
  human answer and a signed webhook delivery are proven by `smoke-local` in
  every build and by the test suite, not against the live revision (P25
  adds a pipeline owned receiver).
