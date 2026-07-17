"""Score engines for the memory policy layer.

Implements Spec v0.2 Section 8: a pure-function score primitive that every
rule references. Two strategies ship; YAML picks one. Both implementations
must satisfy the invariants listed in PHASE0_Property_Test_Invariants.md
Section A:

  1. Monotonicity in lock_in
  2. Monotonicity in relevance
  3. Ring ordering at parity (CORE > INNER > MIDDLE > OUTER)
  4. Permanent floor (a permanent CORE item outranks any non-permanent item)
  5. Determinism / purity

Phase 0 does not wire these into runtime — Phases 2 and 4 do. The module
exists so Phase 0 tests can validate the invariants statically.

Import discipline: this module is imported by ``luna.config.ring_config``
(via the policy loader) which itself is imported by ``luna.core.context``.
To avoid a cycle, ``ContextItem`` and ``ContextRing`` are referenced only
under ``TYPE_CHECKING``; runtime accepts any object with the required
duck-typed attributes (``lock_in``, ``relevance``, ``cite_count``,
``idle_turns``, ``permanent``) and any ring with a ``.name`` attribute.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import exp, log1p
from typing import TYPE_CHECKING, Any, Dict, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from luna.core.context import ContextItem, ContextRing


# Permanent items must dominate every non-permanent score regardless of
# the other axes. Both engines apply the bonus additively after the base
# computation so the floor invariant is satisfied at the worst case.
_PERMANENT_FLOOR_BONUS: float = 1e6


@dataclass(frozen=True)
class LinearConstants:
    """Constants for the weighted-linear scorer (Spec v0.2 §8 Strategy A).

    ``ring_bonus`` is keyed by ring NAME (``"CORE"``, ``"INNER"`` ...) to
    keep this module free of ``ContextRing`` runtime imports.
    """
    w_lock: float
    w_relev: float
    w_cite: float
    w_age: float
    cite_cap: int
    age_cap: int
    ring_bonus: Dict[str, float]


@dataclass(frozen=True)
class MultiplicativeConstants:
    """Constants for the multiplicative scorer (Spec v0.2 §8 Strategy B).

    ``ring_mult`` is keyed by ring NAME for the same reason as ``ring_bonus``.
    """
    age_tau: float
    cite_factor: float
    eps: float
    ring_mult: Dict[str, float]


@dataclass(frozen=True)
class ScoreConstants:
    """Composite score config — both engine constants live here.

    The selected engine name in ``Policy.score.engine`` decides which
    sub-block is read at runtime; both must validate at load time.
    """
    engine: str
    version: str
    linear: LinearConstants
    multiplicative: MultiplicativeConstants


@runtime_checkable
class ScoreEngine(Protocol):
    """Pure scoring function. Higher = more valuable."""

    name: str
    version: str

    def score(
        self,
        item: Any,
        ring: Any,
        constants: ScoreConstants,
    ) -> float: ...


def _ring_key(ring: Any) -> str:
    """Extract a string key from a ``ContextRing`` enum or a bare string."""
    name = getattr(ring, "name", None)
    if isinstance(name, str):
        return name
    return str(ring)


def _normalize(value: float, cap: float) -> float:
    """Map [0, cap] -> [0, 1]; clip above cap. Returns 0 if cap <= 0."""
    if cap <= 0:
        return 0.0
    return min(float(value), float(cap)) / float(cap)


@dataclass(frozen=True)
class LinearScorer:
    """Strategy A — weighted linear. Each axis additive; tunable per-axis."""

    name: str = "linear"
    version: str = "1.0"

    def score(
        self,
        item: Any,
        ring: Any,
        constants: ScoreConstants,
    ) -> float:
        k = constants.linear
        ring_bonus = k.ring_bonus[_ring_key(ring)]
        base = (
            k.w_lock * float(item.lock_in)
            + k.w_relev * float(item.relevance)
            + k.w_cite * _normalize(item.cite_count, k.cite_cap)
            - k.w_age * _normalize(item.idle_turns, k.age_cap)
            + ring_bonus
        )
        if getattr(item, "permanent", False):
            return base + _PERMANENT_FLOOR_BONUS
        return base


@dataclass(frozen=True)
class MultiplicativeScorer:
    """Strategy B — multiplicative. Interaction-aware; zero on any axis tanks score."""

    name: str = "multiplicative"
    version: str = "1.0"

    def score(
        self,
        item: Any,
        ring: Any,
        constants: ScoreConstants,
    ) -> float:
        k = constants.multiplicative
        base = (k.eps + float(item.lock_in)) * (k.eps + float(item.relevance))
        age_decay = exp(-float(item.idle_turns) / k.age_tau) if k.age_tau > 0 else 1.0
        cite_boost = 1.0 + log1p(float(item.cite_count)) * k.cite_factor
        ring_mult = k.ring_mult[_ring_key(ring)]
        score_value = base * age_decay * cite_boost * ring_mult
        if getattr(item, "permanent", False):
            return score_value + _PERMANENT_FLOOR_BONUS
        return score_value


def get_engine(name: str) -> ScoreEngine:
    """Factory — instantiate the selected engine by canonical name."""
    if name == "linear":
        return LinearScorer()
    if name == "multiplicative":
        return MultiplicativeScorer()
    raise ValueError(f"unknown score engine: {name!r}")
