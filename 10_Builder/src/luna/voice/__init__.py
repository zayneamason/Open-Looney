"""
Luna Voice Package
==================

Two-engine voice system that makes Luna sound like Luna.

Components:
- classify_query_type: Query classification heuristics (signal feeder for blend engine)
- VoiceSystemOrchestrator: Single entry point for context_builder
- VoiceBlendEngine: Confidence-weighted scaffolding (primary)
- VoiceCorpusService: Static few-shot + kill list (fallback)

Note: VoiceLock class removed per B7 role resolution (v2.0.0).
"""

from luna.voice.lock import classify_query_type
from luna.voice.models import (
    ConfidenceSignals,
    ConfidenceTier,
    ContextType,
    EmotionalRegister,
    EngineMode,
    VoiceSeed,
    VoiceSystemConfig,
)
from luna.voice.orchestrator import VoiceSystemOrchestrator

__all__ = [
    "classify_query_type",
    # Voice System
    "VoiceSystemOrchestrator",
    "VoiceSystemConfig",
    "VoiceSeed",
    "ConfidenceSignals",
    "ConfidenceTier",
    "ContextType",
    "EmotionalRegister",
    "EngineMode",
]
