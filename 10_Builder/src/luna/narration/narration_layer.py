"""NarrationLayer — post-generation voice/persona enforcement.

Spec: `Docs/Design/LunaAssistantMapping/DESIGN_OpenQuestions_Resolution.md` Q3.

MVP scope: this layer wraps `check_voice_pass()` and returns a populated
`NarrationResult`. The full per-class behavior (Class 7 position conflict,
Class 8d persona enforcement, confidence label injection) lands in follow-up
work — interfaces are stable so integration points are correct now.
"""
from dataclasses import dataclass, field
from typing import Any, Optional

from luna.narration.voice_pass import VoicePassResult, check_voice_pass


@dataclass
class NarrationContext:
    class_id: int
    personality_dna: dict = field(default_factory=dict)
    mood_state: Optional[dict] = None
    past_positions: list = field(default_factory=list)
    persona_profile: Optional[dict] = None


@dataclass
class ConfidenceLabel:
    """[·retrieved node_142] / [·inferred] / [·absent] markers."""
    kind: str       # "retrieved" | "inferred" | "absent"
    node_id: Optional[str] = None


@dataclass
class NarrationResult:
    text: str
    voice_pass: bool
    confidence_labels: list[ConfidenceLabel] = field(default_factory=list)
    past_position_conflict: bool = False
    persona_violated: bool = False
    _voice_pass_detail: Optional[VoicePassResult] = None


class NarrationLayer:
    """Post-generation voice/persona check.

    MVP body: voice-pass only. Per-class variants (position conflict, persona
    enforcement, confidence injection) are scaffolded — fields exist on the
    result so callers can read them, but population is deferred.
    """

    def wrap(
        self,
        raw_output: str,
        context: NarrationContext,
        retrieved_nodes: Optional[list] = None,
        autonomy_mode: Any = None,
    ) -> NarrationResult:
        vp = check_voice_pass(raw_output)
        return NarrationResult(
            text=raw_output,
            voice_pass=vp.passed,
            confidence_labels=[],
            past_position_conflict=False,
            persona_violated=False,
            _voice_pass_detail=vp,
        )
