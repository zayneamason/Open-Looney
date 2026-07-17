"""Tests for voice system Pydantic models."""

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from luna.voice.models import (
    AntiPattern,
    ConfidenceResult,
    ConfidenceSignals,
    ConfidenceTier,
    ContextType,
    EmotionalRegister,
    EngineMode,
    LineBank,
    ResponseSegment,
    SegmentPlan,
    SegmentType,
    VoiceLine,
    VoiceSeed,
    VoiceSeedSource,
    VoiceSystemConfig,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ── VoiceLine ──────────────────────────────────────────────────


class TestVoiceLine:
    def test_serialize_roundtrip(self):
        line = VoiceLine(
            id="test_001",
            text="test line",
            cost=3,
            tier=ConfidenceTier.GROUNDING,
            context_tags=["cold_start"],
            emotional_register=EmotionalRegister.WARM,
            segment_type=SegmentType.OPENER,
        )
        data = line.model_dump()
        restored = VoiceLine.model_validate(data)
        assert restored == line

    def test_cost_bounds(self):
        with pytest.raises(Exception):
            VoiceLine(
                id="x", text="x", cost=0,
                tier=ConfidenceTier.GROUNDING,
                emotional_register=EmotionalRegister.WARM,
                segment_type=SegmentType.OPENER,
            )
        with pytest.raises(Exception):
            VoiceLine(
                id="x", text="x", cost=6,
                tier=ConfidenceTier.GROUNDING,
                emotional_register=EmotionalRegister.WARM,
                segment_type=SegmentType.OPENER,
            )


# ── AntiPattern ────────────────────────────────────────────────


class TestAntiPattern:
    def test_serialize(self):
        ap = AntiPattern(phrase="certainly", reason="butler energy", severity=3)
        data = ap.model_dump()
        assert data["phrase"] == "certainly"
        assert data["severity"] == 3

    def test_severity_bounds(self):
        with pytest.raises(Exception):
            AntiPattern(phrase="x", reason="x", severity=0)
        with pytest.raises(Exception):
            AntiPattern(phrase="x", reason="x", severity=4)


# ── LineBank ───────────────────────────────────────────────────


class TestLineBank:
    @pytest.fixture
    def bank(self) -> LineBank:
        with open(FIXTURES / "sample_bank.json") as f:
            return LineBank.model_validate_json(f.read())

    def test_load_from_fixture(self, bank: LineBank):
        assert bank.version == "test"
        assert len(bank.lines) == 8
        assert len(bank.anti_patterns) == 4

    def test_by_tier(self, bank: LineBank):
        grounding = bank.by_tier(ConfidenceTier.GROUNDING)
        assert all(l.tier == ConfidenceTier.GROUNDING for l in grounding)
        assert len(grounding) == 3

        engaging = bank.by_tier(ConfidenceTier.ENGAGING)
        assert len(engaging) == 3

        flowing = bank.by_tier(ConfidenceTier.FLOWING)
        assert len(flowing) == 2

    def test_by_tags(self, bank: LineBank):
        cold = bank.by_tags(["cold_start"])
        assert all("cold_start" in l.context_tags for l in cold)
        assert len(cold) >= 2

    def test_by_tags_multiple(self, bank: LineBank):
        results = bank.by_tags(["cold_start", "emotional"])
        # Should return lines that have ANY of the tags
        assert len(results) >= 3

    def test_critical_anti_patterns(self, bank: LineBank):
        critical = bank.critical_anti_patterns()
        assert all(ap.severity == 3 for ap in critical)
        assert len(critical) == 2


# ── ConfidenceSignals ──────────────────────────────────────────


class TestConfidenceSignals:
    def test_valid_signals(self):
        signals = ConfidenceSignals(
            memory_retrieval_score=0.5,
            turn_number=1,
            entity_resolution_depth=2,
            context_type=ContextType.COLD_START,
            topic_continuity=0.3,
        )
        assert signals.memory_retrieval_score == 0.5

    def test_bounds(self):
        with pytest.raises(Exception):
            ConfidenceSignals(
                memory_retrieval_score=1.5,
                turn_number=1,
                entity_resolution_depth=0,
                context_type=ContextType.COLD_START,
                topic_continuity=0.0,
            )
        with pytest.raises(Exception):
            ConfidenceSignals(
                memory_retrieval_score=0.5,
                turn_number=0,
                entity_resolution_depth=0,
                context_type=ContextType.COLD_START,
                topic_continuity=0.0,
            )


# ── VoiceSeed ──────────────────────────────────────────────────


class TestVoiceSeed:
    def test_none_source_empty_block(self):
        seed = VoiceSeed(source=VoiceSeedSource.NONE)
        assert seed.to_prompt_block() == ""

    def test_engine_source_renders_xml(self):
        seed = VoiceSeed(
            source=VoiceSeedSource.ENGINE,
            alpha=0.8,
            tier=ConfidenceTier.GROUNDING,
            opener_seed="hmm, catch me up first",
            opener_weight=0.8,
            tone_hints=["warm", "honest"],
            example_lines=["hmm, catch me up first", "fill me in?"],
        )
        block = seed.to_prompt_block()
        assert '<luna_voice source="engine">' in block
        assert 'alpha="0.80"' in block
        assert 'tier="GROUNDING"' in block
        assert 'seed="hmm, catch me up first"' in block
        assert "<say>" in block
        assert "</luna_voice>" in block

    def test_corpus_source_with_anti_patterns(self):
        seed = VoiceSeed(
            source=VoiceSeedSource.CORPUS,
            alpha=0.5,
            tier=ConfidenceTier.ENGAGING,
            anti_patterns=["certainly", "I'd be happy to"],
        )
        block = seed.to_prompt_block()
        assert "<avoid>" in block
        assert "<never>certainly</never>" in block

    def test_merged_source(self):
        seed = VoiceSeed(
            source=VoiceSeedSource.MERGED,
            alpha=0.6,
            tier=ConfidenceTier.ENGAGING,
            opener_seed="test",
            opener_weight=0.6,
            anti_patterns=["certainly"],
            example_lines=["test line"],
            engine_active=True,
            corpus_active=True,
        )
        block = seed.to_prompt_block()
        assert 'source="merged"' in block
        assert "<examples>" in block
        assert "<avoid>" in block

    def test_token_estimate_nonzero(self):
        seed = VoiceSeed(
            source=VoiceSeedSource.ENGINE,
            alpha=0.5,
            tier=ConfidenceTier.ENGAGING,
            opener_seed="test",
            example_lines=["a", "b"],
        )
        assert seed.token_estimate() > 0

    def test_token_estimate_zero_for_none(self):
        seed = VoiceSeed(source=VoiceSeedSource.NONE)
        assert seed.token_estimate() == 0

    def test_no_opener_no_opener_tag(self):
        seed = VoiceSeed(
            source=VoiceSeedSource.ENGINE,
            alpha=0.5,
            tier=ConfidenceTier.ENGAGING,
        )
        block = seed.to_prompt_block()
        assert "<opener" not in block

    def test_no_examples_no_examples_tag(self):
        seed = VoiceSeed(
            source=VoiceSeedSource.ENGINE,
            alpha=0.5,
            tier=ConfidenceTier.ENGAGING,
        )
        block = seed.to_prompt_block()
        assert "<examples>" not in block


# ── VoiceSystemConfig ─────────────────────────────────────────


class TestVoiceSystemConfig:
    def test_defaults(self):
        config = VoiceSystemConfig()
        assert config.blend_engine_mode == EngineMode.ACTIVE
        assert config.voice_corpus_mode == EngineMode.ACTIVE
        assert config.alpha_override is None

    def test_alpha_override_validation(self):
        # Valid
        config = VoiceSystemConfig(alpha_override=0.5)
        assert config.alpha_override == 0.5

        # Invalid
        with pytest.raises(Exception):
            VoiceSystemConfig(alpha_override=1.5)
        with pytest.raises(Exception):
            VoiceSystemConfig(alpha_override=-0.1)

    def test_from_yaml(self):
        config = VoiceSystemConfig.from_yaml(
            str(FIXTURES / "sample_config.yaml")
        )
        assert config.blend_engine_mode == EngineMode.ACTIVE
        assert config.voice_corpus_mode == EngineMode.ACTIVE
        assert config.log_alpha is True

    def test_from_yaml_with_voice_config(self):
        voice_config_path = (
            Path(__file__).parent.parent / "data" / "voice_config.yaml"
        )
        if voice_config_path.exists():
            config = VoiceSystemConfig.from_yaml(str(voice_config_path))
            assert config.blend_engine_mode == EngineMode.OFF
            assert config.voice_corpus_mode == EngineMode.ACTIVE
