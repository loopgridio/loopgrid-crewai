"""Minimal package and tag checks for the LoopGrid CrewAI release workflow."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"^v(\d+\.\d+\.\d+)$")
REQUIRED = (
    "README.md", "LICENSE", "NOTICE.md", "SECURITY.md", "CHANGELOG.md",
    "VALIDATION.md", "INTEGRATION-CONTRACT.md", "pyproject.toml",
    "src/loopgrid_crewai/__init__.py", "src/loopgrid_crewai/integration.py",
    "tests/test_contract.py", ".github/workflows/tests.yml", ".github/workflows/release.yml",
)


def release_check(tag: str | None = None) -> None:
    try:
        import tomllib
    except ModuleNotFoundError as exc:
        raise SystemExit("Use Python 3.11+ to run release_check.py") from exc

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    name, version = project["name"], project["version"]
    if name != "loopgrid-crewai":
        raise SystemExit(f"Unexpected project name: {name!r}")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"Expected stable release version, got: {version!r}")
    if tag is not None:
        match = VERSION_PATTERN.fullmatch(tag)
        if not match or match.group(1) != version:
            raise SystemExit(f"Release tag {tag!r} must be exactly v{version}")
    absent = [path for path in REQUIRED if not (ROOT / path).is_file()]
    if absent:
        raise SystemExit(f"Missing repository files: {', '.join(absent)}")
    print(f"PASS: {name} {version}; release tag and required source files verified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="GitHub release tag; must match pyproject version")
    args = parser.parse_args()
    release_check(args.tag)
