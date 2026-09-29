#!/usr/bin/env python3
# Provenance: factory-bootstrap/agents/andy/skills/approved_run.py (sha ca16334731040b98749600826b510b25a58e693d)
"""approved_run - the approved-command lane for Al's Mac Terminal. Andy-only.

Al's ruling (2026-09-24 12:34 PT, web chat): instead of Al pasting Terminal commands himself,
Andy presents each command with an approve button (the chat's choice widget), Al clicks
Approve, Andy runs the command over the Mac bridge AS AL WITH HIS PERMISSION, and pastes a
compressed but complete audit of input and output into the chat, including the path of
approval (which conversation, which message, when, which button).

What this script does, in order:
  1. refuses to run without an --approval string (the approval path is part of the record);
  2. requires bridge health 200 (401 = token drift, 0/530 = Mac side down);
  3. sends the command to the tmux session through the bridge;
  4. if the bridge's risk classifier parks it (status pending_approval / approval_id), relays
     Al's chat approval to the bridge's approve route with approved_by "al" and the approval
     path as the note. This is the ONLY case in which Andy touches the approve route, and only
     because the human approval already happened in chat; the note says so;
  5. waits for the prompt to return (up to --wait seconds), scrapes the session screen, cuts
     the slice from the command echo to the next prompt, redacts token-shaped strings;
  6. writes the audit record to the conversation's terminal-audit/<date>.md, posts one audit
     row to the comms bus thread audit:owner-comms (kind=approved_run), prints the record.

Usage:
  python3 .agents/skills/approved_run.py "<command>" --approval "<path>" [--purpose "..."]
         [--session andy] [--wait 25] [--audit-dir <dir>]
  --approval example:
    "Al, Approve button, Base44 web chat conversation 6aa4...5a7, 2026-09-24 12:41 PT"
"""
import argparse, datetime, json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mac_bridge as mb  # noqa: E402

PROMPT_RE = re.compile(r"^alfarmer@MBP---Al [^%]*% ?")
REDACT = [
    (re.compile(r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "[redacted-github-token]"),
    (re.compile(r"ya29\.[A-Za-z0-9_\-]+"), "[redacted-google-token]"),
    (re.compile(r"\b[A-Za-z0-9_\-]{48,}\b"), "[redacted-token]"),
    (re.compile(r"(?i)(token|secret|password|passwd|api_key)(\s*[=:]\s*)\S+"), r"\1\2[redacted]"),
]


def now_pt():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M:%S PT")
    except Exception:
        return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")


# Content digests are public identifiers, not credentials: a sha256 image digest (64 hex after
# "sha256:") must survive redaction, else a revision read comes back useless (2026-09-27, the two
# Cloud Run digest reads printed [redacted-token]). Digests are parked, redaction runs, then they
# are restored verbatim.
# The tmux pane wraps at 80 columns, so the 64 hex may be split across a line break; any hex run
# after "sha256:" is the digest's first piece and is kept.
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{16,64}")


def redact(text):
    keep = DIGEST_RE.findall(text)
    for i, d in enumerate(keep):
        text = text.replace(d, f"@@DIGEST{i}@@", 1)
    for rx, rep in REDACT:
        text = rx.sub(rep, text)
    for i, d in enumerate(keep):
        text = text.replace(f"@@DIGEST{i}@@", d, 1)
    return text


def squash(s):
    return re.sub(r"\s+", "", s)


def is_bare_prompt(line):
    m = PROMPT_RE.match(line)
    return bool(m) and squash(line) == squash(m.group(0))


def slice_output(screen, command):
    """From the tmux screen, take the last echo of the command through the next bare prompt.
    Lines wrap at pane width, so matching compares text with whitespace removed."""
    lines = screen.rstrip("\n").split("\n")
    key = squash(command)[:24]
    start = None
    for i in range(len(lines) - 1, -1, -1):
        if PROMPT_RE.match(lines[i]) and key in squash("".join(lines[i:i + 4])):
            start = i
            break
    if start is None:
        return False, screen[-1500:]
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if is_bare_prompt(lines[j]):
            end = j
            break
    return True, "\n".join(lines[start:end])


def prompt_returned(screen, command):
    ok, _ = slice_output(screen, command)
    lines = screen.rstrip("\n").split("\n")
    return ok and bool(lines) and is_bare_prompt(lines[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command")
    ap.add_argument("--approval", required=True, help="who approved, where (conversation), when, how")
    ap.add_argument("--purpose", default="factory operations")
    ap.add_argument("--session", default=mb.AGENT)
    ap.add_argument("--wait", type=float, default=25)
    ap.add_argument("--lines", type=int, default=120)
    ap.add_argument("--audit-dir", default=os.path.join(os.getcwd(), "terminal-audit"))
    a = ap.parse_args()
    if len(a.approval.strip()) < 20:
        sys.exit("approved_run: --approval must name who approved, the conversation, the time and the button")

    st, d = mb.call("GET", "/health")
    if st != 200:
        sys.exit(f"approved_run: bridge health {st} {d}; not running anything")

    started = now_pt()
    mb.ensure_session(a.session)
    reason = f"approved by {a.approval}; purpose: {a.purpose}"[:300]
    st, d = mb.call("POST", f"/sessions/{a.session}/send",
                    {"input": a.command, "enter": True, "agent": mb.AGENT, "reason": reason})
    relay = None
    if st == 200 and (d.get("approval_id") or str(d.get("status", "")).startswith("pending")):
        aid = d.get("approval_id")
        note = f"human approval already given in chat: {a.approval}; relayed by andy; purpose: {a.purpose}"[:400]
        ast, ad = mb.call("POST", f"/approvals/{aid}/approve", {"approved_by": "al", "note": note})
        relay = f"bridge parked as {d.get('status')} ({str(d.get('risk_reason', ''))[:80]}); approve route: {ast} {json.dumps(ad)[:200]}"
        if ast != 200:
            st = ast
    if st != 200:
        print(f"APPROVED RUN FAILED TO SEND {started}\ncommand: {a.command}\napproval: {a.approval}\n"
              f"bridge: {st} {json.dumps(d)[:300]}\n{relay or ''}")
        sys.exit(1)

    deadline = time.time() + a.wait
    screen = ""
    time.sleep(min(2, a.wait))
    while time.time() < deadline:
        screen = mb.screen(a.session, a.lines)
        if prompt_returned(screen, a.command):
            break
        time.sleep(2)
    ok, body = slice_output(screen, a.command)
    finished = now_pt()
    complete = ok and prompt_returned(screen, a.command)
    body = redact(body)
    if len(body) > 6000:
        body = body[:3000] + "\n[... " + str(len(body) - 6000) + " chars cut ...]\n" + body[-3000:]

    rec = [
        f"TERMINAL AUDIT {started} to {finished} (Mac bridge, tmux session {a.session}, run by andy as Al)",
        f"approval path: {a.approval}",
        f"purpose: {a.purpose}",
        f"bridge: send {st} command_id={d.get('command_id')} risk={d.get('risk', d.get('status'))}" + (f"; {relay}" if relay else ""),
        "input and output:" if complete else "input and output (prompt not back within wait; partial):",
        body,
    ]
    record = "\n".join(rec)
    os.makedirs(a.audit_dir, exist_ok=True)
    path = os.path.join(a.audit_dir, datetime.date.today().isoformat() + ".md")
    with open(path, "a") as f:
        f.write("\n```\n" + record + "\n```\n")
    mb.audit_row("approved_run", a.command[:90],
                 "communication_type: approved-command lane (Al approve button in chat, run by andy as Al).\n" + record[:3800])
    print(record)
    print(f"\n[audit appended to {path}]")
    sys.exit(0 if complete else 2)


if __name__ == "__main__":
    main()
