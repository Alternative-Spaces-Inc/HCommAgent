# HCA P17: Chrome Web Store packaging, plan v1 (2026-10-06)

## Why this and why now

The Chrome extension runs unpacked from a folder on the owner's Mac
(~/hca-extension, rsync of extension/ from main). Every merge that touches
extension/ needs an rsync plus a card reload at chrome://extensions by the
owner, Chrome shows the "disable developer mode extensions" nag, and the
host-permission and sign-in state is tied to one unpacked folder. A Web
Store item removes the reload step (Chrome updates installed items itself,
within hours of a publish), gives the extension a real install path for a
second Mac or a second owner later, and ends unpacked loads on the owner's
machine. The owner selected this packet on 2026-10-06 15:40 PT over
per-device quiet hours. Stable Mac signing (P16) is closed.

## The one hard constraint: keep the extension ID

The item must keep the ID jmpjjfhgggjmbebejaapchodjacmbmpm. Three things
hang off it: the Chrome Extension OAuth client in the dedicated project
(P7) is bound to that item ID, the installed device dev_ddea0aae (its
P-256 key in chrome.storage.local) belongs to the install with that ID,
and the manifest "key" field is the public half of ~/hca-extension-key.pem
(Mac only, never in the repo) which derives exactly that ID.

Chrome's documentation (Manifest key reference) describes the reverse flow
(upload first, copy the store's key into the manifest). Day 2 verified it
on 2026-10-07 with the Day 1 zip: the Developer Dashboard refuses the
upload with "key field is not allowed in manifest". Path A is closed; the
owner selected Path B the same morning. Both paths as planned:

- Path A (ID matches): nothing else changes. The existing install is
  replaced by the store install with the same ID, same storage, same
  device key, same Google link.
- Path B (store assigns a different ID or rejects the key field): the
  item ID becomes the store's. Then, owner Console step, the Chrome
  Extension OAuth client's item ID is changed to the new ID (or a second
  client is created and its client_id written into the manifest oauth2
  block), the store install registers as a new device (options page
  flow from P6 and P7: register, Google link), the old device is revoked
  via the admin route, and the manifest "key" is dropped from the repo.
  Path B is a selection for the owner, not an automatic fallback.
  As built: scripts/pack_extension.py drops "key" from the zipped
  manifest (--keep-key only for an unpacked load); the repo manifest keeps
  the key so the unpacked folder keeps its ID until Day 3 removes it.

## Decisions proposed

1. Visibility Unlisted. Anyone with the link can install, but an install
   is inert until an admin registers its device key and the owner links a
   listed Google account, so Unlisted costs nothing. Private visibility
   (trusted testers or a Workspace domain) is available if the developer
   account is a Workspace account; decide at registration.
2. The developer account is the owner's Google account, one-time 5 USD
   registration fee, 2-step verification required. Red gate (spend,
   account), owner Console step, Terminal has no equivalent.
3. Store review needs a privacy policy URL and per-permission
   justifications (identity, notifications, alarms, storage, optional host
   access https://*/*). The policy is served by the API service at
   GET /privacy (static HTML, one paragraph: what the extension sends,
   the Google ID token and signed requests to the owner's own server;
   nothing sold, nothing shared). No new host, no new project.
4. Version: extension/manifest.json "version" becomes the release counter,
   bumped by the PR that ships extension changes (gate: a zip upload with
   an unchanged version is refused by the store). Build metadata (commit)
   goes into "version_name".
5. The zip is built in Cloud Build on every merge to main and kept as a
   build artifact in the dedicated project's bucket. Upload to the store
   is Day 3 and optional: it needs a Chrome Web Store API OAuth client,
   a one-time owner consent for the chromewebstore scope, and the refresh
   token in Secret Manager. Day 1 and 2 upload by hand from the artifact.

## What ships

Day 1 (repo, no owner step): scripts/pack_extension.sh (zips extension/
without tests, package.json, and with "key" kept; prints the zip's
sha256 and the manifest version), a cloudbuild step pack-extension after
smoke-local that writes extension-<version>-<sha>.zip to the artifacts
bucket, GET /privacy on the API with a test, extension version 0.2.0 with
version_name from the build, a node test that the zip manifest parses and
keeps the key. docs/runbooks/HCA_CHROME_WEB_STORE.md with the dashboard
path for every owner click.

Day 2 (owner, dashboard): register the developer account (fee), Add new
item with the Day 1 zip, read the item ID on the Package tab, compare with
jmpjjfhgggjmbebejaapchodjacmbmpm (Path A or B selection), fill the listing
(name, summary, icon from extension/icons, category Productivity,
language English), privacy tab (policy URL, justifications, data use
disclosure: authentication information, not sold, not shared), visibility
Unlisted, submit for review. Review takes hours to days; the record notes
the submission time and the result.

Day 3 (after review passes): owner installs from the store link, removes
the unpacked card, proves one ask answered from the store install (record
read with a proof key, surface browser_overlay), and the install path
from P6 moves to the runbook. Optional: Publish API upload step in Cloud
Build (OAuth client, consent, refresh token in Secret Manager
hca-cws-refresh-token, step cws-upload gated on a version bump).

## Red gates (owner)

- Developer account registration and the 5 USD fee.
- Path B selection if the ID does not match.
- Submit for review (publishes to Unlisted when approved).
- Day 3 optional: OAuth client for the Publish API and the consent.

## Acceptance

1. Cloud Build artifact extension-0.2.0-<sha>.zip exists for the Day 1
   merge; its manifest parses, keeps the key, version 0.2.0.
2. GET /privacy returns 200 text/html on the live service.
3. Dashboard item exists with the Day 1 zip; item ID recorded (A or B).
4. Review passed; the store install on the owner's Mac answers one ask
   recorded with surface browser_overlay from a device the record names.
5. The unpacked card is gone from chrome://extensions.

## Not in P17

Public listing, screenshots beyond the one required 1280x800, Firefox or
Edge stores, Google OAuth app verification (dropped in P13 research), and
per-device quiet hours (next candidate).
