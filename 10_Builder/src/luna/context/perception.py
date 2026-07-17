"""
UserPerceptionField — Luna's observation layer for reading the room.

NOT a classifier. NOT a state machine. An observation accumulator.
Extracts behavioral signals from user messages, pairs each observation
with its trigger context (what Luna did right before), and formats
them for prompt injection.

Zero LLM calls. Pure signal extraction.
Session-scoped. Resets each session.
"""

from dataclasses import dataclass, field
from typing import Optional
import logging
import re

logger = logging.getLogger(__name__)


@dataclass
class Observation:
    """A single thing Luna noticed about the user."""

    signal: str          # What changed
                         # e.g. "length_shift", "correction_detected", "question_density"

    value: str           # The observation in natural language
                         # e.g. "Messages shortened from ~140 chars to ~35 chars"

    trigger: str         # What preceded / caused this
                         # e.g. "after Luna's 400-word technical explanation"

    turn: int            # When (turn number in session)
    confidence: float    # How clear is this signal (0.0-1.0)

    @property
    def paired(self) -> str:
        """Natural language: observation + trigger."""
        return f"{self.value} ({self.trigger})"


@dataclass
class PerceptionField:
    """
    Luna's observations about the current user, this session.

    NOT a state machine. NOT a classifier.
    An accumulator of paired observations.

    Resets each session. Does not persist to Matrix.
    (Future: Librarian could extract patterns across sessions)
    """

    observations: list[Observation] = field(default_factory=list)

    # Running signal state (for delta detection)
    _msg_lengths: list[int] = field(default_factory=list)
    _user_messages: list[str] = field(default_factory=list)
    _question_flags: list[bool] = field(default_factory=list)
    _terse_count: int = 0
    _correction_count: int = 0
    _last_luna_action: str = ""      # What Luna did last (for trigger context)
    _baseline_energy: Optional[dict] = None  # Energy markers from first 3 messages

    # Limits
    MAX_OBSERVATIONS: int = 8        # Cap what gets injected into prompt
    MIN_OBSERVATIONS_TO_INJECT: int = 2  # Need minimum signal before injecting

    def observe(self, obs: Observation) -> None:
        """Add an observation. Oldest drop when over limit."""
        self.observations.append(obs)
        if len(self.observations) > self.MAX_OBSERVATIONS:
            self.observations.pop(0)
        logger.debug("[PERCEPTION] %s (conf=%.2f, turn=%d)", obs.paired, obs.confidence, obs.turn)

    def to_prompt_block(self) -> Optional[str]:
        """
        Format for injection into system prompt.

        Returns None if insufficient observations.
        Returns formatted observation block if enough signal.
        """
        if len(self.observations) < self.MIN_OBSERVATIONS_TO_INJECT:
            return None  # Not enough signal yet

        recent = self.observations[-5:]  # Last 5 observations
        lines = [obs.paired for obs in recent]

        return (
            "## User Observation (this session)\n\n"
            + "\n".join(f"- {line}" for line in lines)
            + "\n\nThese are observations, not conclusions. "
            "Interpret them in context of what you know about this user."
        )

    def reset(self) -> None:
        """Reset for new session."""
        self.observations.clear()
        self._msg_lengths.clear()
        self._user_messages.clear()
        self._question_flags.clear()
        self._terse_count = 0
        self._correction_count = 0
        self._last_luna_action = ""
        self._baseline_energy = None

    # ── Main Ingestion ──────────────────────────────────────────

    def ingest(self, user_message: str, turn_number: int) -> None:
        """
        Process a user message and extract observations.

        Called once per turn, before prompt assembly.
        Zero LLM calls. Pure signal extraction.
        """
        msg_text = user_message.strip()
        msg_len = len(msg_text)
        msg_lower = msg_text.lower()

        self._msg_lengths.append(msg_len)
        self._user_messages.append(msg_text)

        # Detect if this is a question
        question_starters = ("what", "who", "where", "when", "why", "how", "is ", "are ", "can ", "do ", "does ")
        is_question = msg_text.endswith("?") or msg_lower.startswith(question_starters)
        self._question_flags.append(is_question)

        trigger = self._last_luna_action or "start of session"

        # ── Signal 1: Message Length Trajectory ──
        self._check_length_trajectory(trigger, turn_number)

        # ── Signal 2: Correction / Repetition ──
        self._check_corrections(msg_text, trigger, turn_number)

        # ── Signal 3: Question Density ──
        self._check_question_density(trigger, turn_number)

        # ── Signal 4: Brevity Signals ──
        self._check_brevity(msg_lower, trigger, turn_number)

        # ── Signal 5: Energy Markers ──
        self._check_energy_markers(msg_text, trigger, turn_number)

    # ── Luna Action Tracking ────────────────────────────────────

    def record_luna_action(self, luna_response: str) -> None:
        """
        Record a brief summary of Luna's response for trigger context.
        Called after LLM generation, before next turn.

        NOT an LLM call. Simple heuristic classification.
        """
        length = len(luna_response)
        has_question = "?" in luna_response
        has_code = "```" in luna_response

        if length < 100:
            action = "gave brief response"
        elif length < 300:
            action = "gave moderate response"
        elif has_code:
            action = f"gave {(length // 100) * 100}+ char technical response with code"
        else:
            action = f"gave {(length // 100) * 100}+ char explanation"

        if has_question:
            action += " and asked a question"

        self._last_luna_action = action

    # ── Signal Extractors ───────────────────────────────────────

    def _check_length_trajectory(self, trigger: str, turn: int) -> None:
        """Detect sustained message length changes."""
        if len(self._msg_lengths) < 4:
            return

        recent_avg = sum(self._msg_lengths[-3:]) / 3
        earlier_avg = sum(self._msg_lengths[:-3]) / max(len(self._msg_lengths) - 3, 1)

        if earlier_avg <= 0:
            return

        ratio = recent_avg / earlier_avg

        if ratio < 0.4:  # Dropped to less than 40%
            self.observe(Observation(
                signal="length_shift",
                value=f"Messages shortened from ~{int(earlier_avg)} to ~{int(recent_avg)} chars over last 3 turns",
                trigger=trigger,
                turn=turn,
                confidence=0.8,
            ))
        elif ratio > 2.0:  # Doubled
            self.observe(Observation(
                signal="length_shift",
                value=f"Messages expanding — ~{int(recent_avg)} chars, up from ~{int(earlier_avg)}",
                trigger=trigger,
                turn=turn,
                confidence=0.7,
            ))

    def _check_corrections(self, msg_text: str, trigger: str, turn: int) -> None:
        """
        Detect when user repeats or corrects something.

        Heuristic: >40% word overlap with user's message from 2-3 turns ago
        indicates restating/correcting. Excludes common stop words.
        """
        if len(self._user_messages) < 3:
            return

        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "can", "shall", "to", "of",
            "in", "for", "on", "with", "at", "by", "from", "it", "its",
            "this", "that", "these", "those", "i", "you", "we", "they",
            "me", "him", "her", "us", "them", "my", "your", "our", "and",
            "or", "but", "not", "no", "yes", "so", "if", "then", "just",
            "also", "about", "up", "out", "what", "how", "when", "where",
        }

        current_words = set(
            w.lower() for w in re.findall(r'\b\w+\b', msg_text)
            if len(w) > 2 and w.lower() not in stop_words
        )

        if not current_words:
            return

        # Check against messages from 2-3 turns ago
        for offset in [2, 3]:
            if len(self._user_messages) <= offset:
                continue

            prior_msg = self._user_messages[-(offset + 1)]  # +1 because current is already appended
            prior_words = set(
                w.lower() for w in re.findall(r'\b\w+\b', prior_msg)
                if len(w) > 2 and w.lower() not in stop_words
            )

            if not prior_words:
                continue

            overlap = len(current_words & prior_words)
            overlap_ratio = overlap / min(len(current_words), len(prior_words))

            if overlap_ratio > 0.4:
                self._correction_count += 1

                if self._correction_count == 1:
                    self.observe(Observation(
                        signal="correction_detected",
                        value="User rephrased or repeated a prior point",
                        trigger=trigger,
                        turn=turn,
                        confidence=0.75,
                    ))
                elif self._correction_count >= 2:
                    self.observe(Observation(
                        signal="correction_detected",
                        value=f"User has restated/corrected {self._correction_count} times this session",
                        trigger="Luna's responses may not be addressing the core ask",
                        turn=turn,
                        confidence=0.9,
                    ))
                break  # Only fire once per turn

    def _check_question_density(self, trigger: str, turn: int) -> None:
        """Detect sustained high question density."""
        if len(self._question_flags) < 4:
            return

        recent_q = sum(self._question_flags[-4:])

        if recent_q >= 3:
            self.observe(Observation(
                signal="question_density",
                value=f"High question density — {recent_q} of last 4 messages are questions",
                trigger=trigger,
                turn=turn,
                confidence=0.7,
            ))

        # Detect shift from questions to statements
        if len(self._question_flags) >= 6:
            earlier_q = sum(self._question_flags[-6:-3])
            recent_q_3 = sum(self._question_flags[-3:])
            if earlier_q >= 2 and recent_q_3 == 0:
                self.observe(Observation(
                    signal="question_density",
                    value="Shifted from questions to statements",
                    trigger=trigger,
                    turn=turn,
                    confidence=0.65,
                ))

    def _check_brevity(self, msg_lower: str, trigger: str, turn: int) -> None:
        """Detect terse acknowledgment patterns."""
        terse_markers = {
            "ok", "sure", "thanks", "got it", "yep", "yeah",
            "sounds good", "cool", "k", "fine", "right",
            "makes sense", "understood", "noted",
        }

        # Strip trailing punctuation for matching
        cleaned = msg_lower.rstrip(".!?,")

        if cleaned in terse_markers:
            self._terse_count += 1

            if self._terse_count >= 2:
                self.observe(Observation(
                    signal="terse_response",
                    value=f"{self._terse_count} terse acknowledgments in this session",
                    trigger=trigger,
                    turn=turn,
                    confidence=0.7 if self._terse_count == 2 else 0.85,
                ))
        else:
            # Non-terse message resets the counter (not accumulative across gaps)
            if self._terse_count > 0 and len(msg_lower) > 40:
                self._terse_count = 0

    def _check_energy_markers(self, msg_text: str, trigger: str, turn: int) -> None:
        """Detect energy changes via punctuation, caps, emoji."""
        exclamation_count = msg_text.count("!")
        caps_words = sum(1 for w in msg_text.split() if w.isupper() and len(w) > 1)
        # Simple emoji detection (common unicode ranges)
        emoji_count = sum(
            1 for c in msg_text
            if ord(c) > 0x1F300  # Rough emoji range start
        )

        energy = {
            "exclamations": exclamation_count,
            "caps": caps_words,
            "emoji": emoji_count,
        }

        # Establish baseline from first 3 messages
        if len(self._msg_lengths) <= 3:
            if self._baseline_energy is None:
                self._baseline_energy = {"exclamations": 0, "caps": 0, "emoji": 0}
            for k in energy:
                self._baseline_energy[k] = max(self._baseline_energy.get(k, 0), energy[k])
            return

        if self._baseline_energy is None:
            return

        # Detect significant changes from baseline
        baseline_total = sum(self._baseline_energy.values())
        current_total = sum(energy.values())

        if baseline_total == 0 and current_total >= 2:
            self.observe(Observation(
                signal="energy_markers",
                value="Energy markers appeared — exclamation marks, emoji, or emphasis",
                trigger=trigger,
                turn=turn,
                confidence=0.6,
            ))
        elif baseline_total >= 2 and current_total == 0 and turn > 8:
            self.observe(Observation(
                signal="energy_markers",
                value="Energy markers dropped — flat punctuation, no emoji",
                trigger=f"late in session (turn {turn})",
                turn=turn,
                confidence=0.5,
            ))
