#!/usr/bin/env python3
"""Read-only cross-project sync watcher for Open-Looney and Luna Engine."""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "ProjectManager" / "cross_project_sync.json"
SEMVER_RE = re.compile(r"v\d+\.\d+(?:\.\d+)?")
SPEC_RE = re.compile(r"\bSPEC-(\d{3})\b")
COMMIT_RE = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{7,40})(?![0-9A-Fa-f])")
MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\n#]+)(?:#[^)\n]*)?\)")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
PATHISH_RE = re.compile(r"^[A-Za-z0-9_.@() /+-]+/[A-Za-z0-9_.@() /+-]+$")
FORBIDDEN_GIT_SUBCOMMANDS = {"add", "commit", "push", "reset", "checkout", "switch"}
ALLOWED_GIT_SUBCOMMANDS = {
    "branch",
    "cat-file",
    "ls-files",
    "log",
    "rev-parse",
    "status",
}
SEVERITY_ORDER = {"info": 0, "warn": 1, "fail": 2}
KNOWN_RELATIVE_PREFIXES = (
    "00_README/",
    "01_Specs/",
    "02_Handoffs/",
    "03_Format_Spec/",
    "04_Audits/",
    "05_Reference/",
    "06_Prototypes/",
    "07_Sample_Cartridges/",
    "08_Journal/",
    "09_Sample_Sources/",
    "10_Builder/",
    "Builds/",
    "Docs/",
    "ProjectManager/",
    "config/",
    "frontend/",
    "intergalactic_hub/",
    "scripts/",
    "src/",
    "tests/",
)


class SyncError(RuntimeError):
    """Tool/config error."""


class CommandPolicyError(SyncError):
    """A command was blocked by the read-only command policy."""


@dataclass(frozen=True)
class ProjectConfig:
    key: str
    display_name: str
    path: Path
    canonical_ledger: str
    spec_glob: str
    reference_sources: tuple[str, ...]
    version_surfaces: dict[str, dict[str, str]]
    watched_terms: tuple[str, ...]


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    if not args:
        raise CommandPolicyError("git command missing subcommand")
    if args[0] in FORBIDDEN_GIT_SUBCOMMANDS or args[0] not in ALLOWED_GIT_SUBCOMMANDS:
        raise CommandPolicyError(f"git {args[0]} is not allowed by SPEC-016")
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SyncError(f"cannot read manifest {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SyncError(f"invalid manifest JSON {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        raise SyncError("manifest must contain a projects object")
    if set(data["projects"]) != {"open_looney", "luna_engine"}:
        raise SyncError("manifest v1 must define exactly open_looney and luna_engine")
    return data


def project_configs(manifest: dict[str, Any]) -> dict[str, ProjectConfig]:
    projects: dict[str, ProjectConfig] = {}
    for key, raw in manifest["projects"].items():
        projects[key] = ProjectConfig(
            key=key,
            display_name=str(raw.get("display_name") or key),
            path=Path(str(raw["path"])).expanduser().resolve(),
            canonical_ledger=str(raw["canonical_ledger"]),
            spec_glob=str(raw["spec_glob"]),
            reference_sources=tuple(str(item) for item in raw.get("reference_sources", [])),
            version_surfaces=dict(raw.get("version_surfaces", {})),
            watched_terms=tuple(str(item) for item in raw.get("watched_terms", [])),
        )
    return projects


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_updated_frontmatter(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        match = re.match(r"updated:\s*(.+)\s*$", line)
        if match:
            return match.group(1).strip().strip("'\"")
    return None


def parse_version(text: str, parser: str) -> str | None:
    if parser == "wiki_versioning":
        match = re.search(r"Current wiki version:\s*\n\s*\n?\s*-\s*`?(" + SEMVER_RE.pattern + r")`?", text, re.IGNORECASE)
        return match.group(1) if match else None
    if parser == "wiki_home":
        for line in text.splitlines():
            if re.search(r"Current (?:wiki )?version:", line, re.IGNORECASE):
                match = SEMVER_RE.search(line)
                if match:
                    return match.group(0)
        return None
    if parser == "pass_tracker":
        match = re.search(r"Wiki current version:\s*\n\s*\n?\s*-\s*`?(" + SEMVER_RE.pattern + r")`?", text, re.IGNORECASE)
        return match.group(1) if match else None
    if parser == "changelog":
        match = re.search(r"^##\s*\[(" + SEMVER_RE.pattern + r")\]", text, re.MULTILINE)
        return match.group(1) if match else None
    if parser == "format_readme":
        match = re.search(r"## Current format version.*?(" + SEMVER_RE.pattern + r")", text, re.IGNORECASE | re.DOTALL)
        return match.group(1) if match else None
    match = SEMVER_RE.search(text)
    return match.group(0) if match else None


def git_stdout(repo: Path, *args: str) -> str:
    proc = run_git(repo, *args)
    return proc.stdout.rstrip("\n") if proc.returncode == 0 else ""


def parse_status_lines(status: str) -> tuple[list[str], list[str]]:
    modified: list[str] = []
    untracked: list[str] = []
    for line in status.splitlines():
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if line.startswith("??"):
            untracked.append(path)
        else:
            modified.append(path)
    return sorted(modified), sorted(untracked)


def tracked_files(repo: Path) -> set[str]:
    output = git_stdout(repo, "ls-files")
    return {line for line in output.splitlines() if line}


def collect_snapshot(config: ProjectConfig) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "name": config.key,
        "display_name": config.display_name,
        "path": str(config.path),
        "exists": config.path.exists(),
        "branch": None,
        "head": None,
        "upstream": None,
        "dirty": None,
        "modified": [],
        "untracked": [],
        "canonical_ledger": {
            "path": config.canonical_ledger,
            "exists": False,
            "frontmatter_updated": None,
        },
        "version_surfaces": {},
    }
    if not config.path.exists():
        return snapshot

    snapshot["branch"] = git_stdout(config.path, "branch", "--show-current")
    snapshot["head"] = git_stdout(config.path, "rev-parse", "--verify", "HEAD")
    snapshot["upstream"] = git_stdout(config.path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or None
    status = git_stdout(config.path, "status", "--porcelain=v1")
    modified, untracked = parse_status_lines(status)
    snapshot["dirty"] = bool(status)
    snapshot["modified"] = modified
    snapshot["untracked"] = untracked

    ledger_path = config.path / config.canonical_ledger
    snapshot["canonical_ledger"]["exists"] = ledger_path.exists()
    if ledger_path.exists():
        snapshot["canonical_ledger"]["frontmatter_updated"] = parse_updated_frontmatter(read_text(ledger_path))

    for key, raw in config.version_surfaces.items():
        rel_path = str(raw["path"])
        parser = str(raw.get("parser") or key)
        group = str(raw.get("group") or "default")
        surface_path = config.path / rel_path
        value = parse_version(read_text(surface_path), parser) if surface_path.exists() else None
        snapshot["version_surfaces"][key] = {
            "path": rel_path,
            "parser": parser,
            "group": group,
            "exists": surface_path.exists(),
            "version": value,
        }
    return snapshot


def finding(
    project: str,
    kind: str,
    severity: str,
    message: str,
    evidence: list[Any],
    subtype: str | None = None,
    owner: str | None = None,
    suggested_action: str | None = None,
    suggested_bump: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": ".".join(part for part in [project, kind, subtype] if part),
        "project": project,
        "kind": kind,
        "subtype": subtype,
        "severity": severity,
        "owner": owner or project,
        "message": message,
        "evidence": evidence,
    }
    if suggested_action:
        item["suggested_action"] = suggested_action
    if suggested_bump:
        item["suggested_bump"] = suggested_bump
    return item


def detect_snapshot_findings(project: ProjectConfig, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not snapshot["exists"]:
        return [
            finding(
                project.key,
                "missing_project",
                "fail",
                "Configured project path does not exist.",
                [{"path": str(project.path)}],
            )
        ]
    if not snapshot["canonical_ledger"]["exists"]:
        findings.append(
            finding(
                project.key,
                "missing_ledger",
                "fail",
                "Canonical ledger is missing.",
                [{"path": project.canonical_ledger}],
            )
        )
    if snapshot["dirty"]:
        findings.append(
            finding(
                project.key,
                "dirty_worktree_context",
                "info",
                "Project worktree is dirty; findings may depend on local state.",
                [
                    {
                        "branch": snapshot["branch"],
                        "modified": snapshot["modified"],
                        "untracked": snapshot["untracked"],
                    }
                ],
            )
        )

    by_group: dict[str, dict[str, str]] = {}
    for key, surface in snapshot["version_surfaces"].items():
        if not surface["exists"] or not surface["version"]:
            findings.append(
                finding(
                    project.key,
                    "missing_version_surface",
                    "fail",
                    "Configured version surface is absent or unparsable.",
                    [{"surface": key, "path": surface["path"], "exists": surface["exists"]}],
                )
            )
            continue
        by_group.setdefault(surface["group"], {})[key] = surface["version"]

    for group, values in sorted(by_group.items()):
        if len(values) > 1 and len(set(values.values())) > 1:
            findings.append(
                finding(
                    project.key,
                    "native_version_disagreement",
                    "warn",
                    f"Version surfaces disagree within {group}.",
                    [{"surface": key, "version": value} for key, value in sorted(values.items())],
                    subtype=group,
                    suggested_action=f"Run a native {project.display_name} version resync pass.",
                    suggested_bump="PATCH if only duplicated version text changes.",
                )
            )
    return findings


def source_files(project: ProjectConfig) -> list[Path]:
    paths: set[Path] = set()
    for pattern in project.reference_sources:
        for match in glob.glob(str(project.path / pattern), recursive=True):
            path = Path(match)
            if path.is_file():
                paths.add(path)
    return sorted(paths)


def clean_ref(raw: str) -> str:
    value = raw.strip().strip(".,;:()[]{}<>\"'")
    if ":" in value:
        before, after = value.rsplit(":", 1)
        if after.isdigit() and "/" in before:
            value = before
    return value


def looks_like_path(value: str) -> bool:
    if "://" in value or value.startswith("#") or value.startswith("mailto:"):
        return False
    if "*" in value or value.startswith("...") or value.startswith("…"):
        return False
    if not PATHISH_RE.match(value) or value.endswith("/"):
        return False
    if value.startswith("/"):
        return value.startswith(("/Users/", "/private/", "/var/"))
    return value.startswith(KNOWN_RELATIVE_PREFIXES)


def extract_references(path: Path, text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for regex, kind in ((MD_LINK_RE, "path"), (BACKTICK_RE, "path")):
        for match in regex.finditer(text):
            target = clean_ref(match.group(1))
            if not looks_like_path(target):
                continue
            key = (kind, target)
            if key in seen:
                continue
            seen.add(key)
            refs.append({"kind": kind, "target": target, "source": str(path), "line": text.count("\n", 0, match.start()) + 1})

    for match in SPEC_RE.finditer(text):
        target = f"SPEC-{match.group(1)}"
        key = ("spec", target)
        if key not in seen:
            seen.add(key)
            refs.append({"kind": "spec", "target": target, "source": str(path), "line": text.count("\n", 0, match.start()) + 1})

    for match in COMMIT_RE.finditer(text):
        target = match.group(1)
        context = text[max(0, match.start() - 40): match.end() + 20].lower()
        if target.isdigit() or not re.search(r"[a-fA-F]", target):
            continue
        if not any(marker in context for marker in ("commit", "sha", "head", "merge", "rev-parse", "git")):
            continue
        if any(ref["kind"] == "spec" and ref["target"].endswith(target[:3]) for ref in refs):
            continue
        key = ("commit", target)
        if key not in seen:
            seen.add(key)
            refs.append({"kind": "commit", "target": target, "source": str(path), "line": text.count("\n", 0, match.start()) + 1})
    return refs


def rel_to_project(path: Path, project: ProjectConfig) -> str:
    try:
        return path.relative_to(project.path).as_posix()
    except ValueError:
        return str(path)


def resolve_path_ref(ref: dict[str, Any], source_project: ProjectConfig, projects: dict[str, ProjectConfig], tracked: dict[str, set[str]]) -> dict[str, Any]:
    target = ref["target"]
    candidates: list[tuple[ProjectConfig, Path]] = []
    path = Path(target)
    if path.is_absolute():
        for project in projects.values():
            try:
                path.relative_to(project.path)
            except ValueError:
                continue
            candidates.append((project, path))
        if not candidates:
            return {
                **ref,
                "target_project": None,
                "resolved_path": target,
                "status": "external_path",
            }
    else:
        source_path = Path(ref["source"])
        candidates.append((source_project, (source_path.parent / path).resolve()))
        candidates.append((source_project, (source_project.path / path).resolve()))
        for project in projects.values():
            if project.key != source_project.key:
                candidates.append((project, (project.path / path).resolve()))

    checked: set[tuple[str, str]] = set()
    for project, candidate in candidates:
        key = (project.key, str(candidate))
        if key in checked:
            continue
        checked.add(key)
        try:
            rel_path = candidate.relative_to(project.path).as_posix()
        except ValueError:
            continue
        if candidate.exists():
            project_tracked = tracked.get(project.key, set())
            if candidate.is_dir():
                prefix = "" if rel_path == "." else rel_path.rstrip("/") + "/"
                is_tracked = rel_path == "." or any(item.startswith(prefix) for item in project_tracked)
            else:
                is_tracked = rel_path in project_tracked
            status = "ok" if is_tracked else "untracked_file"
            return {
                **ref,
                "target_project": project.key,
                "resolved_path": rel_path,
                "status": status,
            }
    return {**ref, "target_project": None, "resolved_path": target, "status": "missing_file"}


def resolve_spec_ref(ref: dict[str, Any], projects: dict[str, ProjectConfig]) -> dict[str, Any]:
    number = ref["target"].split("-", 1)[1]
    for project in projects.values():
        for path in project.path.glob(project.spec_glob):
            if f"SPEC-{number}" in path.name:
                return {
                    **ref,
                    "target_project": project.key,
                    "resolved_path": rel_to_project(path, project),
                    "status": "ok",
                }
    return {**ref, "target_project": None, "resolved_path": ref["target"], "status": "missing_spec"}


def commit_exists(project: ProjectConfig, sha: str) -> bool:
    proc = run_git(project.path, "cat-file", "-e", f"{sha}^{{commit}}")
    return proc.returncode == 0


def resolve_commit_ref(ref: dict[str, Any], projects: dict[str, ProjectConfig]) -> dict[str, Any]:
    for project in projects.values():
        if project.path.exists() and commit_exists(project, ref["target"]):
            return {
                **ref,
                "target_project": project.key,
                "resolved_path": ref["target"],
                "status": "ok",
            }
    return {**ref, "target_project": None, "resolved_path": ref["target"], "status": "missing_commit"}


def detect_cross_references(projects: dict[str, ProjectConfig]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    refs: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    tracked = {key: tracked_files(project.path) if project.path.exists() else set() for key, project in projects.items()}
    for project in projects.values():
        for source in source_files(project):
            text = read_text(source)
            for ref in extract_references(source, text):
                ref["source_project"] = project.key
                ref["source"] = rel_to_project(Path(ref["source"]), project)
                if ref["kind"] == "path":
                    resolved = resolve_path_ref(ref, project, projects, tracked)
                elif ref["kind"] == "spec":
                    resolved = resolve_spec_ref(ref, projects)
                else:
                    resolved = resolve_commit_ref(ref, projects)
                refs.append(resolved)
                if resolved["status"] in {"missing_file", "untracked_file", "missing_spec", "missing_commit"}:
                    severity = "warn"
                    findings.append(
                        finding(
                            project.key,
                            "cross_reference_target_missing",
                            severity,
                            f"Referenced {resolved['kind']} is {resolved['status']}.",
                            [resolved],
                            subtype=resolved["status"],
                            owner=project.key,
                        )
                    )
    unique_refs: list[dict[str, Any]] = []
    seen = set()
    for ref in refs:
        key = (ref["source_project"], ref["source"], ref["line"], ref["kind"], ref["target"], ref["status"])
        if key not in seen:
            seen.add(key)
            unique_refs.append(ref)
    return unique_refs, findings


def recent_changed_files(project: ProjectConfig, limit: int) -> set[str]:
    proc = run_git(project.path, "log", f"-n{limit}", "--name-only", "--pretty=format:")
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def combined_reference_text(project: ProjectConfig) -> str:
    chunks = []
    for path in source_files(project):
        chunks.append(read_text(path))
    return "\n".join(chunks)


def path_matches(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/") and path.startswith(pattern):
            return True
        if path == pattern or path.startswith(pattern):
            return True
    return False


def detect_cross_boundary_candidates(manifest: dict[str, Any], projects: dict[str, ProjectConfig]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    limit = int(manifest.get("recent_commit_limit") or 20)
    peer_text_cache = {key: combined_reference_text(project) for key, project in projects.items()}
    for rule in manifest.get("cross_boundary_rules", []):
        project_key = str(rule["project"])
        review_owner = str(rule["review_owner"])
        project = projects[project_key]
        peer_text = peer_text_cache.get(review_owner, "")
        for changed in sorted(recent_changed_files(project, limit)):
            if not path_matches(changed, [str(item) for item in rule.get("paths", [])]):
                continue
            basename = Path(changed).name
            if changed in peer_text or basename in peer_text:
                continue
            findings.append(
                finding(
                    project_key,
                    "cross_boundary_review_candidate",
                    "warn",
                    "Recent change touches a cross-boundary surface without a peer ledger/source mention.",
                    [{"changed_path": changed, "review_owner": review_owner}],
                    owner=review_owner,
                    suggested_action=f"Have {projects[review_owner].display_name} review whether a ledger/spec note is needed.",
                )
            )
    return findings


def build_report(manifest_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    projects = project_configs(manifest)
    snapshots = {key: collect_snapshot(project) for key, project in projects.items()}
    findings: list[dict[str, Any]] = []
    for key, project in projects.items():
        findings.extend(detect_snapshot_findings(project, snapshots[key]))
    cross_refs, cross_ref_findings = detect_cross_references(projects)
    findings.extend(cross_ref_findings)
    findings.extend(detect_cross_boundary_candidates(manifest, projects))
    findings.sort(key=lambda item: (-SEVERITY_ORDER[item["severity"]], item["id"], json.dumps(item["evidence"], sort_keys=True)))
    return {
        "schema_version": manifest.get("schema_version", "v0.1.0"),
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "manifest": str(manifest_path),
        "projects": snapshots,
        "findings": findings,
        "cross_references": cross_refs,
        "mutation_performed": False,
    }


def exit_code(report: dict[str, Any]) -> int:
    worst = max((SEVERITY_ORDER[item["severity"]] for item in report["findings"]), default=0)
    if worst >= SEVERITY_ORDER["fail"]:
        return 2
    if worst >= SEVERITY_ORDER["warn"]:
        return 1
    return 0


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-Project Sync Report",
        "",
        f"Generated: {report['generated_at']}",
        f"Watcher schema: {report['schema_version']}",
        "",
        "## Baselines",
        "",
        "| Project | Branch | HEAD | Dirty | Ledger | Ledger updated | Wiki/docs version |",
        "|---|---|---|---|---|---|---|",
    ]
    for snapshot in report["projects"].values():
        versions = [
            f"{key}={surface['version'] or 'missing'}"
            for key, surface in snapshot["version_surfaces"].items()
            if surface["group"] == "wiki"
        ]
        head = (snapshot["head"] or "")[:12]
        lines.append(
            "| {project} | {branch} | {head} | {dirty} | {ledger} | {updated} | {versions} |".format(
                project=snapshot["display_name"],
                branch=snapshot["branch"] or "unknown",
                head=head or "unknown",
                dirty=str(snapshot["dirty"]).lower(),
                ledger=snapshot["canonical_ledger"]["path"],
                updated=snapshot["canonical_ledger"]["frontmatter_updated"] or "missing",
                versions="<br>".join(versions) or "none",
            )
        )

    lines.extend(["", "## Findings", ""])
    if not report["findings"]:
        lines.append("No findings above the detector floor.")
    else:
        for severity in ("fail", "warn", "info"):
            grouped = [item for item in report["findings"] if item["severity"] == severity]
            if not grouped:
                continue
            lines.extend([f"### {severity.upper()}", ""])
            for item in grouped:
                lines.append(f"#### {item['id']}")
                lines.append("")
                lines.append(f"Owner: `{item['owner']}`")
                lines.append("")
                lines.append(item["message"])
                lines.append("")
                lines.append("Evidence:")
                for evidence in item["evidence"]:
                    lines.append(f"- `{json.dumps(evidence, sort_keys=True)}`")
                if item.get("suggested_action"):
                    lines.append("")
                    lines.append(f"Suggested action: {item['suggested_action']}")
                if item.get("suggested_bump"):
                    lines.append("")
                    lines.append(f"Suggested bump: {item['suggested_bump']}")
                lines.append("")

    lines.extend([
        "## Cross-References",
        "",
        "| Ref | Source | Target Project | Status |",
        "|---|---|---|---|",
    ])
    for ref in report["cross_references"][:200]:
        source = f"{ref['source_project']}:{ref['source']}:{ref['line']}"
        lines.append(
            f"| `{ref['target']}` | `{source}` | `{ref.get('target_project') or 'unknown'}` | `{ref['status']}` |"
        )
    if len(report["cross_references"]) > 200:
        lines.append(f"| ... | {len(report['cross_references']) - 200} additional refs omitted | ... | ... |")

    lines.extend(["", f"No mutation performed: `{str(report['mutation_performed']).lower()}`", ""])
    return "\n".join(lines)


def run_check(args: argparse.Namespace) -> int:
    report = build_report(Path(args.manifest))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return exit_code(report)


def run_report(args: argparse.Namespace) -> int:
    report = build_report(Path(args.manifest))
    out = Path(args.out)
    out.write_text(render_markdown(report), encoding="utf-8")
    print(f"Wrote {out}")
    return exit_code(report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to cross-project sync manifest JSON.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="Run detectors and print the report.")
    check.add_argument("--json", action="store_true", help="Print stable JSON instead of Markdown.")
    check.set_defaults(func=run_check)

    report = subparsers.add_parser("report", help="Write a Markdown report to an explicit path.")
    report.add_argument("--out", required=True, help="Markdown report output path.")
    report.set_defaults(func=run_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CommandPolicyError as exc:
        print(f"command policy error: {exc}", file=sys.stderr)
        return 3
    except SyncError as exc:
        print(f"sync error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
