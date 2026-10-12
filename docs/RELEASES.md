# Developer releases

The 0.3.0 candidate aligns the API and developer clients on the v1 route contract.
It has stricter validation/current authorization and authenticated webhook IDs.
Assess existing oversized records and receiver compatibility before upgrading.
[Change log](../CHANGELOG.md) and [Security](../SECURITY.md) describe supported updates.

## Build from an exact revision

Use a reviewed source revision with passing checks and a clean working tree.
Record its full commit, component versions, artifact hashes and validation results.
The commands below create local artifacts; they do not publish a release or update
an installed client/server.

Chrome packaging:

```sh
python scripts/pack_extension.py --out dist --commit "$(git rev-parse HEAD)"
```

This produces `extension-0.3.0-<commit7>.zip`, its SHA256 sidecar and JSON metadata.
The archive excludes tests, documentation, package tooling and private data.
The manifest development public key is omitted for a store upload; `--keep-key`
retains it only for unpacked development loads. Verify the configured extension ID
and OAuth audience separately before store distribution. [Chrome instructions](../extension/README.md)
explain registration, local custody, upgrade notices and uninstall behavior.

Developer Mac packaging on macOS 14+ with Swift 5.9+:

```sh
HCA_VERSION=0.3.0 bash macos/scripts/bundle.sh dist/macos
```

The script validates the numeric marketing/build versions, embeds the source
revision/dirty marker, compiles release code, verifies its local signature, and
creates `HCA-Notifier-0.3.0-macos.zip` plus a SHA256 sidecar. The default build number
is the source commit count; use an explicit, monotonically increasing
`HCA_BUILD_NUMBER` for separately released builds. Compiler/toolchain and signing
identity affect Mac bytes; a checksum identifies a particular artifact, not a
promise of identical signed output across Macs.

Verify checksums from their artifact directories before installation:

```sh
cd dist
shasum -a 256 -c extension-0.3.0-*.zip.sha256
cd macos
shasum -a 256 -c HCA-Notifier-0.3.0-macos.zip.sha256
```

The commands assume the output directories shown above. Check the embedded revision, versions and signatures against the release record.
A release tag/changelog and uploaded artifacts should identify the same reviewed
revision. No published tag or release is established merely by these instructions.

## Install, upgrade and remove

Chrome developer installation loads `extension/` unpacked. A store archive is for
the separately configured Web Store process. Update/reload from the reviewed source,
then review any queue-migration notices and sign-in state. Rotation changes identity;
ask the operator to revoke/register/link the appropriate key. Uninstall removes
Chrome local data but does not purge server decisions or audit records.

Unzip the developer Mac archive and open `HCA Notifier.app`. It uses local or ad-hoc
signing and may trigger macOS/Keychain permission prompts. Quit before replacing an
existing bundle, preserve the signing identity and device Keychain item where
appropriate, reopen once, and verify launch-at-login if enabled. Disable login
launch before uninstalling, quit, remove the bundle, revoke the server device and
follow [Mac cleanup](../macos/README.md#upgrade-and-uninstall) for defaults, Keychain
and notifications.

Public Mac distribution is not provided. Advertising it would require Developer ID,
hardened runtime/entitlements, notarization, installation/update-path verification
and a supported distribution process. A developer build must not be presented as
that public release. API deployment, database/key migration and traffic promotion
remain separate operator work; use [Operations](OPERATIONS.md).
