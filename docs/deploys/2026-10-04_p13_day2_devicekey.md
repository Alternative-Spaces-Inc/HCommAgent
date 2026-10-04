# 2026-10-04: HCA-P13 Day 2, Mac device key registered

## Result

The macOS app holds a P-256 device key in the owner's login Keychain and the
server trusts it: device `dev_41d7938bdf2af6d04f7dab5d767f4868`, label
"Al MacBook Notifier", surface macos_notification, registered
2026-10-04T21:39:42Z (HTTP 201), listed active next to the Chrome device.
No Google link yet (Day 4) and the app has no network path yet (Day 3), so
it cannot answer an ask; the signing contract is proven by shared vectors.

## Evidence

- Branch p13/day2-devicekey, head 1a32d60, PR 43 (on top of PR 42, main cbf9f15d, build bf36f2fc SUCCESS).
- `api/tests/vectors/devicesig_v1.json`: test-only key (registered nowhere),
  device id, four canonical cases (GET no body, GET with query, POST JSON,
  POST UTF-8 body) with recorded signatures.
- pytest `tests/test_devicesig_vectors.py`: 4 passed (device id, canonical
  strings and body hashes, recorded signatures verify and a tampered byte
  fails, fresh signature from the test key verifies).
- Owner Mac `cd macos && swift test`: Executed 12 tests, with 0 failures
  (7 shape tests plus 5 DeviceSignerVectorTests, which verify the Python
  signatures with CryptoKit and reproduce the device id and PEM from the raw
  scalar).
- Bundle version 1a32d60, running. Keychain generic password item present,
  service com.alternativespaces.hca.notifier, account device-key (attributes
  read with `security find-generic-password` without `-w`; the secret was
  not read).
- Device list after registration: dev_a90693e0 (Chrome, revoked),
  dev_ddea0aae (Chrome, active, Google linked), dev_41d7938b (Notifier, active).

## Design notes

- The private scalar is stored as a Keychain generic password
  (kSecAttrAccessibleWhenUnlockedThisDeviceOnly). Ad-hoc signatures change
  with each build, so macOS may prompt once per rebuild before the app may
  read the item; a stable signing identity would remove that (later).
- The public key is written to
  `~/Library/Application Support/HCA Notifier/device_public_key.pem` on each
  launch. Registration reads that file; the owner copies nothing. The first
  attempt asked the owner to copy a PEM from the menu and paste a Terminal
  block, which he could not place; that is a Rule 0 defect and the file
  replaces it. Registration ran from the bridge shell after the owner's
  approval widget, with the admin token read from Secret Manager inside the
  command.

## Next

Day 3: network path in the app (signed GET /v1/inbox, SSE stream with poll
fallback, signed POST answer, dedupe), then an owner proof: a real ask
answered from the macOS banner.
