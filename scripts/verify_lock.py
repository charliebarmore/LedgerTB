#!/usr/bin/env python3
"""Verify that the current Apple Silicon build environment matches its lock."""

from __future__ import annotations

import platform
import re
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


LOCK_LINE = re.compile(r"^([A-Za-z0-9_.-]+)==([^#\s]+)$")


def main() -> int:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        print("macOS arm64 lock verification requires an Apple Silicon Mac.", file=sys.stderr)
        return 2
    if sys.version_info[:2] != (3, 12):
        print("macOS lock verification requires Python 3.12.", file=sys.stderr)
        return 2

    lock_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("requirements-macos-arm64.lock")
    failures: list[str] = []
    checked = 0
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = LOCK_LINE.fullmatch(line)
        if match is None:
            failures.append(f"invalid lock line: {line}")
            continue
        checked += 1
        package, expected = match.groups()
        try:
            actual = version(package)
        except PackageNotFoundError:
            failures.append(f"missing {package}=={expected}")
            continue
        if actual != expected:
            failures.append(f"{package}: expected {expected}, installed {actual}")

    if failures:
        print("MACOS LOCK FAIL:")
        print("\n".join(f"  {failure}" for failure in failures))
        return 1
    print(f"MACOS LOCK OK — verified {checked} packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
