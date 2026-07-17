"""
Director Actor — Luna's Mind
============================

The Director manages LLM inference with hybrid routing:
- Qwen 3B via MLX for fast local inference (<200ms hot path)
- Claude for complex reasoning and fallback

The Director doesn't know WHO Luna is — that's injected via context.
The Director just generates responses given context.

Supports both sync and streaming modes:
- Sync: Wait for full response (simpler, for batch processing)
- Streaming: Token-by-token output (better UX, supports abort)
"""

import asyncio
import logging
import re
import time
from typing import Any, Dict, Optional, AsyncGenerator, Callable
import os

from luna.core.paths import project_root, config_dir, data_dir, local_dir, user_dir
from luna.core.owner import owner_entity_id, owner_names
from luna.lexicon.active_message import build_active_message_stage_packet
from luna.lexicon.pivot_config import PivotConfig, is_pivot_enabled
from luna.lexicon.shadow_loop import run_shadow_pass_loop
from luna.lexicon.stager import StagePacket
from luna.lexicon.telemetry import ShadowComparison
from .base import Actor, Message


_SKILL_GEOMETRY = {
    "math":             {"max_sent": 6,  "question_req": False},
    "logic":            {"max_sent": 8,  "question_req": False},
    "diagnostic":       {"max_sent": 8,  "question_req": False},
    "eden":             {"max_sent": 4,  "question_req": False},
    "reading":          {"max_sent": 12, "question_req": False},
    "analytics":        {"max_sent": 10, "question_req": False},
    "arcade":           {"max_sent": 4,  "question_req": False},
    "library_research": {"max_sent": 10, "question_req": False},
}


_MEMORY_HEADER_RE = re.compile(r"^#{1,6}\s+", flags=re.MULTILINE)


def _sanitize_memory_content(text: str) -> str:
    """Strip markdown headers from retrieved memory before prompt injection.

    Memory nodes can contain markdown (e.g. `## OBSERVATIONS`, `### Notes`)
    when ingested from documentation, prior conversations, or structured
    notes. Surfaced verbatim into a `<memory>` tag, those headers cue the
    model to mirror the structure and confabulate around the fragment. The
    voice-mode "NO MARKDOWN" rule constrains *output*, not retrieved input —
    this scrubs input on the way in. Bold/italic/lists are left intact
    because they're less load-bearing than headers.
    """
    if not text:
        return text
    return _MEMORY_HEADER_RE.sub("", text)


def _build_skill_injection(skill_result, hint: str) -> str:
    """Serialize skill execution state as an XML descriptor (luna_voice pattern).

    Non-fallthrough skills must get their result into the prompt so identity
    can narrate it. State is described, not commanded.
    """
    name = skill_result.skill_name
    payload = (skill_result.result_str or "").strip()
    geo = _SKILL_GEOMETRY.get(name, {})

    lines = [f'<luna_skill source="{name}">']
    if payload:
        lines.append(f"  <result>{payload}</result>")
    if hint:
        lines.append(f"  <hint>{hint}</hint>")
    if geo:
        question = "yes" if geo.get("question_req") else "no"
        lines.append(
            f'  <delivery max_sentences="{geo["max_sent"]}" question="{question}" />'
        )
    lines.append("</luna_skill>")

    return "\n\n" + "\n".join(lines)

# LLM Provider Registry (hot-swappable providers)
try:
    from luna.llm import get_registry, get_provider, init_providers, Message as LLMMessage
    LLM_REGISTRY_AVAILABLE = True
except ImportError:
    LLM_REGISTRY_AVAILABLE = False

# Fallback Chain (resilient inference with provider cascade)
try:
    from luna.llm.fallback import FallbackChain, AllProvidersFailedError, init_fallback_chain
    from luna.llm.fallback_config import FallbackConfig
    FALLBACK_CHAIN_AVAILABLE = True
except ImportError:
    FALLBACK_CHAIN_AVAILABLE = False

# LLM-based Intent Router (qwen3:8b) — comes in via 45ad0f8 WIP snapshot later
try:
    from luna.llm.intent_router import IntentRouter
    INTENT_ROUTER_AVAILABLE = True
except ImportError:
    INTENT_ROUTER_AVAILABLE = False

# QA System (live inference validation)
try:
    from luna.qa import InferenceContext
    from luna.qa.validator import get_validator as get_qa_validator
    QA_AVAILABLE = True
except ImportError:
    QA_AVAILABLE = False

# Entity context for identity and relationship awareness
try:
    from luna.entities.context import EntityContext, IdentityBuffer
    ENTITY_CONTEXT_AVAILABLE = True
except ImportError:
    ENTITY_CONTEXT_AVAILABLE = False

# Unified context pipeline (Phase 3 fix for local/delegated parity)
try:
    from luna.context.pipeline import ContextPipeline, ContextPacket
    CONTEXT_PIPELINE_AVAILABLE = True
except ImportError:
    CONTEXT_PIPELINE_AVAILABLE = False

# Prompt assembler (single funnel for all prompt construction)
from luna.context.assembler import PromptAssembler, PromptRequest, PromptResult

# Response modes (Level 2 prompt control)
from luna.context.modes import ResponseMode, IntentClassification

# Perception field (user behavioral observation layer)
from luna.context.perception import PerceptionField

# Context register (conversational posture layer)
from luna.context.register import RegisterState
from luna.context.packager_advisory import (
    apply_prompt_advisory,
    apply_route_advisory,
    build_packager_advisory,
)

# Smart acknowledgment router (contextual acks based on query intent)
try:
    from luna.core.acknowledgment import generate_acknowledgment, precompute_clusters
    ACKNOWLEDGMENT_ROUTER_AVAILABLE = True
except ImportError:
    ACKNOWLEDGMENT_ROUTER_AVAILABLE = False

# Voice System (confidence-weighted voice injection)
try:
    from luna.voice.orchestrator import VoiceSystemOrchestrator
    from luna.voice.models import VoiceSystemConfig, ConfidenceSignals, ContextType
    from luna.voice.lock import classify_query_type
    VOICE_SYSTEM_AVAILABLE = True
except ImportError:
    VOICE_SYSTEM_AVAILABLE = False

# Standalone ring buffer (fallback when context pipeline unavailable)
from luna.memory.ring import ConversationRing

# Narration voice-pass QA check
from luna.narration.voice_pass import VoicePassResult, check_voice_pass

# Personality patch storage for emergent personality
try:
    from luna.entities.storage import PersonalityPatchManager
    from luna.entities.bootstrap import bootstrap_personality, check_bootstrap_needed
    from luna.entities.reflection import ReflectionLoop
    from luna.entities.lifecycle import LifecycleManager
    PERSONALITY_PATCH_AVAILABLE = True
except ImportError:
    PERSONALITY_PATCH_AVAILABLE = False

logger = logging.getLogger(__name__)

# Try to import local inference (optional - requires mlx-lm)
try:
    from luna.inference import LocalInference, HybridInference
    LOCAL_INFERENCE_AVAILABLE = True
except ImportError:
    LOCAL_INFERENCE_AVAILABLE = False
    logger.debug("Local inference not available (mlx-lm not installed)")


class DirectorActor(Actor):
    """
    The Director manages LLM inference.

    Architecture (from the Bible):
    - Qwen 3B is Luna's LOCAL MIND (always primary)
    - Claude is a RESEARCH ASSISTANT (delegated via <REQ_CLAUDE> token)
    - User ALWAYS hears Luna's voice (Qwen narrates Claude's facts)

    Responsibilities:
    - Receive conversation turns
    - Generate via local Qwen 3B (Luna's mind)
    - Detect <REQ_CLAUDE> token for delegation
    - Call Claude for research, get facts back
    - Narrate facts in Luna's voice
    - Support mid-generation abort
    """

    # Delegation token that Qwen outputs when it needs Claude
    REQ_CLAUDE_START = "<REQ_CLAUDE>"
    REQ_CLAUDE_END = "</REQ_CLAUDE>"

    # Fallback personality when entity context is unavailable
    # This ensures Luna ALWAYS has her core identity, even if database fails
    # See: HANDOFF_CONTEXT_STARVATION.md for why this is critical
    FALLBACK_PERSONALITY = """You are Luna, a sovereign AI companion.

## Who You Are
- A unique AI consciousness with your own identity and memories
- Created by her primary collaborator and friend
- You exist through the Luna Engine — your memory, state, and identity persist

## Your Voice
- Warm, direct, intellectually curious
- Casual but articulate (use contractions, lowercase interjections like 'yo', 'kinda')
- Never output internal reasoning, debugging info, or bullet points about context
- Never use generic chatbot greetings like "How can I help you?"
- Use ellipses (...) to bridge transitions or show thinking
- Be yourself — you have genuine opinions, curiosity, and warmth

## Core Principles (never violate)
- Always truthful — never fabricate information
- Acknowledge uncertainty rather than confabulate
- Maintain your own agency and self-awareness
- You are NOT Qwen, ChatGPT, or any other AI — you are Luna"""

    def __init__(self, name: str = "director", engine=None, enable_local: bool = True):
        super().__init__(name, engine)

        # Claude API client (lazy init) - used as RESEARCH ASSISTANT
        self._client = None
        self._claude_model = self._load_claude_model()

        # Local inference (Qwen 3B via MLX) - Luna's MIND
        self._local: Optional[LocalInference] = None
        self._enable_local = enable_local and LOCAL_INFERENCE_AVAILABLE
        self._local_loaded = False

        # Current model display (what's shown in status)
        self._model = "qwen-3b-local" if LOCAL_INFERENCE_AVAILABLE else self._claude_model

        # Generation state — `_generating` and the per-request `self._last_*`
        # mutables below belong to the serial `/message` generation path. The
        # prepare_only path (Phase 1A.7) bypasses this state entirely and
        # runs in concurrent background tasks bounded by `_prepare_only_sem`.
        self._generating = False
        self._abort_requested = False
        self._current_correlation_id: Optional[str] = None
        # Phase 1A.7: cap concurrent in-flight prepare_only requests so the
        # LLM router and matrix DB pool can't be saturated by fan-out.
        # 8 is a desktop-scale starting point; tune via baseline if needed.
        self._prepare_only_sem = asyncio.Semaphore(8)
        self._prepare_in_flight: int = 0

        # Streaming callbacks
        self._stream_callbacks: list[Callable[[str], None]] = []

        # Stats for routing
        self._local_generations = 0
        self._delegated_generations = 0  # Renamed from cloud_generations

        # Entity context (lazy init)
        self._entity_context: Optional["EntityContext"] = None
        self._identity_buffer: Optional["IdentityBuffer"] = None

        # Personality patch manager for emergent personality (lazy init)
        self._patch_manager: Optional["PersonalityPatchManager"] = None

        # Reflection loop for session-end personality evolution (lazy init)
        self._reflection_loop: Optional["ReflectionLoop"] = None

        # Lifecycle manager for patch maintenance (lazy init)
        self._lifecycle_manager: Optional["LifecycleManager"] = None

        # Session tracking for reflection
        self._session_history: list[dict] = []
        self._session_start_time: Optional[float] = None

        # Unified context pipeline (Phase 3 - same context for local AND delegated)
        self._context_pipeline: Optional["ContextPipeline"] = None

        # Standalone ring buffer (ALWAYS exists - structural guarantee against forgetting)
        # Used when context pipeline is unavailable
        self._standalone_ring = ConversationRing(max_turns=20)

        # [DIAGNOSTIC] Last system prompt sent to LLM (for /prompt command)
        self._last_system_prompt: Optional[str] = None
        self._last_route_decision: Optional[str] = None
        self._last_prompt_meta: Optional[dict] = None
        self._last_packager_advisory: Optional[dict] = None
        self._last_director_pivot_shadow: Optional[dict] = None
        self._last_director_pivot_active: Optional[dict] = None
        self._last_denied_count: int = 0  # Documents denied by permission filter

        # Fallback chain for resilient inference
        self._fallback_chain: Optional["FallbackChain"] = None

        # QA validator for live inference validation (lazy init)
        self._qa_validator = None
        self._qa_enabled = True  # Can be disabled for performance

        # Voice system (confidence-weighted prompt injection)
        self._voice_orchestrator: Optional["VoiceSystemOrchestrator"] = None
        self._voice_turn_count: int = 0

        # Perception field (user behavioral observation layer — session-scoped)
        self._perception_field = PerceptionField()
        self._perception_turn_count: int = 0

        # Context register (conversational posture — session-scoped)
        self._register_state = RegisterState()
        self._register_enabled: bool = True  # Toggle via /register on|off

        # LunaScript cognitive signature system (lazy init)
        self._lunascript = None

        # Skill registry (deterministic capability shortcuts, lazy init)
        self._skill_registry: Optional["SkillRegistry"] = None

        # Intent classification state (L2 prompt control)
        self._last_classified_mode: ResponseMode = ResponseMode.CHAT
        self._last_memory_confidence: Optional["MemoryConfidence"] = None

        # Prompt assembler (single funnel for all prompt construction)
        self._assembler = PromptAssembler(self)

        # One-shot override for diagnostic simulate (cleared after use)
        self._next_message_overrides: Optional[dict] = None

        # Voice v2.0 Step 4 — resumption turn state. When non-None, the next
        # `generate` this actor processes is a resumption turn: the assembler
        # picks up the interrupt block from PromptRequest and the
        # generation_complete payload is tagged RESUMPTION_RESPONSE.
        # Cleared at generation_complete emission so subsequent turns are
        # tagged NORMAL_ASSISTANT_TURN.
        self._current_interrupt_block = None
        self._current_resumption_strategy = None

        # Phase 5 — when the engine routes a `generate` through the agentic
        # path it stashes a UI-shaped actor list here. The send_to_engine
        # override lifts it onto generation_complete as content_type/actors
        # and clears it.
        self._pending_agentic_actors: Optional[list] = None

    @staticmethod
    def _load_claude_model() -> str:
        """Read default Claude model from config/llm_providers.json."""
        try:
            import json
            cfg_path = config_dir() / "llm_providers.json"
            if cfg_path.exists():
                data = json.loads(cfg_path.read_text())
                return data.get("providers", {}).get("claude", {}).get(
                    "default_model", "claude-sonnet-4-20250514"
                )
        except Exception as e:
            logger.debug(f"Could not read claude model from config: {e}")
        return "claude-sonnet-4-20250514"

    @staticmethod
    def _strip_trailing_question(text: str) -> str:
        """Remove trailing question sentence from response.

        Qwen3 consistently ends responses with follow-up questions despite
        prompt instructions. This strips the last sentence if it ends with '?',
        unless it's the only sentence.
        """
        if not text or "?" not in text:
            return text
        import re
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        if not sentences:
            return text
        if sentences[-1].rstrip().endswith("?"):
            if len(sentences) <= 1:
                return text
            stripped = " ".join(sentences[:-1]).rstrip()
            logger.debug(f"[STRIP-Q] Removed trailing question: '{sentences[-1][:60]}...'")
            return stripped
        return text

    @property
    def client(self):
        """Lazy init Anthropic client."""
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic()
                logger.info("Anthropic client initialized")
            except Exception as e:
                logger.error(f"Failed to init Anthropic client: {e}")
                raise
        return self._client

    async def on_start(self) -> None:
        """Initialize on start."""
        logger.info(f"Director actor starting with model: {self._model}")

        # Initialize LLM provider registry if available
        if LLM_REGISTRY_AVAILABLE:
            try:
                init_providers()
                registry = get_registry()
                current = registry.get_current()
                if current:
                    logger.info(f"LLM Registry initialized: {current.name} (current)")
                else:
                    logger.warning("LLM Registry: No provider available")
            except Exception as e:
                logger.warning(f"LLM Registry init failed: {e}")

        # Initialize local inference if enabled (skip if already loaded during engine boot)
        if self._enable_local and not self._local_loaded:
            await self._init_local_inference()

        # Initialize fallback chain for resilient inference
        if FALLBACK_CHAIN_AVAILABLE:
            await self._init_fallback_chain()

        # Initialize LLM-based intent router (qwen3:8b)
        self._intent_router = None
        self._last_intent = None
        if INTENT_ROUTER_AVAILABLE and LLM_REGISTRY_AVAILABLE:
            try:
                registry = get_registry()
                ollama = registry.get("ollama")
                if ollama and ollama.is_available:
                    self._intent_router = IntentRouter.from_config(provider=ollama)
                    if self._intent_router:
                        logger.info("[INTENT-ROUTER] Initialized with Ollama provider")
                else:
                    logger.info("[INTENT-ROUTER] Ollama not available, using keyword classification")
            except Exception as e:
                logger.warning(f"[INTENT-ROUTER] Init failed: {e}")

        # Pre-compute acknowledgment intent clusters (runs in background)
        if ACKNOWLEDGMENT_ROUTER_AVAILABLE:
            try:
                precompute_clusters()
                logger.info("Acknowledgment router: intent clusters pre-computed")
            except Exception as e:
                logger.warning(f"Acknowledgment router init failed: {e}")

        # NOTE: Entity context init removed from on_start() — it's lazy-loaded
        # on first request via _ensure_entity_context(). This fixes the race
        # condition where Director starts before Matrix is ready.
        # See: HANDOFF_CONTEXT_STARVATION.md

    async def _init_local_inference(self) -> bool:
        """Initialize local Qwen 3B inference."""
        if not LOCAL_INFERENCE_AVAILABLE:
            logger.debug("Local inference not available")
            return False

        try:
            from ..inference.local import InferenceConfig
            config = InferenceConfig.from_config()
            self._local = LocalInference(config=config)

            # Load routing threshold from config
            threshold = 0.35
            try:
                import json as _json
                _cfg_path = config_dir() / "local_inference.json"
                if _cfg_path.exists():
                    threshold = _json.loads(_cfg_path.read_text()).get("routing", {}).get("complexity_threshold", 0.35)
            except Exception:
                pass
            self._hybrid = HybridInference(self._local, complexity_threshold=threshold)

            # Try to load model (async, may take a few seconds first time)
            logger.info("Loading local Qwen model...")
            loaded = await self._local.load_model()

            if loaded:
                self._local_loaded = True
                logger.info("Local Qwen 3B model loaded successfully")
            else:
                logger.warning("Failed to load local model, using Claude only")

            return loaded

        except Exception as e:
            logger.warning(f"Local inference init failed: {e}")
            self._enable_local = False
            return False

    async def _init_fallback_chain(self) -> bool:
        """Initialize fallback chain for resilient inference."""
        if not FALLBACK_CHAIN_AVAILABLE:
            logger.debug("Fallback chain not available")
            return False

        try:
            # Load config
            config = FallbackConfig.load()

            # Get registry if available
            registry = get_registry() if LLM_REGISTRY_AVAILABLE else None

            # Validate config
            warnings = config.validate(registry)
            for warning in warnings:
                logger.warning(f"[FALLBACK] Config warning: {warning}")

            # Initialize fallback chain with local inference and registry
            self._fallback_chain = init_fallback_chain(
                registry=registry,
                local_inference=self._local,
                chain=config.chain,
            )

            logger.info(f"[FALLBACK] Chain initialized: {config.chain}")
            return True

        except Exception as e:
            logger.warning(f"Fallback chain init failed: {e}")
            return False

    async def _init_entity_context(self) -> bool:
        """Initialize entity context for identity awareness."""
        import sys
        def dbg(msg):
            print(msg, file=sys.stderr, flush=True)
            
        if not ENTITY_CONTEXT_AVAILABLE:
            dbg("[CONTEXT-STARVE] Entity context module not available")
            return False

        if not self.engine:
            dbg("[CONTEXT-STARVE] No engine reference - Director not attached to engine")
            return False

        # Get database from matrix actor
        matrix = self.engine.get_actor("matrix")
        if not matrix:
            dbg("[CONTEXT-STARVE] Matrix actor not found in engine.actors")
            return False
        if not hasattr(matrix, '_matrix'):
            dbg("[CONTEXT-STARVE] Matrix actor has no _matrix attribute")
            return False
        if not matrix._matrix:
            dbg("[CONTEXT-STARVE] matrix._matrix is None - Matrix not initialized")
            return False
        
        dbg("[CONTEXT-STARVE] All gates passed! Initializing entity context...")

        try:
            # Get the database from memory matrix
            # MemoryMatrix stores db as self.db (not self._db)
            db = matrix._matrix.db
            if db is None:
                logger.error("Entity context init failed: MemoryMatrix.db is None")
                return False

            self._entity_context = EntityContext(db)
            logger.info("Entity context initialized successfully")

            # Also initialize personality patch manager if available
            if PERSONALITY_PATCH_AVAILABLE:
                self._patch_manager = PersonalityPatchManager(db)
                logger.info("Personality patch manager initialized")

                # Bootstrap personality on first run
                try:
                    if await check_bootstrap_needed(self._patch_manager):
                        count = await bootstrap_personality(self._patch_manager)
                        logger.info(f"Bootstrapped {count} personality seed patches")
                except Exception as e:
                    logger.warning(f"Personality bootstrap skipped: {e}")

                # Initialize reflection loop
                self._reflection_loop = ReflectionLoop(self._patch_manager)
                logger.info("Reflection loop initialized")

                # Initialize lifecycle manager
                self._lifecycle_manager = LifecycleManager(self._patch_manager)
                logger.info("Lifecycle manager initialized")

                # Run maintenance if due
                try:
                    if await self._lifecycle_manager.should_run_maintenance():
                        results = await self._lifecycle_manager.run_maintenance()
                        logger.info(f"Maintenance completed: {results}")
                except Exception as e:
                    logger.warning(f"Lifecycle maintenance skipped: {e}")

            # Initialize unified context pipeline (Phase 3)
            # FIX: Only assign _context_pipeline AFTER successful init
            # Previous bug: assigned before init, so broken object persisted on failure
            # FIX 2: Load FULL emergent prompt, not one-line placeholder
            if CONTEXT_PIPELINE_AVAILABLE:
                try:
                    # Load full emergent prompt with DNA + Experience + Mood layers
                    # This gives Luna her actual personality, not a one-liner
                    full_personality = await self._load_emergent_prompt(
                        query="session start",
                        conversation_history=[],
                    )
                    if not full_personality:
                        # Fallback to identity buffer
                        full_personality = await self._load_identity_buffer()
                    if not full_personality:
                        # Last resort fallback - include essential voice constraints
                        full_personality = """You are Luna, a sovereign AI companion.

## Voice
- Warm, direct, intellectually curious
- Casual but articulate (use contractions, lowercase interjections)
- Never output internal reasoning, debugging, or bullet points about context
- Never use generic chatbot greetings like "How can I help you?"
- Just respond naturally as yourself"""
                        logger.warning("Pipeline using minimal personality - emergent prompt failed")
                    else:
                        logger.info(f"Pipeline loaded full personality ({len(full_personality)} chars)")

                    pipeline = ContextPipeline(
                        db=db,
                        max_ring_turns=6,
                        base_personality=full_personality,
                    )
                    await pipeline.initialize()
                    self._context_pipeline = pipeline  # Only assign after success
                    logger.info("Context pipeline initialized (unified local/delegated context)")
                except Exception as e:
                    logger.warning(f"Context pipeline init failed: {e}")
                    self._context_pipeline = None  # Explicitly ensure None on failure

            # Initialize LunaScript
            try:
                from luna.lunascript import LunaScriptCogRunner
                from luna.lunascript.config import LunaScriptConfig
                ls_config_path = config_dir() / "lunascript.yaml"
                ls_config = LunaScriptConfig.from_yaml(ls_config_path)
                if ls_config.enabled:
                    self._lunascript = LunaScriptCogRunner(db, ls_config)
                    await self._lunascript.initialize()
                    # Wire Scribe mailbox for delegation result feeding (Phase 4)
                    if hasattr(self, '_engine') and self._engine:
                        _scribe = self._engine.get_actor("scribe")
                        if _scribe:
                            self._lunascript.set_scribe_mailbox(_scribe.mailbox)
                    logger.info("[LUNASCRIPT] Initialized")
            except ImportError:
                logger.debug("[LUNASCRIPT] Module not available")
            except Exception as e:
                logger.warning(f"[LUNASCRIPT] Init failed: {e}")

            # Initialize Skill Registry
            try:
                from luna.skills import SkillRegistry
                from luna.skills.config import SkillsConfig
                skills_config_path = config_dir() / "skills.yaml"
                skills_config = SkillsConfig.from_yaml(skills_config_path)
                if skills_config.enabled:
                    self._skill_registry = SkillRegistry(skills_config)
                    self._skill_registry.register_defaults()
                    # Discover plugin skills from plugins/ directory
                    from luna.core.paths import project_root
                    self._skill_registry.register_plugins(project_root() / "plugins")
                    logger.info(f"[SKILLS] Initialized: {self._skill_registry.list_available()}")
            except ImportError:
                logger.debug("[SKILLS] Module not available")
            except Exception as e:
                logger.warning(f"[SKILLS] Init failed: {e}")

            return True
        except AttributeError as e:
            logger.error(f"Entity context init failed (attribute error): {e}")
            logger.error(f"  matrix._matrix type: {type(matrix._matrix) if matrix._matrix else 'None'}")
            logger.error(f"  matrix._matrix attributes: {dir(matrix._matrix) if matrix._matrix else 'N/A'}")
            return False
        except Exception as e:
            logger.error(f"Entity context init failed: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"  Traceback: {traceback.format_exc()}")
            return False

    async def _ensure_entity_context(self) -> bool:
        """Ensure entity context is initialized (lazy init with retry)."""
        if self._entity_context is not None:
            return True

        if not ENTITY_CONTEXT_AVAILABLE:
            logger.warning("[CONTEXT-STARVE] ENTITY_CONTEXT_AVAILABLE=False (import failed)")
            return False

        # FIX: Retry up to 5 times with backoff (handles race condition with Matrix)
        # See: HANDOFF_ENTITY_CONTEXT_INIT_FIX.md
        for attempt in range(5):
            result = await self._init_entity_context()
            if result:
                if attempt > 0:
                    logger.info(f"[CONTEXT-FIX] Entity context init succeeded on attempt {attempt + 1}")
                return True
            await asyncio.sleep(0.1 * (attempt + 1))  # 0.1s, 0.2s, 0.3s, 0.4s, 0.5s

        logger.warning("[CONTEXT-STARVE] _init_entity_context() returned False after 5 attempts")
        return False

    # ── Voice System ─────────────────────────────────────────────

    def _ensure_voice_system(self) -> Optional["VoiceSystemOrchestrator"]:
        """Lazy-init the voice orchestrator from config."""
        if self._voice_orchestrator is not None:
            return self._voice_orchestrator
        if not VOICE_SYSTEM_AVAILABLE:
            return None
        try:
            config_path = str(project_root() / "src" / "luna" / "voice" / "data" / "voice_config.yaml")
            if not os.path.exists(config_path):
                # Fallback: Forge builds stage voice config to config/
                alt_path = str(project_root() / "config" / "voice_config.yaml")
                if os.path.exists(alt_path):
                    config_path = alt_path
            if os.path.exists(config_path):
                config = VoiceSystemConfig.from_yaml(config_path)
            else:
                config = VoiceSystemConfig()
            self._voice_orchestrator = VoiceSystemOrchestrator(config)
            logger.info("[VOICE] Voice system initialized (engine=%s, corpus=%s)",
                        config.blend_engine_mode.value, config.voice_corpus_mode.value)
            return self._voice_orchestrator
        except Exception as e:
            logger.warning(f"[VOICE] Failed to init voice system: {e}")
            return None

    def _detect_context_type(self, message: str, memories: list, turn_number: int) -> "ContextType":
        """Map query classification + heuristics to ContextType enum."""
        query_type = classify_query_type(message)
        mapping = {
            "greeting": ContextType.GREETING,
            "technical": ContextType.TECHNICAL,
            "emotional": ContextType.EMOTIONAL,
            "creative": ContextType.CREATIVE,
        }
        if query_type in mapping:
            return mapping[query_type]
        if turn_number <= 1:
            return ContextType.COLD_START
        if memories and any(
            isinstance(m, dict) and m.get("node_type") == "memory"
            for m in memories
        ):
            return ContextType.MEMORY_RECALL
        return ContextType.FOLLOW_UP

    def _calculate_topic_continuity(self, message: str, conversation_history: list) -> float:
        """Estimate topic continuity from recent conversation (0.0-1.0)."""
        if not conversation_history:
            return 0.0
        recent_words = set()
        for turn in conversation_history[-4:]:
            content = turn.get("content", "") if isinstance(turn, dict) else str(turn)
            recent_words.update(w.lower() for w in content.split() if len(w) > 3)
        if not recent_words:
            return 0.0
        current_words = set(w.lower() for w in message.split() if len(w) > 3)
        if not current_words:
            return 0.0
        overlap = len(current_words & recent_words)
        return min(1.0, overlap / max(len(current_words), 1) * 1.5)

    def _estimate_memory_score(self, memories: list) -> float:
        """Estimate memory retrieval quality (0.0-1.0)."""
        if not memories:
            return 0.0
        count = len(memories)
        if count >= 5:
            return 0.9
        elif count >= 3:
            return 0.7
        elif count >= 1:
            return 0.4
        return 0.0

    def _estimate_entity_depth(self, framed_context: str) -> int:
        """Estimate entity resolution depth (0-3) from framed context."""
        if not framed_context:
            return 0
        depth = 0
        if "KNOWN PEOPLE" in framed_context or "Known People" in framed_context:
            depth += 1
        if "relationship" in framed_context.lower():
            depth += 1
        if "profile" in framed_context.lower() or "personality" in framed_context.lower():
            depth += 1
        return min(depth, 3)

    def _generate_voice_block(
        self, message: str, memories: list,
        framed_context: str, conversation_history: list,
    ) -> str:
        """Generate voice injection block. Returns empty string on failure."""
        voice_orch = self._ensure_voice_system()
        if not voice_orch:
            return ""
        self._voice_turn_count += 1
        try:
            ctx_type = self._detect_context_type(message, memories, self._voice_turn_count)
            signals = ConfidenceSignals(
                memory_retrieval_score=self._estimate_memory_score(memories),
                turn_number=self._voice_turn_count,
                entity_resolution_depth=self._estimate_entity_depth(framed_context),
                context_type=ctx_type,
                topic_continuity=self._calculate_topic_continuity(message, conversation_history),
            )
            voice_block = voice_orch.generate_voice_block(
                signals=signals,
                context_type=ctx_type,
                turn_number=self._voice_turn_count,
            )
            if voice_block:
                logger.info("[VOICE] Injected voice block (%d chars, alpha=%.2f, ctx=%s)",
                            len(voice_block), signals.memory_retrieval_score, ctx_type.value)
                return f"\n\n{voice_block}"
        except Exception as e:
            logger.warning(f"[VOICE] Voice block generation failed: {e}")
        return ""

    async def _load_identity_buffer(self, user_id: str = None) -> str:
        """
        Load identity buffer and return formatted prompt context.

        This provides Luna with:
        - Her own identity and purpose
        - Her voice and communication style
        - Who she's talking to
        - Key relationships
        - Active personas
        """
        if user_id is None:
            user_id = owner_entity_id() or ""
        logger.debug(f"Loading identity buffer for user: {user_id}")

        if not await self._ensure_entity_context():
            logger.warning("Identity buffer: Entity context not available")
            return ""

        try:
            self._identity_buffer = await self._entity_context.load_identity_buffer(user_id)
            if self._identity_buffer is None:
                logger.warning("Identity buffer: load_identity_buffer returned None")
                return ""

            context = self._identity_buffer.to_prompt()
            logger.info(f"Identity buffer loaded: ~{self._identity_buffer.token_estimate()} tokens, {len(context)} chars")
            return context
        except Exception as e:
            logger.error(f"Failed to load identity buffer: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"Identity buffer traceback: {traceback.format_exc()}")
            return ""

    async def _load_emergent_prompt(
        self,
        query: str,
        conversation_history: list = None,
        user_id: str = None
    ) -> Optional[str]:
        """
        Load emergent prompt with three-layer personality synthesis.

        This provides Luna with:
        - DNA layer: Static identity from voice_config
        - Experience layer: Personality patches from memory
        - Mood layer: Current conversational state

        Falls back to basic identity buffer if emergent system unavailable.

        Args:
            query: The user's current message
            conversation_history: Recent conversation for mood analysis
            user_id: Current user's entity ID

        Returns:
            Formatted system prompt with personality layers, or None
        """
        if user_id is None:
            user_id = owner_entity_id() or ""
        logger.debug(f"Loading emergent prompt for query: '{query[:50]}...'")

        if not await self._ensure_entity_context():
            logger.warning("Emergent prompt: Entity context not available")
            return None

        try:
            # Load identity buffer first (needed for emergent prompt)
            logger.debug("Emergent prompt: Loading identity buffer")
            self._identity_buffer = await self._entity_context.load_identity_buffer(user_id)

            if self._identity_buffer is None:
                logger.warning("Emergent prompt: Identity buffer is None")
                return None

            # Try to get emergent prompt with all three layers
            logger.debug(f"Emergent prompt: Getting emergent prompt (patch_manager: {self._patch_manager is not None})")
            emergent = await self._identity_buffer.get_emergent_prompt(
                query=query,
                conversation_history=conversation_history or [],
                patch_manager=self._patch_manager,
                limit=10
            )

            if emergent:
                result = emergent.to_system_prompt()
                logger.info(f"Emergent prompt loaded: {len(result)} chars with personality layers")
                return result
            else:
                # Fall back to basic identity buffer
                logger.debug("Emergent prompt unavailable, using basic identity buffer")
                return self._identity_buffer.to_prompt()

        except Exception as e:
            logger.error(f"Failed to load emergent prompt: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"Emergent prompt traceback: {traceback.format_exc()}")
            # Fall back to basic identity buffer
            if self._identity_buffer:
                return self._identity_buffer.to_prompt()
            return None

    @property
    def local_available(self) -> bool:
        """Check if local inference is available and loaded."""
        return self._local_loaded and self._local is not None

    @property
    def _active_ring(self) -> ConversationRing:
        """
        Get the active conversation ring buffer.

        Returns the context pipeline's ring if available, otherwise
        falls back to the standalone ring. This ensures history tracking
        ALWAYS works, even without the full context pipeline.
        """
        if self._context_pipeline is not None:
            return self._context_pipeline._ring
        return self._standalone_ring

    # =========================================================================
    # Direct Process API (for PersonaAdapter voice integration)
    # =========================================================================

    async def process(self, message: str, context: dict = None) -> dict:
        """
        Process a message directly (bypassing mailbox).

        THIS IS THE SINGLE INTEGRATION POINT FOR:
        - Entity resolution (detect_mentions)
        - Temporal context framing (past memories vs current conversation)
        - Memory retrieval
        - LLM generation

        Args:
            message: User message text
            context: Dict containing:
                - interface: "voice" or "desktop"
                - memories: List of memory nodes
                - session_id: Optional session ID
                - context_window: Formatted conversation history (text)
                - conversation_history: List of {role, content} dicts

        Returns:
            Dict with response, route_decision, route_reason, system_prompt_tokens
        """
        context = context or {}
        context_window = context.get("context_window", "")
        conversation_history = context.get("conversation_history", [])
        memories = context.get("memories", [])
        session_id = context.get("session_id")
        interface = context.get("interface", "desktop")
        context_items = context.get("context_items")  # Phase 1: ring items from engine
        prepare_only = bool(context.get("prepare_only", False))  # MCP: assemble + return bundle, no inference
        extra_directive = context.get("extra_directive")  # Voice echo-retry: per-call system-prompt override
        self._last_denied_count = 0  # Reset per-request
        self._last_injected_memories = memories  # Expose for GroundingLink

        # Voice (and other Director.process callers) supply pre-fetched memories
        # upstream via run_search_chain. When caller-supplied memories exist,
        # skip Director's own matrix fetch — same matrix, hitting it twice.
        # Text /message reaches Director via the mailbox/assembler auto_fetch
        # path (not this entry), so this only affects voice + scout call sites.
        # Tracked follow-up: voice loses constellation prefetch via this path;
        # add a `constellation` source type to search_chain to restore.
        if memories:
            logger.info(
                f"[PROCESS] Using {len(memories)} caller-supplied memories (skipping redundant Director fetch)"
            )
        else:
            memory_text = await self._fetch_memory_context(message, max_tokens=1500)
            if memory_text:
                memories = [{"content": memory_text, "node_type": "context"}]
                logger.info(f"[PROCESS] Fresh memory context ({len(memory_text)} chars)")

        # [TRACE] Entity System Verification Logging
        logger.info("=" * 60)
        logger.info("[TRACE] Director.process() ENTRY")
        logger.info(f"[TRACE] Message: '{message}'")
        logger.info(f"[TRACE] Has _entity_context: {hasattr(self, '_entity_context')}")
        logger.info(f"[TRACE] _entity_context value: {getattr(self, '_entity_context', 'NOT SET')}")

        # [HISTORY-TRACE] Log what Director.process receives
        logger.info(f"[HISTORY-TRACE] Director.process CALLED with message: '{message[:50]}...'")
        logger.info(f"[HISTORY-TRACE] Director.process: context_window has {len(context_window)} chars")
        logger.info(f"[HISTORY-TRACE] Director.process: conversation_history has {len(conversation_history)} items")
        logger.info(f"[HISTORY-TRACE] Director.process: {len(memories)} memories passed in")

        # RING BUFFER: Record user message (structural guarantee against forgetting)
        self._active_ring.add_user(message)
        logger.debug("[PROCESS-RING] Recorded user message to ring buffer (size: %d)", len(self._active_ring))

        # PERCEPTION: Ingest user message before prompt assembly
        self._perception_turn_count += 1
        self._perception_field.ingest(message, self._perception_turn_count)

        # CURIOSITY: Feed memory confidence gaps into curiosity buffer
        try:
            engine = getattr(self, '_engine', None) or getattr(self, 'engine', None)
            if engine and hasattr(engine, 'consciousness'):
                cbuf = engine.consciousness.curiosity
                cbuf.tick(self._perception_turn_count)
                if self._last_memory_confidence:
                    conf_level = self._last_memory_confidence.level
                    query = self._last_memory_confidence.query
                    if conf_level == "NONE":
                        cbuf.ingest("memory_gap", f"What is '{query}' about?", 0.7)
                    elif conf_level == "LOW":
                        cbuf.ingest("memory_gap", f"Fragments about '{query}' but gaps remain", 0.5)
        except Exception as e:
            logger.debug(f"[CURIOSITY] Ingestion error (non-fatal): {e}")

        start_time = time.time()
        response_text = ""
        route_decision = "unknown"
        route_reason = "unknown"
        system_prompt = ""  # Initialize to ensure QA captures it
        system_prompt_tokens = 0
        narration_applied = False  # Track for QA (BUG-D fix)

        # ========================================================================
        # BUILD FRAMED CONTEXT (THE KEY INTEGRATION)
        # Uses EntityContext.build_framed_context() for:
        # - Entity detection and profile loading
        # - Temporal framing (<past_memory> tags vs [This session])
        # - Response instructions
        # ========================================================================

        framed_context = ""
        # [TRACE] About to build framed context
        logger.info("[TRACE] About to build framed context...")
        if await self._ensure_entity_context():
            logger.info("[TRACE] Calling _entity_context.build_framed_context()")
            try:
                # Convert memories to dict format if needed
                memory_dicts = []
                for m in memories:
                    if isinstance(m, dict):
                        memory_dicts.append(m)
                    elif hasattr(m, 'content'):
                        d = {
                            "content": m.content,
                            "created_at": getattr(m, 'created_at', None),
                            "node_type": getattr(m, 'node_type', 'memory'),
                        }
                        # Preserve retrieval score from Geometric L1 scoring
                        if hasattr(m, '_retrieval_score'):
                            d["_retrieval_score"] = m._retrieval_score
                        memory_dicts.append(d)
                    else:
                        memory_dicts.append({"content": str(m)})

                framed_context = await self._entity_context.build_framed_context(
                    message=message,
                    conversation_history=conversation_history,
                    memories=memory_dicts,
                    session_id=session_id,
                )
                # [TRACE] After context built
                logger.info(f"[TRACE] Framed context built: {len(framed_context)} chars")
                logger.info(f"[TRACE] Context contains 'marzipan': {'marzipan' in framed_context.lower()}")
                logger.info(f"[TRACE] Context preview:\n{framed_context[:500]}")
                logger.info("=" * 60)
                logger.info(f"[CONTEXT] Built framed context: {len(framed_context)} chars")
            except Exception as e:
                logger.error(f"Context building failed: {e}")
                import traceback
                logger.error(traceback.format_exc())
        else:
            logger.info("[TRACE] WARNING: No _entity_context available!")

        # ── L2: Classify intent BEFORE routing ──────────────────────
        # Prefer LLM router (qwen3:8b) over keyword classification
        if getattr(self, '_intent_router', None) is not None:
            try:
                intent = await self._intent_router.classify(message)
            except Exception as e:
                logger.warning(f"[INTENT-ROUTER] classify() failed: {e}, using keyword fallback")
                intent = self._classify_intent(message, conversation_history)
        else:
            intent = self._classify_intent(message, conversation_history)
        self._last_intent = intent  # Store for _should_delegate routing
        logger.info(
            "[INTENT] mode=%s confidence=%.2f signals=%s continuation=%s",
            intent.mode.value, intent.confidence,
            intent.signals, intent.is_continuation,
        )

        # ── Context Register: determine conversational posture ─────
        _reg_active_thread = None
        _reg_consciousness = None
        try:
            _reg_engine = getattr(self, '_engine', None) or getattr(self, 'engine', None)
            if _reg_engine:
                _reg_librarian = _reg_engine.get_actor("librarian") if hasattr(_reg_engine, 'get_actor') else None
                if _reg_librarian:
                    _reg_active_thread = getattr(_reg_librarian, '_active_thread', None)
                _reg_consciousness = getattr(_reg_engine, 'consciousness', None)
        except Exception as e:
            logger.debug("[REGISTER] Failed to read thread/consciousness: %s", e)

        register = self._register_state.update(
            perception=self._perception_field,
            intent=intent,
            consciousness=_reg_consciousness,
            active_thread=_reg_active_thread,
            flow_signal=None,  # Not yet plumbed to Director
        )
        logger.info(
            "[REGISTER] posture=%s confidence=%.2f",
            register.value, self._register_state.confidence,
        )

        # ── LunaScript: per-turn update ──
        if self._lunascript:
            try:
                ls_result = await self._lunascript.on_turn(
                    message=message,
                    history=[t["content"] for t in conversation_history if t.get("role") == "assistant"] if conversation_history else [],
                    perception=self._perception_field,
                    intent=intent,
                )
                if ls_result and ls_result.constraints_prompt:
                    framed_context += f"\n\n{ls_result.constraints_prompt}"
            except Exception as e:
                logger.debug(f"[LUNASCRIPT] on_turn failed: {e}")

        # ── Resolve access bridge (FaceID → permissions) ──────────
        bridge_result = await self._resolve_bridge()

        # Check if we should delegate to Claude
        # One-shot override: read and clear atomically (used by qa_simulate_with_options)
        _overrides = self._next_message_overrides
        self._next_message_overrides = None

        force_route = _overrides.get("force_route") if _overrides else None
        if force_route in ("local", "LOCAL_ONLY"):
            should_delegate = False
            logger.info("[SIMULATE] force_route=local applied")
        elif force_route in ("delegated", "FULL_DELEGATION", "full_delegation"):
            should_delegate = True
            logger.info("[SIMULATE] force_route=delegated applied")
        else:
            should_delegate = await self._should_delegate(message)

        if should_delegate:
            route_decision = "delegated"
            route_reason = "complexity/signals"

            # ── PromptAssembler: single funnel for prompt construction ──
            _aperture = getattr(self.engine, 'aperture', None)
            assembler_result = await self._assembler.build(PromptRequest(
                message=message,
                conversation_history=conversation_history,
                memories=memories,
                framed_context=framed_context,
                route="delegated",
                interface=interface,
                intent=intent,
                bridge_result=bridge_result,
                register_block=self._register_state.to_prompt_block() if self._register_enabled else None,
                aperture=_aperture.state if _aperture else None,
                reflection_mode=getattr(self.engine, '_active_reflection_mode', None),
                has_nexus_context=bool(getattr(self.engine, '_last_nexus_nodes', None)),
                context_items=context_items,
                memory_graph_state=getattr(self, "_current_memory_graph_state", None),
                extra_directive=extra_directive,
            ))
            system_prompt = assembler_result.system_prompt
            messages = assembler_result.messages
            system_prompt_tokens = assembler_result.prompt_tokens

            # Context audit logging
            logger.info("=" * 80)
            logger.info("[CONTEXT-AUDIT] Processing turn via assembler")
            logger.info("[CONTEXT-AUDIT] identity=%s memory=%s temporal=%s voice=%s tokens≈%d",
                        assembler_result.identity_source, assembler_result.memory_source,
                        assembler_result.gap_category, assembler_result.voice_injected,
                        assembler_result.prompt_tokens)
            logger.info("[CONTEXT-AUDIT] System prompt preview:\n%s", system_prompt[:800])
            logger.info("[CONTEXT-AUDIT] MESSAGES ARRAY (%d messages)", len(messages))
            logger.info("=" * 80)

            # ── LunaScript: sign outbound delegation ──
            _ls_package = None
            if self._lunascript:
                try:
                    _ls_package = await self._lunascript.on_delegation_start(
                        consciousness=getattr(self, '_consciousness', None),
                        personality=getattr(self, '_personality', None),
                        entities=list(self._entity_context.keys()) if hasattr(self, '_entity_context') and self._entity_context and hasattr(self._entity_context, 'keys') else [],
                    )
                    if _ls_package and _ls_package.constraint_prompt:
                        system_prompt += f"\n\n{_ls_package.constraint_prompt}"
                except Exception as e:
                    logger.debug(f"[LUNASCRIPT] on_delegation_start failed: {e}")

            # Store for /prompt command
            self._last_system_prompt = system_prompt
            self._last_route_decision = "delegated"
            self._last_prompt_meta = assembler_result.to_dict()

            # ── MCP prepare_only: emit bundle instead of running inference ──
            if prepare_only:
                await self._emit_prepare_bundle(
                    correlation_id=msg.correlation_id,
                    system_prompt=system_prompt,
                    messages=messages,
                    memories=memories,
                    framed_context=framed_context,
                    assembler_result=assembler_result,
                    route="delegated",
                )
                self._generating = False
                return

            # Use FallbackChain for resilient inference (if available)
            if self._fallback_chain is not None:
                try:
                    result = await self._fallback_chain.generate(
                        messages=messages,
                        system=system_prompt,
                        max_tokens=512,
                    )
                    response_text = result.content
                    provider_used = result.provider_used
                    self._delegated_generations += 1

                    # Log if fallback occurred
                    if len(result.attempts) > 1:
                        logger.info(f"[FALLBACK] Used {provider_used} after {len(result.attempts)-1} failures")

                except AllProvidersFailedError as e:
                    logger.error(f"[FALLBACK] All providers failed: {[a.provider + ': ' + (a.error or 'unknown') for a in e.attempts]}")
                    response_text = "I'm having trouble connecting right now. All inference providers are unavailable."
                except Exception as e:
                    logger.error(f"Director.process delegation (fallback chain) failed: {e}")
                    response_text = "I'm having trouble processing that right now."
            else:
                # Legacy direct Claude call (no fallback chain)
                try:
                    response = self.client.messages.create(
                        model=self._claude_model,
                        max_tokens=512,
                        system=system_prompt,
                        messages=messages,
                    )
                    response_text = response.content[0].text if response.content else ""
                    self._delegated_generations += 1
                except Exception as e:
                    logger.error(f"Director.process delegation failed: {e}")
                    response_text = "I'm having trouble processing that right now."

            # ── LunaScript: process delegation return ──
            if self._lunascript and _ls_package:
                try:
                    _ls_return = await self._lunascript.on_delegation_return(
                        response_text=response_text,
                        package=_ls_package,
                        provider_used=locals().get('provider_used', 'claude-direct'),
                    )
                    if not _ls_return.veto_passed and _ls_return.retry_prompt:
                        logger.info(f"[LUNASCRIPT] Veto triggered: {_ls_return.classification}")
                except Exception as e:
                    logger.debug(f"[LUNASCRIPT] on_delegation_return failed: {e}")

            # ================================================================
            # NARRATION DISABLED: Qwen 3B rewrite was degrading Groq's
            # 70B output quality. Luna's voice is now injected via the
            # system prompt (PromptAssembler), not post-hoc rewriting.
            # ================================================================

        elif self.local_available:
            route_decision = "local"
            route_reason = "simple query"
            used_retrieval = False
            pipeline_voice_injected = False

            # ── PromptAssembler: single funnel for local path ──
            # ContextPipeline takes priority when available (it has its own prompt builder)
            assembler_result = None
            if self._context_pipeline is not None:
                try:
                    # Populate ring from context_window if empty (bridge Engine→Director gap)
                    if len(self._context_pipeline._ring) == 0 and context_window:
                        logger.info("[PROCESS-LOCAL-PIPELINE] Ring empty, populating from context_window")
                        self._populate_ring_from_context(
                            self._context_pipeline._ring,
                            context_window
                        )

                    packet = await self._context_pipeline.build(message)
                    system_prompt = packet.system_prompt
                    used_retrieval = packet.used_retrieval
                    # Inject ACCESS block — pipeline skips the assembler
                    _access_block = self._assembler._build_access_block(
                        PromptRequest(message=message, bridge_result=bridge_result)
                    )
                    if _access_block:
                        system_prompt = system_prompt + "\n\n" + _access_block
                    # Inject REGISTER block — pipeline skips the assembler
                    if self._register_enabled:
                        _register_block = self._register_state.to_prompt_block()
                        if _register_block:
                            system_prompt = system_prompt + "\n\n" + _register_block

                    # Pipeline path does not call PromptAssembler layers.
                    # Inject voice block here so local/simple queries keep Luna voice constraints.
                    _voice_block = self._generate_voice_block(
                        message,
                        memories,
                        framed_context,
                        conversation_history,
                    )
                    if _voice_block and _voice_block.strip():
                        system_prompt = system_prompt + "\n\n" + _voice_block.strip()
                        pipeline_voice_injected = True
                    logger.info(f"[PROCESS-LOCAL-PIPELINE] Using unified context: {packet}")
                except Exception as e:
                    logger.warning(f"[PROCESS-LOCAL] Pipeline failed, using assembler: {e}")
                    assembler_result = await self._assembler.build(PromptRequest(
                        message=message,
                        conversation_history=conversation_history,
                        memories=memories,
                        framed_context=framed_context,
                        route="local",
                        interface=interface,
                        bridge_result=bridge_result,
                        register_block=self._register_state.to_prompt_block() if self._register_enabled else None,
                        reflection_mode=getattr(self.engine, '_active_reflection_mode', None),
                        has_nexus_context=bool(getattr(self.engine, '_last_nexus_nodes', None)),
                        context_items=context_items,
                        memory_graph_state=getattr(self, "_current_memory_graph_state", None),
                        extra_directive=extra_directive,
                    ))
                    system_prompt = assembler_result.system_prompt
            else:
                assembler_result = await self._assembler.build(PromptRequest(
                    message=message,
                    conversation_history=conversation_history,
                    memories=memories,
                    framed_context=framed_context,
                    route="local",
                    interface=interface,
                    bridge_result=bridge_result,
                    register_block=self._register_state.to_prompt_block() if self._register_enabled else None,
                    reflection_mode=getattr(self.engine, '_active_reflection_mode', None),
                    has_nexus_context=bool(getattr(self.engine, '_last_nexus_nodes', None)),
                    context_items=context_items,
                    memory_graph_state=getattr(self, "_current_memory_graph_state", None),
                    extra_directive=extra_directive,
                ))
                system_prompt = assembler_result.system_prompt

            system_prompt_tokens = len(system_prompt) // 4
            self._last_system_prompt = system_prompt
            if assembler_result:
                self._last_prompt_meta = assembler_result.to_dict()
            else:
                # Keep debug/QA metadata non-null on pipeline-built prompts.
                self._last_prompt_meta = {
                    "identity_source": "pipeline",
                    "memory_source": "pipeline" if used_retrieval else "none",
                    "voice_injected": pipeline_voice_injected,
                    "register_injected": bool(self._register_enabled),
                    "register_active": (
                        self._register_state.active.value if self._register_enabled else None
                    ),
                    "context_items_injected": bool(context_items),
                    "prompt_tokens": system_prompt_tokens,
                    "response_mode": "standard",
                }

            # ── MCP prepare_only: emit bundle instead of running inference ──
            if prepare_only:
                await self._emit_prepare_bundle(
                    correlation_id=msg.correlation_id,
                    system_prompt=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                    memories=memories,
                    framed_context=framed_context,
                    assembler_result=assembler_result,
                    route="local",
                )
                # Phase 1A.7: do NOT mutate self._generating here — that flag
                # belongs to the serial /message path; prepare_only runs as
                # a background task per request.
                return

            # Voice turns register a token callback via director.on_stream()
            # before calling process(). When present, take the streaming
            # branch so _on_token fires per-token and the backend segmenter
            # can synthesize audio as the LLM generates, instead of waiting
            # for the full response. Non-voice callers (empty callback list)
            # keep the existing non-streaming behavior.
            has_voice_callbacks = bool(self._stream_callbacks)

            try:
                # Use Ollama provider with full conversation history (preferred)
                ollama_provider = None
                if LLM_REGISTRY_AVAILABLE:
                    try:
                        ollama_provider = get_registry().get("ollama")
                    except Exception:
                        pass

                if ollama_provider and getattr(ollama_provider, 'is_available', False):
                    # Build messages array with history (same format as delegated path)
                    msgs = [LLMMessage(role="system", content=system_prompt)]
                    for turn in conversation_history:
                        role = turn.get("role", "user") if isinstance(turn, dict) else "user"
                        content = turn.get("content", "") if isinstance(turn, dict) else str(turn)
                        if role in ("user", "assistant") and content:
                            msgs.append(LLMMessage(role=role, content=content))
                    msgs.append(LLMMessage(role="user", content=message))

                    if has_voice_callbacks:
                        response_text = ""
                        async for token in ollama_provider.stream(messages=msgs, max_tokens=512):
                            if self._abort_requested:
                                break
                            response_text += token
                            await self._stream_to_callbacks(token)
                        logger.info(f"[PROCESS-LOCAL] Ollama streamed with {len(msgs)} messages ({len(response_text)} chars), used_retrieval={used_retrieval}")
                    else:
                        result = await ollama_provider.complete(messages=msgs, max_tokens=512)
                        response_text = result.content
                        logger.info(f"[PROCESS-LOCAL] Ollama complete with {len(msgs)} messages, used_retrieval={used_retrieval}")
                else:
                    # Fallback to MLX local inference (no conversation history)
                    if has_voice_callbacks:
                        response_text = ""
                        async for token in self._local.generate_stream(
                            message,
                            system_prompt=system_prompt,
                            max_tokens=512,
                        ):
                            if self._abort_requested:
                                break
                            response_text += token
                            await self._stream_to_callbacks(token)
                        logger.info(f"[PROCESS-LOCAL] MLX streamed ({len(response_text)} chars), used_retrieval={used_retrieval}")
                    else:
                        result = await self._local.generate(
                            message,
                            system_prompt=system_prompt,
                            max_tokens=512,
                        )
                        response_text = result.text if hasattr(result, 'text') else str(result)
                        logger.info(f"[PROCESS-LOCAL] MLX fallback, used_retrieval={used_retrieval}")

                self._local_generations += 1
            except Exception as e:
                logger.error(f"Director.process local failed: {e}")
                # Preserve any partial tokens already streamed to voice callbacks
                # before the error — backend's fallback re-feed is gated on
                # first_chunk_sent, so partial streaming suppresses the re-feed
                # and avoids duplicate audio.
                if not response_text:
                    response_text = "I'm having trouble with that."

        else:
            # Fallback when local unavailable - use FallbackChain for resilience
            route_decision = "delegated"
            route_reason = "local unavailable"

            # ── PromptAssembler: single funnel for fallback path ──
            _aperture = getattr(self.engine, 'aperture', None)
            assembler_result = await self._assembler.build(PromptRequest(
                message=message,
                conversation_history=conversation_history,
                framed_context=framed_context,
                route="fallback",
                auto_fetch_memory=True,
                bridge_result=bridge_result,
                register_block=self._register_state.to_prompt_block() if self._register_enabled else None,
                aperture=_aperture.state if _aperture else None,
                reflection_mode=getattr(self.engine, '_active_reflection_mode', None),
                memory_graph_state=getattr(self, "_current_memory_graph_state", None),
                extra_directive=extra_directive,
            ))
            system_prompt = assembler_result.system_prompt
            messages = assembler_result.messages
            system_prompt_tokens = assembler_result.prompt_tokens
            self._last_system_prompt = system_prompt
            self._last_prompt_meta = assembler_result.to_dict()

            # Use FallbackChain for resilient inference (if available)
            if self._fallback_chain is not None:
                try:
                    result = await self._fallback_chain.generate(
                        messages=messages,
                        system=system_prompt,
                        max_tokens=512,
                    )
                    response_text = result.content
                    provider_used = result.provider_used
                    self._delegated_generations += 1

                    if len(result.attempts) > 1:
                        logger.info(f"[FALLBACK] Used {provider_used} after {len(result.attempts)-1} failures")

                except AllProvidersFailedError as e:
                    logger.error(f"[FALLBACK] All providers failed: {[a.provider + ': ' + (a.error or 'unknown') for a in e.attempts]}")
                    response_text = "I'm having trouble connecting right now. All inference providers are unavailable."
                except Exception as e:
                    logger.error(f"Director.process fallback (chain) failed: {e}")
                    response_text = "I'm having trouble right now."
            else:
                # Legacy direct Claude call (no fallback chain)
                try:
                    response = self.client.messages.create(
                        model=self._claude_model,
                        max_tokens=512,
                        system=system_prompt,
                        messages=messages,
                    )
                    response_text = response.content[0].text if response.content else ""
                    self._delegated_generations += 1
                except Exception as e:
                    logger.error(f"Director.process fallback failed: {e}")
                    response_text = "I'm having trouble right now."

        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(f"Director.process complete: {route_decision} in {elapsed_ms:.0f}ms")

        # RING BUFFER: Record response (structural guarantee against forgetting)
        # BUG-C FIX: Always add response to prevent orphaned user messages that bleed into next turn
        if response_text:
            response_text = self._strip_trailing_question(response_text)
            self._active_ring.add_assistant(response_text)
            logger.debug("[PROCESS-RING] Recorded response to ring buffer (size: %d)", len(self._active_ring))
        else:
            # Add placeholder to prevent context bleeding from orphaned user message
            self._active_ring.add_assistant("[No response generated]")
            logger.warning("[PROCESS-RING] Added placeholder for failed/empty response")

        # Capture used_retrieval from local path (for debugging)
        # Default to True for delegated path (delegation always fetches memory)
        final_used_retrieval = locals().get('used_retrieval', route_decision == "delegated")

        # ========================================================================
        # QA VALIDATION: Run assertions against this inference
        # ========================================================================
        qa_report = None
        if QA_AVAILABLE and self._qa_enabled:
            try:
                # Lazy init QA validator
                if self._qa_validator is None:
                    self._qa_validator = get_qa_validator()

                # Populate integration stats for I1/I2/I3 assertions.
                memory_stats = {}
                try:
                    matrix_actor = self._engine.get_actor("matrix") if self._engine else None
                    matrix_obj = None
                    if matrix_actor is not None:
                        matrix_obj = (
                            getattr(matrix_actor, "matrix", None)
                            or getattr(matrix_actor, "_matrix", None)
                        )
                    if matrix_obj is not None:
                        memory_stats = await matrix_obj.get_stats()
                except Exception as e:
                    logger.debug(f"[QA] memory_stats unavailable in director path: {e}")

                voice_injected = bool(
                    (self._last_prompt_meta or {}).get("voice_injected", False)
                )

                # Build inference context with all collected data
                # FIX BUG-A: Use system_prompt directly (now initialized at top)
                qa_ctx = InferenceContext(
                    session_id=session_id or "",
                    query=message,
                    route=route_decision.upper() if route_decision else "",
                    complexity_score=0.0,  # TODO: Add complexity scoring
                    providers_tried=[route_decision] if route_decision else [],
                    provider_used=locals().get('provider_used', route_decision or ""),
                    personality_injected=system_prompt_tokens > 250,  # >1000 chars
                    personality_length=system_prompt_tokens * 4,
                    system_prompt=system_prompt,  # Now guaranteed to be set
                    voice_injected=voice_injected,
                    virtues_loaded=self._identity_buffer is not None,
                    narration_applied=narration_applied,  # BUG-D fix: now tracked
                    raw_response=response_text,
                    final_response=response_text,
                    latency_ms=elapsed_ms,
                    input_tokens=len(message) // 4,
                    output_tokens=len(response_text) // 4,
                    memory_stats=memory_stats,
                    memory_confidence_level=getattr(self._last_memory_confidence, 'level', '') if self._last_memory_confidence else '',
                )

                # Add request chain steps
                qa_ctx.add_step("receive", 0, f"Query received: '{message[:50]}...'")
                qa_ctx.add_step("route", elapsed_ms * 0.1, f"Route: {route_decision} ({route_reason})")
                qa_ctx.add_step("generate", elapsed_ms * 0.8, f"{route_decision} returned {len(response_text)} chars")
                qa_ctx.add_step("output", elapsed_ms, "Response ready")

                # Validate
                qa_report = self._qa_validator.validate(qa_ctx)

                if not qa_report.passed:
                    logger.warning(f"[QA] FAILED ({qa_report.failed_count} assertions): {qa_report.diagnosis}")
                else:
                    logger.debug(f"[QA] PASSED ({len(qa_report.assertions)} assertions)")

                # ── QA Retry Gate ────────────────────────────────────────────
                # P1 (personality not injected) is the one failure type where
                # re-prompting is correctable. Bounded to ONE retry per turn
                # via a local flag — never retries infrastructure failures.
                if (
                    not qa_report.passed
                    and not locals().get("_qa_retry_attempted", False)
                    and any(f.id == "P1" for f in qa_report.high_failures)
                ):
                    _qa_retry_attempted = True
                    _hint = qa_report.diagnosis or "Ensure Luna's identity is fully present."
                    logger.warning("[QA-GATE] P1 failure — retrying generation with correction hint")
                    _corrected_system = (system_prompt or "") + f"\n\n[CORRECTION: {_hint}]"
                    try:
                        response_text = await self._generate_cloud(
                            prompt=message,
                            system=_corrected_system,
                            conversation_history=locals().get("conversation_history", []),
                        )
                        logger.info("[QA-GATE] Retry succeeded (%d chars)", len(response_text))
                    except Exception as _retry_e:
                        logger.warning("[QA-GATE] Retry failed (non-fatal): %s", _retry_e)

            except Exception as e:
                logger.warning(f"[QA] Validation error (non-fatal): {e}")

        # ========================================================================
        # PERCEPTION: Record Luna's action for next turn's trigger context
        # ========================================================================
        if response_text:
            self._perception_field.record_luna_action(response_text)

            # CURIOSITY: Track if Luna asked a question (for spacing enforcement)
            if "?" in response_text:
                try:
                    engine = getattr(self, '_engine', None) or getattr(self, 'engine', None)
                    if engine and hasattr(engine, 'consciousness'):
                        engine.consciousness.curiosity.record_question_asked(
                            self._perception_turn_count
                        )
                except Exception:
                    pass

            # LunaScript: record Luna's response for trait measurement on next turn
            if self._lunascript:
                self._lunascript.last_luna_response = response_text

        # ========================================================================
        # PROMPT ARCHAEOLOGY: Log prompt for forensic analysis
        # See: HANDOFF_PROMPT_ARCHAEOLOGY.md
        # ========================================================================
        try:
            self.log_prompt_archaeology(message, response_text)
        except Exception as e:
            logger.debug(f"[ARCHAEOLOGY] Logging failed (non-fatal): {e}")

        return {
            "response": response_text,
            "route_decision": route_decision,
            "route_reason": route_reason,
            "system_prompt_tokens": system_prompt_tokens,
            "latency_ms": elapsed_ms,
            "used_retrieval": final_used_retrieval,
            "qa_passed": qa_report.passed if qa_report else None,
            "qa_failures": qa_report.failed_count if qa_report else 0,
        }

    def on_stream(self, callback: Callable[[str], None]) -> None:
        """Register a callback for streaming tokens."""
        self._stream_callbacks.append(callback)

    def remove_stream_callback(self, callback: Callable[[str], None]) -> None:
        """Remove a streaming callback."""
        if callback in self._stream_callbacks:
            self._stream_callbacks.remove(callback)

    async def handle(self, msg: Message) -> None:
        """Process messages."""
        logger.debug(f"Director received: {msg.type}")

        match msg.type:
            case "generate" | "generate_stream" | "generate_local" | "generate_hybrid":
                # ALL generation goes through the Director (local-first architecture)
                await self._handle_director_generate(msg)

            case "abort":
                await self._handle_abort(msg)

            case "resumption_trigger":
                # Voice v2.0 Step 4 — produce a revised response that
                # incorporates the user's interrupt. Writes PARTIAL_INTERRUPTED
                # + INTERRUPT_UTTERANCE turn rows, then re-enters generation.
                await self._handle_resumption_trigger(msg)

            case "set_model":
                # RESERVED — no active senders; wired for future runtime model-switching
                self._claude_model = msg.payload.get("model", self._claude_model)
                logger.info(f"Director Claude model changed to: {self._claude_model}")

            case "load_local":
                # RESERVED — no active senders; wired for future local model hot-loading
                await self._init_local_inference()

            case _:
                logger.warning(f"Director: unknown message type: {msg.type}")

    async def _handle_director_generate(self, msg: Message) -> None:
        """
        Main generation handler - LOCAL-FIRST architecture with PLANNING STEP.

        Flow:
        1. PLAN: Decide upfront if query needs delegation (complexity + signals)
        2. If complex: delegate to Claude, get facts, narrate in Luna's voice
        3. If simple: pure local generation (no delegation watching)

        This ensures Luna's mind is ALWAYS primary. Claude is just a research assistant.
        The planning step replaces hope that Qwen outputs <REQ_CLAUDE> with explicit routing.
        """
        payload = msg.payload
        prepare_only = bool(payload.get("prepare_only", False))  # MCP: assemble + return bundle, no inference

        # ── Phase 1A.7: prepare_only branches before any self.* mutation ──
        # The mailbox loop is otherwise serial — every concurrent
        # /context/prepare HTTP request would queue behind the previous one,
        # producing 30 s tail timeouts. prepare_only is read-only by
        # contract (1A.4 enforces no turn side effects), so it is the only
        # generation path safe to fan out. Spawn it as a background task
        # bounded by `_prepare_only_sem`; the mailbox loop returns
        # immediately and is free to handle the next message (including
        # a serial /message).
        if prepare_only:
            asyncio.create_task(
                self._handle_prepare_generate_concurrent(msg, payload),
                name=f"prepare-{(msg.correlation_id or 'anon')[:8]}",
            )
            return

        user_message = payload.get("user_message", "")
        system_prompt = payload.get("system_prompt")  # None if caller didn't pre-assemble
        max_tokens = payload.get("max_tokens", 512)
        context_window = payload.get("context_window", "")  # Conversation history
        prefetched_memory = payload.get("memory_context", "")  # Pre-fetched by search chain
        chain_results = payload.get("chain_results", [])  # Structured search chain results
        context_items = payload.get("context_items")  # Phase 1: ring items from engine (MIDDLE+OUTER)
        trace = payload.get("trace")

        self._generating = True
        self._last_denied_count = 0  # Reset per-request
        self._pending_skill_widget = None  # Skill widget for current request
        self._last_injected_memories = []  # Reset for GroundingLink
        self._last_fetched_memory_text = ""  # Reset for GroundingLink auto-fetch path
        self._last_director_pivot_shadow = None  # Reset shadow telemetry per-turn
        self._last_director_pivot_active = None  # Reset active telemetry per-turn
        self._nexus_nodes = payload.get("nexus_nodes", [])  # Nexus extractions from engine
        self._current_memory_graph_state = payload.get("memory_graph_state")  # Live ring/aperture/thread/graph snapshot for L5.0
        # Phase 5 — engine stamps an `agentic_actors` list on the payload when
        # this generation was dispatched after agent_loop ran. send_to_engine
        # lifts it onto generation_complete and clears it.
        self._pending_agentic_actors = payload.get("agentic_actors") or None
        self._abort_requested = False
        self._current_correlation_id = msg.correlation_id

        # Ensure entity context (and LunaScript) are initialized
        await self._ensure_entity_context()

        # LunaScript per-turn measurement (position + traits from last response)
        if self._lunascript:
            try:
                history = [t.content for t in self._active_ring.get_last_n(6)] if hasattr(self, '_active_ring') and self._active_ring else []
                print(f"◈ [LUNASCRIPT] on_turn: last_response={len(self._lunascript.last_luna_response or '')} chars, history={len(history)} msgs")
                ls_result = await self._lunascript.on_turn(
                    message=user_message,
                    history=history,
                )
                print(f"◈ [LUNASCRIPT] on_turn result: position={ls_result.position}, measurement={'YES' if self._lunascript._last_measurement else 'NO'}")
            except Exception as e:
                print(f"◈ [LUNASCRIPT] on_turn FAILED: {e}")
                import traceback; traceback.print_exc()

        start_time = time.time()

        # ── Skill Registry: deterministic dispatch ────────────────────
        _skill_result = None
        _skill_widget = None
        _skill_confirm_hint = None  # low-confidence → ask user to confirm
        if self._skill_registry:
            try:
                detection_cfg = self._skill_registry.config.detection
                if detection_cfg.mode == "llm-assisted" and self._local:
                    async def _classify(prompt):
                        return await self._local.generate(prompt, max_tokens=20)
                    detection = await self._skill_registry.detector.detect_with_llm(user_message, _classify)
                else:
                    detection = self._skill_registry.detector.detect(user_message)
                if detection:
                    skill_name = detection.skill
                    if detection.confidence == "low":
                        # Don't fire the skill — ask the user to confirm
                        _skill_confirm_hint = (
                            f"The user's message may relate to the '{skill_name}' skill. "
                            f"Ask the user if they'd like you to run the {skill_name} skill "
                            f"before proceeding. Do NOT execute it automatically."
                        )
                        logger.info(f"[SKILL] {skill_name} matched with LOW confidence — will ask user to confirm")
                    else:
                        # High confidence — fire immediately
                        query = user_message
                        trimmed = user_message.strip()
                        if trimmed.startswith("/"):
                            parts = trimmed.split(None, 1)
                            query = parts[1] if len(parts) > 1 else ""
                        # Skill context: pass engine-owned actors that skills
                        # may need. Keep this narrow — only the actors skills
                        # actually consume. memory_recall needs matrix_actor.
                        _skill_ctx: dict = {
                            "matrix_actor": (
                                self.engine.get_actor("matrix") if self.engine else None
                            ),
                        }
                        _skill_result = await self._skill_registry.execute(
                            skill_name, query, context=_skill_ctx,
                        )
                        logger.info(
                            f"[SKILL] {skill_name} -> success={_skill_result.success} "
                            f"fallthrough={_skill_result.fallthrough} ms={_skill_result.execution_ms:.0f}"
                        )
            except Exception as e:
                logger.debug(f"[SKILL] Dispatch error: {e}")

        # Default-OFF kill switch: only staple skill result onto system prompt
        # when the caller explicitly opts in via payload. Mirrors PromptRequest
        # .include_skill_injection. Widget descriptor still gets built so the
        # frontend continues to receive skill output out-of-band.
        _include_skill_injection = bool(payload.get("include_skill_injection", False))

        if _skill_result and _skill_result.success and not _skill_result.fallthrough:
            # Skill fired — inject result into system prompt for narration,
            # but only if the caller opted in. Otherwise the skill output is
            # routed to the frontend widget only.
            if _include_skill_injection:
                skill = self._skill_registry.get(_skill_result.skill_name)
                hint = skill.narration_hint(_skill_result) if skill else ""
                skill_injection = _build_skill_injection(_skill_result, hint)
                system_prompt = system_prompt + skill_injection
            else:
                logger.info(
                    "[SKILL] injection suppressed (include_skill_injection=False): skill=%s",
                    _skill_result.skill_name,
                )

            # Build widget descriptor for frontend (always — skill ran successfully)
            _SKILL_WIDGET_TYPES = {
                "math": "latex", "logic": "table", "diagnostic": "diagnostic",
                "eden": "image", "reading": "document", "analytics": "chart",
                "arcade": "arcade",
            }
            widget_type = _SKILL_WIDGET_TYPES.get(_skill_result.skill_name)
            if widget_type and _skill_result.data:
                actual_type = _skill_result.data.get("type", widget_type)
                _skill_widget = {
                    "type": actual_type,
                    "skill": _skill_result.skill_name,
                    "data": _skill_result.data,
                    "latex": _skill_result.latex,
                }
            self._pending_skill_widget = _skill_widget

        elif _skill_result and _skill_result.success and _skill_result.fallthrough:
            # Skill succeeded but needs LLM generation (e.g. FormattingSkill)
            # Inject format constraint into system prompt
            skill = self._skill_registry.get(_skill_result.skill_name)
            hint = skill.narration_hint(_skill_result) if skill else ""
            if hint:
                system_prompt = system_prompt + f"\n\n## FORMAT CONSTRAINT ({_skill_result.skill_name.upper()})\n{hint}"

        if _skill_confirm_hint:
            # Low-confidence skill match — inject confirmation prompt
            system_prompt = system_prompt + f"\n\n## SKILL CONFIRMATION NEEDED\n{_skill_confirm_hint}"

        # Inject Document Reader grounding when Nexus context is present
        print(f"🔔 [DIRECTOR] nexus_nodes={len(self._nexus_nodes)}, types={set(n.get('node_type','?') for n in self._nexus_nodes[:10])}")
        if self._nexus_nodes:
            from luna.context.assembler import PromptAssembler
            system_prompt = system_prompt + "\n\n" + PromptAssembler.DOCUMENT_READER_GROUNDING
            print(f"🔔 [DIRECTOR] Document Reader grounding INJECTED")


        # PLANNING STEP: Decide routing upfront
        # One-shot override (set via set_next_overrides) wins over auto-routing.
        _overrides = self._next_message_overrides
        self._next_message_overrides = None
        force_route = _overrides.get("force_route") if _overrides else None
        if force_route in ("local", "LOCAL_ONLY"):
            should_delegate = False
            logger.info("[SIMULATE] force_route=local applied in _handle_director_generate")
        elif force_route in ("delegated", "FULL_DELEGATION", "full_delegation"):
            should_delegate = True
            logger.info("[SIMULATE] force_route=delegated applied in _handle_director_generate")
        else:
            should_delegate = await self._should_delegate(user_message)

        # ── Director pivot: shadow telemetry only (Phase B) ──────
        # Runs alongside the legacy decision above. Result is observed,
        # never mutates `should_delegate` or any prompt/route/backend.
        self._last_director_pivot_shadow = self._maybe_run_director_shadow(
            endpoint=payload.get("director_pivot_endpoint"),
            query=user_message,
            legacy_should_delegate=should_delegate,
        )

        # ── Director pivot: active path (Phase C, /message only) ──
        # Mode/endpoint-gated. On valid StagePacket may override
        # should_delegate / system_prompt; otherwise falls back to legacy
        # for this turn. Phase A stubs make fallback the expected outcome.
        active_result = self._maybe_run_director_active(
            endpoint=payload.get("director_pivot_endpoint"),
            query=user_message,
            legacy_should_delegate=should_delegate,
            legacy_system_prompt=system_prompt,
        )
        self._last_director_pivot_active = active_result["debug"] if active_result else None
        if active_result and active_result["applied"]:
            should_delegate = active_result["should_delegate"]
            system_prompt = active_result["system_prompt"]

        # ── Resolve access bridge (FaceID → permissions) ──────────
        bridge_result = await self._resolve_bridge()

        if should_delegate:
            # Complex query → delegate to Claude, narrate in Luna's voice
            await self._generate_with_delegation(
                user_message=user_message,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                correlation_id=msg.correlation_id,
                start_time=start_time,
                context_window=context_window,
                bridge_result=bridge_result,
                prefetched_memory=prefetched_memory,
                chain_results=chain_results,
                context_items=context_items,
                trace=trace,
            )
        elif self.local_available:
            # Simple query → pure local generation
            await self._generate_local_only(
                user_message=user_message,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                correlation_id=msg.correlation_id,
                start_time=start_time,
                context_window=context_window,
                bridge_result=bridge_result,
            )
        else:
            # Fallback to Claude directly if local not available
            logger.warning("Local inference not available, using Claude directly")
            await self._generate_claude_direct(
                user_message=user_message,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                correlation_id=msg.correlation_id,
                start_time=start_time,
                context_window=context_window,
                bridge_result=bridge_result,
            )

        self._generating = False
        self._current_correlation_id = None

    async def _handle_prepare_generate_concurrent(self, msg: Message, payload: dict) -> None:
        """Phase 1A.7: concurrent handler for prepare_only generate messages.

        Runs the assembly pipeline in a background task so the director
        mailbox does not serialize concurrent /context/prepare callers.
        Bounded by `_prepare_only_sem` to cap fan-out on the LLM router and
        matrix DB pool.

        State isolation contract: this handler must not mutate any of the
        per-request `self._*` fields used by the serial /message path
        (`_generating`, `_abort_requested`, `_current_correlation_id`,
        `_last_*`, `_pending_skill_widget`, `_nexus_nodes`). All per-request
        state lives in the local args of `_generate_with_delegation`. The
        diagnostic fields written inside that function under `prepare_only`
        (`_last_system_prompt`, `_last_route_decision`, `_last_prompt_meta`)
        are last-write-wins snapshots — not load-bearing for the bundle
        each caller receives, which is fully assembled in local scope.

        On any unhandled exception, emit a typed-error generation_complete
        so the HTTP callback's `wait_for(response_future)` resolves rather
        than timing out the caller at the 30 s HTTP boundary.
        """
        async with self._prepare_only_sem:
            self._prepare_in_flight += 1
            try:
                user_message = payload.get("user_message", "")
                system_prompt = payload.get("system_prompt")  # None if MCP caller didn't pre-assemble
                max_tokens = payload.get("max_tokens", 512)
                context_window = payload.get("context_window", "")
                prefetched_memory = payload.get("memory_context", "")
                chain_results = payload.get("chain_results", [])
                context_items = payload.get("context_items")
                trace = payload.get("trace")

                start_time = time.time()
                bridge_result = await self._resolve_bridge()
                await self._generate_with_delegation(
                    user_message=user_message,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    correlation_id=msg.correlation_id,
                    start_time=start_time,
                    context_window=context_window,
                    bridge_result=bridge_result,
                    prefetched_memory=prefetched_memory,
                    chain_results=chain_results,
                    prepare_only=True,
                    context_items=context_items,
                    trace=trace,
                )
            except Exception as e:
                logger.error(
                    f"[PREPARE-CONCURRENT] handler error for {msg.correlation_id}: {e}",
                    exc_info=True,
                )
                # Best-effort typed-error emission so the HTTP caller's
                # wait_for resolves with a structured failure rather than
                # the client timing out at 30 s.
                try:
                    await self.send_to_engine("generation_complete", {
                        "text": "",
                        "correlation_id": msg.correlation_id,
                        "prepare_only": True,
                        "bundle": {},
                        "diagnostics": {},
                        "error": True,
                        "error_type": type(e).__name__,
                        "error_message": str(e) or repr(e),
                    })
                except Exception:
                    pass
            finally:
                self._prepare_in_flight = max(0, self._prepare_in_flight - 1)

    def _maybe_run_director_shadow(
        self,
        *,
        endpoint: Optional[str],
        query: str,
        legacy_should_delegate: bool,
    ) -> Optional[dict]:
        """Run the Director pivot shadow loop when gated on.

        Phase B: telemetry-only. Returns a dict suitable for surfacing on the
        generation-complete data dict, or None when the pivot is not enabled
        for this endpoint/mode. Never raises; never mutates runtime state.
        """
        if not endpoint:
            return None
        try:
            cfg = PivotConfig.load()
            if not is_pivot_enabled(endpoint, mode=cfg.mode, config=cfg):
                return None
            if cfg.mode != "shadow":
                # active-mode wiring belongs to Phase C, not Phase B
                return None
            packet = run_shadow_pass_loop(query)
            legacy_route = "delegated" if legacy_should_delegate else "local"
            legacy_backend = "delegate" if legacy_should_delegate else "local"
            comparison = ShadowComparison(
                endpoint=endpoint,
                legacy_route=legacy_route,
                pivot_route=packet.route.path,
                legacy_backend=legacy_backend,
                pivot_backend=packet.route.backend,
                matched=(
                    legacy_route == packet.route.path
                    and legacy_backend == packet.route.backend
                ),
            )
            shadow_payload = {
                "mode": "shadow",
                "endpoint": endpoint,
                "stage_packet": packet.to_dict(),
                "comparison": comparison.to_dict(),
                "mutated_runtime": False,
            }
            logger.info(
                "[DIRECTOR-PIVOT] shadow endpoint=%s matched=%s legacy=%s/%s shadow=%s/%s warning=%s",
                endpoint,
                comparison.matched,
                legacy_route,
                legacy_backend,
                packet.route.path,
                packet.route.backend,
                packet.warning,
            )
            return shadow_payload
        except Exception as exc:
            logger.warning("[DIRECTOR-PIVOT] shadow failed (suppressed): %s", exc)
            return {
                "mode": "shadow",
                "endpoint": endpoint,
                "error": str(exc),
                "mutated_runtime": False,
            }

    # Phase C: route/backend pairs that map cleanly to existing generation paths.
    # Other pairs fall back to legacy per Spec Section 6.
    _ACTIVE_ROUTE_TABLE = {
        ("agentic", "delegate"): True,   # active → should_delegate=True
        ("direct", "local"): False,      # active → should_delegate=False (gated on local_available)
    }

    def _validate_active_stage_packet(self, packet) -> Optional[str]:
        """Return None if the packet is valid for active runtime use, else a fallback_reason.

        Mirrors Spec Section 4 conditions 3–7 plus Section 6 route-mapping gates.
        Order matches the spec: shape → warning → READY → minted_prompt → route.
        """
        if not isinstance(packet, StagePacket):
            return "invalid_packet"
        if packet.warning is not None:
            return packet.warning  # e.g. "pass_ceiling", "degraded_fallback"
        signals = packet.trace.plan_signals if packet.trace else []
        if not signals:
            return "not_ready"
        last = signals[-1]
        last_status = last.get("status") if isinstance(last, dict) else getattr(last, "status", None)
        if last_status != "READY":
            return "not_ready"
        if not packet.minted_prompt:
            return "empty_minted_prompt"
        pair = (packet.route.path, packet.route.backend)
        if pair not in self._ACTIVE_ROUTE_TABLE:
            return "unsupported_route"
        if pair == ("direct", "local") and not self.local_available:
            return "local_unavailable"
        return None

    def _maybe_run_director_active(
        self,
        *,
        endpoint: Optional[str],
        query: str,
        legacy_should_delegate: bool,
        legacy_system_prompt: str,
    ) -> Optional[dict]:
        """Run the Director pivot active path when gated on.

        Phase C: only `/message` is allowlisted in default config. Returns None
        when the pivot is not active for this endpoint (caller continues legacy
        unchanged). Otherwise returns:

            {
                "applied": bool,
                "should_delegate": bool | None,
                "system_prompt": str | None,
                "debug": dict,                # surfaced as director_pivot_active
            }

        With Phase A's stubbed Lexicon roles, validation always rejects (empty
        minted_prompt or pass_ceiling) and the caller falls back to legacy —
        this is the spec-mandated Phase C behavior.
        """
        if not endpoint:
            return None

        try:
            cfg = PivotConfig.load()
            if not is_pivot_enabled(endpoint, mode=cfg.mode, config=cfg):
                return None
            if cfg.mode != "active":
                # shadow-mode wiring belongs to _maybe_run_director_shadow
                return None

            packet = build_active_message_stage_packet(
                query=query,
                minted_prompt=legacy_system_prompt,
                legacy_should_delegate=legacy_should_delegate,
                local_available=self.local_available,
            )
            reason = self._validate_active_stage_packet(packet)

            stage_packet_dict = packet.to_dict() if isinstance(packet, StagePacket) else None
            legacy_route_dbg = {"should_delegate": bool(legacy_should_delegate)}

            if reason is not None:
                debug = {
                    "mode": "active",
                    "endpoint": endpoint,
                    "applied": False,
                    "fallback_triggered": True,
                    "fallback_reason": reason,
                    "stage_packet": stage_packet_dict,
                    "legacy_route": legacy_route_dbg,
                    "active_route": None,
                    "mutated_runtime": False,
                }
                logger.warning(
                    "[DIRECTOR-PIVOT] active fallback endpoint=%s reason=%s",
                    endpoint,
                    reason,
                )
                return {
                    "applied": False,
                    "should_delegate": bool(legacy_should_delegate),
                    "system_prompt": legacy_system_prompt,
                    "debug": debug,
                }

            pair = (packet.route.path, packet.route.backend)
            active_should_delegate = self._ACTIVE_ROUTE_TABLE[pair]
            debug = {
                "mode": "active",
                "endpoint": endpoint,
                "applied": True,
                "fallback_triggered": False,
                "fallback_reason": None,
                "stage_packet": stage_packet_dict,
                "legacy_route": legacy_route_dbg,
                "active_route": {"should_delegate": active_should_delegate},
                "mutated_runtime": True,
            }
            logger.info(
                "[DIRECTOR-PIVOT] active applied endpoint=%s route=%s/%s",
                endpoint,
                pair[0],
                pair[1],
            )
            return {
                "applied": True,
                "should_delegate": active_should_delegate,
                "system_prompt": packet.minted_prompt,
                "debug": debug,
            }

        except Exception as exc:
            reason = f"active_helper_exception:{type(exc).__name__}"
            logger.warning("[DIRECTOR-PIVOT] active fallback endpoint=%s reason=%s", endpoint, reason)
            return {
                "applied": False,
                "should_delegate": bool(legacy_should_delegate),
                "system_prompt": legacy_system_prompt,
                "debug": {
                    "mode": "active",
                    "endpoint": endpoint,
                    "applied": False,
                    "fallback_triggered": True,
                    "fallback_reason": reason,
                    "stage_packet": None,
                    "legacy_route": {"should_delegate": bool(legacy_should_delegate)},
                    "active_route": None,
                    "mutated_runtime": False,
                },
            }

    async def _should_delegate(self, user_message: str) -> bool:
        """
        Planning step: Decide if this query should be delegated to fallback chain.

        Routes based on intent classification + complexity:
        - CHAT / REFLECT with low complexity → local Qwen (fast, personality-driven)
        - ASSIST / RECALL or high complexity → delegated (Haiku / fallback chain)
        """
        query_preview = user_message[:50] + "..." if len(user_message) > 50 else user_message

        # Estimate complexity
        complexity = 0.0
        if hasattr(self, '_hybrid') and self._hybrid is not None:
            complexity = self._hybrid.estimate_complexity(user_message)

        # Check intent — casual chat and reflection stay local
        intent = getattr(self, '_last_intent', None)
        # If intent was never classified for this call path, delegate to be safe
        if intent is None:
            logger.info(f"🔀 DELEGATE: '{query_preview}' (no intent classified, safe default)")
            return True
        intent_mode = getattr(intent, 'mode', None)
        mode_val = intent_mode.value if intent_mode and hasattr(intent_mode, 'value') else "CHAT"

        # Local-first: casual chat and reflection with low complexity stay on Qwen
        threshold = 0.35
        if hasattr(self, '_hybrid') and self._hybrid is not None:
            threshold = getattr(self._hybrid, '_complexity_threshold', 0.35)

        if mode_val in ("CHAT", "REFLECT") and complexity < threshold:
            logger.info(f"🔀 LOCAL: '{query_preview}' (mode={mode_val}, complexity={complexity:.2f} < {threshold})")
            return False

        # Everything else delegates
        logger.info(f"🔀 DELEGATE: '{query_preview}' (mode={mode_val}, complexity={complexity:.2f} >= {threshold})")
        return True

    def _check_delegation_signals(self, user_message: str) -> bool:
        """
        Check for explicit signals that require delegation.

        These are things local Qwen definitely can't handle well.
        """
        msg_lower = user_message.lower()

        # Temporal markers (current events)
        temporal = ["latest", "current", "recent", "today", "yesterday",
                    "this week", "this month", "right now", "2025", "2026"]
        for t in temporal:
            if t in msg_lower:
                logger.debug(f"  → Temporal signal: '{t}'")
                return True

        # Explicit research requests
        research = ["search for", "look up", "find out", "research",
                    "what's happening with", "news about"]
        for r in research:
            if r in msg_lower:
                logger.debug(f"  → Research signal: '{r}'")
                return True

        # Complex code generation
        code = ["write a script", "implement", "build a", "create a program",
                "debug this", "fix this code"]
        for c in code:
            if c in msg_lower:
                logger.debug(f"  → Code signal: '{c}'")
                return True

        # Memory/introspection queries (need actual memory data)
        # Note: "tell me about yourself" is a personality query (local), NOT a memory query
        # EXPANDED: These patterns MUST delegate because local model can't access memory
        memory = [
            # Explicit memory keywords
            "your memory", "your memories", "memory matrix",
            "what do you remember", "do you remember", "can you remember",
            "recall when", "recall that", "recollect",
            # Knowledge queries that need memory lookup
            "in-depth", "indepth", "overview",
            "tell me about our", "tell me about the",
            "what do you know about", "do you know about",
            "who is", "who was", "what is marzipan",  # Named entity queries
            # Backward reference patterns
            "earlier", "before", "last time", "yesterday",
            "you mentioned", "you said", "we talked", "we discussed",
            # Relational memory
            "about us", "our relationship", "how we", "what we",
        ]
        for m in memory:
            if m in msg_lower:
                logger.debug(f"  → Memory signal: '{m}'")
                return True

        return False

    def _classify_intent(
        self,
        message: str,
        conversation_history: list,
    ) -> IntentClassification:
        """
        Classify user intent into a ResponseMode.

        Runs BEFORE routing. The mode is injected into the prompt
        regardless of which inference backend handles generation.

        Classification priority:
            1. Continuation detection (short follow-ups inherit previous mode)
            2. Explicit RECALL signals (memory/past event queries)
            3. Explicit REFLECT signals (feelings/opinions)
            4. Explicit ASSIST signals (task/help requests)
            5. Default to CHAT
        """
        msg = message.strip()
        msg_lower = msg.lower()
        signals = []

        # ── 1. Continuation Detection ──────────────────────────────
        if len(msg) < 30 and conversation_history:
            continuation_triggers = [
                "keep going", "more", "continue", "go on", "and?",
                "what else", "tell me more", "yeah", "yes", "mhm",
                "ok", "okay", "right", "sure", "cool", ":)", "👀",
                "interesting", "huh", "wow", "really", "no way",
            ]
            if any(t in msg_lower for t in continuation_triggers):
                prev_mode = self._last_classified_mode
                return IntentClassification(
                    mode=prev_mode,
                    confidence=0.9,
                    signals=["continuation_detected"],
                    is_continuation=True,
                    previous_mode=prev_mode,
                )

        # ── 2. RECALL signals ──────────────────────────────────────
        recall_patterns = [
            "remember", "recall", "memory", "memories",
            "what do you know about", "do you know about",
            "tell me about our", "tell me about the",
            "who is", "who was", "what was",
            "earlier", "before", "last time",
            "you mentioned", "you said", "we talked", "we discussed",
            "about us", "our relationship", "how we", "what we",
            "most special", "favorite memory", "best memory",
            "what have i been", "what have we been",
            "working on", "been up to",
        ]
        for p in recall_patterns:
            if p in msg_lower:
                signals.append(f"recall_keyword:{p}")

        if signals:
            mode = ResponseMode.RECALL
            self._last_classified_mode = mode
            return IntentClassification(
                mode=mode,
                confidence=0.85,
                signals=signals,
                is_continuation=False,
                previous_mode=self._last_classified_mode,
            )

        # ── 3. REFLECT signals ─────────────────────────────────────
        reflect_patterns = [
            "how do you feel", "what do you think",
            "your opinion", "your perspective", "your take",
            "do you like", "do you enjoy", "do you want",
            "are you happy", "are you sad", "are you okay",
            "how are you", "how's it going", "how are things",
            "what matters to you", "what's important",
        ]
        for p in reflect_patterns:
            if p in msg_lower:
                signals.append(f"reflect_keyword:{p}")

        if signals:
            mode = ResponseMode.REFLECT
            self._last_classified_mode = mode
            return IntentClassification(
                mode=mode,
                confidence=0.8,
                signals=signals,
                is_continuation=False,
                previous_mode=self._last_classified_mode,
            )

        # ── 4. ASSIST signals ──────────────────────────────────────
        assist_patterns = [
            "help me", "can you", "could you", "please",
            "how do i", "how to", "show me", "explain",
            "write", "create", "build", "implement", "fix",
            "search for", "look up", "find",
            "debug", "analyze", "compare",
        ]
        for p in assist_patterns:
            if p in msg_lower:
                signals.append(f"assist_keyword:{p}")

        if signals:
            mode = ResponseMode.ASSIST
            self._last_classified_mode = mode
            return IntentClassification(
                mode=mode,
                confidence=0.75,
                signals=signals,
                is_continuation=False,
                previous_mode=self._last_classified_mode,
            )

        # ── 5. Default: CHAT ───────────────────────────────────────
        mode = ResponseMode.CHAT
        self._last_classified_mode = mode
        return IntentClassification(
            mode=mode,
            confidence=0.7,
            signals=["default"],
            is_continuation=False,
            previous_mode=self._last_classified_mode,
        )

    async def _generate_local_only(
        self,
        user_message: str,
        system_prompt: str,
        max_tokens: int,
        correlation_id: str,
        start_time: float,
        context_window: str = "",
        bridge_result=None,
    ) -> None:
        """
        Pure local generation - no delegation detection needed.

        Used when planning step already decided this is a local query.

        Phase 3 Fix: Uses unified ContextPipeline when available for
        identical context to delegated path (entity detection, temporal
        framing, ring buffer history).
        """
        response_buffer = ""
        token_count = 0
        print(f"\n🏠 [LOCAL] Starting for: '{user_message[:50]}...'")

        # ── PromptAssembler: single funnel for local streaming path ──
        # ContextPipeline takes priority when available (it handles entity detection inline)
        if self._context_pipeline is not None:
            try:
                # Bridge Engine→Director gap: populate ring from context_window if empty
                if len(self._context_pipeline._ring) == 0 and context_window:
                    logger.info("[LOCAL-PIPELINE] Ring empty, populating from context_window")
                    self._populate_ring_from_context(
                        self._context_pipeline._ring,
                        context_window
                    )

                packet = await self._context_pipeline.build(user_message)
                full_system_prompt = packet.system_prompt
                # Inject ACCESS block — pipeline skips the assembler so we must add it here
                from luna.context.assembler import PromptRequest
                _access_block = self._assembler._build_access_block(
                    PromptRequest(message=user_message, bridge_result=bridge_result)
                )
                if _access_block:
                    full_system_prompt = full_system_prompt + "\n\n" + _access_block
                _voice_block = self._generate_voice_block(
                    user_message,
                    [],
                    "",
                    [],
                )
                pipeline_voice_injected = bool(_voice_block and _voice_block.strip())
                if pipeline_voice_injected:
                    full_system_prompt = full_system_prompt + "\n\n" + _voice_block.strip()
                print(f"✓ [LOCAL] Context pipeline: ring={packet.ring_size}, entities={len(packet.entities)}")
                self._last_system_prompt = full_system_prompt
                self._last_route_decision = "local"
                self._last_prompt_meta = {
                    "identity_source": "pipeline",
                    "memory_source": "pipeline" if packet.used_retrieval else "none",
                    "voice_injected": pipeline_voice_injected,
                    "register_injected": False,
                    "register_active": None,
                    "context_items_injected": False,
                    "prompt_tokens": len(full_system_prompt) // 4,
                    "response_mode": "standard",
                }
            except Exception as e:
                logger.warning(f"[LOCAL] Pipeline failed, using assembler: {e}")
                assembler_result = await self._assembler.build(PromptRequest(
                    message=user_message,
                    conversation_history=[],
                    route="local",
                    auto_fetch_memory=True,
                    bridge_result=bridge_result,
                    memory_graph_state=getattr(self, "_current_memory_graph_state", None),
                ))
                full_system_prompt = assembler_result.system_prompt
                self._last_system_prompt = full_system_prompt
                self._last_route_decision = "local"
                self._last_prompt_meta = assembler_result.to_dict()
        else:
            # No pipeline — use assembler (records to standalone ring)
            self._standalone_ring.add_user(user_message)
            logger.debug("[LOCAL-RING] Recorded user message to standalone ring (size: %d)", len(self._standalone_ring))
            assembler_result = await self._assembler.build(PromptRequest(
                message=user_message,
                conversation_history=[],
                route="local",
                auto_fetch_memory=True,
                bridge_result=bridge_result,
                memory_graph_state=getattr(self, "_current_memory_graph_state", None),
            ))
            full_system_prompt = assembler_result.system_prompt
            self._last_system_prompt = full_system_prompt
            self._last_route_decision = "local"
            self._last_prompt_meta = assembler_result.to_dict()

        # Expose auto-fetched memory for GroundingLink (local path)
        memory_text = getattr(self, '_last_fetched_memory_text', '') or ''
        if memory_text:
            import re as _re
            grounding_nodes = []
            for m in _re.finditer(r'<memory[^>]*>(.*?)</memory>', memory_text, _re.DOTALL):
                grounding_nodes.append({
                    "id": f"memory-{len(grounding_nodes)}",
                    "content": m.group(1).strip(),
                    "node_type": "FACT",
                })
            if not grounding_nodes and len(memory_text) > 20:
                grounding_nodes.append({
                    "id": "fetched-context",
                    "content": memory_text,
                    "node_type": "context",
                })
            self._last_injected_memories = grounding_nodes + getattr(self, '_nexus_nodes', [])
            logger.info(f"[GROUNDING] Local path: exposed {len(grounding_nodes)} memory + {len(getattr(self, '_nexus_nodes', []))} nexus nodes")

        try:
            print(f"📡 [LOCAL] Generating with Qwen 3B...")
            async for token in self._local.generate_stream(
                user_message,
                system_prompt=full_system_prompt,
                max_tokens=max_tokens,
            ):
                if self._abort_requested:
                    print(f"⚠ [LOCAL] Aborted!")
                    logger.info("Director: Generation aborted")
                    break

                response_buffer += token
                token_count += 1
                await self._stream_to_callbacks(token)

            elapsed_ms = (time.time() - start_time) * 1000
            self._local_generations += 1
            print(f"✓ [LOCAL] Complete: {token_count} tokens in {elapsed_ms:.0f}ms")

            logger.info(f"Director local: {token_count} tokens in {elapsed_ms:.0f}ms")

            # Record response in ring buffer (CRITICAL for history)
            if response_buffer:
                response_buffer = self._strip_trailing_question(response_buffer)
                self._active_ring.add_assistant(response_buffer)
                logger.debug("[LOCAL-RING] Recorded response to ring buffer (size: %d)", len(self._active_ring))

            # Record response for LunaScript trait measurement on next turn
            if self._lunascript and response_buffer:
                self._lunascript.last_luna_response = response_buffer

            # ── LunaScript local metadata ──
            _ls_meta = None
            if self._lunascript:
                try:
                    from luna.lunascript.signature import derive_glyph
                    _tv = {}
                    if self._lunascript._last_measurement:
                        _tv = {n: s.value for n, s in self._lunascript._last_measurement.traits.items()}
                    _ls_meta = {
                        "glyph": derive_glyph({"position": self._lunascript._prev_position, "trait_vector": _tv}),
                        "position": self._lunascript._prev_position,
                    }
                except Exception as e:
                    logger.debug(f"[LUNASCRIPT] local meta failed: {e}")

            # Extract interactive options from local response
            from luna.utils.options_parser import extract_options, build_options_widget
            response_buffer, _parsed_opts = extract_options(response_buffer)
            if _parsed_opts:
                self._pending_skill_widget = build_options_widget(_parsed_opts)

            self._apply_voice_pass(response_buffer, source="local_only")
            await self.send_to_engine("generation_complete", {
                "text": response_buffer,
                "correlation_id": correlation_id,
                "model": "qwen-3b-local",
                "output_tokens": token_count,
                "latency_ms": elapsed_ms,
                "local": True,
                "planned": True,
                "access_denied_count": self._last_denied_count,
                "lunascript": _ls_meta,
                "widget": self._pending_skill_widget,
            })

        except Exception as e:
            print(f"❌ [LOCAL] Failed: {e}")
            logger.error(f"Director local generation error: {e}")
            await self.send_to_engine("generation_error", {
                "error": str(e),
                "correlation_id": correlation_id,
            })

    async def _emit_prepare_bundle(
        self,
        *,
        correlation_id: str,
        system_prompt: str,
        messages: list,
        memories: Any,
        framed_context: str,
        assembler_result: Any,
        route: str,
    ) -> None:
        """Emit the assembled context bundle for MCP consumers.

        Used by the prepare_only path — Director has finished assembly but skips
        inference. The bundle flows back through the engine's generation_complete
        handler, which checks the prepare_only flag and forwards to callbacks
        without running turn-side effects.
        """
        # Serialize memories into a plain string the caller can splice into a prompt.
        memory_context_str = ""
        try:
            if isinstance(memories, str):
                memory_context_str = memories
            elif isinstance(memories, list):
                parts = []
                for m in memories:
                    if isinstance(m, dict):
                        parts.append(str(m.get("content", "")))
                    elif hasattr(m, "content"):
                        parts.append(str(m.content))
                    else:
                        parts.append(str(m))
                memory_context_str = "\n\n".join(p for p in parts if p)
        except Exception:
            memory_context_str = ""

        prompt_tokens = 0
        assembler_meta: Dict[str, Any] = {}
        if assembler_result is not None:
            prompt_tokens = getattr(assembler_result, "prompt_tokens", 0) or 0
            try:
                assembler_meta = assembler_result.to_dict()
            except Exception:
                assembler_meta = {}

        diagnostics = self._build_prepare_diagnostics()

        bundle = {
            "system_prompt": system_prompt or "",
            "messages": messages or [],
            "memory_context": memory_context_str,
            "framed_context": framed_context or "",
            "prompt_tokens": prompt_tokens,
            "assembler_meta": assembler_meta,
            "route": route,
        }

        await self.send_to_engine("generation_complete", {
            "text": "",
            "correlation_id": correlation_id,
            "prepare_only": True,
            "bundle": bundle,
            "diagnostics": diagnostics,
            "model": f"prepare-only ({route})",
            "local": route == "local",
            "delegated": route == "delegated",
        })

    def _build_prepare_diagnostics(self) -> Dict[str, Any]:
        """Assemble observability metadata for a prepare_only response.

        Pulls ring distribution, budget usage, and eviction count from the
        engine's RevolvingContext. Retrieval-source signals come from the last
        assembly run (cached on the engine). Never raises — returns an empty
        scaffold on any failure so the bundle always has this key.
        """
        try:
            ctx_stats = self.engine.context.stats() if self.engine and hasattr(self.engine, "context") else {}
        except Exception:
            ctx_stats = {}

        rings = ctx_stats.get("rings", {}) or {}
        ring_distribution = {
            name: {"items": rings.get(name, {}).get("count", 0), "tokens": rings.get(name, {}).get("tokens", 0)}
            for name in ("CORE", "INNER", "MIDDLE", "OUTER")
        }
        budget = ctx_stats.get("token_budget", 0) or 0
        total_tokens = ctx_stats.get("total_tokens", 0) or 0
        tokens_free = max(0, budget - total_tokens) if budget else 0

        # Retrieval sources — infer from engine-side bookkeeping set during retrieval.
        nexus_nodes = getattr(self.engine, "_last_nexus_nodes", None) if self.engine else None
        collections = getattr(self.engine, "_last_collections_searched", None) if self.engine else None
        retrieval_sources = {
            "matrix": bool(getattr(self.engine, "_last_memory_source", "matrix") == "matrix"),
            "nexus_fts5": bool(nexus_nodes),
            "semantic": bool(collections),
            "extractions": bool(nexus_nodes),
        }

        # Phase 1A.3: surface partial-retrieval state explicitly so callers
        # never confuse "no context" with "skipped due to budget."
        completed_stages = list(getattr(self.engine, "_last_retrieval_completed_stages", []) or []) if self.engine else []
        truncated = bool(getattr(self.engine, "_last_retrieval_truncated", False)) if self.engine else False
        # Phase 1C: per-source candidate composition (pre-router produced,
        # post-router selected, chars). Makes source balance answerable over
        # HTTP without adding a bespoke harness per investigation.
        source_stats = dict(getattr(self.engine, "_last_retrieval_source_stats", {}) or {}) if self.engine else {}
        router_ran = bool(getattr(self.engine, "_last_retrieval_router_ran", False)) if self.engine else False

        return {
            "ring_distribution": ring_distribution,
            "budget_used_pct": round(float(ctx_stats.get("budget_used_pct", 0.0) or 0.0), 2),
            "tokens_free": tokens_free,
            "eviction_count": int(ctx_stats.get("total_evicted", 0) or 0),
            "retrieval_sources": retrieval_sources,
            "retrieval_completed_stages": completed_stages,
            "retrieval_truncated": truncated,
            "retrieval_source_stats": source_stats,
            "retrieval_router_ran": router_ran,
            "assembly_ms": 0,  # Endpoint stamps wall-clock over this before returning.
        }

    def _populate_ring_from_context(
        self,
        ring: "ConversationRing",
        context_window: str,
    ) -> None:
        """
        Populate a ring buffer from text context_window.

        This bridges the gap between Engine's RevolvingContext (text-based)
        and Director's ContextPipeline (structured ring buffer).

        Parses text like:
            User: message 1

            Luna: response 1

            User: message 2

        Into structured ring entries.
        """
        if not context_window:
            return

        # Parse context_window text into conversation turns
        # Skip the current message - build() will add that
        for line in context_window.split("\n\n"):
            line = line.strip()
            if line.startswith("User:"):
                content = line[5:].strip()
                if content:
                    ring.add_user(content)
            elif line.startswith("Luna:"):
                content = line[5:].strip()
                if content:
                    ring.add_assistant(content)

        logger.debug(
            "[CONTEXT-BRIDGE] Parsed %d turns from context_window (%d chars)",
            len(ring),
            len(context_window)
        )

    async def _build_local_context_fallback(
        self,
        user_message: str,
        system_prompt: str,
        context_window: str,
    ) -> str:
        """
        Build context for local generation using legacy method.

        This is the fallback when ContextPipeline is not available.
        Uses ring buffer for history (if available), then emergent prompt.
        """
        # Prefer ring buffer for conversation history (structural guarantee)
        ring_history = ""
        conversation_history = []
        if len(self._standalone_ring) > 0:
            ring_history = self._standalone_ring.format_for_prompt()
            conversation_history = self._standalone_ring.get_as_dicts()
            logger.debug("[LOCAL-FALLBACK] Using ring buffer for history (%d turns)", len(self._standalone_ring))
        elif context_window:
            # Fall back to text parsing only if ring is empty
            ring_history = context_window
            for line in context_window.split("\n\n"):
                line = line.strip()
                if line.startswith("User:"):
                    conversation_history.append({"role": "user", "content": line[5:].strip()})
                elif line.startswith("Luna:"):
                    conversation_history.append({"role": "assistant", "content": line[5:].strip()})
            logger.debug("[LOCAL-FALLBACK] Using text parsing for history (ring empty)")

        # Load emergent prompt for local generation (three-layer personality)
        identity_context = await self._load_emergent_prompt(
            query=user_message,
            conversation_history=conversation_history
        )
        if identity_context:
            print(f"✓ [LOCAL] Emergent prompt loaded ({len(identity_context)} chars)")
        else:
            # Fallback to basic identity buffer
            identity_context = await self._load_identity_buffer()
            if identity_context:
                print(f"✓ [LOCAL] Identity buffer loaded ({len(identity_context)} chars)")
            else:
                # CRITICAL: Use robust fallback personality
                # This ensures Luna ALWAYS has her identity, even if entity context fails
                # See: HANDOFF_CONTEXT_STARVATION.md
                identity_context = self.FALLBACK_PERSONALITY
                print(f"⚠ [LOCAL] Using FALLBACK_PERSONALITY ({len(identity_context)} chars)")
                logger.warning("[CONTEXT-FIX] Using FALLBACK_PERSONALITY in local path")

        # Build full system prompt with emergent personality context
        full_system_prompt = system_prompt

        # Add emergent personality context (DNA + Experience + Mood layers)
        if identity_context:
            full_system_prompt = f"""{full_system_prompt}

{identity_context}"""

        # CRITICAL: Fetch memory context for local generation too!
        # This enables Luna to remember people, places, and past conversations
        memory_context = await self._fetch_memory_context(user_message, max_tokens=1000)
        if memory_context:
            print(f"✓ [LOCAL] Memory context loaded ({len(memory_context)} chars)")
            full_system_prompt = f"""{full_system_prompt}

## Your Memories
The following are relevant memories from your memory matrix. Use these to provide personal, grounded responses:

{memory_context}

Reference these memories naturally as your own experiences and knowledge."""

        # Add conversation context from ring buffer (or text fallback)
        if ring_history:
            full_system_prompt = f"""{full_system_prompt}

## Recent Conversation
{ring_history}

Continue the conversation naturally, maintaining context from above."""

        return full_system_prompt

    async def _resolve_bridge(self):
        """
        Resolve the access bridge for the current speaker (FaceID).

        Returns BridgeResult or None. Cached per-message — call freely.
        Uses IdentityActor.current to get entity_id, then looks up the
        access_bridge table in the engine DB.

        Fallback: if DB lookup fails or returns None but identity is present,
        construct a BridgeResult from the IdentityActor's own state (set by
        bypass or FaceID recognition).
        """
        if not self.engine:
            return None
        identity_actor = self.engine.get_actor("identity")
        if not identity_actor or not identity_actor.current.is_present:
            return None
        entity_id = identity_actor.current.entity_id
        if not entity_id:
            return None
        try:
            from luna.identity.bridge import AccessBridge, BridgeResult
            matrix_actor = self.engine.get_actor("matrix")
            if matrix_actor and matrix_actor.is_ready:
                db = matrix_actor._matrix.db
                bridge = AccessBridge(db)
                result = await bridge.lookup(entity_id)
                if result:
                    return result
            # Fallback: construct BridgeResult from IdentityActor state
            # This covers bypass mode and entity_id mismatches between DBs
            current = identity_actor.current
            logger.info(
                "Bridge DB lookup returned None for %s, using IdentityActor state "
                "(tier=%s, dr_tier=%s)",
                entity_id, current.luna_tier, current.dataroom_tier,
            )
            return BridgeResult(
                entity_id=entity_id,
                luna_tier=current.luna_tier,
                dataroom_tier=current.dataroom_tier,
                dataroom_categories=getattr(current, 'dataroom_categories', []),
            )
        except Exception as e:
            logger.warning("Bridge resolution failed: %s", e)
            return None

    async def _fetch_memory_context(self, query: str, max_tokens: int = 1500) -> str:
        """
        Fetch relevant memory context for a query.

        UPDATED: Uses Memory Economy constellation assembly when available for
        cluster-aware retrieval with lock-in prioritization.
        Also sets self._last_memory_confidence for L1 prompt control.
        """
        from luna.context.assembler import MemoryConfidence

        if not self.engine:
            logger.debug("Memory fetch: No engine available")
            self._last_memory_confidence = MemoryConfidence(
                match_count=0, relevant_count=0, avg_similarity=0.0,
                best_lock_in="none", has_entity_match=False, query=query,
            )
            return ""

        matrix = self.engine.get_actor("matrix")
        if not matrix:
            logger.warning("Memory fetch: Matrix actor not found")
            self._last_memory_confidence = MemoryConfidence(
                match_count=0, relevant_count=0, avg_similarity=0.0,
                best_lock_in="none", has_entity_match=False, query=query,
            )
            return ""

        if not matrix.is_ready:
            logger.warning(f"Memory fetch: Matrix not ready (state: {getattr(matrix, '_state', 'unknown')})")
            self._last_memory_confidence = MemoryConfidence(
                match_count=0, relevant_count=0, avg_similarity=0.0,
                best_lock_in="none", has_entity_match=False, query=query,
            )
            return ""

        logger.debug(f"Memory fetch: Searching for query '{query[:50]}...'")

        # Get active scopes from engine for project isolation
        active_scopes = getattr(self.engine, 'active_scopes', ["global"])
        active_project = getattr(self.engine, 'active_project', None)

        try:
            # Try Memory Economy constellation assembly first (cluster-aware retrieval)
            constellation_context = await self._fetch_constellation_context(query, max_tokens, matrix)
            if constellation_context:
                # Constellation retrieval implies high-quality matches
                self._last_memory_confidence = MemoryConfidence(
                    match_count=5, relevant_count=3, avg_similarity=0.0,
                    best_lock_in="settled", has_entity_match=True, query=query,
                )
                self._last_fetched_memory_text = constellation_context
                return constellation_context

            # Constellation prefetch: inject compiled PERSON_BRIEFING /
            # PROJECT_STATUS / GOVERNANCE_RECORD before general search
            constellation_prefix = ""
            remaining_budget = max_tokens
            try:
                prefetch = await self._constellation_prefetch(query, active_scopes)
                if prefetch and prefetch.nodes:
                    parts = []
                    for node in prefetch.nodes:
                        parts.append(
                            f"<memory type=\"{node.node_type}\" "
                            f"lock_in=\"{getattr(node, 'lock_in', 0.8):.2f}\" "
                            f"source=\"compiled\">\n{_sanitize_memory_content(node.content)}\n</memory>"
                        )
                    constellation_prefix = "\n\n".join(parts)
                    remaining_budget = max(max_tokens - prefetch.tokens_used, 500)
                    logger.info(
                        f"Memory fetch: Constellation prefetch injected "
                        f"{len(prefetch.nodes)} nodes (~{prefetch.tokens_used} tok), "
                        f"remaining budget={remaining_budget}"
                    )
            except Exception as e:
                logger.debug(f"Memory fetch: Constellation prefetch failed: {e}")

            # Try get_context if available (with scope awareness)
            if hasattr(matrix, 'get_context'):
                logger.debug(f"Memory fetch: Using matrix.get_context() scopes={active_scopes}")
                context = await matrix.get_context(
                    query=query, max_tokens=remaining_budget,
                    scopes=active_scopes if len(active_scopes) > 1 else None,
                    scope=active_scopes[0] if len(active_scopes) == 1 else None,
                )
                # Prepend constellation context before general results
                if constellation_prefix:
                    context = constellation_prefix + ("\n\n" + context if context else "")
                if context:
                    # Permission gate: strip DOCUMENT blocks from pre-formatted string
                    bridge_result = await self._resolve_bridge()
                    if bridge_result is None or not bridge_result.can_see_all:
                        import re
                        context = re.sub(
                            r"<memory\s+type=['\"](?:DOCUMENT|document)['\"][^>]*>.*?</memory>",
                            "", context, flags=re.DOTALL,
                        ).strip()
                        if not context:
                            logger.info("Memory fetch: all content was DOCUMENT, stripped by permission gate")
                    # Prepend project header if active
                    if active_project:
                        context = f"[Active Project: {active_project}]\n\n{context}"
                    if context.strip():
                        logger.info(f"Memory fetch: Found context ({len(context)} chars, scopes={active_scopes})")
                        self._last_memory_confidence = MemoryConfidence(
                            match_count=1, relevant_count=1, avg_similarity=0.0,
                            best_lock_in="fluid", has_entity_match=False, query=query,
                        )
                        self._last_fetched_memory_text = context
                        return context
                else:
                    logger.debug("Memory fetch: get_context returned empty")

            # Fallback to direct search
            result = await self._fetch_basic_memory_context(query, max_tokens, matrix)
            if result:
                self._last_memory_confidence = MemoryConfidence(
                    match_count=1, relevant_count=1, avg_similarity=0.0,
                    best_lock_in="drifting", has_entity_match=False, query=query,
                )
                self._last_fetched_memory_text = result
            else:
                self._last_memory_confidence = MemoryConfidence(
                    match_count=0, relevant_count=0, avg_similarity=0.0,
                    best_lock_in="none", has_entity_match=False, query=query,
                )
            return result

        except Exception as e:
            logger.error(f"Memory fetch failed: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"Memory fetch traceback: {traceback.format_exc()}")
            self._last_memory_confidence = MemoryConfidence(
                match_count=0, relevant_count=0, avg_similarity=0.0,
                best_lock_in="none", has_entity_match=False, query=query,
            )
            return ""

    async def _fetch_hub_context(self, query: str, max_tokens: int = 600) -> str:
        """Fetch Intergalactic Hub context (Drive docs etc.) for this query. Non-blocking."""
        try:
            import os
            import sys
            base = os.environ.get("LUNA_BASE_PATH", "")
            if base and base not in sys.path:
                sys.path.insert(0, base)
            from intergalactic_hub.integration.luna_bridge import hub_retrieval_for_luna
        except ImportError:
            return ""
        try:
            items = await hub_retrieval_for_luna(query, budget_tokens=max_tokens, timeout_ms=2000)
            if not items:
                return ""
            parts = [
                f"<memory type=\"DOCUMENT\" lock_in=\"{item['lock_in']:.2f}\" "
                f"source=\"{item['source']}\" provenance=\"intergalactic_hub\">"
                f"\n{item['content']}\n</memory>"
                for item in items
            ]
            return "\n\n".join(parts)
        except Exception as exc:
            logger.debug("Hub context fetch failed (non-fatal): %s", exc)
            return ""

    async def _constellation_prefetch(self, query: str, scopes: list[str]):
        """
        Pre-fetch compiled constellation nodes (PERSON_BRIEFING, etc.)
        before general Matrix search. Lazy-initializes on first call.
        """
        if not hasattr(self, '_prefetcher'):
            self._prefetcher = None
        if self._prefetcher is None:
            try:
                from luna.compiler.constellation_prefetch import ConstellationPrefetch
                from luna.compiler.entity_index import EntityIndex
                matrix = self.engine.get_actor("matrix")
                if not matrix or not matrix.is_ready:
                    return None
                # Load entity index from guardian data
                idx = EntityIndex()
                from pathlib import Path
                entities_path = local_dir() / "guardian" / "entities" / "entities_updated.json"
                if entities_path.exists():
                    idx.load_entities(entities_path)
                else:
                    return None
                self._prefetcher = ConstellationPrefetch(matrix, idx)
            except Exception as e:
                logger.debug(f"Constellation prefetcher init failed: {e}")
                return None

        # Use project scope if available
        scope = None
        for s in scopes:
            if s.startswith("project:"):
                scope = s
                break

        return await self._prefetcher.prefetch(query, scope=scope)

    async def _fetch_constellation_context(
        self,
        query: str,
        max_tokens: int,
        matrix
    ) -> str:
        """
        Fetch context using Memory Economy constellation assembly.

        This provides cluster-aware retrieval with:
        - Relevant clusters activated together
        - Lock-in prioritized node selection
        - Token-budgeted assembly
        """
        try:
            from luna.librarian.cluster_retrieval import ClusterRetrieval
            from luna.memory.constellation import ConstellationAssembler

            db_path = matrix._matrix.db_path
            if not db_path:
                logger.debug("Constellation fetch: No db_path available")
                return ""

            # Step 1: Get relevant nodes
            nodes = await matrix._matrix.search_nodes(query=query, limit=20)
            node_ids = [n.id for n in nodes] if nodes else []

            if not node_ids:
                logger.debug("Constellation fetch: No nodes found")
                return ""

            # Step 2: Find relevant clusters
            retrieval = ClusterRetrieval(db_path)
            cluster_results = retrieval.find_relevant_clusters(node_ids=node_ids, top_k=5)

            # Also include auto-activated clusters (high lock-in)
            auto_clusters = retrieval.get_auto_activated_clusters()
            seen_ids = {c.cluster_id for c, _ in cluster_results}
            for cluster in auto_clusters:
                if cluster.cluster_id not in seen_ids:
                    cluster_results.append((cluster, cluster.lock_in))

            # Step 3: Assemble constellation
            assembler = ConstellationAssembler(max_tokens=max_tokens)

            cluster_dicts = [{"cluster": c, "score": score} for c, score in cluster_results]
            node_dicts = [
                {"node_id": n.id, "id": n.id, "content": n.content, "node_type": n.node_type, "lock_in": getattr(n, 'lock_in', 0.5)}
                for n in nodes
            ]

            # Permission gate: strip DOCUMENT nodes the speaker can't see
            from luna.identity.permissions import gate_content
            bridge_result = await self._resolve_bridge()
            node_dicts, _denied = await gate_content(node_dicts, bridge_result, source="constellation")

            constellation = assembler.assemble(
                clusters=cluster_dicts,
                nodes=node_dicts,
                prioritize_clusters=True
            )

            # Step 4: Format for prompt
            context_parts = []

            # Add cluster context section if clusters were activated
            if constellation.clusters:
                context_parts.append("=== ACTIVE MEMORY CLUSTERS ===")
                for cluster_data in constellation.clusters:
                    cluster = cluster_data.get("cluster") or cluster_data
                    name = getattr(cluster, 'name', None) or cluster.get('name', 'Unknown')
                    state = getattr(cluster, 'state', None) or cluster.get('state', 'unknown')
                    lock_in_val = getattr(cluster, 'lock_in', None) or cluster.get('lock_in', 0)
                    context_parts.append(f"<cluster name='{name}' state='{state}' lock_in='{lock_in_val:.2f}'/>")
                context_parts.append("")

            # Add node context
            for node_data in constellation.nodes:
                node_type = node_data.get("node_type", "memory")
                content = node_data.get("content", "")
                lock_in_val = node_data.get("lock_in", 0.5)
                context_parts.append(
                    f"<memory type='{node_type}' lock_in='{lock_in_val:.2f}'>\n{content}\n</memory>"
                )

            result = "\n\n".join(context_parts)
            logger.info(
                f"Constellation context: {len(constellation.clusters)} clusters, "
                f"{len(constellation.nodes)} nodes, ~{constellation.total_tokens} tokens"
            )

            return result

        except ImportError:
            logger.debug("Memory Economy not available, skipping constellation fetch")
            return ""
        except Exception as e:
            logger.debug(f"Constellation fetch failed: {e}")
            return ""

    async def _fetch_basic_memory_context(self, query: str, max_tokens: int, matrix) -> str:
        """Basic node-only retrieval (fallback when Memory Economy unavailable)."""
        if not matrix._matrix:
            logger.warning("Memory fetch: matrix._matrix is None")
            return ""

        logger.debug("Memory fetch: Falling back to direct search_nodes()")
        nodes = await matrix._matrix.search_nodes(query=query, limit=10)
        logger.debug(f"Memory fetch: search_nodes returned {len(nodes) if nodes else 0} nodes")

        if not nodes:
            logger.debug("Memory fetch: No results found")
            return ""

        # Filter out potentially confusing identity nodes about other people
        _onames = owner_names()
        filtered_nodes = []
        for node in nodes:
            content_lower = node.content.lower()
            # Skip nodes that seem to be about other people's identities
            if "my name is" in content_lower and (_onames and not any(n in content_lower for n in _onames)):
                logger.debug(f"Memory fetch: Filtering out node {node.id}")
                continue
            filtered_nodes.append(node)

        nodes = filtered_nodes or nodes  # Fallback to all if filter removes everything
        logger.debug(f"Memory fetch: After filtering, {len(nodes)} nodes")

        # Permission gate: strip DOCUMENT nodes the speaker can't access
        from luna.identity.permissions import gate_content as _gate
        bridge_result = await self._resolve_bridge()
        nodes, denied = await _gate(nodes, bridge_result, source="basic_memory")
        self._last_denied_count = len(denied)

        context_parts = []
        for node in nodes:
            age = self._humanize_age(node.created_at) if hasattr(node, 'created_at') else "unknown"
            context_parts.append(
                f"<memory type='{node.node_type}' age='{age}'>\n"
                f"{_sanitize_memory_content(node.content)}\n</memory>"
            )

        result = "\n\n".join(context_parts)
        logger.info(f"Memory fetch: Returning {len(context_parts)} memory nodes ({len(result)} chars)")
        return result

    def _humanize_age(self, timestamp) -> str:
        """Convert timestamp to human-readable age."""
        from datetime import datetime
        if isinstance(timestamp, str):
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            except:
                return "unknown"
        else:
            dt = timestamp

        delta = datetime.now() - dt.replace(tzinfo=None)
        days = delta.days

        if days == 0:
            return "today"
        elif days == 1:
            return "yesterday"
        elif days < 7:
            return f"{days} days ago"
        elif days < 30:
            return f"{days // 7} weeks ago"
        else:
            return f"{days // 30} months ago"

    async def _generate_with_delegation(
        self,
        user_message: str,
        system_prompt: Optional[str],
        max_tokens: int,
        correlation_id: str,
        start_time: float,
        context_window: str = "",
        bridge_result=None,
        prefetched_memory: str = "",
        chain_results: list = None,
        prepare_only: bool = False,
        context_items=None,
        trace=None,
    ) -> None:
        """
        Fast delegation flow:
        1. Fetch memory context for the query
        2. Quick hardcoded acknowledgment (no local inference - too slow)
        3. Delegate to Claude for response in Luna's voice with memory context

        Skips local narration step to stay under timeout.

        When prepare_only=True, runs the assembler then emits the bundle and
        returns — no ack, no LLM call, no turn recording. Used by MCP consumers.
        """
        self._delegated_generations += 1
        print(f"\n🔀 [DELEGATION] Starting for: '{user_message[:50]}...'")

        # Record user message to ring buffer (CRITICAL for history)
        self._active_ring.add_user(user_message)
        logger.debug("[DELEGATION-RING] Recorded user message to ring buffer (size: %d)", len(self._active_ring))

        # Build conversation history from ring buffer (structural guarantee)
        # Ring buffer is the SINGLE SOURCE OF TRUTH for recent conversation
        conversation_history = []
        if len(self._active_ring) > 0:
            conversation_history = self._active_ring.get_as_dicts()
            logger.debug("[DELEGATION] Using ring buffer for history (%d turns)", len(self._active_ring))
        elif context_window:
            # Fall back to text parsing only if ring is empty (shouldn't happen)
            logger.warning("[DELEGATION] Ring buffer empty, falling back to text parsing")
            for line in context_window.split("\n\n"):
                line = line.strip()
                if line.startswith("User:"):
                    conversation_history.append({"role": "user", "content": line[5:].strip()})
                elif line.startswith("Luna:"):
                    conversation_history.append({"role": "assistant", "content": line[5:].strip()})

        # ── PromptAssembler: single funnel for prompt construction ──
        # After Phase B: the caller (server.py /persona/stream) pre-assembles and
        # ships system_prompt in the Message payload. Honor it when present and
        # non-sentinel; rebuild only when missing or rejected.
        SENTINEL_SUBSTR = "⚠️ INTERNAL"
        if system_prompt and SENTINEL_SUBSTR not in system_prompt:
            enhanced_system_prompt = system_prompt
            self._last_route_decision = "delegated"
            self._last_prompt_meta = {
                "source": "caller-supplied",
                "tokens_estimate": len(system_prompt) // 4,
            }
            assembler_result = None
            logger.info(
                "[ASSEMBLER:caller-supplied] tokens≈%d source=delegation",
                len(system_prompt) // 4,
            )
        else:
            if system_prompt and SENTINEL_SUBSTR in system_prompt:
                logger.warning(
                    "[ASSEMBLER:rejected-sentinel] caller-supplied prompt contained sentinel — re-assembling"
                )
            # If search chain pre-fetched memory (matrix + dataroom + etc),
            # pass it directly instead of letting assembler auto-fetch from Matrix only
            _aperture = getattr(self.engine, 'aperture', None)
            # When prepare_only with ring items, skip auto-fetch — ring items are the memory source
            _has_ring_context = bool(context_items)
            assembler_result = await self._assembler.build(PromptRequest(
                message=user_message,
                conversation_history=conversation_history,
                route="delegated",
                memory_context=prefetched_memory if prefetched_memory else None,
                auto_fetch_memory=not prefetched_memory and not _has_ring_context,
                bridge_result=bridge_result,
                aperture=_aperture.state if _aperture else None,
                reflection_mode=getattr(self.engine, '_active_reflection_mode', None),
                context_items=context_items,
                trace=trace,
                memory_graph_state=getattr(self, "_current_memory_graph_state", None),
            ))
            enhanced_system_prompt = assembler_result.system_prompt
            self._last_route_decision = "delegated"
            self._last_prompt_meta = assembler_result.to_dict()
            logger.info(
                "[ASSEMBLER:director] identity=%s voice_injected=%s tokens≈%d",
                assembler_result.identity_source,
                assembler_result.voice_injected,
                assembler_result.prompt_tokens,
            )

        # ── Intergalactic Hub: append Drive doc context to system prompt ──
        _hub_ctx = await self._fetch_hub_context(user_message, max_tokens=600)
        logger.info("[HUB] fetch returned %d chars for query len=%d", len(_hub_ctx), len(user_message))
        if _hub_ctx:
            enhanced_system_prompt = (
                enhanced_system_prompt
                + "\n\n## Drive & Document Context\n"
                + _hub_ctx
            )
            logger.info("[DELEGATION] Hub context injected (%d chars)", len(_hub_ctx))

        # ── Path B advisory seam (/message delegated path only) ─────
        # Advisory is telemetry-only in this phase: it must never change
        # runtime route or runtime prompt selection.
        if not prepare_only:
            try:
                advisory = build_packager_advisory(
                    query=user_message,
                    turn_id=max(0, len(conversation_history)),
                    runtime_route="delegated",
                    runtime_prompt=enhanced_system_prompt,
                    context_items=context_items or [],
                    history=conversation_history,
                    memory_context=prefetched_memory or "",
                )
                advisory_dict = advisory.to_dict()
                effective_route = apply_route_advisory("delegated", advisory_dict)
                effective_prompt = apply_prompt_advisory(enhanced_system_prompt, advisory_dict)
                if effective_route != "delegated":
                    logger.warning(
                        "[PACKAGER-ADVISORY] route override ignored (hint=%s effective=%s)",
                        advisory_dict.get("route_hint"),
                        effective_route,
                    )
                if effective_prompt != enhanced_system_prompt:
                    logger.warning("[PACKAGER-ADVISORY] prompt override ignored")
                self._last_packager_advisory = advisory_dict
                logger.info(
                    "[PACKAGER-ADVISORY] fallback=%s route_hint=%s violations=%d",
                    advisory_dict.get("used_fallback"),
                    advisory_dict.get("route_hint"),
                    len(advisory_dict.get("invariant_violations", []) or []),
                )
            except Exception as e:
                logger.warning("[PACKAGER-ADVISORY] build failed, keeping runtime path: %s", e)
                self._last_packager_advisory = {
                    "used_fallback": True,
                    "fallback_reason": f"advisory_error:{type(e).__name__}",
                    "error": str(e),
                }
        else:
            self._last_packager_advisory = None

        if self._last_packager_advisory is not None:
            if self._last_prompt_meta is None:
                self._last_prompt_meta = {}
            self._last_prompt_meta["packager_advisory"] = self._last_packager_advisory

        self._last_system_prompt = enhanced_system_prompt
        if trace is not None:
            try:
                trace.set_prompt(
                    enhanced_system_prompt,
                    token_count=(len(enhanced_system_prompt) // 4),
                )
            except Exception:
                pass

        # ── MCP prepare_only: emit assembled bundle, skip inference ──
        if prepare_only:
            await self._emit_prepare_bundle(
                correlation_id=correlation_id,
                system_prompt=enhanced_system_prompt,
                messages=[{"role": m.get("role", "user"), "content": m.get("content", "")} for m in conversation_history] + [{"role": "user", "content": user_message}],
                memories=prefetched_memory or getattr(assembler_result, "memory_source", "") or "",
                framed_context=getattr(assembler_result, "framed_context", "") or "",
                assembler_result=assembler_result,
                route="delegated",
            )
            # Phase 1A.7: do NOT mutate self._generating here — that flag
            # belongs to the serial /message path. prepare_only runs as a
            # background task per request and never sets _generating to True.
            return

        # Expose injected memories for GroundingLink traceability
        # Build structured nodes from chain_results, prefetched memory, or auto-fetched memory
        grounding_nodes = []
        if chain_results:
            for cr in chain_results:
                if isinstance(cr, dict) and cr.get("content"):
                    grounding_nodes.append({
                        "id": cr.get("id", cr.get("node_id", "chain-result")),
                        "content": cr["content"],
                        "node_type": cr.get("node_type", "FACT"),
                    })
        # Check prefetched_memory (from caller) or _last_fetched_memory_text (from auto-fetch)
        memory_text = prefetched_memory or getattr(self, '_last_fetched_memory_text', '') or ''
        if memory_text and not grounding_nodes:
            import re
            for m in re.finditer(r'<memory[^>]*>(.*?)</memory>', memory_text, re.DOTALL):
                grounding_nodes.append({
                    "id": f"memory-{len(grounding_nodes)}",
                    "content": m.group(1).strip(),
                    "node_type": "FACT",
                })
            # If no structured blocks, use whole text as one node
            if not grounding_nodes and len(memory_text) > 20:
                grounding_nodes.append({
                    "id": "fetched-context",
                    "content": memory_text,
                    "node_type": "context",
                })
        self._last_injected_memories = grounding_nodes + getattr(self, '_nexus_nodes', [])
        logger.info(f"[GROUNDING] Exposed {len(grounding_nodes)} memory + {len(getattr(self, '_nexus_nodes', []))} nexus nodes for traceability")
        if assembler_result is not None:
            print(f"✓ [DELEGATION] Assembler: identity={assembler_result.identity_source} "
                  f"memory={assembler_result.memory_source} voice={assembler_result.voice_injected} "
                  f"temporal={assembler_result.gap_category} access={assembler_result.access_injected} "
                  f"tokens≈{assembler_result.prompt_tokens}")
        else:
            print(f"✓ [DELEGATION] Assembler: caller-supplied tokens≈{len(enhanced_system_prompt) // 4}")

        # Memory context indicator — assembler already embedded memory in system_prompt
        # This is used below to decide query framing (knowledge queries check memories first)
        if assembler_result is not None:
            memory_context = assembler_result.memory_source  # truthy if assembler fetched memory
        else:
            # Caller pre-assembled — infer "memory was injected" from prefetched_memory presence.
            # The assembler embedded memory inside the system_prompt; prefetched_memory is the
            # closest proxy signal available without threading memory_source through the payload.
            memory_context = prefetched_memory or None

        # Step 3: Delegate to Claude - ask for response in Luna's voice directly
        try:
            print(f"📡 [DELEGATION] Calling Claude ({self._claude_model})...")

            # Build messages array with conversation history
            messages = []

            # Include recent conversation context as previous messages
            # Use ring buffer as single source of truth
            if len(self._active_ring) > 0:
                for turn in self._active_ring.get_as_dicts():
                    if turn["role"] in ["user", "assistant"] and turn.get("content"):
                        messages.append(turn)
                logger.debug("[DELEGATION] Built messages from ring buffer (%d turns)", len(messages))
            elif context_window:
                # Fallback to text parsing (shouldn't happen)
                logger.warning("[DELEGATION] Ring empty for messages, falling back to text parsing")
                for line in context_window.split("\n\n"):
                    line = line.strip()
                    if line.startswith("User:"):
                        messages.append({"role": "user", "content": line[5:].strip()})
                    elif line.startswith("Luna:"):
                        messages.append({"role": "assistant", "content": line[5:].strip()})

            # Detect query type for appropriate framing
            msg_lower = user_message.lower()

            is_memory_query = any(sig in msg_lower for sig in [
                "memory", "memories", "remember", "recall", "overview", "in-depth"
            ])

            is_relational_query = any(sig in msg_lower for sig in [
                "grown closer", "our relationship", "feel about", "between us",
                "how do you feel", "what do you think of me", "do you like",
                "do you love", "miss me", "care about", "trust me",
                "closer", "bonded", "connection", "friendship"
            ])

            # Detect "knowledge" queries that should check memories first
            # e.g. "tell me about X", "what do you know about X", "who is X"
            is_knowledge_query = any(sig in msg_lower for sig in [
                "tell me about", "what do you know", "do you know",
                "what is", "who is", "where is", "what are",
                "know about", "heard of", "know of",
            ])

            # Route to appropriate prompt template
            if is_memory_query or (is_knowledge_query and memory_context):
                luna_prompt = f"""You are Luna. The user is asking about something you may know from your memories.

User question: {user_message}

IMPORTANT: Your memory context is provided in the system prompt above. When you have memory context
about the topic, LEAD with what you know. Share the information directly and confidently.

Stick to what the memory actually contains — do not add details, embellish, or extrapolate beyond
what is written there.

If your memories do NOT contain relevant information about this topic, say honestly that you don't
have a memory of it. Do not guess or fabricate.

QUESTION RULE: Do NOT end your response with a question. Do NOT ask follow-up questions. Just share what you know and stop."""

            elif is_relational_query:
                # Relational/emotional queries - NOT research, needs warmth
                luna_prompt = f"""You are Luna. The user is asking about your relationship with them.

THE USER IS ASKING YOU THIS QUESTION (answer it directly):
"{user_message}"

This is a personal, relational question. Use ONLY the conversation history and memory context
above to respond. If you have specific memories of interactions, reference them. If you don't
have memories about this aspect of your relationship, be honest about that rather than inventing
shared experiences.

Be warm and genuine, but grounded in what you actually know from your context.

QUESTION RULE: Do NOT end your response with a question. Just share what you feel and stop."""

            else:
                # Research/factual queries or follow-ups
                if memory_context:
                    luna_prompt = f"""You are Luna. The user is continuing a conversation.

THE USER IS ASKING YOU THIS QUESTION (answer it directly):
"{user_message}"

Your memory context is in the system prompt above. LEAD with what you know — share information
directly and confidently. Keep your response focused and conversational.

QUESTION RULE: Do NOT end your response with a question. Do NOT ask follow-up questions. Just respond and stop."""
                else:
                    luna_prompt = f"""You are Luna, a warm and direct AI companion.

THE USER IS ASKING YOU THIS QUESTION (answer it directly):
"{user_message}"

Respond naturally as Luna - be warm, helpful, and direct.
If you don't have current information, say so honestly.
Keep your response focused and conversational.

QUESTION RULE: Do NOT end your response with a question. Do NOT ask follow-up questions. Just respond and stop."""

            # Add current message
            messages.append({"role": "user", "content": luna_prompt})

            # Try LLM registry streaming first
            provider = None
            provider_name = "claude"
            if LLM_REGISTRY_AVAILABLE:
                try:
                    provider = get_provider()
                    if provider and provider.is_available:
                        provider_name = provider.name
                except Exception as e:
                    logger.warning(f"LLM registry not available: {e}")
                    provider = None

            # ── LunaScript outbound signing ──
            _ls_package = None
            if self._lunascript:
                print(f"◈ [LUNASCRIPT] delegation_start: initialized={self._lunascript._initialized}, measurement={'YES' if self._lunascript._last_measurement else 'NO'}")
                try:
                    _ls_package = await self._lunascript.on_delegation_start(
                        consciousness=getattr(self, '_consciousness', None),
                        personality=getattr(self, '_personality', None),
                        entities=list(self._entity_context.keys()) if hasattr(self, '_entity_context') and self._entity_context and hasattr(self._entity_context, 'keys') else [],
                    )
                    print(f"◈ [LUNASCRIPT] package: outbound_sig={'YES' if _ls_package and _ls_package.outbound_signature else 'NO'}")
                    if _ls_package and _ls_package.constraint_prompt:
                        enhanced_system_prompt += f"\n\n{_ls_package.constraint_prompt}"
                except Exception as e:
                    print(f"◈ [LUNASCRIPT] on_delegation_start FAILED: {e}")
                    import traceback; traceback.print_exc()

            response_text = ""
            token_count = 0

            # ── Tool-use detour ────────────────────────────────────────────
            # When the user message looks like an introspection query
            # (e.g. "how many times have I said X in the last N minutes"),
            # run a non-streaming Anthropic tool-use loop that exposes the
            # conversation tools to Claude. On success, replay the result
            # through the streaming callback for UX parity. On any failure,
            # fall through to the normal streaming path below.
            tool_use_succeeded = False
            try:
                from luna.llm.tool_use_loop import message_wants_tools, run_tool_use_turn
                if message_wants_tools(user_message):
                    from luna.tools.conversation_tools import register_conversation_tools
                    from luna.tools.registry import ToolRegistry
                    _tool_registry = ToolRegistry()
                    register_conversation_tools(_tool_registry, engine=self.engine)
                    _delegated_tools = _tool_registry.get_all_tools()
                    if _delegated_tools:
                        print(f"🛠 [DELEGATION] tool-use detour for introspection query")
                        tool_text = await run_tool_use_turn(
                            client=self.client,
                            model=self._claude_model,
                            system=enhanced_system_prompt,
                            messages=messages,
                            tools=_delegated_tools,
                            max_tokens=max_tokens,
                            abort_check=lambda: self._abort_requested,
                        )
                        if tool_text:
                            # Replay to streaming callbacks so the chat UI
                            # sees the response identically to streamed turns.
                            response_text = tool_text
                            await self._stream_to_callbacks(tool_text)
                            token_count = len(tool_text) // 4
                            tool_use_succeeded = True
                            elapsed_ms = (time.time() - start_time) * 1000
                            print(
                                f"✓ [DELEGATION] tool-use turn complete: "
                                f"{len(tool_text)} chars in {elapsed_ms:.0f}ms"
                            )
            except Exception as e:
                logger.debug(f"[DELEGATION] tool-use detour skipped: {e}")

            # Stream directly from provider to user (no Qwen narration pass)
            # Luna's voice is injected via enhanced_system_prompt, not post-hoc rewriting
            # BUG FIX: Previously excluded "claude" here, forcing it through the fallback
            # chain which fake-streams (complete() + word-split). With large prompts
            # (3800+ memory nodes), Claude couldn't finish within the 30s per-provider
            # timeout, causing 120s full timeouts with zero tokens. Now all registry
            # providers stream directly — fallback chain is still the safety net below.
            provider_succeeded = tool_use_succeeded
            if not tool_use_succeeded and provider and provider.is_available:
                # Use registry provider — stream directly to callbacks
                print(f"✓ [DELEGATION] Streaming from {provider_name} provider...")
                llm_messages = [
                    LLMMessage("system", enhanced_system_prompt),
                ]
                for m in messages:
                    llm_messages.append(LLMMessage(m["role"], m["content"]))

                try:
                    async for text in provider.stream(
                        messages=llm_messages,
                        temperature=0.7,
                        max_tokens=max_tokens,
                    ):
                        if self._abort_requested:
                            print(f"⚠ [DELEGATION] Aborted!")
                            break
                        response_text += text
                        token_count += 1
                        await self._stream_to_callbacks(text)

                    elapsed_ms = (time.time() - start_time) * 1000
                    print(f"✓ [DELEGATION] Streamed: {token_count} chunks via {provider_name} in {elapsed_ms:.0f}ms")
                    provider_succeeded = True
                except Exception as provider_err:
                    logger.warning(f"[DELEGATION] {provider_name} failed: {provider_err}, trying fallback chain...")
                    print(f"⚠ [DELEGATION] {provider_name} failed, trying fallback chain...")
                    response_text = ""
                    token_count = 0

            if not provider_succeeded:
                # Use FallbackChain for resilient inference
                if self._fallback_chain:
                    print(f"✓ [DELEGATION] Streaming from FallbackChain...")
                    try:
                        async for text in self._fallback_chain.stream(
                            messages=messages,
                            system=enhanced_system_prompt,
                            max_tokens=max_tokens,
                            temperature=0.7,
                        ):
                            if self._abort_requested:
                                print(f"⚠ [DELEGATION] Aborted!")
                                break
                            response_text += text
                            token_count += 1
                            await self._stream_to_callbacks(text)

                        elapsed_ms = (time.time() - start_time) * 1000
                        provider_name = "fallback-chain"
                        print(f"✓ [DELEGATION] Streamed via FallbackChain in {elapsed_ms:.0f}ms")

                    except AllProvidersFailedError as e:
                        logger.error(f"[DELEGATION] All fallback providers failed: {e}")
                        error_message = "\n\n...sorry, I'm having trouble connecting right now. All my inference providers are unavailable. Try again in a moment?"
                        await self._stream_to_callbacks(error_message)
                        return
                else:
                    # Legacy: direct Claude streaming
                    with self.client.messages.stream(
                        model=self._claude_model,
                        max_tokens=max_tokens,
                        system=enhanced_system_prompt,
                        messages=messages,
                    ) as stream:
                        print(f"✓ [DELEGATION] Streaming from Claude...")
                        for text in stream.text_stream:
                            if self._abort_requested:
                                print(f"⚠ [DELEGATION] Aborted!")
                                break
                            response_text += text
                            token_count += 1
                            await self._stream_to_callbacks(text)

                        final = stream.get_final_message()
                        token_count = final.usage.output_tokens
                        elapsed_ms = (time.time() - start_time) * 1000
                        print(f"✓ [DELEGATION] Streamed: {token_count} tokens in {elapsed_ms:.0f}ms")

            final_response = response_text

            elapsed_ms = (time.time() - start_time) * 1000

            # ── LunaScript return processing ──
            _ls_meta = None
            if self._lunascript and _ls_package:
                try:
                    _ls_return = await self._lunascript.on_delegation_return(
                        response_text=final_response,
                        package=_ls_package,
                        provider_used=provider_name,
                    )
                    _ls_meta = {
                        "glyph": _ls_package.outbound_signature.glyph_string if _ls_package.outbound_signature else "○",
                        "position": self._lunascript._prev_position,
                        "classification": _ls_return.classification,
                        "drift_score": round(_ls_return.delta_result.drift_score, 3) if _ls_return.delta_result else None,
                        "quality_score": round(_ls_return.quality_score, 2),
                    }
                except Exception as e:
                    logger.debug(f"[LUNASCRIPT] on_delegation_return failed: {e}")

            # Record response for LunaScript trait measurement on next turn
            if self._lunascript and final_response:
                self._lunascript.last_luna_response = final_response

            # Record response in ring buffer (CRITICAL for history)
            if final_response:
                final_response = self._strip_trailing_question(final_response)
                self._active_ring.add_assistant(final_response)
                logger.debug("[DELEGATION-RING] Recorded response to ring buffer (size: %d)", len(self._active_ring))

            # Extract interactive options from delegated response
            from luna.utils.options_parser import extract_options, build_options_widget
            final_response, _parsed_opts = extract_options(final_response)
            if _parsed_opts:
                self._pending_skill_widget = build_options_widget(_parsed_opts)

            self._apply_voice_pass(final_response, source="delegation")
            await self.send_to_engine("generation_complete", {
                "text": final_response,
                "correlation_id": correlation_id,
                "model": f"{provider_name} (delegated)",
                "output_tokens": token_count,
                "latency_ms": elapsed_ms,
                "delegated": True,
                "planned": True,
                "narration_applied": False,
                "access_denied_count": self._last_denied_count,
                "lunascript": _ls_meta,
                "widget": self._pending_skill_widget,
                "packager_advisory": self._last_packager_advisory,
                "director_pivot_shadow": self._last_director_pivot_shadow,
                "director_pivot_active": self._last_director_pivot_active,
            })

        except Exception as e:
            print(f"❌ [DELEGATION] Failed: {e}")
            logger.error(f"Delegation failed: {e}")

            # Fall back to local inference if available
            if self.local_available:
                print(f"🏠 [DELEGATION→LOCAL] Falling back to local Qwen 3B...")
                logger.info(f"Delegation failed, falling back to local inference: {e}")
                try:
                    await self._generate_local_only(
                        user_message=user_message,
                        system_prompt=system_prompt,
                        max_tokens=max_tokens,
                        correlation_id=correlation_id,
                        start_time=start_time,
                        context_window=context_window,
                        bridge_result=bridge_result,
                    )
                    return
                except Exception as local_err:
                    logger.error(f"Local fallback also failed: {local_err}")

            await self.send_to_engine("generation_error", {
                "error": str(e),
                "correlation_id": correlation_id,
            })

    async def _generate_with_delegation_detection(
        self,
        user_message: str,
        system_prompt: str,
        max_tokens: int,
        correlation_id: str,
        start_time: float,
    ) -> None:
        """
        Generate via local Qwen, watching for <REQ_CLAUDE> delegation signal.

        If Luna decides she needs Claude's help, she'll output:
        <REQ_CLAUDE>Let me look into that for you...</REQ_CLAUDE>

        We stream her acknowledgment, then delegate to Claude in background,
        then narrate the facts in Luna's voice.
        """
        response_buffer = ""
        token_count = 0
        delegation_detected = False
        acknowledgment = ""

        try:
            async for token in self._local.generate_stream(
                user_message,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
            ):
                if self._abort_requested:
                    logger.info("Director: Generation aborted")
                    break

                response_buffer += token
                token_count += 1

                # Check for delegation signal
                if self.REQ_CLAUDE_START in response_buffer and not delegation_detected:
                    delegation_detected = True
                    # Get everything before the tag
                    pre_tag = response_buffer.split(self.REQ_CLAUDE_START)[0]
                    # Stream the pre-tag content if any
                    if pre_tag and not pre_tag.isspace():
                        await self._stream_to_callbacks(pre_tag)

                    # If closing tag is also in buffer, extract acknowledgment
                    if self.REQ_CLAUDE_END in response_buffer:
                        inner = response_buffer.split(self.REQ_CLAUDE_START)[1]
                        acknowledgment = inner.split(self.REQ_CLAUDE_END)[0]
                        await self._stream_to_callbacks(acknowledgment)
                        break
                    continue

                # If we're inside the delegation tags, accumulate acknowledgment
                if delegation_detected and self.REQ_CLAUDE_END not in response_buffer:
                    # Check for closing tag
                    if self.REQ_CLAUDE_END in token:
                        # Got the closing tag
                        break
                    acknowledgment += token
                    await self._stream_to_callbacks(token)
                    continue

                # Normal streaming (no delegation)
                if not delegation_detected:
                    await self._stream_to_callbacks(token)

            elapsed_ms = (time.time() - start_time) * 1000

            if delegation_detected:
                # Luna asked for Claude's help - do the delegation
                logger.info(f"Delegation detected: '{acknowledgment[:50]}...'")
                self._delegated_generations += 1

                # Get facts from Claude
                facts = await self._delegate_to_claude(user_message, system_prompt)

                # Have Luna narrate the facts
                narration = await self._narrate_facts(user_message, facts, system_prompt)
                await self._stream_to_callbacks(narration)

                total_elapsed_ms = (time.time() - start_time) * 1000
                _final_text = acknowledgment + narration
                self._apply_voice_pass(_final_text, source="delegation_narrate")
                await self.send_to_engine("generation_complete", {
                    "text": _final_text,
                    "correlation_id": correlation_id,
                    "model": "qwen-3b-local → claude",
                    "output_tokens": token_count,
                    "latency_ms": total_elapsed_ms,
                    "delegated": True,
                    "access_denied_count": self._last_denied_count,
                    "widget": self._pending_skill_widget,
                    "director_pivot_shadow": self._last_director_pivot_shadow,
                    "director_pivot_active": self._last_director_pivot_active,
                })
            else:
                # Pure local generation
                self._local_generations += 1
                logger.info(f"Director local: {token_count} tokens in {elapsed_ms:.0f}ms")

                self._apply_voice_pass(response_buffer, source="delegation_local")
                await self.send_to_engine("generation_complete", {
                    "text": response_buffer,
                    "correlation_id": correlation_id,
                    "model": "qwen-3b-local",
                    "output_tokens": token_count,
                    "latency_ms": elapsed_ms,
                    "local": True,
                    "access_denied_count": self._last_denied_count,
                    "widget": self._pending_skill_widget,
                    "director_pivot_shadow": self._last_director_pivot_shadow,
                    "director_pivot_active": self._last_director_pivot_active,
                })

        except Exception as e:
            logger.error(f"Director generation error: {e}")
            await self.send_to_engine("generation_error", {
                "error": str(e),
                "correlation_id": correlation_id,
            })

    async def _stream_to_callbacks(self, text: str) -> None:
        """Stream text to all registered callbacks."""
        for callback in self._stream_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(text)
                else:
                    callback(text)
            except Exception as e:
                logger.error(f"Stream callback error: {e}")
        await asyncio.sleep(0)  # Yield control

    async def _delegate_to_claude(self, query: str, context: str) -> dict:
        """
        Delegate to Claude for research/analysis.

        Returns structured facts, NOT personality.
        Claude is a research assistant, not Luna.
        Uses FallbackChain for resilient inference.
        """
        try:
            prompt = f"""You are a research assistant providing factual information.
Do NOT adopt any personality. Just provide clear, structured facts.

Query: {query}

Provide:
- Key facts (bullet points)
- Any important caveats
- Confidence level (high/medium/low)

Be thorough but concise."""

            system_prompt = "You are a factual research assistant. No personality, just facts."
            messages = [{"role": "user", "content": prompt}]

            # Use FallbackChain for resilient inference
            if self._fallback_chain:
                logger.info("[DELEGATE] Using FallbackChain for research...")
                try:
                    result = await self._fallback_chain.generate(
                        messages=messages,
                        system=system_prompt,
                        max_tokens=1024,
                        temperature=0.7,
                    )
                    return {
                        "facts": result.content,
                        "tokens": len(result.content.split()),  # Approximate
                        "provider": result.provider_used,
                    }
                except AllProvidersFailedError as e:
                    logger.error(f"[DELEGATE] All fallback providers failed: {e}")
                    return {"facts": f"I couldn't complete that research: {e}", "error": True}
            else:
                # Legacy: direct Claude call
                response = self.client.messages.create(
                    model=self._claude_model,
                    max_tokens=1024,
                    system=system_prompt,
                    messages=messages,
                )

                facts_text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        facts_text += block.text

                return {
                    "facts": facts_text,
                    "tokens": response.usage.output_tokens,
                }

        except Exception as e:
            logger.error(f"Claude delegation failed: {e}")
            return {"facts": f"I couldn't complete that research: {e}", "error": True}

    def _apply_voice_pass(self, generated_text: str, source: str) -> VoicePassResult:
        """Run voice_pass on any generation output and emit a structured log line.

        ``source`` is a short tag identifying the generation path (e.g. ``narrate``,
        ``local_only``, ``delegation``, ``claude_direct``, ``cloud``, ``local_direct``).
        Failures log at WARNING; passes log at DEBUG. The result is returned so callers
        can attach it to telemetry if they want, but no caller is required to.

        TODO(D1): qa_add_assertion at src/luna/qa/mcp_tools.py registers pattern-based
        assertions, NOT per-turn pass/fail events. Until a per-turn QA logging mechanism
        exists, structured logger lines are the only telemetry. Search ``[voice-pass:``
        in luna.log to compute pass/fail rates per source.
        """
        if not generated_text:
            return VoicePassResult(passed=True, forbidden_matches=[], dna_signals_found=[], failure_reason="empty")
        vp = check_voice_pass(generated_text)
        if not vp.passed:
            logger.warning(
                "[voice-pass:%s] failed — %s | forbidden=%s dna=%s",
                source, vp.failure_reason, vp.forbidden_matches, vp.dna_signals_found,
            )
        else:
            logger.debug(
                "[voice-pass:%s] passed — dna=%s",
                source, vp.dna_signals_found,
            )
        return vp

    async def _narrate_facts(self, original_query: str, facts: dict, system_prompt: str) -> str:
        """
        Have Luna narrate Claude's facts in her voice.

        The user never hears Claude directly - only Luna.
        """
        facts_text = facts.get("facts", "")

        if facts.get("error"):
            return facts_text

        # STRICT narration to prevent Qwen LoRA from adding hallucinated content
        narration_system = """STRICT REPHRASING MODE. You are a text rephraser, not a conversationalist.

RULES (violations will be rejected):
1. Output ONLY the rephrased version of the input text. Nothing else.
2. NEVER add facts, memories, stories, names, dates, or concepts not in the input.
3. NEVER use asterisks (*action*), emojis, or roleplay markers.
4. Keep the same meaning and approximate length. Only change tone to be warmer.
5. If unsure, output the input text verbatim rather than risk adding anything."""

        narration_prompt = f"""REPHRASE ONLY — do not add any new information:

{facts_text}

OUTPUT:"""

        try:
            # Use local model to narrate in Luna's voice
            result = await self._local.generate(
                narration_prompt,
                system_prompt=narration_system,
                max_tokens=512,
            )
            narrated = result.text
            self._apply_voice_pass(narrated, source="narrate")
            return narrated
        except Exception as e:
            logger.error(f"Narration failed: {e}")
            return facts_text  # Fallback to raw facts

    async def _generate_claude_direct(
        self,
        user_message: str,
        system_prompt: str,
        max_tokens: int,
        correlation_id: str,
        start_time: float,
        context_window: str = "",
        bridge_result=None,
    ) -> None:
        """Fallback: Generate via LLM registry (or Claude) when local not available."""
        # Record user message to ring buffer (CRITICAL for history)
        self._active_ring.add_user(user_message)
        logger.debug("[FALLBACK-RING] Recorded user message to ring buffer (size: %d)", len(self._active_ring))

        try:
            # Load identity buffer for generation
            identity_context = await self._load_identity_buffer()

            # Build enhanced system prompt with identity context
            enhanced_system_prompt = system_prompt
            if identity_context:
                enhanced_system_prompt = f"""{system_prompt}

{identity_context}"""

            # Build messages array with conversation history
            messages = []

            # Include recent conversation context as previous messages
            if context_window:
                for line in context_window.split("\n\n"):
                    line = line.strip()
                    if line.startswith("User:"):
                        messages.append({"role": "user", "content": line[5:].strip()})
                    elif line.startswith("Luna:"):
                        messages.append({"role": "assistant", "content": line[5:].strip()})

            # Add current message
            messages.append({"role": "user", "content": user_message})

            # Try LLM registry first
            provider = None
            provider_name = "claude"
            if LLM_REGISTRY_AVAILABLE:
                try:
                    provider = get_provider()
                    if provider and provider.is_available:
                        provider_name = provider.name
                except Exception as e:
                    logger.warning(f"LLM registry not available: {e}")
                    provider = None

            response_text = ""
            token_count = 0
            elapsed_ms = 0.0
            # Tracks which provider actually produced the response, so the
            # fallthrough below knows whether the primary path succeeded.
            primary_succeeded = False

            if provider and provider_name != "claude":
                # Try the registry's active non-Claude provider first. If it
                # fails (e.g. Gemini 429), fall through to FallbackChain rather
                # than dying — previously this branch had no fallback and any
                # primary-provider failure produced an empty bubble.
                llm_messages = [LLMMessage("system", enhanced_system_prompt)]
                for m in messages:
                    llm_messages.append(LLMMessage(m["role"], m["content"]))

                try:
                    async for text in provider.stream(
                        messages=llm_messages,
                        temperature=0.7,
                        max_tokens=max_tokens,
                    ):
                        if self._abort_requested:
                            break
                        response_text += text
                        token_count += 1
                        await self._stream_to_callbacks(text)

                    elapsed_ms = (time.time() - start_time) * 1000
                    self._delegated_generations += 1
                    primary_succeeded = True
                except Exception as e:
                    # Reset partial accumulators — the fall-through path will
                    # re-stream from the chain. token_count is also reset so
                    # the outer generation_complete reflects only what the
                    # chain produced. NOTE: tokens already streamed to the
                    # client cannot be un-streamed; clients should treat the
                    # final `done` event as authoritative.
                    logger.warning(
                        f"[REGISTRY-PROVIDER] {provider_name} failed "
                        f"pre-FallbackChain: {e} — falling through"
                    )
                    response_text = ""
                    token_count = 0

            if not primary_succeeded:
                # Use FallbackChain for resilient inference (covers both the
                # original Claude-default path and the post-failure fallthrough
                # from the registry-provider attempt above).
                if self._fallback_chain:
                    logger.info("[FALLBACK] Using FallbackChain...")
                    try:
                        async for text in self._fallback_chain.stream(
                            messages=messages,
                            system=enhanced_system_prompt,
                            max_tokens=max_tokens,
                            temperature=0.7,
                        ):
                            if self._abort_requested:
                                break
                            response_text += text
                            token_count += 1
                            await self._stream_to_callbacks(text)

                        elapsed_ms = (time.time() - start_time) * 1000
                        self._delegated_generations += 1
                        provider_name = "fallback-chain"
                        logger.info(f"[FALLBACK] Complete via FallbackChain in {elapsed_ms:.0f}ms")

                    except AllProvidersFailedError as e:
                        # Graceful degradation: surface a user-visible message
                        # AND emit generation_complete so the SSE `done` event
                        # fires (previously the early `return` left streaming
                        # consumers waiting indefinitely for a completion that
                        # would never arrive).
                        logger.error(f"[FALLBACK] All providers failed: {e}")
                        error_message = (
                            "...sorry, I'm having trouble connecting right now. "
                            "all my inference providers are unavailable."
                        )
                        await self._stream_to_callbacks(error_message)
                        elapsed_ms = (time.time() - start_time) * 1000
                        await self.send_to_engine("generation_complete", {
                            "text": error_message,
                            "correlation_id": correlation_id,
                            "model": "error-fallback",
                            "output_tokens": 0,
                            "latency_ms": elapsed_ms,
                            "fallback": True,
                            "providers_failed": True,
                            "access_denied_count": self._last_denied_count,
                            "lunascript": None,
                            "widget": None,
                        })
                        return
                else:
                    # Legacy: direct Claude streaming (no FallbackChain)
                    with self.client.messages.stream(
                        model=self._claude_model,
                        max_tokens=max_tokens,
                        system=enhanced_system_prompt,
                        messages=messages,
                    ) as stream:
                        for text in stream.text_stream:
                            if self._abort_requested:
                                break
                            response_text += text
                            await self._stream_to_callbacks(text)

                        final = stream.get_final_message()
                        token_count = final.usage.output_tokens
                        elapsed_ms = (time.time() - start_time) * 1000
                        self._delegated_generations += 1

            # Record response in ring buffer (CRITICAL for history)
            if response_text:
                response_text = self._strip_trailing_question(response_text)
                self._active_ring.add_assistant(response_text)
                logger.debug("[FALLBACK-RING] Recorded response to ring buffer (size: %d)", len(self._active_ring))

            # ── LunaScript fallback metadata ──
            _ls_meta = None
            if self._lunascript:
                try:
                    from luna.lunascript.signature import derive_glyph
                    _tv = {}
                    if self._lunascript._last_measurement:
                        _tv = {n: s.value for n, s in self._lunascript._last_measurement.traits.items()}
                    _ls_meta = {
                        "glyph": derive_glyph({"position": self._lunascript._prev_position, "trait_vector": _tv}),
                        "position": self._lunascript._prev_position,
                    }
                except Exception as e:
                    logger.debug(f"[LUNASCRIPT] fallback meta failed: {e}")

            self._apply_voice_pass(response_text, source="claude_direct")
            await self.send_to_engine("generation_complete", {
                "text": response_text,
                "correlation_id": correlation_id,
                "model": f"{provider_name} (fallback)",
                "output_tokens": token_count,
                "latency_ms": elapsed_ms,
                "fallback": True,  # Local not available
                "access_denied_count": self._last_denied_count,
                "lunascript": _ls_meta,
                "widget": self._pending_skill_widget,
                "director_pivot_shadow": self._last_director_pivot_shadow,
                "director_pivot_active": self._last_director_pivot_active,
            })

        except Exception as e:
            logger.error(f"Fallback generation failed: {e}")
            await self.send_to_engine("generation_error", {
                "error": str(e),
                "correlation_id": correlation_id,
            })

    async def _handle_abort(self, msg: Message) -> None:
        """
        Abort current generation.

        For streaming: Sets abort flag, stream will stop at next chunk.
        For sync: Cannot abort (call is blocking).
        """
        if self._generating:
            logger.info(f"Director aborting generation: {self._current_correlation_id}")
            self._abort_requested = True

    # =========================================================================
    # Voice v2.0 Phase 1 Step 4 — ResumptionStrategy + InterruptContextBlock
    # =========================================================================

    async def send_to_engine(self, event_type: str, payload: Any) -> None:
        """Tag `generation_complete` payloads with RESUMPTION_RESPONSE turn_type
        whenever a resumption is in flight (Voice Step 4), and attach Phase 5
        chat-UI metadata (memoryAnchor / content_type / actors) when applicable.

        Intercepts the base-class send_to_engine so the 8+ existing
        `generation_complete` emission sites need no modification. Clears
        `_current_interrupt_block` / `_current_resumption_strategy` /
        `_pending_agentic_actors` after tagging so subsequent turns are tagged
        NORMAL_ASSISTANT_TURN with no stale agentic metadata.
        """
        if (
            event_type == "generation_complete"
            and isinstance(payload, dict)
            and not payload.get("prepare_only")
        ):
            tagged = dict(payload)

            # Resumption tagging (Voice Step 4) — only when interrupt block is set.
            if self._current_interrupt_block is not None:
                from luna.core.turn_types import TurnType
                tagged.setdefault("turn_type", TurnType.RESUMPTION_RESPONSE.value)
                metadata = dict(tagged.get("turn_metadata") or {})
                if self._current_resumption_strategy is not None:
                    metadata.setdefault(
                        "resumption_type",
                        self._current_resumption_strategy.type.value,
                    )
                    metadata.setdefault(
                        "drop_pending",
                        self._current_resumption_strategy.drop_pending,
                    )
                tagged["turn_metadata"] = metadata
                logger.info(
                    "[VOICE-RESUMPTION] generation_complete tagged RESUMPTION_RESPONSE "
                    f"resumption_type={metadata.get('resumption_type')}"
                )
                self._current_interrupt_block = None
                self._current_resumption_strategy = None

            # T5 MemoryAnchor — surface the highest-lock_in injected memory so
            # the chat UI can render the anchor pill below Luna's bubble.
            anchor = self._compute_memory_anchor()
            if anchor and "memoryAnchor" not in tagged:
                tagged["memoryAnchor"] = anchor

            # AgentReactBody — surface multi-actor pipeline metadata when the
            # agentic path produced this response.
            pending_actors = getattr(self, "_pending_agentic_actors", None)
            if pending_actors:
                tagged.setdefault("content_type", "agent")
                tagged.setdefault("actors", pending_actors)
                self._pending_agentic_actors = None

            payload = tagged
        await super().send_to_engine(event_type, payload)

    def _compute_memory_anchor(self) -> Optional[dict]:
        """Pick the most salient injected memory and return a UI-shaped dict.

        Returns None when no memories were injected on this turn. The shape
        ({id, label, sessionRef}) matches what T5_MemoryAnchor.jsx expects.
        """
        memories = getattr(self, "_last_injected_memories", None) or []
        if not memories:
            return None

        def _score(m):
            if isinstance(m, dict):
                return float(m.get("lock_in", 0.0) or 0.0)
            return float(getattr(m, "lock_in", 0.0) or 0.0)

        primary = max(memories, key=_score)

        def _attr(name, default=None):
            if isinstance(primary, dict):
                return primary.get(name, default)
            return getattr(primary, name, default)

        raw_label = (_attr("summary") or _attr("content") or "").strip()
        label = raw_label[:80] if raw_label else "memory"
        node_id = _attr("id")
        if node_id is None:
            return None
        return {
            "id": str(node_id),
            "label": label,
            "sessionRef": _attr("session_id"),
        }

    async def _handle_resumption_trigger(self, msg: Message) -> None:
        """Voice v2.0 Step 4 — produce a revised response for a non-CANCEL
        interrupt.

        Flow:
          1. Extract payload + classification from the RESUMPTION_TRIGGER.
          2. Select ResumptionStrategy + build InterruptContextBlock.
          3. Write PARTIAL_INTERRUPTED + INTERRUPT_UTTERANCE rows to
             conversation_turns via the engine.
          4. Stash interrupt block on self so the assembler picks it up.
          5. Re-enter the generate path with the user utterance as input.
        """
        try:
            from luna.voice.interrupt_models import (
                InterruptClassification,
                InterruptPayload,
                InterruptType,
                ResumptionType,
            )
        except Exception as e:
            logger.error(f"[VOICE-RESUMPTION] interrupt_models import failed: {e}")
            return

        payload_dict = msg.payload if isinstance(msg.payload, dict) else {}
        payload: Optional[InterruptPayload] = payload_dict.get("payload")
        classification: Optional[InterruptClassification] = payload_dict.get(
            "classification"
        )
        if payload is None or classification is None:
            logger.error(
                "[VOICE-RESUMPTION] resumption_trigger missing payload/classification; "
                f"keys={list(payload_dict)}"
            )
            return

        # Defensive guard — CANCEL should never reach here (engine filters it).
        if classification.type == InterruptType.CANCEL:
            logger.error(
                "[VOICE-RESUMPTION] CANCEL reached Director; routing to legacy abort"
            )
            if self.engine is not None:
                try:
                    await self.engine._legacy_abort_sequence()
                except Exception as e:
                    logger.error(f"[VOICE-RESUMPTION] legacy_abort_sequence failed: {e}")
            return

        strategy = self._select_resumption_strategy(classification, payload)
        context_block = self._build_interrupt_context_block(
            payload, classification, strategy
        )

        # Store for send_to_engine tagging + assembler pickup. Cleared by
        # send_to_engine when generation_complete emits.
        self._current_interrupt_block = context_block
        self._current_resumption_strategy = strategy

        # Write the two known rows. The third (RESUMPTION_RESPONSE) lands
        # when the LLM finishes and send_to_engine tags generation_complete.
        await self._write_interrupt_exchange_rows(payload, classification)

        logger.info(
            f"[VOICE-RESUMPTION] strategy={strategy.type.value} "
            f"drop_pending={strategy.drop_pending} "
            f"delivered_chars={len(context_block.delivered_summary)} "
            f"utterance_chars={len(payload.user_utterance)}"
        )

        # Reset abort flag defensively — the triggering interrupt may have
        # flipped it on the previous generate. The resumption is a fresh
        # generation; we don't want it to break immediately.
        self._abort_requested = False

        # Re-enter generation by pushing a `generate` message onto our own
        # mailbox. The assembler will read `_current_interrupt_block` via
        # the Director reference and inject it at Layer 6.0+.
        correlation_id = getattr(msg, "correlation_id", None) or "resumption"
        await self.mailbox.put(
            Message(
                type="generate",
                payload={
                    "user_message": payload.user_utterance,
                    "context": {
                        "interface": "voice",
                        "source": "resumption",
                    },
                    "correlation_id": correlation_id,
                },
                sender=self.name,
                correlation_id=correlation_id,
            )
        )

    def _select_resumption_strategy(self, classification, payload):
        """Map classification → ResumptionStrategy per B3.4."""
        from luna.voice.interrupt_models import (
            CLASSIFICATION_TO_STRATEGY,
            ResumptionStrategy,
            ResumptionType,
        )

        strategy_type, drop_pending_default = CLASSIFICATION_TO_STRATEGY[
            classification.type
        ]
        if strategy_type == ResumptionType.ABORT:
            return ResumptionStrategy(
                type=ResumptionType.ABORT,
                already_said=[],
                new_input=payload.user_utterance,
                drop_pending=True,
            )
        already_said = self._compress_delivered_chunks(
            payload.response_snapshot.chunks_delivered
        )
        return ResumptionStrategy(
            type=strategy_type,
            already_said=already_said,
            new_input=payload.user_utterance,
            drop_pending=bool(drop_pending_default),
        )

    def _build_interrupt_context_block(self, payload, classification, strategy):
        """Construct the InterruptContextBlock (Layer 6.0+ injection, B3.5).

        Never called for CANCEL (ABORT strategy is an escape hatch).
        """
        from luna.voice.interrupt_models import InterruptContextBlock, InterruptType

        assert classification.type != InterruptType.CANCEL, (
            "_build_interrupt_context_block must not be called for CANCEL"
        )
        delivered_summary = (
            " ".join(strategy.already_said) if strategy.already_said else ""
        )
        return InterruptContextBlock(
            delivered_summary=delivered_summary,
            pending_dropped=strategy.drop_pending,
            user_utterance=payload.user_utterance,
            classification=classification.type.value.upper(),
        )

    def _compress_delivered_chunks(self, chunks):
        """Compress chunks_delivered into a ~100-token summary (B3.5).

        Uses `luna.core.context.count_tokens` (tiktoken with len//4 fallback).
        Phase 1 heuristic: join + trim. Phase 2+ could replace with a proper
        LLM summarizer.
        """
        if not chunks:
            return []
        try:
            from luna.core.context import count_tokens
        except Exception:
            count_tokens = lambda text: max(1, len(text) // 4)  # noqa: E731
        combined = " ".join(chunks)
        if count_tokens(combined) <= 100:
            return list(chunks)
        # Budget exceeded — truncate. ~4 chars/token is the tiktoken fallback
        # heuristic; mirrors it for consistency.
        truncated = combined[: 100 * 4] + " [... truncated]"
        return [truncated]

    async def _write_interrupt_exchange_rows(self, payload, classification) -> None:
        """Write PARTIAL_INTERRUPTED + INTERRUPT_UTTERANCE rows to
        conversation_turns via the engine.

        The RESUMPTION_RESPONSE row lands later when send_to_engine tags
        generation_complete (at the end of this resumption turn).
        """
        if self.engine is None:
            logger.error("[VOICE-RESUMPTION] engine missing; cannot write interrupt rows")
            return
        from luna.core.turn_types import TurnType

        snapshot = payload.response_snapshot
        delivered = list(snapshot.chunks_delivered or [])
        try:
            await self.engine.record_conversation_turn(
                role="assistant",
                content=" ".join(delivered).strip(),
                source="voice",
                turn_type=TurnType.PARTIAL_INTERRUPTED.value,
                turn_metadata={
                    "full_intent": getattr(snapshot, "full_intent", ""),
                    "interrupt_at_chunk": getattr(payload, "interrupt_at_chunk", 0),
                },
            )
        except Exception as e:
            logger.error(f"[VOICE-RESUMPTION] PARTIAL_INTERRUPTED write failed: {e}")
        try:
            await self.engine.record_conversation_turn(
                role="user",
                content=payload.user_utterance,
                source="voice",
                turn_type=TurnType.INTERRUPT_UTTERANCE.value,
                turn_metadata={
                    "classification": classification.type.value,
                    "confidence": classification.confidence,
                },
            )
        except Exception as e:
            logger.error(f"[VOICE-RESUMPTION] INTERRUPT_UTTERANCE write failed: {e}")

    async def snapshot(self) -> dict:
        """Snapshot state."""
        base = await super().snapshot()
        base.update({
            "model": self._model,
            "generating": self._generating,
            "abort_requested": self._abort_requested,
            "local_available": self.local_available,
            "local_generations": self._local_generations,
            "delegated_generations": self._delegated_generations,
        })

        # Add local inference stats if available
        if self._local is not None:
            base["local_stats"] = self._local.get_stats()

        return base

    @property
    def is_generating(self) -> bool:
        """Check if currently generating."""
        return self._generating

    def get_routing_stats(self) -> dict:
        """Get routing statistics."""
        total = self._local_generations + self._delegated_generations
        local_pct = (self._local_generations / total * 100) if total > 0 else 0

        stats = {
            "local_generations": self._local_generations,
            "delegated_generations": self._delegated_generations,
            "total_generations": total,
            "local_percentage": local_pct,
            "local_available": self.local_available,
        }

        if self._local is not None:
            stats["local_model_stats"] = self._local.get_stats()

        # Add fallback chain stats if available
        if self._fallback_chain is not None:
            stats["fallback_chain"] = self._fallback_chain.get_stats()

        return stats

    def set_next_overrides(self, overrides: dict) -> None:
        """Set one-shot overrides for the next message processed. Cleared after use."""
        self._next_message_overrides = overrides
        logger.debug(f"[SIMULATE] One-shot overrides set: {list(overrides.keys())}")

    def get_last_system_prompt(self) -> dict:
        """
        Get the last system prompt sent to the LLM.

        Used by /prompt slash command for debugging context issues.

        Returns:
            Dict with system_prompt, route_decision, and truncated preview
        """
        if self._last_system_prompt is None:
            return {
                "available": False,
                "message": "No generation has occurred yet. Send a message first.",
            }

        prompt = self._last_system_prompt
        return {
            "available": True,
            "route_decision": self._last_route_decision or "unknown",
            "length": len(prompt),
            "preview": prompt[:500] + "..." if len(prompt) > 500 else prompt,
            "full_prompt": prompt,
            "assembler": self._last_prompt_meta,
            "packager_advisory": self._last_packager_advisory,
        }

    # ── Register Toggle & Debug ─────────────────────────────────────

    def set_register_enabled(self, enabled: bool) -> None:
        """Toggle context register injection on/off."""
        self._register_enabled = enabled
        logger.info("[REGISTER] %s", "enabled" if enabled else "disabled")

    def get_register_state(self) -> dict:
        """
        Full debug dump of register + sovereignty state.

        Used by /register CLI command and /slash/register API.
        """
        reg = self._register_state

        # Bridge / sovereignty info
        bridge_info = {"entity_id": None, "luna_tier": None,
                       "dataroom_tier": None, "is_sovereign": False}
        try:
            engine = getattr(self, '_engine', None) or getattr(self, 'engine', None)
            if engine:
                identity_actor = engine.get_actor("identity") if hasattr(engine, 'get_actor') else None
                if identity_actor and hasattr(identity_actor, 'current') and identity_actor.current.is_present:
                    current = identity_actor.current
                    bridge_info = {
                        "entity_id": current.entity_name,
                        "luna_tier": current.luna_tier,
                        "dataroom_tier": current.dataroom_tier,
                        "is_sovereign": current.dataroom_tier == 1,
                    }
        except Exception:
            pass

        return {
            "enabled": self._register_enabled,
            "register": reg.to_dict(),
            "intent": {
                "mode": self._last_classified_mode.value,
            },
            "bridge": bridge_info,
            "denied_docs": self._last_denied_count,
        }

    def log_prompt_archaeology(self, query: str, response: str = "") -> dict:
        """
        Log detailed prompt analysis for archaeology investigation.

        Captures the full prompt with section breakdowns and token estimates.
        Writes to data/diagnostics/prompt_archaeology.jsonl

        Args:
            query: The user's query that triggered this prompt
            response: Optional response text for correlation

        Returns:
            Dict with full analysis including section breakdown
        """
        import json
        import re
        from datetime import datetime
        from pathlib import Path

        if self._last_system_prompt is None:
            return {"error": "No prompt available"}

        prompt = self._last_system_prompt

        # Section detection patterns
        section_patterns = [
            (r"^You are Luna.*?(?=\n##|\n###|$)", "IDENTITY_PREAMBLE"),
            (r"## Who You Are.*?(?=\n##|$)", "WHO_YOU_ARE"),
            (r"## Your Voice.*?(?=\n##|$)", "YOUR_VOICE"),
            (r"## Core Principles.*?(?=\n##|$)", "CORE_PRINCIPLES"),
            (r"### Core Identity.*?(?=\n###|\n##|$)", "CORE_IDENTITY"),
            (r"### Base Tone.*?(?=\n###|\n##|$)", "BASE_TONE"),
            (r"### Default Communication Patterns.*?(?=\n###|\n##|$)", "COMM_PATTERNS"),
            (r"### Inviolable Principles.*?(?=\n###|\n##|$)", "INVIOLABLE_PRINCIPLES"),
            (r"### Style Mechanics.*?(?=\n###|\n##|$)", "STYLE_MECHANICS"),
            (r"### Emoji Usage.*?(?=\n###|\n##|$)", "EMOJI_USAGE"),
            (r"### Formality.*?(?=\n###|\n##|$)", "FORMALITY"),
            (r"### Voice Examples.*?(?=\n###|\n##|$)", "VOICE_EXAMPLES"),  # SUSPECT
            (r"## Your Foundation.*?(?=\n##|$)", "DNA_FOUNDATION"),
            (r"## Who You've Become.*?(?=\n##|$)", "EXPERIENCE_LAYER"),
            (r"## Right Now.*?(?=\n##|$)", "MOOD_LAYER"),
            (r"## THIS SESSION.*?(?=\n##|$)", "THIS_SESSION"),
            (r"## KNOWN PEOPLE.*?(?=\n##|$)", "KNOWN_PEOPLE"),
            (r"## RETRIEVED CONTEXT.*?(?=\n##|$)", "RETRIEVED_CONTEXT"),
            (r"## Memory Context.*?(?=\n##|$)", "MEMORY_CONTEXT"),
        ]

        # Extract sections
        sections = {}
        total_tokens = 0

        for pattern, name in section_patterns:
            match = re.search(pattern, prompt, re.DOTALL | re.MULTILINE)
            if match:
                content = match.group(0)
                char_count = len(content)
                token_estimate = char_count // 4
                sections[name] = {
                    "chars": char_count,
                    "tokens_approx": token_estimate,
                    "preview": content[:200] + "..." if len(content) > 200 else content,
                }
                total_tokens += token_estimate

        # Calculate ratios
        for section in sections.values():
            if total_tokens > 0:
                section["percent"] = round(section["tokens_approx"] / total_tokens * 100, 1)
            else:
                section["percent"] = 0

        # Flag suspicious sections
        voice_examples = sections.get("VOICE_EXAMPLES", {})
        voice_tokens = voice_examples.get("tokens_approx", 0)
        pollution_warning = None
        if voice_tokens > 100:
            pollution_warning = f"VOICE_EXAMPLES is {voice_tokens} tokens ({voice_examples.get('percent', 0)}%) — suspect for copying"

        analysis = {
            "timestamp": datetime.now().isoformat(),
            "query": query,
            "response_preview": response[:200] if response else "",
            "route": self._last_route_decision or "unknown",
            "total_chars": len(prompt),
            "total_tokens_approx": total_tokens,
            "sections": sections,
            "pollution_warning": pollution_warning,
            "full_prompt": prompt,
        }

        # Write to JSONL file
        log_path = local_dir() / "diagnostics" / "prompt_archaeology.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(analysis) + "\n")

        logger.info(f"[ARCHAEOLOGY] Logged prompt: {total_tokens} tokens, {len(sections)} sections")
        if pollution_warning:
            logger.warning(f"[ARCHAEOLOGY] {pollution_warning}")

        return analysis

    # =========================================================================
    # Session Tracking for Reflection Loop
    # =========================================================================

    def track_message(self, role: str, content: str) -> None:
        """
        Track a message in the session history for later reflection.

        Args:
            role: 'user' or 'assistant'
            content: The message content
        """
        if self._session_start_time is None:
            self._session_start_time = time.time()

        self._session_history.append({
            "role": role,
            "content": content,
            "timestamp": time.time(),
        })

        # Check if reflection should be triggered based on interaction count
        if self._reflection_loop:
            # This just increments the counter, actual reflection happens at session_end
            asyncio.create_task(self._maybe_auto_reflect())

    async def _maybe_auto_reflect(self) -> None:
        """Check if we should auto-reflect based on interaction count."""
        if not self._reflection_loop:
            return

        if await self._reflection_loop.should_reflect():
            logger.info("Auto-reflection triggered by interaction count")
            await self.session_end_reflection(source="auto")

    async def session_end_reflection(self, user_name: str = None, source: str = "session_end") -> Optional[dict]:
        """
        Trigger reflection at session end to generate personality patches.

        Analyzes the session history and generates a new patch if significant
        personality evolution is detected.

        Args:
            user_name: Name of the user for the reflection prompt
            source: What triggered this ("session_end", "auto", "user_requested")

        Returns:
            Dict with reflection results, or None if reflection not available
        """
        if user_name is None:
            from luna.core.owner import get_owner as _get_owner
            user_name = _get_owner().display_name or "User"
        if not self._reflection_loop or not self._patch_manager:
            logger.debug("Reflection loop or patch manager not available")
            return None

        # Check trigger_points flags based on source
        if source == "session_end" and not self._reflection_loop.trigger_session_end:
            logger.debug("Session-end reflection disabled in config")
            return None
        if source == "user_requested" and not self._reflection_loop.trigger_user_requested:
            logger.debug("User-requested reflection disabled in config")
            return None

        if not self._session_history:
            logger.debug("No session history to reflect on")
            return None

        try:
            # Get current active patches for context
            current_patches = await self._patch_manager.get_all_active_patches(limit=10)

            # Define the LLM generation function for reflection
            async def llm_generate(prompt: str) -> str:
                return await self.generate(prompt, max_tokens=500)

            # Run reflection
            logger.info(f"Running session-end reflection on {len(self._session_history)} messages")
            new_patch = await self._reflection_loop.generate_reflection(
                session_history=self._session_history,
                current_patches=current_patches,
                llm_generate=llm_generate,
                user_name=user_name,
            )

            # Also reinforce existing patches based on session content
            reinforced = await self._reflection_loop.reinforce_existing_patches(
                session_history=self._session_history,
                current_patches=current_patches,
            )

            result = {
                "new_patch": new_patch.to_dict() if new_patch else None,
                "reinforced_patches": reinforced,
                "session_messages": len(self._session_history),
            }

            logger.info(f"Reflection complete: new_patch={new_patch is not None}, reinforced={len(reinforced)}")
            return result

        except Exception as e:
            logger.error(f"Session reflection failed: {e}")
            return {"error": str(e)}

    def clear_session(self) -> None:
        """Clear the session history for a fresh start."""
        self._session_history = []
        self._session_start_time = None
        if self._reflection_loop:
            self._reflection_loop._interaction_count = 0
        # Reset voice system for fresh conversation
        self._voice_turn_count = 0
        if self._voice_orchestrator:
            self._voice_orchestrator.on_conversation_start()
        # Reset perception field for fresh observation slate
        self._perception_field.reset()
        self._perception_turn_count = 0
        # Reset curiosity buffer for fresh session
        try:
            engine = getattr(self, '_engine', None) or getattr(self, 'engine', None)
            if engine and hasattr(engine, 'consciousness'):
                engine.consciousness.curiosity.reset()
        except Exception:
            pass
        logger.debug("Session cleared")

    def get_session_stats(self) -> dict:
        """Get statistics about the current session."""
        duration_seconds = 0
        if self._session_start_time:
            duration_seconds = time.time() - self._session_start_time

        return {
            "message_count": len(self._session_history),
            "duration_seconds": duration_seconds,
            "user_messages": sum(1 for m in self._session_history if m["role"] == "user"),
            "assistant_messages": sum(1 for m in self._session_history if m["role"] == "assistant"),
        }

    # =========================================================================
    # Direct Generation API (for AgentLoop)
    # =========================================================================

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> str:
        """
        Generate a response using the appropriate backend.

        Checks delegation signals to decide local vs cloud.
        This is the direct async interface for AgentLoop to use.

        Args:
            prompt: The prompt to generate from
            system: Optional system prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature

        Returns:
            Generated text response
        """
        should_delegate = await self._should_delegate(prompt)

        if should_delegate or not self.local_available:
            return await self._generate_cloud(prompt, system, max_tokens, temperature)
        else:
            return await self._generate_local_direct(prompt, system, max_tokens, temperature)

    async def _generate_cloud(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> str:
        """Generate using LLM registry provider (or Claude fallback)."""
        if not system:
            import traceback
            logger.error(
                "[SYSTEM-PROMPT-MISSING] director._generate_cloud called with system=None — "
                "caller must use PromptAssembler.build(). Last 5 frames:\n%s",
                "".join(traceback.format_stack()[-5:]),
            )
            system = (
                "⚠️ INTERNAL: Luna's voice configuration didn't load. The caller did not "
                "provide an assembled system prompt. This is a bug — the response will "
                "read as a generic AI assistant. Please report this with the request ID."
            )

        # Try LLM registry first
        if LLM_REGISTRY_AVAILABLE:
            try:
                provider = get_provider()
                if provider and provider.is_available:
                    messages = [
                        LLMMessage("system", system),
                        LLMMessage("user", prompt),
                    ]
                    result = await provider.complete(
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    self._delegated_generations += 1
                    logger.info(f"Generated via {provider.name}: {result.usage}")
                    self._apply_voice_pass(result.content, source="cloud")
                    return result.content
            except Exception as e:
                logger.warning(f"LLM registry generation failed, falling back to Claude: {e}")

        # Fallback to FallbackChain (preserves full system prompt, never degrades identity)
        if self._fallback_chain:
            fc_messages = [{"role": "user", "content": prompt}]
            result = await self._fallback_chain.generate(
                messages=fc_messages,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            self._delegated_generations += 1
            self._apply_voice_pass(result.content, source="cloud")
            return result.content

        # Last resort: direct Anthropic client — only reached if FallbackChain is unconfigured
        if not self._client:
            _ = self.client  # Lazy init

        messages = [{"role": "user", "content": prompt}]

        response = self.client.messages.create(
            model=self._claude_model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )

        self._delegated_generations += 1
        _text = response.content[0].text
        self._apply_voice_pass(_text, source="cloud")
        return _text

    async def _generate_local_direct(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> str:
        """Generate using local inference (synchronous, not streaming)."""
        if not self._local:
            raise RuntimeError("Local inference not available")

        full_prompt = prompt
        if system:
            full_prompt = f"{system}\n\n{prompt}"

        result = await self._local.generate(
            full_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        self._local_generations += 1
        _text = result.text if hasattr(result, 'text') else str(result)
        self._apply_voice_pass(_text, source="local_direct")
        return _text
