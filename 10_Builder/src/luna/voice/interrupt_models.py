"""
Canonical data contracts for Luna's conversational interrupt layer.

See VOICE_SYSTEM_ARCHITECTURE.md B3.2 (InterruptPayload + ResponseSnapshot),
B3.3 (InterruptClassification + InterruptType), and B3.4/B3.5 for the
Step-4 resumption contracts.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


@dataclass(frozen=True)
class ResponseSnapshot:
    """Immutable record of response state at interrupt time.

    chunks_delivered is audio that already played — Luna cannot unsay it.
    Resumption logic MUST treat delivered audio as immutable and MUST NOT
    feed it back to the LLM as "things to say" (B1 principle 4).
    """

    chunks_delivered: list[str] = field(default_factory=list)
    chunks_pending: list[str] = field(default_factory=list)
    full_intent: str = ""
    turn_context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class InterruptPayload:
    """Carried by USER_INTERRUPT events with structured barge-in context.

    Assembled when user_speaking becomes concurrent with luna_speaking
    (see B2.3 state transition).
    """

    user_utterance: str
    response_snapshot: ResponseSnapshot
    interrupt_at_chunk: int


class InterruptType(Enum):
    """4-value classification output. Drives ResumptionType in Step 4."""

    ADDITIVE = "additive"
    REDIRECTING = "redirecting"
    CLARIFYING = "clarifying"
    CANCEL = "cancel"


@dataclass(frozen=True)
class InterruptClassification:
    """Output of the V1 heuristic classifier.

    Pure function of payload + turn_context + stop-keyword set. No I/O.
    """

    type: InterruptType
    confidence: float
    evidence: str


# =============================================================================
# Step 4 — ResumptionStrategy + InterruptContextBlock (B3.4, B3.5)
# =============================================================================


class ResumptionType(Enum):
    """Director's output shape. ABORT is an escape hatch routing to the
    existing CANCEL path (B2.6); the three non-ABORT shapes are the real
    design surface. See B3.4."""

    INCORPORATE_AND_CONTINUE = "incorporate_and_continue"  # ADDITIVE
    PIVOT = "pivot"                                        # REDIRECTING
    CORRECT_AND_RESUME = "correct_and_resume"              # CLARIFYING
    ABORT = "abort"                                        # CANCEL escape


@dataclass(frozen=True)
class ResumptionStrategy:
    """What Director does with the classification + snapshot. See B3.4."""

    type: ResumptionType
    already_said: list[str]   # compressed "things delivered" (<=100 tokens)
    new_input: str            # interrupt utterance, verbatim
    drop_pending: bool        # whether chunks_pending was discarded


@dataclass(frozen=True)
class InterruptContextBlock:
    """Budget-capped prompt injection for Layer 6.0+ on resumption.

    Assembler enforces MAX_TOKENS at assembly time via the compression
    fallback (B3.5):
      1. Render full → over cap? drop delivered_summary
      2. Still over? drop pending_dropped flag
      3. Still over → hard assert (upstream compression needed)

    CANCEL classifications never produce a block (ABORT routes to legacy
    abort path per B2.6).
    """

    delivered_summary: str    # compressed <=100 tokens, not verbatim
    pending_dropped: Optional[bool]  # True/False, or None after compression
    user_utterance: str       # verbatim — non-negotiable
    classification: str       # "ADDITIVE" | "REDIRECTING" | "CLARIFYING"

    MAX_TOKENS: int = 150     # class-level hard ceiling

    def without_delivered_summary(self) -> "InterruptContextBlock":
        """Compression step 1: drop the delivered_summary field."""
        return dataclasses.replace(self, delivered_summary="")

    def without_pending_dropped(self) -> "InterruptContextBlock":
        """Compression step 2: drop the pending_dropped flag.

        Classification alone is sufficient signal to the LLM; dropping this
        flag frees a few tokens as a last-ditch measure before the hard
        assert.
        """
        return dataclasses.replace(self, pending_dropped=None)


# Mapping per B3.4. Values are (ResumptionType, drop_pending_default).
# CANCEL's drop_pending is None since ABORT is not a real resumption shape.
CLASSIFICATION_TO_STRATEGY: dict[InterruptType, tuple[ResumptionType, Optional[bool]]] = {
    InterruptType.ADDITIVE:    (ResumptionType.INCORPORATE_AND_CONTINUE, False),
    InterruptType.REDIRECTING: (ResumptionType.PIVOT,                    True),
    InterruptType.CLARIFYING:  (ResumptionType.CORRECT_AND_RESUME,       True),
    InterruptType.CANCEL:      (ResumptionType.ABORT,                    None),
}
