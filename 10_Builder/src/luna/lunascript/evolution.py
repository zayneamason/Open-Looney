"""LunaScript evolution — running stats, trait-outcome correlations, epsilon-greedy iteration.

Phase 3: Learn & Evolve. Tracks how traits drift per delegation, correlates
trait deltas with quality outcomes, and iterates trait weights over time.

Mathematical foundation: exponential-decay running statistics (inspired by
dlib's running_stats_decayed / running_scalar_covariance_decayed).
"""

import json
import math
import random
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from .config import LunaScriptConfig
from .signature import DelegationSignature, DeltaResult, DEFAULT_TRAIT_WEIGHTS

logger = logging.getLogger(__name__)


class RunningStatsDecayed:
    """Exponential-decay running statistics (mean, variance, stddev).

    After `decay_halflife` observations, old data contributes 50% weight.
    Equivalent to dlib's running_stats_decayed in Python.
    """

    def __init__(self, decay_halflife: float = 100.0):
        self._decay = 2.0 ** (-1.0 / max(decay_halflife, 1.0))
        self._w = 0.0       # sum of weights
        self._m = 0.0       # weighted mean
        self._s = 0.0       # weighted sum of squared deviations
        self._n = 0         # observation count

    def add(self, x: float) -> None:
        self._n += 1
        self._w = self._decay * self._w + 1.0
        old_m = self._m
        self._m = old_m + (x - old_m) / self._w
        self._s = self._decay * self._s + (x - old_m) * (x - self._m)

    @property
    def mean(self) -> float:
        return self._m if self._n > 0 else 0.0

    @property
    def variance(self) -> float:
        if self._w <= 1.0:
            return 0.0
        return self._s / (self._w - 1.0)

    @property
    def stddev(self) -> float:
        return math.sqrt(max(self.variance, 0.0))

    @property
    def n(self) -> int:
        return self._n

    def to_dict(self) -> dict:
        return {
            "decay": self._decay, "w": self._w, "m": self._m,
            "s": self._s, "n": self._n,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RunningStatsDecayed":
        obj = cls.__new__(cls)
        obj._decay = d.get("decay", 0.9931)
        obj._w = d.get("w", 0.0)
        obj._m = d.get("m", 0.0)
        obj._s = d.get("s", 0.0)
        obj._n = d.get("n", 0)
        return obj


class RunningCovarianceDecayed:
    """Exponential-decay running covariance / correlation between two variables.

    Tracks how a trait's delta correlates with delegation quality score.
    """

    def __init__(self, decay_halflife: float = 100.0):
        self._decay = 2.0 ** (-1.0 / max(decay_halflife, 1.0))
        self._w = 0.0
        self._mx = 0.0
        self._my = 0.0
        self._sx = 0.0
        self._sy = 0.0
        self._sxy = 0.0
        self._n = 0

    def add(self, x: float, y: float) -> None:
        self._n += 1
        self._w = self._decay * self._w + 1.0

        old_mx = self._mx
        old_my = self._my
        self._mx = old_mx + (x - old_mx) / self._w
        self._my = old_my + (y - old_my) / self._w

        self._sx = self._decay * self._sx + (x - old_mx) * (x - self._mx)
        self._sy = self._decay * self._sy + (y - old_my) * (y - self._my)
        self._sxy = self._decay * self._sxy + (x - old_mx) * (y - self._my)

    @property
    def correlation(self) -> float:
        if self._n < 3:
            return 0.0
        denom = math.sqrt(max(self._sx, 1e-12) * max(self._sy, 1e-12))
        if denom < 1e-12:
            return 0.0
        return max(-1.0, min(1.0, self._sxy / denom))

    @property
    def covariance(self) -> float:
        if self._w <= 1.0:
            return 0.0
        return self._sxy / (self._w - 1.0)

    @property
    def n(self) -> int:
        return self._n

    def to_dict(self) -> dict:
        return {
            "decay": self._decay, "w": self._w,
            "mx": self._mx, "my": self._my,
            "sx": self._sx, "sy": self._sy, "sxy": self._sxy,
            "n": self._n,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RunningCovarianceDecayed":
        obj = cls.__new__(cls)
        obj._decay = d.get("decay", 0.9931)
        obj._w = d.get("w", 0.0)
        obj._mx = d.get("mx", 0.0)
        obj._my = d.get("my", 0.0)
        obj._sx = d.get("sx", 0.0)
        obj._sy = d.get("sy", 0.0)
        obj._sxy = d.get("sxy", 0.0)
        obj._n = d.get("n", 0)
        return obj


class TraitEvolution:
    """Manages per-trait running stats and trait-outcome correlations.

    Records each delegation's trait deltas and quality scores, tracks
    correlations between trait drift and delegation success, and uses
    epsilon-greedy exploration to iterate trait weights.
    """

    TRAIT_NAMES = [
        "warmth", "curiosity", "directness", "energy",
        "formality", "humor", "depth", "patience",
    ]

    def __init__(self, config: LunaScriptConfig):
        self.config = config
        self.epsilon = config.epsilon
        self._iteration = 0

        # Per-trait drift running stats (tracks how much each trait drifts)
        self._drift_stats: dict[str, RunningStatsDecayed] = {
            t: RunningStatsDecayed(config.decay_halflife)
            for t in self.TRAIT_NAMES
        }

        # Overall drift running stats (for adaptive classification thresholds)
        self._overall_drift = RunningStatsDecayed(config.decay_halflife)

        # Trait-quality correlations: does drifting on this trait predict bad quality?
        self._trait_quality_corr: dict[str, RunningCovarianceDecayed] = {
            t: RunningCovarianceDecayed(config.decay_halflife)
            for t in self.TRAIT_NAMES
        }

    def record_delegation(
        self,
        outbound: DelegationSignature,
        delta: DeltaResult,
        classification: str,
        quality_score: float,
    ) -> None:
        """Record a delegation round-trip for learning."""
        self._iteration += 1

        # Update overall drift stats
        self._overall_drift.add(delta.drift_score)

        # Update per-trait drift stats and quality correlations
        for trait_name in self.TRAIT_NAMES:
            trait_delta = abs(delta.delta_vector.get(trait_name, 0.0))
            self._drift_stats[trait_name].add(trait_delta)

            # Correlate: does drifting on this trait predict low quality?
            # x = abs(trait delta), y = quality_score
            # Negative correlation = drift hurts quality → weight this trait higher
            self._trait_quality_corr[trait_name].add(trait_delta, quality_score)

        # Decay epsilon
        self.epsilon = max(
            self.config.epsilon_floor,
            self.epsilon * self.config.epsilon_decay,
        )

    def iterate_weights(
        self, current_weights: dict[str, float]
    ) -> dict[str, float]:
        """Epsilon-greedy weight iteration.

        With probability (1 - epsilon): exploit — adjust weights based on correlations.
        With probability epsilon: explore — randomly perturb one weight.
        """
        if self._iteration < 10:
            return dict(current_weights)

        new_weights = dict(current_weights)

        if random.random() < self.epsilon:
            # Explore: randomly bump one trait weight
            trait = random.choice(self.TRAIT_NAMES)
            perturbation = random.gauss(0, 0.3)
            new_weights[trait] = max(0.5, min(5.0, new_weights.get(trait, 1.0) + perturbation))
            logger.debug(f"[EVOLUTION] Explore: {trait} weight {perturbation:+.2f}")
        else:
            # Exploit: adjust weights based on trait-quality correlations
            for trait_name in self.TRAIT_NAMES:
                corr = self._trait_quality_corr[trait_name].correlation
                if self._trait_quality_corr[trait_name].n < 5:
                    continue

                # Negative correlation = drift hurts quality → increase weight
                # Positive correlation = drift helps quality → decrease weight
                adjustment = -corr * 0.1
                old_w = new_weights.get(trait_name, 1.0)
                new_weights[trait_name] = max(0.5, min(5.0, old_w + adjustment))

                if abs(adjustment) > 0.01:
                    logger.debug(
                        f"[EVOLUTION] Exploit: {trait_name} corr={corr:.3f} "
                        f"weight {old_w:.2f}→{new_weights[trait_name]:.2f}"
                    )

        return new_weights

    def get_adaptive_thresholds(self) -> tuple[float, float]:
        """Return (mean, stddev) for drift classification from running stats.

        Replaces hardcoded 0.15/0.08 after enough observations.
        """
        if self._overall_drift.n < 20:
            return 0.15, 0.08
        return self._overall_drift.mean, max(self._overall_drift.stddev, 0.02)

    def get_trait_trends(self) -> dict[str, float]:
        """Per-trait trend: positive = trait is drifting more over time."""
        trends = {}
        for trait_name in self.TRAIT_NAMES:
            stats = self._drift_stats[trait_name]
            if stats.n < 5:
                trends[trait_name] = 0.0
            else:
                trends[trait_name] = stats.mean
        return trends

    def get_trait_correlations(self) -> dict[str, float]:
        """Per-trait correlation with quality (for diagnostics)."""
        return {
            t: self._trait_quality_corr[t].correlation
            for t in self.TRAIT_NAMES
        }

    async def save_state(self, db) -> None:
        """Persist evolution state to lunascript_correlations table."""
        now = time.time()
        for trait_name in self.TRAIT_NAMES:
            state = {
                "drift_stats": self._drift_stats[trait_name].to_dict(),
                "quality_corr": self._trait_quality_corr[trait_name].to_dict(),
            }
            try:
                await db.execute(
                    "INSERT OR REPLACE INTO lunascript_correlations "
                    "(trait_name, task_type, correlation, n_observations, "
                    "serialized_state, last_updated) "
                    "VALUES (?, 'all', ?, ?, ?, ?)",
                    (
                        trait_name,
                        self._trait_quality_corr[trait_name].correlation,
                        self._trait_quality_corr[trait_name].n,
                        json.dumps(state),
                        now,
                    ),
                )
            except Exception as e:
                logger.debug(f"[EVOLUTION] Save failed for {trait_name}: {e}")

        # Also save overall drift stats and epsilon
        try:
            await db.execute(
                "INSERT OR REPLACE INTO lunascript_correlations "
                "(trait_name, task_type, correlation, n_observations, "
                "serialized_state, last_updated) "
                "VALUES ('_overall', 'meta', 0, ?, ?, ?)",
                (
                    self._overall_drift.n,
                    json.dumps({
                        "overall_drift": self._overall_drift.to_dict(),
                        "epsilon": self.epsilon,
                        "iteration": self._iteration,
                    }),
                    now,
                ),
            )
        except Exception as e:
            logger.debug(f"[EVOLUTION] Save failed for _overall: {e}")

    async def load_state(self, db) -> bool:
        """Restore evolution state from lunascript_correlations table."""
        try:
            rows = await db.fetchall(
                "SELECT trait_name, task_type, serialized_state "
                "FROM lunascript_correlations"
            )
        except Exception:
            return False

        if not rows:
            return False

        loaded = 0
        for row in rows:
            trait_name, task_type, serialized = row[0], row[1], row[2]
            if not serialized:
                continue

            state = json.loads(serialized)

            if trait_name == "_overall" and task_type == "meta":
                if "overall_drift" in state:
                    self._overall_drift = RunningStatsDecayed.from_dict(state["overall_drift"])
                self.epsilon = state.get("epsilon", self.config.epsilon)
                self._iteration = state.get("iteration", 0)
                loaded += 1
            elif trait_name in self.TRAIT_NAMES:
                if "drift_stats" in state:
                    self._drift_stats[trait_name] = RunningStatsDecayed.from_dict(state["drift_stats"])
                if "quality_corr" in state:
                    self._trait_quality_corr[trait_name] = RunningCovarianceDecayed.from_dict(state["quality_corr"])
                loaded += 1

        if loaded > 0:
            logger.info(f"[EVOLUTION] Restored {loaded} trait states (iteration={self._iteration}, epsilon={self.epsilon:.4f})")
            return True
        return False
