"""
ConversationConsolidator — Voice v2.0 Phase 1 Step 5.

Standalone actor that detects the 3-turn interrupted-exchange pattern
(PARTIAL_INTERRUPTED → INTERRUPT_UTTERANCE → RESUMPTION_RESPONSE),
assembles a `LogicalTurn`, and dispatches it to Scribe for extraction.

Non-goal (B6.5): the Consolidator does NOT own thread policy. Librarian
retains SHIFT / AMEND / FLOW signal computation. The Consolidator's sole
job is coherent-turn assembly for Scribe.

See VOICE_SYSTEM_ARCHITECTURE.md B6 for the full contract.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from luna.actors.base import Actor, Message
from luna.core.turn_types import LogicalTurn, TurnType

if TYPE_CHECKING:
    from luna.engine import LunaEngine
    from luna.substrate.memory import Turn

logger = logging.getLogger(__name__)


class ConversationConsolidator(Actor):
    """Subscribes to `turn_completed` messages. Filters for
    RESUMPTION_RESPONSE trigger turns. Walks back two rows, detects the
    triplet, assembles a `LogicalTurn`, dispatches to Scribe.

    Idempotent: a dedup set keyed on (partial_id, interrupt_id,
    resumption_id) guards against double-dispatch if the same trigger
    fires twice.
    """

    def __init__(self, engine: Optional["LunaEngine"] = None):
        super().__init__("consolidator", engine)
        self._dispatched: set[tuple[int, int, int]] = set()

    async def handle(self, msg: Message) -> None:
        match msg.type:
            case "turn_completed":
                turn = msg.payload
                await self._maybe_consolidate(turn)
            case _:
                logger.debug(f"Consolidator: unknown message type: {msg.type}")

    async def _maybe_consolidate(self, turn) -> None:
        """Only RESUMPTION_RESPONSE turns trigger consolidation."""
        if turn is None:
            return
        turn_type = getattr(turn, "turn_type", None)
        if turn_type != TurnType.RESUMPTION_RESPONSE.value:
            return

        triplet = await self._find_triplet(turn)
        if triplet is None:
            logger.warning(
                f"[CONSOLIDATOR] orphan_resumption turn_id={turn.id}"
            )
            self._emit_event("consolidator.orphan_resumption", {
                "resumption_turn_id": turn.id,
                "reason": "preceding_triplet_not_matched",
            })
            return

        partial, utterance, resumption = triplet
        dedup_key = (partial.id, utterance.id, resumption.id)
        if dedup_key in self._dispatched:
            logger.debug(
                f"[CONSOLIDATOR] duplicate_dispatch_suppressed key={dedup_key}"
            )
            return

        logical_turn = self._assemble(partial, utterance, resumption)
        scribe = self.engine.get_actor("scribe") if self.engine else None
        if scribe is None:
            logger.error("[CONSOLIDATOR] Scribe actor unavailable; skipping dispatch")
            self._emit_event("consolidator.dispatch_failed", {
                "resumption_turn_id": resumption.id,
                "error": "scribe_unavailable",
            })
            return

        try:
            await scribe.mailbox.put(
                Message(
                    type="extract_logical_turn",
                    payload=logical_turn,
                    sender=self.name,
                )
            )
            self._dispatched.add(dedup_key)
            self._emit_event("consolidator.triplet_detected", {
                "partial_turn_id": partial.id,
                "interrupt_turn_id": utterance.id,
                "resumption_turn_id": resumption.id,
                "interrupt_type": logical_turn.interrupt_type,
            })
            self._emit_event("consolidator.dispatch_success", {
                "resumption_turn_id": resumption.id,
            })
        except Exception as e:
            logger.error(f"[CONSOLIDATOR] dispatch_failed triplet={dedup_key} error={e!r}")
            self._emit_event("consolidator.dispatch_failed", {
                "resumption_turn_id": resumption.id,
                "error": str(e),
            })

    async def _find_triplet(self, resumption):
        """Walk back two rows from the resumption, check pattern."""
        matrix = self._get_matrix()
        if matrix is None:
            return None
        preceding = await matrix.get_preceding_turns(
            session_id=resumption.session_id,
            before_turn_id=resumption.id,
            limit=2,
        )
        if len(preceding) < 2:
            return None
        # get_preceding_turns returns reverse-chronological (most recent first)
        utterance_candidate = preceding[0]
        partial_candidate = preceding[1]
        if (
            getattr(partial_candidate, "turn_type", None) == TurnType.PARTIAL_INTERRUPTED.value
            and getattr(utterance_candidate, "turn_type", None) == TurnType.INTERRUPT_UTTERANCE.value
        ):
            return (partial_candidate, utterance_candidate, resumption)
        return None

    def _assemble(self, partial, utterance, resumption) -> LogicalTurn:
        """Build the LogicalTurn from the three rows."""
        interrupt_type_raw = ""
        if isinstance(utterance.metadata, dict):
            interrupt_type_raw = str(utterance.metadata.get("classification", "")).upper()
        if interrupt_type_raw not in ("ADDITIVE", "REDIRECTING", "CLARIFYING"):
            logger.warning(
                f"[CONSOLIDATOR] unknown classification on turn_id={utterance.id}: "
                f"{interrupt_type_raw!r}; defaulting to REDIRECTING"
            )
            interrupt_type_raw = "REDIRECTING"

        return LogicalTurn(
            type="interrupted_exchange",
            user_original="",   # filled later if we add user_original walkback
            luna_partial=[partial.content] if partial.content else [],
            interrupt_type=interrupt_type_raw,
            user_interrupt=utterance.content,
            luna_resumption=resumption.content,
            exchange_closed=True,
            partial_turn_id=partial.id,
            interrupt_turn_id=utterance.id,
            resumption_turn_id=resumption.id,
            surfaced_curiosity_id=None,
        )

    async def assemble_logical_turn(self, partial, utterance, resumption) -> LogicalTurn:
        """Public entry point so tests + integration fixtures can call without
        synthesizing a `turn_completed` message."""
        return self._assemble(partial, utterance, resumption)

    # ── helpers ─────────────────────────────────────────────────

    def _get_matrix(self):
        if self.engine is None:
            return None
        matrix_actor = self.engine.get_actor("matrix")
        return matrix_actor.matrix if matrix_actor is not None else None

    def _emit_event(self, event_type: str, payload: dict) -> None:
        """Structured log per C3.3 process-channel catalog."""
        logger.info(f"[CONSOLIDATOR-EVENT] {event_type} {payload}")
