"""
Cache Actor for Luna Engine
============================

Owns the Shared Turn Cache: a rotating YAML snapshot that bridges
the Scribe's per-turn extractions to the dimensional engine,
WebSocket subscribers, and MCP consumers.

Pipeline position:  Scribe → CacheActor (parallel with Librarian)
Message source:     ScribeActor via _send_to_cache()
Consumers:          OrbStateManager (dimensional feed), API layer (events),
                    MCP (reads YAML file via read_shared_turn())

Responsibilities:
1. Receive cache_update messages from Scribe
2. Derive emotional tone from extraction content
3. Write the YAML cache file (atomic tmp+rename)
4. Feed the DimensionalEngine via OrbStateManager
5. Emit cache_updated events so the API layer can broadcast
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING
import logging
import math

import yaml

from .base import Actor, Message
from luna.extraction.types import (
    ExtractionOutput,
    ConversationMode,
    FlowSignal,
)
from luna.cache.shared_turn import shared_turn_cache_path

if TYPE_CHECKING:
    from luna.services.orb_state import OrbStateManager

logger = logging.getLogger(__name__)


# ── Tone mapping (moved from ScribeActor) ──────────────────────────────────

TONE_MAP = {
    "engaged": "curious_warm",
    "excited": "joyful_bright",
    "concerned": "thoughtful_dim",
    "focused": "active_steady",
    "resolute": "confident_warm",
    "correcting": "attentive_cool",
    "shifting": "transitional_neutral",
    "neutral": "idle_soft",
}

TYPE_TO_CATEGORY = {
    "FACT": "facts", "DECISION": "decisions", "ACTION": "actions",
    "PROBLEM": "problems", "OBSERVATION": "observations",
    "MILESTONE": "facts", "PREFERENCE": "facts", "RELATION": "facts",
    "MEMORY": "observations", "CORRECTION": "facts",
    "ASSUMPTION": "observations", "CONNECTION": "observations",
    "QUESTION": "observations", "OUTCOME": "observations",
}

# ── Tone-to-sentiment mapping (moved from server.py) ──────────────────────

TONE_TO_SENTIMENT = {
    "engaged": 0.6,
    "excited": 0.85,
    "concerned": -0.3,
    "focused": 0.4,
    "resolute": 0.5,
    "correcting": -0.1,
    "shifting": 0.2,
    "neutral": 0.3,
}


class CacheActor(Actor):
    """
    Cache Actor: owns the Shared Turn Cache and dimensional feed.

    Message Types:
        cache_update  — Write new cache snapshot + feed dimensional engine
        read_cache    — Return current snapshot data
        get_stats     — Return write/feed/error counts
    """

    def __init__(self):
        super().__init__("cache")

        self._cache_path = shared_turn_cache_path()
        self._orb_state_manager: Optional["OrbStateManager"] = None
        self._last_cache_data: Optional[dict] = None

        # Stats
        self._writes_count = 0
        self._dimensional_feeds = 0
        self._errors = 0

        logger.info("CacheActor initialized")

    @property
    def is_ready(self) -> bool:
        return self._running

    def set_orb_state_manager(self, osm: "OrbStateManager") -> None:
        """Wire the OrbStateManager reference (called during server init)."""
        self._orb_state_manager = osm
        logger.info("CacheActor: OrbStateManager wired")

    # =========================================================================
    # MESSAGE HANDLING
    # =========================================================================

    async def handle(self, msg: Message) -> None:
        match msg.type:
            case "cache_update":
                await self._handle_cache_update(msg)
            case _:
                logger.warning(f"CacheActor: Unknown message type: {msg.type}")

    # =========================================================================
    # CACHE UPDATE (primary path — called every extraction turn)
    # =========================================================================

    async def _handle_cache_update(self, msg: Message) -> None:
        """Handle cache_update from Scribe.

        Payload keys:
            extraction   — ExtractionOutput dict
            flow_signal  — FlowSignal dict
            source       — origin surface tag
            session_id   — session identifier
        """
        payload = msg.payload or {}

        extraction = ExtractionOutput.from_dict(payload.get("extraction", {}))
        flow_data = payload.get("flow_signal")
        flow_signal = FlowSignal.from_dict(flow_data) if flow_data else None
        source = payload.get("source", "unknown")
        session_id = payload.get("session_id", "")

        if flow_signal is None:
            logger.warning("CacheActor: cache_update without flow_signal, skipping")
            return

        # 1. Derive emotional tone
        emotional_tone = self._derive_emotional_tone(extraction, flow_signal)

        # 2. Build and write YAML cache
        cache_data = self._build_cache_data(
            extraction, flow_signal, emotional_tone, source, session_id,
        )
        self._write_cache_file(cache_data)

        # 3. Feed dimensional engine
        self._feed_dimensional_engine(emotional_tone, flow_signal, extraction)

        # 4. Notify engine (API layer can subscribe for WebSocket broadcast)
        await self.send_to_engine("cache_updated", {
            "emotional_tone": emotional_tone,
            "expression_hint": TONE_MAP.get(emotional_tone, "idle_soft"),
            "topic": flow_signal.current_topic,
            "total_extractions": len(extraction.objects),
        })

    # =========================================================================
    # EMOTIONAL TONE DERIVATION
    # =========================================================================

    def _derive_emotional_tone(
        self, extraction: ExtractionOutput, flow_signal: FlowSignal,
    ) -> str:
        if flow_signal.mode == ConversationMode.AMEND:
            return "correcting"
        if flow_signal.mode == ConversationMode.RECALIBRATION:
            return "shifting"
        types_present = {
            (obj.type.value if hasattr(obj.type, "value") else str(obj.type))
            for obj in extraction.objects
        }
        if "PROBLEM" in types_present:
            return "concerned"
        if "MILESTONE" in types_present:
            return "excited"
        if "DECISION" in types_present:
            return "resolute"
        if "ACTION" in types_present:
            return "focused"
        if flow_signal.continuity_score > 0.7:
            return "engaged"
        return "neutral"

    # =========================================================================
    # YAML CACHE WRITE (atomic tmp+rename)
    # =========================================================================

    def _build_cache_data(
        self,
        extraction: ExtractionOutput,
        flow_signal: FlowSignal,
        emotional_tone: str,
        source: str,
        session_id: str,
    ) -> dict:
        now = datetime.utcnow()

        categorized: dict[str, list] = {
            "facts": [], "decisions": [], "actions": [],
            "problems": [], "observations": [],
        }

        for obj in extraction.objects:
            obj_type = obj.type.value if hasattr(obj.type, "value") else str(obj.type)
            category = TYPE_TO_CATEGORY.get(obj_type, "observations")
            categorized[category].append({
                "content": obj.content,
                "confidence": round(obj.confidence, 2),
                "entities": obj.entities,
            })

        expression_hint = TONE_MAP.get(emotional_tone, "idle_soft")
        intensity = min(
            1.0,
            len(extraction.objects) * 0.15 + flow_signal.continuity_score * 0.3,
        )

        if extraction.objects:
            best = max(extraction.objects, key=lambda o: o.confidence)
            raw_summary = best.content[:200]
        elif flow_signal.current_topic:
            raw_summary = f"Continuing discussion: {flow_signal.current_topic}"
        else:
            raw_summary = "Light conversation, no high-signal extractions."

        return {
            "schema_version": 1,
            "turn_id": now.isoformat(),
            "timestamp": now.timestamp(),
            "source": source,
            "session_id": session_id,
            "scribed": categorized,
            "flow": {
                "mode": flow_signal.mode.value,
                "topic": flow_signal.current_topic,
                "continuity_score": round(flow_signal.continuity_score, 2),
                "open_threads": flow_signal.open_threads[:5],
            },
            "expression": {
                "emotional_tone": emotional_tone,
                "expression_hint": expression_hint,
                "intensity": round(intensity, 2),
            },
            "raw_summary": raw_summary,
            "ttl": 30,
        }

    def _write_cache_file(self, cache_data: dict) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._cache_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w") as f:
                yaml.dump(cache_data, f, default_flow_style=False, sort_keys=False)
            tmp_path.rename(self._cache_path)
            self._last_cache_data = cache_data
            self._writes_count += 1
            logger.debug(f"CacheActor: Shared turn cache updated (source={cache_data.get('source')})")
        except Exception as e:
            logger.error(f"CacheActor: Failed to write shared turn cache: {e}")
            self._errors += 1
            if tmp_path.exists():
                tmp_path.unlink()

    # =========================================================================
    # DIMENSIONAL ENGINE FEED
    # =========================================================================

    def _feed_dimensional_engine(
        self,
        emotional_tone: str,
        flow_signal: FlowSignal,
        extraction: ExtractionOutput,
    ) -> None:
        """Compute triggers and feed OrbStateManager.update_dimensions()."""
        if self._orb_state_manager is None:
            return

        sentiment = TONE_TO_SENTIMENT.get(emotional_tone, 0.5)
        flow_boost = flow_signal.continuity_score * 0.3

        topic_personal = 0.5
        n_extractions = len(extraction.objects)
        if n_extractions > 3:
            topic_personal = 0.7
        elif n_extractions > 0:
            topic_personal = 0.55

        # Identity confidence from engine
        identity_conf = 0.0
        if self.engine:
            identity_actor = self.engine.get_actor("identity")
            if identity_actor:
                identity_conf = getattr(identity_actor, "confidence", 0.7)

        # Turn count for flow ramp
        turn = 0
        if self.engine and hasattr(self.engine, "context") and hasattr(self.engine.context, "current_turn"):
            turn = self.engine.context.current_turn

        self._orb_state_manager.update_dimensions({
            "sentiment": sentiment,
            "memory_hit": 0.0,
            "identity": identity_conf,
            "topic_personal": topic_personal,
            "flow": min(1.0, turn * 0.15 + flow_boost),
            "time_mod": math.sin(datetime.now().hour * math.pi / 12) * 0.2,
        })
        self._dimensional_feeds += 1

    # =========================================================================
    # STATE SERIALIZATION
    # =========================================================================

    async def snapshot(self) -> dict:
        base = await super().snapshot()
        base.update({
            "writes": self._writes_count,
            "dimensional_feeds": self._dimensional_feeds,
            "errors": self._errors,
        })
        return base
