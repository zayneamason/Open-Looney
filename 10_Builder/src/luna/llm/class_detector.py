"""Class detection for the LatencyGate.

MVP stub — wraps the existing IntentRouter and projects its 4-mode output
into the 10-class taxonomy that LatencyGate consumes.

The full keyword + Qwen 3B + speculative entity check (per
DESIGN_OpenQuestions_Resolution.md Q1) replaces this shim in a follow-up.
"""
from dataclasses import dataclass, field
from typing import Literal, Optional

from luna.context.modes import ResponseMode
from luna.llm.intent_router import IntentRouter

# Coarse projection: 4 IntentRouter modes → 10-class taxonomy.
_MODE_TO_CLASS = {
    ResponseMode.CHAT: 9,     # Conversation
    ResponseMode.RECALL: 2,   # Memory recall
    ResponseMode.REFLECT: 7,  # Opinion / judgment
    ResponseMode.ASSIST: 1,   # Factual / task
}


@dataclass
class ClassDetection:
    primary_class: int                                    # 1–10
    secondary_classes: list[int] = field(default_factory=list)
    verbs: list[str] = field(default_factory=list)
    verb_combo: Optional[str] = None
    confidence: float = 0.0
    method: Literal["keyword", "llm", "hybrid", "stub"] = "stub"
    latency_ms: float = 0.0
    prefetched_entities: list = field(default_factory=list)


class ClassDetector:
    """MVP stub. Wraps existing IntentRouter; LatencyGate-shaped output."""

    def __init__(self, intent_router: Optional[IntentRouter]):
        self._intent = intent_router

    async def detect(self, message: str, context=None) -> ClassDetection:
        if self._intent is None:
            return ClassDetection(primary_class=9, confidence=0.3, method="stub")

        ic = await self._intent.classify(message)
        return ClassDetection(
            primary_class=_MODE_TO_CLASS.get(ic.mode, 9),
            confidence=ic.confidence,
            method="stub",
        )
