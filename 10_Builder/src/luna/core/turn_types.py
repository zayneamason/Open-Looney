"""
Turn-type taxonomy (Voice v2.0 — Phase 1 Step 1).

Defines the `TurnType` enum used throughout the Luna Engine to classify rows in
`conversation_turns`. Consumers:
  - `src/luna/context/assembler.py` — dispatches in `_build_messages()` so
    interrupted-exchange triplets collapse into a single narrative message.
  - Step 4 (write sites in memory.py / history_manager.py) will begin tagging
    new turns with the appropriate TurnType.value.

Reference: Docs/Design/SystemsArchitecture/VOICE_SYSTEM_ARCHITECTURE.md sections
B3.1 (turn-type taxonomy) and B5.2 (render-path dispatch).

Case decision (2026-04-23):
    All TurnType values are UPPERCASE strings. This matches:
      1. The `DEFAULT 'NORMAL_USER_TURN'` literal stored by
         `migrations/006_turn_type.sql`, so enum.value compares directly
         against the DB row without a case-folding step.
      2. The spec's consumer treatment table (B3.1) and pseudocode (B6.4),
         which use UPPERCASE throughout.
    A previous draft handoff used lowercase for the enum values; we went with
    UPPERCASE to keep migration + enum + assembler in one canonical form.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional


class TurnType(str, Enum):
    """Classification of a `conversation_turns` row.

    Inherits `str` so `TurnType.NORMAL_USER_TURN == "NORMAL_USER_TURN"` holds
    (the DB stores the `.value` as a plain TEXT column).
    """

    # Standard turns.
    NORMAL_USER_TURN = "NORMAL_USER_TURN"
    NORMAL_ASSISTANT_TURN = "NORMAL_ASSISTANT_TURN"

    # Interrupted-exchange triplet (written in order 1 → 2 → 3 by the voice
    # pipeline when the user cuts in on Luna mid-utterance).
    PARTIAL_INTERRUPTED = "PARTIAL_INTERRUPTED"       # Luna's truncated utterance
    INTERRUPT_UTTERANCE = "INTERRUPT_UTTERANCE"       # user's interrupting input
    RESUMPTION_RESPONSE = "RESUMPTION_RESPONSE"       # Luna's follow-up response

    @classmethod
    def default_for_role(cls, role: str) -> "TurnType":
        """Return the default turn type for a raw conversation role.

        Used by write sites (Step 4) to classify a turn when no explicit
        TurnType is supplied. `'assistant'` → NORMAL_ASSISTANT_TURN; everything
        else (user, system, unknown strings, empty) → NORMAL_USER_TURN.
        """
        if isinstance(role, str) and role.strip().lower() == "assistant":
            return cls.NORMAL_ASSISTANT_TURN
        return cls.NORMAL_USER_TURN

    @classmethod
    def normalize_for_role(
        cls,
        role: str,
        turn_type: Optional[str | "TurnType"],
    ) -> "TurnType":
        """Resolve a stored turn_type with role-safe guards.

        This keeps special Voice v2.0 turn types intact while preventing normal
        role mismatches (`assistant` + `NORMAL_USER_TURN`, and vice versa).
        Unknown or empty values fall back to `default_for_role(role)`.
        """
        is_assistant = isinstance(role, str) and role.strip().lower() == "assistant"

        if turn_type is None:
            candidate = cls.default_for_role(role)
        elif isinstance(turn_type, cls):
            candidate = turn_type
        elif isinstance(turn_type, str):
            raw = turn_type.strip().upper()
            candidate = cls.__members__.get(raw, cls.default_for_role(role))
        else:
            candidate = cls.default_for_role(role)

        if is_assistant and candidate is cls.NORMAL_USER_TURN:
            return cls.NORMAL_ASSISTANT_TURN
        if not is_assistant and candidate is cls.NORMAL_ASSISTANT_TURN:
            return cls.NORMAL_USER_TURN
        return candidate


# Canonical ordering of the interrupted-exchange triplet. The assembler's
# `_detect_interrupted_triplet` helper walks the history looking for three
# consecutive turns whose turn_types match this tuple in order; when it finds
# them it collapses the three into one narrative message (see B5.2).
INTERRUPTED_EXCHANGE_TRIPLET: tuple[TurnType, TurnType, TurnType] = (
    TurnType.PARTIAL_INTERRUPTED,
    TurnType.INTERRUPT_UTTERANCE,
    TurnType.RESUMPTION_RESPONSE,
)


@dataclass(frozen=True)
class LogicalTurn:
    """Voice v2.0 Phase 1 Step 5 — coherent narrative unit assembled from a
    3-turn interrupted exchange.

    Temporal layers are explicitly labeled so Scribe can extract from
    user-sourced layers only (`user_original`, `user_interrupt`) and skip
    the assistant-sourced, potentially severed ones (`luna_partial`,
    `luna_resumption`). See VOICE_SYSTEM_ARCHITECTURE.md B6.4 / B6.6.
    """

    type: Literal["interrupted_exchange"]
    user_original: str                  # user input that preceded the partial
    luna_partial: list[str]             # chunks_delivered — truncated utterance
    interrupt_type: Literal["ADDITIVE", "REDIRECTING", "CLARIFYING"]
    user_interrupt: str                 # interrupt utterance verbatim
    luna_resumption: str                # post-interrupt continuation
    exchange_closed: bool               # True once resumption was delivered

    # Temporal anchors — rowids into conversation_turns.
    partial_turn_id: int
    interrupt_turn_id: int
    resumption_turn_id: int

    # Phase 2 hook (C1.4). Always None in Phase 1.
    surfaced_curiosity_id: Optional[str] = None


__all__ = ["TurnType", "LogicalTurn", "INTERRUPTED_EXCHANGE_TRIPLET"]
