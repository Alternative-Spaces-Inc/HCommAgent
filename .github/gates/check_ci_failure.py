#!/usr/bin/env python3
"""Demonstrate that a failing Swift command fails its actual CI pipeline."""
from pathlib import Path
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]


def main():
    workflow = yaml.safe_load((ROOT / ".github/workflows/gates.yml").read_text())
    step = next(step for step in workflow["jobs"]["swift-tests"]["steps"]
                if "swift test" in step.get("run", ""))
    if step.get("shell") != "bash":
        raise SystemExit("FAIL: Swift failure propagation requires the explicit bash shell")
    script = step["run"].replace("swift test", "false").replace("swift build -c release", "false")
    with tempfile.TemporaryDirectory(prefix="hca-ci-failure-") as directory:
        result = subprocess.run(["bash", "-e", "-c", script], cwd=directory, capture_output=True)
    if result.returncode == 0:
        raise SystemExit("FAIL: the CI output pipeline hid the simulated Swift failure")
    print("OK: a failing test command fails the actual Swift CI pipeline")


if __name__ == "__main__":
    main()
