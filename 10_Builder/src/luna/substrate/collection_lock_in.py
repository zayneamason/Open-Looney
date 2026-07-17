"""
Collection-Level Lock-In Engine
===============================

Lock-in dynamics for Aibrarian collections, mirroring the cluster-level
lock-in pattern (luna.memory.lock_in) with library-adjusted parameters.

Key differences from memory node/cluster lock-in:
- Decay is dramatically slower (books get dusty, they don't vanish)
- Access signal includes searches AND document opens
- Annotation count is a first-class signal (the bridge mechanism)
- Minimum floor of 0.05 (not 0.15 like nodes)

Usage:
    from luna.substrate.collection_lock_in import (
        CollectionLockInEngine,
        compute_collection_lock_in,
        classify_collection_state,
    )

    engine = CollectionLockInEngine(db)
    lock_in = await engine.get_lock_in("dataroom")
    await engine.bump_access("dataroom")
"""

import math
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from luna.substrate.lock_in import LockInState, sigmoid

logger = logging.getLogger(__name__)


# =============================================================================
# COLLECTION-SPECIFIC CONSTANTS
# =============================================================================

# Collection-level signals (different from node/cluster)
COLLECTION_WEIGHTS = {
    "access": 0.40,           # How often Luna queries/opens this collection
    "annotation": 0.30,       # How many bookmarks/notes/flags Luna has made
    "connections": 0.20,      # Cross-references to other collections
    "entity_overlap": 0.10,   # Entities shared with Memory Matrix
}

# Library-adjusted decay rates (MUCH slower than memory nodes)
# Books get dusty, they don't vanish
COLLECTION_DECAY_LAMBDAS = {
    "settled": 0.0000005,     # ~16 day half-life
    "fluid": 0.00005,         # ~3.8 hours half-life
    "drifting": 0.0005,       # ~23 minutes half-life
}

# Sigmoid parameters (tuned for collection activity ranges)
COLLECTION_SIGMOID_K = 1.2
COLLECTION_SIGMOID_X0 = 0.5

# Bounds
COLLECTION_LOCK_IN_MIN = 0.05   # No collection ever reaches zero
COLLECTION_LOCK_IN_MAX = 1.0

# ── PATTERN-SPECIFIC FLOORS ─────────────────────────────────────────
PATTERN_FLOORS = {
    "ceremonial":  0.30,   # Immutable sovereignty floor
    "emergent":    0.10,   # Luna's own work — persists longer
    "utilitarian": 0.05,   # Existing default — unchanged
}

# ── PATTERN-SPECIFIC DECAY LAMBDAS ──────────────────────────────────
PATTERN_DECAY_LAMBDAS = {
    "ceremonial": {
        "settled":  0.0,
        "fluid":    0.0,
        "drifting": 0.0,
    },
    "emergent": {
        "settled":  0.0000002,
        "fluid":    0.00003,
        "drifting": 0.0003,
    },
    "utilitarian": {
        "settled":  0.0000005,
        "fluid":    0.00005,
        "drifting": 0.0005,
    },
}

# ── STARTING LOCK-IN BY PATTERN ─────────────────────────────────────
PATTERN_STARTING_LOCK_IN = {
    "ceremonial":  0.15,
    "emergent":    0.50,
    "utilitarian": 0.15,
}

# Thresholds (same as node-level for consistency)
COLLECTION_THRESHOLD_SETTLED = 0.70
COLLECTION_THRESHOLD_DRIFTING = 0.30


# =============================================================================
# CORE COMPUTATION
# =============================================================================

def compute_collection_lock_in(
    access_count: int = 0,
    annotation_count: int = 0,
    connected_collections: int = 0,
    entity_overlap: int = 0,
    seconds_since_access: float = 0.0,
    ingestion_pattern: str = "utilitarian",
) -> float:
    """
    Compute lock-in coefficient for an Aibrarian collection.
    Pattern determines floor, decay rates, and minimum visibility.
    """
    floor = PATTERN_FLOORS.get(ingestion_pattern, COLLECTION_LOCK_IN_MIN)
    decay_table = PATTERN_DECAY_LAMBDAS.get(
        ingestion_pattern, PATTERN_DECAY_LAMBDAS["utilitarian"]
    )

    # Weighted activity score
    activity = (
        access_count * COLLECTION_WEIGHTS["access"]
        + annotation_count * COLLECTION_WEIGHTS["annotation"]
        + connected_collections * COLLECTION_WEIGHTS["connections"]
        + entity_overlap * COLLECTION_WEIGHTS["entity_overlap"]
    ) / 10.0

    # Sigmoid mapping
    raw = sigmoid(activity, k=COLLECTION_SIGMOID_K, x0=COLLECTION_SIGMOID_X0)

    # Scale to bounded range using pattern floor
    lock_in = floor + (COLLECTION_LOCK_IN_MAX - floor) * raw

    # State-dependent decay using pattern-specific lambdas
    state = classify_collection_state(lock_in)
    lambda_decay = decay_table.get(state.value, 0.0)

    if seconds_since_access > 0 and lambda_decay > 0:
        decay_factor = math.exp(-lambda_decay * seconds_since_access)
        lock_in *= decay_factor

    # Enforce pattern floor
    return max(floor, min(COLLECTION_LOCK_IN_MAX, round(lock_in, 4)))


def classify_collection_state(lock_in: float) -> LockInState:
    """Classify collection lock-in into state. Uses same thresholds as nodes."""
    if lock_in >= COLLECTION_THRESHOLD_SETTLED:
        return LockInState.SETTLED
    elif lock_in < COLLECTION_THRESHOLD_DRIFTING:
        return LockInState.DRIFTING
    else:
        return LockInState.FLUID


# =============================================================================
# DATABASE-BACKED ENGINE
# =============================================================================

@dataclass
class CollectionLockInRecord:
    """Persisted lock-in state for a single collection."""
    collection_key: str
    lock_in: float
    state: str
    access_count: int
    annotation_count: int
    connected_collections: int
    entity_overlap_count: int
    last_accessed_at: Optional[str]
    created_at: str
    updated_at: str


class CollectionLockInEngine:
    """
    Manages collection-level lock-in state in the main Luna engine database.

    Collections are external (Aibrarian). Lock-in tracking is Luna's internal
    state about them. This table lives in the engine DB alongside memory_nodes.
    """

    def __init__(self, db):
        """
        Args:
            db: A connected MemoryDatabase instance
        """
        self._db = db

    async def ensure_table(self) -> None:
        """Create the collection_lock_in table if it doesn't exist."""
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS collection_lock_in (
                collection_key TEXT PRIMARY KEY,
                lock_in REAL DEFAULT 0.15,
                state TEXT DEFAULT 'drifting',
                access_count INTEGER DEFAULT 0,
                annotation_count INTEGER DEFAULT 0,
                connected_collections INTEGER DEFAULT 0,
                entity_overlap_count INTEGER DEFAULT 0,
                last_accessed_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)

    async def get_lock_in(self, collection_key: str) -> Optional[CollectionLockInRecord]:
        """Get lock-in record for a collection. Returns None if not tracked."""
        row = await self._db.fetchone(
            "SELECT * FROM collection_lock_in WHERE collection_key = ?",
            (collection_key,),
        )
        if not row:
            return None
        return CollectionLockInRecord(
            collection_key=row[0],
            lock_in=row[1],
            state=row[2],
            access_count=row[3],
            annotation_count=row[4],
            connected_collections=row[5],
            entity_overlap_count=row[6],
            last_accessed_at=row[7],
            created_at=row[8],
            updated_at=row[9],
        )

    async def get_all(self) -> list[CollectionLockInRecord]:
        """Get lock-in records for all tracked collections."""
        rows = await self._db.fetchall(
            "SELECT * FROM collection_lock_in ORDER BY lock_in DESC"
        )
        return [
            CollectionLockInRecord(
                collection_key=r[0], lock_in=r[1], state=r[2],
                access_count=r[3], annotation_count=r[4],
                connected_collections=r[5], entity_overlap_count=r[6],
                last_accessed_at=r[7], created_at=r[8], updated_at=r[9],
            )
            for r in rows
        ]

    async def ensure_tracked(self, collection_key: str, pattern: str = "utilitarian") -> None:
        """Ensure a collection has a lock-in tracking record."""
        existing = await self.get_lock_in(collection_key)
        if existing is None:
            starting = PATTERN_STARTING_LOCK_IN.get(pattern, 0.15)
            await self._db.execute(
                "INSERT OR IGNORE INTO collection_lock_in (collection_key, lock_in) VALUES (?, ?)",
                (collection_key, starting),
            )

    async def bump_access(self, collection_key: str, pattern: str = "utilitarian") -> float:
        """
        Increment access count and recalculate lock-in.

        Called on every search() and get_document() in Aibrarian.

        Returns:
            New lock-in value
        """
        await self.ensure_tracked(collection_key, pattern=pattern)

        await self._db.execute(
            """UPDATE collection_lock_in
               SET access_count = access_count + 1,
                   last_accessed_at = datetime('now'),
                   updated_at = datetime('now')
               WHERE collection_key = ?""",
            (collection_key,),
        )

        return await self._recalculate(collection_key, ingestion_pattern=pattern)

    async def bump_annotation(self, collection_key: str, pattern: str = "utilitarian") -> float:
        """
        Increment annotation count and recalculate lock-in.

        Called when Luna creates an annotation on a collection document.

        Returns:
            New lock-in value
        """
        await self.ensure_tracked(collection_key, pattern=pattern)

        await self._db.execute(
            """UPDATE collection_lock_in
               SET annotation_count = annotation_count + 1,
                   updated_at = datetime('now')
               WHERE collection_key = ?""",
            (collection_key,),
        )

        return await self._recalculate(collection_key, ingestion_pattern=pattern)

    async def set_connections(self, collection_key: str, count: int) -> float:
        """Update connected_collections count and recalculate."""
        await self.ensure_tracked(collection_key)

        await self._db.execute(
            """UPDATE collection_lock_in
               SET connected_collections = ?,
                   updated_at = datetime('now')
               WHERE collection_key = ?""",
            (count, collection_key),
        )

        return await self._recalculate(collection_key)

    async def set_entity_overlap(self, collection_key: str, count: int) -> float:
        """Update entity_overlap_count and recalculate."""
        await self.ensure_tracked(collection_key)

        await self._db.execute(
            """UPDATE collection_lock_in
               SET entity_overlap_count = ?,
                   updated_at = datetime('now')
               WHERE collection_key = ?""",
            (count, collection_key),
        )

        return await self._recalculate(collection_key)

    async def _recalculate(self, collection_key: str, ingestion_pattern: str = "utilitarian") -> float:
        """Recalculate and persist lock-in for a collection."""
        record = await self.get_lock_in(collection_key)
        if record is None:
            return PATTERN_FLOORS.get(ingestion_pattern, COLLECTION_LOCK_IN_MIN)

        seconds_since = 0.0
        if record.last_accessed_at:
            try:
                last_dt = datetime.fromisoformat(record.last_accessed_at)
                seconds_since = max(0.0, (datetime.now() - last_dt).total_seconds())
            except (ValueError, TypeError):
                seconds_since = 0.0

        new_lock_in = compute_collection_lock_in(
            access_count=record.access_count,
            annotation_count=record.annotation_count,
            connected_collections=record.connected_collections,
            entity_overlap=record.entity_overlap_count,
            seconds_since_access=seconds_since,
            ingestion_pattern=ingestion_pattern,
        )

        new_state = classify_collection_state(new_lock_in).value

        await self._db.execute(
            """UPDATE collection_lock_in
               SET lock_in = ?, state = ?, updated_at = datetime('now')
               WHERE collection_key = ?""",
            (new_lock_in, new_state, collection_key),
        )

        return new_lock_in

    async def recalculate_all(self) -> dict:
        """Batch recalculate lock-in for all tracked collections."""
        records = await self.get_all()
        results = {"updated": 0, "state_changes": []}

        for record in records:
            old_state = record.state
            new_lock_in = await self._recalculate(record.collection_key)
            new_state = classify_collection_state(new_lock_in).value

            results["updated"] += 1
            if old_state != new_state:
                results["state_changes"].append({
                    "collection_key": record.collection_key,
                    "from": old_state,
                    "to": new_state,
                })

        return results

    async def get_by_state(self, state: str) -> list[CollectionLockInRecord]:
        """Get all collections in a given state."""
        rows = await self._db.fetchall(
            "SELECT * FROM collection_lock_in WHERE state = ? ORDER BY lock_in DESC",
            (state,),
        )
        return [
            CollectionLockInRecord(
                collection_key=r[0], lock_in=r[1], state=r[2],
                access_count=r[3], annotation_count=r[4],
                connected_collections=r[5], entity_overlap_count=r[6],
                last_accessed_at=r[7], created_at=r[8], updated_at=r[9],
            )
            for r in rows
        ]

    async def get_above_threshold(self, threshold: float) -> list[CollectionLockInRecord]:
        """Get all collections with lock-in >= threshold."""
        rows = await self._db.fetchall(
            "SELECT * FROM collection_lock_in WHERE lock_in >= ? ORDER BY lock_in DESC",
            (threshold,),
        )
        return [
            CollectionLockInRecord(
                collection_key=r[0], lock_in=r[1], state=r[2],
                access_count=r[3], annotation_count=r[4],
                connected_collections=r[5], entity_overlap_count=r[6],
                last_accessed_at=r[7], created_at=r[8], updated_at=r[9],
            )
            for r in rows
        ]
