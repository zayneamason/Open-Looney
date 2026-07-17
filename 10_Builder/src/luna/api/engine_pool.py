"""
EnginePool — async pool of LunaEngine instances keyed by profile slug.

Cold start: first request for a profile triggers `factory(slug)` which builds and
boots a fresh engine bound to that profile's .lun. Subsequent requests reuse the
warm engine. Concurrent requests for the same slug share the engine via per-slug
asyncio.Lock so we never cold-start the same profile twice in parallel.

Capacity: when the pool would exceed `max_engines`, the LRU engine is evicted
(stopped + dropped). Idle engines (not used in `idle_timeout_seconds`) are
evicted by an external reaper task — call `reap_idle()` periodically from the
FastAPI lifespan.

Shutdown: `stop_all()` gracefully stops every live engine. Called from lifespan.

This module deliberately depends on a callable factory rather than importing
LunaEngine directly so tests can inject lightweight mocks without booting real
actors / opening real .lun files.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional, Protocol

logger = logging.getLogger(__name__)


class _EngineLike(Protocol):
    """The duck-type the pool needs from a LunaEngine."""
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


# Factory signature: takes a profile slug, returns an awaitable that resolves
# to a started engine. Default factory imports LunaEngine lazily.
EngineFactory = Callable[[str], Awaitable[_EngineLike]]


async def _default_factory(slug: str) -> _EngineLike:
    """Build + boot a real LunaEngine bound to the given profile.

    LunaEngine has no `start()` — its lifecycle is driven by `run()` (main
    loop) which awaits `_shutdown_event`. We fire `run()` as a background
    task and wait for `_ready_event` to confirm boot completed.
    """
    from luna.engine import EngineConfig, LunaEngine
    engine = LunaEngine(EngineConfig(profile_id=slug))
    # Fire run() as a background task; it sets _ready_event after _boot()
    # completes and then awaits _shutdown_event indefinitely.
    asyncio.create_task(engine.run(), name=f"engine-{slug}")
    # Wait for boot (with a generous timeout — cold boot loads MLX, SQLite, etc.)
    try:
        await asyncio.wait_for(engine._ready_event.wait(), timeout=60.0)
    except asyncio.TimeoutError:
        # Try to stop cleanly if it's still half-booted
        try:
            await engine.stop()
        except Exception:
            pass
        raise RuntimeError(f"Engine for profile {slug!r} did not signal ready within 60s")
    return engine


class EnginePool:
    """Process-local pool of LunaEngine instances, keyed by profile slug."""

    def __init__(
        self,
        *,
        factory: Optional[EngineFactory] = None,
        max_engines: int = 8,
        idle_timeout_seconds: float = 900.0,  # 15 minutes
    ) -> None:
        self._factory: EngineFactory = factory or _default_factory
        self._max_engines = max_engines
        self._idle_timeout = idle_timeout_seconds

        # State
        self._engines: dict[str, _EngineLike] = {}
        self._last_used: dict[str, float] = {}
        self._init_locks: dict[str, asyncio.Lock] = {}
        self._dict_lock = asyncio.Lock()  # guards _init_locks dict
        self._closed = False

    # ── Public API ──────────────────────────────────────────────

    @property
    def size(self) -> int:
        return len(self._engines)

    def slugs(self) -> list[str]:
        return list(self._engines.keys())

    def has(self, slug: str) -> bool:
        return slug in self._engines

    async def get(self, slug: str) -> _EngineLike:
        """Return the engine for `slug`, cold-starting if needed.

        Concurrent gets for the same slug share the cold-start. If the pool is
        at capacity, the LRU engine is evicted before the new one is created.
        """
        if self._closed:
            raise RuntimeError("EnginePool is closed")
        if not slug:
            raise ValueError("slug must be a non-empty string")

        # Fast path: warm engine
        if slug in self._engines:
            self._last_used[slug] = time.monotonic()
            return self._engines[slug]

        # Get/create the per-slug init lock
        async with self._dict_lock:
            if slug not in self._init_locks:
                self._init_locks[slug] = asyncio.Lock()
            init_lock = self._init_locks[slug]

        async with init_lock:
            # Re-check after acquiring lock — another task may have just created it
            if slug in self._engines:
                self._last_used[slug] = time.monotonic()
                return self._engines[slug]

            # Capacity check — evict LRU before creating
            if len(self._engines) >= self._max_engines:
                await self._evict_lru()

            logger.info("EnginePool: cold-starting engine for profile %r", slug)
            t0 = time.monotonic()
            try:
                engine = await self._factory(slug)
            except Exception as e:
                logger.error("EnginePool: factory failed for %r: %s", slug, e)
                raise
            elapsed = time.monotonic() - t0
            self._engines[slug] = engine
            self._last_used[slug] = time.monotonic()
            logger.info(
                "EnginePool: engine for %r ready in %.2fs (pool size: %d)",
                slug, elapsed, len(self._engines),
            )
            return engine

    async def register(self, slug: str, engine: _EngineLike) -> None:
        """Register an already-started engine with the pool.

        Used to plug the legacy lifespan-managed `_engine` into the pool as the
        default profile's slot, so other endpoints can resolve it through the
        pool API without double-booting.
        """
        if self._closed:
            raise RuntimeError("EnginePool is closed")
        if slug in self._engines:
            raise ValueError(f"Profile {slug!r} already registered in pool")
        if len(self._engines) >= self._max_engines:
            await self._evict_lru()
        self._engines[slug] = engine
        self._last_used[slug] = time.monotonic()
        logger.info("EnginePool: registered pre-started engine for %r", slug)

    async def evict(self, slug: str) -> bool:
        """Stop + drop a specific engine. Returns True if it was present."""
        if slug not in self._engines:
            return False
        engine = self._engines.pop(slug)
        self._last_used.pop(slug, None)
        await self._stop_quietly(slug, engine)
        return True

    async def reap_idle(self) -> list[str]:
        """Evict engines that haven't been used in `idle_timeout_seconds`.

        Returns the slugs that were evicted. Call periodically from a
        background task.
        """
        if self._closed:
            return []
        now = time.monotonic()
        stale = [
            slug for slug, last in list(self._last_used.items())
            if (now - last) > self._idle_timeout and slug in self._engines
        ]
        evicted: list[str] = []
        for slug in stale:
            engine = self._engines.pop(slug, None)
            self._last_used.pop(slug, None)
            if engine is not None:
                await self._stop_quietly(slug, engine, reason="idle")
                evicted.append(slug)
        return evicted

    async def stop_all(self) -> None:
        """Stop every engine in the pool. Called from lifespan shutdown."""
        self._closed = True
        slugs = list(self._engines.keys())
        for slug in slugs:
            engine = self._engines.pop(slug)
            self._last_used.pop(slug, None)
            await self._stop_quietly(slug, engine, reason="shutdown")
        logger.info("EnginePool: stopped %d engine(s)", len(slugs))

    # ── Internal ────────────────────────────────────────────────

    async def _evict_lru(self) -> None:
        if not self._engines:
            return
        oldest_slug = min(self._engines, key=lambda s: self._last_used.get(s, 0.0))
        engine = self._engines.pop(oldest_slug)
        self._last_used.pop(oldest_slug, None)
        await self._stop_quietly(oldest_slug, engine, reason="lru-eviction")

    async def _stop_quietly(self, slug: str, engine: _EngineLike, *, reason: str = "") -> None:
        suffix = f" ({reason})" if reason else ""
        try:
            await engine.stop()
            logger.info("EnginePool: stopped engine for %r%s", slug, suffix)
        except Exception as e:
            logger.error("EnginePool: error stopping engine for %r%s: %s", slug, suffix, e)
