# Portfolio case study: an auditable human decision boundary

An autonomous workflow often needs one human decision before it can continue: approve a proposed change, choose an option, or confirm a set of rows. HCA provides the request, visible decision context, authorized response and callback contract. The project demonstrates an API/database boundary, two clients, a durable event/job model and an external receiver boundary.

The runnable scope is owner-authorized decisions. Chrome answers all six supported controls. The developer Mac client answers radio and approve/deny and directs other decisions to Chrome. Enterprise connectors, broader organizational roles and public notarized Mac distribution are not part of the demonstration.

## Five-minute reviewer path

1. Follow the [root quickstart](../README.md#run-the-five-minute-demonstration), then run `python scripts/demo_workflow.py`.
2. Inspect its synthetic request, device-signed answer, authenticated callback, mock effect, audit and duplicate retry checks. The program starts its own local API/mock receiver and needs no personal credentials.
3. Open [Architecture](ARCHITECTURE.md) for transaction boundaries and source locations, then inspect the [remediation matrix](SECURITY_REMEDIATION.md) for failure cases and remaining evidence.
4. Read [Chrome](../extension/README.md) and [Mac](../macos/README.md) installation/testing limits before evaluating native-client or production claims.

The demo's scripted human client verifies the API contract. It does not replace interactive browser/Google login or operating-system testing. Actual Chromium tests separately exercise the popup DOM, IndexedDB races and worker restart with synthetic data.

![Synthetic Chrome approval context](../extension/docs/decision-review.png)

This screenshot is produced by the actual popup integration test. It shows the proposed resource, change, impact, exact command, rollback, explicit review acknowledgement and response choices. It contains synthetic data and is not a production deployment screenshot.

## Engineering decisions worth examining

| Decision | Why it was chosen | Cost or limit |
| --- | --- | --- |
| Commit state, audit, event, outbox and retry response together | A crash cannot leave a decision without its durable delivery record or retry result | Larger transactions and careful locking |
| Resource-bound idempotency reservations | Concurrent retries share one operation; a reused key cannot return another ask's answer | Responses expire; downstream effects need their own ledger |
| Current membership plus signed device requests | Owner removal invalidates an existing linked device, and nonces limit replay | Requires clock/credential hygiene and secure client execution |
| Ordered durable events with polling/reset recovery | Reconnect and commit ordering cannot silently skip an authorized pending ask | Serialized Postgres event insertion requires throughput measurement |
| Fenced leased jobs plus authenticated receiver deduplication | Stale workers cannot overwrite newer state; receivers handle repeated deliveries | External delivery remains at least once and can fail dead |
| Non-extractable browser keys and one queue writer | Startup and enqueue/flush races cannot replace identity or lose a newer response | Browser/OS compromise remains trusted-local risk |
| Complete Chrome surface, narrower Mac developer client | The advertised API is answerable without pretending every client has equal controls | Other Mac controls require Chrome; public Mac distribution is additional work |
| Generated OpenAPI and separate integration layers | Contract/source changes are reviewable, with tests at database/network/UI boundaries | Tool success has limited scope; manual and live infrastructure checks remain |

The most useful review files are `api/app/requests_api.py`, `idempotency.py`, `events.py`, `jobs.py`, `auth.py`, `webhooks.py`, `extension/background.js`, `extension/lib/devicekey.js`, and the corresponding API/Chrome/Swift regression tests. See [Contributing](../CONTRIBUTING.md) for reproducible commands rather than relying on a permanent test count.

## Attribution and evidence

This repository contains work from its existing commit history and a security-remediation pass assisted by Codex. The remediation uses targeted failure probes, implementation review, tests and documented operational limits. A maintainer should review and approve the exact changes and release evidence before claiming responsibility for a deployed result. AI assistance is disclosed; no claim is made that all code was authored manually by one person.

Before using this case study in an application, the maintainer should add a short, specific account of their personal design/implementation/review contribution, linked to commits or PRs, and their role in any live deployment. This document deliberately does not invent those contributions, customer results, savings, traffic volume or performance measurements.

Local executable checks and the synthetic screenshot are review evidence. The [dated GitHub settings report](REPOSITORY_SETTINGS_2026-10-12.json) is separate from branch code. Exact candidate CI results, real Google/native-client checks, cloud IAM, rollback/restored backup and load/soak evidence must be linked when available. The [remediation matrix](SECURITY_REMEDIATION.md) names what is implemented and what remains an operator or release requirement.
