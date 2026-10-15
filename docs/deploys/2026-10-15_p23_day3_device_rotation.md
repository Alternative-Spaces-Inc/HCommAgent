# P23 Day 3: live browser device rotation evidence (2026-10-15)

## Implementation receipt

[PR 28](https://github.com/Alternative-Spaces-Inc/HCommAgent/pull/28) merged as `2a6db24987e437bc485a05aefefc2e705f81abd9`. Its final head was `3582747b260a55883d7a95f53bc7306e861bccfd`. All 20 check runs on that head concluded success, including the SQLite/HTTP, Postgres and extension jobs. These are CI results, not proof of a production database row or elapsed key expiry.

Design and acceptance conditions: [P23 plan](../plans/HCA_P23_EXTENSION_HARDENING_v1.md). Runtime contract: [device routes](../../api/app/devices_api.py). Automated coverage: [rotation tests](../../api/tests/test_device_rotation.py).

## Live request evidence

The operator's read-only Cloud Run request-log queries returned these records. Times are UTC on 2026-10-15. No credential, account identifier, service hostname or infrastructure project identifier is reproduced here.

| Time | Method and path | HTTP result |
|---|---|---|
| 22:42:24.744528 | POST /v1/devices | 201 |
| 22:56:01.733706 | POST /v1/devices/self/google-link | 200 |
| 23:01:58.816767 | POST /v1/devices/self/rotate | 200 |
| 23:01:59.016762 | GET /v1/devices/self | 200 |
| 23:01:59.096182 | GET /v1/devices/self | 200 |

Registration was required because the owner removed and reinstalled the unpacked extension. It was not an extra registration step after rotation. The rotation request succeeded, followed by two successful device-authenticated self reads. HTTP logs alone do not expose the old and successor device identifiers or the database audit payload.

## Human observation

After the rotation and an explicit refresh of the options page, the owner selected **Both checks passed** at approximately 23:06 UTC (16:06 Pacific). That selection confirmed exactly these observations:

- Under **This device**, the page displayed **Server state: active**.
- Under **Google account**, the page still displayed **Linked to** the owner's account.

This records signed-in human confirmation of the displayed active state and retained Google link. It does not claim that the owner compared old and new device identifiers, that a request was answered after rotation, or that 24 hours elapsed.

## Unpacked installation issue encountered

Before linking, Google rejected the OAuth client configured in the local manifest. The checked-in client belongs to the store extension ID, while the manifest key identifies the unpacked extension. The mapping is documented in the [store setup runbook](../runbooks/HCA_CHROME_WEB_STORE.md).

The local unpacked manifest was changed to the existing client associated with its own extension ID. The existing deployment configuration already listed that client. After reload, linking succeeded as recorded above. No OAuth client or credential was created or rotated for this correction. The checked-in store client was not changed.

This is a local workaround, not a packaging fix. Copying the repository extension folder over the local installation can overwrite the correction. A durable separation of unpacked and store OAuth configuration remains open.

## Acceptance boundary and remaining evidence

The live rotation request and post-rotation self reads are verified. Active state and retained Google link have human confirmation. The Day 3 plan's additional requirement for old and new device identifiers in live audit rows remains unverified in this record. The implementation writes a `device_rotated` row in `device_events` with the old device ID and a `successor_device_id` in its detail; that source behavior is not a substitute for inspecting the live row.

The 24-hour overlap and expired-key refusal have automated test coverage, but neither an old-key request during this live overlap nor refusal after its expiry was observed in this session. Full Day 3 audit acceptance must remain open until live audit evidence is attached through an authorized read. Do not repeat rotation merely to fill this record.
