# HCA P13: macOS notification surface, plan v1 (2026-10-04)

## Why this and why now

Plan v1 (P3 to P12) is proven: the Chrome extension serves rung 1 (browser
overlay) and rung 2 (browser notification) of the surface ladder on the one
API. The charter names rung 3, a native macOS notification with OS-fitted
actions, and rung 4, a dedicated menu bar app (docs/HCA_CHARTER_v1.md,
Surfaces; docs/decisions/DECISIONS_20260929.md item 3; Cloud Run plan
section 11 items 1 and 2). The owner selected rung 3 as the next phase on
2026-10-04. The server already carries `surface: macos_notification` in
the answer record and openapi enum, so this phase is client work plus one
small server change.

## Facts checked before planning (owner's Mac, read 2026-10-04 via bridge)

- macOS 27.0.1, arm64, Xcode 27.0 at /Applications/Xcode.app, Swift
  present. Xcode license not yet accepted: `xcodebuild` refuses until
  `sudo xcodebuild -license accept` is run by the owner (password is his).
- No codesigning identities. Local notifications work from an ad-hoc
  signed `.app` bundle; a Developer ID is not needed for the owner's own
  Mac.
- The server pins one Google client id (`HCA_GOOGLE_CLIENT_ID`, aud/azp
  check in api/app/googleauth.py). A Mac app cannot use the Chrome
  Extension client type, so linking a Mac device needs a second client id.

## Shape

A small menu bar app, `HCA Notifier`, in `macos/` of this repo. Swift
Package (SwiftPM executable target plus a test target) and a bundle script
that assembles the `.app` with Info.plist and ad-hoc codesign, so no Xcode
project file is kept in the repo. The app has no window in P13: a menu bar
item with Status, Register device, Link Google, Pause, Quit.

### Device identity (mirrors the extension, same server contract)

- P-256 key generated on first run, stored in the login Keychain
  (software key; Secure Enclave is a later option).
- Device id, headers and canonical string exactly as api/app/devicesig.py:
  `HCA1\n` timestamp `\n` nonce `\n` METHOD `\n` path_and_query `\n`
  sha256hex(body); signature base64url of raw r||s. A shared test vector
  file (`api/tests/vectors/devicesig_v1.json`) is added and asserted by
  both pytest and XCTest so the two clients cannot drift.
- Registration: one-time `POST /v1/devices` with the admin token, by the
  owner from Terminal, as for the extension (runbook block).
- Google link: `ASWebAuthenticationSession` with a Desktop OAuth client,
  loopback redirect, PKCE; the ID token goes to
  `POST /v1/devices/self/google-link`. Server change: accept a list
  (`HCA_GOOGLE_CLIENT_IDS`, comma separated, old name kept as alias), with
  the Cloud Build substitution pinned on the trigger like `_GOOGLE_CLIENT_ID`.

### Delivery

- `GET /v1/inbox/stream` (SSE) with reconnect, plus a 60 s poll of
  `GET /v1/inbox` as fallback, as in the extension.
- One `UNNotification` per pending ask. Categories by control shape:
  `approve_deny` gives two action buttons; `radio` with up to four options
  gives one button per option (macOS shows them under the Options
  disclosure); any other control gives an alert-only notification whose
  body says to answer in Chrome. Suggested option is marked in the body.
- `request.answered` and `request.cancelled` events (from any surface)
  remove the matching delivered notification, so answering in Chrome
  clears the Mac banner and the reverse.

### Answer

- Action tap signs and posts `POST /v1/requests/{id}/answer` with
  `surface: macos_notification`, `path_taken` = notification action id,
  `answered_by` = device label. 409/404/410 are treated as already handled
  (notification removed). A transport failure re-posts the notification
  with a one-line error; no offline queue in P13 (an action tap needs the
  Mac awake and the extension's queue already covers the browser path).

### Not in P13

Rung 4 UI (windows, inbox list, text controls), Secure Enclave keys,
Developer ID signing and notarization, auto-update, Sparkle, App Store.

## Days

| Day | Build | Proof |
| --- | --- | --- |
| 1 | `macos/` package, bundle script, menu bar item, notification permission prompt, a test notification from the menu | App runs from `~/hca-notifier/HCA Notifier.app`, banner shown |
| 2 | Keychain P-256 key, signing, shared vectors, device registration | `GET /v1/devices/self` signed by the Mac device returns 200 |
| 3 | Stream and poll, notifications by category, dedupe on answered or cancelled | A proof ask appears as a banner; answering in Chrome removes it |
| 4 | Server `HCA_GOOGLE_CLIENT_IDS`, Desktop OAuth client, link flow | `google_email` set on the Mac device, gate on |
| 5 | Action tap answers, record read, proof record under docs/deploys/ | Record: surface macos_notification, device the Mac device |

## Owner steps (red gates and human proof)

1. Terminal: accept the Xcode license (`sudo xcodebuild -license accept`,
   owner types his password).
2. Console: create an OAuth client of type Desktop app in the dedicated
   project (same consent screen as P7). Client id is public; no secret is
   stored anywhere (PKCE flow).
3. Approve the Cloud Build substitution change (`_GOOGLE_CLIENT_IDS`).
4. First launch: allow notifications when macOS asks.
5. Terminal: register the Mac device (admin token block from the runbook).
6. Menu bar: Link Google, sign in once.
7. Answer the proof ask from the banner.

## Decision (owner, 2026-10-04 12:4x PT)

The Mac device passes the Google-link gate through a Desktop OAuth client
in the dedicated project plus the server accepting a list of client ids
(`HCA_GOOGLE_CLIENT_IDS`, pinned on the Cloud Build trigger). Alternative
offered and declined: a "link by an already linked device" code flow (no
Console step, but a new trust path and no proof of the Google account from
the Mac itself).

## Build mechanics

The agent sandbox is Linux and cannot build macOS binaries. Builds and
XCTest run on the owner's Mac through the bridge (`swift build`,
`swift test`, bundle script). Optional later: a GitHub Actions macOS job
for `swift build` and `swift test` on pull requests; macOS runner minutes
cost ten times Linux minutes, so it is off until the owner approves it.
