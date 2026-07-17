"""
ClusterManager - CRUD operations for Memory Economy clusters.

This is the shared interface used by:
- ClusteringEngine (creates clusters)
- Librarian (reads clusters)
- LockInService (updates lock-in)
- ConstellationAssembler (reads clusters)
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path


@dataclass
class Cluster:
    """Represents a semantic cluster of memories."""
    cluster_id: str
    name: str
    summary: Optional[str]
    lock_in: float
    state: str  # drifting|fluid|settled|crystallized
    created_at: str
    updated_at: str
    last_accessed_at: Optional[str]
    access_count: int
    member_count: int
    avg_node_lock_in: float
    centroid_embedding: Optional[bytes]


@dataclass
class ClusterEdge:
    """Represents a relationship between clusters."""
    from_cluster: str
    to_cluster: str
    relationship: str
    strength: float
    lock_in: float
    created_at: str
    last_reinforced_at: Optional[str]
    reinforcement_count: int


from luna.memory.config_loader import get_state_thresholds


def get_state_from_lock_in(lock_in: float) -> str:
    """Determine state from lock-in value (reads thresholds from config)."""
    thresholds = get_state_thresholds()
    if lock_in < thresholds['drifting']:
        return 'drifting'
    elif lock_in < thresholds['fluid']:
        return 'fluid'
    elif lock_in < thresholds['settled']:
        return 'settled'
    else:
        return 'crystallized'


class ClusterManager:
    """Manages cluster lifecycle operations."""

    # Schema for cluster tables (auto-created if not exist)
    CLUSTER_SCHEMA = """
    CREATE TABLE IF NOT EXISTS clusters (
        cluster_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        summary TEXT,
        lock_in REAL DEFAULT 0.15,
        state TEXT DEFAULT 'drifting',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_accessed_at TEXT,
        access_count INTEGER DEFAULT 0,
        member_count INTEGER DEFAULT 0,
        avg_node_lock_in REAL DEFAULT 0.0,
        centroid_embedding BLOB
    );

    CREATE TABLE IF NOT EXISTS cluster_members (
        cluster_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        membership_strength REAL DEFAULT 1.0,
        added_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (cluster_id, node_id),
        FOREIGN KEY (cluster_id) REFERENCES clusters(cluster_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS cluster_edges (
        from_cluster TEXT NOT NULL,
        to_cluster TEXT NOT NULL,
        relationship TEXT DEFAULT 'related',
        strength REAL DEFAULT 0.5,
        lock_in REAL DEFAULT 0.15,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_reinforced_at TEXT,
        reinforcement_count INTEGER DEFAULT 0,
        PRIMARY KEY (from_cluster, to_cluster, relationship),
        FOREIGN KEY (from_cluster) REFERENCES clusters(cluster_id) ON DELETE CASCADE,
        FOREIGN KEY (to_cluster) REFERENCES clusters(cluster_id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_cluster_lock_in ON clusters(lock_in DESC);
    CREATE INDEX IF NOT EXISTS idx_cluster_state ON clusters(state);
    CREATE INDEX IF NOT EXISTS idx_cluster_members_node ON cluster_members(node_id);
    CREATE INDEX IF NOT EXISTS idx_cluster_edges_from ON cluster_edges(from_cluster);
    CREATE INDEX IF NOT EXISTS idx_cluster_edges_to ON cluster_edges(to_cluster);
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Ensure cluster tables exist."""
        conn = self._get_conn()
        try:
            conn.executescript(self.CLUSTER_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA busy_timeout=15000")
        conn.row_factory = sqlite3.Row
        return conn

    # CREATE
    def create_cluster(
        self,
        name: str,
        summary: Optional[str] = None,
        centroid_embedding: Optional[bytes] = None,
        initial_lock_in: float = 0.0
    ) -> str:
        """Create a new cluster. Returns cluster_id."""
        cluster_id = str(uuid.uuid4())
        state = get_state_from_lock_in(initial_lock_in)

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO clusters (
                cluster_id, name, summary, lock_in, state, centroid_embedding
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (cluster_id, name, summary, initial_lock_in, state, centroid_embedding))

        conn.commit()
        conn.close()

        return cluster_id

    # MEMBERS
    def add_member(self, cluster_id: str, node_id: str, membership_strength: float = 1.0) -> None:
        """Add a node to a cluster."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO cluster_members
            (cluster_id, node_id, membership_strength)
            VALUES (?, ?, ?)
        """, (cluster_id, node_id, membership_strength))

        cursor.execute("""
            UPDATE clusters
            SET member_count = (
                SELECT COUNT(*) FROM cluster_members
                WHERE cluster_id = ?
            ),
            updated_at = CURRENT_TIMESTAMP
            WHERE cluster_id = ?
        """, (cluster_id, cluster_id))

        conn.commit()
        conn.close()

    def remove_member(self, cluster_id: str, node_id: str) -> None:
        """Remove a node from a cluster."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM cluster_members
            WHERE cluster_id = ? AND node_id = ?
        """, (cluster_id, node_id))

        cursor.execute("""
            UPDATE clusters
            SET member_count = (
                SELECT COUNT(*) FROM cluster_members
                WHERE cluster_id = ?
            ),
            updated_at = CURRENT_TIMESTAMP
            WHERE cluster_id = ?
        """, (cluster_id, cluster_id))

        conn.commit()
        conn.close()

    def get_cluster_members(self, cluster_id: str) -> List[Tuple[str, float]]:
        """Get all nodes in a cluster. Returns [(node_id, membership_strength)]."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT node_id, membership_strength
            FROM cluster_members
            WHERE cluster_id = ?
            ORDER BY membership_strength DESC
        """, (cluster_id,))

        members = [(row['node_id'], row['membership_strength']) for row in cursor.fetchall()]
        conn.close()

        return members

    # EDGES
    def add_edge(self, from_cluster: str, to_cluster: str, relationship: str,
                 strength: float = 0.5, lock_in: float = 0.15) -> None:
        """Create or update an edge between clusters."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO cluster_edges
            (from_cluster, to_cluster, relationship, strength, lock_in)
            VALUES (?, ?, ?, ?, ?)
        """, (from_cluster, to_cluster, relationship, strength, lock_in))

        conn.commit()
        conn.close()

    def reinforce_edge(self, from_cluster: str, to_cluster: str, relationship: str) -> None:
        """Record that an edge was traversed."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE cluster_edges
            SET reinforcement_count = reinforcement_count + 1,
                last_reinforced_at = CURRENT_TIMESTAMP,
                lock_in = MIN(1.0, lock_in + 0.01)
            WHERE from_cluster = ? AND to_cluster = ? AND relationship = ?
        """, (from_cluster, to_cluster, relationship))

        conn.commit()
        conn.close()

    def get_edges_from(self, cluster_id: str) -> List[ClusterEdge]:
        """Get all edges originating from a cluster."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM cluster_edges
            WHERE from_cluster = ?
            ORDER BY lock_in DESC
        """, (cluster_id,))

        edges = [ClusterEdge(**dict(row)) for row in cursor.fetchall()]
        conn.close()

        return edges

    def get_connected_clusters(self, cluster_id: str, min_lock_in: float = 0.0) -> List[Tuple[str, float]]:
        """Get clusters connected via high lock-in edges."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT to_cluster, lock_in FROM cluster_edges
            WHERE from_cluster = ? AND lock_in >= ?
            ORDER BY lock_in DESC
        """, (cluster_id, min_lock_in))

        connected = [(row['to_cluster'], row['lock_in']) for row in cursor.fetchall()]
        conn.close()

        return connected

    # READ
    def get_cluster(self, cluster_id: str) -> Optional[Cluster]:
        """Retrieve cluster by ID."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM clusters WHERE cluster_id = ?", (cluster_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return Cluster(**dict(row))

    def get_cluster_by_name(self, name: str) -> Optional[Cluster]:
        """Retrieve cluster by name (exact match)."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM clusters WHERE name = ?", (name,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return Cluster(**dict(row))

    def list_clusters(self, state: Optional[str] = None, min_lock_in: Optional[float] = None,
                      limit: int = 100) -> List[Cluster]:
        """List clusters with optional filters."""
        conn = self._get_conn()
        cursor = conn.cursor()

        query = "SELECT * FROM clusters WHERE 1=1"
        params = []

        if state:
            query += " AND state = ?"
            params.append(state)

        if min_lock_in is not None:
            query += " AND lock_in >= ?"
            params.append(min_lock_in)

        query += " ORDER BY lock_in DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)

        clusters = [Cluster(**dict(row)) for row in cursor.fetchall()]
        conn.close()

        return clusters

    def search_clusters(self, query: str, limit: int = 20) -> List[Cluster]:
        """Search clusters by name or summary."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM clusters
            WHERE name LIKE ? OR summary LIKE ?
            ORDER BY lock_in DESC
            LIMIT ?
        """, (f"%{query}%", f"%{query}%", limit))

        clusters = [Cluster(**dict(row)) for row in cursor.fetchall()]
        conn.close()

        return clusters

    # UPDATE
    def update_lock_in(self, cluster_id: str, new_lock_in: float) -> None:
        """Update cluster lock-in and state."""
        new_lock_in = max(0.0, min(1.0, new_lock_in))
        state = get_state_from_lock_in(new_lock_in)

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE clusters
            SET lock_in = ?, state = ?, updated_at = CURRENT_TIMESTAMP
            WHERE cluster_id = ?
        """, (new_lock_in, state, cluster_id))

        conn.commit()
        conn.close()

    def increment_lock_in(self, cluster_id: str, delta: float = 0.01) -> float:
        """Increment cluster lock-in by delta. Returns new lock-in value."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # Get current lock-in
        cursor.execute("SELECT lock_in FROM clusters WHERE cluster_id = ?", (cluster_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return 0.0

        current = row['lock_in']
        new_lock_in = max(0.0, min(1.0, current + delta))
        state = get_state_from_lock_in(new_lock_in)

        cursor.execute("""
            UPDATE clusters
            SET lock_in = ?, state = ?, updated_at = CURRENT_TIMESTAMP
            WHERE cluster_id = ?
        """, (new_lock_in, state, cluster_id))

        conn.commit()
        conn.close()

        return new_lock_in

    def record_access(self, cluster_id: str) -> None:
        """Record cluster access for lock-in calculation."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE clusters
            SET access_count = access_count + 1,
                last_accessed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE cluster_id = ?
        """, (cluster_id,))

        conn.commit()
        conn.close()

    def update_summary(self, cluster_id: str, summary: str) -> None:
        """Update cluster summary."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE clusters
            SET summary = ?, updated_at = CURRENT_TIMESTAMP
            WHERE cluster_id = ?
        """, (summary, cluster_id))

        conn.commit()
        conn.close()

    def update_name(self, cluster_id: str, name: str) -> None:
        """Update cluster name."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE clusters
            SET name = ?, updated_at = CURRENT_TIMESTAMP
            WHERE cluster_id = ?
        """, (name, cluster_id))

        conn.commit()
        conn.close()

    def update_centroid(self, cluster_id: str, centroid: bytes) -> None:
        """Update cluster centroid embedding."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE clusters
            SET centroid_embedding = ?, updated_at = CURRENT_TIMESTAMP
            WHERE cluster_id = ?
        """, (centroid, cluster_id))

        conn.commit()
        conn.close()

    def update_avg_node_lock_in(self, cluster_id: str, avg: float) -> None:
        """Update average node lock-in for the cluster."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE clusters
            SET avg_node_lock_in = ?, updated_at = CURRENT_TIMESTAMP
            WHERE cluster_id = ?
        """, (avg, cluster_id))

        conn.commit()
        conn.close()

    # DELETE
    def delete_cluster(self, cluster_id: str) -> None:
        """Delete a cluster (CASCADE removes members and edges)."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM clusters WHERE cluster_id = ?", (cluster_id,))

        conn.commit()
        conn.close()

    def delete_edge(self, from_cluster: str, to_cluster: str, relationship: str) -> None:
        """Delete a specific edge between clusters."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM cluster_edges
            WHERE from_cluster = ? AND to_cluster = ? AND relationship = ?
        """, (from_cluster, to_cluster, relationship))

        conn.commit()
        conn.close()

    # STATS
    def get_stats(self) -> dict:
        """Get Memory Economy statistics."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT state, COUNT(*) as count
            FROM clusters
            GROUP BY state
        """)
        state_counts = {row['state']: row['count'] for row in cursor.fetchall()}

        cursor.execute("SELECT COUNT(*) as total FROM clusters")
        total_clusters = cursor.fetchone()['total']

        cursor.execute("SELECT AVG(lock_in) as avg FROM clusters")
        avg_lock_in = cursor.fetchone()['avg'] or 0.0

        cursor.execute("SELECT COUNT(*) as total FROM cluster_edges")
        total_edges = cursor.fetchone()['total']

        cursor.execute("SELECT COUNT(*) as total FROM cluster_members")
        total_members = cursor.fetchone()['total']

        conn.close()

        return {
            'total_clusters': total_clusters,
            'state_distribution': state_counts,
            'avg_lock_in': round(avg_lock_in, 3),
            'total_edges': total_edges,
            'total_memberships': total_members,
        }

    def get_clusters_by_state(self) -> dict:
        """Get clusters grouped by state."""
        result = {}
        for state in ['drifting', 'fluid', 'settled', 'crystallized']:
            result[state] = self.list_clusters(state=state)
        return result

    def get_most_accessed(self, limit: int = 10) -> List[Cluster]:
        """Get most frequently accessed clusters."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM clusters
            ORDER BY access_count DESC
            LIMIT ?
        """, (limit,))

        clusters = [Cluster(**dict(row)) for row in cursor.fetchall()]
        conn.close()

        return clusters

    def get_recently_updated(self, limit: int = 10) -> List[Cluster]:
        """Get most recently updated clusters."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM clusters
            ORDER BY updated_at DESC
            LIMIT ?
        """, (limit,))

        clusters = [Cluster(**dict(row)) for row in cursor.fetchall()]
        conn.close()

        return clusters
