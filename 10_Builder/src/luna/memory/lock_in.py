"""
Lock-in dynamics for Memory Economy clusters.

This module handles cluster-level lock-in calculation with Gemini's corrected formula:
- Logarithmic access boost (1->5 matters more than 100->105)
- State-dependent decay rates (crystallized decays slowest)
- Weighted combination of node strength, access, edges, and age

Note: This is separate from luna.substrate.lock_in which handles individual
memory node lock-in calculation. This module operates at the CLUSTER level.
"""

import math
import sqlite3
from datetime import datetime
from typing import Dict, Optional
import logging

from luna.memory.cluster_manager import ClusterManager, get_state_from_lock_in
from luna.memory.config_loader import get_weights, get_decay_lambdas


logger = logging.getLogger(__name__)


def _weights():
    """Load lock-in weights from config (hot-reloads on file change)."""
    return get_weights()


def _decay_lambdas():
    """Load decay rates from config (hot-reloads on file change)."""
    return get_decay_lambdas()


class LockInCalculator:
    """
    Calculates and updates cluster lock-in values.

    The lock-in formula combines four components:
    1. avg_member_lock_in: Weighted average of member node lock-ins
    2. log_access_factor: Logarithmic boost from access count (caps at 10 accesses)
    3. avg_edge_strength: Average strength*lock_in of connected edges
    4. age_factor: Newer clusters get slight boost (decays over 30 days)

    Then applies exponential decay based on time since last access,
    with decay rate varying by current state.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.cluster_mgr = ClusterManager(db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA busy_timeout=15000")
        conn.row_factory = sqlite3.Row
        return conn

    def calculate_cluster_lock_in(self, cluster_id: str) -> float:
        """
        Calculate lock-in for a cluster using Gemini's corrected formula.

        Formula:
            lock_in = (
                w_node * avg_member_lock_in +
                w_access * log_access_factor +
                w_edge * avg_edge_strength +
                w_age * age_factor
            ) * decay_factor

        Where:
            - log_access_factor = min(log(access_count + 1) / log(11), 1.0)
            - decay_factor = exp(-lambda * seconds_since_access)
            - lambda varies by state (crystallized decays slowest)

        Args:
            cluster_id: UUID of the cluster to calculate

        Returns:
            Lock-in value between 0.0 and 1.0
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Get cluster data
        cursor.execute("""
            SELECT access_count, created_at, last_accessed_at, state
            FROM clusters WHERE cluster_id = ?
        """, (cluster_id,))

        row = cursor.fetchone()
        if not row:
            conn.close()
            return 0.0

        access_count = row['access_count'] or 0
        created_at = row['created_at']
        last_accessed_at = row['last_accessed_at']
        current_state = row['state'] or 'drifting'

        # ==================== COMPONENT 1: Node Lock-In ====================
        # Weighted average of member node lock-ins
        # Uses memory_nodes.lock_in column (not lock_in_coefficient)
        cursor.execute("""
            SELECT n.lock_in, cm.membership_strength
            FROM cluster_members cm
            JOIN memory_nodes n ON cm.node_id = n.id
            WHERE cm.cluster_id = ?
        """, (cluster_id,))

        members = cursor.fetchall()

        if not members:
            # Cluster with no members defaults to low lock-in
            conn.close()
            return 0.15

        # Calculate weighted average of member lock-ins
        total_weight = sum(m['membership_strength'] or 1.0 for m in members)
        if total_weight > 0:
            weighted_node_strength = sum(
                (m['lock_in'] or 0.5) * (m['membership_strength'] or 1.0)
                for m in members
            ) / total_weight
        else:
            weighted_node_strength = 0.5

        # ==================== COMPONENT 2: Access Factor ====================
        # Logarithmic: log(access_count + 1) / log(11)
        # Caps at 1.0 when access_count = 10
        # Emphasizes early accesses (1->5) over late (100->105)
        # This is the key insight from Gemini's correction
        if access_count <= 0:
            access_factor = 0.0
        else:
            access_factor = min(math.log(access_count + 1) / math.log(11), 1.0)

        # ==================== COMPONENT 3: Edge Strength ====================
        # Average of (strength * lock_in) for all connected edges
        cursor.execute("""
            SELECT AVG(strength * lock_in) as avg_edge
            FROM cluster_edges
            WHERE from_cluster = ? OR to_cluster = ?
        """, (cluster_id, cluster_id))

        edge_row = cursor.fetchone()
        edge_factor = edge_row['avg_edge'] if edge_row and edge_row['avg_edge'] else 0.5

        # ==================== COMPONENT 4: Age Factor ====================
        # Newer clusters get a slight boost (simulates recency bias)
        # Decays with inverse function: 1 / (1 + age_days/30)
        try:
            if created_at:
                # Handle both ISO format and SQLite CURRENT_TIMESTAMP format
                created_str = created_at.replace('Z', '+00:00')
                if 'T' not in created_str:
                    # SQLite format: "2024-01-15 10:30:00"
                    created_dt = datetime.fromisoformat(created_str.replace(' ', 'T'))
                else:
                    created_dt = datetime.fromisoformat(created_str)

                # Remove timezone for comparison
                created_dt = created_dt.replace(tzinfo=None)
                age_days = (datetime.now() - created_dt).days
                age_factor = 1.0 / (1.0 + age_days / 30.0)
            else:
                age_factor = 0.5
        except Exception as e:
            logger.debug(f"Age factor calculation failed for {cluster_id}: {e}")
            age_factor = 0.5

        conn.close()

        # ==================== CALCULATE BASE LOCK-IN ====================
        weights = _weights()
        lock_in = (
            weights['node'] * weighted_node_strength +
            weights['access'] * access_factor +
            weights['edge'] * edge_factor +
            weights['age'] * age_factor
        )

        # ==================== APPLY DECAY ====================
        # Exponential decay based on time since last access
        # Decay rate varies by state (crystallized decays slowest)
        if last_accessed_at:
            try:
                # Parse last access timestamp
                last_str = last_accessed_at.replace('Z', '+00:00')
                if 'T' not in last_str:
                    last_access_dt = datetime.fromisoformat(last_str.replace(' ', 'T'))
                else:
                    last_access_dt = datetime.fromisoformat(last_str)

                last_access_dt = last_access_dt.replace(tzinfo=None)
                offline_seconds = (datetime.now() - last_access_dt).total_seconds()

                # Get decay rate for current state
                lambda_decay = _decay_lambdas().get(current_state, 0.001)

                # Exponential decay: lock_in *= exp(-lambda * time)
                decay_factor = math.exp(-lambda_decay * offline_seconds)
                lock_in *= decay_factor

            except Exception as e:
                logger.debug(f"Decay calculation failed for {cluster_id}: {e}")
                # Skip decay if timestamp parsing fails
                pass

        # Clamp to [0.0, 1.0]
        return max(0.0, min(1.0, lock_in))

    def update_cluster(self, cluster_id: str) -> Dict:
        """
        Recalculate and update lock-in for a cluster.

        This is the main entry point for updating a single cluster's lock-in.
        It calculates the new value, determines the new state, and persists both.

        Args:
            cluster_id: UUID of the cluster to update

        Returns:
            Dict with old/new lock-in values and state transition info:
            {
                'cluster_id': str,
                'old_lock_in': float,
                'new_lock_in': float,
                'old_state': str,
                'new_state': str,
                'state_changed': bool
            }
        """
        # Get current state
        old_cluster = self.cluster_mgr.get_cluster(cluster_id)
        if not old_cluster:
            return {'error': 'Cluster not found', 'cluster_id': cluster_id}

        old_lock_in = old_cluster.lock_in
        old_state = old_cluster.state

        # Calculate new lock-in
        new_lock_in = self.calculate_cluster_lock_in(cluster_id)
        new_state = get_state_from_lock_in(new_lock_in)

        # Update cluster
        self.cluster_mgr.update_lock_in(cluster_id, new_lock_in)

        return {
            'cluster_id': cluster_id,
            'old_lock_in': round(old_lock_in, 4),
            'new_lock_in': round(new_lock_in, 4),
            'old_state': old_state,
            'new_state': new_state,
            'state_changed': old_state != new_state
        }

    def update_all_clusters(self) -> Dict:
        """
        Update lock-in for all clusters.

        This is the main entry point for batch updates, typically called
        by the background service on a schedule.

        Returns:
            Dict with summary statistics:
            {
                'total': int,
                'updated': int,
                'state_changes': [
                    {'cluster_id': str, 'name': str, 'from': str, 'to': str}
                ],
                'errors': int
            }
        """
        clusters = self.cluster_mgr.list_clusters(limit=10000)

        results = {
            'total': len(clusters),
            'updated': 0,
            'state_changes': [],
            'errors': 0,
        }

        for cluster in clusters:
            try:
                update = self.update_cluster(cluster.cluster_id)

                if 'error' in update:
                    results['errors'] += 1
                    continue

                results['updated'] += 1

                if update.get('state_changed'):
                    results['state_changes'].append({
                        'cluster_id': cluster.cluster_id,
                        'name': cluster.name,
                        'from': update['old_state'],
                        'to': update['new_state']
                    })

            except Exception as e:
                logger.error(f"Error updating cluster {cluster.cluster_id}: {e}")
                results['errors'] += 1

        return results

    def get_decay_info(self, cluster_id: str) -> Dict:
        """
        Get detailed decay information for a cluster.

        Useful for debugging and understanding why a cluster's lock-in
        is changing over time.

        Args:
            cluster_id: UUID of the cluster

        Returns:
            Dict with decay analysis
        """
        cluster = self.cluster_mgr.get_cluster(cluster_id)
        if not cluster:
            return {'error': 'Cluster not found'}

        lambda_decay = _decay_lambdas().get(cluster.state, 0.001)
        half_life_seconds = math.log(2) / lambda_decay if lambda_decay > 0 else float('inf')

        # Calculate time since last access
        if cluster.last_accessed_at:
            try:
                last_str = cluster.last_accessed_at.replace('Z', '+00:00')
                if 'T' not in last_str:
                    last_access_dt = datetime.fromisoformat(last_str.replace(' ', 'T'))
                else:
                    last_access_dt = datetime.fromisoformat(last_str)
                last_access_dt = last_access_dt.replace(tzinfo=None)
                seconds_since_access = (datetime.now() - last_access_dt).total_seconds()
            except Exception:
                seconds_since_access = 0
        else:
            seconds_since_access = 0

        return {
            'cluster_id': cluster_id,
            'name': cluster.name,
            'current_state': cluster.state,
            'current_lock_in': cluster.lock_in,
            'decay_lambda': lambda_decay,
            'half_life_hours': half_life_seconds / 3600,
            'seconds_since_access': seconds_since_access,
            'decay_factor': math.exp(-lambda_decay * seconds_since_access) if seconds_since_access > 0 else 1.0,
        }


def verify_logarithmic_boost():
    """
    Verify that the logarithmic access boost works as expected.

    Key insight: Going from 1->5 accesses should provide more boost
    than going from 100->105 accesses.
    """
    print("=== Verifying Logarithmic Access Boost ===")
    print("Access Count | log_factor | Marginal Gain")
    print("-" * 45)

    prev_factor = 0.0
    for count in [0, 1, 2, 5, 10, 50, 100, 105, 200]:
        factor = min(math.log(count + 1) / math.log(11), 1.0)
        marginal = factor - prev_factor
        print(f"{count:>12} | {factor:.4f}     | +{marginal:.4f}")
        prev_factor = factor

    print("\nKey insight: 1->5 gain > 100->105 gain")
    gain_1_to_5 = min(math.log(6) / math.log(11), 1.0) - min(math.log(2) / math.log(11), 1.0)
    gain_100_to_105 = min(math.log(106) / math.log(11), 1.0) - min(math.log(101) / math.log(11), 1.0)
    print(f"Gain from 1->5: {gain_1_to_5:.4f}")
    print(f"Gain from 100->105: {gain_100_to_105:.4f}")


if __name__ == "__main__":
    from pathlib import Path

    # Run verification
    verify_logarithmic_boost()
    print()

    # Run actual update
    from luna.core.paths import memory_matrix_path
    db_path = memory_matrix_path()

    if db_path.exists():
        calc = LockInCalculator(str(db_path))

        print("=== Updating All Clusters ===")
        result = calc.update_all_clusters()

        print(f"Total: {result['total']}")
        print(f"Updated: {result['updated']}")
        print(f"Errors: {result['errors']}")

        if result['state_changes']:
            print("\nState Changes:")
            for change in result['state_changes']:
                print(f"  {change['name']}: {change['from']} -> {change['to']}")
        else:
            print("\nNo state changes.")
    else:
        print(f"Database not found: {db_path}")
