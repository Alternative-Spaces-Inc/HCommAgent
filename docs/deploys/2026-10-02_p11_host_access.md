# 2026-10-02: HCA-P11, API host access requested at runtime

## Why

Since P2 the extension could only reach the API after `host_permissions`
in `manifest.json` was patched by hand with the service URL on the owner's
Mac. The deploy on 2026-10-01 reverted that patch once, and every unpacked
load since needed the Site access toggle flipped on the Details page. The
URL is a deployment value, so it does not belong in the repo manifest, and
a build step that bakes it in would only move the patch into the pipeline.

## What this change does

- `manifest.json`: `optional_host_permissions: ["https://*/*"]`;
  `host_permissions` stays empty. Chrome grants nothing at install.
- `lib/hostaccess.js`: `originPatternFor(apiBase)` (pure, tested) maps the
  saved URL to one match pattern, `https://<host>/*`, refusing http, empty,
  junk and credentialed URLs; `requestHostAccess` asks Chrome for that one
  origin, `hasHostAccess` reports it.
- Options page: Save asks first and stores second, so a refused dialog
  leaves the previous working URL in place. A status line under the field
  says whether this install holds access to the saved host. The grant is
  stored against the extension ID (fixed by the manifest `key`) and
  survives unpacked reloads.
- Options help text updated; the manifest patch instruction is gone.

Tests: extension 25 (four new). API untouched.

## Red gate

None on the server. The owner side is one Chrome dialog (Allow) on the
options page after the extension folder is refreshed from main.

## Owner steps for the proof (full path each)

1. HCA refreshes `~/hca-extension` from the merged main through the Mac
   bridge (rsync of `extension/`, the private key file is elsewhere and is
   not touched).
2. Chrome, address bar: `chrome://extensions`. On the card "Human
   Collections Agent", click the circular reload arrow (bottom right of the
   card).
3. Same card, click "Details". Scroll to "Extension options", click it.
   The "API base URL" field still holds the saved URL. Click "Save". Chrome
   shows a dialog naming the host; click "Allow".
4. The status line under the field reads "Access to <host> granted."

## Proof (2026-10-02 14:58 PT)

Extension folder refreshed from main `02dd02ec` (PR 33 runtime host access,
PR 34 icons); card reloaded; options Save then Allow on the Chrome dialog.
Server logs show signed `GET /v1/inbox` 200 from the extension every minute
from 21:35Z on, including twice at 21:43:14Z right after the reload, with
`host_permissions` empty in the loaded manifest. Chrome's card error text
is a fixed Chromium string that lists all four properties whenever any one
is missing, so it cannot tell a stale entry from a fresh one; the proof ask
settled it.

Request `c8076e4574a9433fbc32f5b74671b821`, title "P11 proof: host access
granted at runtime, which letter?", radio A/B, suggested B, posted by
caller `agent-p11-proof`. The desktop notification appeared (icon fix
proven) and the card was answered in the popup.

| field | value |
| --- | --- |
| state | answered |
| value | b |
| answered_by | Al MacBook Chrome |
| answered_at | 2026-10-02T21:58:30Z |
| surface | browser_overlay |
| device | dev_ddea0aae221d2e3b9cedd9e4c05e260e |

Both proof keys (`c87ad00617af`, `dad2d70e89d9`) revoked through the P10
route the same minute. Lesson: GET /v1/requests/{id} is caller scoped, so
read the record before revoking the key; the second key existed only
because the first was revoked too early.

Server unchanged: revision 00012, main `530d2d9f` code; PRs 33 and 34 are
extension and docs only.

## Found during the owner steps: notifications never had an icon

After Save and Allow the card showed "Unchecked runtime.lastError: Some of
the required properties are missing: type, iconUrl, title and message."
That text comes from `chrome.notifications.create` in `background.js`,
which passed `iconUrl: undefined`; Chrome requires an icon for type
`basic`, so the browser notification surface has silently failed since P2
(the catch logged a warning, the badge count still worked). The error
appearing right after Allow is also evidence the inbox fetch to the API
succeeded under the runtime grant. Fix in the same packet: three generated
PNG icons under `extension/icons/`, declared in the manifest `icons` block
and passed as `iconUrl`.
