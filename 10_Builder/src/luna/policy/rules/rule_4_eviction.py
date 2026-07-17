"""Rule 4 — score-driven eviction (Spec v0.2 Section 7).

Phase 2 cutover: replaces the heuristic ``_evict_one()`` previously in
``RevolvingContext``. CORE non-evictability is inherited from the score
engine's permanent floor (``+1e6``); per-ring ``min`` floors are honored
from ``policy.budget.rings[ring].min``.

Tie-break order is ``(score ASC, idle_turns DESC, item_id ASC)`` — lowest
score first; among equals the more idle item leaves first; ``id`` finalizes
determinism for property tests.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional

from luna.policy.policy import Policy
from luna.policy.score import get_engine

logger = logging.getLogger(__name__)

_EVICTABLE_RINGS = ("INNER", "MIDDLE", "OUTER")


@dataclass(frozen=True)
class Eviction:
    """One eviction record. Returned for telemetry and smoke verification."""

    item_id: str
    ring: str
    score: float
    reason: str
    tokens: int


def _ring_enum(ring_name: str):
    from luna.core.context import ContextRing  # local to avoid import cycle
    return ContextRing[ring_name]


def _ring_tokens(ctx: Any, ring_name: str) -> int:
    return sum(item.tokens for item in ctx.rings[_ring_enum(ring_name)])


def _pop_item(ctx: Any, item: Any, ring_name: str) -> None:
    ctx.rings[_ring_enum(ring_name)].remove(item)
    ctx._hash_index.pop(item.content_hash, None)
    ctx._total_evicted += 1


class Rule_4_Eviction:
    """Implements Rule 4 score-driven eviction."""

    @staticmethod
    def evict(ctx: Any, policy: Policy) -> List[Eviction]:
        """Drain low-scoring items until total tokens reach the pressure boundary.

        Halt conditions: ``total_tokens <= target_pressure * total`` OR no
        eligible candidate remains (every remaining ring at floor).
        """
        engine = get_engine(policy.score.engine)
        target = policy.budget.total * policy.budget.target_pressure

        candidates = []
        for ring_name in _EVICTABLE_RINGS:
            for item in ctx.rings[_ring_enum(ring_name)]:
                if getattr(item, "permanent", False):
                    continue
                s = engine.score(item, item.ring, policy.score)
                candidates.append((s, item, ring_name))

        candidates.sort(key=lambda t: (t[0], -t[1].idle_turns, t[1].id))

        ring_tokens = {r: _ring_tokens(ctx, r) for r in _EVICTABLE_RINGS}
        floors = {r: policy.budget.rings[r].min for r in _EVICTABLE_RINGS}

        out: List[Eviction] = []
        total = ctx._total_tokens()
        for score, item, ring_name in candidates:
            if total <= target:
                break
            if ring_tokens[ring_name] - item.tokens < floors[ring_name]:
                continue
            _pop_item(ctx, item, ring_name)
            ring_tokens[ring_name] -= item.tokens
            total -= item.tokens
            ev = Eviction(
                item_id=item.id,
                ring=ring_name,
                score=float(score),
                reason="pressure",
                tokens=item.tokens,
            )
            out.append(ev)
            logger.info(
                "[RULE4] evict ring=%s id=%s score=%.4f reason=pressure tokens=%d",
                ring_name, item.id, score, item.tokens,
            )

        return out

    @staticmethod
    def evict_one_in_ring(
        ctx: Any,
        policy: Policy,
        ring_name: str,
        reason: str,
    ) -> Optional[Eviction]:
        """Evict the lowest-scoring item from ``ring_name``, respecting ring.min.

        Used by ``Rule_2_Decay.enforce_budget`` for per-ring max-ceiling
        enforcement. Returns ``None`` if the ring is at its floor or has no
        evictable items.
        """
        if ring_name not in _EVICTABLE_RINGS:
            return None
        floor = policy.budget.rings[ring_name].min
        current = _ring_tokens(ctx, ring_name)
        engine = get_engine(policy.score.engine)

        candidates = [
            (engine.score(it, it.ring, policy.score), it)
            for it in ctx.rings[_ring_enum(ring_name)]
            if not getattr(it, "permanent", False)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda t: (t[0], -t[1].idle_turns, t[1].id))

        for score, item in candidates:
            if current - item.tokens < floor:
                continue
            _pop_item(ctx, item, ring_name)
            ev = Eviction(
                item_id=item.id,
                ring=ring_name,
                score=float(score),
                reason=reason,
                tokens=item.tokens,
            )
            logger.info(
                "[RULE4] evict ring=%s id=%s score=%.4f reason=%s tokens=%d",
                ring_name, item.id, score, reason, item.tokens,
            )
            return ev
        return None
