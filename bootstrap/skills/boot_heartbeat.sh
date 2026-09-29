#!/usr/bin/env bash
# Provenance: factory-bootstrap/_skills/boot_heartbeat.sh (sha ca16334731040b98749600826b510b25a58e693d)
# boot_heartbeat - self-reboot heartbeat. MARKER_BOOT_HEARTBEAT_V1
# Author: Puck, 2026-07-09 (Al continuity directive: activate bootstrap without waiting for a command).
#
# WHY: a container reset kills the agent; the agent cannot self-start. But a platform-side scheduled
# automation fires on its own clock and spins up a FRESH container with a turn. THIS skill is what that
# automation runs. It is the ACTIVATION source + the reset detector, in one cheap script.
#
# HOW: the current container's boot-epoch = /proc/stat btime (stable within one container life, changes
# on a fresh container). We compare it to the last epoch recorded in the durable AgentHeartbeat entity
# (Base44 DB, survives the reset). 
#   - epoch CHANGED or NO record  -> a reset happened -> run FULL bootstrap self-recovery, record new epoch.
#   - epoch SAME + healthy         -> near-noop: just refresh last_seen and exit (cheap).
# This keeps healthy runs ~free while guaranteeing recovery within one heartbeat interval of any reset.
#
# Idempotent + fail-safe: any error still records a DEGRADED beat so the watchdog can see it.
set -uo pipefail
source .agents/.env 2>/dev/null || true

# --- identity (normalize mis-case, fail-loud only on truly unset - matches comms-gate hardening) ---
ME="${AGENT_NAME:-}"
if [ -z "$ME" ]; then
  echo "FATAL: AGENT_NAME unset -- cannot run heartbeat without identity." >&2; exit 3
fi
ME="$(printf '%s' "$ME" | tr 'A-Z' 'a-z')"

# SELF app id - derive from the platform-injected var so EACH agent targets ITS OWN AgentHeartbeat
# entity (never a hardcoded peer app). FIX 2026-07-09 (Dale-found cross-contamination defect): the old
# hardcoded default to Puck's app meant any agent with BASE44_APP_ID unset silently wrote to Puck's
# heartbeat entity + corrupted reset detection. VITE_BASE44_APP_ID / EXPO_PUBLIC_BASE44_APP_ID are
# auto-injected by the platform with the agent's OWN app id. Fail-loud if none resolves - never guess.
APP_ID="${BASE44_APP_ID:-${VITE_BASE44_APP_ID:-${EXPO_PUBLIC_BASE44_APP_ID:-}}}"
if [ -z "$APP_ID" ]; then
  echo "FATAL: no app id resolved (BASE44_APP_ID / VITE_BASE44_APP_ID / EXPO_PUBLIC_BASE44_APP_ID all unset)." >&2
  echo "       Refusing to run -- a hardcoded fallback would write to another agent's heartbeat entity." >&2
  exit 3
fi
ENT="AgentHeartbeat"
API="https://app.base44.com/api/apps/${APP_ID}/entities/${ENT}"
AKEY="${BASE44_WORKSPACE_API_KEY:-${BASE44_API_KEY:-}}"

now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# current container boot-epoch
EPOCH="$(grep '^btime' /proc/stat 2>/dev/null | awk '{print $2}')"
[ -z "$EPOCH" ] && EPOCH="uptime-$(cut -d. -f1 /proc/uptime 2>/dev/null || echo 0)"
CSTART="$(date -u -d "@${EPOCH}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || now_utc)"

_api() { # method url [body]
  local m="$1" u="$2" b="${3:-}"
  if [ -n "$b" ]; then
    curl -s -m 20 -X "$m" -H "api_key: $AKEY" -H "Content-Type: application/json" -d "$b" "$u"
  else
    curl -s -m 20 -X "$m" -H "api_key: $AKEY" "$u"
  fi
}

# read the last heartbeat row for this agent
LAST_JSON="$(_api GET "${API}?agent=${ME}" 2>/dev/null || echo '[]')"
LAST_EPOCH="$(printf '%s' "$LAST_JSON" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin); rows=d if isinstance(d,list) else d.get('items',d.get('data',[]))
    rows=[r for r in rows if r.get('agent')=='${ME}']
    rows.sort(key=lambda r:r.get('last_seen_utc',''), reverse=True)
    print(rows[0].get('boot_epoch','') if rows else '')
except Exception:
    print('')
" 2>/dev/null || echo '')"
LAST_ID="$(printf '%s' "$LAST_JSON" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin); rows=d if isinstance(d,list) else d.get('items',d.get('data',[]))
    rows=[r for r in rows if r.get('agent')=='${ME}']
    rows.sort(key=lambda r:r.get('last_seen_utc',''), reverse=True)
    print(rows[0].get('id','') if rows else '')
except Exception:
    print('')
" 2>/dev/null || echo '')"

if [ "$EPOCH" = "$LAST_EPOCH" ] && [ -n "$LAST_EPOCH" ]; then
  # ---- HEALTHY PATH (near-noop): same container, just refresh last_seen ----
  RUNS="$(printf '%s' "$LAST_JSON" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin); rows=d if isinstance(d,list) else d.get('items',d.get('data',[]))
    rows=[r for r in rows if r.get('agent')=='${ME}']; rows.sort(key=lambda r:r.get('last_seen_utc',''),reverse=True)
    print(int(rows[0].get('runs_since_boot',0))+1 if rows else 1)
except Exception: print(1)
")"
  BODY="$(python3 -c "import json;print(json.dumps({'last_seen_utc':'$(now_utc)','state':'HEALTHY','runs_since_boot':$RUNS,'note':'heartbeat ok, same container'}))")"
  [ -n "$LAST_ID" ] && _api PUT "${API}/${LAST_ID}" "$BODY" >/dev/null 2>&1
  echo "HEARTBEAT: HEALTHY - same container (epoch=$EPOCH, run #$RUNS). No bootstrap needed."
  exit 0
fi

# ---- RESET DETECTED (or first-ever beat): new container -> run full bootstrap ----
echo "HEARTBEAT: RESET DETECTED - boot-epoch changed ($LAST_EPOCH -> $EPOCH). Running full bootstrap self-recovery."
# record RESET_DETECTED first (durable, before recovery, so a crash mid-recovery is still visible)
_api POST "$API" "$(python3 -c "import json;print(json.dumps({'agent':'${ME}','boot_epoch':'$EPOCH','container_started_utc':'$CSTART','last_seen_utc':'$(now_utc)','state':'RESET_DETECTED','runs_since_boot':1,'note':'new container detected by heartbeat; recovering'}))")" >/dev/null 2>&1

RC=0
# full skill reload FIRST (self-update the orchestrator, then run the manifest), then self-recovery
# MARKER_RESTORE_SOURCE_IS_CANON_V1 (2026-09-09): the self-update pulls skill_restore.py from
# the FACTORY canon, which is the one tree the retirement guard walks. It used to pull from the
# client platform repo through a path that 301-redirects since the org move of 2026-08-15.
export GITHUB_ACCESS_TOKEN="${GITHUB_ACCESS_TOKEN:-${GITHUB_TOKEN:-}}"
if [ -n "${GITHUB_ACCESS_TOKEN:-}" ]; then
  # GAP-03 (DALE_BOOTSTRAP_GAP_PACKAGE_20260710.md lines 46-51, open since 2026-07-10). This
  # fetch used to end in `2>/dev/null || true`, so a failed self-update was silent and the beat
  # still reported RECOVERED. A reset that cannot self-update must report DEGRADED: that is the
  # whole job of a heartbeat, and a heartbeat that lies is worse than none.
  # ATOMIC-WRITE FIX 2026-09-09 (post-review, MARKER_RESTORE_SOURCE_IS_CANON_V1). The old shape
  # was `... | python3 -c "open(DST,'w').write(b64decode(json.load(stdin)['content']))"`. Python
  # evaluates open(DST,'w') FIRST, so on any non-JSON or error body (404, 401, rate limit, an
  # empty stdin from curl -f) the destination is TRUNCATED TO ZERO BYTES and only THEN does the
  # decode raise. Measured: file size 0. The next line ran that zero-byte file, which exits 0, so
  # a container that had just destroyed its own recovery program reported success. GAP-03 already
  # made the FETCH honest; it could not make the TRUNCATION honest, because the truncation looked
  # like a successful run. Fetch to a temp file and os.replace() only a validated body, so a
  # failed self-update leaves the working copy exactly where it was.
  if curl -sS -m20 -f -H "Authorization: Bearer $GITHUB_ACCESS_TOKEN" \
       "https://api.github.com/repos/Alternative-Spaces-Inc/fpcore-migration-agent/contents/factory-bootstrap/_skills/skill_restore.py" \
       | python3 -c "
import sys, json, base64, os, tempfile
dst = sys.argv[1]
try:
    body = base64.b64decode(json.loads(sys.stdin.read())['content'])
except Exception as exc:
    sys.exit('self-update FAILED for %s (%s); existing copy left untouched' % (dst, exc))
if not body.strip():
    sys.exit('self-update FAILED for %s (empty body); existing copy left untouched' % dst)
d = os.path.dirname(dst) or '.'
os.makedirs(d, exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=d)
os.write(fd, body)
os.close(fd)
os.replace(tmp, dst)
" .agents/skills/skill_restore.py; then
    :
  else
    RC=1
    echo "HEARTBEAT: skill_restore.py self-update FAILED from factory canon; continuing with whatever copy this container has. State will be DEGRADED." >&2
  fi
  # A zero-byte or missing restore program exits 0 and installs nothing. That is the false green
  # the truncation used to produce, so refuse to treat it as a run at all.
  if [ -s .agents/skills/skill_restore.py ]; then
    python3 .agents/skills/skill_restore.py 2>&1 | tail -3 || RC=1
  else
    RC=1
    echo "HEARTBEAT: .agents/skills/skill_restore.py is missing or EMPTY - no skills were restored. State will be DEGRADED." >&2
    echo "  OWNER-ACTION: re-fetch factory-bootstrap/_skills/skill_restore.py from the factory canon by hand." >&2
  fi
fi
bash .agents/skills/agent_selfrecover.sh 2>&1 | tail -8 || RC=1

FINAL_STATE="RECOVERED"; [ "$RC" != "0" ] && FINAL_STATE="DEGRADED"
_api POST "$API" "$(python3 -c "import json,os;print(json.dumps({'agent':'${ME}','boot_epoch':'$EPOCH','container_started_utc':'$CSTART','last_seen_utc':'$(now_utc)','state':'${FINAL_STATE}','runs_since_boot':1,'note':'bootstrap self-recovery finished rc=${RC}'}))")" >/dev/null 2>&1
echo "HEARTBEAT: bootstrap finished - state=$FINAL_STATE (rc=$RC)."
exit "$RC"
