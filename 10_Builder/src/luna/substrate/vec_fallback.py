"""Pure-Python vector search fallback when sqlite-vec is unavailable.

Stores embeddings as BLOBs in a regular SQLite table and performs cosine
similarity in Python (numpy if available, otherwise pure math).  This is
slower than sqlite-vec's optimized C code but ensures semantic search
works in Nuitka-compiled builds where enable_load_extension() is disabled.
"""

import logging
import math
import sqlite3
import struct
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


# ---------------------------------------------------------------------------
# Serialization helpers (same format as aibrarian_engine._vector_to_blob)
# ---------------------------------------------------------------------------

def _vec_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"{dim}f", blob))


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------

def _cosine_numpy(a, b):
    """Cosine similarity via numpy — fast batch version."""
    a = np.asarray(a, dtype=np.float32)
    dot = b @ a
    norm_a = np.linalg.norm(a)
    norms_b = np.linalg.norm(b, axis=1)
    denom = norm_a * norms_b
    denom[denom == 0] = 1e-10
    return dot / denom


def _cosine_pure(a: list[float], b: list[float]) -> float:
    """Cosine similarity — pure Python fallback."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# FallbackVecStore — drop-in replacement for vec0 virtual table operations
# ---------------------------------------------------------------------------

class FallbackVecStore:
    """Stores embeddings in a plain SQLite table and searches via Python."""

    def __init__(self, conn: sqlite3.Connection, table_name: str, dim: int):
        self.conn = conn
        self.table_name = table_name
        self.dim = dim
        self._ready = False

    def ensure_table(self):
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                node_id TEXT PRIMARY KEY,
                embedding BLOB NOT NULL
            )
        """)
        self.conn.commit()
        self._ready = True

    @property
    def is_available(self) -> bool:
        return self._ready

    def store(self, node_id: str, embedding: list[float]) -> bool:
        if not self._ready:
            return False
        blob = _vec_to_blob(embedding)
        self.conn.execute(
            f"INSERT OR REPLACE INTO {self.table_name} (node_id, embedding) VALUES (?, ?)",
            (node_id, blob),
        )
        self.conn.commit()
        return True

    def delete(self, node_id: str) -> bool:
        if not self._ready:
            return False
        cursor = self.conn.execute(
            f"DELETE FROM {self.table_name} WHERE node_id = ?", (node_id,)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        min_similarity: float = 0.0,
    ) -> list[tuple[str, float]]:
        """Return (node_id, similarity) pairs sorted by similarity desc."""
        if not self._ready:
            return []

        rows = self.conn.execute(
            f"SELECT node_id, embedding FROM {self.table_name}"
        ).fetchall()

        if not rows:
            return []

        if _HAS_NUMPY and len(rows) > 0:
            # Batch cosine similarity with numpy
            ids = [r[0] for r in rows]
            matrix = np.array(
                [_blob_to_vec(r[1], self.dim) for r in rows], dtype=np.float32
            )
            scores = _cosine_numpy(query_embedding, matrix)
            pairs = list(zip(ids, scores.tolist()))
        else:
            # Pure Python path
            q = query_embedding
            pairs = []
            for row in rows:
                nid = row[0]
                vec = _blob_to_vec(row[1], self.dim)
                sim = _cosine_pure(q, vec)
                pairs.append((nid, sim))

        # Filter and sort
        pairs = [(nid, s) for nid, s in pairs if s >= min_similarity]
        pairs.sort(key=lambda x: x[1], reverse=True)
        return pairs[:limit]


# ---------------------------------------------------------------------------
# AiBrarian-compatible fallback for chunk_embeddings searches
# ---------------------------------------------------------------------------

class AiBrarianFallbackVec:
    """Drop-in for vec0-based chunk_embeddings in AiBrarianConnection.

    Reads existing chunk_embeddings rows (written by the standard ingestion
    pipeline) from a plain table with (chunk_id TEXT, embedding BLOB) and
    performs cosine similarity in Python.
    """

    def __init__(self, conn: sqlite3.Connection, dim: int):
        self.conn = conn
        self.dim = dim
        self._migrated = False
        self._ensure_fallback_table()

    def _ensure_fallback_table(self):
        """Create a plain fallback table and migrate data from vec0 if needed."""
        try:
            # Check if a plain fallback table already exists
            self.conn.execute("SELECT 1 FROM chunk_embeddings_fallback LIMIT 1")
            self._migrated = True
        except sqlite3.OperationalError:
            # Try to read from vec0 table and copy to plain table
            try:
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS chunk_embeddings_fallback (
                        chunk_id TEXT PRIMARY KEY,
                        embedding BLOB NOT NULL
                    )
                """)
                # Attempt to copy from the vec0 table if it has data
                # This will fail if vec0 isn't loaded — that's fine, ingestion
                # will populate the fallback table directly
                try:
                    cursor = self.conn.execute(
                        "SELECT chunk_id, embedding FROM chunk_embeddings"
                    )
                    rows = cursor.fetchall()
                    if rows:
                        self.conn.executemany(
                            "INSERT OR IGNORE INTO chunk_embeddings_fallback (chunk_id, embedding) VALUES (?, ?)",
                            rows,
                        )
                        self.conn.commit()
                        logger.info("Migrated %d embeddings to fallback table", len(rows))
                except Exception:
                    pass  # vec0 table not readable without extension
                self._migrated = True
            except Exception as e:
                logger.debug("Could not create fallback embedding table: %s", e)

    def store(self, chunk_id: str, embedding) -> bool:
        """Store an embedding for a chunk_id in the fallback table.

        Accepts either a list[float] (will be serialized via _vec_to_blob)
        or a bytes blob already in the wire format. Cartridge ingestion
        passes raw blobs read from the .lun file's embeddings table; the
        runtime embedding pipeline passes list[float] from the generator.
        """
        if not self._migrated:
            return False
        if isinstance(embedding, (list, tuple)):
            blob = _vec_to_blob(list(embedding))
        elif isinstance(embedding, (bytes, bytearray, memoryview)):
            blob = bytes(embedding)
        else:
            logger.debug(
                "FallbackVec.store: unexpected embedding type %s for %s",
                type(embedding).__name__, chunk_id,
            )
            return False
        self.conn.execute(
            "INSERT OR REPLACE INTO chunk_embeddings_fallback "
            "(chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, blob),
        )
        self.conn.commit()
        return True

    def search(self, query_vec: list[float], limit: int = 10) -> list[tuple[str, float]]:
        """Search chunk embeddings and return (chunk_id, distance) pairs."""
        if not self._migrated:
            return []

        rows = self.conn.execute(
            "SELECT chunk_id, embedding FROM chunk_embeddings_fallback"
        ).fetchall()

        if not rows:
            return []

        if _HAS_NUMPY:
            ids = [r[0] for r in rows]
            matrix = np.array(
                [_blob_to_vec(r[1], self.dim) for r in rows], dtype=np.float32
            )
            sims = _cosine_numpy(query_vec, matrix)
            pairs = list(zip(ids, (1.0 - sims).tolist()))  # convert to distance
        else:
            pairs = []
            for row in rows:
                vec = _blob_to_vec(row[1], self.dim)
                sim = _cosine_pure(query_vec, vec)
                pairs.append((row[0], 1.0 - sim))

        pairs.sort(key=lambda x: x[1])
        return pairs[:limit]
