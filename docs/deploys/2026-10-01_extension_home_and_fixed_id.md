# 2026-10-01: extension home folder, fixed extension ID, device re-registration

## Why

The first-deploy runbook rebuilds the deploy clone from scratch (`rm -rf` then
`git clone`). The browser extension was loaded unpacked from inside that clone,
so the deploy on 2026-10-01 silently reverted the manifest patch
(`host_permissions` back to `[]`); the loaded copy kept working only because
Chrome had not reloaded it. Separately, an unpacked extension's ID is derived
from its folder path, and the P7 OAuth client (Google sign-in through
`chrome.identity`) needs an ID that does not move.

## What changed

1. The extension now lives in its own folder on the owner's Mac, outside the
   deploy clone: `~/hca-extension`, copied from main `92a3ec7`.
2. An RSA 2048 key pair was generated on the owner's Mac at
   `~/hca-extension-key.pem` (mode 600, never leaves the Mac, never committed).
   Its public half is the manifest `key` field, which fixes the extension ID at
   `jmpjjfhgggjmbebejaapchodjacmbmpm` for every unpacked load of this manifest.
   This commit adds that `key` to `extension/manifest.json` so the ID is the
   same for any clone. The `key` value is public by construction.
3. `host_permissions` stays a runbook patch (the API URL is a deployment value,
   not repo content). Note for the next extension change: request the host
   permission at runtime from the options page so no manifest patch is needed.
4. The old install was removed in Chrome, which destroys its device key. Its
   registration `dev_a90693e0d5e15c341aefe6ce38f6275f` was revoked at
   2026-10-01T20:49:08Z. The new install registered
   `dev_ddea0aae221d2e3b9cedd9e4c05e260e` (label "Al MacBook Chrome", surface
   browser_overlay, HTTP 201 at 20:47:28Z). Device list after: exactly one
   active device.

## Proof, take 4

Request `416be8933d334c6e97c0f33611827e53`, title "New install proof, take 4:
which letter?", radio A/B, suggested B. Answered in the Chrome popup.

| field | value |
| --- | --- |
| state | answered |
| value | b |
| answered_by | Al MacBook Chrome |
| answered_at | 2026-10-01T20:54:58Z |
| surface | browser_overlay |
| device | dev_ddea0aae221d2e3b9cedd9e4c05e260e |

Server: Cloud Run revision hca-api-service-00004, main `754046d3` (unchanged
since the P6 proof; this record is docs plus the manifest `key`).

## Owner steps that were needed (for the runbook)

- chrome://extensions, Developer mode on, Load unpacked, folder `~/hca-extension`.
- On the Details page, Site access: the API host row's toggle was off after
  load and had to be switched on by hand.
- Extension options: paste the API base URL, Save, Copy public key.
- Register the device with the admin token (POST /v1/devices).

## Next

P7: OAuth consent screen and a Chrome Extension OAuth client for ID
`jmpjjfhgggjmbebejaapchodjacmbmpm` in the dedicated project (Console steps,
owner only), then `oauth2` block plus `identity` permission in the manifest
and server-side ID token validation.
