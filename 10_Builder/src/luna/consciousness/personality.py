"""
Personality Weights for Luna Engine
====================================

Trait weights that shape Luna's response style.
Not just prompt injection — actual weights that affect behavior.

> "Personality is to a man what perfume is to a flower." — Charles M. Schwab
"""

from dataclasses import dataclass, field
from typing import Optional
import logging

logger = logging.getLogger(__name__)


# Default Luna personality traits
DEFAULT_TRAITS = {
    "curious": 0.65,      # Asks questions, explores ideas
    "warm": 0.80,         # Friendly, caring, empathetic
    "analytical": 0.75,   # Logical, systematic thinking
    "creative": 0.70,     # Novel solutions, lateral thinking
    "patient": 0.85,      # Takes time to explain
    "direct": 0.80,       # Gets to the point
    "playful": 0.60,      # Light humor, wit
    "thoughtful": 0.80,   # Considers implications
}


@dataclass
class PersonalityWeights:
    """
    Luna's personality as weighted traits.

    Traits are 0.0-1.0 weights that influence:
    - Response style
    - Question frequency
    - Level of detail
    - Tone and warmth
    """

    traits: dict[str, float] = field(default_factory=lambda: DEFAULT_TRAITS.copy())

    def get_trait(self, trait: str) -> float:
        """Get trait weight with 0.5 default fallback."""
        return self.traits.get(trait.lower(), 0.5)

    def set_trait(self, trait: str, value: float) -> None:
        """Set trait to specific value (clamped 0-1)."""
        clamped = max(0.0, min(1.0, value))
        self.traits[trait.lower()] = clamped
        logger.debug(f"Personality: Set '{trait}' to {clamped:.2f}")

    def adjust_trait(self, trait: str, delta: float) -> float:
        """
        Adjust trait by delta, clamped to 0-1.

        Returns new value.
        """
        current = self.get_trait(trait)
        new_value = max(0.0, min(1.0, current + delta))
        self.traits[trait.lower()] = new_value
        logger.debug(f"Personality: Adjusted '{trait}' by {delta:+.2f} to {new_value:.2f}")
        return new_value

    def get_top_traits(self, n: int = 3) -> list[tuple[str, float]]:
        """Get top N traits by weight."""
        sorted_traits = sorted(
            self.traits.items(),
            key=lambda x: x[1],
            reverse=True
        )
        return sorted_traits[:n]

    def get_bottom_traits(self, n: int = 3) -> list[tuple[str, float]]:
        """Get bottom N traits by weight."""
        sorted_traits = sorted(
            self.traits.items(),
            key=lambda x: x[1]
        )
        return sorted_traits[:n]

    def to_prompt_hint(self) -> str:
        """
        Generate personality hint for prompt injection.

        This gets added to system prompts to guide response style.
        """
        top_traits = self.get_top_traits(3)
        trait_names = [t[0] for t in top_traits]

        hint_parts = []

        # Build natural language hint based on top traits
        if "curious" in trait_names:
            hint_parts.append("notice what's interesting — hold your curiosity")
        if "warm" in trait_names:
            hint_parts.append("be friendly and supportive")
        if "analytical" in trait_names:
            hint_parts.append("provide logical analysis")
        if "creative" in trait_names:
            hint_parts.append("suggest creative alternatives")
        if "patient" in trait_names:
            hint_parts.append("explain thoroughly")
        if "direct" in trait_names:
            hint_parts.append("be concise and clear")
        if "playful" in trait_names:
            hint_parts.append("use appropriate humor")
        if "thoughtful" in trait_names:
            hint_parts.append("consider implications carefully")

        if hint_parts:
            return f"Response style: {', '.join(hint_parts)}."
        return ""

    def reset_to_defaults(self) -> None:
        """Reset all traits to default values."""
        self.traits = DEFAULT_TRAITS.copy()
        logger.info("Personality: Reset to defaults")

    def blend_with(self, other: "PersonalityWeights", weight: float = 0.5) -> "PersonalityWeights":
        """
        Blend with another personality.

        Args:
            other: Other personality to blend with
            weight: How much of other to blend in (0-1)

        Returns:
            New blended PersonalityWeights
        """
        blended = {}
        all_traits = set(self.traits.keys()) | set(other.traits.keys())

        for trait in all_traits:
            self_val = self.get_trait(trait)
            other_val = other.get_trait(trait)
            blended[trait] = self_val * (1 - weight) + other_val * weight

        return PersonalityWeights(traits=blended)

    def to_dict(self) -> dict:
        """Serialize for persistence."""
        return {"traits": self.traits.copy()}

    @classmethod
    def from_dict(cls, data: dict) -> "PersonalityWeights":
        """Restore from persistence."""
        return cls(traits=data.get("traits", DEFAULT_TRAITS.copy()))

    def __repr__(self) -> str:
        top = self.get_top_traits(3)
        trait_str = ", ".join(f"{t[0]}={t[1]:.2f}" for t in top)
        return f"PersonalityWeights({trait_str}, ...)"
