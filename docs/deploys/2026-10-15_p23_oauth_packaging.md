# P23 follow-up: separate unpacked and store OAuth packaging (2026-10-15)

## Problem and authorized scope

The [live rotation record](2026-10-15_p23_day3_device_rotation.md) identified a mismatch: the raw extension folder retained the unpacked public key but carried the store OAuth client. Reinstalling that folder made Google reject fresh linking. The owner selected **Fix OAuth packaging** after PR 29 merged on main `18f51c41cfe1e58a2a79593c4ed2b3e3487668b3`.

This correction changes product packaging and tests only. It does not create credentials, change Google clients or server audiences, deploy a service, install an extension, or repeat device registration or rotation. The remaining live old/new audit-identifier gap is separate and stays open.

## Corrected contract

- The raw `extension/manifest.json` uses the existing unpacked OAuth client matching the public key-derived extension ID.
- Build-only `extension/oauth-clients.json` records the existing distinct unpacked and store mappings; neither runtime archive includes that file.
- `--target store` is explicit in Cloud Build. It remains the CLI default, selects the store client and removes the development key without changing the store filename convention.
- `--target unpacked` and the legacy `--keep-key` alias retain the public key and select its matching unpacked client. The archive name has an `-unpacked` suffix, preventing collision with store output.
- Packaging validates the source key-derived extension ID, source OAuth pairing, mapping shapes and distinct target identities before creating output. An explicit store target combined with `--keep-key` fails.
- Build output identifies the selected target, expected extension ID and public client ID, in addition to the existing archive checksum and source revision.

See the [package target instructions](../../extension/README.md#package-targets) for installation/update boundaries. Keep the existing Chrome installation when updating files; removal can erase its registered device key.

## Local verification

- `npm test`: 87 passed, zero failures, including 22 packaging tests. Those cover both target outputs, legacy behavior, source immutability, exclusions and fail-closed invalid configurations.
- `npm run test:browser`: exit 0; all nine scenario groups passed, producing ten PASS receipts. These include actual IndexedDB, popup controls, mobile layout, offline delivery, reconnect, notifications and permission denial.
- `npm ci --ignore-scripts`: dependency audit found zero vulnerabilities.
- Ruff and Python compilation passed for `scripts/pack_extension.py`.
- No-dash, client-data, documentation links, dependency locks and product-isolation gates passed. Whitespace validation passed.

A compatibility build ran both the previous and corrected packers with the same runtime files and revision argument `18f51c41cfe1e58a2a79593c4ed2b3e3487668b3`. Both store archives had 16 files and SHA256 `6fb0d0b833e6d1a2c26b874853a82710e2579fbd8116343818176798b7882cd1`. This proves store artifact compatibility for that input and toolchain, not a new store upload or live deployment.

Local tests establish configuration consistency, not Google's future external client state. No new human sign-in test or installed-extension update is claimed for this packaging branch. CI results belong to the PR checks, not this local receipt.


## CI follow-up: exact public-key source exception

The first PR head `6be7663` failed the credential gate because the manifest's existing exact-file exception still held the old full-file checksum. Inspection confirmed that the only changed manifest value was the OAuth client, and the unchanged `key` decoded as an RSA 2048-bit public key. Only the existing `extension/manifest.json:generic-api-key:5` exception checksum and its review reason were refreshed; no scanner rule, pattern, fingerprint or broad allowlist was changed.

With the checksum-verified Gitleaks 8.30.1 binary, the local source and all fetched history scans passed. All four real-scanner regression tests then passed with `HCA_GITLEAKS_BINARY` explicitly configured, including failure on newly introduced/deleted historical credentials, file changes, reused exception locations and shallow history. These local results do not replace the final PR check results.
