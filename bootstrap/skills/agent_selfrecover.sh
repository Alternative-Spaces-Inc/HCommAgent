#!/usr/bin/env bash
# Provenance: factory-bootstrap/_skills/agent_selfrecover.sh (sha ca16334731040b98749600826b510b25a58e693d)
# agent_selfrecover.sh - Agent Self-Recovery orchestrator (ALL agents).
# MARKER_AGENT_SELF_RECOVERY_V1   Design: AGENT_SELF_RECOVERY_DESIGN_20260623.md (Dale, Al-directed).
#
# Recovers an agent's SKILLS + TOOLS after a container reset and PROVES each by USE (not by
# presence). Fail-closed + honest-gap: a unit that cannot be self-recovered is reported RED with
# the single owner-action that fixes it. Idempotent. Secret VALUES never printed.
#
# SUPERSEDED 2026-09-01 (canon-fix pass): the DOMAIN 3 wire probes were rewritten. The broker
# probe and the Mac-bridge probe were DELETED as RETIRED, not repaired:
#   - BROKER PROBE: RETIRED. It probed a decommissioned broker host. The FactoryComms broker
#     lane is a tombstone, and an agent 401 there is the EXPECTED retirement, not a key to
#     re-mint. Agent comms are the SuperAgents transport via Base44 persona dispatch. The lane
#     is retired, not relocated, so there is no replacement probe and none may be re-added.
#   - MAC BRIDGE PROBE: RETIRED. It probed a rotating third-party BRIDGE_URL for the Mac
#     terminal lane and told the owner to restart the local relay program that published it.
#     That lane and that program were retired fleet-wide on 2026-08-25 (Al-confirmed); the
#     tooling is tombstoned under factory-bootstrap/_skills/_retired/ and the record is
#     reviews/BROKER_RETIREMENT_RECORD_20260908.md. A rotating third-party hostname must
#     never be re-wired from a restore.
#
# MARKER_SELFRECOVER_NO_REMOTE_ACCESS_V1 (2026-09-09, Hank's finding, measured 19:50Z). The
# note above used to NAME the retired relay program and the vendor hostname it dialled. A
# sandbox that blocks remote-access tooling matches the literal string and cannot tell a
# comment from a command, so hank's seat refused to run its own recovery over an arm that had
# already been deleted on 2026-09-01. The names are GONE rather than quoted: a retirement note
# that stops a seat recovering is worse than no note at all. This file carries no remote-access
# command and no name of one, and factory-bootstrap/platform/test_selfrecover_copies.py fails
# the build if either returns, in canon or in any copy.
#
# MARKER_TYPED_FACTS_SKILL_V1 (2026-09-02, broker-host retirement lane): the two live anchors
# below used to be literals in this file with a comment asking the reader to keep them in step
# with CURRENT.md by hand. That is the same failure mode as the retired broker host: a
# coordinate copied into five agent trees, none of which learns when it moves. The GCP project
# is now READ from the typed state store (memory/state/facts, or the generated CURRENT.md fact
# table when the payload has no checkout) through typed_facts.py in this directory, and an
# unresolvable fact is an honest GAP row, never a guess. See also the fail-closed environment
# check below, which refuses to run when this container still names a factory host that is not
# the live edge.
#
# Usage:
#   bash agent_selfrecover.sh                 # full recovery sweep for THIS agent (manifest below)
#   bash agent_selfrecover.sh --probe         # S1 only: probe the two live anchors, no installs
#   bash agent_selfrecover.sh --whoami        # print the seat this copy resolves to, then stop
#   AGENT=dale bash agent_selfrecover.sh      # select per-agent REQUIRED manifest (default: canon)
#
# Exit 0 = every REQUIRED unit OK or RECOVERED-and-proven; non-zero = a REQUIRED unit is RED.
# Exit 3 = the seat identity could not be resolved: nothing probed, nothing installed.
set -uo pipefail

AGENT="${AGENT:-canon}"
ENV_FILE="${ENV_FILE:-.agents/.env}"
SKILLS_DIR="${SKILLS_DIR:-.agents/skills}"
MODE="${1:-full}"
[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE" 2>/dev/null; set +a; }

# typed_facts.py ships beside this script. Prefer the copy next to us (a restore may run this
# from anywhere), then the configured skills dir. Absent = every fact is an honest gap.
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
TYPED_FACTS=""
for c in "$_HERE/typed_facts.py" "$SKILLS_DIR/typed_facts.py"; do
  [ -f "$c" ] && { TYPED_FACTS="$c"; break; }
done

# ---------- SEAT IDENTITY: the banner names the SEAT, never the copy ----------
# MARKER_SELFRECOVER_SEAT_IDENTITY_V1 (2026-09-09, Dale's finding on his own seat, 19:50Z).
#
# The AGENT= line near the top is the COPY SENTINEL, not an identity.
# scripts/check_canon_skill_copies.py permits exactly one byte-difference between the canon copy
# and the five payload copies - that default name - and requires the canon copy to carry the word
# the sentinel below holds, which is deliberately not a roster agent. Until now that sentinel was
# also what the banner PRINTED, and it was pinned BEFORE $ENV_FILE was sourced, so a seat that
# persists its identity as AGENT_NAME in .agents/.env (SHARED_RULE_agent_identity_durability_v1,
# and the durable-secret lesson of 2026-07-08) could not be seen by this script at all. Dale ran
# the canon copy on his own seat and the banner said the sentinel back at him.
#
# Resolution runs AFTER the env file is sourced and reads, in order: an explicit $AGENT, then
# $AGENT_NAME, then $SUPERAGENT_NAME, then the seat named by the payload path this copy was read
# from. Every one of those is an ASSIGNMENT somebody made; none is a guess from whichever
# credential happens to be present, which law 1 of the identity rule forbids. With none of them
# resolvable this script exits 3 and installs nothing - the same fail-loud shape as
# boot_heartbeat.sh and agent_readiness_signal.py - because this sweep WRITES to $SKILLS_DIR, and
# a write attributed to the wrong seat is exactly what law 4 exists to stop.
AGENT_SENTINEL="canon"
if [ "$AGENT" = "$AGENT_SENTINEL" ]; then
  AGENT="${AGENT_NAME:-${SUPERAGENT_NAME:-}}"
  if [ -z "$AGENT" ]; then
    case "$_HERE" in
      */factory-bootstrap/agents/*/files/skills)
        AGENT="${_HERE%/files/skills}"; AGENT="${AGENT##*/}" ;;
    esac
  fi
fi
AGENT="$(printf '%s' "${AGENT:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
if [ -z "$AGENT" ] || [ "$AGENT" = "$AGENT_SENTINEL" ]; then
  echo "FATAL: seat identity unresolved. This copy refuses to report under its copy sentinel," >&2
  echo "  which is a placeholder for the copy gate and not an agent." >&2
  echo "  Tried in order: \$AGENT, \$AGENT_NAME, \$SUPERAGENT_NAME, this copy's payload path." >&2
  echo "  OWNER-ACTION: set AGENT_NAME to this seat's roster id (canon/_roster/ROSTER.yaml) in" >&2
  echo "  $ENV_FILE or in the environment, then re-run. Nothing probed, nothing installed." >&2
  exit 3
fi
if [ "$MODE" = "--whoami" ]; then
  echo "$AGENT"
  exit 0
fi

fact() {  # $1 = fact name -> echoes the value, or nothing (and returns 1) if unresolvable
  [ -n "$TYPED_FACTS" ] || return 1
  python3 "$TYPED_FACTS" get "$1" 2>/dev/null
}

RED=0
row() { printf '  %-26s | %-11s | %-30s | %s\n' "$1" "$2" "$3" "${4:-}"; }
hdr() { echo; echo "== $1 =="; printf '  %-26s | %-11s | %-30s | %s\n' UNIT STATE PROOF OWNER-ACTION-IF-RED; }

# ---------- FAIL-CLOSED: no factory coordinate that is not the live edge ----------
# A restored container can still be carrying a .env from the broker era. Honouring one of those
# values silently would point this sweep, and anything that trusts its GREEN, at a host we do
# not control. Every factory base URL present in the environment must be the live edge from the
# typed state store, or this script refuses to run at all.
GUARD_HOST_VARS="FACTORY_API_BASE FACTORY_API_URL FACTORY_COMMS_BASE FACTORY_COMMS_URL \
FACTORY_BROKER_URL BROKER_BASE_URL COMMS_BASE JOBS_API_BASE"
GUARD_INERT=0
guard_factory_hosts() {
  local var url present=""
  # LOUD-WHEN-INERT 2026-09-09 (post-review). This used to be `[ -n "$TYPED_FACTS" ] || return 0`:
  # with no resolver the guard returned success and said nothing, so on a seat restoring from an
  # older manifest (one that does not list typed_facts.py) the fail-closed retired-host check was
  # SILENTLY off while the sweep still printed its normal report. A protection that installs inert
  # and does not say so is the vacuous pass this whole lane exists to remove. In --probe mode
  # nothing else mentions typed_facts.py either, so the silence was total.
  if [ -z "$TYPED_FACTS" ]; then
    GUARD_INERT=1
    for var in $GUARD_HOST_VARS; do
      eval "url=\${$var:-}"
      [ -n "$url" ] && present="$present $var"
    done
    echo "GUARD INERT: typed_facts.py not found beside this script or in $SKILLS_DIR." >&2
    if [ -n "$present" ]; then
      echo "  The retired-host guard did NOT run, and these factory host vars ARE set:$present" >&2
    else
      echo "  The retired-host guard did NOT run. No factory host vars are set, so nothing was skipped." >&2
    fi
    echo "  OWNER-ACTION: restore typed_facts.py (it is in the current manifest) and re-run. Until" >&2
    echo "  then this container has no fail-closed check on a broker-era coordinate in its .env." >&2
    return 0
  fi
  for var in $GUARD_HOST_VARS; do
    eval "url=\${$var:-}"
    [ -n "$url" ] || continue
    if ! python3 "$TYPED_FACTS" assert-live-edge "$url" >/dev/null 2>&1; then
      echo "REFUSING TO RUN: $var names a factory host that is not the live edge." >&2
      python3 "$TYPED_FACTS" assert-live-edge "$url" >/dev/null
      echo "OWNER-ACTION: unset $var in this container, or set it to the live edge recorded in" >&2
      echo "  the typed state store. This script does not repoint a retired coordinate for you." >&2
      exit 2
    fi
  done
}

# ---------- DOMAIN 3: LIVE ANCHORS (prove-by-use, typed-fact backed only) ----------
probe_wires() {
  hdr "DOMAIN 3 - LIVE ANCHORS (typed facts only)"

  # Carry the inert guard into the report as well as stderr, so it is visible in --probe mode,
  # where probe_skills (which already grades typed_facts.py RED when absent) does not run.
  [ "${GUARD_INERT:-0}" = "1" ] && row retired-host-guard "gap" "typed_facts.py absent, guard did not run" \
    "restore typed_facts.py (it is in the current manifest), then re-run"

  # GCP PROJECT: the only live project, read from the typed state store. Read-only; no auth is
  # minted here, and no project name is hardcoded in this file.
  LIVE_GCP_PROJECT="${LIVE_GCP_PROJECT:-$(fact gcp_project)}"
  if [ -z "$LIVE_GCP_PROJECT" ]; then
    row gcp-project "gap" "fact gcp_project unresolved" "run from a factory checkout, or set FACTORY_TYPED_FACTS_DIR / LIVE_GCP_PROJECT"
  elif command -v gcloud >/dev/null 2>&1; then
    cur=$(gcloud config get-value project 2>/dev/null | tr -d '[:space:]')
    if [ "$cur" = "$LIVE_GCP_PROJECT" ]; then row gcp-project OK "config project matches typed fact"
    elif [ -z "$cur" ] || [ "$cur" = "(unset)" ]; then
      row gcp-project "gap" "project unset" "owner set the project to $LIVE_GCP_PROJECT in their own shell"
    else
      row gcp-project RED "project=$cur" "wrong project; the only live project is $LIVE_GCP_PROJECT"; RED=1
    fi
  else
    row gcp-project "n/a" "gcloud absent (owner-only)" "no agent-side action; GCP actions are the owner's"
  fi

  # ORG REPO OWNER: source recovery depends on reaching the org, not on any legacy owner.
  if [ -n "${GITHUB_ACCESS_TOKEN:-}" ]; then
    code=$(curl -s -m12 -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $GITHUB_ACCESS_TOKEN" \
      "https://api.github.com/repos/$HOME_REPO" 2>/dev/null)
    case "$code" in
      200) row github-org OK "repos/$HOME_REPO 200";;
      404) row github-org RED "404 on $HOME_REPO" "token lacks org access, or the owner is wrong: canonical owner is $ORG_REPO_OWNER"; RED=1;;
      *)   row github-org RED "HTTP $code" "re-mint the github connector token this session"; RED=1;;
    esac
  else
    row github-org RED "token unset" "mint via github connector (get_connector_token github)"; RED=1
  fi

  # TYPED FACT STORE itself: if this is unreachable, every coordinate above is a guess, and this
  # sweep says so rather than reporting a GREEN it cannot back.
  if [ -n "$TYPED_FACTS" ] && python3 "$TYPED_FACTS" get gcp_project >/dev/null 2>&1; then
    row typed-facts OK "store readable (gcp_project resolves)"
  else
    row typed-facts "gap" "store unreachable" "run from a factory checkout or set FACTORY_TYPED_FACTS_DIR; coordinates are unverified until then"
  fi

  # RETIRED, deliberately absent: broker inbox probe, Mac-bridge probe. See the SUPERSEDED note
  # at the top of this file. Do not re-add either, and do not name their tooling here either
  # (MARKER_SELFRECOVER_NO_REMOTE_ACCESS_V1): a sandbox guard reads the literal, not the prose.
}

# ---------- DOMAIN 2: TOOLS (probe; self-install where possible) ----------
probe_tools() {
  hdr "DOMAIN 2 - TOOLS / DEPS"
  python3 -c "import cryptography" 2>/dev/null && row "py:cryptography" OK "import ok" \
    || { python3 -m pip install --quiet cryptography 2>/dev/null && row "py:cryptography" RECOVERED "pip installed" \
         || { row "py:cryptography" RED "import fails" "pip install cryptography"; RED=1; }; }
  command -v node >/dev/null && row node OK "$(node -v 2>/dev/null)" || row node "n/a" "absent" "install node if browser/JS skills needed"
  command -v gcloud >/dev/null && row gcloud OK "present" || row gcloud "gap" "absent (owner-only)" "owner-provision gcloud auth if a GCP read is needed"
  command -v kubectl >/dev/null && row kubectl OK "present" || row kubectl "gap" "absent (owner-only)" "owner-provision kubectl if a cluster read is needed"
}

# ---------- DOMAIN 1: SKILLS (presence + selftest, names DERIVED from the manifest) ----------
# MARKER_SELFRECOVER_READS_MANIFEST_V1 (2026-09-09, Andy directed, Hank found + fixed). This used
# to carry its own hardcoded name list here, a SECOND list living next to skill_restore.py's
# MANIFEST. secret_selfheal.sh and vault_fetch.sh were named in that old list but never in the
# manifest (neither live nor retired), so nothing could ever install them and this domain reported
# them RED forever, an unfixable false-RED. The list below is derived from the manifest's live
# entries at run time instead: a name absent from the manifest is never probed, and a manifest
# live-name genuinely absent from disk still reports RED, the true-positive case is unchanged.
# See factory-bootstrap/platform/test_selfrecover_reads_manifest.py.
probe_skills() {
  hdr "DOMAIN 1 - SKILLS (presence + selftest)"
  local restorer="$SKILLS_DIR/skill_restore.py"
  local live_names=""
  if [ -f "$restorer" ]; then
    live_names="$(python3 - "$restorer" <<'PYMANIFEST'
import importlib.util, sys
path = sys.argv[1]
spec = importlib.util.spec_from_file_location("skill_restore_manifest", path)
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except SystemExit:
    pass
manifest = getattr(mod, "MANIFEST", None)
if isinstance(manifest, dict):
    for name, meta in manifest.items():
        if not meta.get("retired"):
            print(meta.get("local", name))
PYMANIFEST
)"
  fi
  if [ -z "$live_names" ]; then
    row "skill_restore.py-manifest" RED "unreadable, cannot derive the live-skill list" \
      "S2: verify $restorer is present and its MANIFEST is importable"
    RED=1
    live_names=$'sm_restore.py\ntyped_facts.py'
  fi
  while IFS= read -r s; do
    [ -z "$s" ] && continue
    [ -f "$SKILLS_DIR/$s" ] && row "$s" OK "present" || { row "$s" RED "absent" "S2: restore from the home repo skills tree ($HOME_REPO)"; RED=1; }
  done <<< "$live_names"
  # typed_facts ships a selftest; it is the anchor every coordinate above depends on
  if [ -n "$TYPED_FACTS" ]; then
    timeout 20 python3 "$TYPED_FACTS" --selftest >/dev/null 2>&1 \
      && row typed_facts.py OK "selftest pass" || { row typed_facts.py RED "selftest fail" "re-restore typed_facts.py"; RED=1; }
  fi
  # cirun-grade ships a selftest
  if [ -f "$SKILLS_DIR/cirun-grade/grade_cirun.py" ]; then
    timeout 20 python3 "$SKILLS_DIR/cirun-grade/grade_cirun.py" --selftest >/dev/null 2>&1 \
      && row cirun-grade OK "selftest pass" || row cirun-grade RED "selftest fail" "re-restore cirun-grade skill"
  fi
}

# The org owner is an identity, not a live coordinate: it is the roster's home-repo owner, and
# no typed fact records it. It stays overridable so a fork can run this without an edit.
ORG_REPO_OWNER="${ORG_REPO_OWNER:-Alternative-Spaces-Inc}"
HOME_REPO="${HOME_REPO:-$ORG_REPO_OWNER/fpcore-repo}"
# The canon repo is where factory-bootstrap/_skills/ lives. It is NOT the home repo: the home
# repo's skills tree has never carried agent_backup_to_canon.sh, which is exactly why no recovery
# has ever re-installed it.
CANON_REPO="${CANON_REPO:-$ORG_REPO_OWNER/fpcore-migration-agent}"
CANON_REF="${CANON_REF:-main}"

# ---------- DOMAIN 1b: RE-INSTALL THE CANON COPY OF THE SKILLS THAT MUST NOT GO STALE ----------
# MARKER_SELFRECOVER_REINSTALLS_BACKUP_V1 (2026-09-08, Andy).
#
# WHY: presence is not currency. On 2026-09-07 and 2026-09-08 puck's live surface pushed four
# commits straight to main from a STALE copy of agent_backup_to_canon.sh, days after PR #534 put
# the PR path into the canon copy. probe_skills above checks presence only, and skill_restore.py's
# manifest did not list this file at all, so a recovery could run clean and leave the old copy in
# place. A fix to canon that no installer re-installs changes nothing.
#
# WHAT: for every skill named here, fetch the canon bytes, write them over the live file, and
# print the sha256 of what is now installed BESIDE the canon sha256. Fail closed: no canon source
# is RED, a canon copy that still writes to main is RED, and a post-write sha mismatch is RED.
REINSTALL_FROM_CANON="agent_backup_to_canon.sh"

reinstall_canon_skills() {
  hdr "DOMAIN 1b - CANON SKILLS RE-INSTALLED (sha256 against canon)"
  local s out first rest state proof owner
  for s in $REINSTALL_FROM_CANON; do
    out=$(python3 - "$SKILLS_DIR" "$CANON_REPO" "$CANON_REF" "$s" "${GITHUB_ACCESS_TOKEN:-}" "${FACTORY_CANON_DIR:-}" <<'PYRI'
import sys, os, json, base64, hashlib, urllib.request, urllib.error
skills, repo, ref, name, tok, canon_dir = sys.argv[1:7]
rel = "factory-bootstrap/_skills/" + name
dest = os.path.join(skills, name)
sha = lambda b: hashlib.sha256(b).hexdigest()

canon = None; src = ""
if canon_dir:
    p = os.path.join(canon_dir, "factory-bootstrap", "_skills", name)
    if os.path.isfile(p):
        canon = open(p, "rb").read(); src = "checkout " + p
if canon is None and tok:
    url = f"https://api.github.com/repos/{repo}/contents/{rel}?ref={ref}"
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(
            url, headers={"Authorization": "Bearer " + tok,
                          "Accept": "application/vnd.github+json"}), timeout=25))
        canon = base64.b64decode(d["content"]); src = f"{repo}@{ref}"
    except urllib.error.HTTPError as e:
        print(f"RED|canon fetch HTTP {e.code}|re-mint the github connector token, or set FACTORY_CANON_DIR"); sys.exit(0)
    except Exception as e:
        print(f"RED|canon fetch failed|{type(e).__name__}; set FACTORY_CANON_DIR to a factory checkout"); sys.exit(0)
if canon is None:
    print("RED|no canon source|mint GITHUB_ACCESS_TOKEN or set FACTORY_CANON_DIR to a factory checkout"); sys.exit(0)

# Fail closed on a canon copy that would itself commit to main. Installing that is worse than
# leaving the live file alone, so this refuses rather than propagates.
if b'"branch":"main"' in canon.replace(b" ", b""):
    print("RED|canon copy writes to main|fix factory-bootstrap/_skills/ first; nothing installed"); sys.exit(0)

before = open(dest, "rb").read() if os.path.isfile(dest) else None
csha = sha(canon)
if before != canon:
    os.makedirs(skills, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(canon)
    try: os.chmod(dest, 0o755)
    except Exception: pass
after = open(dest, "rb").read()
asha = sha(after)
if asha != csha:
    print("RED|write-back sha mismatch|the filesystem did not take the canon bytes"); sys.exit(0)
verdict = "OK" if before == canon else "RECOVERED"
detail = "was ABSENT" if before is None else ("unchanged" if before == canon else "was STALE " + sha(before)[:16])
print(f"{verdict}|sha256 matches canon|from {src}")
print(f"canon sha256     {csha}")
print(f"installed sha256 {asha}   ({detail})")
PYRI
)
    first=$(printf '%s\n' "$out" | head -1)
    rest=$(printf '%s\n' "$out" | tail -n +2)
    state="${first%%|*}"; owner="${first#*|}"; proof="${owner%%|*}"; owner="${owner#*|}"
    row "$s" "$state" "$proof" "$owner"
    [ -n "$rest" ] && printf '%s\n' "$rest" | sed 's/^/      /'
    if [ "$state" = "RED" ]; then RED=1; fi
  done
}

guard_factory_hosts
echo "AGENT SELF-RECOVERY - agent=$AGENT mode=$MODE  ($(date -u +%FT%TZ))"
if [ "$MODE" = "--probe" ]; then
  probe_wires
else
  probe_skills; reinstall_canon_skills; probe_tools; probe_wires
fi
echo
[ "$RED" = "0" ] && echo "RESULT: GREEN - all REQUIRED units OK or RECOVERED-and-proven." \
                 || echo "RESULT: RED - one or more REQUIRED units down; see OWNER-ACTION column."
exit "$RED"
