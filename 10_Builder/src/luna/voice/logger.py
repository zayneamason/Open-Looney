"""
Voice System Logger — Structured observability for voice system.

Logs alpha computation, line selection, shadow diffs, and injection blocks.
"""

from __future__ import annotations

import logging
from typing import Optional

from luna.voice.models import (
    ConfidenceSignals,
    VoiceSeed,
    VoiceSystemConfig,
)

logger = logging.getLogger("luna.voice")


class VoiceSystemLogger:
    """Structured logger for voice system observability."""

    def __init__(self, config: VoiceSystemConfig):
        self.config = config

    def log_generation(
        self,
        signals: ConfidenceSignals,
        engine_seed: Optional[VoiceSeed],
        corpus_seed: Optional[VoiceSeed],
        final: VoiceSeed,
    ) -> None:
        """Log a complete generation cycle."""
        if self.config.log_alpha and engine_seed:
            logger.info(
                "voice.alpha",
                extra={
                    "alpha": engine_seed.alpha,
                    "tier": engine_seed.tier.value,
                    "turn": signals.turn_number,
                    "memory_score": signals.memory_retrieval_score,
                    "context_type": signals.context_type.value,
                },
            )

        if self.config.log_line_selection and engine_seed and engine_seed.opener_seed:
            logger.info(
                "voice.line_selection",
                extra={
                    "opener": engine_seed.opener_seed,
                    "example_count": len(engine_seed.example_lines),
                    "tone_hints": engine_seed.tone_hints,
                },
            )

        if self.config.log_injection:
            block = final.to_prompt_block()
            logger.debug(
                "voice.injection",
                extra={
                    "source": final.source.value,
                    "block_length": len(block),
                    "token_estimate": final.token_estimate(),
                },
            )

    def log_shadow(self, engine_name: str, seed: VoiceSeed) -> None:
        """Log a shadow mode computation (not injected)."""
        if not self.config.log_shadow_diff:
            return

        logger.info(
            "voice.shadow",
            extra={
                "engine": engine_name,
                "alpha": seed.alpha,
                "tier": seed.tier.value,
                "opener": seed.opener_seed,
                "would_inject": bool(seed.to_prompt_block()),
            },
        )
