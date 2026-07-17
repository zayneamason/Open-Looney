"""
Shared config loader for Memory Economy runtime parameters.

Reads from config/memory_economy_config.json so that settings changed
in the Settings Panel take effect at runtime without restarting.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict

from luna.core.paths import config_dir

logger = logging.getLogger(__name__)

_CONFIG_PATH = config_dir() / "memory_economy_config.json"

# In-memory cache with mtime tracking for hot-reload
_cache: Dict[str, Any] = {}
_cache_mtime: float = 0.0


def load_memory_economy_config() -> Dict[str, Any]:
    """
    Load memory_economy_config.json with file-mtime caching.

    Returns the full config dict.  Falls back to hardcoded defaults
    if the file is missing or unreadable.
    """
    global _cache, _cache_mtime

    try:
        current_mtime = _CONFIG_PATH.stat().st_mtime
        if current_mtime == _cache_mtime and _cache:
            return _cache

        with open(_CONFIG_PATH) as f:
            _cache = json.load(f)
            _cache_mtime = current_mtime
            return _cache
    except Exception as e:
        logger.debug(f"Could not read {_CONFIG_PATH}: {e} — using defaults")
        return {}


def get_weights() -> Dict[str, float]:
    """Lock-in component weights (node/access/edge/age)."""
    defaults = {'node': 0.40, 'access': 0.30, 'edge': 0.20, 'age': 0.10}
    cfg = load_memory_economy_config()
    return cfg.get('weights', defaults)


def get_decay_lambdas() -> Dict[str, float]:
    """Decay rates per state (crystallized/settled/fluid/drifting)."""
    defaults = {
        'crystallized': 0.00001,
        'settled': 0.0001,
        'fluid': 0.001,
        'drifting': 0.01,
    }
    cfg = load_memory_economy_config()
    return cfg.get('decay', defaults)


def get_state_thresholds() -> Dict[str, float]:
    """State boundary thresholds (drifting/fluid/settled)."""
    defaults = {'drifting': 0.20, 'fluid': 0.70, 'settled': 0.85}
    cfg = load_memory_economy_config()
    thresholds = cfg.get('thresholds', {})
    return {
        'drifting': thresholds.get('drifting', defaults['drifting']),
        'fluid': thresholds.get('fluid', defaults['fluid']),
        'settled': thresholds.get('settled', defaults['settled']),
    }


def get_clustering_params() -> Dict[str, Any]:
    """Clustering engine parameters."""
    defaults = {
        'similarity_threshold': 0.82,
        'min_cluster_size': 3,
        'max_cluster_size': 50,
        'min_keyword_overlap': 0.4,
        'max_generic_frequency': 100,
    }
    cfg = load_memory_economy_config()
    clustering = cfg.get('clustering', {})
    thresholds = cfg.get('thresholds', {})
    result = dict(defaults)
    for k in defaults:
        if k in clustering:
            result[k] = clustering[k]
    # similarity_threshold lives under thresholds.similarity in the config
    if 'similarity' in thresholds:
        result['similarity_threshold'] = thresholds['similarity']
    return result


def get_retrieval_params() -> Dict[str, Any]:
    """Cluster retrieval parameters."""
    defaults = {
        'auto_activation_threshold': 0.80,
        'max_clusters_per_query': 5,
    }
    cfg = load_memory_economy_config()
    retrieval = cfg.get('retrieval', {})
    thresholds = cfg.get('thresholds', {})
    result = dict(defaults)
    if 'max_clusters_per_query' in retrieval:
        result['max_clusters_per_query'] = retrieval['max_clusters_per_query']
    if 'auto_activation' in thresholds:
        result['auto_activation_threshold'] = thresholds['auto_activation']
    return result


def get_constellation_params() -> Dict[str, Any]:
    """Constellation assembler parameters."""
    defaults = {'max_tokens': 3000, 'cluster_budget_pct': 0.6}
    cfg = load_memory_economy_config()
    constellation = cfg.get('constellation', defaults)
    return {
        'max_tokens': constellation.get('max_tokens', defaults['max_tokens']),
        'cluster_budget_pct': constellation.get('cluster_budget_pct', defaults['cluster_budget_pct']),
    }


def invalidate_cache():
    """Force next call to re-read the file (used after settings save)."""
    global _cache, _cache_mtime
    _cache = {}
    _cache_mtime = 0.0
