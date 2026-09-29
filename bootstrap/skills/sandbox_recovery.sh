#!/usr/bin/env bash
# Provenance: factory-bootstrap/_skills/sandbox_recovery.sh (sha ca16334731040b98749600826b510b25a58e693d)
# RETIRED 2026-09-01 (canon-fix pass). NEUTRALIZED: this script exits before installing or
# authenticating anything. It activated a service-account key inside the container, pinned GCP
# project api-project-dev-450715, and pulled GKE credentials for propolicy-cluster-uat. The
# project is retired (live GCP project is migrationplane-core-503023, CURRENT.md typed fact
# gcp_project) and the old GKE/old-AMS deployer is retired. No live equivalent exists for an
# agent-side SA activation or a GKE credential pull, so nothing below is repointed and the body
# is kept for audit only.
# MARKER_SANDBOX_RECOVERY_V1
echo "sandbox_recovery: RETIRED 2026-09-01 (project api-project-dev-450715 and the GKE deployer are retired; live project is migrationplane-core-503023)." >&2
exit 1

# ---- historical body below, unreachable by design ----
# sandbox_recovery.sh
# Maya - MigrationPlane SuperAgent
# Historical purpose (do not run): ran after a container reset to restore gcloud, kubectl,
# GCP auth, repo clone and PATH.

set -euo pipefail

echo "=== Maya Sandbox Recovery ==="
echo "Started: $(date)"
echo ""

# ── 1. gcloud SDK ─────────────────────────────────────────────────────────────
if ! command -v gcloud &>/dev/null; then
  echo "[1/6] Installing gcloud SDK..."
  curl -sSL https://sdk.cloud.google.com > /tmp/install_gcloud.sh
  bash /tmp/install_gcloud.sh --disable-prompts --install-dir=/opt/gcloud 2>&1 | tail -3
  export PATH="/opt/gcloud/google-cloud-sdk/bin:$PATH"
  echo "  ✓ gcloud installed: $(gcloud version 2>/dev/null | head -1)"
else
  export PATH="/opt/gcloud/google-cloud-sdk/bin:$PATH"
  echo "[1/6] gcloud already present: $(gcloud version 2>/dev/null | head -1)"
fi

# ── 2. kubectl ────────────────────────────────────────────────────────────────
if ! command -v kubectl &>/dev/null; then
  echo "[2/6] Installing kubectl via gcloud..."
  gcloud components install kubectl --quiet 2>&1 | tail -2
  echo "  ✓ kubectl installed"
else
  echo "[2/6] kubectl already present: $(kubectl version --client 2>/dev/null | head -1)"
fi

# ── 3. GCP service account auth ───────────────────────────────────────────────
echo "[3/6] Authenticating with GCP service account..."
if [ -n "${GCP_SA_KEY:-}" ]; then
  echo "$GCP_SA_KEY" > /tmp/maya-sa-key.json
  gcloud auth activate-service-account --key-file=/tmp/maya-sa-key.json --quiet
  rm -f /tmp/maya-sa-key.json
  gcloud config set project api-project-dev-450715 --quiet
  echo "  ✓ Authenticated as: $(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null)"
else
  echo "  ⚠ GCP_SA_KEY not set - skipping GCP auth"
  echo "    Set secret GCP_SA_KEY to enable autonomous builds"
fi

# ── 4. GKE credentials ────────────────────────────────────────────────────────
echo "[4/6] Fetching GKE credentials..."
if command -v gcloud &>/dev/null && gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | grep -q "@"; then
  gcloud container clusters get-credentials propolicy-cluster-uat \
    --region us-east1 \
    --project api-project-dev-450715 \
    --quiet 2>&1 | tail -2
  echo "  ✓ kubectl context: $(kubectl config current-context 2>/dev/null)"
else
  echo "  ⚠ Skipping GKE credentials - no active GCP auth"
fi

# ── 5. Load .agents/.env ──────────────────────────────────────────────────────
echo "[5/6] Loading environment secrets..."
if [ -f /app/.agents/.env ]; then
  source /app/.agents/.env 2>/dev/null
  GITHUB_OK=$([ -n "${GITHUB_TOKEN:-}" ] && echo "✓ GITHUB_TOKEN present" || echo "⚠ GITHUB_TOKEN missing")
  echo "  $GITHUB_OK"
else
  echo "  ⚠ /app/.agents/.env not found"
fi

# ── 6. Verify repo access ─────────────────────────────────────────────────────
echo "[6/6] Verifying GitHub repo access..."
if [ -n "${GITHUB_TOKEN:-}" ]; then
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: token $GITHUB_TOKEN" \
    "https://api.github.com/repos/Alternative-Spaces-Inc/fpcore-migration-agent")
  if [ "$STATUS" = "200" ]; then
    echo "  ✓ fpcore-migration-agent: accessible"
  else
    echo "  ✗ fpcore-migration-agent: HTTP $STATUS - token may be expired"
  fi

  STATUS2=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: token $GITHUB_TOKEN" \
    "https://api.github.com/repos/Alternative-Spaces-Inc/fpcore-repo")
  if [ "$STATUS2" = "200" ]; then
    echo "  ✓ fpcore-repo: accessible"
  else
    echo "  ✗ fpcore-repo: HTTP $STATUS2"
  fi
else
  echo "  ⚠ Skipping - GITHUB_TOKEN not set"
fi


# ── 5b. Bridge token from GCP Secret Manager (gcloud-free) ────────────────────
# MARKER_BRIDGE_TOKEN_VAULT_PULL
# Closes the reset-strand gap: pull factory-bridge-token from Secret Manager via the
# reader SA (sm_restore.py), arm it where _bridge_client.py reads it. Value-blind.
echo "[5b] Restoring bridge token from vault (factory-bridge-token)..."
if [ -n "${GCP_BOOTSTRAP_SA_KEY:-}" ]; then
  BT=$(python3 /app/.agents/skills/sm_restore.py factory-bridge-token 2>/dev/null || true)
  if [ -n "$BT" ]; then
    mkdir -p /root/.fpcore && printf '%s' "$BT" > /root/.fpcore/terminal_bridge_token && chmod 600 /root/.fpcore/terminal_bridge_token
    grep -q '^export BRIDGE_TOKEN=' /app/.agents/.env 2>/dev/null && sed -i '/^export BRIDGE_TOKEN=/d' /app/.agents/.env
    echo "export BRIDGE_TOKEN=$BT" >> /app/.agents/.env
    echo "  \u2713 bridge token armed from vault (len ${#BT}) -> /root/.fpcore/terminal_bridge_token + BRIDGE_TOKEN"
    unset BT
  else
    echo "  \u26a0 vault pull returned empty (token may need W6 re-sync on the Mac)"
  fi
else
  echo "  \u26a0 GCP_BOOTSTRAP_SA_KEY missing \u2014 cannot pull bridge token from vault"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Recovery complete: $(date) ==="
echo ""
echo "PATH additions needed in shell sessions:"
echo "  export PATH=\"/opt/gcloud/google-cloud-sdk/bin:\$PATH\""
echo ""
echo "Next: read MAYA_RECOVERY_BOOTSTRAP.md for full session context:"
echo "  Alternative-Spaces-Inc/fpcore-repo/src/superagent-memory/MAYA_RECOVERY_BOOTSTRAP.md"
