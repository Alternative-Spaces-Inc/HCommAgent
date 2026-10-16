# HCA Chrome Web Store runbook (HCA-P17)

> Historical setup record. For the 0.3.0 candidate, use [current release instructions](../RELEASES.md) and the [served privacy disclosure](../../api/app/privacy.py). Listing text must describe Google access-token verification, agent/webhook recipients and hosting, local storage/notification previews, and actual export/deletion/retention limits. Do not reuse older token or sharing claims. Source changes alone do not update the live privacy URL or Web Store listing; the operator must verify the deployed policy and configured extension/OAuth IDs before publication.

## Current package target correction (2026-10-15)

The raw repository `extension/manifest.json` now uses the existing unpacked client matching its development key. `scripts/pack_extension.py --target store` explicitly substitutes the existing store client and removes that key. `--target unpacked` (or legacy `--keep-key`) retains the matching unpacked identity. The build-only registry is [oauth-clients.json](../../extension/oauth-clients.json); [package target instructions](../../extension/README.md#package-targets) describe artifact names and validation.

The historical Day 2 statement below that the store client becomes the repository manifest client is superseded. Leaving it there broke fresh Google linking for unpacked reinstalls. No client creation, credential rotation or server allowlist edit is part of this correction. Keep the existing Chrome installation when copying corrected unpacked files, then reload it; do not remove it merely to change packaging metadata.

Plan: docs/plans/HCA_P17_CHROME_WEB_STORE_v1.md. Every owner step below
is a dashboard click because the Chrome Web Store has no Terminal
equivalent for registration, upload or review. The agent fetches the zip,
reads the result and writes the record; the owner clicks.

## 0. Get the zip (agent, Terminal on the Mac)

Every merge to main leaves the zip as a Cloud Build artifact. From the
HCA Terminal session (gcloud signed in):

    gsutil ls gs://<project>_cloudbuild/extension/

Copy the newest extension-<version>-<sha7>.zip to ~/Downloads with
gsutil cp. The build log step pack-extension prints the same JSON line the
test asserts (version, version_name, target store, key_present false, sha256);
compare the sha256 of the downloaded file with it before upload.

Locally, without Cloud Build:

    python3 scripts/pack_extension.py --out dist --commit "$(git rev-parse HEAD)"

## 1. Register the developer account (owner, once, red gate: 5 USD)

Browser, signed in to the owner Google account:
https://chrome.google.com/webstore/devconsole

First visit shows the registration page: tick "I agree to the Developer
Agreement and Privacy Policies", click "Pay registration fee" (one-time
5 USD, Google Pay sheet), then the dashboard opens. Requirements Google
enforces: 2-step verification on the account, a developer email (the
account's own address is fine; it cannot be changed later).

Record: account registered date, no amounts beyond the fee.

## 2. Draft upload and the ID check (owner uploads, agent reads)

Dashboard, left sidebar "Items", top right button "+ New item". In the
dialog click "Browse files", choose the zip from step 0, click "Upload".
The item page opens on the "Store listing" tab. Do not click "Submit for
review" yet.

Click the "Package" tab (top row of tabs on the item page). It shows
"Item ID". Compare with jmpjjfhgggjmbebejaapchodjacmbmpm (the ID of the
unpacked install at chrome://extensions; the agent reads that one from the
device record, not the owner).

Verified 2026-10-07: the store refuses a manifest with a "key" field
("key field is not allowed in manifest"), so the zip has no key and the
Item ID is the store's own, never jmpjjfhgggjmbebejaapchodjacmbmpm. The
draft uploaded 2026-10-07 09:00 PT (zip extension-0.2.0-77b9264.zip) has
Item ID icaocojldacopllkkjdhghkeoeghccfe (store item, Unlisted once published).
Then (Path B):

- Google Cloud Console, dedicated project, "APIs & Services", "Credentials",
  section "OAuth 2.0 Client IDs", row of the Chrome Extension client:
  the "Item ID" field is read-only (verified 2026-10-07), so a second
  client was created: "+ Create credentials", "OAuth client ID", type
  "Chrome Extension", name "HCA Chrome extension (store)", Item ID
  icaocojldacopllkkjdhghkeoeghccfe. Its public client_id
  529822100897-taeem2o8rr3av3s1v1tv7oj9eaf3efrr.apps.googleusercontent.com
  is the manifest oauth2 client_id from this change on and is appended to
  the trigger's _GOOGLE_CLIENT_IDS list (the server accepts any id in
  HCA_GOOGLE_CLIENT_ID plus HCA_GOOGLE_CLIENT_IDS). The old client
  (Item ID jmpjjfhgggjmbebejaapchodjacmbmpm) stays until Day 3; the
  unpacked device is already linked and never signs in again.
  Trigger edit as done 2026-10-07: gcloud builds triggers update github
  with --update-substitutions returns INVALID_ARGUMENT for this trigger
  (the comma inside the value is not the cause; the escaped form fails
  the same way). What works: gcloud beta builds triggers export to a
  yaml file in /tmp, edit the one _GOOGLE_CLIENT_IDS line, gcloud beta
  builds triggers import --source, describe to read it back, delete the
  temp files. All other pinned values stay as exported.
- The store install (step 5) is a new extension with a fresh device key:
  the agent registers it with the admin token from the Terminal, the
  owner clicks "Sign in with Google" once on its options page, the agent
  sets priority 200 and revokes the old device.

## 3. Listing (owner, item page tabs)

Store listing tab: Title "Human Collections Agent", Summary (132 chars
max): "Decision cards for the owner of a Human Collections Agent server:
approve, deny, pick an option, answered from the browser." Description:
three or four sentences from the privacy page's first paragraph plus
"Requires a server the owner operates; an install without a registered
device does nothing." Category "Productivity", Language "English".
Store icon: upload extension/icons/icon128.png. Screenshot: one 1280x800
PNG of the popup with a pending ask (the agent produces it from a test
ask before this step and puts it in ~/Downloads).

Privacy tab (done 2026-10-07 13:2x PT, every field verified from the
owner's screenshot): Single purpose: "Shows decisions sent by the owner's
own server and records the owner's answer." Permission justification
boxes, exactly four (the dashboard shows no box for the optional host
permission https://*/*, so nothing is entered for it): identity: "Google
sign-in proves the device belongs to a listed owner account; openid and
email scopes only." notifications: "Shows a desktop notification for each
new decision." alarms: "Polls the owner's server when the event stream is
down." storage: "Keeps the device key, the server address and unsent
answers." Remote code: "No, I am not using remote code" (the Yes button
is preselected on a fresh item; check it). Data usage: tick
"Personally identifiable information" and "Authentication information"
only, tick the three certifications. Privacy policy URL: the live service
URL plus /privacy (read with gcloud run services describe, format
value(status.url); it answers 200 text/html). Paste one sentence per box:
pasting several at once lands them in one box.

Screenshot (listing): produced by the agent from a test ask with a
temporary agent key: open chrome-extension://<item id>/popup.html in a new
Chrome window sized 520x700 via osascript (window addressed by its id, not
"front window"), screencapture -R of the content area below the 87 px
toolbar, padded onto a white 1280x800 canvas, ask cancelled and key
revoked in the same run. Wait at least 90 s after posting the ask before
opening the window: the Mac Notifier ranks first, so the Chrome surface
shows "Nothing pending" until the fallback window elapses.

Distribution tab: Visibility "Unlisted". Payments "Free". Regions all.
Set and saved 2026-10-07 13:2x PT. Listing tab saved the same hour:
description, category Productivity, language English, 128 icon, one
1280x800 screenshot.

## 4. Publisher email and submission (owner)

The first "Submit for review" click (2026-10-07 13:3x PT) was refused:
"You must provide a contact email before you can publish any item" and
"You must verify the publisher's contact email". Fix, once per account:
left sidebar "Account" (below "Items"), box "Contact email", enter the
developer account address, click "Add email" / "Verify email", open the
Google verification mail, click its link, reload the Account page until
it reads Verified. Then item page, "Submit for review", dialog with
"Publish automatically after it has passed review" ticked, confirm.
Submitted 2026-10-07 13:4x PT; status "Pending review". Review outcome
arrives by email to the contact address; Day 3 starts when the status
reads Published.

## 5. Day 3: install from the store, move the device, retire the unpacked card

Path B applies: the store item has its own ID
(icaocojldacopllkkjdhghkeoeghccfe), so the store install is a second
extension next to the unpacked one, with its own device key. Order
matters: register and link the new device before revoking the old one,
so no ask goes unserved in between. Every server call is
scripts/store_device.sh on the Mac (admin token read inside, never
printed); the owner's clicks are the install, one Copy, one Save and
one Google sign-in.

1. Status check (agent): the dashboard emails the contact address when
   review ends; the item page status line reads "Published". Store link:
   https://chromewebstore.google.com/detail/icaocojldacopllkkjdhghkeoeghccfe
   (Distribution tab shows the same link).
2. Install (owner): open the store link in Chrome, blue button "Add to
   Chrome" (right of the title), dialog "Add extension". Expected: the HCA
   icon appears in the toolbar puzzle menu; chrome://extensions shows two
   Human Collections Agent cards: the new one has ID icaocoj... and no
   folder path, the old one reads "Unpacked extension" with the
   ~/hca-extension path.
3. Server address (owner): on the new card, "Details", row "Extension
   options". Field "API base URL": paste the live service URL (agent reads
   it with gcloud run services describe, format value(status.url)). Button
   "Save". Chrome asks to allow access to that site: "Allow". Expected:
   status line under the field reads saved; badge stays empty until the
   device is registered.
4. Register (owner copies, agent registers): same options page, section
   "This device", button "Copy public key". Then on the Mac:
   bash ~/hca-deploy/scripts/store_device.sh register "<LABEL>" (the label
   audit rows show, for example the browser and machine name)
   Expected: first line BEGIN PUBLIC KEY, a JSON device record, HTTP 201.
   Note the device_id (dev_...). 409 means the key is already registered:
   run store_device.sh list and take the id from there.
5. Google link (owner): options page, button "Sign in with Google"
   (section "Google account"). Chrome shows the Google account chooser for
   the OAuth client in the manifest (the store client from section 2);
   pick the listed owner account. Expected: the page shows the linked
   email. Agent verifies: store_device.sh list shows google_email on the
   new id.
6. Promote (agent): store_device.sh promote <new dev id>. Expected HTTP
   200, priority 200 (the Chrome rank of the P14 ladder; the Notifier
   stays 10).
7. Proof (agent): post a proof ask with a temporary agent key, wait for
   the 90 s fallback window (the Notifier ranks first), the owner answers
   in the new popup, the agent reads the record (surface browser_overlay,
   device id = the new one), cancels nothing, revokes the key.
8. Retire the old device (agent): store_device.sh revoke dev_ddea0aae
   (the unpacked load's record). Expected HTTP 200 with revoked_at set.
9. Remove the unpacked card (owner): chrome://extensions, the card
   labelled "Unpacked extension" with the ~/hca-extension path, button
   "Remove", confirm "Remove". Expected: one HCA card remains, ID
   icaocoj....
10. Drop the manifest key (agent, PR for the owner's merge): remove "key"
   from extension/manifest.json, update extension/tests/pack.test.js
   (the repo manifest no longer carries a key; --keep-key becomes a
   no-op), bump "version" to 0.2.1 so the next store upload is accepted.
   The private half ~/hca-extension-key.pem on the Mac and the folder
   ~/hca-extension can be deleted after the proof. Old item
   jmpjjfhgggjmbebejaapchodjacmbmpm never existed on the store; nothing
   to unpublish. The old Chrome Extension OAuth client (item id
   jmpjjf...) in the Console can be deleted by the owner once the proof
   is recorded.
11. Record: docs/deploys/<date>_p17_day3_store_install.md with the proof
   request id, device ids, revision, and the dashboard status.

## 6. Updates after P17

Bump "version" in extension/manifest.json in the PR that changes the
extension (the store refuses a repeated version). After merge, step 0
then dashboard: item page, Package tab, "Upload new package", pick the
zip, then "Submit for review". Day 3 may replace this with a Publish API
step in Cloud Build (hca-cws-refresh-token in Secret Manager).
