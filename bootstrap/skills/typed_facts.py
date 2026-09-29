#!/usr/bin/env python3
# Provenance: factory-bootstrap/_skills/typed_facts.py (sha ca16334731040b98749600826b510b25a58e693d)
"""typed_facts.py - read the typed operational-fact store from inside an agent's skills tree.
MARKER_TYPED_FACTS_SKILL_V1 (2026-09-02, broker-host retirement lane)

WHY THIS EXISTS. The broker-era skills in this directory each carried their own copy of a
live coordinate - a comms host, a GCP project, a repo owner - written into the file as a
literal. When a coordinate moved, every copy in every agent tree kept pointing at the dead
one, and a restored agent had no way to tell the difference between "this is the address"
and "this was the address in June". The typed state store (`memory/state/facts/*.yaml`,
MARKER_TYPED_STATE_FACTS_V1) is the only sanctioned home for a live coordinate, and the
generated `CURRENT.md` carries the same table into an agent payload that has no repository
checkout. This module reads the first, then the second, and FAILS CLOSED: there is no
baked-in default anywhere in this file, so a skill that cannot resolve a fact must report an
honest gap instead of guessing at a host.

USAGE - CLI form, which is what the shell skills use:
    python3 typed_facts.py get <fact>              # prints the value on stdout
    python3 typed_facts.py assert-live-edge <url>  # exit 0 only if <url> is the live edge
    python3 typed_facts.py --selftest              # proves both paths against a fixture

    exit 0 = resolved / allowed
    exit 3 = FACT UNAVAILABLE (no store reachable, or the store has no such fact)
    exit 4 = REFUSED: the url's host is not the live factory edge
    exit 2 = usage error

USAGE - import form, which is what the python skills use:
    from typed_facts import get_fact, assert_live_edge, FactUnavailable, NotTheLiveEdge

RESOLUTION ORDER (first source that yields the fact wins):
    1. $FACTORY_TYPED_FACTS_DIR                  explicit override, for tests and odd layouts
    2. memory/state/facts/<fact>.yaml            walking up from CWD, then from this file
    3. the "Verified operational facts" table of a generated CURRENT.md - .agents/CURRENT.md,
       ./CURRENT.md, then factory-bootstrap/agents/*/CURRENT.md under a discovered repo root

NEVER, by design: a hardcoded fallback value, a network call, a secret read, or a write. A
stale fact still resolves (the store records `verified_at`, and staleness is CI's job via
`memory/state/verify_state.py --check`); an ABSENT fact is a refusal.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# The fact whose VALUE is the live factory edge base URL. One name, one place, so a caller
# cannot drift from the store.
LIVE_EDGE_FACT = "factory_ui_api_base_is_lb_host"

_FACTS_SUBPATH = ("memory", "state", "facts")
_CURRENT_MD_ROW = re.compile(r"^\|\s*([A-Za-z0-9_]+)\s*\|\s*([^|]*?)\s*\|")
_YAML_VALUE = re.compile(r"^value:\s*(.*?)\s*$", re.MULTILINE)


class FactUnavailable(RuntimeError):
    """No reachable store carries the requested fact. The caller must report a gap."""


class NotTheLiveEdge(RuntimeError):
    """A url was aimed at a host that is not the live factory edge. Refuse to use it."""


# ---------------------------------------------------------------- store discovery
def _ancestors(start: Path) -> list[Path]:
    start = start.resolve()
    return [start, *start.parents]


def _candidate_fact_dirs() -> list[Path]:
    dirs: list[Path] = []
    override = os.environ.get("FACTORY_TYPED_FACTS_DIR", "").strip()
    if override:
        dirs.append(Path(override))
    for base in _ancestors(Path.cwd()) + _ancestors(Path(__file__).parent):
        candidate = base.joinpath(*_FACTS_SUBPATH)
        if candidate not in dirs:
            dirs.append(candidate)
    return dirs


def _candidate_current_mds() -> list[Path]:
    paths: list[Path] = []
    for base in _ancestors(Path.cwd()) + _ancestors(Path(__file__).parent):
        for rel in (Path(".agents") / "CURRENT.md", Path("CURRENT.md")):
            candidate = base / rel
            if candidate not in paths:
                paths.append(candidate)
        agents_dir = base / "factory-bootstrap" / "agents"
        if agents_dir.is_dir():
            for current in sorted(agents_dir.glob("*/CURRENT.md")):
                if current not in paths:
                    paths.append(current)
    return paths


# ---------------------------------------------------------------- parsing
def _strip_scalar(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        raw = raw[1:-1]
    return raw.strip()


def _value_from_yaml(text: str) -> str:
    """The fact files are flat `key: value` documents; only `value:` is needed here.

    pyyaml is used when it happens to be importable, because it handles the quoting forms
    exactly; the regex fallback keeps this skill working in a container that has no
    third-party packages, which is the normal state of a freshly reset agent.
    """
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        if isinstance(loaded, dict) and "value" in loaded:
            return str(loaded["value"]).strip()
    except Exception:
        pass
    match = _YAML_VALUE.search(text)
    if not match:
        return ""
    return _strip_scalar(match.group(1))


def _value_from_current_md(text: str, fact: str) -> str:
    for line in text.splitlines():
        match = _CURRENT_MD_ROW.match(line.strip())
        if match and match.group(1) == fact:
            return match.group(2).strip()
    return ""


# ---------------------------------------------------------------- public API
def get_fact(fact: str) -> str:
    """Return the recorded value of `fact`, or raise FactUnavailable. Never guesses."""
    if not fact or not re.fullmatch(r"[A-Za-z0-9_]+", fact):
        raise FactUnavailable(f"not a fact name: {fact!r}")
    for facts_dir in _candidate_fact_dirs():
        path = facts_dir / f"{fact}.yaml"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        value = _value_from_yaml(text)
        if value:
            return value
    for current in _candidate_current_mds():
        try:
            text = current.read_text(encoding="utf-8")
        except OSError:
            continue
        value = _value_from_current_md(text, fact)
        if value:
            return value
    raise FactUnavailable(
        f"fact {fact!r} is not resolvable from this working directory. Looked for "
        f"memory/state/facts/{fact}.yaml and for the generated CURRENT.md fact table. "
        "OWNER-ACTION: run from a checkout of the factory repo, or set "
        "FACTORY_TYPED_FACTS_DIR to the facts directory. This skill does not guess a value."
    )


def live_edge_base() -> str:
    """The live factory edge base URL, from the typed state store."""
    value = get_fact(LIVE_EDGE_FACT)
    if not value.startswith("https://"):
        raise FactUnavailable(
            f"fact {LIVE_EDGE_FACT!r} is {value!r}, which is not an https base URL. The store "
            "is the authority for this coordinate, so a bad value there is a RED to fix in "
            "the store (memory/state/verify_state.py --stamp), not to work around here."
        )
    return value.rstrip("/")


def _host_of(url: str) -> str:
    remainder = url.split("://", 1)[1] if "://" in url else url
    return remainder.split("/", 1)[0].split("?", 1)[0].strip().lower()


def assert_live_edge(url: str) -> str:
    """Refuse any url whose host is not the live factory edge. Returns the edge base.

    This is the fail-closed half of the retirement: a restored container can still be
    carrying an old `.env` that names a decommissioned factory host, and a skill that
    silently honours it is a skill pointed at a host we do not control. The live edge is
    read from the store on every call, so this check follows the coordinate when it moves
    and needs no edit here.
    """
    edge = live_edge_base()
    if not url or not url.strip():
        raise NotTheLiveEdge("empty url: nothing to check, and nothing safe to assume")
    candidate = _host_of(url.strip())
    expected = _host_of(edge)
    if candidate != expected:
        raise NotTheLiveEdge(
            f"host {candidate!r} is not the live factory edge {expected!r} (typed fact "
            f"{LIVE_EDGE_FACT}). REFUSING. A retired or third-party factory host must never be "
            "honoured from a restored environment; fix the environment, not this check."
        )
    return edge


# ---------------------------------------------------------------- selftest
def _selftest() -> int:
    """Prove the resolver both ways against a temporary fixture. No network, no repo needed."""
    import tempfile

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    saved = os.environ.get("FACTORY_TYPED_FACTS_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        facts = Path(tmp) / "facts"
        facts.mkdir()
        (facts / "gcp_project.yaml").write_text(
            "fact: gcp_project\nvalue: fixture-project-000\ntier: repo\n", encoding="utf-8"
        )
        (facts / f"{LIVE_EDGE_FACT}.yaml").write_text(
            f"fact: {LIVE_EDGE_FACT}\nvalue: https://edge.fixture.invalid\ntier: mac\n",
            encoding="utf-8",
        )
        os.environ["FACTORY_TYPED_FACTS_DIR"] = str(facts)
        try:
            check("get_fact reads the store", get_fact("gcp_project") == "fixture-project-000")
            check("live_edge_base reads the store", live_edge_base() == "https://edge.fixture.invalid")
            check(
                "assert_live_edge allows the live edge",
                assert_live_edge("https://edge.fixture.invalid/api/v1/x") == "https://edge.fixture.invalid",
            )
            try:
                assert_live_edge("https://some-other-host.invalid/api/v1/x")
                check("assert_live_edge refuses another host", False, "it allowed it")
            except NotTheLiveEdge:
                check("assert_live_edge refuses another host", True)
            empty = Path(tmp) / "empty"
            empty.mkdir()
            os.environ["FACTORY_TYPED_FACTS_DIR"] = str(empty)
            try:
                get_fact("no_such_fact_anywhere")
                check("absent fact fails closed", False, "it returned a value")
            except FactUnavailable:
                check("absent fact fails closed", True)
        finally:
            if saved is None:
                os.environ.pop("FACTORY_TYPED_FACTS_DIR", None)
            else:
                os.environ["FACTORY_TYPED_FACTS_DIR"] = saved

    if failures:
        print(f"TYPED_FACTS_SELFTEST: RED - {len(failures)} check(s) failed")
        return 1
    print("TYPED_FACTS_SELFTEST: GREEN")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 1 or argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if argv[1] == "--selftest":
        return _selftest()
    if argv[1] == "get" and len(argv) == 3:
        try:
            print(get_fact(argv[2]))
        except FactUnavailable as exc:
            sys.stderr.write(f"typed_facts: FACT UNAVAILABLE: {exc}\n")
            return 3
        return 0
    if argv[1] == "assert-live-edge" and len(argv) == 3:
        try:
            print(assert_live_edge(argv[2]))
        except NotTheLiveEdge as exc:
            sys.stderr.write(f"typed_facts: REFUSED: {exc}\n")
            return 4
        except FactUnavailable as exc:
            sys.stderr.write(f"typed_facts: FACT UNAVAILABLE: {exc}\n")
            return 3
        return 0
    sys.stderr.write("usage: typed_facts.py {get <fact>|assert-live-edge <url>|--selftest}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
