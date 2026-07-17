"""Rule 5 — deduplication (Spec v0.2 Section 7, clauses 5.1-5.5).

Phase 4 cutover: lifts the inline hash dedup that previously lived in
``RevolvingContext._ingest_item`` into a typed policy module and adds the
cosine-second pass behind ``rule_5_dedup.cosine_pass_enabled``.

Two-pass ordering (clause 5.1):

  Pass A — exact hash. Always runs when ``exact_hash_first: true``. O(1)
  lookup against ``ctx._hash_index``. The current shipping config sets
  this to true.

  Pass B — cosine similarity. Only runs when ``cosine_pass_enabled:
  true``. Bounded per-tick by ``merge_throttle_per_tick`` (counter lives
  on ``RevolvingContext._cosine_calls_this_tick`` and resets in
  ``_reset_tick_state``). Delegates the actual similarity search to a
  pluggable hook ``ctx._cosine_lookup`` so this module stays sync; the
  embedding pipeline wiring is the prerequisite ``HANDOFF_Cosine_Merge_
  Embedding_Pipeline_Verification`` handoff's responsibility.

  When the flag is on but no hook is wired we raise loudly rather than
  silently falling back to the new-item path — per
  ``feedback_no_silent_degradation``: a downstream miss on an upstream
  committed decision (cosine flag) must not silently degrade.

Merge semantics (clause 5.2-5.5, contract requirement 4):

  Older-wins: the ring-resident is the target; the incoming item is
  discarded after its fields fold into the target. The target keeps its
  id, ring, and history.

    target.cite_count        += incoming.cite_count
    target.relevance          = max(target.relevance, incoming.relevance)
    target.lock_in            = max(target.lock_in,   incoming.lock_in)
    target.last_accessed_turn = current_turn
    target.door               = target.door or incoming.door

  ``lock_in`` aggregation is new in Phase 4. The legacy inline dedup
  only updated ``cite_count``, ``relevance``, and ``door`` — ``lock_in``
  was silently dropped, which violated contract requirement 4.

  Variants accumulation: only when ``incoming.content_hash !=
  target.content_hash`` (the cosine-merge case). The incoming hash is
  appended to ``target.variants`` once, never duplicated.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from luna.policy.policy import Policy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DedupDecision:
    """One dedup decision record. Returned for telemetry / smoke verification.

    ``outcome`` is one of:
      - ``hash_hit``    — Pass A matched; target shares content_hash.
      - ``cosine_hit``  — Pass B matched; target is semantically similar.
      - ``throttled``   — Pass B skipped due to per-tick budget.
      - ``new_item``    — neither pass matched; caller proceeds to Rule 1.
    """

    outcome: str
    incoming_id: str
    target_id: Optional[str]
    similarity: Optional[float] = None


class Rule_5_Dedup:
    """Implements Rule 5 — deduplication (hash-first, cosine-second)."""

    @staticmethod
    def find_merge_target(item: Any, ctx: Any, policy: Policy) -> Optional[Any]:
        """Return an existing item to merge into, or None for a new item.

        Pass A (hash) runs unconditionally when ``exact_hash_first`` is
        true. Pass B (cosine) only runs when ``cosine_pass_enabled`` is
        true AND the per-tick throttle has budget remaining AND a cosine
        lookup hook is wired.
        """
        params = policy.rule_5_dedup or {}

        # Pass A — exact hash (clause 5.1, mandatory ordering)
        if params.get("exact_hash_first", True):
            target = ctx._hash_index.get(item.content_hash)
            if target is not None:
                return target

        # Pass B — cosine (clause 5.2, behind feature flag)
        if not params.get("cosine_pass_enabled", False):
            return None

        # Per-tick throttle (contract requirement 2)
        budget = int(params.get("merge_throttle_per_tick", 8))
        used = getattr(ctx, "_cosine_calls_this_tick", 0)
        if used >= budget:
            logger.debug(
                "[RULE5] cosine throttled: used=%d budget=%d incoming=%s",
                used, budget, item.id,
            )
            return None

        # Loud raise when flag on but no hook wired
        # (feedback_no_silent_degradation)
        hook = getattr(ctx, "_cosine_lookup", None)
        if hook is None:
            raise RuntimeError(
                "rule_5_dedup.cosine_pass_enabled=True but "
                "ctx._cosine_lookup is None; wire an embedding-backed "
                "lookup before flipping the flag"
            )

        ctx._cosine_calls_this_tick = used + 1
        threshold = float(params.get("cosine_threshold", 0.85))
        match = hook(item, ctx, threshold)
        if match is None:
            return None
        target, _similarity = match
        return target

    @staticmethod
    def merge(
        incoming: Any,
        target: Any,
        policy: Policy,
        *,
        current_turn: int,
    ) -> Any:
        """Fold ``incoming`` into ``target`` (older-wins) and return target.

        Mutates ``target`` in place. ``incoming`` is not modified — the
        caller discards it.
        """
        target.cite_count += incoming.cite_count
        if incoming.relevance > target.relevance:
            target.relevance = incoming.relevance
        if incoming.lock_in > target.lock_in:
            target.lock_in = incoming.lock_in
        target.last_accessed_turn = current_turn
        if target.door is None and incoming.door is not None:
            target.door = incoming.door

        if incoming.content_hash != target.content_hash:
            variants = getattr(target, "variants", None)
            if variants is not None and incoming.content_hash not in variants:
                variants.append(incoming.content_hash)

        return target
