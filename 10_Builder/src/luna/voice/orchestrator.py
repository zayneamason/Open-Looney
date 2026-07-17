"""
Voice System Orchestrator — Single entry point for context_builder.

Manages both VoiceBlendEngine and VoiceCorpusService. Merges their
outputs into one VoiceSeed. Handles shadow mode and hot-reload.
"""

from __future__ import annotations

from typing import Optional

from luna.voice.blend_engine import VoiceBlendEngine
from luna.voice.corpus_service import VoiceCorpusService
from luna.voice.logger import VoiceSystemLogger
from luna.voice.models import (
    ConfidenceSignals,
    ContextType,
    EmotionalRegister,
    EngineMode,
    VoiceSeed,
    VoiceSeedSource,
    VoiceSystemConfig,
)


class VoiceSystemOrchestrator:
    """Single entry point for context_builder. Manages both engines."""

    def __init__(self, config: VoiceSystemConfig):
        self.config = config
        self.engine: Optional[VoiceBlendEngine] = None
        self.corpus: Optional[VoiceCorpusService] = None
        self._logger = VoiceSystemLogger(config)

        if config.blend_engine_mode != EngineMode.OFF:
            self.engine = VoiceBlendEngine(config)
        if config.voice_corpus_mode != EngineMode.OFF:
            self.corpus = VoiceCorpusService(config)

    def generate_voice_block(
        self,
        signals: ConfidenceSignals,
        context_type: ContextType,
        turn_number: int,
        emotional_register: Optional[EmotionalRegister] = None,
    ) -> str:
        """THE interface. Returns string to inject into prompt, or ""."""
        engine_seed: Optional[VoiceSeed] = None
        corpus_seed: Optional[VoiceSeed] = None

        if self.engine:
            engine_seed = self.engine.generate_seed(signals)
            if self.config.blend_engine_mode == EngineMode.SHADOW:
                self._logger.log_shadow("engine", engine_seed)
                engine_seed = None

        if self.corpus:
            corpus_seed = self.corpus.generate_seed(
                context_type, turn_number, emotional_register
            )
            if self.config.voice_corpus_mode == EngineMode.SHADOW:
                self._logger.log_shadow("corpus", corpus_seed)
                corpus_seed = None

        final = self._merge(engine_seed, corpus_seed)
        self._logger.log_generation(signals, engine_seed, corpus_seed, final)
        return final.to_prompt_block()

    def _merge(
        self,
        engine_seed: Optional[VoiceSeed],
        corpus_seed: Optional[VoiceSeed],
    ) -> VoiceSeed:
        """Merge engine and corpus seeds. Engine wins on confidence, corpus provides kill list."""
        if not engine_seed and not corpus_seed:
            return VoiceSeed(source=VoiceSeedSource.NONE)
        if engine_seed and not corpus_seed:
            return engine_seed
        if corpus_seed and not engine_seed:
            return corpus_seed

        # Both active: engine confidence + corpus kill list
        return VoiceSeed(
            source=VoiceSeedSource.MERGED,
            alpha=engine_seed.alpha,
            tier=engine_seed.tier,
            opener_seed=engine_seed.opener_seed,
            opener_weight=engine_seed.opener_weight,
            tone_hints=list(
                set(engine_seed.tone_hints + corpus_seed.tone_hints)
            ),
            example_lines=engine_seed.example_lines or corpus_seed.example_lines,
            anti_patterns=corpus_seed.anti_patterns,
            engine_active=True,
            corpus_active=True,
        )

    def on_conversation_start(self) -> None:
        """Reset engine fade state for new conversation."""
        if self.engine:
            self.engine.reset_conversation()

    def on_config_change(self, new_config: VoiceSystemConfig) -> None:
        """Hot-reload without restart."""
        self.config = new_config
        self._logger = VoiceSystemLogger(new_config)

        if new_config.blend_engine_mode == EngineMode.OFF:
            self.engine = None
        elif not self.engine:
            self.engine = VoiceBlendEngine(new_config)

        if new_config.voice_corpus_mode == EngineMode.OFF:
            self.corpus = None
        elif not self.corpus:
            self.corpus = VoiceCorpusService(new_config)
