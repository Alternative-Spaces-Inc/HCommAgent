#!/usr/bin/env python3
# Provenance: factory-bootstrap/_skills/skill_restore.py (sha ca16334731040b98749600826b510b25a58e693d)
"""skill_restore.py - Domain 1 auto-restore: recover SKILL files from the FACTORY CANON.
MARKER_AGENT_SELF_RECOVERY_V1  (S2)  Design: AGENT_SELF_RECOVERY_DESIGN_20260623.md (Dale, Al-directed).
MARKER_RESTORE_SOURCE_IS_CANON_V1 (2026-09-09, Andy) - the change this file exists to carry.

WHY THE SOURCE MOVED. Until 2026-09-09 this program restored from the CLIENT platform repository's
memory tree, while the CI guard that proves the broker-era coordinates are gone
(factory-bootstrap/platform/test_broker_era_skills_retired.py) walked only the FACTORY repository's
agent payload trees. Two trees, one gate: the guard was green by construction while every recovery
arm wrote broker-era bytes back over the fix. Measured by Dale on 2026-09-09, reproduced by Maya.
The fix is one tree. The factory repository is canon, the guard and the restore read the same tree,
and no cross-repository credential is needed by either.

WHAT IT DOES. For each manifest skill, compare the local file to the canon blob at a pinned ref.
LIVE skill: if the local file is MISSING or DIFFERS, fetch the canon version (Contents API) and
write it to .agents/skills/, preserving +x. A live skill that is NOT in canon is RED, never SKIP:
a recovery arm that shrugs at a missing source is the same failed-fetch-passes-vacuously shape the
guard was built to catch, one layer down.
RETIRED skill: never fetched from the client repository and never left alone. Absent locally is
RETIRED and NOT red, because an expected retirement is not a gap. Present and equal to the
tombstone is OK. Present and DIFFERENT is REPLACED with the tombstone body from canon _retired/,
and the run stays GREEN, because replacing a live broker body with its tombstone is the success
case, not a fault.

PROVE-BY-USE, not by presence (MARKER_SHARED_ANCHOR_CAPABILITY_WIRE_V1): a written file is a claim;
a passing selftest is proof.

Usage:
  python3 skill_restore.py             # restore all manifest skills from canon HEAD (or RECOVERY_REF)
  python3 skill_restore.py --check     # report drift only, write nothing
  python3 skill_restore.py --selftest  # offline manifest proof: no token, no network, no writes
  python3 skill_restore.py <name>      # restore a single skill by canon filename

Auth: GITHUB_ACCESS_TOKEN must be live (mint via connector first). Never logs secrets.
Exit 0 = all targeted skills present-and-current (and selftest-proven where applicable); non-0 otherwise.
"""
import os, sys, json, base64, subprocess, urllib.request, urllib.error

# THE ONE TREE. Both defaults name the factory repository, and
# factory-bootstrap/platform/test_broker_era_skills_retired.py parses these DEFAULTS - not the
# environment - in every copy of this file in the repository, with a seeded-RED fixture that
# proves the parse can fail. The env overrides below stay for an operator pinning a known-good
# SHA; they are not a way to point the fleet at another repository by accident.
REPO = os.environ.get("RECOVERY_REPO", "Alternative-Spaces-Inc/fpcore-migration-agent")
REF  = os.environ.get("RECOVERY_REF", "main")            # pin to a known-good SHA in prod
SRC_DIR = "factory-bootstrap/_skills"
RETIRED_SRC_DIR = "factory-bootstrap/_skills/_retired"
DEST = os.environ.get("SKILLS_DIR", ".agents/skills")
TOK = os.environ.get("GITHUB_ACCESS_TOKEN", "") or os.environ.get("GITHUB_PAT", "") or os.environ.get("GITHUB_TOKEN", "")

# MARKER_SELFRECOVER_REINSTALLS_BACKUP_V1 (2026-09-08, Andy): a manifest entry may name its own
# repo, ref and source dir. Since MARKER_RESTORE_SOURCE_IS_CANON_V1 the canon IS the default, so
# these are aliases rather than an exception, and they are kept because #606 introduced the shape
# and an operator pinning canon to a SHA uses them.
CANON_REPO = os.environ.get("CANON_REPO", "Alternative-Spaces-Inc/fpcore-migration-agent")
CANON_REF  = os.environ.get("CANON_REF", "main")
CANON_SRC_DIR = "factory-bootstrap/_skills"

# Manifest: canon filename -> {local path, executable?, retired?, selftest cmd (optional)}.
# 19 live + 17 retired = 36. A retired entry resolves to _retired/ and may never name any repo but
# the factory one: that is asserted by the guard and by --selftest below.
MANIFEST = {
    # ---------------------------------------------------------------- LIVE (18)
    "sm_restore.py":              {"local": "sm_restore.py",              "x": True,  "selftest": ["python3","{p}","--selftest"]},
    "secret-parity-check.sh":     {"local": "secret-parity-check.sh",     "x": True,  "selftest": None},
    "agent_selfrecover.sh":       {"local": "agent_selfrecover.sh",       "x": True,  "selftest": None},
    "typed_facts.py":             {"local": "typed_facts.py",             "x": False, "selftest": ["python3","{p}","--selftest"]},
    "gh_safe_commit.py":          {"local": "gh_safe_commit.py",          "x": True,  "selftest": None},
    "gcp_auth.sh":                {"local": "gcp_auth.sh",                "x": True,  "selftest": None},
    "regression-gap.py":          {"local": "regression-gap.py",          "x": True,  "selftest": None},
    "sandbox_recovery.sh":        {"local": "sandbox_recovery.sh",        "x": True,  "selftest": None},
    "shift-load.sh":              {"local": "shift-load.sh",              "x": True,  "selftest": None},
    "shift-save.sh":              {"local": "shift-save.sh",              "x": True,  "selftest": None},
    "task-journal.sh":            {"local": "task-journal.sh",            "x": True,  "selftest": None},
    "agent_readiness_signal.py":  {"local": "agent_readiness_signal.py",  "x": False, "selftest": None},
    "selfheal-guard.sh":          {"local": "selfheal-guard.sh",          "x": True,  "selftest": None},
    "skill_restore.py":           {"local": "skill_restore.py",           "x": False, "selftest": ["python3","{p}","--selftest"]},
    "boot_heartbeat.sh":          {"local": "boot_heartbeat.sh",          "x": True,  "selftest": None},
    # The two whose only source has always been canon. The selftest is --check: it proves the
    # installed copy runs and writes nothing.
    "agent_backup_to_canon.sh":   {"local": "agent_backup_to_canon.sh",   "x": True,  "selftest": ["bash","{p}","--check"]},
    "turn_start_pull.sh":         {"local": "turn_start_pull.sh",         "x": True,  "selftest": None},
    # MARKER_SEAT_SANDBOX_BOOTSTRAP_V1 (2026-09-16): the factory administrator seat's own reset
    # recovery; --selftest proves the installed copy runs, finds the repo and finds canon, writes
    # nothing and touches no network (the full --check ran 47 s here against a 40 s budget).
    "seat_sandbox_bootstrap.sh":  {"local": "seat_sandbox_bootstrap.sh",  "x": True,  "selftest": ["bash","{p}","--selftest"]},
    "sandbox-bootstrap.sh":       {"local": "sandbox-bootstrap.sh",       "x": True,  "selftest": ["bash","{p}","--selftest"]},
    # MARKER_FACTORY_RESTART_SKILL_V1 (2026-09-28): crew-wide restart through factory-ops (spec item 4).
    # --selftest proves the installed copy parses its allowlist and finds curl and python3; no network.
    "factory_restart.sh":         {"local": "factory_restart.sh",         "x": True,  "selftest": ["bash","{p}","--selftest"]},

    # ---------------------------------------------------------------- RETIRED (17)
    # Every one of these is on RETIRED_SKILLS in
    # factory-bootstrap/platform/test_broker_era_skills_retired.py. They are listed HERE, rather
    # than deleted from the manifest, precisely so that a restore NEUTRALISES an installed copy
    # instead of leaving whatever the container came up with. A retirement that only removes the
    # manifest line leaves the broker-era body running on every seat that already has it.
    #
    # mac_ssh.py moved from LIVE to RETIRED on 2026-09-09 and is the reason that paragraph is not
    # theoretical. It was the remote-access client of the Mac terminal lane, which was retired on
    # 2026-08-25; measured across the whole repository, no skill, rule or bootstrap has run it
    # since. Its LIVE entry here was the last thing keeping it alive, and what a live entry does is
    # INSTALL: every seat that reset got a working remote-access client written into its skills
    # directory for a lane that no longer exists. A seat sandbox that refuses remote-access tooling
    # reads the file as bytes, so that install is also a plausible way to block a seat from
    # recovering at all (hank, 2026-09-09, one file over in agent_selfrecover.sh). Retired rather
    # than deleted, for the reason above: an installed copy is now REPLACED with the tombstone.
    "session_hydrate.py":         {"local": "session_hydrate.py",         "x": False, "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "night_shift_supervisor.py":  {"local": "night_shift_supervisor.py",  "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "overnight_factory_watch.py": {"local": "overnight_factory_watch.py", "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "comms-gate.sh":              {"local": "comms-gate.sh",              "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "bridge_url_resolve.sh":      {"local": "bridge_url_resolve.sh",      "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "bridge_url_recover.sh":      {"local": "bridge_url_recover.sh",      "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "bridge_sync.sh":             {"local": "bridge_sync.sh",             "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "precheck-al-command.sh":     {"local": "precheck-al-command.sh",     "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "mac_ssh.py":                 {"local": "mac_ssh.py",                 "x": False, "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "pulse_quiesce.sh":           {"local": "pulse_quiesce.sh",           "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "fn007_p2_close.sh":          {"local": "fn007_p2_close.sh",          "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "secret-parity-check.sh.bak": {"local": "secret-parity-check.sh.bak", "x": False, "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "comms_drain/scripts/drain.py":       {"local": "comms_drain/scripts/drain.py",       "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "comms_drain/scripts/owed.py":        {"local": "comms_drain/scripts/owed.py",        "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "comms_drain/scripts/run.sh":         {"local": "comms_drain/scripts/run.sh",         "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "comms_drain/scripts/clear_queue.py": {"local": "comms_drain/scripts/clear_queue.py", "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
    "comms_drain/scripts/set_state.py":   {"local": "comms_drain/scripts/set_state.py",   "x": True,  "retired": True, "src": RETIRED_SRC_DIR, "selftest": None},
}


def gh_blob(path, repo=None, ref=None):
    """Return (text, sha) for a repo file via Contents API, or (None, None) if absent."""
    repo = repo or REPO
    ref = ref or REF
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
    req = urllib.request.Request(url, headers={"Authorization":"Bearer "+TOK,"Accept":"application/vnd.github+json"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=25))
    except urllib.error.HTTPError as e:
        if e.code == 404: return (None, None)
        raise
    content = base64.b64decode(d["content"]).decode("utf-8", "replace")
    return (content, d["sha"])


def local_text(p):
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f: return f.read()
    except FileNotFoundError:
        return None


def norm(s):  # ignore trailing-newline / CRLF noise in drift compare
    return None if s is None else s.replace("\r\n","\n").rstrip("\n")


def write_out(dest, text, executable):
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(text)
    if executable:
        try: os.chmod(dest, 0o755)
        except Exception: pass


def run_selftest(cmd, path):
    real = [c.replace("{p}", path) for c in cmd]
    try:
        r = subprocess.run(real, capture_output=True, text=True, timeout=40)
        ok = r.returncode == 0
        return ok, (r.stdout.strip().splitlines()[-1] if r.stdout.strip() else f"exit {r.returncode}")
    except Exception as e:
        return False, f"selftest error: {e}"


def selftest():
    """Offline proof of the manifest's own invariants. No token, no network, no writes.

    This is what makes the restore arm checkable on a seat that has no credential: the property
    that matters most about this file - that it names ONE tree, the factory one - is decidable
    from the file alone.
    """
    bad = []
    live = [n for n, m in MANIFEST.items() if not m.get("retired")]
    retired = [n for n, m in MANIFEST.items() if m.get("retired")]
    for name, meta in MANIFEST.items():
        where = meta.get("repo", REPO)
        src = meta.get("src", RETIRED_SRC_DIR if meta.get("retired") else SRC_DIR)
        if where != REPO or "fpcore-migration-agent" not in where:
            bad.append(f"{name}: names repo {where!r}; the only recovery source is the factory repo")
        if not src.startswith("factory-bootstrap/"):
            bad.append(f"{name}: source dir {src!r} is outside factory-bootstrap/")
        if meta.get("retired") and src != RETIRED_SRC_DIR:
            bad.append(f"{name}: retired entry must resolve to {RETIRED_SRC_DIR}, not {src!r}")
        if not meta.get("retired") and src != SRC_DIR:
            bad.append(f"{name}: live entry must resolve to {SRC_DIR}, not {src!r}")
        if "local" not in meta or "x" not in meta:
            bad.append(f"{name}: entry is missing 'local' or 'x'")
    if not SRC_DIR.startswith("factory-bootstrap/") or not RETIRED_SRC_DIR.startswith("factory-bootstrap/"):
        bad.append("SRC_DIR / RETIRED_SRC_DIR must both live under factory-bootstrap/")
    if "fpcore-migration-agent" not in REPO:
        bad.append(f"REPO default {REPO!r} is not the factory repository")
    if bad:
        print("SKILL RESTORE SELFTEST: RED")
        for b in bad:
            print("  " + b)
        return 1
    print(f"SKILL RESTORE SELFTEST: GREEN - {len(live)} live + {len(retired)} retired = "
          f"{len(MANIFEST)} entries, every one resolving to {REPO} under factory-bootstrap/.")
    return 0


def main():
    args = [a for a in sys.argv[1:]]
    if "--selftest" in args:
        sys.exit(selftest())
    if not TOK:
        print("RED: no github token (checked GITHUB_ACCESS_TOKEN, GITHUB_PAT, GITHUB_TOKEN) - mint the github connector token first."); sys.exit(2)
    check_only = "--check" in args
    targets = [a for a in args if not a.startswith("--")]
    items = {k:v for k,v in MANIFEST.items() if (not targets or k in targets)}

    red = 0
    print(f"SKILL RESTORE - canon={REPO}@{REF} ({SRC_DIR}, retired in {RETIRED_SRC_DIR})  "
          f"mode={'check' if check_only else 'restore'}")
    print(f"  {'SKILL':30} | {'ACTION':10} | PROOF")

    def say(name, action, proof):
        print(f"  {name:30} | {action:10} | {proof}")

    for name, meta in items.items():
        dest = os.path.join(DEST, meta["local"])
        is_retired = bool(meta.get("retired"))
        src_repo = meta.get("repo", REPO)
        src_ref = meta.get("ref", REF)
        src_dir = meta.get("src", RETIRED_SRC_DIR if is_retired else SRC_DIR)
        loc = local_text(dest)

        if is_retired:
            # An expected retirement is not a gap. Nothing is fetched and nothing is written when
            # the seat does not have the file at all.
            if loc is None:
                say(name, "RETIRED", "absent by decision; nothing written, nothing to heal")
                continue
            tomb, _ = gh_blob(f"{src_dir}/{name}", src_repo, src_ref)
            if tomb is None:
                say(name, "RED", f"tombstone missing from {src_repo}/{src_dir}/ - cannot neutralise an installed copy")
                red = 1
                continue
            if norm(loc) == norm(tomb):
                say(name, "OK", "tombstone in place")
                continue
            if check_only:
                say(name, "REPLACE", "a non-tombstone body is installed; a run replaces it with the tombstone")
                red = 1
                continue
            write_out(dest, tomb, meta["x"])
            say(name, "REPLACED", "retired body replaced with its tombstone (this is the success case)")
            continue

        blob, _ = gh_blob(f"{src_dir}/{name}", src_repo, src_ref)
        if blob is None:
            # NOT "SKIP". A live skill whose canon source is gone is a hole in the recovery arm,
            # and a fetch that fails quietly is exactly how a gate passes vacuously.
            say(name, "RED", f"missing from canon {src_repo}/{src_dir}/ - a live skill with no source is a gap")
            red = 1
            continue
        drift = norm(loc) != norm(blob)
        if not drift:
            action = "OK"
        elif check_only:
            say(name, "DRIFT" if loc is not None else "MISSING", "would restore from canon")
            red = 1
            continue
        else:
            write_out(dest, blob, meta["x"])
            action = "RESTORED" if loc is not None else "FETCHED"
        proof = ("present, matches canon" if action == "OK"
                 else f"written from {src_repo}@{src_ref}")
        if meta.get("selftest"):
            ok, line = run_selftest(meta["selftest"], dest)
            proof = f"selftest: {line}"
            if not ok:
                action = "RED"; red = 1
        say(name, action, proof)

    print()
    if red == 0:
        print("RESULT: GREEN - every targeted live skill is present-and-current from canon, and every "
              "retired one is absent or a tombstone.")
    else:
        print("RESULT: RED - drift/missing/failed-selftest above. Re-run without --check to restore.")
    sys.exit(red)


if __name__ == "__main__":
    main()
