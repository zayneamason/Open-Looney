"""
Collection Annotation System — The Only Bridge
================================================

Annotations are how collection knowledge enters Luna's Memory Matrix.
They are the ONLY pathway from Aibrarian collections to native memory.

Three types:
- BOOKMARK: Mark a chunk for later reference
- NOTE: Luna's interpretation or connection
- FLAG: Needs attention or has an issue

The Wall (non-negotiable):
- Annotation nodes in Memory Matrix are tagged source=aibrarian
- They are NEVER treated as native memory during recall
- They carry provenance that traces back to the collection
- No amount of lock-in dissolves this distinction

Usage:
    from luna.substrate.collection_annotations import AnnotationEngine, AnnotationType

    engine = AnnotationEngine(db, memory_matrix, lock_in_engine)
    node_id = await engine.create(
        collection_key="dataroom",
        doc_id="uuid-here",
        annotation_type=AnnotationType.NOTE,
        content="Cross-ref project budget with grant funding",
        chunk_index=3,
        original_text_preview="The Rotary Foundation Global Grant..."
    )
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class AnnotationType(str, Enum):
    BOOKMARK = "bookmark"   # Mark for later
    NOTE = "note"           # Luna's interpretation
    FLAG = "flag"           # Needs attention


@dataclass
class Annotation:
    """A single annotation linking a collection chunk to Memory Matrix."""
    id: str
    collection_key: str
    doc_id: str
    chunk_index: Optional[int]
    annotation_type: str
    content: Optional[str]
    matrix_node_id: Optional[str]
    created_at: str


@dataclass
class AnnotationProvenance:
    """Provenance metadata attached to Matrix nodes created from annotations."""
    source: str = "aibrarian"
    collection: str = ""
    doc_id: str = ""
    chunk_index: Optional[int] = None
    annotation_type: str = ""
    original_text_preview: str = ""

    def to_dict(self) -> dict:
        d = {
            "source": self.source,
            "collection": self.collection,
            "doc_id": self.doc_id,
            "annotation_type": self.annotation_type,
        }
        if self.chunk_index is not None:
            d["chunk_index"] = self.chunk_index
        if self.original_text_preview:
            d["original_text_preview"] = self.original_text_preview
        return d


class AnnotationEngine:
    """
    Manages annotations that bridge Aibrarian collections into Memory Matrix.

    Each annotation creates:
    1. A record in collection_annotations (tracking table)
    2. A node in Memory Matrix with source=aibrarian provenance
    3. An increment to the collection's annotation_count in lock-in

    Deleting an annotation does NOT delete the Matrix node.
    Luna's notes are hers — provenance is permanent.
    """

    def __init__(self, db, memory_matrix=None, lock_in_engine=None, nexus_registry=None):
        """
        Args:
            db: Connected MemoryDatabase instance
            memory_matrix: Optional MemoryMatrix for creating bridge nodes
            lock_in_engine: Optional CollectionLockInEngine for bumping counts
            nexus_registry: Optional NexusRegistry for unified collection metadata
        """
        self._db = db
        self._matrix = memory_matrix
        self._lock_in = lock_in_engine
        self._nexus_registry = nexus_registry

    async def ensure_table(self) -> None:
        """Create the collection_annotations table if it doesn't exist."""
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS collection_annotations (
                id TEXT PRIMARY KEY,
                collection_key TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                chunk_index INTEGER,
                annotation_type TEXT NOT NULL,
                content TEXT,
                matrix_node_id TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # Indexes for querying
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_annotations_collection "
            "ON collection_annotations(collection_key)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_annotations_type "
            "ON collection_annotations(annotation_type)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_annotations_doc "
            "ON collection_annotations(collection_key, doc_id)"
        )

    async def create(
        self,
        collection_key: str,
        doc_id: str,
        annotation_type: AnnotationType,
        content: Optional[str] = None,
        chunk_index: Optional[int] = None,
        original_text_preview: str = "",
    ) -> str:
        """
        Create an annotation on a collection document.

        Steps:
        1. Create annotation record in collection_annotations
        2. Create bridge node in Memory Matrix (with provenance)
        3. Store Matrix node ID back in annotation record
        4. Bump annotation_count in collection lock-in

        Args:
            collection_key: Which collection this annotates
            doc_id: Document ID within the collection
            annotation_type: BOOKMARK, NOTE, or FLAG
            content: Luna's note text (required for NOTE, optional for others)
            chunk_index: Which chunk within the document (optional)
            original_text_preview: First ~200 chars of source chunk

        Returns:
            The annotation ID
        """
        annotation_id = str(uuid.uuid4())

        # Build provenance
        provenance = AnnotationProvenance(
            source="aibrarian",
            collection=collection_key,
            doc_id=doc_id,
            chunk_index=chunk_index,
            annotation_type=annotation_type.value,
            original_text_preview=original_text_preview[:200] if original_text_preview else "",
        )

        # Build Matrix node content
        node_content = content or f"[{annotation_type.value}] on {collection_key}/{doc_id}"
        if chunk_index is not None:
            node_content += f" (chunk {chunk_index})"

        # Create Matrix node if memory_matrix is available
        matrix_node_id = None
        if self._matrix is not None:
            try:
                matrix_node_id = await self._matrix.add_node(
                    node_type="ANNOTATION",
                    content=node_content,
                    source=f"aibrarian:{collection_key}",
                    summary=f"{annotation_type.value}: {(content or '')[:80]}",
                    confidence=1.0,
                    importance=0.6 if annotation_type == AnnotationType.FLAG else 0.4,
                    metadata={
                        "provenance": provenance.to_dict(),
                        "tags": ["aibrarian", collection_key, doc_id],
                    },
                )
                logger.info(
                    f"Annotation bridge: created Matrix node {matrix_node_id} "
                    f"for {collection_key}/{doc_id}"
                )
            except Exception as e:
                logger.warning(f"Failed to create Matrix bridge node: {e}")

        # Insert annotation record
        await self._db.execute(
            """INSERT INTO collection_annotations
               (id, collection_key, doc_id, chunk_index, annotation_type, content, matrix_node_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                annotation_id,
                collection_key,
                doc_id,
                chunk_index,
                annotation_type.value,
                content,
                matrix_node_id,
            ),
        )

        # Bump annotation count in lock-in engine
        if self._lock_in is not None:
            try:
                await self._lock_in.bump_annotation(collection_key)
            except Exception as e:
                logger.warning(f"Failed to bump annotation lock-in: {e}")

        # Bump annotation count in nexus_registry (parallel to lock-in)
        if self._nexus_registry is not None:
            try:
                await self._nexus_registry.bump_annotation(collection_key)
            except Exception as e:
                logger.warning(f"Failed to bump annotation in nexus_registry: {e}")

        logger.info(
            f"Created {annotation_type.value} annotation {annotation_id} "
            f"on {collection_key}/{doc_id}"
        )

        return annotation_id

    async def get(self, annotation_id: str) -> Optional[Annotation]:
        """Get a single annotation by ID."""
        row = await self._db.fetchone(
            "SELECT * FROM collection_annotations WHERE id = ?",
            (annotation_id,),
        )
        if not row:
            return None
        return Annotation(
            id=row[0], collection_key=row[1], doc_id=row[2],
            chunk_index=row[3], annotation_type=row[4],
            content=row[5], matrix_node_id=row[6], created_at=row[7],
        )

    async def list_by_collection(
        self,
        collection_key: str,
        annotation_type: Optional[AnnotationType] = None,
        limit: int = 100,
    ) -> list[Annotation]:
        """List annotations for a collection, optionally filtered by type."""
        if annotation_type:
            rows = await self._db.fetchall(
                """SELECT * FROM collection_annotations
                   WHERE collection_key = ? AND annotation_type = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (collection_key, annotation_type.value, limit),
            )
        else:
            rows = await self._db.fetchall(
                """SELECT * FROM collection_annotations
                   WHERE collection_key = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (collection_key, limit),
            )
        return [
            Annotation(
                id=r[0], collection_key=r[1], doc_id=r[2],
                chunk_index=r[3], annotation_type=r[4],
                content=r[5], matrix_node_id=r[6], created_at=r[7],
            )
            for r in rows
        ]

    async def list_by_document(
        self,
        collection_key: str,
        doc_id: str,
    ) -> list[Annotation]:
        """List annotations for a specific document within a collection."""
        rows = await self._db.fetchall(
            """SELECT * FROM collection_annotations
               WHERE collection_key = ? AND doc_id = ?
               ORDER BY chunk_index, created_at""",
            (collection_key, doc_id),
        )
        return [
            Annotation(
                id=r[0], collection_key=r[1], doc_id=r[2],
                chunk_index=r[3], annotation_type=r[4],
                content=r[5], matrix_node_id=r[6], created_at=r[7],
            )
            for r in rows
        ]

    async def count_by_collection(self, collection_key: str) -> int:
        """Count annotations for a collection."""
        row = await self._db.fetchone(
            "SELECT COUNT(*) FROM collection_annotations WHERE collection_key = ?",
            (collection_key,),
        )
        return row[0] if row else 0

    async def delete(self, annotation_id: str) -> bool:
        """
        Delete an annotation record.

        IMPORTANT: This does NOT delete the Memory Matrix node.
        Luna's notes are hers — the bridge node with provenance persists.
        Only the annotation tracking record is removed.
        """
        existing = await self.get(annotation_id)
        if not existing:
            return False

        await self._db.execute(
            "DELETE FROM collection_annotations WHERE id = ?",
            (annotation_id,),
        )

        logger.info(
            f"Deleted annotation {annotation_id} from {existing.collection_key}. "
            f"Matrix node {existing.matrix_node_id} preserved."
        )
        return True

    async def get_bridged_collections(self) -> list[dict]:
        """
        Get collections that have annotations creating Matrix nodes.

        Returns list of {collection_key, annotation_count, has_bridge_nodes}
        """
        rows = await self._db.fetchall(
            """SELECT collection_key,
                      COUNT(*) as annotation_count,
                      COUNT(matrix_node_id) as bridge_count
               FROM collection_annotations
               GROUP BY collection_key
               ORDER BY annotation_count DESC"""
        )
        return [
            {
                "collection_key": r[0],
                "annotation_count": r[1],
                "bridge_node_count": r[2],
            }
            for r in rows
        ]
