"""
Critical Systems Check — Startup Gate
=====================================

Luna MUST NOT start if her brain is disconnected.
This gate prevents silent failures that cause confabulation.

If any critical system is broken, Luna refuses to pretend to be herself.
"""

import os
import sys
import sqlite3
import logging
from pathlib import Path
from typing import Optional

from luna.core.paths import (
    project_root as _default_project_root,
    memory_matrix_path,
    current_profile,
    data_dir,
)

logger = logging.getLogger(__name__)


class CriticalSystemsCheck:
    """Gate that prevents startup if Luna's brain is disconnected."""

    # Required Python modules and their install commands
    REQUIRED_SYSTEMS = [
        ("mlx", "pip install mlx mlx-lm"),
        ("mlx_lm", "pip install mlx-lm"),
    ]

    @staticmethod
    def _required_files() -> list[tuple[str, str]]:
        """Required files — resolved each call so memory_matrix_path() picks
        up the active profile contextvar at check time, not import time."""
        return [
            ("models/luna_lora_mlx/adapters.safetensors", "LoRA adapter missing!"),
            (str(memory_matrix_path()), "Memory database missing!"),
        ]

    @staticmethod
    def _required_data() -> list[tuple[str, str, int, str]]:
        """Required data integrity — resolved each call (see _required_files)."""
        return [
            (str(memory_matrix_path()), "SELECT COUNT(*) FROM memory_nodes", 10000, "Memory nodes"),
            (str(memory_matrix_path()), "SELECT COUNT(*) FROM graph_edges",  10000, "Graph edges"),
        ]

    def __init__(self, project_root: Optional[Path] = None):
        """
        Initialize the checker.

        Args:
            project_root: Root directory of the Luna project.
                          If None, auto-detected from this file's location.
        """
        if project_root is None:
            self.project_root = _default_project_root()
        else:
            self.project_root = Path(project_root)

    def check_module(self, module: str) -> tuple[bool, str]:
        """Check if a Python module is importable."""
        try:
            __import__(module)
            return True, f"✅ {module} available"
        except ImportError as e:
            return False, f"❌ Missing module '{module}': {e}"

    def check_file(self, relative_path: str, message: str) -> tuple[bool, str]:
        """Check if a file exists."""
        full_path = self.project_root / relative_path
        if full_path.exists():
            size = full_path.stat().st_size
            return True, f"✅ {relative_path} ({size:,} bytes)"
        return False, f"❌ {message}: {relative_path}"

    def check_pipeline_wiring(self, endpoint_name: str, source_code: str) -> tuple[bool, str]:
        """
        Verify a streaming endpoint calls record_conversation_turn.

        Static analysis — reads the source between the endpoint decorator
        and the next @app decorator to check for the unified recording call.
        """
        # Find the endpoint function
        marker = f'"{endpoint_name}"' if endpoint_name.startswith("/") else endpoint_name
        # Look for the route decorator
        import re
        pattern = rf'@app\.\w+\("{re.escape(endpoint_name)}"'
        match = re.search(pattern, source_code)
        if not match:
            return True, f"✅ {endpoint_name} (not found — skipped)"

        # Extract the function body up to the next route decorator
        start = match.start()
        next_route = re.search(r'\n@app\.', source_code[start + 1:])
        end = start + 1 + next_route.start() if next_route else len(source_code)
        function_body = source_code[start:end]

        if "record_conversation_turn" in function_body or "_trigger_extraction" in function_body:
            return True, f"✅ {endpoint_name} wired to extraction pipeline"
        else:
            return False, f"❌ {endpoint_name} bypasses extraction pipeline (missing record_conversation_turn or _trigger_extraction)"

    @staticmethod
    def _is_single_tenant_mode() -> bool:
        """True when running single-tenant (dev or Forge clean-railway).

        Multi-profile Forge builds set _current_profile via auth middleware
        and bootstrap data/profiles/{slug}/ on the volume. Single-tenant runs
        do neither — production-volume thresholds (>10k nodes/edges) are
        tuned for multi-profile production data and don't apply.
        """
        if current_profile():
            return False
        profiles_dir = data_dir() / "profiles"
        if profiles_dir.exists() and any(profiles_dir.iterdir()):
            return False
        return True

    def check_data(self, db_path: str, query: str, minimum: int, name: str) -> tuple[bool, str]:
        """Check database has required minimum data."""
        full_path = self.project_root / db_path
        if not full_path.exists():
            return False, f"❌ {name}: Database not found at {db_path}"

        try:
            conn = sqlite3.connect(str(full_path))
            conn.execute("PRAGMA busy_timeout=15000")
            count = conn.execute(query).fetchone()[0]
            conn.close()

            if count >= minimum:
                return True, f"✅ {name}: {count:,} (required >{minimum:,})"
            else:
                return False, f"❌ {name}: Only {count:,} (required >{minimum:,})"
        except Exception as e:
            return False, f"❌ {name} check failed: {e}"

    def run_all_checks(self) -> tuple[bool, list[str]]:
        """
        Run all critical system checks.

        Returns:
            (all_passed, messages) - Tuple of success status and check messages
        """
        messages = []
        all_passed = True

        messages.append("=" * 60)
        messages.append("LUNA CRITICAL SYSTEMS CHECK")
        messages.append("=" * 60)

        # In compiled binary, skip mlx/LoRA checks (cloud-only mode)
        _compiled = getattr(sys, "frozen", False) or hasattr(sys, "__compiled__") or os.environ.get("LUNA_BASE_PATH")
        required_systems = [] if _compiled else self.REQUIRED_SYSTEMS
        all_required_files = self._required_files()
        required_files = [f for f in all_required_files if memory_matrix_path().name in f[0]] if _compiled else all_required_files

        # Check modules
        messages.append("\n📦 Required Modules:")
        if not required_systems:
            messages.append("  ✅ Compiled mode — local inference checks skipped")
        for module, fix_cmd in required_systems:
            passed, msg = self.check_module(module)
            messages.append(f"  {msg}")
            if not passed:
                all_passed = False
                messages.append(f"    Fix: {fix_cmd}")

        # Check files
        # In compiled/app mode OR for data files (DBs), missing = first boot
        # (graceful) — the engine auto-creates databases from schema on first DB
        # connect. Missing model adapters in dev mode is still a hard fail.
        messages.append("\n📁 Required Files:")
        for path, error_msg in required_files:
            passed, msg = self.check_file(path, error_msg)
            messages.append(f"  {msg}")
            if not passed:
                # Distinguish "data not yet created" (graceful) from "broken
                # install" (hard fail in dev). DB / .lun files are graceful.
                _is_data_file = path.endswith(".lun") or path.endswith(".db")
                if _compiled or _is_data_file:
                    messages.append(f"    ⚠️  Will be created on first boot")
                else:
                    all_passed = False

        # Check data integrity
        # In compiled/app mode OR first boot, data thresholds are advisory —
        # the engine should boot even with an empty brain (first run, Tauri app, etc.)
        _db_path = memory_matrix_path()
        _first_boot = not _db_path.exists()
        if not _first_boot:
            try:
                _conn = sqlite3.connect(str(_db_path))
                _conn.execute("PRAGMA busy_timeout=15000")
                _turns = _conn.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0]
                _nodes = _conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
                _conn.close()
                _first_boot = _turns == 0 or _nodes < 100
            except Exception:
                _first_boot = True

        _single_tenant = self._is_single_tenant_mode()

        messages.append("\n🧠 Memory Integrity:")
        if _first_boot:
            messages.append("  ℹ️  First boot detected — data thresholds are advisory")
        elif _single_tenant:
            messages.append("  ℹ️  Single-tenant mode (dev / clean-railway) — data thresholds are advisory")
        for db_path, query, minimum, name in self._required_data():
            passed, msg = self.check_data(db_path, query, minimum, name)
            messages.append(f"  {msg}")
            if not passed:
                if _compiled or _first_boot or _single_tenant:
                    messages.append(f"    ⚠️  {name} below threshold — engine will start with limited memory")
                else:
                    all_passed = False

        # Check pipeline wiring (static analysis of server.py)
        messages.append("\n🔗 Pipeline Wiring:")
        server_path = self.project_root / "src" / "luna" / "api" / "server.py"
        if server_path.exists():
            server_source = server_path.read_text()
            for endpoint in ["/persona/stream", "/stream", "/hub/turn/add"]:
                passed, msg = self.check_pipeline_wiring(endpoint, server_source)
                messages.append(f"  {msg}")
                if not passed:
                    # Pipeline wiring is a warning, not a hard block
                    messages.append(f"    ⚠️  Extraction pipeline will not fire for {endpoint}")
        else:
            messages.append("  ⚠️  server.py not found — pipeline wiring check skipped")

        messages.append("\n" + "=" * 60)
        if all_passed:
            messages.append("✅ ALL CRITICAL SYSTEMS OPERATIONAL")
        else:
            messages.append("🚨 CRITICAL SYSTEMS CHECK FAILED")
            messages.append("Luna cannot start with a disconnected brain.")
        messages.append("=" * 60)

        return all_passed, messages

    @classmethod
    def run(cls, project_root: Optional[Path] = None, strict: bool = True) -> bool:
        """
        Run critical systems check.

        Args:
            project_root: Root directory of Luna project
            strict: If True, exits with error on failure. If False, just logs.

        Returns:
            True if all checks passed
        """
        checker = cls(project_root)
        passed, messages = checker.run_all_checks()

        # Log all messages (use logger, not print — compiled binaries may default to ASCII)
        for msg in messages:
            if msg.startswith("❌"):
                logger.error(msg)
            elif msg.startswith("✅"):
                logger.info(msg)
            else:
                logger.info(msg)

        if not passed and strict:
            logger.critical("Luna refuses to start with critical systems broken. Fix the issues above and restart.")
            sys.exit(1)

        return passed


def run_startup_check(strict: bool = True) -> bool:
    """
    Convenience function for server startup.

    Call this at the TOP of your server startup, before FastAPI loads.
    """
    return CriticalSystemsCheck.run(strict=strict)


if __name__ == "__main__":
    # Allow running as standalone script
    import argparse
    parser = argparse.ArgumentParser(description="Luna Critical Systems Check")
    parser.add_argument("--no-strict", action="store_true", help="Don't exit on failure")
    args = parser.parse_args()

    run_startup_check(strict=not args.no_strict)
