#!/usr/bin/env python3
"""Install the verified scanner release into an explicit local tool path."""
import argparse
import hashlib
import io
from pathlib import Path
import platform
import tarfile
from urllib.request import urlopen

VERSION = "8.30.1"
ARCHIVES = {
    ("Linux", "x86_64"): ("linux_x64", "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"),
    ("Darwin", "arm64"): ("darwin_arm64", "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    name, expected = ARCHIVES[(platform.system(), platform.machine())]
    url = f"https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/gitleaks_{VERSION}_{name}.tar.gz"
    with urlopen(url, timeout=60) as response:
        archive = response.read(64 * 1024 * 1024 + 1)
    if hashlib.sha256(archive).hexdigest() != expected:
        raise SystemExit("Scanner release checksum does not match the reviewed pin")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        member = tar.getmember("gitleaks")
        if not member.isfile() or member.size > 64 * 1024 * 1024:
            raise SystemExit("Scanner archive contains an invalid executable")
        binary = tar.extractfile(member).read()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(binary)
    args.output.chmod(0o700)
    print(f"Installed checksum-verified Gitleaks {VERSION}")


if __name__ == "__main__":
    main()
