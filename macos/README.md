# HCA Notifier (macOS)

Rung 3 of the surface ladder: native macOS notifications with action
buttons for pending asks, on the same API as the Chrome extension. Plan:
docs/plans/HCA_P13_MACOS_NOTIFICATION_v1.md.

Build on a Mac with Xcode (license accepted):

    bash macos/scripts/bundle.sh          # writes ~/hca-notifier/HCA Notifier.app
    open ~/hca-notifier/HCA\ Notifier.app

Tests (pure logic, no bundle needed):

    cd macos && swift test

The bundle is ad-hoc signed for the owner's own Mac; notifications will not
post from the bare executable.
