"""
Semantic Chunker for Luna Engine
=================================

Splits conversations into extractable chunks while preserving context.
Chunks are sized for efficient extraction while maintaining semantic coherence.
"""

from dataclasses import dataclass
from typing import Optional
import re
import uuid

from .types import Chunk


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for text.

    Uses simple heuristic: ~4 characters per token on average.
    This is faster than calling a tokenizer and accurate enough
    for chunking purposes.
    """
    return len(text) // 4


@dataclass
class Turn:
    """A single conversation turn for chunking."""
    id: int
    role: str  # "user" or "assistant"
    content: str
    tokens: int = 0

    def __post_init__(self):
        if self.tokens == 0:
            self.tokens = estimate_tokens(self.content)


class SemanticChunker:
    """
    Splits conversations into semantic chunks for extraction.

    Chunking strategy:
    - Target size: 200-500 tokens (fits in single extraction call)
    - Overlap: 50 tokens (context continuity)
    - Boundaries: Topic shifts, speaker changes
    """

    def __init__(
        self,
        target_tokens: int = 350,
        max_tokens: int = 500,
        overlap_tokens: int = 50,
        min_tokens: int = 15,
    ):
        self.target_tokens = target_tokens
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.min_tokens = min_tokens

        # Topic shift detection patterns
        self._topic_patterns = [
            r"(?i)^(anyway|so|now|moving on|next|also|by the way)",
            r"(?i)^(speaking of|regarding|about|on the topic)",
            r"(?i)^(let me|i want to|can we|could you)",
        ]

    def chunk_turns(
        self,
        turns: list[Turn],
        source_id: str = "",
    ) -> list[Chunk]:
        """
        Split conversation turns into semantic chunks.

        Args:
            turns: List of conversation turns
            source_id: ID to tag chunks with (e.g., session_id)

        Returns:
            List of Chunk objects ready for extraction
        """
        if not turns:
            return []

        chunks = []
        current_content = []
        current_tokens = 0
        current_turn_ids = []

        for turn in turns:
            # Check for topic shift
            if current_content and self._is_topic_shift(current_content, turn):
                # Finalize current chunk if it has enough content
                if current_tokens >= self.min_tokens:
                    chunks.append(self._create_chunk(
                        current_content,
                        current_tokens,
                        current_turn_ids,
                        source_id,
                    ))

                    # Start new chunk with overlap
                    overlap_content, overlap_tokens, overlap_ids = self._get_overlap(
                        current_content, current_turn_ids
                    )
                    current_content = overlap_content
                    current_tokens = overlap_tokens
                    current_turn_ids = overlap_ids
                else:
                    # Not enough content, keep building
                    pass

            # Add turn to current chunk
            turn_text = f"{turn.role}: {turn.content}"
            current_content.append(turn_text)
            current_tokens += turn.tokens
            current_turn_ids.append(turn.id)

            # Check size limit
            if current_tokens >= self.max_tokens:
                chunks.append(self._create_chunk(
                    current_content,
                    current_tokens,
                    current_turn_ids,
                    source_id,
                ))

                # Start new chunk with overlap
                overlap_content, overlap_tokens, overlap_ids = self._get_overlap(
                    current_content, current_turn_ids
                )
                current_content = overlap_content
                current_tokens = overlap_tokens
                current_turn_ids = overlap_ids

        # Finalize remaining content
        if current_content and current_tokens >= self.min_tokens:
            chunks.append(self._create_chunk(
                current_content,
                current_tokens,
                current_turn_ids,
                source_id,
            ))

        return chunks

    def chunk_text(
        self,
        text: str,
        source_id: str = "",
    ) -> list[Chunk]:
        """
        Split raw text into chunks.

        For documents, files, or other non-conversation content.

        Args:
            text: Raw text to chunk
            source_id: ID to tag chunks with

        Returns:
            List of Chunk objects
        """
        if not text or not text.strip():
            return []

        # Split by paragraphs first
        paragraphs = re.split(r'\n\n+', text.strip())

        chunks = []
        current_content = []
        current_tokens = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_tokens = estimate_tokens(para)

            # If single paragraph exceeds max, split by sentences
            if para_tokens > self.max_tokens:
                if current_content:
                    chunks.append(self._create_chunk(
                        current_content,
                        current_tokens,
                        [],
                        source_id,
                    ))
                    current_content = []
                    current_tokens = 0

                # Split large paragraph
                sentence_chunks = self._chunk_large_paragraph(para, source_id)
                chunks.extend(sentence_chunks)
                continue

            # Check if adding paragraph exceeds limit
            if current_tokens + para_tokens > self.max_tokens:
                if current_content:
                    chunks.append(self._create_chunk(
                        current_content,
                        current_tokens,
                        [],
                        source_id,
                    ))
                    current_content = []
                    current_tokens = 0

            current_content.append(para)
            current_tokens += para_tokens

        # Finalize remaining
        if current_content:
            chunks.append(self._create_chunk(
                current_content,
                current_tokens,
                [],
                source_id,
            ))

        return chunks

    def _is_topic_shift(self, current_content: list[str], new_turn: Turn) -> bool:
        """
        Detect if new turn represents a topic shift.

        Heuristics:
        - Matches topic shift patterns
        - Speaker change after long silence (simulated by checking patterns)
        - Question after statements
        """
        text = new_turn.content.strip()

        # Check topic shift patterns
        for pattern in self._topic_patterns:
            if re.match(pattern, text):
                return True

        # Question after non-question content
        if text.endswith("?") and current_content:
            last = current_content[-1]
            if not last.endswith("?"):
                return True

        return False

    def _get_overlap(
        self,
        content: list[str],
        turn_ids: list[int],
    ) -> tuple[list[str], int, list[int]]:
        """
        Get overlap content for context continuity.

        Returns the last portion of content to carry over to new chunk.
        """
        if not content:
            return [], 0, []

        # Keep last item(s) up to overlap_tokens
        overlap_content = []
        overlap_tokens = 0
        overlap_ids = []

        for i in range(len(content) - 1, -1, -1):
            item_tokens = estimate_tokens(content[i])
            if overlap_tokens + item_tokens > self.overlap_tokens:
                break
            overlap_content.insert(0, content[i])
            overlap_tokens += item_tokens
            if turn_ids and i < len(turn_ids):
                overlap_ids.insert(0, turn_ids[i])

        return overlap_content, overlap_tokens, overlap_ids

    def _chunk_large_paragraph(
        self,
        paragraph: str,
        source_id: str,
    ) -> list[Chunk]:
        """Split a large paragraph by sentences."""
        # Simple sentence splitting
        sentences = re.split(r'(?<=[.!?])\s+', paragraph)

        chunks = []
        current_content = []
        current_tokens = 0

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            sent_tokens = estimate_tokens(sentence)

            if current_tokens + sent_tokens > self.max_tokens:
                if current_content:
                    chunks.append(self._create_chunk(
                        current_content,
                        current_tokens,
                        [],
                        source_id,
                    ))
                    current_content = []
                    current_tokens = 0

            current_content.append(sentence)
            current_tokens += sent_tokens

        if current_content:
            chunks.append(self._create_chunk(
                current_content,
                current_tokens,
                [],
                source_id,
            ))

        return chunks

    def _create_chunk(
        self,
        content: list[str],
        tokens: int,
        turn_ids: list[int],
        source_id: str,
    ) -> Chunk:
        """Create a Chunk object from content."""
        return Chunk(
            id=str(uuid.uuid4())[:8],
            content="\n".join(content),
            tokens=tokens,
            source_id=source_id,
            turn_ids=turn_ids,
        )
