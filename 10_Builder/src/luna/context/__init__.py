"""Luna Context Module — Unified context pipeline for all inference paths."""

from .pipeline import ContextPipeline, ContextPacket
from .assembler import PromptAssembler, PromptRequest, PromptResult, MemoryConfidence
from .modes import ResponseMode, IntentClassification, MODE_CONTRACTS
from .temporal import TemporalContext, build_temporal_context
from .perception import PerceptionField, Observation
from .register import ContextRegister, RegisterState, REGISTER_CONTRACTS
from .study_context import StudyContextRenderer, load_study_context

__all__ = [
    "ContextPipeline", "ContextPacket",
    "PromptAssembler", "PromptRequest", "PromptResult", "MemoryConfidence",
    "ResponseMode", "IntentClassification", "MODE_CONTRACTS",
    "TemporalContext", "build_temporal_context",
    "PerceptionField", "Observation",
    "ContextRegister", "RegisterState", "REGISTER_CONTRACTS",
    "StudyContextRenderer", "load_study_context",
]
