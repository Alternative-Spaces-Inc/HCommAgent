#!/usr/bin/env bash
# Provenance: factory-bootstrap/_skills/secret-parity-check.sh (sha ca16334731040b98749600826b510b25a58e693d)
set -u
# agent-neutral: arg > AGENT_NAME/SUPERAGENT_NAME env > infer from <AGENT>_BROKER_KEY in env > puck
_infer_self() {
  if [ -n "${1:-}" ]; then echo "$1"; return; fi
  if [ -n "${AGENT_NAME:-}" ]; then echo "$AGENT_NAME"; return; fi
  if [ -n "${SUPERAGENT_NAME:-}" ]; then echo "$SUPERAGENT_NAME"; return; fi
  for a in puck hank maya dale zach andy harness scout liaison data builder2; do
    eval "v=\${$(echo $a | tr a-z A-Z)_BROKER_KEY:-}"
    [ -n "$v" ] && { echo "$a"; return; }
  done
  echo puck
}
SELF="$(_infer_self "${1:-}")"
ENV_FILE=".agents/.env"
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" 2>/dev/null && set +a
echo "===== SECRET PARITY CHECK - agent: $SELF ====="
echo "(names + status only; values are NEVER printed)"
SELF_UC=$(echo "$SELF" | tr a-z A-Z)
# REQUIRED = shared secrets every agent must have. Per-agent <SELF>_BROKER_KEY / _AGENT_API_KEY
# are OPTIONAL (shared-key-model agents like maya/hank authenticate via FACTORY_COMMS_TOKEN /
# FACTORY_COMMS_BROKER_KEY + the shared FACTORY_RELAY_TOKEN, and have NO per-agent named keys).
# Counting per-agent keys as REQUIRED false-fails those agents on the green-gate (the bug Maya+Hank hit).
# MARKER_SEAT_VAULT_SET_V1 (2026-09-28). Owner rulings: 2026-09-20, a seat vault holds exactly
# FACTORY_COMMS_TOKEN, GITHUB_READONLY_TOKEN and BASE44_WORKSPACE_PAT and never a GCP service
# account key (client secrets stay in Secret Manager under their project-credentials names);
# 2026-09-22, every vault value is a copy taken FROM Secret Manager, never pasted from a source
# page. The former single EXPECT list (BRIDGE_TOKEN CDP_SECRET GITHUB_TOKEN GCP_SA_KEY
# BASE44_WORKSPACE_API_KEY FACTORY_RELAY_TOKEN ...) predates both rulings and false-failed every
# seat on names it must not hold. Andy alone carries the lane extras below (Mac bridge, classic
# GitHub token for check runs, jump key). A retired name found in a non-andy vault is reported as
# RETIRED_PRESENT so the owner can remove it; it is never required and never restored.
EXPECT_SEAT=(FACTORY_COMMS_TOKEN GITHUB_READONLY_TOKEN BASE44_WORKSPACE_PAT)
EXPECT_ANDY_EXTRA=(BRIDGE_TOKEN GITHUB_CLASSIC_TOKEN ANDY_JUMP_SSH_KEY)
RETIRED_ON_SEATS=(GCP_SA_KEY CDP_SECRET FACTORY_RELAY_TOKEN BASE44_WORKSPACE_API_KEY GITHUB_TOKEN BRIDGE_TOKEN)
if [ "$SELF" = "andy" ]; then EXPECT=("${EXPECT_SEAT[@]}" "${EXPECT_ANDY_EXTRA[@]}"); else EXPECT=("${EXPECT_SEAT[@]}"); fi
OPTIONAL=("${SELF_UC}_BROKER_KEY" "${SELF_UC}_AGENT_API_KEY" OPENROUTER_API_KEY GITHUB_CLASSIC_TOKEN GITHUB_ACCESS_TOKEN)

# ---- map env var name -> GCP Secret Manager secret id, LIVE NAMES ONLY ----
# MARKER_VAULT_MAP_LIVE_NAMES_V1 (2026-09-15). The previous map named thirteen factory-* ids
# "verified 2026-06-22 during Tier-2 provision". The owner listed the live vault by hand on
# 2026-09-15 (gcloud secrets list, project from the typed fact gcp_project, names only): NOT ONE
# of those factory-* ids exists there. The only factory- name in the vault is factory-comms-tokens
# (the map said factory-comms-tokens-map). So every SM-RESTORED fallback this script has offered
# since June resolved to a name that does not exist, and a seat that lost a secret was told
# [MISSING] with a dead restore behind it. The names below are copied from that listing, not
# derived from a pattern (L-1816: parity by name is not reach, and a name nobody listed is not a
# name). A variable with no vault copy maps to "" on purpose and is reported NO-VAULT-COPY, which
# is the true state: a single copy living only in the Base44 app secret store.
sm_id_for() {
  case "$1" in
    BASE44_WORKSPACE_API_KEY)  echo "base44-workspace-api-key" ;;
    BASE44_WORKSPACE_PAT)      echo "base44-workspace-pat" ;;
    GITHUB_READONLY_TOKEN)     echo "github-readonly-token" ;;   # fine-grained factory-seats-readonly, stored 2026-09-22
    # MARKER_GITHUB_TOKENS_20260915. Two GitHub tokens issued by the owner on 2026-09-15, both for
    # login: the owner account, both expiring 2026-12-14. GITHUB_ACCESS_TOKEN (also held as GITHUB_TOKEN on the
    # Andy seat) is FINE-GRAINED: contents, pull requests, actions, metadata; it cannot read check
    # runs, code-scanning alerts or Actions administration, so it has no vault copy and is the
    # everyday branch-and-PR token. GITHUB_CLASSIC_TOKEN is CLASSIC, scopes repo and workflow, and
    # covers what the fine-grained one cannot (check runs, code-scanning alerts, repo Actions
    # permissions and secrets reads; org-level Actions permissions still need admin:org, which
    # stays with the owner's own gh login). Vault copy github-classic-token, created by the owner
    # from a read -rs prompt so the value never sat in a chat or a shell history; sha256 prefix of
    # the value as issued: 46c9825afae59b05, for a MATCH/DIFFER read without ever printing it.
    GITHUB_CLASSIC_TOKEN)      echo "github-classic-token" ;;
    FACTORY_COMMS_TOKENS)      echo "factory-comms-tokens" ;;
    *_AGENT_API_KEY)           # base44-agent-key-<agent> is optional for ANY roster seat (secrets_inventory.py
                               # roster_agents(), 2026-09-18); a seat with none simply reports NO-VAULT-COPY below.
                               local a; a="$(echo "${1%_AGENT_API_KEY}" | tr A-Z a-z)"
                               echo "base44-agent-key-$a" ;;
    # No vault copy as of the 2026-09-15 listing: BRIDGE_TOKEN (andy lane only since 2026-09-23), CDP_SECRET,
    # GITHUB_TOKEN and GITHUB_ACCESS_TOKEN (fine-grained, see MARKER_GITHUB_TOKENS_20260915), GCP_SA_KEY, FACTORY_RELAY_TOKEN, FACTORY_COMMS_TOKEN (the per-seat value; the
    # map secret is factory-comms-tokens), OPENROUTER_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY,
    # GCS_BUCKET_NAME, WORKER_URL, BROKER_KEYS, BASE44_SERVICE_KEY, BASE44_SERVICE_TOKEN.
    *)                         echo "" ;;
  esac
}
# Fingerprint compare, never a value: sha256 of what the env holds against sha256 of what the
# vault holds, printed as MATCH or DIFFER. Two stores (Base44 app secrets at runtime, Secret
# Manager as the copy the factory owns) can both be present, both valid, and different; a name
# check cannot see that, a fingerprint can.
fp16() { printf %s "$1" | sha256sum | cut -c1-16; }

# ---- MARKER_TYPED_FACTS_SKILL_V1 (2026-09-02, broker-host retirement lane) ------------------
# Two changes here, both closing the same hole. (1) The vault PROJECT was a literal, copied into
# five agent trees, and when the project moved every copy kept pointing at the retired one; it is
# now READ from the typed state store (memory/state/facts, or the generated CURRENT.md fact table
# when the payload has no checkout) through typed_facts.py in this directory. (2) A restored
# container can still carry a .env from the broker era, so any factory base URL in the
# environment must be the live edge from that same store, or this script refuses to run rather
# than smoke-testing a host we do not control. Nothing here invents a coordinate: an unresolved
# fact is reported, never guessed.
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
TYPED_FACTS=""
for c in "$SKILL_DIR/typed_facts.py" ".agents/skills/typed_facts.py"; do
  [ -f "$c" ] && { TYPED_FACTS="$c"; break; }
done
if [ -n "$TYPED_FACTS" ]; then
  for var in FACTORY_API_BASE FACTORY_API_URL FACTORY_COMMS_BASE FACTORY_COMMS_URL \
             FACTORY_BROKER_URL BROKER_BASE_URL COMMS_BASE JOBS_API_BASE; do
    eval "u=\${$var:-}"
    [ -n "$u" ] || continue
    if ! python3 "$TYPED_FACTS" assert-live-edge "$u" >/dev/null 2>&1; then
      echo "REFUSING TO RUN: $var names a factory host that is not the live edge." >&2
      python3 "$TYPED_FACTS" assert-live-edge "$u" >/dev/null
      echo "OWNER-ACTION: unset $var in this container, or set it to the live edge recorded in" >&2
      echo "  the typed state store. This script does not repoint a retired coordinate for you." >&2
      exit 2
    fi
  done
fi

# ---- OPTIONAL vault restore: only if reader SA is provisioned (GCP_BOOTSTRAP_SA_KEY present) ----
PROJECT="${VAULT_PROJECT:-}"
if [ -z "$PROJECT" ] && [ -n "$TYPED_FACTS" ]; then PROJECT="$(python3 "$TYPED_FACTS" get gcp_project 2>/dev/null)"; fi
VAULT_READY=0
VAULT_MODE="none"
SAVED_ACCT=""
if [ -n "${GCP_BOOTSTRAP_SA_KEY:-}" ] && [ -n "$PROJECT" ]; then
  if command -v gcloud >/dev/null 2>&1; then
    SAVED_ACCT="$(gcloud config get-value account 2>/dev/null)"
    TMPK="$(mktemp)"; echo "$GCP_BOOTSTRAP_SA_KEY" | base64 -d > "$TMPK" 2>/dev/null
    if gcloud auth activate-service-account --key-file="$TMPK" >/dev/null 2>&1; then VAULT_READY=1; VAULT_MODE="gcloud"; fi
  fi
  if [ "$VAULT_READY" = "0" ] && command -v python3 >/dev/null 2>&1 && [ -f "$SKILL_DIR/sm_restore.py" ]; then
    if python3 "$SKILL_DIR/sm_restore.py" factory-bridge-token >/dev/null 2>&1; then VAULT_READY=1; VAULT_MODE="rest"; fi
  fi
fi
vault_restore() {  # $1 = env var name; echoes value or empty
  [ "$VAULT_READY" = "1" ] || return 1
  local sid; sid="$(sm_id_for "$1")"; [ -n "$sid" ] || return 1
  if [ "$VAULT_MODE" = "gcloud" ]; then
    gcloud secrets versions access latest --secret="$sid" --project="$PROJECT" 2>/dev/null
  else
    python3 "$SKILL_DIR/sm_restore.py" "$sid" 2>/dev/null
  fi
}

echo ""; echo "--- presence (LOADED = non-empty in env; SM-RESTORED = pulled from GCP Secret Manager) ---"; MISS=0
if [ "$VAULT_READY" = "1" ]; then echo "  (vault fallback ARMED - agent-secret-reader SA active, mode=$VAULT_MODE)"; else echo "  (vault fallback OFF - GCP_BOOTSTRAP_SA_KEY unset or gcloud absent)"; fi
for name in "${EXPECT[@]}"; do
  val="${!name:-}"
  sid="$(sm_id_for "$name")"
  if [ -n "$val" ]; then
    if [ -n "$sid" ] && [ "$VAULT_READY" = "1" ]; then
      vv="$(vault_restore "$name" 2>/dev/null)"
      if [ -z "$vv" ]; then echo "  [LOADED]  $name (vault $sid: unreadable, parity not graded)"
      elif [ "$(fp16 "$val")" = "$(fp16 "$vv")" ]; then echo "  [LOADED]  $name (vault $sid: MATCH)"
      else echo "  [LOADED]  $name (vault $sid: DIFFER - env and vault hold different values; owner decides which is live)"; fi
    elif [ -n "$sid" ]; then echo "  [LOADED]  $name (vault $sid, parity not graded: vault fallback OFF)"
    else echo "  [LOADED]  $name (NO-VAULT-COPY: single copy, Base44 app secret store only)"; fi
  else
    rv=""; [ -n "$sid" ] && rv="$(vault_restore "$name" 2>/dev/null)"
    if [ -n "$rv" ]; then export "$name"="$rv"; echo "  [SM-RESTORED] $name (recovered from GCP Secret Manager: $sid)"
    elif [ -z "$sid" ]; then echo "  [MISSING] $name (NO-VAULT-COPY: nothing to restore from; owner re-pastes into the Base44 app secret store)"; MISS=$((MISS+1))
    else echo "  [MISSING] $name (vault $sid present in the listing but not restorable from here)"; MISS=$((MISS+1)); fi
  fi
done
if [ "$SELF" != "andy" ]; then
  for name in "${RETIRED_ON_SEATS[@]}"; do
    [ -n "${!name:-}" ] && echo "  [RETIRED_PRESENT] $name (not part of the seat vault set, MARKER_SEAT_VAULT_SET_V1; owner removes it from this seat's vault)"
  done
fi
echo "  [INFO]    BRIDGE_URL (Mac lane, andy seat only since 2026-09-23; not graded here, mac_bridge health is the check)"
echo ""; echo "--- per-agent keys (OPTIONAL - informational only; shared-key agents have none) ---"
for name in "${OPTIONAL[@]}"; do
  if [ -n "${!name:-}" ]; then echo "  [LOADED]  $name"; else echo "  [n/a   ]  $name (shared-key model - not required)"; fi
done

echo ""; echo "--- smokes (github 200, base44 200; broker and bridge are RETIRED, not graded) ---"
# The broker-key selection chain that used to live here went with its probe: nothing in this
# script authenticates to the broker any more, so resolving a key would be dead work on a
# credential whose lane is retired.
# RETIRED (canon-fix pass, restated 2026-09-02): the broker inbox probe and the Mac-bridge
# /health probe are removed, not repointed. The broker host they called is decommissioned and
# the FactoryComms broker lane is a tombstone: an agent 401 there is the EXPECTED retirement,
# not a key to re-mint, and the lane is retired rather than relocated, so pointing these at the
# live edge would be wrong too. Agent comms are the SuperAgents transport via Base44 persona
# dispatch. The old BROKER_OK=1 pin is gone with them: a variable held at 1 so a gate could keep
# ANDing against it is a green nobody proved, which is the false-green this file exists to
# avoid. The gate below now grades exactly what this script still measures - secret presence -
# and says so in its own verdict line. Do not re-add either probe.
echo "  broker -> RETIRED (broker lane is a tombstone; comms are the SuperAgents transport via Base44 persona dispatch)"
echo "  bridge -> RETIRED (cloudflared/trycloudflare Mac bridge retired; Mac terminal lane retired 2026-08-25)"
if [ -n "${GITHUB_TOKEN:-}" ]; then c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 12 -H "Authorization: Bearer $GITHUB_TOKEN" "https://api.github.com/rate_limit"); echo "  github /rate_limit -> $c (200=ok)"; else echo "  github -> SKIP (GITHUB_TOKEN missing)"; fi
if [ -n "${BASE44_WORKSPACE_API_KEY:-}" ]; then c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 12 -H "api_key: $BASE44_WORKSPACE_API_KEY" "https://app.base44.com/api/apps"); echo "  base44 /api/apps -> $c (200=ok; uses api_key header NOT Bearer)"; else echo "  base44 -> SKIP (BASE44_WORKSPACE_API_KEY missing)"; fi
# relay token: verify it stored the FULL value (scanner can truncate 48->31), then smoke outbound
if [ -n "${FACTORY_RELAY_TOKEN:-}" ]; then
  RLEN=${#FACTORY_RELAY_TOKEN}; RSHA=$(printf %s "$FACTORY_RELAY_TOKEN" | sha256sum | cut -c1-16)
  if [ "$RLEN" = "48" ] && [ "$RSHA" = "84af79f06d933789" ]; then
    echo "  relay token -> OK (len=48 sha16=84af79f06d933789)"
    rc=$(curl -s -o /dev/null -w "%{http_code}" --max-time 40 -X POST "https://puck-d4ff2abb.base44.app/functions/superagentDispatch" -H "Content-Type: application/json" -H "api_key: ${BASE44_WORKSPACE_API_KEY:-}" -H "X-Factory-Relay: ${FACTORY_RELAY_TOKEN}" -d "{\"to\":\"puck\",\"from\":\"$SELF\",\"message\":\"parity-check relay smoke from $SELF\",\"wait_for_reply\":false}")
    echo "  relay outbound -> $rc (200=ok; 401=token mismatch; 403=missing api_key/edge)"
  else
    echo "  relay token -> WRONG (len=$RLEN sha16=$RSHA; need len=48 sha16=84af79f06d933789 - re-paste, scanner truncated it)"
  fi
else
  echo "  relay token -> SKIP (FACTORY_RELAY_TOKEN missing)"
fi

# restore prior gcloud account so the vault check never leaves the reader SA active
if [ -n "$SAVED_ACCT" ] && [ "$VAULT_READY" = "1" ]; then gcloud config set account "$SAVED_ACCT" >/dev/null 2>&1; fi
[ -n "${TMPK:-}" ] && rm -f "$TMPK" 2>/dev/null
echo ""
# GREEN GATE (revised 2026-09-01): all REQUIRED shared secrets present. The old gate also
# required a 200 from the retired broker inbox; that clause is gone with the probe.
# Per-agent keys are OPTIONAL and never block the gate.
if [ "$MISS" -eq 0 ]; then
  echo "SECRET_PARITY: GREEN - all required secrets LOADED ($SELF); comms reachability is NOT graded here"
else
  echo "SECRET_PARITY: $MISS REQUIRED MISSING ($SELF)"
fi
