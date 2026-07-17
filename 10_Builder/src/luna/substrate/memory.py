"""
Memory Matrix Operations Layer for Luna Engine
===============================================

High-level memory operations that wrap the database layer.
Provides CRUD for memory nodes, search, context retrieval,
and access tracking.

The Memory Matrix is Luna's soul - all knowledge lives here.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Union
import asyncio
import json
import logging
import math
import os
import time
import uuid

from .database import MemoryDatabase
from .embeddings import EmbeddingStore, EmbeddingGenerator

logger = logging.getLogger(__name__)

# Lazy-loaded entity resolver for mention detection
_entity_resolver = None


# =============================================================================
# RETRIEVAL SCORING HELPERS (Geometric Layer 1)
# =============================================================================

def _recency_decay(created_at: Optional[datetime], half_life: float = 30.0) -> float:
    """Exponential decay: ~1.0 for today, ~0.37 at half_life days, ~0.05 at 3x."""
    if created_at is None:
        return 0.5  # unknown age → neutral
    age_days = (datetime.now() - created_at).total_seconds() / 86400
    if age_days < 0:
        return 1.0  # future timestamp → treat as fresh
    return math.exp(-age_days / half_life)


# =============================================================================
# COLUMN MAPPINGS FOR ROW PARSING
# =============================================================================

# Column order for memory_nodes table (matches schema.sql)
MEMORY_NODE_COLUMNS = [
    "id", "node_type", "content", "summary", "source",
    "confidence", "importance", "access_count", "reinforcement_count",
    "lock_in", "lock_in_state", "last_accessed",
    "created_at", "updated_at", "metadata", "scope"
]

# Column order for conversation_turns table.
# Must match the leading columns of the DB schema so `row_to_dict(row, TURN_COLUMNS)`
# maps tuple rows correctly. Voice v2.0 Step 5 adds `turn_type`.
TURN_COLUMNS = [
    "id", "session_id", "role", "content", "tokens", "created_at", "metadata",
    "turn_type",
]


def _ei_ie_variants(token: str) -> list[str]:
    """Return edit-distance-1 variants of `token` for the ei↔ie typo class.

    One swap per variant; ineligible tokens return []. Catches the dominant
    English misspelling pattern (weiner→wiener, recieve→receive, thier→their)
    used by the typo-tolerant retry path in `fts5_search`.
    """
    variants: list[str] = []
    seen: set[str] = set()
    for i in range(len(token) - 1):
        pair = token[i:i + 2]
        if pair == "ei":
            v = token[:i] + "ie" + token[i + 2:]
        elif pair == "ie":
            v = token[:i] + "ei" + token[i + 2:]
        else:
            continue
        if v != token and v not in seen:
            seen.add(v)
            variants.append(v)
    return variants


def _soundex(s: str) -> str:
    """Compact Soundex — maps phonetically similar tokens to the same 4-char code.

    Enables the phonetic retry path in hybrid_search to surface 'wiener' when
    an STT engine transcribes it as 'winner' (both encode to W560).
    """
    if not s:
        return ""
    codes = {
        'B': '1', 'F': '1', 'P': '1', 'V': '1',
        'C': '2', 'G': '2', 'J': '2', 'K': '2', 'Q': '2', 'S': '2', 'X': '2', 'Z': '2',
        'D': '3', 'T': '3',
        'L': '4',
        'M': '5', 'N': '5',
        'R': '6',
    }
    s = s.upper()
    result = s[0]
    prev = codes.get(s[0], '0')
    for c in s[1:]:
        code = codes.get(c, '0')
        if code != '0' and code != prev:
            result += code
        prev = code if code != '0' else prev
    return (result + '000')[:4]


_PHONETIC_THRESHOLD = 5  # retry with phonetic expansion when FTS returns fewer hits


def row_to_dict(row: tuple, columns: list[str]) -> dict:
    """Convert a database row tuple to a dictionary."""
    if row is None:
        return None
    return dict(zip(columns, row))


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class MemoryNode:
    """
    A node in the memory graph.

    Represents facts, decisions, problems, actions, and other typed knowledge.
    """
    id: str
    node_type: str  # FACT, DECISION, PROBLEM, ACTION, CONTEXT, etc.
    content: str
    summary: Optional[str] = None
    source: Optional[str] = None
    confidence: float = 1.0
    importance: float = 0.5
    access_count: int = 0
    reinforcement_count: int = 0
    lock_in: float = 0.15
    lock_in_state: str = "drifting"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)
    scope: str = "global"

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()

    @classmethod
    def from_row(cls, row) -> "MemoryNode":
        """Create MemoryNode from database row (tuple, dict, or sqlite3.Row)."""
        if row is None:
            return None
        # Convert non-dict to dict
        if not isinstance(row, dict):
            try:
                row = dict(row)
            except (TypeError, ValueError):
                row = row_to_dict(row, MEMORY_NODE_COLUMNS)

        if row is None:
            return None

        metadata = row.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        created_at = row.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = None

        updated_at = row.get("updated_at")
        if isinstance(updated_at, str):
            try:
                updated_at = datetime.fromisoformat(updated_at)
            except ValueError:
                updated_at = None

        return cls(
            id=row["id"],
            node_type=row["node_type"],
            content=row["content"],
            summary=row.get("summary"),
            source=row.get("source"),
            confidence=row.get("confidence") or 1.0,
            importance=row.get("importance") or 0.5,
            access_count=row.get("access_count") or 0,
            reinforcement_count=row.get("reinforcement_count") or 0,
            lock_in=row.get("lock_in") or 0.15,
            lock_in_state=row.get("lock_in_state") or "drifting",
            created_at=created_at,
            updated_at=updated_at,
            metadata=metadata or {},
            scope=row.get("scope") or "global",
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "node_type": self.node_type,
            "content": self.content,
            "summary": self.summary,
            "source": self.source,
            "confidence": self.confidence,
            "importance": self.importance,
            "access_count": self.access_count,
            "reinforcement_count": self.reinforcement_count,
            "lock_in": self.lock_in,
            "lock_in_state": self.lock_in_state,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "metadata": self.metadata,
            "scope": self.scope,
        }


@dataclass
class Turn:
    """
    A single turn in a conversation.

    Represents one message exchange (user or assistant).
    """
    id: int
    session_id: str
    role: str  # user, assistant, system
    content: str
    tokens: Optional[int] = None
    created_at: Optional[datetime] = None
    # Voice v2.0 Step 5 — `turn_type` taxonomy. None on legacy rows; downstream
    # consumers should treat None as NORMAL_USER_TURN / NORMAL_ASSISTANT_TURN
    # per `TurnType.default_for_role`.
    turn_type: Optional[str] = None
    metadata: Optional[dict] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

    @classmethod
    def from_row(cls, row) -> "Turn":
        """Create Turn from database row (tuple, dict, or sqlite3.Row)."""
        if row is None:
            return None
        # Convert non-dict to dict
        if not isinstance(row, dict):
            try:
                row = dict(row)
            except (TypeError, ValueError):
                row = row_to_dict(row, TURN_COLUMNS)

        if row is None:
            return None

        created_at = row.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = None

        metadata = row.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = None

        return cls(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            tokens=row.get("tokens"),
            created_at=created_at,
            turn_type=row.get("turn_type"),
            metadata=metadata,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "tokens": self.tokens,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "turn_type": self.turn_type,
            "metadata": self.metadata,
        }


# =============================================================================
# MEMORY MATRIX
# =============================================================================

# Node types excluded from retrieval results.
# These are metadata or echo nodes — not knowledge. They remain in the DB
# for extraction, graph edges, and history, but never enter Luna's context window.
RETRIEVAL_EXCLUDED_TYPES = frozenset({
    'CONVERSATION_TURN', 'SESSION', 'THREAD', 'PARENT', 'CATEGORY',
})


class MemoryMatrix:
    """
    High-level memory operations layer.

    Wraps the database with intuitive methods for:
    - Adding and retrieving memory nodes
    - Searching by text
    - Getting context for queries
    - Tracking access patterns
    - Managing conversation turns

    This is Luna's soul - all knowledge lives here.
    """

    def __init__(self, db: MemoryDatabase, enable_embeddings: bool = True):
        """
        Initialize the Memory Matrix.

        Args:
            db: The underlying MemoryDatabase instance
            enable_embeddings: Whether to auto-generate embeddings on add_node (default True)
        """
        self.db = db
        self.graph: Optional["MemoryGraph"] = None  # Set by MatrixActor after init
        self._enable_embeddings = enable_embeddings
        self._embedding_store: Optional[EmbeddingStore] = None
        self._embedding_generator: Optional[EmbeddingGenerator] = None
        logger.info("MemoryMatrix initialized")

    # =========================================================================
    # RETRIEVAL SCORING (Geometric Layer 1)
    # =========================================================================

    def _get_degree(self, node_id: str) -> int:
        """Get edge count from in-memory NetworkX graph (O(1))."""
        if self.graph and hasattr(self.graph, '_graph') and node_id in self.graph._graph:
            return self.graph._graph.degree(node_id)
        return 0

    # =========================================================================
    # NODE OPERATIONS
    # =========================================================================

    async def add_node(
        self,
        node_type: str,
        content: str,
        source: Optional[str] = None,
        metadata: Optional[dict] = None,
        summary: Optional[str] = None,
        confidence: float = 1.0,
        importance: float = 0.5,
        link_entities: bool = True,
        scope: str = "global",
    ) -> str:
        """
        Add a new memory node.

        Args:
            node_type: Type of node (FACT, DECISION, PROBLEM, ACTION, etc.)
            content: The actual content/information
            source: Where this came from (conversation, file, etc.)
            metadata: Optional extra data as dict
            summary: Optional short summary for display
            confidence: Confidence score 0-1 (default 1.0)
            importance: Importance score 0-1 (default 0.5)
            link_entities: Whether to detect and link mentioned entities (default True)
            scope: Memory scope - 'global' or 'project:{slug}'

        Returns:
            The ID of the created node
        """
        node_id = str(uuid.uuid4())[:12]
        now = datetime.now().isoformat()

        metadata_json = json.dumps(metadata) if metadata else None

        await self.db.execute(
            """
            INSERT INTO memory_nodes (
                id, node_type, content, summary, source,
                confidence, importance, access_count, reinforcement_count,
                lock_in, lock_in_state,
                created_at, updated_at, metadata, scope
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0.15, 'drifting', ?, ?, ?, ?)
            """,
            [
                node_id, node_type, content, summary, source,
                confidence, importance, now, now, metadata_json, scope
            ]
        )

        logger.debug(f"Added memory node: {node_id} ({node_type})")

        # Detect and link entity mentions
        if link_entities:
            await self._link_entity_mentions(node_id, content)

        # Generate and store embedding
        if self._enable_embeddings:
            await self._store_embedding(node_id, content, summary)

        return node_id

    async def _ensure_embedding_components(self) -> bool:
        """
        Ensure embedding store and generator are initialized.

        Returns:
            True if embeddings are available, False otherwise
        """
        if self._embedding_store is None:
            from .local_embeddings import EMBEDDING_DIM
            self._embedding_store = EmbeddingStore(
                self.db,
                dim=EMBEDDING_DIM,
                table_name="memory_embeddings_local"
            )
            await self._embedding_store.initialize()

        if self._embedding_generator is None:
            self._embedding_generator = EmbeddingGenerator(model="local-minilm")

        return self._embedding_store.is_available

    async def _store_embedding(
        self,
        node_id: str,
        content: str,
        summary: Optional[str] = None,
    ) -> bool:
        """
        Generate and store embedding for a node.

        Args:
            node_id: The node ID
            content: The node content
            summary: Optional summary (prepended to content for embedding)

        Returns:
            True if embedding was stored, False otherwise
        """
        try:
            available = await self._ensure_embedding_components()
            if not available:
                return False

            # Combine summary and content for embedding
            text = content or ""
            if summary:
                text = f"{summary}\n{text}"

            if not text.strip():
                return False

            embedding = await self._embedding_generator.generate(text)
            await self._embedding_store.store(node_id, embedding)
            logger.debug(f"Stored embedding for node {node_id}")
            return True

        except Exception as e:
            logger.warning(f"Failed to store embedding for {node_id}: {e}")
            return False

    async def _get_entity_resolver(self):
        """Get or create the entity resolver instance."""
        global _entity_resolver
        if _entity_resolver is None:
            try:
                from ..entities.resolution import EntityResolver
                _entity_resolver = EntityResolver(self.db)
                logger.debug("EntityResolver initialized for mention detection")
            except ImportError as e:
                logger.warning(f"Could not import EntityResolver: {e}")
                return None
        return _entity_resolver

    async def _link_entity_mentions(self, node_id: str, content: str) -> int:
        """
        Detect entities in content and create relevance-scored mention links.

        Scoring based on:
        - Frequency: how many times the entity name appears
        - Position: early mentions suggest the node is ABOUT the entity
        - Density: what fraction of content is the entity name

        Classification:
        - "subject": node is primarily about this entity (high density/frequency)
        - "focus": entity is prominently featured (early + repeated)
        - "reference": passing mention (low relevance)

        Mentions below confidence 0.3 are dropped entirely.
        """
        resolver = await self._get_entity_resolver()
        if resolver is None:
            return 0

        try:
            entities = await resolver.detect_mentions(content)
            if not entities:
                return 0

            content_lower = content.lower()
            content_len = len(content)
            word_count = len(content.split())

            if content_len == 0 or word_count == 0:
                return 0

            mention_count = 0
            for entity in entities:
                name_lower = entity.name.lower()
                name_word_count = len(entity.name.split())

                # --- Signal 1: Frequency ---
                occurrences = content_lower.count(name_lower)
                frequency_score = min(occurrences / 3.0, 1.0)

                # --- Signal 2: Position ---
                first_pos = content_lower.find(name_lower)
                if first_pos >= 0:
                    position_score = 1.0 - (first_pos / content_len)
                else:
                    position_score = 0.0

                # --- Signal 3: Density ---
                density = (occurrences * name_word_count) / word_count
                density_score = min(density * 10, 1.0)

                # --- Composite Confidence ---
                confidence = min(1.0, (
                    0.3 * frequency_score +
                    0.3 * position_score +
                    0.4 * density_score
                ))

                # --- Drop low-relevance mentions ---
                if confidence < 0.3:
                    logger.debug(
                        f"Skipping low-relevance mention: '{entity.name}' "
                        f"in node {node_id} (conf={confidence:.2f})"
                    )
                    continue

                # --- Classify mention type ---
                if density > 0.1 or occurrences >= 3:
                    mention_type = "subject"
                elif position_score > 0.8 and occurrences >= 2:
                    mention_type = "focus"
                else:
                    mention_type = "reference"

                # --- Build context snippet ---
                pos = content_lower.find(name_lower)
                if pos >= 0:
                    start = max(0, pos - 30)
                    end = min(content_len, pos + len(entity.name) + 70)
                    snippet = content[start:end]
                    if start > 0:
                        snippet = "..." + snippet
                    if end < content_len:
                        snippet = snippet + "..."
                else:
                    snippet = content[:100] + "..." if content_len > 100 else content

                await resolver.create_mention(
                    entity_id=entity.id,
                    node_id=node_id,
                    mention_type=mention_type,
                    confidence=round(confidence, 3),
                    context_snippet=snippet,
                )
                mention_count += 1
                logger.debug(
                    f"Linked entity '{entity.name}' to node {node_id} "
                    f"(type={mention_type}, conf={confidence:.2f})"
                )

            if mention_count > 0:
                logger.info(f"Linked {mention_count} entities to node {node_id}")

            return mention_count

        except Exception as e:
            logger.warning(f"Failed to link entity mentions for node {node_id}: {e}")
            return 0

    async def get_node(self, node_id: str) -> Optional[MemoryNode]:
        """
        Retrieve a memory node by ID.

        Args:
            node_id: The node's unique identifier

        Returns:
            MemoryNode if found, None otherwise
        """
        row = await self.db.fetchone(
            "SELECT * FROM memory_nodes WHERE id = ?",
            (node_id,)
        )

        if row is None:
            return None

        return MemoryNode.from_row(row)

    async def update_node(
        self,
        node_id: str,
        content: Optional[str] = None,
        summary: Optional[str] = None,
        confidence: Optional[float] = None,
        importance: Optional[float] = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        Update an existing memory node.

        Args:
            node_id: The node's unique identifier
            content: New content (if provided)
            summary: New summary (if provided)
            confidence: New confidence score (if provided)
            importance: New importance score (if provided)
            metadata: New metadata (if provided)

        Returns:
            True if node was updated, False if not found
        """
        updates = []
        params = []

        if content is not None:
            updates.append("content = ?")
            params.append(content)
        if summary is not None:
            updates.append("summary = ?")
            params.append(summary)
        if confidence is not None:
            updates.append("confidence = ?")
            params.append(confidence)
        if importance is not None:
            updates.append("importance = ?")
            params.append(importance)
        if metadata is not None:
            updates.append("metadata = ?")
            params.append(json.dumps(metadata))

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(node_id)

        result = await self.db.execute(
            f"UPDATE memory_nodes SET {', '.join(updates)} WHERE id = ?",
            params
        )

        return result.rowcount > 0

    async def delete_node(self, node_id: str) -> bool:
        """
        Delete a memory node.

        Args:
            node_id: The node's unique identifier

        Returns:
            True if node was deleted, False if not found
        """
        result = await self.db.execute(
            "DELETE FROM memory_nodes WHERE id = ?",
            (node_id,)
        )
        return result.rowcount > 0

    # =========================================================================
    # SEARCH OPERATIONS
    # =========================================================================

    async def search_nodes(
        self,
        query: str,
        node_type: Optional[str] = None,
        limit: int = 10,
        scope: Optional[str] = None,
        include_provisional: bool = True,
    ) -> list[MemoryNode]:
        """
        Search memory nodes by text using LIKE query.

        Args:
            query: Search text (uses SQL LIKE with wildcards)
            node_type: Optional filter by node type
            limit: Maximum number of results (default 10)
            scope: Optional scope filter ('global', 'project:slug'). None = all scopes.

        Returns:
            List of matching MemoryNodes, ordered by importance
        """
        search_pattern = f"%{query}%"
        conditions = ["(content LIKE ? OR summary LIKE ?)", "namespace = 'active'"]
        params: list = [search_pattern, search_pattern]

        # Exclude noise node types from retrieval results
        _excl_placeholders = ",".join("?" for _ in RETRIEVAL_EXCLUDED_TYPES)
        conditions.append(f"node_type NOT IN ({_excl_placeholders})")
        params.extend(RETRIEVAL_EXCLUDED_TYPES)

        # Exclude bare entity stubs (< 20 chars content)
        conditions.append("LENGTH(content) > 20")

        if node_type:
            conditions.append("node_type = ?")
            params.append(node_type)

        if scope is not None:
            conditions.append("scope = ?")
            params.append(scope)

        if not include_provisional:
            conditions.append("(source IS NULL OR source NOT LIKE 'lunafm:%')")

        where = " AND ".join(conditions)
        params.append(limit)

        rows = await self.db.fetchall(
            f"""
            SELECT * FROM memory_nodes
            WHERE {where}
            ORDER BY importance DESC, created_at DESC
            LIMIT ?
            """,
            tuple(params)
        )

        return [MemoryNode.from_row(row) for row in rows]

    # =========================================================================
    # FTS5 SEARCH (Full-Text Search with Stemming)
    # =========================================================================

    async def fts5_search(
        self,
        query: str,
        node_type: Optional[str] = None,
        limit: int = 20,
        scope: Optional[str] = None,
    ) -> list[tuple[MemoryNode, float]]:
        """
        Search memory nodes using FTS5 full-text search.

        Uses Porter stemmer: "collaborate" matches "collaborator", "collaboration".
        Supports phrase search, boolean operators, and prefix matching.

        Args:
            query: Search query (FTS5 syntax supported)
            node_type: Optional filter by node type
            limit: Maximum number of results (default 10)
            scope: Optional scope filter. None = all scopes.

        Returns:
            List of (MemoryNode, score) tuples, sorted by relevance
        """
        call_start = time.perf_counter()
        timing: dict[str, float] = {
            "exists_check": 0.0, "shape_query": 0.0,
            "sql_fetch": 0.0, "hydrate": 0.0,
        }

        # Check if FTS5 table exists
        exists_start = time.perf_counter()
        fts_exists = await self.db.fetchone("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='memory_nodes_fts'
        """)
        timing["exists_check"] = time.perf_counter() - exists_start

        if not fts_exists:
            logger.warning("FTS5 table not found, falling back to LIKE search")
            nodes = await self.search_nodes(query, node_type, limit, scope=scope)
            return [(node, 1.0) for node in nodes]

        # Tokenize query: strip stopwords, keep top-5 most distinctive terms,
        # then OR-join for recall. Capping prevents BM25 score dilution on long queries.
        shape_start = time.perf_counter()
        _fts_stops = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been", "to",
            "of", "in", "for", "on", "with", "at", "by", "from", "and", "or",
            "but", "not", "it", "its", "this", "that", "my", "me", "you", "we",
            "our", "they", "them", "what", "who", "how", "do", "does", "did",
            "will", "would", "can", "could", "should", "may", "have", "has",
            "had", "about", "all", "just", "like", "tell", "know", "luna",
        }
        # Strip surrounding punctuation AND drop any internal apostrophes /
        # smart quotes — `.strip()` only operates on ends, so without the
        # explicit replace `Let's` keeps its `'`, gets joined with `OR`, and
        # FTS5 reads the apostrophe as a stray string-delimiter → "fts5: syntax
        # error near \"'\"". Cheaper than full FTS5 escaping and matches the
        # tokenizer used in aibrarian_engine.
        _quote_chars = "'’‘“”"
        raw_words = [
            w.translate(str.maketrans("", "", _quote_chars))
             .strip(".,!?;:\"()[]{}")
             .lower()
            for w in query.split()
        ]
        meaningful = [w for w in raw_words if w and len(w) >= 2 and w not in _fts_stops]
        # Prefer longer (more specific) words; cap at 5 to keep BM25 focused
        meaningful.sort(key=len, reverse=True)
        top_terms = meaningful[:5] if meaningful else raw_words[:3]

        # Inline typo expansion (ei↔ie class). Variants ride alongside the
        # original term in the OR query so misspellings like 'weiner' surface
        # the canonical 'wiener' content even when other tokens rescue the OR.
        # Cheaper than per-token hit-counting; BM25 weights distinctive tokens
        # higher so noise from variants of common words stays low-rank.
        expanded_terms: list[str] = []
        seen_terms: set[str] = set()
        for t in top_terms:
            if t not in seen_terms:
                seen_terms.add(t)
                expanded_terms.append(t)
            for v in _ei_ie_variants(t):
                if v not in seen_terms:
                    seen_terms.add(v)
                    expanded_terms.append(v)

        escaped = [t.replace('"', '""') for t in expanded_terms]
        safe_query = " OR ".join(escaped) if len(escaped) > 1 else (escaped[0] if escaped else query.replace('"', '""'))

        # CROSS JOIN pins the FTS virtual table as the outer loop; SQLite
        # documents that CROSS JOIN is never reordered by the planner. This
        # keeps the fast FTS-first plan even when `m.scope = ?` is present.
        # Previously a plain JOIN let the planner drive from
        # `idx_nodes_scope_type` and probe FTS per row, blowing the cap on the
        # global path.
        conditions = ["memory_nodes_fts MATCH ?"]
        params: list = [safe_query]

        _excl_placeholders = ",".join("?" for _ in RETRIEVAL_EXCLUDED_TYPES)
        conditions.append(f"m.node_type NOT IN ({_excl_placeholders})")
        params.extend(RETRIEVAL_EXCLUDED_TYPES)

        # Exclude bare entity stubs (< 20 chars content)
        conditions.append("LENGTH(m.content) > 20")

        if node_type:
            conditions.append("m.node_type = ?")
            params.append(node_type)

        if scope is not None:
            conditions.append("m.scope = ?")
            params.append(scope)

        where = " AND ".join(conditions)
        params.append(limit)
        timing["shape_query"] = time.perf_counter() - shape_start

        fts_sql = f"""
            SELECT m.*, bm25(memory_nodes_fts) AS score
            FROM memory_nodes_fts
            CROSS JOIN memory_nodes m ON m.rowid = memory_nodes_fts.rowid
            WHERE m.namespace = 'active' AND {where}
            ORDER BY bm25(memory_nodes_fts)
            LIMIT ?
            """
        fetch_params = tuple(params)

        fetch_start = time.perf_counter()
        rows = await self.db.fetchall(fts_sql, fetch_params)
        timing["sql_fetch"] = time.perf_counter() - fetch_start

        hydrate_start = time.perf_counter()
        results = []
        for row in rows:
            # Last column is the score, rest are node columns
            node_data = row[:-1]
            score = abs(row[-1])  # bm25 returns negative, lower is better
            node = MemoryNode.from_row(node_data)
            results.append((node, score))
        timing["hydrate"] = time.perf_counter() - hydrate_start

        self._log_fts_profile(
            query=query, scope=scope, limit=limit,
            total=time.perf_counter() - call_start,
            timing=timing,
            safe_query=safe_query,
            raw_words_n=len(raw_words),
            meaningful_n=len(meaningful),
            top_terms_n=len(top_terms),
            rows_n=len(rows),
            node_type_filter=node_type,
        )

        if os.getenv("LUNA_FTS_EXPLAIN"):
            await self._fts_explain_plan(
                sql=fts_sql,
                params=fetch_params,
                scope=scope,
                safe_query=safe_query,
                limit=limit,
            )

        return results

    # =========================================================================
    # SEMANTIC SEARCH (Vector Similarity)
    # =========================================================================

    async def semantic_search(
        self,
        query: str,
        node_type: Optional[str] = None,
        limit: int = 10,
        min_similarity: float = 0.3,
        scope: Optional[str] = None,
    ) -> list[tuple[MemoryNode, float]]:
        """
        Search memory nodes using semantic similarity.

        Uses local MiniLM embeddings for vector search.
        Finds semantically similar content even without keyword matches.

        Args:
            query: Search query text
            node_type: Optional filter by node type
            limit: Maximum number of results (default 10)
            min_similarity: Minimum cosine similarity threshold (default 0.3)
            scope: Optional scope filter. None = all scopes.

        Returns:
            List of (MemoryNode, similarity) tuples, sorted by similarity
        """
        call_start = time.perf_counter()
        timing: dict[str, float] = {
            "init": 0.0, "embed": 0.0, "vec_search": 0.0,
            "hydrate": 0.0, "filter": 0.0,
        }
        similar_n = 0
        rows_n = 0

        # Get or create embedding components
        init_start = time.perf_counter()
        if not hasattr(self, '_embedding_store') or self._embedding_store is None:
            from .local_embeddings import EMBEDDING_DIM
            self._embedding_store = EmbeddingStore(
                self.db,
                dim=EMBEDDING_DIM,
                table_name="memory_embeddings_local"
            )
            await self._embedding_store.initialize()

        if not hasattr(self, '_embedding_generator') or self._embedding_generator is None:
            self._embedding_generator = EmbeddingGenerator(model="local-minilm")
        timing["init"] = time.perf_counter() - init_start

        # Capture warm-state snapshot before any call that might load the model,
        # so the profile can separate a cold first retrieval from warm repeats.
        model_loaded = self._is_local_model_loaded()

        store_available = bool(self._embedding_store.is_available)

        if not store_available:
            from luna.diagnostics.maturity import compiled_debug
            compiled_debug(logger, "sqlite-vec not available, falling back to LIKE search")
            fallback_start = time.perf_counter()
            nodes = await self.search_nodes(query, node_type, limit)
            timing["vec_search"] = time.perf_counter() - fallback_start
            results = [(node, 1.0) for node in nodes]
            self._log_semantic_profile(
                query=query, scope=scope, limit=limit,
                total=time.perf_counter() - call_start,
                timing=timing,
                similar_n=0, rows_n=len(nodes), results_n=len(results),
                store_available=store_available,
                cache_hit=False, model_loaded=model_loaded,
            )
            return results

        # Generate query embedding
        embed_start = time.perf_counter()
        query_embedding = await self._embedding_generator.generate(query)
        timing["embed"] = time.perf_counter() - embed_start
        cache_hit = bool(getattr(self._embedding_generator, "last_cache_hit", False))

        # Search for similar embeddings
        vec_start = time.perf_counter()
        similar = await self._embedding_store.search(
            query_embedding,
            limit=limit * 2,  # Get extra for filtering
            min_similarity=min_similarity,
        )
        timing["vec_search"] = time.perf_counter() - vec_start
        similar_n = len(similar)

        if not similar:
            self._log_semantic_profile(
                query=query, scope=scope, limit=limit,
                total=time.perf_counter() - call_start,
                timing=timing,
                similar_n=0, rows_n=0, results_n=0,
                store_available=store_available,
                cache_hit=cache_hit, model_loaded=model_loaded,
            )
            return []

        # Phase 1D: batch hydration.
        # Previously hydrated each candidate via a per-node get_node() call
        # (N serial SELECTs). With limit*2 candidates this dominated matrix
        # latency (measured ~324 ms/call; 90% of hybrid_search wall time).
        # Single batched SELECT matches the pattern already used by graph
        # expansion at ~1118. Order and filters are preserved by iterating
        # `similar` (which carries similarity-desc order) and looking up in
        # the hydrated dict.
        hydrate_start = time.perf_counter()
        node_ids = [nid for nid, _sim in similar]
        placeholders = ",".join("?" * len(node_ids))
        rows = await self.db.fetchall(
            f"SELECT * FROM memory_nodes WHERE id IN ({placeholders})",
            tuple(node_ids),
        )
        nodes_by_id: dict[str, MemoryNode] = {}
        for row in rows:
            n = MemoryNode.from_row(row)
            if n is not None:
                nodes_by_id[n.id] = n
        rows_n = len(rows)
        timing["hydrate"] = time.perf_counter() - hydrate_start

        filter_start = time.perf_counter()
        results = []
        for node_id, similarity in similar:
            node = nodes_by_id.get(node_id)
            if node is None:
                continue
            if node.node_type in RETRIEVAL_EXCLUDED_TYPES:
                continue
            if node_type and node.node_type != node_type:
                continue
            if scope is not None and node.scope != scope:
                continue
            results.append((node, similarity))
            if len(results) >= limit:
                break
        timing["filter"] = time.perf_counter() - filter_start

        self._log_semantic_profile(
            query=query, scope=scope, limit=limit,
            total=time.perf_counter() - call_start,
            timing=timing,
            similar_n=similar_n, rows_n=rows_n, results_n=len(results),
            store_available=store_available,
            cache_hit=cache_hit, model_loaded=model_loaded,
        )
        return results

    def _is_local_model_loaded(self) -> bool:
        """Return True if the local MiniLM model is already in memory.

        Used to surface cold-start vs warm-path state on `[SEMANTIC_PROFILE]`
        without forcing a load. Safe to call before the generator exists.
        """
        try:
            from .local_embeddings import _instance as _singleton  # type: ignore
        except ImportError:
            return False
        return bool(_singleton is not None and _singleton.is_loaded())

    async def preload_embeddings(self) -> None:
        """Warm the local embedding model so the first semantic call is cheap.

        Safe no-op if sentence-transformers is unavailable. Callers can invoke
        this during a matrix init seam to move cold-start cost out of the
        retrieval hot path. Kept explicit rather than auto-run so the caller
        decides when to pay startup time.
        """
        try:
            from .local_embeddings import get_embeddings
            emb = get_embeddings()
            # Run the blocking model load off the event loop so warm-up doesn't
            # stall concurrent engine work.
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, emb.preload)
        except Exception as exc:
            logger.warning("preload_embeddings failed: %r", exc)

    # =========================================================================
    # PHONETIC EXPANSION HELPERS
    # =========================================================================

    async def _ensure_fts_vocab(self, conn) -> None:
        """Register soundex UDF and create fts5vocab view (both idempotent)."""
        await conn.create_function("soundex_py", 1, _soundex)
        await conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_nodes_fts_vocab "
            "USING fts5vocab('memory_nodes_fts', 'row')"
        )

    async def _phonetic_expand_terms(self, query: str) -> list[str]:
        """Query FTS vocab for indexed terms that share a Soundex code with any query term.

        Returns novel terms not already present in the query.  Used by
        hybrid_search's sparse-hit retry path to surface STT-misheard tokens
        (e.g. 'winner' → 'wiener') without touching the hot path.
        """
        terms = [
            w.strip(".,!?;:\"()[]{}").lower()
            for w in query.split()
            if len(w) >= 4  # most English stop words are ≤3 chars
        ]
        if not terms:
            return []
        extras: list[str] = []
        seen: set[str] = set(terms)
        try:
            async with self.db.get_connection() as conn:
                await self._ensure_fts_vocab(conn)
                for term in terms[:5]:
                    rows = await conn.execute(
                        "SELECT DISTINCT term FROM memory_nodes_fts_vocab "
                        "WHERE soundex_py(term) = soundex_py(?) "
                        "  AND term != ? AND length(term) >= 3 "
                        "LIMIT 3",
                        (term, term),
                    )
                    async for row in rows:
                        t = row[0]
                        if t not in seen:
                            seen.add(t)
                            extras.append(t)
        except Exception as exc:
            logger.debug("[PHONETIC_EXPAND] vocab lookup failed: %s", exc)
        return extras

    # =========================================================================
    # HYBRID SEARCH (FTS5 + Semantic with RRF Fusion)
    # =========================================================================

    async def hybrid_search(
        self,
        query: str,
        node_type: Optional[str] = None,
        limit: int = 10,
        keyword_weight: float = 0.6,
        semantic_weight: float = 0.4,
        rrf_k: int = 60,
        scope: Optional[str] = None,
        min_similarity: float = 0.45,
    ) -> list[tuple[MemoryNode, float]]:
        """
        Search using both FTS5 and semantic search with Reciprocal Rank Fusion.

        Combines keyword matching (exact terms, stemming) with semantic
        similarity (meaning-based) for best results.

        Weights favour FTS5 (0.6) over semantic (0.4) because:
        - FTS5 uses BM25 with per-document scoring — always reliable
        - Semantic search falls back to LIKE with flat scores when
          sqlite-vec is offline, making its rank signal noise

        Args:
            query: Search query text
            node_type: Optional filter by node type
            limit: Maximum number of results (default 10)
            keyword_weight: Weight for FTS5 results (default 0.6)
            semantic_weight: Weight for semantic results (default 0.4)
            rrf_k: RRF constant (default 60, higher = more emphasis on top ranks)
            scope: Optional scope filter. None = all scopes.
            min_similarity: Minimum cosine similarity floor passed through to
                semantic_search. Raised from semantic_search's permissive 0.3
                default to 0.45 here so low-signal queries (e.g. "what???",
                isolated stopwords) don't surface tangentially-similar nodes
                that the model then confabulates around.

        Returns:
            List of (MemoryNode, combined_score) tuples, sorted by score
        """
        # Run both searches in parallel conceptually (sequential here for SQLite)
        call_start = time.perf_counter()
        timing: dict[str, float] = {
            "fts": 0.0, "semantic": 0.0,
            "rank_maps": 0.0, "rrf": 0.0, "sort": 0.0,
        }

        fts_start = time.perf_counter()
        fts_results = await self.fts5_search(query, node_type, limit * 2, scope=scope)
        timing["fts"] = time.perf_counter() - fts_start

        # Phonetic retry for STT mishearing class (winner→wiener, etc.).
        # Only fires when FTS returns a sparse result set — the ei↔ie expansion
        # already handles spelling swaps, so by this point a low count likely
        # means an acoustic confusion.  Merges retry hits into fts_results so
        # the downstream RRF weighting still applies.
        if 0 < len(fts_results) < _PHONETIC_THRESHOLD:
            phonetic_extras = await self._phonetic_expand_terms(query)
            if phonetic_extras:
                phonetic_query = query + " " + " ".join(phonetic_extras)
                fts_phonetic = await self.fts5_search(phonetic_query, node_type, limit * 2, scope=scope)
                if len(fts_phonetic) > len(fts_results):
                    orig_count = len(fts_results)
                    merged: dict[str, tuple] = {r[0].id: r for r in fts_results}
                    for r in fts_phonetic:
                        if r[0].id not in merged:
                            merged[r[0].id] = r
                    fts_results = list(merged.values())
                    logger.info(
                        "[PHONETIC_EXPAND] FTS %d → %d hits; extras=%s",
                        orig_count, len(fts_results), phonetic_extras,
                    )

        semantic_start = time.perf_counter()
        semantic_results = await self.semantic_search(
            query, node_type, limit * 2, scope=scope, min_similarity=min_similarity,
        )
        timing["semantic"] = time.perf_counter() - semantic_start

        # Semantic-only weighting when FTS returns nothing (typo escapes the
        # ei↔ie retry, OOV terms, etc.). Redirecting full RRF mass to semantic
        # widens the score range so downstream composite ranking has signal to
        # work with — without this, every surviving node sits in a narrow
        # 0.4/(60+rank) band and frequency_boost dominates the composite.
        if not fts_results and semantic_results:
            keyword_weight, semantic_weight = 0.0, 1.0
            logger.info("[HYBRID_SEMANTIC_ONLY] FTS=0 → semantic weight elevated to 1.0")

        # Build node lookup and rank maps
        rank_maps_start = time.perf_counter()
        nodes_by_id: dict[str, MemoryNode] = {}
        fts_ranks: dict[str, int] = {}
        semantic_ranks: dict[str, int] = {}

        for rank, (node, _score) in enumerate(fts_results, start=1):
            nodes_by_id[node.id] = node
            fts_ranks[node.id] = rank

        for rank, (node, _score) in enumerate(semantic_results, start=1):
            nodes_by_id[node.id] = node
            semantic_ranks[node.id] = rank
        timing["rank_maps"] = time.perf_counter() - rank_maps_start

        # Calculate RRF scores
        # RRF(d) = Σ (weight / (k + rank(d)))
        rrf_start = time.perf_counter()
        rrf_scores: dict[str, float] = {}

        for node_id in nodes_by_id:
            score = 0.0

            if node_id in fts_ranks:
                score += keyword_weight / (rrf_k + fts_ranks[node_id])

            if node_id in semantic_ranks:
                score += semantic_weight / (rrf_k + semantic_ranks[node_id])

            rrf_scores[node_id] = score
        timing["rrf"] = time.perf_counter() - rrf_start

        # Sort by RRF score (higher is better)
        sort_start = time.perf_counter()
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        timing["sort"] = time.perf_counter() - sort_start

        # Per-source diagnostics: without these we can't tell if semantic went dark.
        # Fields are carried inline into [HYBRID_PROFILE] — no separate log line.
        overlap_n = sum(1 for nid in nodes_by_id if nid in fts_ranks and nid in semantic_ranks)
        sem_only_rank = next(
            (
                i + 1
                for i, nid in enumerate(sorted_ids[:limit])
                if nid in semantic_ranks and nid not in fts_ranks
            ),
            None,
        )

        # Return top results
        results = [
            (nodes_by_id[node_id], rrf_scores[node_id])
            for node_id in sorted_ids[:limit]
        ]

        self._log_hybrid_profile(
            query=query, scope=scope, limit=limit,
            total=time.perf_counter() - call_start,
            timing=timing,
            fts_n=len(fts_ranks), semantic_n=len(semantic_ranks),
            overlap_n=overlap_n, sem_only_top_rank=sem_only_rank,
        )

        return results

    @staticmethod
    def _log_semantic_profile(
        *,
        query: str,
        scope: Optional[str],
        limit: int,
        total: float,
        timing: dict[str, float],
        similar_n: int,
        rows_n: int,
        results_n: int,
        store_available: bool,
        cache_hit: bool,
        model_loaded: bool,
    ) -> None:
        # WARNING level is intentional while this diagnostic slice is active —
        # default scripts/run.py log level is WARNING. Downgrade to INFO once
        # the scope=None semantic tail culprit is isolated and this slice
        # closes out.
        logger.warning(
            "[SEMANTIC_PROFILE] total=%.3fs query=%r scope=%s limit=%d "
            "init=%.3fs embed=%.3fs vec_search=%.3fs hydrate=%.3fs filter=%.3fs "
            "similar_n=%d rows_n=%d results_n=%d store_available=%s "
            "cache_hit=%s model_loaded=%s",
            total, query[:60], scope, limit,
            timing["init"], timing["embed"], timing["vec_search"],
            timing["hydrate"], timing["filter"],
            similar_n, rows_n, results_n,
            "true" if store_available else "false",
            "true" if cache_hit else "false",
            "true" if model_loaded else "false",
        )

    @staticmethod
    def _log_hybrid_profile(
        *,
        query: str,
        scope: Optional[str],
        limit: int,
        total: float,
        timing: dict[str, float],
        fts_n: int,
        semantic_n: int,
        overlap_n: int,
        sem_only_top_rank: Optional[int],
    ) -> None:
        # WARNING level is intentional while this diagnostic slice is active —
        # default scripts/run.py log level is WARNING. Downgrade to INFO once
        # the hybrid backend culprit is isolated and this slice closes out.
        logger.warning(
            "[HYBRID_PROFILE] total=%.3fs query=%r scope=%s limit=%d "
            "fts=%.3fs semantic=%.3fs rank_maps=%.3fs rrf=%.3fs sort=%.3fs "
            "fts_n=%d semantic_n=%d overlap_n=%d sem_only_top_rank=%s",
            total, query[:60], scope, limit,
            timing["fts"], timing["semantic"],
            timing["rank_maps"], timing["rrf"], timing["sort"],
            fts_n, semantic_n, overlap_n,
            sem_only_top_rank if sem_only_top_rank is not None else "-",
        )

    @staticmethod
    def _log_fts_profile(
        *,
        query: str,
        scope: Optional[str],
        limit: int,
        total: float,
        timing: dict[str, float],
        safe_query: str,
        raw_words_n: int,
        meaningful_n: int,
        top_terms_n: int,
        rows_n: int,
        node_type_filter: Optional[str],
    ) -> None:
        # WARNING level is intentional while this diagnostic slice is active —
        # default scripts/run.py log level is WARNING. Downgrade to INFO once
        # the global FTS tail is isolated and this slice closes out.
        logger.warning(
            "[FTS_PROFILE] total=%.3fs query=%r scope=%s scope_filter=%s limit=%d "
            "exists_check=%.3fs shape_query=%.3fs sql_fetch=%.3fs hydrate=%.3fs "
            "safe_query=%r raw_words_n=%d meaningful_n=%d top_terms_n=%d rows_n=%d "
            "node_type_filter=%s excluded_types_n=%d",
            total, query[:60], scope,
            "none" if scope is None else scope,
            limit,
            timing["exists_check"], timing["shape_query"],
            timing["sql_fetch"], timing["hydrate"],
            safe_query, raw_words_n, meaningful_n, top_terms_n, rows_n,
            node_type_filter if node_type_filter else "-",
            len(RETRIEVAL_EXCLUDED_TYPES),
        )

    @staticmethod
    def _log_matrix_score_trace(
        *,
        node_id: str,
        rrf: float,
        degree: int,
        freq_boost: float,
        decay: float,
        owner_boost: float,
        composite: float,
    ) -> None:
        # Diagnostic-only: decomposes composite = rrf * freq_boost * decay
        # (* owner_boost) so the tail that keeps Matrix below 0.25 ingest
        # is measurable. Gated by LUNA_MATRIX_SCORE_TRACE; pays zero cost
        # on the hot retrieval path unless active profiling is requested.
        logger.warning(
            "[MATRIX-SCORE-TRACE] id=%s rrf=%.4f degree=%d "
            "freq_boost=%.3f decay=%.3f owner_boost=%.1f composite=%.4f",
            node_id[:20], rrf, degree,
            freq_boost, decay, owner_boost, composite,
        )

    async def _fts_explain_plan(
        self,
        *,
        sql: str,
        params: tuple,
        scope: Optional[str],
        safe_query: str,
        limit: int,
    ) -> None:
        # Diagnostic-only: runs EXPLAIN QUERY PLAN on the exact SQL +
        # params used by fts5_search(). Gated by LUNA_FTS_EXPLAIN so the
        # hot path pays no cost unless active profiling is requested.
        try:
            plan_rows = await self.db.fetchall(
                f"EXPLAIN QUERY PLAN {sql}", params
            )
        except Exception as exc:
            logger.warning(
                "[FTS_PLAN] explain_failed scope=%s limit=%d safe_query=%r err=%r",
                scope, limit, safe_query, exc,
            )
            return
        # EXPLAIN QUERY PLAN row shape: (id, parent, notused, detail)
        details = " | ".join(str(row[-1]) for row in plan_rows)
        logger.warning(
            "[FTS_PLAN] scope=%s scope_filter=%s limit=%d safe_query=%r plan=%r",
            scope, "none" if scope is None else scope,
            limit, safe_query, details,
        )

    async def get_context(
        self,
        query: str,
        max_tokens: int = 2000,
        node_types: Optional[list[str]] = None,
        scope: Optional[str] = None,
        scopes: Optional[list[str]] = None,
    ) -> list[MemoryNode]:
        """
        Get relevant context for a query.

        Uses hybrid_search (FTS5 + semantic with RRF ranking) — the same
        proven path as /memory/search — then trims to token budget.

        Args:
            query: The query to find context for
            max_tokens: Maximum tokens worth of context (approximate)
            node_types: Optional list of node types to include
            scope: Single scope filter. None = all scopes.
            scopes: Multi-scope list (e.g. ["global", "project:crooked-nail"]).
                    When provided, queries each scope separately and merges results.
                    Overrides `scope` if both provided.

        Returns:
            List of relevant MemoryNodes within token budget
        """
        # Multi-scope: query each scope and merge with budget split
        if scopes and len(scopes) > 1:
            return await self._get_context_multi_scope(query, max_tokens, node_types, scopes)

        # Single scope (or all scopes if scope=None)
        effective_scope = scopes[0] if scopes else scope
        results, _timing = await self._get_context_profiled(
            query, max_tokens, node_types, effective_scope
        )
        return results

    async def _get_context_profiled(
        self,
        query: str,
        max_tokens: int,
        node_types: Optional[list[str]],
        effective_scope: Optional[str],
    ) -> tuple[list[MemoryNode], dict[str, float]]:
        """
        Single-scope `get_context` body, instrumented with per-stage timing.

        Diagnostic slice: emits one `[MATRIX_GET_CONTEXT]` log line per call
        and returns the timing dict alongside the result set so a focused
        harness can consume it without re-parsing logs. Behavior is
        identical to `get_context` — no node ordering or filtering differs.
        Downgrade / remove logging once the dominant cost center is known.
        """
        timing: dict[str, float] = {
            "hybrid": 0.0, "filter": 0.0, "composite": 0.0,
            "fill": 0.0, "access": 0.0, "access_count": 0.0,
            "graph_traverse": 0.0, "graph_fetch": 0.0, "graph_append": 0.0,
        }
        call_start = time.perf_counter()

        chars_per_token = 4
        max_chars = max_tokens * chars_per_token

        # ─── Use hybrid_search: the same path /memory/search uses ───
        # This gives us FTS5 (BM25) + semantic search with RRF ranking,
        # instead of the old LIKE-with-stopwords approach that was failing.
        hybrid_start = time.perf_counter()
        try:
            scored_results = await self.hybrid_search(
                query,
                node_type=node_types[0] if node_types and len(node_types) == 1 else None,
                limit=50,
                scope=effective_scope,
            )
        except Exception as e:
            logger.warning(f"hybrid_search failed, falling back to fts5: {e}")
            try:
                scored_results = await self.fts5_search(
                    query, limit=50, scope=effective_scope
                )
            except Exception as e2:
                logger.error(f"fts5_search also failed: {e2}")
                scored_results = []
        timing["hybrid"] = time.perf_counter() - hybrid_start

        if not scored_results:
            logger.warning(
                f"GET_CONTEXT_ZERO: hybrid_search returned 0 results for "
                f"query='{query[:80]}', scope={effective_scope}"
            )
            total_elapsed = time.perf_counter() - call_start
            self._log_matrix_get_context(
                query=query, scope=effective_scope, total=total_elapsed,
                results=0, chars=0, timing=timing,
            )
            return [], timing

        # ─── Filter by node_types if multiple were requested ───
        filter_start = time.perf_counter()
        if node_types and len(node_types) > 1:
            type_set = set(node_types)
            scored_results = [(n, s) for n, s in scored_results if n.node_type in type_set]
        timing["filter"] = time.perf_counter() - filter_start

        # ─── Three-signal composite scoring (Geometric Layer 1) ───
        # score = similarity * (1 + log(1 + degree)) * recency_decay
        composite_start = time.perf_counter()
        from ..core.owner import owner_entity_id
        owner_eid = owner_entity_id()

        scored_with_composite: list[tuple[MemoryNode, float]] = []
        for node, rrf_score in scored_results:
            content_lower = node.content.lower()

            # Filter: system prompt dumps (not knowledge)
            if "# luna's foundation" in content_lower or "# luna's core identity" in content_lower:
                continue

            # Filter: raw SESSION blobs > 2000 chars with headers
            stripped = node.content.strip()
            if (stripped.startswith("###") or stripped.startswith("## ")) and node.node_type == "SESSION" and len(stripped) > 2000:
                continue

            # Filter: raw conversation log prefixes
            if stripped.startswith("User (desktop):") or stripped.startswith("User (mobile):"):
                continue

            # Composite score: similarity × frequency boost × recency decay
            degree = self._get_degree(node.id)
            freq_boost = 1 + math.log(1 + degree)
            decay = _recency_decay(node.created_at)
            composite = rrf_score * freq_boost * decay

            # Owner entity boost — structurally prevents identity confusion
            owner_boost = 1.0
            if owner_eid and node.id == owner_eid:
                owner_boost = 2.0
                composite *= owner_boost

            if os.getenv("LUNA_MATRIX_SCORE_TRACE"):
                self._log_matrix_score_trace(
                    node_id=node.id,
                    rrf=rrf_score,
                    degree=degree,
                    freq_boost=freq_boost,
                    decay=decay,
                    owner_boost=owner_boost,
                    composite=composite,
                )

            scored_with_composite.append((node, composite))

        # Re-sort by composite score (strongest first)
        scored_with_composite.sort(key=lambda x: x[1], reverse=True)

        # Composite-score floor. Catches nodes that survived the per-engine
        # similarity threshold but are still low-signal contributions for the
        # query (e.g. for "what???", a node that barely cleared 0.45 cosine and
        # got amplified by frequency_boost would otherwise leak into the
        # prompt and the model confabulates around it). Tunable via
        # LUNA_MATRIX_MIN_COMPOSITE env var; default 0.015 is calibrated for
        # the typical RRF*freq_boost*decay product range.
        _min_composite = float(os.getenv("LUNA_MATRIX_MIN_COMPOSITE", "0.015"))
        if _min_composite > 0:
            _pre_floor = len(scored_with_composite)
            scored_with_composite = [
                (n, s) for n, s in scored_with_composite if s >= _min_composite
            ]
            if _pre_floor != len(scored_with_composite):
                logger.info(
                    "COMPOSITE_FLOOR: dropped %d/%d below threshold=%.4f",
                    _pre_floor - len(scored_with_composite), _pre_floor, _min_composite,
                )
        timing["composite"] = time.perf_counter() - composite_start

        if scored_with_composite:
            top = scored_with_composite[0]
            logger.debug(
                "COMPOSITE_TOP: id=%s score=%.4f (of %d candidates)",
                top[0].id[:20], top[1], len(scored_with_composite),
            )

        # ─── Fill token budget ───
        # `fill` measures the wall time of the loop *excluding* access writes;
        # `access` is the cumulative cost of `record_access()` itself. This
        # separation is the whole point of the slice — access currently runs
        # inline and may be the dominant cost center.
        results: list[MemoryNode] = []
        total_chars = 0
        access_accum = 0.0
        access_count = 0
        fill_start = time.perf_counter()

        for node, composite in scored_with_composite:
            node_chars = len(node.content) + (len(node.summary) if node.summary else 0)

            if total_chars + node_chars > max_chars:
                break

            node._retrieval_score = composite  # transient attr for assembler
            results.append(node)
            total_chars += node_chars

            # Record access for lock-in scoring — timed separately.
            access_start = time.perf_counter()
            await self.record_access(node.id)
            access_accum += time.perf_counter() - access_start
            access_count += 1

        fill_wall = time.perf_counter() - fill_start
        timing["fill"] = max(0.0, fill_wall - access_accum)
        timing["access"] = access_accum
        timing["access_count"] = float(access_count)

        # ─── Graph expansion: typed 1-hop traversal (Phase 2 Slice 2) ───
        # Typed, bounded, deterministic. Allowlist: SUPPORTS, CONTRADICTS.
        # Traversal rank (strength * lock_in) is preserved through hydration —
        # do NOT re-sort by lock_in after fetch; that would throw traversal away.
        if results and self.graph:
            try:
                seed_ids = [n.id for n in results[:10]]
                existing_ids = {n.id for n in results}
                traverse_start = time.perf_counter()
                expansion = await self.graph.traverse_typed(
                    seed_ids,
                    scope=effective_scope,
                    excluded_node_types=RETRIEVAL_EXCLUDED_TYPES,
                )
                timing["graph_traverse"] = time.perf_counter() - traverse_start
                expansion_by_id = {
                    e["related_id"]: e for e in expansion
                    if e["related_id"] not in existing_ids
                }
                candidate_ids = list(expansion_by_id.keys())
                if candidate_ids:
                    placeholders = ",".join("?" * len(candidate_ids))
                    excluded = list(RETRIEVAL_EXCLUDED_TYPES)
                    excl_ph = ",".join("?" * len(excluded))
                    scope_clause = " AND scope = ?" if effective_scope else ""
                    scope_params = [effective_scope] if effective_scope else []
                    fetch_start = time.perf_counter()
                    # No ORDER BY — reordered by traversal rank below.
                    graph_rows = await self.db.fetchall(
                        f"SELECT * FROM memory_nodes WHERE id IN ({placeholders}) "
                        f"AND node_type NOT IN ({excl_ph})"
                        f"{scope_clause}",
                        tuple(candidate_ids + excluded + scope_params),
                    )
                    timing["graph_fetch"] = time.perf_counter() - fetch_start
                    append_start = time.perf_counter()
                    # Hydrate once, then preserve traversal rank order.
                    hydrated = [MemoryNode.from_row(row) for row in graph_rows]
                    node_by_id = {n.id: n for n in hydrated if n is not None}
                    ordered_nodes = [
                        node_by_id[cid] for cid in candidate_ids if cid in node_by_id
                    ]
                    added = 0
                    for node in ordered_nodes:
                        node_chars = len(node.content)
                        if total_chars + node_chars > max_chars:
                            break
                        # Tag the node with typed-traversal provenance so
                        # UnifiedRetrieval can surface it as a first-class
                        # `source="graph"` candidate instead of silently
                        # masquerading as a direct matrix hit. Also carry the
                        # traversal rank into `_retrieval_score` so it lands
                        # in the shared ingest domain rather than the default
                        # 1.0 fallback at the retrieval boundary.
                        meta = expansion_by_id.get(node.id)
                        if meta is not None:
                            node._graph_expansion = {
                                "seed_id": meta["seed_id"],
                                "edge_type": meta["edge_type"],
                                "strength": meta["strength"],
                                "rank_score": meta["rank_score"],
                            }
                            node._retrieval_score = meta["rank_score"]
                        results.append(node)
                        total_chars += node_chars
                        added += 1
                    timing["graph_append"] = time.perf_counter() - append_start
                    logger.info(
                        f"GRAPH_EXPAND_TYPED: seeds={len(seed_ids)} "
                        f"candidates={len(candidate_ids)} "
                        f"hydrated={len(ordered_nodes)} added={added}"
                    )
            except Exception as e:
                logger.warning(f"GRAPH_EXPAND_FAIL: {type(e).__name__}: {e}")

        total_elapsed = time.perf_counter() - call_start
        self._log_matrix_get_context(
            query=query, scope=effective_scope, total=total_elapsed,
            results=len(results), chars=total_chars, timing=timing,
        )
        return results, timing

    @staticmethod
    def _log_matrix_get_context(
        *,
        query: str,
        scope: Optional[str],
        total: float,
        results: int,
        chars: int,
        timing: dict[str, float],
    ) -> None:
        # WARNING level is intentional while this diagnostic slice is active —
        # default scripts/run.py log level is WARNING. Downgrade to INFO once
        # the hybrid backend culprit is isolated and both diagnostic slices
        # close out.
        logger.warning(
            "[MATRIX_GET_CONTEXT] total=%.3fs query=%r scope=%s results=%d chars=%d "
            "hybrid=%.3fs filter=%.3fs composite=%.3fs fill=%.3fs "
            "access=%.3fs access_count=%d "
            "graph_traverse=%.3fs graph_fetch=%.3fs graph_append=%.3fs",
            total, query[:60], scope, results, chars,
            timing["hybrid"], timing["filter"], timing["composite"],
            timing["fill"], timing["access"], int(timing["access_count"]),
            timing["graph_traverse"], timing["graph_fetch"], timing["graph_append"],
        )

    async def _get_context_multi_scope(
        self,
        query: str,
        max_tokens: int,
        node_types: Optional[list[str]],
        scopes: list[str],
    ) -> list[MemoryNode]:
        """
        Get context from multiple scopes with budget splitting.

        For project + global queries: 60% tokens to project scope, 40% to global.
        Deduplicates by node ID across scopes.
        """
        project_scopes = [s for s in scopes if s.startswith("project:")]
        global_scopes = [s for s in scopes if s == "global"]

        # Budget split: project gets priority
        if project_scopes and global_scopes:
            project_budget = int(max_tokens * 0.6)
            global_budget = max_tokens - project_budget
        else:
            # Only one type of scope
            project_budget = max_tokens
            global_budget = max_tokens

        all_results: list[MemoryNode] = []
        seen_ids: set[str] = set()

        # Query project scopes first (higher priority)
        for ps in project_scopes:
            nodes = await self.get_context(
                query, max_tokens=project_budget, node_types=node_types, scope=ps
            )
            for node in nodes:
                if node.id not in seen_ids:
                    seen_ids.add(node.id)
                    all_results.append(node)

        # Then global scope
        for gs in global_scopes:
            nodes = await self.get_context(
                query, max_tokens=global_budget, node_types=node_types, scope=gs
            )
            for node in nodes:
                if node.id not in seen_ids:
                    seen_ids.add(node.id)
                    all_results.append(node)

        return all_results

    async def get_nodes_by_type(
        self,
        node_type: str,
        limit: int = 100,
    ) -> list[MemoryNode]:
        """
        Get all nodes of a specific type.

        Args:
            node_type: The type to filter by
            limit: Maximum number of results

        Returns:
            List of MemoryNodes of the specified type
        """
        rows = await self.db.fetchall(
            """
            SELECT * FROM memory_nodes
            WHERE node_type = ? AND namespace = 'active'
            ORDER BY importance DESC, created_at DESC
            LIMIT ?
            """,
            (node_type, limit)
        )
        return [MemoryNode.from_row(row) for row in rows]

    async def get_recent_nodes(self, limit: int = 20) -> list[MemoryNode]:
        """
        Get the most recently created nodes.

        Args:
            limit: Maximum number of results

        Returns:
            List of recent MemoryNodes
        """
        rows = await self.db.fetchall(
            """
            SELECT * FROM memory_nodes
            WHERE namespace = 'active'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        return [MemoryNode.from_row(row) for row in rows]

    # =========================================================================
    # CONVERSATION OPERATIONS
    # =========================================================================

    async def add_conversation_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        tokens: Optional[int] = None,
        turn_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> int:
        """
        Add a conversation turn to history.

        Args:
            session_id: The conversation session ID
            role: Who said this (user, assistant, system)
            content: The message content
            tokens: Optional token count
            turn_type: Voice v2.0 Step 5 turn-type taxonomy value. Defaults to
                the role-appropriate NORMAL_* value when omitted.
            metadata: Optional JSON-serializable dict (used by INTERRUPT_UTTERANCE
                writes to carry classification + confidence).

        Returns:
            The ID of the created turn
        """
        from luna.core.turn_types import TurnType  # local import avoids cycles

        now = datetime.now().isoformat()
        turn_type = TurnType.normalize_for_role(role, turn_type).value
        metadata_json = json.dumps(metadata) if metadata else None

        result = await self.db.execute(
            """
            INSERT INTO conversation_turns (
                session_id, role, content, tokens, created_at, turn_type, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, role, content, tokens, now, turn_type, metadata_json)
        )

        turn_id = result.lastrowid
        logger.debug(f"Added conversation turn: {turn_id} ({role}, {turn_type})")
        return turn_id

    async def get_preceding_turns(
        self,
        session_id: str,
        before_turn_id: int,
        limit: int = 2,
    ) -> list[Turn]:
        """
        Voice v2.0 Step 5 — fetch the `limit` turns immediately preceding
        `before_turn_id` in the given session, reverse-chronological (most
        recent first). Used by ConversationConsolidator to walk back from a
        RESUMPTION_RESPONSE and detect the interrupted-exchange triplet.
        """
        rows = await self.db.fetchall(
            """
            SELECT * FROM conversation_turns
            WHERE session_id = ? AND id < ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, before_turn_id, limit)
        )
        return [Turn.from_row(row) for row in rows]

    async def get_preceding_turn(
        self,
        session_id: str,
        before_turn_id: int,
    ) -> Optional[Turn]:
        """Voice v2.0 Step 5 — single-turn variant of get_preceding_turns."""
        turns = await self.get_preceding_turns(session_id, before_turn_id, limit=1)
        return turns[0] if turns else None

    async def get_recent_turns(
        self,
        session_id: str,
        limit: int = 10,
    ) -> list[Turn]:
        """
        Get recent conversation turns for a session.

        Args:
            session_id: The conversation session ID
            limit: Maximum number of turns (default 10)

        Returns:
            List of Turns in chronological order
        """
        rows = await self.db.fetchall(
            """
            SELECT * FROM conversation_turns
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, limit)
        )

        # Reverse to get chronological order
        turns = [Turn.from_row(row) for row in rows]
        turns.reverse()
        return turns

    async def get_session_history(self, session_id: str) -> list[Turn]:
        """
        Get full conversation history for a session.

        Args:
            session_id: The conversation session ID

        Returns:
            List of all Turns in chronological order
        """
        rows = await self.db.fetchall(
            """
            SELECT * FROM conversation_turns
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,)
        )
        return [Turn.from_row(row) for row in rows]

    # =========================================================================
    # ACCESS TRACKING
    # =========================================================================

    async def record_access(self, node_id: str) -> None:
        """
        Record that a node was accessed.

        Increments access_count, updates last_accessed timestamp,
        and recalculates lock-in coefficient (including network effects).

        Args:
            node_id: The node's unique identifier
        """
        from .lock_in import compute_lock_in_for_node

        # First increment access count
        await self.db.execute(
            """
            UPDATE memory_nodes
            SET access_count = access_count + 1,
                last_accessed = datetime('now')
            WHERE id = ?
            """,
            (node_id,)
        )

        # Get updated counts for lock-in calculation
        row = await self.db.fetchone(
            "SELECT access_count, reinforcement_count FROM memory_nodes WHERE id = ?",
            (node_id,)
        )

        if row:
            access_count, reinforcement_count = row
            lock_in, state = await compute_lock_in_for_node(
                node_id=node_id,
                retrieval_count=access_count,
                reinforcement_count=reinforcement_count,
                graph=self.graph,
                db=self.db,
            )

            await self.db.execute(
                "UPDATE memory_nodes SET lock_in = ?, lock_in_state = ? WHERE id = ?",
                (lock_in, state.value, node_id)
            )

    async def get_most_accessed(self, limit: int = 10) -> list[MemoryNode]:
        """
        Get the most frequently accessed nodes.

        Args:
            limit: Maximum number of results

        Returns:
            List of most accessed MemoryNodes
        """
        rows = await self.db.fetchall(
            """
            SELECT * FROM memory_nodes
            WHERE namespace = 'active'
            ORDER BY access_count DESC
            LIMIT ?
            """,
            (limit,)
        )
        return [MemoryNode.from_row(row) for row in rows]

    async def reinforce_node(self, node_id: str) -> bool:
        """
        Explicitly reinforce a memory node.

        This is for user-initiated reinforcement ("this is important").
        Increments reinforcement_count and recalculates lock-in.

        Args:
            node_id: The node's unique identifier

        Returns:
            True if node was reinforced, False if not found
        """
        from .lock_in import compute_lock_in_for_node

        # Increment reinforcement count
        result = await self.db.execute(
            "UPDATE memory_nodes SET reinforcement_count = reinforcement_count + 1 WHERE id = ?",
            (node_id,)
        )

        if result.rowcount == 0:
            return False

        # Get updated counts for lock-in calculation
        row = await self.db.fetchone(
            "SELECT access_count, reinforcement_count FROM memory_nodes WHERE id = ?",
            (node_id,)
        )

        if row:
            access_count, reinforcement_count = row
            lock_in, state = await compute_lock_in_for_node(
                node_id=node_id,
                retrieval_count=access_count,
                reinforcement_count=reinforcement_count,
                graph=self.graph,
                db=self.db,
            )

            await self.db.execute(
                "UPDATE memory_nodes SET lock_in = ?, lock_in_state = ? WHERE id = ?",
                (lock_in, state.value, node_id)
            )

        logger.debug(f"Reinforced memory node: {node_id}")
        return True

    async def get_nodes_by_lock_in_state(
        self,
        state: str,
        limit: int = 100,
    ) -> list[MemoryNode]:
        """
        Get nodes filtered by lock-in state.

        Args:
            state: 'drifting', 'fluid', or 'settled'
            limit: Maximum number of results

        Returns:
            List of MemoryNodes in that state
        """
        rows = await self.db.fetchall(
            """
            SELECT * FROM memory_nodes
            WHERE lock_in_state = ? AND namespace = 'active'
            ORDER BY lock_in DESC
            LIMIT ?
            """,
            (state, limit)
        )
        return [MemoryNode.from_row(row) for row in rows]

    async def get_drifting_nodes(self, limit: int = 100) -> list[MemoryNode]:
        """Get nodes that are drifting (candidates for pruning)."""
        return await self.get_nodes_by_lock_in_state("drifting", limit)

    async def get_settled_nodes(self, limit: int = 100) -> list[MemoryNode]:
        """Get nodes that are settled (core knowledge)."""
        return await self.get_nodes_by_lock_in_state("settled", limit)

    # =========================================================================
    # STATISTICS
    # =========================================================================

    async def get_stats(self) -> dict:
        """
        Get statistics about the memory matrix.

        Returns:
            Dictionary with stats including:
            - total_nodes: Total number of memory nodes
            - nodes_by_type: Count of nodes per type
            - total_turns: Total conversation turns
            - total_sessions: Unique session count
            - avg_confidence: Average confidence score
            - avg_importance: Average importance score
        """
        # Total nodes
        total_row = await self.db.fetchone(
            "SELECT COUNT(*) FROM memory_nodes"
        )
        total_nodes = total_row[0] if total_row else 0

        # Nodes by type
        type_rows = await self.db.fetchall(
            """
            SELECT node_type, COUNT(*)
            FROM memory_nodes
            GROUP BY node_type
            """
        )
        nodes_by_type = {row[0]: row[1] for row in type_rows}

        # Total turns
        turns_row = await self.db.fetchone(
            "SELECT COUNT(*) FROM conversation_turns"
        )
        total_turns = turns_row[0] if turns_row else 0

        # Unique sessions
        sessions_row = await self.db.fetchone(
            "SELECT COUNT(DISTINCT session_id) FROM conversation_turns"
        )
        total_sessions = sessions_row[0] if sessions_row else 0

        # Average scores
        averages_row = await self.db.fetchone(
            """
            SELECT
                AVG(confidence),
                AVG(importance),
                AVG(access_count)
            FROM memory_nodes
            """
        )

        avg_confidence = averages_row[0] if averages_row and averages_row[0] else 0.0
        avg_importance = averages_row[1] if averages_row and averages_row[1] else 0.0
        avg_access_count = averages_row[2] if averages_row and averages_row[2] else 0.0

        # Lock-in state distribution
        lock_in_rows = await self.db.fetchall(
            """
            SELECT lock_in_state, COUNT(*)
            FROM memory_nodes
            GROUP BY lock_in_state
            """
        )
        nodes_by_lock_in = {row[0]: row[1] for row in lock_in_rows}

        # Average lock-in
        avg_lock_in_row = await self.db.fetchone(
            "SELECT AVG(lock_in) FROM memory_nodes"
        )
        avg_lock_in = avg_lock_in_row[0] if avg_lock_in_row and avg_lock_in_row[0] else 0.15

        # Total edges
        edges_row = await self.db.fetchone(
            "SELECT COUNT(*) FROM graph_edges"
        )
        total_edges = edges_row[0] if edges_row else 0

        return {
            "total_nodes": total_nodes,
            "nodes_by_type": nodes_by_type,
            "nodes_by_lock_in": nodes_by_lock_in,
            "total_turns": total_turns,
            "total_sessions": total_sessions,
            "avg_confidence": avg_confidence,
            "avg_importance": avg_importance,
            "avg_access_count": avg_access_count,
            "avg_lock_in": avg_lock_in,
            "total_edges": total_edges,
        }

    # =========================================================================
    # OPINION OPERATIONS
    # =========================================================================

    async def store_opinion(
        self,
        content: str,
        subject: str,
        session_id: Optional[str] = None,
        confidence: float = 0.5,
        source_turn_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        """Store an OPINION node and return its node_id."""
        meta = {**(metadata or {}), "subject": subject, "superseded": False}
        if session_id:
            meta["session_id"] = session_id
        if source_turn_id:
            meta["source_turn_id"] = source_turn_id
        return await self.add_node(
            node_type="OPINION",
            content=content,
            confidence=confidence,
            metadata=meta,
        )

    async def get_past_positions(
        self,
        subject: str,
        limit: int = 10,
        include_superseded: bool = False,
    ) -> list[dict]:
        """Retrieve OPINION nodes for a subject, newest first.

        Used by NarrationLayer to detect position conflicts.
        """
        rows = await self.db.fetchall(
            """
            SELECT id, content, confidence, created_at, metadata
            FROM memory_nodes
            WHERE node_type = 'OPINION' AND namespace = 'active'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit * 5,),  # over-fetch; subject filter below
        )
        results = []
        for row in rows:
            try:
                meta = json.loads(row[4]) if row[4] else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}
            if meta.get("subject") != subject:
                continue
            superseded = bool(meta.get("superseded", False))
            if superseded and not include_superseded:
                continue
            results.append({
                "node_id": row[0],
                "content": row[1],
                "confidence": row[2],
                "created_at": row[3],
                "superseded": superseded,
            })
            if len(results) >= limit:
                break
        return results

    async def supersede_opinion(self, old_node_id: str, new_node_id: str) -> None:
        """Mark old_node_id as superseded and add a SUPERSEDES edge from new to old."""
        old_node = await self.get_node(old_node_id)
        if old_node is None:
            logger.warning(f"supersede_opinion: old node {old_node_id!r} not found")
            raise ValueError(f"Opinion node {old_node_id!r} not found")

        meta = old_node.metadata.copy() if isinstance(old_node.metadata, dict) else {}
        meta["superseded"] = True
        meta["superseded_by"] = new_node_id
        await self.update_node(old_node_id, metadata=meta)

        now = datetime.now().isoformat()
        await self.db.execute(
            """
            INSERT OR IGNORE INTO graph_edges
                (from_id, to_id, relationship, strength, created_at, origin)
            VALUES (?, ?, 'SUPERSEDES', 1.0, ?, 'system')
            """,
            [new_node_id, old_node_id, now],
        )
        logger.debug(f"supersede_opinion: {old_node_id!r} → superseded by {new_node_id!r}")

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def close(self) -> None:
        """Close the underlying database connection."""
        await self.db.close()
        logger.info("MemoryMatrix closed")
