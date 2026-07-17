"""
GroundingLink -- Post-generation traceability
=============================================

After Luna generates a response, GroundingLink matches each sentence
against the memory nodes injected into the context window.

Every claim gets tagged with the node_id it was synthesised from
and a confidence score based on content similarity.

Two similarity backends:
  Option A  -- Embedding cosine similarity (preferred, async).
  Option B  -- Jaccard token overlap (synchronous fallback).

Sovereignty is not the absence of sharing. It is the presence of conditions.
"""

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Stopwords (lightweight, no NLTK dependency) ─────────────────────
_STOPWORDS = frozenset(
    "the a an is are was were in on at to for of and or but with by from "
    "that this it as be has have had not no i you we they she he her his "
    "my our their its me us them do does did will would shall should can "
    "could may might been being about into through during before after "
    "above below between".split()
)

# ── Sentence splitter ───────────────────────────────────────────────
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_MIN_SENTENCE_LEN = 10  # Ignore fragments shorter than this


# ── Data structures ─────────────────────────────────────────────────


@dataclass
class GroundingSupport:
    """One sentence's grounding verdict."""

    sentence_index: int
    sentence: str
    node_id: Optional[str]       # Best-matching memory node ID
    node_type: Optional[str]     # FACT, DECISION, PERSON_BRIEFING, etc.
    node_preview: Optional[str]  # First 120 chars of source node content
    confidence: float            # 0.0 -- 1.0 similarity score
    level: str                   # "GROUNDED" | "INFERRED" | "UNGROUNDED"
    source: Optional[str] = None       # e.g. 'nexus/priests_and_programmers', 'matrix'
    doc_title: Optional[str] = None    # e.g. 'Priests and Programmers'


@dataclass
class GroundingSummary:
    grounded: int = 0
    inferred: int = 0
    ungrounded: int = 0
    avg_confidence: float = 0.0


@dataclass
class GroundingResult:
    supports: list[GroundingSupport] = field(default_factory=list)
    summary: GroundingSummary = field(default_factory=GroundingSummary)

    def _unique_sources(self) -> list[dict]:
        """Collect unique sources with grounded sentence counts."""
        seen: dict[str, dict] = {}
        for s in self.supports:
            if s.source and s.source not in seen:
                seen[s.source] = {
                    "source": s.source,
                    "doc_title": s.doc_title,
                    "grounded_count": 0,
                }
            if s.source and s.level == "GROUNDED":
                seen[s.source]["grounded_count"] += 1
        return list(seen.values())

    def to_dict(self) -> dict:
        return {
            "supports": [
                {
                    "sentence_index": s.sentence_index,
                    "sentence": s.sentence,
                    "node_id": s.node_id,
                    "node_type": s.node_type,
                    "node_preview": s.node_preview,
                    "confidence": round(s.confidence, 3),
                    "level": s.level,
                    "source": s.source,
                    "doc_title": s.doc_title,
                }
                for s in self.supports
            ],
            "summary": {
                "grounded": self.summary.grounded,
                "inferred": self.summary.inferred,
                "ungrounded": self.summary.ungrounded,
                "avg_confidence": round(self.summary.avg_confidence, 3),
            },
            "sources_used": self._unique_sources(),
        }


# ── Core class ──────────────────────────────────────────────────────


class GroundingLink:
    """Post-generation grounding pipeline."""

    def __init__(self, embeddings=None):
        """
        Args:
            embeddings: Optional embeddings module with .embed(text) -> list[float].
                        If provided, cosine similarity is used (Option A).
                        Otherwise falls back to Jaccard token overlap (Option B).
        """
        self._embeddings = embeddings

    # ── Public API ──────────────────────────────────────────────────

    def ground(
        self,
        response_text: str,
        injected_nodes: list[dict],
    ) -> GroundingResult:
        """
        Ground a response against injected memory nodes (synchronous).

        Uses token-overlap similarity (Option B).

        Args:
            response_text: Luna's full response text.
            injected_nodes: List of dicts with at least ``id``, ``content``,
                            and optionally ``node_type``.

        Returns:
            GroundingResult with per-sentence supports and summary.
        """
        if not response_text or not injected_nodes:
            return GroundingResult()

        sentences = self._split_sentences(response_text)
        if not sentences:
            return GroundingResult()

        supports: list[GroundingSupport] = []

        for idx, sentence in enumerate(sentences):
            best_node: Optional[dict] = None
            best_score = 0.0

            for node in injected_nodes:
                node_content = node.get("content", "")
                if not node_content:
                    continue
                score = self._similarity_token_overlap(sentence, node_content)
                # Boost score for primary grounding sources (cartridges/collections)
                gp = node.get("grounding_priority", "")
                if gp == "primary":
                    score = min(score * 1.3, 1.0)
                elif gp == "background":
                    score = score * 0.8
                if score > best_score:
                    best_score = score
                    best_node = node

            supports.append(
                GroundingSupport(
                    sentence_index=idx,
                    sentence=sentence,
                    node_id=best_node.get("id") if best_node else None,
                    node_type=best_node.get("node_type") if best_node else None,
                    node_preview=self._preview(best_node.get("content", "")) if best_node else None,
                    confidence=best_score,
                    level=self._classify(best_score),
                    source=best_node.get("source") if best_node else None,
                    doc_title=best_node.get("doc_title", best_node.get("title")) if best_node else None,
                )
            )

        summary = self._summarize(supports)
        logger.debug(
            "[GROUNDING] %d sentences: %d grounded, %d inferred, %d ungrounded (avg %.2f)",
            len(supports),
            summary.grounded,
            summary.inferred,
            summary.ungrounded,
            summary.avg_confidence,
        )
        return GroundingResult(supports=supports, summary=summary)

    async def ground_async(
        self,
        response_text: str,
        injected_nodes: list[dict],
    ) -> GroundingResult:
        """
        Async grounding using embedding cosine similarity (Option A).

        Falls back to synchronous token overlap if embeddings unavailable.
        """
        if not self._embeddings or not response_text or not injected_nodes:
            return self.ground(response_text, injected_nodes)

        sentences = self._split_sentences(response_text)
        if not sentences:
            return GroundingResult()

        try:
            # Embed all sentences
            sentence_vecs = [await self._embeddings.embed(s) for s in sentences]

            # Embed all node contents (or use cached)
            node_vecs = []
            for node in injected_nodes:
                content = node.get("content", "")
                vec = await self._embeddings.embed(content) if content else None
                node_vecs.append(vec)

            supports: list[GroundingSupport] = []
            for idx, (sentence, s_vec) in enumerate(zip(sentences, sentence_vecs)):
                best_node: Optional[dict] = None
                best_score = 0.0

                if s_vec:
                    for node, n_vec in zip(injected_nodes, node_vecs):
                        if n_vec is None:
                            continue
                        score = self._cosine(s_vec, n_vec)
                        gp = node.get("grounding_priority", "")
                        if gp == "primary":
                            score = min(score * 1.3, 1.0)
                        elif gp == "background":
                            score = score * 0.8
                        if score > best_score:
                            best_score = score
                            best_node = node

                supports.append(
                    GroundingSupport(
                        sentence_index=idx,
                        sentence=sentence,
                        node_id=best_node.get("id") if best_node else None,
                        node_type=best_node.get("node_type") if best_node else None,
                        node_preview=self._preview(best_node.get("content", "")) if best_node else None,
                        confidence=best_score,
                        level=self._classify(best_score),
                        source=best_node.get("source") if best_node else None,
                        doc_title=best_node.get("doc_title", best_node.get("title")) if best_node else None,
                    )
                )

            summary = self._summarize(supports)
            return GroundingResult(supports=supports, summary=summary)

        except Exception as e:
            logger.warning("[GROUNDING] Embedding path failed, falling back to token overlap: %s", e)
            return self.ground(response_text, injected_nodes)

    # ── Internal helpers ────────────────────────────────────────────

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences, filtering short fragments."""
        raw = _SENTENCE_RE.split(text.strip())
        return [s.strip() for s in raw if len(s.strip()) >= _MIN_SENTENCE_LEN]

    @staticmethod
    def _tokenise(text: str) -> set[str]:
        """Lowercase, split on non-alpha, remove stopwords."""
        tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
        return tokens - _STOPWORDS

    @classmethod
    def _similarity_token_overlap(cls, sentence: str, node_content: str) -> float:
        """Containment similarity: fraction of sentence tokens found in node.

        Uses containment (|A∩B|/|A|) instead of Jaccard (|A∩B|/|A∪B|) because
        sentences are short and nodes are long — Jaccard is dominated by the
        large union denominator and produces near-zero scores.
        """
        a = cls._tokenise(sentence)
        b = cls._tokenise(node_content)
        if not a or not b:
            return 0.0
        intersection = a & b
        return len(intersection) / len(a)

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        """Cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return max(0.0, dot / (mag_a * mag_b))

    @staticmethod
    def _classify(score: float) -> str:
        if score >= 0.7:
            return "GROUNDED"
        if score >= 0.4:
            return "INFERRED"
        return "UNGROUNDED"

    @staticmethod
    def _preview(content: str, max_len: int = 120) -> str:
        """First ``max_len`` characters, truncated with ellipsis."""
        if len(content) <= max_len:
            return content
        return content[:max_len] + "..."

    @staticmethod
    def _summarize(supports: list[GroundingSupport]) -> GroundingSummary:
        if not supports:
            return GroundingSummary()
        grounded = sum(1 for s in supports if s.level == "GROUNDED")
        inferred = sum(1 for s in supports if s.level == "INFERRED")
        ungrounded = sum(1 for s in supports if s.level == "UNGROUNDED")
        avg = sum(s.confidence for s in supports) / len(supports)
        return GroundingSummary(
            grounded=grounded,
            inferred=inferred,
            ungrounded=ungrounded,
            avg_confidence=avg,
        )
