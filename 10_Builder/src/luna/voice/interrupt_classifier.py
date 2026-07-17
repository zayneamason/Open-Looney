"""
V1 interrupt classifier — heuristic rules only. No ML, no LLM.

See VOICE_SYSTEM_ARCHITECTURE.md B3.3 for the rule table and the
force-resolved ambiguous-default behavior (→ REDIRECTING, safer drop).

Phase 2+ horizons (D2.1):
- Thinking-channel input for better REDIRECTING/CLARIFYING disambiguation
- ML classifier trained on labeled production interrupts
"""
from __future__ import annotations

from typing import Set

from luna.voice.entity_extraction import has_new_entity
from luna.voice.interrupt_models import (
    InterruptClassification,
    InterruptPayload,
    InterruptType,
)

DEFAULT_STOP_KEYWORDS: Set[str] = {
    "stop",
    "cancel",
    "wait",
    "hold on",
    "nevermind",
    "never mind",
}

_ADDITIVE_LEADS = ("also", "and", "another", "plus")
_CLARIFYING_LEADS = ("no", "actually", "i meant", "correction")
_QUESTION_LEADS = (
    "what",
    "who",
    "why",
    "how",
    "when",
    "where",
    "is",
    "are",
    "do",
    "does",
    "can",
    "could",
    "would",
)


def classify(
    payload: InterruptPayload,
    turn_context: dict,
    stop_keywords: Set[str] = DEFAULT_STOP_KEYWORDS,
) -> InterruptClassification:
    """Classify an interrupt. First rule that matches wins.

    Rules (B3.3):
      1. Empty utterance or stop keyword → CANCEL
      2. Leading additive marker (also/and/another/plus) → ADDITIVE
      3. Leading clarifying marker (no/actually/i meant/correction) → CLARIFYING
      4. Leading question word → CLARIFYING (no new entity)
                              → REDIRECTING (new entity)
      5. New entity in utterance → REDIRECTING
      6. Ambiguous → REDIRECTING (force-resolved default, confidence < 0.7)
    """
    utterance = payload.user_utterance.strip()
    lower = utterance.lower()

    if not utterance:
        return InterruptClassification(
            type=InterruptType.CANCEL,
            confidence=0.95,
            evidence="rule_1: empty utterance (silence)",
        )

    for keyword in stop_keywords:
        if (
            lower == keyword
            or lower.startswith(keyword + " ")
            or lower.endswith(" " + keyword)
        ):
            return InterruptClassification(
                type=InterruptType.CANCEL,
                confidence=0.90,
                evidence=f"rule_1: stop keyword matched: {keyword!r}",
            )

    tokens = lower.split()
    first_token = tokens[0] if tokens else ""

    if first_token in _ADDITIVE_LEADS:
        return InterruptClassification(
            type=InterruptType.ADDITIVE,
            confidence=0.85,
            evidence=f"rule_2: leading additive marker: {first_token!r}",
        )

    for marker in _CLARIFYING_LEADS:
        if lower == marker or lower.startswith(marker + " "):
            return InterruptClassification(
                type=InterruptType.CLARIFYING,
                confidence=0.85,
                evidence=f"rule_3: leading clarifying marker: {marker!r}",
            )

    if first_token in _QUESTION_LEADS:
        if has_new_entity(utterance, turn_context):
            return InterruptClassification(
                type=InterruptType.REDIRECTING,
                confidence=0.75,
                evidence=(
                    f"rule_4: question-word {first_token!r} + new entity in utterance"
                ),
            )
        return InterruptClassification(
            type=InterruptType.CLARIFYING,
            confidence=0.75,
            evidence=f"rule_4: question-word {first_token!r} without new entity",
        )

    if has_new_entity(utterance, turn_context):
        return InterruptClassification(
            type=InterruptType.REDIRECTING,
            confidence=0.70,
            evidence="rule_5: new entity detected in utterance",
        )

    return InterruptClassification(
        type=InterruptType.REDIRECTING,
        confidence=0.45,
        evidence="rule_6: ambiguous — defaulting to REDIRECTING (safer drop)",
    )
