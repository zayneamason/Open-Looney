"""
Luna auth — profile-aware authentication for the multi-tenant engine.

Public surface:
    ProfileRegistry — load/save profiles.json, hash + verify passwords, list/create profiles
    make_session_token / parse_session_token — sign/parse session cookies
    auth_middleware — FastAPI middleware that sets the active profile from a cookie
    auto_migrate_to_profile_system — one-shot data/user/ → data/profiles/<slug>/

The active profile is propagated via the contextvar in luna.core.paths
(set_current_profile/current_profile). All path resolvers respect it.
"""

from .registry import (
    ProfileRegistry,
    ProfileNotFoundError,
    ProfileAlreadyExistsError,
    InvalidSlugError,
    InvalidPasswordError,
    profile_registry_path,
)
from .session import make_session_token, parse_session_token, SESSION_COOKIE_NAME
from .middleware import auth_middleware, current_session, require_auth, require_admin
from .migration import auto_migrate_to_profile_system, MigrationResult

__all__ = [
    "ProfileRegistry",
    "ProfileNotFoundError",
    "ProfileAlreadyExistsError",
    "InvalidSlugError",
    "InvalidPasswordError",
    "profile_registry_path",
    "make_session_token",
    "parse_session_token",
    "SESSION_COOKIE_NAME",
    "auth_middleware",
    "current_session",
    "require_auth",
    "require_admin",
    "auto_migrate_to_profile_system",
    "MigrationResult",
]
