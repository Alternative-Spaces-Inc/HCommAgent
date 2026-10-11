#!/usr/bin/env bash
# HCA-P17 Day 3: move the owner's Chrome device from the unpacked load to
# the Chrome Web Store install (Path B: the store item has a new extension
# ID, so the store install generates a new device key and needs its own
# device record).
#
# Runs on the owner's Mac (Terminal or the bridge shell) where gcloud is
# signed in and the project is the configured default. The admin token is
# read from Secret Manager inside the process and never printed. Nothing
# here reads a private key: the extension exports only its public key.
#
# Usage:
#   store_device.sh register <LABEL>        clipboard holds the PEM copied
#                                           with "Copy public key" on the
#                                           store install's options page;
#                                           registers it, prints the id
#   store_device.sh promote <DEVICE_ID>     priority 200 (Chrome rank in
#                                           the P14 ladder; Notifier is 10)
#   store_device.sh list                    all devices, id label priority
#                                           google_email revoked
#   store_device.sh revoke <DEVICE_ID>      revoke a device (the old
#                                           unpacked load's record)
#   store_device.sh get <PATH>              read an admin page, for example
#                                           get /v1/internal/metrics (P20)
#
# Exit codes: 0 on the expected HTTP code (201, 200), 1 otherwise. A 409 on
# register means the key is already registered; run list to find its id.
set -euo pipefail

REGION="${HCA_REGION:-us-west1}"
SERVICE="${HCA_SERVICE:-hca-api-service}"

project() { gcloud config get-value project 2>/dev/null; }
service_url() { gcloud run services describe "$SERVICE" --project="$(project)" --region="$REGION" --format='value(status.url)'; }
admin_call() {
  # $1 method, $2 path, $3 json body or empty. Prints body then "HTTP <code>".
  local T
  T="$(gcloud secrets versions access latest --secret=hca-admin-token --project="$(project)")"
  if [ -n "${3:-}" ]; then
    curl -sS -w '\nHTTP %{http_code}\n' -X "$1" -H "Authorization: Bearer ${T}" -H "Content-Type: application/json" --data-binary "$3" "$(service_url)$2"
  else
    curl -sS -w '\nHTTP %{http_code}\n' -X "$1" -H "Authorization: Bearer ${T}" "$(service_url)$2"
  fi
  unset T
}
expect_code() {
  # $1 output, $2 expected code
  if printf '%s\n' "$1" | tail -1 | grep -q "^HTTP $2\$"; then return 0; fi
  echo "store_device: expected HTTP $2" >&2; return 1
}

cmd="${1:-}"; shift || true
case "$cmd" in
  register)
    label="${1:?label required}"
    pem="$HOME/hca-store-device.pem"
    pbpaste > "$pem"
    head -1 "$pem" | grep -q 'BEGIN PUBLIC KEY' || { echo "store_device: clipboard does not hold a public key PEM; click Copy public key on the options page again" >&2; exit 1; }
    body="$(python3 -c 'import json,sys; print(json.dumps({"label": sys.argv[1], "public_key_pem": open(sys.argv[2]).read(), "surface": "browser_overlay"}))' "$label" "$pem")"
    out="$(admin_call POST /v1/devices "$body")"
    printf '%s\n' "$out"
    expect_code "$out" 201
    ;;
  promote)
    id="${1:?device id required}"
    out="$(admin_call PATCH "/v1/devices/$id" '{"priority": 200}')"
    printf '%s\n' "$out"
    expect_code "$out" 200
    ;;
  get)
    # HCA-P20 Day 5: read any admin page, e.g. get /v1/internal/metrics
    admin_call GET "${1:?store_device: get needs a path, e.g. /v1/internal/metrics}"
    ;;
  list)
    out="$(admin_call GET /v1/devices)"
    printf '%s\n' "$out" | sed '$d' | python3 -c 'import json,sys; [print(d["device_id"], repr(d.get("label")), d.get("priority"), d.get("google_email"), "revoked" if d.get("revoked_at") else "active") for d in json.load(sys.stdin)]'
    printf '%s\n' "$out" | tail -1
    ;;
  revoke)
    id="${1:?device id required}"
    out="$(admin_call POST "/v1/devices/$id/revoke" "")"
    printf '%s\n' "$out"
    expect_code "$out" 200
    ;;
  *)
    sed -n '2,25p' "$0"; exit 1
    ;;
esac
