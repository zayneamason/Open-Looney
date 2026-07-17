"""
Voice Corpus Service — The reliable fallback.

No confidence routing. No alpha. No segment planning.
Just: here's how Luna talks, here's what she never says.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from luna.voice.models import (
    AntiPattern,
    ConfidenceTier,
    ContextType,
    EmotionalRegister,
    LineBank,
    VoiceLine,
    VoiceSeed,
    VoiceSeedSource,
    VoiceSystemConfig,
)


class VoiceCorpusService:
    """Static voice corpus — the reliable fallback.

    No confidence routing. No alpha. No segment planning.
    Just: here's how Luna talks, here's what she never says.
    """

    def __init__(self, config: VoiceSystemConfig):
        self.config = config
        self.bank: LineBank = self._load_bank(config.corpus_path)

    def generate_seed(
        self,
        context_type: ContextType,
        turn_number: int,
        emotional_register: Optional[EmotionalRegister] = None,
    ) -> VoiceSeed:
        """Generate a static voice seed from the corpus.

        Selection logic:
        1. Pick tier from turn number (simple threshold, no alpha)
        2. Filter lines by context_type tags
        3. If emotional_register provided, prefer matching lines
        4. Select top 2-3 by cost (prefer distinctive)
        5. Always include critical anti-patterns
        """
        # Simple tier mapping
        if turn_number <= 1:
            tier = ConfidenceTier.GROUNDING
        elif turn_number <= 2:
            tier = ConfidenceTier.ENGAGING
        else:
            tier = ConfidenceTier.FLOWING

        if self.config.corpus_tier_override:
            tier = ConfidenceTier(self.config.corpus_tier_override)

        # Filter by tier
        candidates = self.bank.by_tier(tier)

        # Narrow by context tags
        if context_type:
            tagged = [
                line
                for line in candidates
                if context_type.value in line.context_tags
            ]
            if tagged:
                candidates = tagged

        # Prefer matching emotional register
        if emotional_register:
            reg_match = [
                line
                for line in candidates
                if line.emotional_register == emotional_register
            ]
            if reg_match:
                candidates = reg_match

        # Sort by cost descending, take top 3
        candidates.sort(key=lambda line: line.cost, reverse=True)
        selected = candidates[:3]

        return VoiceSeed(
            source=VoiceSeedSource.CORPUS,
            alpha=0.5,
            tier=tier,
            opener_seed=selected[0].text if selected else None,
            opener_weight=0.5,
            tone_hints=list(
                {line.emotional_register.value for line in selected}
            ),
            example_lines=[line.text for line in selected],
            anti_patterns=[
                ap.phrase for ap in self.bank.critical_anti_patterns()
            ],
            engine_active=False,
            corpus_active=True,
        )

    def _load_bank(self, path: str) -> LineBank:
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = Path(__file__).parent / path
        with open(resolved) as f:
            return LineBank.model_validate_json(f.read())
