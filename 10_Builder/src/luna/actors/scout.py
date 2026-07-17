"""
Scout Actor — Blockage Detection, Overdrive Retrieval, and Watchdog
====================================================================

Scout inspects Director's draft responses for blockage patterns
(surrender, shallow recall, deflection, hedging) before delivery.
When blockage is detected, Overdrive expands retrieval with tiered
token budgets and re-generates. Watchdog detects stuck states and
forces reset to IDLE.

See: Docs/HANDOFF_SCOUT_OVERDRIVE_WATCHDOG.md
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from luna.actors.base import Actor, Message

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Blockage Detection Patterns
# ═══════════════════════════════════════════════════════════════════

# Surrender: Luna admits she has no information
SURRENDER_PATTERN = re.compile(
    r"i don.t have (any |specific )?(information|memory|memories|context|knowledge|details)"
    r"|tell me (more|a bit more)"
    r"|i.m not (sure|familiar)"
    r"|i don.t know (about|anything)"
    r"|not in my (memory|records|context)"
    r"|i.m afraid i don.t"
    r"|doesn.t ring (any )?bells?"
    r"|outside my (current )?knowledge",
    re.IGNORECASE,
)

# Shallow recall: Luna found something but not enough
SHALLOW_PATTERN = re.compile(
    r"details are (a bit |quite )?fuzzy"
    r"|i don.t have much more (concrete |specific )?(information|details)"
    r"|beyond that.{0,20}(i.m afraid|i don.t)"
    r"|that.s (about )?all i can (confidently |really )?(say|recall|remember)"
    r"|a bit out of the loop",
    re.IGNORECASE,
)

# Deflection: Luna asks user to teach her about her own stuff
DEFLECTION_PATTERN = re.compile(
    r"(can|could) you (tell|share|fill) me (more|in|a bit)"
    r"|i.d (love|be happy|be eager) to (learn|hear|know) more"
    r"|what can you (tell|share|teach) me about"
    r"|why don.t you fill me in"
    r"|i.m (all ears|listening closely|eager to learn)",
    re.IGNORECASE,
)

# Hedging: vague claims without evidence (only flagged on short responses)
HEDGING_PATTERN = re.compile(
    r"(it seems like|from what i can gather|if i recall correctly|i believe).{0,40}$"
    r"|some kind of.{0,30}(environment|platform|project|system|tool)",
    re.IGNORECASE,
)

# Confabulation: Luna claims memory-based knowledge
MEMORY_CLAIM_PATTERN = re.compile(
    r"(i remember|from what i recall|according to my (records|memory|memories))"
    r"|that rings a (very )?(clear )?bell"
    r"|if i.m remembering correctly"
    r"|from my memory banks?"
    r"|i have (some|a few) (relevant|fond) (details|memories|bits)"
    r"|as i understand it"
    r"|i (can see|have|recall) that",
    re.IGNORECASE,
)

# Claim types that indicate recalled (not inferred) knowledge
FACTUAL_CLAIM_PATTERNS = [
    re.compile(r"(\b[A-Z][a-z]+\b).{0,30}(is|was|has|are|were)\b", re.IGNORECASE),
    re.compile(r"(designed to|built to|created for|its job is to)\b"),
    re.compile(r"(works? with|part of|connected to|related to|within)\b"),
]


# ═══════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BlockageReport:
    """Result of Scout's draft inspection."""
    blocked: bool
    blockage_type: Optional[str] = None       # surrender, shallow_recall, deflection, hedging, confabulation
    severity: str = "none"                     # none, medium, high
    overdrive_tier: int = 0                    # 0 = no overdrive, 1-3
    patterns_matched: List[str] = field(default_factory=list)
    draft_length: int = 0
    recommendation: str = "pass"              # pass, overdrive_t1/t2/t3, pass_cooling, reconcile
    confabulation_data: Optional[Dict] = None  # Populated when confabulation detected


# ═══════════════════════════════════════════════════════════════════
# Scout Actor
# ═══════════════════════════════════════════════════════════════════

class ScoutActor(Actor):
    """
    Blockage detection agent. Inspects Director drafts before delivery.
    Triggers Overdrive when blockage detected. Manages cooldown state.
    """

    def __init__(self):
        super().__init__(name="scout")
        self._cooldown_until: Optional[datetime] = None
        self._cooldown_duration: float = 60.0  # seconds
        self._last_overdrive_tier: int = 0
        self._consecutive_blockages: int = 0
        self.is_ready = True

    async def handle(self, msg) -> None:
        # All access is via scout.inspect() directly — mailbox unused
        logger.warning(f"ScoutActor: unexpected mailbox message: {msg.type if hasattr(msg, 'type') else msg}")

    def inspect(self, draft: str, query: str, context_size: int = 0,
                retrieved_context: str = "") -> BlockageReport:
        """
        Inspect a draft response for blockage and confabulation patterns.

        Args:
            draft: The Director's draft response text
            query: The original user query
            context_size: How many chars of context were available
            retrieved_context: The actual retrieved context text (for confabulation cross-ref)

        Returns:
            BlockageReport with blockage type, severity, and recommended tier
        """
        report = BlockageReport(blocked=False, draft_length=len(draft))

        # ── CONFABULATION CHECK (runs first — most dangerous) ──
        confab_risk = self._check_confabulation_risk(
            draft=draft,
            context_tokens=context_size,
            response_claims=len(self._extract_claims(draft)),
        )

        if confab_risk in ("medium", "high"):
            claims = self._extract_claims(draft)
            unsupported = self._cross_reference_claims(claims, retrieved_context)

            if unsupported:
                report.blocked = True
                report.blockage_type = "confabulation"
                report.severity = confab_risk
                report.confabulation_data = {
                    "risk_level": confab_risk,
                    "total_claims": len(claims),
                    "unsupported_claims": unsupported,
                    "context_tokens": context_size,
                }
                report.recommendation = "reconcile"
                logger.warning(
                    f"[SCOUT] Confabulation detected: risk={confab_risk} "
                    f"claims={len(claims)} unsupported={len(unsupported)} "
                    f"context_tokens={context_size}"
                )
                return report

        # ── EXISTING CHECKS (surrender, shallow, deflection, hedging) ──
        if SURRENDER_PATTERN.search(draft):
            report.patterns_matched.append("surrender")
        if SHALLOW_PATTERN.search(draft):
            report.patterns_matched.append("shallow_recall")
        if DEFLECTION_PATTERN.search(draft):
            report.patterns_matched.append("deflection")
        if HEDGING_PATTERN.search(draft) and len(draft) < 500:
            report.patterns_matched.append("hedging")

        if not report.patterns_matched:
            self._consecutive_blockages = 0
            return report  # Clean draft, pass through

        report.blocked = True
        self._consecutive_blockages += 1

        if "surrender" in report.patterns_matched or "deflection" in report.patterns_matched:
            report.blockage_type = report.patterns_matched[0]
            report.severity = "high"
        else:
            report.blockage_type = report.patterns_matched[0]
            report.severity = "medium"

        report.overdrive_tier = self._select_tier(report)
        report.recommendation = f"overdrive_t{report.overdrive_tier}"

        # Check cooldown — may downgrade tier
        if self._is_cooling():
            available_tier = self._available_tier()
            if report.overdrive_tier > available_tier:
                report.overdrive_tier = available_tier
                report.recommendation = f"overdrive_t{available_tier}" if available_tier > 0 else "pass_cooling"

        logger.info(
            f"[SCOUT] Blockage detected: type={report.blockage_type} "
            f"severity={report.severity} tier={report.overdrive_tier} "
            f"patterns={report.patterns_matched} consecutive={self._consecutive_blockages}"
        )
        return report

    # ═══════════════════════════════════════════════════════════════════
    # Confabulation Detection (Level 1 + 2)
    # ═══════════════════════════════════════════════════════════════════

    def _check_confabulation_risk(
        self,
        draft: str,
        context_tokens: int,
        response_claims: int,
    ) -> str:
        """
        Assess confabulation risk (Level 1: context-response mismatch).

        Returns: "none", "low", "medium", "high"
        """
        has_memory_claims = bool(MEMORY_CLAIM_PATTERN.search(draft))
        draft_length = len(draft)

        # ANY memory claim with zero context is confabulation
        if has_memory_claims and context_tokens == 0:
            return "high"

        # Rich confident response with no context backing
        if context_tokens < 200 and has_memory_claims and draft_length > 300:
            return "high"

        # Moderately detailed response with thin context
        if context_tokens < 500 and has_memory_claims and draft_length > 500:
            return "medium"

        return "none"

    def _extract_claims(self, draft: str) -> List[str]:
        """Extract factual claims from draft for verification (Level 2)."""
        claims = []
        sentences = re.split(r'[.!?]+', draft)

        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 20:
                continue

            for pattern in FACTUAL_CLAIM_PATTERNS:
                if pattern.search(sentence):
                    claims.append(sentence)
                    break

        return claims

    def _cross_reference_claims(
        self,
        claims: List[str],
        retrieved_context: str,
    ) -> List[Dict]:
        """
        Cross-reference extracted claims against retrieved context (Level 2).

        Returns list of unsupported claims.
        """
        unsupported = []
        context_lower = retrieved_context.lower() if retrieved_context else ""

        for claim in claims:
            key_terms = re.findall(r'\b[A-Z][a-z]{2,}\b', claim)
            key_terms += re.findall(r'\b\w{4,}\b', claim.lower())
            key_terms = list(set(key_terms))

            if not context_lower:
                unsupported.append({"claim": claim, "support": "none", "reason": "empty_context"})
                continue

            matches = sum(1 for term in key_terms if term.lower() in context_lower)
            match_ratio = matches / max(len(key_terms), 1)

            if match_ratio < 0.3:
                unsupported.append({
                    "claim": claim,
                    "support": "unsupported",
                    "match_ratio": match_ratio,
                    "reason": "key_terms_absent_from_context",
                })
            elif match_ratio < 0.5:
                unsupported.append({
                    "claim": claim,
                    "support": "weak",
                    "match_ratio": match_ratio,
                    "reason": "partial_term_overlap",
                })

        return unsupported

    def _select_tier(self, report: BlockageReport) -> int:
        """Select Overdrive tier based on blockage depth."""
        if self._consecutive_blockages >= 3:
            return 3
        if report.severity == "high":
            return 2
        return 1

    def _is_cooling(self) -> bool:
        """Check if Overdrive is in cooldown."""
        if self._cooldown_until is None:
            return False
        return datetime.now() < self._cooldown_until

    def _available_tier(self) -> int:
        """What tier is available during cooldown?"""
        if self._cooldown_until is None:
            return 3
        elapsed = (datetime.now() - (self._cooldown_until - timedelta(seconds=self._cooldown_duration))).total_seconds()
        if elapsed < 20:
            return 0  # 0-20s: COOLING — nothing available
        elif elapsed < 50:
            return 1  # 20-50s: WARMING — Tier 1 only
        else:
            return 3  # 50s+: READY — all tiers

    def start_cooldown(self, tier_used: int):
        """Start cooldown after Overdrive fires."""
        self._cooldown_until = datetime.now() + timedelta(seconds=self._cooldown_duration)
        self._last_overdrive_tier = tier_used
        logger.info(f"[SCOUT] Cooldown started: {self._cooldown_duration}s after Tier {tier_used}")

    async def overdrive(self, query: str, tier: int, engine: Any) -> Optional[str]:
        """
        Run expanded retrieval at the specified tier and re-generate.

        Args:
            query: Original user query
            tier: Overdrive tier (1-3)
            engine: LunaEngine instance

        Returns:
            Re-generated response text, or None if Overdrive also fails
        """
        from luna.tools.search_chain import SearchChainConfig, SearchSourceConfig, run_search_chain

        logger.info(f"[OVERDRIVE] Tier {tier} activating for: {query[:50]}")

        config = self._build_tier_config(tier)
        results = await run_search_chain(config, query, engine)

        if not results:
            logger.warning(f"[OVERDRIVE] Tier {tier} retrieval returned empty")
            return None

        # For Tier 3, also run AgentLoop
        agent_context = ""
        if tier >= 3:
            agent_loop = getattr(engine, 'agent_loop', None)
            if agent_loop:
                try:
                    loop_result = await asyncio.wait_for(agent_loop.run(query), timeout=30.0)
                    if loop_result and loop_result.success:
                        agent_context = loop_result.response or ""
                except Exception as e:
                    logger.warning(f"[OVERDRIVE] AgentLoop failed in Tier 3: {e}")

        # Assemble enriched context
        enriched_context = "\n\n".join([r.get("content", "") for r in results])
        if agent_context:
            enriched_context += f"\n\n[Agent Research]\n{agent_context}"

        # Re-generate through Director with enriched context
        director = engine.get_actor("director") if hasattr(engine, 'get_actor') else None
        if not director:
            logger.error("[OVERDRIVE] No Director actor available for re-generation")
            return None

        try:
            result = await director.process(
                message=query,
                context={
                    "interface": "voice",
                    "memories": results,
                    "overdrive": True,
                    "overdrive_tier": tier,
                    "enriched_context": enriched_context,
                }
            )
            response = result.get("response", "") if result else ""

            if response:
                logger.info(f"[OVERDRIVE] Tier {tier} re-generation succeeded ({len(response)} chars)")
                self.start_cooldown(tier)

            return response

        except Exception as e:
            logger.error(f"[OVERDRIVE] Re-generation failed: {e}")
            return None

    def _build_tier_config(self, tier: int):
        """Build SearchChainConfig for a specific Overdrive tier."""
        from luna.tools.search_chain import SearchChainConfig, SearchSourceConfig

        if tier == 1:
            return SearchChainConfig(
                max_total_tokens=8000,
                sources=[
                    SearchSourceConfig(type="matrix", max_tokens=4000),
                    SearchSourceConfig(type="dataroom", max_tokens=4000, limit=5),
                ]
            )
        elif tier == 2:
            return SearchChainConfig(
                max_total_tokens=12000,
                sources=[
                    SearchSourceConfig(type="matrix", max_tokens=6000),
                    SearchSourceConfig(type="dataroom", max_tokens=6000, limit=8),
                    SearchSourceConfig(type="local_files", max_tokens=3000, limit=5),
                ]
            )
        else:  # Tier 3
            return SearchChainConfig(
                max_total_tokens=16000,
                sources=[
                    SearchSourceConfig(type="matrix", max_tokens=8000),
                    SearchSourceConfig(type="dataroom", max_tokens=8000, limit=12),
                    SearchSourceConfig(type="local_files", max_tokens=4000, limit=8),
                ]
            )

    def get_status(self) -> dict:
        """Current Scout state for debug output."""
        return {
            "cooling": self._is_cooling(),
            "available_tier": self._available_tier(),
            "consecutive_blockages": self._consecutive_blockages,
            "last_tier_used": self._last_overdrive_tier,
            "cooldown_remaining": max(0, (self._cooldown_until - datetime.now()).total_seconds()) if self._cooldown_until else 0,
        }


# ═══════════════════════════════════════════════════════════════════
# Watchdog — Stuck State Detection and Recovery
# ═══════════════════════════════════════════════════════════════════

class Watchdog:
    """
    Detects stuck states in Luna's processing pipeline.
    Forces reset to IDLE when timeouts exceeded.
    """

    def __init__(self, engine: Any):
        self._engine = engine
        self._active_operations: Dict[str, datetime] = {}
        self._max_durations: Dict[str, float] = {
            "agent_loop": 45.0,
            "planning": 15.0,
            "waiting": 30.0,
            "overdrive": 40.0,
            "director_process": 30.0,
        }
        self._recursion_depth: int = 0
        self._max_recursion: int = 1  # Scout → Overdrive → Director. No deeper.

    def start_operation(self, op_id: str) -> None:
        """Mark an operation as started."""
        self._active_operations[op_id] = datetime.now()
        logger.debug(f"[WATCHDOG] Operation started: {op_id}")

    def end_operation(self, op_id: str) -> None:
        """Mark an operation as completed."""
        if op_id in self._active_operations:
            elapsed = (datetime.now() - self._active_operations[op_id]).total_seconds()
            del self._active_operations[op_id]
            logger.debug(f"[WATCHDOG] Operation completed: {op_id} ({elapsed:.1f}s)")

    def check_stuck(self) -> List[str]:
        """Check for stuck operations. Returns list of stuck operation IDs."""
        stuck = []
        now = datetime.now()
        for op_id, start_time in list(self._active_operations.items()):
            max_dur = self._max_durations.get(op_id, 30.0)
            elapsed = (now - start_time).total_seconds()
            if elapsed > max_dur:
                logger.warning(f"[WATCHDOG] STUCK detected: {op_id} running for {elapsed:.1f}s (max {max_dur}s)")
                stuck.append(op_id)
        return stuck

    async def force_reset(self, op_id: str) -> None:
        """Force-reset a stuck operation."""
        logger.warning(f"[WATCHDOG] Forcing reset for: {op_id}")

        if op_id == "agent_loop":
            agent_loop = getattr(self._engine, 'agent_loop', None)
            if agent_loop and hasattr(agent_loop, 'abort'):
                agent_loop.abort()

        self.end_operation(op_id)

    def enter_recursion(self) -> bool:
        """Track recursion depth. Returns False if max depth exceeded."""
        self._recursion_depth += 1
        if self._recursion_depth > self._max_recursion:
            logger.warning(f"[WATCHDOG] Recursion blocked at depth {self._recursion_depth}")
            self._recursion_depth = 0
            return False
        return True

    def exit_recursion(self) -> None:
        """Exit one level of recursion."""
        self._recursion_depth = max(0, self._recursion_depth - 1)

    def get_status(self) -> dict:
        """Current Watchdog state for debug output."""
        now = datetime.now()
        active = {
            op_id: {
                "elapsed_s": round((now - start).total_seconds(), 1),
                "max_s": self._max_durations.get(op_id, 30.0),
                "stuck": (now - start).total_seconds() > self._max_durations.get(op_id, 30.0),
            }
            for op_id, start in self._active_operations.items()
        }
        return {
            "active_operations": active,
            "recursion_depth": self._recursion_depth,
            "stuck_count": sum(1 for v in active.values() if v["stuck"]),
        }


async def watchdog_loop(watchdog: Watchdog, interval: float = 5.0):
    """Background task that periodically checks for stuck states."""
    while True:
        await asyncio.sleep(interval)
        try:
            stuck = watchdog.check_stuck()
            for op_id in stuck:
                await watchdog.force_reset(op_id)
        except Exception as e:
            logger.error(f"[WATCHDOG] Loop error: {e}")
