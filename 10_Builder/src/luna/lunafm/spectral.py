"""
Spectral engine — eigendecomposition of the memory graph Laplacian.

Computes spectral coordinates for memory_nodes, enabling V3 resonance
retrieval: nodes that are "spectrally close" tend to share structural
role in the graph, even if they're not directly connected.

Runs as a background task owned by the Station. Edge-delta triggered
(5% threshold per V3 spec §5.2). Silently disables itself if scipy is
not installed.

Reference: Luna_Protocol_V3_Spectral_Retrieval_Layer, §5.1-5.4
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Edge type weights from V3 spec §5.1
EDGE_WEIGHTS = {
    "CONTRADICTS": 1.0,
    "DEPENDS_ON": 0.9,
    "SUPERSEDES": 0.9,
    "SUPPORTS": 0.85,
    "ENABLES": 0.85,
    "INVOLVES": 0.7,
    "CLARIFIES": 0.7,
    "SYNTHESIZED_CONNECTION": 0.6,
    "RELATES_TO": 0.3,
    "MENTIONS": 0.2,
}

K_EIGENVECTORS = 30         # number of spectral dimensions
MIN_NODES = K_EIGENVECTORS + 2
EDGE_DELTA_THRESHOLD = 0.05  # 5% per V3


class SpectralEngine:
    """Computes and caches spectral coordinates for memory_nodes."""

    def __init__(self, db):
        self._db = db
        self._last_edge_count = 0
        self._last_node_count = 0
        self._last_computed: Optional[str] = None
        self._fiedler: Optional[float] = None
        self._coords: dict[str, list[float]] = {}
        self._dirty = True

    def status(self) -> dict:
        return {
            "last_computed": self._last_computed,
            "node_count": self._last_node_count,
            "edge_count": self._last_edge_count,
            "fiedler": self._fiedler,
            "coord_count": len(self._coords),
        }

    def mark_dirty(self) -> None:
        self._dirty = True

    async def should_recompute(self) -> bool:
        if self._dirty or self._last_edge_count == 0:
            return True
        try:
            row = await self._db.fetchone("SELECT COUNT(*) FROM graph_edges")
            current = int(row[0]) if row else 0
            delta_pct = abs(current - self._last_edge_count) / max(self._last_edge_count, 1)
            return delta_pct > EDGE_DELTA_THRESHOLD
        except Exception as e:
            logger.debug(f"[SPECTRAL] should_recompute failed: {e}")
            return False

    async def compute(self) -> bool:
        """Run Lanczos eigendecomposition on the graph Laplacian."""
        try:
            import numpy as np
            from scipy.sparse import csr_matrix
            from scipy.sparse.linalg import eigsh
        except ImportError:
            logger.debug("[SPECTRAL] scipy not installed, skipping")
            return False

        try:
            node_rows = await self._db.fetchall(
                "SELECT id FROM memory_nodes WHERE lock_in > 0.3"
            )
            nodes = [r[0] for r in node_rows]
            if len(nodes) < MIN_NODES:
                logger.debug(f"[SPECTRAL] not enough nodes ({len(nodes)})")
                return False

            node_idx = {nid: i for i, nid in enumerate(nodes)}
            n = len(nodes)

            edge_rows = await self._db.fetchall(
                "SELECT from_id, to_id, relationship FROM graph_edges"
            )
            rows, cols, vals = [], [], []
            for from_id, to_id, rel in edge_rows:
                if from_id in node_idx and to_id in node_idx:
                    w = EDGE_WEIGHTS.get(rel, 0.3)
                    i, j = node_idx[from_id], node_idx[to_id]
                    rows.extend([i, j])
                    cols.extend([j, i])
                    vals.extend([w, w])

            if not vals:
                logger.debug("[SPECTRAL] no internal edges")
                return False

            A = csr_matrix((vals, (rows, cols)), shape=(n, n))
            degrees = np.array(A.sum(axis=1)).flatten()
            import numpy as _np
            D = csr_matrix(
                (degrees, (_np.arange(n), _np.arange(n))),
                shape=(n, n),
            )
            L = D - A

            k = min(K_EIGENVECTORS, n - 2)
            # Phase 1A.6: scipy.sparse.linalg.eigsh is synchronous and was
            # blocking the asyncio event loop for 100+ seconds when ARPACK
            # failed to converge — starving the engine's input_buffer poll
            # and causing /context/prepare requests to time out at 30s.
            # Run in a thread executor so the event loop keeps spinning,
            # and cap maxiter so a non-converging case fails fast and is
            # caught by the existing exception handler below.
            loop = asyncio.get_event_loop()
            eigenvalues, eigenvectors = await loop.run_in_executor(
                None,
                lambda: eigsh(L, k=k, which="SM", maxiter=2000),
            )

            self._coords = {
                nid: eigenvectors[idx].tolist()
                for nid, idx in node_idx.items()
            }
            self._last_edge_count = len(edge_rows)
            self._last_node_count = n
            self._fiedler = float(eigenvalues[1]) if len(eigenvalues) > 1 else float(eigenvalues[0])
            self._last_computed = datetime.utcnow().isoformat()
            self._dirty = False

            # Persist to spectral_coordinates
            await self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS spectral_coordinates (
                    node_id TEXT PRIMARY KEY,
                    coords TEXT,
                    computed_at TEXT
                )
                """
            )
            await self._db.execute("DELETE FROM spectral_coordinates")
            for nid, coords in self._coords.items():
                await self._db.execute(
                    "INSERT INTO spectral_coordinates (node_id, coords, computed_at) VALUES (?, ?, ?)",
                    (nid, json.dumps(coords), self._last_computed),
                )

            logger.info(
                f"[SPECTRAL] Decomposition complete: {n} nodes, "
                f"{len(edge_rows)} edges, {k} eigenvectors, λ₁={self._fiedler:.4f}"
            )
            return True
        except Exception as e:
            logger.warning(f"[SPECTRAL] compute failed: {e}", exc_info=True)
            return False

    def find_resonance_pairs(self, node_id: str, sigma: float = 0.05, limit: int = 5) -> list[dict]:
        """V3 §5.3 — return nodes within spectral distance σ."""
        if node_id not in self._coords:
            return []
        try:
            import numpy as np
        except ImportError:
            return []
        target = np.array(self._coords[node_id])
        pairs = []
        for other, coords in self._coords.items():
            if other == node_id:
                continue
            dist = float(np.linalg.norm(target - np.array(coords)))
            if dist < sigma:
                pairs.append({"node_id": other, "distance": dist})
        pairs.sort(key=lambda p: p["distance"])
        return pairs[:limit]

    def has_coords(self) -> bool:
        return len(self._coords) > 0
