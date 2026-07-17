"""
Voice System Models — All Pydantic models for the voice system.

Shared across VoiceCorpusService, VoiceBlendEngine, and VoiceSystemOrchestrator.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ── Enums ──────────────────────────────────────────────────────


class EngineMode(str, Enum):
    ACTIVE = "active"
    SHADOW = "shadow"
    OFF = "off"


class ConfidenceTier(str, Enum):
    GROUNDING = "GROUNDING"
    ENGAGING = "ENGAGING"
    FLOWING = "FLOWING"


class ContextType(str, Enum):
    GREETING = "greeting"
    COLD_START = "cold_start"
    TOPIC_SHIFT = "topic_shift"
    FOLLOW_UP = "follow_up"
    EMOTIONAL = "emotional"
    TECHNICAL = "technical"
    CREATIVE = "creative"
    MEMORY_RECALL = "memory_recall"


class EmotionalRegister(str, Enum):
    WARM = "warm"
    DIRECT = "direct"
    PLAYFUL = "playful"
    ANALYTICAL = "analytical"
    UNCERTAIN = "uncertain"


class SegmentType(str, Enum):
    OPENER = "opener"
    BRIDGE = "bridge"
    CLOSER = "closer"
    CLARIFIER = "clarifier"
    REACTION = "reaction"


class VoiceSeedSource(str, Enum):
    ENGINE = "engine"
    CORPUS = "corpus"
    MERGED = "merged"
    NONE = "none"


# ── Line Models ────────────────────────────────────────────────


class VoiceLine(BaseModel):
    """A single line from Luna's voice palette."""

    id: str
    text: str
    cost: int = Field(ge=1, le=5)
    tier: ConfidenceTier
    context_tags: list[str] = []
    emotional_register: EmotionalRegister
    segment_type: SegmentType
    source: Optional[str] = None


class AntiPattern(BaseModel):
    """A phrase Luna should never say."""

    phrase: str
    reason: str
    severity: int = Field(ge=1, le=3)


class LineBank(BaseModel):
    """The complete voice line collection."""

    version: str
    lines: list[VoiceLine]
    anti_patterns: list[AntiPattern]
    updated_at: datetime

    def by_tier(self, tier: ConfidenceTier) -> list[VoiceLine]:
        return [line for line in self.lines if line.tier == tier]

    def by_tags(self, tags: list[str]) -> list[VoiceLine]:
        tag_set = set(tags)
        return [line for line in self.lines if tag_set & set(line.context_tags)]

    def critical_anti_patterns(self) -> list[AntiPattern]:
        return [ap for ap in self.anti_patterns if ap.severity == 3]


# ── Confidence & Blending Models ──────────────────────────────


class ConfidenceSignals(BaseModel):
    """Raw inputs to the ConfidenceRouter."""

    memory_retrieval_score: float = Field(ge=0.0, le=1.0)
    turn_number: int = Field(ge=1)
    entity_resolution_depth: int = Field(ge=0, le=3)
    context_type: ContextType
    topic_continuity: float = Field(ge=0.0, le=1.0)


class ConfidenceResult(BaseModel):
    """Output of the ConfidenceRouter."""

    alpha: float = Field(ge=0.05, le=0.95)
    tier: ConfidenceTier
    signals: ConfidenceSignals
    signal_contributions: dict[str, float]
    fade_adjustment: float = 0.0
    fade_reason: Optional[str] = None


class ResponseSegment(BaseModel):
    """A planned segment of the response."""

    segment_type: SegmentType
    alpha: float = Field(ge=0.0, le=1.0)
    cost_budget: float
    selected_lines: list[VoiceLine] = []


class SegmentPlan(BaseModel):
    """Full response plan."""

    segments: list[ResponseSegment]
    total_alpha: float
    expected_length: str  # "short" | "medium" | "long"


# ── VoiceSeed — The Output Contract ───────────────────────────


class VoiceSeed(BaseModel):
    """The voice injection block. Only contract with context_builder."""

    source: VoiceSeedSource

    alpha: float = 0.5
    tier: ConfidenceTier = ConfidenceTier.ENGAGING

    opener_seed: Optional[str] = None
    opener_weight: float = 0.0
    tone_hints: list[str] = []

    example_lines: list[str] = []
    anti_patterns: list[str] = []

    engine_active: bool = False
    corpus_active: bool = False

    def to_prompt_block(self) -> str:
        """Render as XML block for context_builder injection."""
        if self.source == VoiceSeedSource.NONE:
            return ""

        parts = [f'<luna_voice source="{self.source.value}">']
        parts.append(
            f'  <confidence alpha="{self.alpha:.2f}" tier="{self.tier.value}" />'
        )

        if self.opener_seed:
            parts.append(
                f'  <opener seed="{self.opener_seed}" weight="{self.opener_weight:.2f}" />'
            )

        if self.tone_hints:
            parts.append(f'  <tone hints="{", ".join(self.tone_hints)}" />')

        if self.example_lines:
            parts.append("  <examples>")
            for line in self.example_lines:
                parts.append(f"    <say>{line}</say>")
            parts.append("  </examples>")

        if self.anti_patterns:
            parts.append("  <avoid>")
            for ap in self.anti_patterns:
                parts.append(f"    <never>{ap}</never>")
            parts.append("  </avoid>")

        parts.append("</luna_voice>")
        return "\n".join(parts)

    def token_estimate(self) -> int:
        """Approximate token count of the injection block."""
        block = self.to_prompt_block()
        return len(block.split()) + len(block) // 4


# ── Config Model ──────────────────────────────────────────────


class VoiceSystemConfig(BaseModel):
    """Top-level voice system configuration."""

    blend_engine_mode: EngineMode = EngineMode.ACTIVE
    voice_corpus_mode: EngineMode = EngineMode.ACTIVE

    alpha_override: Optional[float] = None
    corpus_tier_override: Optional[str] = None

    bypass_confidence_router: bool = False
    bypass_segment_planner: bool = False
    bypass_line_sampler: bool = False
    bypass_fade_controller: bool = False

    line_bank_path: str = "data/voice/line_bank.json"
    corpus_path: str = "data/voice/corpus.json"

    log_alpha: bool = True
    log_line_selection: bool = True
    log_injection: bool = False
    log_shadow_diff: bool = True

    # Voice v2.0 Phase 1 Step 2 — bounded segmenter + interrupt-aware queue
    bounded_segmenter: bool = False

    # Voice v2.0 Phase 1 Step 3 — InterruptClassifier gate (see flags.py).
    interrupt_classifier_enabled: bool = False

    # Voice v2.0 Phase 1 Step 4 — ResumptionStrategy dispatch gate (see flags.py).
    resumption_enabled: bool = False

    # Voice v2.0 Phase 1 Step 5 — ConversationConsolidator gate (see flags.py).
    consolidator_enabled: bool = False

    # Voice v2.0 Phase 1 Step 2 — runtime segmenter tuning knobs.
    # Mirrors SegmenterConfig defaults (response_segmenter.py) + current
    # audio_ready_queue maxsize. Runtime-only; not persisted to YAML.
    segmenter_max_words: int = 30
    segmenter_min_words: int = 8
    segmenter_lookahead_depth: int = 2

    @field_validator("alpha_override")
    @classmethod
    def validate_alpha(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("alpha must be 0.0-1.0")
        return v

    @classmethod
    def from_yaml(cls, path: str) -> VoiceSystemConfig:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data.get("voice_system", {}))
