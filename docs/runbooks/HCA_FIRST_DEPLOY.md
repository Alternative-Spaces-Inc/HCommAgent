# HCA first deploy runbook (P5b)

Every block below goes into the Mac Terminal application, one block at a time, each waiting for its output before the next. Each block is self-contained: it sets its own variables and never relies on a block run earlier. Replace the two placeholders once, everywhere: `<PROJECT_ID>` (your GCP project id) and `<REGION>` (one Cloud Run region, for example `us-west1`). Every other name is fixed by this runbook and by `cloudbuild.yaml`; change them together or not at all.

What this runbook stands up, matching `docs/plans/HCA_CLOUD_RUN_PLAN_v1.md` section 6:

| Resource | Name | Why |
| :--- | :--- | :--- |
| Artifact Registry repo | `hca-services` | image storage |
| Runtime service account | `sa-hca-api` | identity of the service and the migrate job |
| Cloud SQL instance | `hca-pg` (Postgres 16, Enterprise edition, `db-f1-micro`) | the database; resize later, the tier is not a code fact |
| Database and user | `hca` / `hca` | the app's schema |
| Secrets | `hca-database-url`, `hca-sweep-trigger-token`, `hca-admin-token` | mounted as env vars, never in code |
| Cloud Run job | `hca-migrate` | `alembic upgrade head` before each deploy |
| Cloud Run service | `hca-api-service` | the API, min 1 instance, max 1 (see note) |
| Cloud Scheduler job | `hca-expiry-sweep` | hits the bearer-guarded sweep every 5 minutes |
| Cloud Build connection | `github-hca` | 2nd gen GitHub connection, this repository only |
| Cloud Build trigger | `hca-main-deploy` | runs `cloudbuild.yaml` on every merge to main, P7 values pinned |

Note on max instances: `app/events.py` is an in-process event bus, so the service runs one uvicorn worker and one instance until a shared bus lands (Postgres LISTEN/NOTIFY or Redis, a later packet). The extension's alarm fallback re-reads the inbox, so a push missed for that reason is recovered on the next alarm. Raising `_MAX_INSTANCES` before that packet lands would silently drop SSE events.

Red gates (each is a spend, an IAM grant, a credential or a public endpoint; every one is yours): steps 1, 2, 3, 4, 5, 6, 8, 10, 11, 12.

## Step 0: prerequisites (once)

Google Cloud SDK installed on the Mac and signed in. If `gcloud --version` prints nothing useful, install from the Cloud SDK page first. Then sign in:

```bash
gcloud auth login
```

A browser window opens; choose the Google account that owns the project. Billing must already be enabled on `<PROJECT_ID>` (Console: Billing, left menu, Account management).

## Step 1: project, region, APIs

```bash
gcloud config set project <PROJECT_ID> && gcloud config set run/region <REGION> && gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com cloudscheduler.googleapis.com iam.googleapis.com cloudresourcemanager.googleapis.com
```

Expected: `Operation ... finished successfully.`

## Step 2: Artifact Registry repository

```bash
gcloud artifacts repositories create hca-services --repository-format=docker --location=<REGION> --project=<PROJECT_ID> --description="HCA service images"
```

Expected: `Created repository [hca-services].` (or `ALREADY_EXISTS`, which is fine.)

## Step 3: runtime service account and its four roles

```bash
P=<PROJECT_ID> && SA="sa-hca-api@${P}.iam.gserviceaccount.com" && gcloud iam service-accounts create sa-hca-api --project="$P" --display-name="HCA Cloud Run runtime" && sleep 20 && for R in roles/cloudsql.client roles/secretmanager.secretAccessor roles/logging.logWriter roles/monitoring.metricWriter; do gcloud projects add-iam-policy-binding "$P" --member="serviceAccount:${SA}" --role="$R" --condition=None --quiet >/dev/null && echo "granted $R"; done
```

Expected: four `granted roles/...` lines. The 20 second pause is not optional: IAM takes a few seconds to see a new service account, and without it every grant fails with "service account does not exist" (seen on the first run, 2026-09-30).

## Step 4: Cloud SQL Postgres, database, user, and the DATABASE_URL secret in one go

The password is generated inside the block and written straight into Secret Manager; it is never printed and never pasted anywhere. This block takes several minutes (instance creation; the first instance in a new project has taken over 20). `--edition=enterprise` is required: gcloud now defaults to Enterprise Plus, which rejects the shared-core tier (seen on the first run, 2026-09-30).

```bash
P=<PROJECT_ID> && R=<REGION> && gcloud sql instances create hca-pg --project="$P" --database-version=POSTGRES_16 --edition=enterprise --tier=db-f1-micro --region="$R" --storage-size=10GB --storage-auto-increase --backup-start-time=03:00 --quiet && gcloud sql databases create hca --instance=hca-pg --project="$P" && PW="$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)" && gcloud sql users create hca --instance=hca-pg --project="$P" --password="$PW" && CONN="$(gcloud sql instances describe hca-pg --project="$P" --format='value(connectionName)')" && printf 'postgresql+psycopg2://hca:%s@/hca?host=/cloudsql/%s' "$PW" "$CONN" | gcloud secrets create hca-database-url --project="$P" --replication-policy=automatic --data-file=- && echo "CONNECTION_NAME=$CONN"
```

Expected: the last line `CONNECTION_NAME=<PROJECT_ID>:<REGION>:hca-pg`. Keep that value; steps 8 and 10 use it as `_CLOUDSQL_INSTANCE`.

## Step 5: the two bearer tokens as secrets

Generated inside the block, never printed.

```bash
P=<PROJECT_ID> && openssl rand -hex 32 | tr -d '\n' | gcloud secrets create hca-sweep-trigger-token --project="$P" --replication-policy=automatic --data-file=- && openssl rand -hex 32 | tr -d '\n' | gcloud secrets create hca-admin-token --project="$P" --replication-policy=automatic --data-file=- && gcloud secrets list --project="$P" --filter="name~hca-" --format="value(name)"
```

Expected: three names, `hca-admin-token`, `hca-database-url`, `hca-sweep-trigger-token`.

## Step 6: let Cloud Build deploy

Cloud Build runs as the project's Compute Engine default service account on current projects. It needs to push images, deploy Cloud Run services and jobs, and act as the runtime service account.

```bash
P=<PROJECT_ID> && N="$(gcloud projects describe "$P" --format='value(projectNumber)')" && CB="${N}-compute@developer.gserviceaccount.com" && for R in roles/run.admin roles/artifactregistry.writer roles/logging.logWriter roles/storage.objectViewer; do gcloud projects add-iam-policy-binding "$P" --member="serviceAccount:${CB}" --role="$R" --condition=None --quiet >/dev/null && echo "granted $R"; done && gcloud iam service-accounts add-iam-policy-binding "sa-hca-api@${P}.iam.gserviceaccount.com" --project="$P" --member="serviceAccount:${CB}" --role=roles/iam.serviceAccountUser --quiet >/dev/null && echo "granted actAs on sa-hca-api"
```

Expected: four `granted roles/...` lines plus `granted actAs on sa-hca-api`. If step 8 later fails with a permission error naming `@cloudbuild.gserviceaccount.com`, rerun this block with `CB="${N}@cloudbuild.gserviceaccount.com"` in place of the compute one.

## Step 7: a fresh clone to build from

```bash
cd "$HOME" && rm -rf hca-deploy && git clone --quiet --depth 1 https://github.com/Alternative-Spaces-Inc/human-collections-agent.git hca-deploy && cd hca-deploy && git rev-parse HEAD
```

Expected: one 40-character sha (main's head).

## Step 8: first build and deploy by hand

Runs the whole pipeline: tests, image, push, migrate job, service. Around 8 to 12 minutes. `CONNECTION_NAME` is the value step 4 printed.

```bash
P=<PROJECT_ID> && R=<REGION> && cd "$HOME/hca-deploy" && SHA="$(git rev-parse HEAD)" && CONN="$(gcloud sql instances describe hca-pg --project="$P" --format='value(connectionName)')" && gcloud builds submit --project="$P" --region="$R" --config=cloudbuild.yaml --substitutions="_REGION=${R},_CLOUDSQL_INSTANCE=${CONN},COMMIT_SHA=${SHA}" .
```

Since HCA-P7 the deploy also carries three plain (not secret) values: the
public client id of the Chrome Extension OAuth client, the owner account
list and the link requirement flag. Add them to the substitutions:

```bash
P=<PROJECT_ID> && R=<REGION> && CID=<GOOGLE_CLIENT_ID> && OWNERS=<OWNER_EMAIL> && REQ=0 && cd "$HOME/hca-deploy" && SHA="$(git rev-parse HEAD)" && CONN="$(gcloud sql instances describe hca-pg --project="$P" --format='value(connectionName)')" && gcloud builds submit --project="$P" --region="$R" --config=cloudbuild.yaml --substitutions="_REGION=${R},_CLOUDSQL_INSTANCE=${CONN},COMMIT_SHA=${SHA},_GOOGLE_CLIENT_ID=${CID},_OWNER_EMAILS=${OWNERS},_REQUIRE_GOOGLE_LINK=${REQ}" .
```

`REQ=0` first: deploy, sign in from the extension options page (Sign in
with Google), confirm `GET /v1/devices` shows `google_email` on the
owner's device, then redeploy with `REQ=1` so unlinked devices are
refused on the human routes. Every later deploy must keep `REQ=1`, since the
substitution default is 0 and a rebuild without it turns the gate back off.

Expected: the final status line `STATUS: SUCCESS`. Any `FAILURE` stops here; paste the last 30 lines and I read them.

## Step 9: the service URL and its health

```bash
P=<PROJECT_ID> && R=<REGION> && URL="$(gcloud run services describe hca-api-service --project="$P" --region="$R" --format='value(status.url)')" && echo "SERVICE_URL=$URL" && curl -sS -w '\nHTTP %{http_code}\n' "$URL/health"
```

Expected: `SERVICE_URL=https://...run.app`, then `{"status":"ok","db":"ok"}` and `HTTP 200`. Paste the SERVICE_URL line; I take the verification from there (/health, an unauthenticated 401 on POST /v1/requests, the SSE stream opening). Use /health, not /healthz, from outside: Cloud Run's Google frontend answers exactly /healthz with its own HTML 404 and never forwards it; the in-container probe still uses /healthz.

## Step 10: the expiry sweep on a schedule

Every 5 minutes, bearer read from Secret Manager inside the block, never printed.

```bash
P=<PROJECT_ID> && R=<REGION> && URL="$(gcloud run services describe hca-api-service --project="$P" --region="$R" --format='value(status.url)')" && T="$(gcloud secrets versions access latest --secret=hca-sweep-trigger-token --project="$P")" && gcloud scheduler jobs create http hca-expiry-sweep --project="$P" --location="$R" --schedule="*/5 * * * *" --time-zone="Etc/UTC" --uri="${URL}/v1/internal/expiry-sweep" --http-method=POST --headers="Authorization=Bearer ${T}" --attempt-deadline=60s && gcloud scheduler jobs run hca-expiry-sweep --project="$P" --location="$R" && echo "sweep scheduled and run once"
```

Expected: `sweep scheduled and run once`. Verification: the service's logs show a `POST /v1/internal/expiry-sweep 200` within a minute (I read that once I have read access; until then, the block below prints it).

```bash
P=<PROJECT_ID> && gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="hca-api-service" AND httpRequest.requestUrl:"expiry-sweep"' --project="$P" --limit=3 --format='value(httpRequest.status, timestamp)'
```

Expected: at least one `200` line.

## Step 11: deploy on every merge to main (HCA-P8)

Three parts, Terminal first. Values that are not secrets but are yours (client id, owner account) are pinned on the trigger, not in the repo.

Part 1, the GitHub connection. The command prints one URL; opening it is the only browser step, and it installs the Cloud Build GitHub App on the organization, limited to this repository:

```bash
P=<PROJECT_ID> && R=<REGION> && gcloud builds connections create github github-hca --project="$P" --region="$R"
```

Expected: a line starting `Please authorize the Cloud Build GitHub app by visiting` followed by a URL, then (after the browser step) `Created connection [github-hca]`. In the browser: sign in to GitHub if asked, pick the organization `Alternative-Spaces-Inc`, choose `Only select repositories`, pick `human-collections-agent`, button `Install`. If gcloud asks to grant `roles/secretmanager.admin` to the Cloud Build service agent, answer `Y`; the connection stores its GitHub token in Secret Manager.

Part 2, link the repository:

```bash
P=<PROJECT_ID> && R=<REGION> && gcloud builds repositories create human-collections-agent --project="$P" --region="$R" --connection=github-hca --remote-uri="https://github.com/Alternative-Spaces-Inc/human-collections-agent.git" && gcloud builds repositories list --project="$P" --region="$R" --connection=github-hca --format='value(name)'
```

Expected: one name ending in `human-collections-agent`.

Part 3, the trigger, with the P7 values pinned. The `guard` step in `cloudbuild.yaml` fails any build that arrives without them:

```bash
P=<PROJECT_ID> && R=<REGION> && CID=<GOOGLE_CLIENT_ID> && OWNERS=<OWNER_EMAIL> && CONN="$(gcloud sql instances describe hca-pg --project="$P" --format='value(connectionName)')" && SA="$(gcloud builds describe "$(gcloud builds list --project="$P" --region="$R" --limit=1 --format='value(id)')" --project="$P" --region="$R" --format='value(serviceAccount)')" && gcloud builds triggers create github --project="$P" --region="$R" --name=hca-main-deploy --repository="projects/${P}/locations/${R}/connections/github-hca/repositories/human-collections-agent" --branch-pattern='^main$' --build-config=cloudbuild.yaml --service-account="$SA" --substitutions="_REGION=${R},_CLOUDSQL_INSTANCE=${CONN},_GOOGLE_CLIENT_ID=${CID},_OWNER_EMAILS=${OWNERS},_REQUIRE_GOOGLE_LINK=1" && echo "trigger created"
```

Expected: `trigger created`. The trigger runs as the same service account the manual builds used, so no new IAM grant. From here every squash merge to main runs guard, tests, build, migrate and deploy; the run shows under Cloud Build, History, and `gcloud builds list --project=<PROJECT_ID> --region=<REGION> --limit=3` from Terminal. Step 8's manual block remains the fallback when the trigger is paused; it must pass the same `_GOOGLE_CLIENT_ID` and `_REQUIRE_GOOGLE_LINK=1` or the guard stops it.

First deploy on a fresh project, before any OAuth client exists: pass `_ALLOW_UNGATED=1` once in the step 8 block, then follow the P7 section to link the owner's device, then let the trigger take over.

## Step 11b: let the pipeline read the admin token (HCA-P9)

The `smoke-live` step mints a throwaway agent key against the new revision, so the build service account needs to read one secret. One grant, Terminal:

```bash
P=<PROJECT_ID> && N="$(gcloud projects describe "$P" --format='value(projectNumber)')" && gcloud secrets add-iam-policy-binding hca-admin-token --project="$P" --member="serviceAccount:${N}-compute@developer.gserviceaccount.com" --role=roles/secretmanager.secretAccessor --format='value(etag)' >/dev/null && echo "smoke can read the admin token"
```

Expected: `smoke can read the admin token`. Scope is that single secret; the database URL and sweep token stay unreadable to the build. Each merge now leaves one cancelled request and one agent key named `smoke-<unix time>` in the database; both are inert, and a key clean-up route is a later packet.

## Step 12: the first agent API key

Mints a key for one automation caller through the guarded endpoint. The admin token is read from Secret Manager inside the block; the minted key is printed once, because you are the one who stores it where that agent reads its secrets. Replace `<CALLER>` with the agent id (for example `andy`).

```bash
P=<PROJECT_ID> && R=<REGION> && URL="$(gcloud run services describe hca-api-service --project="$P" --region="$R" --format='value(status.url)')" && T="$(gcloud secrets versions access latest --secret=hca-admin-token --project="$P")" && curl -sS -X POST "${URL}/v1/api-keys" -H "Authorization: Bearer ${T}" -H "Content-Type: application/json" -d '{"caller":"<CALLER>"}'
```

Expected: JSON with `api_key`, `prefix`, `caller`, `created_at`. The `api_key` value is the secret; store it, do not paste it into chat.

## Step 13: point the extension at the service

Chrome path: address bar `chrome://extensions`, top right toggle `Developer mode` on, button `Load unpacked`, pick the `extension` folder inside `$HOME/hca-deploy`. On the HCA card, link `Details`, then `Extension options`. Field `API base URL`: paste the SERVICE_URL from step 9. Button `Save`. The badge shows the pending count once the inbox has an ask.

## Step 11: register your browser device (HCA-P6)

Since P6 the human routes (inbox, stream, answer, audit) accept only requests signed by a registered device key. Once per browser profile.

In Chrome: address bar, `chrome://extensions`, Human Collections Agent card, **Details** button, scroll to **Extension options** row, click it. On the options page, section **This device**, button **Copy public key** (below the Public key box). The key is now on the clipboard.

In Terminal, paste the clipboard into a file and register it. The admin token is read from Secret Manager inside the block and never printed; the label is what audit rows show as answered_by when the answer body names nobody.

```bash
P=<PROJECT_ID> && R=<REGION> && pbpaste > "$HOME/hca-device.pem" && head -1 "$HOME/hca-device.pem" && URL="$(gcloud run services describe hca-api-service --project="$P" --region="$R" --format='value(status.url)')" && T="$(gcloud secrets versions access latest --secret=hca-admin-token --project="$P")" && python3 -c 'import json,sys; print(json.dumps({"label": sys.argv[1], "public_key_pem": open(sys.argv[2]).read()}))' "<LABEL>" "$HOME/hca-device.pem" > "$HOME/hca-device.json" && curl -sS -w '\nHTTP %{http_code}\n' -X POST -H "Authorization: Bearer ${T}" -H "Content-Type: application/json" --data-binary @"$HOME/hca-device.json" "${URL}/v1/devices" ; unset T
```

Expected: first line `-----BEGIN PUBLIC KEY-----`, then a JSON body with `device_id` matching the id shown on the options page, then `HTTP 201`. `HTTP 422` means the clipboard did not hold the PEM (copy again). To revoke a device later: same block shape, `POST ${URL}/v1/devices/<DEVICE_ID>/revoke` with the same bearer.

## Rollback

Cloud Run keeps every revision. To route traffic back to the previous one:

```bash
P=<PROJECT_ID> && R=<REGION> && PREV="$(gcloud run revisions list --service=hca-api-service --project="$P" --region="$R" --format='value(metadata.name)' --sort-by='~metadata.creationTimestamp' | sed -n 2p)" && gcloud run services update-traffic hca-api-service --project="$P" --region="$R" --to-revisions="${PREV}=100" && echo "traffic on $PREV"
```

Migrations are forward-only by policy; a revision rollback never runs `alembic downgrade`.

## Teardown (only if you decide to stop)

Deletes everything this runbook created, in dependency order. Irreversible.

```bash
P=<PROJECT_ID> && R=<REGION> && gcloud scheduler jobs delete hca-expiry-sweep --project="$P" --location="$R" --quiet ; gcloud builds triggers delete hca-main-deploy --project="$P" --region="$R" --quiet ; gcloud builds repositories delete human-collections-agent --project="$P" --region="$R" --connection=github-hca --quiet ; gcloud builds connections delete github-hca --project="$P" --region="$R" --quiet ; gcloud run services delete hca-api-service --project="$P" --region="$R" --quiet ; gcloud run jobs delete hca-migrate --project="$P" --region="$R" --quiet ; gcloud sql instances delete hca-pg --project="$P" --quiet ; for S in hca-database-url hca-sweep-trigger-token hca-admin-token; do gcloud secrets delete "$S" --project="$P" --quiet; done ; gcloud artifacts repositories delete hca-services --project="$P" --location="$R" --quiet ; gcloud iam service-accounts delete "sa-hca-api@${P}.iam.gserviceaccount.com" --project="$P" --quiet
```
