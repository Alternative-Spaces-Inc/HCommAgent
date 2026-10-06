# HCA P16: stable Mac signing identity, Day 1 and Day 2 record (2026-10-06)

Plan: docs/plans/HCA_P16_MAC_SIGNING_v1.md (PR 57, main 5eb297e6).
Code: this PR (branch p16/day1-identity): macos/scripts/make_signing_identity.sh,
bundle.sh identity selection and requirement print, macos/README.md section.

## Identity

Created 2026-10-06 19:35Z on the owner's Mac by make_signing_identity.sh
from the HCA Terminal session. `security find-identity -v -p codesigning`
went from "0 valid identities found" to one entry:
FC90E48A9C93983815CD248FD95C4C2DB7A8CB0A "HCA Notifier Local Signing".
Owner step observed: the admin dialog from `security add-trusted-cert`
(the script returned without error, so the trust setting was accepted).

Change from the plan: the key access grant is not a Keychain password typed
in the Terminal. `security import -T /usr/bin/codesign` plus the codesign
dialog on first use covers it, so the plan's red gate 2 became a dialog
and the plan's three owner steps shrank to at most three clicks.

## Signature

bundle.sh (44d0164) printed `signing: HCA Notifier Local Signing` and
`designated => identifier "com.alternativespaces.hca.notifier" and
certificate root = H"fc90e48a9c93983815cd248fd95c4c2db7a8cb0a"`.
`codesign -dvv`: Authority=HCA Notifier Local Signing, TeamIdentifier=not
set. The requirement names the identifier and the certificate, not a code
hash, which is the property the plan asked for.

## Acceptance: three relaunches, first signed call within 10 s

Times from the server log (Cloud Run request lines) against the `open`
timestamp printed in the Terminal. A Keychain dialog blocks the device key
read, so a signed call this soon after launch means no dialog was waited
on.

| Relaunch | Build | CDHash | Launched (UTC) | First signed call | Gap |
| --- | --- | --- | --- | --- | --- |
| 1 | 44d0164, first build under the identity | not recorded | 19:37:07 | 19:37:14 GET /v1/devices/self 200 (with inbox and stream) | 7 s |
| 2 | 44d0164, source touched, rebuilt | 5ece85cc... | 19:38:37 | 19:38:38 GET /v1/devices/self 200 | 1 s |
| 3 | 103002e (local empty commit so CFBundleVersion and CDHash change) | 309d5088... | 19:40:07 | 19:40:11 GET /v1/inbox 200 and GET /v1/inbox/stream 200 | 4 s |

Relaunch 3 is the real test: a different code hash under the same
identity, no prompt. Baseline for comparison is the P14 closing record,
finding 2 (ad-hoc rebuild prompts and the app cannot sign until the owner
clicks). Relaunch 3 showed no GET /v1/devices/self in the first minute,
only inbox and stream; not a P16 matter, noted for the app.

## Ask answered from the menu on the re-signed app

Key agent-p16-proof, prefix 32e476d9eb23, minted 19:43:33Z; ask
f90e489e5dad4ad380b9dd320a43f5f7 posted queued. The proof script crashed
after posting (keyword bug, fixed in the sandbox copy only), so the record
could not be read with the agent key; evidence is the app's action log and
the server log instead:

- app log (subsystem com.alternativespaces.hca.notifier, actions):
  13:28:49.597 PT "menu click from_menu on f90e489e", "sending menu
  from_menu", 13:28:49.701 "answered from_menu on f90e489e".
- server: 20:28:49.683Z POST /v1/requests/f90e489e.../answer 201 Created.

Key 32e476d9eb23 revoked 20:3xZ via the admin route. Live keys after:
the two smoke keys only (smoke-1790967512, smoke-1790969481).
Note: GET /v1/audit is a device-signed human route, not an admin route;
an admin token gets 401 there.

## Findings

1. The identity script runs end to end from the Terminal; one admin
   dialog is the whole owner cost. Rebuilds no longer prompt.
2. `gcloud auth login` piped through grep hung after the browser flow even
   though the credential was saved; kill the Python child. Run it with
   output to a file next time.
3. `gcloud builds list` without --region returned nothing (exit 0) for
   most of this session; `--region=us-west1` lists the builds. Use the
   region flag in every build read. Plan merge build: f47860cb SUCCESS for
   5eb297e6 (19:32:37Z), revision hca-api-service-00036-j9z created
   19:35:58Z, so live equals main.

## Not done

Developer ID, notarization, Secure Enclave key (see plan, Not in P16).
