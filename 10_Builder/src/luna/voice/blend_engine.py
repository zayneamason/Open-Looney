"""
Voice Blend Engine — Confidence-weighted voice scaffolding.

Five-stage pipeline:
  1. ConfidenceRouter  — signals → alpha
  2. FadeController    — alpha adjustment from conversation history
  3. SegmentPlanner    — alpha → segment plan (front-loaded)
  4. LineSampler       — segment plan → scored line candidates
  5. BlendAssembler    — confidence + plan → VoiceSeed
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from luna.voice.models import (
    ConfidenceResult,
    ConfidenceSignals,
    ConfidenceTier,
    ContextType,
    LineBank,
    ResponseSegment,
    SegmentPlan,
    SegmentType,
    VoiceLine,
    VoiceSeed,
    VoiceSeedSource,
    VoiceSystemConfig,
)


class VoiceBlendEngine:
    """Confidence-weighted voice scaffolding engine.

    Computes how much help Luna needs per turn and fades as she
    finds her voice.
    """

    W_MEMORY = 0.35
    W_TURN = 0.25
    W_ENTITY = 0.15
    W_CONTEXT = 0.15
    W_CONTINUITY = 0.10

    CONTEXT_PENALTIES: dict[ContextType, float] = {
        ContextType.COLD_START: 0.9,
        ContextType.GREETING: 0.7,
        ContextType.TOPIC_SHIFT: 0.6,
        ContextType.CREATIVE: 0.5,
        ContextType.EMOTIONAL: 0.4,
        ContextType.TECHNICAL: 0.3,
        ContextType.MEMORY_RECALL: 0.2,
        ContextType.FOLLOW_UP: 0.1,
    }

    def __init__(self, config: VoiceSystemConfig):
        self.config = config
        self.bank: LineBank = self._load_bank(config.line_bank_path)
        self._alpha_history: list[float] = []
        self._turn_history: list[ContextType] = []

    def generate_seed(self, signals: ConfidenceSignals) -> VoiceSeed:
        """Full pipeline: signals -> alpha -> fade -> segments -> lines -> seed"""
        confidence = self._compute_confidence(signals)
        confidence = self._apply_fade(confidence, signals)
        plan = self._plan_segments(confidence)
        plan = self._sample_lines(plan, signals)
        seed = self._assemble_seed(confidence, plan)
        self._alpha_history.append(confidence.alpha)
        self._turn_history.append(signals.context_type)
        return seed

    def reset_conversation(self) -> None:
        """Reset fade state for new conversation."""
        self._alpha_history.clear()
        self._turn_history.clear()

    # ── Stage 1: ConfidenceRouter ──────────────────────────────

    def _compute_confidence(self, signals: ConfidenceSignals) -> ConfidenceResult:
        """Compute alpha from signals."""
        if self.config.bypass_confidence_router:
            alpha = self.config.alpha_override if self.config.alpha_override is not None else 0.5
            return ConfidenceResult(
                alpha=_clamp(alpha, 0.05, 0.95),
                tier=_alpha_to_tier(alpha),
                signals=signals,
                signal_contributions={},
            )

        memory_contrib = self.W_MEMORY * (1.0 - signals.memory_retrieval_score)
        turn_contrib = self.W_TURN * _turn_decay(signals.turn_number)
        entity_contrib = self.W_ENTITY * (1.0 - signals.entity_resolution_depth / 3.0)
        context_contrib = self.W_CONTEXT * self.CONTEXT_PENALTIES.get(
            signals.context_type, 0.5
        )
        continuity_contrib = self.W_CONTINUITY * (1.0 - signals.topic_continuity)

        raw_alpha = (
            memory_contrib
            + turn_contrib
            + entity_contrib
            + context_contrib
            + continuity_contrib
        )

        if self.config.alpha_override is not None:
            raw_alpha = self.config.alpha_override

        alpha = _clamp(raw_alpha, 0.05, 0.95)

        return ConfidenceResult(
            alpha=alpha,
            tier=_alpha_to_tier(alpha),
            signals=signals,
            signal_contributions={
                "memory": memory_contrib,
                "turn": turn_contrib,
                "entity": entity_contrib,
                "context": context_contrib,
                "continuity": continuity_contrib,
            },
        )

    # ── Stage 2: FadeController ────────────────────────────────

    def _apply_fade(
        self, confidence: ConfidenceResult, signals: ConfidenceSignals
    ) -> ConfidenceResult:
        """Adjust alpha based on conversation history."""
        if self.config.bypass_fade_controller:
            return confidence

        alpha = confidence.alpha
        adjustment = 0.0
        reason: Optional[str] = None

        # Context switch detected — reset (no fade)
        if (
            self._turn_history
            and signals.context_type != self._turn_history[-1]
            and signals.context_type == ContextType.TOPIC_SHIFT
        ):
            reason = "context switch detected -> reset alpha (no fade adjustment)"
            return ConfidenceResult(
                alpha=alpha,
                tier=_alpha_to_tier(alpha),
                signals=confidence.signals,
                signal_contributions=confidence.signal_contributions,
                fade_adjustment=0.0,
                fade_reason=reason,
            )

        # Emotional context — cap at 0.40
        if signals.context_type == ContextType.EMOTIONAL:
            if alpha > 0.40:
                adjustment = 0.40 - alpha
                alpha = 0.40
                reason = "emotional context -> cap alpha at 0.40"

        # Strong memory — drop by 0.3
        elif signals.memory_retrieval_score > 0.8:
            adjustment = -0.3
            alpha = _clamp(alpha + adjustment, 0.05, 0.95)
            reason = "strong memory -> drop alpha by 0.3"

        # Turn-based fade
        else:
            turn_count = len(self._alpha_history) + 1
            if turn_count >= 4:
                # Turn 4+: cap at 0.15
                if alpha > 0.15:
                    adjustment = 0.15 - alpha
                    alpha = 0.15
                    reason = "turn 4+ -> cap at 0.15"
            elif turn_count == 3:
                # Turn 3: subtract 0.2
                adjustment = -0.2
                alpha = _clamp(alpha + adjustment, 0.05, 0.95)
                reason = "turn 3 -> subtract 0.2"

        alpha = _clamp(alpha, 0.05, 0.95)

        return ConfidenceResult(
            alpha=alpha,
            tier=_alpha_to_tier(alpha),
            signals=confidence.signals,
            signal_contributions=confidence.signal_contributions,
            fade_adjustment=adjustment,
            fade_reason=reason,
        )

    # ── Stage 3: SegmentPlanner ────────────────────────────────

    def _plan_segments(self, confidence: ConfidenceResult) -> SegmentPlan:
        """Distribute alpha across response segments, front-loaded."""
        if self.config.bypass_segment_planner:
            return SegmentPlan(
                segments=[
                    ResponseSegment(
                        segment_type=SegmentType.OPENER,
                        alpha=confidence.alpha,
                        cost_budget=confidence.alpha * 5.0,
                    )
                ],
                total_alpha=confidence.alpha,
                expected_length="medium",
            )

        alpha = confidence.alpha

        # Low alpha — single opener only
        if alpha < 0.3:
            return SegmentPlan(
                segments=[
                    ResponseSegment(
                        segment_type=SegmentType.OPENER,
                        alpha=alpha,
                        cost_budget=alpha * 5.0,
                    )
                ],
                total_alpha=alpha,
                expected_length="short",
            )

        # Front-loaded distribution
        opener_alpha = min(alpha * 1.3, 0.95)
        body_alpha = alpha * 0.8
        closer_alpha = alpha * 0.5

        segments = [
            ResponseSegment(
                segment_type=SegmentType.OPENER,
                alpha=opener_alpha,
                cost_budget=opener_alpha * 5.0,
            ),
            ResponseSegment(
                segment_type=SegmentType.BRIDGE,
                alpha=body_alpha,
                cost_budget=body_alpha * 5.0,
            ),
            ResponseSegment(
                segment_type=SegmentType.CLOSER,
                alpha=closer_alpha,
                cost_budget=closer_alpha * 5.0,
            ),
        ]

        length = "long" if alpha > 0.6 else "medium"

        return SegmentPlan(
            segments=segments,
            total_alpha=alpha,
            expected_length=length,
        )

    # ── Stage 4: LineSampler ───────────────────────────────────

    def _sample_lines(
        self, plan: SegmentPlan, signals: ConfidenceSignals
    ) -> SegmentPlan:
        """Score and select line candidates for each segment."""
        if self.config.bypass_line_sampler:
            return plan

        for segment in plan.segments:
            candidates = self._filter_candidates(segment, signals)
            scored = self._score_candidates(candidates, segment)
            segment.selected_lines = scored[:3]

        return plan

    def _filter_candidates(
        self, segment: ResponseSegment, signals: ConfidenceSignals
    ) -> list[VoiceLine]:
        """Filter lines by tier, segment_type, and context tags."""
        tier = _alpha_to_tier(segment.alpha)

        # Start with tier match
        candidates = self.bank.by_tier(tier)

        # Prefer matching segment_type
        typed = [
            line for line in candidates if line.segment_type == segment.segment_type
        ]
        if typed:
            candidates = typed

        # Prefer matching context tags
        tagged = [
            line
            for line in candidates
            if signals.context_type.value in line.context_tags
        ]
        if tagged:
            candidates = tagged

        return candidates

    def _score_candidates(
        self, candidates: list[VoiceLine], segment: ResponseSegment
    ) -> list[VoiceLine]:
        """Score by cost alignment with segment budget."""
        if not candidates:
            return []

        def score(line: VoiceLine) -> float:
            return 1.0 - abs(line.cost - segment.cost_budget) / 5.0

        return sorted(candidates, key=score, reverse=True)

    # ── Stage 5: BlendAssembler ────────────────────────────────

    def _assemble_seed(
        self, confidence: ConfidenceResult, plan: SegmentPlan
    ) -> VoiceSeed:
        """Build VoiceSeed from confidence + plan."""
        # Collect selected lines across segments
        all_lines: list[VoiceLine] = []
        for segment in plan.segments:
            all_lines.extend(segment.selected_lines)

        opener = all_lines[0] if all_lines else None

        return VoiceSeed(
            source=VoiceSeedSource.ENGINE,
            alpha=confidence.alpha,
            tier=confidence.tier,
            opener_seed=opener.text if opener else None,
            opener_weight=confidence.alpha,
            tone_hints=list(
                {line.emotional_register.value for line in all_lines}
            ),
            example_lines=[line.text for line in all_lines[:3]],
            anti_patterns=[],  # Engine doesn't inject anti-patterns; Corpus owns those
            engine_active=True,
            corpus_active=False,
        )

    # ── Helpers ────────────────────────────────────────────────

    def _load_bank(self, path: str) -> LineBank:
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = Path(__file__).parent / path
        with open(resolved) as f:
            return LineBank.model_validate_json(f.read())


# ── Module-level helpers ───────────────────────────────────────


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _turn_decay(turn_number: int) -> float:
    """max(0, 1 - (turn-1) * 0.3) -> Turn 1=1.0, Turn 2=0.7, Turn 3=0.4, Turn 4+=0.1"""
    return max(0.0, 1.0 - (turn_number - 1) * 0.3)


def _alpha_to_tier(alpha: float) -> ConfidenceTier:
    if alpha > 0.6:
        return ConfidenceTier.GROUNDING
    if alpha > 0.3:
        return ConfidenceTier.ENGAGING
    return ConfidenceTier.FLOWING
