"""Ring configuration loader (Phase 0 façade).

In Phase 0 of Memory Policy v1.0 this module becomes a thin façade per
``Docs/RCW/PHASE0_RING_CONFIG_FACADE_CONTRACT.md``:

  1. If ``config/policy.yaml`` exists and validates, derive the legacy
     ``RingConfig`` shape from the new ``Policy`` dataclass.
  2. Else, load ``config/rings.yaml`` unchanged (legacy path).

The public ``RingConfig`` dataclass shape is unchanged; every existing
caller (``engine.py``, ``context.py``, ``context/assembler.py``,
``luna_mcp/tools/state.py``) sees the same surface. Config changes still
require engine restart — this is intentional.

Usage:
    from luna.config.ring_config import ring_config
    ring_config.ingest.min_useful_score  # 0.25
    ring_config.rebalance.promotion_threshold  # 0.8
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml

# config sits at project root: src/luna/config/ -> ../../../../config/
_CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config"
POLICY_PATH = _CONFIG_DIR / "policy.yaml"
LEGACY_PATH = _CONFIG_DIR / "rings.yaml"
# Backwards-compatible alias preserved for any importer that referenced it.
CONFIG_PATH = LEGACY_PATH


@dataclass(frozen=True)
class RingBudget:
    max: int
    min: int
    percent: float


@dataclass(frozen=True)
class IngestConfig:
    min_useful_score: float
    floor_score: float
    relevant_threshold: float


@dataclass(frozen=True)
class RebalanceConfig:
    tick_interval_ms: int
    age_guard_turns: int
    demotion_threshold: float
    promotion_threshold: float
    decay_factor: float


@dataclass(frozen=True)
class RingConfig:
    total_budget: int
    core: RingBudget
    inner: RingBudget
    middle: RingBudget
    outer: RingBudget
    ingest: IngestConfig
    source_rules: dict
    assembly_order: list
    rebalance: RebalanceConfig
    capabilities: dict


def _ring_budget_from_policy(ring_cfg) -> RingBudget:
    return RingBudget(max=ring_cfg.max, min=ring_cfg.min, percent=ring_cfg.target_pct)


def _load_from_policy(path: Path) -> RingConfig:
    """Build a legacy ``RingConfig`` from a validated ``Policy`` at ``path``.

    The Phase 0 façade reads the new schema for the budget + Rule 3
    thresholds, and reads the legacy compat blocks (``ingest``, ``rebalance``,
    ``sources``, ``capabilities``, ``assembly_order``) verbatim from
    ``policy.yaml`` for fields not yet covered by the new schema.
    """
    # Lazy import: ``luna.policy`` would otherwise cycle through
    # ``luna.core.context`` (which imports this module).
    from luna.policy import PolicyLoader

    policy = PolicyLoader.load_and_validate(path)
    rings = policy.budget.rings
    rebalance_legacy = dict(policy.rebalance)
    ingest_legacy = dict(policy.ingest)

    rule_3 = dict(policy.rule_3_transitions)
    rule_2 = dict(policy.rule_2_decay)

    return RingConfig(
        total_budget=policy.budget.total,
        core=_ring_budget_from_policy(rings["CORE"]),
        inner=_ring_budget_from_policy(rings["INNER"]),
        middle=_ring_budget_from_policy(rings["MIDDLE"]),
        outer=_ring_budget_from_policy(rings["OUTER"]),
        ingest=IngestConfig(
            min_useful_score=float(ingest_legacy["min_useful_score"]),
            floor_score=float(ingest_legacy["floor_score"]),
            relevant_threshold=float(ingest_legacy["relevant_threshold"]),
        ),
        source_rules=dict(policy.sources),
        assembly_order=list(policy.assembly_order),
        rebalance=RebalanceConfig(
            tick_interval_ms=int(rebalance_legacy["tick_interval_ms"]),
            age_guard_turns=int(rule_2.get(
                "age_guard_turns",
                rebalance_legacy.get("age_guard_turns"),
            )),
            demotion_threshold=float(rule_3.get(
                "demote_threshold",
                rebalance_legacy.get("demotion_threshold"),
            )),
            promotion_threshold=float(rule_3.get(
                "promote_threshold",
                rebalance_legacy.get("promotion_threshold"),
            )),
            decay_factor=float(rebalance_legacy["decay_factor"]),
        ),
        capabilities=dict(policy.capabilities),
    )


def _load_from_legacy(path: Path) -> RingConfig:
    """Load ``rings.yaml`` exactly as the pre-Phase-0 loader did."""
    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    rings = raw["budget"]["rings"]
    reb = raw["rebalance"]
    return RingConfig(
        total_budget=raw["budget"]["total"],
        core=RingBudget(**rings["core"]),
        inner=RingBudget(**rings["inner"]),
        middle=RingBudget(**rings["middle"]),
        outer=RingBudget(**rings["outer"]),
        ingest=IngestConfig(**raw["ingest"]),
        source_rules=raw.get("sources", {}),
        assembly_order=raw.get("assembly_order", []),
        rebalance=RebalanceConfig(
            tick_interval_ms=reb["tick_interval_ms"],
            age_guard_turns=reb["age_guard_turns"],
            demotion_threshold=reb["demotion_threshold"],
            promotion_threshold=reb["promotion_threshold"],
            decay_factor=reb["decay_factor"],
        ),
        capabilities=raw.get("capabilities", {}),
    )


def _load() -> RingConfig:
    if POLICY_PATH.exists():
        return _load_from_policy(POLICY_PATH)
    return _load_from_legacy(LEGACY_PATH)


# Singleton — loaded at import time. Engine restart required for config changes.
ring_config: RingConfig = _load()
