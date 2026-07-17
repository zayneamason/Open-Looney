"""
LunaFM Channel — a single mode of background attention.

YAML-driven async state machine:
    idle → scanning → processing → emitting → cooldown → idle

States map to Python coroutines defined in
`luna.lunafm.handlers.{channel_id}`. Channels do not own actors or
database connections — they call into the Station, which exposes
`engine`, `db` (via MatrixActor), and `llm` (OllamaProvider).

Preemption is cooperative: channels check `station.preempted` at
every state transition and yield cleanly if set.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import time
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, TYPE_CHECKING

import yaml

from luna.lunafm.cache import LRUDedupCache

if TYPE_CHECKING:
    from luna.lunafm.station import Station

logger = logging.getLogger(__name__)


class ChannelState(Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    PROCESSING = "processing"
    EMITTING = "emitting"
    COOLDOWN = "cooldown"
    PAUSED = "paused"


class Channel:
    """A single LunaFM channel driven by its YAML config."""

    def __init__(self, config: dict, station: "Station"):
        self._full_config = config
        self._config = config["channel"]
        self._station = station
        self.id: str = self._config["id"]
        self.name: str = self._config.get("name", self.id)
        self._state = ChannelState.IDLE
        self._last_tick = 0.0
        self._is_active = False

        cache_cfg = self._config.get("cache", {}) or {}
        self._cache = LRUDedupCache(
            max_items=cache_cfg.get("max_items", 50),
            dedup_window_s=cache_cfg.get("dedup_window_s", 300),
        )

        self._handlers = self._load_handlers()

    # ------------------------------------------------------------------
    # YAML loading
    # ------------------------------------------------------------------
    @classmethod
    def from_yaml(cls, path: Path, station: "Station") -> "Channel":
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return cls(config, station)

    def _load_handlers(self) -> dict[str, Callable]:
        module_name = f"luna.lunafm.handlers.{self.id}"
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            logger.warning(f"[LUNAFM:{self.id}] No handler module '{module_name}': {e}")
            return {}
        handlers: dict[str, Callable] = {}
        for attr in dir(module):
            if attr.startswith("_"):
                continue
            obj = getattr(module, attr)
            if callable(obj):
                handlers[attr] = obj
        return handlers

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def state(self) -> ChannelState:
        return self._state

    @property
    def interval_s(self) -> float:
        freq = self._config.get("frequency", {})
        return float(freq.get("interval_s", 60))

    @property
    def max_nodes_per_hour(self) -> int:
        return int(
            self._station.config.get("station", {})
            .get("targets", {})
            .get("memory_matrix", {})
            .get("max_nodes_per_hour", 20)
        )

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        # Default True; explicit false in YAML disables the channel.
        return bool(self._config.get("enabled", True))

    def should_tick(self) -> bool:
        if not self.enabled:
            return False
        if self._state == ChannelState.PAUSED:
            return False
        if self._is_active:
            return False
        if (time.time() - self._last_tick) < self.interval_s:
            return False
        only_when = self._config.get("frequency", {}).get("only_when")
        if only_when == "idle" and self._station.engine_is_processing():
            return False
        return True

    def apply_config_update(self, updates: dict) -> None:
        """Apply a partial update to this channel's config in-memory."""
        if "enabled" in updates:
            self._config["enabled"] = bool(updates["enabled"])
        if "interval_s" in updates:
            self._config.setdefault("frequency", {})["interval_s"] = float(updates["interval_s"])

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------
    async def tick(self) -> None:
        """Run one full cycle of the channel state machine."""
        self._is_active = True
        self._last_tick = time.time()
        try:
            await self._run_cycle()
        except asyncio.CancelledError:
            logger.debug(f"[LUNAFM:{self.id}] cancelled in {self._state.value}")
        except Exception as e:
            logger.warning(f"[LUNAFM:{self.id}] error in {self._state.value}: {e}", exc_info=True)
        finally:
            prev = self._state
            self._state = ChannelState.IDLE
            self._is_active = False
            if prev != ChannelState.IDLE:
                self._broadcast_state_change(prev.value, "idle")

    # Canonical order: state machine walks states in this order, skipping any
    # that don't have a process defined. First stage's result must have
    # signal=True to continue; subsequent stages must have emit!=False.
    _CHAIN_ORDER = (
        "scanning", "surveying", "sampling",
        "processing", "analyzing", "associating",
        "consolidating", "evaluating", "emitting",
    )

    async def _run_cycle(self) -> None:
        # Hourly emission cap
        if self._cache.hourly_count() >= self.max_nodes_per_hour:
            logger.debug(f"[LUNAFM:{self.id}] hourly cap reached")
            return
        if self._station.preempted:
            return

        states_cfg = self._config.get("states", {})
        chain = [
            s for s in self._CHAIN_ORDER
            if states_cfg.get(s, {}).get("process")
        ]
        if not chain:
            return

        ctx: dict = {}
        for i, state_name in enumerate(chain):
            if self._station.preempted:
                return
            result = await self._run_process(state_name, ctx)
            ctx = {**ctx, **(result or {})}
            # First stage must return signal=True
            if i == 0:
                if not ctx.get("signal"):
                    return
            else:
                # Middle / final stages can short-circuit with emit=False
                if result.get("emit") is False:
                    return

        if ctx.get("artifact_written"):
            self._cache.increment_hour()

        # Cooldown — interruptible
        self._state = ChannelState.COOLDOWN
        cooldown_ms = (
            states_cfg.get("cooldown", {}).get("duration_ms")
            or self._config.get("cooldown_ms")
            or 5000
        )
        waited = 0.0
        while waited < cooldown_ms / 1000.0:
            if self._station.preempted:
                return
            await asyncio.sleep(0.2)
            waited += 0.2

    async def _run_process(self, state_name: str, context: dict) -> dict:
        prev = self._state
        if state_name in ChannelState._value2member_map_:
            self._state = ChannelState(state_name)
        if self._state != prev:
            self._broadcast_state_change(prev.value, self._state.value)
        state_cfg = self._config.get("states", {}).get(state_name, {})
        process_name = state_cfg.get("process")
        timeout_ms = state_cfg.get("timeout_ms", 5000)

        if not process_name:
            return {}

        handler = self._handlers.get(process_name)
        if not handler:
            logger.debug(f"[LUNAFM:{self.id}] no handler '{process_name}'")
            return {}

        try:
            return await asyncio.wait_for(
                handler(self._station, self, context),
                timeout=timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            logger.debug(f"[LUNAFM:{self.id}] '{process_name}' timed out ({timeout_ms}ms)")
            return {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def pause(self) -> None:
        self._state = ChannelState.PAUSED

    async def resume(self) -> None:
        if self._state == ChannelState.PAUSED:
            self._state = ChannelState.IDLE

    def status(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "state": self._state.value,
            "is_active": self._is_active,
            "interval_s": self.interval_s,
            "emissions_last_hour": self._cache.hourly_count(),
        }

    def _broadcast_state_change(self, from_state: str, to_state: str) -> None:
        """Push a state_change event to SSE subscribers."""
        try:
            from datetime import datetime
            self._station.broadcast({
                "type": "state_change",
                "channel": self.id,
                "from": from_state,
                "to": to_state,
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception:
            pass
