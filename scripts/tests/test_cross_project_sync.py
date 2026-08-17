from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import cross_project_sync as cps


def sh(repo: Path, *args: str) -> None:
    proc = subprocess.run(args, cwd=repo, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    sh(path, "git", "init")
    sh(path, "git", "config", "user.email", "fixture@example.test")
    sh(path, "git", "config", "user.name", "Fixture")


def commit_all(path: Path, message: str = "fixture") -> str:
    sh(path, "git", "add", ".")
    sh(path, "git", "commit", "-m", message)
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def version_files(repo: Path, version: str = "v1.2.3") -> None:
    write(repo / "WIKI_VERSIONING.md", f"# Wiki Versioning\n\nCurrent wiki version:\n\n- `{version}`\n")
    write(repo / "WIKI_HOME.md", f"# Home\n\n**Current version:** `{version}`\n")
    write(repo / "WIKI_PASS_TRACKER.md", f"# Tracker\n\nWiki current version:\n\n- `{version}`\n")
    write(repo / "WIKI_CHANGELOG.md", f"# Changelog\n\n## [{version}] - 2026-08-17\n")


def ledger(repo: Path, body: str = "") -> None:
    write(
        repo / "TODO.md",
        "---\ndoc_type: ledger\nstatus: active\ncreated: 2026-08-17\nupdated: 2026-08-17\n---\n\n"
        + body,
    )


def manifest(path: Path, open_repo: Path, engine_repo: Path) -> Path:
    data = {
        "schema_version": "v0.1.0",
        "recent_commit_limit": 5,
        "projects": {
            "open_looney": {
                "display_name": "Open-Looney",
                "path": str(open_repo),
                "canonical_ledger": "TODO.md",
                "spec_glob": "01_Specs/*/SPEC-*.md",
                "reference_sources": ["TODO.md"],
                "version_surfaces": {
                    "versioning": {"path": "WIKI_VERSIONING.md", "parser": "wiki_versioning", "group": "wiki"},
                    "home": {"path": "WIKI_HOME.md", "parser": "wiki_home", "group": "wiki"},
                    "tracker": {"path": "WIKI_PASS_TRACKER.md", "parser": "pass_tracker", "group": "wiki"},
                    "changelog": {"path": "WIKI_CHANGELOG.md", "parser": "changelog", "group": "wiki"},
                },
            },
            "luna_engine": {
                "display_name": "Luna Engine",
                "path": str(engine_repo),
                "canonical_ledger": "TODO.md",
                "spec_glob": "Docs/Design/Architecture/SPEC*.md",
                "reference_sources": ["TODO.md"],
                "version_surfaces": {
                    "versioning": {"path": "WIKI_VERSIONING.md", "parser": "wiki_versioning", "group": "wiki"},
                    "home": {"path": "WIKI_HOME.md", "parser": "wiki_home", "group": "wiki"},
                    "tracker": {"path": "WIKI_PASS_TRACKER.md", "parser": "pass_tracker", "group": "wiki"},
                    "changelog": {"path": "WIKI_CHANGELOG.md", "parser": "changelog", "group": "wiki"},
                },
            },
        },
        "cross_boundary_rules": [
            {
                "project": "luna_engine",
                "review_owner": "open_looney",
                "paths": ["src/luna_mcp/tools/aibrarian.py"],
            }
        ],
    }
    out = path / "manifest.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


def policy_manifest(path: Path, open_repo: Path, engine_repo: Path) -> Path:
    data = json.loads(manifest(path, open_repo, engine_repo).read_text(encoding="utf-8"))
    data["projects"]["open_looney"]["reference_sources"] = [
        {"glob": "TODO.md", "tier": "ledger", "finding_mode": "warn"},
        {"glob": "02_Handoffs/*.md", "tier": "handoff", "finding_mode": "summary"},
    ]
    data["projects"]["luna_engine"]["reference_sources"] = [
        {"glob": "TODO.md", "tier": "ledger", "finding_mode": "warn"},
        {"glob": "Docs/Handoffs/**/*.md", "tier": "handoff", "finding_mode": "summary"},
    ]
    out = path / "policy_manifest.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


class CrossProjectSyncTests(unittest.TestCase):
    def test_porcelain_modified_paths_preserve_first_character(self) -> None:
        modified, untracked = cps.parse_status_lines(" M 01_Specs/file.md\n M Docs/file.md\n?? scripts/\n")
        self.assertEqual(["01_Specs/file.md", "Docs/file.md"], modified)
        self.assertEqual(["scripts/"], untracked)

    def test_version_disagreement_flags_peer_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_repo = root / "open"
            engine_repo = root / "engine"
            init_repo(open_repo)
            init_repo(engine_repo)
            version_files(open_repo)
            ledger(open_repo)
            version_files(engine_repo, "v2.23.2")
            write(engine_repo / "WIKI_HOME.md", "# Home\n\n**Current version:** `v2.23.0`\n")
            ledger(engine_repo)
            commit_all(open_repo)
            commit_all(engine_repo)
            report = cps.build_report(manifest(root, open_repo, engine_repo))
            self.assertTrue(
                any(item["kind"] == "native_version_disagreement" and item["severity"] == "warn" for item in report["findings"])
            )

    def test_missing_version_surface_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_repo = root / "open"
            engine_repo = root / "engine"
            init_repo(open_repo)
            init_repo(engine_repo)
            version_files(open_repo)
            ledger(open_repo)
            version_files(engine_repo)
            (engine_repo / "WIKI_CHANGELOG.md").unlink()
            ledger(engine_repo)
            commit_all(open_repo)
            commit_all(engine_repo)
            report = cps.build_report(manifest(root, open_repo, engine_repo))
            self.assertEqual(2, cps.exit_code(report))
            self.assertTrue(any(item["kind"] == "missing_version_surface" for item in report["findings"]))

    def test_cross_reference_to_untracked_file_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_repo = root / "open"
            engine_repo = root / "engine"
            init_repo(open_repo)
            init_repo(engine_repo)
            version_files(open_repo)
            ledger(open_repo, "See `Docs/Reports/local.md`.\n")
            version_files(engine_repo)
            ledger(engine_repo)
            write(engine_repo / "Docs/Reports/local.md", "# local\n")
            commit_all(open_repo)
            commit_all(engine_repo)
            write(engine_repo / "Docs/Reports/local.md", "# local changed but untracked path remains tracked\n")
            sh(engine_repo, "git", "rm", "--cached", "Docs/Reports/local.md")
            report = cps.build_report(manifest(root, open_repo, engine_repo))
            self.assertTrue(any(item["subtype"] == "untracked_file" for item in report["findings"]))

    def test_canonical_ledger_reference_remains_warning_under_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_repo = root / "open"
            engine_repo = root / "engine"
            init_repo(open_repo)
            init_repo(engine_repo)
            version_files(open_repo)
            ledger(open_repo, "See `Docs/Reports/local.md`.\n")
            version_files(engine_repo)
            ledger(engine_repo)
            write(engine_repo / "Docs/Reports/local.md", "# local\n")
            commit_all(open_repo)
            commit_all(engine_repo)
            sh(engine_repo, "git", "rm", "--cached", "Docs/Reports/local.md")
            report = cps.build_report(policy_manifest(root, open_repo, engine_repo))
            self.assertTrue(any(item["subtype"] == "untracked_file" for item in report["findings"]))
            finding = [item for item in report["findings"] if item["subtype"] == "untracked_file"][0]
            self.assertEqual("ledger", finding["evidence"][0]["source_tier"])
            self.assertEqual("warn", finding["evidence"][0]["finding_mode"])

    def test_historical_handoff_reference_is_summary_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_repo = root / "open"
            engine_repo = root / "engine"
            init_repo(open_repo)
            init_repo(engine_repo)
            version_files(open_repo)
            ledger(open_repo)
            write(open_repo / "02_Handoffs/HANDOFF_old.md", "See `Docs/Missing.md`.\n")
            version_files(engine_repo)
            ledger(engine_repo)
            commit_all(open_repo)
            commit_all(engine_repo)
            report = cps.build_report(policy_manifest(root, open_repo, engine_repo))
            self.assertFalse(any(item.get("subtype") == "missing_file" for item in report["findings"]))
            self.assertTrue(
                any(
                    item["source_tier"] == "handoff"
                    and item["finding_mode"] == "summary"
                    and item["status"] == "missing_file"
                    for item in report["reference_summary"]
                )
            )

    def test_missing_spec_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_repo = root / "open"
            engine_repo = root / "engine"
            init_repo(open_repo)
            init_repo(engine_repo)
            version_files(open_repo)
            ledger(open_repo, "See SPEC-999.\n")
            version_files(engine_repo)
            ledger(engine_repo)
            commit_all(open_repo)
            commit_all(engine_repo)
            report = cps.build_report(manifest(root, open_repo, engine_repo))
            self.assertTrue(any(item["subtype"] == "missing_spec" for item in report["findings"]))

    def test_moved_spec_path_is_summary_not_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_repo = root / "open"
            engine_repo = root / "engine"
            init_repo(open_repo)
            init_repo(engine_repo)
            version_files(open_repo)
            ledger(open_repo, "Old path `01_Specs/active/SPEC-001_demo.md`.\n")
            write(open_repo / "01_Specs/implemented/SPEC-001_demo.md", "# SPEC-001: Demo\n")
            version_files(engine_repo)
            ledger(engine_repo)
            commit_all(open_repo)
            commit_all(engine_repo)
            report = cps.build_report(policy_manifest(root, open_repo, engine_repo))
            self.assertFalse(any(item.get("subtype") == "moved_spec_path" for item in report["findings"]))
            self.assertTrue(any(ref["status"] == "moved_spec_path" for ref in report["cross_references"]))
            self.assertTrue(any(item["status"] == "moved_spec_path" for item in report["reference_summary"]))

    def test_dirty_worktree_is_context_not_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_repo = root / "open"
            engine_repo = root / "engine"
            init_repo(open_repo)
            init_repo(engine_repo)
            version_files(open_repo)
            ledger(open_repo)
            version_files(engine_repo)
            ledger(engine_repo)
            commit_all(open_repo)
            commit_all(engine_repo)
            write(open_repo / "dirty.txt", "dirty\n")
            report = cps.build_report(manifest(root, open_repo, engine_repo))
            self.assertTrue(any(item["kind"] == "dirty_worktree_context" and item["severity"] == "info" for item in report["findings"]))
            dirty = [item for item in report["findings"] if item["kind"] == "dirty_worktree_context"][0]
            self.assertIn("dirty.txt", dirty["evidence"][0]["untracked"])

    def test_missing_commit_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_repo = root / "open"
            engine_repo = root / "engine"
            init_repo(open_repo)
            init_repo(engine_repo)
            version_files(open_repo)
            ledger(open_repo, "Missing commit deadbee.\n")
            version_files(engine_repo)
            ledger(engine_repo)
            commit_all(open_repo)
            commit_all(engine_repo)
            report = cps.build_report(manifest(root, open_repo, engine_repo))
            self.assertTrue(any(item["subtype"] == "missing_commit" for item in report["findings"]))

    def test_hex_without_commit_context_is_skipped(self) -> None:
        refs = cps.extract_references(Path("/tmp/TODO.md"), "SQLite application id 1280659011 and magic 4C554E43.\n")
        self.assertFalse(any(ref["kind"] == "commit" for ref in refs))

    def test_route_and_branch_like_paths_are_skipped(self) -> None:
        refs = cps.extract_references(Path("/tmp/TODO.md"), "`/api/diagnostics/assemble-preview` and `feat/stage0-branch`.\n")
        self.assertEqual([], refs)

    def test_script_command_args_are_stripped_from_path_reference(self) -> None:
        refs = cps.extract_references(Path("/tmp/TODO.md"), "Run `scripts/run.py --server`.\n")
        self.assertEqual("scripts/run.py", refs[0]["target"])

    def test_local_artifact_from_handoff_is_summary_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_repo = root / "open"
            engine_repo = root / "engine"
            init_repo(open_repo)
            init_repo(engine_repo)
            version_files(open_repo)
            ledger(open_repo)
            write(open_repo / "02_Handoffs/HANDOFF_old.md", "Generated `frontend/dist/index.html`.\n")
            version_files(engine_repo)
            ledger(engine_repo)
            commit_all(open_repo)
            commit_all(engine_repo)
            report = cps.build_report(policy_manifest(root, open_repo, engine_repo))
            self.assertFalse(any(item.get("subtype") == "local_artifact" for item in report["findings"]))
            self.assertTrue(any(item["status"] == "local_artifact" for item in report["reference_summary"]))

    def test_cross_boundary_candidate_and_suppression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_repo = root / "open"
            engine_repo = root / "engine"
            init_repo(open_repo)
            init_repo(engine_repo)
            version_files(open_repo)
            ledger(open_repo)
            version_files(engine_repo)
            ledger(engine_repo)
            write(engine_repo / "src/luna_mcp/tools/aibrarian.py", "print('x')\n")
            commit_all(open_repo)
            commit_all(engine_repo)
            report = cps.build_report(manifest(root, open_repo, engine_repo))
            self.assertTrue(any(item["kind"] == "cross_boundary_review_candidate" for item in report["findings"]))

            ledger(open_repo, "Reviewed `src/luna_mcp/tools/aibrarian.py`.\n")
            commit_all(open_repo, "mention")
            report = cps.build_report(manifest(root, open_repo, engine_repo))
            self.assertFalse(any(item["kind"] == "cross_boundary_review_candidate" for item in report["findings"]))

    def test_json_and_markdown_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_repo = root / "open"
            engine_repo = root / "engine"
            init_repo(open_repo)
            init_repo(engine_repo)
            version_files(open_repo)
            ledger(open_repo)
            version_files(engine_repo)
            ledger(engine_repo)
            commit_all(open_repo)
            commit_all(engine_repo)
            report = cps.build_report(manifest(root, open_repo, engine_repo))
            self.assertIn("schema_version", report)
            self.assertIn("projects", report)
            self.assertIn("findings", report)
            self.assertIn("cross_references", report)
            self.assertIn("reference_summary", report)
            self.assertFalse(report["mutation_performed"])
            rendered = cps.render_markdown(report)
            self.assertIn("# Cross-Project Sync Report", rendered)
            self.assertIn("## Reference Summary", rendered)
            self.assertIn("No mutation performed: `false`", rendered)

    def test_no_mutation_commands_are_allowed(self) -> None:
        with mock.patch.object(cps.subprocess, "run") as run:
            for subcommand in cps.FORBIDDEN_GIT_SUBCOMMANDS:
                with self.assertRaises(cps.CommandPolicyError):
                    cps.run_git(Path("/tmp"), subcommand)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
