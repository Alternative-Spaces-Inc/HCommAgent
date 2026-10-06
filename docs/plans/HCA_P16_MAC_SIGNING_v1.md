# HCA P16: stable Mac signing identity, plan v1 (2026-10-06)

## Why this and why now

The HCA Notifier is ad-hoc signed (macos/scripts/bundle.sh, `codesign
--sign -`). An ad-hoc signature has no certificate, so its designated
requirement is the code hash of that one build. The device key lives in the
login Keychain as a generic password item (KeychainKeyStore.swift) whose
access list names the app by that requirement. Every rebuild therefore
changes the app's identity in the Keychain's eyes, the first key read
prompts the owner ("HCANotifier wants to use your confidential
information"), and the app cannot sign requests until the owner clicks
Always Allow (P14 closing record, finding 2). Each phase rebuilds the app
several times, so this is a recurring red gate that a stable identity
removes. The owner selected this packet on 2026-10-06 06:2x PT over
per-device quiet hours and Chrome Web Store packaging.

## Decision: local self-signed code signing certificate, not Developer ID

The app runs only on the owner's Mac and is built there from the repo, so
Gatekeeper never evaluates it and notarization buys nothing (P13 plan,
Not in P13). What matters is a designated requirement that survives
rebuilds: `identifier "com.alternativespaces.hca.notifier" and certificate
leaf = H"<sha1>"`, which any certificate identity gives. A self-signed
Code Signing certificate in the owner's login Keychain does that at no
cost and without an Apple account. Developer ID (99 USD a year, Apple
account, notarization) is deferred until the app must run on a Mac that
did not build it.

## What ships

Scripts and docs only; no Swift change expected and no server change.

1. `macos/scripts/make_signing_identity.sh`: creates the identity from the
   Terminal (Rule 0: Terminal first, no Keychain Access clicking). Generates
   a P-256 key and a self-signed X.509 certificate with the Code Signing
   extended key usage (openssl), common name "HCA Notifier Local Signing",
   validity 10 years, imports the pair into the login Keychain
   (`security import` of a PKCS#12 with a random throwaway passphrase that
   never leaves the script), marks it trusted for code signing
   (`security add-trusted-cert -r trustRoot -p codeSign`, admin prompt,
   the one owner step), and grants codesign access to the key
   (`security set-key-partition-list -S apple-tool:,apple: -s`, login
   Keychain password prompt in the Terminal, second owner step). Prints
   the identity's SHA-1 and `security find-identity -v -p codesigning`.
   Idempotent: refuses to create a second certificate with the same name.
2. `macos/scripts/bundle.sh`: signs with `HCA_SIGN_IDENTITY` when set, else
   with the first "HCA Notifier Local Signing" identity found, else ad-hoc
   as today, and prints which. After signing, prints the designated
   requirement (`codesign -d -r- "$APP"`) so the proof reads it.
3. `macos/README.md`: the two owner prompts, what each means, and how to
   check the identity (`security find-identity`).
4. Runbook note under docs/deploys/ (the record): the Keychain item created
   under the ad-hoc identity is read once more under the new identity;
   that first read prompts one last time and Always Allow ends it. The
   script offers no migration because the app rewrites nothing; the ACL
   entry is added by the Keychain on Always Allow.

## Proof

1. Baseline: rebuild the current ad-hoc app, relaunch, and read the unified
   log for a SecurityAgent authorization and the first action line
   (expected: prompt, no signed request until the owner clicks).
2. Run make_signing_identity.sh; `security find-identity -v -p codesigning`
   lists exactly one HCA Notifier Local Signing identity.
3. bundle.sh with the identity; `codesign -dvv` shows Authority=HCA
   Notifier Local Signing and `codesign -d -r-` shows the identifier plus
   certificate leaf requirement. Relaunch, Always Allow once.
4. Change nothing, rebuild and relaunch twice more. Each time: no
   SecurityAgent prompt in the log, and the app's first signed call
   (GET /v1/devices/self 200 in the server log, or the app's action log
   line) lands within 10 s of launch. That is the acceptance test.
5. A real ask answered from the menu after the third relaunch (P15 path),
   record read with the proof key, key revoked.

## Not in P16

Developer ID, notarization, hardened runtime, auto-update, distribution
to any other Mac, moving the device key to the Secure Enclave (would also
end the prompt but changes the devicesig contract and needs an entitled
app), Chrome Web Store packaging, quiet hours.

## Red gates (owner)

1. Trust the certificate for code signing: macOS admin password prompt
   from `security add-trusted-cert` (a system dialog titled "security
   wants to make changes", Password field, Modify Settings button).
2. Allow codesign to use the new key: login Keychain password typed in the
   Terminal when `security set-key-partition-list` asks.
3. One final Keychain prompt on the first launch of the re-signed app:
   click Always Allow (bottom row of the dialog, leftmost of the three
   buttons: Always Allow, Deny, Allow).

## Days

| Day | Work | Proof |
| --- | --- | --- |
| 1 | make_signing_identity.sh, bundle.sh identity selection, README, run on the owner Mac | identity listed, bundle signed by it, DR read |
| 2 | two rebuilds without prompt, menu ask proof, record | log shows no SecurityAgent, action log within 10 s, record read, key revoked |
