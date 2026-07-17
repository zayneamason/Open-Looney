"""
Dimensional Engine — 5-axis emotional heartbeat for the Luna Orb.

Computes continuous emotional state from conversation context signals.
Six input triggers blend into five emotional dimensions via weighted
averaging with momentum smoothing.

Dimensions:
    valence   (-1 → 1):  negative ← → positive
    arousal   ( 0 → 1):  calm ← → excited
    certainty ( 0 → 1):  uncertain ← → confident
    engagement( 0 → 1):  passive ← → deeply invested
    warmth    ( 0 → 1):  stranger ← → intimate
"""

from dataclasses import dataclass
import math
import logging

logger = logging.getLogger(__name__)


@dataclass
class DimensionalState:
    """Five-axis emotional state snapshot."""
    valence: float = 0.3        # -1 (negative) to 1 (positive)
    arousal: float = 0.35       #  0 (calm) to 1 (excited)
    certainty: float = 0.7      #  0 (uncertain) to 1 (confident)
    engagement: float = 0.5     #  0 (passive) to 1 (deeply invested)
    warmth: float = 0.6         #  0 (stranger) to 1 (intimate)

    def to_dict(self) -> dict:
        return {
            "valence": round(self.valence, 3),
            "arousal": round(self.arousal, 3),
            "certainty": round(self.certainty, 3),
            "engagement": round(self.engagement, 3),
            "warmth": round(self.warmth, 3),
        }


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class DimensionalEngine:
    """
    Blends six conversation triggers into a 5-axis emotional state
    with momentum smoothing so dimensions lerp toward targets.

    Triggers:
        sentiment      (-1→1):  emotional tone of the exchange
        memory_hit     (-1→1):  retrieval success/failure
        identity       ( 0→1):  FaceID recognition confidence
        topic_personal ( 0→1):  personal weight of the topic
        flow           ( 0→1):  conversation momentum (ramps with turns)
        time_mod    (-0.3→0.3): time-of-day arousal modifier
    """

    def __init__(self, smoothing: float = 0.3):
        self.state = DimensionalState()
        self.smoothing = smoothing

    def blend(self, triggers: dict, dt: float = 1.0) -> DimensionalState:
        """
        Blend triggers into dimensional state with momentum smoothing.

        Args:
            triggers: dict with keys sentiment, memory_hit, identity,
                      topic_personal, flow, time_mod
            dt: time delta (unused for now, reserved for frame-rate
                independent smoothing)

        Returns:
            Updated DimensionalState
        """
        sentiment = triggers.get("sentiment", 0.5)
        memory_hit = triggers.get("memory_hit", 0.0)
        identity = triggers.get("identity", 0.0)
        topic_personal = triggers.get("topic_personal", 0.5)
        flow = triggers.get("flow", 0.0)
        time_mod = triggers.get("time_mod", 0.0)

        # ── Compute raw targets from weighted trigger combination ──
        raw_valence = (
            sentiment * 0.7
            + memory_hit * 0.2
            + time_mod * 0.1
        )

        raw_arousal = (
            abs(sentiment) * 0.3
            + flow * 0.4
            + time_mod * 0.3
        )

        raw_certainty = (
            0.5
            + memory_hit * 0.4
            + topic_personal * 0.1
        )

        raw_engagement = (
            topic_personal * 0.4
            + flow * 0.3
            + identity * 0.3
        )

        raw_warmth = (
            identity * 0.6
            + topic_personal * 0.3
            + max(0, sentiment) * 0.1
        )

        # ── Smooth toward targets (momentum) ──
        s = self.smoothing
        self.state.valence += (raw_valence - self.state.valence) * s
        self.state.arousal += (raw_arousal - self.state.arousal) * s
        self.state.certainty += (raw_certainty - self.state.certainty) * s
        self.state.engagement += (raw_engagement - self.state.engagement) * s
        self.state.warmth += (raw_warmth - self.state.warmth) * s

        # ── Clamp to valid ranges ──
        self.state.valence = _clamp(self.state.valence, -1.0, 1.0)
        self.state.arousal = _clamp(self.state.arousal, 0.0, 1.0)
        self.state.certainty = _clamp(self.state.certainty, 0.0, 1.0)
        self.state.engagement = _clamp(self.state.engagement, 0.0, 1.0)
        self.state.warmth = _clamp(self.state.warmth, 0.0, 1.0)

        logger.debug(
            f"Dimensional blend: v={self.state.valence:.2f} a={self.state.arousal:.2f} "
            f"c={self.state.certainty:.2f} e={self.state.engagement:.2f} w={self.state.warmth:.2f}"
        )

        return self.state

    def reset(self):
        """Reset to default dimensional state."""
        self.state = DimensionalState()

    @staticmethod
    def time_modifier() -> float:
        """Compute time-of-day arousal modifier from system clock.

        Returns a value in [-0.2, 0.2] — lower energy at night.
        Uses sin curve peaking around noon (hour 12).
        """
        from datetime import datetime
        hour = datetime.now().hour
        return math.sin(hour * math.pi / 12) * 0.2

    @staticmethod
    def flow_from_turns(turn_count: int) -> float:
        """Compute flow signal from conversation turn count.

        Ramps linearly, capping at 1.0 around turn 7.
        """
        return min(1.0, turn_count * 0.15)
