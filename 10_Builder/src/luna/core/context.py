"""
Revolving Context System for Luna Engine
=========================================

Manages Luna's working memory through a ring-based context system.
Items enter at outer rings and migrate inward based on relevance and access patterns.

Key insight: Context is not just what fits - it's what MATTERS right now.

The context window is assembled dynamically each tick, prioritizing:
1. CORE: Identity (never evicted)
2. INNER: Active conversation turns
3. MIDDLE: Recently accessed memories
4. OUTER: Background context (first to evict)
"""

from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Callable, Deque, Dict, List, Optional
import hashlib
import logging
import uuid

from luna.config.ring_config import ring_config
from luna.core.types import Door, ItemKind
from luna.policy.rules import Rule_2_Decay, Rule_3_Transitions

if TYPE_CHECKING:  # pragma: no cover
    from luna.policy import Policy

logger = logging.getLogger(__name__)


# Try to import tiktoken for accurate token counting
try:
    import tiktoken
    _ENCODER = tiktoken.get_encoding("cl100k_base")
    _HAS_TIKTOKEN = True
except ImportError:
    _ENCODER = None
    _HAS_TIKTOKEN = False
    from luna.diagnostics.maturity import compiled_debug
    compiled_debug(logger, "tiktoken not available, using fallback token counting (len/4)")


def count_tokens(text: str) -> int:
    """
    Count tokens in text.

    Uses tiktoken if available, otherwise falls back to len/4 approximation.

    Args:
        text: The text to count tokens for.

    Returns:
        Estimated token count.
    """
    if _HAS_TIKTOKEN and _ENCODER is not None:
        return len(_ENCODER.encode(text))
    # Fallback: rough approximation (~4 chars per token for English)
    return max(1, len(text) // 4)


class ContextRing(IntEnum):
    """
    Concentric rings of context priority.

    Lower number = higher priority = closer to Luna's attention.
    Items migrate between rings based on relevance decay and access patterns.
    """
    CORE = 0    # Identity, personality - NEVER evicted
    INNER = 1   # Active conversation, current task
    MIDDLE = 2  # Recently accessed memories, relevant context
    OUTER = 3   # Background context, candidate for eviction


class ContextSource(IntEnum):
    """
    Sources that contribute context items.

    Each source has different priority weights for queue processing.
    """
    IDENTITY = 0      # Luna's core identity (highest priority)
    CONVERSATION = 1  # Current conversation turns
    MEMORY = 2        # Retrieved memories from substrate
    TOOL = 3          # Tool call results
    TASK = 4          # Current task context
    SCRIBE = 5        # Recent extraction results (Ben)
    LIBRARIAN = 6     # Retrieved knowledge (Dude)
    # Phase 1 policy aliases (Spec v0.2, Rule 1 door model)
    USER_TURN = CONVERSATION
    MEMORY_MATRIX = MEMORY
    NEXUS = LIBRARIAN


# Default priority weights for queue processing
DEFAULT_SOURCE_WEIGHTS: Dict[ContextSource, float] = {
    ContextSource.IDENTITY: 1.0,
    ContextSource.CONVERSATION: 0.9,
    ContextSource.MEMORY: 0.7,
    ContextSource.TOOL: 0.8,
    ContextSource.TASK: 0.75,
    ContextSource.SCRIBE: 0.6,
    ContextSource.LIBRARIAN: 0.65,
}

# Default TTL in TURNS (conversation exchanges) for each source
# This is more meaningful than time - an item expires after N turns, not N seconds
# Note: A "turn" = one user message + one Luna response
DEFAULT_SOURCE_TTL_TURNS: Dict[ContextSource, int] = {
    ContextSource.IDENTITY: -1,       # Never expires (-1 = infinite)
    ContextSource.CONVERSATION: 20,   # Last 20 turns of conversation (was 10)
    ContextSource.MEMORY: 25,         # Memories persist a bit longer
    ContextSource.TOOL: 5,            # Tool results are short-lived
    ContextSource.TASK: 30,           # Tasks persist through completion
    ContextSource.SCRIBE: 10,         # Extraction results
    ContextSource.LIBRARIAN: 20,      # Retrieved knowledge
}


def _default_kind_for_source(source: ContextSource) -> ItemKind:
    """Best-effort kind inference for callers that only pass source."""
    if source in (ContextSource.CONVERSATION, ContextSource.TASK):
        return ItemKind.CONVERSATION
    if source == ContextSource.IDENTITY:
        return ItemKind.IDENTITY
    if source == ContextSource.SCRIBE:
        return ItemKind.REFLECTION
    if source in (ContextSource.LIBRARIAN, ContextSource.MEMORY):
        return ItemKind.MEMORY
    return ItemKind.OBSERVATION

@dataclass
class ContextItem:
    """
    A single item in the context window.

    Items have:
    - Relevance score (0-1) that decays per turn
    - Ring placement based on source and relevance
    - Token count for budget management
    - TTL in TURNS for automatic expiration (not time!)

    Attributes:
        id: Unique identifier for this item.
        content: The text content of this context item.
        source: Where this item came from (ContextSource enum).
        ring: Current ring placement (ContextRing enum).
        relevance: Relevance score from 0.0 to 1.0.
        created_at_turn: Turn number when this item was created.
        last_accessed_turn: Turn number when this item was last accessed.
        ttl_turns: Time-to-live in conversation turns (-1 = never expires).
        tokens: Token count for this item's content.
        metadata: Additional metadata dictionary.
    """
    content: str
    source: ContextSource
    ring: ContextRing = ContextRing.MIDDLE
    relevance: float = 1.0  # 0.0 to 1.0
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at_turn: Optional[int] = None
    last_accessed_turn: Optional[int] = None
    ttl_turns: int = 10  # Default: expires after 10 turns
    tokens: int = field(init=False)
    metadata: Dict[str, Any] = field(default_factory=dict)
    content_hash: str = field(init=False)   # MD5 fingerprint for ring-level dedup (Phase 1.5f)
    cite_count: int = 1                      # times this content has been re-retrieved
    # Memory Policy v1.0 — Phase 0 scaffolding (Spec v0.2 §4, §7 Rule 1.2).
    # Defaults preserve current behavior; no rule reads these fields yet.
    lock_in: float = 0.6
    kind: ItemKind = ItemKind.MEMORY
    permanent: bool = False
    # Rule 1 provenance (Phase 1): every admitted item flows through a Door.
    door: Optional[Door] = None
    # Rule 3 (Phase 2 Phase 3): per-tick reactivation signal. Reset at the
    # end of each ``advance_turn``. ``accessed_this_tick`` is the basic
    # promotion gate (3.2); ``tick_relevance_jump`` accumulates access
    # boost for the fast-track gate (3.3).
    accessed_this_tick: bool = False
    tick_relevance_jump: float = 0.0
    # Rule 5 (Phase 4): hashes of merged-in variants for cosine merges. Hash
    # merges leave this empty (content is byte-equal). Cosine merges append
    # the incoming.content_hash here; never duplicates.
    variants: List[str] = field(default_factory=list)
    _turn_provider: Optional[Callable[[], int]] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Calculate token count on creation and set TTL based on source."""
        self.tokens = count_tokens(self.content)
        # Set TTL based on source if still at default
        if self.ttl_turns == 10 and self.source in DEFAULT_SOURCE_TTL_TURNS:
            self.ttl_turns = DEFAULT_SOURCE_TTL_TURNS[self.source]
        # Compute content fingerprint for dedup — normalize before hashing
        normalized = " ".join(self.content.lower().strip().split())
        self.content_hash = hashlib.md5(normalized.encode()).hexdigest()[:12]

    def bind_turn_provider(self, turn_provider: Callable[[], int]) -> None:
        """Bind this item to a context-local turn counter."""
        self._turn_provider = turn_provider
        now = turn_provider()
        if self.created_at_turn is None:
            self.created_at_turn = now
        if self.last_accessed_turn is None:
            self.last_accessed_turn = now

    def _current_turn(self) -> int:
        """Return the current turn for this item's owning context."""
        if self._turn_provider is None:
            return self.created_at_turn or 0
        return self._turn_provider()

    def decay(self, factor: float = 0.95) -> None:
        """
        Apply relevance decay.

        Called periodically to reduce relevance of items not being accessed.
        Core items (ring 0) are immune to decay.

        Args:
            factor: Decay multiplier (0.95 = 5% decay per call).
        """
        if self.ring == ContextRing.CORE:
            return  # Core identity never decays
        self.relevance = max(0.0, self.relevance * factor)

    def access(self, relevance_boost: float = 0.1) -> None:
        """
        Mark item as accessed, boosting relevance.

        Called when item is used in context assembly or matched by query.

        Args:
            relevance_boost: Amount to increase relevance (capped at 1.0).
        """
        self.last_accessed_turn = self._current_turn()
        before = self.relevance
        self.relevance = min(1.0, self.relevance + relevance_boost)
        self.accessed_this_tick = True
        self.tick_relevance_jump += self.relevance - before

    @property
    def is_expired(self) -> bool:
        """Check if item has exceeded its TTL in turns."""
        if self.ttl_turns == -1:  # -1 means never expires
            return False
        age_turns = self._current_turn() - (self.created_at_turn or 0)
        return age_turns > self.ttl_turns

    @property
    def age_turns(self) -> int:
        """Turns since creation."""
        return self._current_turn() - (self.created_at_turn or 0)

    @property
    def idle_turns(self) -> int:
        """Turns since last access."""
        return self._current_turn() - (self.last_accessed_turn or 0)

    def __repr__(self) -> str:
        preview = self.content[:40].replace('\n', ' ')
        return (f"ContextItem(id={self.id}, ring={self.ring.name}, "
                f"rel={self.relevance:.2f}, tok={self.tokens}, age={self.age_turns}t, '{preview}...')")


class QueueManager:
    """
    Manages multiple input queues for different context sources.

    Each source type has its own queue with configurable priority weights.
    The RevolvingContext pulls from these queues during context assembly.

    Key insight: Different sources have different urgency and importance.
    Conversation messages need immediate attention; memories can wait.

    Attributes:
        _max_size: Maximum items per queue.
        _weights: Priority weights for each source.
        _queues: Dictionary mapping sources to their deque queues.
    """

    def __init__(
        self,
        max_queue_size: int = 50,
        weights: Optional[Dict[ContextSource, float]] = None,
        turn_provider: Optional[Callable[[], int]] = None,
    ):
        """
        Initialize queue manager.

        Args:
            max_queue_size: Maximum items per queue.
            weights: Priority weights for each source (higher = more priority).
        """
        self._max_size = max_queue_size
        self._weights = weights or DEFAULT_SOURCE_WEIGHTS.copy()
        self._turn_provider = turn_provider

        # Create a deque for each source type
        self._queues: Dict[ContextSource, Deque[ContextItem]] = {
            source: deque(maxlen=max_queue_size)
            for source in ContextSource
        }

        # Statistics
        self._total_pushed = 0
        self._total_polled = 0

    def push(self, item: ContextItem) -> bool:
        """
        Push an item onto its source queue.

        Args:
            item: Context item to queue.

        Returns:
            True if added without eviction, False if queue was full (oldest was evicted).
        """
        if self._turn_provider is not None:
            item.bind_turn_provider(self._turn_provider)

        queue = self._queues[item.source]
        was_full = len(queue) >= self._max_size
        queue.append(item)
        self._total_pushed += 1

        if was_full:
            logger.debug(f"Queue {item.source.name} full, oldest item evicted")

        return not was_full

    def poll_all(self) -> List[ContextItem]:
        """
        Poll all queues, returning items sorted by weighted priority.

        Items from higher-weight sources appear first.
        Within same weight, older items (FIFO by turn) appear first.

        Returns:
            List of all queued items, sorted by priority.
        """
        items: List[tuple[float, int, ContextItem]] = []

        for source, queue in self._queues.items():
            weight = self._weights.get(source, 0.5)
            while queue:
                item = queue.popleft()
                items.append((weight, item.created_at_turn, item))
                self._total_polled += 1

        # Sort by weight (descending), then turn (ascending for FIFO)
        items.sort(key=lambda x: (-x[0], x[1]))

        return [item for _, _, item in items]

    def poll_up_to(self, max_items: int) -> List[ContextItem]:
        """
        Poll up to max_items without draining the remainder.

        Items preserve the same weighted-priority semantics as poll_all().
        """
        if max_items <= 0:
            return []

        items: List[ContextItem] = []
        while len(items) < max_items:
            candidates: List[tuple[float, int, ContextSource]] = []
            for source, queue in self._queues.items():
                if not queue:
                    continue
                head = queue[0]
                created_at_turn = head.created_at_turn or 0
                candidates.append((self._weights.get(source, 0.5), created_at_turn, source))

            if not candidates:
                break

            _, _, source = min(candidates, key=lambda x: (-x[0], x[1]))
            items.append(self._queues[source].popleft())
            self._total_polled += 1

        return items

    def poll_source(self, source: ContextSource, max_items: int = 10) -> List[ContextItem]:
        """
        Poll items from a specific source queue.

        Args:
            source: Source to poll from.
            max_items: Maximum items to return.

        Returns:
            List of items from the specified source.
        """
        items: List[ContextItem] = []
        queue = self._queues[source]

        while queue and len(items) < max_items:
            items.append(queue.popleft())
            self._total_polled += 1

        return items

    def peek_source(self, source: ContextSource) -> Optional[ContextItem]:
        """
        Peek at the next item in a source queue without removing it.

        Args:
            source: Source queue to peek at.

        Returns:
            The next item or None if queue is empty.
        """
        queue = self._queues[source]
        return queue[0] if queue else None

    def size(self, source: Optional[ContextSource] = None) -> int:
        """
        Get queue size for a source, or total across all sources.

        Args:
            source: Specific source to check, or None for total.

        Returns:
            Number of items in specified queue(s).
        """
        if source is not None:
            return len(self._queues[source])
        return sum(len(q) for q in self._queues.values())

    def clear(self, source: Optional[ContextSource] = None) -> int:
        """
        Clear one or all queues.

        Args:
            source: Specific source to clear, or None for all.

        Returns:
            Number of items cleared.
        """
        count = 0
        if source is not None:
            count = len(self._queues[source])
            self._queues[source].clear()
        else:
            for queue in self._queues.values():
                count += len(queue)
                queue.clear()
        return count

    def stats(self) -> Dict[str, Any]:
        """
        Get queue statistics.

        Returns:
            Dictionary containing queue stats.
        """
        return {
            "total_pushed": self._total_pushed,
            "total_polled": self._total_polled,
            "queue_sizes": {
                source.name: len(queue)
                for source, queue in self._queues.items()
            },
            "weights": {
                source.name: weight
                for source, weight in self._weights.items()
            },
        }

    def __repr__(self) -> str:
        total = self.size()
        return f"QueueManager(total_items={total}, queues={len(self._queues)})"


class RevolvingContext:
    """
    Luna's working memory - a revolving context window.

    Items are organized in concentric rings:
    - CORE: Identity (never evicted)
    - INNER: Active conversation
    - MIDDLE: Relevant memories
    - OUTER: Background context (evicted first)

    The context window is assembled dynamically each tick,
    respecting the token budget while prioritizing inner rings.

    Key insight: It's not about cramming more context in.
    It's about having the RIGHT context at the right moment.

    Attributes:
        token_budget: Maximum tokens allowed in assembled context.
        rings: Dictionary mapping ContextRing to list of items.
        queue_manager: QueueManager for incoming items.
    """

    def __init__(
        self,
        token_budget: int = 8000,
        decay_factor: float = 0.95,
        rebalance_threshold: float = 0.3
    ):
        """
        Initialize revolving context.

        Args:
            token_budget: Maximum tokens in assembled context window.
            decay_factor: Relevance decay multiplier per cycle.
            rebalance_threshold: Relevance threshold for ring demotion.
        """
        self.token_budget = token_budget
        self._decay_factor = decay_factor
        self._rebalance_threshold = rebalance_threshold

        # Ring storage
        self.rings: Dict[ContextRing, List[ContextItem]] = {
            ring: [] for ring in ContextRing
        }

        self._current_turn = 0

        # Queue manager for incoming items
        self.queue_manager = QueueManager(turn_provider=self._get_current_turn)

        # Core identity (special handling)
        self._core_identity: Optional[ContextItem] = None

        # O(1) content-hash index for dedup — kept in sync with rings (Phase 1.5f)
        self._hash_index: Dict[str, ContextItem] = {}

        # Rule 5 (Phase 4): per-tick cosine throttle. Counter resets in
        # ``_reset_tick_state``. ``_cosine_lookup`` is the pluggable hook
        # that performs the embedding-backed similarity search; it stays
        # None until the cosine pipeline verification handoff wires it.
        self._cosine_calls_this_tick: int = 0
        self._cosine_lookup: Optional[Callable[..., Any]] = None

        # Policy snapshot. Strict load (Phase 2): a missing or malformed
        # policy.yaml aborts construction so corrupted config cannot mask
        # budget or admission drift.
        self._policy: "Policy" = self._load_policy()

        # Statistics
        self._total_added = 0
        self._total_evicted = 0
        self._assembly_count = 0

    @property
    def current_turn(self) -> int:
        """Get current turn number."""
        return self._current_turn

    def _get_current_turn(self) -> int:
        """Private turn provider used by context items and queues."""
        return self._current_turn

    def advance_turn(self) -> int:
        """
        Advance the conversation turn counter.

        Call this once per conversation exchange (user message + response).
        This drives TTL expiration and decay for all context items.

        Returns:
            The new turn number.
        """
        self._current_turn += 1

        Rule_2_Decay.tick_decay(self, self._policy)
        Rule_3_Transitions.rebalance(self, self._policy)
        Rule_2_Decay.enforce_budget(self, self._policy)
        self._reset_tick_state()

        logger.debug(f"Advanced to turn {self._current_turn}")
        return self._current_turn

    def _reset_tick_state(self) -> None:
        """Clear per-tick reactivation flags after Rule 3 has read them."""
        for ring_items in self.rings.values():
            for item in ring_items:
                item.accessed_this_tick = False
                item.tick_relevance_jump = 0.0
        # Rule 5 (Phase 4): reset cosine throttle counter alongside per-item
        # tick state so the budget is per-tick, not per-ingest.
        self._cosine_calls_this_tick = 0

    def set_core_identity(
        self,
        identity_text: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ContextItem:
        """
        Set Luna's core identity. This is NEVER evicted.

        Args:
            identity_text: Luna's identity/personality description.
            metadata: Optional metadata for the identity item.

        Returns:
            The created ContextItem.
        """
        self._core_identity = ContextItem(
            content=identity_text,
            source=ContextSource.IDENTITY,
            ring=ContextRing.CORE,
            relevance=1.0,
            ttl_turns=-1,  # Never expires
            metadata=metadata or {},
            kind=ItemKind.IDENTITY,
            lock_in=1.0,
            permanent=True,
        )
        self._core_identity.bind_turn_provider(self._get_current_turn)

        # Clear and set in CORE ring
        self.rings[ContextRing.CORE] = [self._core_identity]
        logger.info(f"Core identity set ({self._core_identity.tokens} tokens)")

        return self._core_identity

    def add(
        self,
        content: str,
        source: ContextSource,
        ring: Optional[ContextRing] = None,
        door: Optional[Door] = None,
        relevance: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ContextItem:
        """
        Add an item to the context.

        Args:
            content: Text content.
            source: Source type.
            ring: Target ring (auto-assigned if None).
            relevance: Initial relevance score.
            metadata: Optional metadata.

        Returns:
            The created ContextItem.
        """
        if door is Door.NEXUS and ring is not None:
            logger.warning(
                "[RULE1] explicit ring=%s overrides NEXUS routing for source=%s",
                ring.name, source.name,
            )

        item = ContextItem(
            content=content,
            source=source,
            ring=ring if ring is not None else ContextRing.MIDDLE,
            relevance=relevance,
            metadata=metadata or {},
            kind=_default_kind_for_source(source),
        )
        item = self._ingest_item(item, ring=ring, door=door)

        Rule_2_Decay.enforce_budget(self, self._policy)

        logger.debug(f"Added context item: {item}")
        return item

    def add_from_queues(self, max_items: int = 20) -> int:
        """
        Pull items from the queue manager and add to context.

        Args:
            max_items: Maximum items to pull.

        Returns:
            Number of items added.
        """
        items = self.queue_manager.poll_up_to(max_items)

        for item in items:
            self._ingest_item(item)

        if items:
            Rule_2_Decay.enforce_budget(self, self._policy)
            logger.debug(f"Added {len(items)} items from queues")

        return len(items)

    def _ingest_item(
        self,
        item: ContextItem,
        ring: Optional[ContextRing] = None,
        door: Optional[Door] = None,
    ) -> ContextItem:
        """Insert an existing item while preserving dedup and index invariants."""
        item.bind_turn_provider(self._get_current_turn)
        resolved_door = door or item.door or self._infer_door(item.source)
        item.door = resolved_door

        # Rule 5 (Phase 4): dedup pass — hash-first, cosine-second behind flag.
        from luna.policy.rules import Rule_5_Dedup
        merge_target = Rule_5_Dedup.find_merge_target(item, self, self._policy)
        if merge_target is not None:
            outcome = (
                "hash_hit"
                if merge_target.content_hash == item.content_hash
                else "cosine_hit"
            )
            merged = Rule_5_Dedup.merge(
                item, merge_target, self._policy, current_turn=self._current_turn
            )
            logger.debug(
                "[RULE5] %s incoming=%s target=%s cite_count=%d rel=%.2f '%s...'",
                outcome, item.id, merged.id,
                merged.cite_count, merged.relevance, item.content[:40],
            )
            return merged

        target_ring = ring
        if target_ring is None and resolved_door is not None:
            from luna.policy.rules import Rule_1_Admission
            decision = Rule_1_Admission.admit(item=item, door=resolved_door, policy=self._policy)
            if not decision.admit:
                logger.debug("[RULE1] rejected item id=%s reason=%s", item.id, decision.reason)
                return item
            target_ring = ContextRing[decision.ring] if decision.ring is not None else None

        if target_ring is None:
            target_ring = self._infer_ring(item.source)

        if target_ring == ContextRing.CORE and item.source != ContextSource.IDENTITY:
            target_ring = ContextRing.INNER
            logger.warning("Cannot add non-identity item to CORE ring, placing in INNER")

        item.ring = target_ring
        self.rings[target_ring].append(item)
        self._hash_index[item.content_hash] = item
        self._total_added += 1
        logger.debug(
            "[RULE5] new_item id=%s hash=%s ring=%s",
            item.id, item.content_hash, target_ring.name,
        )
        return item

    def _load_policy(self) -> "Policy":
        """Strict load (Phase 2). A missing or invalid policy.yaml aborts.

        Raises:
            FileNotFoundError: ``config/policy.yaml`` is absent.
            PolicyValidationError: ``policy.yaml`` violates a loader invariant.
        """
        from luna.config.ring_config import POLICY_PATH
        from luna.policy import PolicyLoader

        if not POLICY_PATH.exists():
            raise FileNotFoundError(
                f"Memory policy required at {POLICY_PATH}; engine refuses to start"
            )
        return PolicyLoader.load_and_validate(POLICY_PATH)

    def _infer_door(self, source: ContextSource) -> Optional[Door]:
        """Map legacy ContextSource values into the three sanctioned doors."""
        if source == ContextSource.IDENTITY:
            return None
        if source in (ContextSource.CONVERSATION, ContextSource.TASK):
            return Door.USER_TURN
        if source == ContextSource.LIBRARIAN:
            return Door.NEXUS
        return Door.MEMORY_MATRIX

    def _infer_ring(self, source: ContextSource) -> ContextRing:
        """
        Infer appropriate ring from source type.

        Args:
            source: The context source.

        Returns:
            The appropriate ContextRing for this source.
        """
        ring_mapping = {
            ContextSource.IDENTITY: ContextRing.CORE,
            ContextSource.CONVERSATION: ContextRing.INNER,
            ContextSource.TASK: ContextRing.INNER,
            ContextSource.TOOL: ContextRing.MIDDLE,
            ContextSource.MEMORY: ContextRing.MIDDLE,
            ContextSource.SCRIBE: ContextRing.OUTER,
            ContextSource.LIBRARIAN: ContextRing.MIDDLE,
        }
        return ring_mapping.get(source, ContextRing.OUTER)

    def _rebalance_rings(self) -> int:
        """Compatibility shim over ``Rule_3_Transitions.rebalance``.

        Returns the count of movements so legacy tests that assert on the
        integer return value keep working.
        """
        return len(Rule_3_Transitions.rebalance(self, self._policy))

    def get_context_window(
        self,
        max_tokens: Optional[int] = None,
        include_metadata: bool = False
    ) -> str:
        """
        Assemble the context window for LLM consumption.

        Prioritizes inner rings, respecting token budget.

        Args:
            max_tokens: Override token budget (uses self.token_budget if None).
            include_metadata: Include source/ring info in output.

        Returns:
            Assembled context string.
        """
        budget = max_tokens or self.token_budget
        self._assembly_count += 1

        # MIDDLE breakdown (Phase 1.5a diagnostic — confirms MEMORY items are present)
        middle_items = self.rings[ContextRing.MIDDLE]
        if middle_items:
            from collections import defaultdict
            by_source: dict = defaultdict(list)
            for _item in middle_items:
                by_source[_item.source.name].append(_item.relevance)
            breakdown = {
                s: {"n": len(v), "avg_rel": round(sum(v) / len(v), 3), "max_rel": round(max(v), 3)}
                for s, v in by_source.items()
            }
            logger.warning("[MIDDLE-BREAKDOWN] %s", breakdown)

        parts: List[str] = []
        used_tokens = 0

        # Process rings from innermost to outermost
        for ring in ContextRing:
            items = self.rings[ring]

            # Sort by relevance within ring (highest first)
            sorted_items = sorted(items, key=lambda x: x.relevance, reverse=True)

            for item in sorted_items:
                # Check if we have room
                if used_tokens + item.tokens > budget:
                    continue  # Skip but keep checking (smaller items might fit)

                # Mark as accessed
                item.access(relevance_boost=0.05)

                # Format content
                if include_metadata:
                    prefix = f"[{item.source.name}/{item.ring.name}] "
                    content = prefix + item.content
                else:
                    content = item.content

                parts.append(content)
                used_tokens += item.tokens

        logger.debug(f"Assembled context: {used_tokens}/{budget} tokens, {len(parts)} items")
        return "\n\n".join(parts)

    def query(self, keywords: str, max_results: int = 10) -> List[ContextItem]:
        """
        Simple keyword search across all context items.

        Args:
            keywords: Space-separated keywords to search for.
            max_results: Maximum results to return.

        Returns:
            Matching items sorted by relevance.
        """
        keywords_lower = keywords.lower().split()
        matches: List[tuple[float, ContextItem]] = []

        for ring in ContextRing:
            for item in self.rings[ring]:
                content_lower = item.content.lower()

                # Count keyword matches
                match_count = sum(1 for kw in keywords_lower if kw in content_lower)

                if match_count > 0:
                    # Score combines match count and relevance
                    score = (match_count / len(keywords_lower)) * item.relevance
                    matches.append((score, item))

        # Sort by score descending
        matches.sort(key=lambda x: x[0], reverse=True)

        # Mark accessed items
        results: List[ContextItem] = []
        for _, item in matches[:max_results]:
            item.access(relevance_boost=0.1)
            results.append(item)

        return results

    def _total_tokens(self) -> int:
        """
        Calculate total tokens across all rings.

        Returns:
            Total token count.
        """
        return sum(
            item.tokens
            for ring_items in self.rings.values()
            for item in ring_items
        )

    def _item_count(self) -> int:
        """
        Count total items across all rings.

        Returns:
            Total item count.
        """
        return sum(len(items) for items in self.rings.values())

    def stats(self) -> Dict[str, Any]:
        """
        Get context statistics.

        Returns:
            Dictionary containing context stats.
        """
        ring_stats: Dict[str, Dict[str, Any]] = {}
        for ring in ContextRing:
            items = self.rings[ring]
            ring_stats[ring.name] = {
                "count": len(items),
                "tokens": sum(item.tokens for item in items),
                "avg_relevance": (
                    sum(item.relevance for item in items) / len(items)
                    if items else 0.0
                ),
            }

        return {
            "total_items": self._item_count(),
            "total_tokens": self._total_tokens(),
            "token_budget": self.token_budget,
            "budget_used_pct": (
                self._total_tokens() / self.token_budget * 100
                if self.token_budget else 0
            ),
            "total_added": self._total_added,
            "total_evicted": self._total_evicted,
            "assembly_count": self._assembly_count,
            "has_core_identity": self._core_identity is not None,
            "rings": ring_stats,
            "queue_stats": self.queue_manager.stats(),
        }

    def clear(self, preserve_core: bool = True) -> int:
        """
        Clear all context items.

        Args:
            preserve_core: If True, keep core identity.

        Returns:
            Number of items cleared.
        """
        count = 0

        for ring in ContextRing:
            if ring == ContextRing.CORE and preserve_core:
                continue
            count += len(self.rings[ring])
            self.rings[ring] = []

        if not preserve_core:
            self._core_identity = None

        # Rebuild hash index from whatever items remain (Phase 1.5f)
        self._hash_index = {
            item.content_hash: item
            for ring_items in self.rings.values()
            for item in ring_items
        }

        logger.info(f"Cleared {count} context items (preserve_core={preserve_core})")
        return count

    def __repr__(self) -> str:
        return (f"RevolvingContext(items={self._item_count()}, "
                f"tokens={self._total_tokens()}/{self.token_budget})")
