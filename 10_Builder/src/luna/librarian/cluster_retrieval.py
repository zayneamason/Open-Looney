"""
Cluster-aware retrieval for the Librarian.

Adds hybrid_search_with_clusters() that returns both nodes and relevant clusters.
Enables "constellation" context assembly where related knowledge is retrieved
as coherent groups rather than isolated fragments.

Key features:
1. find_relevant_clusters() - Score clusters by membership and lock-in
2. get_auto_activated_clusters() - Always-warm high lock-in clusters
3. expand_cluster_context() - Expand cluster into member nodes
4. get_multi_hop_clusters() - Traverse cluster graph for constellation recall
"""

import sqlite3
import logging
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from luna.memory.cluster_manager import ClusterManager, Cluster
from luna.memory.config_loader import get_retrieval_params

logger = logging.getLogger(__name__)


class ClusterRetrieval:
    """Cluster-aware retrieval layer for the Librarian."""

    def __init__(self, db_path: str):
        """
        Initialize cluster retrieval.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self.cluster_mgr = ClusterManager(db_path)

        # Configuration — loaded from config/memory_economy_config.json
        params = get_retrieval_params()
        self.auto_activation_threshold = params['auto_activation_threshold']
        self.max_clusters_per_query = params['max_clusters_per_query']

        logger.info(f"ClusterRetrieval initialized with db: {db_path}")

    def _get_conn(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA busy_timeout=15000")
        conn.row_factory = sqlite3.Row
        return conn

    def find_relevant_clusters(
        self,
        node_ids: List[str],
        top_k: int = 5
    ) -> List[Tuple[Cluster, float]]:
        """
        Find clusters relevant to retrieved nodes.

        Strategy:
        1. Check which clusters contain retrieved nodes
        2. Score by: membership_strength * cluster_lock_in
        3. Return top clusters

        Args:
            node_ids: List of memory node IDs that were retrieved
            top_k: Maximum number of clusters to return

        Returns:
            List of (Cluster, relevance_score) tuples, sorted by score descending
        """
        if not node_ids:
            return []

        conn = self._get_conn()
        cursor = conn.cursor()

        # Score each cluster based on how many retrieved nodes it contains
        # and the strength of those memberships
        cluster_scores = defaultdict(float)

        for node_id in node_ids:
            cursor.execute("""
                SELECT cm.cluster_id, cm.membership_strength, c.lock_in, c.state
                FROM cluster_members cm
                JOIN clusters c ON cm.cluster_id = c.cluster_id
                WHERE cm.node_id = ?
            """, (node_id,))

            for row in cursor.fetchall():
                cluster_id = row['cluster_id']
                membership = row['membership_strength']
                lock_in = row['lock_in']

                # Score = membership_strength * cluster_lock_in
                # This favors both strong memberships and settled clusters
                cluster_scores[cluster_id] += membership * lock_in

        conn.close()

        # Sort by score and take top_k
        sorted_clusters = sorted(
            cluster_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )[:top_k]

        # Fetch full cluster objects and record access
        results = []
        for cluster_id, score in sorted_clusters:
            cluster = self.cluster_mgr.get_cluster(cluster_id)
            if cluster:
                # Record access to update lock-in
                self.cluster_mgr.record_access(cluster_id)
                results.append((cluster, score))

        logger.debug(f"Found {len(results)} relevant clusters for {len(node_ids)} nodes")
        return results

    def get_auto_activated_clusters(self) -> List[Cluster]:
        """
        Get clusters above auto-activation threshold.
        These are "always warm" clusters - core knowledge constellations.

        Returns:
            List of high lock-in clusters
        """
        return self.cluster_mgr.list_clusters(
            min_lock_in=self.auto_activation_threshold,
            limit=self.max_clusters_per_query
        )

    def expand_cluster_context(
        self,
        cluster_id: str,
        max_nodes: int = 10
    ) -> List[Dict]:
        """
        Expand a cluster into its member nodes.

        Args:
            cluster_id: The cluster to expand
            max_nodes: Maximum nodes to return

        Returns:
            List of node dictionaries with membership info
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT n.id, n.node_type, n.content, n.lock_in AS lock_in_coefficient,
                   cm.membership_strength
            FROM cluster_members cm
            JOIN memory_nodes n ON cm.node_id = n.id
            WHERE cm.cluster_id = ?
            ORDER BY cm.membership_strength DESC
            LIMIT ?
        """, (cluster_id, max_nodes))

        nodes = []
        for row in cursor.fetchall():
            nodes.append({
                'node_id': row['id'],
                'node_type': row['node_type'],
                'content': row['content'],
                'lock_in': row['lock_in_coefficient'],
                'membership_strength': row['membership_strength']
            })

        conn.close()
        logger.debug(f"Expanded cluster {cluster_id} to {len(nodes)} nodes")
        return nodes

    def get_multi_hop_clusters(
        self,
        cluster_id: str,
        min_edge_lock_in: float = 0.7,
        max_hops: int = 1
    ) -> List[Tuple[Cluster, float]]:
        """
        Get clusters connected via high lock-in edges.
        Enables "constellation" recall across cluster boundaries.

        Args:
            cluster_id: Starting cluster
            min_edge_lock_in: Minimum edge lock-in for traversal
            max_hops: Maximum number of hops (currently only 1 supported)

        Returns:
            List of (Cluster, edge_lock_in) tuples
        """
        # Get directly connected clusters with strong edges
        connected = self.cluster_mgr.get_connected_clusters(
            cluster_id=cluster_id,
            min_lock_in=min_edge_lock_in
        )

        results = []
        for neighbor_id, edge_lock_in in connected:
            cluster = self.cluster_mgr.get_cluster(neighbor_id)
            if cluster:
                results.append((cluster, edge_lock_in))

        logger.debug(f"Multi-hop from {cluster_id}: found {len(results)} connected clusters")
        return results

    def get_cluster_context_for_query(
        self,
        node_ids: List[str],
        expand_top_clusters: int = 2,
        include_multi_hop: bool = True
    ) -> Dict:
        """
        Get comprehensive cluster context for a query.

        This is the main entry point for cluster-aware retrieval.

        Args:
            node_ids: Node IDs from initial retrieval
            expand_top_clusters: How many top clusters to expand
            include_multi_hop: Whether to include connected clusters

        Returns:
            Dict with:
                - relevant_clusters: Scored clusters
                - auto_activated: Always-warm clusters
                - expanded_nodes: Nodes from expanded clusters
                - connected_clusters: Multi-hop related clusters
        """
        result = {
            'relevant_clusters': [],
            'auto_activated': [],
            'expanded_nodes': [],
            'connected_clusters': []
        }

        # Find relevant clusters based on retrieved nodes
        relevant = self.find_relevant_clusters(node_ids, top_k=self.max_clusters_per_query)
        result['relevant_clusters'] = [
            {'cluster': c, 'relevance_score': s}
            for c, s in relevant
        ]

        # Get auto-activated clusters
        auto = self.get_auto_activated_clusters()
        # Deduplicate with relevant clusters
        relevant_ids = {c.cluster_id for c, _ in relevant}
        result['auto_activated'] = [c for c in auto if c.cluster_id not in relevant_ids]

        # Expand top clusters
        expanded = []
        for cluster, _ in relevant[:expand_top_clusters]:
            nodes = self.expand_cluster_context(cluster.cluster_id)
            expanded.extend(nodes)
        result['expanded_nodes'] = expanded

        # Multi-hop traversal
        if include_multi_hop and relevant:
            top_cluster = relevant[0][0]
            connected = self.get_multi_hop_clusters(top_cluster.cluster_id)
            result['connected_clusters'] = [
                {'cluster': c, 'edge_lock_in': l}
                for c, l in connected
            ]

        return result


def integrate_with_librarian(librarian_instance, db_path: str):
    """
    Monkey-patch existing Librarian with cluster support.

    This adds hybrid_search_with_clusters() method to an existing
    Librarian instance, enabling cluster-aware retrieval without
    modifying the original class.

    Usage:
        from luna.librarian.librarian import Librarian
        from luna.librarian.cluster_retrieval import integrate_with_librarian

        lib = Librarian(db_path)
        integrate_with_librarian(lib, db_path)

        # Now lib has cluster-aware search
        result = lib.hybrid_search_with_clusters(query)

    Args:
        librarian_instance: An existing Librarian instance
        db_path: Path to the SQLite database

    Returns:
        The librarian instance with added methods
    """
    cluster_retrieval = ClusterRetrieval(db_path)

    def hybrid_search_with_clusters(
        query: str,
        limit: int = 10,
        include_clusters: bool = True
    ) -> Dict:
        """
        Enhanced search returning nodes + clusters.

        Args:
            query: Search query
            limit: Maximum nodes to return
            include_clusters: Whether to include cluster context

        Returns:
            Dict with 'nodes' and 'clusters' keys
        """
        # Call existing hybrid search
        # Note: assumes librarian has hybrid_search method
        # If not, this will need to be adapted
        if hasattr(librarian_instance, 'hybrid_search'):
            nodes = librarian_instance.hybrid_search(query, limit=limit)
        elif hasattr(librarian_instance, 'search'):
            nodes = librarian_instance.search(query, limit=limit)
        else:
            # Fallback: return empty list
            nodes = []

        if not include_clusters:
            return {'nodes': nodes, 'clusters': []}

        # Extract node IDs
        node_ids = [n.get('id') or n.get('node_id') for n in nodes if isinstance(n, dict)]
        if not node_ids and nodes:
            # Try to get IDs from node objects
            node_ids = [getattr(n, 'id', None) for n in nodes]
            node_ids = [nid for nid in node_ids if nid is not None]

        # Find relevant clusters
        cluster_results = cluster_retrieval.find_relevant_clusters(
            node_ids=node_ids,
            top_k=5
        )

        # Get auto-activated clusters
        auto_clusters = cluster_retrieval.get_auto_activated_clusters()

        # Merge and deduplicate
        seen_ids = {c.cluster_id for c, _ in cluster_results}
        for cluster in auto_clusters:
            if cluster.cluster_id not in seen_ids:
                cluster_results.append((cluster, cluster.lock_in))

        return {
            'nodes': nodes,
            'clusters': [
                {'cluster': c, 'relevance_score': s}
                for c, s in cluster_results
            ]
        }

    # Attach methods to librarian instance
    librarian_instance.hybrid_search_with_clusters = hybrid_search_with_clusters
    librarian_instance.cluster_retrieval = cluster_retrieval

    logger.info("Integrated cluster retrieval into Librarian instance")
    return librarian_instance
