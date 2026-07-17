"""Policy loader for Memory Policy v1.0 (Phase 0 scaffolding).

Reads ``config/policy.yaml`` and materializes a frozen ``Policy`` dataclass
mirroring Spec v0.2 Section 9. Validation enforces the loader invariants
listed in PHASE0_Property_Test_Invariants.md Section B; any violation raises
``PolicyValidationError`` with the failing clause cited. No silent defaults.

In Phase 0 nothing in the runtime calls the rule-handler dicts (``rule_1...``
through ``rule_5...``) — they are passed through verbatim as dicts so Phases
1–4 can replace each one with a typed sub-dataclass without breaking the
loader contract. The legacy compat blocks (``ingest``, ``rebalance``,
``sources``, ``capabilities``) are also preserved so the ``ring_config``
façade can read them directly.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping
import yaml

from luna.policy.score import (
    LinearConstants,
    MultiplicativeConstants,
    ScoreConstants,
)


_VALID_RINGS = ("CORE", "INNER", "MIDDLE", "OUTER")
_VALID_ENGINES = ("linear", "multiplicative")
_LINEAR_KEYS = (
    "w_lock", "w_relev", "w_cite", "w_age", "cite_cap", "age_cap", "ring_bonus",
)
_MULTIPLICATIVE_KEYS = ("age_tau", "cite_factor", "eps", "ring_mult")


class PolicyValidationError(ValueError):
    """Raised when ``policy.yaml`` violates a loader invariant."""


@dataclass(frozen=True)
class RingBudget:
    """Per-ring budget allocation (Spec v0.2 §7 Rule 2.4)."""
    min: int
    max: int
    target_pct: float
    permanent: bool = False


@dataclass(frozen=True)
class Budget:
    """Tiered token budget across the four rings."""
    total: int
    target_pressure: float
    rings: Mapping[str, RingBudget]


@dataclass(frozen=True)
class Policy:
    """Materialized memory policy. Mirrors ``config/policy.yaml`` schema."""
    version: str
    effective: str
    owner: str
    profile: str
    rule_1_admission: Mapping[str, Any]
    rule_2_decay: Mapping[str, Any]
    budget: Budget
    rule_3_transitions: Mapping[str, Any]
    rule_4_eviction: Mapping[str, Any]
    rule_5_dedup: Mapping[str, Any]
    score: ScoreConstants
    assembly_order: List[Mapping[str, Any]] = field(default_factory=list)
    # Legacy compatibility blocks — consumed by the ring_config façade
    # until Phases 1–4 retire them.
    ingest: Mapping[str, Any] = field(default_factory=dict)
    rebalance: Mapping[str, Any] = field(default_factory=dict)
    sources: Mapping[str, Any] = field(default_factory=dict)
    capabilities: Mapping[str, Any] = field(default_factory=dict)


class PolicyLoader:
    """Loads and validates ``config/policy.yaml``."""

    @staticmethod
    def load_and_validate(path: Path) -> Policy:
        """Read YAML at ``path``, validate, return a frozen ``Policy``.

        Raises:
            FileNotFoundError: if ``path`` does not exist.
            PolicyValidationError: on any invariant violation.
        """
        with open(path, "r") as fh:
            raw = yaml.safe_load(fh) or {}

        if not isinstance(raw, dict):
            raise PolicyValidationError(
                f"policy.yaml root must be a mapping, got {type(raw).__name__}"
            )

        meta = raw.get("policy") or {}
        budget = PolicyLoader._build_budget(raw.get("budget"))
        score = PolicyLoader._build_score(raw.get("score"))
        rule_1 = raw.get("rule_1_admission") or {}
        rule_2 = raw.get("rule_2_decay") or {}
        rule_3 = raw.get("rule_3_transitions") or {}
        rule_4 = raw.get("rule_4_eviction") or {}
        rule_5 = raw.get("rule_5_dedup") or {}

        PolicyLoader._validate_lock_in_bounds(rule_1)
        PolicyLoader._validate_door_ring_refs(rule_1, budget)

        return Policy(
            version=str(meta.get("version", "")),
            effective=str(meta.get("effective", "")),
            owner=str(meta.get("owner", "")),
            profile=str(meta.get("profile", "")),
            rule_1_admission=rule_1,
            rule_2_decay=rule_2,
            budget=budget,
            rule_3_transitions=rule_3,
            rule_4_eviction=rule_4,
            rule_5_dedup=rule_5,
            score=score,
            assembly_order=list(raw.get("assembly_order") or []),
            ingest=raw.get("ingest") or {},
            rebalance=raw.get("rebalance") or {},
            sources=raw.get("sources") or {},
            capabilities=raw.get("capabilities") or {},
        )

    # --- builders ---------------------------------------------------------

    @staticmethod
    def _build_budget(raw_budget: Any) -> Budget:
        if not isinstance(raw_budget, dict):
            raise PolicyValidationError("budget block missing or not a mapping")

        total = raw_budget.get("total")
        if not isinstance(total, int) or total <= 0:
            raise PolicyValidationError(
                f"budget.total must be a positive int, got {total!r}"
            )

        target_pressure = raw_budget.get("target_pressure")
        if not isinstance(target_pressure, (int, float)) or not (0 < target_pressure < 1):
            raise PolicyValidationError(
                "budget.target_pressure must be in open interval (0, 1), "
                f"got {target_pressure!r}"
            )

        raw_rings = raw_budget.get("rings") or {}
        if not isinstance(raw_rings, dict):
            raise PolicyValidationError("budget.rings must be a mapping")

        rings: Dict[str, RingBudget] = {}
        sum_min = 0
        sum_max = 0
        for ring_name in _VALID_RINGS:
            ring_cfg = raw_rings.get(ring_name)
            if not isinstance(ring_cfg, dict):
                raise PolicyValidationError(
                    f"budget.rings.{ring_name} missing or not a mapping"
                )
            for required in ("min", "max", "target_pct"):
                if required not in ring_cfg:
                    raise PolicyValidationError(
                        f"budget.rings.{ring_name}.{required} missing"
                    )
            rings[ring_name] = RingBudget(
                min=int(ring_cfg["min"]),
                max=int(ring_cfg["max"]),
                target_pct=float(ring_cfg["target_pct"]),
                permanent=bool(ring_cfg.get("permanent", False)),
            )
            sum_min += rings[ring_name].min
            sum_max += rings[ring_name].max

        if not (sum_min <= total <= sum_max):
            raise PolicyValidationError(
                f"budget bounds violated: sum(min)={sum_min}, total={total}, "
                f"sum(max)={sum_max} (require sum(min) <= total <= sum(max))"
            )

        return Budget(total=total, target_pressure=float(target_pressure), rings=rings)

    @staticmethod
    def _build_score(raw_score: Any) -> ScoreConstants:
        if not isinstance(raw_score, dict):
            raise PolicyValidationError("score block missing or not a mapping")

        engine = raw_score.get("engine")
        if engine not in _VALID_ENGINES:
            raise PolicyValidationError(
                f"score.engine must be one of {_VALID_ENGINES}, got {engine!r}"
            )

        version = str(raw_score.get("version", ""))
        constants = raw_score.get("constants") or {}
        if not isinstance(constants, dict):
            raise PolicyValidationError("score.constants must be a mapping")

        linear_raw = constants.get("linear")
        if not isinstance(linear_raw, dict):
            raise PolicyValidationError("score.constants.linear missing or not a mapping")
        for key in _LINEAR_KEYS:
            if key not in linear_raw:
                raise PolicyValidationError(
                    f"score.constants.linear.{key} missing"
                )
        linear = LinearConstants(
            w_lock=float(linear_raw["w_lock"]),
            w_relev=float(linear_raw["w_relev"]),
            w_cite=float(linear_raw["w_cite"]),
            w_age=float(linear_raw["w_age"]),
            cite_cap=int(linear_raw["cite_cap"]),
            age_cap=int(linear_raw["age_cap"]),
            ring_bonus=PolicyLoader._coerce_ring_map(
                linear_raw["ring_bonus"], "score.constants.linear.ring_bonus"
            ),
        )

        mult_raw = constants.get("multiplicative")
        if not isinstance(mult_raw, dict):
            raise PolicyValidationError(
                "score.constants.multiplicative missing or not a mapping"
            )
        for key in _MULTIPLICATIVE_KEYS:
            if key not in mult_raw:
                raise PolicyValidationError(
                    f"score.constants.multiplicative.{key} missing"
                )
        multiplicative = MultiplicativeConstants(
            age_tau=float(mult_raw["age_tau"]),
            cite_factor=float(mult_raw["cite_factor"]),
            eps=float(mult_raw["eps"]),
            ring_mult=PolicyLoader._coerce_ring_map(
                mult_raw["ring_mult"], "score.constants.multiplicative.ring_mult"
            ),
        )

        return ScoreConstants(
            engine=str(engine),
            version=version,
            linear=linear,
            multiplicative=multiplicative,
        )

    @staticmethod
    def _coerce_ring_map(raw: Any, where: str) -> Dict[str, float]:
        if not isinstance(raw, dict):
            raise PolicyValidationError(f"{where} must be a mapping of ring -> float")
        out: Dict[str, float] = {}
        for ring in _VALID_RINGS:
            if ring not in raw:
                raise PolicyValidationError(f"{where}.{ring} missing")
            out[ring] = float(raw[ring])
        return out

    # --- standalone invariant checks --------------------------------------

    @staticmethod
    def _validate_lock_in_bounds(rule_1: Mapping[str, Any]) -> None:
        doors = rule_1.get("doors") or {}
        if not isinstance(doors, dict):
            raise PolicyValidationError("rule_1_admission.doors must be a mapping")
        for door_name, door_cfg in doors.items():
            if not isinstance(door_cfg, dict):
                continue
            for table_key in ("lock_in_by_kind",):
                table = door_cfg.get(table_key) or {}
                if not isinstance(table, dict):
                    raise PolicyValidationError(
                        f"rule_1_admission.doors.{door_name}.{table_key} must be a mapping"
                    )
                for kind, value in table.items():
                    if not isinstance(value, (int, float)) or not (0.0 <= value <= 1.0):
                        raise PolicyValidationError(
                            f"rule_1_admission.doors.{door_name}.{table_key}.{kind} "
                            f"= {value!r} out of [0, 1]"
                        )
            gate = door_cfg.get("relevance_gate")
            if gate is not None:
                if not isinstance(gate, (int, float)) or not (0.0 <= gate <= 1.0):
                    raise PolicyValidationError(
                        f"rule_1_admission.doors.{door_name}.relevance_gate "
                        f"= {gate!r} out of [0, 1]"
                    )

    @staticmethod
    def _validate_door_ring_refs(rule_1: Mapping[str, Any], budget: Budget) -> None:
        valid_rings = set(budget.rings.keys())
        doors = rule_1.get("doors") or {}
        for door_name, door_cfg in doors.items():
            if not isinstance(door_cfg, dict):
                continue
            default_ring = door_cfg.get("default_ring")
            if default_ring is not None and default_ring not in valid_rings:
                raise PolicyValidationError(
                    f"rule_1_admission.doors.{door_name}.default_ring "
                    f"= {default_ring!r} not declared in budget.rings"
                )
            ring_by_kind = door_cfg.get("default_ring_by_kind") or {}
            if not isinstance(ring_by_kind, dict):
                raise PolicyValidationError(
                    f"rule_1_admission.doors.{door_name}.default_ring_by_kind "
                    f"must be a mapping"
                )
            for kind, ring in ring_by_kind.items():
                if ring not in valid_rings:
                    raise PolicyValidationError(
                        f"rule_1_admission.doors.{door_name}.default_ring_by_kind"
                        f".{kind} = {ring!r} not declared in budget.rings"
                    )
