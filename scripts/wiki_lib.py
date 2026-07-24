"""Shared parsing and config helpers for the wiki verifier and generator.

wiki_check.py (verification) and wiki_home.py (index generation) both need to
answer the same questions about the tree - which specs exist, what state each
is in, what the control plane's current version is. Reading those facts
through one shared implementation, rather than two independent readings, is
the point: two parsers of the same fact are exactly the kind of drift this
system exists to catch, and building the generator without this split would
have reintroduced that risk inside the tool meant to prevent it.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "project_organization.json"

SEMVER_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
SPEC_ID_RE = re.compile(r"SPEC-(\d{3})")
STATUS_HEADER_RE = re.compile(r"^\*\*Status:\*\*\s*(.+)$", re.MULTILINE)


def load_config() -> dict:
    with CONFIG_FILE.open(encoding="utf-8") as handle:
        return json.load(handle)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return proc.stdout if proc.returncode == 0 else ""


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def matches_governed(path: str, globs: list[str]) -> bool:
    for glob in globs:
        # fnmatch treats ** as a single *, so translate directory globs by prefix.
        if glob.endswith("/**"):
            if path.startswith(glob[:-2]):
                return True
        elif fnmatch.fnmatch(path, glob):
            return True
    return False


def governed_markdown(globs: list[str]) -> list[Path]:
    out = []
    for path in sorted(ROOT.rglob("*.md")):
        if ".git" in path.parts:
            continue
        if matches_governed(rel(path), globs):
            out.append(path)
    return out


def header_status(text: str) -> tuple[str, int] | None:
    """First **Status:** above the first `---` rule, with its 1-indexed line.

    Scoping to the header block matters: SPEC-002_portable-ids.md carries
    further **Status:** lines deep in its body that describe phases of work,
    not the spec's lifecycle state.
    """
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == "---":
            break
        match = STATUS_HEADER_RE.match(line)
        if match:
            return match.group(1).strip(), idx + 1
    return None


def spec_actual_states(spec_glob: str) -> dict[str, set[str]]:
    """SPEC number -> set of lifecycle folders holding a file with that number."""
    states: dict[str, set[str]] = {}
    for path in sorted(ROOT.glob(spec_glob)):
        match = SPEC_ID_RE.search(path.name)
        if match:
            states.setdefault(match.group(1), set()).add(path.parent.name)
    return states


def current_versions(config: dict) -> dict[str, tuple[str, str | None]]:
    """Label -> (path, version string or None) for each control-plane file."""
    plane = config["wiki"]["control_plane"]
    out: dict[str, tuple[str, str | None]] = {}

    versioning = ROOT / plane["versioning"]
    if versioning.exists():
        match = SEMVER_RE.search(read(versioning))
        out["versioning"] = (plane["versioning"], match.group(0) if match else None)

    tracker = ROOT / plane["pass_tracker"]
    if tracker.exists():
        # Anchor to the field. A whole-file first-semver scan returns the
        # baseline, which is listed above the current version.
        text = read(tracker)
        field = re.search(
            r"Wiki current version:\s*\n\s*\n?\s*-\s*`?(v\d+\.\d+\.\d+)`?", text
        )
        out["pass_tracker"] = (plane["pass_tracker"], field.group(1) if field else None)

    changelog = ROOT / plane["changelog"]
    if changelog.exists():
        match = re.search(r"^##\s*\[(v\d+\.\d+\.\d+)\]", read(changelog), re.MULTILINE)
        out["changelog"] = (plane["changelog"], match.group(1) if match else None)

    return out
