#!/usr/bin/env python3
"""Drift verifier for the .lun Development spec corpus.

Reports disagreements between the repo's documents and the repo's tree. Run it
before closing a wiki pass:

    python3 scripts/wiki_check.py

Configuration lives in project_organization.json under the "wiki" key. Exit code
honors that file's "policy" field ("warning-only" -> 0, "strict" -> 1 on any
gating finding); --strict overrides it.

Deliberately absent: date validation. Content dates in this tree run ahead of
the system clock, so any today-vs-file-date check would fire spuriously.
"""

from __future__ import annotations

import argparse
import re
import sys

from wiki_home import build_blocks, extract_block
from wiki_lib import (
    ROOT,
    SPEC_ID_RE,
    current_versions,
    governed_markdown,
    header_status,
    matches_governed,
    read,
    rel,
    spec_actual_states,
    git,
    load_config,
)

MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\n]+)\)")
FENCE_RE = re.compile(r"^\s*```")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
LINK_TARGET_RE = re.compile(r"\]\([^)\n]*\)")

EXTERNAL = "external-reference (unresolved)"


class Finding:
    """One reported disagreement. `gating` False means informational only."""

    def __init__(self, check: str, path: str, line: int | None, message: str,
                 gating: bool = True) -> None:
        self.check = check
        self.path = path
        self.line = line
        self.message = message
        self.gating = gating

    def location(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


def strip_for_claims(text: str) -> str:
    """Blank fenced code, drop inline code and link targets; keep line numbers."""
    lines = text.splitlines()
    in_fence = False
    kept = []
    for line in lines:
        if FENCE_RE.match(line):
            in_fence = not in_fence
            kept.append("")
            continue
        kept.append("" if in_fence else line)
    joined = "\n".join(kept)
    joined = INLINE_CODE_RE.sub("", joined)
    joined = LINK_TARGET_RE.sub("]", joined)
    return joined


# ---------------------------------------------------------------- checks


def check_1_spec_status_vs_folder(config: dict) -> list[Finding]:
    """Header status word must equal the lifecycle folder holding the file."""
    wiki = config["wiki"]
    states = set(wiki["lifecycle_states"])
    findings = []
    for path in sorted(ROOT.glob(wiki["spec_lifecycle_glob"])):
        folder = path.parent.name
        if folder not in states:
            continue
        parsed = header_status(read(path))
        if parsed is None:
            findings.append(Finding(
                "1 spec status vs folder", rel(path), None,
                "no **Status:** line in the header block",
            ))
            continue
        raw, line = parsed
        word = raw.split()[0].strip("*`").lower() if raw.split() else ""
        if word != folder:
            findings.append(Finding(
                "1 spec status vs folder", rel(path), line,
                f"header says {word!r} but the file sits in {folder}/",
            ))
    return findings


def check_2_readme_claims(config: dict) -> list[Finding]:
    """Lifecycle claims about a SPEC in the top-level README must match the tree.

    Matching is bold-insensitive on purpose. The README states claims three
    ways: `SPEC-010 (**accepted** ...)`, `**SPEC-010 accepted**` as one bold
    span, and plain unbolded prose. A bold-adjacency rule sees only the first.
    """
    wiki = config["wiki"]
    hub = ROOT / wiki["control_plane"]["readme"]
    states = wiki["lifecycle_states"]
    state_re = re.compile(r"\b(" + "|".join(states) + r")\b", re.IGNORECASE)
    actual = spec_actual_states(wiki["spec_lifecycle_glob"])

    text = strip_for_claims(read(hub))
    findings = []
    for match in SPEC_ID_RE.finditer(text):
        number = match.group(1)
        line = text.count("\n", 0, match.start()) + 1

        # Clause = up to the first sentence/clause terminator or blank line.
        # It may cross a newline; the README hard-wraps claims away from IDs.
        tail = text[match.end():]
        stop = len(tail)
        for terminator in (";", ".", "\n\n"):
            found = tail.find(terminator)
            if found != -1:
                stop = min(stop, found)
        clause = tail[:stop]

        claimed = state_re.search(clause)
        if not claimed:
            continue
        claimed_state = claimed.group(1).lower()

        folders = actual.get(number)
        if not folders:
            findings.append(Finding(
                "2 README claims vs tree", rel(hub), line,
                f"claims SPEC-{number} is {claimed_state!r} but no such spec exists",
            ))
        elif len(folders) > 1:
            findings.append(Finding(
                "2 README claims vs tree", rel(hub), line,
                f"SPEC-{number} files disagree across folders: {sorted(folders)}",
            ))
        elif claimed_state != next(iter(folders)):
            findings.append(Finding(
                "2 README claims vs tree", rel(hub), line,
                f"claims SPEC-{number} is {claimed_state!r} "
                f"but it sits in {next(iter(folders))}/",
            ))
    return findings


def check_3_link_integrity(config: dict) -> list[Finding]:
    """Relative links inside the repo must resolve. Links that escape are noted."""
    findings = []
    for path in governed_markdown(config["wiki"]["governed"]):
        text = read(path)
        for match in MD_LINK_RE.finditer(text):
            target = match.group(1).split("#")[0].strip()
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if target.startswith("/"):
                continue
            line = text.count("\n", 0, match.start()) + 1
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                # Escapes the repo. Do not stat it: links pointing into a
                # sibling engine tree are absent from any clone, so resolving
                # them would make this check machine-dependent.
                findings.append(Finding(
                    EXTERNAL, rel(path), line,
                    f"target escapes the repo root: {target}", gating=False,
                ))
                continue
            if not resolved.exists():
                findings.append(Finding(
                    "3 link integrity", rel(path), line,
                    f"broken relative link: {target}",
                ))
    return findings


def check_4_version_agreement(config: dict) -> list[Finding]:
    """All control-plane files must name the same current version."""
    versions = current_versions(config)
    findings = []
    for label, (path, value) in versions.items():
        if value is None:
            findings.append(Finding(
                "4 version agreement", path, None,
                f"could not parse a current version from the {label} file",
            ))
    parsed = {label: value for label, (_, value) in versions.items() if value}
    if len(set(parsed.values())) > 1:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        for label, (path, _) in versions.items():
            findings.append(Finding(
                "4 version agreement", path, None,
                f"control-plane versions disagree: {detail}",
            ))
    return findings


def check_5_changelog_completeness(config: dict) -> list[Finding]:
    """The current version's entry needs changes and a stated rationale."""
    plane = config["wiki"]["control_plane"]
    changelog = ROOT / plane["changelog"]
    if not changelog.exists():
        return [Finding("5 changelog completeness", plane["changelog"], None,
                        "changelog file is missing")]

    text = read(changelog)
    heading = re.search(r"^##\s*\[(v\d+\.\d+\.\d+)\]", text, re.MULTILINE)
    if not heading:
        return [Finding("5 changelog completeness", plane["changelog"], None,
                        "no `## [vX.Y.Z]` entry found")]

    start = heading.start()
    nxt = re.search(r"^##\s*\[", text[heading.end():], re.MULTILINE)
    entry = text[start:heading.end() + nxt.start()] if nxt else text[start:]
    line = text.count("\n", 0, start) + 1

    findings = []
    changes = re.search(r"^Changes:\s*$", entry, re.MULTILINE)
    if not changes or not re.search(r"^-\s+\S", entry[changes.end():], re.MULTILINE):
        findings.append(Finding(
            "5 changelog completeness", plane["changelog"], line,
            f"{heading.group(1)} entry has no non-empty `Changes:` list",
        ))
    # Match the label loosely: the Engine writes "Rationale for MINOR bump:".
    if not re.search(r"^Rationale\b.*:", entry, re.MULTILINE):
        findings.append(Finding(
            "5 changelog completeness", plane["changelog"], line,
            f"{heading.group(1)} entry states no rationale for the bump",
        ))
    return findings


def check_6_pass_table(config: dict) -> list[Finding]:
    """Rows under `## Pass Status` must be complete; done rows need a gate."""
    plane = config["wiki"]["control_plane"]
    tracker = ROOT / plane["pass_tracker"]
    if not tracker.exists():
        return [Finding("6 pass-table integrity", plane["pass_tracker"], None,
                        "pass tracker file is missing")]

    lines = read(tracker).splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().startswith("## Pass Status"))
    except StopIteration:
        return [Finding("6 pass-table integrity", plane["pass_tracker"], None,
                        "no `## Pass Status` section")]

    findings = []
    for offset, line in enumerate(lines[start + 1:], start=start + 2):
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(set(c) <= {"-", ":"} for c in cells if c):
            continue
        if cells and cells[0].lower() in {"pass", "pass id"}:
            continue
        if len(cells) < 6:
            findings.append(Finding(
                "6 pass-table integrity", plane["pass_tracker"], offset,
                f"row has {len(cells)} columns, expected 6",
            ))
            continue
        status, gate = cells[3].lower(), cells[4]
        if status == "done" and not gate:
            findings.append(Finding(
                "6 pass-table integrity", plane["pass_tracker"], offset,
                f"pass {cells[0]} is done but its gate is empty",
            ))
    return findings


def check_7_unbumped(config: dict) -> list[Finding]:
    """Governed docs changed since the last bump, including the working tree."""
    plane = config["wiki"]["control_plane"]
    globs = config["wiki"]["governed"]

    # Anchor to the whole control plane, not just the policy file. Every bump
    # necessarily touches at least one of the three, so this avoids forcing a
    # ceremonial edit to WIKI_VERSIONING.md on passes that do not change policy.
    anchors = [plane["versioning"], plane["changelog"], plane["pass_tracker"]]
    baseline = git("log", "-1", "--format=%H", "--", *anchors).strip()
    if not baseline:
        # Never pass an empty baseline to git diff: "..HEAD" silently becomes
        # HEAD..HEAD and exits 0 with no output, reading as a pass.
        print("check 7: no bump baseline yet "
              "(no control-plane file committed) - skipped\n")
        return []

    changed = set()
    for line in git("diff", "--name-only", f"{baseline}..HEAD").splitlines():
        if line.strip():
            changed.add(line.strip())
    # Committed history alone cannot see the state a pass is actually closed in.
    # Note: git collapses a wholly-untracked directory to one line ending in
    # "/" rather than listing its files - matches_governed() still matches
    # that path against a "**" glob, so the finding fires at directory
    # granularity until the first `git add`. Harmless: it still flags that
    # something under there needs a bump, just not which file.
    for line in git("status", "--porcelain").splitlines():
        if len(line) > 3:
            changed.add(line[3:].strip().split(" -> ")[-1])

    findings = []
    for path in sorted(changed):
        if path in anchors:
            continue
        if matches_governed(path, globs):
            findings.append(Finding(
                "7 unbumped governed change", path, None,
                "governed doc changed since the last version bump",
            ))
    return findings


def check_8_wiki_home_fresh(config: dict) -> list[Finding]:
    """WIKI_HOME.md's generated blocks must match a fresh regeneration.

    Covers exactly the four indexed classes (specs, format specs, audits,
    breakdowns) - not the whole governed corpus. Everything else governed is
    caught by check 7 instead: two different guarantees, not one duplicated.
    """
    plane = config["wiki"]["control_plane"]
    home = ROOT / plane["home"]
    if not home.exists():
        return [Finding("8 wiki home freshness", plane["home"], None,
                        "wiki home file is missing")]

    committed = read(home)
    fresh_blocks = build_blocks(config)
    findings = []
    for name, fresh in fresh_blocks.items():
        current = extract_block(committed, name)
        if current is None:
            findings.append(Finding(
                "8 wiki home freshness", rel(home), None,
                f"missing AUTOGEN:{name} sentinels",
            ))
        elif current.strip() != fresh.strip():
            findings.append(Finding(
                "8 wiki home freshness", rel(home), None,
                f"{name} block is stale - run scripts/wiki_home.py",
            ))
    return findings


CHECKS = [
    check_1_spec_status_vs_folder,
    check_2_readme_claims,
    check_3_link_integrity,
    check_4_version_agreement,
    check_5_changelog_completeness,
    check_6_pass_table,
    check_7_unbumped,
    check_8_wiki_home_fresh,
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 on any gating finding, overriding config policy")
    args = parser.parse_args()

    config = load_config()
    if "wiki" not in config:
        print("project_organization.json has no 'wiki' block", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(config))

    grouped: dict[str, list[Finding]] = {}
    for finding in findings:
        grouped.setdefault(finding.check, []).append(finding)

    gating = [f for f in findings if f.gating]

    if not findings:
        print("wiki_check: clean - no drift found.")
        return 0

    for check in sorted(grouped):
        print(f"{check}  ({len(grouped[check])})")
        for finding in grouped[check]:
            print(f"  {finding.location()}: {finding.message}")
        print()

    informational = len(findings) - len(gating)
    summary = f"{len(gating)} gating finding(s)"
    if informational:
        summary += f", {informational} informational"
    print(summary)

    policy = config.get("policy", "warning-only")
    if gating and (args.strict or policy == "strict"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
