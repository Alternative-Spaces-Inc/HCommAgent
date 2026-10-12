# Browser decision surface

This developer build targets Chrome 122 or later. Load `extension/` as an unpacked extension in Chrome's extension settings. The public manifest key stabilizes its development ID; it is not a credential. The Google OAuth client must authorize that extension ID for sign-in. Store packaging removes the development key; configure the published ID before distributing a store build.

Open Options, save a trusted **HTTPS origin** without a path, query, fragment or credentials, and grant access to that origin. The administrator must register the displayed public key, configure the permitted owner account and OAuth audience, and then you must sign in through Options. This extension intentionally refuses HTTP origins, including local HTTP; use a local HTTPS service for development.

The popup supports all six v1 controls: radio, checkbox, approve/deny, searchable dropdown, chat text up to 4,000 characters, and per-row confirmation. Suggestions never select a response automatically. Material decisions display resource, change, impact, command and rollback, and require an explicit acknowledgement that the context has been reviewed. Supporting data and history supplied in `zoom` can be inspected without leaving the popup. Notification contents are hidden by default; enable previews only when lock-screen disclosure is acceptable.

## Stored data and trust assumptions

The private signing key is a non-extractable WebCrypto `CryptoKey` persisted in IndexedDB. Atomic initialization selects one key even when multiple extension contexts start together. Older plaintext JWK storage is imported once, retaining the registered identity, and removed only after the durable key has been stored. Upgrading discards older caches and offline responses without origin/device provenance and shows a delivery notice; review the fresh inbox. Chrome local storage is restricted to trusted extension contexts. Non-extractability prevents ordinary key export; it does not protect against a compromised browser, operating system, malicious extension update, or an attacker executing code inside this extension.

Caches, replay cursors and focus state are scoped to the API origin and device. Switching servers aborts the old connections and discards old cached questions and offline responses rather than submitting them to the new server. Signed fetches refuse redirects and do not send browser cookies. Owner-only deployment is the supported audience model; a device can access only the inbox authorized by the server.

Every response is durably queued before delivery, bound to the origin and device, and assigned a stable idempotency key. One background worker owns mutations, and retries sign fresh timestamps/nonces. The queue is limited to 100 entries, 32 KiB per answer, 1 MiB total, 20 attempts and 24 hours (or the decision deadline when earlier). An answer already saved cannot silently be edited. An ambiguous network failure retains the same idempotency key for retry. A saved answer is not a server-accepted decision. Expired, rejected and stale delivery notices appear in the popup; authentication failures clear sensitive cached and queued state.

A stream supplies prompt updates while the worker is active; Chrome may suspend it. A one-minute persistent alarm reconciles the complete paginated inbox after restarts and disconnects. Replay-gap signals clear the cursor and trigger reconciliation. Delivery timing depends on Chrome and network availability.

Use **Rotate device key** in Options when necessary, then ask the administrator to revoke the old server registration (`POST /v1/devices/{old_id}/revoke`), register the new key and sign in again. Local rotation clears saved responses and does not itself revoke server authorization. Uninstalling the extension removes its local data; reinstalling requires registering and linking the new device. Server-side decisions and audit retention follow the operator's policy.

## Regression checks

Run `npm test` inside this directory for deterministic Node tests covering signed requests, cross-context initialization, durable retries, queue races, origin changes, revocation, stream generations and restart reconciliation. This suite does not require installed npm dependencies.

For actual Chromium DOM, IndexedDB and worker-context checks:

```sh
npm ci
npx playwright install chromium
npm run test:browser
```

The browser tests use only a loopback server and synthetic fixtures. They verify two-tab startup races, non-extractable durable keys across worker termination/restart, and the popup flow for all six controls, including explicit selections, readable material context, accessible labels/live errors and submission locking. Node 24 or later is required. The test stops if Chromium cannot launch; it does not silently skip. Full unpacked-extension Google OAuth, Chrome alarm scheduling and Web Store deployment still require manual checks with a configured test server and owner account.

A [synthetic material-decision screenshot](docs/decision-review.png) is generated by the browser checks with `HCA_SCREENSHOT_DIR=docs npm run test:browser`.
