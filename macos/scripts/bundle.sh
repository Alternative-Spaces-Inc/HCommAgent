#!/bin/bash
# Build HCA Notifier and assemble a signed .app bundle.
# Signing (HCA-P16): HCA_SIGN_IDENTITY if set, else the "HCA Notifier Local
# Signing" identity when present (macos/scripts/make_signing_identity.sh),
# else ad-hoc. A certificate identity keeps the designated requirement
# stable across rebuilds, so the Keychain stops prompting for the device key.
# Usage: bash macos/scripts/bundle.sh [output_dir]   (default ~/hca-notifier)
# UNUserNotificationCenter refuses a bare executable, so the bundle is required.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$HOME/hca-notifier}"
APP="$OUT/HCA Notifier.app"
cd "$HERE"
swift build -c release --product HCANotifier
BIN="$(swift build -c release --show-bin-path)/HCANotifier"
if [ -d "$APP" ]; then rm -r "$APP"; fi
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/HCANotifier"
VERSION="$(git -C "$HERE" rev-parse --short HEAD 2>/dev/null || echo dev)"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>HCA Notifier</string>
  <key>CFBundleDisplayName</key><string>HCA Notifier</string>
  <key>CFBundleIdentifier</key><string>com.alternativespaces.hca.notifier</string>
  <key>CFBundleExecutable</key><string>HCANotifier</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
IDENTITY="${HCA_SIGN_IDENTITY:-}"
if [ -z "$IDENTITY" ] && security find-identity -v -p codesigning 2>/dev/null | grep -q "HCA Notifier Local Signing"; then
  IDENTITY="HCA Notifier Local Signing"
fi
if [ -n "$IDENTITY" ]; then
  codesign --force --sign "$IDENTITY" --identifier com.alternativespaces.hca.notifier "$APP"
  echo "signing: $IDENTITY"
else
  codesign --force --sign - --identifier com.alternativespaces.hca.notifier "$APP"
  echo "signing: ad-hoc (no HCA Notifier Local Signing identity found)"
fi
echo "requirement: $(codesign -d -r- "$APP" 2>&1 | grep '^designated' | cut -c1-160)"
echo "bundle: $APP"
echo "version: ${VERSION}"
