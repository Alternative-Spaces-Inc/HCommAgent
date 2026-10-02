# 2026-10-02: HCA-P7, Google sign-in on top of the device key

## Decision (owner, 2026-10-02)

Consent screen: External, testing mode, in the dedicated project. Reason
given: non-Workspace Google accounts must be addable as test users.
Consequences accepted: refresh tokens expire every 7 days until the app is
verified (irrelevant to this design, which only uses short-lived access
tokens at link time), and the test-user list is capped at 100.

Verified from the terminal after the IAP API was enabled (owner approved):
brand `Human Collections Agent`, support email the owner's, orgInternalOnly
unset. gcloud warns the oauth-brands and oauth-clients reads are deprecated
and the client list came back empty, so the Chrome Extension OAuth client
is read from the Console only.

## What this change does

- `extension/manifest.json`: `identity` permission and an `oauth2` block
  carrying the public client id of the Chrome Extension OAuth client bound
  to extension ID `jmpjjfhgggjmbebejaapchodjacmbmpm`, scopes `openid` and
  `email`.
- Options page: a **Sign in with Google** button. `chrome.identity.getAuthToken`
  yields an access token; it goes to `POST /v1/devices/self/google-link` over a
  device-signed call. The page shows the linked account from
  `GET /v1/devices/self`.
- Server (`api/app/googleauth.py`): verifies the token at Google's tokeninfo
  endpoint and accepts it only when `aud` or `azp` equals `HCA_GOOGLE_CLIENT_ID`,
  `email_verified` is true, the token is unexpired and, when `HCA_OWNER_EMAILS`
  is set, the email is on the list. Missing client id fails closed (503).
- Device row gains `google_sub`, `google_email`, `google_linked_at`
  (migration `f3a4b5c6d7e8`).
- `HCA_REQUIRE_GOOGLE_LINK=1` makes the human routes (inbox, inbox/stream,
  answer, audit) refuse an unlinked device with 403. Default off, so this
  deploy cannot lock out the current device; the runbook turns it on after
  the owner's device shows `google_email`.
- `cloudbuild.yaml`: three plain substitutions (`_GOOGLE_CLIENT_ID`,
  `_OWNER_EMAILS`, `_REQUIRE_GOOGLE_LINK`) become env vars on the service.
  None is a secret; the client id is public by design.

Why an access token and not an ID token: `chrome.identity.getAuthToken` is
the only flow that works inside an extension without a redirect page, and
tokeninfo returns the same audience and account claims for it. The token
is used once, at link time, and never stored by the server.

## Tests

`api/tests/test_google_link.py` (9 tests): link records identity and
self view shows it, signature required, token for another client refused
(403), unverified email refused, expired or invalid token 401, owner list
gates the account, missing client id 503, require flag blocks an unlinked
device until it links while the link route stays open, flag off leaves an
unlinked device working. Suite: 66 passed. Extension tests: 21 passed.

## First deploy attempt

Build 596d79a5 (main 995dc2a5, REQ=0) failed at the deploy step: the
`--set-env-vars` list used `@` as its custom separator and the owner email
carries an `@`, so gcloud read `alternative-spaces.com` as a bad dict entry.
Fixed by switching the separator to `:` (none of the three values contains
one). Tests, image, push and the migration job (f3a4b5c6d7e8 applied) had
already succeeded in that build.

## Deploy and proof (after merge)

1. Deploy from the Mac with the P7 substitutions block in the runbook,
   `REQ=0`.
2. Reload the unpacked extension from `~/hca-extension` after copying the
   new `extension/` files there (the folder is outside the deploy clone).
3. Options page, **Sign in with Google**, pick the owner account, Allow.
   Expected status line: `Linked to <owner email> since <time>`.
4. `GET /v1/devices` (admin bearer) shows `google_email` on
   `dev_ddea0aae...`.
5. Redeploy with `REQ=1`; a proof ask answered from the linked device is
   the P7 proof record.

## Result (2026-10-02, 15:4x to 15:55 UTC)

| Item | Value |
| --- | --- |
| Merged | PR 23 squashed as 995dc2a5; PR 24 (separator fix) squashed as d13f221b |
| Build | 02468081-8366-422f-9e0f-1e1be1d0064f, SUCCESS, 2m44s, image tag d13f221b |
| Revision, REQ=0 | hca-api-service-00005-8dg; env HCA_GOOGLE_CLIENT_ID (public), HCA_OWNER_EMAILS (owner account), HCA_REQUIRE_GOOGLE_LINK=0 |
| Link | options page Sign in with Google, owner account, 15:51:40 UTC; GET /v1/devices shows google_email on dev_ddea0aae221d2e3b9cedd9e4c05e260e |
| Revision, REQ=1 | hca-api-service-00006-tsf, same image, set with gcloud run services update --update-env-vars (no rebuild; the runbook block with REQ=1 is the equivalent full path) |
| Unsigned probes | GET /v1/devices/self 401, POST /v1/devices/self/google-link 401, /health 200 |

## Proof (read with the agent key of caller agent-p7-proof)

Request 7afbf8e45d05444393cca6ff26bb6ef9, title "P7 proof: Google-linked device, which letter?", control radio, options a and b, suggested a, expiry_policy hold, deliver_to poll.

- state: answered
- value: a
- answered_by: Al MacBook Chrome
- answered_at: 2026-10-02T15:55:35Z
- surface: browser_overlay
- device: dev_ddea0aae221d2e3b9cedd9e4c05e260e

Answered from the popup with HCA_REQUIRE_GOOGLE_LINK=1 live, so the route
accepted the device only because its row carries the owner's Google
account. The revoked device dev_a90693e0 stays unlinked.

## Extension on the Mac

The unpacked folder outside the deploy clone was synced from the merged
extension/ tree with rsync (manifest.json excluded), then the repo manifest
was written back with the folder's host_permissions entry kept. Result:
permissions storage, notifications, alarms, identity; oauth2 client id
present; manifest key present (fixed ID). Reloaded from chrome://extensions.

## Open items after P7

- host_permissions is still a per-install patch (the service URL is not in
  the repo manifest). Candidate: a build step that writes it from the
  runbook value.
- Consent screen stays in testing mode; the link flow only needs a fresh
  access token at link time, so the 7-day refresh limit does not affect it.
- The REQ flag is per service revision; a future full rebuild must pass
  _REQUIRE_GOOGLE_LINK=1 or it silently returns to 0. Runbook block says so.
