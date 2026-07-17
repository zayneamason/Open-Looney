"""Tests for VoiceBlendEngine — 5-stage pipeline."""

from pathlib import Path

import pytest

from luna.voice.blend_engine import VoiceBlendEngine, _alpha_to_tier, _clamp, _turn_decay
from luna.voice.models import (
    ConfidenceSignals,
    ConfidenceTier,
    ContextType,
    EngineMode,
    VoiceSeedSource,
    VoiceSystemConfig,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def config() -> VoiceSystemConfig:
    return VoiceSystemConfig(
        blend_engine_mode=EngineMode.ACTIVE,
        voice_corpus_mode=EngineMode.OFF,
        line_bank_path=str(FIXTURES / "sample_bank.json"),
    )


@pytest.fixture
def engine(config: VoiceSystemConfig) -> VoiceBlendEngine:
    return VoiceBlendEngine(config)


def _cold_start_signals() -> ConfidenceSignals:
    return ConfidenceSignals(
        memory_retrieval_score=0.08,
        turn_number=1,
        entity_resolution_depth=0,
        context_type=ContextType.COLD_START,
        topic_continuity=0.0,
    )


def _warm_signals() -> ConfidenceSignals:
    return ConfidenceSignals(
        memory_retrieval_score=0.78,
        turn_number=3,
        entity_resolution_depth=2,
        context_type=ContextType.FOLLOW_UP,
        topic_continuity=0.9,
    )


def _emotional_signals() -> ConfidenceSignals:
    return ConfidenceSignals(
        memory_retrieval_score=0.55,
        turn_number=2,
        entity_resolution_depth=1,
        context_type=ContextType.EMOTIONAL,
        topic_continuity=0.3,
    )


def _strong_memory_signals() -> ConfidenceSignals:
    return ConfidenceSignals(
        memory_retrieval_score=0.9,
        turn_number=2,
        entity_resolution_depth=2,
        context_type=ContextType.FOLLOW_UP,
        topic_continuity=0.8,
    )


# ── Helpers ────────────────────────────────────────────────────


class TestHelpers:
    def test_clamp(self):
        assert _clamp(0.5, 0.0, 1.0) == 0.5
        assert _clamp(-0.1, 0.05, 0.95) == 0.05
        assert _clamp(1.5, 0.05, 0.95) == 0.95

    def test_turn_decay(self):
        assert _turn_decay(1) == 1.0
        assert _turn_decay(2) == pytest.approx(0.7)
        assert _turn_decay(3) == pytest.approx(0.4)
        assert _turn_decay(4) == pytest.approx(0.1)
        assert _turn_decay(5) == 0.0

    def test_alpha_to_tier(self):
        assert _alpha_to_tier(0.8) == ConfidenceTier.GROUNDING
        assert _alpha_to_tier(0.61) == ConfidenceTier.GROUNDING
        assert _alpha_to_tier(0.6) == ConfidenceTier.ENGAGING
        assert _alpha_to_tier(0.31) == ConfidenceTier.ENGAGING
        assert _alpha_to_tier(0.3) == ConfidenceTier.FLOWING
        assert _alpha_to_tier(0.1) == ConfidenceTier.FLOWING


# ── Stage 1: ConfidenceRouter ─────────────────────────────────


class TestConfidenceRouter:
    def test_cold_start_high_alpha(self, engine: VoiceBlendEngine):
        signals = _cold_start_signals()
        result = engine._compute_confidence(signals)
        # Cold start, no memory, turn 1 — should be high
        assert result.alpha > 0.8
        assert result.tier == ConfidenceTier.GROUNDING

    def test_warm_followup_low_alpha(self, engine: VoiceBlendEngine):
        signals = _warm_signals()
        result = engine._compute_confidence(signals)
        # Strong memory, turn 3, follow_up — should be low
        assert result.alpha < 0.4
        assert result.tier in (ConfidenceTier.ENGAGING, ConfidenceTier.FLOWING)

    def test_alpha_clamped_low(self, engine: VoiceBlendEngine):
        signals = ConfidenceSignals(
            memory_retrieval_score=1.0,
            turn_number=5,
            entity_resolution_depth=3,
            context_type=ContextType.FOLLOW_UP,
            topic_continuity=1.0,
        )
        result = engine._compute_confidence(signals)
        assert result.alpha >= 0.05

    def test_alpha_clamped_high(self, engine: VoiceBlendEngine):
        signals = ConfidenceSignals(
            memory_retrieval_score=0.0,
            turn_number=1,
            entity_resolution_depth=0,
            context_type=ContextType.COLD_START,
            topic_continuity=0.0,
        )
        result = engine._compute_confidence(signals)
        assert result.alpha <= 0.95

    def test_signal_contributions_sum(self, engine: VoiceBlendEngine):
        signals = _cold_start_signals()
        result = engine._compute_confidence(signals)
        total = sum(result.signal_contributions.values())
        # Should roughly match alpha (before clamping)
        assert 0.0 < total < 1.5

    def test_bypass_returns_default(self):
        config = VoiceSystemConfig(
            blend_engine_mode=EngineMode.ACTIVE,
            voice_corpus_mode=EngineMode.OFF,
            line_bank_path=str(FIXTURES / "sample_bank.json"),
            bypass_confidence_router=True,
        )
        engine = VoiceBlendEngine(config)
        result = engine._compute_confidence(_cold_start_signals())
        assert result.alpha == pytest.approx(0.5)
        assert result.signal_contributions == {}

    def test_alpha_override(self):
        config = VoiceSystemConfig(
            blend_engine_mode=EngineMode.ACTIVE,
            voice_corpus_mode=EngineMode.OFF,
            line_bank_path=str(FIXTURES / "sample_bank.json"),
            alpha_override=0.75,
        )
        engine = VoiceBlendEngine(config)
        result = engine._compute_confidence(_cold_start_signals())
        assert result.alpha == pytest.approx(0.75)


# ── Stage 2: FadeController ──────────────────────────────────


class TestFadeController:
    def test_turn_3_reduces_by_0_2(self, engine: VoiceBlendEngine):
        signals = _cold_start_signals()
        # Simulate 2 prior turns
        engine._alpha_history = [0.9, 0.7]
        engine._turn_history = [ContextType.COLD_START, ContextType.FOLLOW_UP]

        confidence = engine._compute_confidence(signals)
        faded = engine._apply_fade(confidence, signals)
        # Should subtract 0.2
        assert faded.alpha < confidence.alpha
        assert faded.fade_reason is not None
        assert "turn 3" in faded.fade_reason

    def test_turn_4_plus_caps_at_0_15(self, engine: VoiceBlendEngine):
        signals = _cold_start_signals()
        # Simulate 3 prior turns
        engine._alpha_history = [0.9, 0.7, 0.5]
        engine._turn_history = [
            ContextType.COLD_START,
            ContextType.FOLLOW_UP,
            ContextType.FOLLOW_UP,
        ]

        confidence = engine._compute_confidence(signals)
        faded = engine._apply_fade(confidence, signals)
        assert faded.alpha <= 0.15
        assert "turn 4+" in faded.fade_reason

    def test_emotional_caps_at_0_40(self, engine: VoiceBlendEngine):
        signals = _emotional_signals()
        confidence = engine._compute_confidence(signals)
        faded = engine._apply_fade(confidence, signals)
        assert faded.alpha <= 0.40
        assert "emotional" in faded.fade_reason

    def test_strong_memory_drops_by_0_3(self, engine: VoiceBlendEngine):
        signals = _strong_memory_signals()
        confidence = engine._compute_confidence(signals)
        faded = engine._apply_fade(confidence, signals)
        assert faded.alpha < confidence.alpha
        assert faded.fade_adjustment == pytest.approx(-0.3)
        assert "strong memory" in faded.fade_reason

    def test_context_switch_resets(self, engine: VoiceBlendEngine):
        # Simulate prior turns in a different context
        engine._alpha_history = [0.9, 0.7]
        engine._turn_history = [ContextType.COLD_START, ContextType.FOLLOW_UP]

        signals = ConfidenceSignals(
            memory_retrieval_score=0.35,
            turn_number=3,
            entity_resolution_depth=1,
            context_type=ContextType.TOPIC_SHIFT,
            topic_continuity=0.05,
        )

        confidence = engine._compute_confidence(signals)
        faded = engine._apply_fade(confidence, signals)
        # Context switch — no fade adjustment
        assert faded.fade_adjustment == 0.0
        assert "context switch" in faded.fade_reason

    def test_bypass_skips_all(self):
        config = VoiceSystemConfig(
            blend_engine_mode=EngineMode.ACTIVE,
            voice_corpus_mode=EngineMode.OFF,
            line_bank_path=str(FIXTURES / "sample_bank.json"),
            bypass_fade_controller=True,
        )
        engine = VoiceBlendEngine(config)
        engine._alpha_history = [0.9, 0.7, 0.5]
        engine._turn_history = [
            ContextType.COLD_START,
            ContextType.FOLLOW_UP,
            ContextType.FOLLOW_UP,
        ]

        signals = _cold_start_signals()
        confidence = engine._compute_confidence(signals)
        faded = engine._apply_fade(confidence, signals)
        # Bypassed — alpha unchanged
        assert faded.alpha == confidence.alpha


# ── Stage 3: SegmentPlanner ──────────────────────────────────


class TestSegmentPlanner:
    def test_high_alpha_three_segments(self, engine: VoiceBlendEngine):
        from luna.voice.models import ConfidenceResult

        confidence = ConfidenceResult(
            alpha=0.7,
            tier=ConfidenceTier.GROUNDING,
            signals=_cold_start_signals(),
            signal_contributions={},
        )
        plan = engine._plan_segments(confidence)
        assert len(plan.segments) == 3
        # Front-loaded: opener > body > closer
        assert plan.segments[0].alpha > plan.segments[1].alpha
        assert plan.segments[1].alpha > plan.segments[2].alpha

    def test_low_alpha_single_segment(self, engine: VoiceBlendEngine):
        from luna.voice.models import ConfidenceResult

        confidence = ConfidenceResult(
            alpha=0.2,
            tier=ConfidenceTier.FLOWING,
            signals=_warm_signals(),
            signal_contributions={},
        )
        plan = engine._plan_segments(confidence)
        assert len(plan.segments) == 1
        assert plan.expected_length == "short"

    def test_opener_alpha_capped_at_0_95(self, engine: VoiceBlendEngine):
        from luna.voice.models import ConfidenceResult

        confidence = ConfidenceResult(
            alpha=0.9,
            tier=ConfidenceTier.GROUNDING,
            signals=_cold_start_signals(),
            signal_contributions={},
        )
        plan = engine._plan_segments(confidence)
        assert plan.segments[0].alpha <= 0.95

    def test_bypass_returns_uniform(self):
        config = VoiceSystemConfig(
            blend_engine_mode=EngineMode.ACTIVE,
            voice_corpus_mode=EngineMode.OFF,
            line_bank_path=str(FIXTURES / "sample_bank.json"),
            bypass_segment_planner=True,
        )
        engine = VoiceBlendEngine(config)
        from luna.voice.models import ConfidenceResult

        confidence = ConfidenceResult(
            alpha=0.7,
            tier=ConfidenceTier.GROUNDING,
            signals=_cold_start_signals(),
            signal_contributions={},
        )
        plan = engine._plan_segments(confidence)
        assert len(plan.segments) == 1
        assert plan.segments[0].alpha == confidence.alpha


# ── Stage 4: LineSampler ─────────────────────────────────────


class TestLineSampler:
    def test_selects_lines_for_segments(self, engine: VoiceBlendEngine):
        seed = engine.generate_seed(_cold_start_signals())
        # Should have selected at least some lines
        assert seed.example_lines  # Not empty

    def test_bypass_empty_selected_lines(self):
        config = VoiceSystemConfig(
            blend_engine_mode=EngineMode.ACTIVE,
            voice_corpus_mode=EngineMode.OFF,
            line_bank_path=str(FIXTURES / "sample_bank.json"),
            bypass_line_sampler=True,
        )
        engine = VoiceBlendEngine(config)
        seed = engine.generate_seed(_cold_start_signals())
        # Bypassed sampler — no lines selected, empty examples
        assert seed.example_lines == []
        assert seed.opener_seed is None


# ── Full Pipeline ─────────────────────────────────────────────


class TestFullPipeline:
    def test_cold_start_produces_engine_seed(self, engine: VoiceBlendEngine):
        seed = engine.generate_seed(_cold_start_signals())
        assert seed.source == VoiceSeedSource.ENGINE
        assert seed.engine_active is True
        assert seed.corpus_active is False

    def test_warm_followup_low_injection(self, engine: VoiceBlendEngine):
        seed = engine.generate_seed(_warm_signals())
        assert seed.alpha < 0.5

    def test_no_anti_patterns_from_engine(self, engine: VoiceBlendEngine):
        """Engine doesn't inject anti-patterns — Corpus owns those."""
        seed = engine.generate_seed(_cold_start_signals())
        assert seed.anti_patterns == []

    def test_reset_conversation(self, engine: VoiceBlendEngine):
        engine.generate_seed(_cold_start_signals())
        engine.generate_seed(_warm_signals())
        assert len(engine._alpha_history) == 2
        assert len(engine._turn_history) == 2

        engine.reset_conversation()
        assert engine._alpha_history == []
        assert engine._turn_history == []

    def test_alpha_history_grows(self, engine: VoiceBlendEngine):
        engine.generate_seed(_cold_start_signals())
        assert len(engine._alpha_history) == 1

        engine.generate_seed(_warm_signals())
        assert len(engine._alpha_history) == 2

    def test_produces_valid_prompt_block(self, engine: VoiceBlendEngine):
        seed = engine.generate_seed(_cold_start_signals())
        block = seed.to_prompt_block()
        assert '<luna_voice source="engine">' in block
        assert "</luna_voice>" in block
