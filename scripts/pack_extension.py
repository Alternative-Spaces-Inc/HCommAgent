#!/usr/bin/env python3
"""Build a target-specific extension archive without reading any private key.

Default / --target store: remove the public development key and use the
store OAuth client. --target unpacked (or legacy --keep-key): retain the
public key and use its matching development client. The source manifest
must always be a valid unpacked pairing, so copying extension/ remains safe.
Public client/extension mappings live in extension/oauth-clients.json and
are build inputs only, never runtime assets. Store archive names stay stable;
unpacked archives have an -unpacked suffix to avoid target collisions.
"""

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(ROOT, "extension")
REQUIRED = ["manifest.json", "background.js", "popup.html", "popup.js", "decision.html", "options.html", "options.js"]


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


def target_manifest(manifest, target):
    """Validate both public mappings and the raw folder before making a copy."""
    with open(os.path.join(EXT, "oauth-clients.json"), encoding="utf-8") as source:
        clients = json.load(source)
    if not isinstance(clients, dict) or set(clients) != {"unpacked", "store"}:
        raise ValueError("OAuth mappings must contain exactly unpacked and store")
    for entry in clients.values():
        if not isinstance(entry, dict) or set(entry) != {"extension_id", "client_id"}:
            raise ValueError("OAuth mapping needs extension_id and client_id")
        if not isinstance(entry["extension_id"], str) or not re.fullmatch(r"[a-p]{32}", entry["extension_id"]):
            raise ValueError("OAuth mapping has an invalid extension ID")
        if not isinstance(entry["client_id"], str) or not re.fullmatch(
            r"[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com", entry["client_id"]
        ):
            raise ValueError("OAuth mapping has an invalid client ID")
    if any(clients["unpacked"][field] == clients["store"][field] for field in ("extension_id", "client_id")):
        raise ValueError("Unpacked and store OAuth mappings must be distinct")
    key = manifest.get("key")
    if not isinstance(key, str) or not key:
        raise ValueError("Source manifest must retain its public unpacked key")
    try:
        public_key = base64.b64decode(key, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Source manifest key is not valid base64") from exc
    key_hash = hashlib.sha256(public_key).hexdigest()[:32]
    extension_id = "".join(chr(ord("a") + int(nibble, 16)) for nibble in key_hash)
    if extension_id != clients["unpacked"]["extension_id"]:
        raise ValueError("Source manifest key does not match unpacked extension ID")
    oauth = manifest.get("oauth2")
    if not isinstance(oauth, dict) or oauth.get("client_id") != clients["unpacked"]["client_id"]:
        raise ValueError("Source manifest OAuth client does not match unpacked mapping")
    result = dict(manifest)
    result["oauth2"] = dict(oauth, client_id=clients[target]["client_id"])
    if target == "store":
        result.pop("key", None)
    return result, clients[target]["extension_id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist")
    ap.add_argument("--commit", default="")
    ap.add_argument("--target", choices=("store", "unpacked"), help="archive target (default: store)")
    ap.add_argument("--keep-key", action="store_true", help="legacy alias for --target unpacked")
    args = ap.parse_args()
    if args.keep_key and args.target == "store":
        ap.error("--keep-key conflicts with --target store")
    target = args.target or ("unpacked" if args.keep_key else "store")
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
    try:
        manifest, extension_id = target_manifest(manifest, target)
    except (OSError, ValueError) as exc:
        print(f"pack: {exc}", file=sys.stderr)
        return 1
    short = args.commit[:7] if args.commit else "local"
    manifest["version_name"] = f"{version}+{short}"

    os.makedirs(args.out, exist_ok=True)
    suffix = "-unpacked" if target == "unpacked" else ""
    zip_path = os.path.join(args.out, f"extension-{version}-{short}{suffix}.zip")
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
        "target": target,
        "extension_id": extension_id,
        "oauth_client_id": manifest["oauth2"]["client_id"],
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
