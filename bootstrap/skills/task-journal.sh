#!/usr/bin/env bash
# Provenance: factory-bootstrap/_skills/task-journal.sh (sha ca16334731040b98749600826b510b25a58e693d)
# task-journal - hardened interruption-recovery. See ../SKILL.md.
# Repo-backed append-only JSONL journal; state derived by folding events.
set -uo pipefail
source .agents/.env 2>/dev/null || true

REPO="Alternative-Spaces-Inc/fpcore-repo"
AGENT="${TASK_JOURNAL_AGENT:-canon}"
JPATH="src/superagent-memory/task-journal/${AGENT}.journal.jsonl"
API="https://api.github.com/repos/${REPO}/contents/${JPATH}"
GH="${GITHUB_TOKEN:-}"

if [ -z "$GH" ]; then echo "ERROR: GITHUB_TOKEN not in env"; exit 1; fi

_SHAFILE="$(mktemp)"
trap 'rm -f "$_SHAFILE"' EXIT
_fetch() {  # prints journal text to stdout; writes SHA to $_SHAFILE (survives $(...) subshell)
  local resp; resp=$(curl -s -H "Authorization: token ${GH}" "${API}?ref=main" 2>/dev/null)
  printf '%s' "$resp" | python3 -c "import sys,json
try: sha=json.load(sys.stdin).get('sha','')
except: sha=''
open('${_SHAFILE}','w').write(sha)" 2>/dev/null
  # content via RAW accept (freshest HEAD; avoids cached-blob propagation lag)
  curl -s -H "Authorization: token ${GH}" -H "Accept: application/vnd.github.raw" "${API}?ref=main" 2>/dev/null
}

_append() {  # $1 = JSON line to append, $2 = commit note. Retries on SHA conflict (concurrent-write hardening).
  local newline="$1" note="$2" attempt=0 rc
  while [ "$attempt" -lt 4 ]; do
    attempt=$((attempt+1))
    local cur; cur="$(_fetch)"   # re-fetch each attempt -> fresh SHA (mem #871/#873)
    local esha; esha="$(cat "$_SHAFILE" 2>/dev/null)"
    local combined
    if [ -n "$cur" ]; then combined="${cur}"$'\n'"${newline}"; else combined="${newline}"; fi
    local b64; b64=$(printf '%s\n' "$combined" | base64 -w0)
    local payload; payload=$(python3 -c "import json,sys
d={'message':'task-journal(${AGENT}): '+sys.argv[1],'content':sys.argv[2],'branch':'main'}
sha=sys.argv[3]
if sha: d['sha']=sha
print(json.dumps(d))" "$note" "$b64" "${esha:-}")
    rc=$(curl -s -X PUT -H "Authorization: token ${GH}" -H "Content-Type: application/json" \
      "$API" -d "$payload" | python3 -c "import sys,json
try:
    d=json.load(sys.stdin); c=d.get('commit',{}).get('sha')
    print('OK '+c[:8]) if c else print('RETRY '+str(d.get('message','conflict')))
except Exception as e: print('RETRY '+str(e))")
    case "$rc" in
      OK\ *) echo "committed ${rc#OK }"; return 0;;
      *) sleep 1;;
    esac
  done
  echo "append-FAILED after $attempt attempts: $rc"; return 1
}

_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

cmd="${1:-status}"; shift || true

case "$cmd" in
  begin)
    tid="${1:?task_id}"; goal="${2:?goal}"; steps="${3:-}"; proof="${4:-}"
    line=$(python3 -c "import json,sys
print(json.dumps({'ev':'begin','task_id':sys.argv[1],'goal':sys.argv[2],
'steps':[s for s in sys.argv[3].split('|') if s],'proof_required':sys.argv[4],'ts':sys.argv[5]}))" \
      "$tid" "$goal" "$steps" "$proof" "$(_now)")
    _append "$line" "begin $tid"
    ;;
  step)
    tid="${1:?task_id}"; no="${2:?step_no}"; label="${3:?label}"; ev="${4:-}"; next="${5:-}"
    line=$(python3 -c "import json,sys
print(json.dumps({'ev':'step','task_id':sys.argv[1],'step_no':sys.argv[2],'label':sys.argv[3],
'status':'done','evidence':sys.argv[4],'next':sys.argv[5],'ts':sys.argv[6]}))" \
      "$tid" "$no" "$label" "$ev" "$next" "$(_now)")
    _append "$line" "step $tid #$no"
    ;;
  note)
    tid="${1:?task_id}"; txt="${2:?text}"
    line=$(python3 -c "import json,sys
print(json.dumps({'ev':'note','task_id':sys.argv[1],'text':sys.argv[2],'ts':sys.argv[3]}))" \
      "$tid" "$txt" "$(_now)")
    _append "$line" "note $tid"
    ;;
  done)
    tid="${1:?task_id}"; proof="${2:?proof}"
    line=$(python3 -c "import json,sys
print(json.dumps({'ev':'done','task_id':sys.argv[1],'proof':sys.argv[2],'ts':sys.argv[3]}))" \
      "$tid" "$proof" "$(_now)")
    _append "$line" "done $tid"
    ;;
  abort)
    tid="${1:?task_id}"; reason="${2:?reason}"
    line=$(python3 -c "import json,sys
print(json.dumps({'ev':'abort','task_id':sys.argv[1],'reason':sys.argv[2],'ts':sys.argv[3]}))" \
      "$tid" "$reason" "$(_now)")
    _append "$line" "abort $tid"
    ;;
  status|resume)
    txt="$(_fetch)"
    printf '%s\n' "$txt" | python3 -c "
import sys,json
mode='$cmd'
tasks={}
order=[]
for ln in sys.stdin:
    ln=ln.strip()
    if not ln: continue
    try: e=json.loads(ln)
    except: continue
    tid=e.get('task_id')
    if not tid: continue
    if tid not in tasks:
        tasks[tid]={'goal':'','steps_done':[],'last_next':'','closed':False,'proof':'','proof_req':''}
        order.append(tid)
    t=tasks[tid]; ev=e.get('ev')
    if ev=='begin': t['goal']=e.get('goal',''); t['proof_req']=e.get('proof_required','')
    elif ev=='step': t['steps_done'].append((e.get('step_no'),e.get('label'),e.get('evidence'))); t['last_next']=e.get('next','')
    elif ev=='done': t['closed']=True; t['proof']=e.get('proof','')
    elif ev=='abort': t['closed']=True; t['proof']='ABORTED: '+e.get('reason','')
opens=[tid for tid in order if not tasks[tid]['closed']]
if not opens:
    print('RESUME GATE: no open tasks. Clean - nothing was interrupted.' if mode=='resume' else 'STATUS: 0 open tasks.')
    sys.exit(0)
MUT=('deploy','gcloud run','kubectl set','kubectl apply','rotate','iam ','secret','set-image','delete','domain-mapping','PUT ','PATCH ','POST ')
print(('RESUME GATE - %d OPEN task(s) (possible interruption):' % len(opens)) if mode=='resume' else ('STATUS - %d open task(s):' % len(opens)))
for tid in opens:
    t=tasks[tid]
    print('----')
    print(' task_id :',tid)
    print(' goal    :',t['goal'])
    print(' proof_req:',t['proof_req'])
    if t['steps_done']:
        last=t['steps_done'][-1]
        print(' last landed step: #%s %s | evidence: %s' % (last[0],last[1],last[2]))
    else:
        print(' last landed step: (none yet - interrupted before first checkpoint)')
    nxt=t['last_next'] or '(no explicit next recorded - re-derive from goal)'
    print(' NEXT    :',nxt)
    if mode=='resume':
        low=nxt.lower()
        mutating=any(m.lower() in low for m in MUT)
        if mutating:
            print(' CLASS   : MUTATING -> STOP. Do NOT auto-run. Surface to operator for confirm.')
        else:
            print(' CLASS   : SAFE (read-only/idempotent) -> re-verify last step LIVE, then continue.')
print('----')
if mode=='resume':
    print('PROTOCOL: SAFE next -> re-verify last landed step against live truth (HEAD re-read/probe), then continue.')
    print('          MUTATING next -> tell operator + confirm before running. Close with: task-journal done <id> <proof>.')
"
    ;;
  *)
    echo "usage: task-journal {begin|step|note|done|abort|status|resume} ..."; exit 1;;
esac
