"""TopologyClusterStore — storage-only slice for the topology substrate.

Separate namespace from the memory-economy ``clusters`` owned by
``luna.memory.cluster_manager.ClusterManager``. Attaches topology clusters to
canonical ``threads.id`` rows, not memory nodes. Uses the async
``MemoryDatabase`` interface exclusively — no raw sqlite connections.

See Docs/bible/Handoffs/HANDOFF_ADD_TOPOLOGY_CLUSTER_SUBSTRATE.md for the
full design contract.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from luna.substrate.database import MemoryDatabase

logger = logging.getLogger(__name__)


@dataclass
class TopologyCluster:
    cluster_id: str
    label: str
    shape_class: Optional[str]
    lock_state: Optional[str]
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    thread_count: int = 0


@dataclass
class TopologyClusterThread:
    cluster_id: str
    thread_id: str
    relationship: str
    created_at: str
    updated_at: str


_CLUSTER_COLUMNS = (
    "cluster_id, label, shape_class, lock_state, "
    "created_at, updated_at, metadata_json"
)

_CLUSTER_COLUMNS_PREFIXED = ", ".join(
    f"c.{col.strip()}" for col in _CLUSTER_COLUMNS.split(",")
)

_MEMBERSHIP_COLUMNS = (
    "cluster_id, thread_id, relationship, created_at, updated_at"
)


def _row_to_cluster(row: Any, thread_count: int = 0) -> TopologyCluster:
    metadata: dict[str, Any] = {}
    if row[6]:
        try:
            metadata = json.loads(row[6]) or {}
        except json.JSONDecodeError:
            metadata = {}
    return TopologyCluster(
        cluster_id=row[0],
        label=row[1],
        shape_class=row[2],
        lock_state=row[3],
        created_at=row[4],
        updated_at=row[5],
        metadata=metadata,
        thread_count=thread_count,
    )


def _row_to_membership(row: Any) -> TopologyClusterThread:
    return TopologyClusterThread(
        cluster_id=row[0],
        thread_id=row[1],
        relationship=row[2],
        created_at=row[3],
        updated_at=row[4],
    )


class TopologyClusterStore:
    """CRUD surface for topology clusters and their thread memberships."""

    def __init__(self, db: "MemoryDatabase") -> None:
        self._db = db

    async def create_cluster(
        self,
        label: str,
        shape_class: Optional[str] = None,
        lock_state: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TopologyCluster:
        cluster_id = str(uuid.uuid4())
        metadata_json = json.dumps(metadata or {})
        await self._db.execute(
            f"INSERT INTO topology_clusters ({_CLUSTER_COLUMNS}) "
            "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'), ?)",
            (cluster_id, label, shape_class, lock_state, metadata_json),
        )
        row = await self._db.fetchone(
            f"SELECT {_CLUSTER_COLUMNS} FROM topology_clusters WHERE cluster_id = ?",
            (cluster_id,),
        )
        assert row is not None, "create_cluster: row missing immediately after insert"
        return _row_to_cluster(row, thread_count=0)

    async def get_cluster(self, cluster_id: str) -> Optional[TopologyCluster]:
        row = await self._db.fetchone(
            f"SELECT {_CLUSTER_COLUMNS} FROM topology_clusters WHERE cluster_id = ?",
            (cluster_id,),
        )
        if row is None:
            return None
        count_row = await self._db.fetchone(
            "SELECT COUNT(*) FROM topology_cluster_threads WHERE cluster_id = ?",
            (cluster_id,),
        )
        thread_count = int(count_row[0]) if count_row else 0
        return _row_to_cluster(row, thread_count=thread_count)

    async def list_clusters(self) -> list[TopologyCluster]:
        rows = await self._db.fetchall(
            f"SELECT {_CLUSTER_COLUMNS_PREFIXED}, COALESCE(m.n, 0) AS thread_count "
            "FROM topology_clusters c "
            "LEFT JOIN ("
            "  SELECT cluster_id, COUNT(*) AS n "
            "  FROM topology_cluster_threads GROUP BY cluster_id"
            ") m ON m.cluster_id = c.cluster_id "
            "ORDER BY c.created_at ASC, c.cluster_id ASC"
        )
        return [_row_to_cluster(row, thread_count=int(row[7] or 0)) for row in rows]

    async def attach_thread(
        self,
        cluster_id: str,
        thread_id: str,
        relationship: str = "member",
    ) -> bool:
        thread_row = await self._db.fetchone(
            "SELECT 1 FROM threads WHERE id = ?",
            (thread_id,),
        )
        if thread_row is None:
            return False
        await self._db.execute(
            "INSERT OR IGNORE INTO topology_cluster_threads "
            f"({_MEMBERSHIP_COLUMNS}) "
            "VALUES (?, ?, ?, datetime('now'), datetime('now'))",
            (cluster_id, thread_id, relationship),
        )
        return True

    async def detach_thread(self, cluster_id: str, thread_id: str) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM topology_cluster_threads "
            "WHERE cluster_id = ? AND thread_id = ?",
            (cluster_id, thread_id),
        )
        return bool(cursor.rowcount and cursor.rowcount > 0)

    async def list_cluster_threads(
        self, cluster_id: str
    ) -> list[TopologyClusterThread]:
        rows = await self._db.fetchall(
            f"SELECT {_MEMBERSHIP_COLUMNS} FROM topology_cluster_threads "
            "WHERE cluster_id = ? ORDER BY created_at ASC, thread_id ASC",
            (cluster_id,),
        )
        return [_row_to_membership(row) for row in rows]

    async def find_clusters_for_thread(
        self, thread_id: str
    ) -> list[TopologyCluster]:
        rows = await self._db.fetchall(
            f"SELECT {_CLUSTER_COLUMNS_PREFIXED} "
            "FROM topology_clusters c "
            "INNER JOIN topology_cluster_threads m "
            "  ON m.cluster_id = c.cluster_id "
            "WHERE m.thread_id = ? "
            "ORDER BY c.created_at ASC, c.cluster_id ASC",
            (thread_id,),
        )
        clusters: list[TopologyCluster] = []
        for row in rows:
            count_row = await self._db.fetchone(
                "SELECT COUNT(*) FROM topology_cluster_threads WHERE cluster_id = ?",
                (row[0],),
            )
            thread_count = int(count_row[0]) if count_row else 0
            clusters.append(_row_to_cluster(row, thread_count=thread_count))
        return clusters
