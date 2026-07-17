"""
ResponseSegmenter — Voice v2.0 Phase 1 Step 2.

Bounded chunking for streaming TTS. Takes raw token stream from Director and
emits chunks that satisfy:

    min_words <= word_count <= max_words
    sentence_count <= max_sentences

Chunk emission fires on:
    1. Sentence terminator (`.`, `?`, `!`) followed by end-of-stream or whitespace,
       once min_words is met. Up to max_sentences per chunk.
    2. Secondary break markers (`—`, `– `, `- `) once min_words is met, used as a
       forced flush opportunity to keep latency low on long clauses.
    3. Hard word cap: when word count crosses max_words, emit immediately even
       without punctuation (prevents runaway chunks on technical prose).

Abbreviations and decimals that contain `.` mid-word do NOT trigger emission
because the regex anchor requires the terminator to be at end-of-buffer or
followed by whitespace.

`ResponseSnapshot` lives in `luna.voice.interrupt_models` since Step 3;
re-exported here for backward-compat with Step 2 importers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from luna.voice.interrupt_models import ResponseSnapshot  # re-export

__all__ = ["SegmenterConfig", "ResponseSegmenter", "ResponseSnapshot"]


# ── Config ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class SegmenterConfig:
    """Tunable bounds for chunk emission."""

    max_words: int = 30
    max_sentences: int = 2
    min_words: int = 8
    sentence_terminators: tuple = (".", "?", "!")
    secondary_markers: tuple = ("—", "– ", "- ")


# ── Segmenter ──────────────────────────────────────────────────


class ResponseSegmenter:
    """Bounded streaming chunker.

    Usage::

        seg = ResponseSegmenter(SegmenterConfig())
        for token in token_stream:
            chunk = seg.accept_token(token)
            if chunk is not None:
                synth_queue.put_nowait(chunk)
        tail = seg.flush()
        if tail is not None:
            synth_queue.put_nowait(tail)
    """

    def __init__(self, config: Optional[SegmenterConfig] = None):
        self._config = config or SegmenterConfig()
        self._buffer: str = ""
        self._sentence_count: int = 0
        self._chunk_index: int = 0
        # Precompile regex: terminator at end of buffer (possibly followed by
        # trailing whitespace). Anchored so `Dr.` mid-string does NOT match —
        # only end-of-buffer triggers.
        terminators = "".join(re.escape(t) for t in self._config.sentence_terminators)
        self._terminator_re = re.compile(rf"[{terminators}]\s*$")

    # ── Public API ────────────────────────────────────────────

    @property
    def chunk_index(self) -> int:
        """Number of chunks emitted so far."""
        return self._chunk_index

    def accept_token(self, token: str) -> Optional[str]:
        """Append token, possibly emit a bounded chunk.

        Returns the emitted chunk (stripped) or None if still buffering.
        """
        if not token:
            return None
        self._buffer += token
        return self._try_emit()

    def flush(self) -> Optional[str]:
        """Emit any remaining buffered text as a final chunk.

        Returns the emitted chunk (stripped) or None if buffer is empty / only
        whitespace. Never loses buffered text.
        """
        remaining = self._buffer.strip()
        if not remaining:
            self._buffer = ""
            self._sentence_count = 0
            return None
        self._buffer = ""
        self._sentence_count = 0
        self._chunk_index += 1
        return remaining

    def reset(self) -> None:
        """Clear all state. Used between responses."""
        self._buffer = ""
        self._sentence_count = 0
        self._chunk_index = 0

    # ── Internals ─────────────────────────────────────────────

    def _word_count(self, text: str) -> int:
        return len(text.split())

    def _try_emit(self) -> Optional[str]:
        """Check emission conditions in priority order."""
        cfg = self._config
        words = self._word_count(self._buffer)

        # 1. Hard word cap — emit even without punctuation.
        if words >= cfg.max_words:
            return self._emit_all()

        # Below min_words → keep buffering.
        if words < cfg.min_words:
            return None

        # 2. Sentence terminator at end of buffer.
        if self._terminator_re.search(self._buffer):
            self._sentence_count += 1
            if self._sentence_count >= cfg.max_sentences:
                return self._emit_all()
            # Check if we should continue accumulating another sentence within
            # the max_words budget. If next sentence would likely overflow,
            # emit now. Heuristic: words already >= max_words/2 → emit.
            if words >= cfg.max_words // 2:
                return self._emit_all()
            # Otherwise keep going; next sentence will join this chunk.
            return None

        # 3. Secondary break marker — emit if min_words met.
        for marker in cfg.secondary_markers:
            if self._buffer.rstrip().endswith(marker.rstrip()):
                return self._emit_all()

        return None

    def _emit_all(self) -> Optional[str]:
        """Emit entire buffer as a chunk. Reset sentence count."""
        out = self._buffer.strip()
        self._buffer = ""
        self._sentence_count = 0
        if not out:
            return None
        self._chunk_index += 1
        return out
