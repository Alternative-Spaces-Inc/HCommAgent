# HCA P23: Chrome extension hardening, plan v1 (2026-10-14)

## 0 Summary

P23 builds the P23 row of docs/plans/HCA_P19_PRODUCTION_HARDENING_PROGRAM_v1.md for the browser overlay surface in `extension/`. Most of the row's mechanics already exist on main 599be4ac (the 43 item review of 2026-10-12 hardened the client): strict HTTPS origin handling, a non-extractable P-256 device key created once across contexts, a durable offline answer queue with TTL and size limits, controls disabled during submit, idempotency keys on every answer, and a Playwright run in CI. What is missing is proof and lifecycle: the browser suite covers two scenarios out of the eight the program row names, device key rotation still ends in a manual admin re-registration, the notification click path depends on `chrome.action.openPopup()` succeeding, and the popup is a fixed 360 px column that has never been tested against the mobile first rule for decision cards (owner rule 2026-10-10). P23 closes those gaps in three days: client layout and click path, browser proofs, then device rotation on the server and client with the documentation that goes with it.

Owner selections are listed in section 6 and recorded in docs/decisions once taken.

## 1 Current behaviour, verified against main 599be4ac

Present and kept:

- `extension/lib/hostaccess.js` `normalizeApiOrigin`: the API base must be a plain `https:` origin with no path, query, fragment or credentials; host access is an optional host permission requested inside a user gesture.
- `extension/lib/devicekey.js`: one in flight initialisation per storage adapter (`loadOrCreateDeviceKey`), atomic `insertIfAbsent` in IndexedDB so independent contexts converge on one key, private key rejected if extractable, canonical string `HCA1\n<ts>\n<nonce>\n<METHOD>\n<path?query>\n<sha256(body)>` mirroring `api/app/devicesig.py`, `createSignedFetch` refuses non HTTPS targets, credentials, fragments and redirects (`redirect: "error"`, `credentials: "omit"`, `cache: "no-store"`). The body that is hashed is the exact string that is sent, so serialisation is deterministic by construction.
- `extension/lib/offlinequeue.js`: `hca_answer_queue_v2` in `chrome.storage.local`, bounded (100 entries, 32 KiB per body, 1 MiB total, 24 h TTL or the ask deadline), one serial executor per worker, acknowledgements remove only the attempted entry, outcome classes sent, stale (404, 409, 410), retry (network, 5xx, 429), dropped (401, 403, attempt cap 20), rejected.
- `extension/lib/api.js`: `postAnswer` carries `Idempotency-Key` from the queue entry; inbox pagination is bounded to 50 pages with loop detection.
- `extension/popup.js` `setBusy`: every button, input, select and textarea inside the card is disabled while a submit is in flight; `extension/tests/browser.integration.js` proves duplicate submit is refused for all six controls.
- `extension/background.js`: `onInstalled`, `onStartup` and a one minute alarm all call `wake`, so a restarted worker re-reads config, reconnects the stream and flushes the queue; `hca_rotate_device` message rotates the local key and tells the owner to re-register by hand.
- CI job `extension` (Node 24): `npm ci`, `npm test` (7 unit files), Playwright chromium install, `npm run test:browser`, `npm audit`.

Failure modes still open:

1. Browser proof coverage. `tests/browser.integration.js` runs two scenarios: atomic IndexedDB initialisation across tabs plus key durability across a worker restart, and the six controls through the real popup with duplicate submit refused. The program row also names offline answer and later flush, stream reconnect, concurrent queue writes, the notification click path, host permission denial and malformed stream events. None of those run in a browser today; the unit tests cover the queue and stream guard logic only.
2. Device key rotation is half a feature. `rotateDeviceKey()` deletes the local key and generates a new one, and the server has no route that accepts a new public key from a signed request, so after rotation the device is unregistered until an admin posts `POST /v1/devices` with the new PEM and the owner links Google again. `Device` (api/app/models.py line 387) has `public_key_pem`, `created_at`, `revoked_at`, no rotation state.
3. Notification click path. `notifications.onClicked` writes `focusId` into state and calls `chrome.action.openPopup()` with the failure swallowed. `openPopup` is refused when the active window has no toolbar focus or the popup is already open, so a click can silently do nothing; no fallback page exists and nothing tests the path.
4. Mobile first rule. `popup.html` is a fixed 360 px body with every control in one flowing column; a long ask pushes the action buttons below the fold and the user scrolls the whole card. There is no design rule page in `docs/` (the P27 row expects one) and no viewport test.
5. Manifest hardening. `manifest.json` relies on the MV3 default `extension_pages` CSP and declares `identity`, `notifications`, `alarms`, `storage`; the policy is not stated explicitly and no test asserts the permission set, so a future change can widen it unnoticed.

## 2 Scope

In: popup and decision page layout at phone width, explicit CSP and permission assertions, deterministic notification click path with a fallback page, browser proofs for the eight program scenarios, device key rotation end to end (server route, migration, client flow), THREAT_MODEL and README rows, a surface design rule page.

Out: the Chrome Web Store upload itself (P17 path, item id unchanged), Google sign in changes, macOS client (P24), any new control type.

## 3 Day 1: layout, click path, manifest (branch p23/day1-client)

- `popup.html`: card becomes a flex column at `width: 360px; max-height: 560px` (Chrome caps popups at 800 by 600); header with the question line, a scrolling body (`overflow-y: auto`, `min-height: 0`) holding context, evidence and option meanings, and a pinned action bar (`position: sticky; bottom: 0`) carrying the submit and cancel controls. Same structure in `decision.html` (new), a full tab page that renders one ask at `max-width: 420px` centred, used by the notification fallback.
- `background.js` `notifications.onClicked`: set `focusId`, call `chrome.action.openPopup()`, and when it rejects open `decision.html?request=<id>` with `chrome.tabs.create`; a second click while a decision tab is open focuses that tab (`chrome.tabs.query` on the extension URL) instead of opening another. `onClosed` clears nothing: the ask stays in the inbox until answered or expired.
- `manifest.json`: explicit `content_security_policy.extension_pages` of `script-src 'self'; object-src 'self'; connect-src https:`; `tests/pack.test.js` asserts the exact permission list, the empty `host_permissions`, the optional pattern and the CSP string, so a widening fails CI.
- `popup.js`: idempotent click handling made explicit: the submit handler captures a per card token at first click and ignores later clicks until the outcome returns, in addition to `setBusy`.
- Unit tests for the fallback decision (`openPopup` rejects, tab exists, tab missing) with the injected `chromeObj`.
- Version 0.4.0 in `manifest.json` and `package.json`.

Done when: `npm test` green, the popup renders a 40 line ask with the action bar visible without page scroll at 360 by 560, and `decision.html` renders the same ask at a 375 by 667 viewport with pinned actions.

## 4 Day 2: browser proofs (branch p23/day2-proofs)

Extend `tests/browser.integration.js` (or split into `tests/browser/*.js` run by one runner) so each program scenario is a named PASS line:

1. Offline answer: route the API to abort, answer in the popup, assert one queue entry, restore the route, trigger `wake`, assert exactly one POST with the same `Idempotency-Key` and an empty queue.
2. Reconnect: close the SSE route mid stream, assert the alarm poll keeps the inbox current, restore, assert one new stream connection.
3. Worker restart: terminate the service worker (`context.serviceWorkers()` and `evaluate(() => self.registration.unregister())` or the CDP `terminate`), wake it, assert the queue flushes once and the key is unchanged (extends the existing scenario).
4. Duplicate clicks: double click submit on every control, assert one POST (exists, kept).
5. Concurrent queue writes: two popups answering two asks within the same tick, assert two entries, no lost write.
6. Notifications: create a notification through the worker, dispatch `onClicked`, assert `focusId` and the fallback tab when `openPopup` is stubbed to reject.
7. Permission denial: `chrome.permissions.request` stubbed to false, assert the options page shows the host access error and no signed request leaves.
8. Malformed events: feed non JSON, wrong `type`, oversized and foreign origin events into the stream handler, assert they are dropped and counted, nothing rendered.
9. Mobile first: viewport 375 by 667 on `decision.html` and 360 by 560 on the popup, a 40 line ask, assert the question heading and the submit button are both within the viewport without scrolling and the body region scrolls.

CI: the existing `extension` job runs the suite headless; a failing PASS line fails the job. Runtime budget 6 minutes.

Done when: nine PASS lines in CI on the PR, THREAT_MODEL rows cite the scenario names.

## 5 Day 3: device key rotation and docs (branch p23/day3-rotation)

Server (api):

- Migration adding `devices.superseded_by` (String, nullable, FK devices.id) and `devices.previous_valid_until` (DateTime, nullable), mirroring api key rotation (P21b Day 3).
- `POST /v1/devices/self/rotate`, signed by the current device key, body `{ "public_key_pem": "<new>" }`: creates the successor device row with the same tenant, label, surface, priority and Google link, marks the old row `superseded_by` and `previous_valid_until = now + HCA_DEVICE_ROTATION_OVERLAP_HOURS` (default 24), audit event `device_rotated`. During the overlap both keys verify; after it the old row is revoked by the existing expiry path. 409 when the device is already rotating or revoked.
- `DeviceView` gains `state` (active, rotating, expired, revoked), `superseded_by`, `previous_valid_until`; `openapi.yaml` regenerated through `.github/gates/export_openapi.py`; tests in `api/tests/test_device_rotation.py`.

Client:

- `rotateDeviceKey()` becomes two phase: generate the successor key, call the rotate route signed with the current key, and only on 2xx swap the durable key (IndexedDB write of the successor, old key deleted); on failure the current key stays. Options page row "Rotate device key" shows the state and the overlap end.
- `background.js` retries the swap on the next wake if the server accepted but the local write failed (idempotent: the server returns the same successor on a repeated call with the same PEM).

Docs: `docs/THREAT_MODEL.md` rows for stolen device key (rotation with overlap), notification spoofing (ids prefixed, request must be in cache), offline queue disclosure (bounded, dropped on 401 and 403); `extension/README.md` sections for rotation and the decision page; `docs/design/SURFACE_DESIGN_RULE.md` stating the mobile first rule for every decision card and owner facing selection, with the viewport numbers used by the proofs; plan status table updated.

Done when: pytest and the browser suite green, a rotation proven on the live API with the owner's Chrome (old and new device ids in the audit rows), owner says so.

## 6 Owner selections

1. Device rotation model. Suggested: signed self rotate route with a 24 hour overlap, mirroring api key rotation, no admin step, Google link carried over. Alternatives: keep the manual admin re-registration (no server work, rotation stays a support task); require a fresh Google link on every rotation (stronger, but an owner without the Google account at hand cannot rotate).
2. Notification fallback surface. Suggested: a full tab `decision.html` at phone width when `openPopup` is refused. Alternatives: the options page with the ask focused (one page fewer, but the options form shares the viewport); do nothing on refusal (current behaviour, silent).
3. Browser suite placement. Suggested: all nine scenarios in the existing `extension` CI job on every PR, six minute budget. Alternative: a nightly scheduled job only, PRs run the two existing scenarios (faster PRs, slower detection).

## 7 Red gates

None. No credential, IAM, DNS or spend change. The Chrome Web Store upload of 0.4.0 follows the P17 path after Day 3 and is a separate owner step.

## 8 Status

| Day | Branch | State |
|---|---|---|
| Plan | p23/plan | this document |
| 1 | p23/day1-client | merged as PR 26 (main 31f19882): layout, decision page, click path, CSP pin, 0.4.0; selection 2 in docs/decisions/DECISIONS_20261015.md |
| 2 | p23/day2-proofs | merged as PR 27 (main b6fb8ba2): nine browser proofs on every PR |
| 3 | p23/day3-rotation | not started |
