#!/usr/bin/env python3
# Provenance: factory-bootstrap/_skills/gh_safe_commit.py (sha ca16334731040b98749600826b510b25a58e693d)
"""gh_safe_commit.py - idempotent, concurrency-safe commits to the canonical repo.

WHY: two agents (or two turns of the same agent) can edit the same canonical file at once.
The GitHub Contents API is optimistic-concurrency safe by SHA: a PUT with a stale blob sha
returns 409, so a clobber is impossible server-side. This helper turns that into a REAL
mechanism (not luck) by:
  1. ANCHOR EDITS ARE IDEMPOTENT: replace OLD->NEW only if OLD is present. If NEW is already
     present (another writer applied it), it's a no-op success -- never a duplicate/clobber commit.
  2. 409-SAFE RETRY: on a sha conflict, re-fetch fresh content+sha, RE-EVALUATE idempotency
     (maybe the other writer already applied our exact change), and retry. Loser of a race
     converges to a no-op instead of overwriting the winner.

USAGE
  Anchor mode (preferred for edits to existing files):
    gh_safe_commit.py --path <repo/path> --message "<msg>" \
        --anchor-old-file /tmp/old.txt --anchor-new-file /tmp/new.txt
  Whole-content mode (new files / full rewrites):
    gh_safe_commit.py --path <repo/path> --message "<msg>" --content-file /tmp/full.txt

ENV: GITHUB_ACCESS_TOKEN (then GITHUB_PAT, then GITHUB_TOKEN), first one set wins.
REPO defaults to Alternative-Spaces-Inc/fpcore-repo (--repo to override).
EXIT: 0 committed | 0 idempotent-noop | 2 anchor-not-found | 3 no token | 4 retries-exhausted
"""
import os, sys, json, time, base64, argparse, urllib.request, urllib.error

def _token():
    # FIX 2026-09-09 (Puck's 15:35Z finding, re-derived): ORDER MATTERS. GITHUB_TOKEN is the
    # stale variable on these seats and it used to be preferred, so a live GITHUB_ACCESS_TOKEN
    # sat unused behind a dead one and every write failed 401 with a token in the environment.
    # Same order as skill_restore.py, deliberately: two recovery skills that disagree about
    # which credential is live is the next drift.
    for v in ("GITHUB_ACCESS_TOKEN", "GITHUB_PAT", "GITHUB_TOKEN"):
        t = os.environ.get(v)
        if t:
            return t
    print("FATAL: no GITHUB_ACCESS_TOKEN / GITHUB_PAT / GITHUB_TOKEN in env", file=sys.stderr)
    sys.exit(3)

def _api(method, url, tok, data=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None, method=method)
    req.add_header("Authorization", f"Bearer {tok}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req) as r:
        return r.status, json.load(r)

def _get(path, tok, repo):
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    try:
        _, d = _api("GET", url, tok)
        return base64.b64decode(d["content"]).decode(), d["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    ap.add_argument("--message", required=True)
    # FIX 2026-09-09: org moved to Alternative-Spaces-Inc on 2026-08-15. The old path
    # 301-redirects, which the Contents API does not follow for a write.
    ap.add_argument("--repo", default="Alternative-Spaces-Inc/fpcore-repo")
    ap.add_argument("--anchor-old-file")
    ap.add_argument("--anchor-new-file")
    ap.add_argument("--content-file")
    ap.add_argument("--max-retries", type=int, default=5)
    a = ap.parse_args()
    tok = _token()

    anchor = bool(a.anchor_old_file and a.anchor_new_file)
    if not anchor and not a.content_file:
        print("FATAL: provide --anchor-old-file+--anchor-new-file OR --content-file", file=sys.stderr)
        sys.exit(2)
    old = open(a.anchor_old_file).read() if anchor else None
    new = open(a.anchor_new_file).read() if anchor else None
    whole = open(a.content_file).read() if a.content_file else None

    url = f"https://api.github.com/repos/{a.repo}/contents/{a.path}"
    for attempt in range(1, a.max_retries + 1):
        cur, sha = _get(a.path, tok, a.repo)

        if anchor:
            if cur is None:
                print(f"FATAL: {a.path} not found for anchor edit", file=sys.stderr); sys.exit(2)
            if new in cur and old not in cur:
                print(f"IDEMPOTENT: change already applied to {a.path} (no commit)"); sys.exit(0)
            if old not in cur:
                print(f"ANCHOR-NOT-FOUND: neither old anchor nor applied-new present in {a.path}", file=sys.stderr)
                sys.exit(2)
            payload_text = cur.replace(old, new, 1)
        else:
            if cur is not None and cur == whole:
                print(f"IDEMPOTENT: {a.path} already at desired content (no commit)"); sys.exit(0)
            payload_text = whole

        body = {"message": a.message, "content": base64.b64encode(payload_text.encode()).decode()}
        if sha:
            body["sha"] = sha
        try:
            _, d = _api("PUT", url, tok, body)
            print(f"COMMITTED {a.path} @ {d['commit']['sha'][:12]} (attempt {attempt})")
            sys.exit(0)
        except urllib.error.HTTPError as e:
            if e.code == 409:  # sha changed under us -> another writer won; re-eval + retry
                print(f"  409 conflict on {a.path} (attempt {attempt}) -> re-fetch + re-evaluate", file=sys.stderr)
                time.sleep(0.6 * attempt)
                continue
            raise
    print(f"FATAL: retries exhausted ({a.max_retries}) on {a.path}", file=sys.stderr)
    sys.exit(4)

if __name__ == "__main__":
    main()
