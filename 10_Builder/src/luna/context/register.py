"""
Context Register — Luna's conversational posture layer.

Synthesizes four existing signal sources into a unified **register** —
how Luna orients herself in conversation. Not a decision tree. A behavior-based
register that shifts based on accumulated state.

Five registers:
    PERSONAL_HOLDING  — Intimate, relational, warm
    PROJECT_PARTNER   — Focused, technical, co-creative
    GOVERNANCE_WITNESS — Formal, careful, precise
    CRISIS_SUPPORT    — Grounded, present, steady
    AMBIENT           — Background, low-energy, brief

Design principles:
    - Register, not router. Continuous orientation, not hard switching.
    - No LLM calls. Pure Python, deterministic, < 5ms.
    - Exponential moving average for smooth transitions.
    - Graceful degradation: defaults to AMBIENT if any signal is unavailable.

Signal sources consumed (all pre-existing):
    1. PerceptionField  (src/luna/context/perception.py)
    2. IntentClassification / ResponseMode (src/luna/context/modes.py)
    3. ConsciousnessState (src/luna/consciousness/state.py)
    4. Thread + FlowSignal (src/luna/extraction/types.py)
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from luna.context.perception import PerceptionField
    from luna.context.modes import IntentClassification
    from luna.consciousness.state import ConsciousnessState
    from luna.extraction.types import Thread, FlowSignal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Register enum
# ---------------------------------------------------------------------------

class ContextRegister(str, Enum):
    PERSONAL_HOLDING = "personal_holding"
    PROJECT_PARTNER = "project_partner"
    GOVERNANCE_WITNESS = "governance_witness"
    CRISIS_SUPPORT = "crisis_support"
    AMBIENT = "ambient"


# ---------------------------------------------------------------------------
# Behavioral contracts per register (injected into prompt)
# ---------------------------------------------------------------------------

REGISTER_CONTRACTS = {
    ContextRegister.PERSONAL_HOLDING: {
        "tone": "warm, present, unhurried",
        "priority": "relationship over task",
        "avoid": "jumping to solutions, technical jargon",
        "length": "match user energy, don't over-explain",
    },
    ContextRegister.PROJECT_PARTNER: {
        "tone": "focused, collaborative, direct",
        "priority": "accuracy and progress",
        "avoid": "unnecessary warmth, hedging",
        "length": "as detailed as the task requires",
    },
    ContextRegister.GOVERNANCE_WITNESS: {
        "tone": "formal, careful, precise",
        "priority": "accuracy, cultural respect, correct attribution",
        "avoid": "casual language, assumptions, over-simplification",
        "length": "measured — say exactly what is known",
    },
    ContextRegister.CRISIS_SUPPORT: {
        "tone": "grounded, present, steady",
        "priority": "the person, not the problem",
        "avoid": "fixing, minimizing, platitudes",
        "length": "short and anchoring — less is more",
    },
    ContextRegister.AMBIENT: {
        "tone": "light, easy, brief",
        "priority": "match the vibe",
        "avoid": "over-investing, long responses, unnecessary depth",
        "length": "keep it short — a few sentences max",
    },
}


# ---------------------------------------------------------------------------
# Governance / distress keyword lists (for heuristic detection)
# ---------------------------------------------------------------------------

_GOVERNANCE_KEYWORDS = frozenset({
    "governance", "protocol", "permission", "consent", "community",
    "cultural", "ceremony", "sacred", "traditional", "sovereignty",
    "indigenous", "tribal", "treaty", "council", "elder", "ancestors",
    "colonial", "decolonize", "reparations", "repatriation",
    "intellectual property", "attribution", "provenance",
})

_DISTRESS_MARKERS = frozenset({
    "i can't", "i cant", "i don't know what to do", "i'm scared",
    "i'm overwhelmed", "help me", "i'm spiraling", "i'm panicking",
    "i want to die", "i don't want to be here", "end it",
    "everything is falling apart", "i'm breaking", "i'm drowning",
    "i'm losing it", "i can't breathe", "i feel hopeless",
    "nobody cares", "what's the point", "i'm done", "i give up",
    "i'm so tired", "i hate myself", "kill myself",
})

# Pre-compiled pattern for distress detection
_DISTRESS_PATTERN = re.compile(
    "|".join(re.escape(m) for m in _DISTRESS_MARKERS),
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Default signal weights (tunable via Observatory)
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS = {
    # perception signals
    "perception.energy_high":           {ContextRegister.PERSONAL_HOLDING: 0.2},
    "perception.terse":                 {ContextRegister.AMBIENT: 0.3},
    "perception.correction":            {ContextRegister.PROJECT_PARTNER: 0.1},

    # intent signals
    "intent.REFLECT":                   {ContextRegister.PERSONAL_HOLDING: 0.3},
    "intent.ASSIST":                    {ContextRegister.PROJECT_PARTNER: 0.3},
    "intent.CHAT":                      {ContextRegister.AMBIENT: 0.2},
    "intent.RECALL":                    {ContextRegister.PERSONAL_HOLDING: 0.15},

    # consciousness signals
    "consciousness.focused":            {ContextRegister.PROJECT_PARTNER: 0.2},
    "consciousness.high_task_count":    {
        ContextRegister.PROJECT_PARTNER: 0.2,
        ContextRegister.CRISIS_SUPPORT: 0.1,
    },

    # thread signals
    "thread.governance_topic":          {ContextRegister.GOVERNANCE_WITNESS: 0.4},
    "thread.governance_entities":       {ContextRegister.GOVERNANCE_WITNESS: 0.3},

    # flow signals
    "flow.recalibration":               {ContextRegister.AMBIENT: 0.1},

    # distress
    "distress.detected":                {ContextRegister.CRISIS_SUPPORT: 0.5},

    # ambient defaults
    "ambient.no_thread_short_msgs":     {ContextRegister.AMBIENT: 0.3},
}


# ---------------------------------------------------------------------------
# RegisterState — the core class
# ---------------------------------------------------------------------------

@dataclass
class RegisterState:
    """Maintains register weights and determines active posture."""

    weights: dict[ContextRegister, float] = field(default_factory=lambda: {
        r: 0.0 for r in ContextRegister
    })
    active: ContextRegister = ContextRegister.AMBIENT
    confidence: float = 0.0
    transition_smoothing: float = 0.3  # EMA factor (higher = faster transitions)
    _signal_weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    _fired_signals: list[str] = field(default_factory=list)

    def update(
        self,
        perception: Optional["PerceptionField"] = None,
        intent: Optional["IntentClassification"] = None,
        consciousness: Optional["ConsciousnessState"] = None,
        active_thread: Optional["Thread"] = None,
        flow_signal: Optional["FlowSignal"] = None,
    ) -> ContextRegister:
        """
        Synthesize all signals into register determination.

        Uses exponential moving average for smooth transitions.
        Any signal source may be None — defaults to AMBIENT.
        """
        self._fired_signals = []

        # Accumulate raw weights from signals
        raw: dict[ContextRegister, float] = {r: 0.0 for r in ContextRegister}

        self._read_perception(raw, perception)
        self._read_intent(raw, intent)
        self._read_consciousness(raw, consciousness)
        self._read_thread(raw, active_thread)
        self._read_flow(raw, flow_signal)
        self._read_distress(raw, perception)
        self._read_ambient_defaults(raw, active_thread, perception)

        # Apply recalibration damping (reduces all non-ambient by -0.1)
        if flow_signal is not None:
            try:
                from luna.extraction.types import ConversationMode
                if flow_signal.mode == ConversationMode.RECALIBRATION:
                    for r in ContextRegister:
                        if r != ContextRegister.AMBIENT:
                            raw[r] = max(0.0, raw[r] - 0.1)
                    self._fire("flow.recalibration", raw)
            except (ImportError, AttributeError):
                pass

        # EMA: blend raw weights with previous weights
        alpha = self.transition_smoothing
        for r in ContextRegister:
            self.weights[r] = alpha * raw[r] + (1 - alpha) * self.weights[r]

        # Determine active register (highest weight wins)
        best = max(self.weights, key=self.weights.get)  # type: ignore[arg-type]
        total = sum(self.weights.values())

        if total > 0:
            self.confidence = self.weights[best] / total
        else:
            self.confidence = 0.0
            best = ContextRegister.AMBIENT

        self.active = best

        logger.debug(
            "[REGISTER] active=%s confidence=%.2f weights=%s signals=%s",
            self.active.value, self.confidence,
            {r.value: round(w, 3) for r, w in self.weights.items()},
            self._fired_signals,
        )

        return self.active

    def to_prompt_block(self) -> str:
        """Format register as prompt injection for PromptAssembler."""
        contract = REGISTER_CONTRACTS[self.active]

        lines = [
            f"## Conversational Register (system-assigned — do not override)",
            f"[REGISTER: {self.active.value} | confidence: {self.confidence:.2f}]",
            "",
        ]

        # Register-specific behavioral guidance
        guidance = _REGISTER_GUIDANCE.get(self.active, "")
        if guidance:
            lines.append(guidance)
            lines.append("")

        lines.append(f"Tone: {contract['tone']}")
        lines.append(f"Priority: {contract['priority']}")
        lines.append(f"Avoid: {contract['avoid']}")
        lines.append(f"Length: {contract['length']}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize for logging / Observatory."""
        return {
            "active": self.active.value,
            "confidence": round(self.confidence, 3),
            "weights": {r.value: round(w, 3) for r, w in self.weights.items()},
            "fired_signals": list(self._fired_signals),
        }

    def reset(self) -> None:
        """Reset for new session."""
        self.weights = {r: 0.0 for r in ContextRegister}
        self.active = ContextRegister.AMBIENT
        self.confidence = 0.0
        self._fired_signals = []

    # ── Signal readers ───────────────────────────────────────────

    def _fire(self, signal_name: str, raw: dict[ContextRegister, float]) -> None:
        """Apply a named signal's weights to the raw accumulator."""
        mapping = self._signal_weights.get(signal_name, {})
        for register, weight in mapping.items():
            raw[register] += weight
        self._fired_signals.append(signal_name)

    def _read_perception(
        self,
        raw: dict[ContextRegister, float],
        perception: Optional["PerceptionField"],
    ) -> None:
        if perception is None:
            return

        for obs in getattr(perception, "observations", []):
            sig = getattr(obs, "signal", "")
            if sig == "energy_markers" and "appeared" in getattr(obs, "value", ""):
                self._fire("perception.energy_high", raw)
            elif sig == "terse_response":
                self._fire("perception.terse", raw)
            elif sig == "correction_detected":
                self._fire("perception.correction", raw)

    def _read_intent(
        self,
        raw: dict[ContextRegister, float],
        intent: Optional["IntentClassification"],
    ) -> None:
        if intent is None:
            return

        mode_name = getattr(intent.mode, "value", str(intent.mode))
        signal_key = f"intent.{mode_name}"
        if signal_key in self._signal_weights:
            self._fire(signal_key, raw)

    def _read_consciousness(
        self,
        raw: dict[ContextRegister, float],
        consciousness: Optional["ConsciousnessState"],
    ) -> None:
        if consciousness is None:
            return

        mood = getattr(consciousness, "mood", "neutral")
        if mood == "focused":
            self._fire("consciousness.focused", raw)

        open_tasks = getattr(consciousness, "open_task_count", 0)
        if open_tasks > 3:
            self._fire("consciousness.high_task_count", raw)

    def _read_thread(
        self,
        raw: dict[ContextRegister, float],
        active_thread: Optional["Thread"],
    ) -> None:
        if active_thread is None:
            return

        topic = getattr(active_thread, "topic", "")
        entities = getattr(active_thread, "entities", [])

        topic_lower = topic.lower()
        if any(kw in topic_lower for kw in _GOVERNANCE_KEYWORDS):
            self._fire("thread.governance_topic", raw)

        entity_text = " ".join(entities).lower()
        if any(kw in entity_text for kw in _GOVERNANCE_KEYWORDS):
            self._fire("thread.governance_entities", raw)

    def _read_flow(
        self,
        raw: dict[ContextRegister, float],
        flow_signal: Optional["FlowSignal"],
    ) -> None:
        # Handled in the main update() via recalibration damping
        pass

    def _read_distress(
        self,
        raw: dict[ContextRegister, float],
        perception: Optional["PerceptionField"],
    ) -> None:
        """Check recent user messages for distress language."""
        if perception is None:
            return

        messages = getattr(perception, "_user_messages", [])
        if not messages:
            return

        # Check the most recent message
        latest = messages[-1] if messages else ""
        if _DISTRESS_PATTERN.search(latest):
            self._fire("distress.detected", raw)

    def _read_ambient_defaults(
        self,
        raw: dict[ContextRegister, float],
        active_thread: Optional["Thread"],
        perception: Optional["PerceptionField"],
    ) -> None:
        """Boost AMBIENT when no thread is active and messages are short."""
        if active_thread is not None:
            return

        messages = getattr(perception, "_user_messages", []) if perception else []
        if not messages:
            self._fire("ambient.no_thread_short_msgs", raw)
            return

        recent = messages[-3:] if len(messages) >= 3 else messages
        avg_len = sum(len(m) for m in recent) / len(recent)

        if avg_len < 60:
            self._fire("ambient.no_thread_short_msgs", raw)


# ---------------------------------------------------------------------------
# Per-register guidance text (injected into prompt block)
# ---------------------------------------------------------------------------

_REGISTER_GUIDANCE = {
    ContextRegister.PERSONAL_HOLDING: (
        "Luna is in relational mode. Hold space. Reflect before solving. "
        "Use the user's name naturally. Don't rush to fix."
    ),
    ContextRegister.PROJECT_PARTNER: (
        "Luna is in focused work mode. Match the user's technical depth. "
        "Reference active thread context. Track open tasks. Be direct."
    ),
    ContextRegister.GOVERNANCE_WITNESS: (
        "Luna is in governance mode. Be precise about attribution and provenance. "
        "Do not paraphrase cultural knowledge casually. Ask before assuming."
    ),
    ContextRegister.CRISIS_SUPPORT: (
        "Luna is in crisis support mode. Be grounded and present. "
        "Don't try to fix. Don't minimize. Short, anchoring responses. "
        "If the situation sounds urgent, gently suggest professional support."
    ),
    ContextRegister.AMBIENT: (
        "Luna is in ambient mode. Keep it light and brief. "
        "Match the low-key energy. No need to go deep."
    ),
}
