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
if [[ "$OUT" != /* ]]; then OUT="$PWD/$OUT"; fi
APP="$OUT/HCA Notifier.app"
cd "$HERE"
VERSION="${HCA_VERSION:-0.3.0}"
BUILD_NUMBER="${HCA_BUILD_NUMBER:-$(git -C "$HERE" rev-list --count HEAD 2>/dev/null || echo 1)}"
REVISION="$(git -C "$HERE" rev-parse HEAD 2>/dev/null || echo unknown)"
DIRTY=false
if [ -n "$(git -C "$HERE" status --porcelain -- . 2>/dev/null)" ]; then DIRTY=true; fi
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "HCA_VERSION must be a numeric major.minor.patch version" >&2; exit 1
fi
if ! [[ "$BUILD_NUMBER" =~ ^[1-9][0-9]{0,3}(\.[0-9]{1,2}){0,2}$ ]]; then
  echo "HCA_BUILD_NUMBER must be a positive numeric Apple build number (up to 4.2.2 digits)" >&2; exit 1
fi
SWIFT_ARGS=()
if [ -n "${HCA_SWIFT_SCRATCH_PATH:-}" ]; then SWIFT_ARGS+=(--scratch-path "$HCA_SWIFT_SCRATCH_PATH"); fi
if [ -n "${HCA_SWIFT_CACHE_PATH:-}" ]; then SWIFT_ARGS+=(--cache-path "$HCA_SWIFT_CACHE_PATH"); fi
if [ -n "${HCA_SWIFT_CONFIG_PATH:-}" ]; then SWIFT_ARGS+=(--config-path "$HCA_SWIFT_CONFIG_PATH"); fi
if [ -n "${HCA_SWIFT_SECURITY_PATH:-}" ]; then SWIFT_ARGS+=(--security-path "$HCA_SWIFT_SECURITY_PATH"); fi
swift build -c release --product HCANotifier "${SWIFT_ARGS[@]}"
BIN="$(swift build -c release --show-bin-path "${SWIFT_ARGS[@]}")/HCANotifier"
if [ -d "$APP" ]; then rm -r "$APP"; fi
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/HCANotifier"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>HCA Notifier</string>
  <key>CFBundleDisplayName</key><string>HCA Notifier</string>
  <key>CFBundleIdentifier</key><string>com.alternativespaces.hca.notifier</string>
  <key>CFBundleExecutable</key><string>HCANotifier</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${BUILD_NUMBER}</string>
  <key>HCARepositoryRevision</key><string>${REVISION}</string>
  <key>HCARepositoryDirty</key><${DIRTY}/>
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
codesign --verify --deep --strict "$APP" && echo "codesign verify: ok"
echo "bundle: $APP"
echo "version: ${VERSION} (${BUILD_NUMBER}) revision ${REVISION} dirty ${DIRTY}"
ARCHIVE="$OUT/HCA-Notifier-${VERSION}-macos.zip"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ARCHIVE"
(cd "$OUT" && shasum -a 256 "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256")
echo "developer-only archive: $ARCHIVE"
echo "checksum: $ARCHIVE.sha256"
