"""
Luna Engine Parameter Registry
==============================

Central registry of all tunable parameters with metadata, bounds, and live updates.

Every parameter has:
- default: The default value
- bounds: (min, max) tuple
- step: Suggested increment for grid search
- description: Human-readable explanation
- path: Dot-notation path to the actual config location
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from luna.engine import LunaEngine

logger = logging.getLogger(__name__)


@dataclass
class ParamSpec:
    """Specification for a tunable parameter."""
    name: str
    default: Any
    bounds: tuple[float, float]
    step: float
    description: str
    category: str
    getter: Optional[Callable] = None
    setter: Optional[Callable] = None


# =============================================================================
# TUNABLE PARAMETERS REGISTRY
# =============================================================================

TUNABLE_PARAMS: dict[str, dict] = {
    # -------------------------------------------------------------------------
    # INFERENCE PARAMETERS
    # -------------------------------------------------------------------------
    "inference.temperature": {
        "default": 0.7,
        "bounds": (0.0, 2.0),
        "step": 0.1,
        "description": "Response creativity vs. consistency. Higher = more creative, lower = more focused.",
        "category": "inference",
    },
    "inference.top_p": {
        "default": 0.9,
        "bounds": (0.1, 1.0),
        "step": 0.05,
        "description": "Nucleus sampling threshold. Lower = more focused token selection.",
        "category": "inference",
    },
    "inference.repetition_penalty": {
        "default": 1.1,
        "bounds": (1.0, 2.0),
        "step": 0.1,
        "description": "Penalty for repeating tokens. Higher = less repetition.",
        "category": "inference",
    },
    "inference.hot_path_timeout_ms": {
        "default": 200,
        "bounds": (50, 1000),
        "step": 50,
        "description": "Timeout for hot path (local) inference in milliseconds.",
        "category": "inference",
    },
    "inference.max_tokens": {
        "default": 512,
        "bounds": (64, 2048),
        "step": 64,
        "description": "Maximum tokens to generate per response.",
        "category": "inference",
    },

    # -------------------------------------------------------------------------
    # MEMORY LOCK-IN PARAMETERS
    # -------------------------------------------------------------------------
    "memory.lock_in.enabled": {
        "default": 1.0,
        "bounds": (0.0, 1.0),
        "step": 1.0,
        "description": "Enable lock-in memory system. 0 = off, 1 = on. When off, all memories have equal weight.",
        "category": "memory",
    },
    "memory.lock_in.access_weight": {
        "default": 0.3,
        "bounds": (0.0, 1.0),
        "step": 0.1,
        "description": "Weight of access count in lock-in calculation. Higher = more weight on retrieval frequency.",
        "category": "memory",
    },
    "memory.lock_in.reinforcement_weight": {
        "default": 0.5,
        "bounds": (0.0, 1.0),
        "step": 0.1,
        "description": "Weight of explicit reinforcement in lock-in. Higher = more weight on user confirmations.",
        "category": "memory",
    },
    "memory.lock_in.recency_weight": {
        "default": 0.2,
        "bounds": (0.0, 1.0),
        "step": 0.1,
        "description": "Weight of recency in lock-in. Higher = newer memories lock in faster.",
        "category": "memory",
    },
    "memory.lock_in.sigmoid_k": {
        "default": 10.0,
        "bounds": (1.0, 50.0),
        "step": 5.0,
        "description": "Steepness of sigmoid curve for lock-in progression.",
        "category": "memory",
    },
    "memory.lock_in.settled_threshold": {
        "default": 0.70,
        "bounds": (0.5, 0.95),
        "step": 0.05,
        "description": "Lock-in threshold for 'settled' state. Higher = harder to reach settled.",
        "category": "memory",
    },
    "memory.lock_in.drifting_threshold": {
        "default": 0.30,
        "bounds": (0.1, 0.5),
        "step": 0.05,
        "description": "Lock-in threshold below which memories are 'drifting' (may fade).",
        "category": "memory",
    },

    # -------------------------------------------------------------------------
    # ROUTER PARAMETERS
    # -------------------------------------------------------------------------
    "router.direct_threshold": {
        "default": 0.2,
        "bounds": (0.0, 0.5),
        "step": 0.05,
        "description": "Complexity threshold for direct (cached/simple) responses.",
        "category": "router",
    },
    "router.simple_threshold": {
        "default": 0.5,
        "bounds": (0.3, 0.7),
        "step": 0.05,
        "description": "Complexity threshold for simple (local model) responses.",
        "category": "router",
    },
    "router.full_threshold": {
        "default": 0.8,
        "bounds": (0.6, 1.0),
        "step": 0.05,
        "description": "Complexity threshold for full (delegated) responses.",
        "category": "router",
    },
    "router.force_memory_to_plan": {
        "default": 1.0,
        "bounds": (0.0, 1.0),
        "step": 1.0,
        "description": "Force memory queries to SIMPLE_PLAN path. 0 = off, 1 = on. When on, memory queries always trigger the RETRIEVE action.",
        "category": "router",
    },
    "router.memory_min_complexity": {
        "default": 0.3,
        "bounds": (0.1, 0.6),
        "step": 0.05,
        "description": "Minimum complexity floor for forced memory queries. Ensures they don't accidentally fall to DIRECT.",
        "category": "router",
    },
    "router.force_research_to_full": {
        "default": 1.0,
        "bounds": (0.0, 1.0),
        "step": 1.0,
        "description": "Force research queries to FULL_PLAN path. 0 = off, 1 = on. When on, research queries get multi-step planning.",
        "category": "router",
    },

    # -------------------------------------------------------------------------
    # HISTORY PARAMETERS
    # -------------------------------------------------------------------------
    "history.max_active_tokens": {
        "default": 1000,
        "bounds": (500, 4000),
        "step": 250,
        "description": "Maximum tokens in active conversation window.",
        "category": "history",
    },
    "history.max_active_turns": {
        "default": 10,
        "bounds": (3, 20),
        "step": 1,
        "description": "Maximum turns in active conversation window.",
        "category": "history",
    },
    "history.compression_ratio": {
        "default": 0.3,
        "bounds": (0.1, 0.5),
        "step": 0.05,
        "description": "Target compression ratio for recent tier summaries.",
        "category": "history",
    },

    # -------------------------------------------------------------------------
    # CONTEXT PARAMETERS
    # -------------------------------------------------------------------------
    "context.token_budget": {
        "default": 8000,
        "bounds": (2000, 16000),
        "step": 1000,
        "description": "Total token budget for context window.",
        "category": "context",
    },
    "context.memory_allocation": {
        "default": 0.3,
        "bounds": (0.1, 0.5),
        "step": 0.05,
        "description": "Fraction of context budget allocated to memory retrieval.",
        "category": "context",
    },
    "context.decay_factor": {
        "default": 0.95,
        "bounds": (0.8, 0.99),
        "step": 0.01,
        "description": "Decay factor for attention weights over time.",
        "category": "context",
    },
    "context.rebalance_threshold": {
        "default": 0.3,
        "bounds": (0.1, 0.5),
        "step": 0.05,
        "description": "Imbalance threshold that triggers context rebalancing.",
        "category": "context",
    },

    # -------------------------------------------------------------------------
    # RETRIEVAL PARAMETERS
    # -------------------------------------------------------------------------
    "retrieval.top_k": {
        "default": 10,
        "bounds": (3, 50),
        "step": 5,
        "description": "Number of memories to retrieve for context.",
        "category": "retrieval",
    },
    "retrieval.min_similarity": {
        "default": 0.5,
        "bounds": (0.0, 0.9),
        "step": 0.1,
        "description": "Minimum similarity threshold for memory retrieval.",
        "category": "retrieval",
    },
    "retrieval.recency_boost": {
        "default": 0.1,
        "bounds": (0.0, 0.5),
        "step": 0.05,
        "description": "Boost factor for recently accessed memories.",
        "category": "retrieval",
    },
    "retrieval.importance_boost": {
        "default": 0.2,
        "bounds": (0.0, 0.5),
        "step": 0.05,
        "description": "Boost factor for high-importance memories.",
        "category": "retrieval",
    },

    # -------------------------------------------------------------------------
    # ENGINE TICK PARAMETERS
    # -------------------------------------------------------------------------
    "engine.cognitive_interval": {
        "default": 0.5,
        "bounds": (0.1, 2.0),
        "step": 0.1,
        "description": "Interval between cognitive ticks in seconds.",
        "category": "engine",
    },
    "engine.reflective_interval": {
        "default": 300.0,
        "bounds": (60.0, 600.0),
        "step": 60.0,
        "description": "Interval between reflective ticks in seconds.",
        "category": "engine",
    },

    # -------------------------------------------------------------------------
    # MEMORY ECONOMY PARAMETERS
    # -------------------------------------------------------------------------
    "memory_economy.enabled": {
        "default": 1.0,
        "bounds": (0.0, 1.0),
        "step": 1.0,
        "description": "Enable Memory Economy cluster-based retrieval. 0 = basic node search, 1 = constellation assembly.",
        "category": "memory_economy",
    },
    "memory_economy.use_clusters": {
        "default": 1.0,
        "bounds": (0.0, 1.0),
        "step": 1.0,
        "description": "Use cluster-aware retrieval. When enabled, related memories are retrieved together.",
        "category": "memory_economy",
    },
    "memory_economy.max_clusters_per_query": {
        "default": 5,
        "bounds": (1, 10),
        "step": 1,
        "description": "Maximum clusters to activate per query. Higher = more context, more tokens.",
        "category": "memory_economy",
    },
    "memory_economy.cluster_budget_pct": {
        "default": 0.6,
        "bounds": (0.3, 0.9),
        "step": 0.1,
        "description": "Fraction of token budget allocated to cluster content (vs individual nodes).",
        "category": "memory_economy",
    },
    "memory_economy.auto_activation_threshold": {
        "default": 0.80,
        "bounds": (0.5, 0.95),
        "step": 0.05,
        "description": "Lock-in threshold for auto-activated clusters (always warm).",
        "category": "memory_economy",
    },
    "memory_economy.similarity_threshold": {
        "default": 0.82,
        "bounds": (0.5, 0.95),
        "step": 0.02,
        "description": "Minimum similarity for cluster membership.",
        "category": "memory_economy",
    },
    "memory_economy.merge_threshold": {
        "default": 0.6,
        "bounds": (0.3, 0.9),
        "step": 0.1,
        "description": "Similarity threshold for merging clusters.",
        "category": "memory_economy",
    },
    "memory_economy.min_cluster_size": {
        "default": 3,
        "bounds": (2, 10),
        "step": 1,
        "description": "Minimum members for a valid cluster.",
        "category": "memory_economy",
    },
    "memory_economy.max_cluster_size": {
        "default": 50,
        "bounds": (20, 100),
        "step": 10,
        "description": "Maximum members before cluster splits.",
        "category": "memory_economy",
    },
    "memory_economy.decay.drifting": {
        "default": 0.01,
        "bounds": (0.001, 0.1),
        "step": 0.005,
        "description": "Lock-in decay rate for drifting clusters (per second).",
        "category": "memory_economy",
    },
    "memory_economy.decay.fluid": {
        "default": 0.001,
        "bounds": (0.0001, 0.01),
        "step": 0.001,
        "description": "Lock-in decay rate for fluid clusters.",
        "category": "memory_economy",
    },
    "memory_economy.decay.settled": {
        "default": 0.0001,
        "bounds": (0.00001, 0.001),
        "step": 0.0001,
        "description": "Lock-in decay rate for settled clusters.",
        "category": "memory_economy",
    },
    "memory_economy.weight.node": {
        "default": 0.40,
        "bounds": (0.1, 0.7),
        "step": 0.05,
        "description": "Weight of node content in cluster lock-in calculation.",
        "category": "memory_economy",
    },
    "memory_economy.weight.access": {
        "default": 0.30,
        "bounds": (0.1, 0.5),
        "step": 0.05,
        "description": "Weight of access frequency in cluster lock-in.",
        "category": "memory_economy",
    },
    "memory_economy.weight.edge": {
        "default": 0.20,
        "bounds": (0.05, 0.4),
        "step": 0.05,
        "description": "Weight of edge connections in cluster lock-in.",
        "category": "memory_economy",
    },
    "memory_economy.weight.age": {
        "default": 0.10,
        "bounds": (0.0, 0.3),
        "step": 0.05,
        "description": "Weight of age/recency in cluster lock-in.",
        "category": "memory_economy",
    },
}


class ParamRegistry:
    """
    Central registry for all tunable parameters.

    Provides get/set methods that route to actual engine config,
    enabling live updates without restart.
    """

    def __init__(self, engine: Optional["LunaEngine"] = None):
        """
        Initialize the parameter registry.

        Args:
            engine: Optional LunaEngine instance for live updates.
                    If None, operates in standalone mode (for testing).
        """
        self.engine = engine
        self._overrides: dict[str, Any] = {}
        self._specs = self._build_specs()

    def _build_specs(self) -> dict[str, ParamSpec]:
        """Build ParamSpec objects from TUNABLE_PARAMS."""
        specs = {}
        for name, config in TUNABLE_PARAMS.items():
            specs[name] = ParamSpec(
                name=name,
                default=config["default"],
                bounds=config["bounds"],
                step=config["step"],
                description=config["description"],
                category=config["category"],
            )
        return specs

    def list_params(self, category: Optional[str] = None) -> list[str]:
        """List all parameter names, optionally filtered by category."""
        if category:
            return [name for name, spec in self._specs.items() if spec.category == category]
        return list(self._specs.keys())

    def list_categories(self) -> list[str]:
        """List all parameter categories."""
        return sorted(set(spec.category for spec in self._specs.values()))

    def get_spec(self, name: str) -> ParamSpec:
        """Get the specification for a parameter."""
        if name not in self._specs:
            raise KeyError(f"Unknown parameter: {name}")
        return self._specs[name]

    def get(self, name: str) -> Any:
        """
        Get the current value of a parameter.

        Priority: override > engine config > default
        """
        if name not in self._specs:
            raise KeyError(f"Unknown parameter: {name}")

        # Check overrides first
        if name in self._overrides:
            return self._overrides[name]

        # Try to get from engine if available
        if self.engine:
            value = self._get_from_engine(name)
            if value is not None:
                return value

        # Fall back to default
        return self._specs[name].default

    def set(self, name: str, value: Any, validate: bool = True) -> Any:
        """
        Set a parameter value.

        Args:
            name: Parameter name
            value: New value
            validate: If True, validate against bounds

        Returns:
            The previous value
        """
        if name not in self._specs:
            raise KeyError(f"Unknown parameter: {name}")

        spec = self._specs[name]

        # Validate bounds
        if validate and isinstance(value, (int, float)):
            min_val, max_val = spec.bounds
            if value < min_val or value > max_val:
                raise ValueError(f"Value {value} out of bounds [{min_val}, {max_val}] for {name}")

        # Get previous value
        prev_value = self.get(name)

        # Store override
        self._overrides[name] = value

        # Apply to engine if available
        if self.engine:
            self._apply_to_engine(name, value)

        logger.info(f"Parameter {name}: {prev_value} -> {value}")
        return prev_value

    def reset(self, name: str) -> Any:
        """Reset a parameter to its default value."""
        if name in self._overrides:
            prev = self._overrides.pop(name)
            default = self._specs[name].default
            if self.engine:
                self._apply_to_engine(name, default)
            logger.info(f"Parameter {name} reset to default: {default}")
            return prev
        return None

    def reset_all(self) -> int:
        """Reset all parameters to defaults. Returns count of reset params."""
        count = len(self._overrides)
        for name in list(self._overrides.keys()):
            self.reset(name)
        return count

    def get_all(self) -> dict[str, Any]:
        """Get all current parameter values as a dict."""
        return {name: self.get(name) for name in self._specs}

    def get_overrides(self) -> dict[str, Any]:
        """Get only the overridden parameters."""
        return dict(self._overrides)

    def export(self) -> dict:
        """Export current parameters with metadata."""
        return {
            name: {
                "value": self.get(name),
                "default": spec.default,
                "bounds": spec.bounds,
                "category": spec.category,
                "is_overridden": name in self._overrides,
            }
            for name, spec in self._specs.items()
        }

    def import_params(self, params: dict[str, Any]) -> int:
        """
        Import parameter values from a dict.

        Args:
            params: Dict of parameter name -> value

        Returns:
            Number of parameters imported
        """
        count = 0
        for name, value in params.items():
            try:
                self.set(name, value)
                count += 1
            except (KeyError, ValueError) as e:
                logger.warning(f"Skipping {name}: {e}")
        return count

    def _get_from_engine(self, name: str) -> Optional[Any]:
        """Get a parameter value from the live engine."""
        if not self.engine:
            return None

        parts = name.split(".")

        # Route to appropriate actor/component
        try:
            if parts[0] == "inference":
                director = self.engine.get_actor("director")
                if director and hasattr(director, "_local_model"):
                    local = director._local_model
                    if parts[1] == "temperature":
                        return getattr(local, "temperature", None)
                    if parts[1] == "top_p":
                        return getattr(local, "top_p", None)

            if parts[0] == "memory":
                matrix = self.engine.get_actor("matrix")
                if matrix and hasattr(matrix, "_matrix"):
                    mm = matrix._matrix
                    if parts[1] == "lock_in" and len(parts) > 2:
                        if parts[2] == "access_weight":
                            return getattr(mm, "access_weight", None)
                        if parts[2] == "reinforcement_weight":
                            return getattr(mm, "reinforcement_weight", None)

            if parts[0] == "router":
                router = self.engine.get_actor("router")
                if router:
                    if parts[1] == "direct_threshold":
                        return getattr(router, "DIRECT_THRESHOLD", None)
                    if parts[1] == "simple_threshold":
                        return getattr(router, "SIMPLE_THRESHOLD", None)

            if parts[0] == "history":
                history = self.engine.get_actor("history")
                if history and hasattr(history, "config"):
                    cfg = history.config
                    if parts[1] == "max_active_tokens":
                        return getattr(cfg, "max_active_tokens", None)
                    if parts[1] == "max_active_turns":
                        return getattr(cfg, "max_active_turns", None)

        except Exception as e:
            logger.debug(f"Could not get {name} from engine: {e}")

        return None

    def _apply_to_engine(self, name: str, value: Any) -> bool:
        """Apply a parameter change to the live engine."""
        if not self.engine:
            return False

        parts = name.split(".")

        try:
            if parts[0] == "inference":
                director = self.engine.get_actor("director")
                if director and hasattr(director, "_local_model"):
                    local = director._local_model
                    if parts[1] == "temperature":
                        local.temperature = value
                        return True
                    if parts[1] == "top_p":
                        local.top_p = value
                        return True

            if parts[0] == "memory":
                if parts[1] == "lock_in" and len(parts) > 2:
                    # Handle lock-in enabled toggle directly via lock_in module
                    if parts[2] == "enabled":
                        from luna.substrate.lock_in import get_config, set_config
                        config = get_config()
                        config.enabled = bool(value)
                        set_config(config)
                        logger.info(f"Lock-in system {'enabled' if config.enabled else 'disabled'}")
                        return True

                # Other memory params go through matrix actor
                matrix = self.engine.get_actor("matrix")
                if matrix and hasattr(matrix, "_matrix"):
                    mm = matrix._matrix
                    if parts[1] == "lock_in" and len(parts) > 2:
                        if parts[2] == "access_weight":
                            mm.access_weight = value
                            return True
                        if parts[2] == "reinforcement_weight":
                            mm.reinforcement_weight = value
                            return True

            if parts[0] == "router":
                router = self.engine.get_actor("router")
                if router:
                    if parts[1] == "direct_threshold":
                        router.DIRECT_THRESHOLD = value
                        return True
                    if parts[1] == "simple_threshold":
                        router.SIMPLE_THRESHOLD = value
                        return True
                    if parts[1] == "full_threshold":
                        router.FULL_THRESHOLD = value
                        return True
                    # Path forcing params
                    if parts[1] == "force_memory_to_plan":
                        router._force_memory_to_plan = bool(value)
                        logger.info(f"Memory path forcing {'enabled' if value else 'disabled'}")
                        return True
                    if parts[1] == "memory_min_complexity":
                        router._memory_min_complexity = float(value)
                        return True
                    if parts[1] == "force_research_to_full":
                        router._force_research_to_full = bool(value)
                        logger.info(f"Research path forcing {'enabled' if value else 'disabled'}")
                        return True

            if parts[0] == "history":
                history = self.engine.get_actor("history")
                if history and hasattr(history, "config"):
                    cfg = history.config
                    if parts[1] == "max_active_tokens":
                        cfg.max_active_tokens = value
                        return True
                    if parts[1] == "max_active_turns":
                        cfg.max_active_turns = value
                        return True

            if parts[0] == "memory_economy":
                # Update Memory Economy config file
                return self._apply_memory_economy_param(parts[1:], value)

            logger.debug(f"No live update path for {name}")
            return False

        except Exception as e:
            logger.warning(f"Failed to apply {name}={value} to engine: {e}")
            return False

    def _apply_memory_economy_param(self, parts: list, value: Any) -> bool:
        """Apply a Memory Economy parameter change to the config file."""
        import json
        from pathlib import Path

        from luna.core.paths import config_dir as _cfg_dir
        config_path = _cfg_dir() / "memory_economy_config.json"

        try:
            # Load current config
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = json.load(f)
            else:
                # Create default config if it doesn't exist
                config = {
                    "enabled": True,
                    "use_clusters": True,
                    "constellation": {"max_tokens": 3000, "prioritize_clusters": True},
                    "thresholds": {"drifting": 0.20, "fluid": 0.70, "settled": 0.85, "similarity": 0.82, "auto_activation": 0.80},
                    "weights": {"node": 0.40, "access": 0.30, "edge": 0.20, "age": 0.10},
                    "decay": {"crystallized": 0.00001, "settled": 0.0001, "fluid": 0.001, "drifting": 0.01},
                    "clustering": {"min_cluster_size": 3, "max_cluster_size": 50, "merge_similarity_threshold": 0.6}
                }

            # Route parameter to config location
            if len(parts) == 1:
                param = parts[0]
                if param == "enabled":
                    config["enabled"] = bool(value)
                elif param == "use_clusters":
                    config["use_clusters"] = bool(value)
                elif param == "max_clusters_per_query":
                    config.setdefault("retrieval", {})["max_clusters_per_query"] = int(value)
                elif param == "cluster_budget_pct":
                    config.setdefault("constellation", {})["cluster_budget_pct"] = float(value)
                elif param == "auto_activation_threshold":
                    config.setdefault("thresholds", {})["auto_activation"] = float(value)
                elif param == "similarity_threshold":
                    config.setdefault("thresholds", {})["similarity"] = float(value)
                elif param == "merge_threshold":
                    config.setdefault("clustering", {})["merge_similarity_threshold"] = float(value)
                elif param == "min_cluster_size":
                    config.setdefault("clustering", {})["min_cluster_size"] = int(value)
                elif param == "max_cluster_size":
                    config.setdefault("clustering", {})["max_cluster_size"] = int(value)
                else:
                    logger.debug(f"Unknown memory_economy param: {param}")
                    return False
            elif len(parts) == 2:
                category, param = parts
                if category == "decay":
                    config.setdefault("decay", {})[param] = float(value)
                elif category == "weight":
                    config.setdefault("weights", {})[param] = float(value)
                else:
                    logger.debug(f"Unknown memory_economy category: {category}")
                    return False
            else:
                logger.debug(f"Unknown memory_economy param path: {parts}")
                return False

            # Write updated config
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)

            logger.info(f"Memory Economy config updated: {'.'.join(parts)}={value}")
            return True

        except Exception as e:
            logger.error(f"Failed to update Memory Economy config: {e}")
            return False
