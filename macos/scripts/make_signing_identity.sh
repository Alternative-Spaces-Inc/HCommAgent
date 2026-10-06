#!/bin/bash
# HCA-P16: create the local self-signed code signing identity used by
# bundle.sh, so rebuilds of HCA Notifier keep one designated requirement
# and the Keychain stops prompting for the device key on every rebuild.
# Usage: bash macos/scripts/make_signing_identity.sh
# Owner steps while it runs (both are macOS dialogs, nothing typed here):
#   1. "security wants to make changes": admin password, Modify Settings.
#   2. On the first codesign with the identity (bundle.sh, not this script):
#      "codesign wants to sign using key ...": click Always Allow.
# Idempotent: a second run finds the identity and exits 0.
set -euo pipefail
NAME="HCA Notifier Local Signing"
OPENSSL=/usr/bin/openssl   # the system LibreSSL writes a PKCS#12 that security(1) imports
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

if security find-identity -v -p codesigning 2>/dev/null | grep -q "$NAME"; then
  echo "identity already present:"
  security find-identity -v -p codesigning | grep "$NAME"
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/cert.cnf" <<CNF
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = $NAME
O = Alternative Spaces
[v3]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
subjectKeyIdentifier = hash
CNF

"$OPENSSL" genrsa -out "$TMP/key.pem" 2048 2>/dev/null
"$OPENSSL" req -x509 -new -key "$TMP/key.pem" -out "$TMP/cert.pem" -days 3650 -sha256 -config "$TMP/cert.cnf"
PASS="$("$OPENSSL" rand -hex 16)"   # throwaway, dies with $TMP
"$OPENSSL" pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -out "$TMP/identity.p12" -name "$NAME" -passout "pass:$PASS"

# Import key plus certificate; -T lets codesign and security use the key
# (macOS still asks once, Always Allow, the first time codesign signs).
security import "$TMP/identity.p12" -k "$KEYCHAIN" -P "$PASS" -T /usr/bin/codesign -T /usr/bin/security >/dev/null
# Trust the self-signed certificate for code signing in the owner's trust
# settings (admin dialog; the one owner step of this script).
security add-trusted-cert -r trustRoot -p codeSign -k "$KEYCHAIN" "$TMP/cert.pem"

echo "created:"
security find-identity -v -p codesigning | grep "$NAME"
echo "sha1: $("$OPENSSL" x509 -in "$TMP/cert.pem" -noout -fingerprint -sha1 | cut -d= -f2 | tr -d :)"
