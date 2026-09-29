# Human Collections Agent (HCA): Architecture, Cloud Run Hosting, and Execution Plan

## 1. Executive Summary and Context Assumptions

This document establishes the architecture, deployment design, and 10-working-day roadmap for the Human Collections Agent (HCA) in repo Alternative-Spaces-Inc/human-collections-agent (PR 1 open: charter and OpenAPI skeleton). Acting for Andy, factory administrator of the Migration Factory (GitHub org Alternative-Spaces-Inc), this plan enables a fresh engineering agent to execute a rapid build, test, and enhance loop.

### Purpose and Core Paradigm
HCA is a universal human interface for background automations operating in the factory GCP project. When an automated workflow requires human input or approval, it submits an asynchronous ask to HCA. HCA routes the ask to an active human surface, starting with a Manifest V3 Chrome browser extension. The user selects an answer, and HCA signs and returns the decision so the automation can resume.

### Owner Principles for Interaction
1. **Selection First UI**: Easy UI choices (Approve/Deny, option cards, radio/checkbox lists). Open text input is forbidden where a discrete selection can be provided.
2. **Contextual Recommendations**: Every ask carries a suggested option, the rationale why, and the value/trade-off of each alternative.
3. **Atomic Decisions**: Exactly one decision widget per ask item.
4. **Immutable Auditability**: Every ask and answer generates a tamper-evident audit row recording who asked, which surface answered, exact timestamp, and cryptographic proof of the responding device.

### System Source of Truth Assumptions
Repository content is the source of truth for existing systems, and reconciliation was completed against base commit 24c7ec2 on branch andy/charter-v1.


## Reconciliation against PR 1 (base commit 24c7ec2)

The draft plan was reconciled against repo canon (docs/HCA_CHARTER_v1.md and api/openapi.yaml) at base commit 24c7ec2 on branch `andy/charter-v1`.

### Reconciliation Summary
- Total items reconciled: 14
- RECONCILED (identical to repo): 4
- CHANGED (repo uses different name or path): 6
- PROPOSED (absent from repo openapi.yaml): 4

| Item | Draft Says | Repo Says | Action |
| --- | --- | --- | --- |
| `/v1/requests` | `/v1/requests` | `/v1/requests` | CHANGED |
| `/v1/requests/{request_id}` | `/v1/requests/{request_id}` | `/v1/requests/{request_id}` | CHANGED |
| `/v1/requests/{request_id}/answer` | `/v1/requests/{request_id}/answer` | `/v1/requests/{request_id}/answer` | CHANGED |
| `/v1/requests/{request_id}/cancel` | `/v1/requests/{request_id}/cancel` | Absent | DECIDED 2026-09-29 |
| `/v1/inbox` | `/v1/inbox` | Absent | DECIDED 2026-09-29 |
| `/v1/inbox/stream` | `/v1/inbox/stream` | Absent | DECIDED 2026-09-29 |
| `/v1/audit` | `/v1/audit` | Absent | DECIDED 2026-09-29 |
| `/v1/controls` | Absent | `/v1/controls` | RECONCILED |
| `CollectionRequest` | Ask creation payload | `CollectionRequest` | CHANGED |
| `Option` | Option object | `Option` | RECONCILED |
| `RequestReceipt` | Ask receipt object | `RequestReceipt` | CHANGED |
| `AnswerRecord` | Answer object / audit row | `AnswerRecord` | CHANGED |
| `PathHint` | Path hint payload | `PathHint` | RECONCILED |
| `ControlSpec` | Control spec definition | `ControlSpec` | RECONCILED |


---

## 2. System Architecture and Component Design

### System Components

1. **HCA API Service (Cloud Run)**:
   - Built with Python 3.12 and FastAPI.
   - Containerized service deployed to Google Cloud Run in the factory GCP project in the factory region.
   - Exposes REST endpoints for creating asks, retrieving status, and querying audit logs.
   - Provides long-lived Server-Sent Events (SSE) channels for real-time push delivery to human surfaces.
   - Dispatches webhook notifications when asks are answered, expired, or cancelled.

2. **HCA Datastore (Cloud SQL PostgreSQL)**:
   - Managed PostgreSQL database in the factory GCP project.
   - Stores tenant integrations, API key hashes, human user profiles, device public keys, active asks, and audit logs.
   - Connected via Cloud SQL Auth Proxy using IAM database authentication.

3. **Chrome Browser Extension (Client Surface)**:
   - **Manifest V3 Architecture**: Content-script-free design with zero DOM injection on web pages.
   - **Background Service Worker**: Maintains SSE stream connections, manages local storage, updates badge counts, handles background sync alarms, and fires desktop notifications.
   - **Popup Interface**: Displays the active ask card, recommendation highlight, alternative trade-off details, and single-click decision buttons.
   - **Options Page**: Authenticates users via Google OAuth2 (Chrome Identity API), manages automation scopes, and registers per-device cryptographic key pairs.

### Text Sequence Diagram: Ask, Deliver, Answer, Resume, and Escalation

```
+------------+       +-------------+       +--------------+       +------------------+
| Automation |       | HCA API     |       | Chrome Ext   |       | Secondary Surface|
| Workflows  |       | (Cloud Run) |       | (ServiceWkr) |       | (Slack/SMS)      |
+------------+       +-------------+       +--------------+       +------------------+
      |                     |                     |                        |
      | 1. POST /v1/requests    |                     |                        |
      |-------------------->|                     |                        |
      |                     | 2. Persist Ask      |                        |
      |                     |    & Audit Row      |                        |
      |                     |------------------+  |                        |
      |                     |                  |  |                        |
      |                     |<-----------------+  |                        |
      | 3. 201 Created      |                     |                        |
      |<--------------------|                     |                        |
      |                     | 4. SSE Push Event   |                        |
      |                     |-------------------->|                        |
      |                     |                     | 5. Update Badge &      |
      |                     |                     |    Show Notification   |
      |                     |                     |---------------------+  |
      |                     |                     |                     |  |
      |                     |<--------------------+  |
      |                     |                     |                        |
      |                     |                     | 6. User Clicks Option  |
      |                     |                     |    (Sign with Device)  |
      |                     |                     |---------------------+  |
      |                     |                     |                     |  |
      |                     |                     |<--------------------+  |
      |                     | 7. POST /asks/ans   |                        |
      |                     |<--------------------|                        |
      |                     | 8. Verify Signature |                        |
      |                     |    & Record Audit   |                        |
      |                     |------------------+  |                        |
      |                     |                  |  |                        |
      |                     |<-----------------+  |                        |
      | 9. Webhook Callback |                     |                        |
      |<--------------------|                     |                        |
      |                     |                     |                        |
      |=== ALTERNATIVE FLOW: TIMEOUT AND SECONDARY ESCALATION =============|
      |                     |                     |                        |
      | (Timer Expires)     |                     |                        |
      |------------------+  |                     |                        |
      |                  |  |                     |                        |
      |<-----------------+  |                     |                        |
      |                     | 10. Escalate Ask    |                        |
      |                     |--------------------------------------------->|
      |                     |                     |                        |
      |=== ALTERNATIVE FLOW: AUTOMATION CANCELLATION ======================|
      |                     |                     |                        |
      | 11. POST /cancel    |                     |                        |
      |-------------------->|                     |                        |
      |                     | 12. SSE Cancel Msg  |                        |
      |                     |-------------------->|                        |
      |                     |                     | 13. Dismiss UI         |
      |                     |                     |---------------------+  |
      |                     |                     |                     |  |
      |                     |                     |<--------------------+  |
```

---

## 3. API Contract Sketch

The API contract enforces strict typing, explicit recommendation contexts, and structured options across all v1 endpoints.

### Endpoint Specifications

| HTTP Method | Path | Summary | Key Request Fields / Parameters | Primary Response Fields |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/v1/requests` | Create new ask | `kind` (enum: approve, choose_one, choose_many, value, acknowledge), `title` (str), `context` (str), `options` (array of Option objects), `default_option_id` (str), `timeout_seconds` (int), `escalation` (object), `callback_url` (str) | `id` (uuid), `status` (pending), `created_at` (iso8601), `expires_at` (iso8601) |
| `GET` | `/v1/requests/{request_id}` | Fetch ask state | Path param: `id` (uuid) | Full ask payload, `status` (pending, answered, expired, cancelled), `answer` (object if answered) |
| `POST` | `/v1/requests/{request_id}/answer` | Submit human decision | Path param: `id` (uuid); Body: `selected_option_ids` (array), `custom_value` (str), `device_id` (str), `signature` (base64 string) | `status` (accepted), `audit_id` (uuid), `recorded_at` (iso8601) |
| `POST` | `/v1/requests/{request_id}/cancel` | Cancel active ask | Path param: `id` (uuid); Body: `reason` (str) | `id` (uuid), `status` (cancelled), `cancelled_at` (iso8601) |
| `GET` | `/v1/inbox` | List pending asks | Query params: `status` (default: pending), `limit` (int), `offset` (int) | `items` (array of ask objects), `total` (int) |
| `GET` | `/v1/inbox/stream` | Real-time SSE channel | Headers: `Authorization: Bearer <user_token>`, `Last-Event-ID` | Text/event-stream pushing `ask.created`, `ask.cancelled`, `ask.expired` |
| `GET` | `/v1/audit` | Query audit trail | Query params: `integration_id`, `user_id`, `from_date`, `to_date`, `limit` | `audit_rows` (array of audit records with cryptographic proofs) |

### Option Schema Details
- `id` (string, required): Unique identifier for the option within the ask (e.g. "opt_approve").
- `label` (string, required): Text shown on button or card (e.g. "Approve Deployment").
- `description` (string, optional): Detailed explanation of outcome.
- `recommended` (boolean, required): Flag indicating system-suggested option.
- `recommended_why` (string, required if `recommended` is true): Rationale for recommendation.
- `alternative_value` (string, required if `recommended` is false): Trade-off or value of choosing this alternative.

---

## 4. Architectural Choices and Tech Stack Justifications

### Runtime Selection
- **Choice**: Python 3.12 with FastAPI.
- **Justification**: FastAPI preferred for parity with the factory's other services. Python 3.12 provides high performance async execution and native Pydantic schema validation.

### Datastore Selection
- **Choice**: Cloud SQL PostgreSQL.
- **Justification**: Durability of audit rows and simple relational queries favor Postgres over document stores. Higher baseline cost is justified by strict transactional integrity.

### Real-Time Delivery Selection
- **Choice**: Server-Sent Events (SSE) with HTTP polling fallback first, WebSocket later.
- **Justification**: SSE works smoothly over HTTP/2 on Cloud Run without WebSocket connection upgrades, reducing service worker overhead in Chrome MV3.

### Auth and Cryptography Model
- **Automations**: Use per-integration API keys formatted as `hca_live_<prefix>_<secret_hash>`. SHA256 hashed at rest with visible prefix.
- **Humans**: Authenticate via Google OAuth2 (`chrome.identity` API). Scope checks restrict which automations can route asks to a human.
- **Proof of Surface**: Answers are signed locally using a per-device ECDSA (P-256) key generated in extension storage. The public key is registered on the API service, allowing audit rows to prove exact surface origin.
- **Secret Management**: All secrets reside in Secret Manager and are mounted to Cloud Run as secret environment variable references. No plaintext secrets exist in code or configuration.

---

## 5. Chrome Extension Design (Manifest V3)

### Manifest Structure (`manifest.json`)
Uses Manifest V3 with minimal permissions:
- `permissions`: `["identity", "storage", "notifications", "alarms"]`.
- `host_permissions`: None required for user browsing. API origin access restricted to HCA domain.
- `background`: `{"service_worker": "background.js", "type": "module"}`.
- `action`: `{"default_popup": "popup.html", "default_icon": "assets/icon_128.png"}`.

### Core Workflows
1. **Background Worker (`background.js`)**: Listens to `/v1/inbox/stream`, updates badge counter, manages offline storage, and triggers system notifications.
2. **Popup UI (`popup.html` / `popup.js`)**: Renders active ask cards with option buttons, recommended highlights, and alternative trade-off details.
3. **Notification Action**: Clicking notifications focuses popup directly on the specific ask ID.
4. **Offline Queueing**: Offline answers are signed, queued in `chrome.storage.local`, and auto-flushed upon network recovery.

---

## 6. Google Cloud Run Hosting and Infrastructure Plan

### Dockerfile Outline

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runner
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PATH="/root/.local/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl
COPY --from=builder /root/.local /root/.local
COPY . .
EXPOSE 8080
USER nobody
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
```

### Infrastructure and Deployment Settings
- **Artifact Registry**: Repository `hca-services` in the factory region.
- **Cloud Build Trigger**: Triggered on GitHub `main` branch PR merges only.
- **Service Configuration**: Name `hca-api-service`, region set to the factory region, min instances set to 1 (maintains active SSE handlers and eliminates cold-start delay), max instances 10, concurrency 80, CPU always allocated enabled (required for SSE), request timeout 3600 seconds.
- **Cloud SQL Connector**: Connected via Unix socket `/cloudsql/<PROJECT_ID>:<REGION>:<INSTANCE_NAME>`.
- **IAM Account & Roles**: Account `sa-hca-api@<PROJECT_ID>.iam.gserviceaccount.com` granted `roles/cloudsql.client`, `roles/secretmanager.secretAccessor`, `roles/logging.logWriter`, and `roles/monitoring.metricWriter`.
- **Custom Domain & Armor**: Custom domain mapping with managed TLS certificate; optional Cloud Armor security policy for IP rate limiting.

### Deployment Commands
*Note: The owner substitutes all ALL_CAPS token placeholders in angle brackets before execution.*

```bash
gcloud config set project <PROJECT_ID>
gcloud config set run/region <REGION>

gcloud artifacts repositories create hca-services     --repository-format=docker     --location=<REGION>     --description="Docker repository for HCA services"

gcloud iam service-accounts create sa-hca-api     --display-name="HCA Cloud Run Runtime SA"

gcloud projects add-iam-policy-binding <PROJECT_ID>     --member="serviceAccount:sa-hca-api@<PROJECT_ID>.iam.gserviceaccount.com"     --role="roles/cloudsql.client"

gcloud projects add-iam-policy-binding <PROJECT_ID>     --member="serviceAccount:sa-hca-api@<PROJECT_ID>.iam.gserviceaccount.com"     --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding <PROJECT_ID>     --member="serviceAccount:sa-hca-api@<PROJECT_ID>.iam.gserviceaccount.com"     --role="roles/logging.logWriter"

gcloud projects add-iam-policy-binding <PROJECT_ID>     --member="serviceAccount:sa-hca-api@<PROJECT_ID>.iam.gserviceaccount.com"     --role="roles/monitoring.metricWriter"

gcloud builds submit --tag <REGION>-docker.pkg.dev/<PROJECT_ID>/hca-services/hca-api:v1.0.0 .

gcloud run deploy hca-api-service     --image=<REGION>-docker.pkg.dev/<PROJECT_ID>/hca-services/hca-api:v1.0.0     --platform=managed     --region=<REGION>     --service-account=sa-hca-api@<PROJECT_ID>.iam.gserviceaccount.com     --min-instances=1     --max-instances=10     --concurrency=80     --no-cpu-throttling     --timeout=3600     --add-cloudsql-instances=<PROJECT_ID>:<REGION>:<DATABASE_INSTANCE_NAME>     --set-secrets=DATABASE_URL=<SECRET_DB_URL>:latest,GOOGLE_OAUTH_CLIENT_SECRET=<SECRET_OAUTH_CLIENT>:latest     --allow-unauthenticated
```

---

## 7. Threat Model and Security Mitigations

| Threat Vector | Severity | Vulnerability Description | Mitigation Strategy |
| :--- | :--- | :--- | :--- |
| **Forged Answers** | Critical | Attacker submits fake answer impersonating a human user. | Verify per-device ECDSA signatures on backend before accepting answers into database. |
| **Replayed Asks** | High | Attacker captures valid answer traffic and replays payload. | Validate unique request nonces, server timestamps, and enforce single-use ask state transitions. |
| **Prompt Injection** | High | Automation puts malicious script or text into ask context. | Strict UI widget constraints; sanitize all plain text; ban HTML rendering in popup interface. |
| **Extension Impersonation** | Medium | Rogue extension attempts to connect to HCA API. | Validate Google OAuth ID tokens and check Chrome Extension ID in token claims and headers. |
| **API Key Leakage** | Medium | Automation API keys exposed in logs or source code. | Store SHA256 hashed keys at rest; validate using constant-time checks; mount runtime secrets via Secret Manager. |
| **Unanswered Red-Gate Expiry** | High | Red-gate request remains unanswered at deadline_at. | Automatic deny: system generates an explicit Deny answer record to caller at deadline_at (expiry_policy default auto_deny). |

---

## 8. Observability and Monitoring

### Structured JSON Logging
All logs emitted to Cloud Logging follow structured JSON format:

```json
{
  "timestamp": "2026-09-29T11:14:00Z",
  "severity": "INFO",
  "trace_id": "projects/<PROJECT_ID>/traces/a1b2c3d4e5f6",
  "component": "hca-api",
  "event": "ask.answered",
  "ask_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "integration_id": "int_migration_factory",
  "user_id": "usr_andy_admin",
  "surface_type": "chrome_extension",
  "device_id": "dev_macbook_pro_01",
  "execution_time_ms": 42.5
}
```

### Dashboard Specification
Single Cloud Monitoring dashboard tracking:
1. **Ask Volume & Status**: Line chart tracking Created, Answered, Expired, and Cancelled asks per minute.
2. **Endpoint Latencies**: P50, P95, and P99 response times for `/v1/requests` and `/v1/requests/{request_id}/answer`.
3. **Active Real-Time Streams**: Gauge showing active SSE client connection counts.
4. **Signature Failures**: Counter tracking rejected answers due to cryptographic signature mismatch.

### Alert Rules
1. **High Ask Timeout Rate**: Triggered when expired asks exceed 10% of total created asks over a rolling 15 minute window.
2. **SSE Disconnection Spike**: Triggered when active streaming connections drop over 20% in 5 minutes.
3. **API System Errors**: Triggered when HTTP 5xx responses exceed 2% of overall requests over 5 minutes.

---

## 9. Automated Testing Strategy

### 1. Unit Testing (Pytest)
Tests schema validation, option recommended constraints, ECDSA signature helpers, and ask state machine transitions.

### 2. Contract Testing (Schemathesis)
Runs property-based contract tests against OpenAPI v3 specifications to enforce strict status code and payload schema compliance.

### 3. Extension E2E Testing (Playwright)
Launches Chromium with unpacked extension loaded, simulates OAuth authentication, receives mock SSE ask, clicks UI decision card, and asserts signed answer delivery.

### 4. Golden Path Smoke Test Script (< 5 Minutes)

```bash
#!/usr/bin/env bash
set -euo pipefail

API_URL="${1:-http://localhost:8080}"
echo "=== Starting HCA Golden Path Smoke Test against ${API_URL} ==="

echo "[1/4] Testing Health Check..."
curl -sSf "${API_URL}/healthz" | grep -q "ok"

echo "[2/4] Submitting Test Ask..."
ASK_RESPONSE=$(curl -sSf -X POST "${API_URL}/v1/requests"   -H "Content-Type: application/json"   -H "X-HCA-API-Key: hca_live_test_prefix_secret"   -d '{
    "kind": "approve",
    "title": "Smoke Test Ask",
    "context": "Verifying basic pipeline integrity",
    "options": [
      {
        "id": "opt_yes",
        "label": "Approve",
        "recommended": true,
        "recommended_why": "Initial smoke setup verification"
      },
      {
        "id": "opt_no",
        "label": "Deny",
        "recommended": false,
        "alternative_value": "Cancels verification run"
      }
    ]
  }')

ASK_ID=$(echo "${ASK_RESPONSE}" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "Ask created with ID: ${ASK_ID}"

echo "[3/4] Submitting Signed Answer..."
curl -sSf -X POST "${API_URL}/v1/requests/${ASK_ID}/answer"   -H "Content-Type: application/json"   -H "Authorization: Bearer mock_test_jwt"   -d '{
    "selected_option_ids": ["opt_yes"],
    "device_id": "smoke_device_01",
    "signature": "mock_signature_data"
  }'

echo "[4/4] Verifying Audit Entry..."
curl -sSf "${API_URL}/v1/audit?ask_id=${ASK_ID}" | grep -q "${ASK_ID}"

echo "=== Golden Path Smoke Test Passed Successfully ==="
```

---

## 10. Ten-Working-Day Implementation Sequence

| Working Day | Core Deliverable | Acceptance Test |
| :--- | :--- | :--- |
| **Day 1** | Walking Skeleton on Cloud Run with FastAPI health check and Cloud SQL Postgres connection. | `curl -f https://<SERVICE_URL>/healthz` returns status 200 OK with database ping response under 10ms. |
| **Day 2** | Database schema migrations and ORM models for Asks, Options, API Keys, and Audit Logs. | Pytest passes full CRUD and constraint validation suite against test database. |
| **Day 3** | Asks API endpoints (`POST /v1/requests`, `GET /v1/requests/{request_id}`, `POST /v1/requests/{request_id}/answer`) and Webhook worker. | Automated test creates ask, posts answer, and receives webhook callback payload at receiver. |
| **Day 4** | Surface Auth & Cryptography (Google OAuth token validation and ECDSA signature middleware). | Unsigned answer posts return HTTP 401/422; valid ECDSA signed answers return HTTP 200. |
| **Day 5** | Real-Time SSE Engine (`GET /v1/inbox/stream`) with heartbeat management and reconnection logic. | EventSource client connects, receives `ask.created` within 100ms of POST, and reconnects on disconnect. |
| **Day 6** | Chrome Extension MV3 skeleton, Service Worker SSE listener, badge counter, and storage manager. | Unpacked extension in Chrome connects background SSE and updates badge count when ask arrives. |
| **Day 7** | Chrome Extension Popup UI rendering single decision cards, recommendation highlights, and alternative trade-offs. | UI test confirms popup correctly renders single decision choices and recommendation labels. |
| **Day 8** | Chrome Notifications, direct popup targeting on click, offline queueing, and auto-sync worker. | Disconnecting network, answering ask, and reconnecting automatically flushes queued answer to API. |
| **Day 9** | Infrastructure Hardening (Cloud Build PR merge triggers, Secret Manager refs, and IAM roles). | PR merge to main branch automatically triggers Cloud Build, runs tests, and deploys new service revision. |
| **Day 10** | End to End Test Suite & Golden Path Validation (Schemathesis, Playwright e2e, and smoke script). | Golden path smoke script completes under 300 seconds; Schemathesis reports 0 contract violations. |

---

## 11. Future Enhancement Backlog

1. **macOS notification client**: Client surface for macOS desktop notifications as step 2 in the surface ladder, receiving `/v1/inbox/stream` events and triggering native OS notifications.
2. **macOS app**: Native Swift/SwiftUI application in macOS menu bar listening to `/v1/inbox/stream` and displaying native notifications and decision widgets.
2. **Slack Surface Integration**: Slack Bot mapping asks to interactive Block Kit message cards with button actions translating to signed answers.
3. **iMessage Surface Bridge**: Apple Messages integration pushing concise decision prompts to registered mobile devices for high priority approvals.
4. **Model Context Protocol (MCP) Server**: MCP server exposing HCA capabilities directly as tools (`hca_create_ask`, `hca_check_status`) for LLM agents (e.g. Claude Desktop).

---

## 12. Open Decisions for Owner Selection

### Decision 1: Extension SSE Stream Reconnection Strategy
- **Recommended Option**: Exponential backoff with jitter (initial 1s, max 60s) + periodic alarm ping every 5 minutes.
- **Why Recommended**: Minimizes battery and CPU overhead on laptops while guaranteeing fast reconnection after network drops.
- **Alternative (Fixed Interval Polling Fallback)**: Simple implementation, but wastes battery and network bandwidth when idle.

### Decision 2: Database Connection Pooling on Cloud Run
- **Recommended Option**: Cloud SQL Python Connector with Asyncpg connection pool (min 2, max 10 connections).
- **Why Recommended**: Integrates natively with FastAPI async loops, manages IAM auth, and prevents connection exhaustion.
- **Alternative (PgBouncer Sidecar deployment)**: Increases operational complexity and container memory footprint without performance benefits at initial scale.

### Decision 3: Secondary Escalation Channel Default
- **Recommended Option**: Slack webhook notification as default secondary channel.
- **Why Recommended**: Provides rich formatting, action buttons, and high visibility across engineering teams.
- **Alternative (Email via SendGrid)**: Higher latency and lower response rate compared to instant messaging surfaces.

### Decision 4: Audit Log Retention Policy
- **Recommended Option**: Hot PostgreSQL storage for 90 days, followed by automated export to Cloud Storage Coldline in Parquet format.
- **Why Recommended**: Maintains fast database queries and keeps storage costs low while ensuring permanent compliance storage.
- **Alternative (Indefinite PostgreSQL retention)**: Simple query setup, but continuously inflates database disk usage and backup costs over time.

### Decision 5: SSE Stream Heartbeat Interval
- **Recommended Option**: 15-second ping interval.
- **Why Recommended**: Keeps Cloud Run HTTP/2 streaming connections active through GCP load balancers without triggering idle timeout limits.
- **Alternative (60-second ping interval)**: Risks load balancer or proxy connection drops due to silent timeout thresholds.
