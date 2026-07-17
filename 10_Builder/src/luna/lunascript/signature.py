"""LunaScript signature — sign, compare, classify delegation round-trips.

Signs outbound delegation with Luna's cognitive snapshot.
Signs return with the delegated response's measured traits.
Compares via weighted Euclidean distance.
Classifies: RESONANCE | DRIFT | EXPANSION | COMPRESSION.
"""

import math
import time
from dataclasses import dataclass, field

from .measurement import SignatureMeasurement, TRAIT_FEATURE_MAP


@dataclass
class DelegationSignature:
    trait_vector: dict[str, float]    # measured trait values at this moment
    glyph_string: str                 # derived symbolic compression
    mode: str                         # from ConsciousnessState.mood
    active_entities: list[str]        # from entity detection
    version: int                      # incrementing counter
    timestamp: float = field(default_factory=time.time)


@dataclass
class DeltaResult:
    delta_vector: dict[str, float]    # per-trait deltas (return - outbound)
    drift_score: float                # weighted Euclidean distance (0-1)
    classification: str = ""          # filled by classify_delta


# Default trait weights — how much each trait matters for drift detection.
# Higher = more sensitive to drift in that trait.
DEFAULT_TRAIT_WEIGHTS: dict[str, float] = {
    "warmth": 3.0,
    "curiosity": 2.5,
    "directness": 2.0,
    "energy": 1.5,
    "formality": 2.0,
    "humor": 1.0,
    "depth": 1.0,
    "patience": 1.5,
}


def sign_outbound(
    consciousness,
    personality,
    entities: list[str],
    measurement: SignatureMeasurement,
    glyph: str = "○",
    version: int = 0,
) -> DelegationSignature:
    """Snapshot Luna's cognitive state before delegation."""
    trait_vector = {}
    for trait_name, score in measurement.traits.items():
        trait_vector[trait_name] = score.value

    mood = "neutral"
    if consciousness and hasattr(consciousness, "mood"):
        mood = consciousness.mood or "neutral"

    return DelegationSignature(
        trait_vector=trait_vector,
        glyph_string=glyph,
        mode=mood,
        active_entities=list(entities) if entities else [],
        version=version,
    )


def sign_return(
    consciousness,
    personality,
    entities: list[str],
    measurement: SignatureMeasurement,
    glyph: str = "○",
    version: int = 0,
) -> DelegationSignature:
    """Measure the delegated response and create its signature."""
    trait_vector = {}
    for trait_name, score in measurement.traits.items():
        trait_vector[trait_name] = score.value

    mood = "neutral"
    if consciousness and hasattr(consciousness, "mood"):
        mood = consciousness.mood or "neutral"

    return DelegationSignature(
        trait_vector=trait_vector,
        glyph_string=glyph,
        mode=mood,
        active_entities=list(entities) if entities else [],
        version=version,
    )


def compare_signatures(
    outbound: DelegationSignature,
    returned: DelegationSignature,
    trait_weights: dict[str, float] = None,
) -> DeltaResult:
    """Compare outbound and return signatures via weighted Euclidean distance."""
    weights = trait_weights or DEFAULT_TRAIT_WEIGHTS

    delta_vector = {}
    weighted_sq_sum = 0.0
    max_possible_sq = 0.0

    for trait in outbound.trait_vector:
        out_val = outbound.trait_vector.get(trait, 0.5)
        ret_val = returned.trait_vector.get(trait, 0.5)
        delta = ret_val - out_val
        delta_vector[trait] = delta

        w = weights.get(trait, 1.0)
        weighted_sq_sum += w * delta ** 2
        max_possible_sq += w  # max delta is 1.0, so max sq = w * 1.0

    if max_possible_sq > 0:
        drift_score = math.sqrt(weighted_sq_sum) / math.sqrt(max_possible_sq)
    else:
        drift_score = 0.0

    return DeltaResult(
        delta_vector=delta_vector,
        drift_score=drift_score,
    )


def classify_delta(
    delta: DeltaResult,
    baseline_mean: float = 0.15,
    baseline_stddev: float = 0.08,
) -> str:
    """Classify the drift into RESONANCE | DRIFT | EXPANSION | COMPRESSION.

    Adaptive thresholds based on baseline drift statistics.
    """
    drift = delta.drift_score
    sigma = baseline_stddev if baseline_stddev > 0.01 else 0.08

    # Low drift = voice preserved
    if drift < baseline_mean + 0.5 * sigma:
        classification = "RESONANCE"
    # Severe drift = fidelity loss
    elif drift > baseline_mean + 2.0 * sigma:
        classification = "COMPRESSION"
    else:
        # Check direction: did traits mostly increase or decrease?
        increases = sum(1 for d in delta.delta_vector.values() if d > 0.05)
        decreases = sum(1 for d in delta.delta_vector.values() if d < -0.05)

        if increases > decreases:
            classification = "EXPANSION"
        else:
            classification = "DRIFT"

    delta.classification = classification
    return classification


def derive_glyph(state: dict) -> str:
    """Deterministic projection from full state to glyph string.

    Encodes position + dominant traits into a compact symbolic form.
    """
    position = state.get("position", "EXPLORING")
    traits = state.get("trait_vector", {})

    # Base glyph from position
    base_glyphs = {
        "OPENING": "◇", "EXPLORING": "◈", "BUILDING": "▣",
        "DEEPENING": "◉", "PIVOTING": "⟳", "CLOSING": "○",
    }
    glyph = base_glyphs.get(position, "○")

    # Add trait modifiers
    warmth = traits.get("warmth", 0.5)
    energy = traits.get("energy", 0.5)
    curiosity = traits.get("curiosity", 0.5)

    if warmth > 0.75:
        glyph += "♡"
    if energy > 0.75:
        glyph += "⚡"
    if curiosity > 0.75:
        glyph += "?"

    return glyph
