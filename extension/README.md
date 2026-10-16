# Browser decision surface

This developer build targets Chrome 122 or later. Load `extension/` as an unpacked extension in Chrome's extension settings. The checked-in manifest pairs its public development key with the existing unpacked OAuth client, so copying the repository folder does not restore the store client. The key and OAuth client IDs are public configuration, not credentials. Store packaging selects a different existing client and removes the development key. See [Package targets](#package-targets) before distributing an artifact.

Open Options, save a trusted **HTTPS origin** without a path, query, fragment or credentials, and grant access to that origin. The administrator must register the displayed public key, configure the permitted owner account and OAuth audience, and then you must sign in through Options. This extension intentionally refuses HTTP origins, including local HTTP; use a local HTTPS service for development.

The popup supports all six v1 controls: radio, checkbox, approve/deny, searchable dropdown, chat text up to 4,000 characters, and per-row confirmation. Suggestions never select a response automatically. Material decisions display resource, change, impact, command and rollback, and require an explicit acknowledgement that the context has been reviewed. Supporting data and history supplied in `zoom` can be inspected without leaving the popup. Notification contents are hidden by default; enable previews only when lock-screen disclosure is acceptable. Turning previews off clears existing decision notifications, including any whose creation is in progress.

## Stored data and trust assumptions

The private signing key is a non-extractable WebCrypto `CryptoKey` persisted in IndexedDB. Atomic initialization selects one key even when multiple extension contexts start together. Older plaintext JWK storage is imported once, retaining the registered identity, and removed only after the durable key has been stored. Upgrading discards older caches and offline responses without origin/device provenance and shows a delivery notice; review the fresh inbox. Chrome local storage is restricted to trusted extension contexts. Non-extractability prevents ordinary key export; it does not protect against a compromised browser, operating system, malicious extension update, or an attacker executing code inside this extension.

Caches, replay cursors and focus state are scoped to the API origin and device. Switching servers aborts the old connections and discards old cached questions and offline responses rather than submitting them to the new server. Signed fetches refuse redirects and do not send browser cookies. Owner-only deployment is the supported audience model; a device can access only the inbox authorized by the server.

Every response is durably queued before delivery, bound to the origin and device, and assigned a stable idempotency key. One background worker owns mutations, and retries sign fresh timestamps/nonces. The queue is limited to 100 entries, 32 KiB per answer, 1 MiB total, 20 attempts and 24 hours (or the decision deadline when earlier). An answer already saved cannot silently be edited. An ambiguous network failure retains the same idempotency key for retry. A saved answer is not a server-accepted decision. Expired, rejected and stale delivery notices appear in the popup; authentication failures clear sensitive cached and queued state.

A stream supplies prompt updates while the worker is active; Chrome may suspend it. A one-minute persistent alarm reconciles the complete paginated inbox after restarts and disconnects. Replay-gap signals clear the cursor and trigger reconciliation. Delivery timing depends on Chrome and network availability.

Use **Rotate device key** in Options when necessary. The extension creates a non-extractable successor key in IndexedDB, signs `POST /v1/devices/self/rotate` with the current key, and promotes the staged key only after a successful response. The server carries the tenant, label, surface, priority and Google link to the successor; both keys verify during the configured overlap (`HCA_DEVICE_ROTATION_OVERLAP_HOURS`, default 24), after which the old device is refused. A repeated request with the same staged public key returns the same successor, so a lost response or failed local promotion is recoverable. Saved local responses are cleared only after server acceptance. The Options page shows the current server state and overlap end. Uninstalling the extension removes local keys; recovery then requires the normal owner-approved device registration process. Server-side decisions and audit retention follow the operator policy.

## Regression checks

Run `npm test` inside this directory for deterministic Node tests covering signed requests, cross-context initialization, durable retries, queue races, origin changes, revocation, stream generations and restart reconciliation. This suite does not require installed npm dependencies.

For actual Chromium DOM, IndexedDB and worker-context checks:

```sh
npm ci
npx playwright install chromium
npm run test:browser
```

The browser tests use only a loopback server and synthetic fixtures. They verify two-tab startup races, non-extractable durable keys across worker termination/restart, and the popup flow for all six controls, including explicit selections, readable material context, accessible labels/live errors and submission locking. Node 22 or 24 is supported; CI uses Node 24. The test stops if Chromium cannot launch; it does not silently skip. Full unpacked-extension Google OAuth, Chrome alarm scheduling and Web Store deployment still require manual checks with a configured test server and owner account.

A [synthetic material-decision screenshot](docs/decision-review.png) is generated by the browser checks with `HCA_SCREENSHOT_DIR=docs npm run test:browser`.

The developer release candidate is 0.3.0. [Release instructions](../docs/RELEASES.md) cover versioned archives/checksums, extension ID and upgrade compatibility. The owner-reported manual sign-in/client/lifecycle/accessibility approval is [recorded separately](../docs/reviews/2026-10-12_manual_validation_approval.md); Web Store publication and a different deployment still need their own evidence.

## Decision page

When a notification click cannot open the popup (`chrome.action.openPopup()` rejects), the extension opens `decision.html?request=<id>` in a tab at phone width with that one ask and the action bar pinned; a second click focuses the existing tab. The ask stays in the inbox until answered or expired. Proven by the `notifications` and `mobileFirstLayout` browser scenarios on every PR.


## Package targets

The build-only [OAuth mappings](oauth-clients.json) bind each existing extension ID to its public OAuth client ID. They do not grant access or create credentials. The server must separately accept the chosen client audience. These existing mappings are recorded in the [store setup runbook](../docs/runbooks/HCA_CHROME_WEB_STORE.md).

| Installation | Extension ID | Manifest key | OAuth mapping |
|---|---|---|---|
| Raw `extension/` folder or unpacked archive | `jmpjjfhgggjmbebejaapchodjacmbmpm` | Retained | `unpacked` |
| Chrome Web Store archive | `icaocojldacopllkkjdhghkeoeghccfe` | Removed | `store` |

From the repository root, `python3 scripts/pack_extension.py --target store --out dist` builds the store archive. This remains the default target and keeps the existing `extension-<version>-<revision>.zip` filename. Cloud Build explicitly selects `--target store`.

For an unpacked archive, use `python3 scripts/pack_extension.py --target unpacked --out dist`. Its filename ends in `-unpacked.zip`, so building both targets into the same directory cannot overwrite the store archive. Extract this archive before selecting its folder in Chrome. The legacy `--keep-key` flag is an alias for the unpacked target; combining it with `--target store` fails.

Each successful build prints target, key presence, source revision and archive checksum as JSON. It deliberately omits extension and OAuth client identifiers from stdout; the selected mapping is validated and applied only to the archive. The manifest is rewritten only inside the archive. Packaging rejects a raw manifest whose public key hashes to a different unpacked extension ID or whose OAuth client disagrees with the unpacked mapping. It also rejects malformed, incomplete or duplicate target mappings before creating output. The mappings file is excluded from both runtime archives.

To update an existing unpacked installation, keep its current Chrome installation and replace its runtime files with this corrected folder or an unpacked artifact, then reload the existing extension. Do not remove/reinstall it or copy a store archive over it: removing the installation can erase the registered device key. No new key registration or Google sign-in is required merely to correct public packaging metadata on an already linked device. Do not carry a manual store-client override forward into `extension/manifest.json`.

The tests validate the configured pairing and archive contents locally. They cannot establish that Google has not subsequently deleted or reconfigured an external OAuth client; sign-in against Google remains a separate live check.
