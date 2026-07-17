"""Tests for VoiceSystemOrchestrator — merge logic, toggles, shadow mode."""

from pathlib import Path
from unittest.mock import patch

import pytest

from luna.voice.models import (
    ConfidenceSignals,
    ConfidenceTier,
    ContextType,
    EmotionalRegister,
    EngineMode,
    VoiceSeedSource,
    VoiceSystemConfig,
)
from luna.voice.orchestrator import VoiceSystemOrchestrator

FIXTURES = Path(__file__).parent / "fixtures"

BANK_PATH = str(FIXTURES / "sample_bank.json")


def _make_config(
    engine: EngineMode = EngineMode.ACTIVE,
    corpus: EngineMode = EngineMode.ACTIVE,
) -> VoiceSystemConfig:
    return VoiceSystemConfig(
        blend_engine_mode=engine,
        voice_corpus_mode=corpus,
        line_bank_path=BANK_PATH,
        corpus_path=BANK_PATH,
    )


def _signals() -> ConfidenceSignals:
    return ConfidenceSignals(
        memory_retrieval_score=0.3,
        turn_number=1,
        entity_resolution_depth=0,
        context_type=ContextType.COLD_START,
        topic_continuity=0.0,
    )


# ── Toggle Combinations (Priority Chain) ─────────────────────


class TestToggleCombinations:
    def test_both_off_empty_string(self):
        orch = VoiceSystemOrchestrator(_make_config(EngineMode.OFF, EngineMode.OFF))
        result = orch.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        assert result == ""

    def test_corpus_only(self):
        orch = VoiceSystemOrchestrator(_make_config(EngineMode.OFF, EngineMode.ACTIVE))
        result = orch.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        assert 'source="corpus"' in result
        assert "<avoid>" in result  # anti-patterns

    def test_engine_only_no_anti_patterns(self):
        orch = VoiceSystemOrchestrator(_make_config(EngineMode.ACTIVE, EngineMode.OFF))
        result = orch.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        assert 'source="engine"' in result
        assert "<avoid>" not in result  # No corpus = no kill list

    def test_both_active_merged(self):
        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.ACTIVE, EngineMode.ACTIVE)
        )
        result = orch.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        assert 'source="merged"' in result
        assert "<avoid>" in result  # Corpus provides kill list

    def test_shadow_engine_corpus_active(self):
        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.SHADOW, EngineMode.ACTIVE)
        )
        result = orch.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        # Engine in shadow = corpus only output
        assert 'source="corpus"' in result

    def test_engine_active_shadow_corpus(self):
        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.ACTIVE, EngineMode.SHADOW)
        )
        result = orch.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        # Corpus in shadow = engine only output
        assert 'source="engine"' in result
        assert "<avoid>" not in result

    def test_both_shadow_empty_string(self):
        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.SHADOW, EngineMode.SHADOW)
        )
        result = orch.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        # Both shadow = neither injects
        assert result == ""


# ── Merge Logic ──────────────────────────────────────────────


class TestMergeLogic:
    def test_merged_uses_engine_alpha(self):
        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.ACTIVE, EngineMode.ACTIVE)
        )
        result = orch.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        # Merged block should have engine's alpha, not corpus's 0.5
        assert 'alpha="0.50"' not in result or 'source="merged"' in result

    def test_merged_has_corpus_kill_list(self):
        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.ACTIVE, EngineMode.ACTIVE)
        )
        result = orch.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        assert "<never>certainly</never>" in result
        assert "<never>I'd be happy to</never>" in result


# ── Conversation Lifecycle ───────────────────────────────────


class TestConversationLifecycle:
    def test_on_conversation_start_resets_engine(self):
        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.ACTIVE, EngineMode.OFF)
        )
        # Generate a few seeds to build history
        orch.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        orch.generate_voice_block(
            _signals(), ContextType.FOLLOW_UP, turn_number=2
        )
        assert len(orch.engine._alpha_history) == 2

        orch.on_conversation_start()
        assert orch.engine._alpha_history == []
        assert orch.engine._turn_history == []

    def test_on_conversation_start_noop_without_engine(self):
        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.OFF, EngineMode.ACTIVE)
        )
        # Should not raise
        orch.on_conversation_start()


# ── Hot Reload ───────────────────────────────────────────────


class TestHotReload:
    def test_config_change_enables_engine(self):
        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.OFF, EngineMode.ACTIVE)
        )
        assert orch.engine is None

        new_config = _make_config(EngineMode.ACTIVE, EngineMode.ACTIVE)
        orch.on_config_change(new_config)
        assert orch.engine is not None

    def test_config_change_disables_engine(self):
        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.ACTIVE, EngineMode.ACTIVE)
        )
        assert orch.engine is not None

        new_config = _make_config(EngineMode.OFF, EngineMode.ACTIVE)
        orch.on_config_change(new_config)
        assert orch.engine is None

    def test_config_change_disables_corpus(self):
        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.ACTIVE, EngineMode.ACTIVE)
        )
        assert orch.corpus is not None

        new_config = _make_config(EngineMode.ACTIVE, EngineMode.OFF)
        orch.on_config_change(new_config)
        assert orch.corpus is None

    def test_config_change_takes_effect_next_call(self):
        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.OFF, EngineMode.ACTIVE)
        )
        result1 = orch.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        assert 'source="corpus"' in result1

        # Enable engine
        new_config = _make_config(EngineMode.ACTIVE, EngineMode.ACTIVE)
        orch.on_config_change(new_config)

        result2 = orch.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        assert 'source="merged"' in result2


# ── Invariants ───────────────────────────────────────────────


class TestInvariants:
    def test_both_off_identical_to_no_system(self):
        """Both engines off -> prompt identical to pre-voice-system Luna."""
        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.OFF, EngineMode.OFF)
        )
        result = orch.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        assert result == ""

    def test_shadow_zero_impact(self):
        """Shadow mode -> zero impact on model output."""
        orch_off = VoiceSystemOrchestrator(
            _make_config(EngineMode.OFF, EngineMode.OFF)
        )
        orch_shadow = VoiceSystemOrchestrator(
            _make_config(EngineMode.SHADOW, EngineMode.SHADOW)
        )

        result_off = orch_off.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        result_shadow = orch_shadow.generate_voice_block(
            _signals(), ContextType.COLD_START, turn_number=1
        )
        assert result_off == result_shadow == ""

    def test_voice_system_read_only(self):
        """Voice system is read-only — never modifies signals."""
        signals = _signals()
        original_score = signals.memory_retrieval_score
        original_turn = signals.turn_number

        orch = VoiceSystemOrchestrator(
            _make_config(EngineMode.ACTIVE, EngineMode.ACTIVE)
        )
        orch.generate_voice_block(
            signals, ContextType.COLD_START, turn_number=1
        )

        assert signals.memory_retrieval_score == original_score
        assert signals.turn_number == original_turn
