# Client and API remediation: review items 13 to 23

This branch builds on the approval security fixes in [items 1 to 12](SECURITY_REMEDIATION_01_12.md). It addresses API resource bounds, record lifecycle and the supported browser/Mac decision surfaces. CI, container, deployment, dependency locking, governance and project presentation changes are coordinated separately.

| Item | Implementation | Evidence |
| --- | --- | --- |
| 13 | Bound request bodies, fields, collections and nested JSON; share actor/tenant/admin rate counters in the database; cap active asks; paginate inbox, audit, tenant, device, API-key and webhook lists. | API resource and paging tests |
| 14 | Remove expired transient state in bounded batches; offer privileged tenant-scoped record export and completed-record purge; reconcile replay gaps with the current inbox. Keep hold semantics from item 8. | retention, export/purge and replay tests |
| 15 | Validate a single trusted origin, reject credentials/path/query/fragment and redirects, safely encode request IDs. Chrome requires HTTPS; Mac permits loopback HTTP only through explicit development settings. | browser signed-request/origin tests and Mac client security tests |
| 16 | Atomically initialize one durable non-extractable browser signing key in IndexedDB across extension contexts. Migrate the existing key once and clear its plaintext JWK after durable storage. | Node key tests and actual Chromium startup/restart tests |
| 17 | One worker owns a durable, bounded response queue scoped to origin/device; persist one idempotency key per response; preserve ambiguous retries and make rejection/expiry visible. | queue race tests and real popup submission tests |
| 18 | Origin/device changes abort previous connections and clear their cursor, cached questions, focus, notifications and pending responses. Authorization failures remove sensitive stale state. | configuration and stream generation tests |
| 19 | Chrome renders all six API controls. Mac advertises radio and approve/deny, directing other controls to Chrome. | actual Chromium flows for all six controls and Mac capability tests |
| 20 | Safely reject duplicate OAuth parameters and unauthenticated errors; accept fragmented bounded loopback requests; bound connections, timeout and cancellation. | actual loopback TCP and OAuth parser tests |
| 21 | Isolate UI state and use safe transferable stream values; compile Swift with complete concurrency checks and warnings as errors. | strict Swift build and overlap/reconnect tests |
| 22 | Resolve a notification action against a fresh authorized inbox after restart; bind it to the saved origin and shown choices; prune categories and stale notifications. | restart recovery, changed choices and category tests |
| 23 | Show complete material decision context, require explicit selection/review, lock duplicate submissions, label controls and announce errors. Keep notification labels and submitted choice IDs consistent. Hide test commands in release builds. | actual popup flow tests and Mac decision/notification tests |

## API resource and retention defaults

The maximum HTTP request body is 1 MiB. Collection requests permit 500 options/rows and 50 delivery descriptors; answer paths permit 30 segments. Default strings are at most 4,000 characters, with tighter limits on identity/title/labels and other documented fields. Nested dictionaries/lists permit depth 12 and 10,000 visited values per field, with field names at most 100 characters. Non-finite numbers and unknown typed model fields are rejected.

Typed nested records validate their own fields rather than applying one aggregate cap to a complete request and answer. Human answer output permits depth 14 and 10,009 nodes for the server's audit wrapper while preserving the input limits above. Boundary regressions cover successful answers, original metadata retention, identical idempotent replay and valid readback. Response timestamps identify UTC explicitly; input fingerprints and database storage retain their existing representation.

The integrated API contract also covers the static control route from review item 25. CI compares the entire saved contract with the served schema, including constraints, security, request and response bodies, status codes and headers, with no exceptions. Real response validation and deliberate contract mutations verify those checks.

Paginated lists default to 200 records and accept a maximum of 500. Follow `X-HCA-Next-Cursor` using `after`; filtering or visibility can leave an empty page with a next cursor. Cursors are scoped to the authenticated endpoint/tenant and are not authorization proofs. Responses carry `X-Request-ID` for correlation without logging request bodies or credentials.

Database-shared fixed-minute counters default to 1,000 requests per authenticated actor, 3,000 per tenant and 300 for the platform administrator. Actor/tenant limits can be configured with `HCA_ACTOR_RATE_PER_MINUTE` and `HCA_TENANT_RATE_PER_MINUTE`. A tenant can have at most 1,000 active requests by default (`HCA_MAX_ACTIVE_REQUESTS`). Exceeding a rate/quota returns 429 with `Retry-After`. Registration caps are 100 active devices per tenant (HCA_MAX_ACTIVE_DEVICES), 50 active owners (HCA_MAX_ACTIVE_OWNERS) and 1,000 active API keys (HCA_MAX_ACTIVE_API_KEYS). Re-registering an existing device/owner or replacing an existing key does not consume another slot. Each caller may have 20 active webhook destinations; revoked destination history is paginated.

| Records | Default retention |
| --- | --- |
| Inbox events | 7 days, extended to cover a longer configured replay window |
| Completed jobs | 7 days |
| Terminal webhook deliveries and attempts | 30 days; queued deliveries and active jobs stay |
| Webhook registry events | 90 days |
| Rate counters | Expired minute buckets removed in bounded cleanup |
| Nonces | 601 seconds, covering the full signature acceptance window |
| Idempotency receipts | 24 hours by default |
| Decision/audit records and request contents | Retain until an explicit tenant-scoped purge |
| Account/device/key/destination registry and external backups/logs | Operator lifecycle; revocation alone does not erase history |

Cleanup removes at most 500 rows per record type per pass. It runs during periodic worker reconciliation and through the privileged sweep. Expiry reconciliation also examines at most 500 unscheduled candidates per pass, avoiding starvation behind already scheduled records. A backlog may take multiple passes; recovery/load behavior remains an operator validation task.

Export through `GET /v1/tenants/{tenant_id}/records`, following the cursor. To purge completed records created before a timezone-qualified cutoff, use `POST /v1/tenants/{tenant_id}/records/purge` with `confirm_slug`, `before` and a bounded `limit`. The administrator bearer is required, and the confirmation slug must match the tenant. Pending decisions are preserved. Backups/logs must expire separately under the operator's documented policy.

## Supported client behavior

See [Browser client](../extension/README.md) and [Mac client](../macos/README.md) for installation and trust assumptions. The Chrome extension supports radio, checkbox, approve/deny, dropdown, chat and row confirmation. Mac supports radio and approve/deny; the browser is the usable surface for other controls.

Browser responses expire after 24 hours or the request deadline, whichever is earlier. Its queue holds at most 100 responses, 32 KiB per response, 1 MiB total and 20 delivery attempts. Saving a response locally does not mean the server accepted it. Changing origin or rotating the device clears local state; local rotation does not revoke the old server registration. Uninstalling clears the browser's local stores.

The Mac key resides in Keychain. Uninstalling the application does not itself delete that key. Remove the documented Keychain item and remaining system notifications when retiring a device; revoke its server registration separately. Notifications surviving restart are checked against the current authorized inbox before an action is submitted.

## Operator and validation boundaries

Server audit records remain until an explicit authorized purge. Export records before deleting when the operator requires an archive; the repository does not infer a business retention requirement. Purging records skips requests with queued deliveries or active job leases; retry deletion after delivery completes. It removes associated decision/audit/options/delivery/idempotency/event/registry history. Reconcile the receiver before recovery or re-drive. Backup copies and externally retained logs are outside the API deletion transaction. Operators must document their backup/log retention and expiration, and test restore/deletion behavior before claiming complete data erasure.

Automated browser checks use synthetic fixtures and actual Chromium DOM/IndexedDB contexts. Mac socket tests exercise the real local TCP listener without a Google account. These checks do not establish real Google owner OAuth, unpacked Chrome permission/alarm scheduling, native notification/Keychain lifecycle, assistive-technology accessibility or production load/recovery behavior. Those checks require a configured test account and the operator's environment. No production deployment, traffic change or account sign-in is performed by the regression tests.

Before rollout, assess existing records exceeding the new schema bounds or registration quotas. Archive or migrate them under the operator's policy; bounded response validation can refuse oversized legacy records. Do not rotate or discard existing webhook encryption material as part of this client/API change.

## Owner validation approval

The owner reported the requested manual validation finished and approved on October 12, 2026. The [approval record](reviews/2026-10-12_manual_validation_approval.md) distinguishes that confirmation from automated integration results and deployment code requirements. The validated browser/Mac behavior is preserved during integration.
