"""
ClusteringEngine - Automatic semantic grouping of memories.

MVP Strategy: Keyword-based clustering
- Uses FTS5 to find nodes with common keywords
- Groups nodes that share significant keyword overlap

The ClusteringEngine runs periodically to discover natural memory groupings,
helping Luna organize knowledge into coherent clusters that can be retrieved
together and have their lock-in values propagate collectively.

Uses the ClusterManager for all database operations on clusters.
"""

import sqlite3
from typing import List, Dict, Set, Tuple
from collections import defaultdict, Counter
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    logger.warning("NumPy not available - centroid calculation disabled")

from luna.memory.cluster_manager import ClusterManager, Cluster
from luna.memory.config_loader import get_clustering_params


class ClusteringEngine:
    """Automatically groups related memories into clusters."""

    def __init__(self, db_path: str):
        """
        Initialize the clustering engine.

        Args:
            db_path: Path to the SQLite database
        """
        self.db_path = db_path
        self.cluster_mgr = ClusterManager(db_path)

        # Clustering parameters — loaded from config/memory_economy_config.json
        params = get_clustering_params()
        self.similarity_threshold = params['similarity_threshold']
        self.min_cluster_size = params['min_cluster_size']
        self.max_cluster_size = params['max_cluster_size']
        self.min_keyword_overlap = params['min_keyword_overlap']
        self.max_generic_frequency = params['max_generic_frequency']

        # Stopwords - common words to exclude from clustering
        self.stopwords = {
            # Articles and conjunctions
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to',
            'for', 'of', 'as', 'by', 'is', 'was', 'are', 'were', 'be', 'been',
            # Pronouns and common verbs
            'this', 'that', 'with', 'from', 'it', 'i', 'you', 'we', 'they',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'must', 'can', 'not', 'no', 'yes',
            # Common conversation words
            'luna', 'user', 'assistant', 'message', 'response', 'question',
            'just', 'also', 'very', 'really', 'like', 'know', 'think', 'want',
            'need', 'make', 'get', 'go', 'come', 'see', 'look', 'take', 'give',
            'use', 'find', 'tell', 'ask', 'work', 'seem', 'feel', 'try', 'leave',
            'call', 'good', 'new', 'first', 'last', 'long', 'great', 'little',
            'own', 'other', 'old', 'right', 'big', 'high', 'different', 'small',
            'large', 'next', 'early', 'young', 'important', 'few', 'public', 'bad',
            'same', 'able', 'about', 'then', 'now', 'when', 'where', 'why', 'how',
            'all', 'each', 'every', 'both', 'few', 'more', 'most', 'some', 'any',
            'than', 'too', 'only', 'into', 'over', 'such', 'here', 'there', 'been',
            'being', 'after', 'before', 'between', 'under', 'again', 'further',
            'once', 'during', 'through', 'while', 'above', 'below', 'out', 'off',
        }

    def _get_conn(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA busy_timeout=15000")
        conn.row_factory = sqlite3.Row
        return conn

    def _extract_keywords(self, text: str, top_n: int = 15) -> List[str]:
        """
        Extract top keywords from text, excluding stopwords.

        Args:
            text: The text to extract keywords from
            top_n: Maximum number of keywords to return

        Returns:
            List of keywords
        """
        if not text:
            return []

        # Tokenize and clean
        words = text.lower().split()
        word_counts = Counter()

        for word in words:
            # Remove punctuation and numbers
            word = ''.join(c for c in word if c.isalnum())

            # Skip short words, stopwords, and numbers
            if (word
                and len(word) > 3
                and word not in self.stopwords
                and not word.isdigit()):
                word_counts[word] += 1

        # Return top keywords by frequency
        return [word for word, _ in word_counts.most_common(top_n)]

    def cluster_by_keywords(self) -> List[Dict]:
        """
        Group memories by keyword co-occurrence.

        Algorithm:
        1. Extract keywords from all memory nodes
        2. Build inverted index (keyword -> node_ids)
        3. For each keyword, find nodes with high Jaccard overlap
        4. Form clusters from nodes with sufficient overlap

        Returns:
            List of cluster candidates with keyword and node lists
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Get all memory nodes with content
        cursor.execute("""
            SELECT id, content FROM memory_nodes
            WHERE content IS NOT NULL AND content != ''
        """)

        nodes = cursor.fetchall()
        logger.info(f"Analyzing {len(nodes)} nodes for clustering...")

        # Extract keywords for each node
        node_keywords: Dict[str, Set[str]] = {}
        for row in nodes:
            node_id = row['id']
            content = row['content']
            keywords = self._extract_keywords(content)
            if keywords:
                node_keywords[node_id] = set(keywords)

        logger.info(f"Extracted keywords from {len(node_keywords)} nodes")

        # Build inverted index: keyword -> set of node_ids
        keyword_index: Dict[str, Set[str]] = defaultdict(set)
        for node_id, keywords in node_keywords.items():
            for kw in keywords:
                keyword_index[kw].add(node_id)

        # Count keyword frequencies
        keyword_counts = Counter()
        for keywords in node_keywords.values():
            keyword_counts.update(keywords)

        conn.close()

        # Find clusters
        clusters = []
        processed_nodes: Set[str] = set()

        # Sort keywords by frequency (mid-frequency is best for clustering)
        # Too common = generic, too rare = not useful
        sorted_keywords = sorted(
            keyword_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )

        for keyword, count in sorted_keywords:
            # Skip too-common keywords (generic)
            if count > self.max_generic_frequency:
                continue

            # Skip already-processed keywords with too few remaining candidates
            candidates = keyword_index[keyword] - processed_nodes
            if len(candidates) < self.min_cluster_size:
                continue

            # Find nodes with high keyword overlap
            cluster_nodes = []
            for node_id in candidates:
                if node_id in processed_nodes:
                    continue

                node_kw = node_keywords[node_id]

                # Check overlap with other candidates
                good_overlaps = 0
                for other_id in candidates:
                    if other_id == node_id or other_id in processed_nodes:
                        continue

                    other_kw = node_keywords[other_id]

                    # Jaccard similarity
                    intersection = len(node_kw & other_kw)
                    union = len(node_kw | other_kw)
                    overlap = intersection / union if union > 0 else 0

                    if overlap >= self.min_keyword_overlap:
                        good_overlaps += 1

                # Node needs at least min_cluster_size-1 good overlaps
                if good_overlaps >= self.min_cluster_size - 1:
                    cluster_nodes.append(node_id)

            # Form cluster if enough nodes
            if len(cluster_nodes) >= self.min_cluster_size:
                # Limit cluster size
                cluster_nodes = cluster_nodes[:self.max_cluster_size]

                clusters.append({
                    'keyword': keyword,
                    'nodes': cluster_nodes
                })

                # Mark nodes as processed
                processed_nodes.update(cluster_nodes)

        logger.info(f"Found {len(clusters)} potential clusters")
        logger.info(f"Clustered {len(processed_nodes)}/{len(node_keywords)} nodes")

        return clusters

    def _calculate_centroid(self, node_ids: List[str]) -> bytes:
        """
        Calculate centroid embedding from member embeddings.

        Args:
            node_ids: List of node IDs to average

        Returns:
            Centroid embedding as bytes, or empty bytes if unavailable
        """
        if not HAS_NUMPY:
            return b''

        conn = self._get_conn()
        cursor = conn.cursor()

        # Check if embedding column exists in memory_nodes
        cursor.execute("PRAGMA table_info(memory_nodes)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'embedding' not in columns:
            conn.close()
            return b''  # No embeddings available in this schema

        embeddings = []
        for node_id in node_ids:
            cursor.execute("""
                SELECT embedding FROM memory_nodes WHERE id = ?
            """, (node_id,))
            row = cursor.fetchone()
            if row and row['embedding']:
                try:
                    emb = np.frombuffer(row['embedding'], dtype=np.float32)
                    embeddings.append(emb)
                except Exception:
                    pass

        conn.close()

        if not embeddings:
            return b''

        # Calculate mean
        centroid = np.mean(embeddings, axis=0).astype(np.float32)
        return centroid.tobytes()

    def create_clusters_from_analysis(self, clusters: List[Dict]) -> List[str]:
        """
        Create cluster records in database from analysis results.

        Args:
            clusters: List of cluster candidates from cluster_by_keywords()

        Returns:
            List of created cluster IDs
        """
        created_ids = []

        for cluster_data in clusters:
            keyword = cluster_data['keyword']
            nodes = cluster_data['nodes']

            # Generate meaningful name and summary
            name = f"Cluster: {keyword.title()}"
            summary = f"Semantic grouping around '{keyword}' with {len(nodes)} related memories"

            # Check if a cluster with this name already exists
            existing = self.cluster_mgr.get_cluster_by_name(name)
            if existing:
                logger.info(f"  Skipping existing cluster: {name}")
                continue

            # Calculate centroid embedding
            centroid = self._calculate_centroid(nodes)

            # Create cluster using ClusterManager
            cluster_id = self.cluster_mgr.create_cluster(
                name=name,
                summary=summary,
                centroid_embedding=centroid,
                initial_lock_in=0.15
            )

            # Add members using ClusterManager
            for node_id in nodes:
                self.cluster_mgr.add_member(
                    cluster_id=cluster_id,
                    node_id=node_id,
                    membership_strength=0.8  # Default strength
                )

            created_ids.append(cluster_id)
            logger.info(f"  Created: {name} ({len(nodes)} members)")

        return created_ids

    def run_clustering(self) -> Dict:
        """
        Execute full clustering pipeline.

        Steps:
        1. Analyze all memory nodes for keywords
        2. Find clusters via keyword co-occurrence
        3. Create cluster records in database
        4. Update cluster lock-in values

        Returns:
            Dict with results including cluster count and IDs
        """
        logger.info("")
        logger.info("=" * 50)
        logger.info("CLUSTERING ENGINE - Starting")
        logger.info("=" * 50)
        logger.info("")

        # Step 1 & 2: Analyze and find clusters
        clusters = self.cluster_by_keywords()

        if not clusters:
            logger.info("No clusters found.")
            return {
                'clusters_created': 0,
                'cluster_ids': [],
                'timestamp': datetime.now().isoformat()
            }

        # Step 3: Create cluster records
        logger.info(f"\nCreating {len(clusters)} clusters...")
        created_ids = self.create_clusters_from_analysis(clusters)

        # Step 4: Update average node lock-in for all created clusters
        for cluster_id in created_ids:
            self._update_cluster_avg_lock_in(cluster_id)

        logger.info(f"\nClustering complete: {len(created_ids)} clusters created")

        return {
            'clusters_created': len(created_ids),
            'cluster_ids': created_ids,
            'timestamp': datetime.now().isoformat()
        }

    def _update_cluster_avg_lock_in(self, cluster_id: str) -> float:
        """
        Calculate and update average node lock-in for a cluster.

        Args:
            cluster_id: The cluster to update

        Returns:
            The calculated average lock-in
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                SELECT AVG(mn.lock_in) as avg_lock_in
                FROM cluster_members cm
                JOIN memory_nodes mn ON cm.node_id = mn.id
                WHERE cm.cluster_id = ?
            """, (cluster_id,))
            row = cursor.fetchone()
            avg_lock_in = row['avg_lock_in'] if row and row['avg_lock_in'] else 0.15

            # Update cluster's avg_node_lock_in
            self.cluster_mgr.update_avg_node_lock_in(cluster_id, avg_lock_in)

            return avg_lock_in
        finally:
            conn.close()

    def get_unclustered_nodes(self, limit: int = 100) -> List[Dict]:
        """
        Find nodes that haven't been assigned to any cluster.

        Args:
            limit: Maximum nodes to return

        Returns:
            List of unclustered node data
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                SELECT mn.* FROM memory_nodes mn
                LEFT JOIN cluster_members cm ON mn.id = cm.node_id
                WHERE cm.node_id IS NULL
                  AND mn.content IS NOT NULL
                  AND mn.content != ''
                ORDER BY mn.lock_in DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def merge_similar_clusters(self, similarity_threshold: float = 0.6) -> int:
        """
        Merge clusters that have high member overlap.

        Args:
            similarity_threshold: Minimum Jaccard similarity to merge

        Returns:
            Number of merges performed
        """
        clusters = self.cluster_mgr.list_clusters()
        if len(clusters) < 2:
            return 0

        # Get members for each cluster
        cluster_members: Dict[str, Set[str]] = {}
        for c in clusters:
            members = self.cluster_mgr.get_cluster_members(c.cluster_id)
            # members is List[Tuple[str, float]] - (node_id, membership_strength)
            cluster_members[c.cluster_id] = {m[0] for m in members}

        merges = 0
        merged_clusters: Set[str] = set()

        cluster_ids = list(cluster_members.keys())

        for i, cid1 in enumerate(cluster_ids):
            if cid1 in merged_clusters:
                continue

            for cid2 in cluster_ids[i+1:]:
                if cid2 in merged_clusters:
                    continue

                members1 = cluster_members[cid1]
                members2 = cluster_members[cid2]

                # Jaccard similarity
                intersection = len(members1 & members2)
                union = len(members1 | members2)
                similarity = intersection / union if union > 0 else 0

                if similarity >= similarity_threshold:
                    # Merge cid2 into cid1
                    for node_id in members2:
                        if node_id not in members1:
                            self.cluster_mgr.add_member(cid1, node_id)

                    # Delete cid2
                    self.cluster_mgr.delete_cluster(cid2)
                    merged_clusters.add(cid2)
                    merges += 1

                    logger.info(f"Merged cluster {cid2} into {cid1}")

        return merges


if __name__ == "__main__":
    # Quick test
    from pathlib import Path
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    from luna.core.paths import memory_matrix_path
    db_path = memory_matrix_path()
    engine = ClusteringEngine(str(db_path))

    # Run clustering
    result = engine.run_clustering()
    print(f"\nResult: {result}")

    # Show stats
    stats = engine.cluster_mgr.get_stats()
    print(f"\nCluster stats: {stats}")
