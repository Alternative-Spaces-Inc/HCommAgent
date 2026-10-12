# HCA Notifier for macOS

HCA Notifier is a **developer-only** macOS 14 or later menu-bar client for
HCA API 0.3.x. Build it on your own Mac with Xcode 15 or later and Swift 5.9
or later. The package enables complete concurrency checking and treats
compiler warnings as errors. It is not a notarized public-download product.

## Build and install

From the repository root:

```sh
bash macos/scripts/bundle.sh
open "$HOME/hca-notifier/HCA Notifier.app"
```

The output contains the app, `HCA-Notifier-0.3.0-macos.zip`, and a SHA-256
checksum file. Verify an archive inside the output directory with
`shasum -a 256 -c HCA-Notifier-0.3.0-macos.zip.sha256`. Notifications require
the app bundle; running the bare executable does not work.

`HCA_VERSION` overrides the numeric marketing version and `HCA_BUILD_NUMBER`
overrides the numeric Apple build number. The default build number is the
Git commit count; the Git revision and whether the Mac source tree has
uncommitted changes are stored separately in the plist.
Release builds omit test-notification menu commands. Debug builds retain them.

Local signing defaults to ad-hoc; if a local `HCA Notifier Local Signing`
identity already exists, it is used instead. `HCA_SIGN_IDENTITY` can name an
existing identity. A consistent local identity avoids repeated Keychain
prompts after rebuilds. The optional `scripts/make_signing_identity.sh`
creates that local identity and changes your login Keychain/trust settings;
read it before choosing to run it. Neither this script nor a successful
local signature provides Developer ID signing, hardened runtime,
notarization, public distribution, or an automatic updater.

## Configure and register

1. Start the API using the repository's local quickstart or use an operator's
   authorized server. Choose **Configure API origin…** in the Mac menu.
   Enter an HTTPS origin such as `https://api.example.com`, with no path,
   credentials, query, or fragment. Review the explicit trust confirmation.
   API and Google token requests refuse every redirect.
2. Choose **Copy device public key (PEM)** and **Copy device id**. Give only
   the public key to your API operator. The operator registers it with
   `POST /v1/devices`, using their admin bearer credential and a body such as
   `{"label":"My Mac","surface":"macos_notification","public_key_pem":"<PEM>","tenant_id":"default"}`.
   The app never needs the admin credential. The public key is also saved at
   `~/Library/Application Support/HCA Notifier/device_public_key.pem`.
3. For a server requiring Google linking, configure the **Desktop OAuth
   client** whose public client ID the operator includes in the server's
   Google client allowlist:

   ```sh
   defaults write com.alternativespaces.hca.notifier googleClientId 'YOUR_DESKTOP_CLIENT_ID'
   ```

   If the operator's Desktop client requires its installed-app client
   secret, set `googleClientSecret` in the same defaults domain. This value
   is not a confidential server secret; do not substitute an admin token or
   any server credential. Choose **Link Google account** and sign in using
   a current authorized tenant owner's account. Callback state is checked
   before success or error, duplicate keys are rejected, fragmented reads
   are bounded, each connection has a five-second deadline, and the complete
   browser flow expires after five minutes. Tokens are exchanged in an
   ephemeral session and are not persisted by the client.
4. Choose **Refresh inbox now**. The device row reports registration and
   linking status. Select this Mac's surface priority if needed.

Local HTTP is permitted only after explicitly enabling development mode:

```sh
defaults write com.alternativespaces.hca.notifier allowLocalHTTPDevelopment -bool true
```

Only `localhost`, `127.0.0.1`, and `[::1]` are accepted with HTTP. Choose the
local origin in the menu, and disable the setting when finished. External
`defaults` changes to `apiBaseURL` take effect on the next launch and require
trust confirmation when the origin changes. The running client freezes its
trusted origin; switching through the menu cancels old requests and clears
old asks, notifications, retry keys, and the event cursor.

## Decisions and privacy

| Control | Mac support |
| --- | --- |
| `approve_deny` | Menu choice with complete context and explicit confirmation |
| `radio` | Menu choices with full labels available in review and tooltips |
| `checkbox`, `dropdown`, `chat`, `confirm_rows` | Notification and context view; answer using the Chrome extension |

Notifications hide decision details by default. **Hide decision details in
notifications** controls this setting; disabling it may expose titles,
caller names, suggested choices, and option labels in Notification Center or
on a locked screen, depending on your system settings. Red-gate asks always
require review of resource, change, impact, exact command, rollback, and all
option explanations before a Mac answer. Suggestions are descriptive; no
answer is sent merely by opening the menu or receiving a notification.

With details enabled, ordinary approve/deny and radio asks with at most four
options can show native action buttons. Tapping a button refreshes the
current authorized inbox, validates the stored origin and displayed option
mapping, and opens full-context confirmation. Stale, changed, expired, or
revoked asks cannot be answered from old notification metadata. Categories
are pruned as asks leave the inbox and are capped at 64 dynamic categories;
additional notifications use the alert category and remain answerable in
the menu. Notification mappings recover after restart from the server.

The app keeps pending asks in memory. UserDefaults stores the trusted origin,
preferences, and last event cursor; the login Keychain stores the P-256
private key with this-device-only accessibility. System notifications retain
their displayed content, request ID, origin, and option IDs until removed.
A server 401/403 or explicit stream revocation clears local pending asks and
notifications. Stream reset signals trigger inbox reconciliation. The app
polls every 60 seconds as a recovery path; it follows every bounded inbox
page and rejects repeated cursors. A failed read prevents a notification
answer. Failed submissions retain the ask visibly, and retries within the
same running session reuse the idempotency key for the same decision.

## Upgrade and uninstall

Quit the app before rebuilding it in the same path. A rebuild preserves the
Keychain device identity and trusted configuration. Keep the same local
signing identity when possible; a changed ad-hoc signature may require a
Keychain prompt. If launch-at-login is enabled, reopen the new bundle once
and the app loads or restarts its existing user LaunchAgent. Re-registering
the same public key is idempotent; a revoked key requires rotation.

**Launch at login** is off until selected. It installs the user LaunchAgent
`~/Library/LaunchAgents/com.alternativespaces.hca.notifier.plist` for this
bundle's executable. Disable it in the menu before uninstalling; this can
stop the launchd instance immediately. macOS background-item permission is
managed in System Settings → General → Login Items and Extensions.

To uninstall, disable launch-at-login, quit, remove the app/output directory,
and revoke the device through your API operator. For complete local cleanup,
remove the `com.alternativespaces.hca.notifier` defaults domain, the
`HCA Notifier` Application Support directory, and the generic password item
whose service is `com.alternativespaces.hca.notifier` and account is
`device-key` using Keychain Access. Clear remaining HCA notifications in
Notification Center. Removing the Keychain key changes the device ID on the
next launch and requires registration and Google linking again.

## Validation

```sh
cd macos
swift test
swift build -c release --product HCANotifier
```

Tests cover signing vectors, origin/redirect rules, duplicate OAuth fields,
notification recovery, material context, stream reset/revocation, signed
pagination, retry keys, overlapping refreshes, cancellation, and real
loopback TCP fragmentation, authenticated errors, timeouts, and cancellation.
They need a Mac with loopback socket access, but no Google credentials,
production account, notification permission, or signing identity. Native
notification presentation, accessibility with assistive tools, real Google
OAuth, launch-at-login behavior, and local Keychain dialogs still require
manual checks on the target Mac; passing logic tests does not establish
notarized release readiness.
