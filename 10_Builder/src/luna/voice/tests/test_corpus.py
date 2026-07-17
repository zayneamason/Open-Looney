"""Tests for VoiceCorpusService."""

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from luna.voice.corpus_service import VoiceCorpusService
from luna.voice.models import (
    ConfidenceTier,
    ContextType,
    EmotionalRegister,
    EngineMode,
    VoiceSeedSource,
    VoiceSystemConfig,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def config() -> VoiceSystemConfig:
    return VoiceSystemConfig(
        blend_engine_mode=EngineMode.OFF,
        voice_corpus_mode=EngineMode.ACTIVE,
        corpus_path=str(FIXTURES / "sample_bank.json"),
    )


@pytest.fixture
def service(config: VoiceSystemConfig) -> VoiceCorpusService:
    return VoiceCorpusService(config)


class TestVoiceCorpusService:
    def test_generates_corpus_source(self, service: VoiceCorpusService):
        seed = service.generate_seed(ContextType.COLD_START, turn_number=1)
        assert seed.source == VoiceSeedSource.CORPUS
        assert seed.corpus_active is True
        assert seed.engine_active is False

    def test_turn_1_grounding_tier(self, service: VoiceCorpusService):
        seed = service.generate_seed(ContextType.COLD_START, turn_number=1)
        assert seed.tier == ConfidenceTier.GROUNDING

    def test_turn_2_engaging_tier(self, service: VoiceCorpusService):
        seed = service.generate_seed(ContextType.FOLLOW_UP, turn_number=2)
        assert seed.tier == ConfidenceTier.ENGAGING

    def test_turn_3_plus_flowing_tier(self, service: VoiceCorpusService):
        seed = service.generate_seed(ContextType.FOLLOW_UP, turn_number=3)
        assert seed.tier == ConfidenceTier.FLOWING

        seed5 = service.generate_seed(ContextType.FOLLOW_UP, turn_number=5)
        assert seed5.tier == ConfidenceTier.FLOWING

    def test_context_tag_filtering(self, service: VoiceCorpusService):
        seed = service.generate_seed(ContextType.COLD_START, turn_number=1)
        # Should prefer lines tagged with "cold_start"
        assert seed.opener_seed is not None
        assert len(seed.example_lines) > 0

    def test_emotional_register_preference(self, service: VoiceCorpusService):
        seed_warm = service.generate_seed(
            ContextType.EMOTIONAL, turn_number=2,
            emotional_register=EmotionalRegister.WARM,
        )
        # Should have warm in tone_hints if warm lines were available
        assert seed_warm.tone_hints  # Not empty

    def test_always_includes_critical_anti_patterns(self, service: VoiceCorpusService):
        seed = service.generate_seed(ContextType.COLD_START, turn_number=1)
        # Fixture has 2 severity=3 anti-patterns
        assert len(seed.anti_patterns) == 2
        assert "certainly" in seed.anti_patterns
        assert "I'd be happy to" in seed.anti_patterns

    def test_corpus_tier_override(self):
        config = VoiceSystemConfig(
            blend_engine_mode=EngineMode.OFF,
            voice_corpus_mode=EngineMode.ACTIVE,
            corpus_path=str(FIXTURES / "sample_bank.json"),
            corpus_tier_override="FLOWING",
        )
        service = VoiceCorpusService(config)
        seed = service.generate_seed(ContextType.COLD_START, turn_number=1)
        # Even though turn_number=1 would be GROUNDING, override forces FLOWING
        assert seed.tier == ConfidenceTier.FLOWING

    def test_empty_bank_no_opener(self):
        empty_bank = {
            "version": "empty",
            "updated_at": "2026-01-01T00:00:00Z",
            "lines": [],
            "anti_patterns": [],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(empty_bank, f)
            f.flush()
            config = VoiceSystemConfig(
                blend_engine_mode=EngineMode.OFF,
                voice_corpus_mode=EngineMode.ACTIVE,
                corpus_path=f.name,
            )
            service = VoiceCorpusService(config)
            seed = service.generate_seed(ContextType.COLD_START, turn_number=1)
            assert seed.opener_seed is None
            assert seed.example_lines == []

    def test_produces_valid_prompt_block(self, service: VoiceCorpusService):
        seed = service.generate_seed(ContextType.COLD_START, turn_number=1)
        block = seed.to_prompt_block()
        assert '<luna_voice source="corpus">' in block
        assert "</luna_voice>" in block

    def test_alpha_always_0_5_for_corpus(self, service: VoiceCorpusService):
        """Corpus doesn't compute alpha — always 0.5."""
        for turn in [1, 2, 3, 5, 10]:
            seed = service.generate_seed(ContextType.FOLLOW_UP, turn_number=turn)
            assert seed.alpha == 0.5
