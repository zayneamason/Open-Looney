"""
Centralized path resolution for Luna Engine.

Under normal Python, paths resolve relative to this file's location.
Under Nuitka (compiled binary), paths resolve relative to the executable.
The LUNA_BASE_PATH env var overrides both.

Profile-aware resolution: user_dir() and memory_matrix_path() consult the
active profile (from the _current_profile contextvar, set by auth middleware
on HTTP or by --profile flag on CLI). Pre-profile-system callers continue to
get data/user/ via the legacy fallback path.
"""

import os
import sys
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Optional


@lru_cache(maxsize=1)
def project_root() -> Path:
    """Return the Luna Engine project root directory."""
    env = os.environ.get("LUNA_BASE_PATH")
    if env:
        return Path(env).resolve()
    if getattr(sys, "frozen", False) or hasattr(sys, "__compiled__"):
        return Path(sys.executable).parent
    # core/paths.py -> luna -> src -> PROJECT_ROOT
    return Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def _data_root() -> Path:
    """User data directory — separate from binary in Tauri mode.

    If LUNA_DATA_DIR is set (e.g. ~/Library/Application Support/Luna/),
    config and data live there. Otherwise, fall back to project_root().
    """
    env = os.environ.get("LUNA_DATA_DIR")
    if env:
        return Path(env).resolve()
    return project_root()


# ── Active profile context ────────────────────────────────────────────────
#
# Set by:
#   - HTTP auth middleware (per-request, from session cookie)
#   - CLI --profile flag (process-wide at startup)
#   - Test fixtures (per-test, with reset token)
#
# When None, user_dir() falls back to the legacy data/user/ path so that
# pre-profile-system code paths continue to work during the migration.
_current_profile: ContextVar[Optional[str]] = ContextVar(
    "luna_profile", default=None
)


def current_profile() -> Optional[str]:
    """Active profile slug for this async context, or None."""
    return _current_profile.get()


def set_current_profile(slug: Optional[str]):
    """Set the active profile for this async context.

    Returns a Token that can be passed to reset_current_profile() to
    restore the previous value (useful for nested overrides in tests).
    """
    return _current_profile.set(slug)


def reset_current_profile(token) -> None:
    """Reset the active profile to its prior value (companion to set_current_profile)."""
    _current_profile.reset(token)


def config_dir() -> Path:
    return _data_root() / "config"


def data_dir() -> Path:
    return _data_root() / "data"


def system_dir() -> Path:
    """System data — ships with every install. Read-only at runtime."""
    return data_dir() / "system"


def user_dir(profile_id: Optional[str] = None) -> Path:
    """Per-profile user data directory — the active profile's soul.

    Resolution order:
      1. Explicit profile_id argument (admin tooling, tests)
      2. Active profile from contextvar (auth middleware / CLI flag)
      3. Legacy fallback to data/user/ (pre-profile-system callers)

    Once the profile system is fully active, callers set the contextvar at
    boot and the fallback is unreachable. Until then, the fallback keeps
    existing single-tenant code paths working.
    """
    slug = profile_id or current_profile()
    if slug:
        return data_dir() / "profiles" / slug
    return data_dir() / "user"


def local_dir() -> Path:
    """Local/dev data — never ships. Gitignored."""
    return data_dir() / "local"


# Memory Matrix master DB — single source of truth for the canonical path.
_MEMORY_MATRIX_FILENAME = "memory_matrix.lun"


def memory_matrix_path(profile_id: Optional[str] = None) -> Path:
    """Path to the Memory Matrix master DB for the active (or specified) profile.

    Canonical resolver — every consumer should call this rather than
    constructing user_dir() / "memory_matrix.lun" themselves.
    """
    return user_dir(profile_id) / _MEMORY_MATRIX_FILENAME


def tools_dir() -> Path:
    return project_root() / "Tools"


def scripts_dir() -> Path:
    return project_root() / "scripts"


def frontend_dir() -> Path:
    return project_root() / "frontend" / "dist"
