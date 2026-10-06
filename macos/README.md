# HCA Notifier (macOS)

Rung 3 of the surface ladder: native macOS notifications with action
buttons for pending asks, on the same API as the Chrome extension. Plan:
docs/plans/HCA_P13_MACOS_NOTIFICATION_v1.md.

Build on a Mac with Xcode (license accepted):

    bash macos/scripts/bundle.sh          # writes ~/hca-notifier/HCA Notifier.app
    open ~/hca-notifier/HCA\ Notifier.app

Tests (pure logic, no bundle needed):

    cd macos && swift test

Notifications will not post from the bare executable; the bundle is required.

## Signing (HCA-P16)

bundle.sh signs with the local identity "HCA Notifier Local Signing" when it
exists, else ad-hoc. Create it once:

    bash macos/scripts/make_signing_identity.sh

Two macOS dialogs appear, one on this script and one on the next bundle.sh:

1. "security wants to make changes": admin password, then Modify Settings
   (trusts the self-signed certificate for code signing).
2. "codesign wants to sign using key HCA Notifier Local Signing in your
   keychain": click Always Allow (lets codesign use the key silently).

A certificate identity gives the app one designated requirement across
rebuilds, so the Keychain's device-key item asks once more after the first
re-signed launch (Always Allow) and never again. Check the identity with
`security find-identity -v -p codesigning`; force one with HCA_SIGN_IDENTITY.
Developer ID and notarization are not used: the app runs only on the Mac
that builds it.
