"""
Conversation Ring Buffer — Guaranteed working memory.

This buffer holds recent conversation turns and CANNOT be displaced
by Memory Matrix retrieval. It is structurally prior to retrieval
in the context building process.

Key property: Ring fills FIRST. Retrieval gets leftovers. History can never be displaced.
"""

from collections import deque
from typing import List, Dict, Optional, Set
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class Turn:
    """A single conversation turn."""
    role: str  # "user" or "assistant"
    content: str

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


class ConversationRing:
    """
    Fixed-size ring buffer for conversation history.

    Guarantees:
    - Last N turns are always available
    - O(1) insert and eviction
    - Cannot be displaced by retrieval
    - FIFO eviction (oldest falls off naturally)

    This is the SINGLE SOURCE OF TRUTH for recent conversation.
    Both local and delegated paths read from this buffer.
    """

    def __init__(self, max_turns: int = 20):
        """
        Args:
            max_turns: Maximum turns to retain (default 20 = 10 exchanges)
        """
        self._buffer: deque = deque(maxlen=max_turns)
        self._max_turns = max_turns
        logger.info(f"[RING] Initialized with max_turns={max_turns}")

    def add(self, role: str, content: str) -> None:
        """Add a turn to the buffer. Oldest evicted if full."""
        self._buffer.append(Turn(role=role, content=content))
        logger.debug(f"[RING] Added {role} turn, buffer size: {len(self._buffer)}")

    def add_user(self, content: str) -> None:
        """Convenience: add a user turn."""
        self.add("user", content)

    def add_assistant(self, content: str) -> None:
        """Convenience: add an assistant turn."""
        self.add("assistant", content)

    def get_turns(self) -> List[Turn]:
        """Get all turns in chronological order."""
        return list(self._buffer)

    def get_as_dicts(self) -> List[Dict[str, str]]:
        """Get turns as list of dicts (for LLM messages array)."""
        return [t.to_dict() for t in self._buffer]

    def contains(self, text: str, case_sensitive: bool = False) -> bool:
        """Check if any turn contains the given text."""
        search = text if case_sensitive else text.lower()
        for turn in self._buffer:
            content = turn.content if case_sensitive else turn.content.lower()
            if search in content:
                return True
        return False

    def contains_any(self, texts: List[str], case_sensitive: bool = False) -> bool:
        """Check if any of the given texts appear in recent history."""
        return any(self.contains(t, case_sensitive) for t in texts)

    def contains_entity(self, entity_name: str) -> bool:
        """Check if an entity was mentioned recently."""
        return self.contains(entity_name, case_sensitive=False)

    def get_mentioned_topics(self) -> Set[str]:
        """
        Extract likely topics/entities from recent history.
        Simple implementation - can be enhanced with NER.
        """
        # For now, just return unique words > 4 chars that appear capitalized
        topics = set()
        for turn in self._buffer:
            words = turn.content.split()
            for word in words:
                clean = word.strip(".,!?\"'()[]")
                if len(clean) > 4 and clean[0].isupper():
                    topics.add(clean.lower())
        return topics

    def format_for_prompt(
        self,
        user_name: str = "User",
        assistant_name: str = "Luna"
    ) -> str:
        """
        Format buffer for injection into system prompt.

        Returns:
            Formatted string with turn markers
        """
        if not self._buffer:
            return "(No conversation history yet)"

        lines = []
        for i, turn in enumerate(self._buffer):
            name = user_name if turn.role == "user" else assistant_name
            lines.append(f"[{i+1}] {name}: {turn.content}")

        return "\n".join(lines)

    def get_last_n(self, n: int) -> List[Turn]:
        """Get the last N turns."""
        turns = list(self._buffer)
        return turns[-n:] if len(turns) >= n else turns

    def get_last_user_message(self) -> Optional[str]:
        """Get the most recent user message."""
        for turn in reversed(list(self._buffer)):
            if turn.role == "user":
                return turn.content
        return None

    def get_last_assistant_message(self) -> Optional[str]:
        """Get the most recent assistant message."""
        for turn in reversed(list(self._buffer)):
            if turn.role == "assistant":
                return turn.content
        return None

    def clear(self) -> None:
        """Clear all history (use sparingly — e.g., session reset)."""
        self._buffer.clear()
        logger.info("[RING] Buffer cleared")

    def __len__(self) -> int:
        return len(self._buffer)

    def __bool__(self) -> bool:
        return len(self._buffer) > 0

    def __repr__(self) -> str:
        return f"ConversationRing(turns={len(self._buffer)}, max={self._max_turns})"

    def __iter__(self):
        """Allow iteration over turns."""
        return iter(self._buffer)
