"""
Nexus Registry
==============

Source-of-truth for which collections exist, are enabled, and admit into
the Aperture inner ring. Replaces YAML-driven startup registration.

YAML (`config/aibrarian_registry.yaml`) is consulted exactly once on first
boot — when `nexus_registry` is empty — to seed collection metadata.
After that, the table is authoritative; YAML edits are ignored.

The Aperture catch-22 fix lives in `list_admitted()`: collections with
`access_count == 0` are admitted as "unknown, admit" so newly-registered
collections actually receive traffic to climb their lock_in score.

See: ClaudeCo-Projects/Project Eclipse/NEXUS_CORTEX_ARCHITECTURE_BRIEF.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from luna.substrate.collection_lock_in import PATTERN_STARTING_LOCK_IN

logger = logging.getLogger(__name__)


@dataclass
class NexusRegistryRecord:
    collection_key: str
    lun_path: str
    ingestion_pattern: str
    lock_in: float
    access_count: int
    annotation_count: int
    enabled: bool
    created_at: str
    updated_at: str


def _row_to_record(row) -> NexusRegistryRecord:
    return NexusRegistryRecord(
        collection_key=row[0],
        lun_path=row[1],
        ingestion_pattern=row[2],
        lock_in=row[3],
        access_count=row[4],
        annotation_count=row[5],
        enabled=bool(row[6]),
        created_at=row[7],
        updated_at=row[8],
    )


class NexusRegistry:
    """Async wrapper around the `nexus_registry` table in memory_matrix.lun."""

    SELECT_COLS = (
        "collection_key, lun_path, ingestion_pattern, lock_in, "
        "access_count, annotation_count, enabled, created_at, updated_at"
    )

    def __init__(self, db):
        self._db = db

    async def is_empty(self) -> bool:
        row = await self._db.fetchone("SELECT COUNT(*) FROM nexus_registry")
        return (row[0] if row else 0) == 0

    async def get(self, key: str) -> Optional[NexusRegistryRecord]:
        row = await self._db.fetchone(
            f"SELECT {self.SELECT_COLS} FROM nexus_registry WHERE collection_key = ?",
            (key,),
        )
        return _row_to_record(row) if row else None

    async def list_enabled(self) -> list[NexusRegistryRecord]:
        rows = await self._db.fetchall(
            f"SELECT {self.SELECT_COLS} FROM nexus_registry "
            f"WHERE enabled = 1 ORDER BY lock_in DESC"
        )
        return [_row_to_record(r) for r in rows]

    async def list_admitted(self, threshold: float) -> list[NexusRegistryRecord]:
        """
        Admission gate for Aperture inner ring.

        Admits a collection when EITHER:
          - access_count == 0 (unknown, give it a chance), OR
          - lock_in >= threshold (proven relevant)

        The access_count==0 branch is the catch-22 fix: utilitarian/ceremonial
        collections start at lock_in=0.15 (below default BALANCED 0.40), so
        without this clause they could never accumulate traffic.
        """
        rows = await self._db.fetchall(
            f"SELECT {self.SELECT_COLS} FROM nexus_registry "
            f"WHERE enabled = 1 AND (access_count = 0 OR lock_in >= ?) "
            f"ORDER BY lock_in DESC",
            (threshold,),
        )
        return [_row_to_record(r) for r in rows]

    async def ensure_collection(
        self,
        key: str,
        lun_path: str,
        pattern: str = "utilitarian",
        enabled: bool = True,
    ) -> None:
        """Insert a collection row if missing. Idempotent."""
        starting = PATTERN_STARTING_LOCK_IN.get(pattern, 0.15)
        await self._db.execute(
            "INSERT OR IGNORE INTO nexus_registry "
            "(collection_key, lun_path, ingestion_pattern, lock_in, enabled) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, lun_path, pattern, starting, 1 if enabled else 0),
        )

    async def bump_access(self, key: str) -> None:
        await self._db.execute(
            "UPDATE nexus_registry SET "
            "access_count = access_count + 1, "
            "updated_at = datetime('now') "
            "WHERE collection_key = ?",
            (key,),
        )

    async def bump_annotation(self, key: str) -> None:
        await self._db.execute(
            "UPDATE nexus_registry SET "
            "annotation_count = annotation_count + 1, "
            "updated_at = datetime('now') "
            "WHERE collection_key = ?",
            (key,),
        )

    async def seed_from_yaml(self, yaml_path: Path) -> int:
        """
        One-shot seed from `aibrarian_registry.yaml`. Idempotent at the row
        level (INSERT OR IGNORE), but callers should gate on `is_empty()` to
        treat YAML as a true bootstrap rather than a recurring sync source.

        Returns:
            Count of rows inserted (== enabled collections in YAML on first
            seed; 0 on subsequent calls because of INSERT OR IGNORE).
        """
        yaml_path = Path(yaml_path)
        if not yaml_path.exists():
            logger.warning("NexusRegistry seed skipped — YAML missing: %s", yaml_path)
            return 0

        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}

        defaults = data.get("defaults", {})
        collections = data.get("collections", {})

        # Snapshot pre-seed count so we can report what THIS call wrote
        before = await self._db.fetchone("SELECT COUNT(*) FROM nexus_registry")
        before_count = before[0] if before else 0

        for key, conf in collections.items():
            merged = {**defaults, **conf}
            if not merged.get("enabled", True):
                continue
            db_path = merged.get("db_path", "")
            pattern = merged.get("ingestion_pattern", "utilitarian")
            await self.ensure_collection(
                key=key,
                lun_path=db_path,
                pattern=pattern,
                enabled=True,
            )

        after = await self._db.fetchone("SELECT COUNT(*) FROM nexus_registry")
        after_count = after[0] if after else 0
        return max(0, after_count - before_count)
