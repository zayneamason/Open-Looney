"""LunaScript trait measurement engine — weighted feature combinations.

Combines 21 linguistic features into 8 trait scores using calibrated weights.
Zero LLM calls. All sigmoid-normalized arithmetic.
"""

import math
import re
from dataclasses import dataclass, field

from .features import ALL_FEATURES


@dataclass
class FeatureVector:
    features: dict[str, float]
    text_length: int
    sentence_count: int


@dataclass
class TraitScore:
    value: float                              # 0.0-1.0 (sigmoid normalized)
    raw_score: float                          # unnormalized weighted sum
    feature_contributions: dict[str, float]   # what drove this score


@dataclass
class SignatureMeasurement:
    traits: dict[str, TraitScore]
    features: FeatureVector


# Calibrated weights from cross-validation against Luna's 1268-response corpus.
# Format: trait -> list of (feature_name, weight).
# Positive weight = higher feature value pushes trait higher.
TRAIT_FEATURE_MAP: dict[str, list[tuple[str, float]]] = {
    "curiosity": [
        ("question_density", 5.44),
        ("exploratory_ratio", 4.56),
        ("closing_question", 3.20),
        ("tangent_markers", 1.91),
        ("list_usage", -1.67),
    ],
    "depth": [
        ("avg_word_length", 1.72),
        ("sentence_length_variance", 1.24),
        ("list_usage", -1.11),
        ("conditional_ratio", 1.00),
        ("avg_sentence_length", 0.92),
        ("question_density", 0.91),
    ],
    "directness": [
        ("hedge_ratio", -2.68),
        ("conditional_ratio", -2.49),
        ("filler_ratio", -2.08),
        ("first_person_ratio", 1.51),
        ("passive_ratio", -1.32),
        ("avg_sentence_length", -1.23),
        ("contraction_rate", 0.96),
    ],
    "energy": [
        ("opening_reaction", 2.73),
        ("emphasis_density", 2.00),
        ("slang_ratio", 1.63),
        ("sentence_length_variance", 1.24),
        ("emoji_density", 1.23),
        ("avg_sentence_length", -0.61),
    ],
    "formality": [
        ("avg_word_length", 3.44),
        ("slang_ratio", -3.26),
        ("contraction_rate", -2.89),
        ("formal_vocab_ratio", 1.85),
        ("passive_ratio", 1.32),
        ("avg_sentence_length", 0.92),
    ],
    "humor": [
        ("slang_ratio", 2.17),
        ("formal_vocab_ratio", -1.23),
        ("emoji_density", 1.23),
        ("emphasis_density", 1.20),
        ("tangent_markers", 0.96),
        ("avg_sentence_length", -0.61),
    ],
    "patience": [
        ("question_density", 2.72),
        ("you_ratio", 1.31),
        ("emphasis_density", -0.80),
        ("avg_sentence_length", 0.61),
        ("hedge_ratio", 0.45),
    ],
    "warmth": [
        ("question_density", 2.72),
        ("we_ratio", 2.58),
        ("you_ratio", 1.97),
        ("contraction_rate", 1.93),
        ("hedge_ratio", -1.34),
        ("formal_vocab_ratio", -1.23),
        ("emphasis_density", 0.80),
        ("passive_ratio", -0.66),
    ],
}

# Map LunaScript trait names -> PersonalityWeights trait names
TRAIT_TO_PERSONALITY = {
    "warmth": "warm",
    "directness": "direct",
    "curiosity": "curious",
    "humor": "playful",
    "formality": "thoughtful",
    "energy": "creative",
    "depth": "analytical",
    "patience": "patient",
}


def _sigmoid(x: float) -> float:
    """Standard sigmoid, clamped to avoid overflow."""
    x = max(-20.0, min(20.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def extract_features(text: str) -> FeatureVector:
    """Extract all 21 features from text."""
    words = re.findall(r"\b\w+(?:['']\w+)?\b", text)
    from .features import _split_sentences
    sentences = _split_sentences(text)

    features = {}
    for name, func in ALL_FEATURES.items():
        features[name] = func(text, words)

    return FeatureVector(
        features=features,
        text_length=len(text),
        sentence_count=len(sentences),
    )


def measure_trait(
    features: FeatureVector,
    trait_name: str,
    weights: list[tuple[str, float]],
    baselines: dict,
) -> TraitScore:
    """Measure a single trait from features using calibrated weights.

    Features are z-scored against baselines before weighting.
    """
    raw_score = 0.0
    contributions = {}

    for feat_name, weight in weights:
        feat_val = features.features.get(feat_name, 0.0)

        # Z-score against baseline if available
        bl = baselines.get(feat_name)
        if bl and hasattr(bl, "stddev") and bl.stddev > 0:
            z = (feat_val - bl.mean) / bl.stddev
        else:
            z = feat_val

        contribution = weight * z
        contributions[feat_name] = contribution
        raw_score += contribution

    return TraitScore(
        value=_sigmoid(raw_score),
        raw_score=raw_score,
        feature_contributions=contributions,
    )


def measure_signature(text: str, baselines: dict) -> SignatureMeasurement:
    """Full signature measurement: extract features, measure all 8 traits."""
    fv = extract_features(text)
    traits = {}

    for trait_name, weights in TRAIT_FEATURE_MAP.items():
        traits[trait_name] = measure_trait(fv, trait_name, weights, baselines)

    return SignatureMeasurement(traits=traits, features=fv)
