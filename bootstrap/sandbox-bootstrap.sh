#!/usr/bin/env bash
# HCA sandbox-bootstrap.sh - thin caller into HCA bootstrap source.
set -u

CLONE_TIMEOUT_SECONDS="${SANDBOX_CLONE_TIMEOUT:-120}"

if [ "${1:-}" = "--selftest" ]; then
  echo "SANDBOX BOOTSTRAP THIN CALLER SELFTEST: GREEN - caller present, forwards to HCA seat_sandbox_bootstrap.sh"
  exit 0
fi

is_hca_repo() {
  [ -n "$1" ] && [ -f "$1/bootstrap/HCA_MANIFEST.yaml" ]
}

REPO_DIR="${SANDBOX_REPO_DIR:-}"

if ! is_hca_repo "$REPO_DIR"; then
  REPO_DIR=""
  for c in /app/conversations/*/hca-repo /app/conversations/*/repo "${HOME:-/root}/hca-repo" "${HOME:-/root}"/*/hca-repo; do
    if is_hca_repo "$c"; then
      REPO_DIR="$c"
      break
    fi
  done
fi

if [ -z "$REPO_DIR" ]; then
  REPO_DIR="${HOME:-/root}/hca-repo"
  TOKEN="${GITHUB_CLASSIC_TOKEN:-${GITHUB_TOKEN:-${GITHUB_READONLY_TOKEN:-}}}"
  
  if [ -d "$REPO_DIR" ] && [ -n "$(ls -A "$REPO_DIR" 2>/dev/null)" ]; then
    if ! is_hca_repo "$REPO_DIR"; then
      echo "MISSING: repo checkout ($REPO_DIR exists but lacks bootstrap/HCA_MANIFEST.yaml)"
      echo "RESULT: something was missing (see MISSING lines above)"
      exit 1
    fi
  elif [ -n "$TOKEN" ]; then
    if ! mkdir -p "$(dirname "$REPO_DIR")" 2>/dev/null; then
      echo "MISSING: repo checkout (cannot create $(dirname "$REPO_DIR"))"
      echo "RESULT: something was missing (see MISSING lines above)"
      exit 1
    fi
    timeout "$CLONE_TIMEOUT_SECONDS" git clone -q "https://x-access-token:${TOKEN}@github.com/Alternative-Spaces-Inc/HCommAgent.git" "$REPO_DIR" 2>/dev/null || {
      echo "MISSING: repo checkout (clone failed or exceeded ${CLONE_TIMEOUT_SECONDS}s)"
      echo "RESULT: something was missing (see MISSING lines above)"
      exit 1
    }
  else
    echo "MISSING: repo checkout and no GitHub token in environment"
    echo "RESULT: something was missing (see MISSING lines above)"
    exit 1
  fi
fi

if git -C "$REPO_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  if ! timeout "$CLONE_TIMEOUT_SECONDS" git -C "$REPO_DIR" fetch -q origin main 2>/dev/null; then
    echo "WARNING: fetch of origin main failed, continuing with the local checkout"
  fi
fi

export SANDBOX_REPO_DIR="$REPO_DIR"
exec bash "$REPO_DIR/bootstrap/seat_sandbox_bootstrap.sh" "$@"
