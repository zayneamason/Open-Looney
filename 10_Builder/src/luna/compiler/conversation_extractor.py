"""
Conversation Extractor -- Phase 1b (Knowledge Compiler Phase 7)
===============================================================

Processes raw conversation thread JSON files and extracts structured
memory nodes (FACT, QUOTE, THREAD_ARC) using pattern-based extraction.

No LLM is used -- extraction is purely from structured message format.

Usage:
    extractor = ConversationExtractor(entity_index, existing_node_map)
    result = await extractor.extract_all(local_dir() / "guardian" / "conversations", matrix, scope)
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .entity_index import EntityIndex

logger = logging.getLogger(__name__)

# ── Patterns for fact extraction ────────────────────────────────────

# GPS coordinates
_GPS_RE = re.compile(r"\d+\.\d+[°]?\s*[NSEW]", re.IGNORECASE)
# Dates (YYYY-MM-DD, Month YYYY, etc.)
_DATE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
    re.IGNORECASE,
)
# Measurements / numbers with units
_MEASURE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:km|m|kg|L|liters?|meters?|hectares?|people|springs?|wells?)", re.IGNORECASE)
# Action verbs indicating factual claims
_ACTION_VERBS = re.compile(
    r"\b(?:surveyed|documented|completed|discovered|established|founded|built|tested|measured|mapped|trained|organized|conducted)\b",
    re.IGNORECASE,
)
# Quote patterns
_QUOTE_RE = re.compile(r'"([^"]{15,})"')
_SAID_RE = re.compile(
    r"(\w[\w\s]{2,30})\s+(?:said|told|asked|explained|mentioned|noted|reported|recalled|described)\s*[,:]?\s*[\"'](.+?)[\"']",
    re.IGNORECASE,
)

# Sentence splitter
_SENT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class ExtractResult:
    """Statistics from a conversation extraction run."""

    facts: int = 0
    quotes: int = 0
    thread_arcs: int = 0
    corroborated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "facts": self.facts,
            "quotes": self.quotes,
            "thread_arcs": self.thread_arcs,
            "corroborated": self.corroborated,
            "skipped": self.skipped,
            "errors": self.errors[:10],
        }

    def __iadd__(self, other):
        if isinstance(other, int):
            return self
        self.facts += other.facts
        self.quotes += other.quotes
        self.thread_arcs += other.thread_arcs
        self.corroborated += other.corroborated
        self.skipped += other.skipped
        self.errors.extend(other.errors)
        return self


class ConversationExtractor:
    """
    Extract structured memory nodes from conversation thread JSON files.

    Produces FACT, QUOTE, and THREAD_ARC nodes with deduplication
    against previously compiled nodes.
    """

    def __init__(
        self,
        entity_index: EntityIndex,
        existing_node_map: dict[str, str],
    ):
        self.entity_index = entity_index
        self.existing_node_map = existing_node_map
        self._seen_hashes: set[str] = set()
        self._node_map: dict[str, str] = {}  # source_id -> matrix_node_id

    # ── Public API ──────────────────────────────────────────────────

    async def extract_all(
        self,
        threads_dir: Path,
        matrix,
        scope: str = "global",
    ) -> ExtractResult:
        """Process all conversation thread JSON files in a directory."""
        result = ExtractResult()

        if not threads_dir.exists():
            logger.warning(f"ConvExtractor: threads dir not found: {threads_dir}")
            result.errors.append(f"Directory not found: {threads_dir}")
            return result

        thread_files = sorted(threads_dir.glob("*.json"))
        if not thread_files:
            logger.info(f"ConvExtractor: no JSON files in {threads_dir}")
            return result

        for path in thread_files:
            try:
                sub = await self.extract_thread(path, matrix, scope)
                result += sub
            except Exception as e:
                logger.error(f"ConvExtractor: failed on {path.name}: {e}")
                result.errors.append(f"{path.name}: {e}")

        logger.info(
            f"ConvExtractor: {result.facts} facts, {result.quotes} quotes, "
            f"{result.thread_arcs} arcs, {result.corroborated} corroborated"
        )
        return result

    async def extract_thread(
        self,
        thread_path: Path,
        matrix,
        scope: str,
    ) -> ExtractResult:
        """Process one conversation thread file."""
        result = ExtractResult()

        try:
            with open(thread_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            result.errors.append(f"Load failed: {e}")
            return result

        thread_id = data.get("thread_id", thread_path.stem)
        messages = data.get("messages", [])

        # Track recent topics for THREAD_ARC detection
        recent_topics: list[dict] = []  # sliding window of assistant messages

        for msg in messages:
            if msg.get("role") != "assistant":
                continue

            msg_id = msg.get("id", "")
            content = msg.get("content", "")
            metadata = msg.get("metadata", {})
            entities_mentioned = metadata.get("entities", [])
            topics = metadata.get("topics", [])

            if not content or len(content) < 20:
                continue

            # ── FACT extraction ─────────────────────────────────────
            sentences = _SENT_RE.split(content)
            for sentence in sentences:
                sentence = sentence.strip()
                if len(sentence) < 15:
                    continue

                is_factual = (
                    _GPS_RE.search(sentence)
                    or _DATE_RE.search(sentence)
                    or _MEASURE_RE.search(sentence)
                    or _ACTION_VERBS.search(sentence)
                )

                if is_factual:
                    source_id = f"conv:{thread_id}:{msg_id}:fact"
                    dup = await self._check_dedup(sentence, matrix, scope)
                    if dup:
                        # Create CORROBORATES edge
                        result.corroborated += 1
                        continue

                    content_hash = self._hash(sentence)
                    if content_hash in self._seen_hashes:
                        result.skipped += 1
                        continue
                    self._seen_hashes.add(content_hash)

                    # Resolve entities in sentence
                    resolved = []
                    for eid in entities_mentioned:
                        r = self.entity_index.resolve(eid)
                        if r:
                            resolved.append(r)

                    tags = ["guardian", "conversation", "compiled", "fact", thread_id]
                    tags.extend(resolved[:3])

                    provenance = f"{thread_id}:{msg_id}"
                    full_content = f"{sentence}\nSource: {provenance}."
                    if resolved:
                        full_content += f"\nEntities: {', '.join(resolved)}."

                    try:
                        node_id = await matrix.store_memory(
                            content=full_content,
                            node_type="FACT",
                            tags=tags,
                            confidence=70,
                            scope=scope,
                        )
                        self._node_map[source_id] = node_id
                        result.facts += 1
                    except Exception as e:
                        result.errors.append(f"Fact store failed: {e}")

            # ── QUOTE extraction ────────────────────────────────────
            for match in _QUOTE_RE.finditer(content):
                quote_text = match.group(1)
                if len(quote_text) < 15:
                    continue

                content_hash = self._hash(quote_text)
                if content_hash in self._seen_hashes:
                    continue
                self._seen_hashes.add(content_hash)

                # Try to attribute quote to an entity
                speaker = None
                said_match = _SAID_RE.search(content[max(0, match.start() - 80):match.end()])
                if said_match:
                    speaker_name = said_match.group(1).strip()
                    speaker = self.entity_index.resolve(speaker_name)

                tags = ["guardian", "conversation", "compiled", "quote", thread_id]
                if speaker:
                    tags.append(speaker)

                quote_content = f'"{quote_text}"'
                if speaker:
                    quote_content = f"{speaker}: {quote_content}"
                quote_content += f"\nSource: {thread_id}:{msg_id}."

                try:
                    node_id = await matrix.store_memory(
                        content=quote_content,
                        node_type="FACT",
                        tags=tags,
                        confidence=65,
                        scope=scope,
                    )
                    result.quotes += 1
                except Exception as e:
                    result.errors.append(f"Quote store failed: {e}")

            # ── THREAD_ARC tracking ─────────────────────────────────
            recent_topics.append({
                "msg_id": msg_id,
                "entities": entities_mentioned,
                "topics": topics,
                "content": content[:300],
            })

            # Check for arc (3+ messages sharing entities/topics)
            if len(recent_topics) >= 3:
                arc = self._detect_arc(recent_topics[-5:])
                if arc:
                    content_hash = self._hash(arc["summary"])
                    if content_hash not in self._seen_hashes:
                        self._seen_hashes.add(content_hash)

                        tags = ["guardian", "conversation", "compiled", "thread_arc", thread_id]
                        tags.extend(arc["entities"][:3])

                        try:
                            await matrix.store_memory(
                                content=arc["summary"],
                                node_type="FACT",
                                tags=tags,
                                confidence=60,
                                scope=scope,
                            )
                            result.thread_arcs += 1
                        except Exception as e:
                            result.errors.append(f"Arc store failed: {e}")

        return result

    # ── Internal helpers ────────────────────────────────────────────

    def _detect_arc(self, window: list[dict]) -> Optional[dict]:
        """Detect a narrative arc in a window of recent messages."""
        if len(window) < 3:
            return None

        # Find shared entities across the window
        entity_counts: dict[str, int] = {}
        for msg in window:
            for e in msg.get("entities", []):
                entity_counts[e] = entity_counts.get(e, 0) + 1

        shared = [e for e, c in entity_counts.items() if c >= 2]
        if not shared:
            # Try shared topics
            topic_counts: dict[str, int] = {}
            for msg in window:
                for t in msg.get("topics", []):
                    topic_counts[t] = topic_counts.get(t, 0) + 1
            shared_topics = [t for t, c in topic_counts.items() if c >= 2]
            if not shared_topics:
                return None
            theme = ", ".join(shared_topics[:3])
            entities = list(entity_counts.keys())[:3]
        else:
            theme = ", ".join(shared[:3])
            entities = shared[:3]

        # Build arc summary from first and last messages
        first_preview = window[0]["content"][:150]
        last_preview = window[-1]["content"][:150]
        summary = (
            f"Narrative arc about {theme} "
            f"({len(window)} messages).\n"
            f"Begins: {first_preview}...\n"
            f"Continues: {last_preview}..."
        )

        return {"summary": summary, "entities": entities}

    async def _check_dedup(
        self, content: str, matrix, scope: str
    ) -> Optional[str]:
        """Check if content is already in the matrix (simple token overlap)."""
        content_lower = content.lower()
        tokens = set(re.findall(r"\w{4,}", content_lower))
        if len(tokens) < 3:
            return None

        # Check against existing node map content hashes
        for source_id in list(self.existing_node_map.keys())[:200]:
            if any(t in source_id.lower() for t in list(tokens)[:3]):
                return self.existing_node_map[source_id]

        return None

    @staticmethod
    def _hash(text: str) -> str:
        """Content hash for deduplication."""
        normalized = re.sub(r"\s+", " ", text.lower().strip())
        return hashlib.md5(normalized.encode()).hexdigest()[:12]
