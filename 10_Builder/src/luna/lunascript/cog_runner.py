"""LunaScript per-turn orchestrator — coordinates all cogs each turn.

Phase 1: measure traits, detect position, inject geometry constraints.
Phase 2: sign outbound, veto return, compare signatures, classify, log.
Phase 3: learn from delegation outcomes, evolve trait weights, adaptive thresholds.
Phase 4: feed delegation results to Scribe, pattern library.
"""

import json
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from .config import LunaScriptConfig
from .measurement import (
    measure_signature,
    SignatureMeasurement,
    TRAIT_TO_PERSONALITY,
)
from .baselines import (
    BaselineStats,
    load_baselines,
    save_baselines,
    calibrate_from_corpus,
    get_hardcoded_baselines,
)
from .position import detect_position, get_geometry, merge_geometry_with_mode
from .schema import apply_lunascript_schema
from .signature import (
    DelegationSignature,
    DeltaResult,
    sign_outbound,
    sign_return,
    compare_signatures,
    classify_delta,
    derive_glyph,
    DEFAULT_TRAIT_WEIGHTS,
)
from .veto import veto_check, build_retry_prompt
from .evolution import TraitEvolution

logger = logging.getLogger(__name__)


@dataclass
class LunaScriptTurnResult:
    position: str
    position_confidence: float
    geometry: dict
    glyph: str
    constraints_prompt: Optional[str]
    measurement: Optional[SignatureMeasurement] = None


@dataclass
class DelegationPackage:
    """Carries outbound signature + geometry through delegation round-trip."""
    outbound_signature: Optional[DelegationSignature] = None
    geometry: dict = field(default_factory=dict)
    constraint_prompt: Optional[str] = None
    measurement: Optional[SignatureMeasurement] = None


@dataclass
class DelegationResult:
    """Result of delegation return processing."""
    veto_passed: bool = True
    retry_prompt: Optional[str] = None
    delta_result: Optional[DeltaResult] = None
    classification: str = ""
    quality_score: float = 1.0
    return_signature: Optional[DelegationSignature] = None


class LunaScriptCogRunner:
    """Main LunaScript entry point — one instance per Director lifetime."""

    def __init__(self, db, config: LunaScriptConfig):
        self.db = db
        self.config = config
        self.baselines: dict[str, BaselineStats] = {}
        self.last_luna_response: Optional[str] = None
        self._prev_position: str = "OPENING"
        self._version: int = 1
        self._initialized: bool = False
        self._last_measurement: Optional[SignatureMeasurement] = None
        self._last_geometry: dict = {}
        self._drift_baseline_mean: float = 0.15
        self._drift_baseline_stddev: float = 0.08
        self._evolution: TraitEvolution = TraitEvolution(config)
        self._trait_weights: dict[str, float] = dict(DEFAULT_TRAIT_WEIGHTS)
        self._scribe_mailbox = None  # Set by Director after wiring (Phase 4)
        self._last_outbound_sig = None   # Cached for feedback (Phase 4)
        self._last_delta = None
        self._last_classification = ""

    async def initialize(self) -> None:
        """Apply schema, load or calibrate baselines."""
        try:
            await apply_lunascript_schema(self.db)
        except Exception as e:
            logger.warning(f"[LUNASCRIPT] Schema apply failed: {e}")

        # Try loading from DB first
        baselines = await load_baselines(self.db)
        if baselines:
            self.baselines = baselines
            logger.info(f"[LUNASCRIPT] Loaded {len(baselines)} baselines from DB")
        else:
            # Try calibrating from corpus
            baselines = await calibrate_from_corpus(
                self.db, self.config.min_corpus_size
            )
            if baselines:
                self.baselines = baselines
                await save_baselines(self.db, baselines)
                logger.info(f"[LUNASCRIPT] Calibrated {len(baselines)} baselines from corpus")
            else:
                # Fall back to hardcoded
                self.baselines = get_hardcoded_baselines()
                logger.info("[LUNASCRIPT] Using hardcoded baselines")

        # Load evolution state (Phase 3)
        if await self._evolution.load_state(self.db):
            # Use adaptive thresholds if we have enough data
            mean, stddev = self._evolution.get_adaptive_thresholds()
            self._drift_baseline_mean = mean
            self._drift_baseline_stddev = stddev
            logger.info(
                f"[LUNASCRIPT] Evolution loaded: epsilon={self._evolution.epsilon:.4f}, "
                f"drift_baseline={mean:.3f}±{stddev:.3f}"
            )

        self._initialized = True

    async def on_turn(
        self,
        message: str,
        history: list[str],
        perception=None,
        intent=None,
    ) -> LunaScriptTurnResult:
        """Per-turn cog: detect position, measure traits, build constraints."""
        if not self._initialized:
            return LunaScriptTurnResult(
                position="EXPLORING", position_confidence=0.0,
                geometry=get_geometry("EXPLORING"), glyph="○",
                constraints_prompt=None,
            )

        # 1. Detect position
        position, confidence = detect_position(
            message, history, self._prev_position
        )
        self._prev_position = position

        # 2. Measure traits on last Luna response (if exists)
        measurement = None
        if self.last_luna_response and len(self.last_luna_response) > 40:
            measurement = measure_signature(
                self.last_luna_response, self.baselines
            )
            self._last_measurement = measurement

        # 3. Get geometry and merge with response mode
        geometry = get_geometry(position)
        if intent and hasattr(intent, "mode"):
            geometry = merge_geometry_with_mode(geometry, intent.mode)
        self._last_geometry = geometry

        # 4. Build glyph
        glyph_state = {
            "position": position,
            "trait_vector": {
                name: score.value for name, score in measurement.traits.items()
            } if measurement else {},
        }
        glyph = derive_glyph(glyph_state)

        # 5. Build constraint prompt
        constraints_prompt = self._build_constraint_prompt(
            position, confidence, geometry, measurement
        )

        # 6. Persist state (best-effort)
        await self._persist_state(position, geometry, glyph, measurement)

        return LunaScriptTurnResult(
            position=position,
            position_confidence=confidence,
            geometry=geometry,
            glyph=glyph,
            constraints_prompt=constraints_prompt,
            measurement=measurement,
        )

    async def on_delegation_start(
        self,
        consciousness=None,
        personality=None,
        entities=None,
    ) -> DelegationPackage:
        """Sign outbound delegation with Luna's current cognitive snapshot."""
        if not self._initialized or not self._last_measurement:
            return DelegationPackage()

        outbound_sig = sign_outbound(
            consciousness=consciousness,
            personality=personality,
            entities=entities or [],
            measurement=self._last_measurement,
            glyph=derive_glyph({
                "position": self._prev_position,
                "trait_vector": {
                    n: s.value for n, s in self._last_measurement.traits.items()
                },
            }),
            version=self._version,
        )

        constraint_prompt = self._build_delegation_constraint(
            outbound_sig, self._last_geometry
        )

        return DelegationPackage(
            outbound_signature=outbound_sig,
            geometry=self._last_geometry,
            constraint_prompt=constraint_prompt,
            measurement=self._last_measurement,
        )

    async def on_delegation_return(
        self,
        response_text: str = "",
        package: DelegationPackage = None,
        provider_used: str = "",
    ) -> DelegationResult:
        """Process delegated response: veto -> sign -> compare -> classify -> log."""
        if not self._initialized or not package or not package.outbound_signature:
            return DelegationResult(veto_passed=True)

        # 1. Veto check — structural validation
        veto = veto_check(
            response_text,
            package.geometry,
            self.baselines,
            self.config.forbidden_phrases,
        )

        retry_prompt = None
        if not veto.passed and self.config.max_retries > 0:
            retry_prompt = build_retry_prompt(veto.violations, package.geometry)

        # 2. Measure the returned response
        return_measurement = measure_signature(response_text, self.baselines)

        # 3. Sign the return
        return_sig = sign_return(
            consciousness=None,
            personality=None,
            entities=package.outbound_signature.active_entities,
            measurement=return_measurement,
            version=package.outbound_signature.version,
        )

        # 4. Compare signatures (using evolved weights)
        delta = compare_signatures(
            package.outbound_signature,
            return_sig,
            self._trait_weights,
        )

        # 5. Classify
        classification = classify_delta(
            delta,
            self._drift_baseline_mean,
            self._drift_baseline_stddev,
        )

        # 6. Log to delegation_log table
        await self._log_delegation(
            outbound=package.outbound_signature,
            return_sig=return_sig,
            delta=delta,
            classification=classification,
            provider_used=provider_used,
            quality_score=veto.quality_score,
            veto_violations=veto.violations,
        )

        # 7. Evolution: record delegation and iterate weights (Phase 3)
        try:
            self._evolution.record_delegation(
                outbound=package.outbound_signature,
                delta=delta,
                classification=classification,
                quality_score=veto.quality_score,
            )

            # Iterate trait weights (epsilon-greedy)
            self._trait_weights = self._evolution.iterate_weights(self._trait_weights)

            # Update adaptive drift thresholds
            mean, stddev = self._evolution.get_adaptive_thresholds()
            self._drift_baseline_mean = mean
            self._drift_baseline_stddev = stddev

            # Persist evolution state (best-effort, async)
            await self._evolution.save_state(self.db)
        except Exception as e:
            logger.debug(f"[LUNASCRIPT] Evolution update failed: {e}")

        # 8. Feed delegation result to Scribe for memory filing (Phase 4)
        await self._send_to_scribe(
            outbound=package.outbound_signature,
            return_sig=return_sig,
            delta=delta,
            classification=classification,
            provider_used=provider_used,
            quality_score=veto.quality_score,
            veto_violations=veto.violations,
        )

        # Cache for feedback endpoint
        self._last_outbound_sig = package.outbound_signature
        self._last_delta = delta
        self._last_classification = classification

        logger.info(
            f"[LUNASCRIPT] Delegation: {classification} "
            f"(drift={delta.drift_score:.3f}, quality={veto.quality_score:.2f}, "
            f"provider={provider_used}, epsilon={self._evolution.epsilon:.4f})"
        )

        return DelegationResult(
            veto_passed=veto.passed,
            retry_prompt=retry_prompt,
            delta_result=delta,
            classification=classification,
            quality_score=veto.quality_score,
            return_signature=return_sig,
        )

    def _build_constraint_prompt(
        self,
        position: str,
        confidence: float,
        geometry: dict,
        measurement: Optional[SignatureMeasurement],
    ) -> Optional[str]:
        """Build the constraint string for system_prompt injection."""
        lines = ["## CONVERSATIONAL POSTURE (LunaScript)"]
        lines.append(f"Position: {position} (confidence: {confidence:.0%})")
        lines.append(f"Max sentences: {geometry.get('max_sent', 8)}")

        if geometry.get("question_req"):
            lines.append("End with a question.")
        if geometry.get("tangent"):
            lines.append("Tangents allowed — follow interesting threads.")
        else:
            lines.append("Stay focused, no tangents.")

        pattern = geometry.get("pattern", "")
        if pattern:
            lines.append(f"Flow: {pattern}")

        if measurement:
            trait_hints = []
            for trait_name, score in measurement.traits.items():
                if score.value > 0.75:
                    trait_hints.append(f"{trait_name}:high")
                elif score.value < 0.25:
                    trait_hints.append(f"{trait_name}:low")
            if trait_hints:
                lines.append(f"Current voice: {', '.join(trait_hints)}")

        return "\n".join(lines)

    def _build_delegation_constraint(
        self,
        signature: DelegationSignature,
        geometry: dict,
    ) -> str:
        """Build constraint prompt specifically for delegation system_prompt."""
        lines = ["## VOICE FIDELITY CONSTRAINTS (LunaScript)"]
        lines.append("Maintain Luna's voice profile in your response:")
        lines.append(f"- Max sentences: {geometry.get('max_sent', 8)}")

        if geometry.get("question_req"):
            lines.append("- End with a question.")
        if not geometry.get("tangent", True):
            lines.append("- No bullet lists. No tangents.")

        lines.append("- Use contractions (don't, can't, won't).")
        lines.append("- Vary sentence lengths. Mix short and long.")
        lines.append("- Never use: 'I'd be happy to', 'Certainly!', 'As an AI'.")

        high_traits = [t for t, v in signature.trait_vector.items() if v > 0.7]
        low_traits = [t for t, v in signature.trait_vector.items() if v < 0.3]
        if high_traits:
            lines.append(f"- Maintain high: {', '.join(high_traits)}")
        if low_traits:
            lines.append(f"- Keep low: {', '.join(low_traits)}")

        return "\n".join(lines)

    async def _log_delegation(
        self,
        outbound: DelegationSignature,
        return_sig: DelegationSignature,
        delta: DeltaResult,
        classification: str,
        provider_used: str,
        quality_score: float,
        veto_violations: list[str],
    ) -> None:
        """Log delegation round-trip to lunascript_delegation_log."""
        try:
            await self.db.execute(
                "INSERT INTO lunascript_delegation_log "
                "(outbound_sig, outbound_glyph, return_sig, return_glyph, "
                "delta_vector, delta_class, drift_score, task_type, "
                "provider_used, success_score, veto_violations, "
                "iteration_applied, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    json.dumps(outbound.trait_vector),
                    outbound.glyph_string,
                    json.dumps(return_sig.trait_vector),
                    return_sig.glyph_string,
                    json.dumps(delta.delta_vector),
                    classification,
                    delta.drift_score,
                    None,  # task_type — Phase 3
                    provider_used,
                    quality_score,
                    json.dumps(veto_violations) if veto_violations else None,
                    None,  # iteration_applied — Phase 3
                    time.time(),
                ),
            )
        except Exception as e:
            logger.debug(f"[LUNASCRIPT] Delegation log failed: {e}")

    async def _persist_state(
        self,
        position: str,
        geometry: dict,
        glyph: str,
        measurement: Optional[SignatureMeasurement],
    ) -> None:
        """Save current state to lunascript_state table."""
        try:
            trait_vector = {}
            if measurement:
                trait_vector = {
                    name: score.value
                    for name, score in measurement.traits.items()
                }

            trait_trends = self._evolution.get_trait_trends()

            await self.db.execute(
                "INSERT OR REPLACE INTO lunascript_state "
                "(id, trait_vector, trait_weights, trait_trends, mode, "
                "glyph_string, constraints, version, epsilon, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    json.dumps(trait_vector),
                    json.dumps(self._trait_weights),
                    json.dumps(trait_trends),
                    position,
                    glyph,
                    json.dumps(geometry),
                    self._version,
                    self._evolution.epsilon,
                    time.time(),
                ),
            )
            self._version += 1
        except Exception as e:
            logger.debug(f"[LUNASCRIPT] State persist failed: {e}")

    # ── Phase 4: Scribe feed & pattern library ──

    def set_scribe_mailbox(self, mailbox) -> None:
        """Set the Scribe actor's mailbox for delegation result feeding."""
        self._scribe_mailbox = mailbox
        logger.info("[LUNASCRIPT] Scribe mailbox wired for delegation feed")

    async def _send_to_scribe(
        self,
        outbound: DelegationSignature,
        return_sig: DelegationSignature,
        delta: DeltaResult,
        classification: str,
        provider_used: str,
        quality_score: float,
        veto_violations: list[str],
    ) -> None:
        """Feed delegation result to Scribe for memory filing."""
        if not self._scribe_mailbox:
            return

        delegation_data = {
            "type": "DELEGATION_RESULT",
            "outbound_sig": outbound.trait_vector,
            "outbound_glyph": outbound.glyph_string,
            "return_sig": return_sig.trait_vector,
            "return_glyph": return_sig.glyph_string,
            "delta_vector": delta.delta_vector,
            "classification": classification,
            "drift_score": delta.drift_score,
            "provider_used": provider_used,
            "quality_score": quality_score,
            "veto_violations": veto_violations,
            "position": self._prev_position,
            "epsilon": self._evolution.epsilon,
            "iteration": self._evolution._iteration,
        }

        try:
            from luna.actors.base import Message
            msg = Message(
                type="extract_turn",
                payload={
                    "role": "system",
                    "content": json.dumps(delegation_data),
                    "immediate": True,
                    "source": "lunascript",
                },
            )
            await self._scribe_mailbox.put(msg)
            logger.debug(f"[LUNASCRIPT] Delegation result sent to Scribe ({classification})")
        except Exception as e:
            logger.debug(f"[LUNASCRIPT] Scribe feed failed: {e}")

    async def save_pattern(self, name: str) -> bool:
        """Save current trait vector + glyph as a named pattern."""
        if not self._last_measurement:
            return False

        trait_vector = {
            n: s.value for n, s in self._last_measurement.traits.items()
        }
        glyph = derive_glyph({
            "position": self._prev_position,
            "trait_vector": trait_vector,
        })

        try:
            await self.db.execute(
                "INSERT OR REPLACE INTO lunascript_patterns "
                "(name, trait_vector, glyph_string, usage_count, avg_success, "
                "created_at, last_used) "
                "VALUES (?, ?, ?, 0, 0.0, ?, ?)",
                (name, json.dumps(trait_vector), glyph, time.time(), time.time()),
            )
            logger.info(f"[LUNASCRIPT] Pattern saved: {name} ({glyph})")
            return True
        except Exception as e:
            logger.debug(f"[LUNASCRIPT] Pattern save failed: {e}")
            return False

    async def load_pattern(self, name: str) -> Optional[dict]:
        """Load a named pattern from the pattern library."""
        try:
            row = await self.db.fetchone(
                "SELECT name, trait_vector, glyph_string, usage_count, avg_success "
                "FROM lunascript_patterns WHERE name = ?",
                (name,),
            )
            if not row:
                return None

            await self.db.execute(
                "UPDATE lunascript_patterns SET usage_count = usage_count + 1, "
                "last_used = ? WHERE name = ?",
                (time.time(), name),
            )
            return {
                "name": row[0],
                "trait_vector": json.loads(row[1]),
                "glyph": row[2],
                "usage_count": row[3],
                "avg_success": row[4],
            }
        except Exception as e:
            logger.debug(f"[LUNASCRIPT] Pattern load failed: {e}")
            return None

    async def list_patterns(self) -> list[dict]:
        """List all saved patterns."""
        try:
            rows = await self.db.fetchall(
                "SELECT name, glyph_string, usage_count, avg_success "
                "FROM lunascript_patterns ORDER BY usage_count DESC"
            )
            return [
                {"name": r[0], "glyph": r[1], "usage_count": r[2], "avg_success": r[3]}
                for r in (rows or [])
            ]
        except Exception as e:
            logger.debug(f"[LUNASCRIPT] Pattern list failed: {e}")
            return []
