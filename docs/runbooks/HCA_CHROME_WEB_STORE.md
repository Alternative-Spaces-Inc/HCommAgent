# HCA Chrome Web Store runbook (HCA-P17)

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
test asserts (version, version_name, key_present true, files 13, sha256);
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

## 4. Submit (owner, red gate)

Top right "Submit for review". Dialog: leave "Publish automatically after
it has passed review" ticked (Unlisted means only link holders see it).
Click "Submit". Status column shows "Pending review". Review takes hours
to days; the agent polls the status with the Publish API later (Day 3) or
the owner reads the status badge on request.

## 5. Install from the store and retire the unpacked card (owner, Day 3)

When status is "Published": open the item link (Distribution tab shows
it) in Chrome, click "Add to Chrome", "Add extension". Then
chrome://extensions: the store install appears with the same ID (Path A),
so Chrome replaces the unpacked card; if two cards show, click "Remove"
on the one labelled "Unpacked extension" with the folder path. The agent
posts a proof ask and reads the record.

## 6. Updates after P17

Bump "version" in extension/manifest.json in the PR that changes the
extension (the store refuses a repeated version). After merge, step 0
then dashboard: item page, Package tab, "Upload new package", pick the
zip, then "Submit for review". Day 3 may replace this with a Publish API
step in Cloud Build (hca-cws-refresh-token in Secret Manager).
