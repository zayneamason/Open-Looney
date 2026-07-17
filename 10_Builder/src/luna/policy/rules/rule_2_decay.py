"""Rule 2 — decay and tiered budget enforcement (Spec v0.2 Section 7).

Phase 2 cutover. Implements:

  - Rule 2.1 universal decay (``decay_floor``)
  - Rule 2.2 lock_in protection (``lock_in_protection``)
  - Rule 2.3 permanent kinds + age guard (``permanent_kinds``, ``age_guard_turns``)
  - Rule 2.4 tiered per-ring budget (``policy.budget.rings[*].max``) and global
    pressure-driven eviction (delegates to ``Rule_4_Eviction``)

The previous ``RevolvingContext.decay_all`` and ``_enforce_budget`` heuristics
are removed; this module is the only runtime budget path when a policy is
loaded.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List

from luna.core.types import ItemKind
from luna.policy.policy import Policy
from luna.policy.rules.rule_4_eviction import (
    _EVICTABLE_RINGS,
    Eviction,
    Rule_4_Eviction,
    _pop_item,
    _ring_enum,
    _ring_tokens,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecayResult:
    """Summary of a single ``tick_decay`` pass."""

    decayed: int
    permanent_skipped: int
    age_guarded: int


class Rule_2_Decay:
    """Implements Rule 2 — decay clauses 2.1–2.3 and budget clause 2.4."""

    @staticmethod
    def tick_decay(ctx: Any, policy: Policy) -> DecayResult:
        params = policy.rule_2_decay or {}
        decay_floor = float(params.get("decay_floor", 0.85))
        lock_in_protection = float(params.get("lock_in_protection", 0.13))
        age_guard = int(params.get("age_guard_turns", 2))

        permanent_kinds = set()
        for name in params.get("permanent_kinds") or []:
            try:
                permanent_kinds.add(ItemKind[name])
            except KeyError:
                logger.warning("[RULE2] unknown permanent_kind=%s in policy.yaml", name)

        decayed = 0
        permanent_skipped = 0
        age_guarded = 0

        for ring_name in _EVICTABLE_RINGS:
            for item in ctx.rings[_ring_enum(ring_name)]:
                if getattr(item, "permanent", False) or item.kind in permanent_kinds:
                    permanent_skipped += 1
                    continue
                if item.age_turns < age_guard:
                    age_guarded += 1
                    continue
                factor = decay_floor + float(item.lock_in) * lock_in_protection
                item.relevance = max(0.0, item.relevance * factor)
                decayed += 1

        result = DecayResult(
            decayed=decayed,
            permanent_skipped=permanent_skipped,
            age_guarded=age_guarded,
        )
        logger.info(
            "[RULE2] decay decayed=%d permanent_skipped=%d age_guarded=%d",
            decayed, permanent_skipped, age_guarded,
        )
        return result

    @staticmethod
    def enforce_budget(ctx: Any, policy: Policy) -> List[Eviction]:
        """Enforce per-ring ceilings, expire TTL items, then drain to pressure."""
        evictions: List[Eviction] = []

        # Pass A — per-ring max ceiling (Rule 2.4 clause 2)
        for ring_name in _EVICTABLE_RINGS:
            ring_max = policy.budget.rings[ring_name].max
            while _ring_tokens(ctx, ring_name) > ring_max:
                ev = Rule_4_Eviction.evict_one_in_ring(
                    ctx, policy, ring_name, f"ceiling:{ring_name}"
                )
                if ev is None:
                    break
                evictions.append(ev)

        # Pass B — TTL expiry sweep (preserves legacy lifetime semantics)
        for ring_name in _EVICTABLE_RINGS:
            for item in list(ctx.rings[_ring_enum(ring_name)]):
                if not item.is_expired:
                    continue
                if getattr(item, "permanent", False):
                    continue
                _pop_item(ctx, item, ring_name)
                ev = Eviction(
                    item_id=item.id,
                    ring=ring_name,
                    score=0.0,
                    reason="expired",
                    tokens=item.tokens,
                )
                evictions.append(ev)
                logger.info(
                    "[RULE4] evict ring=%s id=%s score=%.4f reason=expired tokens=%d",
                    ring_name, item.id, 0.0, item.tokens,
                )

        # Pass C — global target_pressure (Rule 4)
        target = policy.budget.total * policy.budget.target_pressure
        if ctx._total_tokens() > target:
            evictions.extend(Rule_4_Eviction.evict(ctx, policy))

        return evictions
