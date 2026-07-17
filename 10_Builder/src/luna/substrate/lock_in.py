"""
Lock-In Coefficient Calculator
==============================

Activity-based memory persistence using sigmoid dynamics.

The lock-in coefficient (L) represents how "settled" a memory is:
- L < 0.30: drifting (rarely accessed, may fade)
- 0.30 <= L < 0.70: fluid (active but not settled)
- L >= 0.70: settled (core knowledge, persistent)

Factors affecting L:
1. retrieval_count: How often this memory is accessed
2. reinforcement_count: Explicit reinforcement (by user)
3. locked_neighbors: Connected nodes that are settled
4. locked_tag_siblings: Tag-sharing nodes that are settled

Usage:
    from luna.substrate.lock_in import compute_lock_in, classify_state

    lock_in = compute_lock_in(
        retrieval_count=5,
        reinforcement_count=1,
        locked_neighbor_count=3,
        locked_tag_sibling_count=2
    )
    state = classify_state(lock_in)  # 'settled', 'fluid', or 'drifting'
"""

import math
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

class LockInState(str, Enum):
    """Memory persistence states."""
    DRIFTING = "drifting"  # L < 0.30 - rarely accessed, may fade
    FLUID = "fluid"        # 0.30 <= L < 0.70 - active but not settled
    SETTLED = "settled"    # L >= 0.70 - core knowledge, persistent


# Default weights for activity computation
DEFAULT_WEIGHTS = {
    "retrieval": 0.4,       # How often accessed
    "reinforcement": 0.3,   # Explicitly marked important
    "network": 0.2,         # Connected to settled nodes
    "tag_siblings": 0.1     # Shares tags with settled nodes
}

# Sigmoid parameters
DEFAULT_SIGMOID_K = 1.2   # Steepness
DEFAULT_SIGMOID_X0 = 0.5  # Midpoint

# Thresholds
THRESHOLD_SETTLED = 0.70
THRESHOLD_DRIFTING = 0.30

# Output bounds (never fully 0 or 1)
LOCK_IN_MIN = 0.15
LOCK_IN_MAX = 0.85


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class LockInConfig:
    """Configuration for lock-in computation."""
    enabled: bool = True  # When False, all memories return neutral lock-in (0.5)
    weight_retrieval: float = DEFAULT_WEIGHTS["retrieval"]
    weight_reinforcement: float = DEFAULT_WEIGHTS["reinforcement"]
    weight_network: float = DEFAULT_WEIGHTS["network"]
    weight_tag_siblings: float = DEFAULT_WEIGHTS["tag_siblings"]
    sigmoid_k: float = DEFAULT_SIGMOID_K
    sigmoid_x0: float = DEFAULT_SIGMOID_X0
    threshold_settled: float = THRESHOLD_SETTLED
    threshold_drifting: float = THRESHOLD_DRIFTING


# Global config (can be overridden)
_config: Optional[LockInConfig] = None


def get_config() -> LockInConfig:
    """Get current lock-in configuration."""
    global _config
    if _config is None:
        _config = LockInConfig()
    return _config


def set_config(config: LockInConfig) -> None:
    """Override lock-in configuration."""
    global _config
    _config = config
    logger.info("Lock-in config updated")


# =============================================================================
# CORE FUNCTIONS
# =============================================================================

def compute_activity(
    retrieval_count: int = 0,
    reinforcement_count: int = 0,
    locked_neighbor_count: int = 0,
    locked_tag_sibling_count: int = 0,
) -> float:
    """
    Compute raw activity score from weighted factors.

    Args:
        retrieval_count: How often this memory was accessed
        reinforcement_count: Times explicitly reinforced
        locked_neighbor_count: Count of connected nodes with L >= 0.7
        locked_tag_sibling_count: Count of tag-sharing nodes with L >= 0.7

    Returns:
        Activity score (typically 0.0 to 2.0+)
    """
    config = get_config()

    activity = (
        retrieval_count * config.weight_retrieval +
        reinforcement_count * config.weight_reinforcement +
        locked_neighbor_count * config.weight_network +
        locked_tag_sibling_count * config.weight_tag_siblings
    ) / 10.0

    return activity


def sigmoid(x: float, k: float = None, x0: float = None) -> float:
    """
    Standard sigmoid function.

    Args:
        x: Input value
        k: Steepness (higher = sharper transition)
        x0: Midpoint (where sigmoid = 0.5)

    Returns:
        Value between 0 and 1
    """
    config = get_config()
    k = k if k is not None else config.sigmoid_k
    x0 = x0 if x0 is not None else config.sigmoid_x0

    # Clamp to avoid overflow
    z = -k * (x - x0)
    if z > 700:
        return 0.0
    if z < -700:
        return 1.0

    return 1.0 / (1.0 + math.exp(z))


def compute_lock_in(
    retrieval_count: int = 0,
    reinforcement_count: int = 0,
    locked_neighbor_count: int = 0,
    locked_tag_sibling_count: int = 0,
) -> float:
    """
    Compute lock-in coefficient using sigmoid of activity.

    Returns value between LOCK_IN_MIN (0.15) and LOCK_IN_MAX (0.85).
    When lock-in is disabled, returns 0.5 (neutral) for all memories.

    Args:
        retrieval_count: How often this memory was accessed
        reinforcement_count: Times explicitly reinforced
        locked_neighbor_count: Connected nodes with L >= 0.7
        locked_tag_sibling_count: Tag-sharing nodes with L >= 0.7

    Returns:
        Lock-in coefficient (0.15 to 0.85), or 0.5 if disabled
    """
    config = get_config()

    # If lock-in is disabled, return neutral value (all memories equal)
    if not config.enabled:
        return 0.5

    activity = compute_activity(
        retrieval_count=retrieval_count,
        reinforcement_count=reinforcement_count,
        locked_neighbor_count=locked_neighbor_count,
        locked_tag_sibling_count=locked_tag_sibling_count,
    )

    raw_sigmoid = sigmoid(activity)

    # Scale to bounded range
    lock_in = LOCK_IN_MIN + (LOCK_IN_MAX - LOCK_IN_MIN) * raw_sigmoid

    return round(lock_in, 4)


def classify_state(lock_in: float) -> LockInState:
    """
    Classify lock-in coefficient into operational state.

    Args:
        lock_in: Lock-in coefficient (0.0-1.0)

    Returns:
        LockInState enum value
    """
    config = get_config()

    if lock_in >= config.threshold_settled:
        return LockInState.SETTLED
    elif lock_in <= config.threshold_drifting:
        return LockInState.DRIFTING
    else:
        return LockInState.FLUID


def get_state_emoji(state: LockInState) -> str:
    """Visual indicator for state."""
    return {
        LockInState.SETTLED: "🟢",
        LockInState.FLUID: "🔵",
        LockInState.DRIFTING: "🟠",
    }.get(state, "⚪")


# =============================================================================
# BATCH OPERATIONS
# =============================================================================

async def compute_lock_in_for_node(
    node_id: str,
    retrieval_count: int,
    reinforcement_count: int,
    graph: "MemoryGraph",
    db=None,
) -> tuple[float, LockInState]:
    """
    Compute lock-in for a specific node, including network effects.

    Queries the graph for neighbors and the DB for their lock-in states
    to count how many settled nodes surround this one.

    Args:
        node_id: The node to compute lock-in for
        retrieval_count: Access count for this node
        reinforcement_count: Reinforcement count for this node
        graph: The memory graph for neighbor lookup
        db: Optional DB handle for querying neighbor lock-in states

    Returns:
        Tuple of (lock_in_coefficient, state)
    """
    locked_neighbor_count = 0

    if graph and graph.has_node(node_id):
        neighbors = await graph.get_neighbors(node_id, depth=1)
        if neighbors and db:
            # Batch query: count neighbors with lock_in_state = 'settled'
            placeholders = ",".join("?" for _ in neighbors)
            try:
                row = await db.fetchone(
                    f"SELECT COUNT(*) FROM memory_nodes WHERE id IN ({placeholders}) AND lock_in_state = 'settled'",
                    tuple(neighbors),
                )
                locked_neighbor_count = row[0] if row else 0
            except Exception:
                locked_neighbor_count = len(neighbors) // 3  # Fallback
        elif neighbors:
            locked_neighbor_count = len(neighbors) // 3  # No DB, rough estimate

    # TODO: Implement tag sibling counting
    locked_tag_sibling_count = 0

    lock_in = compute_lock_in(
        retrieval_count=retrieval_count,
        reinforcement_count=reinforcement_count,
        locked_neighbor_count=locked_neighbor_count,
        locked_tag_sibling_count=locked_tag_sibling_count,
    )

    state = classify_state(lock_in)

    return lock_in, state
