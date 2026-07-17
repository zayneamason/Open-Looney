"""
Engine dispatch — bridge between the auth layer and the EnginePool.

`request_engine(request)` is the new way endpoints get the engine for the active
profile. Falls back to a registered "default" engine for unauth / pre-profile
code paths so the legacy single-tenant flow keeps working during migration.

Design note: this module deliberately holds two module-level references:

    _pool          — the singleton EnginePool, set by lifespan startup
    _default_engine — the legacy single LunaEngine (admin's), set by lifespan
                      after migration. Used by endpoints that haven't been
                      migrated to per-profile dispatch yet.

The plan is that as endpoints migrate to `await request_engine(request)`, they
opt into per-profile resolution. Legacy endpoints reading the global `_engine`
in server.py continue using the default. Eventually the default goes away.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from fastapi import HTTPException, Request

from luna.auth.middleware import current_session

if TYPE_CHECKING:
    from luna.engine import LunaEngine

    from .engine_pool import EnginePool

logger = logging.getLogger(__name__)


_pool: Optional["EnginePool"] = None
_default_engine: Optional["LunaEngine"] = None


# ── Configuration (called from lifespan) ────────────────────────

def set_pool(pool: Optional["EnginePool"]) -> None:
    """Install the global pool instance. Pass None on shutdown."""
    global _pool
    _pool = pool


def get_pool() -> Optional["EnginePool"]:
    return _pool


def set_default_engine(engine: Optional["LunaEngine"]) -> None:
    """Install the fallback engine for unauth / legacy code paths."""
    global _default_engine
    _default_engine = engine


def get_default_engine() -> Optional["LunaEngine"]:
    return _default_engine


# ── Resolution (called from endpoints) ──────────────────────────

async def request_engine(request: Request) -> "LunaEngine":
    """Return the LunaEngine for the request's active profile.

    Order:
        1. If request is authenticated AND pool is installed → pool.get(slug)
        2. Else if default engine is installed → default
        3. Else → 503

    Raises 503 if no engine is available (e.g., pool not yet started).
    """
    session = current_session(request)
    if session is not None and _pool is not None:
        try:
            return await _pool.get(session.slug)
        except Exception as e:
            logger.error("request_engine: pool.get(%r) failed: %s", session.slug, e)
            raise HTTPException(
                status_code=503,
                detail=f"Engine for profile {session.slug!r} unavailable",
            )

    if _default_engine is not None:
        return _default_engine

    raise HTTPException(status_code=503, detail="Engine not available")


def request_engine_optional(request: Request) -> Optional["LunaEngine"]:
    """Same as request_engine but returns None instead of raising.

    Useful for diagnostic endpoints that want to soft-detect engine availability
    without forcing an HTTPException.
    """
    session = current_session(request)
    if session is not None and _pool is not None and _pool.has(session.slug):
        # Don't trigger cold-start in the optional path — only return if warm
        return None  # callers should use request_engine() if they want cold-start
    return _default_engine
