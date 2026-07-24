#!/usr/bin/env python3
"""Generator for the wiki home's mechanical index.

    python3 scripts/wiki_home.py

Rebuilds the AUTOGEN blocks in WIKI_HOME.md from the current tree - which
specs exist, what state each is in, what format specs and audits are on
file, and which authored breakdowns exist under Looney-WIKI/sections/. It
never touches anything outside those sentinel-delimited blocks: the title,
purpose paragraph, and reading-path suggestions are hand-authored and are
not regenerated.

Authored prose cannot regenerate itself - no script writes explanations of
what a subsystem is. What this generator can honestly keep current is the
mechanical fact of what exists and what state it's in. wiki_check.py's
check 8 diffs this generator's own output against what's committed, so a
stale index is a reported finding, not a silent gap.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from wiki_lib import ROOT, header_status, load_config, read, rel

SENTINEL_RE_TEMPLATE = (
    r"<!-- AUTOGEN:{name} START -->\n(.*?)<!-- AUTOGEN:{name} END -->"
)

BLOCK_NAMES = ["STATUS", "SPEC_INDEX", "FORMAT_SPEC_INDEX", "AUDIT_INDEX",
               "BREAKDOWN_INDEX"]


def _relative_link(config: dict, target: Path) -> str:
    home_dir = (ROOT / config["wiki"]["control_plane"]["home"]).parent
    try:
        return target.resolve().relative_to(home_dir.resolve()).as_posix()
    except ValueError:
        return os.path.relpath(target.resolve(), home_dir.resolve())


def _first_heading(text: str) -> str | None:
    match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _frontmatter_field(text: str, field: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    frontmatter = text[3:end]
    match = re.search(rf"^{field}:\s*(.+)$", frontmatter, re.MULTILINE)
    return match.group(1).strip() if match else None


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").strip()


def _table(headers: list[str], rows: list[list[str]], empty_note: str) -> str:
    if not rows:
        return f"_{empty_note}_\n"
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(_escape_cell(c) for c in row) + " |")
    return "\n".join(lines) + "\n"


def build_blocks(config: dict) -> dict[str, str]:
    wiki = config["wiki"]
    states = wiki["lifecycle_states"]

    spec_rows: list[list[str]] = []
    status_counts: dict[str, int] = {s: 0 for s in states}
    for state in states:
        folder = ROOT / "01_Specs" / state
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("SPEC-*.md")):
            status_counts[state] += 1
            title = _first_heading(read(path)) or path.stem
            title = re.sub(r"^SPEC-\d{3}:\s*", "", title)
            number = re.search(r"SPEC-(\d{3})", path.name)
            number = number.group(1) if number else "???"
            link = _relative_link(config, path)
            spec_rows.append([f"SPEC-{number}", title, state, f"[{path.name}]({link})"])
    spec_rows.sort(key=lambda r: r[0])

    format_rows: list[list[str]] = []
    format_dir = ROOT / "03_Format_Spec"
    for path in sorted(format_dir.glob("*.md")):
        parsed = header_status(read(path))
        status_text = parsed[0] if parsed else "(no status line)"
        version = re.search(r"v(\d+\.\d+)", path.stem)
        version = f"v{version.group(1)}" if version else path.stem
        link = _relative_link(config, path)
        format_rows.append([version, status_text, f"[{path.name}]({link})"])

    audit_rows: list[list[str]] = []
    audit_dir = ROOT / "04_Audits"
    for path in sorted(audit_dir.iterdir()):
        if path.is_dir() or path.name.startswith("."):
            continue
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
        date = date_match.group(1) if date_match else "?"
        if path.suffix == ".md":
            description = _first_heading(read(path)) or "(no heading)"
        else:
            description = f"({path.suffix.lstrip('.').upper()} data file)"
        link = _relative_link(config, path)
        audit_rows.append([path.name, date, description, f"[{path.name}]({link})"])
    audit_rows.sort(key=lambda r: r[1])

    breakdown_rows: list[list[str]] = []
    sections_dir = (ROOT / config["wiki"]["control_plane"]["home"]).parent / "sections"
    if sections_dir.is_dir():
        for path in sorted(sections_dir.glob("*.md")):
            text = read(path)
            title = _first_heading(text) or path.stem
            description = _frontmatter_field(text, "description") or ""
            link = _relative_link(config, path)
            breakdown_rows.append([title, description, f"[{path.name}]({link})"])

    version = None
    versioning_path = ROOT / wiki["control_plane"]["versioning"]
    if versioning_path.exists():
        m = re.search(r"v(\d+)\.(\d+)\.(\d+)", read(versioning_path))
        version = m.group(0) if m else None

    status_lines = [f"**Current version:** `{version or '(unknown)'}`", ""]
    status_lines.append(
        "| Specs (implemented) | Specs (accepted) | Specs (active) | "
        "Specs (rejected) | Format specs | Audits | Breakdowns |"
    )
    status_lines.append("|---|---|---|---|---|---|---|")
    status_lines.append(
        f"| {status_counts.get('implemented', 0)} "
        f"| {status_counts.get('accepted', 0)} "
        f"| {status_counts.get('active', 0)} "
        f"| {status_counts.get('rejected', 0)} "
        f"| {len(format_rows)} | {len(audit_rows)} | {len(breakdown_rows)} |"
    )

    return {
        "STATUS": "\n".join(status_lines) + "\n",
        "SPEC_INDEX": _table(
            ["Spec", "Title", "Status", "File"], spec_rows, "no specs found"
        ),
        "FORMAT_SPEC_INDEX": _table(
            ["Version", "Status", "File"], format_rows, "no format specs found"
        ),
        "AUDIT_INDEX": _table(
            ["File", "Date", "Description", "Link"], audit_rows, "no audits found"
        ),
        "BREAKDOWN_INDEX": _table(
            ["Title", "Description", "File"], breakdown_rows,
            "no authored breakdowns yet",
        ),
    }


def extract_block(text: str, name: str) -> str | None:
    pattern = re.compile(
        SENTINEL_RE_TEMPLATE.format(name=re.escape(name)), re.DOTALL
    )
    match = pattern.search(text)
    return match.group(1) if match else None


def _write_blocks(text: str, blocks: dict[str, str]) -> tuple[str, list[str]]:
    missing = []
    for name, content in blocks.items():
        pattern = re.compile(
            SENTINEL_RE_TEMPLATE.format(name=re.escape(name)), re.DOTALL
        )
        if not pattern.search(text):
            missing.append(name)
            continue
        replacement = f"<!-- AUTOGEN:{name} START -->\n{content}<!-- AUTOGEN:{name} END -->"
        text = pattern.sub(lambda m, r=replacement: r, text, count=1)
    return text, missing


def main() -> int:
    config = load_config()
    home = ROOT / config["wiki"]["control_plane"]["home"]
    if not home.exists():
        print(f"wiki_home: {rel(home)} does not exist - author the static shell first",
              file=sys.stderr)
        return 2

    blocks = build_blocks(config)
    text = read(home)
    new_text, missing = _write_blocks(text, blocks)
    if missing:
        print(f"wiki_home: {rel(home)} is missing sentinels for: {', '.join(missing)}",
              file=sys.stderr)
        return 2
    home.write_text(new_text, encoding="utf-8")
    print(f"wiki_home: regenerated {rel(home)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
