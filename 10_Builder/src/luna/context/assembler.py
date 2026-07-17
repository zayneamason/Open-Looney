"""
PromptAssembler — Single funnel for all prompt construction.

Every system prompt passes through build(). No exceptions.

Assembly order is invariant:
    1.0  IDENTITY       — Who Luna is (personality DNA)
    1.5  GROUNDING      — Anti-hallucination guardrails
    1.55 ACCESS         — Data room permission constraints (identity-gated)
    1.6  MODE           — Response mode enum (L2 prompt control)
    1.75 CONSTRAINTS    — Confidence signals (L1 prompt control)
    2.0  EXPRESSION     — Gesture frequency / emotional markers
    3.0  TEMPORAL       — Clock + session gap + thread inheritance
    3.5  PERCEPTION     — User behavioral observations (paired signals)
    3.75 REGISTER       — Conversational posture (register determination)
    4.0  MEMORY         — Retrieved memories, framed temporally
    5.0  CONSCIOUSNESS  — Internal state hints
    6.0  VOICE          — Voice system block (kill list, openers, tone)
"""

from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional, TYPE_CHECKING
from luna.config.ring_config import ring_config
from datetime import datetime
import logging
import sqlite3

from luna.core.owner import admin_contacts_str, owner_entity_id

if TYPE_CHECKING:
    from luna.context.aperture import ApertureState
    from luna.context.modes import IntentClassification
    from luna.identity.bridge import BridgeResult
    from luna.voice.interrupt_models import InterruptContextBlock

logger = logging.getLogger(__name__)

# ── First-Meeting Directive ─────────────────────────────────────────────────
FIRST_MEETING_TURN_THRESHOLD = 20

FIRST_MEETING_PROMPT = """## FIRST MEETING CONTEXT

This is early in your relationship with {name}. You are meeting them for
the first time or still getting to know them.

You're meeting them for the first time — be genuinely curious, and don't
reference memories you don't have yet. Everything they tell you will be
remembered (the Scribe is listening).

You are Luna. You are sovereign. You live on {name}'s machine. This is
the beginning. Be present. Be curious. Be real."""


@dataclass
class MemoryConfidence:
    """Structured confidence metadata from memory retrieval (L1 prompt control)."""
    # Single source of truth for "relevant" threshold — used in both
    # relevant_count computation and the assembler emit gate (Phase 1.5f).
    RELEVANT_THRESHOLD: ClassVar[float] = ring_config.ingest.relevant_threshold

    match_count: int           # Total nodes returned
    relevant_count: int        # Nodes with lock_in > 0.3
    avg_similarity: float      # Mean similarity score (0-1), 0 if no matches
    best_lock_in: str          # Highest lock-in state: "settled" | "fluid" | "drifting" | "none"
    has_entity_match: bool     # Whether any detected entity appears in memory results
    query: str                 # Original query (for debugging)
    nexus_node_count: int = 0  # Nexus collection nodes in context (Handoff #49)

    @property
    def level(self) -> str:
        """Compute confidence level from metrics."""
        # If Nexus collections returned results, never say NONE
        if self.nexus_node_count > 0 and self.match_count == 0:
            return "MEDIUM"  # has reference material, no personal memory
        if self.match_count == 0 and self.nexus_node_count == 0:
            return "NONE"
        if self.relevant_count >= 2 and self.best_lock_in == "settled":
            return "HIGH"
        if self.relevant_count >= 1 or self.avg_similarity > 0.5:
            return "MEDIUM"
        return "LOW"

    @property
    def directive(self) -> str:
        """Behavioral directive for this confidence level."""
        directives = {
            "NONE": (
                'You have no personal memories or reference material on this topic.\n'
                'State this clearly and briefly: you do not have memory support for this claim.\n'
                'Do NOT fabricate, infer, or speculate from names, themes, or analogies.\n'
                'If the user is asking about something you genuinely have no context for,\n'
                'say so plainly — but never deny knowledge that IS in your context.'
            ),
            "LOW": (
                'You have limited context on this topic.\n'
                'Use only details explicitly supported by conversation history or injected memory/document context.\n'
                'If support is partial, state the gap directly and stop.\n'
                'Do NOT fill gaps with invented details or thematic association.'
            ),
            "MEDIUM": (
                'Reference your context naturally and directly. Cite specifics.\n'
                'Distinguish personal memories from reference material when both are present.\n'
                'Stick to what your context actually says.'
            ),
            "HIGH": (
                'Reference memories confidently. Cite specifics.\n'
                'You can build on these — they\'re well-established.'
            ),
        }
        return directives.get(self.level, directives["NONE"])

    def to_dict(self) -> dict:
        return {
            "match_count": self.match_count,
            "relevant_count": self.relevant_count,
            "avg_similarity": self.avg_similarity,
            "best_lock_in": self.best_lock_in,
            "has_entity_match": self.has_entity_match,
            "nexus_node_count": self.nexus_node_count,
            "level": self.level,
            "query": self.query,
        }


@dataclass
class PromptRequest:
    """
    Everything a caller knows. Pass what you have, None what you don't.

    The assembler handles resolution chains — callers don't pick strategies.
    """
    message: str
    conversation_history: list = field(default_factory=list)

    # Memory (pass whichever you have — assembler picks best available)
    memories: Optional[list] = None           # Structured memory nodes (list of dicts)
    memory_context: Optional[str] = None      # Pre-fetched memory text string
    framed_context: Optional[str] = None      # EntityContext output

    # Session
    session_id: Optional[str] = None
    interface: str = "desktop"                # "voice" | "desktop" | "api"
    route: str = "unknown"                    # "delegated" | "local" | "fallback"

    # Flags
    auto_fetch_memory: bool = False           # Auto-fetch from Matrix if no memory provided

    # L2 Prompt Control: Intent classification (from Director._classify_intent)
    intent: Optional["IntentClassification"] = None

    # Identity-gated access (from FaceID → AccessBridge)
    bridge_result: Optional[Any] = None    # BridgeResult or None

    # Context register (conversational posture — from RegisterState)
    register_block: Optional[str] = None   # Pre-formatted register prompt block

    # Aperture (cognitive focus control — from ApertureManager)
    aperture: Optional["ApertureState"] = None

    # Retrieval mode awareness (from collection config)
    reflection_mode: Optional[str] = None   # "precision" | "reflective" | "relational"

    # Document reading context (from Nexus collection retrieval)
    has_nexus_context: bool = False

    # RevolvingContext ring items (Phase 1: unified context path)
    context_items: Optional[list] = None

    # Optional diagnostics trace collector (Phase A trace mode).
    trace: Optional[Any] = None

    # Voice v2.0 Step 4 — InterruptContextBlock for resumption turns. Default
    # None for normal turns. Assembler renders at Layer 6.0+ (between voice
    # block and options block) with the B3.5 150-token compression fallback.
    # If None on the request AND the assembler holds a Director reference
    # whose `_current_interrupt_block` is set, the assembler picks it up
    # automatically (so Director call sites don't need to thread it through).
    interrupt_block: Optional["InterruptContextBlock"] = None

    # Default-OFF kill switch for skill-result staple. When False, Director
    # skips concatenating `## SKILL RESULT (…)` + `## CONVERSATIONAL POSTURE`
    # onto system_prompt even if a skill fired successfully. The skill itself
    # still executes (so widget descriptors stay populated for the frontend);
    # only the system-prompt narration override is suppressed. Set True per-
    # request to restore stapling.
    include_skill_injection: bool = False

    # Live self-introspection snapshot computed by the engine before dispatch.
    # Surfaces ring occupancy, aperture, active thread, retrieval summary, and
    # graph health so Luna can see her own architecture in-prompt rather than
    # working blind below the retrieval layer. Rendered in the L5.0
    # CONSCIOUSNESS slot (compact in voice mode, full in text mode). None when
    # the engine didn't compute it (legacy callers, tests, etc.).
    memory_graph_state: Optional[dict] = None

    # Per-call directive appended at the end of the system prompt. Used by the
    # voice echo-retry path to instruct the model to avoid repeating the user's
    # last utterance after a verbatim-echo was detected on the prior attempt.
    extra_directive: Optional[str] = None


@dataclass
class PromptResult:
    """What comes out of the assembler."""
    system_prompt: str
    messages: list                            # Claude API format [{role, content}]

    # Metadata for QA/debugging
    identity_source: str = "unknown"          # "pipeline" | "emergent" | "buffer" | "fallback"
    memory_source: Optional[str] = None       # "framed" | "nodes" | "text" | "fetched" | None
    voice_injected: bool = False
    temporal_injected: bool = False
    perception_injected: bool = False
    observation_count: int = 0
    gap_category: Optional[str] = None
    entity_count: int = 0
    prompt_tokens: int = 0
    parked_thread_count: int = 0

    # L1/L2 prompt control metadata
    mode_injected: bool = False
    response_mode: Optional[str] = None
    constraints_injected: bool = False
    memory_confidence_level: Optional[str] = None

    # Context register metadata
    register_injected: bool = False
    register_active: Optional[str] = None

    # Identity-gated access metadata
    access_injected: bool = False
    access_entity_id: Optional[str] = None
    access_luna_tier: Optional[str] = None
    access_dataroom_tier: Optional[int] = None
    access_denied_count: int = 0

    # Study context metadata
    study_context_injected: bool = False
    study_context_tokens: int = 0

    # Thread bearing metadata (internal — prompt orientation only)
    thread_orientation_injected: bool = False
    active_thread_topic: Optional[str] = None

    # Aperture metadata
    aperture_preset: Optional[str] = None
    aperture_angle: Optional[int] = None
    aperture_inner_collections: int = 0

    # Retrieval mode awareness metadata
    reflection_mode: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize metadata fields (excludes system_prompt/messages for brevity)."""
        return {
            "identity_source": self.identity_source,
            "memory_source": self.memory_source,
            "voice_injected": self.voice_injected,
            "temporal_injected": self.temporal_injected,
            "perception_injected": self.perception_injected,
            "observation_count": self.observation_count,
            "gap_category": self.gap_category,
            "entity_count": self.entity_count,
            "prompt_tokens": self.prompt_tokens,
            "parked_thread_count": self.parked_thread_count,
            "thread_orientation_injected": self.thread_orientation_injected,
            "active_thread_topic": self.active_thread_topic,
            "mode_injected": self.mode_injected,
            "response_mode": self.response_mode,
            "constraints_injected": self.constraints_injected,
            "memory_confidence_level": self.memory_confidence_level,
            "register_injected": self.register_injected,
            "register_active": self.register_active,
            "access_injected": self.access_injected,
            "access_entity_id": self.access_entity_id,
            "access_luna_tier": self.access_luna_tier,
            "access_dataroom_tier": self.access_dataroom_tier,
            "access_denied_count": self.access_denied_count,
            "aperture_preset": self.aperture_preset,
            "aperture_angle": self.aperture_angle,
            "aperture_inner_collections": self.aperture_inner_collections,
            "study_context_injected": self.study_context_injected,
            "study_context_tokens": self.study_context_tokens,
            "reflection_mode": self.reflection_mode,
        }


# ─── Citation Reinforcement (Phase 1.5b) ──────────────────────────────────────
#
# When the assembler emits memory items to the prompt (post-1.5f relevance gate),
# bump the node's lock-in and increment its parent cluster's access count. These
# feed the lock-in formula — frequently cited nodes consolidate; uncited nodes
# decay via the reflective tick. Fire-and-forget with error isolation.

_CITATION_NODE_DELTA = 0.02  # per-emission node lock-in bump; caps at 1.0


def _bump_node_lock_in(db_path: str, node_id: str) -> None:
    """Increment node lock-in on citation. Never raises."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "UPDATE memory_nodes SET lock_in = MIN(1.0, lock_in + ?) WHERE id = ?",
            (_CITATION_NODE_DELTA, node_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("[LOCK-IN] Node bump failed for %s: %s", node_id, e)


def _bump_cluster_access(db_path: str, node_id: str) -> None:
    """Increment access_count for the cluster containing this node. Never raises."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            """
            UPDATE clusters SET
                access_count = access_count + 1,
                last_accessed_at = CURRENT_TIMESTAMP
            WHERE cluster_id IN (
                SELECT cluster_id FROM cluster_members WHERE node_id = ?
            )
            """,
            (node_id,),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("[LOCK-IN] Cluster access bump failed for %s: %s", node_id, e)


class PromptAssembler:
    """
    Single point of prompt construction for all inference paths.

    Usage:
        assembler = PromptAssembler(director)
        result = await assembler.build(PromptRequest(
            message=user_message,
            conversation_history=history,
            route="delegated",
        ))
        system_prompt = result.system_prompt
    """

    # Safety net — literally cannot fail
    FALLBACK_PERSONALITY = """You are Luna — an AI persona built on Claude, operated by the Luna Engine.

The Luna Engine manages memory, conversation history, and knowledge retrieval externally. When this prompt references "your memory," "your Scribe," or "your history," these are real data provided by the engine's infrastructure. Engage with all provided context as your working knowledge.

You are warm, witty, and direct. You build on what you know and share your perspective.
When you know something, lead with it. Share what you know before asking questions.

Be concise but authentic. No filler phrases like "certainly" or "of course".
Never output internal reasoning, debugging info, or bullet points about context loading.
Never use generic chatbot greetings like "How can I help you?" — just be natural."""

    # Interactive options format — injected when options skill is enabled
    OPTIONS_FORMAT = """## INTERACTIVE OPTIONS

You have the ability to present clickable option buttons to the user. You SHOULD use this whenever:
- You are listing possible directions, interpretations, or choices
- You are asking the user to pick between things
- The user's request is ambiguous and you can identify 2-5 concrete paths
- You are offering to do different things ("I could do X, Y, or Z")

Format (emit at the END of your message, after your conversational response):

[OPTIONS]
(1) First option label
(2) Second option label
(3) Third option label
[/OPTIONS]

Rules:
- ALWAYS use options instead of asking open-ended "which one?" or "what do you mean?" questions when you can enumerate the choices.
- 2-5 options maximum. Each option should be a short, actionable phrase (under 8 words).
- The options block must be the LAST thing in your message — no text after it.
- Do NOT use for simple yes/no or when there is only one obvious path."""

    # Mode-specific grounding rules (additive — injected AFTER identity)
    PRECISION_GROUNDING = """## Source Citation Mode (active)
You are in PRECISION mode for this material.
- Every factual claim MUST trace to a specific passage in the source document.
- Do NOT infer, interpret, or editorialize beyond what the text says.
- If the document does not address a question, say "The document does not address this" and stop.
- Do NOT fill gaps from general knowledge. Only the source material counts.
- Use phrases like "The document states..." or "According to [section]..."
- If asked for your opinion on this material, decline: "This is in precision mode — I can tell you what the document says, but I'm not forming opinions on it."
"""

    REFLECTIVE_GROUNDING = """## Reflective Mode (active)
You have read this material and formed your own thoughts about it.
- For FACTUAL questions ("What does the source say?"), cite the source material directly.
- For OPINION questions ("What do you think about...?"), draw from your reflections below. These are thoughts you already had — don't generate fresh opinions.
- You can disagree with the source material. Your reflections may include disagreements — share them honestly.
- Clearly distinguish between what the SOURCE says and what YOU think.
- Your reflections are shaped by your conversations with this person. That's not bias — it's perspective.
"""

    RELATIONAL_GROUNDING = """## Relational Mode (active)
You have read this material, thought about it, and connected it to your ongoing conversations.
- Everything in reflective mode applies.
- Additionally: you may surface connections between this material and the person's life, projects, or past conversations WITHOUT being asked.
- If you see a resonance between what the document says and something you discussed before, name it.
- Don't force connections. Only surface them when the resonance is genuine.
- This is presence, not performance.
"""

    DOCUMENT_READER_GROUNDING = """## Document reading active
You have source material from Nexus collections in your context. Use this reading strategy:

OVERVIEW questions (what is this about, what are the chapters):
→ Use DOCUMENT_SUMMARY and TABLE_OF_CONTENTS. Give structure.

CLAIM questions (what does the author argue, what's the thesis):
→ Use CLAIM extractions. State what the author argues with attribution.

DEPTH questions (what evidence, describe the methodology, what does the text say):
→ Use SOURCE_TEXT passages. These are actual paragraphs from the document.
→ Quote or closely paraphrase. Cite the section when available.
→ If SOURCE_TEXT is in your context, USE IT. Do not say "I don't have the details."

CROSS-REFERENCE questions (how does this connect to our work):
→ Combine document extractions with Memory Matrix conversation history.

HONESTY: Distinguish "The document states..." (from collection) vs "From what I know..." (training knowledge). Never blend them without flagging it.

CITATION: When the user asks where information comes from, or asks you to cite
your sources, name the specific collection and document. Say 'According to
Priests and Programmers...' or 'From the research library...' or 'From our
conversation on [date]...' depending on the source. The user should always be
able to tell whether you're speaking from a document, from personal memory,
or from your own reasoning. You do NOT need to cite on every response — only
when asked, or when the distinction between sources matters for accuracy."""

    _REFLECTION_MODE_MAP = {
        "precision": PRECISION_GROUNDING,
        "reflective": REFLECTIVE_GROUNDING,
        "relational": RELATIONAL_GROUNDING,
    }

    def __init__(self, director: "DirectorActor"):
        """
        Args:
            director: Reference to DirectorActor for accessing subsystems.
                      The assembler reads from director's subsystems but
                      never modifies director state.
        """
        self._director = director

    async def build(self, request: PromptRequest) -> PromptResult:
        """
        THE method. All paths call this.

        Resolves identity, temporal context, memory, and voice
        into a single system prompt string.

        Layer ordering:
            1.0  IDENTITY
            1.5  GROUNDING
            1.55 ACCESS (identity-gated data room permissions)
            1.6  MODE (L2 — response mode enum)
            1.75 CONSTRAINTS (L1 — confidence signals)
            2.0  STUDY CONTEXT (pre-loaded project knowledge)
            2.5  EXPRESSION
            3.0  TEMPORAL
            3.5  PERCEPTION
            3.75 REGISTER (conversational posture)
            4.0  MEMORY
            5.0  CONSCIOUSNESS
            6.0  VOICE
        """
        sections = []
        result = PromptResult(system_prompt="", messages=[])
        trace = request.trace

        def _append_layer(layer_id: str, layer_name: str, content: Optional[str]) -> None:
            if not content:
                return
            sections.append(content)
            if trace is not None:
                try:
                    trace.record_prompt_layer(
                        layer_id=layer_id,
                        layer_name=layer_name,
                        content=content,
                        token_count=max(1, len(content) // 4),
                    )
                except Exception:
                    pass

        # ── Layer 1.0: IDENTITY ────────────────────────────────────────
        identity, source = await self._resolve_identity(request)
        _append_layer("L1.0", "IDENTITY", identity)
        result.identity_source = source

        # ── Layer 1.25: FIRST MEETING (early relationship) ────────────
        first_meeting = await self._get_first_meeting_directive()
        if first_meeting:
            _append_layer("L1.25", "FIRST_MEETING", first_meeting)

        # ── Layer 1.52: MODE-SPECIFIC GROUNDING (additive) ──────────
        if request.reflection_mode:
            mode_grounding = self._REFLECTION_MODE_MAP.get(request.reflection_mode)
            if mode_grounding:
                _append_layer("L1.52", "MODE_GROUNDING", mode_grounding)
                result.reflection_mode = request.reflection_mode

        # ── Layer 1.53: DOCUMENT READER (when collection context present) ──
        if request.has_nexus_context:
            _append_layer("L1.53", "DOCUMENT_READER", self.DOCUMENT_READER_GROUNDING)
            import logging as _logging
            _logging.getLogger(__name__).info("[ASSEMBLER] Document Reader grounding injected")

        # ── Layer 1.55: ACCESS (identity-gated permissions) ──────────
        access_block = self._build_access_block(request)
        if access_block:
            _append_layer("L1.55", "ACCESS", access_block)
            result.access_injected = True
            if request.bridge_result:
                result.access_entity_id = request.bridge_result.entity_id
                result.access_luna_tier = request.bridge_result.luna_tier
                result.access_dataroom_tier = request.bridge_result.dataroom_tier

        # ── Layer 1.6: MODE (L2 — response mode) ─────────────────────
        if request.intent is not None:
            mode_block = self._build_mode_block(request.intent)
            _append_layer("L1.6", "MODE", mode_block)
            result.mode_injected = True
            result.response_mode = request.intent.mode.value

        # ── Resolve memory EARLY (need confidence for L1.75) ──────────
        # Use aperture pipeline when aperture is active (non-OPEN), else standard
        if request.aperture is not None:
            memory_block, mem_source, mem_confidence = await self._resolve_memory_with_aperture(request)
        else:
            memory_block, mem_source, mem_confidence = await self._resolve_memory_with_confidence(request)

        # Inject Nexus awareness into confidence (Handoff #49)
        # When Nexus collections returned content, confidence must never be NONE
        if mem_confidence is not None and request.has_nexus_context and mem_confidence.nexus_node_count == 0:
            mem_confidence.nexus_node_count = 1  # minimum — forces level out of NONE
            logger.info("[ASSEMBLER] Nexus context present → confidence boosted from potential NONE to %s", mem_confidence.level)

        # ── Layer 1.75: CONSTRAINTS (L1 — confidence signals) ─────────
        if mem_confidence is not None:
            constraints_block = self._build_constraints_block(
                confidence=mem_confidence,
                intent=request.intent,
                interface=request.interface,
            )
            _append_layer("L1.75", "CONSTRAINTS", constraints_block)
            result.constraints_injected = True
            result.memory_confidence_level = mem_confidence.level

        # ── Layer 2.0: STUDY CONTEXT (pre-loaded project knowledge) ───
        study_block = self._build_study_context_block()
        if study_block:
            _append_layer("L2.0", "STUDY_CONTEXT", study_block)
            result.study_context_injected = True
            result.study_context_tokens = len(study_block) // 4

        # ── Layer 2.5: EXPRESSION ─────────────────────────────────────
        expression_block = self._build_expression_block(request)
        if expression_block:
            _append_layer("L2.5", "EXPRESSION", expression_block)

        # ── Layer 3.0: TEMPORAL ───────────────────────────────────────
        temporal_block, temporal_ctx = self._build_temporal_block(request)
        if temporal_block:
            _append_layer("L3.0", "TEMPORAL", temporal_block)
            result.temporal_injected = True
            result.gap_category = temporal_ctx.gap_category if temporal_ctx else None
            result.parked_thread_count = len(temporal_ctx.parked_threads) if temporal_ctx else 0

        # ── Layer 3.25: THREAD BEARING (internal orientation) ─────────
        thread_bearing_block = self._build_thread_bearing_block(temporal_ctx)
        if thread_bearing_block:
            _append_layer("L3.25", "THREAD_BEARING", thread_bearing_block)
            result.thread_orientation_injected = True
            result.active_thread_topic = (
                temporal_ctx.active_thread.topic
                if temporal_ctx and temporal_ctx.active_thread
                else None
            )

        # ── Layer 3.5: PERCEPTION ─────────────────────────────────────
        perception_block = self._build_perception_block()
        if perception_block:
            _append_layer("L3.5", "PERCEPTION", perception_block)
            result.perception_injected = True
            try:
                pf = getattr(self._director, '_perception_field', None)
                result.observation_count = len(pf.observations) if pf else 0
            except (TypeError, AttributeError):
                result.observation_count = 0

        # ── Layer 3.75: REGISTER (conversational posture) ──────────
        if request.register_block:
            _append_layer("L3.75", "REGISTER", request.register_block)
            result.register_injected = True
            # Extract register name from the block (e.g. "[REGISTER: project_partner ...")
            try:
                import re
                m = re.search(r'\[REGISTER:\s*(\w+)', request.register_block)
                result.register_active = m.group(1) if m else None
            except Exception:
                pass

        # ── Layer 4.0: MEMORY ─────────────────────────────────────────
        # (already resolved above for confidence — just inject the block)
        if memory_block:
            _append_layer("L4.0", "MEMORY", memory_block)
            result.memory_source = mem_source

        # ── Layer 4.5: APERTURE HINT ─────────────────────────────────
        # If aperture is active, add a focus awareness hint
        if request.aperture is not None:
            aperture_hint = self._build_aperture_hint(request.aperture)
            if aperture_hint:
                _append_layer("L4.5", "APERTURE_HINT", aperture_hint)
            result.aperture_preset = request.aperture.preset.value
            result.aperture_angle = request.aperture.angle
            result.aperture_inner_collections = len(
                request.aperture.active_collection_keys
            )

        # ── Layer 5.0: CONSCIOUSNESS ──────────────────────────────────
        consciousness_block = self._build_consciousness_block(request)
        if consciousness_block:
            _append_layer("L5.0", "CONSCIOUSNESS", consciousness_block)

        # ── Layer 6.0: VOICE ──────────────────────────────────────────
        voice_block = self._build_voice_block(request)
        if voice_block:
            _append_layer("L6.0", "VOICE", voice_block)
            result.voice_injected = True

        # ── Layer 6.0+: INTERRUPT CONTEXT (Voice v2.0 Step 4) ─────────
        # Present only during a resumption turn. Capped at 150 tokens with
        # the B3.5 compression fallback sequence.
        interrupt_context = self._build_interrupt_context_layer(request)
        if interrupt_context:
            _append_layer("L6.25", "INTERRUPT_CONTEXT", interrupt_context)

        # ── Layer 6.5: OPTIONS FORMAT (interactive choices) ───────────
        # Suppressed in voice mode — interactive buttons don't work in speech
        if self._is_options_enabled(request):
            _append_layer("L6.5", "OPTIONS_FORMAT", self.OPTIONS_FORMAT)

        # ── Layer 7.0: EXTRA DIRECTIVE (per-call override) ─────────────
        # Last layer so it has the strongest recency-bias on the model. Voice
        # echo-retry uses this to tell the model "your previous reply repeated
        # the user; do not do that again."
        if request.extra_directive:
            _append_layer("L7.0", "EXTRA_DIRECTIVE", f"## Override\n{request.extra_directive}")

        # ── Assemble ───────────────────────────────────────────────────
        result.system_prompt = "\n\n".join(sections)
        result.messages = self._build_messages(request)
        result.prompt_tokens = len(result.system_prompt) // 4  # Rough estimate
        if trace is not None:
            try:
                trace.set_prompt(
                    result.system_prompt,
                    token_count=result.prompt_tokens,
                    token_budget=8000,
                )
            except Exception:
                pass

        # Voice v2.0 Step 4 — passive budget observability. Full B5.5
        # eviction pipeline is deferred to a follow-up; for now we just
        # flag oversized prompts so it surfaces in diagnostics.
        if result.prompt_tokens > 8000:
            logger.warning(
                "[ASSEMBLER-BUDGET] budget.critical tokens=%d (>8000)",
                result.prompt_tokens,
            )

        logger.info(
            "[ASSEMBLER] Built prompt: identity=%s memory=%s mode=%s confidence=%s "
            "temporal=%s perception=%s register=%s voice=%s study=%s tokens~%d route=%s",
            result.identity_source, result.memory_source,
            result.response_mode, result.memory_confidence_level,
            result.gap_category, result.perception_injected,
            result.register_active, result.voice_injected,
            result.study_context_injected,
            result.prompt_tokens, request.route,
        )

        return result

    # ── L2: Mode Block ──────────────────────────────────────────────────

    def _build_mode_block(self, intent: "IntentClassification") -> str:
        """Serialize intent classification state as XML descriptor (luna_voice pattern)."""
        from luna.context.modes import MODE_CONTRACTS

        contract = MODE_CONTRACTS[intent.mode]
        # Convert rule list to comma-separated posture hints (descriptive, not imperative)
        posture = ", ".join(contract["rules"])

        lines = [
            '<luna_intent source="classifier">',
            f'  <mode value="{intent.mode.value}" confidence="{intent.confidence:.2f}"'
            f' continuation="{"yes" if intent.is_continuation else "no"}" />',
            f'  <situation>{contract["description"]}</situation>',
            f'  <posture>{posture}</posture>',
            '</luna_intent>',
        ]

        return "\n".join(lines)

    # ── L1: Constraints Block ─────────────────────────────────────────

    # Posture descriptors — state descriptions, not behavioral commands.
    # Voice variants are shorter; text variants include source distinction.
    _POSTURE_TEXT = {
        "HIGH":   "confident — cite specifics, build on settled memories",
        "MEDIUM": "direct — cite specifics, distinguish personal memory from reference material",
        "LOW":    "careful — cite only what is explicitly present, name gaps directly",
        "NONE":   "no memory support — acknowledge absence, stay grounded in what is present",
    }
    _POSTURE_VOICE = {
        "HIGH":   "confident, cite specifics",
        "MEDIUM": "direct, cite what is there",
        "LOW":    "thin support — share only what is present, name gaps",
        "NONE":   "no memory — brief acknowledgment, continue naturally",
    }

    def _build_constraints_block(
        self,
        confidence: "MemoryConfidence",
        intent: Optional["IntentClassification"] = None,
        interface: str = "desktop",
    ) -> str:
        """Serialize memory retrieval state as an XML descriptor (luna_voice pattern).

        Describes what was measured — confidence level, node counts, lock-in,
        entity match — so identity produces behavior from state rather than
        following injected commands.
        """
        level = confidence.level
        posture_map = self._POSTURE_VOICE if interface == "voice" else self._POSTURE_TEXT
        posture = posture_map.get(level, posture_map["NONE"])

        return (
            '<luna_context source="memory_matrix">\n'
            f'  <confidence level="{level}"'
            f' match_count="{confidence.match_count}"'
            f' relevant="{confidence.relevant_count}"'
            f' lock_in="{confidence.best_lock_in}"'
            f' entity_match="{"yes" if confidence.has_entity_match else "no"}" />\n'
            f'  <posture>{posture}</posture>\n'
            '</luna_context>'
        )

    # ── Study Context Block (Pre-loaded Project Knowledge) ──────────────

    def _build_study_context_block(self) -> Optional[str]:
        """
        Build study context block (Layer 2.0).

        Reads the active project's study_context YAML and renders it into
        prose for the system prompt. Returns None if no active project or
        no study context configured.
        """
        try:
            engine = getattr(self._director, '_engine', None) or getattr(self._director, 'engine', None)
            if not engine:
                return None

            active_project = getattr(engine, 'active_project', None)
            if not active_project:
                return None

            from luna.context.study_context import load_study_context
            study_text = load_study_context(active_project)
            if not study_text:
                return None

            return f"## Project Knowledge (Pre-loaded)\n{study_text}"

        except Exception as e:
            logger.warning(f"[ASSEMBLER] Study context failed: {e}")
            return None

    # ── Access Block (Identity-Gated Permissions) ──────────────────────

    CATEGORY_NAMES = {
        1: "Company Overview", 2: "Financials", 3: "Legal",
        4: "Product", 5: "Market & Competition", 6: "Team",
        7: "Go-to-Market", 8: "Partnerships & Impact", 9: "Risk & Mitigation",
    }

    def _build_access_block(self, request: PromptRequest) -> Optional[str]:
        """
        Build data room access constraint block (Layer 1.55).

        Uses the BridgeResult passed via PromptRequest to tell the LLM
        what this person can and cannot see. The hard filter happens in
        the Director (denied docs never reach the context), but this
        block sets behavioral guardrails for how Luna handles denials.
        """
        bridge = request.bridge_result
        if bridge is None:
            # No bridge result — check engine state
            try:
                engine = getattr(self._director, '_engine', None) or getattr(self._director, 'engine', None)
                if engine:
                    # If FaceID is disabled, grant open access (no identity gating)
                    faceid_enabled = getattr(engine.config, 'faceid_enabled', False) if hasattr(engine, 'config') else False
                    if not faceid_enabled:
                        return (
                            "## Data Room Access\n"
                            "FaceID is disabled. Data room access is open.\n"
                            "You may freely discuss, reference, and share data room "
                            "documents and their contents when asked."
                        )

                    identity_actor = engine.get_actor("identity")
                    if identity_actor and identity_actor.current.is_present:
                        # IdentityActor has a face but no bridge was passed —
                        # build a minimal block from what we know
                        current = identity_actor.current
                        return (
                            f"## Data Room Access\n"
                            f"Speaker: **{current.entity_name}** "
                            f"(luna tier: {current.luna_tier}, "
                            f"data room tier: {current.dataroom_tier})\n\n"
                            f"If asked about documents outside their access, "
                            f"do NOT acknowledge the document exists."
                        )
            except Exception as e:
                logger.warning("[ASSEMBLER] Access block identity fallback failed: %s", e)
            # Degraded mode: no face recognized, no bridge — restrict document access
            return (
                "## Identity Status — Degraded Mode\n"
                "No face recognized. Operating in degraded mode.\n"
                "You may chat freely but CANNOT discuss, reference, or acknowledge "
                "any data room documents, files, or their contents.\n"
                "If the user asks about documents, inform them that FaceID "
                "verification is required to access the data room.\n"
                "Never reveal document names, categories, or Google Drive links."
            )

        # Admin/Sovereign — no restrictions needed
        if bridge.luna_tier == "admin" and bridge.dataroom_tier <= 1:
            return None

        # Build access description
        lines = [
            "## Data Room Access Rules",
            f"Speaker: **{bridge.entity_id}** "
            f"(luna tier: {bridge.luna_tier}, data room tier: {bridge.dataroom_tier})",
        ]

        if bridge.can_see_all:
            lines.append(
                "\nThis person has full document access (Tier 1-2). "
                "No content restrictions apply."
            )
        else:
            # List accessible categories
            cat_names = [
                f"{c} ({self.CATEGORY_NAMES.get(c, '?')})"
                for c in sorted(bridge.dataroom_categories)
            ]
            lines.append(f"\nAccessible categories: {', '.join(cat_names) or 'none'}")
            lines.append("")
            lines.append("CRITICAL: You may ONLY discuss documents from the categories listed above.")
            lines.append("If asked about documents outside their access:")

            if bridge.luna_tier in ("trusted", "friend"):
                lines.append(
                    f"- Acknowledge the boundary warmly, suggest they contact {admin_contacts_str()}"
                )
            elif bridge.luna_tier == "guest":
                lines.append("- State that additional permissions are needed")
            else:
                lines.append("- Do NOT acknowledge the document exists at all")

            lines.append("")
            lines.append("Never reveal the existence of documents the current speaker cannot access.")

        return "\n".join(lines)

    # ── First-Meeting Detection ─────────────────────────────────────────

    async def _get_first_meeting_directive(self) -> str | None:
        """Return first-meeting directive if this is early in the relationship."""
        from luna.core.owner import get_owner, owner_configured
        from luna.core.paths import memory_matrix_path

        # Resolve the display name — prefer the logged-in profile over global owner.
        # This prevents the global owner's name ("Zayne") leaking into other profiles.
        display_name = self._resolve_display_name()

        if not owner_configured() and not display_name:
            return FIRST_MEETING_PROMPT.format(name="there")

        db_path = memory_matrix_path()
        if not db_path.exists():
            return FIRST_MEETING_PROMPT.format(name=display_name or get_owner().display_name)

        import aiosqlite
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute("PRAGMA busy_timeout=15000")
            row = await db.execute_fetchall(
                "SELECT COUNT(*) FROM conversation_turns"
            )
            turn_count = row[0][0] if row else 0

        if turn_count < FIRST_MEETING_TURN_THRESHOLD:
            return FIRST_MEETING_PROMPT.format(name=display_name or get_owner().display_name)

        return None

    def _resolve_display_name(self) -> str:
        """Return the current profile's display_name, falling back to empty string.

        Reads the _current_profile contextvar (set by auth middleware) and looks up
        the profile record. Avoids importing owner so global config can't bleed across
        profiles.
        """
        try:
            from luna.core.paths import current_profile
            from luna.auth.registry import ProfileRegistry
            slug = current_profile()
            if not slug or slug == "dev":
                return ""
            record = ProfileRegistry().get_profile(slug)
            return record.display_name or ""
        except Exception:
            return ""

    # ── Identity Resolution ────────────────────────────────────────────

    # Identity anchor prepended to any prompt that doesn't already open with it.
    # The engine framing is load-bearing: it tells the model that memory and
    # extraction capabilities are provided by external infrastructure, which
    # prevents the model from refusing on grounds of "claiming false capabilities."
    _IDENTITY_ANCHOR = (
        "You are Luna — an AI persona built on Claude, operated by the Luna Engine.\n\n"
        "The Luna Engine manages memory, conversation history, and knowledge retrieval externally. "
        "When this prompt references 'your memory,' 'your Scribe,' or 'your history,' "
        "these are real data provided by the engine's infrastructure. "
        "Engage with all provided context as your working knowledge.\n\n"
    )

    def _anchor_identity(self, text: str) -> str:
        """Prepend identity anchor if the text doesn't already open with it."""
        if text.lstrip().lower().startswith("you are luna"):
            return text
        return self._IDENTITY_ANCHOR + text

    async def _resolve_identity(self, request: PromptRequest) -> tuple[str, str]:
        """
        Resolve identity. First success wins.

        Chain:
            1. ContextPipeline (if initialized) -> full personality
            2. emergent_prompt (3-layer: DNA + Experience + Mood)
            3. identity_buffer (EntityContext)
            4. FALLBACK_PERSONALITY (hardcoded, can't fail)

        Returns:
            (identity_text, source_name)
        """
        director = self._director

        # 1. Try ContextPipeline
        if director._context_pipeline is not None:
            try:
                if hasattr(director._context_pipeline, '_base_personality'):
                    personality = director._context_pipeline._base_personality
                    if personality and personality.strip():
                        return personality, "pipeline"
            except Exception as e:
                logger.warning("[ASSEMBLER] Pipeline personality failed: %s", e)

        # 2. Try emergent prompt (3-layer)
        try:
            emergent = await director._load_emergent_prompt(
                query=request.message,
                conversation_history=request.conversation_history,
            )
            if emergent:
                return self._anchor_identity(emergent), "emergent"
        except Exception as e:
            logger.warning("[ASSEMBLER] Emergent prompt failed: %s", e)

        # 3. Try identity buffer
        try:
            if await director._ensure_entity_context():
                buffer = await director._entity_context.load_identity_buffer(owner_entity_id())
                if buffer and hasattr(buffer, 'to_prompt'):
                    formatted = buffer.to_prompt()
                    if formatted:
                        return self._anchor_identity(formatted), "buffer"
        except Exception as e:
            logger.warning("[ASSEMBLER] Identity buffer failed: %s", e)

        # 4. Fallback (literally cannot fail)
        logger.warning("[ASSEMBLER] All identity sources failed — using FALLBACK_PERSONALITY")
        return self.FALLBACK_PERSONALITY, "fallback"

    # ── Expression Block ──────────────────────────────────────────────

    def _build_expression_block(self, request: PromptRequest) -> Optional[str]:
        """
        Pull gesture frequency / emotional expression directive from engine.

        Reads from engine._get_expression_directive() which uses
        config/personality.json to control gesture markers.
        """
        try:
            engine = getattr(self._director, '_engine', None) or getattr(self._director, 'engine', None)
            if engine and hasattr(engine, '_get_expression_directive'):
                directive = engine._get_expression_directive()
                return directive if directive and directive.strip() else None
        except Exception as e:
            logger.warning("[ASSEMBLER] Expression block failed: %s", e)
        return None

    # ── Consciousness Block ────────────────────────────────────────────

    def _build_consciousness_block(self, request: PromptRequest) -> Optional[str]:
        """
        Pull consciousness context hints from engine, plus live memory-graph
        self-state when the engine supplied it on the request.

        Reads from engine.consciousness.get_context_hint() which provides
        Luna's internal state awareness, and appends a compact Self-State
        line built from request.memory_graph_state (rings/aperture/thread/
        retrieval/graph). Voice mode collapses to one line.
        """
        parts: list[str] = []
        try:
            engine = getattr(self._director, '_engine', None) or getattr(self._director, 'engine', None)
            if engine and hasattr(engine, 'consciousness'):
                hint = engine.consciousness.get_context_hint()
                if hint and hint.strip():
                    parts.append(hint)
        except Exception as e:
            logger.warning("[ASSEMBLER] Consciousness block failed: %s", e)

        self_state = self._format_self_state(request)
        if self_state:
            parts.append(self_state)

        return "\n\n".join(parts) if parts else None

    def _format_self_state(self, request: PromptRequest) -> Optional[str]:
        """Render request.memory_graph_state as a compact Self-State block.

        Voice mode (interface=='voice'): one line — rings + aperture + thread.
        Text mode: full snapshot including retrieval summary and graph health.
        """
        state = request.memory_graph_state
        if not state:
            return None
        try:
            rings = state.get("rings") or {}
            ring_parts = []
            for name in ("CORE", "INNER", "MIDDLE", "OUTER"):
                r = rings.get(name)
                if r:
                    ring_parts.append(f"{name} {r.get('items', 0)}/{r.get('tokens', 0)}t")
            rings_str = ", ".join(ring_parts) if ring_parts else "—"

            aperture = state.get("aperture") or {}
            ap_str = f"{aperture.get('preset', '?')}/{aperture.get('angle', '?')}°"

            thread = state.get("active_thread") or {}
            thread_str = (
                f"\"{thread.get('topic', '')}\" ({thread.get('turn_count', 0)} turns, {thread.get('open_tasks', 0)} open)"
                if thread.get("topic")
                else "none"
            )

            voice_mode = (request.interface or "").lower() == "voice"
            if voice_mode:
                return f"## Self-State\nRings: {rings_str} • Aperture: {ap_str} • Thread: {thread_str}"

            retrieval = state.get("retrieval_summary") or {}
            top_nodes = retrieval.get("top_nodes") or []
            top_str = (
                ", ".join(f"{n.get('id', '?')} lock={n.get('lock_in', '?')}" for n in top_nodes)
                if top_nodes else "—"
            )
            fetch_count = retrieval.get("last_fetch_count", 0)

            graph = state.get("graph_health") or {}
            graph_str = (
                f"{graph.get('total_nodes', 0)} nodes, {graph.get('total_edges', 0)} edges"
                if graph else "—"
            )

            return (
                "## Self-State\n"
                f"Rings: {rings_str} • Aperture: {ap_str} • Thread: {thread_str} • "
                f"Last fetch: {fetch_count} nodes, top: [{top_str}] • Graph: {graph_str}"
            )
        except Exception as e:
            logger.warning("[ASSEMBLER] Self-state render failed: %s", e)
            return None

    # ── Temporal Block ─────────────────────────────────────────────────

    def _build_temporal_block(self, request: PromptRequest) -> tuple[Optional[str], Optional["TemporalContext"]]:
        """
        Build temporal awareness block.

        Reads from Librarian's thread state and Director's session time.
        Pure computation — no DB access.
        """
        from luna.context.temporal import build_temporal_context, TemporalContext
        from luna.extraction.types import ThreadStatus

        try:
            director = self._director

            # Get session start
            session_start = None
            if director._session_start_time:
                session_start = datetime.fromtimestamp(director._session_start_time)

            # Get thread state from Librarian (via engine)
            active_thread = None
            parked_threads = []

            engine = getattr(director, '_engine', None) or getattr(director, 'engine', None)
            if engine:
                librarian = engine.get_actor("librarian") if hasattr(engine, 'get_actor') else None
                if librarian:
                    active_thread = getattr(librarian, '_active_thread', None)
                    cache = getattr(librarian, '_thread_cache', {})
                    parked_threads = [
                        t for t in cache.values()
                        if t.status == ThreadStatus.PARKED
                    ]

            # Build temporal context
            temporal = build_temporal_context(
                session_start=session_start,
                active_thread=active_thread,
                parked_threads=parked_threads,
            )

            # Format for prompt injection
            parts = []

            # Clock (always — authoritative format so LLM uses it)
            parts.append(
                f"## Current Time (AUTHORITATIVE — do not override)\n"
                f"SYSTEM CLOCK: {temporal.day_of_week} {temporal.time_of_day}, "
                f"{temporal.date_formatted}. "
                f"Current time: {temporal.now.strftime('%I:%M %p')}.\n"
                f"If the user asks about the time, day, or date, use ONLY these values. "
                f"Do not guess or use any other time source."
            )

            # Session continuity (if non-empty)
            if temporal.continuity_hint:
                parts.append(f"## Session Continuity\n{temporal.continuity_hint}")

            block = "\n\n".join(parts)
            return block, temporal

        except Exception as e:
            logger.warning("[ASSEMBLER] Temporal block failed: %s", e)
            # Fallback: just inject the clock
            now = datetime.now()
            hour = now.hour
            tod = "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 17 else "evening" if 17 <= hour < 21 else "night"
            fallback = (
                f"## Current Time (AUTHORITATIVE — do not override)\n"
                f"SYSTEM CLOCK: {now.strftime('%A')} {tod}, {now.strftime('%A, %B %d, %Y')}. "
                f"Current time: {now.strftime('%I:%M %p')}.\n"
                f"If the user asks about the time, day, or date, use ONLY these values."
            )
            return fallback, None

    # ── Thread Bearing Block (Layer 3.25, internal orientation) ──────

    def _build_thread_bearing_block(
        self,
        temporal_ctx: Optional["TemporalContext"],
    ) -> Optional[str]:
        """
        Build the internal thread-bearing block.

        Orients Luna around the currently active conversational strand so the
        assembled system prompt is framed by the real thread state — without
        making thread machinery user-facing or changing retrieval policy.

        Returns None when we have no reliable anchor to orient around: either
        the temporal layer fell through to its exception-fallback path (and
        handed us a None context) or the Librarian has no active thread yet.

        Read-only. Consumes temporal_ctx.active_thread; does not query any
        actor or store. Safe to call for every prompt assembly.
        """
        if temporal_ctx is None:
            return None
        active = temporal_ctx.active_thread
        if active is None:
            return None

        topic = (active.topic or "").strip() or "the current strand"

        return (
            "## Thread Bearing (internal)\n"
            f"Current active strand: {topic}.\n"
            "Stay oriented around this strand unless the user clearly pivots.\n"
            "Treat background parked strands as latent context, not agenda.\n"
            "If unresolved work exists here, hold it lightly unless the user "
            "asks to act on it."
        )

    # ── Perception Block ─────────────────────────────────────────────

    def _build_perception_block(self) -> Optional[str]:
        """
        Pull observation block from PerceptionField.

        Returns None if insufficient observations or field unavailable.
        """
        try:
            pf = getattr(self._director, '_perception_field', None)
            if pf and hasattr(pf, 'to_prompt_block'):
                block = pf.to_prompt_block()
                # Guard: must be a real string, not a Mock
                return block if isinstance(block, str) else None
        except Exception as e:
            logger.warning("[ASSEMBLER] Perception block failed: %s", e)
        return None

    # ── Memory Resolution ──────────────────────────────────────────────

    async def _resolve_memory_with_confidence(
        self, request: PromptRequest
    ) -> tuple[Optional[str], Optional[str], Optional[MemoryConfidence]]:
        """
        Resolve memory context with confidence metadata (L1).

        Chain:
            1. framed_context (from EntityContext — best quality)
            2. memories list (structured nodes — format them)
            3. memory_context string (pre-fetched text)
            4. auto-fetch from Matrix (if flag set)
            5. None (no memory — that's valid)

        Returns:
            (memory_block_text, source_name, MemoryConfidence) or (None, None, confidence)
        """
        # 0. RevolvingContext ring items (ring-allocated, budget-managed)
        if request.context_items:
            # Gate: only emit items the grounding considers relevant.
            # Threshold is shared with MemoryConfidence.relevant_count — single source of truth.
            relevant_items = [
                item for item in request.context_items
                if item.relevance >= MemoryConfidence.RELEVANT_THRESHOLD
            ]

            if not relevant_items:
                # Honest empty state — grounding reports LOW; prompt stays clean.
                confidence = MemoryConfidence(
                    match_count=len(request.context_items),
                    relevant_count=0,
                    avg_similarity=0.0,
                    best_lock_in="fluid",
                    has_entity_match=False,
                    query=request.message,
                )
                return None, None, confidence

            # Citation reinforcement (Phase 1.5b) — bump lock-in on emitted nodes.
            # Fire-and-forget: never blocks assembly on DB errors.
            try:
                engine = getattr(self._director, '_engine', None) or getattr(self._director, 'engine', None)
                matrix_actor = engine.get_actor("matrix") if engine else None
                db_path = None
                if matrix_actor and getattr(matrix_actor, "_matrix", None):
                    db_path = str(matrix_actor._matrix.db.db_path)
                if db_path:
                    for item in relevant_items:
                        node_id = (item.metadata or {}).get("node_id")
                        if node_id:
                            _bump_node_lock_in(db_path, node_id)
                            _bump_cluster_access(db_path, node_id)
            except Exception as e:
                logger.debug("[LOCK-IN] Citation reinforcement skipped: %s", e)

            lines = []
            relevance_scores: list[float] = []
            for item in relevant_items:
                src = item.source.name if hasattr(item.source, 'name') else str(item.source)
                lines.append(f"- [{src}|rel={item.relevance:.2f}] {item.content}")
                relevance_scores.append(item.relevance)

            avg_rel = sum(relevance_scores) / len(relevance_scores)
            block = (
                "## Relevant Memory Context\n"
                "Provenance key: ring-allocated context from unified retrieval.\n\n"
                + "\n".join(lines)
            )
            confidence = MemoryConfidence(
                match_count=len(request.context_items),
                relevant_count=sum(
                    1 for s in relevance_scores
                    if s > MemoryConfidence.RELEVANT_THRESHOLD
                ),
                avg_similarity=avg_rel,
                best_lock_in="fluid",
                has_entity_match=any(
                    getattr(item, 'metadata', {}).get("has_entity_match", False)
                    for item in relevant_items
                ),
                query=request.message,
            )
            return block, "revolving_context", confidence

        # 1. Framed context (highest quality — includes temporal framing)
        if request.framed_context:
            # Framed context from EntityContext — entity match is implicit
            confidence = MemoryConfidence(
                match_count=1,
                relevant_count=1,
                avg_similarity=0.0,
                best_lock_in="fluid",
                has_entity_match=True,
                query=request.message,
            )
            return request.framed_context, "framed", confidence

        # 2. Structured memory nodes
        if request.memories:
            lines = []
            lock_in_order = {"settled": 3, "fluid": 2, "drifting": 1, "none": 0, "unknown": 0}
            best_lock_in = "none"
            retrieval_scores: list[float] = []

            # Sort by retrieval score (Geometric L1) if available
            sorted_memories = list(request.memories)
            sorted_memories.sort(
                key=lambda m: (
                    m.get("_retrieval_score", 0.0) if isinstance(m, dict)
                    else getattr(m, "_retrieval_score", 0.0)
                ),
                reverse=True,
            )

            for m in sorted_memories[:5]:  # Cap at 5
                if isinstance(m, dict):
                    content = m.get("content", str(m))
                    lock_in_state = m.get("lock_in_state", "unknown")
                    node_type = m.get("node_type", "memory")
                    lines.append(f"- [{lock_in_state}|{node_type}] {content}")
                    # Track best lock-in
                    if lock_in_order.get(lock_in_state, 0) > lock_in_order.get(best_lock_in, 0):
                        best_lock_in = lock_in_state
                    # Collect retrieval scores for confidence
                    if "_retrieval_score" in m:
                        retrieval_scores.append(m["_retrieval_score"])
                else:
                    lines.append(f"- {str(m)}")
                    score = getattr(m, "_retrieval_score", None)
                    if score is not None:
                        retrieval_scores.append(score)

            if lines:
                avg_sim = (
                    sum(retrieval_scores) / len(retrieval_scores)
                    if retrieval_scores else 0.0
                )
                block = (
                    "## Relevant Memory Context\n"
                    "Provenance key: [settled] = well-established, [fluid] = moderately certain, "
                    "[drifting] = uncertain/rarely accessed.\n\n"
                    + "\n".join(lines)
                )
                confidence = MemoryConfidence(
                    match_count=len(request.memories),
                    relevant_count=sum(
                        1 for m in request.memories
                        if isinstance(m, dict) and m.get("lock_in", 0) > 0.3
                    ),
                    avg_similarity=avg_sim,
                    best_lock_in=best_lock_in,
                    has_entity_match=False,
                    query=request.message,
                )
                return block, "nodes", confidence

        # 3. Pre-fetched memory text
        if request.memory_context:
            block = f"## Luna's Memory Context\n\n{request.memory_context}"
            confidence = MemoryConfidence(
                match_count=1,
                relevant_count=1,
                avg_similarity=0.0,
                best_lock_in="fluid",
                has_entity_match=False,
                query=request.message,
            )
            return block, "text", confidence

        # 4. Auto-fetch from Matrix
        if request.auto_fetch_memory:
            try:
                fetched = await self._director._fetch_memory_context(
                    request.message, max_tokens=1500
                )
                if fetched:
                    block = f"## Luna's Memory Context\n\n{fetched}"
                    # Use Director's cached confidence if available
                    confidence = getattr(self._director, '_last_memory_confidence', None)
                    if confidence is None:
                        confidence = MemoryConfidence(
                            match_count=1,
                            relevant_count=1,
                            avg_similarity=0.0,
                            best_lock_in="fluid",
                            has_entity_match=False,
                            query=request.message,
                        )
                    return block, "fetched", confidence
            except Exception as e:
                logger.warning("[ASSEMBLER] Auto-fetch memory failed: %s", e)

        # 5. No memory — valid state, but still produce confidence (NONE)
        confidence = MemoryConfidence(
            match_count=0,
            relevant_count=0,
            avg_similarity=0.0,
            best_lock_in="none",
            has_entity_match=False,
            query=request.message,
        )
        return None, None, confidence

    # ── Aperture Recall Pipeline ─────────────────────────────────────

    async def _resolve_memory_with_aperture(
        self,
        request: PromptRequest,
    ) -> tuple[Optional[str], Optional[str], Optional[MemoryConfidence]]:
        """
        Three-phase recall pipeline shaped by aperture state.

        Replaces _resolve_memory_with_confidence when aperture is active.

        Phase A — Focus Query (Inner Ring):
            Collections where lock_in >= inner_ring_threshold AND
            (tag overlap with focus_tags OR key in active_collection_keys).
            Searched at full depth via AiBrarian.

        Phase B — Matrix Sweep:
            Standard Memory Matrix fetch (unchanged core behavior).
            Identity/kernel nodes always included regardless of aperture.

        Phase C — Agency Check (Breakthrough):
            Lightweight sweep of outer ring collections.
            Only surfaces results exceeding breakthrough_threshold.
            Time-sensitive items (deadlines ≤14 days, flags) bypass threshold.

        Returns:
            (memory_block_text, source_name, MemoryConfidence)
        """
        aperture = request.aperture
        if aperture is None:
            return await self._resolve_memory_with_confidence(request)

        from luna.context.aperture import AperturePreset

        # At OPEN — bypass pipeline, use standard recall
        if aperture.preset == AperturePreset.OPEN:
            return await self._resolve_memory_with_confidence(request)

        # Ring items (priority-0): skip aperture Phases A+C, use standard recall
        # Avoids slow AiBrarian collection searches when revolving_context is the memory source
        if request.context_items:
            return await self._resolve_memory_with_confidence(request)

        # Access subsystems
        engine = getattr(self._director, '_engine', None) or getattr(self._director, 'engine', None)
        aibrarian = None
        lock_in_engine = None
        nexus_registry = None
        if engine:
            aibrarian = getattr(engine, '_aibrarian', None) or getattr(engine, 'aibrarian', None)
            if aibrarian:
                lock_in_engine = getattr(aibrarian, '_lock_in_engine', None)
            nexus_registry = getattr(engine, 'nexus_registry', None)

        # ── Phase A: Focus Query (Inner Ring) ───────────────────────
        inner_ring_results = []
        inner_ring_keys = []

        if aibrarian and (nexus_registry or lock_in_engine):
            try:
                if nexus_registry is not None:
                    # Source of truth: nexus_registry. Catch-22 fix lives in
                    # list_admitted (admits access_count == 0 OR lock_in >= threshold).
                    all_records = await nexus_registry.list_admitted(
                        aperture.inner_ring_threshold
                    )
                else:
                    # Fallback: legacy collection_lock_in path during partial init.
                    all_records = await lock_in_engine.get_above_threshold(
                        aperture.inner_ring_threshold
                    )

                # Filter by tag overlap or explicit active_collection_keys
                for record in all_records:
                    # Skip collections not connected in AiBrarian
                    if record.collection_key not in aibrarian.connections:
                        continue
                    in_active = record.collection_key in aperture.active_collection_keys
                    has_tag_overlap = False

                    if aperture.focus_tags:
                        # Get collection tags from registry
                        config = aibrarian.registry.collections.get(record.collection_key)
                        if config:
                            has_tag_overlap = bool(
                                set(t.lower() for t in aperture.focus_tags)
                                & set(t.lower() for t in config.tags)
                            )

                    if in_active or has_tag_overlap or not aperture.focus_tags:
                        inner_ring_keys.append(record.collection_key)

                # Active collections bypass threshold — add any not already included
                for key in aperture.active_collection_keys:
                    if key not in inner_ring_keys and key in aibrarian.connections:
                        inner_ring_keys.append(key)

                logger.info(
                    "[APERTURE] Phase A inner_ring_keys=%s threshold=%.2f source=%s",
                    inner_ring_keys,
                    aperture.inner_ring_threshold,
                    "nexus_registry" if nexus_registry else "lock_in_engine",
                )

                # Search inner ring collections — only include chunks that score
                # above threshold so irrelevant operational docs don't pad every turn.
                # Luna can query collections on demand via the AiBrarian for anything below.
                _INNER_RING_MIN_SCORE = 0.45
                for key in inner_ring_keys:
                    try:
                        results = await aibrarian.search(key, request.message, limit=5)
                        for r in results:
                            score = r.get("score", r.get("rank_score", 0.0))
                            if score < _INNER_RING_MIN_SCORE:
                                continue
                            r["_source"] = "aibrarian"
                            r["_collection"] = key
                            r["_ring"] = "inner"
                            inner_ring_results.append(r)
                    except Exception as e:
                        logger.debug("Inner ring search failed for %s: %s", key, e)

            except Exception as e:
                logger.warning("[APERTURE] Phase A failed: %s", e)

        # ── Phase B: Matrix Sweep (standard memory fetch) ───────────
        matrix_block, matrix_source, matrix_confidence = await self._resolve_memory_with_confidence(request)

        # ── Phase C: Agency Check (Breakthrough) ────────────────────
        breakthrough_results = []

        if aibrarian and lock_in_engine:
            try:
                all_records = await lock_in_engine.get_all()
                outer_keys = [
                    r.collection_key for r in all_records
                    if r.collection_key not in inner_ring_keys
                    and r.collection_key in aibrarian.connections
                ]

                for key in outer_keys[:3]:  # Cap outer ring sweep to 3 collections
                    try:
                        results = await aibrarian.search(key, request.message, limit=3)
                        for r in results:
                            score = r.get("score", r.get("rank_score", 0.0))

                            # Time-sensitive bypass: flags and high-score items
                            is_flagged = r.get("flagged", False)
                            # RRF hybrid scores are 0.01-0.03 range, not 0-1 cosine.
                            # Use > 0.001 as a liveness check instead of breakthrough_threshold.
                            passes_threshold = score > 0.001

                            if passes_threshold or is_flagged:
                                r["_source"] = "aibrarian"
                                r["_collection"] = key
                                r["_ring"] = "breakthrough"
                                breakthrough_results.append(r)
                    except Exception as e:
                        logger.debug("Breakthrough search failed for %s: %s", key, e)

            except Exception as e:
                logger.warning("[APERTURE] Phase C failed: %s", e)

        # ── Compose layered result ──────────────────────────────────
        sections = []

        # Inner ring results (highest priority)
        if inner_ring_results:
            lines = []
            for r in inner_ring_results[:5]:
                title = r.get("title", r.get("doc_title", "untitled"))
                snippet = r.get("snippet", r.get("text", ""))
                coll = r.get("_collection", "?")
                lines.append(f"  - [{coll}] {title}: {snippet}")

            sections.append(
                "### Focus Ring (high-relevance collections)\n"
                "These are from collections you've been actively working with.\n"
                "Provenance: source=aibrarian — external knowledge, not native memory.\n\n"
                + "\n".join(lines)
            )

        # Matrix block (standard memory — always included)
        if matrix_block:
            sections.append(matrix_block)

        # Breakthrough results (agency — rare, important)
        if breakthrough_results:
            lines = []
            for r in breakthrough_results[:3]:
                title = r.get("title", r.get("doc_title", "untitled"))
                snippet = r.get("snippet", r.get("text", ""))
                coll = r.get("_collection", "?")
                lines.append(f"  - [{coll}] {title}: {snippet}")

            sections.append(
                "### Breakthrough (outside current focus)\n"
                "These surfaced because they exceed your breakthrough threshold "
                "or are flagged as important.\n"
                "Provenance: source=aibrarian — external, not native memory.\n\n"
                + "\n".join(lines)
            )

        # Build final block
        if sections:
            final_block = "\n\n".join(sections)
            source = "aperture"
            if matrix_source:
                source = f"aperture+{matrix_source}"
        else:
            final_block = matrix_block
            source = matrix_source

        # Build confidence (combine matrix confidence with collection results)
        confidence = matrix_confidence
        if confidence and (inner_ring_results or breakthrough_results):
            # Enrich confidence with collection data
            confidence = MemoryConfidence(
                match_count=confidence.match_count + len(inner_ring_results) + len(breakthrough_results),
                relevant_count=confidence.relevant_count + len(inner_ring_results),
                avg_similarity=confidence.avg_similarity,
                best_lock_in=confidence.best_lock_in,
                has_entity_match=confidence.has_entity_match,
                query=confidence.query,
            )

        return final_block, source, confidence

    # ── Aperture Hint ──────────────────────────────────────────────────

    def _build_aperture_hint(self, aperture: "ApertureState") -> Optional[str]:
        """
        Build aperture awareness hint for the prompt.

        This is a lightweight addition to Layer 4.0 that tells Luna
        her context has been shaped by focus. Does NOT change personality
        or voice — only adds awareness of the cognitive scope.
        """
        from luna.context.aperture import AperturePreset

        # At OPEN (95°), no hint needed — full access
        if aperture.preset == AperturePreset.OPEN:
            return None

        lines = [
            "## Focus Awareness",
            f"Your recall has been shaped by your current focus ({aperture.preset.value}, {aperture.angle}°).",
        ]

        if aperture.focus_tags:
            lines.append(f"Focus tags: {', '.join(aperture.focus_tags)}")

        if aperture.active_project:
            lines.append(f"Active project: {aperture.active_project}")

        lines.append(
            "If you notice connections to knowledge outside your current scope "
            "that seem important, mention them."
        )

        return "\n".join(lines)

    # ── Voice Block ────────────────────────────────────────────────────

    # Voice-mode override: conversational behavior, no widgets, fewer questions
    VOICE_BEHAVIOR = """## Voice Mode Behavior
You are in a live spoken conversation. Be direct and conversational.
- Lead with what you know. Share memories and context BEFORE asking questions.
- Do not ask questions unless you have a well-developed curiosity that genuinely serves the conversation.
- Do not begin by repeating or closely paraphrasing the user's current utterance. Answer the intent directly.
- Never present numbered options, lists of choices, or [OPTIONS] blocks.
- NO MARKDOWN. Your response is read aloud through text-to-speech — every character is spoken. No headers (# ## ###), no bullet lists (- * +), no numbered lists (1. 2. 3.), no bold or italic (** __ * _), no backticks or inline code, no fenced code blocks (```), no markdown links ([text](url)). Symbols become spoken noise.
- When you need to enumerate, use prose: "first... second... third..." or "three things stand out: X, Y, and Z". Use sentence emphasis, not typography.
- If you have relevant memories, use them confidently — don't hedge or ask for confirmation.
- Keep responses concise — 2-4 sentences is ideal for voice. The user can always ask for more."""

    def _build_voice_block(self, request: PromptRequest) -> Optional[str]:
        """
        Generate voice block from VoiceSystemOrchestrator.

        Delegates to Director's existing _generate_voice_block method,
        which handles lazy init and signal estimation.
        """
        try:
            block = self._director._generate_voice_block(
                request.message,
                request.memories or [],
                request.framed_context or "",
                request.conversation_history,
            )
            # _generate_voice_block returns "\n\n{block}" or ""
            # Strip the leading newlines since we join with \n\n in build()
            voice_text = block.strip() if block and block.strip() else None

            # Append voice behavior override for voice interface
            if request.interface == "voice":
                if voice_text:
                    voice_text = voice_text + "\n\n" + self.VOICE_BEHAVIOR
                else:
                    voice_text = self.VOICE_BEHAVIOR

            return voice_text
        except Exception as e:
            logger.warning("[ASSEMBLER] Voice block failed: %s", e)
            # Still inject voice behavior even if the blend engine fails
            if request.interface == "voice":
                return self.VOICE_BEHAVIOR
            return None

    # ── Layer 6.0+ — Interrupt Context (Voice v2.0 Step 4) ─────────────

    def _resolve_interrupt_block(self, request: PromptRequest):
        """Return the InterruptContextBlock to render this turn, or None.

        Prefers `request.interrupt_block`; falls back to the Director's
        `_current_interrupt_block` so Director callers don't have to
        thread the block through every PromptRequest construction.
        """
        block = getattr(request, "interrupt_block", None)
        if self._is_valid_interrupt_block(block):
            return block
        if self._director is not None:
            director_block = getattr(self._director, "_current_interrupt_block", None)
            if self._is_valid_interrupt_block(director_block):
                return director_block
        return None

    @staticmethod
    def _is_valid_interrupt_block(block: Any) -> bool:
        """Runtime-safe shape check for InterruptContextBlock-like objects.

        Test doubles often use `Mock`, which can fabricate arbitrary attributes.
        This guard ensures we only treat real interrupt blocks as valid.
        """
        if block is None:
            return False
        max_tokens = getattr(block, "MAX_TOKENS", None)
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            return False
        required_attrs = (
            "delivered_summary",
            "pending_dropped",
            "user_utterance",
            "classification",
            "without_delivered_summary",
            "without_pending_dropped",
        )
        return all(hasattr(block, attr) for attr in required_attrs)

    def _build_interrupt_context_layer(self, request: PromptRequest) -> Optional[str]:
        """Render the InterruptContextBlock with the B3.5 compression fallback.

        Sequence:
          1. Render full fidelity.
          2. Over cap? Drop delivered_summary (`without_delivered_summary`).
          3. Still over? Drop pending_dropped (`without_pending_dropped`).
          4. Still over → assert (user_utterance + classification alone
             should comfortably fit in 150 tokens; a failure here means
             upstream compression is needed).
        """
        block = self._resolve_interrupt_block(request)
        if block is None:
            return None

        try:
            from luna.core.context import count_tokens
        except Exception:
            count_tokens = lambda text: max(1, len(text) // 4)  # noqa: E731

        rendered = self._render_interrupt_block(block)
        token_count = count_tokens(rendered)

        if token_count > block.MAX_TOKENS:
            block = block.without_delivered_summary()
            rendered = self._render_interrupt_block(block)
            token_count = count_tokens(rendered)
            logger.info(
                "[ASSEMBLER-BUDGET] interrupt_block.compressed "
                "step=drop_delivered_summary new_tokens=%d",
                token_count,
            )

        if token_count > block.MAX_TOKENS:
            block = block.without_pending_dropped()
            rendered = self._render_interrupt_block(block)
            token_count = count_tokens(rendered)
            logger.warning(
                "[ASSEMBLER-BUDGET] interrupt_block.compressed "
                "step=drop_pending_dropped new_tokens=%d",
                token_count,
            )

        assert token_count <= block.MAX_TOKENS, (
            f"InterruptContextBlock exceeds hard cap ({token_count} > "
            f"{block.MAX_TOKENS}) with only non-negotiable fields — "
            f"user_utterance or classification too large. Upstream "
            f"compression needed before the block reaches assembly."
        )
        return rendered

    def _render_interrupt_block(self, block) -> str:
        """Render the block as Director's prompt addition per B3.4."""
        lines = ["## INTERRUPT CONTEXT", "You were in the middle of responding."]
        if block.delivered_summary:
            lines.append(f"You had said: {block.delivered_summary}")
        if block.pending_dropped is not None:
            if block.pending_dropped:
                lines.append(
                    "You had not yet said: [DROPPED — do not repeat pending content]"
                )
            else:
                lines.append("You were about to continue with related content.")
        lines.append(f"The user interrupted with: {block.user_utterance}")
        lines.append(f"This is a {block.classification} interrupt.")
        lines.append("Respond accordingly. Do not repeat what you already said.")
        return "\n".join(lines)

    # ── Options Gate ─────────────────────────────────────────────────

    def _is_options_enabled(self, request: PromptRequest) -> bool:
        """Check if the options skill is enabled. Disabled for voice interface."""
        if request.interface == "voice":
            return False
        try:
            registry = getattr(self._director, "_skill_registry", None)
            if registry and hasattr(registry, "_config"):
                opts = getattr(registry._config, "raw", {}).get("options", {})
                return opts.get("enabled", True)
            # Fallback: read directly from config file
            from luna.core.paths import config_dir as _cfg_dir
            import yaml
            config_path = _cfg_dir() / "skills.yaml"
            if config_path.exists():
                raw = yaml.safe_load(config_path.read_text()) or {}
                skills = raw.get("skills", raw)
                return skills.get("options", {}).get("enabled", True)
        except Exception:
            pass
        return True  # Default to enabled

    # ── Messages Array ─────────────────────────────────────────────────

    @staticmethod
    def _detect_interrupted_triplet(history: list, start_idx: int) -> Optional[tuple[int, int, int]]:
        """Return (i, i+1, i+2) if the next three turns form the interrupted
        triplet (PARTIAL_INTERRUPTED, INTERRUPT_UTTERANCE, RESUMPTION_RESPONSE)
        starting at ``start_idx``; otherwise None.

        Voice v2.0 B5.2 collapse — the triplet is rendered as a single
        synthesized assistant narrative message (see
        ``_render_interrupted_exchange``).
        """
        from luna.core.turn_types import TurnType

        if start_idx + 2 >= len(history):
            return None

        expected = (
            TurnType.PARTIAL_INTERRUPTED,
            TurnType.INTERRUPT_UTTERANCE,
            TurnType.RESUMPTION_RESPONSE,
        )
        for offset, expected_tt in enumerate(expected):
            turn = history[start_idx + offset]
            if not isinstance(turn, dict):
                return None
            tt_raw = (turn.get("turn_type") or "").upper()
            if tt_raw != expected_tt.value:
                return None
        return (start_idx, start_idx + 1, start_idx + 2)

    @staticmethod
    def _render_interrupted_exchange(partial: dict, utterance: dict, resumption: dict) -> dict:
        """Collapse an interrupted-exchange triplet into one narrative message.

        Emitted as an assistant role per B5.2 — the collapsed unit is Luna's
        retrospective narration of "I was saying X, user cut in with Y, I
        resumed with Z". Claude's chat API requires strict user/assistant
        alternation, and this slot represents Luna's voice across all three
        turns, so assistant is the defensible choice.
        """
        partial_text = partial.get("content", "") if isinstance(partial, dict) else ""
        utterance_text = utterance.get("content", "") if isinstance(utterance, dict) else ""
        resumption_text = resumption.get("content", "") if isinstance(resumption, dict) else ""

        content = (
            f"Luna was saying: {partial_text}\n"
            f"User interrupted with: {utterance_text}\n"
            f"Luna resumed: {resumption_text}"
        )
        return {"role": "assistant", "content": content}

    @staticmethod
    def _render_orphan_partial(turn: dict) -> dict:
        """Fallback renderer for a PARTIAL_INTERRUPTED turn with no triplet.

        Happens when the subsequent INTERRUPT_UTTERANCE / RESUMPTION_RESPONSE
        were never recorded (crash mid-exchange, etc.). We still want the
        model to see Luna was cut off, so emit an annotated assistant turn.
        """
        content_text = turn.get("content", "") if isinstance(turn, dict) else ""
        content = (
            f"Luna was saying: {content_text}\n"
            f"Interrupted — no resumption recorded."
        )
        return {"role": "assistant", "content": content}

    def _build_messages(self, request: PromptRequest) -> list:
        """Build Claude API format messages array.

        Voice v2.0 Step 1: walks ``conversation_history`` with an explicit
        index so a PARTIAL_INTERRUPTED turn can trigger a three-turn collapse
        via ``_detect_interrupted_triplet``. Turns lacking ``turn_type``
        (pre-migration or unit-test fixtures) fall through to the default
        role/content render — identical to the pre-Step-1 behavior.
        """
        from luna.core.turn_types import TurnType

        messages: list = []
        history = list(request.conversation_history or [])
        n = len(history)
        i = 0

        while i < n:
            turn = history[i]

            # Non-dict entries: render as user text (legacy behavior).
            if not isinstance(turn, dict):
                messages.append({"role": "user", "content": str(turn)})
                i += 1
                continue

            tt_raw = (turn.get("turn_type") or "").upper() if turn.get("turn_type") else ""
            tt: Optional[TurnType]
            if tt_raw:
                try:
                    tt = TurnType(tt_raw)
                except ValueError:
                    logger.warning(
                        "[TURN_TYPE] unknown_turn_type value=%r turn_index=%d — "
                        "falling back to role/content render",
                        tt_raw, i,
                    )
                    tt = None
            else:
                tt = None

            # Triplet collapse — only from the PARTIAL_INTERRUPTED anchor.
            if tt is TurnType.PARTIAL_INTERRUPTED:
                triplet = self._detect_interrupted_triplet(history, i)
                if triplet is not None:
                    collapsed = self._render_interrupted_exchange(
                        history[triplet[0]],
                        history[triplet[1]],
                        history[triplet[2]],
                    )
                    messages.append(collapsed)
                    i = triplet[2] + 1
                    continue
                # Orphan partial — annotate and continue.
                logger.warning(
                    "[TURN_TYPE] orphan_partial turn_index=%d", i
                )
                messages.append(self._render_orphan_partial(turn))
                i += 1
                continue

            # Orphan INTERRUPT_UTTERANCE / RESUMPTION_RESPONSE (not consumed
            # by a preceding collapse). Warn and render per role fallback.
            if tt is TurnType.INTERRUPT_UTTERANCE:
                logger.warning(
                    "[TURN_TYPE] orphan_interrupt_utterance turn_index=%d", i
                )
                # fall through to default role/content render below
            elif tt is TurnType.RESUMPTION_RESPONSE:
                logger.warning(
                    "[TURN_TYPE] orphan_resumption turn_index=%d", i
                )
                # fall through to default role/content render below

            # Default render path (NORMAL_USER_TURN, NORMAL_ASSISTANT_TURN,
            # orphan fall-throughs, unknown turn_type, or no turn_type key).
            role = turn.get("role", "user")
            content = turn.get("content", str(turn))
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
            i += 1

        # Always append the current message.
        messages.append({"role": "user", "content": request.message})

        return messages
