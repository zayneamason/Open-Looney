"""
ConstellationAssembler - Combines clusters and nodes into context.

This is the final assembly layer that:
1. Takes retrieval results (nodes + clusters)
2. Prioritizes by lock-in
3. Respects token budget
4. Formats for Director

The constellation represents Luna's "working context" — the memories and knowledge
clusters that are currently activated and ready to inform her response.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional

from luna.memory.cluster_manager import Cluster
from luna.memory.config_loader import get_constellation_params


@dataclass
class Constellation:
    """Assembled context from Memory Economy."""
    clusters: List[Dict] = field(default_factory=list)
    nodes: List[Dict] = field(default_factory=list)
    total_tokens: int = 0
    lock_in_distribution: Dict[str, int] = field(default_factory=dict)
    assembly_stats: Dict = field(default_factory=dict)


class ConstellationAssembler:
    """
    Assembles context constellations from retrieval results.

    The assembler takes raw retrieval results (clusters and nodes) and
    assembles them into a token-budgeted context that can be injected
    into the Director's prompt.

    Strategy:
    1. Sort clusters by lock-in (settled/crystallized first)
    2. Include clusters until budget is ~60% consumed
    3. Fill remaining budget with individual nodes
    4. Format for Director consumption
    """

    def __init__(self, max_tokens: int = None):
        """
        Initialize the assembler.

        Args:
            max_tokens: Maximum tokens to allocate for context (reads from config if None)
        """
        if max_tokens is None:
            params = get_constellation_params()
            max_tokens = params['max_tokens']
        self.max_tokens = max_tokens
        self.words_per_token = 0.75  # Conservative estimate

    def assemble(
        self,
        clusters: List[Dict],
        nodes: List[Dict],
        prioritize_clusters: bool = True
    ) -> Constellation:
        """
        Assemble constellation respecting token budget.

        Strategy:
        1. Include high lock-in clusters first
        2. Add individual nodes
        3. Stop when budget exhausted

        Args:
            clusters: List of cluster dicts with 'cluster' key containing Cluster object
            nodes: List of node dicts with 'content', 'node_type', etc.
            prioritize_clusters: Whether to prioritize clusters over nodes

        Returns:
            Constellation object with selected content and stats
        """
        selected_clusters = []
        selected_nodes = []
        total_tokens = 0

        # Sort clusters by lock-in (highest first)
        if clusters:
            clusters_sorted = sorted(
                clusters,
                key=lambda c: c['cluster'].lock_in if hasattr(c.get('cluster'), 'lock_in') else 0,
                reverse=True
            )
        else:
            clusters_sorted = []

        # Cluster budget: 60% of total if prioritizing clusters
        cluster_budget = int(self.max_tokens * 0.6) if prioritize_clusters else int(self.max_tokens * 0.3)
        node_budget = self.max_tokens - cluster_budget

        # Add clusters
        cluster_tokens = 0
        for cluster_data in clusters_sorted:
            cluster = cluster_data.get('cluster')
            if not cluster:
                continue

            estimated = self._estimate_cluster_tokens(cluster)

            if cluster_tokens + estimated <= cluster_budget:
                selected_clusters.append(cluster_data)
                cluster_tokens += estimated
            else:
                # Try to fit at least one more cluster if small
                if estimated < 100 and cluster_tokens + estimated <= self.max_tokens:
                    selected_clusters.append(cluster_data)
                    cluster_tokens += estimated
                break

        total_tokens = cluster_tokens

        # Add nodes
        for node in nodes:
            if not isinstance(node, dict):
                continue

            estimated = self._estimate_node_tokens(node)

            if total_tokens + estimated <= self.max_tokens:
                selected_nodes.append(node)
                total_tokens += estimated
            else:
                break

        # Calculate lock-in distribution
        lock_in_dist = {'crystallized': 0, 'settled': 0, 'fluid': 0, 'drifting': 0}

        for cluster_data in selected_clusters:
            cluster = cluster_data.get('cluster')
            if cluster:
                state = getattr(cluster, 'state', 'fluid')
                lock_in_dist[state] = lock_in_dist.get(state, 0) + 1

        return Constellation(
            clusters=selected_clusters,
            nodes=selected_nodes,
            total_tokens=total_tokens,
            lock_in_distribution=lock_in_dist,
            assembly_stats={
                'clusters_considered': len(clusters),
                'clusters_selected': len(selected_clusters),
                'nodes_considered': len(nodes),
                'nodes_selected': len(selected_nodes),
                'budget_used_pct': round(total_tokens / self.max_tokens * 100, 1) if self.max_tokens > 0 else 0,
                'cluster_tokens': cluster_tokens,
                'node_tokens': total_tokens - cluster_tokens,
            }
        )

    def _estimate_cluster_tokens(self, cluster) -> int:
        """
        Estimate tokens for a cluster.

        Args:
            cluster: Cluster object or dict

        Returns:
            Estimated token count
        """
        tokens = 50  # Base overhead for formatting

        # Add summary tokens
        if hasattr(cluster, 'summary') and cluster.summary:
            tokens += int(len(cluster.summary.split()) / self.words_per_token)

        # Add name tokens
        if hasattr(cluster, 'name') and cluster.name:
            tokens += int(len(cluster.name.split()) / self.words_per_token)

        # Estimate member content (assume ~20 tokens per member, max 5)
        member_count = getattr(cluster, 'member_count', 5)
        tokens += min(member_count, 5) * 20

        return tokens

    def _estimate_node_tokens(self, node: Dict) -> int:
        """
        Estimate tokens for a node.

        Args:
            node: Node dict with 'content' key

        Returns:
            Estimated token count
        """
        content = node.get('content', '')
        if not content:
            return 10  # Minimum for metadata
        return max(10, int(len(content.split()) / self.words_per_token))

    def format_for_director(self, constellation: Constellation) -> str:
        """
        Format constellation as text for Director's context.

        This produces a markdown-like format that Claude can easily parse
        and use for context-aware responses.

        Args:
            constellation: Assembled constellation

        Returns:
            Formatted context string
        """
        lines = []

        # Format clusters
        if constellation.clusters:
            lines.append("=== ACTIVE MEMORY CLUSTERS ===")
            lines.append("")

            for cluster_data in constellation.clusters:
                cluster = cluster_data.get('cluster')
                if not cluster:
                    continue

                name = getattr(cluster, 'name', 'Unnamed Cluster')
                state = getattr(cluster, 'state', 'unknown')
                lock_in = getattr(cluster, 'lock_in', 0)
                summary = getattr(cluster, 'summary', '')
                member_count = getattr(cluster, 'member_count', 0)
                relevance = cluster_data.get('relevance_score', cluster_data.get('edge_lock_in', 0))

                lines.append(f"**{name}**")
                lines.append(f"  State: {state} | Lock-in: {lock_in:.2f} | Members: {member_count} | Relevance: {relevance:.2f}")
                if summary:
                    lines.append(f"  {summary}")
                lines.append("")

        # Format nodes
        if constellation.nodes:
            lines.append("=== RELEVANT MEMORIES ===")
            lines.append("")

            for node in constellation.nodes:
                node_type = node.get('node_type', 'UNKNOWN')
                content = node.get('content', '')
                lock_in = node.get('lock_in', node.get('lock_in_coefficient', 0))

                # Truncate long content
                if len(content) > 200:
                    content = content[:200] + "..."

                lines.append(f"[{node_type}] (lock-in: {lock_in:.2f})")
                lines.append(f"  {content}")
                lines.append("")

        # Add context metadata as HTML comment (invisible to user but useful for debugging)
        stats = constellation.assembly_stats
        lines.append(f"<!-- Context: {stats.get('clusters_selected', 0)} clusters, "
                    f"{stats.get('nodes_selected', 0)} nodes, "
                    f"{stats.get('budget_used_pct', 0)}% budget used -->")

        return "\n".join(lines)

    def format_compact(self, constellation: Constellation) -> str:
        """
        Format constellation in a compact form for limited context windows.

        Args:
            constellation: Assembled constellation

        Returns:
            Compact formatted string
        """
        parts = []

        # Compact cluster summaries
        if constellation.clusters:
            cluster_summaries = []
            for cd in constellation.clusters:
                c = cd.get('cluster')
                if c:
                    name = getattr(c, 'name', '').replace('Cluster: ', '')
                    state = getattr(c, 'state', '?')[0].upper()  # First letter
                    cluster_summaries.append(f"{name}[{state}]")
            parts.append(f"Clusters: {', '.join(cluster_summaries)}")

        # Compact node types
        if constellation.nodes:
            node_types = [n.get('node_type', '?') for n in constellation.nodes]
            type_counts = {}
            for t in node_types:
                type_counts[t] = type_counts.get(t, 0) + 1
            type_str = ', '.join(f"{t}:{c}" for t, c in type_counts.items())
            parts.append(f"Nodes: {type_str}")

        return " | ".join(parts)


# Convenience function for quick assembly
def assemble_context(
    clusters: List[Dict],
    nodes: List[Dict],
    max_tokens: int = None
) -> str:
    """
    Quick helper to assemble and format context.

    Args:
        clusters: Cluster results from ClusterRetrieval
        nodes: Node results from Librarian
        max_tokens: Token budget

    Returns:
        Formatted context string ready for Director
    """
    assembler = ConstellationAssembler(max_tokens=max_tokens)
    constellation = assembler.assemble(clusters, nodes)
    return assembler.format_for_director(constellation)
