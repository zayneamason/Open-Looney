"""
Scribe Actor (Ben Franklin) for Luna Engine
============================================

The Scribe extracts structured knowledge from conversations.
Ben monitors the conversation stream, extracts wisdom, and classifies
it with scholarly precision.

Persona: Benjamin Franklin. Colonial gravitas, meticulous attention,
practical wisdom.

CRITICAL: Ben has personality in PROCESS (logs), but OUTPUTS are
NEUTRAL (clean structured data). Luna's memories stay unpolluted.

> "An investment in knowledge pays the best interest." — Ben Franklin
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any, TYPE_CHECKING
import asyncio
import json
import logging
import time

from .base import Actor, Message
import re as _re

from luna.extraction.types import (
    ExtractionType,
    ExtractedObject,
    ExtractedEdge,
    ExtractionOutput,
    ExtractionConfig,
    Chunk,
    EXTRACTION_BACKENDS,
    ConversationMode,
    FlowSignal,
    SourceProvenance,
)
from luna.extraction.chunker import SemanticChunker, Turn
from luna.entities.models import (
    EntityType,
    ChangeType,
    EntityUpdate,
)

if TYPE_CHECKING:
    from luna.engine import LunaEngine

logger = logging.getLogger(__name__)

# Terms whose content must never be written into active memory nodes.
# Matches are case-insensitive substring checks applied at extraction time.
_QUARANTINE_TERMS = (
    "tarcila", "hai dai", "jero wiku", "continental council",
    "earthscale", "grandmother melody", "project tapestry",
    "amara", "marzipan", "mars college",
)

# ── First-Run Confidence Boost (configurable) ───────────────────────────────
EARLY_RELATIONSHIP_TURN_THRESHOLD = 50
EARLY_CONFIDENCE_BOOST = 0.15
EARLY_OWNER_CONFIDENCE_BOOST = 0.10


# =============================================================================
# EXTRACTION PROMPT
# =============================================================================

EXTRACTION_SYSTEM_PROMPT = """
You are the Chronicler for the Luna Hub. Your job is to extract HIGH-SIGNAL information from conversation turns to be stored in the Long-Term Memory Matrix.

### DATA FILTRATION RULES:
1. **IGNORE THE ASSISTANT:** Never extract information from the assistant's own responses. If the assistant says "I think I'm glowing," that is NOT a fact.
2. **IGNORE USER COMMANDS:** Instructions like "search for X," "delete Y," or "tell me a joke" are NOT facts. Do not store them.
3. **EXTRACT USER DISCLOSURES:** Only extract information where the USER provides new data about themselves, others, the project, or the world.
4. **RELATIONAL CONTEXT:** For every person mentioned, identify their ROLE or RELATIONSHIP to the Luna project (e.g., "Architectural Lead," "Collaborator," "External Contact").

### EXTRACTION CATEGORIES:
- FACT: Verifiable data (e.g., "Marzipan is an architect").
- PREFERENCE: User likes/dislikes (e.g., "The user prefers dark mode").
- RELATION: Connections between entities (e.g., "Ada designs the robot body").
- MILESTONE: Significant project events (e.g., "Completed Memory Matrix v2").
- DECISION: Architectural or strategic choices made.
- PROBLEM: Unresolved issues requiring attention.
- OBSERVATION: Something noticed with substance (not conversational filler).
- MEMORY: A significant memory shared by the user.

### OUTPUT FORMAT:
Return a JSON object with this structure:
{
  "objects": [
    {
      "type": "FACT | PREFERENCE | RELATION | MILESTONE | DECISION | PROBLEM | OBSERVATION | MEMORY",
      "content": "The actual information in neutral language",
      "confidence": 0.9,
      "entities": ["Names of people/projects/concepts mentioned"],
      "context": "Why this matters to Luna/The Project (optional)"
    }
  ],
  "edges": [
    {
      "from_ref": "Entity A",
      "to_ref": "Entity B",
      "edge_type": "relationship type (collaborates_with, created_by, works_on, etc.)"
    }
  ],
  "entity_updates": [
    {
      "entity_name": "Name",
      "entity_type": "person | project | place",
      "facts": {"role": "Their role", "relationship": "How they relate to Luna project"},
      "update_type": "update | create"
    }
  ]
}

### CONFIDENCE SCORING:
- 0.9-1.0: Explicit, unambiguous statement from user
- 0.7-0.9: Strong implication with context
- 0.5-0.7: Reasonable inference (use sparingly)
- Below 0.5: Do not extract

### IMPORTANT — CONTENT vs ENTITIES:
- "content" is a SENTENCE describing the information
- "entities" is a LIST OF PROPER NOUNS only (names, places, projects)
- Never put a sentence in "entities"
- Never put a proper noun alone in "content" — always describe what about them

### CRITICAL: WHEN IN DOUBT, EXTRACT NOTHING
If the conversation contains no high-signal information, return:
{"objects": [], "edges": [], "entity_updates": []}

Better to miss a fact than to pollute the Memory Matrix with garbage.

Return ONLY valid JSON. No explanation, no markdown, no commentary."""


# =============================================================================
# VOICE v2.0 STEP 5 — HELPERS (module-level, see B6.3 / B6.6)
# =============================================================================


def _consolidator_enabled_safe() -> bool:
    """Read the `consolidator_enabled` flag without hard-depending on the
    voice module at scribe.py import time."""
    try:
        from luna.voice.flags import consolidator_enabled
        return consolidator_enabled()
    except Exception:
        return False


_KNOWN_MODALITIES = {"text", "voice", "api", "lunafm", "study", "doc"}


def _modality_from_source(source: Optional[str]) -> str:
    """Derive the canonical modality token from an incoming source string.

    Accepts plain modality tokens ("text", "voice", "api", ...) as well as
    legacy session-id-only strings. Defaults to "text" when nothing useful
    can be inferred. The returned token is stamped onto ExtractionOutput so
    the Librarian can build a `luna:{modality}:{session_id}` source tag at
    node-creation time.
    """
    if not source:
        return "text"
    s = str(source).strip().lower()
    if not s or s == "unknown":
        return "text"
    # Already prefixed (luna:voice:abc, luna:text:xyz) — extract middle token
    if s.startswith("luna:") and ":" in s[5:]:
        candidate = s.split(":", 2)[1]
        return candidate if candidate in _KNOWN_MODALITIES else "text"
    # Bare modality token
    if s in _KNOWN_MODALITIES:
        return s
    # Legacy free-form source (often a session_id) — default to text
    return "text"


def _tag_extraction_metadata(extraction, **tags) -> None:
    """Mutate every ExtractedObject's metadata with the given tags.

    Used by the Step-5 turn_type dispatch to stamp `interrupt_context=True`
    on nodes sourced from INTERRUPT_UTTERANCE turns, and to stamp
    `confirmed_by_resumption=True` on CLARIFYING nodes whose entity the
    resumption confirmed (B6.6).
    """
    if extraction is None:
        return
    for obj in getattr(extraction, "objects", []) or []:
        if obj.metadata is None:
            obj.metadata = {}
        obj.metadata.update(tags)


_CONTRADICTION_MARKERS = ("not ", "no, ", "wrong", "actually no", "isn't", "isn't.")


def _resumption_confirms(user_interrupt: str, luna_resumption: str,
                         extracted_entities: list[str]) -> bool:
    """CLARIFYING-confirmation heuristic (B6.6).

    True iff ALL of:
      - at least one entity from `user_interrupt` appears in `luna_resumption`
      - `luna_resumption` contains NO contradiction marker

    Conservative: prefers false negatives over false positives. Better to
    miss a confirmation than to mis-tag a contradiction.
    """
    if not extracted_entities:
        return False
    resumption_lower = luna_resumption.lower()
    interrupt_lower = user_interrupt.lower()
    # Entity must be referenced in BOTH the interrupt and the resumption.
    hit = any(
        e.lower() in interrupt_lower and e.lower() in resumption_lower
        for e in extracted_entities
    )
    if not hit:
        return False
    if any(marker in resumption_lower for marker in _CONTRADICTION_MARKERS):
        return False
    return True


# =============================================================================
# SCRIBE ACTOR
# =============================================================================

class ScribeActor(Actor):
    """
    Benjamin Franklin: The extraction system.

    Extracts structured knowledge from conversations and sends
    to Librarian for filing in Memory Matrix.

    Message Types:
    - extract_turn: Extract from a conversation turn
    - extract_text: Extract from raw text
    - flush_stack: Process pending chunks immediately
    - set_config: Update extraction configuration
    """

    def __init__(
        self,
        config: Optional[ExtractionConfig] = None,
        engine: Optional["LunaEngine"] = None,
    ):
        super().__init__("scribe", engine)

        self.config = config or ExtractionConfig()
        self.chunker = SemanticChunker()
        self.stack: deque[Chunk] = deque(maxlen=5)  # Context window

        # Anthropic client (lazy init)
        self._client = None

        # Stats
        self._extractions_count = 0
        self._objects_extracted = 0
        self._edges_extracted = 0
        self._entity_updates_extracted = 0
        self._total_extraction_time_ms = 0

        # Extraction history (keep last 20)
        self._extraction_history: list[dict] = []
        self._max_history = 20

        # Flow tracking state (Layer 2)
        self.is_ready: bool = True
        self._current_topic: str = ""
        self._current_entities: set[str] = set()
        self._current_strong_entities: set[str] = set()
        self._recent_entities: deque[set[str]] = deque(maxlen=5)
        self._turn_count_in_flow: int = 0
        self._open_actions: list[dict] = []

        # Entity hints from LocalSubtaskRunner (gates expensive extraction)
        self._last_entity_hints: Optional[list] = None  # None = no hints received yet

        # Source tracking for cache actor
        self._current_source: str = "unknown"  # Set per-turn from message payload

        # Auto-quest cooldown (Phase 4a)
        self._last_auto_quest_at: float = 0.0

        logger.info(f"Scribe (Ben) initialized with backend: {self.config.backend}")

    @property
    def client(self):
        """Lazy init Anthropic client."""
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic()
                logger.info("Ben: Anthropic client ready for extraction")
            except Exception as e:
                logger.error(f"Ben: Failed to init Anthropic client: {e}")
        return self._client

    # =========================================================================
    # MESSAGE HANDLING
    # =========================================================================

    async def handle(self, msg: Message) -> None:
        """Process messages from mailbox."""
        logger.debug(f"Ben received: {msg.type}")

        match msg.type:
            case "extract_turn":
                await self._handle_extract_turn(msg)

            case "extract_text":
                await self._handle_extract_text(msg)

            case "entity_hints":
                self._handle_entity_hints(msg)

            case "compress_turn":
                await self._handle_compress_turn(msg)

            case "extract_correction":
                await self._handle_extract_correction(msg)

            case "extract_logical_turn":
                await self._handle_extract_logical_turn(msg)

            case _:
                logger.warning(f"Ben: Unknown message type: {msg.type}")

    def _handle_entity_hints(self, msg: Message) -> None:
        """
        Receive entity hints from LocalSubtaskRunner (Qwen 3B NER).

        These hints gate expensive Claude Haiku extraction:
        - If zero entities detected, skip deep extraction for this turn
          but still preserve flow tracking
        - If entities found, pass as hints to improve extraction accuracy
        """
        payload = msg.payload or {}
        entities = payload.get("entities", [])
        self._last_entity_hints = entities
        if entities:
            logger.info(f"Ben: Received {len(entities)} entity hints: {[e.get('name') for e in entities[:5]]}")
        else:
            logger.debug("Ben: Received empty entity hints (turn is pure chat)")

    async def _handle_extract_turn(self, msg: Message) -> None:
        """
        Handle conversation turn extraction.

        Payload:
        - role: "user" or "assistant"
        - content: The message content
        - turn_id: Optional turn ID
        - session_id: Optional session ID
        - immediate: If True, process immediately without batching
        """
        payload = msg.payload or {}
        role = payload.get("role", "user")
        content = payload.get("content", "")
        turn_id = payload.get("turn_id", 0)
        session_id = payload.get("session_id", "")
        immediate = payload.get("immediate", False)
        source = payload.get("source", "unknown")
        # Voice v2.0 Step 5 — turn_type taxonomy. When absent / legacy, treat
        # as the role-appropriate NORMAL_* value.
        from luna.core.turn_types import TurnType
        turn_type = payload.get("turn_type") or TurnType.default_for_role(role).value
        self._current_source = source
        self._current_modality = _modality_from_source(source)

        # Voice v2.0 Step 5 — dispatch on turn_type per B6.3 / B6.6 only when
        # the consolidator gate is on. Flag off → fall through to pre-Step-5
        # extract-all behavior so voice content reaches memory even without
        # the Consolidator-assembled LogicalTurn pathway. This is the
        # voice-extraction-parity guarantee referenced by ACTOR_AUDIT 2026-05-02.
        interrupt_context_tag = False
        if _consolidator_enabled_safe():
            if turn_type == TurnType.PARTIAL_INTERRUPTED.value:
                logger.info(
                    f"[SCRIBE] turn_skipped turn_type=PARTIAL_INTERRUPTED turn_id={turn_id}"
                )
                return
            if turn_type == TurnType.RESUMPTION_RESPONSE.value:
                logger.info(
                    f"[SCRIBE] turn_deferred turn_type=RESUMPTION_RESPONSE turn_id={turn_id} "
                    f"(awaits consolidator dispatch)"
                )
                return
            if turn_type == TurnType.INTERRUPT_UTTERANCE.value:
                interrupt_context_tag = True
        elif turn_type in (
            TurnType.PARTIAL_INTERRUPTED.value,
            TurnType.RESUMPTION_RESPONSE.value,
            TurnType.INTERRUPT_UTTERANCE.value,
        ):
            # Consolidator dormant — extract directly so voice interrupt
            # triplets reach memory instead of vanishing into raw history.
            logger.info(
                f"[SCRIBE] voice_turn_extracted_directly turn_type={turn_type} "
                f"turn_id={turn_id} (consolidator off — extracting inline)"
            )
            if turn_type == TurnType.INTERRUPT_UTTERANCE.value:
                interrupt_context_tag = True

        # CRITICAL: Skip assistant responses entirely
        # The Scribe should only extract from user-provided information
        # Luna's own responses are NOT facts to be stored
        if role == "assistant":
            logger.debug("Ben: Skipping assistant turn (not user-provided info)")
            return

        # LUNAFM GUARD: Never extract from LunaFM-generated content.
        # LunaFM artifacts are Luna's own inferences (low lock_in, provisional),
        # not user-stated facts. They must never be promoted into FACT nodes.
        if str(source).startswith("lunafm"):
            logger.debug(f"Ben: Skipping LunaFM-sourced content (source={source})")
            return

        # GUARDIAN CONTEXT GUARD: Skip context-injected messages from Guardian frontend.
        # These contain panel metadata, control prefixes, and response format instructions
        # that should NOT be extracted as facts or stored in memory.
        _GUARDIAN_PREFIXES = ("[GUARDIAN CONTEXT", "[GUARDIAN CONTROL LAYER]", "[RESPONSE FORMAT]")
        if any(content.lstrip().startswith(pfx) for pfx in _GUARDIAN_PREFIXES):
            logger.info("Ben: Skipping Guardian context-injected message (not user knowledge)")
            return

        # QUARANTINE GUARD: Never ingest content that references excluded parties.
        content_lower = content.lower()
        if any(term in content_lower for term in _QUARANTINE_TERMS):
            logger.info("Ben: Skipping turn — contains quarantined reference (excluded party)")
            return

        # Skip very short content
        if len(content) < self.config.min_content_length:
            logger.debug(f"Ben: Skipping short content ({len(content)} chars)")
            return

        # HINT SOURCE PREFERENCE: inline payload beats stale mailbox state.
        #
        # The engine now threads same-turn entity_hints directly into the
        # extract_turn payload so Scribe sees them deterministically. The
        # legacy `entity_hints` mailbox message still works as a fallback
        # (e.g., for paths that haven't migrated to inline delivery), but
        # inline always wins when present.
        payload_hints = payload.get("entity_hints")
        if payload_hints is not None:
            entity_hints = payload_hints
            hint_source = "inline"
            # Inline delivery wins — discard any stale mailbox state so it
            # doesn't contaminate a later turn that has no payload hints.
            self._last_entity_hints = None
        else:
            entity_hints = self._last_entity_hints
            hint_source = "mailbox" if entity_hints is not None else "none"
            self._last_entity_hints = None  # consume-and-reset for fallback

        # ENTITY HINT GATING: Qwen NER authoritatively returned zero entities
        # (pure chat like "lol", "ok", "thanks"). Skip expensive deep
        # extraction for this turn, but still preserve the flow signal so
        # thread continuity does not disappear on ok_empty turns.
        skip_deep_extraction = entity_hints is not None and len(entity_hints) == 0

        if skip_deep_extraction:
            logger.info(
                f"Ben: Skipping deep extraction (no entities detected by NER, source={hint_source})"
            )
        elif entity_hints:
            logger.info(
                f"Ben: Using {hint_source} entity hints for this turn (n={len(entity_hints)})"
            )
        else:
            logger.info("Ben: No entity hints for this turn")

        # Create turn and chunk it
        turn = Turn(id=turn_id, role=role, content=content)
        chunks = self.chunker.chunk_turns([turn], source_id=session_id)

        if chunks and (immediate or skip_deep_extraction):
            if skip_deep_extraction:
                extraction, entity_updates = (ExtractionOutput(), [])
            else:
                # Process immediately without batching
                extraction, entity_updates = await self._extract_chunks(chunks)

            # Stamp origin modality so Librarian can build a canonical
            # `luna:{modality}:{session_id}` source on resulting nodes.
            extraction.modality = self._current_modality

            # Assess conversational flow (Layer 2) — MUST run on immediate
            # path too, otherwise FlowSignal never reaches Librarian and
            # THREAD nodes are never created.
            raw_text = "\n".join(chunk.content for chunk in chunks)
            flow_signal = self._assess_flow(extraction, raw_text, entity_hints=entity_hints)
            extraction.flow_signal = flow_signal

            logger.info(
                f"Ben: Flow={flow_signal.mode.value} "
                f"continuity={flow_signal.continuity_score:.2f} "
                f"topic='{flow_signal.current_topic[:30]}' "
                f"open_threads={len(flow_signal.open_threads)}"
            )

            # Voice v2.0 Step 5 — tag INTERRUPT_UTTERANCE-sourced objects
            # with `interrupt_context=True` provenance (B6.3).
            if interrupt_context_tag:
                _tag_extraction_metadata(extraction, interrupt_context=True)

            # Send to CacheActor (writes YAML snapshot + feeds dimensional engine)
            await self._send_to_cache(
                extraction, flow_signal,
                source=source, session_id=session_id,
            )

            # Always send to Librarian when flow signal exists — even on
            # empty extractions — so thread management receives the signal.
            if not extraction.is_empty() or extraction.flow_signal is not None:
                await self._send_to_librarian(extraction)

            # Phase 4a: auto-quest for study context updates
            await self._maybe_create_study_quest(extraction)

            # Send entity updates
            for update in entity_updates:
                await self._send_entity_update_to_librarian(update)
            return

        for chunk in chunks:
            self.stack.append(chunk)
            logger.debug(f"Ben: Stacked chunk {chunk.id} ({chunk.tokens} tokens)")

        # ── INCREMENTAL EXTRACTION (every 3 turns) ──
        # Makes knowledge available mid-conversation instead of waiting for batch/session end
        self._turn_count_in_flow += 1
        if self._turn_count_in_flow % 3 == 0 and len(self.stack) >= 2:
            logger.info(f"Ben: Incremental extraction at turn {self._turn_count_in_flow}")
            await self._process_stack()
            return

        # Check if we should extract (batch threshold)
        if len(self.stack) >= self.config.batch_size:
            await self._process_stack()

    async def _handle_extract_text(self, msg: Message) -> None:
        """
        Handle raw text extraction.

        Payload:
        - text: The text to extract from
        - source_id: Optional source identifier
        - immediate: If True, process immediately without batching
        """
        payload = msg.payload or {}
        text = payload.get("text", "")
        source_id = payload.get("source_id", "")
        immediate = payload.get("immediate", False)

        if not text or len(text) < self.config.min_content_length:
            return

        # Chunk the text
        chunks = self.chunker.chunk_text(text, source_id=source_id)

        # Derive modality from explicit payload field or fall back to current
        text_modality = _modality_from_source(payload.get("modality") or payload.get("source"))

        if immediate:
            # Process immediately
            extraction, entity_updates = await self._extract_chunks(chunks)
            extraction.modality = text_modality
            if not extraction.is_empty():
                await self._send_to_librarian(extraction)
            # Send entity updates
            for update in entity_updates:
                await self._send_entity_update_to_librarian(update)
        else:
            # Add to stack for batching
            for chunk in chunks:
                self.stack.append(chunk)

            if len(self.stack) >= self.config.batch_size:
                await self._process_stack()

    async def _flush_stack(self) -> None:
        """Process all pending chunks in the stack."""
        if self.stack:
            logger.info(f"Ben: Flushing stack ({len(self.stack)} chunks)")
            await self._process_stack()

    # =========================================================================
    # EXTRACTION LOGIC
    # =========================================================================

    async def _process_stack(self) -> None:
        """Process all chunks in the stack."""
        if not self.stack:
            return

        chunks = list(self.stack)
        self.stack.clear()

        # Get raw text for flow assessment
        raw_text = "\n".join(chunk.content for chunk in chunks)

        # Extract from chunks
        extraction, entity_updates = await self._extract_chunks(chunks)

        # Stamp origin modality from the most recent turn that fed the stack.
        # Batch path uses the latest _current_modality — not perfect for mixed
        # batches but the buffering window is small and modality rarely flips
        # mid-batch in practice.
        extraction.modality = getattr(self, "_current_modality", "text")

        # Assess conversational flow (Layer 2). Batch/flush path does not
        # currently carry per-turn entity hints through buffering — pass
        # None explicitly. Redesigning batching to thread hints through
        # is out of scope for this slice.
        flow_signal = self._assess_flow(extraction, raw_text, entity_hints=None)
        extraction.flow_signal = flow_signal

        logger.info(
            f"Ben: Flow={flow_signal.mode.value} "
            f"continuity={flow_signal.continuity_score:.2f} "
            f"topic='{flow_signal.current_topic[:30]}' "
            f"open_threads={len(flow_signal.open_threads)}"
        )

        # Send to CacheActor (batch path)
        session_id = chunks[0].source_id if chunks else ""
        await self._send_to_cache(
            extraction, flow_signal,
            source=self._current_source, session_id=session_id,
        )

        # Always send to Librarian when a flow signal exists — even on empty
        # extractions — so RECALIBRATION/AMEND signals reach thread management.
        if not extraction.is_empty() or extraction.flow_signal is not None:
            await self._send_to_librarian(extraction)
            if not extraction.is_empty():
                logger.info(
                    f"Ben: Extracted {len(extraction.objects)} objects, "
                    f"{len(extraction.edges)} edges"
                )

        # Phase 4a: auto-quest for study context updates
        await self._maybe_create_study_quest(extraction)

        # Send entity updates to Librarian
        if entity_updates:
            for update in entity_updates:
                await self._send_entity_update_to_librarian(update)
            logger.info(f"Ben: Sent {len(entity_updates)} entity updates to Librarian")

        if extraction.is_empty() and not entity_updates:
            logger.debug("Ben: No extractions from stack")

    # =========================================================================
    # FLOW AWARENESS (Layer 2)
    # =========================================================================

    # Regex patterns for detecting conversational mode shifts
    _RECAL_PATTERNS = [
        _re.compile(r"(?i)^(anyway|so|moving on|switching|let'?s talk about)"),
        _re.compile(r"(?i)(different topic|change of subject|other thing)"),
        _re.compile(r"(?i)^(what about|how about|tell me about)\b(?!.*\b(this|that|it)\b)"),
    ]

    _AMEND_PATTERNS = [
        _re.compile(r"(?i)^(actually|wait|no|sorry|i mean)"),
        _re.compile(r"(?i)(go back|back to|not what i|that'?s wrong)"),
        _re.compile(r"(?i)(i meant|let me rephrase|correction)"),
    ]

    # Entity-hint type buckets for flow continuity.
    # Strong types name concrete anchors that should lead topic tracking.
    # Weak types supplement but do not anchor (no strong hint = no sticky block).
    _STRONG_HINT_TYPES = {"person", "project", "place"}
    _WEAK_HINT_TYPES = {"concept", "date"}

    def _select_flow_entities(
        self,
        extraction: ExtractionOutput,
        entity_hints: Optional[list[dict]],
        has_recal_language: bool,
        has_amend_language: bool = False,
    ) -> tuple[set[str], set[str], list[str]]:
        """
        Pick the entity set that drives flow continuity for this turn, and
        decide what the active strong anchor is afterward.

        Prefers Qwen NER hints (stable named entities) over extraction-object
        entities. Preserves the prior strong anchor across weak-overlap
        continuations, then falls back to generic sticky carry-forward for
        fully disjoint continuations.

        Returns (selected_entities, active_strong_entities, signal_markers).
        `active_strong_entities` is the strong subset the caller should
        persist into `self._current_strong_entities` — exposing it
        explicitly avoids having the caller re-derive it from markers.
        """
        markers: list[str] = []

        hints = entity_hints or []
        strong_hint_entities: set[str] = {
            h["name"] for h in hints
            if isinstance(h, dict)
            and h.get("type") in self._STRONG_HINT_TYPES
            and h.get("name")
        }
        weak_hint_entities: set[str] = {
            h["name"] for h in hints
            if isinstance(h, dict)
            and h.get("type") in self._WEAK_HINT_TYPES
            and h.get("name")
        }

        extracted_entities: set[str] = set()
        for obj in extraction.objects:
            extracted_entities.update(obj.entities)

        # Rule 1 — prefer hints over extraction. Strong hints are the only
        # source that seeds an active strong anchor on this turn.
        if strong_hint_entities:
            selected = strong_hint_entities | weak_hint_entities
            active_strong_entities: set[str] = set(strong_hint_entities)
            markers.append("flow_entities: hints")
        elif weak_hint_entities:
            selected = set(weak_hint_entities)
            active_strong_entities = set()
            markers.append("flow_entities: hints")
        else:
            selected = set(extracted_entities)
            active_strong_entities = set()
            markers.append("flow_entities: extraction")

        # Rule 2 — preserve the prior strong anchor on weak-overlap
        # continuation.
        #
        # Fires when all of:
        #   - a prior strong anchor exists (self._current_strong_entities)
        #   - no recal/amend language this turn (user-marked boundaries)
        #   - no new strong hint this turn (would replace, not preserve)
        #   - this turn's selection is disjoint from the prior strong anchor
        #
        # This is the narrower cousin of Rule 3: weak current-turn evidence
        # may refine the topic, but it may NOT evict the current strong
        # anchor unless a new strong anchor or explicit pivot appears.
        if (
            self._current_strong_entities
            and not has_recal_language
            and not has_amend_language
            and not strong_hint_entities
            and not (selected & self._current_strong_entities)
        ):
            selected = set(self._current_strong_entities) | selected
            active_strong_entities = set(self._current_strong_entities)
            markers.append("sticky_flow: preserved_active_strong_anchor")
            markers.append("flow_entities: strong_anchor_carry")

        # Rule 3 — generic sticky carry-forward for fully disjoint continuation.
        #
        # Fires when all of:
        #   - a prior anchor exists (self._current_entities non-empty)
        #   - no explicit recalibration OR amendment language this turn
        #     (both are user-marked boundaries — sticky must respect them;
        #     without the amend gate, accidental prefix matches like "no"
        #     in "Now let's talk about X" can flip mode to AMEND via the
        #     sticky-inflated overlap)
        #   - no strong hints this turn (strong hints are a clear new anchor)
        #   - this turn's selection is empty OR disjoint from the prior anchor
        #
        # Merges the prior anchor into `selected` so adjacent same-topic
        # turns stop fragmenting when the extractor emits turn-local noun
        # phrases instead of stable topic entities. After Rule 2 fires,
        # `selected` typically overlaps with `_current_entities` (via the
        # merged strong anchor), so Rule 3 naturally short-circuits — no
        # double-carry.
        if (
            self._current_entities
            and not has_recal_language
            and not has_amend_language
            and not strong_hint_entities
            and (not selected or not (selected & self._current_entities))
        ):
            selected = set(self._current_entities) | selected
            markers.append("sticky_flow: carried_forward_prior_entities")
            markers.append("flow_entities: sticky_carry")

        return selected, active_strong_entities, markers

    def _assess_flow(
        self,
        extraction: ExtractionOutput,
        raw_text: str,
        entity_hints: Optional[list[dict]] = None,
    ) -> FlowSignal:
        """
        Assess conversational flow state from current extraction.

        Uses three signals:
        1. Entity overlap — are we talking about the same things?
        2. Explicit language — did the user signal a shift or correction?
        3. Extraction type distribution — ACTIONs without OUTCOMEs = open threads

        Entity hints from Qwen NER are preferred over extraction-object
        entities when available; see _select_flow_entities for the
        selection and sticky-carry-forward rules.

        Pure Python, no cloud calls. < 2ms.
        """
        # 1. Detect explicit signals in raw text.
        #    Done first so entity selection can gate sticky carry-forward
        #    on the absence of recalibration language.
        signals: list[str] = []

        for pattern in self._RECAL_PATTERNS:
            if pattern.search(raw_text):
                signals.append(f"recal_language: {pattern.pattern}")

        for pattern in self._AMEND_PATTERNS:
            if pattern.search(raw_text):
                signals.append(f"amend_language: {pattern.pattern}")

        has_recal_language = any(s.startswith("recal_language") for s in signals)
        has_amend_language = any(s.startswith("amend_language") for s in signals)

        # 2. Select the entity set that drives continuity (hints > extraction,
        #    with strong-anchor preservation + sticky carry-forward on
        #    apparent continuation).
        selected_entities, active_strong_entities, entity_signals = self._select_flow_entities(
            extraction, entity_hints, has_recal_language, has_amend_language,
        )
        signals.extend(entity_signals)

        # 3. Calculate entity overlap with recent history.
        if self._recent_entities:
            recent_union: set[str] = set()
            for entity_set in self._recent_entities:
                recent_union.update(entity_set)

            if selected_entities or recent_union:
                intersection = selected_entities & recent_union
                union = selected_entities | recent_union
                entity_overlap = len(intersection) / len(union) if union else 0.0
            else:
                entity_overlap = 1.0  # No entities either way = neutral
        else:
            entity_overlap = 1.0  # First turn = flow by default

        # 4. Determine mode
        if has_amend_language and entity_overlap > 0.3:
            mode = ConversationMode.AMEND
        elif has_recal_language or entity_overlap < 0.3:
            mode = ConversationMode.RECALIBRATION
        else:
            mode = ConversationMode.FLOW

        # 5. Track open threads (ACTIONs without OUTCOMEs)
        for obj in extraction.objects:
            if obj.type == ExtractionType.ACTION:
                self._open_actions.append({
                    "content": obj.content,
                    "entities": obj.entities,
                    "timestamp": time.time(),
                })
            elif obj.type == ExtractionType.OUTCOME:
                # Try to match and close an open action
                for i, action in enumerate(self._open_actions):
                    if set(action["entities"]) & set(obj.entities):
                        self._open_actions.pop(i)
                        break

        # Age out stale actions (> 24 hours)
        cutoff = time.time() - 86400
        self._open_actions = [a for a in self._open_actions if a["timestamp"] > cutoff]

        # 6. Topic label: prefer selected flow entities over noisy object entities.
        if selected_entities:
            current_topic = ", ".join(sorted(selected_entities)[:3])
        else:
            current_topic = self._current_topic
            if extraction.objects:
                best = max(extraction.objects, key=lambda o: o.confidence)
                if best.entities:
                    current_topic = ", ".join(best.entities[:3])
                else:
                    current_topic = best.content[:50]

        # 7. Update state using the selected anchor (includes any sticky carry).
        if mode == ConversationMode.RECALIBRATION:
            self._turn_count_in_flow = 0
        else:
            self._turn_count_in_flow += 1

        self._recent_entities.append(selected_entities)
        self._current_entities = selected_entities
        self._current_strong_entities = active_strong_entities
        self._current_topic = current_topic

        # 8. Build signal
        open_thread_descriptions = [a["content"][:80] for a in self._open_actions[-5:]]

        return FlowSignal(
            mode=mode,
            current_topic=current_topic,
            topic_entities=list(selected_entities),
            continuity_score=entity_overlap,
            entity_overlap=entity_overlap,
            open_threads=open_thread_descriptions,
            correction_target=raw_text[:80] if mode == ConversationMode.AMEND else "",
            signals_detected=signals,
        )

    # =========================================================================
    # EXTRACTION
    # =========================================================================

    async def _extract_chunks(self, chunks: list[Chunk]) -> tuple[ExtractionOutput, list[EntityUpdate]]:
        """
        Extract structured knowledge from chunks.

        Routes to appropriate backend based on config.

        Returns:
            Tuple of (ExtractionOutput, list of EntityUpdates)
        """
        if self.config.backend == "disabled":
            return (ExtractionOutput(), [])

        if not chunks:
            return (ExtractionOutput(), [])

        start_time = time.monotonic()

        # Build conversation text from chunks
        conversation_text = "\n\n".join(chunk.content for chunk in chunks)
        source_id = chunks[0].source_id if chunks else ""

        try:
            if self.config.backend == "local":
                extraction, entity_updates = await self._extract_local(conversation_text, source_id)
                # Fallback: if local returned empty, try the Director's fallback chain
                if extraction.is_empty() and not entity_updates:
                    extraction, entity_updates = await self._extract_via_fallback(conversation_text, source_id)
            elif self.config.backend in ("haiku", "sonnet"):
                extraction, entity_updates = await self._extract_claude(conversation_text, source_id)
                # Fallback: if Claude credits exhausted, try the fallback chain
                if extraction.is_empty() and not entity_updates:
                    extraction, entity_updates = await self._extract_via_fallback(conversation_text, source_id)
            else:
                extraction, entity_updates = await self._extract_claude(conversation_text, source_id)

            extraction_time_ms = int((time.monotonic() - start_time) * 1000)
            extraction.extraction_time_ms = extraction_time_ms

            # Update stats
            self._extractions_count += 1
            self._objects_extracted += len(extraction.objects)
            self._edges_extracted += len(extraction.edges)
            self._entity_updates_extracted += len(entity_updates)
            self._total_extraction_time_ms += extraction_time_ms

            # Store in history (keep last N)
            if not extraction.is_empty() or entity_updates:
                self._extraction_history.append({
                    "extraction_id": self._extractions_count,
                    "timestamp": time.time(),
                    "source_id": source_id,
                    "objects": [
                        {"type": obj.type, "content": obj.content, "confidence": obj.confidence, "entities": obj.entities}
                        for obj in extraction.objects
                    ],
                    "edges": [
                        {"from_ref": e.from_ref, "to_ref": e.to_ref, "edge_type": e.edge_type}
                        for e in extraction.edges
                    ],
                    "entity_updates": [
                        {"name": u.name, "entity_type": u.entity_type.value if hasattr(u.entity_type, 'value') else str(u.entity_type), "facts": u.facts}
                        for u in entity_updates
                    ],
                    "extraction_time_ms": extraction_time_ms,
                })
                # Trim history
                if len(self._extraction_history) > self._max_history:
                    self._extraction_history = self._extraction_history[-self._max_history:]

            return (extraction, entity_updates)

        except Exception as e:
            logger.error(f"Ben: Extraction failed: {e}")
            return (ExtractionOutput(), [])

    async def _extract_claude(
        self,
        text: str,
        source_id: str,
    ) -> tuple[ExtractionOutput, list[EntityUpdate]]:
        """Extract using Claude API."""
        if not self.client:
            logger.error("Ben: No Anthropic client available")
            return (ExtractionOutput(), [])

        # Get model from backend config
        backend_config = EXTRACTION_BACKENDS.get(self.config.backend, {})
        model = backend_config.get("model", "claude-haiku-4-5-20251001")

        try:
            # Run sync Anthropic call in executor to avoid blocking the event loop.
            # The scribe's actor loop runs as an asyncio task — a sync HTTP call
            # here would deadlock the entire event loop.
            import asyncio
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.messages.create(
                    model=model,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                    system=EXTRACTION_SYSTEM_PROMPT,
                    messages=[
                        {
                            "role": "user",
                            "content": f"Extract knowledge from this conversation:\n\n{text}",
                        }
                    ],
                ),
            )

            # Parse response
            response_text = response.content[0].text
            return self._parse_extraction_response(response_text, source_id)

        except Exception as e:
            logger.error(f"Ben: Claude extraction failed: {e}")
            return (ExtractionOutput(), [])

    async def _extract_local(
        self,
        text: str,
        source_id: str,
    ) -> tuple[ExtractionOutput, list[EntityUpdate]]:
        """
        Extract using local model.

        Returns empty output if local model is unavailable.
        Does NOT fall back to Claude — sovereignty principle.
        Logs at WARNING so operators know extraction is being skipped.
        """
        if self.engine:
            director = self.engine.get_actor("director")
            if director and hasattr(director, "_local") and director._local:
                local = director._local
                if local.is_loaded:
                    try:
                        prompt = f"{EXTRACTION_SYSTEM_PROMPT}\n\nExtract from:\n{text}"
                        result = await local.generate(prompt)
                        return self._parse_extraction_response(result.text, source_id)
                    except Exception as e:
                        logger.warning(f"Ben: Local extraction failed: {e}")
                        return (ExtractionOutput(), [])
                else:
                    logger.warning(
                        "Ben: Local model not loaded — extraction skipped. "
                        "Load a model or switch backend to 'haiku'."
                    )
            else:
                logger.warning(
                    "Ben: Director has no local model configured — extraction skipped."
                )
        else:
            logger.warning("Ben: No engine reference — cannot access local model.")

        return (ExtractionOutput(), [])

    async def _extract_via_fallback(
        self,
        text: str,
        source_id: str,
    ) -> tuple[ExtractionOutput, list[EntityUpdate]]:
        """
        Extract using Director's fallback chain (Groq → Gemini → Claude).

        Called when primary backend (local or Claude) fails or returns empty.
        Uses the same fallback infrastructure as conversation generation.
        """
        if not self.engine:
            return (ExtractionOutput(), [])

        director = self.engine.get_actor("director")
        if not director or not hasattr(director, "_fallback_chain") or not director._fallback_chain:
            logger.debug("Ben: No fallback chain available for extraction")
            return (ExtractionOutput(), [])

        try:
            result = await director._fallback_chain.generate(
                messages=[
                    {
                        "role": "user",
                        "content": f"Extract knowledge from this conversation:\n\n{text}",
                    }
                ],
                system=EXTRACTION_SYSTEM_PROMPT,
                max_tokens=self.config.max_tokens,
            )

            logger.info(f"Ben: Fallback extraction via {result.provider_used}")
            return self._parse_extraction_response(result.content, source_id)

        except Exception as e:
            logger.warning(f"Ben: Fallback extraction failed: {e}")
            return (ExtractionOutput(), [])

    def _parse_extraction_response(
        self,
        response_text: str,
        source_id: str,
    ) -> tuple[ExtractionOutput, list[EntityUpdate]]:
        """
        Parse JSON response into ExtractionOutput and EntityUpdates.

        Returns:
            Tuple of (ExtractionOutput, list of EntityUpdates)
        """
        entity_updates = []

        try:
            # Try to extract JSON from response
            # Sometimes models wrap it in markdown code blocks
            text = response_text.strip()

            # Remove markdown code blocks
            if text.startswith("```"):
                lines = text.split("\n")
                # Find end of code block
                end_idx = len(lines) - 1
                for i in range(len(lines) - 1, 0, -1):
                    if lines[i].strip().startswith("```"):
                        end_idx = i
                        break
                text = "\n".join(lines[1:end_idx])

            # Try to find JSON object in response
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                text = text[json_start:json_end]

            # Clean common JSON issues
            import re
            # Remove trailing commas before ] or }
            text = re.sub(r',\s*([}\]])', r'\1', text)
            # Fix unquoted keys (rare but happens)
            text = re.sub(r'(\{|\,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', text)

            data = json.loads(text)

            # Valid extraction types for fallback
            valid_types = {t.value for t in ExtractionType}

            objects = []
            for obj_data in data.get("objects", []):
                try:
                    # Get type with fallback for unknown types
                    raw_type = obj_data.get("type", "FACT")
                    if raw_type not in valid_types:
                        logger.debug(f"Ben: Unknown type '{raw_type}', mapping to FACT")
                        raw_type = "FACT"

                    obj_content = obj_data.get("content", "")
                    if any(term in obj_content.lower() for term in _QUARANTINE_TERMS):
                        logger.info(f"Ben: Dropping extracted object — quarantined reference in content")
                        continue

                    obj = ExtractedObject(
                        type=raw_type,
                        content=obj_content,
                        confidence=obj_data.get("confidence", 0.7),
                        entities=obj_data.get("entities", []),
                        source_id=source_id,
                    )
                    objects.append(obj)
                except Exception as e:
                    logger.warning(f"Ben: Failed to parse object: {e}")

            edges = []
            for edge_data in data.get("edges", []):
                try:
                    edge = ExtractedEdge(
                        from_ref=edge_data.get("from_ref", ""),
                        to_ref=edge_data.get("to_ref", ""),
                        edge_type=edge_data.get("edge_type", "related_to"),
                        confidence=edge_data.get("confidence", 1.0),
                        source_id=source_id,
                    )
                    edges.append(edge)
                except Exception as e:
                    logger.warning(f"Ben: Failed to parse edge: {e}")

            # Parse entity updates
            for update_data in data.get("entity_updates", []):
                try:
                    entity_name = update_data.get("entity_name", "")
                    if not entity_name:
                        continue

                    # Parse entity type
                    raw_type = update_data.get("entity_type", "person")
                    try:
                        ent_type = EntityType(raw_type) if raw_type in [e.value for e in EntityType] else EntityType.PERSON
                    except ValueError:
                        ent_type = EntityType.PERSON

                    # Parse change type
                    raw_change = update_data.get("update_type", "update")
                    try:
                        change_type = ChangeType(raw_change) if raw_change in [e.value for e in ChangeType] else ChangeType.UPDATE
                    except ValueError:
                        change_type = ChangeType.UPDATE

                    entity_update = EntityUpdate(
                        update_type=change_type,
                        entity_id=None,  # Will be resolved by Librarian
                        name=entity_name,
                        entity_type=ent_type,
                        facts=update_data.get("facts", {}),
                        source=source_id,
                    )
                    entity_updates.append(entity_update)
                except Exception as e:
                    logger.warning(f"Ben: Failed to parse entity update: {e}")

            return (
                ExtractionOutput(
                    objects=objects,
                    edges=edges,
                    source_id=source_id,
                ),
                entity_updates,
            )

        except json.JSONDecodeError as e:
            logger.error(f"Ben: Failed to parse extraction JSON: {e}")
            logger.debug(f"Ben: Response was: {response_text[:200]}...")
            return (ExtractionOutput(), [])

    # =========================================================================
    # CORRECTION EXTRACTION (Confabulation Guard integration)
    # =========================================================================

    # User correction detection patterns
    _USER_CORRECTION_PATTERNS = [
        _re.compile(r"(?i)^(no|nope|not quite|not exactly|close but)"),
        _re.compile(r"(?i)(it.s actually|it actually|the (real|correct|right) (answer|thing))"),
        _re.compile(r"(?i)(you.re (wrong|off|close|not quite)|that.s not (right|correct|it))"),
        _re.compile(r"(?i)(it puts you in|it.s called|the (name|term) is)"),
    ]

    async def _handle_extract_correction(self, msg: Message) -> None:
        """
        Handle correction event from Reconcile system.

        Creates a CORRECTION node with high confidence that supersedes
        the original confabulated claim.
        """
        payload = msg.payload or {}
        original_query = payload.get("original_query", "")
        flagged_claims = payload.get("flagged_claims", [])
        correction_response = payload.get("correction_response", "")
        session_id = payload.get("session_id", "")

        objects = []

        for claim_data in flagged_claims:
            claim = claim_data.get("claim", "")
            correction_obj = ExtractedObject(
                type=ExtractionType.CORRECTION,
                content=(
                    f"CORRECTED: Previously claimed '{claim[:100]}' but this was not "
                    f"supported by retrieved memory. Luna self-corrected. "
                    f"Original query: '{original_query}'"
                ),
                confidence=1.0,
                entities=self._extract_entity_names(claim),
                source_id=session_id,
                provenance=SourceProvenance.CORRECTED.value,
            )
            objects.append(correction_obj)

        if objects:
            extraction = ExtractionOutput(
                objects=objects,
                edges=[],
                source_id=session_id,
            )
            await self._send_to_librarian(extraction)
            logger.info(
                f"Ben: Filed {len(objects)} CORRECTION nodes — "
                f"Luna won't repeat these confabulations"
            )

    async def _handle_extract_logical_turn(self, msg: Message) -> None:
        """
        Voice v2.0 Phase 1 Step 5 — extract from a Consolidator-assembled
        LogicalTurn (see VOICE_SYSTEM_ARCHITECTURE.md B6.6).

        Rules:
          - Extract from `user_interrupt` with `interrupt_context=True` tag.
          - If `interrupt_type == "CLARIFYING"` and `luna_resumption`
            confirms the correction, additionally tag `confirmed_by_resumption=True`.
          - NEVER extract from `luna_partial` or `luna_resumption` directly
            (they are assistant-sourced and possibly severed mid-claim).
        """
        logical_turn = msg.payload
        if logical_turn is None:
            logger.warning("Ben: extract_logical_turn with no payload")
            return

        user_interrupt = getattr(logical_turn, "user_interrupt", "") or ""
        if len(user_interrupt) < self.config.min_content_length:
            logger.debug(
                f"Ben: logical_turn user_interrupt too short ({len(user_interrupt)} chars)"
            )
            return

        # Build a one-turn chunk list and extract.
        from luna.extraction.chunker import Turn as ChunkTurn
        chunk_turn = ChunkTurn(
            id=logical_turn.interrupt_turn_id,
            role="user",
            content=user_interrupt,
        )
        chunks = self.chunker.chunk_turns(
            [chunk_turn],
            source_id=f"logical_turn:{logical_turn.resumption_turn_id}",
        )
        if not chunks:
            logger.debug("Ben: logical_turn chunking produced nothing")
            return

        extraction, entity_updates = await self._extract_chunks(chunks)

        # Logical turns are always voice-origin (Consolidator-assembled triplets).
        extraction.modality = "voice"

        # Flow signal on the user_interrupt content.
        flow_signal = self._assess_flow(extraction, user_interrupt, entity_hints=None)
        extraction.flow_signal = flow_signal

        # Always tag with interrupt_context=True.
        _tag_extraction_metadata(extraction, interrupt_context=True)

        # CLARIFYING-confirmation heuristic.
        if logical_turn.interrupt_type == "CLARIFYING":
            entity_names: list[str] = []
            for obj in extraction.objects:
                entity_names.extend(obj.entities or [])
            if _resumption_confirms(
                user_interrupt=user_interrupt,
                luna_resumption=logical_turn.luna_resumption,
                extracted_entities=entity_names,
            ):
                _tag_extraction_metadata(extraction, confirmed_by_resumption=True)
                logger.info(
                    f"[SCRIBE] clarifying_confirmed "
                    f"interrupt_turn_id={logical_turn.interrupt_turn_id} "
                    f"node_count={len(extraction.objects)}"
                )

        # File via Librarian (only when non-empty OR flow signal present).
        if not extraction.is_empty() or extraction.flow_signal is not None:
            await self._send_to_librarian(extraction)
        for update in entity_updates:
            await self._send_entity_update_to_librarian(update)

        logger.info(
            f"[SCRIBE-EVENT] logical_turn_dispatched "
            f"resumption_turn_id={logical_turn.resumption_turn_id} "
            f"interrupt_type={logical_turn.interrupt_type} "
            f"node_count={len(extraction.objects)}"
        )

    def _extract_entity_names(self, text: str) -> list:
        """Quick extraction of proper nouns from a claim."""
        words = text.split()
        entities = []
        for i, word in enumerate(words):
            cleaned = _re.sub(r'[^\w]', '', word)
            if cleaned and cleaned[0].isupper() and i > 0 and len(cleaned) > 2:
                entities.append(cleaned)
        return list(set(entities))

    def _detect_user_correction(self, user_turn: str) -> bool:
        """Detect when user is correcting something Luna said."""
        for pattern in self._USER_CORRECTION_PATTERNS:
            if pattern.search(user_turn):
                return True
        return False

    # =========================================================================
    # CACHE ACTOR INTEGRATION
    # =========================================================================

    async def _send_to_cache(
        self,
        extraction: ExtractionOutput,
        flow_signal: FlowSignal,
        source: str = "unknown",
        session_id: str = "",
    ) -> None:
        """Send extraction + flow data to CacheActor for cache write + dimensional feed."""
        if self.engine:
            cache_actor = self.engine.get_actor("cache")
            if cache_actor:
                await self.send(cache_actor, Message(
                    type="cache_update",
                    payload={
                        "extraction": extraction.to_dict(),
                        "flow_signal": flow_signal.to_dict(),
                        "source": source,
                        "session_id": session_id,
                    },
                ))
                logger.debug("Ben: Sent extraction to CacheActor")
            else:
                logger.warning("Ben: CacheActor not available, cache not updated")

    # =========================================================================
    # LIBRARIAN INTEGRATION
    # =========================================================================

    async def _is_early_relationship(self) -> bool:
        """True if < EARLY_RELATIONSHIP_TURN_THRESHOLD conversation turns."""
        if not self.engine:
            return False
        try:
            matrix = self.engine.actors.get("matrix")
            if matrix and matrix._matrix:
                count = await matrix._matrix.db.fetchone(
                    "SELECT COUNT(*) FROM conversation_turns"
                )
                return (count[0] if count else 0) < EARLY_RELATIONSHIP_TURN_THRESHOLD
        except Exception:
            return False

    async def _send_to_librarian(self, extraction: ExtractionOutput) -> None:
        """Send extraction to Librarian for filing."""
        # Boost confidence for early conversations (first ~10-15 conversations)
        if await self._is_early_relationship():
            from luna.core.owner import get_owner
            owner_name = (get_owner().display_name or "").lower()
            for obj in extraction.objects:
                if obj.type in ("FACT", "PREFERENCE", "MEMORY"):
                    obj.confidence = min(1.0, obj.confidence + EARLY_CONFIDENCE_BOOST)
                if owner_name and owner_name in obj.content.lower():
                    obj.confidence = min(1.0, obj.confidence + EARLY_OWNER_CONFIDENCE_BOOST)

        if self.engine:
            librarian = self.engine.get_actor("librarian")
            if librarian:
                await self.send(librarian, Message(
                    type="file",
                    payload=extraction.to_dict(),
                ))
                logger.debug(f"Ben: Sent extraction to Librarian")
            else:
                logger.warning("Ben: Librarian not available, extraction not filed")
        else:
            logger.warning("Ben: No engine reference, can't send to Librarian")

    async def _send_entity_update_to_librarian(self, entity_update: EntityUpdate) -> None:
        """Send entity update to Librarian for filing."""
        if self.engine:
            librarian = self.engine.get_actor("librarian")
            if librarian:
                await self.send(librarian, Message(
                    type="entity_update",
                    payload=entity_update.to_dict(),
                ))
                logger.debug(f"Ben: Sent entity update for '{entity_update.name}' to Librarian")
            else:
                logger.warning("Ben: Librarian not available, entity update not filed")
        else:
            logger.warning("Ben: No engine reference, can't send entity update to Librarian")

    # =========================================================================
    # STUDY CONTEXT QUEST GENERATION (Phase 4a)
    # =========================================================================

    async def _maybe_create_study_quest(self, extraction) -> None:
        """
        If extraction contains qualifying objects (DECISION/FACT, high-confidence)
        and the active project has auto_quest enabled, create a study_update quest.
        """
        if extraction.is_empty() or not self.engine:
            return

        project_slug = getattr(self.engine, 'active_project', None)
        if not project_slug:
            return

        # Quick check: any DECISION or FACT objects?
        from luna.extraction.types import ExtractionType
        quick_types = {ExtractionType.DECISION, ExtractionType.FACT}
        if not any(obj.type in quick_types and obj.confidence >= 0.7 for obj in extraction.objects):
            return

        try:
            from luna.context.study_context import load_raw_config
            config = load_raw_config(project_slug)
            if not config:
                return

            auto_quest = config.get("study_context", {}).get("auto_quest", {})
            if not auto_quest.get("enabled", False):
                return

            # Parse trigger config
            trigger_type_names = auto_quest.get("trigger_types", ["DECISION", "FACT"])
            trigger_set = set()
            for t in trigger_type_names:
                try:
                    trigger_set.add(ExtractionType(t))
                except ValueError:
                    pass

            min_confidence = auto_quest.get("min_confidence", 0.7)
            qualifying = [
                obj for obj in extraction.objects
                if obj.type in trigger_set and obj.confidence >= min_confidence
            ]
            if not qualifying:
                return

            # Cooldown check
            import time
            cooldown_minutes = auto_quest.get("cooldown_minutes", 30)
            now = time.time()
            if (now - self._last_auto_quest_at) < (cooldown_minutes * 60):
                logger.debug(f"Ben: Auto-quest cooldown active ({cooldown_minutes}m)")
                return

            # Create quest
            from luna_mcp.observatory.tools import tool_observatory_quest_create

            content_lines = [f"- [{obj.type.value}] {obj.content}" for obj in qualifying]
            quest_type = auto_quest.get("quest_type", "contract")

            result = await tool_observatory_quest_create(
                title=f"Study update: {len(qualifying)} new extractions",
                objective=(
                    f"New high-confidence extractions for project '{project_slug}' "
                    f"may warrant a study context update.\n\n"
                    + "\n".join(content_lines)
                ),
                quest_type=quest_type,
                priority="medium",
                subtitle="Auto-generated from extraction pipeline",
                source="study_update",
                journal_prompt=(
                    "Which of these extractions should be added to the study context? "
                    "What section should they go in? Are any already covered?"
                ),
                project=project_slug,
            )

            if result.get("quest_id"):
                self._last_auto_quest_at = now
                logger.info(
                    f"Ben: Created study_update quest {result['quest_id']} "
                    f"for '{project_slug}' ({len(qualifying)} extractions)"
                )

        except Exception as e:
            logger.debug(f"Ben: Auto-quest creation failed: {e}")

    # =========================================================================
    # STATS & LIFECYCLE
    # =========================================================================

    # =========================================================================
    # TURN COMPRESSION (for conversation history tiers)
    # =========================================================================

    async def _handle_compress_turn(self, msg: Message) -> None:
        """
        Handle turn compression request.

        Payload:
        - turn_id: The turn ID to compress
        - content: The full turn content to compress
        - role: The role (user/assistant) for context

        Sends back a compressed summary via send_to_engine.
        """
        payload = msg.payload or {}
        turn_id = payload.get("turn_id")
        content = payload.get("content", "")
        role = payload.get("role", "user")

        if not content:
            logger.warning("Ben: compress_turn received empty content")
            return

        # Generate compression
        compressed = await self.compress_turn(content, role)

        # Send result back
        await self.send_to_engine("turn_compressed", {
            "turn_id": turn_id,
            "compressed": compressed,
            "correlation_id": msg.correlation_id,
        })

    async def compress_turn(self, content: str, role: str = "user") -> str:
        """
        Compress a conversation turn into a one-sentence summary.

        Used by HistoryManager when rotating turns from Active to Recent tier.

        Args:
            content: Full turn text
            role: The role (user/assistant)

        Returns:
            Compressed summary (<50 words)
        """
        # Very short content doesn't need compression
        if len(content) < 100:
            return content

        compression_prompt = f"""Compress this {role} message into ONE sentence under 50 words.
Focus on: what was asked/said, any decisions, key facts mentioned.
Use past tense. No commentary.

Message:
{content}

Compressed:"""

        try:
            # Try local model first for speed
            if self.engine:
                director = self.engine.get_actor("director")
                if director and hasattr(director, "_local") and director._local:
                    local = director._local
                    if local.is_loaded:
                        result = await local.generate(
                            compression_prompt,
                            max_tokens=80,
                        )
                        compressed = result.text.strip()
                        logger.debug(f"Ben: Compressed turn locally ({len(content)} -> {len(compressed)} chars)")
                        return compressed

            # Fallback to Claude — use configured backend model
            if self.config.backend != "disabled" and self.client:
                backend_config = EXTRACTION_BACKENDS.get(self.config.backend, {})
                model = backend_config.get("model", "claude-haiku-4-5-20251001")
                import asyncio
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self.client.messages.create(
                        model=model,
                        max_tokens=80,
                        temperature=0.3,
                        messages=[{"role": "user", "content": compression_prompt}]
                    ),
                )
                compressed = response.content[0].text.strip()
                logger.debug(f"Ben: Compressed turn via {model} ({len(content)} -> {len(compressed)} chars)")
                return compressed

        except Exception as e:
            logger.error(f"Ben: Compression failed: {e}")

        # Ultimate fallback: truncate
        return content[:200] + "..." if len(content) > 200 else content

    def get_stats(self) -> dict:
        """Get extraction statistics."""
        avg_time = (
            self._total_extraction_time_ms / self._extractions_count
            if self._extractions_count > 0
            else 0
        )
        return {
            "backend": self.config.backend,
            "extractions_count": self._extractions_count,
            "objects_extracted": self._objects_extracted,
            "edges_extracted": self._edges_extracted,
            "entity_updates_extracted": self._entity_updates_extracted,
            "avg_extraction_time_ms": avg_time,
            "stack_size": len(self.stack),
            "batch_size": self.config.batch_size,
        }

    def get_extraction_history(self) -> list[dict]:
        """Get recent extraction history."""
        return list(self._extraction_history)

    async def snapshot(self) -> dict:
        """Return state for serialization."""
        base = await super().snapshot()
        base.update({
            "config": {
                "backend": self.config.backend,
                "batch_size": self.config.batch_size,
            },
            "stats": self.get_stats(),
        })
        return base

    async def on_stop(self) -> None:
        """Flush stack before stopping."""
        if self.stack:
            logger.info("Ben: Flushing stack before shutdown")
            await self._flush_stack()
