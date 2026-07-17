"""
FastAPI auth middleware — reads luna_session cookie, sets _current_profile contextvar.

The middleware itself never rejects requests. Endpoints opt-in to enforcement via
require_auth() / require_admin() helpers. This keeps unauth-required routes
(/api/auth/login, /api/health, the first-run wizard) accessible.

The contextvar is the single source of truth for "who is this request for?". All
path resolvers (user_dir, memory_matrix_path, hub_db_path, etc.) read from it
automatically — no per-endpoint plumbing needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request, status

from luna.core.paths import (
    current_profile,
    reset_current_profile,
    set_current_profile,
)

from .registry import ProfileNotFoundError, ProfileRegistry
from .session import SESSION_COOKIE_NAME, parse_session_token

logger = logging.getLogger(__name__)


@dataclass
class CurrentSession:
    """Snapshot of who's logged in for this request."""
    slug: str
    display_name: str
    tier: str  # "admin" | "tester"


async def auth_middleware(request: Request, call_next):
    """Read session cookie, set _current_profile contextvar for the request.

    Always calls through to the endpoint — enforcement lives in require_*().
    Resets the contextvar on the way out so per-task state doesn't leak.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    slug = parse_session_token(token) if token else None

    request.state.luna_session = None  # default — endpoints can introspect
    cv_token = None

    if slug:
        # Validate the slug still exists in the registry. A profile could have
        # been deleted while a session cookie was still valid — fail closed.
        try:
            registry = ProfileRegistry()
            if registry.exists():
                record = registry.get_profile(slug)
                cv_token = set_current_profile(slug)
                request.state.luna_session = CurrentSession(
                    slug=record.slug,
                    display_name=record.display_name,
                    tier=record.tier,
                )
        except ProfileNotFoundError:
            logger.warning(
                "Valid session token for unknown profile %r — treating as unauthenticated",
                slug,
            )
        except Exception as e:
            # Registry read failed — log loudly, treat as unauthenticated.
            logger.error("Auth middleware: registry read failed: %s", e)

    try:
        response = await call_next(request)
    finally:
        if cv_token is not None:
            reset_current_profile(cv_token)

    return response


# ── Enforcement helpers (used as FastAPI deps or inline in endpoints) ────

def current_session(request: Request) -> Optional[CurrentSession]:
    """Return the active session, or None if unauthenticated."""
    return getattr(request.state, "luna_session", None)


def require_auth(request: Request) -> CurrentSession:
    """Endpoint-level auth gate. Raises 401 if no session.

    Usage:
        @app.get("/api/protected")
        async def handler(session: CurrentSession = Depends(require_auth)):
            ...
    """
    session = current_session(request)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return session


def require_admin(request: Request) -> CurrentSession:
    """Endpoint-level admin gate. Raises 401/403."""
    session = require_auth(request)
    if session.tier != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return session
