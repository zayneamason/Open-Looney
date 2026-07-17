"""
Luna Orb State Manager

Manages the orb's visual state based on:
1. Gesture markers in Luna's responses
2. System events (voice, memory, processing)
3. Priority resolution for conflicting states
"""

import re
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Set, Dict, Any
from datetime import datetime
import logging

from luna.services.orb_renderer import OrbRenderer
from luna.services.dimensional_engine import DimensionalEngine, DimensionalState
from luna.services.dim_renderer_map import map_dimensions_to_renderer

logger = logging.getLogger(__name__)


class OrbAnimation(Enum):
    IDLE = "idle"
    PULSE = "pulse"
    PULSE_FAST = "pulse_fast"
    SPIN = "spin"
    SPIN_FAST = "spin_fast"
    FLICKER = "flicker"
    WOBBLE = "wobble"
    DRIFT = "drift"
    ORBIT = "orbit"
    GLOW = "glow"
    SPLIT = "split"
    # System states
    PROCESSING = "processing"
    LISTENING = "listening"
    SPEAKING = "speaking"
    MEMORY_SEARCH = "memory_search"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class StatePriority(Enum):
    """Priority levels for state resolution (higher = takes precedence)

    P0 DEFAULT  — only when nothing else is active
    P1 DIMENSION — always active; continuous 5-axis heartbeat
    P1.5 EMOJI  — emoji detected in response text
    P2 GESTURE  — 12 surviving override gestures
    P3 SYSTEM   — processing, listening, speaking, memory_search
    P4 ERROR    — error, disconnected
    """
    DEFAULT = 0
    DIMENSION = 10      # continuous heartbeat
    EMOJI = 15          # lightweight emoji signal (P1.5)
    GESTURE = 20        # 12 surviving override gestures
    SYSTEM = 30         # processing, listening, speaking, memory_search
    ERROR = 40          # error, disconnected


@dataclass
class OrbState:
    animation: OrbAnimation = OrbAnimation.IDLE
    color: Optional[str] = None
    brightness: float = 1.0
    priority: StatePriority = StatePriority.DEFAULT
    source: str = "default"
    timestamp: datetime = field(default_factory=datetime.now)


# ── 12 Surviving Override Gestures ──
# These produce effects that dimensional blending cannot: discrete animation
# modes that punch through the continuous heartbeat. Everything else is now
# handled by the 5-axis dimensional engine.
GESTURE_PATTERNS: Dict[str, OrbState] = {
    r"\*splits?\b":        OrbState(OrbAnimation.SPLIT, priority=StatePriority.GESTURE, source="gesture"),
    r"\*searches?\b":      OrbState(OrbAnimation.ORBIT, "#06b6d4", priority=StatePriority.GESTURE, source="gesture"),
    r"\*dims?\b":          OrbState(OrbAnimation.FLICKER, brightness=0.6, priority=StatePriority.GESTURE, source="gesture"),
    r"\*gasps?\*":         OrbState(OrbAnimation.PULSE_FAST, brightness=1.4, priority=StatePriority.GESTURE, source="gesture"),
    r"\*startl(es?|ed)\*": OrbState(OrbAnimation.FLICKER, brightness=1.3, priority=StatePriority.GESTURE, source="gesture"),
    r"\*sparks?\*":        OrbState(OrbAnimation.FLICKER, brightness=1.4, priority=StatePriority.GESTURE, source="gesture"),
    r"\*lights? up\*":     OrbState(OrbAnimation.GLOW, brightness=1.45, priority=StatePriority.GESTURE, source="gesture"),
    r"\*spins?\b":         OrbState(OrbAnimation.SPIN, priority=StatePriority.GESTURE, source="gesture"),
    r"\*spins? fast\*":    OrbState(OrbAnimation.SPIN_FAST, priority=StatePriority.GESTURE, source="gesture"),
    r"\*wobbles?\*":       OrbState(OrbAnimation.WOBBLE, priority=StatePriority.GESTURE, source="gesture"),
    r"\*settles?\b":       OrbState(OrbAnimation.IDLE, brightness=0.9, priority=StatePriority.GESTURE, source="gesture"),
    r"\*flickers?\b":      OrbState(OrbAnimation.FLICKER, priority=StatePriority.GESTURE, source="gesture"),
}


# Emoji → OrbState mappings (fallback when no gesture marker present)
EMOJI_PATTERNS: Dict[str, OrbState] = {
    # Joy / Warmth
    "✨": OrbState(OrbAnimation.GLOW, brightness=1.3, priority=StatePriority.GESTURE, source="emoji"),
    "💜": OrbState(OrbAnimation.GLOW, "#A78BFA", brightness=1.2, priority=StatePriority.GESTURE, source="emoji"),
    "🫶": OrbState(OrbAnimation.PULSE, brightness=1.2, priority=StatePriority.GESTURE, source="emoji"),
    "😊": OrbState(OrbAnimation.GLOW, brightness=1.15, priority=StatePriority.GESTURE, source="emoji"),
    "🥰": OrbState(OrbAnimation.GLOW, "#FFB7B2", brightness=1.25, priority=StatePriority.GESTURE, source="emoji"),

    # Thinking / Curious
    "🤔": OrbState(OrbAnimation.ORBIT, brightness=0.9, priority=StatePriority.GESTURE, source="emoji"),
    "💭": OrbState(OrbAnimation.DRIFT, brightness=0.85, priority=StatePriority.GESTURE, source="emoji"),
    "🧐": OrbState(OrbAnimation.ORBIT, brightness=1.0, priority=StatePriority.GESTURE, source="emoji"),
    "👀": OrbState(OrbAnimation.PULSE, brightness=1.1, priority=StatePriority.GESTURE, source="emoji"),

    # Playful
    "😏": OrbState(OrbAnimation.SPIN, brightness=1.1, priority=StatePriority.GESTURE, source="emoji"),
    "😈": OrbState(OrbAnimation.SPIN_FAST, "#A78BFA", brightness=1.2, priority=StatePriority.GESTURE, source="emoji"),
    "🤭": OrbState(OrbAnimation.FLICKER, brightness=1.1, priority=StatePriority.GESTURE, source="emoji"),
    "🎉": OrbState(OrbAnimation.PULSE_FAST, brightness=1.35, priority=StatePriority.GESTURE, source="emoji"),
    "🎵": OrbState(OrbAnimation.SPIN, brightness=1.1, priority=StatePriority.GESTURE, source="emoji"),

    # Surprise
    "😮": OrbState(OrbAnimation.PULSE_FAST, brightness=1.3, priority=StatePriority.GESTURE, source="emoji"),
    "🤯": OrbState(OrbAnimation.FLICKER, brightness=1.45, priority=StatePriority.GESTURE, source="emoji"),

    # Concern / Empathy
    "😔": OrbState(OrbAnimation.DRIFT, brightness=0.75, priority=StatePriority.GESTURE, source="emoji"),
    "🥺": OrbState(OrbAnimation.WOBBLE, brightness=0.8, priority=StatePriority.GESTURE, source="emoji"),
    "😢": OrbState(OrbAnimation.DRIFT, brightness=0.7, priority=StatePriority.GESTURE, source="emoji"),
    "🤗": OrbState(OrbAnimation.GLOW, brightness=1.2, priority=StatePriority.GESTURE, source="emoji"),

    # Focus / Energy
    "⚡": OrbState(OrbAnimation.PULSE_FAST, "#F59E0B", brightness=1.3, priority=StatePriority.GESTURE, source="emoji"),
    "🔥": OrbState(OrbAnimation.PULSE_FAST, "#EF4444", brightness=1.35, priority=StatePriority.GESTURE, source="emoji"),
    "💡": OrbState(OrbAnimation.GLOW, "#FDE68A", brightness=1.4, priority=StatePriority.GESTURE, source="emoji"),
    "🎯": OrbState(OrbAnimation.IDLE, brightness=1.15, priority=StatePriority.GESTURE, source="emoji"),

    # Calm / Settling
    "🌙": OrbState(OrbAnimation.DRIFT, "#818CF8", brightness=0.8, priority=StatePriority.GESTURE, source="emoji"),
    "🍃": OrbState(OrbAnimation.DRIFT, "#10B981", brightness=0.85, priority=StatePriority.GESTURE, source="emoji"),
    "🫧": OrbState(OrbAnimation.DRIFT, "#06B6D4", brightness=0.9, priority=StatePriority.GESTURE, source="emoji"),
}

# Constants
EMOJI_SCAN_LIMIT = 500
SPEECH_HINT_ENABLED = True

# System events → OrbState mappings
SYSTEM_EVENTS: Dict[str, OrbState] = {
    "processing_query": OrbState(OrbAnimation.PROCESSING, priority=StatePriority.SYSTEM, source="system"),
    "voice_listening": OrbState(OrbAnimation.LISTENING, "#10b981", priority=StatePriority.SYSTEM, source="system"),
    "voice_speaking": OrbState(OrbAnimation.SPEAKING, priority=StatePriority.SYSTEM, source="system"),
    "memory_search": OrbState(OrbAnimation.MEMORY_SEARCH, "#06b6d4", priority=StatePriority.SYSTEM, source="system"),
    "memory_write": OrbState(OrbAnimation.ORBIT, "#06b6d4", brightness=1.2, priority=StatePriority.SYSTEM, source="system"),
    "delegation_start": OrbState(OrbAnimation.FLICKER, "#6b7280", priority=StatePriority.SYSTEM, source="system"),
    "delegation_end": OrbState(OrbAnimation.GLOW, brightness=1.1, priority=StatePriority.SYSTEM, source="system"),
    "error": OrbState(OrbAnimation.ERROR, "#ef4444", priority=StatePriority.ERROR, source="system"),
    "disconnected": OrbState(OrbAnimation.DISCONNECTED, "#6b7280", priority=StatePriority.ERROR, source="system"),
}


class ExpressionConfig:
    """Expression configuration loaded from personality.json."""

    def __init__(
        self,
        gesture_frequency: str = "moderate",
        gesture_display_mode: str = "visible",
        settings: Optional[Dict] = None
    ):
        self.gesture_frequency = gesture_frequency
        self.gesture_display_mode = gesture_display_mode
        self.settings = settings or {}

    @classmethod
    def from_dict(cls, data: dict) -> "ExpressionConfig":
        return cls(
            gesture_frequency=data.get("gesture_frequency", "moderate"),
            gesture_display_mode=data.get("gesture_display_mode", "visible"),
            settings=data.get("settings", {})
        )

    def should_strip_gestures(self) -> bool:
        """Check if gestures should be stripped from output."""
        display_modes = self.settings.get("display_modes", {})
        mode_config = display_modes.get(self.gesture_display_mode, {})
        return mode_config.get("strip_from_output", False)

    def should_annotate(self) -> bool:
        """Check if gestures should be annotated with debug info."""
        display_modes = self.settings.get("display_modes", {})
        mode_config = display_modes.get(self.gesture_display_mode, {})
        return mode_config.get("annotate", False)


class OrbStateManager:
    """
    Manages orb state with priority resolution and WebSocket broadcasting.
    """

    def __init__(self, expression_config: Optional[ExpressionConfig] = None):
        self.current_state = OrbState()
        self.expression_config = expression_config
        self.renderer = OrbRenderer()
        self._subscribers: Set[Callable] = set()
        # Sort patterns by length descending so specific patterns match before generic
        sorted_patterns = sorted(GESTURE_PATTERNS.items(), key=lambda x: len(x[0]), reverse=True)
        self._compiled_patterns = {
            re.compile(pattern, re.IGNORECASE): state
            for pattern, state in sorted_patterns
        }
        self._gesture_detected_this_response = False
        self._idle_task: Optional[asyncio.Task] = None
        self._speech_hint: Optional[Dict[str, Any]] = None
        # Dimensional heartbeat
        self._dimensional_engine = DimensionalEngine()
        self._dimensional_state = DimensionalState()
        self._dimension_overrides: Dict[str, float] = {}

    def subscribe(self, callback: Callable[[OrbState], None]) -> Callable:
        """Subscribe to state changes. Returns unsubscribe function."""
        self._subscribers.add(callback)
        return lambda: self._subscribers.discard(callback)

    def _broadcast(self, state: OrbState):
        """Broadcast state to all subscribers."""
        for callback in self._subscribers:
            try:
                callback(state)
            except Exception as e:
                logger.error(f"Subscriber error: {e}")

    def _should_update(self, new_state: OrbState) -> bool:
        """Check if new state should override current state."""
        return new_state.priority.value >= self.current_state.priority.value

    def set_state(self, state: OrbState):
        """Set orb state if priority allows."""
        if self._should_update(state):
            self.current_state = state
            self.renderer.reset()
            self.renderer.apply_state(state)
            self._broadcast(state)
            logger.debug(f"Orb state: {state.animation.value} (priority: {state.priority.value})")

    def emit_system_event(self, event_name: str):
        """Emit a system event."""
        if event_name in SYSTEM_EVENTS:
            self.set_state(SYSTEM_EVENTS[event_name])
        else:
            logger.warning(f"Unknown system event: {event_name}")

    def reset_to_idle(self):
        """Reset to idle state."""
        self.current_state = OrbState()
        self._gesture_detected_this_response = False
        self._broadcast(self.current_state)

    def update_dimensions(self, triggers: dict):
        """Feed conversation context signals into the dimensional engine.

        Called each turn with trigger signals. The dimensional heartbeat
        only applies if no higher-priority state (gesture/system/error)
        is currently active.

        Args:
            triggers: dict with keys sentiment, memory_hit, identity,
                      topic_personal, flow, time_mod
        """
        self._dimensional_state = self._dimensional_engine.blend(triggers, dt=1.0)

        # Only apply if no higher-priority state is active
        if self.current_state.priority.value <= StatePriority.DIMENSION.value:
            self.renderer.reset()
            map_dimensions_to_renderer(self._dimensional_state, self.renderer)
            self._broadcast_dimensional()

    def _broadcast_dimensional(self):
        """Broadcast dimensional state as the orb's current heartbeat."""
        state = OrbState(
            animation=OrbAnimation.IDLE,  # not really idle — dimensions drive params
            priority=StatePriority.DIMENSION,
            source="dimension",
        )
        self.current_state = state
        self._broadcast(state)

    def apply_dimension_override(self, overrides: dict):
        """Apply manual dimension overrides from the diagnostic tool.

        Args:
            overrides: dict with optional keys: valence, arousal, certainty,
                       engagement, warmth. Only provided keys are overridden.
                       Pass empty dict to clear all overrides.
        """
        if not overrides:
            self._dimension_overrides = {}
        else:
            self._dimension_overrides.update(overrides)

        # Apply overrides to current dimensional state
        ds = self._dimensional_state
        if 'valence' in self._dimension_overrides:
            ds.valence = max(-1.0, min(1.0, self._dimension_overrides['valence']))
        if 'arousal' in self._dimension_overrides:
            ds.arousal = max(0.0, min(1.0, self._dimension_overrides['arousal']))
        if 'certainty' in self._dimension_overrides:
            ds.certainty = max(0.0, min(1.0, self._dimension_overrides['certainty']))
        if 'engagement' in self._dimension_overrides:
            ds.engagement = max(0.0, min(1.0, self._dimension_overrides['engagement']))
        if 'warmth' in self._dimension_overrides:
            ds.warmth = max(0.0, min(1.0, self._dimension_overrides['warmth']))

        # Re-apply to renderer and broadcast
        if self.current_state.priority.value <= StatePriority.DIMENSION.value:
            self.renderer.reset()
            map_dimensions_to_renderer(ds, self.renderer)
            self._broadcast_dimensional()

    def process_text_chunk(self, text: str) -> str:
        """
        Process a text chunk for gestures, emoji, and speech hints.

        Returns the text (potentially modified based on display mode).
        """
        # 1. Gesture markers first (existing)
        if not self._gesture_detected_this_response:
            for pattern, state in self._compiled_patterns.items():
                if pattern.search(text):
                    self.set_state(state)
                    self._gesture_detected_this_response = True
                    break

        # 2. Emoji fallback — only scan first 500 chars
        if not self._gesture_detected_this_response:
            scan_text = text[:EMOJI_SCAN_LIMIT]
            for emoji, state in EMOJI_PATTERNS.items():
                if emoji in scan_text:
                    self.set_state(state)
                    self._gesture_detected_this_response = True
                    break

        # 3. Compute speech hint during speaking/processing state
        if SPEECH_HINT_ENABLED and self.current_state.animation in (
            OrbAnimation.SPEAKING, OrbAnimation.PROCESSING
        ):
            self._update_speech_hint(text)

        # 4. Handle display mode (existing — emoji NOT stripped)
        if self.expression_config:
            if self.expression_config.should_strip_gestures():
                text = self._strip_gestures(text)
            elif self.expression_config.should_annotate():
                text = self._annotate_gestures(text)

        return text

    def _update_speech_hint(self, text: str):
        """Compute speech rhythm hint from text content."""
        words = text.split()
        word_count = len(words)
        avg_word_len = sum(len(w) for w in words) / max(word_count, 1)

        # Short punchy text → fast tight pulses
        # Long flowing text → slow wide pulses
        if word_count <= 3 and avg_word_len <= 4:
            tempo = "staccato"
        elif avg_word_len > 7 or word_count > 20:
            tempo = "legato"
        else:
            tempo = "natural"

        self._speech_hint = {
            "tempo": tempo,
            "emphasis": '!' in text or text.isupper(),
            "question": '?' in text,
            "trailing": '...' in text or '\u2026' in text,
            "wordCount": word_count,
        }

    def _strip_gestures(self, text: str) -> str:
        """
        Remove gesture markers from text cleanly.

        P0 FIX: Properly cleans up whitespace when removing gestures.
        Before: "Hello *smiles warmly* world" -> "Hello  world" (double space)
        After:  "Hello *smiles warmly* world" -> "Hello world" (clean)

        See: Docs/HANDOFF_Luna_Voice_Restoration.md
        """
        # First pass: Remove gesture patterns with surrounding whitespace normalization
        # Handles: " *gesture* " -> " " (single space)
        text = re.sub(r'\s*\*[^*]+\*\s*', ' ', text)

        # Second pass: Clean up any double spaces
        text = re.sub(r'  +', ' ', text)

        # Third pass: Clean up leading/trailing spaces from lines
        text = '\n'.join(line.strip() for line in text.split('\n'))

        return text.strip()

    def _annotate_gestures(self, text: str) -> str:
        """Add debug annotations to gestures."""
        def annotate(match):
            gesture = match.group(0)
            for pattern, state in self._compiled_patterns.items():
                if pattern.search(gesture):
                    return f"{gesture} [ORB:{state.animation.value}]"
            return gesture

        return re.sub(r'\*[^*]+\*', annotate, text)

    def end_response(self):
        """Called when response streaming completes.

        Falls back to the dimensional heartbeat — NOT idle.
        The orb always has a pulse.
        """
        # Cancel any existing idle task
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()

        # Schedule return to dimensional heartbeat after delay
        async def delayed_return():
            await asyncio.sleep(2.0)
            # Fall back to dimensions, NOT idle
            self.renderer.reset()
            map_dimensions_to_renderer(self._dimensional_state, self.renderer)
            self._broadcast_dimensional()

        try:
            loop = asyncio.get_running_loop()
            self._idle_task = loop.create_task(delayed_return())
        except RuntimeError:
            # No running loop, skip delayed return
            pass

    def start_response(self):
        """Called when a new response starts streaming."""
        self._gesture_detected_this_response = False
        self._speech_hint = None
        self.emit_system_event("processing_query")

    def to_dict(self) -> dict:
        """Convert current state to dict for WebSocket transmission."""
        result = {
            "animation": self.current_state.animation.value,
            "color": self.current_state.color,
            "brightness": self.current_state.brightness,
            "source": self.current_state.source,
            "timestamp": self.current_state.timestamp.isoformat(),
        }
        if hasattr(self, 'renderer'):
            result["renderer"] = self.renderer.to_dict()
        if self._speech_hint:
            result["speechHint"] = self._speech_hint
        if self._dimensional_state:
            result["dimensions"] = self._dimensional_state.to_dict()
        return result
