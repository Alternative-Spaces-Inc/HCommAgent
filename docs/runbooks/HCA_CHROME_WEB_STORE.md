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
  click its name, change "Item ID" to the new store ID, "Save". If the
  field is read-only, "+ Create credentials", "OAuth client ID", type
  "Chrome Extension", the new Item ID; the agent then writes the new
  client_id into extension/manifest.json oauth2 and adds it to the
  trigger's _GOOGLE_CLIENT_IDS.
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

Privacy tab: Single purpose: "Shows decisions sent by the owner's own
server and records the owner's answer." Permission justifications:
identity: "Google sign-in proves the device belongs to a listed owner
account; openid and email scopes only." notifications: "Shows a desktop
notification for each new decision." alarms: "Polls the owner's server
when the event stream is down." storage: "Keeps the device key, the
server address and unsent answers." Host permission (optional, any
https): "Site access is requested at runtime for the one server address
the owner enters; no other site is read." Remote code: "No, this item
does not use remote code." Data usage: tick "Authentication information"
and "Personally identifiable information" (the verified email), tick the
three certifications (not sold, not used outside the single purpose, not
used for creditworthiness). Privacy policy URL: the live service URL
plus /privacy (the agent reads it from gcloud run services describe and
pastes it into the chat for the owner to copy).

Distribution tab: Visibility "Unlisted". Payments "Free". Regions all.

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
