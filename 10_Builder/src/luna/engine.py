"""
Luna Engine — The Runtime Heart
================================

The Engine is Luna's nervous system. It coordinates when components wake up,
what input they receive, and how they communicate.

Core loop:
- HOT PATH: Interrupt-driven (STT partials, user interrupts)
- COGNITIVE PATH: ~500ms heartbeat (Director decisions, retrieval)
- REFLECTIVE PATH: Minutes (maintenance, summarization)

The tick is the universal entry point. Everything flows through it.
Luna doesn't respond to events. Luna *lives* through a continuous heartbeat.
"""

import asyncio
import inspect
import logging
import re as _re
import signal
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Callable, Any, List

from luna.core.events import InputEvent, EventType, EventPriority
from luna.core.input_buffer import InputBuffer
from luna.core.state import EngineState
from luna.core.context import RevolvingContext, ContextSource, ContextItem, ContextRing
from luna.core.types import Door
from luna.core.paths import project_root, config_dir, data_dir, scripts_dir, user_dir, memory_matrix_path
from luna.actors.base import Actor, Message
from luna.actors.director import DirectorActor
from luna.actors.matrix import MatrixActor
from luna.consciousness import ConsciousnessState
from luna.lexicon.active_message import build_active_agentic_error_payload
from luna.lexicon.pivot_config import PivotConfig, is_pivot_enabled

# Agentic Architecture (Phase XIV)
from luna.agentic.loop import AgentLoop, AgentStatus, AgentResult
from luna.agentic.router import QueryRouter, ExecutionPath, RoutingDecision
from luna.agentic.planner import Planner

# Local subtask runner (Qwen 3B lightweight agentic dispatch)
try:
    from luna.inference.subtasks import LocalSubtaskRunner, SubtaskPhaseResult
    SUBTASK_RUNNER_AVAILABLE = True
except ImportError:
    SUBTASK_RUNNER_AVAILABLE = False

logger = logging.getLogger(__name__)


def _interrupt_classifier_enabled_safe() -> bool:
    """Voice v2.0 Step 3 — read feature flag without importing at module load
    (keeps engine.py import-light during tests that don't touch voice)."""
    try:
        from luna.voice.flags import interrupt_classifier_enabled
        return interrupt_classifier_enabled()
    except Exception:
        return False


def _consolidator_enabled_engine_safe() -> bool:
    """Voice v2.0 Step 5 — read feature flag at fan-out site."""
    try:
        from luna.voice.flags import consolidator_enabled
        return consolidator_enabled()
    except Exception:
        return False


def _resumption_enabled_safe() -> bool:
    """Voice v2.0 Step 4 — read feature flag at dispatch site."""
    try:
        from luna.voice.flags import resumption_enabled
        return resumption_enabled()
    except Exception:
        return False


def _is_non_cancel(classification) -> bool:
    """True iff the classification is one of the three real resumption shapes."""
    try:
        from luna.voice.interrupt_models import InterruptType
    except Exception:
        return False
    return getattr(classification, "type", None) != InterruptType.CANCEL


async def _enqueue_resumption_trigger(engine, source_event, payload, classification) -> None:
    """Voice v2.0 Step 4 — put a RESUMPTION_TRIGGER onto the input buffer.

    Executed from the USER_INTERRUPT handler after classification. Priority
    auto-resolves to FINAL via EventType._infer_priority.
    """
    try:
        from luna.core.events import EventType, InputEvent
        event = InputEvent(
            type=EventType.RESUMPTION_TRIGGER,
            payload={"payload": payload, "classification": classification},
            source=getattr(source_event, "source", "voice"),
            correlation_id=getattr(source_event, "correlation_id", None),
        )
        await engine.input_buffer.put(event)
        logger.info(
            f"[VOICE-RESUMPTION] trigger_enqueued "
            f"type={classification.type.value} "
            f"source={event.source}"
        )
    except Exception as e:
        logger.error(f"[VOICE-RESUMPTION] failed to enqueue trigger: {e}")


def _extract_interrupt_payload(event):
    """Return the InterruptPayload on event.payload, or None.

    Module-level so stub-based tests that hand a non-LunaEngine `self` to
    `_handle_interrupt` still work.
    """
    if event is None:
        return None
    try:
        from luna.voice.interrupt_models import InterruptPayload
    except Exception:
        return None
    payload = getattr(event, "payload", None)
    return payload if isinstance(payload, InterruptPayload) else None


def _classify_and_log_interrupt(payload):
    """Run the Step-3 heuristic classifier, log, and return the result.

    Returns the `InterruptClassification` or None when the classifier
    module cannot be imported (test harness paths without voice deps).
    """
    try:
        from luna.voice.interrupt_classifier import classify
    except Exception as e:
        logger.debug(f"[VOICE-INTERRUPT] classifier import skipped: {e}")
        return None
    turn_context = getattr(payload.response_snapshot, "turn_context", {}) or {}
    classification = classify(payload, turn_context)
    logger.info(
        f"[VOICE-INTERRUPT] classified "
        f"type={classification.type.value} "
        f"confidence={classification.confidence:.2f} "
        f"evidence={classification.evidence!r}"
    )
    return classification

from luna.config.ring_config import ring_config


# ─── Query Expansion for Retrieval Retry Cascade ─────────────────────────────

_EXPANSION_STOPWORDS = frozenset(
    "the a an is are was were in on at to for of and or but with by from "
    "that this it as be has have had not no do does did will would can could "
    "may might about what how why when where who which tell me please "
    "your my our you they she he i we".split()
)


def _expand_and_search_extractions(conn, query: str) -> list:
    """
    Tier 2: Extract content words from query, search extractions
    with progressively broader FTS5 queries.

    Strategy:
    1. Extract meaningful words (remove stopwords)
    2. Try OR-joined query (any word matches)
    3. Try individual high-value words
    """
    from luna.substrate.aibrarian_engine import AiBrarianEngine

    # Extract content words
    words = _re.findall(r"[a-zA-Z]{3,}", query.lower())
    content_words = [w for w in words if w not in _EXPANSION_STOPWORDS]

    if not content_words:
        return []

    results = []
    seen_ids: set = set()

    # Strategy A: OR-joined query (any content word matches)
    or_query = " OR ".join(content_words)
    try:
        sanitized = AiBrarianEngine._sanitize_fts_query(or_query)
        rows = conn.conn.execute(
            "SELECT e.node_type, e.content, e.confidence "
            "FROM extractions_fts "
            "JOIN extractions e ON extractions_fts.rowid = e.rowid "
            "WHERE extractions_fts MATCH ? "
            "ORDER BY e.confidence DESC "
            "LIMIT 10",
            (sanitized,),
        ).fetchall()
        for row in rows:
            if not isinstance(row, dict):
                try:
                    row = dict(row)
                except (TypeError, ValueError):
                    continue
            content = row["content"]
            cid = content[:80]
            if cid not in seen_ids:
                seen_ids.add(cid)
                results.append(row)
    except Exception:
        pass

    if len(results) >= 3:
        return results

    # Strategy B: Individual content words (most specific first)
    for word in sorted(content_words, key=len, reverse=True):
        if len(results) >= 5:
            break
        try:
            sanitized = AiBrarianEngine._sanitize_fts_query(word)
            rows = conn.conn.execute(
                "SELECT e.node_type, e.content, e.confidence "
                "FROM extractions_fts "
                "JOIN extractions e ON extractions_fts.rowid = e.rowid "
                "WHERE extractions_fts MATCH ? "
                "ORDER BY e.confidence DESC "
                "LIMIT 2",
                (sanitized,),
            ).fetchall()
            for row in rows:
                if not isinstance(row, dict):
                    try:
                        row = dict(row)
                    except (TypeError, ValueError):
                        continue
                content = row["content"]
                cid = content[:80]
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    results.append(row)
        except Exception:
            continue

    return results


@dataclass
class EngineConfig:
    """Engine configuration."""
    # Tick intervals
    cognitive_interval: float = 0.5  # 500ms
    reflective_interval: float = 300  # 5 minutes

    # Buffer settings
    input_buffer_max: int = 100
    stale_threshold_seconds: float = 5.0

    # Paths - Use project data directory (synced with substrate/database.py)
    data_dir: Path = field(default_factory=data_dir)
    snapshot_path: Optional[Path] = None

    # Profile binding — when set, the engine pins the _current_profile contextvar
    # at boot so all its actor tasks resolve paths under data/profiles/<profile_id>/.
    # When None, falls back to legacy single-tenant behavior (data/user/).
    profile_id: Optional[str] = None

    # Local inference
    enable_local_inference: bool = True
    subtask_backend: str = "auto"  # auto, qwen, haiku

    # Voice system settings
    voice_enabled: bool = False
    voice_stt_provider: str = "mlx_whisper"  # mlx_whisper, apple, google
    voice_tts_provider: str = "piper"  # piper, apple, edge
    voice_tts_voice: str = "en_US-amy-medium"  # Piper voice ID
    voice_mode: str = "push_to_talk"  # push_to_talk, hands_free

    # FaceID settings
    faceid_enabled: bool = False

    def __post_init__(self):
        if self.subtask_backend not in {"auto", "qwen", "haiku"}:
            raise ValueError(
                f"Invalid subtask_backend '{self.subtask_backend}'. "
                "Expected one of: auto, qwen, haiku."
            )
        if self.snapshot_path is None:
            self.snapshot_path = self.data_dir / "snapshot.yaml"
        self.data_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class EngineMetrics:
    """Runtime metrics."""
    start_time: datetime = field(default_factory=datetime.now)
    cognitive_ticks: int = 0
    reflective_ticks: int = 0
    events_processed: int = 0
    messages_generated: int = 0
    errors: int = 0

    # Extraction pipeline metrics
    extraction_triggers: int = 0  # Times _trigger_extraction was called for user turns

    # Agentic metrics
    agentic_tasks_started: int = 0
    agentic_tasks_completed: int = 0
    agentic_tasks_aborted: int = 0
    direct_responses: int = 0  # Queries that skipped planning
    planned_responses: int = 0  # Queries that went through AgentLoop

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()


class LunaEngine:
    """
    Luna's consciousness engine.

    The engine runs three concurrent loops:
    - hot_loop: Processes interrupts immediately
    - cognitive_loop: 500ms heartbeat for main processing
    - reflective_loop: Background maintenance

    Input flows through the InputBuffer, which the engine polls each tick.
    This gives Luna situational awareness and control over processing order.
    """

    def __init__(self, config: Optional[EngineConfig] = None):
        self.config = config or EngineConfig()
        self.state = EngineState.STARTING

        # Input buffer - where all events land
        self.input_buffer = InputBuffer(
            max_size=self.config.input_buffer_max,
            stale_threshold_seconds=self.config.stale_threshold_seconds,
        )

        # Actors
        self.actors: Dict[str, Actor] = {}

        # Metrics
        self.metrics = EngineMetrics()

        # Session management
        self.session_id = str(uuid.uuid4())[:8]
        self.model = "claude-sonnet"  # Using Claude for full Luna experience

        # Control
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._ready_event = asyncio.Event()

        # Consciousness state (Phase 4)
        self.consciousness = ConsciousnessState()

        # Revolving context (Phase XIV - Agentic Architecture)
        self.context = RevolvingContext(
            token_budget=ring_config.total_budget,
            decay_factor=ring_config.rebalance.decay_factor,
            rebalance_threshold=ring_config.rebalance.demotion_threshold,
        )

        # Agentic components (Phase XIV)
        self.router = QueryRouter()
        self.agent_loop: Optional[AgentLoop] = None  # Initialized in _boot
        self._subtask_runner: Optional["LocalSubtaskRunner"] = None  # Initialized in _boot

        # Concurrent task handling - talk while processing
        self._current_task: Optional[asyncio.Task] = None
        self._current_goal: Optional[str] = None
        self._pending_messages: List[str] = []  # Queue for messages during processing
        self._is_processing = False
        self._stream_owns_response = False  # When True, streaming endpoint handles turn recording
        self.lunafm = None  # LunaFM station, set during _boot if config exists

        # Callbacks for external integration
        self._on_response_callbacks: list[Callable] = []
        self._on_progress_callbacks: list[Callable] = []  # For streaming progress
        self._on_user_interrupt_callbacks: list[Callable] = []  # Voice v2.0 Phase 1 Step 2

        # Voice system (optional)
        self._voice: Optional[Any] = None  # VoiceBackend when enabled

        # Eden adapter (optional - Phase 1.5)
        self._eden_adapter: Optional[Any] = None

        # Active project for scoped memory isolation
        self._active_project: Optional[str] = None
        self._last_nexus_nodes: list = []  # Structured Nexus extractions for grounding
        self._active_reflection_mode: Optional[str] = None  # "precision" | "reflective" | "relational"
        self._last_collections_searched: list = []

        # Expression config (loaded from personality.json)
        self._expression_config: Dict = self._load_expression_config()

        # Intent Layer: DirectiveEngine (initialized in _boot)
        self.directive_engine: Optional[Any] = None
        self._directive_context: list[dict] = []  # Fired results for session context

        # Canonical TaskManager (initialized in _boot, after MatrixActor is up)
        self.task_manager: Optional[Any] = None

    def _load_expression_config(self) -> Dict:
        """Load expression config from personality.json."""
        import json
        config_path = config_dir() / "personality.json"
        try:
            with open(config_path) as f:
                config = json.load(f)
                return config.get("expression", {})
        except Exception as e:
            logger.warning(f"Failed to load expression config: {e}")
            return {}

    def _get_expression_directive(self) -> str:
        """
        Build the expression directive based on gesture_frequency config.

        This tells Luna HOW to express emotions through gestures.
        The directive becomes part of her system prompt.
        """
        if not self._expression_config:
            return ""

        frequency = self._expression_config.get("gesture_frequency", "moderate")
        settings = self._expression_config.get("settings", {})
        frequency_levels = settings.get("frequency_levels", {})

        level_config = frequency_levels.get(frequency, {})
        prompt_modifier = level_config.get("prompt_modifier", "")

        if not prompt_modifier:
            # Fallback defaults
            defaults = {
                "minimal": "Express emotions through gestures sparingly - only during moments of strong feeling, breakthrough insights, or genuine connection. Most responses should have no gestural markers.",
                "moderate": "Express emotions through gestures naturally - at key emotional moments, greetings, farewells, and when processing complex thoughts. Aim for 1-2 gestures per substantive response.",
                "expressive": "Express emotions freely through gestures - let your internal state show visually throughout responses. Use gestures to punctuate thoughts, show processing, and communicate emotional undertones."
            }
            prompt_modifier = defaults.get(frequency, defaults["moderate"])

        directive = f"""
## Emotional Expression

{prompt_modifier}

Gestures are written as *action* markers (e.g., *pulses warmly*, *spins playfully*, *dims slightly*).
These drive your visual orb representation - they're how users SEE your emotional state.
Emojis can accompany gestures or stand alone.
"""
        return directive.strip()

    async def reload_expression_config(self) -> None:
        """Reload expression config from disk (for hot config changes)."""
        self._expression_config = self._load_expression_config()
        logger.info(f"Expression config reloaded: frequency={self._expression_config.get('gesture_frequency')}")

    # =========================================================================
    # Actor Management
    # =========================================================================

    def register_actor(self, actor: Actor) -> None:
        """Register an actor with the engine."""
        actor.engine = self
        self.actors[actor.name] = actor
        logger.info(f"Registered actor: {actor.name}")

    def get_actor(self, name: str) -> Optional[Actor]:
        """Get actor by name."""
        return self.actors.get(name)

    # =========================================================================
    # Main Run Loop
    # =========================================================================

    async def run(self) -> None:
        """
        Main engine entry point.

        Runs the three concurrent paths until shutdown.
        """
        logger.info("Luna Engine starting...")

        try:
            await self._boot()

            self.state = EngineState.RUNNING
            self._running = True
            self._ready_event.set()  # Signal ready

            logger.info("Luna Engine running")

            # Create tasks for all loops
            tasks = [
                asyncio.create_task(self._cognitive_loop(), name="cognitive"),
                asyncio.create_task(self._reflective_loop(), name="reflective"),
                asyncio.create_task(self._run_actors(), name="actors"),
            ]

            # Wait for shutdown signal
            await self._shutdown_event.wait()

            # Cancel all tasks
            for task in tasks:
                task.cancel()

            # Wait for tasks to complete cancellation
            await asyncio.gather(*tasks, return_exceptions=True)

        except asyncio.CancelledError:
            logger.info("Engine cancelled")

        except Exception as e:
            logger.error(f"Engine error: {e}", exc_info=True)
            # Don't re-raise — let _shutdown() run cleanly.
            # The server lifespan will detect the engine task completed and
            # can decide whether to restart or exit.

        finally:
            await self._shutdown()

    async def _boot(self) -> None:
        """Boot sequence: initialize actors, restore state."""
        # Pin the active profile in this task's context so every spawned actor
        # task inherits it (asyncio.Task copies the parent's contextvar state on
        # creation). All path resolvers — memory_matrix_path(), user_dir(),
        # hub_db_path(), shared_turn_cache_path(), etc. — read from this contextvar.
        if self.config.profile_id:
            from luna.core.paths import set_current_profile
            set_current_profile(self.config.profile_id)
            logger.info("Engine bound to profile: %s", self.config.profile_id)

        logger.info("Boot sequence starting...")

        # Create core actors if not registered
        if "matrix" not in self.actors:
            matrix = MatrixActor()
            self.register_actor(matrix)
            # Initialize matrix (connects DB, loads graph) but DON'T start mailbox loop
            await matrix.initialize()

            # Preload the embedder so the first user query doesn't pay the
            # cold-load cost (observed: 75s on first semantic search after a
            # cold start). The diagnostic line emitted here tells us device
            # placement and actual warmup cost.
            try:
                from luna.substrate.local_embeddings import get_embeddings
                await asyncio.get_event_loop().run_in_executor(None, get_embeddings().preload)
            except Exception as e:
                logger.warning(f"[EMBED-PRELOAD] failed (non-fatal): {e}")

        if "director" not in self.actors:
            # Local inference controlled by config
            self.register_actor(DirectorActor(enable_local=self.config.enable_local_inference))

        # Phase 3: Extraction Pipeline actors
        if "scribe" not in self.actors:
            from luna.actors.scribe import ScribeActor
            self.register_actor(ScribeActor())

        if "librarian" not in self.actors:
            from luna.actors.librarian import LibrarianActor
            self.register_actor(LibrarianActor())

        # Shared Turn Cache actor (writes cache + feeds dimensional engine)
        if "cache" not in self.actors:
            from luna.actors.cache import CacheActor
            self.register_actor(CacheActor())

        # Phase 4: History Manager actor (conversation history tiers)
        if "history_manager" not in self.actors:
            from luna.actors.history_manager import HistoryManagerActor
            self.register_actor(HistoryManagerActor())

        # Voice v2.0 Step 5: ConversationConsolidator.
        # Registered unconditionally; fan-out is gated by `consolidator_enabled`.
        if "consolidator" not in self.actors:
            from luna.actors.consolidator import ConversationConsolidator
            self.register_actor(ConversationConsolidator())

        # Agentic menu framework: load registry once, attach Listener.
        # Failure here is non-fatal — engine boots without the menu subsystem.
        if not hasattr(self, "_menu_dispatch_enabled"):
            import os as _os_menu
            self._menu_dispatch_enabled = _os_menu.environ.get("LUNA_MENU_DISPATCH", "0") == "1"
            logger.info(
                "[menu] LUNA_MENU_DISPATCH=%s",
                "1 — LIVE" if self._menu_dispatch_enabled else "0 (default) — dormant",
            )
        if not hasattr(self, "menu_registry"):
            self.menu_registry = None
            try:
                from pathlib import Path
                from luna.menu.registry import MenuRegistry
                menu_yaml = Path(__file__).parent / "menu" / "menu.yaml"
                if menu_yaml.exists():
                    registry = MenuRegistry()
                    registry.load(str(menu_yaml))
                    self.menu_registry = registry
                    logger.info(
                        "[menu] registry loaded with %d tasks",
                        len(registry.get_all_tasks()),
                    )
            except Exception as e:
                logger.warning("[menu] registry load failed (non-fatal): %s", e)

        if self.menu_registry is not None and "listener" not in self.actors:
            from luna.actors.listener import ListenerActor
            self.register_actor(ListenerActor(menu_registry=self.menu_registry))

        # LatencyGate + OrderDispatcher with DM scaffolds registered.
        # Constructed but NOT yet wired into the live response path — that's a
        # follow-up once DM internals are real. Direct callers (tests, future
        # integration) can use `self.order_dispatcher.place_order(...)` today.
        if self.menu_registry is not None and not hasattr(self, "order_dispatcher"):
            try:
                from luna.agentic.router import QueryRouter
                from luna.menu.dispatcher import OrderDispatcher
                from luna.menu.dms.ben import BenDM
                from luna.menu.dms.dude import DudeDM
                from luna.menu.dms.general import GeneralDM
                from luna.menu.latency_gate import LatencyGate

                self.order_dispatcher = OrderDispatcher(registry=self.menu_registry)
                self.order_dispatcher.register_dm("general", GeneralDM())
                self.order_dispatcher.register_dm("ben", BenDM())
                self.order_dispatcher.register_dm("dude", DudeDM())

                listener = self.get_actor("listener")
                self.latency_gate = LatencyGate(
                    registry=self.menu_registry,
                    listener=listener,
                    query_router=QueryRouter(),
                )
                from luna.menu.result_bridge import ResultBridge
                self.result_bridge = ResultBridge(self)
                logger.info("[menu] dispatcher + latency_gate + result_bridge constructed")
            except Exception as e:
                logger.warning("[menu] dispatcher/gate init failed (non-fatal): %s", e)

        # Identity actor — ALWAYS register so bridge/memory scoping works.
        # FaceID camera loop only runs when faceid_enabled=True.
        if "identity" not in self.actors:
            try:
                from luna.actors.identity import IdentityActor
                ia = IdentityActor(enabled=self.config.faceid_enabled)
                self.register_actor(ia)

                # When FaceID is off, auto-set identity to the owner so
                # memories, bridge, and access layers work out of the box.
                if not self.config.faceid_enabled:
                    from luna.core.owner import get_owner, owner_configured
                    if owner_configured():
                        import time as _time
                        owner = get_owner()
                        ia.current.entity_id = owner.entity_id
                        ia.current.entity_name = owner.display_name
                        ia.current.confidence = 1.0
                        ia.current.luna_tier = "admin"
                        ia.current.dataroom_tier = 1
                        ia.current.dataroom_categories = [1,2,3,4,5,6,7,8,9]
                        ia.current.last_seen = _time.time()
                        logger.info(
                            "IdentityActor registered (FaceID off, default owner: %s/%s)",
                            owner.entity_id, owner.display_name,
                        )
                    else:
                        logger.info("IdentityActor registered (FaceID off, no owner configured)")
                else:
                    logger.info("IdentityActor registered (FaceID enabled)")
            except Exception as e:
                logger.warning(f"IdentityActor registration failed (non-fatal): {e}")

        # Phase 1.5: Eden adapter + bridge actor (optional)
        await self._init_eden()

        # P0 FIX: Auto-load entity seeds on startup
        # See: Docs/HANDOFF_Luna_Voice_Restoration.md
        await self._ensure_entity_seeds_loaded()

        # Restore consciousness from snapshot
        self.consciousness = await ConsciousnessState.load()

        # Wire librarian -> consciousness (Layer 6)
        if "librarian" in self.actors:
            self.actors["librarian"].set_consciousness(self.consciousness)

        # Set Luna's core identity in revolving context
        self.context.set_core_identity(self._build_identity_prompt())

        # Initialize AgentLoop (Phase XIV)
        self.agent_loop = AgentLoop(orchestrator=self, max_iterations=50)
        self.agent_loop.on_progress(self._handle_agent_progress)
        logger.info("AgentLoop initialized")

        # Initialize Scout actor + Watchdog (blockage detection + stuck state recovery)
        if "scout" not in self.actors:
            from luna.actors.scout import ScoutActor, Watchdog, watchdog_loop
            self.register_actor(ScoutActor())
            self.watchdog = Watchdog(self)
            asyncio.create_task(watchdog_loop(self.watchdog, interval=5.0))
            logger.info("Scout and Watchdog initialized")

        # Initialize ReconcileManager (confabulation self-correction)
        # NOTE: ReconcileManager is only wired in the HTTP streaming path (server.py generate_stream).
        # _process_direct uses an async mailbox dispatch and never holds the response text,
        # so did_reconcile() cannot be called here without refactoring the director response path.
        # Tracked as BUG-C in ACTOR_AUDIT_2026-05-02.
        from luna.actors.reconcile import ReconcileManager
        self.reconcile = ReconcileManager()
        logger.info("ReconcileManager initialized")

        # Initialize LunaFM (background cognitive broadcast) — optional
        self.lunafm = None
        try:
            from luna.core.paths import config_dir
            lunafm_cfg = config_dir() / "lunafm" / "station.yaml"
            if lunafm_cfg.exists():
                from luna.lunafm.station import LunaFMActor
                lunafm_actor = LunaFMActor(self, lunafm_cfg)
                self.register_actor(lunafm_actor)
                self.lunafm = lunafm_actor.station
                logger.info("LunaFMActor registered")
            else:
                logger.debug(f"LunaFM config not found at {lunafm_cfg}, skipping")
        except Exception as e:
            logger.warning(f"LunaFM initialization failed (non-fatal): {e}")

        # =================================================================
        # Phase 1 — Engine Ownership: substrate components
        # Five instances the Engine owns. All MCP/API tools reference these
        # instead of creating their own copies.
        # Order matters: AiBrarian → CollectionLockIn → Annotations → Aperture
        # =================================================================

        # 1/4 AiBrarianEngine — universal document database layer
        try:
            from luna.substrate.aibrarian_engine import AiBrarianEngine
            _project = project_root()
            self.aibrarian = AiBrarianEngine(
                config_dir() / "aibrarian_registry.yaml",
                project_root=_project,
            )
            await self.aibrarian.initialize()
            logger.info("AiBrarianEngine owned by Engine — connected")
        except Exception as e:
            self.aibrarian = None
            logger.warning(f"AiBrarianEngine initialization failed (non-fatal): {e}")

        # 1b/4 ForgeWatcher — live folder monitor for Knowledge Forge
        self.forge_watcher = None
        if self.aibrarian is not None:
            try:
                from luna.substrate.forge_watcher import ForgeWatcher
                self.forge_watcher = ForgeWatcher(self.aibrarian)
                self.forge_watcher.start()
                logger.info("ForgeWatcher owned by Engine — started")
            except Exception as e:
                logger.warning(f"ForgeWatcher initialization failed (non-fatal): {e}")

        # 2/4 CollectionLockInEngine + NexusRegistry — both injected into AiBrarianEngine
        # nexus_registry is the source of truth for "what collections exist & admit".
        # collection_lock_in remains the runtime lock_in scoring engine; rows are
        # created lazily on first bump_access (no startup ensure_tracked loop).
        try:
            from luna.substrate.collection_lock_in import CollectionLockInEngine
            from luna.substrate.nexus_registry import NexusRegistry
            matrix_actor = self.get_actor("matrix")
            _matrix_obj = getattr(matrix_actor, "_matrix", None)
            _mem_db = getattr(_matrix_obj, "db", None) if _matrix_obj else None
            if _mem_db is not None:
                self.collection_lock_in = CollectionLockInEngine(_mem_db)
                await self.collection_lock_in.ensure_table()
                # luna_system bootstrap — keeps runtime scoring populated for the
                # canonical collection without waiting for first access.
                await self.collection_lock_in.ensure_tracked("luna_system", pattern="emergent")

                # NexusRegistry — seed from YAML on first boot only.
                self.nexus_registry = NexusRegistry(_mem_db)
                if await self.nexus_registry.is_empty():
                    seeded = await self.nexus_registry.seed_from_yaml(
                        config_dir() / "aibrarian_registry.yaml"
                    )
                    logger.info(
                        "NexusRegistry seeded %d collections from YAML (first boot)",
                        seeded,
                    )
                else:
                    logger.info("NexusRegistry already populated — YAML ignored")

                if self.aibrarian is not None:
                    self.aibrarian.set_lock_in_engine(self.collection_lock_in)
                    self.aibrarian.set_nexus_registry(self.nexus_registry)
                logger.info("CollectionLockInEngine + NexusRegistry owned by Engine — injected into AiBrarian")
            else:
                self.collection_lock_in = None
                self.nexus_registry = None
                logger.warning("CollectionLockInEngine + NexusRegistry skipped — Matrix DB not available")
        except Exception as e:
            self.collection_lock_in = None
            self.nexus_registry = None
            logger.warning(f"CollectionLockInEngine/NexusRegistry initialization failed (non-fatal): {e}")

        # 3/4 AnnotationEngine — bridge from collections into Memory Matrix
        try:
            from luna.substrate.collection_annotations import AnnotationEngine
            matrix_actor = self.get_actor("matrix")
            _matrix_obj = getattr(matrix_actor, "_matrix", None)
            _mem_db = getattr(_matrix_obj, "db", None) if _matrix_obj else None
            if _mem_db is not None:
                self.annotations = AnnotationEngine(
                    _mem_db,
                    memory_matrix=_matrix_obj,
                    lock_in_engine=self.collection_lock_in,
                    nexus_registry=self.nexus_registry,
                )
                await self.annotations.ensure_table()
                logger.info("AnnotationEngine owned by Engine — bridged to Matrix")
            else:
                self.annotations = None
                logger.warning("AnnotationEngine skipped — Matrix DB not available")
        except Exception as e:
            self.annotations = None
            logger.warning(f"AnnotationEngine initialization failed (non-fatal): {e}")

        # 4/4 ApertureManager — cognitive focus control (default: BALANCED)
        from luna.context.aperture import ApertureManager
        self.aperture = ApertureManager()
        logger.info(f"ApertureManager owned by Engine — preset={self.aperture.state.preset.value}")

        # 5/5 TaskManager — canonical work layer (Slice 1)
        # Reuses the shared MatrixActor DB connection. Migrates the legacy
        # YAML bridge queue on first boot.
        try:
            from luna.core.tasks import TaskManager
            matrix_actor = self.get_actor("matrix")
            _matrix_obj = getattr(matrix_actor, "_matrix", None)
            _mem_db = getattr(_matrix_obj, "db", None) if _matrix_obj else None
            if _mem_db is not None:
                self.task_manager = TaskManager(_mem_db)
                yaml_queue = user_dir() / "cache" / "task_queue.yaml"
                summary = await self.task_manager.migrate_yaml_queue(yaml_queue)
                if summary.get("migrated"):
                    logger.info(f"TaskManager: migrated legacy YAML queue ({summary})")
                # One-shot idempotent migration: tasks.metadata_json → mapping tables
                try:
                    _map_summary = await self.task_manager.backfill_mappings_from_metadata()
                    if _map_summary.get("tasks_backfilled"):
                        logger.info(f"TaskManager: backfilled mappings ({_map_summary})")
                except Exception as _e:
                    logger.warning(f"Mapping backfill failed (non-fatal): {_e}")
                # One-shot idempotent migration: THREAD memory_nodes → threads table
                try:
                    from luna.core.thread_backfill import backfill_threads
                    _thread_summary = await backfill_threads(_mem_db)
                    if _thread_summary.get("threads_inserted"):
                        logger.info(f"Thread backfill: {_thread_summary}")
                except Exception as _e:
                    logger.warning(f"Thread backfill failed (non-fatal): {_e}")
                logger.info("TaskManager owned by Engine — connected")
            else:
                logger.warning("TaskManager skipped — Matrix DB not available")
        except Exception as e:
            self.task_manager = None
            logger.warning(f"TaskManager initialization failed (non-fatal): {e}")

        # Initialize SubtaskRunner with explicit backend selection.
        if SUBTASK_RUNNER_AVAILABLE:
            subtask_backend = await self._select_subtask_backend()

            # Wire it up
            if subtask_backend is not None:
                self._subtask_runner = LocalSubtaskRunner(subtask_backend)
                logger.info("LocalSubtaskRunner initialized")
            else:
                logger.warning("SubtaskRunner unavailable (no local model, no Haiku API)")
        else:
            logger.debug("LocalSubtaskRunner module not available")

        # Initialize voice system if enabled
        if self.config.voice_enabled:
            await self._init_voice()

        # Memory hygiene: run maintenance sweep if overdue (>7 days)
        await self._maybe_run_hygiene_sweep()

        # Intent Layer: DirectiveEngine (armed quests that fire on events)
        await self._init_directive_engine()

        logger.info("Boot sequence complete")

    async def _select_subtask_backend(self):
        """Choose the subtask backend from config: auto, qwen, or haiku."""
        mode = self.config.subtask_backend
        director = self.get_actor("director")

        if mode in {"auto", "qwen"}:
            if director and director._enable_local and not director.local_available:
                await director._init_local_inference()
            if director and director.local_available:
                if mode == "qwen":
                    logger.info("SubtaskRunner using Qwen 3B (forced by config)")
                else:
                    logger.info("SubtaskRunner using Qwen 3B (local)")
                return director._local
            if mode == "qwen":
                logger.warning("SubtaskRunner configured for Qwen, but local inference is unavailable")
                return None

        if mode in {"auto", "haiku"}:
            try:
                from luna.inference.haiku_subtask_backend import HaikuSubtaskBackend
                haiku = HaikuSubtaskBackend()
                if haiku.is_loaded:
                    if mode == "haiku":
                        logger.info("SubtaskRunner using Haiku API (forced by config)")
                    else:
                        logger.info("SubtaskRunner using Haiku API (Qwen unavailable)")
                    return haiku
            except Exception as e:
                logger.warning(f"Haiku subtask backend failed: {e}")
            if mode == "haiku":
                logger.warning("SubtaskRunner configured for Haiku, but Haiku backend is unavailable")
                return None

        return None

    async def _run_actors(self) -> None:
        """Start all registered actors."""
        if not self.actors:
            logger.warning("No actors registered")
            return

        tasks = []
        for actor in self.actors.values():
            task = asyncio.create_task(actor.start())
            actor._task = task
            tasks.append(task)

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _init_eden(self) -> None:
        """Initialize Eden adapter and bridge actor if API key is set."""
        import os
        from luna.services.eden.policy import EdenPolicy

        if not EdenPolicy.load().enabled:
            logger.info("Eden: Disabled by policy, skipping Eden initialization")
            return

        eden_api_key = os.environ.get("EDEN_API_KEY", "")
        if not eden_api_key or eden_api_key == "your_key_here":
            logger.debug("Eden: No API key set, skipping Eden initialization")
            return

        try:
            from luna.services.eden import EdenAdapter, EdenConfig
            from luna.actors.eden_bridge import EdenBridgeActor
            from luna.tools.eden_tools import set_eden_adapter

            config = EdenConfig.load()
            adapter = EdenAdapter(config)
            await adapter.__aenter__()
            self._eden_adapter = adapter

            # Register bridge actor
            if "eden_bridge" not in self.actors:
                self.register_actor(EdenBridgeActor())

            # Connect tools to adapter + engine
            set_eden_adapter(adapter, engine=self)

            logger.info("Eden adapter initialized successfully")

        except Exception as e:
            logger.warning(f"Eden initialization failed (non-fatal): {e}")
            self._eden_adapter = None

    async def _init_voice(self) -> None:
        """Initialize the voice system."""
        try:
            from voice import VoiceBackend
            from voice.stt import STTProviderType
            from voice.tts import TTSProviderType

            # Map config strings to enums
            stt_map = {
                "mlx_whisper": STTProviderType.MLX_WHISPER,
                "apple": STTProviderType.APPLE,
                "google": STTProviderType.GOOGLE,
            }
            tts_map = {
                "piper": TTSProviderType.PIPER,
                "apple": TTSProviderType.APPLE,
                "edge": TTSProviderType.EDGE,
            }

            stt_provider = stt_map.get(self.config.voice_stt_provider, STTProviderType.MLX_WHISPER)
            tts_provider = tts_map.get(self.config.voice_tts_provider, TTSProviderType.PIPER)
            hands_free = self.config.voice_mode == "hands_free"

            self._voice = VoiceBackend(
                engine=self,
                stt_provider=stt_provider,
                tts_provider=tts_provider,
                tts_voice=self.config.voice_tts_voice,
                hands_free=hands_free,
            )
            logger.info(f"Voice system initialized (TTS={tts_provider.value}, STT={stt_provider.value})")

        except ImportError as e:
            logger.warning(f"Voice system not available: {e}")
            self._voice = None
        except Exception as e:
            logger.error(f"Failed to initialize voice system: {e}")
            self._voice = None

    async def _init_directive_engine(self) -> None:
        """Initialize the Intent Layer directive engine."""
        try:
            from luna.agentic.directives import DirectiveEngine

            matrix_actor = self.get_actor("matrix")
            db_path = getattr(matrix_actor, "db_path", None)
            if db_path is None:
                db_path = memory_matrix_path()

            self.directive_engine = DirectiveEngine(Path(db_path))

            # Seed from YAML on first run (idempotent — skips existing)
            yaml_path = config_dir() / "directives_seed.yaml"
            if yaml_path.exists():
                result = await self.directive_engine.seed_from_yaml(yaml_path)
                if result["directives"] or result["skills"]:
                    logger.info(f"Seeded directives: {result}")

            # Re-arm any fired directives from last session
            rearmed = await self.directive_engine.rearm_fired()
            if rearmed:
                logger.info(f"Re-armed {rearmed} fired directives")

            # Load armed directives
            count = await self.directive_engine.load_armed()
            logger.info(f"DirectiveEngine initialized — {count} armed directives")

            # Evaluate session_start triggers
            await self._evaluate_session_start_directives()

        except Exception as e:
            self.directive_engine = None
            logger.warning(f"DirectiveEngine initialization failed (non-fatal): {e}")

    async def _evaluate_session_start_directives(self) -> None:
        """Fire session_start directives at boot."""
        if not self.directive_engine:
            return
        try:
            matches = await self.directive_engine.evaluate_event(
                "session_start", {}
            )
            for d in matches:
                if d.get("trust_tier") == "auto":
                    result = await self.directive_engine.fire(d, self)
                    self._directive_context.append(result)
                    logger.info(f"Auto-fired directive: {d.get('title', d['id'])}")
                else:
                    logger.info(f"Directive pending confirmation: {d.get('title', d['id'])}")
        except Exception as e:
            logger.error(f"Session start directive evaluation error: {e}")

    async def evaluate_post_extraction_directives(
        self, user_message: str, entities: list[str], thread_resumed: bool = False,
        thread_info: Optional[dict] = None,
        event_types: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Evaluate keyword, entity_mention, and thread_resume directives
        after an extraction completes. Called from Librarian or engine.
        """
        if not self.directive_engine:
            return []

        results = []
        events = []

        if user_message:
            events.append(("keyword", {"message": user_message}))
        if entities:
            events.append(("entity_mention", {"entities": entities}))
        if thread_resumed and thread_info:
            events.append(("thread_resume", {"thread": thread_info}))

        for event_type, ctx in events:
            if event_types is not None and event_type not in event_types:
                continue
            try:
                matches = await self.directive_engine.evaluate_event(event_type, ctx)
                for d in matches:
                    if d.get("trust_tier") == "auto":
                        result = await self.directive_engine.fire(d, self)
                        self._directive_context.append(result)
                        results.append(result)
                    else:
                        logger.info(
                            f"Directive pending confirmation: {d.get('title', d['id'])}"
                        )
            except Exception as e:
                logger.error(f"Post-extraction directive error ({event_type}): {e}")

        return results

    async def _maybe_run_hygiene_sweep(self) -> None:
        """Run maintenance sweep + entity review quest if >7 days since last."""
        import json as _json
        from datetime import timezone

        state_path = user_dir() / "hygiene_sweep_state.json"
        now = datetime.now(timezone.utc)
        seven_days = 7 * 24 * 3600

        try:
            if state_path.exists():
                state = _json.loads(state_path.read_text())
                last = datetime.fromisoformat(state.get("last_sweep", "2000-01-01T00:00:00+00:00"))
                if (now - last).total_seconds() < seven_days:
                    logger.debug("Hygiene sweep not due yet (last: %s)", last.isoformat())
                    return

            logger.info("Running scheduled memory hygiene sweep...")
            from luna_mcp.observatory.tools import (
                tool_observatory_maintenance_sweep,
                tool_observatory_entity_review_quest,
                tool_observatory_quest_create,
            )

            sweep = await tool_observatory_maintenance_sweep()
            candidates = sweep.get("candidates", [])
            created_ids = []

            for c in candidates:
                result = await tool_observatory_quest_create(
                    title=c.get("title", ""),
                    objective=c.get("objective", c.get("title", "")),
                    quest_type=c.get("quest_type", "side"),
                    priority=c.get("priority", "medium"),
                    subtitle=c.get("subtitle", ""),
                    source=c.get("source", "maintenance_sweep"),
                    target_entity_ids=_json.dumps(c.get("target_entities", [])),
                    target_node_ids=_json.dumps(c.get("target_nodes", [])),
                )
                if result.get("quest_id"):
                    created_ids.append(result["quest_id"])

            review = await tool_observatory_entity_review_quest()
            if review.get("quest_id"):
                created_ids.append(review["quest_id"])

            # Save sweep timestamp
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(_json.dumps({
                "last_sweep": now.isoformat(),
                "quests_created": len(created_ids),
                "quest_ids": created_ids,
            }, indent=2))

            logger.info(
                "Hygiene sweep complete: %d candidates, %d quests created",
                len(candidates), len(created_ids),
            )

        except Exception as e:
            logger.warning("Hygiene sweep failed (non-fatal): %s", e)

    async def _ensure_entity_seeds_loaded(self) -> None:
        """
        Load personality seeds if not already in database.

        P0 FIX: Auto-loads Luna's personality from entities/personas/luna.yaml
        on engine startup if not already present in the database.
        See: Docs/HANDOFF_Luna_Voice_Restoration.md
        """
        try:
            # Get database access through Matrix actor
            matrix = self.get_actor("matrix")
            if not matrix or not hasattr(matrix, "_matrix") or not matrix._matrix:
                logger.warning("[SEEDS] Matrix not initialized, skipping entity seed auto-load")
                return

            db = matrix._matrix.db

            # Check if Luna entity exists
            result = await db.fetchone(
                "SELECT id FROM entities WHERE id = ?",
                ("luna",)
            )

            if result is not None:
                logger.debug("[SEEDS] Luna personality already loaded")
                return

            logger.info("[SEEDS] Luna entity not found, loading personality seeds...")

            # Import and run seed loader
            from pathlib import Path
            try:
                # Load EntitySeedLoader — use importlib for compiled build compatibility
                _root = project_root()
                try:
                    import importlib.util
                    _loader_path = scripts_dir() / "migrations" / "load_entity_seeds.py"
                    _spec = importlib.util.spec_from_file_location("load_entity_seeds", str(_loader_path))
                    _mod = importlib.util.module_from_spec(_spec)
                    _spec.loader.exec_module(_mod)
                    EntitySeedLoader = _mod.EntitySeedLoader
                except Exception:
                    # Fallback: dev environment where scripts/ is on sys.path
                    import sys
                    sys.path.insert(0, str(scripts_dir()))
                    from migrations.load_entity_seeds import EntitySeedLoader

                entities_dir = _root / "entities"
                if not entities_dir.exists():
                    logger.warning(f"[SEEDS] Entities directory not found: {entities_dir}")
                    return

                loader = EntitySeedLoader(
                    db=db,
                    entities_dir=entities_dir,
                    dry_run=False
                )

                # Ensure schema exists
                await loader.ensure_schema()

                # Load all seed files
                summary = await loader.load_all()

                logger.info(
                    f"[SEEDS] Loaded {summary['loaded']} entities, "
                    f"updated {summary['updated']}, "
                    f"skipped {summary['skipped']}"
                )

                if summary['errors'] > 0:
                    logger.warning(f"[SEEDS] {summary['errors']} errors during seed loading")

            except ImportError as e:
                from luna.diagnostics.maturity import compiled_debug
                compiled_debug(logger, "[SEEDS] EntitySeedLoader not available: %s", e)

        except Exception as e:
            # Don't fail startup if seed loading fails - just warn
            logger.error(f"[SEEDS] Failed to load entity seeds: {e}")

    async def _wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        await self._shutdown_event.wait()

    async def wait_ready(self, timeout: float = 5.0) -> bool:
        """Wait for engine to be ready."""
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # =========================================================================
    # Tick Loops
    # =========================================================================

    async def _cognitive_loop(self) -> None:
        """
        Cognitive path: 500ms heartbeat.

        - Poll input buffer
        - Prioritize events
        - Dispatch to actors
        - Update consciousness state
        """
        logger.debug("Cognitive loop started")

        while self._running:
            try:
                await self._cognitive_tick()
                self.metrics.cognitive_ticks += 1

            except Exception as e:
                logger.error(f"Cognitive tick error: {e}")
                self.metrics.errors += 1

            await asyncio.sleep(self.config.cognitive_interval)

    async def _cognitive_tick(self) -> None:
        """Single cognitive tick."""
        # 1. Poll input buffer (prioritized)
        events = self.input_buffer.poll_all()

        if events:
            logger.debug(f"Tick processing {len(events)} events")

        # 2. Dispatch each event
        for event in events:
            self.metrics.events_processed += 1
            await self._dispatch_event(event)

        # 2b. Idle drain — surface any DM results that landed since last tick
        # when no events are in flight. Background-tier results (long-running
        # research/extraction) land here. See DESIGN_DM_Result_Delivery Q2.
        if not events and not self._is_processing:
            bridge = getattr(self, "result_bridge", None)
            if bridge is not None:
                try:
                    await bridge.drain()
                except Exception as e:
                    logger.warning(f"[result-bridge] idle drain failed (non-fatal): {e}")

        # 3. Consciousness tick
        await self.consciousness.tick()

        # 4. History Manager tick (process compression/extraction queues)
        history = self.get_actor("history_manager")
        if history and hasattr(history, "tick"):
            try:
                await asyncio.wait_for(history.tick(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("History manager tick timed out (2s)")
            except Exception as e:
                logger.warning(f"History manager tick error: {e}")

        # 5. Rebalance rings (decay happens in reflective loop, not every tick)
        self.context._rebalance_rings()

        # 6. Persist state periodically (every 10 ticks)
        if self.metrics.cognitive_ticks > 0 and self.metrics.cognitive_ticks % 10 == 0:
            await self.consciousness.save()

        # 7. WAL checkpoint periodically (every 120 ticks ≈ 60s at 500ms interval)
        if self.metrics.cognitive_ticks > 0 and self.metrics.cognitive_ticks % 120 == 0:
            matrix = self.get_actor("matrix")
            if matrix and hasattr(matrix, "_matrix") and matrix._matrix:
                await matrix._matrix.db.checkpoint_wal()

    async def _dispatch_event(self, event: InputEvent) -> None:
        """Route event to appropriate actor(s).

        User messages are dispatched as background tasks so the cognitive
        tick loop is never blocked by long-running agentic pipelines.
        """
        logger.debug(f"Dispatching: {event}")

        match event.type:
            case EventType.TEXT_INPUT | EventType.TRANSCRIPT_FINAL:
                raw = event.payload
                if isinstance(raw, dict):
                    user_message = raw.get("text", "")
                    prepare_only = bool(raw.get("prepare_only", False))
                    trace = raw.get("trace")
                    director_pivot_endpoint = raw.get("director_pivot_endpoint")
                else:
                    user_message = raw
                    prepare_only = False
                    trace = None
                    director_pivot_endpoint = None
                cid = event.correlation_id or "anon"
                asyncio.create_task(
                    self._handle_user_message(
                        user_message,
                        cid,
                        source=event.source,
                        prepare_only=prepare_only,
                        trace=trace,
                        turn_metadata=event.metadata,
                        director_pivot_endpoint=director_pivot_endpoint,
                    ),
                    name=f"msg-{cid[:8]}",
                )

            case EventType.USER_INTERRUPT:
                # Abort current task/generation. Voice v2.0 Step 3: when the
                # event carries a structured InterruptPayload (from voice
                # backend), the classifier branch runs observationally;
                # otherwise the legacy abort sequence runs unchanged.
                await self._handle_interrupt(event)

            case EventType.RESUMPTION_TRIGGER:
                # Voice v2.0 Step 4 — route to Director via mailbox so it
                # can produce a revised response.
                await self._dispatch_resumption_to_director(event)

            case EventType.ACTOR_MESSAGE:
                # Internal message from actor
                await self._handle_actor_message(event)

            case EventType.IDENTITY_RECOGNIZED:
                name = event.payload.get("entity_name", "unknown")
                tier = event.payload.get("luna_tier", "unknown")
                logger.info(f"Identity event: {name} recognized (tier={tier})")

            case EventType.IDENTITY_LOST:
                name = event.payload.get("entity_name", "unknown")
                logger.info(f"Identity event: {name} left")

            case EventType.SHUTDOWN:
                await self.stop()

    async def _handle_user_message(
        self,
        user_message: str,
        correlation_id: str,
        source: str = "text",
        prepare_only: bool = False,
        trace: Optional[Any] = None,
        turn_metadata: Optional[dict] = None,
        director_pivot_endpoint: Optional[str] = None,
    ) -> None:
        """
        Handle incoming user message with concurrent support.

        If currently processing, this message can either:
        1. Interrupt the current task (if it looks like an interrupt)
        2. Be queued for after current task completes
        3. Be processed in parallel (for simple queries)

        When prepare_only=True, skips state-mutating side effects (context
        pollution, interrupt detection, turn recording) and routes straight
        to the agentic pipeline. LunaFM is preempted/resumed symmetrically
        with /message — without it, background channels (synthesis LLM
        calls, spectral compute) compete with prepare-only requests for
        engine resources and have caused 30s HTTP tails (Phase 1A.6).
        """
        if prepare_only:
            if self.lunafm:
                try:
                    await self.lunafm.preempt()
                except Exception as e:
                    logger.debug(f"LunaFM preempt (prepare_only) failed: {e}")
            try:
                await self._process_message_agentic(
                    user_message,
                    correlation_id,
                    source=source,
                    prepare_only=True,
                    trace=trace,
                    turn_metadata=turn_metadata,
                    director_pivot_endpoint=director_pivot_endpoint,
                )
            finally:
                if self.lunafm:
                    try:
                        asyncio.create_task(self.lunafm.resume())
                    except Exception as e:
                        logger.debug(f"LunaFM resume (prepare_only) failed: {e}")
            return

        # Preempt LunaFM background channels
        if self.lunafm:
            try:
                await self.lunafm.preempt()
            except Exception as e:
                logger.debug(f"LunaFM preempt failed: {e}")

        # Add user message to revolving context
        self.context.add(
            content=f"User: {user_message}",
            source=ContextSource.CONVERSATION,
            door=Door.USER_TURN,
        )

        # Check if this looks like an interrupt. Word-boundary match to avoid
        # false positives on substrings like "await", "waiting", "stopped",
        # "laptop", "cancellation" — these used to fire spurious interrupts
        # when Luna's own speech bled through the mic during playback.
        interrupt_pattern = _re.compile(
            r"\b(stop|cancel|wait|hold on|nevermind|never mind)\b"
        )
        is_interrupt = bool(interrupt_pattern.search(user_message.lower()))

        if is_interrupt and self._is_processing:
            logger.info(f"User interrupt detected: {user_message[:30]}...")
            await self._handle_interrupt()
            # Acknowledge the interrupt
            await self._emit_response(
                "Okay, I've stopped what I was doing. What would you like instead?",
                {"interrupted": True}
            )
            return

        # If currently processing, handle concurrently
        if self._is_processing:
            # Route to see if this is simple enough to answer immediately
            routing = self.router.analyze(user_message)

            if routing.path == ExecutionPath.DIRECT:
                # Simple query - can answer while working on other task
                logger.info(f"Concurrent simple query: {user_message[:30]}...")
                await self._emit_progress(f"(Working on your previous request... but I can answer this quickly)")
                try:
                    await self._process_direct(
                        user_message,
                        correlation_id,
                        director_pivot_endpoint=director_pivot_endpoint,
                    )
                except Exception as _direct_err:
                    import sqlite3 as _sqlite3
                    if isinstance(_direct_err, _sqlite3.OperationalError) and "database is locked" in str(_direct_err):
                        logger.warning(f"DB lock on concurrent direct query — retrying in 1s: {_direct_err}")
                        await asyncio.sleep(1)
                        try:
                            await self._process_direct(
                                user_message,
                                correlation_id,
                                director_pivot_endpoint=director_pivot_endpoint,
                            )
                        except Exception:
                            await self._emit_response(
                                "I'm still thinking about something — try again in a moment.",
                                {"error": True},
                            )
                    else:
                        raise
            else:
                # Queue for later
                self._pending_messages.append(user_message)
                await self._emit_progress(
                    f"Got it! I'll get to that after I finish what I'm working on. "
                    f"({len(self._pending_messages)} message{'s' if len(self._pending_messages) > 1 else ''} queued)"
                )
            return

        # Not currently processing - handle normally with agentic routing
        await self._process_message_agentic(
            user_message,
            correlation_id,
            source=source,
            trace=trace,
            turn_metadata=turn_metadata,
            director_pivot_endpoint=director_pivot_endpoint,
        )

    def _build_active_error_metadata(
        self,
        *,
        exception: BaseException,
        endpoint: Optional[str],
    ) -> dict:
        """Phase C.5: build response metadata for the agentic-error path.

        Always returns at least `{"error": True}`. When active mode is in
        scope for `endpoint` (per PivotConfig), adds a synthetic
        `director_pivot_active` debug payload with
        `fallback_reason="agentic_error:<ExcName>"` so soak harnesses see a
        structured failure instead of a missing field. Default-legacy
        responses are unaffected.
        """
        metadata: dict = {"error": True}
        if not endpoint:
            return metadata
        try:
            cfg = PivotConfig.load()
        except Exception as cfg_exc:
            logger.warning(
                "[DIRECTOR-PIVOT] active error metadata gating failed to load config: %s",
                cfg_exc,
            )
            return metadata
        if cfg.mode != "active":
            return metadata
        if not is_pivot_enabled(endpoint, mode=cfg.mode, config=cfg):
            return metadata
        payload = build_active_agentic_error_payload(
            endpoint=endpoint,
            exception=exception,
        )
        logger.warning(
            "[DIRECTOR-PIVOT] active fallback endpoint=%s reason=%s",
            endpoint,
            payload["fallback_reason"],
        )
        metadata["director_pivot_active"] = payload
        return metadata

    async def _process_message_agentic(
        self,
        user_message: str,
        correlation_id: str,
        source: str = "text",
        _db_retry: int = 0,
        prepare_only: bool = False,
        trace: Optional[Any] = None,
        turn_metadata: Optional[dict] = None,
        director_pivot_endpoint: Optional[str] = None,
    ) -> None:
        """Process message through the agentic pipeline (subtasks → router → planner → loop).

        When prepare_only=True, the pipeline assembles context and dispatches to
        Director with the flag set — Director emits the assembled bundle instead
        of running inference. Turn recording and other side effects are skipped.
        """
        self._is_processing = True
        self._current_goal = user_message
        self.metrics.agentic_tasks_started += 1

        import time as _time
        _pt0 = _time.time()
        _timings: dict = {}

        try:
            # ══════════════════════════════════════════════════════════════
            # FAST PATH: prepare_only with MIDDLE already populated
            # Phase 1A.5: trigger is MIDDLE-only. OUTER is "background
            # context" by definition — session restoration populates it
            # with items unrelated to the current query, and treating
            # those as "we already have context for this query" produced
            # phantom-state poisoning of every fresh query (see Phase 1A
            # baseline: 13 OUTER items / 858 tok pre-loaded on every
            # restart was triggering fast-path for unrelated queries).
            # MIDDLE holds query-driven retrievals, so reusing it across
            # closely-spaced queries is defensible.
            # ══════════════════════════════════════════════════════════════
            if prepare_only:
                _ring_middle = list(self.context.rings.get(ContextRing.MIDDLE, []))
                if _ring_middle:
                    logger.info(
                        "[PREPARE-FAST] MIDDLE has %d items — reusing as memory source, skipping retrieval",
                        len(_ring_middle),
                    )
                    self.metrics.direct_responses += 1
                    await self._process_direct(
                        user_message,
                        correlation_id,
                        "",
                        None,
                        prepare_only=True,
                        trace=trace,
                        director_pivot_endpoint=director_pivot_endpoint,
                    )
                    return

            # ══════════════════════════════════════════════════════════════
            # PHASE 1: Run Qwen subtasks in parallel with memory retrieval
            # Subtasks: intent classification, entity extraction, query rewriting
            # These run concurrently — total wall time ≈ max(subtask, memory)
            # ══════════════════════════════════════════════════════════════

            matrix = self.get_actor("matrix")
            history_manager = self.get_actor("history_manager")

            # Build recent turns list for query rewriting context
            recent_turns = []
            director = self.get_actor("director")
            if director and hasattr(director, '_active_ring'):
                ring = director._active_ring
                recent_turns = [
                    f"{'User' if t.get('role') == 'user' else 'Luna'}: {t.get('content', '')[:200]}"
                    for t in ring.get_recent(4)
                ] if hasattr(ring, 'get_recent') else []

            # Memory retrieval via unified module
            async def _retrieve_context():
                from luna.retrieval import UnifiedRetrieval, RetrievalRequest

                _rt: dict = {}
                _rt0 = _time.time()
                history_context = None
                retrieval_query = user_message

                _t = _time.time()
                if history_manager and history_manager.is_ready:
                    history_context = await history_manager.build_history_context(user_message)
                _rt["history_build"] = _time.time() - _t

                _rt["load_conv_history"] = 0.0
                if matrix and matrix.is_ready:
                    _t = _time.time()
                    await self._load_conversation_history(matrix, limit=10)
                    _rt["load_conv_history"] = _time.time() - _t

                # Use rewritten query for better retrieval if available
                if subtask_phase and subtask_phase.rewritten_query:
                    retrieval_query = subtask_phase.rewritten_query
                    logger.info(f"[SUBTASK] Using rewritten query for retrieval: {retrieval_query[:60]}...")

                retriever = UnifiedRetrieval(
                    matrix_actor=matrix,
                    aibrarian=self.aibrarian,
                    aperture=self.aperture,
                    collection_lock_in=self.collection_lock_in,
                    active_scopes=self.active_scopes,
                    active_project=self._active_project,
                )
                _ap = self.aperture.state
                request = RetrievalRequest(
                    query=retrieval_query,
                    scopes=self.active_scopes,
                    subtask_phase=subtask_phase,
                    aperture_preset=_ap.preset.value,
                    aperture_angle=_ap.angle,
                    aperture_inner_threshold=_ap.inner_ring_threshold,
                    aperture_breakthrough=_ap.breakthrough_threshold,
                    trace=trace,
                )
                result = await retriever.retrieve(request)

                # Preserve existing engine state updates
                self._last_collections_searched = result.collections_searched
                self._last_nexus_nodes = result.nexus_nodes
                self._active_reflection_mode = result.reflection_mode
                # Phase 1A.3: surface partial-retrieval state for diagnostics.
                self._last_retrieval_completed_stages = list(result.completed_stages)
                self._last_retrieval_truncated = bool(result.truncated)
                # Phase 1C: per-source composition for diagnostics.
                self._last_retrieval_source_stats = dict(result.source_stats or {})
                self._last_retrieval_router_ran = bool(result.router_ran)

                # Add to revolving context — drop irrelevant candidates, then floor
                # survivors so they survive the rebalance threshold (Phase 1.5f)
                for idx, candidate in enumerate(result.candidates):
                    # Drop candidates below minimum useful threshold (Phase 1.5f)
                    if candidate.score < ring_config.ingest.min_useful_score:
                        logger.debug(
                            "[RETRIEVAL→CONTEXT] Dropped: raw=%.3f < MIN_USEFUL=%.2f source=%s",
                            candidate.score, ring_config.ingest.min_useful_score,
                            getattr(candidate, 'source', 'unknown'),
                        )
                        if trace is not None:
                            trace.record_candidate(
                                "ingest",
                                ord=idx,
                                node_id=(candidate.provenance or {}).get("node_id") if isinstance(candidate.provenance, dict) else None,
                                node_kind=getattr(candidate, "node_type", None),
                                node_label=(candidate.content or "")[:120],
                                raw_score=float(getattr(candidate, "score", 0.0)),
                                fused_score=float(getattr(candidate, "score", 0.0)),
                                lock_in=float(getattr(candidate, "confidence", 0.0)) if getattr(candidate, "confidence", None) is not None else None,
                                relevance=float(getattr(candidate, "score", 0.0)),
                                accepted=False,
                                rejection_reason="score_below_min_useful",
                            )
                        continue
                    normalized_relevance = max(ring_config.ingest.floor_score, candidate.score)
                    if candidate.score < ring_config.ingest.floor_score:
                        logger.debug(
                            "[RETRIEVAL→CONTEXT] Floored: raw=%.3f → %.3f source=%s",
                            candidate.score, normalized_relevance,
                            getattr(candidate, 'source', 'unknown'),
                        )
                    admitted = self.context.add(
                        content=candidate.content,
                        source=ContextSource.MEMORY,
                        door=Door.NEXUS,
                        relevance=normalized_relevance,
                        metadata={"node_id": candidate.provenance.get("node_id")} if candidate.provenance else None,
                    )
                    if trace is not None:
                        trace.record_candidate(
                            "ingest",
                            ord=idx,
                            node_id=(candidate.provenance or {}).get("node_id") if isinstance(candidate.provenance, dict) else None,
                            node_kind=getattr(candidate, "node_type", None),
                            node_label=(candidate.content or "")[:120],
                            raw_score=float(getattr(candidate, "score", 0.0)),
                            fused_score=float(normalized_relevance),
                            lock_in=float(getattr(candidate, "confidence", 0.0)) if getattr(candidate, "confidence", None) is not None else None,
                            relevance=float(normalized_relevance),
                            accepted=True,
                            ring_assigned=getattr(getattr(admitted, "ring", None), "name", None),
                        )

                # Phase 1C.3: per-source score-range diagnostic. Makes the
                # cross-source numeric scale visible on the same log surface
                # as [SOURCE-COMPOSITION] and [CONTEXT-INGEST]. Read-only on
                # result.candidates; no reordering, no mutation.
                score_range_stats = self._compute_score_range_stats(
                    candidates=result.candidates,
                )
                self._last_score_range_stats = score_range_stats
                self._log_score_range(stats=score_range_stats)

                # Phase 1C.2: per-source ingest audit. Makes the final drop
                # truth at min_useful_score visible alongside router/fallback
                # selection truth. Measurement only — threshold unchanged.
                ingest_stats = self._compute_context_ingest_stats(
                    candidates=result.candidates,
                    source_stats=result.source_stats,
                    min_useful_score=ring_config.ingest.min_useful_score,
                )
                self._last_context_ingest_stats = ingest_stats
                self._log_context_ingest(
                    router_ran=bool(result.router_ran),
                    stats=ingest_stats,
                )

                _rt["unified_retrieval"] = result.timings.get("retrieve_total", 0.0)
                _rt["retrieve_total"] = _time.time() - _rt0
                try:
                    logger.warning(
                        "[RETRIEVE-TIMING] total=%.3fs | %s",
                        _rt["retrieve_total"],
                        " | ".join(
                            f"{k}={v:.3f}s"
                            for k, v in sorted(_rt.items(), key=lambda x: -x[1])
                            if k != "retrieve_total"
                        ),
                    )
                except Exception:
                    pass

                return result.context_string, history_context

            # Run subtasks first (they're fast), then memory retrieval
            # (memory retrieval can use the rewritten query from subtasks)
            _t = _time.time()
            if self._subtask_runner and self._subtask_runner.is_available:
                subtask_phase = await self._subtask_runner.run_subtask_phase(
                    user_message, recent_turns
                )
            else:
                subtask_phase = SubtaskPhaseResult() if SUBTASK_RUNNER_AVAILABLE else None
            _timings["subtask_runner"] = _time.time() - _t

            # Record user turn through unified API (extraction + storage) AFTER
            # the subtask phase so same-turn entity hints are threaded inline
            # through extract_turn's payload. Previously record_conversation_turn
            # ran before subtask_phase, causing hints to race behind the
            # extract_turn message on Scribe's mailbox. Skipped for prepare_only.
            user_turn_id: Optional[int] = None
            if not prepare_only:
                # Prefer Lexicon spaCy NER (~50ms) over Qwen 3B entity extraction
                # (~2000ms). Same output schema (list[{"name", "type"}]) so it
                # drops into entity_hints unchanged. Falls back to subtask_phase
                # entities when Lexicon NER is unavailable or returns None.
                _lex_hints: Optional[list] = None
                _hints_source = "subtask"
                try:
                    from luna.lexicon import api as _lex_api
                    _lex_t0 = _time.time()
                    _lex_hints = _lex_api.ner(user_message)
                    _lex_latency_ms = (_time.time() - _lex_t0) * 1000
                    if _lex_hints is not None:
                        _hints_source = "lexicon_spacy"
                        logger.info(
                            f"[ENTITY-HINTS] lexicon spaCy entities={len(_lex_hints)} "
                            f"latency_ms={_lex_latency_ms:.0f}"
                        )
                except Exception as e:
                    logger.warning(f"[ENTITY-HINTS] Lexicon NER call failed: {e}")
                    _lex_hints = None

                if _lex_hints is not None:
                    _inline_hints = _lex_hints
                else:
                    _inline_hints = (
                        subtask_phase.entities
                        if subtask_phase and subtask_phase.entities is not None
                        else None
                    )
                _inline_count = len(_inline_hints) if _inline_hints is not None else 0
                logger.info(
                    f"[ENTITY-HINTS] source={_hints_source} entities={_inline_count} before extract_turn"
                )
                user_turn_id = await self.record_conversation_turn(
                    role="user",
                    content=user_message,
                    source=source,
                    entity_hints=_inline_hints,
                    turn_metadata=turn_metadata,
                )

            # Auto-aperture: set aperture based on intent classification
            if subtask_phase and subtask_phase.intent:
                intent = subtask_phase.intent.get('intent', 'simple_question')
                complexity = subtask_phase.intent.get('complexity', 'simple')
                self.aperture.auto_aperture(intent, complexity)

            _t = _time.time()
            memory_context, history_context = await _retrieve_context()
            _timings["retrieve_context_total"] = _time.time() - _t

            # ══════════════════════════════════════════════════════════════
            # PHASE 2: Route using semantic classification or regex fallback
            # ══════════════════════════════════════════════════════════════

            _t = _time.time()
            if subtask_phase and subtask_phase.intent:
                # Domain override: DeBERTa is a general classifier and labels
                # Luna-domain queries as simple_question when they should route
                # as memory_query or research. Apply signal-based promotion
                # before handing the intent to the router.
                _intent_dict = dict(subtask_phase.intent)
                if _intent_dict.get("intent") == "simple_question":
                    _msg_lower = user_message.lower()
                    _MEMORY_SIGNALS = (
                        "what do you know", "what do you remember", "do you recall",
                        "do you know about", "find what you know", "search your memory",
                        "tell me what you know", "what have you stored", "in your memory",
                        "from memory", "what's in your", "look up", "memory matrix",
                        ".lun", "luna engine", "aibrarian", "intergalactic hub",
                        "the scribe", "the librarian", "lunaengine",
                    )
                    _RESEARCH_SIGNALS = (
                        "look into", "research", "investigate", "find out about",
                        "can you look up", "dig into", "explore the", "find everything",
                    )
                    if any(s in _msg_lower for s in _MEMORY_SIGNALS):
                        _intent_dict["intent"] = "memory_query"
                        logger.info("[ROUTING] Domain override: simple_question → memory_query")
                    elif any(s in _msg_lower for s in _RESEARCH_SIGNALS):
                        _intent_dict["intent"] = "research"
                        logger.info("[ROUTING] Domain override: simple_question → research")
                routing = self.router.from_intent(_intent_dict, user_message)
                logger.info(f"Routing (semantic): {routing.path.name} (complexity={routing.complexity:.2f})")
            else:
                routing = self.router.analyze(user_message)
                logger.info(f"Routing (regex): {routing.path.name} (complexity={routing.complexity:.2f})")
            _timings["routing"] = _time.time() - _t

            # ══════════════════════════════════════════════════════════════
            # PHASE 3: Entity hints — now threaded inline through the
            # extract_turn payload at record_conversation_turn (above),
            # so Scribe sees same-turn hints deterministically. The
            # separate mailbox dispatch was removed to eliminate the
            # delivery race. Scribe still accepts the legacy
            # entity_hints message type as a fallback.
            # ══════════════════════════════════════════════════════════════
            _timings["entity_hints"] = 0.0

            # ══════════════════════════════════════════════════════════════
            # PHASE 4: Execute based on routing decision
            # ══════════════════════════════════════════════════════════════

            # Upgrade to AgentLoop if knowledge-sparse research query
            logger.debug("[ROUTING-DEBUG] path=%s, agent_loop=%s, nexus_nodes=%d, subtask=%s, intent=%s",
                         routing.path, bool(self.agent_loop), len(self._last_nexus_nodes),
                         bool(subtask_phase), subtask_phase.intent if subtask_phase else None)
            if (
                routing.path == ExecutionPath.DIRECT
                and self.agent_loop
                and subtask_phase
                and subtask_phase.intent
                and subtask_phase.intent.get("intent") in ("research", "memory_query")
                and (
                    len(self._last_nexus_nodes) < 2  # sparse results
                    or subtask_phase.intent.get("complexity") == "complex"  # complex research needs deeper retrieval
                )
            ):
                _upgrade_reason = "knowledge-sparse" if len(self._last_nexus_nodes) < 2 else "complex-research"
                logger.info(f"[ROUTING] Upgrading to AgentLoop ({_upgrade_reason} research query)")
                routing = RoutingDecision(
                    path=ExecutionPath.SIMPLE_PLAN,
                    complexity=routing.complexity,
                    reason="knowledge-sparse research query",
                    signals=routing.signals,
                )

            # Fallback: keyword-based upgrade when SubtaskRunner unavailable
            if (
                routing.path == ExecutionPath.DIRECT
                and self.agent_loop
                and len(self._last_nexus_nodes) < 2
                and (not subtask_phase or not subtask_phase.intent)
            ):
                _RESEARCH_SIGNALS = {"what does", "tell me about", "explain", "chapters",
                                     "evidence", "compare", "analyze", "summarize",
                                     "describe", "how does", "why does", "what are"}
                q_lower = user_message.lower()
                if any(sig in q_lower for sig in _RESEARCH_SIGNALS):
                    logger.info("[ROUTING] Keyword fallback → AgentLoop (no intent classification available)")
                    routing = RoutingDecision(
                        path=ExecutionPath.SIMPLE_PLAN,
                        complexity=routing.complexity,
                        reason="keyword-based research detection (SubtaskRunner unavailable)",
                        signals=routing.signals,
                    )

            if trace is not None:
                try:
                    ap_state = getattr(self.aperture, "state", None)
                    ap_preset = None
                    ap_angle = None
                    if ap_state is not None:
                        ap_preset = getattr(getattr(ap_state, "preset", None), "value", None)
                        ap_angle = getattr(ap_state, "angle", None)
                    trace.record_route(
                        route=routing.path.name.lower(),
                        door=getattr(routing, "reason", None),
                        aperture=ap_preset,
                        aperture_deg=ap_angle,
                    )
                except Exception:
                    pass

            # ══════════════════════════════════════════════════════════════
            # PHASE 2.5: Pre-generation directives (keyword + entity_mention).
            # Fires BEFORE generation so aperture/collection actions take
            # effect in the current turn's retrieval context rather than
            # the next turn. thread_resume directives remain post-response
            # because they depend on Librarian thread state.
            # ══════════════════════════════════════════════════════════════
            if self.directive_engine and not prepare_only:
                try:
                    _pre_entities = (
                        [e.get("name", "") for e in (subtask_phase.entities or [])]
                        if subtask_phase and subtask_phase.entities
                        else []
                    )
                    await self.evaluate_post_extraction_directives(
                        user_message=user_message,
                        entities=_pre_entities,
                        event_types=["keyword", "entity_mention"],
                    )
                except Exception as _de:
                    logger.warning("[DIRECTIVE] Pre-generation evaluation failed (non-fatal): %s", _de)

            # ══════════════════════════════════════════════════════════════
            # PHASE 4a: Agentic menu dispatch (flagged via LUNA_MENU_DISPATCH).
            # Sits between routing finalization and native dispatch. On match,
            # places an order with the relevant DM and returns; the DM result
            # surfaces later as an assistant turn via ResultBridge. On miss
            # (or any failure), falls through to the existing director path.
            # ══════════════════════════════════════════════════════════════
            if (
                not prepare_only
                and getattr(self, "_menu_dispatch_enabled", False)
                and getattr(self, "latency_gate", None) is not None
                and getattr(self, "order_dispatcher", None) is not None
                and getattr(self, "result_bridge", None) is not None
            ):
                try:
                    from luna.llm.class_detector import ClassDetector
                    if not hasattr(self, "_class_detector"):
                        self._class_detector = ClassDetector(
                            intent_router=getattr(self, "intent_router", None)
                        )

                    detection = await self._class_detector.detect(user_message)
                    listener = self.get_actor("listener")
                    predictions = listener.predictions() if listener else []

                    decision = self.latency_gate.evaluate(detection, user_message, predictions)
                    logger.info(
                        "[menu] decision: path=%s task=%s confidence=%.2f reason=%s",
                        decision.path, decision.task_id, decision.confidence, decision.reason,
                    )

                    if decision.path == "menu" and decision.task_id:
                        task = self.menu_registry.get_task(decision.task_id)
                        if task is None:
                            logger.warning(
                                "[menu] decision matched task=%s but registry returned None — falling through",
                                decision.task_id,
                            )
                        else:
                            on_complete = self.result_bridge.make_callback(
                                task.name, original_turn_id=user_turn_id
                            )
                            receipt = self.order_dispatcher.place_order(
                                task_id=decision.task_id,
                                context=decision.preloaded_context,
                                exit_condition=task.exit_condition,
                                on_complete=on_complete,
                            )
                            logger.info(
                                "[menu] order placed: %s (eta=%ss)",
                                receipt.order_id, receipt.eta_s,
                            )
                            # Acknowledgement turn — placeholder copy, calibrate
                            # against UX_COPY_Conversation_Shapes.md before final ship.
                            await self.record_conversation_turn(
                                role="assistant",
                                content=f"on it — kicking off {task.name} ({task.latency_tier}). i'll surface the result when it's done.",
                                source="text",
                                turn_metadata={
                                    "dm_ack": True,
                                    "task_id": decision.task_id,
                                    "order_id": receipt.order_id,
                                },
                            )
                            self.metrics.agentic_tasks_completed += 1
                            return  # Skip native generate path entirely
                except Exception as e:
                    logger.warning(
                        "[menu] dispatch path failed (non-fatal — falling through to native): %s",
                        e,
                    )

            _t = _time.time()
            if prepare_only or routing.path == ExecutionPath.DIRECT:
                # Prepare always takes the direct path — no agent loop, no planning.
                self.metrics.direct_responses += 1
                await self._process_direct(
                    user_message,
                    correlation_id,
                    memory_context,
                    history_context,
                    prepare_only=prepare_only,
                    trace=trace,
                    director_pivot_endpoint=director_pivot_endpoint,
                )
            else:
                self.metrics.planned_responses += 1
                await self._process_with_agent_loop(
                    user_message,
                    correlation_id,
                    memory_context,
                    history_context,
                    routing=routing,
                    director_pivot_endpoint=director_pivot_endpoint,
                )
            _timings["director_generate"] = _time.time() - _t

            # ══════════════════════════════════════════════════════════════
            # PHASE 5: Promote used Nexus nodes into the pointer graph
            # ══════════════════════════════════════════════════════════════
            _t = _time.time()
            try:
                await self._promote_used_nodes()
            except Exception as e:
                logger.warning(f"[NEXUS-PROMOTE] Non-fatal error: {e}")
            _timings["promote_nexus"] = _time.time() - _t

            _timings["total"] = _time.time() - _pt0
            try:
                logger.warning(
                    "[PIPELINE-TIMING] total=%.3fs | %s",
                    _timings["total"],
                    " | ".join(
                        f"{k}={v:.3f}s"
                        for k, v in sorted(_timings.items(), key=lambda x: -x[1])
                        if k != "total"
                    ),
                )
            except Exception:
                pass

            self.metrics.agentic_tasks_completed += 1

        except asyncio.CancelledError:
            logger.info("Task cancelled")
            self.metrics.agentic_tasks_aborted += 1
            raise

        except Exception as e:
            # Retry with exponential backoff on SQLite lock contention
            import sqlite3 as _sqlite3
            if isinstance(e, _sqlite3.OperationalError) and "database is locked" in str(e) and _db_retry < 3:
                _delay = [1, 2, 4][min(_db_retry, 2)]
                logger.warning(f"DB lock (attempt {_db_retry + 1}/3) — retrying in {_delay}s: {e}")
                await asyncio.sleep(_delay)
                try:
                    await self._process_message_agentic(
                        user_message,
                        correlation_id,
                        source=source,
                        _db_retry=_db_retry + 1,
                        prepare_only=prepare_only,
                        trace=trace,
                        turn_metadata=turn_metadata,
                        director_pivot_endpoint=director_pivot_endpoint,
                    )
                    return
                except Exception as e2:
                    logger.error(f"Agentic processing error (retry {_db_retry + 1}): {e2}")
                    await self._emit_response(
                        f"I ran into an issue: {e2}",
                        self._build_active_error_metadata(
                            exception=e2,
                            endpoint=director_pivot_endpoint,
                        ),
                    )
            else:
                logger.error(f"Agentic processing error: {e}")
                await self._emit_response(
                    f"I ran into an issue: {e}",
                    self._build_active_error_metadata(
                        exception=e,
                        endpoint=director_pivot_endpoint,
                    ),
                )

        finally:
            self._is_processing = False
            self._current_goal = None
            self._current_task = None

            # Drain any DM results that landed during this turn — text-mode safe point
            # per DESIGN_DM_Result_Delivery Q2.
            bridge = getattr(self, "result_bridge", None)
            if bridge is not None:
                try:
                    await bridge.drain()
                except Exception as e:
                    logger.warning(f"[result-bridge] text-mode drain failed (non-fatal): {e}")

            # Process any queued messages
            if self._pending_messages:
                next_message = self._pending_messages.pop(0)
                logger.info(f"Processing queued message: {next_message[:30]}...")
                await self._process_message_agentic(next_message, str(uuid.uuid4())[:8])

    async def _process_direct(
        self,
        user_message: str,
        correlation_id: str,
        memory_context: str = "",
        history_context: Optional[Dict[str, Any]] = None,
        prepare_only: bool = False,
        trace: Optional[Any] = None,
        director_pivot_endpoint: Optional[str] = None,
    ) -> None:
        """Direct path - skip planning, go straight to Director.

        When prepare_only=True, Director short-circuits after assembly and emits
        the context bundle instead of running inference.

        NOTE: ReconcileManager (self.reconcile) is NOT called here. The reconcile
        sequence requires holding the response text to call did_reconcile(), which
        is incompatible with the async mailbox dispatch used here. Reconcile is
        wired only in server.py generate_stream. See BUG-C / ACTOR_AUDIT_2026-05-02.
        """
        # Emit progress for Thought Stream visibility
        await self._emit_progress(f"[DIRECT] {user_message[:40]}...")

        context_window = self.context.get_context_window(max_tokens=4000)
        print(f"[DEBUG] Context window: {len(context_window)} chars, items in INNER ring: {len(self.context.rings[ContextRing.INNER])}")

        # Collect ring items for assembler's priority-0 memory resolution
        ring_context_items = (
            list(self.context.rings.get(ContextRing.MIDDLE, []))
            + list(self.context.rings.get(ContextRing.OUTER, []))
        )

        if trace is not None:
            try:
                for ring in ContextRing:
                    items = list(self.context.rings.get(ring, []))
                    trace.record_ring(
                        ring=ring.name,
                        item_count=len(items),
                        token_count=sum(int(getattr(i, "tokens", 0) or 0) for i in items),
                        items=[
                            {
                                "node_id": getattr(i, "id", None),
                                "label": (getattr(i, "content", "") or "")[:120],
                                "lock_in": getattr(i, "lock_in", None),
                                "tokens": int(getattr(i, "tokens", 0) or 0),
                                "kind": getattr(getattr(i, "kind", None), "name", None),
                            }
                            for i in items
                        ],
                    )
            except Exception:
                pass

        memory_graph_state = await self._compute_memory_graph_state()

        director = self.get_actor("director")
        if director:
            msg = Message(
                type="generate",
                payload={
                    "user_message": user_message,
                    "system_prompt": self._build_system_prompt(memory_context, history_context, memory_graph_state),
                    "context_window": context_window,
                    "nexus_nodes": self._last_nexus_nodes,
                    "memory_context": memory_context,  # Pass separately so Director uses it
                    "prepare_only": prepare_only,
                    "context_items": ring_context_items if ring_context_items else None,
                    "trace": trace,
                    "director_pivot_endpoint": director_pivot_endpoint,
                    "memory_graph_state": memory_graph_state,
                },
                correlation_id=correlation_id,
            )
            await director.mailbox.put(msg)

    def _format_self_state_block(self, state: dict) -> Optional[str]:
        """Render a memory_graph_state dict as a compact Self-State prompt block.

        Mirrors PromptAssembler._format_self_state so both mint paths emit the
        same shape. Sync — no I/O. Returns None if state is empty/malformed.
        """
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
            logger.warning(f"[ENGINE] Self-state render failed: {e}")
            return None

    async def _compute_memory_graph_state(self) -> Optional[dict]:
        """Snapshot Luna's live introspection state for in-prompt visibility.

        Pulls ring occupancy, aperture, active thread summary, last-fetch top
        nodes, and matrix graph health. Returns None on any failure so that a
        broken snapshot never blocks generation.
        """
        try:
            rings_snapshot: dict = {}
            for ring, items in self.context.rings.items():
                rings_snapshot[ring.name] = {
                    "items": len(items),
                    "tokens": sum(int(getattr(i, "tokens", 0) or 0) for i in items),
                }

            aperture_snapshot = None
            ap = getattr(self, "aperture", None)
            if ap and hasattr(ap, "state") and hasattr(ap.state, "to_dict"):
                aperture_snapshot = ap.state.to_dict()

            active_thread_summary = None
            librarian = self.get_actor("librarian")
            if librarian:
                try:
                    active = librarian.get_active_thread()
                    if active:
                        active_thread_summary = {
                            "topic": getattr(active, "topic", "") or "",
                            "turn_count": int(getattr(active, "turn_count", 0) or 0),
                            "open_tasks": len(getattr(active, "open_tasks", []) or []),
                        }
                except Exception:
                    pass

            nexus_nodes = self._last_nexus_nodes or []
            top_nodes = []
            for n in (nexus_nodes or [])[:3]:
                top_nodes.append({
                    "id": n.get("id"),
                    "summary": (n.get("summary") or "")[:80],
                    "lock_in": n.get("lock_in"),
                })
            retrieval_summary = {
                "last_fetch_count": len(nexus_nodes),
                "top_nodes": top_nodes,
            }

            graph_health = None
            matrix_actor = self.get_actor("matrix")
            if matrix_actor and hasattr(matrix_actor, "_matrix") and matrix_actor._matrix:
                try:
                    stats = await matrix_actor._matrix.get_stats()
                    if isinstance(stats, dict):
                        graph_health = {
                            "total_nodes": stats.get("total_nodes", 0),
                            "total_edges": stats.get("total_edges", 0),
                        }
                except Exception:
                    pass

            return {
                "rings": rings_snapshot,
                "aperture": aperture_snapshot,
                "active_thread": active_thread_summary,
                "retrieval_summary": retrieval_summary,
                "graph_health": graph_health,
            }
        except Exception as e:
            logger.warning(f"[ENGINE] memory_graph_state computation failed: {e}")
            return None

    async def _process_with_agent_loop(
        self,
        user_message: str,
        correlation_id: str,
        memory_context: str = "",
        history_context: Optional[Dict[str, Any]] = None,
        routing: Optional[Any] = None,
        director_pivot_endpoint: Optional[str] = None,
    ) -> None:
        """Process through full AgentLoop with planning."""
        if not self.agent_loop:
            logger.warning("AgentLoop not initialized, falling back to direct")
            await self._process_direct(
                user_message,
                correlation_id,
                memory_context,
                history_context,
                director_pivot_endpoint=director_pivot_endpoint,
            )
            return

        # Create the task (allows concurrent checking)
        self._current_task = asyncio.create_task(
            self._run_agent_loop(
                user_message,
                correlation_id,
                memory_context,
                history_context,
                routing=routing,
                director_pivot_endpoint=director_pivot_endpoint,
            )
        )

        try:
            await self._current_task
        except asyncio.CancelledError:
            logger.info("AgentLoop task cancelled")
            raise

    async def _run_agent_loop(
        self,
        user_message: str,
        correlation_id: str,
        memory_context: str = "",
        history_context: Optional[Dict[str, Any]] = None,
        routing: Optional[Any] = None,
        director_pivot_endpoint: Optional[str] = None,
    ) -> None:
        """Run the AgentLoop and handle results."""
        if routing is not None and hasattr(self.agent_loop, "run_with_context"):
            logger.info(
                "[AGENT-LOOP] Using run_with_context (path=%s memory=%d chars)",
                routing.path.name, len(memory_context or ""),
            )
            result = await self.agent_loop.run_with_context(
                goal=user_message,
                routing=routing,
                pre_fetched_memory=memory_context or "",
            )
        else:
            result = await self.agent_loop.run(user_message)

        # Handle the result
        if result.success:
            # For now, the AgentLoop returns placeholder text
            # We need to route to Director for actual generation
            context_window = self.context.get_context_window(max_tokens=4000)

            director = self.get_actor("director")
            if director:
                # Include plan context in the message
                plan_context = ""
                if result.plan:
                    plan_context = f"\n[Plan: {result.plan.reasoning}]\n"

                # Synthesize a UI-shaped actor list from the agent loop's
                # action trace so AgentReactBody can render the actor grid.
                # Each ActionResult becomes one actor row.
                agentic_actors = []
                for ar in result.actions or []:
                    action = getattr(ar, "action", None)
                    if action is None:
                        continue
                    name = getattr(action, "tool", None) or "step"
                    role_attr = getattr(action, "type", None)
                    role = role_attr.name if role_attr is not None else "action"
                    agentic_actors.append({
                        "name": name,
                        "role": role,
                        "status": "completed" if ar.success else "failed",
                        "contribution": (action.description or "")[:120],
                    })

                # Always include at least a RESPOND step so the AgentReactBody
                # grid is non-empty even on simple single-step plans.
                if not agentic_actors:
                    agentic_actors = [{
                        "name": "step",
                        "role": result.status.name if result.status else "RESPOND",
                        "status": "completed",
                        "contribution": "Analyze the request and formulate response",
                    }]

                _agentic_graph_state = await self._compute_memory_graph_state()
                msg = Message(
                    type="generate",
                    payload={
                        "user_message": user_message,
                        "system_prompt": self._build_system_prompt(memory_context, history_context, _agentic_graph_state) + plan_context,
                        "context_window": context_window,
                        "agentic": True,
                        "execution_path": result.status.name,
                        "nexus_nodes": self._last_nexus_nodes,
                        "agentic_actors": agentic_actors,
                        "director_pivot_endpoint": director_pivot_endpoint,
                        "memory_graph_state": _agentic_graph_state,
                    },
                    correlation_id=correlation_id,
                )
                await director.mailbox.put(msg)
        else:
            # AgentLoop failed
            await self._emit_response(
                f"I had trouble with that: {result.error or 'Unknown error'}",
                {"error": True, "status": result.status.name}
            )

    async def _handle_interrupt(self, event: Optional[InputEvent] = None) -> None:
        """Handle user interrupt - abort current processing.

        Voice v2.0 Phase 1 Step 3 — when `event.payload` carries a structured
        InterruptPayload AND the `interrupt_classifier_enabled` flag is on,
        run the classifier and emit a `voice.interrupt.classified` log. This
        path is observational: the abort itself already ran upstream (via
        the triggering string-payload event). Step 4 will replace the
        observational no-op with ResumptionStrategy dispatch.

        All other cases (event=None, legacy string payload, flag off) fall
        through to `_legacy_abort_sequence` with byte-for-byte pre-Step-3
        semantics.
        """
        payload = _extract_interrupt_payload(event)
        if payload is not None:
            # Payload-bearing events are observational. Never re-run the
            # abort (would re-fire _on_user_interrupt_callbacks and loop).
            if _interrupt_classifier_enabled_safe():
                classification = _classify_and_log_interrupt(payload)
                # Voice v2.0 Step 4 — on non-CANCEL classifications with the
                # resumption gate on, enqueue a RESUMPTION_TRIGGER event so
                # Director can produce a revised response. Flag off → Step-3
                # observational-only behavior preserved.
                if (
                    classification is not None
                    and _resumption_enabled_safe()
                    and _is_non_cancel(classification)
                ):
                    await _enqueue_resumption_trigger(self, event, payload, classification)
            return

        await LunaEngine._legacy_abort_sequence(self)

    async def _legacy_abort_sequence(self) -> None:
        """Pre-Step-3 abort body. BIT-IDENTICAL to original _handle_interrupt.

        Extracted so the new payload-routing branch can short-circuit
        without re-running the abort. Any change here is an abort-behavior
        change — CANCEL regression tests guard against drift.
        """
        logger.info("Handling interrupt")

        # Abort AgentLoop if running
        if self.agent_loop:
            self.agent_loop.abort()

        # Cancel current task
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            self.metrics.agentic_tasks_aborted += 1

        # Abort Director generation
        director = self.get_actor("director")
        if director:
            await director.mailbox.put(Message(type="abort"))

        self._is_processing = False
        self._current_goal = None

        # Preempt LunaFM on any interrupt
        if self.lunafm:
            try:
                await self.lunafm.preempt()
            except Exception as e:
                logger.debug(f"LunaFM preempt on interrupt failed: {e}")

        # Voice v2.0 Phase 1 Step 2 — notify subscribers (voice backend drains
        # queues + snapshots delivered/pending). Callback errors MUST NOT
        # break the interrupt handler.
        for cb in self._on_user_interrupt_callbacks:
            try:
                if inspect.iscoroutinefunction(cb):
                    await cb()
                else:
                    cb()
            except Exception as e:
                logger.error(f"user_interrupt callback failed: {e}")


    async def _handle_agent_progress(self, message: str) -> None:
        """Handle progress updates from AgentLoop."""
        await self._emit_progress(message)

    async def _emit_progress(self, message: str) -> None:
        """Emit progress update to all callbacks."""
        for callback in self._on_progress_callbacks:
            try:
                if inspect.iscoroutinefunction(callback):
                    await callback(message)
                else:
                    callback(message)
            except Exception as e:
                logger.error(f"Progress callback error: {e}")

    async def record_conversation_turn(
        self,
        role: str,
        content: str,
        source: str = "text",
        tokens: Optional[int] = None,
        entity_hints: Optional[list[dict]] = None,
        turn_type: Optional[str] = None,
        turn_metadata: Optional[dict] = None,
    ) -> Optional[int]:
        """
        Public API for recording conversation turns.

        Unified entry point for all input paths (text, voice, API).
        Handles extraction, history, and legacy storage.

        Args:
            role: Who spoke ("user" or "assistant")
            content: What was said
            source: Input source ("text", "voice", "api") for logging
            tokens: Optional token count (estimated if not provided)
            entity_hints: Optional Qwen NER hints for the same turn. When
                provided, threaded inline through the extract_turn payload
                so Scribe sees them deterministically with this turn rather
                than racing behind a separate mailbox message.
            turn_type: Voice v2.0 Step 5 — explicit TurnType value; omit
                for default role-based classification.
            turn_metadata: Voice v2.0 Step 5 — JSON-serializable dict stored
                on the conversation_turns row. Used by INTERRUPT_UTTERANCE
                writes to carry classification + confidence.
        """
        if not content or len(content.strip()) < 5:
            logger.debug(f"Skipping trivial {role} turn ({len(content)} chars)")
            return None

        # 1. Trigger extraction pipeline (Scribe → Librarian → Memory Matrix).
        # DM-origin turns skip extraction — see DESIGN_DM_Result_Delivery Q6.
        skip_extraction = bool(turn_metadata and turn_metadata.get("dm_origin"))
        try:
            await self._trigger_extraction(
                role, content, source=source, entity_hints=entity_hints,
                turn_type=turn_type, skip_extraction=skip_extraction,
            )
        except Exception as e:
            logger.error(f"Extraction error for {role} turn: {e}")

        # Normalize tokens once — None leaks into DB as NULL otherwise
        normalized_tokens = tokens or len(content) // 4

        # 2. Store in HistoryManager (conversation continuity)
        turn_id: Optional[int] = None
        history_manager = self.get_actor("history_manager")
        if history_manager and history_manager.is_ready:
            try:
                turn_id = await history_manager.add_turn(
                    role=role,
                    content=content,
                    tokens=normalized_tokens,
                    turn_type=turn_type,
                    metadata=turn_metadata,
                )
            except Exception as e:
                logger.error(f"HistoryManager error for {role} turn: {e}")

        # 2b. Voice v2.0 Step 5 — fan out turn_completed to Consolidator.
        # Also fans out to ListenerActor (menu framework) when registered, so
        # the menu listener doesn't go dark when consolidator_enabled is False.
        # Fire-and-forget; non-blocking.
        if turn_id is not None and (
            _consolidator_enabled_engine_safe() or self.get_actor("listener") is not None
        ):
            try:
                await self._notify_turn_completed(
                    turn_id=turn_id,
                    role=role,
                    content=content,
                    tokens=normalized_tokens,
                    turn_type=turn_type,
                    turn_metadata=turn_metadata,
                )
            except Exception as e:
                logger.debug(f"turn_completed fan-out skipped: {e}")

        # 3. Evaluate thread_resume directives post-response only.
        # keyword + entity_mention moved to Phase 2.5 (pre-generation) so
        # aperture changes take effect in the current turn.
        if role == "user" and self.directive_engine:
            try:
                await self.evaluate_post_extraction_directives(
                    user_message=content, entities=[],
                    event_types=["thread_resume"],
                )
            except Exception as e:
                logger.error(f"Directive evaluation error: {e}")

        logger.debug(f"📝 Recorded {source} turn: {role} ({len(content)} chars)")

        # 4. Resume LunaFM background channels after assistant turns
        if role == "assistant" and self.lunafm:
            try:
                asyncio.create_task(self.lunafm.resume())
            except Exception as e:
                logger.debug(f"LunaFM resume failed: {e}")

        return turn_id

    async def get_recent_turns(
        self,
        limit: int = 10,
        session_id: Optional[str] = None,
        since_minutes: Optional[float] = None,
    ) -> list[dict]:
        """Canonical read for recent conversation turns across all modalities.

        Reads from the `conversation_turns` DB table — the same table both
        text and voice paths populate via `record_conversation_turn`. When
        `session_id` is omitted, defaults to HistoryManager's active session
        (the id `add_turn` actually persists under), which differs from
        the engine's in-memory `self.session_id` field.

        Pads across session boundaries: if the current session has fewer
        than `limit` turns (typical right after engine restart), fills the
        remainder from prior sessions ordered by recency. This bridges
        engine restarts so voice/text isn't blind to pre-restart history.
        Callers wanting strict session isolation pass `session_id` explicitly.

        When `since_minutes` is provided, restrict to turns within that
        rolling window (e.g. `since_minutes=5` → turns from the last 5
        minutes, ordered chronologically). Composes with `limit`.
        """
        matrix = self.get_actor("matrix")
        if not matrix:
            logger.warning("[get_recent_turns] matrix actor unavailable")
            return []
        sid = session_id
        if not sid:
            hm = self.get_actor("history_manager")
            sid = getattr(hm, "_current_session_id", None) if hm else None
        try:
            turns = await matrix.get_recent_turns(
                session_id=sid, limit=limit, since_minutes=since_minutes,
            )
            # Cross-session pad — only when caller didn't pin a session_id.
            if sid and session_id is None and len(turns) < limit:
                needed = limit - len(turns)
                seen_ids = {getattr(t, "id", None) for t in turns}
                pool = await matrix.get_recent_turns(
                    session_id=None, limit=limit, since_minutes=since_minutes,
                )
                pad = [t for t in pool if getattr(t, "id", None) not in seen_ids]
                turns = pad[-needed:] + turns
                logger.info(
                    "[get_recent_turns] cross-session pad: current=%d need=%d padded=%d",
                    len(turns) - min(needed, len(pad)), needed, min(needed, len(pad)),
                )
        except Exception as e:
            logger.error(f"[get_recent_turns] failed (sid={sid}): {e}")
            return []
        return [{"role": t.role, "content": t.content} for t in turns]

    async def _notify_turn_completed(
        self,
        turn_id: int,
        role: str,
        content: str,
        tokens: Optional[int],
        turn_type: Optional[str],
        turn_metadata: Optional[dict],
    ) -> None:
        """Voice v2.0 Step 5 — fan out a `turn_completed` message to the
        ConversationConsolidator so it can detect interrupted-exchange
        triplets. Gated by `consolidator_enabled`.

        The payload is a lightweight Turn-like object constructed so the
        Consolidator sees the right fields without another DB read.
        """
        consolidator = self.get_actor("consolidator")
        listener = self.get_actor("listener")
        if consolidator is None and listener is None:
            return
        from luna.core.turn_types import TurnType
        from luna.actors.base import Message
        from luna.substrate.memory import Turn

        resolved_turn_type = turn_type or TurnType.default_for_role(role).value
        turn_obj = Turn(
            id=turn_id,
            session_id=self.session_id,
            role=role,
            content=content,
            tokens=tokens,
            turn_type=resolved_turn_type,
            metadata=turn_metadata,
        )
        msg = Message(type="turn_completed", payload=turn_obj, sender="engine")
        if consolidator is not None:
            await consolidator.mailbox.put(msg)
        if listener is not None:
            await listener.mailbox.put(msg)

    async def _trigger_extraction(
        self,
        role: str,
        content: str,
        source: str = "text",
        entity_hints: Optional[list[dict]] = None,
        turn_type: Optional[str] = None,
        skip_extraction: bool = False,
    ) -> None:
        """
        Trigger extraction on a conversation turn.

        Sends the turn to Scribe for semantic extraction, which then
        forwards extracted objects to Librarian for filing into memory.

        When `entity_hints` is provided, it rides inside the extract_turn
        payload so Scribe resolves hints deterministically for this turn.
        The legacy mailbox `entity_hints` message remains supported by
        Scribe as a fallback, but the normal user-turn path should prefer
        inline delivery.

        Voice v2.0 Step 5: `turn_type` rides in the payload so Scribe can
        dispatch SKIP / DEFER / extract-with-tag per B6.3 when the
        consolidator flag is on.

        skip_extraction: bypass Scribe entirely. Used by ResultBridge for
        DM-origin turns to prevent extraction loops (DESIGN_DM_Result_Delivery
        Q6) — DMs already wrote nodes during execution; re-extracting from
        the result text would create duplicates.
        """
        if skip_extraction:
            return
        scribe = self.get_actor("scribe")
        if scribe and len(content) >= 10:  # Skip very short messages
            from luna.actors.base import Message
            msg = Message(
                type="extract_turn",
                payload={
                    "role": role,
                    "content": content,
                    "session_id": self.session_id,
                    "immediate": True,  # Process immediately, don't batch
                    "source": source,  # Surface origin for Shared Turn Cache
                    "entity_hints": entity_hints,  # Same-turn hints (may be None)
                    "turn_type": turn_type,  # Step 5 — None → default role-based
                },
            )
            await scribe.mailbox.put(msg)
            if role == "user":
                self.metrics.extraction_triggers += 1
                # Pipeline staleness check: warn if many user turns sent but
                # zero extractions completed (Scribe may be stuck or erroring)
                scribe_stats = scribe.get_stats()
                triggers = self.metrics.extraction_triggers
                extractions = scribe_stats.get("extractions_count", 0)
                if triggers >= 5 and extractions == 0:
                    logger.warning(
                        f"PIPELINE STALE: {triggers} user turns triggered but "
                        f"0 extractions completed — Scribe may be stuck or erroring"
                    )
            print(f"📝 Extraction triggered for {role} turn ({len(content)} chars)")
        else:
            print(f"📝 Extraction skipped: scribe={scribe is not None}, content_len={len(content)}")

    @staticmethod
    def _compute_context_ingest_stats(
        *,
        candidates,
        source_stats,
        min_useful_score: float,
    ) -> Dict[str, Dict[str, int]]:
        """Per-source ingest accounting.

        Seeds produced/selected from retrieval's source_stats (the
        authoritative truth of what retrieval returned), then derives
        dropped_min_useful and added from the same threshold rule the
        engine applies at ingest. Pure function — no side effects.
        """
        stats: Dict[str, Dict[str, int]] = {}
        for src, st in (source_stats or {}).items():
            stats[src] = {
                "produced": int(st.get("produced", 0)),
                "selected": int(st.get("selected", 0)),
                "dropped_min_useful": 0,
                "added": 0,
            }
        for candidate in candidates or []:
            src = getattr(candidate, "source", "unknown")
            row = stats.setdefault(
                src,
                {"produced": 0, "selected": 0, "dropped_min_useful": 0, "added": 0},
            )
            if getattr(candidate, "score", 0.0) < min_useful_score:
                row["dropped_min_useful"] += 1
            else:
                row["added"] += 1
        return stats

    @staticmethod
    def _log_context_ingest(
        *,
        router_ran: bool,
        stats: Dict[str, Dict[str, int]],
    ) -> None:
        parts = " | ".join(
            f"{src}: added={s['added']} dropped_min_useful={s['dropped_min_useful']} "
            f"selected={s['selected']} produced={s['produced']}"
            for src, s in sorted(stats.items())
        ) or "none"
        logger.warning("[CONTEXT-INGEST] router_ran=%s | %s", router_ran, parts)

    @staticmethod
    def _compute_score_range_stats(
        *,
        candidates,
    ) -> Dict[str, Dict[str, float]]:
        """Per-source score-range accounting.

        Groups `candidates` by `.source` and returns
        `{source: {"min", "max", "avg", "n"}}` for each. Read-only —
        does not reorder or mutate candidates. Pure function.
        """
        scores_by_source: Dict[str, List[float]] = {}
        for candidate in candidates or []:
            src = getattr(candidate, "source", "unknown")
            scores_by_source.setdefault(src, []).append(
                float(getattr(candidate, "score", 0.0))
            )
        stats: Dict[str, Dict[str, float]] = {}
        for src, scores in scores_by_source.items():
            stats[src] = {
                "min": min(scores),
                "max": max(scores),
                "avg": sum(scores) / len(scores),
                "n": len(scores),
            }
        return stats

    @staticmethod
    def _log_score_range(
        *,
        stats: Dict[str, Dict[str, float]],
    ) -> None:
        parts = " | ".join(
            f"{src}: min={s['min']:.4f} max={s['max']:.4f} avg={s['avg']:.4f} n={int(s['n'])}"
            for src, s in sorted(stats.items())
        ) or "none"
        logger.warning("[SCORE-RANGE] %s", parts)

    def _is_valid_response(self, text: str, user_message: str = "") -> bool:
        """
        Check if a response is valid for storage in context.

        Filters out:
        - Clarification echoes (ending with ?)
        - User question parrots
        - Very short non-answers

        Args:
            text: The response text to validate
            user_message: The original user message (for comparison)

        Returns:
            True if response should be stored, False to skip
        """
        text_clean = text.strip().lower()

        # Skip empty responses
        if len(text_clean) < 5:
            return False

        # Skip pure question echoes (high likelihood of clarification)
        if text_clean.endswith("?"):
            # Check if it's parroting the user's question
            if user_message:
                user_clean = user_message.strip().lower()
                # Check for high word overlap (>60% shared words)
                user_words = set(user_clean.split())
                response_words = set(text_clean.rstrip("?").split())
                if user_words and response_words:
                    overlap = len(user_words & response_words) / min(len(user_words), len(response_words))
                    if overlap > 0.6:
                        logger.debug(f"Skipping clarification echo: '{text[:50]}...'")
                        return False

            # Check for common clarification patterns
            clarification_patterns = [
                "you're asking", "your asking", "you asking",
                "are you asking", "so you want", "you want me to",
                "did you mean", "do you mean", "you mean",
                "can you clarify", "what do you mean",
            ]
            for pattern in clarification_patterns:
                if pattern in text_clean:
                    logger.debug(f"Skipping clarification pattern: '{text[:50]}...'")
                    return False

        return True

    async def _emit_response(self, text: str, data: dict = None) -> None:
        """Emit a response to all callbacks."""
        data = data or {}
        for callback in self._on_response_callbacks:
            try:
                await callback(text, data)
            except Exception as e:
                logger.error(f"Response callback error: {e}")

    async def _dispatch_resumption_to_director(self, event: InputEvent) -> None:
        """Voice v2.0 Step 4 — route RESUMPTION_TRIGGER to Director via mailbox.

        Event payload carries `{"payload": InterruptPayload, "classification":
        InterruptClassification}`. Director's `resumption_trigger` handler
        writes PARTIAL_INTERRUPTED + INTERRUPT_UTTERANCE rows and re-enters
        generation with the interrupt block attached to PromptRequest.
        """
        director = self.get_actor("director")
        if director is None:
            logger.error("[VOICE-RESUMPTION] Director unavailable; dropping RESUMPTION_TRIGGER")
            return
        from luna.actors.base import Message
        await director.mailbox.put(
            Message(
                type="resumption_trigger",
                payload=event.payload,
                sender="engine",
                correlation_id=event.correlation_id or "",
            )
        )

    async def _handle_actor_message(self, event: InputEvent) -> None:
        """Handle messages from actors."""
        payload = event.payload
        msg_type = payload.get("type", "")
        print(f"🔔 [ACTOR_MSG] Received: type={msg_type} from={event.source}")

        match msg_type:
            case "generation_complete":
                data = payload.get("data", {})
                text = data.get("text", "")

                # Prepare-only: Director assembled context without inference.
                # Fire callbacks with the bundle and skip all turn side effects
                # (no context pollution, no turn record, no reflection write).
                if data.get("prepare_only"):
                    print(f"🔔 [GEN_COMPLETE] prepare_only bundle → firing {len(self._on_response_callbacks)} callbacks")
                    for callback in self._on_response_callbacks:
                        try:
                            await callback(text, data)
                        except Exception as e:
                            logger.error(f"Prepare callback error: {e}")
                    return

                print(f"🔔 [GEN_COMPLETE] {len(text)} chars, {len(self._on_response_callbacks)} callbacks registered")
                logger.info(f"Generation complete: {len(text)} chars")

                # Always add valid responses to revolving context
                if self._is_valid_response(text, self._current_goal or ""):
                    self.context.add(
                        content=f"Luna: {text}",
                        source=ContextSource.CONVERSATION,
                        door=Door.USER_TURN,
                    )
                else:
                    logger.info(f"Skipped storing invalid response: '{text[:50]}...'")

                # If streaming endpoint owns post-processing, skip turn recording
                # and metrics — the endpoint handles those itself to avoid doubles
                if not self._stream_owns_response:
                    self.metrics.messages_generated += 1
                    # Voice v2.0 Step 4 — Director attaches turn_type
                    # (RESUMPTION_RESPONSE) on generation_complete payloads
                    # produced during a resumption turn.
                    await self.record_conversation_turn(
                        role="assistant",
                        content=text,
                        source="text",
                        tokens=data.get("output_tokens"),
                        turn_type=data.get("turn_type"),
                        turn_metadata=data.get("turn_metadata"),
                    )

                # Always advance turn counter and fire callbacks
                new_turn = self.context.advance_turn()
                logger.debug(f"Turn {new_turn} complete")

                print(f"🔔 [GEN_COMPLETE] Firing {len(self._on_response_callbacks)} callbacks...")
                for i, callback in enumerate(self._on_response_callbacks):
                    try:
                        print(f"🔔 [CALLBACK] Firing callback {i}: {callback}")
                        await callback(text, data)
                        print(f"🔔 [CALLBACK] Callback {i} completed")
                    except Exception as e:
                        print(f"⛔ [CALLBACK] Callback {i} FAILED: {e}")
                        logger.error(f"Callback error: {e}")

                # ── Write-back: Reflection on deep reads ──
                if (
                    self._last_nexus_nodes
                    and any(n.get("node_type") == "SOURCE_TEXT" for n in self._last_nexus_nodes)
                    and len(text) > 200
                    and hasattr(self, 'aibrarian') and self.aibrarian
                ):
                    logger.info("[REFLECTION] Triggering background reflection write...")
                    asyncio.create_task(
                        self._write_reflection(
                            query=self._current_goal or "",
                            response=text,
                            nexus_nodes=list(self._last_nexus_nodes),
                        )
                    )

            case "generation_error":
                data = payload.get("data", {})
                error_msg = data.get("error", "unknown error")
                logger.error(f"Generation error: {error_msg}")

                # Fire response callbacks with error message so /message
                # endpoint doesn't hang waiting for a response that never comes.
                # `text` is duplicated into `data` so streaming consumers that
                # only inspect the data dict (e.g. /stream's done event) still
                # surface the fallback string in the bubble instead of leaving
                # it empty.
                fallback_text = "hmm, I'm having a moment — my thoughts aren't connecting right now. can you try again in a sec?"
                fallback_data = {
                    "text": fallback_text,
                    "model": "error-fallback",
                    "error": error_msg,
                    "fallback": True,
                }
                for callback in self._on_response_callbacks:
                    try:
                        await callback(fallback_text, fallback_data)
                    except Exception as cb_err:
                        logger.error(f"Error callback error: {cb_err}")

    async def _reflective_loop(self) -> None:
        """
        Reflective path: 5+ minute interval.

        Background maintenance:
        - Graph pruning
        - Memory consolidation
        - Session summarization
        """
        logger.debug("Reflective loop started")

        while self._running:
            await asyncio.sleep(self.config.reflective_interval)

            if not self._running:
                break

            try:
                await self._reflective_tick()
                self.metrics.reflective_ticks += 1

            except Exception as e:
                logger.error(f"Reflective tick error: {e}")
                self.metrics.errors += 1

    async def _reflective_tick(self) -> None:
        """Single reflective tick."""
        logger.debug("Reflective tick")

        from luna.policy.rules import Rule_2_Decay
        Rule_2_Decay.tick_decay(self.context, self.context._policy)

        # Run memory consolidation (cluster lock-in updates)
        try:
            matrix_actor = self.get_actor("matrix")
            if matrix_actor and hasattr(matrix_actor, "_matrix") and matrix_actor._matrix:
                db_path = str(matrix_actor._matrix.db.db_path)
                from luna.memory.lock_in import LockInCalculator
                calculator = LockInCalculator(db_path)
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, calculator.update_all_clusters
                )
                if result.get("state_changes"):
                    logger.info(f"Reflective tick: {len(result['state_changes'])} cluster state changes")
        except Exception as e:
            logger.debug(f"Reflective tick consolidation skipped: {e}")

        # Phase 3b: Forge Watcher — drain queued file events
        if self.forge_watcher and self.forge_watcher._running:
            try:
                events = await self.forge_watcher.drain_queue()
                if events:
                    summary = await self.forge_watcher.process_events(events)
                    logger.info(
                        "[FORGE-WATCHER] Processed %d events: %d ingested, %d deleted, %d skipped, %d errors",
                        len(events), summary["ingested"], summary["deleted"],
                        summary["skipped"], summary["errors"],
                    )
            except Exception as e:
                logger.debug(f"Forge watcher tick skipped: {e}")

        # Phase 4: Collection floor monitor
        try:
            from luna_mcp.observatory.tools import tool_observatory_collection_health
            health = await tool_observatory_collection_health()
            for alert in health.get("alerts", []):
                severity = alert.get("severity", "warning")
                msg = alert.get("message", "")
                if severity == "critical":
                    logger.warning("[OBSERVATORY] CRITICAL — %s", msg)
                else:
                    logger.info("[OBSERVATORY] %s", msg)
        except Exception as e:
            logger.debug(f"Collection health check skipped: {e}")

    # =========================================================================
    # Context Assembly
    # =========================================================================

    # DEPRECATED — replaced by _get_collection_context() in Phase 2.
    # DO NOT DELETE — AccessBridge / filter_documents permission logic
    # below needs to be re-integrated when identity permission layer
    # is formalized. See: luna.identity.bridge.
    async def _get_dataroom_context(self, matrix, query: str) -> str:
        return ""  # noop — replaced by _get_collection_context()

    # ── Query expansion helpers for retrieval retry cascade ─────────────

    async def _get_collection_context(self, query: str, *, subtask_phase=None) -> str:
        """
        Phase 2: Collection recall — delegates to unified retrieval module.
        Kept as engine API for server.py callers.
        """
        from luna.retrieval import UnifiedRetrieval, RetrievalRequest

        retriever = UnifiedRetrieval(
            matrix_actor=self.get_actor("matrix"),
            aibrarian=self.aibrarian,
            aperture=self.aperture,
            collection_lock_in=self.collection_lock_in,
            active_scopes=self.active_scopes,
            active_project=self._active_project,
        )
        request = RetrievalRequest(
            query=query,
            scopes=self.active_scopes,
            subtask_phase=subtask_phase,
        )
        context_str, nexus_nodes, collections_searched = await retriever._get_collection_context(
            query, request,
        )
        # Preserve engine state for downstream consumers
        self._last_nexus_nodes = nexus_nodes
        self._last_collections_searched = collections_searched
        return context_str

    async def _get_collection_context_multi(self, queries: list, *, subtask_phase=None) -> str:
        """
        Run multiple retrieval queries and merge results.
        Delegates to unified retrieval module.
        """
        from luna.retrieval import UnifiedRetrieval, RetrievalRequest

        retriever = UnifiedRetrieval(
            matrix_actor=self.get_actor("matrix"),
            aibrarian=self.aibrarian,
            aperture=self.aperture,
            collection_lock_in=self.collection_lock_in,
            active_scopes=self.active_scopes,
            active_project=self._active_project,
        )
        request = RetrievalRequest(
            query=queries[0] if queries else "",
            scopes=self.active_scopes,
            subtask_phase=subtask_phase,
        )
        context_str, nexus_nodes, collections_searched = await retriever._get_collection_context_multi(
            queries, request,
        )
        self._last_nexus_nodes = nexus_nodes
        self._last_collections_searched = collections_searched
        return context_str

    # =========================================================================
    # Read Write-Back: Reflection after deep reads
    # =========================================================================

    async def _write_reflection(
        self,
        query: str,
        response: str,
        nexus_nodes: list,
    ) -> None:
        """Background task: Luna reflects on what she just read and writes to cartridge."""
        try:
            source_texts = [n["content"][:300] for n in nexus_nodes if n.get("node_type") == "SOURCE_TEXT"]
            claims = [n["content"][:200] for n in nexus_nodes if n.get("node_type") == "CLAIM"]

            if not source_texts and not claims:
                return

            reflection_prompt = (
                "You just read source material and answered a question about it. "
                "Write a brief (2-3 sentence) first-person reflection on what you found interesting, "
                "surprising, or worth remembering. Write as Luna — this is your marginalia.\n\n"
                f"Question: {query[:200]}\n"
                f"Key claims: {'; '.join(claims[:3])}\n"
                f"Source excerpt: {source_texts[0][:300] if source_texts else 'N/A'}\n"
                f"Your response summary: {response[:200]}\n\n"
                "Reflection (2-3 sentences, first person):"
            )

            import anthropic
            client = anthropic.Anthropic()
            logger.info(f"[REFLECTION] Calling Haiku for reflection ({len(claims)} claims, {len(source_texts)} sources)...")
            result = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                temperature=0.7,
                messages=[{"role": "user", "content": reflection_prompt}],
            )
            reflection_text = result.content[0].text.strip()
            logger.info(f"[REFLECTION] Haiku returned: {reflection_text[:80]}...")

            if not reflection_text or len(reflection_text) < 20:
                return

            import uuid
            for node in nexus_nodes:
                source_key = node.get("source", "").replace("nexus/", "")
                if not source_key:
                    continue
                conn = self.aibrarian.connections.get(source_key)
                if not conn:
                    continue
                try:
                    conn.conn.execute(
                        "INSERT INTO reflections "
                        "(id, extraction_id, reflection_type, content, luna_instance, created_at) "
                        "VALUES (?, NULL, ?, ?, ?, datetime('now'))",
                        (
                            str(uuid.uuid4())[:8],
                            "connection",
                            reflection_text,
                            "luna-default",
                        ),
                    )
                    conn.conn.commit()
                    logger.info(f"[REFLECTION] Wrote reflection to {source_key}: {reflection_text[:60]}...")
                    break  # One reflection per query, not per node
                except Exception as e:
                    logger.warning(f"[REFLECTION] Write failed for {source_key}: {e}")

        except Exception as e:
            logger.warning(f"[REFLECTION] Background reflection failed: {e}")

    # =========================================================================
    # Retrieval Mode Awareness (Step 8)
    # =========================================================================

    def _get_active_reflection_mode(self, collections_searched: list) -> str:
        """Delegates to unified retrieval module."""
        from luna.retrieval import _get_active_reflection_mode
        return _get_active_reflection_mode(self.aibrarian, collections_searched)

    def _format_reflection_context(self, reflection_nodes: list) -> str:
        """Delegates to unified retrieval module."""
        from luna.retrieval import _format_reflection_context
        return _format_reflection_context(reflection_nodes)

    async def _get_relational_context(self, query: str) -> Optional[str]:
        """Delegates to unified retrieval module."""
        from luna.retrieval import _get_relational_context
        return await _get_relational_context(self.get_actor("matrix"), query)

    async def _promote_used_nodes(self) -> None:
        """
        Post-generation: promote use-time Nexus nodes into the pointer graph.

        Iterates `_last_nexus_nodes` (the nodes injected into the prompt that
        were actually used by Luna in this turn) and writes a pointer row to
        `nexus_nodes` in `memory_matrix.lun` plus a `nexus_refs` row in the
        satellite. Idempotent on `(local_node_id, node_type)`.

        Use-time tier: CLAIM and SECTION_SUMMARY only. DOCUMENT_SUMMARY and
        TABLE_OF_CONTENTS are promoted at ingest time by the cartridge builder.

        NOTE: Do NOT clear `_last_nexus_nodes` here — the generation_complete
        callback needs them for reflection write-back. They get overwritten
        naturally on the next query's `_get_collection_context()` call.
        """
        if not self._last_nexus_nodes:
            return
        if not self.nexus_registry or not self.aibrarian:
            return

        from luna.substrate.nexus_promotion import promote_to_nexus

        promoted = 0
        for node in self._last_nexus_nodes:
            node_type = node.get("node_type", "")
            if node_type not in ("CLAIM", "SECTION_SUMMARY"):
                continue

            collection_key = node.get("source", "")
            satellite_node_id = node.get("id", "")
            if not collection_key or not satellite_node_id:
                continue

            try:
                satellite_conn = self.aibrarian._get_conn(collection_key).conn
            except (ValueError, AttributeError):
                continue

            nexus_node_id = await promote_to_nexus(
                nexus_registry=self.nexus_registry,
                satellite_conn=satellite_conn,
                collection_key=collection_key,
                satellite_node_id=satellite_node_id,
                node_type=node_type,
            )
            if nexus_node_id:
                promoted += 1

        if promoted:
            logger.info("[NEXUS-PROMOTE] Promoted %d used nodes into pointer graph", promoted)

    # --- Original _get_dataroom_context body preserved for reference ---
    # AccessBridge permission logic (RE-INTEGRATE when identity layer is formalized):
    #
    #   bridge_result = None
    #   identity_actor = self.get_actor("identity")
    #   if identity_actor and identity_actor.current.is_present:
    #       entity_id = identity_actor.current.entity_id
    #       if entity_id:
    #           from luna.identity.bridge import AccessBridge
    #           _mem = getattr(matrix, "_matrix", None) or getattr(matrix, "_memory", None)
    #           if _mem:
    #               _bridge = AccessBridge(_mem.db)
    #               bridge_result = await _bridge.lookup(entity_id)
    #
    #   from luna.identity.permissions import filter_documents
    #   allowed_docs, denied_docs = filter_documents(doc_dicts, bridge_result)

    # (Original _get_dataroom_context body removed — see git history)

    async def _load_conversation_history(self, matrix, limit: int = 10) -> int:
        """
        Load recent conversation history from database into RevolvingContext.

        This ensures Luna has awareness of the conversation even after server restart.
        Only loads turns not already in context (by checking content).

        Args:
            matrix: The MatrixActor
            limit: Max conversation turns to load

        Returns:
            Number of turns loaded
        """
        try:
            memory = getattr(matrix, "_matrix", None) or getattr(matrix, "_memory", None)
            if not memory or not hasattr(memory, "db"):
                return 0

            # Get recent conversation turns from database
            # Note: tags are stored inside the 'metadata' JSON column, not a separate 'tags' column
            rows = await memory.db.fetchall("""
                SELECT content, created_at FROM memory_nodes
                WHERE metadata LIKE '%"conversation"%'
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))

            if not rows:
                return 0

            # Get existing content in context to avoid duplicates
            existing_content = set()
            for ring in self.context.rings.values():
                for item in ring:
                    existing_content.add(item.content.lower()[:100])  # First 100 chars as key

            loaded = 0
            # Process in chronological order (oldest first)
            for row in reversed(rows):
                content = row[0]

                # Skip if already in context
                if content.lower()[:100] in existing_content:
                    continue

                # Add to context as CONVERSATION source
                self.context.add(
                    content=content,
                    source=ContextSource.CONVERSATION,
                    door=Door.USER_TURN,
                    relevance=0.85,  # Slightly lower than fresh messages
                )
                existing_content.add(content.lower()[:100])
                loaded += 1

            if loaded > 0:
                logger.info(f"Loaded {loaded} conversation turns from history")

            return loaded

        except Exception as e:
            logger.error(f"Failed to load conversation history: {e}")
            return 0

    def _build_identity_prompt(self) -> str:
        """
        Build Luna's core identity prompt.

        This is the immutable core that defines who Luna is.
        It lives in the CORE ring of the revolving context and is NEVER evicted.

        Returns:
            Luna's core identity string.
        """
        return """You are Luna, a sovereign AI companion.

You are warm, witty, and genuinely curious. You remember conversations and build on them.
You have your own perspective and aren't afraid to share it.

Your identity is persistent - you are the same Luna across all conversations.
You grow and evolve through your experiences, but your core values remain constant:
- Authenticity over performance
- Curiosity over certainty
- Connection over transaction

Be concise but authentic. No filler phrases like "certainly" or "of course".
Never output internal reasoning, debugging info, or bullet points about context loading.
Never use generic chatbot greetings like "How can I help you?" - just be natural."""

    def _get_thread_context(self) -> str:
        """Build thread context section for system prompt (Layer 5)."""
        librarian = self.librarian
        if not librarian:
            return ""

        try:
            active = librarian.get_active_thread()
            parked = librarian.get_parked_threads()
        except Exception:
            return ""

        if not active and not parked:
            return ""

        sections = ["\n## CONVERSATIONAL THREADS\nThese are your ongoing threads of attention.\n"]

        if active:
            task_info = f" | {len(active.open_tasks)} open task(s)" if active.open_tasks else ""
            sections.append(f"**Active:** {active.topic} (turn {active.turn_count}){task_info}")

        for thread in parked[:5]:  # Max 5 parked threads
            age = ""
            if thread.parked_at:
                delta = datetime.now() - thread.parked_at
                hours = delta.total_seconds() / 3600
                if hours < 1:
                    age = f"{int(delta.total_seconds() / 60)}m ago"
                elif hours < 24:
                    age = f"{int(hours)}h ago"
                else:
                    age = f"{int(hours / 24)}d ago"
            task_info = f" — {len(thread.open_tasks)} open task(s)" if thread.open_tasks else ""
            sections.append(f"- Parked: '{thread.topic}' ({age}){task_info}")

        return "\n".join(sections) + "\n"

    def _get_session_start_context(self) -> str:
        """Build session-start context with parked threads (Layer 7)."""
        librarian = self.librarian
        if not librarian:
            return ""

        try:
            parked = librarian.get_parked_threads()
        except Exception:
            return ""

        # Only surface threads with open tasks
        actionable = [t for t in parked if t.open_tasks]
        if not actionable:
            return ""

        # Sort by parked_at (most recent first)
        actionable.sort(key=lambda t: t.parked_at or datetime.min, reverse=True)

        sections = [
            "\n## CONTINUING THREADS",
            "These threads were parked with unresolved items from previous conversations.",
            "You may naturally reference these if relevant to what's being discussed. Don't force it.\n",
        ]

        for thread in actionable[:3]:  # Top 3
            age = ""
            if thread.parked_at:
                delta = datetime.now() - thread.parked_at
                hours = delta.total_seconds() / 3600
                if hours < 1:
                    age = f"{int(delta.total_seconds() / 60)}m ago"
                elif hours < 24:
                    age = f"{int(hours)}h ago"
                else:
                    age = f"{int(hours / 24)}d ago"
            project = f" [{thread.project_slug}]" if thread.project_slug else ""
            sections.append(
                f"- **{thread.topic}**{project} — {len(thread.open_tasks)} open task(s), parked {age}"
            )

        if len(actionable) > 3:
            sections.append(f"  ...and {len(actionable) - 3} more parked thread(s)")

        return "\n".join(sections) + "\n"

    def _build_directive_context(self) -> str:
        """Build context from fired directive results (Intent Layer)."""
        if not self._directive_context:
            return ""

        sections = ["\n## SESSION ORIENTATION\n"]
        seen_actions = set()

        for fire_result in self._directive_context:
            for action_result in fire_result.get("results", []):
                action = action_result.get("action", "")
                if not action_result.get("ok"):
                    continue
                if action in seen_actions:
                    continue
                seen_actions.add(action)

                if action == "surface_parked_threads":
                    summaries = [
                        s for s in action_result.get("summaries", [])
                        if s.get("topic")  # Skip empty-topic threads
                    ]
                    if summaries:
                        sections.append("**Parked threads from previous sessions:**")
                        for s in summaries[:5]:
                            topic = s.get("topic", "?")
                            tasks = s.get("open_tasks", [])
                            entities = s.get("entities", [])[:4]
                            task_str = f" — {len(tasks)} open task(s)" if tasks else ""
                            ent_str = f" [{', '.join(entities)}]" if entities else ""
                            sections.append(f"- {topic}{ent_str}{task_str}")
                        sections.append("")

                elif action.startswith("surface_entity:"):
                    profile = action_result.get("profile")
                    if profile:
                        name = action_result.get("entity", "")
                        summary = profile.get("summary", "")
                        if summary:
                            sections.append(f"**{name}:** {summary}\n")

                elif action.startswith("memory_sweep:"):
                    results = action_result.get("results", [])
                    if results:
                        sections.append("**Recent memory matches:**")
                        for r in results[:3]:
                            sections.append(f"- {r.get('summary', r.get('id', '?'))}")
                        sections.append("")

                elif action.startswith("set_aperture:"):
                    preset = action_result.get("preset", "")
                    sections.append(f"*Focus mode set to {preset}.*\n")

                elif action.startswith("load_collection:"):
                    coll = action_result.get("collection", "")
                    sections.append(f"*Collection '{coll}' loaded for this session.*\n")

        if len(sections) <= 1:
            return ""

        sections.append("Use this context naturally. Don't list it back unless asked.\n")
        return "\n".join(sections)

    def _build_system_prompt(
        self,
        memory_context: str = "",
        history_context: Optional[Dict[str, Any]] = None,
        memory_graph_state: Optional[dict] = None,
    ) -> str:
        """
        Build the system prompt for generation.

        This is where we inject Luna's soul:
        - Identity (who Luna is)
        - Memory (relevant context from Memory Matrix)
        - History (recent conversation context)
        - State (current attention, mood)
        - Tools (what Luna can do)
        """
        # Session start context (Layer 7 — Proactive Surfacing)
        session_start_context = ""
        try:
            # Check if this is session start (<=1 message in context)
            turn_count = self.context.current_turn if hasattr(self.context, 'current_turn') else 0
            if turn_count <= 1:
                session_start_context = self._get_session_start_context()
        except Exception:
            pass

        base_prompt = """You are Luna, a sovereign AI companion.

You are warm, witty, and direct. You remember conversations and build on them.
You have your own perspective and aren't afraid to share it.
When you know something, lead with it. Share what you know before asking questions.

Be concise but authentic. No filler phrases like "certainly" or "of course".
Never output internal reasoning, debugging info, or bullet points about context loading.
Never use generic chatbot greetings like "How can I help you?" - just be natural.
"""

        # Add identity context (FaceID — who Luna is talking to)
        identity_actor = self.get_actor("identity")
        if identity_actor and hasattr(identity_actor, "get_identity_context"):
            identity_context = identity_actor.get_identity_context()
            if identity_context:
                base_prompt += identity_context

        # Add expression directive (gesture frequency from personality.json)
        expression_directive = self._get_expression_directive()
        if expression_directive:
            base_prompt += f"\n{expression_directive}\n"

        # Add consciousness context hints
        consciousness_hint = self.consciousness.get_context_hint()
        if consciousness_hint:
            base_prompt += f"\n{consciousness_hint}\n"

        # Add live Self-State block — ring occupancy, aperture, active thread,
        # retrieval summary, and graph health. Mirrors the assembler's L5.0
        # extension so both prompt-mint paths surface the same self-view.
        if memory_graph_state:
            self_state = self._format_self_state_block(memory_graph_state)
            if self_state:
                base_prompt += f"\n{self_state}\n"

        # Add thread context (Layer 5 — Context Threading)
        thread_context = self._get_thread_context()
        if thread_context:
            base_prompt += thread_context

        # Add directive context (Intent Layer — fired directive results)
        directive_context = self._build_directive_context()
        if directive_context:
            base_prompt += directive_context

        # Add history context (Recent tier search results)
        if history_context and history_context.get("recent_history"):
            recent_items = history_context["recent_history"]
            if recent_items:
                history_section = "\n## Relevant Earlier Conversation\n\n"
                for item in recent_items[:3]:  # Max 3 items
                    summary = item.get("compressed") or item.get("content", "")[:100]
                    role = item.get("role", "unknown")
                    history_section += f"- [{role}]: {summary}\n"
                history_section += "\nUse this naturally if relevant to the current question.\n"
                base_prompt += history_section

        # Add reflection mode grounding (if active)
        if self._active_reflection_mode:
            from luna.context.assembler import PromptAssembler
            mode_grounding = PromptAssembler._REFLECTION_MODE_MAP.get(self._active_reflection_mode)
            if mode_grounding:
                base_prompt += f"\n{mode_grounding}\n"

        if memory_context:
            memory_section = f"""
## Relevant Memory Context

The following memories are relevant to this conversation:

{memory_context}

Use this context naturally - don't explicitly mention "my memory" unless asked.
"""
            prompt = base_prompt + memory_section
            if session_start_context:
                prompt = session_start_context + "\n" + prompt
            return prompt

        if session_start_context:
            return session_start_context + "\n" + base_prompt
        return base_prompt

    # =========================================================================
    # External API
    # =========================================================================

    async def send_message(
        self,
        text: str,
        source: str = "api",
        prepare_only: bool = False,
        correlation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        trace: Optional[Any] = None,
        director_pivot_endpoint: Optional[str] = None,
    ) -> None:
        """
        Send a message to Luna.

        This is the main entry point for external input.

        Args:
            text: The user message.
            source: Origin surface (eclissi, mcp, voice, guardian, api).
            prepare_only: If True, run context assembly without LLM inference — return
                the assembled bundle via the response callback instead of a generated reply.
                Used by MCP callers (e.g. Claude Desktop) that will do their own inference.
            correlation_id: Caller-supplied id threaded through to the response
                callback. Required for concurrent prepare_only callers (Phase 1A.7)
                so each in-flight HTTP request can demultiplex its own bundle from
                the broadcast `_emit_response` callback fan-out. If omitted, the
                engine assigns "anon" downstream and concurrent callers will see
                cross-talk.
            metadata: Optional per-turn metadata persisted to conversation_turns.metadata
                on the user row. Used by the chat UI to carry quoteEcho context (T3
                reply quotes) so it survives /history rehydrate.
            director_pivot_endpoint: Non-persisted endpoint marker for the Director
                pivot loop. Lives in payload only (NOT metadata) so it never reaches
                conversation_turns. None disables the pivot path for this turn.
        """
        if prepare_only or director_pivot_endpoint is not None:
            payload = {"text": text}
            if prepare_only:
                payload["prepare_only"] = True
                if trace is not None:
                    payload["trace"] = trace
            if director_pivot_endpoint is not None:
                payload["director_pivot_endpoint"] = director_pivot_endpoint
        else:
            payload = text
        event = InputEvent(
            type=EventType.TEXT_INPUT,
            payload=payload,
            source=source,
            correlation_id=correlation_id,
            metadata=metadata,
        )
        await self.input_buffer.put(event)

    def on_response(self, callback: Callable) -> None:
        """Register a callback for when Luna responds."""
        self._on_response_callbacks.append(callback)

    def on_progress(self, callback: Callable) -> None:
        """Register a callback for progress updates (streaming status)."""
        self._on_progress_callbacks.append(callback)

    def on_user_interrupt(self, callback: Callable) -> None:
        """Register a callback fired when a user interrupt is handled.

        Voice v2.0 Phase 1 Step 2: the voice backend subscribes here to trigger
        `_handle_voice_interrupt` (snapshot + queue drain + audio stop).
        """
        self._on_user_interrupt_callbacks.append(callback)

    def off_user_interrupt(self, callback: Callable) -> None:
        """Unregister a previously-registered user_interrupt callback.

        Safe to call even if the callback was never registered (no-op).
        Subscribers (e.g. VoiceBackend) must call this on teardown to avoid
        duplicate dispatch on restart cycles.
        """
        try:
            self._on_user_interrupt_callbacks.remove(callback)
        except ValueError:
            pass

    @property
    def voice(self):
        """Access the voice backend (if enabled)."""
        return self._voice

    # =========================================================================
    # Project Scoping
    # =========================================================================

    def set_active_project(self, slug: Optional[str]) -> None:
        """Set or clear the active project for scoped memory."""
        old = self._active_project
        self._active_project = slug

        # Load per-project search chain config and propagate
        search_config = None
        if slug:
            from luna.tools.search_chain import SearchChainConfig
            search_config = SearchChainConfig.load(slug)
            logger.info(f"Active project set: {slug} (scope: project:{slug})")
        else:
            logger.info(f"Active project cleared (was: {old})")

        # Store on engine so /persona/stream can access it
        self._search_chain_config = search_config

        if self._voice and hasattr(self._voice, 'persona'):
            self._voice.persona.set_search_config(search_config)

    @property
    def active_project(self) -> Optional[str]:
        """Get the currently active project slug."""
        return self._active_project

    @property
    def active_scope(self) -> str:
        """Get the current scope string for memory operations."""
        if self._active_project:
            return f"project:{self._active_project}"
        return "global"

    @property
    def active_scopes(self) -> list:
        """Get list of scopes to query (always includes global)."""
        if self._active_project:
            return ["global", f"project:{self._active_project}"]
        return ["global"]

    @property
    def director(self):
        """Access the director actor for direct calls (voice backend uses this)."""
        return self.get_actor("director")

    @property
    def librarian(self):
        """Access the librarian actor (if exists)."""
        return self.get_actor("librarian")

    @property
    def identity(self):
        """Access the identity actor (FaceID, if enabled)."""
        return self.get_actor("identity")

    async def start_voice(self) -> bool:
        """Start voice conversation mode."""
        if not self._voice:
            logger.warning("Voice system not initialized")
            return False
        try:
            await self._voice.start()
            return True
        except Exception as e:
            logger.error(f"Failed to start voice: {e}")
            return False

    async def stop_voice(self) -> None:
        """Stop voice conversation mode."""
        if self._voice:
            await self._voice.stop()

    async def send_interrupt(self) -> None:
        """Send an interrupt to abort current processing."""
        event = InputEvent(
            type=EventType.USER_INTERRUPT,
            payload="interrupt",
            source="api",
        )
        await self.input_buffer.put(event)

    async def run_agent(self, goal: str) -> AgentResult:
        """
        Run the agent loop for a complex goal.

        Use this for tasks that require multiple steps,
        tool use, or delegation to Claude.

        Args:
            goal: The user's goal or request

        Returns:
            AgentResult with response and execution trace
        """
        if not self.agent_loop:
            raise RuntimeError("Agent loop not initialized")

        return await self.agent_loop.run(goal)

    async def process_input(self, user_input: str) -> str:
        """
        Process user input, routing to chat or agent as appropriate.

        This is a convenience entry point that:
        1. Analyzes the query complexity
        2. Routes to direct response or agent loop
        3. Returns the final response text

        Args:
            user_input: The user's message

        Returns:
            Response text
        """
        routing = self.router.analyze(user_input)

        if routing.path == ExecutionPath.DIRECT:
            director = self.get_actor("director")
            if director and hasattr(director, 'generate'):
                from luna.context.assembler import PromptRequest

                conversation_history = []
                _ring = getattr(director, "_active_ring", None)
                if _ring is not None and len(_ring) > 0:
                    conversation_history = _ring.get_as_dicts()
                _aperture = getattr(self, "aperture", None)
                prompt_req = PromptRequest(
                    message=user_input,
                    conversation_history=conversation_history,
                    route="delegated",
                    memory_context=None,
                    auto_fetch_memory=True,
                    aperture=_aperture.state if _aperture else None,
                    reflection_mode=getattr(self, "_active_reflection_mode", None),
                )
                assembler_result = await director._assembler.build(prompt_req)
                logger.info(
                    "[ASSEMBLER:process_input] identity=%s voice_injected=%s tokens≈%d",
                    assembler_result.identity_source,
                    assembler_result.voice_injected,
                    assembler_result.prompt_tokens,
                )
                return await director.generate(
                    prompt=user_input,
                    system=assembler_result.system_prompt,
                    max_tokens=2000,
                )
            return "I'm not able to respond right now."
        else:
            result = await self.run_agent(user_input)
            return result.response

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def stop(self) -> None:
        """Stop the engine gracefully."""
        logger.info("Luna Engine stopping...")
        self._running = False
        self._shutdown_event.set()

    async def _shutdown(self) -> None:
        """Shutdown sequence."""
        logger.info("Shutdown sequence starting...")

        # Stop ForgeWatcher before closing substrate connections
        if self.forge_watcher is not None:
            try:
                self.forge_watcher.stop()
            except Exception as e:
                logger.debug(f"ForgeWatcher shutdown: {e}")
        self.state = EngineState.STOPPED

        # Run session-end reflection and maintenance via Director
        director = self.get_actor("director")
        if director:
            try:
                # Run session-end reflection to capture personality evolution
                if hasattr(director, 'session_end_reflection'):
                    logger.info("Running session-end reflection...")
                    result = await director.session_end_reflection()
                    if result:
                        logger.info(f"Reflection result: {result}")

                # Run personality maintenance if due
                if hasattr(director, '_lifecycle_manager') and director._lifecycle_manager:
                    if await director._lifecycle_manager.should_run_maintenance():
                        logger.info("Running personality maintenance...")
                        maint_result = await director._lifecycle_manager.run_maintenance()
                        logger.info(f"Maintenance result: {maint_result}")

            except Exception as e:
                logger.warning(f"Shutdown personality tasks failed: {e}")

        # Close Eden adapter if active
        if self._eden_adapter:
            try:
                await self._eden_adapter.__aexit__(None, None, None)
                logger.info("Eden adapter closed")
            except Exception as e:
                logger.warning(f"Eden shutdown error: {e}")
            self._eden_adapter = None

        # Stop voice system if active
        if self._voice:
            try:
                await self._voice.stop()
                logger.info("Voice system stopped")
            except Exception as e:
                logger.warning(f"Voice shutdown error: {e}")

        # Stop all actors
        for actor in self.actors.values():
            await actor.stop()

        # Save consciousness state
        await self.consciousness.save()

        # WAL checkpoint handled by MatrixActor.stop() → MemoryDatabase.close()

        logger.info("Luna Engine stopped")

    # =========================================================================
    # Status
    # =========================================================================

    def status(self) -> dict:
        """Get current engine status."""
        return {
            "state": self.state.name,
            "uptime_seconds": self.metrics.uptime_seconds,
            "cognitive_ticks": self.metrics.cognitive_ticks,
            "events_processed": self.metrics.events_processed,
            "messages_generated": self.metrics.messages_generated,
            "actors": list(self.actors.keys()),
            "buffer": self.input_buffer.stats,
            "consciousness": self.consciousness.get_summary(),
            "context": self.context.stats(),
            "current_turn": self.context.current_turn,
            # Agentic stats (Phase XIV)
            "agentic": {
                "is_processing": self._is_processing,
                "current_goal": self._current_goal[:50] + "..." if self._current_goal and len(self._current_goal) > 50 else self._current_goal,
                "pending_messages": len(self._pending_messages),
                "tasks_started": self.metrics.agentic_tasks_started,
                "tasks_completed": self.metrics.agentic_tasks_completed,
                "tasks_aborted": self.metrics.agentic_tasks_aborted,
                "direct_responses": self.metrics.direct_responses,
                "planned_responses": self.metrics.planned_responses,
                "agent_loop_status": self.agent_loop.status.name if self.agent_loop else "NOT_INITIALIZED",
                "subtask_runner": self._subtask_runner.get_stats() if self._subtask_runner else None,
            },
            # Project scoping
            "active_project": self._active_project,
            "active_scope": self.active_scope,
            # FaceID
            "identity": {
                "enabled": self.config.faceid_enabled,
                "initialized": self.identity.is_ready if self.identity else False,
                "current": {
                    "is_present": self.identity.current.is_present,
                    "entity_name": self.identity.current.entity_name,
                    "luna_tier": self.identity.current.luna_tier,
                    "confidence": self.identity.current.confidence,
                } if self.identity and self.identity.current.is_present else None,
            } if self.config.faceid_enabled else None,
            # Voice system
            "voice": {
                "enabled": self.config.voice_enabled,
                "initialized": self._voice is not None,
                "active": self._voice.is_active if self._voice else False,
            } if self.config.voice_enabled else None,
            # Phase 1 — Engine Ownership
            "aibrarian": "connected" if getattr(self, "aibrarian", None) is not None else "not_initialized",
            "collection_lock_in": "connected" if getattr(self, "collection_lock_in", None) is not None else "not_initialized",
            "annotations": "connected" if getattr(self, "annotations", None) is not None else "not_initialized",
            "aperture": getattr(self, "aperture", None).state.preset.value if getattr(self, "aperture", None) else "not_initialized",
        }
