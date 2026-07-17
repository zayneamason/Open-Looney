"""Rule 3 — tier transitions (Spec v0.2 Section 7).

Phase 3 cutover: the heuristic ``_rebalance_rings`` previously held the
demote/promote logic and the one-move-per-tick guard inline. This module
relocates that policy into a typed entrypoint and adds the missing
``accessed_this_tick`` gate from clause 3.2 plus the fast-track promotion
path from clause 3.3.

Behavior preserved from Phase 2:

  - One-move-per-tick (clause 3.4): items in ``moved_item_ids`` are skipped
    by every later loop. Demote pass runs first, then promote, so a single
    item can never demote-then-promote within one rebalance call.
  - Demotion uses ``relevance < demote_threshold`` AND ``age_turns >=
    age_guard_turns`` — same gating shape as the legacy code.
  - Standard promotion is one ring up only.

New in Phase 3:

  - Promotion (clause 3.2) requires ``item.accessed_this_tick`` AND
    ``relevance >= promote_threshold``.
  - Fast-track promotion (clause 3.3) lifts an item from OUTER straight to
    INNER when ``tick_relevance_jump >= promotion_jump_min``. This is the
    only Spec-sanctioned violation of one-move-per-tick.

The ``Movement.score_at_decision`` records the composite score from the
configured engine for traceability, even though the gate uses relevance to
match the legacy YAML calibration.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List

from luna.policy.policy import Policy
from luna.policy.score import get_engine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Movement:
    """One ring transition record."""

    item_id: str
    from_ring: str
    to_ring: str
    reason: str  # "demote:rule_3_1" | "promote:rule_3_2" | "fast_track:rule_3_3"
    score_at_decision: float


def _ring_enum(name: str):
    from luna.core.context import ContextRing
    return ContextRing[name]


_DEMOTION_PAIRS = (("INNER", "MIDDLE"), ("MIDDLE", "OUTER"))
_PROMOTION_PAIRS = (("OUTER", "MIDDLE"), ("MIDDLE", "INNER"))


class Rule_3_Transitions:
    """Implements Rule 3 — tier transitions."""

    @staticmethod
    def rebalance(ctx: Any, policy: Policy) -> List[Movement]:
        params = policy.rule_3_transitions or {}
        demote_thr = float(params.get("demote_threshold", 0.30))
        promote_thr = float(params.get("promote_threshold", 0.80))
        jump_min = float(params.get("promotion_jump_min", 0.50))
        # age_guard_turns is owned by Rule 2 in policy.yaml; mirror it here so
        # demotion behavior matches Phase 2 exactly.
        age_guard = int((policy.rule_2_decay or {}).get("age_guard_turns", 2))

        engine = get_engine(policy.score.engine)
        moved_ids: set[str] = set()
        movements: List[Movement] = []

        # --- Demotion pass (INNER → MIDDLE → OUTER) ----------------------
        for src_name, dst_name in _DEMOTION_PAIRS:
            src = _ring_enum(src_name)
            dst = _ring_enum(dst_name)
            demote_indices: List[int] = []
            for idx, item in enumerate(ctx.rings[src]):
                if item.id in moved_ids:
                    continue
                if getattr(item, "permanent", False):
                    continue
                if item.age_turns < age_guard:
                    continue
                if item.relevance >= demote_thr:
                    continue
                demote_indices.append(idx)

            for idx in reversed(demote_indices):
                item = ctx.rings[src].pop(idx)
                score = float(engine.score(item, item.ring, policy.score))
                item.ring = dst
                ctx.rings[dst].append(item)
                moved_ids.add(item.id)
                mv = Movement(
                    item_id=item.id,
                    from_ring=src_name,
                    to_ring=dst_name,
                    reason="demote:rule_3_1",
                    score_at_decision=score,
                )
                movements.append(mv)
                logger.info(
                    "[RULE3] demote id=%s %s->%s score=%.4f rel=%.3f age=%d",
                    item.id, src_name, dst_name, score, item.relevance, item.age_turns,
                )

        # --- Fast-track promotion (OUTER → INNER) ------------------------
        # Spec 3.3 — strong reactivation skips MIDDLE. Runs before standard
        # promotion so a qualifying OUTER item is consumed once.
        outer = _ring_enum("OUTER")
        inner = _ring_enum("INNER")
        ft_indices: List[int] = []
        for idx, item in enumerate(ctx.rings[outer]):
            if item.id in moved_ids:
                continue
            if not item.accessed_this_tick:
                continue
            if item.relevance < promote_thr:
                continue
            if item.tick_relevance_jump < jump_min:
                continue
            ft_indices.append(idx)

        for idx in reversed(ft_indices):
            item = ctx.rings[outer].pop(idx)
            score = float(engine.score(item, item.ring, policy.score))
            item.ring = inner
            ctx.rings[inner].append(item)
            moved_ids.add(item.id)
            mv = Movement(
                item_id=item.id,
                from_ring="OUTER",
                to_ring="INNER",
                reason="fast_track:rule_3_3",
                score_at_decision=score,
            )
            movements.append(mv)
            logger.info(
                "[RULE3] fast_track id=%s OUTER->INNER score=%.4f jump=%.3f",
                item.id, score, item.tick_relevance_jump,
            )

        # --- Standard promotion (OUTER → MIDDLE → INNER) -----------------
        for src_name, dst_name in _PROMOTION_PAIRS:
            src = _ring_enum(src_name)
            dst = _ring_enum(dst_name)
            promote_indices: List[int] = []
            for idx, item in enumerate(ctx.rings[src]):
                if item.id in moved_ids:
                    continue
                if not item.accessed_this_tick:
                    continue
                if item.relevance < promote_thr:
                    continue
                promote_indices.append(idx)

            for idx in reversed(promote_indices):
                item = ctx.rings[src].pop(idx)
                score = float(engine.score(item, item.ring, policy.score))
                item.ring = dst
                ctx.rings[dst].append(item)
                moved_ids.add(item.id)
                mv = Movement(
                    item_id=item.id,
                    from_ring=src_name,
                    to_ring=dst_name,
                    reason="promote:rule_3_2",
                    score_at_decision=score,
                )
                movements.append(mv)
                logger.info(
                    "[RULE3] promote id=%s %s->%s score=%.4f rel=%.3f",
                    item.id, src_name, dst_name, score, item.relevance,
                )

        return movements
