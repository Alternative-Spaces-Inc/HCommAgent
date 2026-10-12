#!/usr/bin/env python3
"""pack_extension.py (HCA-P17): build the Chrome Web Store zip of extension/.

Usage:
  python3 scripts/pack_extension.py --out dist [--commit <sha>]

What goes in: manifest.json, background.js, popup.html, popup.js,
options.html, options.js, lib/*.js, icons/*.png. What stays out: tests/,
package.json, package-lock.json, anything dotfile. The manifest is copied
unchanged except for "version_name", set to "<version>+<commit7>" when a
commit is given, so a store listing shows which main commit built it.
The "key" field is removed from the zipped manifest: the Chrome Web Store
refuses an upload whose manifest carries one ("key field is not allowed in
manifest", verified on the first draft upload, 2026-10-07). The repo
manifest keeps the key so an unpacked load on the owner's Mac keeps its
development ID; the store assigns its own ID to the published item (plan,
Path B). Pass --keep-key only for a zip meant for an unpacked load.
Nothing here reads a private key.

Output: dist/extension-<version>-<commit7|local>.zip, a SHA256 sidecar and one JSON line on
stdout: {"zip": ..., "version": ..., "version_name": ..., "key_present":
false, "files": N, "sha256": ...}. Exit 1 if a required file is missing or
the manifest version is not a plain dotted number.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(ROOT, "extension")
REQUIRED = ["manifest.json", "background.js", "popup.html", "popup.js", "options.html", "options.js"]


def collect():
    # Ship the declared runtime surface, not arbitrary local files that happen
    # to be under extension/. This excludes tooling, docs and private scratch data.
    files = list(REQUIRED)
    for directory, suffix in (("lib", ".js"), ("icons", ".png")):
        for dirpath, dirnames, filenames in os.walk(os.path.join(EXT, directory)):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for fn in sorted(filenames):
                if fn.endswith(suffix) and not fn.startswith("."):
                    files.append(os.path.relpath(os.path.join(dirpath, fn), EXT))
    for rel in files:
        path = os.path.join(EXT, rel)
        if os.path.islink(path) or os.path.commonpath([os.path.realpath(path), os.path.realpath(EXT)]) != os.path.realpath(EXT):
            raise ValueError("pack refuses linked or external runtime assets")
    return sorted(files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist")
    ap.add_argument("--commit", default="")
    ap.add_argument("--keep-key", action="store_true", help="keep the manifest key (unpacked loads only)")
    args = ap.parse_args()
    if args.commit and not re.fullmatch(r"[0-9a-fA-F]{7,40}", args.commit):
        print("pack: commit must be a hexadecimal source revision", file=sys.stderr)
        return 1

    for req in REQUIRED:
        if not os.path.isfile(os.path.join(EXT, req)):
            print(f"pack: missing {req}", file=sys.stderr)
            return 1
    with open(os.path.join(EXT, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    version = manifest.get("version", "")
    if not re.fullmatch(r"\d+(\.\d+){1,3}", version):
        print(f"pack: manifest version {version!r} is not a dotted number", file=sys.stderr)
        return 1
    short = args.commit[:7] if args.commit else "local"
    manifest["version_name"] = f"{version}+{short}"
    if not args.keep_key:
        manifest.pop("key", None)

    os.makedirs(args.out, exist_ok=True)
    zip_path = os.path.join(args.out, f"extension-{version}-{short}.zip")
    files = collect()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            # Stable archive order, timestamps and permissions permit rebuilding
            # identical extension bytes from the same source and Python/zlib toolchain.
            entry = zipfile.ZipInfo(rel.replace(os.sep, "/"), (1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            if rel == "manifest.json":
                zf.writestr(entry, json.dumps(manifest, indent=2) + "\n")
            else:
                with open(os.path.join(EXT, rel), "rb") as source:
                    zf.writestr(entry, source.read())
    with open(zip_path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    checksum_path = zip_path + ".sha256"
    with open(checksum_path, "w", encoding="utf-8") as checksum:
        checksum.write(f"{digest}  {os.path.basename(zip_path)}\n")
    print(json.dumps({
        "zip": zip_path,
        "version": version,
        "version_name": manifest["version_name"],
        "key_present": bool(manifest.get("key")),
        "files": len(files),
        "sha256": digest,
        "checksum": checksum_path,
        "source_revision": args.commit or "local",
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
