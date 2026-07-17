"""
CuriosityBuffer — Luna's internal question holding pattern.

Instead of asking questions immediately, Luna accumulates curiosities
from signals the engine already produces (memory gaps, new topics,
user ambiguity). When enough curiosities have developed over several
turns, the buffer synthesizes them into a single, well-formed question
directive injected into the prompt.

Zero LLM calls. Pure signal accumulation + heuristic synthesis.
Session-scoped. Resets each session.

> "Judge a man by his questions rather than by his answers." — Voltaire
"""

from dataclasses import dataclass, field
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class CuriosityEntry:
    """A single thing Luna is curious about but has not asked."""

    signal: str          # Source: "memory_gap", "new_topic", "entity_unknown", "user_ambiguity"
    question: str        # Natural language curiosity
    source_turn: int     # Turn number when this arose
    priority: float      # 0.0-1.0
    suppressed: bool = False   # Auto-suppressed after aging
    asked: bool = False        # Surfaced in a synthesis


@dataclass
class CuriosityBuffer:
    """
    Luna's internal question holding pattern.

    Accumulates curiosities from signals the engine already produces.
    Periodically synthesizes them into a single, well-formed question
    directive when conditions are right (the "ripeness" heuristic).

    Session-scoped. Resets each session.
    """

    entries: list[CuriosityEntry] = field(default_factory=list)

    # Tracking
    _last_question_turn: int = 0
    _total_surfaced: int = 0
    _turn_count: int = 0

    # ── Tuning Constants ───────────────────────────────────────────
    MAX_ENTRIES: int = 8
    MIN_TURNS_BETWEEN_QUESTIONS: int = 4
    RIPENESS_THRESHOLD: float = 0.6
    MIN_ENTRIES_TO_SYNTHESIZE: int = 2
    MAX_QUESTIONS_PER_SESSION: int = 5
    SUPPRESSION_AGE: int = 6         # Turns before low-priority entries auto-suppress
    SUPPRESSION_PRIORITY_CAP: float = 0.4  # Below this priority → eligible for suppression

    # ── Ingestion ──────────────────────────────────────────────────

    def ingest(self, signal: str, question: str, priority: float) -> None:
        """
        Add a curiosity entry. Drop lowest priority if buffer overflows.

        Called by Director/assembler at known signal points.
        """
        priority = max(0.0, min(1.0, priority))
        entry = CuriosityEntry(
            signal=signal,
            question=question,
            source_turn=self._turn_count,
            priority=priority,
        )
        self.entries.append(entry)
        logger.debug(
            "[CURIOSITY] Ingested: signal=%s priority=%.2f turn=%d — %s",
            signal, priority, self._turn_count, question[:60],
        )

        # Overflow: drop lowest-priority non-asked entry
        if len(self.entries) > self.MAX_ENTRIES:
            active = [e for e in self.entries if not e.asked]
            if active:
                weakest = min(active, key=lambda e: e.priority)
                self.entries.remove(weakest)
                logger.debug("[CURIOSITY] Dropped lowest-priority entry: %s", weakest.question[:40])

    # ── Tick (Aging + Auto-Suppression) ────────────────────────────

    def tick(self, turn: int) -> None:
        """
        Called once per cognitive cycle. Ages entries and auto-suppresses
        old low-priority curiosities (Luna decides they weren't worth asking).
        """
        self._turn_count = turn

        for entry in self.entries:
            if entry.suppressed or entry.asked:
                continue
            age = turn - entry.source_turn
            if age >= self.SUPPRESSION_AGE and entry.priority < self.SUPPRESSION_PRIORITY_CAP:
                entry.suppressed = True
                logger.debug(
                    "[CURIOSITY] Auto-suppressed (age=%d, priority=%.2f): %s",
                    age, entry.priority, entry.question[:40],
                )

    # ── Ripeness Heuristic ─────────────────────────────────────────

    @property
    def _active_entries(self) -> list[CuriosityEntry]:
        """Non-suppressed, non-asked entries."""
        return [e for e in self.entries if not e.suppressed and not e.asked]

    def is_ripe(self) -> bool:
        """
        Check if the buffer should surface a question.

        All conditions must be true:
        1. Enough active entries (≥ MIN_ENTRIES_TO_SYNTHESIZE)
        2. Sufficient spacing since last question (≥ MIN_TURNS_BETWEEN_QUESTIONS)
        3. Average priority of active entries exceeds threshold
        4. Haven't hit session cap
        """
        active = self._active_entries
        if len(active) < self.MIN_ENTRIES_TO_SYNTHESIZE:
            return False

        if self._turn_count - self._last_question_turn < self.MIN_TURNS_BETWEEN_QUESTIONS:
            return False

        avg_priority = sum(e.priority for e in active) / len(active)
        if avg_priority < self.RIPENESS_THRESHOLD:
            return False

        if self._total_surfaced >= self.MAX_QUESTIONS_PER_SESSION:
            return False

        return True

    # ── Synthesis ──────────────────────────────────────────────────

    def synthesize(self) -> Optional[str]:
        """
        Merge top curiosities into a single question directive.

        Returns natural-language directive or None if not ripe.
        Marks consumed entries as asked. Updates tracking.
        """
        if not self.is_ripe():
            return None

        # Take top 2-3 by priority
        active = sorted(self._active_entries, key=lambda e: e.priority, reverse=True)
        to_surface = active[:3]

        # Build curiosity list
        curiosity_lines = []
        for entry in to_surface:
            curiosity_lines.append(f"- {entry.question}")
            entry.asked = True

        # Update tracking
        self._last_question_turn = self._turn_count
        self._total_surfaced += 1

        logger.info(
            "[CURIOSITY] Synthesized question directive (%d curiosities, surfaced #%d)",
            len(to_surface), self._total_surfaced,
        )

        return "\n".join(curiosity_lines)

    # ── Prompt Injection ───────────────────────────────────────────

    def to_prompt_block(self) -> Optional[str]:
        """
        Format for injection into system prompt.

        Returns None if not ripe. Otherwise returns the synthesized
        question directive wrapped in a guiding frame.
        """
        curiosity_lines = self.synthesize()
        if not curiosity_lines:
            return None

        return (
            "## Internal Curiosity (synthesized)\n"
            "You've been holding a few things you're curious about:\n"
            f"{curiosity_lines}\n\n"
            "If the conversation reaches a natural pause or transition, weave ONE concise, "
            "genuine question that addresses these. Do not ask more than one question. "
            "If the moment isn't right, continue without asking."
        )

    # ── External Question Tracking ─────────────────────────────────

    def record_question_asked(self, turn: int) -> None:
        """
        Called when Luna asks a question (even organically, detected by '?' in response).
        Updates spacing tracker so the ripeness heuristic accounts for it.
        """
        self._last_question_turn = turn
        logger.debug("[CURIOSITY] Recorded organic question at turn %d", turn)

    # ── Reset ──────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all state for new session."""
        self.entries.clear()
        self._last_question_turn = 0
        self._total_surfaced = 0
        self._turn_count = 0
        logger.debug("[CURIOSITY] Buffer reset")

    # ── Diagnostics ────────────────────────────────────────────────

    def get_summary(self) -> dict:
        """Diagnostic summary for debugging / Guardian panel."""
        active = self._active_entries
        return {
            "total_entries": len(self.entries),
            "active_entries": len(active),
            "suppressed": sum(1 for e in self.entries if e.suppressed),
            "asked": sum(1 for e in self.entries if e.asked),
            "is_ripe": self.is_ripe(),
            "last_question_turn": self._last_question_turn,
            "total_surfaced": self._total_surfaced,
            "turn_count": self._turn_count,
        }
