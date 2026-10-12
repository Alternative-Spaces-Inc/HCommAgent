#!/usr/bin/env python3
"""Validate local file/heading links in maintained documentation entry points."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md",
           "docs/INDEX.md", "docs/STATUS.md", "docs/ARCHITECTURE.md", "docs/CASE_STUDY.md",
           "docs/OPERATIONS.md", "docs/THREAT_MODEL.md", "docs/SECURITY_REMEDIATION_01_12.md",
           "docs/SECURITY_REMEDIATION_13_23.md", "docs/SECURITY_REMEDIATION_24_43.md", "docs/RELEASES.md", "docs/webhooks.md",
           "docs/runbooks/HCA_DEPLOY_ROLLBACK.md", "docs/runbooks/HCA_MIGRATION_RECOVERY.md", "extension/README.md", "macos/README.md"]


def headings(source):
    anchors, counts = set(), {}
    for line in source.read_text().splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        slug = re.sub(r"[^\w\- ]", "", match.group(1).lower()).replace(" ", "-")
        previous = counts.get(slug, 0)
        anchors.add(slug if previous == 0 else f"{slug}-{previous}")
        counts[slug] = previous + 1
    return anchors


def main():
    errors = []
    for name in CURRENT:
        source = ROOT / name
        if not source.is_file():
            errors.append(name + ": missing maintained entry point")
            continue
        for target in re.findall(r"\]\(([^\s)]+)(?:\s+[^)]*)?\)", source.read_text()):
            parsed = urlsplit(target.strip("<>"))
            if parsed.scheme or parsed.netloc:
                continue
            resolved = (source.parent / unquote(parsed.path)).resolve() if parsed.path else source
            if not resolved.is_file():
                errors.append(name + ": " + target)
            elif parsed.fragment and resolved.suffix == ".md" and unquote(parsed.fragment) not in headings(resolved):
                errors.append(name + ": missing heading " + target)
    for error in errors:
        print("FAIL: " + error)
    if errors:
        return 1
    print(f"OK: local file/heading links in {len(CURRENT)} maintained entry points")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
