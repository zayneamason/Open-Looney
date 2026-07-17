"""
LunaFM Station — scheduler for background cognitive channels.

Registered as a LunaFMActor so lifecycle ties into engine._boot() and
the actor mailbox can carry pause/resume control messages. The actual
scheduling happens in a separate asyncio task owned by the Station.

Safety guardrails enforced here:
  - source tag always 'lunafm:{channel_id}'
  - lock_in clamped to [0.01, 0.5]
  - Writes skipped while engine._is_processing is True
  - Station never blocks conversation: preempted is a flag channels poll
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

import yaml

from luna.actors.base import Actor, Message
from luna.lunafm.channel import Channel

if TYPE_CHECKING:
    from luna.engine import LunaEngine

logger = logging.getLogger(__name__)


class _FallbackLLMAdapter:
    """Wraps FallbackChain to expose a .complete() matching OllamaProvider."""

    def __init__(self, chain):
        self._chain = chain

    async def complete(self, messages=None, temperature=0.7, max_tokens=512, model=None, **kw):
        from luna.llm.base import Message as LLMMessage
        # Convert LLMMessage objects to dicts, extract system prompt
        system = ""
        msg_dicts = []
        for m in (messages or []):
            role = m.role if hasattr(m, "role") else m.get("role", "user")
            content = m.content if hasattr(m, "content") else m.get("content", "")
            if role == "system":
                system = content
            else:
                msg_dicts.append({"role": role, "content": content})
        result = await self._chain.generate(
            messages=msg_dicts,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        # Return an object with .content like OllamaProvider does
        return LLMMessage(role="assistant", content=result.content)


class Station:
    """
    LunaFM station — owns channels, schedules them, and exposes shared
    resources (engine, db, llm) to handlers.
    """

    def __init__(self, engine: "LunaEngine", config_path: Path):
        self.engine = engine
        self._config_path = config_path
        self.config: dict = self._load_config(config_path)
        self.channels: dict[str, Channel] = {}
        self._running = False
        self._preempted = False
        self._task: Optional[asyncio.Task] = None
        self._llm = None  # lazy OllamaProvider
        self._start_time = 0.0
        self._subscribers: set[asyncio.Queue] = set()
        self._spectral = None  # SpectralEngine, set in start()
        self._spectral_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------
    def _load_config(self, path: Path) -> dict:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    # ------------------------------------------------------------------
    # Shared resources
    # ------------------------------------------------------------------
    @property
    def db(self):
        """Shared MemoryDatabase via MatrixActor (async, WAL, retry)."""
        matrix = self.engine.get_actor("matrix") if self.engine else None
        return getattr(matrix, "_db", None) if matrix else None

    @property
    def llm(self):
        """LLM access via the engine's fallback chain (online or offline).

        Returns a wrapper with a .complete() method matching the
        OllamaProvider interface so handlers work unchanged, while
        routing through the Director's fallback chain under the hood.
        """
        if self._llm is not None:
            return self._llm
        # Use the Director's fallback chain — respects user's provider config
        director = self.engine.get_actor("director") if self.engine else None
        chain = getattr(director, "_fallback_chain", None) if director else None
        if chain is not None:
            self._llm = _FallbackLLMAdapter(chain)
            return self._llm
        # Last resort: try OllamaProvider directly
        try:
            from luna.llm.providers.ollama_provider import OllamaProvider
            self._llm = OllamaProvider()
        except Exception as e:
            logger.warning(f"[LUNAFM] No LLM available: {e}")
        return self._llm

    @property
    def default_model(self) -> str:
        return (
            self.config.get("station", {})
            .get("default_model", {})
            .get("model", "qwen3.5:9b")
        )

    def engine_is_processing(self) -> bool:
        return bool(getattr(self.engine, "_is_processing", False))

    @property
    def preempted(self) -> bool:
        return self._preempted or self.engine_is_processing()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        channels_dir_cfg = self.config.get("station", {}).get(
            "channels_dir", "config/lunafm/channels/"
        )
        channels_dir = Path(channels_dir_cfg)
        if not channels_dir.is_absolute():
            # Resolve relative to project root (engine cwd at boot)
            channels_dir = Path.cwd() / channels_dir

        count = 0
        if channels_dir.exists():
            for yaml_file in sorted(channels_dir.glob("*.yaml")):
                try:
                    ch = Channel.from_yaml(yaml_file, self)
                    self.channels[ch.id] = ch
                    count += 1
                    logger.info(f"[LUNAFM] Loaded channel: {ch.id} ({ch.name})")
                except Exception as e:
                    logger.warning(f"[LUNAFM] Failed to load {yaml_file}: {e}")
        else:
            logger.warning(f"[LUNAFM] channels_dir not found: {channels_dir}")

        self._running = True
        self._start_time = time.time()
        self._task = asyncio.create_task(self._run_loop())

        # Spectral engine (optional — requires scipy)
        try:
            from luna.lunafm.spectral import SpectralEngine
            if self.db is not None:
                self._spectral = SpectralEngine(self.db)
                self._spectral_task = asyncio.create_task(self._spectral_loop())
                logger.info("[LUNAFM] Spectral engine online")
        except Exception as e:
            logger.info(f"[LUNAFM] Spectral engine disabled: {e}")

        logger.info(f"[LUNAFM] Station on air. {count} channels loaded.")

    async def stop(self) -> None:
        self._running = False
        if self._spectral_task:
            self._spectral_task.cancel()
            try:
                await self._spectral_task
            except asyncio.CancelledError:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _spectral_loop(self) -> None:
        """Periodic edge-delta check + decomposition. 60s cadence."""
        if self._spectral is None:
            return
        # Initial compute after a short warmup
        await asyncio.sleep(10.0)
        while self._running:
            try:
                if not self.engine_is_processing():
                    if await self._spectral.should_recompute():
                        await self._spectral.compute()
                await asyncio.sleep(60.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[SPECTRAL] loop error: {e}")
                await asyncio.sleep(30.0)

    async def preempt(self) -> None:
        if not self._preempted:
            self._preempted = True
            logger.debug("[LUNAFM] All channels preempted")
        for ch in self.channels.values():
            await ch.pause()

    async def resume(self) -> None:
        cooldown_ms = (
            self.config.get("station", {})
            .get("budget", {})
            .get("cooldown_after_conversation_ms", 3000)
        )
        await asyncio.sleep(cooldown_ms / 1000.0)
        self._preempted = False
        for ch in self.channels.values():
            await ch.resume()
        logger.debug("[LUNAFM] Channels resuming after conversation cooldown")

    # ------------------------------------------------------------------
    # Hot reload (Settings panel)
    # ------------------------------------------------------------------
    @property
    def station_enabled(self) -> bool:
        return bool(self.config.get("station", {}).get("enabled", True))

    def _channels_dir(self) -> Path:
        channels_dir_cfg = self.config.get("station", {}).get(
            "channels_dir", "config/lunafm/channels/"
        )
        channels_dir = Path(channels_dir_cfg)
        if not channels_dir.is_absolute():
            channels_dir = Path.cwd() / channels_dir
        return channels_dir

    async def reload(self) -> dict:
        """
        Re-read station.yaml + channels/*.yaml from disk without restarting
        the main loop. Channels added in YAML get loaded; channels removed
        get paused and discarded; existing channels pick up new interval /
        enabled values.
        """
        self.config = self._load_config(self._config_path)
        channels_dir = self._channels_dir()
        loaded_ids: set[str] = set()
        if channels_dir.exists():
            for yaml_file in sorted(channels_dir.glob("*.yaml")):
                try:
                    with open(yaml_file, encoding="utf-8") as f:
                        config = yaml.safe_load(f) or {}
                    ch_cfg = config.get("channel") or {}
                    ch_id = ch_cfg.get("id")
                    if not ch_id:
                        continue
                    loaded_ids.add(ch_id)
                    existing = self.channels.get(ch_id)
                    if existing:
                        existing._full_config = config
                        existing._config = ch_cfg
                    else:
                        new_ch = Channel(config, self)
                        self.channels[ch_id] = new_ch
                        logger.info(f"[LUNAFM] Loaded channel (reload): {ch_id}")
                except Exception as e:
                    logger.warning(f"[LUNAFM] reload {yaml_file} failed: {e}")

        # Prune channels that no longer have a YAML
        for ch_id in list(self.channels.keys()):
            if ch_id not in loaded_ids:
                logger.info(f"[LUNAFM] Removing channel: {ch_id}")
                await self.channels[ch_id].pause()
                del self.channels[ch_id]

        return self.status()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def _run_loop(self) -> None:
        while self._running:
            try:
                if self._preempted or not self.station_enabled:
                    await asyncio.sleep(0.5)
                    continue

                # priority list may drift if channels were reloaded
                priority = (
                    self.config.get("station", {}).get("priority_order")
                    or list(self.channels.keys())
                )
                max_concurrent = int(
                    self.config.get("station", {}).get("budget", {}).get("max_concurrent_channels", 2)
                )
                active = sum(1 for c in self.channels.values() if c.is_active)
                for channel_id in priority:
                    if active >= max_concurrent:
                        break
                    channel = self.channels.get(channel_id)
                    if channel and channel.should_tick():
                        asyncio.create_task(channel.tick())
                        active += 1

                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[LUNAFM] run loop error: {e}", exc_info=True)
                await asyncio.sleep(2.0)

    # ------------------------------------------------------------------
    # Artifact writer — the ONLY path to memory_nodes for channels
    # ------------------------------------------------------------------
    async def write_artifact(
        self,
        *,
        channel_id: str,
        node_type: str,
        content: str,
        lock_in: float = 0.15,
        importance: float = 0.3,
        metadata: Optional[dict] = None,
    ) -> Optional[str]:
        """
        Insert a LunaFM-tagged memory node. Returns the new node id.

        Safety: clamps lock_in, forces source, skips during active turn.
        """
        if self.engine_is_processing():
            logger.debug(f"[LUNAFM:{channel_id}] skipped write during active turn")
            return None

        db = self.db
        if db is None:
            logger.debug(f"[LUNAFM:{channel_id}] db not available, skipping write")
            return None

        lock_in = max(0.01, min(0.5, float(lock_in)))
        source = f"lunafm:{channel_id}"
        node_id = f"lfm_{uuid.uuid4().hex[:16]}"

        import json
        from datetime import datetime

        now_iso = datetime.utcnow().isoformat()
        meta_json = json.dumps(metadata or {})

        try:
            await db.execute(
                """
                INSERT INTO memory_nodes
                    (id, node_type, content, source, lock_in, confidence,
                     importance, access_count, created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    node_id,
                    node_type,
                    content,
                    source,
                    lock_in,
                    0.5,
                    importance,
                    now_iso,
                    now_iso,
                    meta_json,
                ),
            )
        except Exception as e:
            logger.warning(f"[LUNAFM:{channel_id}] write_artifact failed: {e}")
            return None

        # Drop a pointer into OUTER ring so foreground retrieval can see it
        try:
            from luna.core.context import ContextSource, ContextRing
            from luna.core.types import Door
            ctx = getattr(self.engine, "context", None)
            if ctx is not None:
                ctx.add(
                    content=content,
                    source=ContextSource.MEMORY,
                    door=Door.MEMORY_MATRIX,
                    ring=ContextRing.OUTER,
                    relevance=0.3,
                    metadata={"lunafm_channel": channel_id, "node_id": node_id},
                )
        except Exception as e:
            logger.debug(f"[LUNAFM:{channel_id}] revolving context add failed: {e}")

        logger.info(f"[LUNAFM:{channel_id}] {node_type}: {content[:80]}")

        # Nudge LunaScript trait (best-effort, never blocks)
        try:
            from luna.lunafm.frequency_coupling import (
                EMISSION_NUDGES,
                nudge_trait,
            )
            spec = EMISSION_NUDGES.get((channel_id, node_type))
            if spec:
                trait, delta = spec
                new_val = await nudge_trait(db, trait, delta)
                if new_val is not None:
                    self.broadcast({
                        "type": "trait_nudge",
                        "trait": trait,
                        "delta": delta,
                        "new_value": new_val,
                        "source": channel_id,
                        "timestamp": now_iso,
                    })
        except Exception as e:
            logger.debug(f"[LUNAFM:{channel_id}] nudge failed: {e}")

        # Fan out to SSE subscribers (non-blocking)
        event = {
            "type": "emission",
            "channel": channel_id,
            "node_type": node_type,
            "content": content,
            "lock_in": lock_in,
            "node_id": node_id,
            "metadata": metadata or {},
            "timestamp": now_iso,
        }
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

        return node_id

    # ------------------------------------------------------------------
    # SSE subscription
    # ------------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        """Register a subscriber queue for live emissions."""
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def broadcast(self, event: dict) -> None:
        """Fan out an arbitrary event to all SSE subscribers (non-blocking)."""
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # ------------------------------------------------------------------
    # Status (for /lunafm/status endpoint and debugging)
    # ------------------------------------------------------------------
    def status(self) -> dict:
        return {
            "running": self._running,
            "preempted": self._preempted,
            "uptime_s": time.time() - self._start_time if self._start_time else 0,
            "channels": [c.status() for c in self.channels.values()],
            "spectral": self._spectral.status() if self._spectral else None,
        }


# =============================================================================
# LunaFMActor — thin wrapper that plugs Station into actor lifecycle
# =============================================================================

class LunaFMActor(Actor):
    """Actor wrapper so LunaFM gets boot/shutdown + mailbox for control."""

    def __init__(self, engine: "LunaEngine", config_path: Path):
        super().__init__(name="lunafm", engine=engine)
        self._station = Station(engine, config_path)

    @property
    def station(self) -> Station:
        return self._station

    async def on_start(self) -> None:
        await self._station.start()

    async def on_stop(self) -> None:
        await self._station.stop()

    async def handle(self, msg: Message) -> None:
        if msg.type == "preempt":
            await self._station.preempt()
        elif msg.type == "resume":
            await self._station.resume()
        elif msg.type == "status":
            # reply_to could be used here if another actor wants status
            logger.debug(f"[LUNAFM] status: {self._station.status()}")
