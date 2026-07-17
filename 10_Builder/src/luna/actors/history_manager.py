"""
History Manager Actor - Manages three-tier conversation history.

Tiers:
- Active: Last 5-10 turns, always loaded (~1000 tokens max)
- Recent: Last 50-100 turns, compressed summaries, searchable
- Archive: Extracted to Memory Matrix

Responsibilities:
- Monitor Active Window token budget
- Rotate Active -> Recent when budget exceeded
- Queue compression for Recent turns
- Queue extraction for archival
- Generate and store embeddings for semantic search
"""

from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
import logging
import time
import uuid
import struct

from luna.actors.base import Actor, Message

logger = logging.getLogger(__name__)


def _vector_to_blob(vector: list[float]) -> bytes:
    """Convert a float vector to sqlite-vec compatible blob format."""
    return struct.pack(f'{len(vector)}f', *vector)


def _blob_to_vector(blob: bytes) -> list[float]:
    """Convert a sqlite-vec blob back to a float vector."""
    n_floats = len(blob) // 4  # 4 bytes per float
    return list(struct.unpack(f'{n_floats}f', blob))


@dataclass
class HistoryConfig:
    """Configuration for conversation history management."""
    max_active_tokens: int = 1000
    max_active_turns: int = 10
    max_recent_age_minutes: int = 60
    compression_enabled: bool = True
    default_search_limit: int = 3
    search_type: str = "hybrid"  # "hybrid", "keyword", or "semantic"
    app_context: str = "terminal"


class HistoryManagerActor(Actor):
    """Manages the three-tier conversation history system."""

    def __init__(self, config: Optional[HistoryConfig] = None, engine=None):
        super().__init__("history_manager", engine)
        self.config = config or HistoryConfig()

        # Stats
        self._turns_added = 0
        self._rotations = 0
        self._compressions_queued = 0
        self._extractions_queued = 0
        self._searches_performed = 0
        self._embeddings_generated = 0

        # State
        self._current_session_id: Optional[str] = None
        self._is_ready = False
        self._embedding_generator = None  # Lazy init

    @property
    def is_ready(self) -> bool:
        return self._is_ready

    async def on_start(self):
        """Initialize connection to matrix."""
        logger.info("HistoryManager starting...")
        matrix = await self._get_matrix()
        if matrix:
            logger.info("HistoryManager connected to memory matrix")
            self._is_ready = True
            await self._reclaim_stuck_queue_rows(matrix)
        else:
            logger.warning("HistoryManager: Matrix not available yet")

    async def _reclaim_stuck_queue_rows(self, matrix) -> None:
        """Reset orphaned 'processing' rows to 'pending' on startup.

        Background compression/extraction tasks spawned via asyncio.create_task
        don't survive engine restarts. Any row left in 'processing' is a zombie
        from the prior boot. Resetting to 'pending' makes the tick-based
        consumer pick them up again.
        """
        try:
            for table in ("compression_queue", "extraction_queue"):
                cursor = await matrix.db.execute_with_retry(
                    f"UPDATE {table} SET status = 'pending' "
                    f"WHERE status = 'processing'"
                )
                reclaimed = getattr(cursor, "rowcount", 0) or 0
                if reclaimed:
                    logger.info(
                        f"HistoryManager: reclaimed {reclaimed} stuck rows in {table}"
                    )
        except Exception as e:
            logger.warning(f"HistoryManager: queue reclamation skipped: {e}")

    async def handle(self, msg) -> None:
        # All access is via direct method calls — mailbox unused
        logger.warning(f"HistoryManagerActor: unexpected mailbox message: {msg.type if hasattr(msg, 'type') else msg}")

    # ========================================================================
    # DATABASE ACCESS
    # ========================================================================

    async def _get_matrix(self):
        """Get MemoryMatrix from MatrixActor."""
        if not self.engine:
            return None
        matrix_actor = self.engine.get_actor("matrix")
        if not matrix_actor:
            return None
        return (
            getattr(matrix_actor, "_matrix", None) or
            getattr(matrix_actor, "_memory", None)
        )

    # ========================================================================
    # PUBLIC INTERFACE
    # ========================================================================

    async def get_active_window(
        self,
        session_id: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all Active tier turns for context injection.
        This is called by PersonaCore when building context.
        """
        matrix = await self._get_matrix()
        if not matrix:
            return []

        sid = session_id or self._current_session_id
        lim = limit or self.config.max_active_turns

        rows = await matrix.db.fetchall(
            """SELECT id, role, content, tokens, created_at, context_refs
               FROM conversation_turns
               WHERE tier = 'active' AND (session_id = ? OR ? IS NULL)
               ORDER BY created_at ASC
               LIMIT ?""",
            (sid, sid, lim)
        )

        return [
            {
                "turn_id": r[0],
                "role": r[1],
                "content": r[2],
                "tokens": r[3],
                "timestamp": r[4],
                "context_refs": r[5]
            }
            for r in rows
        ]

    async def search_recent(
        self,
        query: str,
        limit: int = 3,
        search_type: str = "hybrid",
        session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Search Recent tier for relevant turns.
        Used when PersonaCore detects backward references.
        """
        matrix = await self._get_matrix()
        if not matrix:
            return []

        self._searches_performed += 1
        sid = session_id or self._current_session_id

        results = []

        # Keyword search via FTS5
        if search_type in ("keyword", "hybrid"):
            try:
                rows = await matrix.db.fetchall(
                    """SELECT ct.id, ct.role, ct.content, ct.compressed, ct.created_at,
                              bm25(history_fts) as score
                       FROM history_fts hf
                       JOIN conversation_turns ct ON ct.id = hf.rowid
                       WHERE history_fts MATCH ?
                         AND ct.tier = 'recent'
                         AND (ct.session_id = ? OR ? IS NULL)
                       ORDER BY score
                       LIMIT ?""",
                    (query, sid, sid, limit)
                )
                for r in rows:
                    results.append({
                        "turn_id": r[0],
                        "role": r[1],
                        "content": r[2],
                        "compressed": r[3],
                        "timestamp": r[4],
                        "relevance_score": abs(r[5]) if r[5] else 0.5,
                        "search_type": "keyword"
                    })
            except Exception as e:
                logger.warning(f"FTS5 search failed, falling back: {e}")
                # Fallback to LIKE search
                rows = await matrix.db.fetchall(
                    """SELECT id, role, content, compressed, created_at
                       FROM conversation_turns
                       WHERE tier = 'recent'
                         AND (content LIKE ? OR compressed LIKE ?)
                         AND (session_id = ? OR ? IS NULL)
                       ORDER BY created_at DESC
                       LIMIT ?""",
                    (f"%{query}%", f"%{query}%", sid, sid, limit)
                )
                for r in rows:
                    results.append({
                        "turn_id": r[0],
                        "role": r[1],
                        "content": r[2],
                        "compressed": r[3],
                        "timestamp": r[4],
                        "relevance_score": 0.5,
                        "search_type": "keyword_fallback"
                    })

        # Semantic search via history_embeddings
        if search_type in ("semantic", "hybrid"):
            semantic_results = await self._semantic_search_recent(
                query=query,
                limit=limit,
                session_id=sid
            )
            # For hybrid, merge and dedupe by turn_id
            if search_type == "hybrid":
                seen_ids = {r["turn_id"] for r in results}
                for sr in semantic_results:
                    if sr["turn_id"] not in seen_ids:
                        results.append(sr)
                        seen_ids.add(sr["turn_id"])
            else:
                # Pure semantic
                results = semantic_results

        # Sort by relevance and limit
        results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        return results[:limit]

    async def add_turn(
        self,
        role: str,
        content: str,
        tokens: int,
        session_id: Optional[str] = None,
        context_refs: Optional[Dict] = None,
        turn_type: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> int:
        """
        Add a new turn to the conversation.
        Called after user message or Luna response.

        Voice v2.0 Step 5: `turn_type` + `metadata` are optional. When
        `turn_type` is omitted, the DB DEFAULT ('NORMAL_USER_TURN') applies
        via `TurnType.default_for_role`.
        """
        matrix = await self._get_matrix()
        if not matrix:
            raise RuntimeError("Matrix not available")

        sid = session_id or self._current_session_id
        if not sid:
            sid = await self.create_session()

        import json
        refs_json = json.dumps(context_refs) if context_refs else None
        metadata_json = json.dumps(metadata) if metadata else None

        from luna.core.turn_types import TurnType
        resolved_turn_type = TurnType.normalize_for_role(role, turn_type).value

        safe_tokens = int(tokens or 0)

        cursor = await matrix.db.execute_with_retry(
            """INSERT INTO conversation_turns
               (session_id, role, content, tokens, tier, created_at,
                context_refs, turn_type, metadata)
               VALUES (?, ?, ?, ?, 'active', datetime('now'), ?, ?, ?)""",
            (sid, role, content, safe_tokens, refs_json, resolved_turn_type, metadata_json)
        )
        turn_id = getattr(cursor, "lastrowid", 0) or 0

        self._turns_added += 1

        # Check if we need to rotate
        await self._check_active_budget(sid)

        return turn_id

    async def get_active_token_count(
        self,
        session_id: Optional[str] = None
    ) -> Dict[str, int]:
        """Get token count and turn count for Active tier."""
        matrix = await self._get_matrix()
        if not matrix:
            return {"total_tokens": 0, "turn_count": 0}

        sid = session_id or self._current_session_id

        row = await matrix.db.fetchone(
            """SELECT COALESCE(SUM(tokens), 0), COUNT(*)
               FROM conversation_turns
               WHERE tier = 'active' AND (session_id = ? OR ? IS NULL)""",
            (sid, sid)
        )

        return {
            "total_tokens": row[0] if row else 0,
            "turn_count": row[1] if row else 0
        }

    async def get_oldest_active_turn(
        self,
        session_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get the oldest turn in Active tier."""
        matrix = await self._get_matrix()
        if not matrix:
            return None

        sid = session_id or self._current_session_id

        row = await matrix.db.fetchone(
            """SELECT id, role, content, tokens, created_at
               FROM conversation_turns
               WHERE tier = 'active' AND (session_id = ? OR ? IS NULL)
               ORDER BY created_at ASC
               LIMIT 1""",
            (sid, sid)
        )

        if not row:
            return None

        return {
            "turn_id": row[0],
            "role": row[1],
            "content": row[2],
            "tokens": row[3],
            "timestamp": row[4]
        }

    # ========================================================================
    # SESSION MANAGEMENT
    # ========================================================================

    async def create_session(
        self,
        app_context: Optional[str] = None
    ) -> str:
        """Create a new conversation session."""
        session_id = str(uuid.uuid4())

        matrix = await self._get_matrix()
        if matrix:
            await matrix.db.execute_with_retry(
                """INSERT INTO sessions (session_id, started_at, app_context)
                   VALUES (?, ?, ?)""",
                (session_id, time.time(), app_context or self.config.app_context)
            )

        self._current_session_id = session_id
        logger.info(f"Created session: {session_id}")
        return session_id

    async def end_session(self, session_id: Optional[str] = None):
        """End a conversation session."""
        sid = session_id or self._current_session_id
        if not sid:
            return

        matrix = await self._get_matrix()
        if matrix:
            await matrix.db.execute_with_retry(
                "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
                (time.time(), sid)
            )

        if sid == self._current_session_id:
            self._current_session_id = None

        logger.info(f"Ended session: {sid}")

    async def get_active_session(self) -> Optional[Dict[str, Any]]:
        """Get the currently active session."""
        if not self._current_session_id:
            return None

        matrix = await self._get_matrix()
        if not matrix:
            return None

        row = await matrix.db.fetchone(
            """SELECT session_id, started_at, ended_at, app_context
               FROM sessions WHERE session_id = ?""",
            (self._current_session_id,)
        )

        if not row:
            return None

        return {
            "session_id": row[0],
            "started_at": row[1],
            "ended_at": row[2],
            "app_context": row[3]
        }

    # ========================================================================
    # TIER MANAGEMENT
    # ========================================================================

    async def _check_active_budget(self, session_id: str):
        """Rotate oldest Active turn to Recent if budget exceeded."""
        matrix = await self._get_matrix()
        if not matrix:
            return

        counts = await self.get_active_token_count(session_id)
        total_tokens = counts["total_tokens"]
        turn_count = counts["turn_count"]

        # Rotate while over budget
        while (total_tokens > self.config.max_active_tokens or
               turn_count > self.config.max_active_turns):
            oldest = await self.get_oldest_active_turn(session_id)
            if not oldest:
                break

            # Rotate to recent
            await self._rotate_turn_tier(oldest["turn_id"], "recent")

            total_tokens -= (oldest["tokens"] or 0)
            turn_count -= 1
            self._rotations += 1

            # Queue for compression
            if self.config.compression_enabled:
                await self.queue_compression(oldest["turn_id"])

            logger.debug(
                f"Rotated turn {oldest['turn_id']} to recent "
                f"(tokens: {total_tokens}, turns: {turn_count})"
            )

    async def _rotate_turn_tier(self, turn_id: int, new_tier: str):
        """Move a turn to a new tier."""
        matrix = await self._get_matrix()
        if not matrix:
            return

        await matrix.db.execute_with_retry(
            "UPDATE conversation_turns SET tier = ? WHERE id = ?",
            (new_tier, turn_id)
        )

    async def queue_compression(self, turn_id: int):
        """Queue a turn for compression by Ben Franklin."""
        matrix = await self._get_matrix()
        if not matrix:
            return

        await matrix.db.execute_with_retry(
            """INSERT INTO compression_queue (turn_id, queued_at, status)
               VALUES (?, ?, 'pending')""",
            (turn_id, time.time())
        )
        self._compressions_queued += 1

        # TODO: Notify Scribe actor to process compression

    async def queue_extraction(self, turn_id: int):
        """Queue a turn for extraction to Memory Matrix."""
        matrix = await self._get_matrix()
        if not matrix:
            return

        await matrix.db.execute_with_retry(
            """INSERT INTO extraction_queue (turn_id, queued_at, status)
               VALUES (?, ?, 'pending')""",
            (turn_id, time.time())
        )
        self._extractions_queued += 1

        # TODO: Notify Scribe actor to process extraction

    # ========================================================================
    # TICK-BASED PROCESSING
    # ========================================================================

    async def tick(self) -> None:
        """
        Called on every cognitive loop cycle (~500ms-1s).
        Processes one compression and one extraction per tick.
        """
        # Guard: skip if previous tick is still running
        if getattr(self, '_tick_in_progress', False):
            return
        self._tick_in_progress = True
        try:
            await self._process_compression_queue()
            await self._process_extraction_queue()
            await self._check_archivable_turns()
        finally:
            self._tick_in_progress = False

    async def _process_compression_queue(self) -> None:
        """Process one pending compression per tick."""
        matrix = await self._get_matrix()
        if not matrix:
            return

        # Get oldest pending compression
        row = await matrix.db.fetchone(
            """SELECT cq.id, cq.turn_id, ct.content, ct.role
               FROM compression_queue cq
               JOIN conversation_turns ct ON ct.id = cq.turn_id
               WHERE cq.status = 'pending'
               ORDER BY cq.queued_at ASC
               LIMIT 1"""
        )

        if not row:
            return

        queue_id, turn_id, content, role = row

        # Mark as processing (quick write, releases immediately)
        await matrix.db.execute_with_retry(
            "UPDATE compression_queue SET status = 'processing' WHERE id = ?",
            (queue_id,)
        )

        # Fire compression as background task — DO NOT await
        import asyncio
        asyncio.create_task(
            self._do_compression(queue_id, turn_id, content, role)
        )

    async def _do_compression(self, queue_id, turn_id, content, role):
        """Background: compress a turn and write result. Not called inside tick."""
        try:
            matrix = await self._get_matrix()
            if not matrix:
                return

            compressed = content[:200] + "..."  # fallback

            if self.engine:
                scribe = self.engine.get_actor("scribe")
                if scribe and hasattr(scribe, "compress_turn"):
                    compressed = await scribe.compress_turn(content, role)

            await matrix.db.execute_with_retry(
                """UPDATE conversation_turns
                   SET compressed = ?, compressed_at = ?
                   WHERE id = ?""",
                (compressed, time.time(), turn_id)
            )

            await self._generate_turn_embedding(turn_id, compressed, matrix)

            await matrix.db.execute_with_retry(
                """UPDATE compression_queue
                   SET status = 'completed', processed_at = ?
                   WHERE id = ?""",
                (time.time(), queue_id)
            )

            logger.debug(f"HistoryManager: Compressed turn {turn_id}")

        except Exception as e:
            logger.error(f"HistoryManager: Background compression failed: {e}")
            try:
                matrix = await self._get_matrix()
                if matrix:
                    await matrix.db.execute_with_retry(
                        "UPDATE compression_queue SET status = 'failed' WHERE id = ?",
                        (queue_id,)
                    )
            except Exception:
                pass

    async def _process_extraction_queue(self) -> None:
        """Process one pending extraction per tick."""
        matrix = await self._get_matrix()
        if not matrix:
            return

        # Get oldest pending extraction
        row = await matrix.db.fetchone(
            """SELECT eq.id, eq.turn_id, ct.content, ct.compressed
               FROM extraction_queue eq
               JOIN conversation_turns ct ON ct.id = eq.turn_id
               WHERE eq.status = 'pending'
               ORDER BY eq.queued_at ASC
               LIMIT 1"""
        )

        if not row:
            return

        queue_id, turn_id, content, compressed = row

        # Mark as processing
        await matrix.db.execute_with_retry(
            "UPDATE extraction_queue SET status = 'processing' WHERE id = ?",
            (queue_id,)
        )

        try:
            # Send to Scribe for extraction (which sends to Librarian)
            if self.engine:
                scribe = self.engine.get_actor("scribe")
                if scribe:
                    from luna.actors.base import Message
                    await scribe.mailbox.put(Message(
                        type="extract_text",
                        payload={
                            "text": content,
                            "source_id": f"history_turn_{turn_id}",
                            "immediate": True
                        },
                        sender="history_manager"
                    ))

            # Mark turn as archived
            await matrix.db.execute_with_retry(
                """UPDATE conversation_turns
                   SET tier = 'archived', archived_at = ?
                   WHERE id = ?""",
                (time.time(), turn_id)
            )

            # Mark extraction complete
            await matrix.db.execute_with_retry(
                """UPDATE extraction_queue
                   SET status = 'completed', processed_at = ?
                   WHERE id = ?""",
                (time.time(), queue_id)
            )

            logger.debug(f"HistoryManager: Extracted turn {turn_id} to archive")

        except Exception as e:
            logger.error(f"HistoryManager: Extraction failed for turn {turn_id}: {e}")
            await matrix.db.execute_with_retry(
                "UPDATE extraction_queue SET status = 'failed' WHERE id = ?",
                (queue_id,)
            )

    async def _check_archivable_turns(self) -> None:
        """Check for Recent turns old enough to archive."""
        matrix = await self._get_matrix()
        if not matrix:
            return

        # Find turns older than threshold (in 'recent' tier, not yet archived)
        age_threshold = time.time() - (self.config.max_recent_age_minutes * 60)

        row = await matrix.db.fetchone(
            """SELECT id FROM conversation_turns
               WHERE tier = 'recent'
                 AND compressed IS NOT NULL
                 AND compressed_at < ?
                 AND archived_at IS NULL
               ORDER BY created_at ASC
               LIMIT 1""",
            (age_threshold,)
        )

        if row:
            turn_id = row[0]
            await self.queue_extraction(turn_id)

    # ========================================================================
    # EMBEDDING GENERATION
    # ========================================================================

    async def _generate_turn_embedding(self, turn_id: int, text: str, matrix) -> bool:
        """
        Generate and store embedding for a compressed turn.

        Uses OpenAI text-embedding-3-small (1536 dims) to enable semantic search
        on the Recent tier via the history_embeddings table.

        Args:
            turn_id: The turn ID to embed
            text: The text to generate embedding for (usually compressed summary)
            matrix: The MemoryMatrix for database access

        Returns:
            True if embedding was stored successfully
        """
        if not text or len(text) < 10:
            return False

        try:
            # Lazy init the embedding generator
            if self._embedding_generator is None:
                from luna.substrate.embeddings import EmbeddingGenerator
                self._embedding_generator = EmbeddingGenerator(model="text-embedding-3-small")

            # Generate embedding
            embedding = await self._embedding_generator.generate(text)

            # Store in history_embeddings table
            blob = _vector_to_blob(embedding)
            await matrix.db.execute_with_retry(
                """INSERT OR REPLACE INTO history_embeddings (turn_id, embedding)
                   VALUES (?, ?)""",
                (turn_id, blob)
            )

            self._embeddings_generated += 1
            logger.debug(f"Generated embedding for turn {turn_id} ({len(embedding)} dims)")
            return True

        except ImportError:
            logger.warning("OpenAI not available for embedding generation")
            return False
        except Exception as e:
            logger.warning(f"Failed to generate embedding for turn {turn_id}: {e}")
            return False

    async def _semantic_search_recent(
        self,
        query: str,
        limit: int = 3,
        session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Perform semantic search on Recent tier using vector embeddings.

        Args:
            query: The search query
            limit: Max results to return
            session_id: Optional session filter

        Returns:
            List of relevant turns with similarity scores
        """
        matrix = await self._get_matrix()
        if not matrix:
            return []

        try:
            # Lazy init embedding generator
            if self._embedding_generator is None:
                from luna.substrate.embeddings import EmbeddingGenerator
                self._embedding_generator = EmbeddingGenerator(model="text-embedding-3-small")

            # Generate query embedding
            query_embedding = await self._embedding_generator.generate(query)
            query_blob = _vector_to_blob(query_embedding)

            # Search via vec_distance_cosine
            sid = session_id or self._current_session_id
            rows = await matrix.db.fetchall(
                """SELECT
                       ct.id, ct.role, ct.content, ct.compressed, ct.created_at,
                       1 - vec_distance_cosine(he.embedding, ?) AS similarity
                   FROM history_embeddings he
                   JOIN conversation_turns ct ON ct.id = he.turn_id
                   WHERE ct.tier = 'recent'
                     AND (ct.session_id = ? OR ? IS NULL)
                   ORDER BY vec_distance_cosine(he.embedding, ?) ASC
                   LIMIT ?""",
                (query_blob, sid, sid, query_blob, limit)
            )

            return [
                {
                    "turn_id": r[0],
                    "role": r[1],
                    "content": r[2],
                    "compressed": r[3],
                    "timestamp": r[4],
                    "relevance_score": r[5] if r[5] else 0.5,
                    "search_type": "semantic"
                }
                for r in rows
            ]

        except ImportError:
            logger.warning("OpenAI not available for semantic search")
            return []
        except Exception as e:
            logger.warning(f"Semantic search failed: {e}")
            return []

    # ========================================================================
    # CONTEXT BUILDING HELPERS (for PersonaCore integration)
    # ========================================================================

    def needs_recent_search(self, message: str) -> bool:
        """
        Detect if message references recent past.

        Heuristics for backward reference detection.
        """
        backward_markers = [
            "earlier", "before", "ago", "just",
            "we discussed", "you said", "you mentioned", "you told",
            "what did", "when did", "why did",
            "last time", "previously", "remember when",
            "what we", "as we", "like we"
        ]

        message_lower = message.lower()
        return any(marker in message_lower for marker in backward_markers)

    async def build_history_context(
        self,
        message: str,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build conversation history context for prompt construction.

        Returns:
            Dict with 'active_history' and optionally 'recent_history'
        """
        context = {
            "active_history": [],
            "recent_history": [],
            "total_tokens": 0
        }

        # Always get Active Window
        active = await self.get_active_window(session_id=session_id)
        context["active_history"] = active
        context["total_tokens"] = sum(t.get("tokens", 0) for t in active)

        # Conditionally search Recent if backward reference detected
        if self.needs_recent_search(message):
            recent = await self.search_recent(
                query=message,
                limit=self.config.default_search_limit,
                session_id=session_id
            )
            context["recent_history"] = recent

        return context

    # ========================================================================
    # STATS
    # ========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """Return actor statistics."""
        return {
            "turns_added": self._turns_added,
            "rotations": self._rotations,
            "compressions_queued": self._compressions_queued,
            "extractions_queued": self._extractions_queued,
            "embeddings_generated": self._embeddings_generated,
            "searches_performed": self._searches_performed,
            "current_session": self._current_session_id,
            "is_ready": self._is_ready,
            "config": asdict(self.config)
        }
