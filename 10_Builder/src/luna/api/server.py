"""
Luna Engine API Server
======================

FastAPI server exposing Luna to HTTP clients.

Endpoints:
- POST /message - Send a message and get response (sync)
- POST /stream - Send a message and stream response (SSE)
- GET /status - Engine health and metrics
- GET /health - Health check
"""

# Load .env before any other imports (providers check env at import time)
import os
from pathlib import Path
from luna.core.paths import project_root, config_dir, data_dir, tools_dir, scripts_dir, frontend_dir, user_dir, local_dir, memory_matrix_path
try:
    from dotenv import load_dotenv
    _env_path = project_root() / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass  # dotenv not installed

import asyncio
from copy import deepcopy
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Optional, AsyncGenerator

import httpx
from fastapi import Depends, FastAPI, HTTPException, BackgroundTasks, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from luna.services.orb_state import OrbStateManager, ExpressionConfig
from luna.services.dimensional_engine import DimensionalEngine
from luna.services.performance_state import VoiceKnobs, OrbKnobs, EMOTION_PRESETS, EmotionPreset
from luna.services.performance_orchestrator import PerformanceOrchestrator

from luna.engine import LunaEngine, EngineConfig
from luna.core.events import InputEvent, EventType
from luna.actors.base import Message
from luna.agentic.router import ExecutionPath
from luna.context.assembler import PromptRequest
from luna.diagnostics import run_startup_check
from luna.diagnostics.trace_builder import TraceBuilder
from luna.diagnostics.trace_config import load_trace_config
from luna.diagnostics.trace_reader import TraceReader
from luna.diagnostics.trace_writer import TraceWriter
from luna.services.kozmo.routes import router as kozmo_router
from luna.services.guardian.routes import router as guardian_router
from luna.utils.options_parser import extract_options, build_options_widget
from luna.services.settings.routes import router as settings_router
from luna.services.settings.lunafm_routes import router as lunafm_settings_router
from luna.services.observatory.routes import router as observatory_router
from luna.services.guardian.memory_bridge import GuardianMemoryBridge
from luna.compiler import KnowledgeCompiler
from luna.compiler.conversation_extractor import ConversationExtractor, ExtractResult
from luna.compiler.markdown_export import MarkdownExporter, ExportResult
from luna.grounding import GroundingLink
from luna.topology import ThreadAttachmentStatus, TopologyAttachmentService
from luna.auth import (
    ProfileRegistry,
    ProfileNotFoundError,
    SESSION_COOKIE_NAME,
    auth_middleware,
    current_session,
    make_session_token,
    require_admin,
    require_auth,
)
from luna.auth.middleware import CurrentSession
from luna.api.engine_dispatch import request_engine

# QA System imports
try:
    from luna.qa import QAValidator, InferenceContext, get_default_assertions
    from luna.qa.validator import get_validator as get_qa_validator
    from luna.qa.assertions import Assertion, PatternConfig
    QA_AVAILABLE = True
except ImportError:
    QA_AVAILABLE = False

logger = logging.getLogger(__name__)


_DEFAULT_FRONTEND_CONFIG = {
    "pages": {
        "eclissi": True,
        "studio": True,
        "kozmo": False,
        "guardian": True,
        "observatory": True,
        "settings": True,
    },
    "widgets": {
        "engine": True,
        "voice": True,
        "memory": True,
        "qa": True,
        "prompt": True,
        "debug": True,
        "vk": True,
        "arcade": False,
        "cache": True,
        "thought": True,
        "lunascript": True,
        "radio": True,
        "director_console": True,
    },
    "remap": {},
    "settings": {},
    "debug_mode": True,
    "demo_mode": False,
    "has_preloaded_keys": False,
}


def _load_frontend_config_data() -> dict:
    """Load frontend feature config with safe defaults."""
    config_path = config_dir() / "frontend_config.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text())
        except Exception as e:
            logger.warning("Failed to load frontend_config.json: %s", e)
    return deepcopy(_DEFAULT_FRONTEND_CONFIG)


def _merge_config_dict(base: dict, updates: dict) -> dict:
    """Recursively merge frontend config updates into base config."""
    merged = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _save_frontend_config_data(config: dict) -> None:
    """Persist frontend config to config/frontend_config.json."""
    config_path = config_dir() / "frontend_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n")


def _frontend_page_enabled(page: str) -> bool:
    return _load_frontend_config_data().get("pages", {}).get(page, True)


def _frontend_widget_enabled(widget: str) -> bool:
    return _load_frontend_config_data().get("widgets", {}).get(widget, True)


def _eden_enabled() -> bool:
    from luna.services.eden.policy import EdenPolicy

    return EdenPolicy.load().enabled


def _ensure_arcade_enabled() -> None:
    if not _frontend_widget_enabled("arcade"):
        logger.info("Arcade endpoint blocked: arcade widget is offline")
        raise HTTPException(status_code=503, detail="Arcade is offline")


# =============================================================================
# Background Task Registry — track all fire-and-forget tasks
# =============================================================================

_background_tasks: set[asyncio.Task] = set()


def _track_task(coro, *, name: str = None) -> asyncio.Task:
    """Create a tracked background task with error logging."""
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task):
        _background_tasks.discard(t)
        if not t.cancelled() and t.exception():
            logger.error("Background task %s failed: %s", t.get_name(), t.exception())

    task.add_done_callback(_on_done)
    return task


async def _cancel_all_background_tasks() -> None:
    """Cancel and await all tracked background tasks."""
    if not _background_tasks:
        return
    logger.info("Cancelling %d background tasks...", len(_background_tasks))
    for task in list(_background_tasks):
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()


async def _fetch_matrix_context_with_door_routing(
    engine: LunaEngine,
    matrix_actor,
    query: str,
    *,
    max_tokens: int = 1500,
) -> str:
    """Fetch matrix context and ingest the same candidates through Door routing.

    Primary path uses the inner matrix node API so we can both:
    - add candidates to `RevolvingContext` via `Door.MEMORY_MATRIX`
    - produce the same formatted text block for legacy prompt consumers

    Falls back to `matrix_actor.get_context(...)` on any incompatibility.
    """
    if not matrix_actor or not getattr(matrix_actor, "is_ready", False):
        return ""

    inner_matrix = getattr(matrix_actor, "_matrix", None)
    formatter = getattr(matrix_actor, "_format_context", None)
    if inner_matrix is None or not callable(formatter):
        return await matrix_actor.get_context(query, max_tokens=max_tokens)

    try:
        nodes = await inner_matrix.get_context(query, max_tokens=max_tokens)
    except Exception as e:
        logger.warning("[STREAM] matrix node fetch failed, using legacy text fetch: %s", e)
        return await matrix_actor.get_context(query, max_tokens=max_tokens)

    if not nodes:
        return ""

    from luna.core.context import ContextSource as _CS
    from luna.core.types import Door as _Door

    for node in nodes:
        score = getattr(node, "_retrieval_score", 1.0)
        text = getattr(node, "summary", None) or getattr(node, "content", "")
        if not text:
            continue
        engine.context.add(
            content=text,
            source=_CS.MEMORY,
            door=_Door.MEMORY_MATRIX,
            relevance=score,
        )

    try:
        return formatter(nodes) or ""
    except Exception as e:
        logger.warning("[STREAM] matrix format failed, using legacy text fetch: %s", e)
        return await matrix_actor.get_context(query, max_tokens=max_tokens)


# =============================================================================
# QA Background Validation
# =============================================================================

async def _run_qa_validation_background(
    query: str,
    response_text: str,
    response_data: dict,
) -> None:
    """
    Run QA validation in background (fire-and-forget).

    This does not block the response to the user.
    """
    if not QA_AVAILABLE:
        return

    try:
        validator = get_qa_validator()

        # Yield event loop so director can finish writing _last_system_prompt
        # before we read it. The background task fires immediately after the
        # response callback, which runs in the same processing cycle as the
        # prompt write — one yield ensures the write completes first.
        await asyncio.sleep(0)

        # Get personality info from director
        personality_injected = False
        personality_length = 0
        system_prompt = ""
        voice_injected = False
        virtues_loaded = False
        narration_applied = response_data.get("narration_applied", False)

        if _engine:
            director = _engine.get_actor("director")
            if director:
                prompt_info = director.get_last_system_prompt()
                if prompt_info.get("available"):
                    system_prompt = prompt_info.get("full_prompt", "")
                    personality_length = prompt_info.get("length", 0)
                    personality_injected = personality_length > 1000
                    assembler_meta = prompt_info.get("assembler") or {}
                    voice_injected = bool(assembler_meta.get("voice_injected", False))
                    # Check if virtues/identity loaded
                    virtues_loaded = getattr(director, "_identity_buffer", None) is not None

        # Get memory stats for QA assertions.
        # Cold-start race: on the first inference after a backend restart the
        # matrix actor may exist before its MemoryMatrix is wired. Poll briefly
        # for warmth — empty memory_stats here cascades into misleading
        # I1/I2/I3 failures downstream.
        memory_stats = {}
        if _engine:
            matrix = _engine.get_actor("matrix")
            if not matrix:
                logger.warning("QA_STATS: engine.get_actor('matrix') returned None")
            else:
                mem = None
                for _ in range(5):  # up to ~1s of warmth wait
                    mem = getattr(matrix, "matrix", None) or getattr(matrix, "_matrix", None)
                    if mem is not None:
                        break
                    await asyncio.sleep(0.2)

                if mem is None:
                    logger.warning(
                        "QA_STATS: matrix actor present but .matrix/_matrix is None after 1s wait | "
                        f"matrix_type={type(matrix).__name__}"
                    )
                else:
                    try:
                        memory_stats = await mem.get_stats()
                        # Defensive retry if edges read as 0 — guards against a
                        # transient query during late-init.
                        if memory_stats.get("total_edges", 0) == 0:
                            await asyncio.sleep(0.1)
                            memory_stats = await mem.get_stats()
                    except Exception as e:
                        logger.warning(
                            f"QA_STATS: get_stats() raised — error_type={type(e).__name__} error={e}"
                        )

        # Build inference context from response data
        ctx = InferenceContext(
            query=query,
            final_response=response_text,
            raw_response=response_text,
            provider_used=response_data.get("model", "unknown"),
            latency_ms=response_data.get("latency_ms", 0),
            input_tokens=response_data.get("input_tokens", 0),
            output_tokens=response_data.get("output_tokens", 0),
            # Infer route from delegation flags
            route="FULL_DELEGATION" if response_data.get("delegated") else "LOCAL_ONLY",
            # Mark if local model was used
            providers_tried=[response_data.get("model", "unknown")],
            # Personality tracking
            personality_injected=personality_injected,
            personality_length=personality_length,
            system_prompt=system_prompt,
            voice_injected=voice_injected,
            virtues_loaded=virtues_loaded,
            narration_applied=narration_applied,
            memory_stats=memory_stats,
            memory_confidence_level=getattr(getattr(director, '_last_memory_confidence', None), 'level', '') if director else '',
        )

        # Run validation (this stores the report automatically)
        report = validator.validate(ctx)

        if not report.passed:
            logger.warning(
                f"[QA] Inference failed {report.failed_count} assertions: "
                f"{[a.id for a in report.failed_assertions]}"
            )
        else:
            logger.debug(f"[QA] Inference passed all {len(report.assertions)} assertions")

    except Exception as e:
        logger.error(f"[QA] Background validation error: {e}")

# Global engine instance
_engine: Optional[LunaEngine] = None
_trace_writer: Optional[TraceWriter] = None
_trace_reader: Optional[TraceReader] = None

# Guardian memory bridge
_guardian_bridge: Optional[GuardianMemoryBridge] = None
_knowledge_compiler: Optional[KnowledgeCompiler] = None
_grounding_link: Optional[GroundingLink] = GroundingLink()

# Global orb state manager and WebSocket connections
_orb_state_manager: Optional[OrbStateManager] = None
_orb_websockets: set[WebSocket] = set()

# Global chat WebSocket connections (for shared session viewing)
_chat_websockets: dict[WebSocket, Optional[str]] = {}

# Global identity WebSocket connections (FaceID state)
_identity_websockets: set[WebSocket] = set()

# Global knowledge event WebSocket connections (extraction pipeline events)
_knowledge_websockets: set[WebSocket] = set()

# Global performance orchestrator (coordinates voice + orb)
_performance_orchestrator: Optional[PerformanceOrchestrator] = None


def _get_trace_writer() -> Optional[TraceWriter]:
    """Build (or refresh) trace writer from current diagnostics config."""
    global _trace_writer
    cfg = load_trace_config()
    if not cfg.enabled:
        return None
    if _trace_writer is None or _trace_writer.db_path != cfg.db_path:
        _trace_writer = TraceWriter(cfg.db_path, cfg)
    return _trace_writer


def _get_trace_reader() -> Optional[TraceReader]:
    """Build (or refresh) trace reader from current diagnostics config."""
    global _trace_reader
    cfg = load_trace_config()
    if not cfg.enabled:
        return None
    if _trace_reader is None or _trace_reader.db_path != cfg.db_path:
        _trace_reader = TraceReader(cfg.db_path)
    return _trace_reader


# ── Security helpers ─────────────────────────────────────────────────────────

async def _resolve_current_bridge():
    """Get the current speaker's BridgeResult from FaceID → AccessBridge."""
    if _engine is None:
        return None
    identity_actor = _engine.get_actor("identity")
    if not identity_actor or not identity_actor.current.is_present:
        return None
    entity_id = identity_actor.current.entity_id
    if not entity_id:
        return None
    try:
        from luna.identity.bridge import AccessBridge
        matrix = _engine.get_actor("matrix")
        mem = getattr(matrix, "_matrix", None) if matrix else None
        if not mem:
            return None
        bridge = AccessBridge(mem.db)
        return await bridge.lookup(entity_id)
    except Exception:
        return None


async def _require_admin():
    """Require admin identity via FaceID. Raises 403 if not admin."""
    bridge = await _resolve_current_bridge()
    if bridge is None or bridge.luna_tier != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin identity required (FaceID not recognized or insufficient tier)",
        )
    return bridge


async def _gate_results(results: list, source: str = "api") -> list:
    """Apply permission gate to a list of result dicts/nodes. Returns allowed list."""
    from luna.identity.permissions import gate_content
    from luna.identity.bridge import BridgeResult
    bridge = await _resolve_current_bridge()
    db = None
    try:
        if _engine:
            matrix = _engine.get_actor("matrix")
            mem = getattr(matrix, "_matrix", None) if matrix else None
            if mem:
                db = mem.db
    except Exception:
        pass

    # MCP API calls are local/trusted — if no FaceID identity is active,
    # treat as admin rather than denying all DOCUMENT nodes silently.
    # The MCP server itself is authenticated by the session that started it.
    if bridge is None and source.startswith("api/"):
        bridge = BridgeResult(
            entity_id="mcp-local",
            luna_tier="admin",
            dataroom_tier=1,
            dataroom_categories=[],
        )
        logger.debug("GATE: No FaceID active — using admin fallback for MCP source '%s'", source)

    allowed, _denied = await gate_content(results, bridge, db=db, source=source)
    return allowed


# ─────────────────────────────────────────────────────────────────────────────

def _normalize_chat_client_id(client_id: Any) -> Optional[str]:
    """Normalize optional Eclissi chat client IDs for origin-aware broadcasts."""
    if client_id is None:
        return None
    value = str(client_id).strip()
    if not value:
        return None
    return value[:128]


async def _broadcast_chat_message(
    message_type: str,
    data: dict,
    origin_client_id: Optional[str] = None,
) -> None:
    """
    Broadcast a chat message to all connected WebSocket clients.

    This enables multiple viewers to see the same conversation in real-time.
    message_type: 'user' | 'assistant' | 'system'
    """
    if not _chat_websockets:
        return
    origin_client_id = _normalize_chat_client_id(origin_client_id)

    payload = {
        "type": message_type,
        "data": data,
        "timestamp": asyncio.get_event_loop().time(),
    }

    disconnected = set()
    for ws, client_id in list(_chat_websockets.items()):
        if origin_client_id and client_id == origin_client_id:
            continue
        try:
            await ws.send_json(payload)
        except Exception:
            disconnected.add(ws)

    for ws in disconnected:
        _chat_websockets.pop(ws, None)


async def _lookup_pending_interrupt_type() -> Optional[str]:
    """Return the classification of the most recent unresolved INTERRUPT_UTTERANCE.

    The Consolidator stamps `metadata.classification` (ADDITIVE/REDIRECTING/
    CLARIFYING) on INTERRUPT_UTTERANCE rows. An interrupt is "unresolved" when
    no assistant turn has been written after it — i.e. the response we're
    about to emit IS the resumption. Returns the classification string in that
    case, otherwise None.
    """
    try:
        import aiosqlite
        from luna.core.paths import user_dir

        db_path = memory_matrix_path()
        if not db_path.exists():
            return None

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=5000")
            cursor = await db.execute(
                "SELECT id, metadata FROM conversation_turns "
                "WHERE turn_type = 'INTERRUPT_UTTERANCE' "
                "ORDER BY id DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            if not row:
                return None

            interrupt_id = row["id"]
            cursor = await db.execute(
                "SELECT 1 FROM conversation_turns "
                "WHERE id > ? AND role = 'assistant' LIMIT 1",
                (interrupt_id,),
            )
            if await cursor.fetchone():
                # Already resumed — not relevant to the response we're emitting.
                return None

        if not row["metadata"]:
            return None
        try:
            meta = json.loads(row["metadata"])
        except (TypeError, ValueError):
            return None
        classification = meta.get("classification")
        if not classification:
            return None
        normalized = str(classification).upper()
        if normalized not in ("ADDITIVE", "REDIRECTING", "CLARIFYING", "CANCEL"):
            return None
        return normalized
    except Exception as e:
        logger.debug(f"[INTERRUPT-TYPE] lookup failed: {e}")
        return None


class MessageRequest(BaseModel):
    """Request body for /message endpoint."""
    message: str = Field(..., min_length=1, max_length=10000)
    timeout: float = Field(default=30.0, ge=1.0, le=120.0)
    stream: bool = Field(default=False, description="Use streaming mode")
    source: str = Field(default="api", description="Origin surface: eclissi, mcp, voice, guardian, api")
    client_id: Optional[str] = Field(default=None, max_length=128, description="Origin chat client to exclude from /ws/chat rebroadcasts")
    # Per-turn metadata persisted on the user row (e.g. {"quoteEcho": {"text", "attribution"}})
    metadata: Optional[dict] = None


class MessageResponse(BaseModel):
    """Response from /message endpoint."""
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    # Routing indicators
    delegated: bool = False
    local: bool = False
    fallback: bool = False
    # GroundingLink traceability (Phase 2)
    groundingMetadata: Optional[dict] = None
    # Phase 5 chat-UI metadata — present only when applicable
    interrupt_type: Optional[str] = None       # T6 InterruptStrip: ADDITIVE/REDIRECTING/CLARIFYING/CANCEL
    memoryAnchor: Optional[dict] = None        # T5 MemoryAnchor: {id, label, sessionRef}
    quoteEcho: Optional[dict] = None           # T3 QuoteEcho: {text, attribution} (echoed back from request)
    content_type: Optional[str] = None         # 'agent' triggers AgentReactBody slot
    actors: Optional[list] = None              # AgentReactBody actor grid: [{name, role, status, contribution}]
    # Director + Lexicon pivot — Phase B shadow telemetry. Present only when
    # DIRECTOR_PIVOT_MODE=shadow and the endpoint is allowlisted; null otherwise.
    directorPivotShadow: Optional[dict] = None
    # Director + Lexicon pivot — Phase C active telemetry. Present only when
    # DIRECTOR_PIVOT_MODE=active and the endpoint is allowlisted; null otherwise.
    directorPivotActive: Optional[dict] = None


class PrepareContextRequest(BaseModel):
    """Request body for /context/prepare endpoint.

    Prepare runs the canonical assembly pipeline but stops before inference,
    returning the assembled context bundle. Used by MCP callers (Claude Desktop)
    that will run their own inference against the bundle.
    """
    message: str = Field(..., min_length=1, max_length=10000)
    # Retrieval is the dominant cost; 30s covers warm retrieval + assembly.
    # Cold starts (BGE model load) can push closer to 20s, so give headroom.
    timeout: float = Field(default=30.0, ge=1.0, le=90.0)
    source: str = Field(default="mcp", description="Origin surface for the prepare call")
    verbose: bool = Field(default=False, description="Include pipeline trace payload")
    session_id: Optional[str] = Field(default=None, description="Optional caller session id for trace grouping")


class PrepareContextResponse(BaseModel):
    """Assembled context bundle from /context/prepare.

    `system_prompt` + `messages` together form the LLM call the caller should make.
    `diagnostics` is observability metadata — ring distribution, budget usage,
    retrieval sources, wall-clock assembly time.
    """
    system_prompt: str
    messages: list
    memory_context: str
    framed_context: str
    prompt_tokens: int
    route: str
    assembler_meta: dict
    engine_connected: bool = True
    diagnostics: dict
    trace_id: Optional[str] = None
    trace: Optional[dict] = None


class TraceListItem(BaseModel):
    id: str
    ts: float
    session_id: Optional[str] = None
    turn_num: Optional[int] = None
    query: str
    route: Optional[str] = None
    backend: Optional[str] = None
    prepare_only: bool
    latency_ms: Optional[int] = None
    token_count: Optional[int] = None
    token_budget: Optional[int] = None
    stage_count: int = 0
    candidate_count: int = 0


class TraceListResponse(BaseModel):
    items: list[TraceListItem]
    total: int
    limit: int
    offset: int


class TraceDiffRequest(BaseModel):
    a: str = Field(..., min_length=1)
    b: str = Field(..., min_length=1)


class TraceDiffResponse(BaseModel):
    a_id: str
    b_id: str
    stage_diffs: list[dict]
    candidate_diffs: dict
    ring_diffs: list[dict]
    layer_diffs: list[dict]
    summary: str


class AgenticStats(BaseModel):
    """Agentic processing statistics."""
    is_processing: bool
    current_goal: Optional[str] = None
    pending_messages: int
    tasks_started: int
    tasks_completed: int
    tasks_aborted: int
    direct_responses: int
    planned_responses: int
    agent_loop_status: str


class IdentityState(BaseModel):
    """Current FaceID identity state."""
    enabled: bool = False
    is_present: bool = False
    entity_name: Optional[str] = None
    luna_tier: str = "unknown"
    confidence: float = 0.0


class StatusResponse(BaseModel):
    """Response from /status endpoint."""
    state: str
    uptime_seconds: float
    cognitive_ticks: int
    events_processed: int
    messages_generated: int
    actors: list[str]
    buffer_size: int
    current_turn: int = 0  # Conversation turn counter (for context TTL)
    context: Optional[dict] = None  # Revolving context stats
    agentic: Optional[AgenticStats] = None  # Agentic processing stats
    identity: Optional[IdentityState] = None  # FaceID identity state


class HistoryMessage(BaseModel):
    """A single message in conversation history."""
    role: str
    content: str
    timestamp: Optional[str] = None
    # Per-turn metadata (e.g. quoteEcho) round-tripped from conversation_turns.metadata.
    # Lets T3/T5 visuals re-render after page reload.
    metadata: Optional[dict] = None


class HistoryResponse(BaseModel):
    """Response from /history endpoint."""
    messages: list[HistoryMessage]
    total: int


class ConsciousnessResponse(BaseModel):
    """Response from /consciousness endpoint."""
    mood: str
    coherence: float
    attention_topics: int
    focused_topics: list[dict]
    top_traits: list[tuple]
    tick_count: int
    last_updated: str


def _build_engine_config_from_env() -> EngineConfig:
    subtask_backend = os.getenv("LUNA_SUBTASK_BACKEND", "auto").lower()
    faceid_enabled = os.getenv("LUNA_FACEID_ENABLED", "false").lower() == "true"
    enable_local_inference = (
        os.getenv("LUNA_ENABLE_LOCAL_INFERENCE", "true").lower() == "true"
    )
    return EngineConfig(
        faceid_enabled=faceid_enabled,
        subtask_backend=subtask_backend,
        enable_local_inference=enable_local_inference,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage engine lifecycle with FastAPI lifespan.

    Engine starts on server startup, stops on shutdown.
    Critical systems are verified BEFORE anything else.
    """
    global _engine

    # =========================================================
    # CRITICAL SYSTEMS CHECK — Luna refuses to start with a
    # disconnected brain. This gate prevents silent failures
    # that cause confabulation.
    # =========================================================
    # ── Volume seed (idempotent) ─────────────────────────────────
    # On Railway etc., LUNA_DATA_DIR points at a persistent volume that starts
    # empty. Bundled config files ship in /app/config/ via Forge but the engine
    # reads from <LUNA_DATA_DIR>/config/. Seed any missing files (no-clobber)
    # so the volume has working defaults on first boot. User edits via /api/
    # settings/* land in <LUNA_DATA_DIR>/config/ and survive future redeploys.
    try:
        import shutil as _shutil
        _bundled_config = project_root() / "config"
        _volume_config = config_dir()
        if _bundled_config != _volume_config and _bundled_config.exists():
            _volume_config.mkdir(parents=True, exist_ok=True)
            _seeded = []
            for src_path in _bundled_config.iterdir():
                dst_path = _volume_config / src_path.name
                if dst_path.exists():
                    continue  # don't clobber user state
                if src_path.is_dir():
                    _shutil.copytree(str(src_path), str(dst_path))
                else:
                    _shutil.copy2(str(src_path), str(dst_path))
                _seeded.append(src_path.name)
            if _seeded:
                logger.info(
                    "Volume config seed: copied %d bundled file(s) to %s: %s",
                    len(_seeded), _volume_config, _seeded,
                )
    except Exception as e:
        logger.warning("Volume config seed failed (non-fatal): %s", e)

    # Load secrets.json into env before anything else
    try:
        from luna.services.settings.routes import _inject_secrets_to_env
        _inject_secrets_to_env()
        logger.info("Loaded config/secrets.json into environment")
    except Exception as e:
        logger.debug(f"No secrets.json to load: {e}")

    logger.info("Running critical systems check...")
    _strict = not os.environ.get("LUNA_DEV_MODE")
    run_startup_check(strict=_strict)  # LUNA_DEV_MODE=1 downgrades to warning-only

    # ── Profile system bootstrap (idempotent) ─────────────────────
    # On first boot of a new deployment, seed the admin profile from
    # LUNA_ADMIN_PASSWORD if no registry exists. After this point, all path
    # resolvers (user_dir, memory_matrix_path, etc.) resolve under the
    # admin's profile dir for non-authenticated code paths in this lifespan
    # task. Per-request auth middleware overrides via contextvar.
    from luna.auth.migration import auto_migrate_to_profile_system, is_migrated
    from luna.auth.registry import ProfileRegistry
    from luna.core.paths import set_current_profile

    if not is_migrated():
        admin_pw = os.environ.get("LUNA_ADMIN_PASSWORD")
        if admin_pw:
            try:
                _bootstrap_result = auto_migrate_to_profile_system(admin_password=admin_pw)
                logger.info(
                    "Profile system auto-bootstrapped: slug=%s reason=%s",
                    _bootstrap_result.slug, _bootstrap_result.reason,
                )
            except Exception as e:
                logger.error("Profile auto-bootstrap FAILED: %s", e)
                raise
        else:
            logger.warning(
                "Profile system not initialized — auth endpoints will return 503. "
                "Set LUNA_ADMIN_PASSWORD and restart, or run "
                "`python scripts/seed_profile.py migrate-admin` manually."
            )

    # Set default profile context for this lifespan task. Engine and all
    # downstream lifespan code inherit it. Per-request auth middleware
    # overrides via its own contextvar.set(...) + reset.
    if is_migrated():
        _registry = ProfileRegistry()
        _admins = [p for p in _registry.list_profiles() if p.tier == "admin"]
        if _admins:
            _default_slug = _admins[0].slug
            set_current_profile(_default_slug)
            logger.info("Lifespan default profile context: %s", _default_slug)

    logger.info("Starting Luna Engine...")

    # Create and start engine
    config = _build_engine_config_from_env()
    _engine = LunaEngine(config)

    # Start engine in background
    engine_task = asyncio.create_task(_engine.run())

    # Wait for actual readiness — not a fixed sleep. The engine signals
    # _ready_event at the end of _boot(); awaiting it surfaces boot failures
    # immediately instead of letting FastAPI start serving requests against
    # a dead engine (which projects every endpoint as cryptic
    # "Director not available" 503s rather than the real boot error).
    _READY_TIMEOUT_S = 30.0
    try:
        ready_wait = asyncio.create_task(_engine._ready_event.wait())
        done, _pending = await asyncio.wait(
            {ready_wait, engine_task},
            timeout=_READY_TIMEOUT_S,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if engine_task in done:
            # engine_task completed before signaling ready → boot failure
            ready_wait.cancel()
            exc = engine_task.exception()
            if exc is not None:
                logger.error("Engine boot failed: %s", exc, exc_info=exc)
                raise RuntimeError(f"Engine boot failed: {exc}") from exc
            raise RuntimeError("Engine task exited before signaling ready")
        if ready_wait not in done:
            ready_wait.cancel()
            raise RuntimeError(
                f"Engine did not signal ready within {_READY_TIMEOUT_S}s "
                f"(state={getattr(_engine, 'state', 'unknown')})"
            )
    except Exception:
        # Cancel the engine task on any failure so we don't leave a zombie
        # background task swallowing exceptions.
        if not engine_task.done():
            engine_task.cancel()
        raise

    # Initialize orb state manager
    global _orb_state_manager
    try:
        import json
        from pathlib import Path
        config_path = config_dir() / "personality.json"
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
                expression_config = ExpressionConfig.from_dict(config.get("expression", {}))
        else:
            expression_config = ExpressionConfig()
        _orb_state_manager = OrbStateManager(expression_config)
        logger.info("Orb state manager initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize orb state manager: {e}")
        _orb_state_manager = OrbStateManager()

    # Wire CacheActor → OrbStateManager (dimensional feed)
    if _engine:
        _cache_actor = _engine.get_actor("cache")
        if _cache_actor and hasattr(_cache_actor, "set_orb_state_manager"):
            _cache_actor.set_orb_state_manager(_orb_state_manager)
            logger.info("CacheActor wired to OrbStateManager")

    # Subscribe to identity state changes (FaceID → WebSocket broadcast)
    identity_actor = _engine.get_actor("identity")
    if identity_actor and hasattr(identity_actor, "on_change"):
        def _on_identity_change(current):
            _track_task(_broadcast_identity_state(), name="identity-broadcast")
        identity_actor.on_change(_on_identity_change)
        logger.info("Identity WebSocket broadcast wired")

    # Phase 1 — Engine Ownership: wire Engine-owned AiBrarian into consumers
    if _engine and getattr(_engine, "aibrarian", None) is not None:
        try:
            from luna_mcp.tools.aibrarian import set_engine as _mcp_set_engine
            _mcp_set_engine(_engine.aibrarian)
            logger.info("MCP aibrarian tools wired to Engine-owned AiBrarianEngine")
        except Exception as _e:
            logger.warning(f"Failed to wire MCP aibrarian tools: {_e}")
        try:
            from luna.tools.dataroom_tools import set_engine as _dr_set_engine, set_collection_key as _dr_set_key
            _dr_set_engine(_engine.aibrarian)
            # Use first enabled collection from registry as the dataroom key
            if hasattr(_engine.aibrarian, 'connections') and _engine.aibrarian.connections:
                _first_key = next(iter(_engine.aibrarian.connections))
                _dr_set_key(_first_key)
                logger.info("Dataroom collection key set to '%s'", _first_key)
            logger.info("Dataroom tools wired to Engine-owned AiBrarianEngine")
        except Exception as _e:
            logger.warning(f"Failed to wire dataroom tools: {_e}")

    # Wire ForgeWatcher into MCP tools
    if _engine and getattr(_engine, "forge_watcher", None) is not None:
        try:
            from luna_mcp.tools.aibrarian import set_forge_watcher
            set_forge_watcher(_engine.forge_watcher)
            logger.info("MCP forge tools wired to Engine-owned ForgeWatcher")
        except Exception as _e:
            logger.warning(f"Failed to wire ForgeWatcher into MCP: {_e}")

    # Wire engine into Guardian capability registry (A2A-style local contract)
    try:
        from luna.services.guardian.capabilities import get_registry as _get_cap_registry
        _get_cap_registry().set_engine(_engine)
        logger.info("Guardian capability registry wired to engine")
    except Exception as _e:
        logger.warning(f"Failed to wire Guardian capability registry: {_e}")

    # Start runtime watchdog for continuous health monitoring
    # Wire alerts into QA event store
    def _watchdog_to_qa(alert):
        try:
            sev = {"critical": "critical", "warning": "medium"}.get(alert.level, "medium")
            source = "health" if alert.system.startswith("health:") else "watchdog"
            component = alert.system.removeprefix("health:")
            get_qa_validator()._db.store_event(
                source=source, severity=sev,
                component=component, message=alert.message,
            )
        except Exception as e:
            logger.error(f"[QA] Failed to store watchdog alert: {e}")

    from luna.diagnostics.watchdog import get_watchdog
    wd = get_watchdog(check_interval=60, engine=_engine)
    wd._alert_callback = _watchdog_to_qa
    watchdog_task = await wd.start_background()
    logger.info("Runtime watchdog started (wired to QA events)")

    # Auto-restore identity bypass if sentinel file exists
    # Runs as a background task so engine actor loop is fully running first
    _bypass_sentinel = config_dir() / "identity_bypass.json"
    if _bypass_sentinel.exists():
        async def _auto_restore_bypass():
            await asyncio.sleep(2)  # Let engine actor loop fully start
            try:
                import json as _json, time as _time
                _ia = _engine.get_actor("identity") if _engine else None
                if not _ia:
                    logger.warning("[BYPASS] Auto-restore skipped — identity actor not found")
                    return
                _bypass_data = _json.loads(_bypass_sentinel.read_text())
                _ia.current.entity_id = _bypass_data.get("entity_id", "")
                _ia.current.entity_name = _bypass_data.get("entity_name", "")
                _ia.current.confidence = 1.0
                _ia.current.luna_tier = _bypass_data.get("luna_tier", "admin")
                _ia.current.dataroom_tier = _bypass_data.get("dataroom_tier", 1)
                _ia.current.dataroom_categories = _bypass_data.get("dataroom_categories", [1,2,3,4,5,6,7,8,9])
                _ia.current.last_seen = _time.time()
                _ia._bypass_active = True

                async def _bypass_keepalive_startup():
                    while getattr(_ia, '_bypass_active', False):
                        _ia.current.last_seen = _time.time()
                        await asyncio.sleep(5)
                _track_task(_bypass_keepalive_startup(), name="bypass-keepalive")
                logger.info("[BYPASS] Auto-restored identity bypass from sentinel file")
            except Exception as _e:
                logger.warning(f"[BYPASS] Failed to restore bypass from sentinel: {_e}")
        _track_task(_auto_restore_bypass(), name="auto-restore-bypass")

    # Wire knowledge event bus → WebSocket broadcast
    from luna.core.event_bus import event_bus as _knowledge_bus

    async def _on_knowledge_event(ev):
        await _broadcast_knowledge_event(ev)

    _knowledge_bus.subscribe("knowledge", _on_knowledge_event)

    # ── EnginePool (step 3d) ──────────────────────────────────────
    # Build the pool of per-profile engines. The legacy `_engine` above stays
    # as the default fallback for unauth / pre-profile-system code paths.
    # Authenticated requests for tester profiles get their own pool entries on
    # first hit, via request_engine(). The pool reaps engines idle for >15min.
    from luna.api.engine_dispatch import set_default_engine, set_pool
    from luna.api.engine_pool import EnginePool

    _pool = EnginePool(
        max_engines=int(os.environ.get("LUNA_POOL_MAX_ENGINES", "8")),
        idle_timeout_seconds=float(os.environ.get("LUNA_POOL_IDLE_TIMEOUT", "900")),
    )
    set_pool(_pool)
    set_default_engine(_engine)

    # Register the lifespan-booted engine in the pool under the admin's slug
    # so authenticated admin requests resolve to the SAME engine (no second
    # cold-start). Tester slugs trigger a pool cold-start via the factory.
    if is_migrated():
        try:
            _registry_for_pool = ProfileRegistry()
            _admins_for_pool = [p for p in _registry_for_pool.list_profiles() if p.tier == "admin"]
            if _admins_for_pool:
                await _pool.register(_admins_for_pool[0].slug, _engine)
                logger.info(
                    "EnginePool: registered lifespan engine under admin slug %r",
                    _admins_for_pool[0].slug,
                )
        except Exception as e:
            logger.error("Failed to register lifespan engine in pool: %s", e)

    async def _pool_reaper():
        """Periodic idle-engine eviction. Every 60s."""
        while True:
            try:
                await asyncio.sleep(60)
                evicted = await _pool.reap_idle()
                if evicted:
                    logger.info("EnginePool: reaped idle engines: %s", evicted)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("EnginePool reaper error: %s", e)

    _pool_reaper_task = _track_task(_pool_reaper(), name="engine-pool-reaper")
    logger.info("EnginePool initialized (max=%d, idle_timeout=%ds)",
                _pool._max_engines, int(_pool._idle_timeout))

    logger.info("Luna Engine ready")

    yield

    # Shutdown
    logger.info("Stopping Luna Engine...")

    # Stop the pool first (gracefully stops all profile engines except the
    # default, which lifespan still owns and stops below).
    try:
        await _pool.stop_all()
    except Exception as e:
        logger.error("EnginePool shutdown error: %s", e)
    set_pool(None)
    set_default_engine(None)

    # Cancel all tracked background tasks first
    await _cancel_all_background_tasks()

    # Stop the watchdog
    if watchdog_task and not watchdog_task.done():
        watchdog_task.cancel()
        logger.info("Runtime watchdog stopped")

    if _engine:
        await _engine.stop()

    # Wait for engine task to complete
    try:
        await asyncio.wait_for(engine_task, timeout=5.0)
    except asyncio.TimeoutError:
        engine_task.cancel()
        try:
            await engine_task
        except asyncio.CancelledError:
            pass

    logger.info("Luna Engine stopped")


# Create FastAPI app
app = FastAPI(
    title="Luna Engine API",
    description="Consciousness engine that uses LLMs the way game engines use GPUs",
    version="2.0.0",
    lifespan=lifespan,
)

# Add CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth gating policy ────────────────────────────────────────────────────
# Default-deny: every endpoint requires authentication unless its path appears
# in PUBLIC_PATHS_EXACT or starts with one of PUBLIC_PATH_PREFIXES. Admin-only
# endpoints additionally require tier=admin via ADMIN_PATH_PREFIXES.
#
# Set LUNA_AUTH_DISABLE=1 to bypass gating entirely (local dev / debugging).
# WebSocket paths are exempt for now — they have their own connection model
# and need cookie/token handling out-of-band.

PUBLIC_PATHS_EXACT = frozenset({
    "/",                           # frontend root (login page)
    "/api/auth/me",                # session probe — endpoint handles its own auth logic
    "/api/auth/login",             # log in
    "/api/auth/logout",            # log out is always idempotent
    "/api/status/first-run",       # first-run wizard probe
    "/health",                     # health check (uptime probe)
    "/openapi.json",               # FastAPI schema
    "/docs",                       # Swagger UI
    "/redoc",                      # ReDoc UI
})

PUBLIC_PATH_PREFIXES = (
    "/assets/",                    # frontend static assets
    "/static/",                    # alternative static mount
    "/docs/",                      # Swagger sub-paths
)

ADMIN_PATH_PREFIXES = (
    "/api/factory-reset",
    "/api/onboarding",
    "/api/demo-reset",
    "/observatory/",
    "/guardian/api/",
)


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS_EXACT:
        return True
    if path.startswith("/ws/"):  # WebSockets exempt for now
        return True
    return any(path.startswith(p) for p in PUBLIC_PATH_PREFIXES)


def _is_admin_path(path: str) -> bool:
    return any(path.startswith(p) for p in ADMIN_PATH_PREFIXES)


@app.middleware("http")
async def luna_auth_middleware(request: Request, call_next):
    """Auth flow + path-based default-deny gating.

    Order:
      1. Run auth_middleware to read cookie + populate request.state.luna_session
      2. If LUNA_AUTH_DISABLE=1 or path is public → pass through
      3. If unauthenticated → 401
      4. If admin-only path and session is not admin → 403
      5. Otherwise → endpoint

    This is the single chokepoint for endpoint authorization. New endpoints
    are protected by default — opt INTO public access via PUBLIC_PATHS_EXACT.
    """
    # Pass through when auth is explicitly disabled OR when the profile system
    # hasn't been initialized yet (no profiles.json → no way to log in).
    _auth_off = os.environ.get("LUNA_AUTH_DISABLE", "").lower() in ("1", "true", "yes")
    if _auth_off or not ProfileRegistry().exists():
        return await auth_middleware(request, call_next)

    async def gated_call(req: Request):
        path = req.url.path
        if _is_public_path(path):
            return await call_next(req)
        session = current_session(req)
        if session is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
            )
        if _is_admin_path(path) and session.tier != "admin":
            return JSONResponse(
                status_code=403,
                content={"detail": "Admin access required"},
            )
        return await call_next(req)

    return await auth_middleware(request, gated_call)


@app.middleware("http")
async def qa_error_capture(request: Request, call_next):
    """Catch unhandled 500 errors and store them in QA events."""
    try:
        return await call_next(request)
    except Exception as exc:
        if QA_AVAILABLE:
            try:
                import traceback as _tb
                get_qa_validator()._db.store_event(
                    source="api_error",
                    severity="high",
                    component=f"{request.method} {request.url.path}",
                    message=str(exc),
                    details={"traceback": _tb.format_exc()[-2000:]},
                )
            except Exception:
                pass
        raise


@app.middleware("http")
async def guardian_project_scope(request: Request, call_next):
    """Auto-activate guardian project scope and sync memory bridge."""
    global _guardian_bridge
    path = request.url.path
    # Skip sync/clear/status endpoints — they manage the bridge directly
    _bridge_paths = ("/guardian/api/sync", "/guardian/api/clear")
    if path.startswith("/guardian/") and not any(path.startswith(p) for p in _bridge_paths) and _engine is not None:
        # Auto-sync guardian bridge if a project is active (no forced project)
        if _engine.active_project is not None:
            pass  # Project already active — let it stay

        # Auto-sync on first request (lazy init)
        if _guardian_bridge is None:
            _guardian_bridge = GuardianMemoryBridge(_engine)
        if not _guardian_bridge.is_synced:
            try:
                stats = await _guardian_bridge.sync_all()
                logger.info(f"Guardian bridge auto-synced: {stats}")
            except Exception as e:
                logger.error(f"Guardian bridge auto-sync failed: {e}")

    response = await call_next(request)
    return response


# Mount KOZMO service router
if _frontend_page_enabled("kozmo"):
    app.include_router(kozmo_router)
else:
    logger.info("KOZMO router disabled by frontend config")

# Mount GUARDIAN service router
app.include_router(guardian_router)

# Mount SETTINGS service router
app.include_router(settings_router)
app.include_router(lunafm_settings_router)

# Mount OBSERVATORY service router (replaces httpx reverse proxy to :8100)
app.include_router(observatory_router)


# ============================================
# GUARDIAN MEMORY BRIDGE ENDPOINTS
# ============================================

@app.post("/guardian/api/sync")
async def guardian_sync():
    """Sync Guardian demo data into Luna's Memory Matrix."""
    global _guardian_bridge
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    if _guardian_bridge is None:
        _guardian_bridge = GuardianMemoryBridge(_engine)

    stats = await _guardian_bridge.sync_all()
    return {"status": "synced", **stats}


@app.post("/guardian/api/clear")
async def guardian_clear():
    """Clear Guardian data from Memory Matrix."""
    global _guardian_bridge
    if _guardian_bridge is None:
        return {"status": "nothing_to_clear"}

    removed = await _guardian_bridge.clear()
    return {"status": "cleared", "removed": removed}


@app.get("/guardian/api/sync/status")
async def guardian_sync_status():
    """Check if Guardian data is synced."""
    if _guardian_bridge is None:
        return {"synced": False}
    return {"synced": _guardian_bridge.is_synced}


# ============================================
# KNOWLEDGE COMPILER ENDPOINTS
# ============================================

@app.post("/compiler/run")
async def compiler_run(data_root: str = "data/guardian"):
    """Run the Knowledge Compiler on Guardian source data."""
    global _knowledge_compiler
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    from pathlib import Path
    _knowledge_compiler = KnowledgeCompiler(
        engine=_engine,
        data_root=Path(data_root),
    )
    stats = await _knowledge_compiler.compile_all()
    return {"status": "compiled", **stats.to_dict()}


@app.post("/compiler/clear")
async def compiler_clear():
    """Clear all compiler-produced nodes from the Memory Matrix."""
    global _knowledge_compiler
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    if _knowledge_compiler is None:
        _knowledge_compiler = KnowledgeCompiler(
            engine=_engine,
            data_root=local_dir() / "guardian",
        )

    removed = await _knowledge_compiler.clear()
    return {"status": "cleared", "removed": removed}


@app.post("/compiler/conversations")
async def compiler_conversations(
    threads_dir: str = "data/guardian/conversations",
    data_root: str = "data/guardian",
):
    """Phase 1b: Extract knowledge from conversation threads."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix or not matrix.is_ready:
        raise HTTPException(status_code=503, detail="Matrix not ready")

    from luna.compiler.entity_index import EntityIndex
    entity_index = EntityIndex()
    entities_path = Path(data_root) / "entities" / "entities_updated.json"
    if entities_path.exists():
        entity_index.load_entities(entities_path)

    # Build existing node_map from the compiler if available
    node_map = {}
    if _knowledge_compiler:
        node_map = _knowledge_compiler.node_map

    extractor = ConversationExtractor(entity_index, node_map)
    scope = _engine.active_scope if _engine else "global"
    result = await extractor.extract_all(Path(threads_dir), matrix, scope=scope)
    return {"status": "extracted", **result.to_dict()}


@app.post("/compiler/export")
async def compiler_export(
    scope: str = None,
    output_dir: str = "data/archive",
    format: str = "markdown",
):
    """Phase 1c: Export compiled memory nodes as Markdown archive."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    scope = scope or (_engine.active_scope if _engine else "global")

    matrix = _engine.get_actor("matrix")
    if not matrix or not matrix.is_ready:
        raise HTTPException(status_code=503, detail="Matrix not ready")

    entity_index = None
    if _knowledge_compiler:
        entity_index = _knowledge_compiler.entity_index

    exporter = MarkdownExporter(matrix, entity_index)
    result = await exporter.export_scope(scope, Path(output_dir) / scope.replace(":", "-"))
    return {"status": "exported", **result.to_dict()}


@app.post("/compiler/suggest_study_update")
async def compiler_suggest_study_update(scope: str = ""):
    """
    Read completed study_update quests for a project, compare against
    current study context, and return structured suggestions.

    Does NOT auto-apply — returns suggestions for human review.
    """
    if not scope:
        raise HTTPException(status_code=400, detail="scope (project slug) required")

    from luna.context.study_context import load_raw_config, flatten_to_text
    config = load_raw_config(scope)
    if not config:
        raise HTTPException(status_code=404, detail=f"No project config for '{scope}'")

    study = config.get("study_context", {})

    # Read completed study_update quests for this project
    try:
        from luna_mcp.observatory.tools import tool_observatory_quest_board
        quest_result = await tool_observatory_quest_board(
            action="list", status="complete", quest_type="contract", project=scope,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Observatory unavailable: {e}")

    quests = quest_result.get("quests", [])
    study_quests = [q for q in quests if q.get("source") == "study_update"]

    if not study_quests:
        return {"scope": scope, "suggestions": [], "message": "No completed study_update quests"}

    import re as _re
    metadata_keys = {"version", "token_budget", "enabled", "changelog", "auto_quest"}
    full_text = flatten_to_text(study).lower()

    suggestions = []
    for quest in study_quests:
        objective = quest.get("objective", "")
        quest_id = quest.get("id", "")

        items = _re.findall(r'-\s*\[(DECISION|FACT)\]\s*(.+?)(?:\n|$)', objective)
        for ext_type, content in items:
            content_stripped = content.strip()
            content_lower = content_stripped.lower()

            # Match to best section by word overlap
            best_section = "active_work"
            best_score = 0
            content_words = set(content_lower.split())

            for sname, sdata in study.items():
                if sname in metadata_keys:
                    continue
                section_words = set(flatten_to_text(sdata).lower().split())
                overlap = len(content_words & section_words)
                score = overlap / len(content_words) if content_words else 0
                if score > best_score:
                    best_score = score
                    best_section = sname

            if best_score <= 0.2:
                best_section = "active_work"

            # Check if already covered
            found = sum(1 for w in content_words if w in full_text)
            already_covered = (found / len(content_words)) > 0.6 if content_words else True

            suggestions.append({
                "quest_id": quest_id,
                "extraction_type": ext_type,
                "content": content_stripped,
                "suggested_section": best_section,
                "action": "skip" if already_covered else "append",
                "already_covered": already_covered,
            })

    return {
        "scope": scope,
        "quest_count": len(study_quests),
        "suggestions": suggestions,
        "current_version": study.get("version", "unknown"),
    }


# Serve KOZMO project assets (generated reference images, etc.)
try:
    if _frontend_page_enabled("kozmo"):
        _kozmo_assets = user_dir() / "kozmo_projects"
        _kozmo_assets.mkdir(parents=True, exist_ok=True)
        app.mount("/kozmo-assets", StaticFiles(directory=str(_kozmo_assets)), name="kozmo-assets")
    else:
        logger.info("KOZMO assets disabled by frontend config")
except Exception:
    pass  # Non-fatal — assets won't be served if dir is missing

# Serve GUARDIAN frontend
try:
    _guardian_frontend = project_root().parent / "Eclissi-Guardian" / "frontend"
    if not _guardian_frontend.exists():
        _guardian_frontend = Path("frontend/guardian")
    if _guardian_frontend.exists():
        from starlette.responses import RedirectResponse as _RedirectResponse

        @app.get("/guardian")
        async def _guardian_redirect():
            return _RedirectResponse(url="/guardian/")

        app.mount("/guardian", StaticFiles(directory=str(_guardian_frontend), html=True), name="guardian")
        logger.info(f"Guardian frontend mounted at /guardian from {_guardian_frontend}")
except Exception as e:
    logger.warning(f"Guardian frontend mount failed: {e}")

# Serve LUNAR STUDIO frontend (Expression Pipeline Diagnostic)
try:
    _studio_frontend = tools_dir() / "Luna-Expression-Pipeline" / "diagnostic" / "dist"
    if _studio_frontend.exists():
        from starlette.responses import RedirectResponse

        @app.get("/studio")
        async def _studio_redirect():
            return RedirectResponse(url="/studio/")

        app.mount("/studio", StaticFiles(directory=str(_studio_frontend), html=True), name="studio")
        logger.info(f"Lunar Studio mounted at /studio from {_studio_frontend}")
except Exception as e:
    logger.warning(f"Lunar Studio mount failed: {e}")


# ============================================
# ORB STATE WEBSOCKET
# ============================================

async def _broadcast_orb_state():
    """Broadcast current orb state to all connected WebSocket clients."""
    if _orb_state_manager is None:
        return
    state_dict = _orb_state_manager.to_dict()
    disconnected = set()
    for ws in _orb_websockets:
        try:
            await ws.send_json(state_dict)
        except Exception:
            disconnected.add(ws)
    _orb_websockets.difference_update(disconnected)


@app.websocket("/ws/orb")
async def orb_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for Luna Orb state streaming.

    Clients receive JSON updates whenever the orb state changes:
    {
        "animation": "pulse",
        "color": "#a78bfa",
        "brightness": 1.0,
        "source": "gesture",
        "timestamp": "2025-01-27T12:00:00"
    }
    """
    await websocket.accept()
    _orb_websockets.add(websocket)
    logger.info(f"Orb WebSocket connected. Total: {len(_orb_websockets)}")

    # Send current state immediately
    if _orb_state_manager:
        await websocket.send_json(_orb_state_manager.to_dict())

    # Subscribe to state changes
    if _orb_state_manager:
        def on_state_change(state):
            _track_task(_broadcast_orb_state(), name="orb-broadcast")
        unsubscribe = _orb_state_manager.subscribe(on_state_change)
    else:
        unsubscribe = lambda: None

    try:
        while True:
            # Keep connection alive, ignore incoming messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _orb_websockets.discard(websocket)
        unsubscribe()
        logger.info(f"Orb WebSocket disconnected. Total: {len(_orb_websockets)}")


@app.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for shared chat session viewing.

    Clients receive JSON updates for all messages (user and assistant):
    {
        "type": "user" | "assistant" | "system",
        "data": {
            "content": "message text",
            "model": "...",  // for assistant
            "metadata": {...}
        },
        "timestamp": 1234567890
    }

    This allows multiple viewers to see the same conversation in real-time,
    including messages sent via API (curl, MCP, etc).
    """
    await websocket.accept()
    client_id = _normalize_chat_client_id(websocket.query_params.get("client_id"))
    _chat_websockets[websocket] = client_id
    logger.info(f"Chat WebSocket connected. Total: {len(_chat_websockets)}")

    # Send connection confirmation
    await websocket.send_json({
        "type": "system",
        "data": {"content": "Connected to Luna chat stream"},
        "timestamp": asyncio.get_event_loop().time(),
    })

    try:
        while True:
            # Keep connection alive, ignore incoming messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _chat_websockets.pop(websocket, None)
        logger.info(f"Chat WebSocket disconnected. Total: {len(_chat_websockets)}")


# ============================================
# IDENTITY STATE WEBSOCKET (FaceID)
# ============================================

async def _broadcast_identity_state():
    """Broadcast current identity state to all connected WebSocket clients."""
    if _engine is None:
        return

    identity_actor = _engine.get_actor("identity")
    if not identity_actor:
        return

    current = identity_actor.current
    payload = {
        "type": "identity_update",
        "data": {
            "is_present": current.is_present,
            "entity_id": current.entity_id,
            "entity_name": current.entity_name,
            "confidence": round(current.confidence, 3),
            "luna_tier": current.luna_tier,
            "dataroom_tier": current.dataroom_tier,
            "last_seen": current.last_seen,
        },
        "timestamp": asyncio.get_event_loop().time(),
    }

    disconnected = set()
    for ws in _identity_websockets:
        try:
            await ws.send_json(payload)
        except Exception:
            disconnected.add(ws)
    _identity_websockets.difference_update(disconnected)


@app.websocket("/ws/identity")
async def identity_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for FaceID identity state streaming.

    Clients receive JSON updates when identity changes:
    {
        "type": "identity_update",
        "data": {
            "is_present": true,
            "entity_id": "entity_2ca6b7c7",
            "entity_name": "User",
            "confidence": 0.987,
            "luna_tier": "admin",
            "dataroom_tier": 1,
            "last_seen": 1708349600.0
        },
        "timestamp": 1234567890
    }
    """
    await websocket.accept()
    _identity_websockets.add(websocket)
    logger.info(f"Identity WebSocket connected. Total: {len(_identity_websockets)}")

    # Send current state immediately
    identity_actor = _engine.get_actor("identity") if _engine else None
    if identity_actor:
        current = identity_actor.current
        await websocket.send_json({
            "type": "identity_update",
            "data": {
                "is_present": current.is_present,
                "entity_id": current.entity_id,
                "entity_name": current.entity_name,
                "confidence": round(current.confidence, 3),
                "luna_tier": current.luna_tier,
                "dataroom_tier": current.dataroom_tier,
                "last_seen": current.last_seen,
            },
            "timestamp": asyncio.get_event_loop().time(),
        })

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _identity_websockets.discard(websocket)
        logger.info(f"Identity WebSocket disconnected. Total: {len(_identity_websockets)}")


# ============================================
# KNOWLEDGE EVENT STREAM
# ============================================

async def _broadcast_knowledge_event(event) -> None:
    """Broadcast a KnowledgeEvent to all /ws/knowledge clients."""
    if not _knowledge_websockets:
        return
    payload = json.dumps({
        "type": event.type,
        "payload": event.payload,
        "ts": event.timestamp,
    })
    disconnected: set[WebSocket] = set()
    for ws in _knowledge_websockets:
        try:
            await ws.send_text(payload)
        except Exception:
            disconnected.add(ws)
    _knowledge_websockets.difference_update(disconnected)


@app.websocket("/ws/knowledge")
async def knowledge_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for real-time knowledge pipeline events.

    Clients receive JSON events for entity creation, fact extraction,
    edge creation, and entity confirmation/rejection.
    """
    await websocket.accept()
    _knowledge_websockets.add(websocket)
    logger.info(f"Knowledge WebSocket connected. Total: {len(_knowledge_websockets)}")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _knowledge_websockets.discard(websocket)
        logger.info(f"Knowledge WebSocket disconnected. Total: {len(_knowledge_websockets)}")


# ============================================
# ENTITY CONFIRMATION / REJECTION
# ============================================

@app.post("/api/entities/{entity_id}/confirm")
async def confirm_entity(entity_id: str):
    """Confirm a newly auto-created entity (keeps it, removes from review queue)."""
    if not _engine:
        raise HTTPException(status_code=503, detail="Engine not ready")
    matrix = _engine.get_actor("matrix")
    if not matrix or not getattr(matrix, "_db", None):
        raise HTTPException(status_code=503, detail="Memory not available")

    row = await matrix._db.fetchone(
        "SELECT id, name FROM entities WHERE id = ?", (entity_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Entity not found")

    # Mark confirmed in metadata
    await matrix._db.execute(
        "UPDATE entities SET metadata = json_set(COALESCE(metadata, '{}'), '$.confirmed', 1) WHERE id = ?",
        (entity_id,),
    )

    # Remove from review queue file
    from luna.entities.resolution import review_queue_path
    queue_path = review_queue_path()
    if queue_path.exists():
        try:
            queue = json.loads(queue_path.read_text())
            queue = [e for e in queue if e.get("entity_id") != entity_id]
            queue_path.write_text(json.dumps(queue, indent=2))
        except (json.JSONDecodeError, OSError):
            pass

    # Emit confirmation event
    from luna.core.event_bus import event_bus, KnowledgeEvent
    event_bus.emit("knowledge", KnowledgeEvent(
        type="entity_confirmed",
        payload={"entity_id": entity_id, "name": row[1]},
    ))
    return {"success": True, "entity_id": entity_id}


@app.delete("/api/entities/{entity_id}")
async def reject_entity(entity_id: str):
    """Hard-delete a rejected auto-created entity and all its mentions/relationships."""
    if not _engine:
        raise HTTPException(status_code=503, detail="Engine not ready")
    matrix = _engine.get_actor("matrix")
    if not matrix or not getattr(matrix, "_db", None):
        raise HTTPException(status_code=503, detail="Memory not available")

    row = await matrix._db.fetchone(
        "SELECT name FROM entities WHERE id = ?", (entity_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Entity not found")

    # CASCADE deletes handle entity_mentions, entity_versions, entity_relationships
    await matrix._db.execute("DELETE FROM entities WHERE id = ?", (entity_id,))

    # Remove from review queue file
    from luna.entities.resolution import review_queue_path
    queue_path = review_queue_path()
    if queue_path.exists():
        try:
            queue = json.loads(queue_path.read_text())
            queue = [e for e in queue if e.get("entity_id") != entity_id]
            queue_path.write_text(json.dumps(queue, indent=2))
        except (json.JSONDecodeError, OSError):
            pass

    # Emit rejection event
    from luna.core.event_bus import event_bus, KnowledgeEvent
    event_bus.emit("knowledge", KnowledgeEvent(
        type="entity_rejected",
        payload={"entity_id": entity_id, "name": row[0]},
    ))
    return {"success": True, "entity_id": entity_id}


# ============================================
# GUARDIAN LUNA — OPERATIONAL CHAT STREAM
# ============================================

@app.post("/api/guardian/chat/stream")
async def guardian_chat_stream(request: MessageRequest):
    """
    Stream Guardian Luna's response to admin queries.

    Guardian Luna is a READ-ONLY inspector of Luna's knowledge system.
    Uses the same LLM fallback chain as companion chat but with a flat
    operational system prompt — no personality kernel, no prosody.

    SSE format matches /persona/stream:
    - {"type": "context", "state": {...}}
    - {"type": "token", "text": "chunk"}
    - {"type": "done", "response": "full text", "metadata": {...}}
    - {"type": "error", "message": "..."}
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    async def generate_guardian_sse() -> AsyncGenerator[str, None]:
        """Generate Guardian Luna SSE stream."""
        import time as _time

        start_time = _time.time()

        try:
            # 1. Build Guardian system prompt with live state
            from luna.context.guardian_prompt import build_guardian_prompt, fetch_guardian_history
            system_prompt = await build_guardian_prompt(_engine)

            # 1b. Route to a Guardian capability if the query matches a triage intent.
            #     The typed result is injected into the system prompt so Luna narrates it.
            from luna.services.guardian.capabilities import (
                detect_capability_intent,
                format_triage_for_prompt,
                get_registry as _cap_registry,
            )
            capability_name = detect_capability_intent(request.message)
            capability_result = None
            if capability_name:
                try:
                    capability_result = await _cap_registry().invoke(capability_name, {})
                    system_prompt += (
                        f"\n\n--- GUARDIAN CAPABILITY INVOKED: {capability_name.upper()} ---\n"
                        + format_triage_for_prompt(capability_result)
                        + "\n\nNarrate this result in plain, read-only operator language."
                    )
                except Exception as _cap_err:
                    logger.warning(f"Guardian capability '{capability_name}' failed: {_cap_err}")

            # 2. Fetch Guardian conversation history
            history = await fetch_guardian_history(_engine, limit=20)

            # 3. Build messages array for LLM
            messages = []
            for turn in history:
                messages.append({"role": turn["role"], "content": turn["content"]})
            messages.append({"role": "user", "content": request.message})

            # 4. Send context event (system state summary for frontend)
            context_event = {
                "type": "context",
                "state": {
                    "source": "guardian",
                    "history_turns": len(history),
                    "capability_invoked": capability_name,
                },
            }
            yield f"data: {json.dumps(context_event)}\n\n"

            # 4b. Emit structured capability result for future UI consumption
            if capability_result is not None:
                cap_event = {
                    "type": "capability",
                    "name": capability_name,
                    "result": capability_result.to_jsonable(),
                }
                yield f"data: {json.dumps(cap_event)}\n\n"

            # 5. Get the fallback chain from Director (shares same LLM config)
            director = _engine.get_actor("director")
            if not director:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Director not available'})}\n\n"
                return

            fallback_chain = getattr(director, "_fallback_chain", None)
            if not fallback_chain:
                yield f"data: {json.dumps({'type': 'error', 'message': 'No LLM provider available'})}\n\n"
                return

            # 6. Stream from fallback chain with Guardian prompt
            response_text = ""
            token_count = 0

            try:
                async for text in fallback_chain.stream(
                    messages=messages,
                    system=system_prompt,
                    max_tokens=1024,
                    temperature=0.5,  # Lower temp for factual/diagnostic responses
                ):
                    response_text += text
                    token_count += 1
                    yield f"data: {json.dumps({'type': 'token', 'text': text})}\n\n"

            except Exception as llm_err:
                logger.error(f"Guardian LLM error: {llm_err}")
                yield f"data: {json.dumps({'type': 'error', 'message': f'LLM inference failed: {llm_err}'})}\n\n"
                return

            elapsed_ms = (_time.time() - start_time) * 1000

            # 7. Record turns to DB with guardian source tag
            # Direct DB insert — skips Scribe extraction (admin chatter is not knowledge)
            try:
                import aiosqlite
                from luna.core.paths import user_dir
                from datetime import datetime

                db_path = memory_matrix_path()
                now = datetime.utcnow().isoformat()
                meta = json.dumps({"source": "guardian"})

                async with aiosqlite.connect(str(db_path)) as db:
                    await db.execute("PRAGMA busy_timeout=15000")
                    await db.execute(
                        "INSERT OR IGNORE INTO conversation_turns "
                        "(session_id, role, content, tokens, created_at, metadata) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (_engine.session_id, "user", request.message, None, now, meta),
                    )
                    await db.execute(
                        "INSERT OR IGNORE INTO conversation_turns "
                        "(session_id, role, content, tokens, created_at, metadata) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (_engine.session_id, "assistant", response_text, token_count, now, meta),
                    )
                    await db.commit()
            except Exception as db_err:
                logger.warning(f"Guardian: failed to record turns: {db_err}")

            # 8. Send done event
            done_event = {
                "type": "done",
                "response": response_text,
                "metadata": {
                    "source": "guardian",
                    "latency_ms": round(elapsed_ms),
                    "output_tokens": token_count,
                },
            }
            yield f"data: {json.dumps(done_event)}\n\n"

        except Exception as e:
            logger.error(f"Guardian stream error: {e}")
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate_guardian_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================
# BROWSER FACE RECOGNITION ENDPOINT
# ============================================

# ============================================
# FACEID PROXY — forwards to FaceID microservice on :8100
# ============================================

FACEID_SERVICE = "http://127.0.0.1:8101"


async def _proxy_faceid(path: str, body: dict) -> dict:
    """Forward a request to the FaceID microservice."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{FACEID_SERVICE}{path}", json=body)
            return resp.json()
    except httpx.ConnectError:
        return {"error": "FaceID service not running (start: cd Tools/FaceID && source .venv/bin/activate && python serve.py)"}
    except Exception as e:
        return {"error": f"FaceID proxy error: {e}"}


@app.post("/identity/recognize")
async def recognize_frame(frame_data: dict):
    """Proxy to FaceID microservice for recognition."""
    return await _proxy_faceid("/recognize", frame_data)


@app.post("/identity/enroll")
async def enroll_frame(frame_data: dict):
    """Proxy to FaceID microservice for enrollment. Admin-only."""
    await _require_admin()
    return await _proxy_faceid("/enroll", frame_data)


@app.post("/identity/reset")
async def reset_identity(data: dict):
    """Proxy to FaceID microservice for reset. Admin-only."""
    await _require_admin()
    return await _proxy_faceid("/reset", data)


@app.post("/identity/bypass")
async def bypass_identity():
    """
    Manually grant identity as owner (admin) without FaceID camera.
    Starts a keepalive that refreshes last_seen every 5s.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    identity_actor = _engine.get_actor("identity")
    if not identity_actor:
        raise HTTPException(status_code=503, detail="Identity actor not found")

    import time as _time, sqlite3 as _sqlite3
    from luna.core.owner import get_owner

    # Read identity from owner config — falls back to empty for unconfigured installs
    _owner = get_owner()
    entity_id = _owner.entity_id or "admin"
    entity_name = _owner.display_name or "Admin"
    luna_tier = "admin"
    dataroom_tier = 1
    dataroom_categories = [1, 2, 3, 4, 5, 6, 7, 8, 9]

    # Set identity directly on the actor
    identity_actor.current.entity_id = entity_id
    identity_actor.current.entity_name = entity_name
    identity_actor.current.confidence = 1.0
    identity_actor.current.luna_tier = luna_tier
    identity_actor.current.dataroom_tier = dataroom_tier
    identity_actor.current.dataroom_categories = dataroom_categories
    identity_actor.current.last_seen = _time.time()

    # Keepalive task — refreshes last_seen so is_present stays true
    async def _bypass_keepalive():
        while getattr(identity_actor, '_bypass_active', False):
            identity_actor.current.last_seen = _time.time()
            await _broadcast_identity_state()
            await asyncio.sleep(5)

    identity_actor._bypass_active = True
    _track_task(_bypass_keepalive(), name="bypass-keepalive")
    await _broadcast_identity_state()

    # Persist bypass so it survives restarts
    import json as _json
    _sentinel = config_dir() / "identity_bypass.json"
    _sentinel.parent.mkdir(parents=True, exist_ok=True)
    _sentinel.write_text(_json.dumps({
        "entity_id": entity_id,
        "entity_name": entity_name,
        "luna_tier": luna_tier,
        "dataroom_tier": dataroom_tier,
        "dataroom_categories": dataroom_categories,
    }))
    logger.info(f"[BYPASS] Identity set to {entity_name} ({entity_id}) — persisted to sentinel")

    return {
        "bypassed": True,
        "entity_id": entity_id,
        "entity_name": entity_name,
        "luna_tier": luna_tier,
        "dataroom_tier": dataroom_tier,
    }


@app.post("/identity/bypass-off")
async def bypass_off():
    """Revoke the identity bypass — clear manual identity."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    identity_actor = _engine.get_actor("identity")
    if not identity_actor:
        raise HTTPException(status_code=503, detail="Identity actor not found")

    identity_actor._bypass_active = False
    identity_actor.current.clear()
    await _broadcast_identity_state()

    # Remove sentinel so bypass doesn't auto-restore on next restart
    _sentinel = config_dir() / "identity_bypass.json"
    if _sentinel.exists():
        _sentinel.unlink()
    logger.info("[BYPASS] Identity bypass revoked — sentinel removed")
    return {"bypassed": False}


@app.post("/message", response_model=MessageResponse)
async def send_message(
    request: MessageRequest,
    engine=Depends(request_engine),
):
    """
    Send a message to Luna and get a response.

    This is the main interaction endpoint. Messages are queued in the
    input buffer and processed by the engine's cognitive loop.
    """
    # Create a future to wait for response
    response_future: asyncio.Future = asyncio.Future()

    async def on_response(text: str, data: dict) -> None:
        print(f"🔔 [/message] on_response FIRED: {len(text)} chars, future_done={response_future.done()}")
        if not response_future.done():
            response_future.set_result((text, data))

    # Register callback
    engine.on_response(on_response)
    print(f"🔔 [/message] Callback registered, total callbacks: {len(engine._on_response_callbacks)}")

    try:
        # Send message with source tag (+ per-turn metadata, e.g. quoteEcho).
        # director_pivot_endpoint is non-persisted — lives in payload only.
        await engine.send_message(
            request.message,
            source=request.source,
            metadata=request.metadata,
            director_pivot_endpoint="/message",
        )

        # Wait for response with timeout
        text, data = await asyncio.wait_for(
            response_future,
            timeout=request.timeout
        )

        # Fire-and-forget QA validation (non-blocking)
        _track_task(
            _run_qa_validation_background(request.message, text, data),
            name="qa-validation",
        )

        # Process text for gesture detection + stripping (before broadcast so all paths get clean text)
        if _orb_state_manager:
            text = _orb_state_manager.process_text_chunk(text)

        # GroundingLink: trace each sentence back to injected memory nodes
        grounding_metadata = None
        if _grounding_link:
            try:
                director = engine.get_actor("director")
                injected = getattr(director, "_last_injected_memories", None) or []
                if injected:
                    grounding_result = _grounding_link.ground(text, injected)
                    grounding_metadata = grounding_result.to_dict()
            except Exception as e:
                logger.debug(f"[GROUNDING] Non-fatal error: {e}")

        # BROADCAST DEDUPLICATION: Suppress chat broadcast when source is
        # "mcp" to prevent shadow conversations in Eclissi. MCP calls still
        # trigger Scribe extraction (cache updates) but don't echo to the UI.
        if request.source != "mcp":
            _track_task(_broadcast_chat_message("user", {
                "content": request.message,
                "source": request.source,
            }, origin_client_id=request.client_id), name="chat-broadcast-user")
            broadcast_data = {
                "content": text,
                "model": data.get("model", "unknown"),
                "delegated": data.get("delegated", False),
                "local": data.get("local", False),
                "latency_ms": data.get("latency_ms", 0),
                "source": request.source,
            }
            if grounding_metadata:
                broadcast_data["groundingMetadata"] = grounding_metadata
            _track_task(
                _broadcast_chat_message(
                    "assistant",
                    broadcast_data,
                    origin_client_id=request.client_id,
                ),
                name="chat-broadcast-assistant",
            )

        # Phase 5 chat-UI metadata. Lookup is post-hoc so it survives
        # whichever generation path Director took.
        interrupt_type = await _lookup_pending_interrupt_type()
        # quoteEcho echoes back what the request supplied so the frontend
        # can attach it to the user bubble symmetrically with /history rehydrate.
        quote_echo = (request.metadata or {}).get("quoteEcho")

        return MessageResponse(
            text=text,
            model=data.get("model", "unknown"),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            latency_ms=data.get("latency_ms", 0),
            delegated=data.get("delegated", False),
            local=data.get("local", False),
            fallback=data.get("fallback", False),
            groundingMetadata=grounding_metadata,
            interrupt_type=interrupt_type,
            memoryAnchor=data.get("memoryAnchor"),
            quoteEcho=quote_echo,
            content_type=data.get("content_type"),
            actors=data.get("actors"),
            directorPivotShadow=data.get("director_pivot_shadow"),
            directorPivotActive=data.get("director_pivot_active"),
        )

    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Response timeout after {request.timeout}s"
        )

    finally:
        # Remove callback
        if on_response in engine._on_response_callbacks:
            engine._on_response_callbacks.remove(on_response)


@app.post("/context/prepare", response_model=PrepareContextResponse, response_model_exclude_none=True)
async def prepare_context(
    request: PrepareContextRequest,
    engine=Depends(request_engine),
):
    """Run context assembly without LLM inference; return the assembled bundle.

    For MCP consumers (Claude Desktop) that will run inference against the bundle
    themselves. Reuses the canonical Director assembler pipeline — context is
    identical to what `/message` would see. On failure, returns a typed error
    body (never an empty response) via the shared `serialize_error` helper.
    """
    trace_cfg = load_trace_config()
    trace_builder: Optional[TraceBuilder] = None
    if request.verbose:
        if not trace_cfg.enabled:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "diagnostics_disabled",
                    "message": "trace mode is disabled in config",
                },
            )
        trace_builder = TraceBuilder(
            query=request.message,
            session_id=request.session_id,
        )

    prepare_t0 = time.time()
    # Phase 1A.7: per-request correlation id. The on_response callback fan-out
    # is broadcast to every registered listener, so concurrent prepare_only
    # callers must demultiplex by id or they will satisfy each other's
    # futures — scrambling responses. Filtering by `prepare_only` flag alone
    # was safe only while exactly one request was ever in flight.
    import uuid
    cid = uuid.uuid4().hex
    response_future: asyncio.Future = asyncio.Future()

    async def on_prepare_response(text: str, data: dict) -> None:
        if not data.get("prepare_only"):
            return  # Not for us — normal generation, ignore.
        if data.get("correlation_id") != cid:
            return  # Not our request — concurrent prepare from another caller.
        if not response_future.done():
            response_future.set_result(data)

    engine.on_response(on_prepare_response)

    try:
        await engine.send_message(
            request.message,
            source=request.source,
            prepare_only=True,
            correlation_id=cid,
            trace=trace_builder,
        )
        data = await asyncio.wait_for(response_future, timeout=request.timeout)
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={
                "error": True,
                "error_type": "TimeoutError",
                "error_message": f"Prepare timed out after {request.timeout}s",
                "engine_connected": True,
                "assembly_failed": True,
            },
        )
    except Exception as e:
        logger.error("/context/prepare failed", exc_info=True)
        try:
            from luna_mcp.errors import serialize_error
            payload = serialize_error(e)
        except Exception:
            payload = {"error": True, "error_type": type(e).__name__, "error_message": str(e) or repr(e)}
        return JSONResponse(
            status_code=500,
            content={**payload, "engine_connected": True, "assembly_failed": True},
        )
    finally:
        if on_prepare_response in engine._on_response_callbacks:
            engine._on_response_callbacks.remove(on_prepare_response)

    bundle = data.get("bundle") or {}
    diagnostics = dict(data.get("diagnostics") or {})
    diagnostics["assembly_ms"] = int((time.time() - prepare_t0) * 1000)
    trace_id: Optional[str] = None
    trace_payload: Optional[dict] = None

    if trace_builder is not None:
        trace_builder.set_prompt(
            bundle.get("system_prompt", ""),
            token_count=int(bundle.get("prompt_tokens", 0) or 0),
            token_budget=int(diagnostics.get("token_budget", 0) or 0) or None,
        )
        trace_record = trace_builder.finalize(
            latency_ms=int((time.time() - prepare_t0) * 1000),
            timeout_ms=trace_cfg.finalize_timeout_ms,
        )
        trace_id = trace_record.id
        trace_payload = trace_record.to_dict()

        if trace_cfg.persist:
            writer = _get_trace_writer()
            if writer is not None:
                ok = writer.persist(trace_record)
                if not ok:
                    notes = trace_payload.setdefault("notes", {})
                    notes["warning"] = "persist_failed"
                    notes["reason"] = writer.last_error or "unknown"

    return PrepareContextResponse(
        system_prompt=bundle.get("system_prompt", ""),
        messages=bundle.get("messages", []) or [],
        memory_context=bundle.get("memory_context", ""),
        framed_context=bundle.get("framed_context", ""),
        prompt_tokens=int(bundle.get("prompt_tokens", 0) or 0),
        route=bundle.get("route", "unknown"),
        assembler_meta=bundle.get("assembler_meta", {}) or {},
        engine_connected=True,
        diagnostics=diagnostics,
        trace_id=trace_id,
        trace=trace_payload,
    )


@app.get("/traces/list", response_model=TraceListResponse)
async def list_traces(
    limit: int = 50,
    offset: int = 0,
    route: Optional[str] = None,
    since: Optional[float] = None,
    session_id: Optional[str] = None,
):
    """List persisted traces with optional filters."""
    trace_cfg = load_trace_config()
    if not trace_cfg.enabled:
        return JSONResponse(
            status_code=403,
            content={
                "error": "diagnostics_disabled",
                "message": "trace mode is disabled in config",
            },
        )

    reader = _get_trace_reader()
    if reader is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "trace_reader_unavailable",
                "message": "trace reader unavailable",
            },
        )

    items, total = reader.list(
        limit=limit,
        offset=offset,
        route=route,
        since=since,
        session_id=session_id,
    )
    return TraceListResponse(
        items=items,
        total=total,
        limit=max(1, min(int(limit or 50), 500)),
        offset=max(0, int(offset or 0)),
    )


@app.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    """Fetch one full trace payload."""
    trace_cfg = load_trace_config()
    if not trace_cfg.enabled:
        return JSONResponse(
            status_code=403,
            content={
                "error": "diagnostics_disabled",
                "message": "trace mode is disabled in config",
            },
        )

    reader = _get_trace_reader()
    if reader is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "trace_reader_unavailable",
                "message": "trace reader unavailable",
            },
        )

    trace = reader.get(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace_not_found")
    return trace


@app.post("/traces/diff", response_model=TraceDiffResponse)
async def diff_traces(request: TraceDiffRequest):
    """Compute structured diff between two trace ids."""
    trace_cfg = load_trace_config()
    if not trace_cfg.enabled:
        return JSONResponse(
            status_code=403,
            content={
                "error": "diagnostics_disabled",
                "message": "trace mode is disabled in config",
            },
        )

    reader = _get_trace_reader()
    if reader is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "trace_reader_unavailable",
                "message": "trace reader unavailable",
            },
        )

    diff = reader.diff(request.a, request.b)
    if diff is None:
        raise HTTPException(status_code=404, detail="trace_not_found")
    return TraceDiffResponse(**diff)


@app.get("/traces/export/db")
async def export_traces_db():
    """Download current trace database snapshot."""
    trace_cfg = load_trace_config()
    if not trace_cfg.enabled:
        return JSONResponse(
            status_code=403,
            content={
                "error": "diagnostics_disabled",
                "message": "trace mode is disabled in config",
            },
        )

    db_path = trace_cfg.db_path
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="trace_db_not_found")

    return FileResponse(
        path=str(db_path),
        media_type="application/octet-stream",
        filename="traces.db",
    )


@app.get("/api/cache/shared-turn")
async def get_shared_turn_cache():
    """
    Read the Shared Turn Cache for Eclissi frontend widgets.

    Returns the current YAML snapshot as JSON, or 204 if no cache exists.
    """
    from luna.cache.shared_turn import read_shared_turn

    snapshot = read_shared_turn()
    if snapshot is None:
        return Response(status_code=204)

    return {
        "turn_id": snapshot.turn_id,
        "timestamp": snapshot.timestamp,
        "source": snapshot.source,
        "is_stale": snapshot.is_stale,
        "age_seconds": round(snapshot.age_seconds, 1),
        "flow": {
            "mode": snapshot.flow_mode,
            "topic": snapshot.topic,
            "continuity_score": snapshot.continuity_score,
            "open_threads": snapshot.open_threads,
        },
        "expression": {
            "emotional_tone": snapshot.emotional_tone,
            "expression_hint": snapshot.expression_hint,
            "intensity": snapshot.intensity,
        },
        "scribed": {
            "facts": snapshot.facts,
            "decisions": snapshot.decisions,
            "actions": snapshot.actions,
            "problems": snapshot.problems,
            "observations": snapshot.observations,
            "total": snapshot.total_extractions,
        },
        "raw_summary": snapshot.raw_summary,
    }


@app.get("/lunafm/status")
async def lunafm_status():
    """LunaFM station status — channels, states, emission counts."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    station = getattr(_engine, "lunafm", None)
    if station is None:
        return {"running": False, "preempted": False, "uptime_s": 0, "channels": []}
    return station.status()


@app.get("/lunafm/traits")
async def lunafm_traits():
    """Current LunaScript trait vector + derived aperture."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    matrix = _engine.get_actor("matrix")
    db = getattr(matrix, "_db", None) if matrix else None
    try:
        from luna.lunafm.frequency_coupling import read_traits, trait_to_aperture
        traits = await read_traits(db) if db else {}
    except Exception:
        traits = {}
    curiosity = float(traits.get("curiosity", 0.5))
    try:
        from luna.lunafm.frequency_coupling import trait_to_aperture
        preset, sigma = trait_to_aperture(curiosity)
    except Exception:
        preset, sigma = "BALANCED", 0.05
    return {
        "traits": traits,
        "aperture": {"preset": preset, "sigma": sigma},
    }


@app.get("/lunafm/spectral")
async def lunafm_spectral():
    """Spectral engine status — last computed, node/edge count, Fiedler."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    station = getattr(_engine, "lunafm", None)
    if station is None or station._spectral is None:
        return {"enabled": False}
    return {"enabled": True, **station._spectral.status()}


@app.get("/lunafm/pollution")
async def lunafm_pollution():
    """Live aggregate of lunafm-tagged nodes per node_type, bucketed."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    matrix = _engine.get_actor("matrix")
    db = getattr(matrix, "_db", None) if matrix else None
    if db is None:
        return {"buckets": {"1h": {}, "24h": {}, "total": {}}}

    async def _bucket(where: str) -> dict:
        rows = await db.fetchall(
            f"""
            SELECT node_type, COUNT(*) AS cnt
            FROM memory_nodes
            WHERE source LIKE 'lunafm:%' {where}
            GROUP BY node_type
            """
        )
        return {r[0]: r[1] for r in rows}

    return {
        "buckets": {
            "1h": await _bucket("AND created_at > datetime('now', '-1 hour')"),
            "24h": await _bucket("AND created_at > datetime('now', '-24 hours')"),
            "total": await _bucket(""),
        }
    }


@app.get("/lunafm/stream")
async def lunafm_stream():
    """SSE stream of LunaFM channel emissions (real-time)."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    station = getattr(_engine, "lunafm", None)
    if station is None:
        raise HTTPException(status_code=404, detail="LunaFM not running")

    import json as _json

    async def event_gen() -> AsyncGenerator[str, None]:
        q = station.subscribe()
        try:
            # Initial hello so the client knows the stream is live
            yield f"event: hello\ndata: {_json.dumps(station.status())}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: {event.get('type', 'emission')}\ndata: {_json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat keeps proxies from closing the connection
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            station.unsubscribe(q)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/lunafm/artifacts")
async def lunafm_artifacts(limit: int = 20, channel: Optional[str] = None):
    """Recent LunaFM-tagged memory nodes."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    matrix = _engine.get_actor("matrix")
    db = getattr(matrix, "_db", None) if matrix else None
    if db is None:
        return {"artifacts": []}
    limit = max(1, min(int(limit), 100))
    if channel:
        rows = await db.fetchall(
            """
            SELECT id, node_type, source, content, lock_in, created_at, metadata
            FROM memory_nodes
            WHERE source = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (f"lunafm:{channel}", limit),
        )
    else:
        rows = await db.fetchall(
            """
            SELECT id, node_type, source, content, lock_in, created_at, metadata
            FROM memory_nodes
            WHERE source LIKE 'lunafm:%'
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        )
    artifacts = []
    for r in rows:
        artifacts.append({
            "id": r[0], "node_type": r[1], "source": r[2],
            "content": r[3], "lock_in": r[4], "created_at": r[5],
            "metadata": r[6],
        })
    return {"artifacts": artifacts}


@app.get("/status", response_model=StatusResponse)
async def get_status():
    """
    Get engine status and metrics.

    Returns current state, uptime, tick counts, and actor list.
    Includes agentic processing stats when available.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    status = _engine.status()

    # Build agentic stats if available
    agentic_stats = None
    if "agentic" in status:
        agentic_data = status["agentic"]
        agentic_stats = AgenticStats(
            is_processing=agentic_data.get("is_processing", False),
            current_goal=agentic_data.get("current_goal"),
            pending_messages=agentic_data.get("pending_messages", 0),
            tasks_started=agentic_data.get("tasks_started", 0),
            tasks_completed=agentic_data.get("tasks_completed", 0),
            tasks_aborted=agentic_data.get("tasks_aborted", 0),
            direct_responses=agentic_data.get("direct_responses", 0),
            planned_responses=agentic_data.get("planned_responses", 0),
            agent_loop_status=agentic_data.get("agent_loop_status", "idle"),
        )

    # Build identity state if available
    identity_state = None
    identity_actor = _engine.get_actor("identity")
    if identity_actor:
        identity_state = IdentityState(
            enabled=True,
            is_present=identity_actor.current.is_present,
            entity_name=identity_actor.current.entity_name,
            luna_tier=identity_actor.current.luna_tier,
            confidence=round(identity_actor.current.confidence, 3),
        )

    return StatusResponse(
        state=status["state"],
        uptime_seconds=status["uptime_seconds"],
        cognitive_ticks=status["cognitive_ticks"],
        events_processed=status["events_processed"],
        messages_generated=status["messages_generated"],
        actors=status["actors"],
        buffer_size=status["buffer"]["size"],
        current_turn=status.get("current_turn", 0),
        context=status.get("context"),
        agentic=agentic_stats,
        identity=identity_state,
    )


@app.get("/health")
async def health_check():
    """Simple health check endpoint with pipeline status."""
    if _engine is None:
        return {"status": "starting"}

    # Pipeline status
    pipeline = {"connected": False, "scribe_extractions": None, "librarian_filings": None}
    scribe = _engine.get_actor("scribe")
    librarian = _engine.get_actor("librarian")
    if scribe and librarian:
        pipeline["connected"] = scribe.engine is not None and librarian.engine is not None
        pipeline["scribe_extractions"] = scribe.get_stats().get("extractions_count", 0)
        pipeline["librarian_filings"] = librarian.get_stats().get("filings_count", 0)

    # Pipeline staleness flag: true if user turns went in but nothing came out
    triggers = getattr(_engine.metrics, "extraction_triggers", 0)
    extractions = pipeline.get("scribe_extractions") or 0
    pipeline["stale"] = triggers >= 5 and extractions == 0
    pipeline["user_turns_triggered"] = triggers

    return {
        "status": "healthy",
        "state": _engine.state.name,
        "pipeline": pipeline,
    }


# ── Profile Auth (login / logout / me) ─────────────────────────────────────


class LoginRequest(BaseModel):
    slug: str
    password: str


@app.post("/api/auth/login")
async def auth_login(payload: LoginRequest, response: Response):
    """Verify slug+password against the registry, set the session cookie.

    Returns the public profile record. The cookie is HttpOnly + SameSite=Lax;
    Secure flag is set automatically when the request was served over HTTPS
    (Railway always serves HTTPS in production).
    """
    registry = ProfileRegistry()
    if not registry.exists():
        raise HTTPException(
            status_code=503,
            detail="Profile system not initialized. Run scripts/seed_profile.py migrate-admin.",
        )

    if not registry.verify_password(payload.slug, payload.password):
        # Generic message — don't leak whether the slug exists.
        raise HTTPException(status_code=401, detail="Invalid credentials")

    record = registry.get_profile(payload.slug)
    registry.touch_last_login(payload.slug)

    token = make_session_token(record.slug)
    is_https = os.environ.get("LUNA_FORCE_SECURE_COOKIE", "").lower() in ("1", "true", "yes")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=30 * 86400,
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/",
    )
    return {
        "profile": record.to_public_dict(),
        "session": {"expires_in": 30 * 86400},
    }


@app.post("/api/auth/logout")
async def auth_logout(response: Response):
    """Clear the session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@app.get("/api/auth/me")
async def auth_me(request: Request):
    """Return the current session's profile, or 401.

    Special cases:
      - LUNA_AUTH_DISABLE=1: returns a synthetic local-dev session (no login needed).
      - Profile system not initialized: returns 503 so the frontend can bypass the
        login gate rather than showing a form that can never succeed.
    """
    # Dev bypass — synthetic session so LoginGate renders immediately.
    if os.environ.get("LUNA_AUTH_DISABLE", "").lower() in ("1", "true", "yes"):
        return {
            "slug": "dev",
            "display_name": "Dev",
            "tier": "admin",
            "auth_disabled": True,
        }

    # If profiles.json doesn't exist yet we're in dev mode — return a synthetic
    # session so the frontend passes through without a login form.
    registry = ProfileRegistry()
    if not registry.exists():
        return {
            "slug": "dev",
            "display_name": "Dev",
            "tier": "admin",
            "auth_disabled": True,
        }

    session = current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "slug": session.slug,
        "display_name": session.display_name,
        "tier": session.tier,
    }


@app.get("/api/auth/profiles")
async def auth_list_profiles(request: Request):
    """List all profiles. Admin only — testers see only their own profile."""
    session = current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    registry = ProfileRegistry()
    if session.tier == "admin":
        profiles = [p.to_public_dict() for p in registry.list_profiles()]
    else:
        try:
            profiles = [registry.get_profile(session.slug).to_public_dict()]
        except ProfileNotFoundError:
            profiles = []
    return {"profiles": profiles}


# ── Admin profile management (invite + delete + password reset) ──────────


class AdminCreateProfileRequest(BaseModel):
    slug: str
    display_name: str
    password: str
    tier: str = "tester"  # accepts "tester" or "admin"


class AdminResetPasswordRequest(BaseModel):
    password: str


@app.post("/api/auth/profiles", status_code=201)
async def admin_create_profile(
    payload: AdminCreateProfileRequest,
    request: Request,
):
    """Admin-invite endpoint: create a new profile (tester by default).

    Mirror of `scripts/seed_profile.py create` but callable from the admin UI.
    Only callable by an admin session.
    """
    require_admin(request)
    from luna.auth.registry import (
        InvalidPasswordError,
        InvalidSlugError,
        ProfileAlreadyExistsError,
        ProfileRegistry,
    )

    if payload.tier not in ("tester", "admin"):
        raise HTTPException(status_code=400, detail="tier must be 'tester' or 'admin'")

    try:
        record = ProfileRegistry().create_profile(
            slug=payload.slug,
            display_name=payload.display_name,
            password=payload.password,
            tier=payload.tier,
            metadata={"created_by": current_session(request).slug},
        )
    except (InvalidSlugError, InvalidPasswordError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ProfileAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(
        "Admin %s created profile %s (tier=%s)",
        current_session(request).slug, record.slug, record.tier,
    )
    return {"profile": record.to_public_dict()}


@app.delete("/api/auth/profiles/{slug}")
async def admin_delete_profile(slug: str, request: Request):
    """Remove a profile from the registry. Does NOT delete the data dir.

    Caller is responsible for archiving/removing data/profiles/<slug>/ if needed.
    Admins cannot delete themselves (prevents accidental lockout).
    """
    session = require_admin(request)
    from luna.auth.registry import ProfileNotFoundError, ProfileRegistry

    if slug == session.slug:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete your own profile (would lock you out).",
        )
    registry = ProfileRegistry()
    try:
        target = registry.get_profile(slug)
    except ProfileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No profile {slug!r}")
    if target.tier == "admin":
        # Admins can't delete other admins via API — must use CLI to avoid
        # accidentally locking out the only admin
        raise HTTPException(
            status_code=403,
            detail="Cannot delete admin profiles via API (use scripts/seed_profile.py).",
        )
    registry.delete_profile(slug)
    logger.info("Admin %s deleted profile %s", session.slug, slug)
    return {"slug": slug, "deleted": True, "data_dir_preserved": True}


@app.post("/api/auth/profiles/{slug}/password")
async def admin_reset_password(
    slug: str,
    payload: AdminResetPasswordRequest,
    request: Request,
):
    """Reset another user's password. Admin-only — admins use the CLI for self-reset."""
    session = require_admin(request)
    from luna.auth.registry import (
        InvalidPasswordError,
        ProfileNotFoundError,
        ProfileRegistry,
    )

    registry = ProfileRegistry()
    try:
        registry.get_profile(slug)
    except ProfileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No profile {slug!r}")

    try:
        registry.set_password(slug, payload.password)
    except InvalidPasswordError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info("Admin %s reset password for profile %s", session.slug, slug)
    return {"slug": slug, "password_reset": True}


# ── Profile Config (per-profile cartridge config) ─────────────────────────


class ProfileConfigSetRequest(BaseModel):
    value: Any
    value_type: Optional[str] = None  # inferred from value if not given
    description: Optional[str] = None


async def _open_active_profile_db():
    """Open a MemoryDatabase connection to the currently-active profile's .lun.

    The auth middleware has already set the _current_profile contextvar from the
    cookie, so MemoryDatabase() (which calls memory_matrix_path()) lands on the
    right .lun. Caller must close the connection.

    NOTE: Opens a fresh connection per call. Acceptable for config endpoints which
    are low-frequency. The engine pool (step 3d) will replace this with a pooled
    engine's existing connection.
    """
    from luna.substrate.database import MemoryDatabase
    db = MemoryDatabase()
    await db.connect()
    return db


@app.get("/api/profile/config")
async def profile_config_get_all(request: Request):
    """Return all config keys for the active profile, with typed values."""
    require_auth(request)
    from luna.substrate.profile_config import ProfileConfig
    db = await _open_active_profile_db()
    try:
        return {"config": await ProfileConfig(db).all()}
    finally:
        await db.close()


@app.get("/api/profile/config/metadata")
async def profile_config_get_metadata(request: Request):
    """Return all config rows including audit fields (updated_at, updated_by, description)."""
    require_auth(request)
    from luna.substrate.profile_config import ProfileConfig
    db = await _open_active_profile_db()
    try:
        return {"rows": await ProfileConfig(db).all_with_metadata()}
    finally:
        await db.close()


@app.get("/api/profile/config/{key:path}")
async def profile_config_get_one(key: str, request: Request):
    """Return a single config key, or 404 if missing."""
    require_auth(request)
    from luna.substrate.profile_config import ProfileConfig
    db = await _open_active_profile_db()
    try:
        config = ProfileConfig(db)
        if not await config.has(key):
            raise HTTPException(status_code=404, detail=f"No config key {key!r}")
        return {"key": key, "value": await config.get(key)}
    finally:
        await db.close()


@app.put("/api/profile/config/{key:path}")
async def profile_config_set(key: str, payload: ProfileConfigSetRequest, request: Request):
    """Insert or update a config key. Value type is inferred unless specified."""
    session = require_auth(request)
    from luna.substrate.profile_config import ProfileConfig, ProfileConfigError
    db = await _open_active_profile_db()
    try:
        try:
            await ProfileConfig(db).set(
                key,
                payload.value,
                value_type=payload.value_type,
                updated_by=session.slug,
                description=payload.description,
            )
        except ProfileConfigError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"key": key, "value": payload.value, "updated_by": session.slug}
    finally:
        await db.close()


@app.delete("/api/profile/config/{key:path}")
async def profile_config_delete(key: str, request: Request):
    """Remove a config key."""
    require_auth(request)
    from luna.substrate.profile_config import ProfileConfig
    db = await _open_active_profile_db()
    try:
        deleted = await ProfileConfig(db).delete(key)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"No config key {key!r}")
        return {"key": key, "deleted": True}
    finally:
        await db.close()


# ── First-Run Detection + Onboarding ────────────────────────────────────────

def _check_api_keys_configured() -> bool:
    """Check if at least one LLM provider has an API key."""
    return any([
        os.environ.get("ANTHROPIC_API_KEY"),
        os.environ.get("GROQ_API_KEY"),
        os.environ.get("GOOGLE_API_KEY"),
    ])


@app.get("/api/status/first-run")
async def check_first_run():
    """Check if this is a first-run (unconfigured) instance."""
    from luna.core.owner import owner_configured, get_owner

    owner = get_owner()
    is_first = not owner_configured()

    return {
        "is_first_run": is_first,
        "owner_configured": not is_first,
        "owner_name": owner.display_name or None,
        "has_api_keys": _check_api_keys_configured(),
    }


@app.post("/api/factory-reset")
async def factory_reset():
    """Reset the instance to first-run state."""
    from luna.core.owner import get_owner

    targets = [
        memory_matrix_path(),
        config_dir() / "owner.yaml",
        config_dir() / "identity_bypass.json",
    ]
    for p in targets:
        if p.exists():
            p.unlink()

    # Clear secrets values (keep keys, empty values)
    sp = config_dir() / "secrets.json"
    if sp.exists():
        data = json.loads(sp.read_text())
        sp.write_text(json.dumps({k: "" for k in data if isinstance(data[k], str)}, indent=2))

    get_owner.cache_clear()
    return {"status": "reset", "restart_required": True}


@app.post("/api/preflight")
async def preflight_tracer():
    """Pre-flight check: exercise all subsystems, return health report."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    from luna.api.preflight import run_preflight
    return await run_preflight(_engine)


@app.post("/api/jumpstart")
async def jumpstart():
    """Restart degraded subsystems and verify via pre-flight."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    from luna.api.jumpstart import run_jumpstart
    return await run_jumpstart(_engine)


@app.post("/api/demo-reset")
async def demo_reset():
    """Reset demo instance to post-build, pre-wizard state.

    Only works if demo_mode is enabled in frontend_config.json.
    Preserves: secrets.json, collections, personality, frontend config.
    Wipes: memory_matrix.lun, owner.yaml, identity_bypass.json.
    """
    # Verify demo_mode is enabled
    fc_path = config_dir() / "frontend_config.json"
    if fc_path.exists():
        fc = json.loads(fc_path.read_text())
        if not fc.get("demo_mode", False):
            raise HTTPException(status_code=403, detail="Demo mode not enabled")
    else:
        raise HTTPException(status_code=403, detail="No frontend config found")

    wipe_targets = [
        memory_matrix_path(),
        config_dir() / "owner.yaml",
        config_dir() / "identity_bypass.json",
    ]
    wiped = []
    for p in wipe_targets:
        if p.exists():
            p.unlink()
            wiped.append(p.name)

    # Clear owner cache so /api/status/first-run returns is_first_run: true
    from luna.core.owner import get_owner
    get_owner.cache_clear()

    return {"status": "reset", "wiped": wiped, "message": "Refresh browser to start wizard"}


@app.post("/api/onboarding/owner")
async def set_owner(data: dict):
    """Set the owner identity during first-run wizard."""
    import yaml
    import aiosqlite
    from luna.core.owner import get_owner

    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    entity_id = name.lower().replace(" ", "-")

    # Write config/owner.yaml — ensure config dir exists (Railway volume starts empty)
    config_dir().mkdir(parents=True, exist_ok=True)

    owner_data = {
        "owner": {
            "entity_id": entity_id,
            "display_name": name,
            "aliases": [],
            "admin_contacts": [name],
        }
    }
    (config_dir() / "owner.yaml").write_text(
        yaml.dump(owner_data, default_flow_style=False)
    )

    # Write config/identity_bypass.json
    bypass = {
        "entity_id": entity_id,
        "entity_name": name,
        "luna_tier": "admin",
        "dataroom_tier": 1,
        "dataroom_categories": [],
    }
    (config_dir() / "identity_bypass.json").write_text(
        json.dumps(bypass, indent=2)
    )

    # Clear cached owner config
    get_owner.cache_clear()

    # Create owner entity in database (non-fatal — files above are sufficient)
    db_path = memory_matrix_path()
    try:
        if db_path.exists():
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute("PRAGMA busy_timeout=15000")
                await db.execute(
                    """INSERT OR IGNORE INTO entities
                       (id, name, entity_type, core_facts, aliases, created_at, updated_at)
                       VALUES (?, ?, 'person', ?, '[]', datetime('now'), datetime('now'))""",
                    (entity_id, name,
                     json.dumps({"relationship": "owner", "trust_level": "absolute"})),
                )
                await db.execute(
                    """INSERT OR IGNORE INTO entity_versions
                       (entity_id, version, changed_by, change_type, change_summary, created_at)
                       VALUES (?, 1, 'onboarding', 'create', ?, datetime('now'))""",
                    (entity_id,
                     json.dumps({"source": "welcome_wizard", "initial_setup": True})),
                )
                await db.commit()
    except Exception as e:
        logger.warning(f"Owner entity DB insert skipped: {e}")

    # Trigger personality bootstrap
    try:
        from luna.entities.bootstrap import bootstrap_personality, check_bootstrap_needed
        from luna.entities.storage import PersonalityPatchManager
        from luna.substrate.database import MemoryDatabase

        mdb = MemoryDatabase(db_path)
        await mdb.connect()
        pm = PersonalityPatchManager(mdb)
        if await check_bootstrap_needed(pm):
            await bootstrap_personality(pm)
        await mdb.close()
    except Exception as e:
        logger.warning(f"Personality bootstrap skipped: {e}")

    return {"status": "ok", "entity_id": entity_id, "name": name}


@app.get("/api/frontend-config")
async def get_frontend_config():
    """Serve frontend configuration (pages, widgets, remaps) from build profile."""
    return _load_frontend_config_data()


@app.post("/api/frontend-config")
async def save_frontend_config(updates: dict):
    """Persist frontend configuration updates from the client."""
    if not isinstance(updates, dict):
        raise HTTPException(status_code=400, detail="Config updates must be a JSON object")

    try:
        current = _load_frontend_config_data()
        merged = _merge_config_dict(current, updates)
        _save_frontend_config_data(merged)
        return merged
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to save frontend config")
        raise HTTPException(status_code=500, detail=f"Failed to save frontend config: {e}") from e


@app.get("/eden/health")
async def eden_health_check():
    """Check whether Eden API is configured and reachable."""
    if not _eden_enabled():
        return {"status": "disabled", "reason": "Eden disabled by policy"}

    try:
        from luna.services.eden.config import EdenConfig
        config = EdenConfig.load()
        if not config.is_configured:
            return {"status": "unconfigured", "reason": "EDEN_API_KEY not set"}

        from luna.services.eden.adapter import EdenAdapter
        adapter = EdenAdapter(config)
        async with adapter:
            healthy = await adapter.health_check()
        return {
            "status": "healthy" if healthy else "unreachable",
            "api_base": config.api_base,
        }
    except Exception as e:
        return {"status": "error", "reason": str(e)}


class EdenGenerateRequest(BaseModel):
    prompt: str
    wait: bool = True


@app.post("/eden/generate")
async def eden_generate_image(req: EdenGenerateRequest):
    """Generate an image via Eden API."""
    if not _eden_enabled():
        raise HTTPException(status_code=503, detail="Eden is offline")

    try:
        from luna.services.eden.config import EdenConfig
        from luna.services.eden.adapter import EdenAdapter

        config = EdenConfig.load()
        if not config.is_configured:
            raise HTTPException(status_code=503, detail="Eden not configured (EDEN_API_KEY missing)")

        adapter = EdenAdapter(config)
        async with adapter:
            task = await adapter.create_image(prompt=req.prompt, wait=req.wait)
            return {
                "status": str(task.status.value) if hasattr(task.status, 'value') else str(task.status),
                "image_url": task.first_output_url,
                "task_id": task.id,
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# Project Scoping Endpoints
# =========================================================================

class ProjectActivateRequest(BaseModel):
    slug: str

@app.post("/project/activate")
async def activate_project(req: ProjectActivateRequest):
    """Set the active project for scoped memory isolation."""
    _engine.set_active_project(req.slug)

    # Forward to Librarian for thread tagging
    librarian = _engine.get_actor("librarian")
    if librarian:
        from luna.actors.base import Message
        msg = Message(type="set_project_context", payload={"slug": req.slug})
        await librarian.handle(msg)

    return {
        "status": "activated",
        "project": req.slug,
        "scope": _engine.active_scope,
        "scopes": _engine.active_scopes,
    }

@app.post("/project/deactivate")
async def deactivate_project():
    """Clear the active project (return to global memory only)."""
    old = _engine.active_project
    _engine.set_active_project(None)

    # Forward to Librarian — auto-parks active thread
    librarian = _engine.get_actor("librarian")
    if librarian:
        from luna.actors.base import Message
        msg = Message(type="clear_project_context", payload={})
        await librarian.handle(msg)

    return {
        "status": "deactivated",
        "previous_project": old,
        "scope": _engine.active_scope,
    }

@app.get("/project/active")
async def get_active_project():
    """Get the currently active project and scope."""
    return {
        "project": _engine.active_project,
        "scope": _engine.active_scope,
        "scopes": _engine.active_scopes,
    }


@app.get("/api/projects")
async def get_projects():
    """List all defined projects with active state."""
    import yaml as _yaml
    registry_path = config_dir() / "projects" / "projects.yaml"
    if not registry_path.exists():
        return {"projects": [], "active": _engine.active_project if _engine else None}
    with open(registry_path) as f:
        data = _yaml.safe_load(f) or {}
    projects = []
    for slug, conf in data.get("projects", {}).items():
        projects.append({
            "slug": slug,
            "name": conf.get("name", slug),
            "description": conf.get("description", ""),
            "ingestion_pattern": conf.get("ingestion_pattern", "utilitarian"),
            "aperture_default": conf.get("aperture_default", "BALANCED"),
            "collections": conf.get("collections", []),
            "icon": conf.get("icon", "◇"),
            "accent": conf.get("accent", "A78BFA"),
            "active": slug == (_engine.active_project if _engine else None),
        })
    return {"projects": projects, "active": _engine.active_project if _engine else None}


@app.post("/api/system/relaunch")
async def relaunch_system(background_tasks: BackgroundTasks):
    """
    Trigger a system relaunch. Admin-only.

    Executes the relaunch script in the background and returns immediately.
    The server will restart, so the client should expect a brief disconnection.
    """
    await _require_admin()
    import subprocess
    import os
    from pathlib import Path

    script_path = scripts_dir() / "relaunch.sh"

    logger.info(f"[RELAUNCH] Looking for script at: {script_path}")

    if not script_path.exists():
        logger.error(f"[RELAUNCH] Script not found at: {script_path}")
        raise HTTPException(status_code=404, detail=f"Relaunch script not found at {script_path}")

    def run_relaunch():
        """Run the relaunch script in background."""
        try:
            subprocess.Popen(
                ["/bin/bash", str(script_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=str(project_root),
            )
        except Exception as e:
            logger.error(f"Failed to execute relaunch script: {e}")

    background_tasks.add_task(run_relaunch)

    return {
        "status": "restarting",
        "message": "Relaunch initiated. Server will restart shortly.",
    }


# ===========================================================================
# Ring Buffer API — Conversation memory controls
# ===========================================================================

class RingBufferStatus(BaseModel):
    """Ring buffer status response."""
    current_turns: int
    max_turns: int
    topics: list[str]
    recent_messages: list[dict]


class RingBufferConfig(BaseModel):
    """Ring buffer configuration request."""
    max_turns: int = Field(..., ge=2, le=20, description="Max turns (2-20)")


@app.get("/api/ring/status", response_model=RingBufferStatus)
async def get_ring_status():
    """
    Get current ring buffer status.

    Returns the number of turns, max capacity, detected topics,
    and recent messages in the conversation ring buffer.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    director = _engine.get_actor("director")
    if not director:
        raise HTTPException(status_code=503, detail="Director not available")

    # Access the active ring buffer
    ring = getattr(director, "_active_ring", None)
    if ring is None:
        ring = getattr(director, "_standalone_ring", None)

    if ring is None:
        return RingBufferStatus(
            current_turns=0,
            max_turns=20,
            topics=[],
            recent_messages=[],
        )

    # Get topics and recent messages
    topics = list(ring.get_mentioned_topics()) if hasattr(ring, "get_mentioned_topics") else []
    messages = ring.get_as_dicts() if hasattr(ring, "get_as_dicts") else []

    return RingBufferStatus(
        current_turns=len(ring),
        max_turns=ring._max_turns,
        topics=topics[:10],  # Limit to 10 topics
        recent_messages=messages[-6:],  # Last 6 messages
    )


@app.post("/api/ring/config")
async def configure_ring(config: RingBufferConfig):
    """
    Configure the ring buffer size.

    Changes take effect immediately. Existing history is preserved
    up to the new limit (oldest evicted if shrinking).
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    director = _engine.get_actor("director")
    if not director:
        raise HTTPException(status_code=503, detail="Director not available")

    # Get current ring
    ring = getattr(director, "_standalone_ring", None)
    if ring is None:
        raise HTTPException(status_code=503, detail="Ring buffer not available")

    # Preserve existing messages
    old_messages = list(ring._buffer)
    old_max = ring._max_turns

    # Create new buffer with new size
    from collections import deque
    ring._buffer = deque(maxlen=config.max_turns)
    ring._max_turns = config.max_turns

    # Re-add old messages (newest will be kept if shrinking)
    for msg in old_messages:
        ring._buffer.append(msg)

    logger.info(f"[RING-API] Resized from {old_max} to {config.max_turns} turns")

    return {
        "status": "configured",
        "previous_max_turns": old_max,
        "new_max_turns": config.max_turns,
        "current_turns": len(ring),
    }


@app.post("/api/ring/clear")
async def clear_ring():
    """
    Clear the ring buffer.

    Resets conversation memory. Use sparingly — typically for
    starting a fresh conversation or after significant context shifts.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    director = _engine.get_actor("director")
    if not director:
        raise HTTPException(status_code=503, detail="Director not available")

    ring = getattr(director, "_standalone_ring", None)
    if ring is None:
        raise HTTPException(status_code=503, detail="Ring buffer not available")

    old_size = len(ring)
    ring.clear()
    logger.info(f"[RING-API] Cleared {old_size} turns")

    return {
        "status": "cleared",
        "cleared_turns": old_size,
    }


@app.post("/api/history/clear")
async def clear_history():
    """
    Delete all conversation_turns from the DB and wipe the ring buffer.
    Called by the frontend on new session start or "Start fresh".
    """
    import aiosqlite

    deleted = 0
    try:
        db_path = memory_matrix_path()
        if db_path.exists():
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute("PRAGMA busy_timeout=15000")
                cursor = await db.execute("DELETE FROM conversation_turns")
                await db.commit()
                deleted = cursor.rowcount
    except Exception as e:
        logger.error(f"[HISTORY-CLEAR] DB delete failed: {e}")

    ring_cleared = 0
    if _engine is not None:
        director = _engine.get_actor("director")
        if director:
            for attr in ("_standalone_ring", "_active_ring"):
                ring = getattr(director, attr, None)
                if ring is not None:
                    ring_cleared = len(ring)
                    ring.clear()
                    break

    logger.info(f"[HISTORY-CLEAR] Deleted {deleted} DB turns, cleared {ring_cleared} ring turns")
    return {
        "status": "cleared",
        "deleted_turns": deleted,
        "ring_cleared": ring_cleared,
    }


@app.get("/history", response_model=HistoryResponse)
async def get_history(limit: int = 50):
    """
    Get recent conversation history from the conversation_turns table.

    Queries the database directly — no dependency on matrix actor readiness.
    """
    try:
        import aiosqlite
        from luna.core.paths import user_dir

        db_path = memory_matrix_path()
        if not db_path.exists():
            return HistoryResponse(messages=[], total=0)

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=15000")
            cursor = await db.execute("""
                SELECT role, content, created_at, metadata
                FROM conversation_turns
                WHERE role IN ('user', 'assistant')
                  AND content NOT LIKE '[Memory %'
                  AND content NOT LIKE '[SYSTEM:%'
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()

        messages = []
        for row in rows:
            # metadata is JSON-encoded TEXT; legacy rows may have NULL or
            # malformed values, so parse defensively and drop on failure.
            raw_meta = row["metadata"]
            parsed_meta = None
            if raw_meta:
                try:
                    parsed_meta = json.loads(raw_meta)
                except (TypeError, ValueError):
                    parsed_meta = None
            messages.append(HistoryMessage(
                role=row["role"],
                content=row["content"],
                timestamp=row["created_at"],
                metadata=parsed_meta,
            ))

        # Reverse to chronological order
        messages.reverse()

        return HistoryResponse(messages=messages, total=len(messages))
    except Exception as e:
        import traceback
        logger.error(f"Failed to get history: {e}\n{traceback.format_exc()}")
        return HistoryResponse(messages=[], total=0)


@app.get("/api/history/timeline")
async def history_timeline():
    """Return conversation volume per month for the timeline scrubber."""
    try:
        import aiosqlite
        from luna.core.paths import user_dir

        db_path = memory_matrix_path()
        if not db_path.exists():
            return {"months": []}

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=15000")
            cursor = await db.execute(
                "SELECT strftime('%Y-%m', created_at) AS month, COUNT(*) AS count "
                "FROM conversation_turns "
                "WHERE role IN ('user', 'assistant') "
                "GROUP BY month ORDER BY month"
            )
            rows = await cursor.fetchall()

        return {"months": [{"month": row["month"], "count": row["count"]} for row in rows]}
    except Exception as e:
        logger.error(f"Failed to get history timeline: {e}")
        return {"months": []}


@app.get("/consciousness", response_model=ConsciousnessResponse)
async def get_consciousness():
    """
    Get Luna's current consciousness state.

    Returns mood, coherence, attention topics, personality traits, and tick count.
    """
    if _engine is None or _engine.consciousness is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    summary = _engine.consciousness.get_summary()

    return ConsciousnessResponse(
        mood=summary["mood"],
        coherence=summary["coherence"],
        attention_topics=summary["attention_topics"],
        focused_topics=summary["focused_topics"],
        top_traits=summary["top_traits"],
        tick_count=summary["tick_count"],
        last_updated=summary["last_updated"],
    )


@app.post("/stream")
async def stream_message(request: MessageRequest):
    """
    Send a message to Luna and stream the response via SSE.

    Returns Server-Sent Events with:
    - event: token - For each generated token
    - event: done - When generation completes
    - event: error - If an error occurs
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    async def generate_sse() -> AsyncGenerator[str, None]:
        """Generate SSE events for streaming response."""
        token_queue: asyncio.Queue[str | None] = asyncio.Queue()
        response_complete = asyncio.Event()
        final_data: dict = {}
        error_msg: str | None = None
        # Tracks whether any token was streamed before completion. If not,
        # on_complete pushes the completion text into the queue so error-path
        # responses (no per-token streaming) still render in the client bubble.
        # List-wrapped for closure mutability.
        tokens_streamed = [False]

        def on_token(text: str) -> None:
            """Callback for each token."""
            tokens_streamed[0] = True
            token_queue.put_nowait(text)

        async def on_complete(text: str, data: dict) -> None:
            """Callback when generation completes."""
            nonlocal final_data
            # Always carry text in final_data so the SSE done payload and the
            # downstream turn recorder see it regardless of which event path
            # fired (success or error fallback).
            final_data = {**data, "text": data.get("text") or text} if (text or data) else {}
            if text and not tokens_streamed[0]:
                token_queue.put_nowait(text)
            token_queue.put_nowait(None)  # Signal end
            response_complete.set()

        # Get director and register callbacks
        director = _engine.get_actor("director")
        if not director:
            yield f"event: error\ndata: {json.dumps({'error': 'Director not available'})}\n\n"
            return

        # Register streaming callback
        director.on_stream(on_token)
        _engine.on_response(on_complete)

        try:
            # Get memory context
            memory_context = ""
            matrix = _engine.get_actor("matrix")
            if matrix and matrix.is_ready:
                # Constellation prefetch before general search
                _constellation_ctx = ""
                try:
                    from luna.compiler.constellation_prefetch import ConstellationPrefetch as _CPF2
                    from luna.compiler.entity_index import EntityIndex as _EI2
                    from pathlib import Path as _PP2
                    _ep2 = _PP2("data/guardian/entities/entities_updated.json")
                    if _ep2.exists():
                        _idx2 = _EI2()
                        _idx2.load_entities(_ep2)
                        _cpf2 = _CPF2(matrix, _idx2)
                        _scopes2 = getattr(_engine, 'active_scopes', None) or ["global"]
                        _sc2 = next((s for s in _scopes2 if s.startswith("project:")), None)
                        _pf2 = await _cpf2.prefetch(request.message, scope=_sc2)
                        if _pf2 and _pf2.nodes:
                            _pts2 = []
                            for _nn in _pf2.nodes:
                                _pts2.append(
                                    f"<memory type=\"{_nn.node_type}\" "
                                    f"lock_in=\"{getattr(_nn, 'lock_in', 0.8):.2f}\" "
                                    f"source=\"compiled\">\n{_nn.content}\n</memory>"
                                )
                            _constellation_ctx = "\n\n".join(_pts2)
                except Exception:
                    pass

                memory_context = await _fetch_matrix_context_with_door_routing(
                    _engine,
                    matrix,
                    request.message,
                    max_tokens=1500,
                )
                if _constellation_ctx:
                    memory_context = _constellation_ctx + ("\n\n" + memory_context if memory_context else "")

                # ── Phase 2: Nexus collection context — DEFAULT-OFF ──
                # Mirrors the post-cut /message path: dataroom is no longer
                # unconditionally injected. Direct callers (engine.
                # _get_collection_context, search_chain, MCP aibrarian_*)
                # remain available for explicit on-demand retrieval. Emit a
                # WARNING-level skip log to match retrieval.py's pattern.
                logger.warning(
                    "[STREAM] dataroom skipped: include_dataroom=False"
                )

            # Record user turn through unified API (extraction + history + matrix)
            import sqlite3 as _sqlite3_s
            for _srec_attempt in range(3):
                try:
                    await _engine.record_conversation_turn(
                        role="user",
                        content=request.message,
                        source="stream",
                        turn_metadata=request.metadata,
                    )
                    break
                except _sqlite3_s.OperationalError as _srec_err:
                    if "database is locked" in str(_srec_err) and _srec_attempt < 2:
                        logger.warning(f"[STREAM] DB lock on user turn record (attempt {_srec_attempt + 1}/3), retrying...")
                        await asyncio.sleep([0.5, 1.0, 2.0][_srec_attempt])
                    else:
                        raise

            # Send streaming generation request
            msg = Message(
                type="generate_stream",
                payload={
                    "user_message": request.message,
                    "system_prompt": _engine._build_system_prompt(memory_context),
                },
            )
            await director.mailbox.put(msg)

            # Stream tokens as they arrive — track TTFT for Guardian
            import time as _ttft_time2
            _ttft_start2 = _ttft_time2.time()
            _first_token2 = False

            while True:
                try:
                    token = await asyncio.wait_for(
                        token_queue.get(),
                        timeout=request.timeout
                    )

                    if token is None:
                        # End of stream
                        break

                    # TTFT tracking → Guardian alert if >10s
                    if not _first_token2:
                        _first_token2 = True
                        _ttft2_ms = (_ttft_time2.time() - _ttft_start2) * 1000
                        if _ttft2_ms > 10_000:
                            try:
                                from luna.core.event_bus import event_bus, KnowledgeEvent
                                event_bus.emit("knowledge", KnowledgeEvent(
                                    type="response_slow",
                                    payload={
                                        "severity": "warning",
                                        "time_to_first_token_ms": round(_ttft2_ms),
                                        "provider": "unknown",
                                        "route": "stream",
                                        "message_preview": request.message[:60],
                                        "diagnosis_hint": f"TTFT {_ttft2_ms/1000:.1f}s on /message stream",
                                    },
                                ))
                            except Exception:
                                pass

                    # Send token as SSE event
                    yield f"event: token\ndata: {json.dumps({'text': token})}\n\n"

                except asyncio.TimeoutError:
                    _to_ms = (_ttft_time2.time() - _ttft_start2) * 1000
                    try:
                        from luna.core.event_bus import event_bus, KnowledgeEvent
                        event_bus.emit("knowledge", KnowledgeEvent(
                            type="response_slow",
                            payload={
                                "severity": "critical",
                                "time_to_first_token_ms": round(_to_ms),
                                "provider": "unknown",
                                "route": "stream",
                                "message_preview": request.message[:60],
                                "diagnosis_hint": f"Full timeout after {_to_ms/1000:.0f}s on /message stream",
                            },
                        ))
                    except Exception:
                        pass
                    yield f"event: error\ndata: {json.dumps({'error': 'Timeout waiting for tokens'})}\n\n"
                    break

            # Send completion event
            yield f"event: done\ndata: {json.dumps(final_data)}\n\n"

            # Record assistant turn (Scribe skips assistant turns by design,
            # but this feeds HistoryManager + matrix storage)
            response_text = final_data.get("text", "")
            if response_text:
                await _engine.record_conversation_turn(
                    role="assistant",
                    content=response_text,
                    source="stream",
                    tokens=final_data.get("output_tokens"),
                )

        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

        finally:
            # Cleanup callbacks
            director.remove_stream_callback(on_token)
            if on_complete in _engine._on_response_callbacks:
                _engine._on_response_callbacks.remove(on_complete)

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@app.post("/persona/stream")
async def persona_stream(
    request: MessageRequest,
    engine=Depends(request_engine),
):
    """
    Stream Luna's response with context-first SSE format.

    This endpoint sends context (memory + state) BEFORE streaming tokens,
    allowing the frontend to prepare UI with relevant information.

    SSE data format (no named events, typed JSON):
    - {"type": "context", "memory": [...], "state": {...}}
    - {"type": "token", "text": "chunk"}
    - {"type": "done", "response": "full text", "metadata": {...}}
    - {"type": "error", "message": "..."}
    """

    async def generate_sse() -> AsyncGenerator[str, None]:
        """Generate context-first SSE stream."""
        token_queue: asyncio.Queue[str | None] = asyncio.Queue()
        response_complete = asyncio.Event()
        full_response: list[str] = []
        final_metadata: dict = {}

        def on_token(text: str) -> None:
            """Callback for each token."""
            full_response.append(text)
            token_queue.put_nowait(text)

        async def on_complete(text: str, data: dict) -> None:
            """Callback when generation completes.

            If no tokens were streamed (e.g. error-fallback path or any
            non-streaming completion), surface the completion text as a single
            chunk so `final_text = "".join(full_response)` resolves to the
            user-visible message instead of an empty string. This makes the
            engine's friendly fallback ("hmm, I'm having a moment...") render
            in the bubble rather than leaving an empty error-fallback row.
            """
            nonlocal final_metadata
            final_metadata = data
            if text and not full_response:
                full_response.append(text)
                token_queue.put_nowait(text)
            token_queue.put_nowait(None)  # Signal end
            response_complete.set()

        # Get director
        director = engine.get_actor("director")
        if not director:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Director not available'})}\n\n"
            return

        # Get matrix for memory context + search chain for dataroom/web/local
        matrix = engine.get_actor("matrix")
        memory_items = []
        memory_context = ""

        import time as _stime
        _st0 = _stime.time()
        _st: dict = {}

        try:
            # --- PHASE 1: Send context first ---

            # Constellation prefetch: inject PERSON_BRIEFING etc. before search chain
            _t_st = _stime.time()
            constellation_prefix = ""
            try:
                from luna.compiler.constellation_prefetch import ConstellationPrefetch
                from luna.compiler.entity_index import EntityIndex
                _entities_path = local_dir() / "guardian" / "entities" / "entities_updated.json"
                if _entities_path.exists() and matrix and matrix.is_ready:
                    _eidx = EntityIndex()
                    _eidx.load_entities(_entities_path)
                    _cpf = ConstellationPrefetch(matrix, _eidx)
                    _active_scopes = getattr(engine, 'active_scopes', None) or ["global"]
                    _scope = next((s for s in _active_scopes if s.startswith("project:")), None)
                    _pfr = await _cpf.prefetch(request.message, scope=_scope)
                    if _pfr and _pfr.nodes:
                        _parts = []
                        for _n in _pfr.nodes:
                            _parts.append(
                                f"<memory type=\"{_n.node_type}\" "
                                f"lock_in=\"{getattr(_n, 'lock_in', 0.8):.2f}\" "
                                f"source=\"compiled\">\n{_n.content}\n</memory>"
                            )
                        constellation_prefix = "\n\n".join(_parts)
                        logger.info(
                            f"[STREAM] Constellation prefetch: {len(_pfr.nodes)} nodes, "
                            f"~{_pfr.tokens_used} tokens"
                        )
            except Exception as _cpf_err:
                logger.warning(f"[STREAM] Constellation prefetch failed: {_cpf_err}")
            _st["constellation_prefetch"] = _stime.time() - _t_st

            # Use the configurable search chain (matrix + dataroom + optional sources)
            _t_st = _stime.time()
            from luna.tools.search_chain import SearchChainConfig, run_search_chain
            _search_cfg = getattr(engine, "_search_chain_config", None) or SearchChainConfig.default()
            chain_results = await run_search_chain(_search_cfg, request.message, engine)
            if chain_results:
                memory_context = "\n\n".join(r.get("content", "") for r in chain_results if r.get("content"))
                logger.info(f"[STREAM] Search chain returned {len(chain_results)} results, {len(memory_context)} chars")
            else:
                logger.info("[STREAM] Search chain returned no results")
            _st["search_chain"] = _stime.time() - _t_st

            # L1 Geometric Layer: populate RevolvingContext with composite-scored
            _t_st = _stime.time()
            # memory nodes so _retrieval_score flows through as per-item relevance.
            # This runs alongside run_search_chain (which feeds memory_context string);
            # our purpose here is ring-state population for decay/rebalance.
            try:
                _inner = getattr(matrix, '_matrix', None)
                if _inner:
                    from luna.core.context import ContextSource as _CS_L1
                    from luna.core.types import Door as _Door_L1
                    _l1_scopes = getattr(engine, 'active_scopes', None) or ["global"]
                    _l1_nodes = await _inner.get_context(
                        request.message, max_tokens=1500, scopes=_l1_scopes
                    )
                    if _l1_nodes:
                        for _ln in _l1_nodes:
                            _lscore = getattr(_ln, '_retrieval_score', 1.0)
                            _ltext = _ln.summary or _ln.content
                            engine.context.add(
                                content=_ltext,
                                source=_CS_L1.MEMORY,
                                door=_Door_L1.NEXUS,
                                relevance=_lscore,
                            )
                        logger.warning(
                            f"[STREAM-L1] Added {len(_l1_nodes)} scored memory nodes to ring "
                            f"(top score={_l1_nodes[0]._retrieval_score:.3f})"
                        )
            except Exception as _l1_err:
                logger.warning(f"[STREAM-L1] Ring population failed: {_l1_err}")
            _st["l1_ring_population"] = _stime.time() - _t_st

            # ── Nexus collection context (mirrors /stream path) ──
            _t_st = _stime.time()
            try:
                collection_context = await engine._get_collection_context(request.message)
                if collection_context:
                    memory_context = (memory_context or "") + "\n\n" + collection_context
                    logger.info(f"[PERSONA-STREAM] Nexus context injected: {len(collection_context)} chars")
            except Exception as _col_err:
                logger.warning(f"[PERSONA-STREAM] collection context failed: {_col_err}")
            _st["nexus_collection"] = _stime.time() - _t_st

            # Prepend constellations before general search results
            if constellation_prefix:
                memory_context = constellation_prefix + ("\n\n" + memory_context if memory_context else "")

            # Get recent memories for frontend display
            if matrix and matrix.is_ready:
                recent = await matrix.get_recent_turns(limit=5)
                memory_items = [
                    {
                        "id": str(m.id),
                        "content": (m.content or "")[:200],  # Truncate for frontend
                        "type": "conversation_turn",
                        "source": "conversation",
                        "role": getattr(m, "role", "unknown"),
                    }
                    for m in recent
                ]

            # Record user turn through unified API (extraction + history + matrix)
            # Retry on DB lock — this write contends with background extraction tasks
            import sqlite3 as _sqlite3
            for _rec_attempt in range(3):
                try:
                    await engine.record_conversation_turn(
                        role="user",
                        content=request.message,
                        source="stream",
                        turn_metadata=request.metadata,
                    )
                    break
                except _sqlite3.OperationalError as _rec_err:
                    if "database is locked" in str(_rec_err) and _rec_attempt < 2:
                        logger.warning(f"[PERSONA-STREAM] DB lock on user turn record (attempt {_rec_attempt + 1}/3), retrying...")
                        await asyncio.sleep([0.5, 1.0, 2.0][_rec_attempt])
                    else:
                        raise

            # Build state summary
            state_summary = {
                "session_id": getattr(engine, "session_id", "unknown"),
                "is_processing": True,
                "state": str(getattr(engine, "_state", "unknown")),
                "model": getattr(director, "_current_model", "unknown"),
            }

            # Send context event FIRST
            context_event = {
                "type": "context",
                "memory": memory_items,
                "state": state_summary,
            }
            yield f"data: {json.dumps(context_event)}\n\n"

            # --- ROUTING: Determine execution path ---
            routing = engine.router.analyze(request.message)
            engine.metrics.agentic_tasks_started += 1

            # Emit actual routing decision to Thought Stream
            await engine._emit_progress(
                f"[{routing.path.name}] {request.message[:40]}..."
                + (f" signals={routing.signals}" if routing.signals else "")
            )

            # --- PHASE 2: Execute based on routing ---
            if routing.path == ExecutionPath.DIRECT:
                # DIRECT path: stream straight from director
                engine.metrics.direct_responses += 1
            else:
                # SIMPLE_PLAN or FULL_PLAN: execute memory retrieval first
                engine.metrics.planned_responses += 1

                # OBSERVE phase
                await engine._emit_progress("[OBSERVE] Gathering context... (1/2)")

                # Execute knowledge retrieval via search chain
                if "memory_query" in routing.signals or matrix and matrix.is_ready:
                    await engine._emit_progress("[THINK] Deciding: Execute knowledge search...")
                    await engine._emit_progress("[ACT:tool] Execute search chain...")

                    # Re-fetch with higher budget for planned queries
                    _plan_cfg = getattr(engine, "_search_chain_config", None) or SearchChainConfig.default()
                    plan_results = await run_search_chain(_plan_cfg, request.message, engine)
                    if plan_results:
                        _plan_ctx = "\n\n".join(r.get("content", "") for r in plan_results if r.get("content"))
                        # Preserve constellation prefix from earlier prefetch
                        if constellation_prefix:
                            memory_context = constellation_prefix + "\n\n" + _plan_ctx
                        else:
                            memory_context = _plan_ctx
                        sources = list(set(r.get("source", "?") for r in plan_results))
                        await engine._emit_progress(
                            f"[OK] Retrieved {len(memory_context)} chars from {sources}"
                        )
                    else:
                        await engine._emit_progress("[OK] No knowledge found, proceeding without")

                    # ── Nexus collection context for planned queries ──
                    try:
                        _plan_col_ctx = await engine._get_collection_context(request.message)
                        if _plan_col_ctx:
                            memory_context = (memory_context or "") + "\n\n" + _plan_col_ctx
                            logger.info(f"[PERSONA-STREAM] Planned Nexus context: {len(_plan_col_ctx)} chars")
                    except Exception as _pcol_err:
                        logger.warning(f"[PERSONA-STREAM] planned collection context failed: {_pcol_err}")

                # OBSERVE phase 2
                await engine._emit_progress("[OBSERVE] Gathering context... (2/2)")
                await engine._emit_progress("[THINK] Deciding: Present result to user...")
                await engine._emit_progress("[ACT:respond] Present result to user...")

            # --- PHASE 3: Stream tokens ---
            # Notify orb state manager that response is starting.
            # Dimensional feed (sentiment, flow, topic) is handled by CacheActor
            # on every extraction turn — no inline feed needed here.
            if _orb_state_manager:
                _orb_state_manager.start_response()

            director.on_stream(on_token)
            engine.on_response(on_complete)

            # Tell engine that this streaming endpoint owns turn recording
            # (prevents _handle_actor_message from double-recording)
            engine._stream_owns_response = True

            # --- PRE-GENERATION: Reconcile tick ---
            # If Scout flagged confabulation on a previous turn, inject
            # a self-correction instruction into the system prompt.
            # Voice surfaces get the short-form template so the correction
            # fits the 2-4 sentence voice budget.
            reconcile = getattr(engine, 'reconcile', None)
            _voice_mode = (getattr(request, 'source', '') or '').lower() == 'voice'
            reconcile_instruction = reconcile.tick(voice_mode=_voice_mode) if reconcile else None

            _t_st = _stime.time()

            # Mirror director._generate_with_delegation (director.py:3088-3120):
            # pull conversation history from the director's ring buffer so the
            # assembled prompt has prior-turn context.
            conversation_history = []
            _ring = getattr(director, "_active_ring", None)
            if _ring is not None and len(_ring) > 0:
                conversation_history = _ring.get_as_dicts()

            _aperture = getattr(engine, "aperture", None)
            prompt_req = PromptRequest(
                message=request.message,
                conversation_history=conversation_history,
                route="delegated",
                memory_context=memory_context if memory_context else None,
                auto_fetch_memory=False,
                aperture=_aperture.state if _aperture else None,
                reflection_mode=getattr(engine, "_active_reflection_mode", None),
            )
            assembler_result = await director._assembler.build(prompt_req)
            system_prompt = assembler_result.system_prompt

            _st["system_prompt_build"] = _stime.time() - _t_st
            logger.info(
                "[STREAM] PromptAssembler: identity=%s voice_injected=%s tokens≈%d",
                assembler_result.identity_source,
                assembler_result.voice_injected,
                assembler_result.prompt_tokens,
            )
            try:
                _st["pre_director_total"] = _stime.time() - _st0
                logger.warning(
                    "[STREAM-TIMING] pre-director=%.3fs | %s",
                    _st["pre_director_total"],
                    " | ".join(
                        f"{k}={v:.3f}s"
                        for k, v in sorted(_st.items(), key=lambda x: -x[1])
                        if k != "pre_director_total"
                    ),
                )
            except Exception:
                pass
            if reconcile_instruction:
                # Budget-aware injection — match config/rings.yaml total_budget
                # to avoid pushing the prompt past the assembler's ceiling.
                # Rough char-to-token estimate (~4 chars/token) is consistent
                # with assembler's len()//4 helper used elsewhere in this file.
                _RECONCILE_BUDGET = 8000
                _current_tokens = len(system_prompt) // 4
                _reconcile_tokens = len(reconcile_instruction) // 4
                if _current_tokens + _reconcile_tokens <= int(_RECONCILE_BUDGET * 0.95):
                    system_prompt += f"\n\n## Self-Correction\n{reconcile_instruction}\n"
                    logger.info("[STREAM] Reconcile instruction injected into system prompt")
                    await engine._emit_progress("[RECONCILE] Self-correction instruction active for this turn")
                else:
                    logger.warning(
                        f"[RECONCILE] Skipping injection — budget {_current_tokens}+{_reconcile_tokens}/{_RECONCILE_BUDGET}"
                    )
                    await engine._emit_progress("[RECONCILE] Self-correction skipped — prompt budget exhausted")

            # Send generation request — pass pre-fetched memory_context
            # so Director/Assembler uses it instead of auto-fetching from Matrix only
            # Also pass structured chain_results so Director can extract dataroom content
            # director_pivot_endpoint is non-persisted — lives in payload only.
            msg = Message(
                type="generate_stream",
                payload={
                    "user_message": request.message,
                    "system_prompt": system_prompt,
                    "memory_context": memory_context,
                    "chain_results": chain_results if chain_results else [],
                    "director_pivot_endpoint": "/persona/stream",
                },
            )
            await director.mailbox.put(msg)

            # Stream tokens as they arrive — track time-to-first-token for Guardian
            import time as _ttft_time
            _ttft_start = _ttft_time.time()
            _first_token_seen = False

            while True:
                try:
                    token = await asyncio.wait_for(
                        token_queue.get(),
                        timeout=request.timeout
                    )

                    if token is None:
                        break

                    # Track time-to-first-token → alert Guardian if >10s
                    if not _first_token_seen:
                        _first_token_seen = True
                        _ttft_ms = (_ttft_time.time() - _ttft_start) * 1000
                        if _ttft_ms > 10_000:
                            try:
                                from luna.core.event_bus import event_bus, KnowledgeEvent
                                event_bus.emit("knowledge", KnowledgeEvent(
                                    type="response_slow",
                                    payload={
                                        "severity": "warning",
                                        "time_to_first_token_ms": round(_ttft_ms),
                                        "provider": final_metadata.get("model", "unknown"),
                                        "route": "delegated" if final_metadata.get("delegated") else "local",
                                        "memory_nodes": len(memory_context) if memory_context else 0,
                                        "message_preview": request.message[:60],
                                        "diagnosis_hint": f"TTFT {_ttft_ms/1000:.1f}s — check provider latency or cold start",
                                    },
                                ))
                                logger.warning(f"[GUARDIAN-ALERT] Slow TTFT: {_ttft_ms:.0f}ms for '{request.message[:40]}'")
                            except Exception:
                                pass

                    # Process token for gestures (may strip or annotate based on config)
                    processed_token = token
                    if _orb_state_manager:
                        processed_token = _orb_state_manager.process_text_chunk(token)

                    yield f"data: {json.dumps({'type': 'token', 'text': processed_token})}\n\n"

                except asyncio.TimeoutError:
                    # Full timeout — critical Guardian alert
                    _timeout_ms = (_ttft_time.time() - _ttft_start) * 1000
                    try:
                        from luna.core.event_bus import event_bus, KnowledgeEvent
                        event_bus.emit("knowledge", KnowledgeEvent(
                            type="response_slow",
                            payload={
                                "severity": "critical",
                                "time_to_first_token_ms": round(_timeout_ms),
                                "provider": final_metadata.get("model", "unknown"),
                                "route": "unknown",
                                "memory_nodes": len(memory_context) if memory_context else 0,
                                "message_preview": request.message[:60],
                                "diagnosis_hint": f"Full timeout after {_timeout_ms/1000:.0f}s — no tokens received. Check Director/LLM.",
                            },
                        ))
                        logger.error(f"[GUARDIAN-ALERT] Full timeout: {_timeout_ms:.0f}ms for '{request.message[:40]}'")
                    except Exception:
                        pass
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Timeout waiting for tokens'})}\n\n"
                    break

            # --- PHASE 3: Send done event ---
            # Emit completion for Thought Stream
            tokens = final_metadata.get("output_tokens", len("".join(full_response)) // 4)
            route = "local" if final_metadata.get("local") else "delegated"
            await engine._emit_progress(f"[OK] {route}: {tokens} tokens")

            final_text = "".join(full_response)
            # Strip gesture markers from final response (detection already happened per-token)
            if _orb_state_manager and _orb_state_manager.expression_config:
                if _orb_state_manager.expression_config.should_strip_gestures():
                    final_text = _orb_state_manager._strip_gestures(final_text)

            # GroundingLink: trace response sentences to injected memories
            grounding_meta = None
            if _grounding_link and engine:
                try:
                    director = engine.get_actor("director")
                    injected = getattr(director, "_last_injected_memories", None) or []
                    if injected:
                        grounding_result = _grounding_link.ground(final_text, injected)
                        grounding_meta = grounding_result.to_dict()
                except Exception:
                    pass

            # Extract interactive options from response
            final_text, parsed_options = extract_options(final_text)
            if parsed_options:
                final_metadata["widget"] = build_options_widget(parsed_options)
                logger.info(f"[OPTIONS] Extracted {len(parsed_options)} options, widget attached to done event")

            # Phase 5 chat-UI metadata. memoryAnchor / content_type / actors
            # arrive on `final_metadata` from Director when applicable; quoteEcho
            # is echoed back from the request so the user-bubble can re-attach it.
            _interrupt_type = await _lookup_pending_interrupt_type()
            if _interrupt_type:
                final_metadata["interrupt_type"] = _interrupt_type
            _quote_echo = (request.metadata or {}).get("quoteEcho")
            if _quote_echo:
                final_metadata["quoteEcho"] = _quote_echo

            done_event = {
                "type": "done",
                "response": final_text,
                "metadata": final_metadata,
            }
            if grounding_meta:
                done_event["groundingMetadata"] = grounding_meta
            yield f"data: {json.dumps(done_event)}\n\n"

            # Notify orb state manager that response is complete
            if _orb_state_manager:
                _orb_state_manager.end_response()

            # Update completion metrics
            engine.metrics.agentic_tasks_completed += 1
            engine.metrics.messages_generated += 1

            # --- POST-GENERATION: Scout inspection + Reconcile ---
            response_text = "".join(full_response)

            if response_text:
                scout = engine.get_actor("scout") if hasattr(engine, 'get_actor') else None
                if scout:
                    context_size = len(memory_context) if memory_context else 0
                    try:
                        scout_report = scout.inspect(
                            draft=response_text,
                            query=request.message,
                            context_size=context_size,
                            retrieved_context=memory_context or "",
                        )

                        # Confabulation → flag for next-turn reconcile
                        if scout_report.blocked and scout_report.recommendation == "reconcile":
                            n_claims = len(scout_report.confabulation_data.get("unsupported_claims", [])) if scout_report.confabulation_data else 0
                            if reconcile and scout_report.confabulation_data:
                                reconcile.flag_confabulation(
                                    claims=scout_report.confabulation_data.get("unsupported_claims", []),
                                    original_query=request.message,
                                )
                                logger.warning(
                                    f"[STREAM] Confabulation flagged — "
                                    f"{n_claims} unsupported claims queued for reconcile"
                                )
                            await engine._emit_progress(
                                f"[SCOUT] Confabulation detected — {n_claims} unsupported claims, reconcile queued"
                            )
                        elif scout_report.blocked:
                            # Blocked for non-confabulation reason (surrender, shallow, etc.)
                            await engine._emit_progress(
                                f"[SCOUT] Blockage: {scout_report.blockage_type} "
                                f"(tier {scout_report.overdrive_tier})"
                            )
                        else:
                            # Clean pass
                            await engine._emit_progress("[SCOUT] Integrity check passed")
                    except Exception as e:
                        logger.error(f"[STREAM] Scout inspection failed: {e}")

                # Check if Luna self-corrected (reconcile detection)
                if reconcile and reconcile.did_reconcile(response_text):
                    logger.info("[STREAM] Luna reconciled — notifying Scribe")
                    await engine._emit_progress("[RECONCILE] Self-correction detected — filing CORRECTION node")
                    scribe = engine.get_actor("scribe") if hasattr(engine, 'get_actor') else None
                    if scribe:
                        from luna.actors.base import Message as ActorMessage
                        await scribe.handle(ActorMessage(
                            type="extract_correction",
                            payload={
                                "original_query": reconcile._state.original_query,
                                "flagged_claims": reconcile._state.flagged_claims,
                                "correction_response": response_text,
                                "session_id": getattr(engine, "session_id", "unknown"),
                            }
                        ))
                    reconcile.clear()

            # Record assistant turn (Scribe skips assistant turns by design,
            # but this feeds HistoryManager + matrix storage)
            if response_text:
                for _arec_attempt in range(3):
                    try:
                        await engine.record_conversation_turn(
                            role="assistant",
                            content=response_text,
                            source="stream",
                            tokens=final_metadata.get("output_tokens"),
                        )
                        break
                    except _sqlite3.OperationalError as _arec_err:
                        if "database is locked" in str(_arec_err) and _arec_attempt < 2:
                            logger.warning(f"[PERSONA-STREAM] DB lock on assistant turn record (attempt {_arec_attempt + 1}/3), retrying...")
                            await asyncio.sleep([0.5, 1.0, 2.0][_arec_attempt])
                        else:
                            logger.error(f"[PERSONA-STREAM] Failed to record assistant turn after retries: {_arec_err}")
                            break  # Non-fatal for assistant recording

        except Exception as e:
            logger.error(f"Persona stream error: {e}")
            # Surface a user-friendly message for DB lock instead of raw error
            _msg = str(e)
            if "database is locked" in _msg:
                _msg = "I'm still processing something — try again in a moment."
            yield f"data: {json.dumps({'type': 'error', 'message': _msg})}\n\n"

        finally:
            # Release stream ownership so engine handles non-streaming paths normally
            engine._stream_owns_response = False
            # Cleanup callbacks
            if director:
                director.remove_stream_callback(on_token)
            if on_complete in engine._on_response_callbacks:
                engine._on_response_callbacks.remove(on_complete)

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/thoughts")
async def thought_stream():
    """
    Stream Luna's internal thought process via SSE.

    Returns Server-Sent Events showing what Luna is doing:
    - event: phase - Current phase (idle, planning, observing, thinking, acting)
    - event: thought - Internal thought/progress message
    - event: step - Plan step being executed
    - event: status - Status change (processing, complete, aborted)

    Connect to this endpoint to see Luna's agentic process in real-time.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    async def generate_thoughts() -> AsyncGenerator[str, None]:
        """Generate SSE events for thought stream."""
        thought_queue: asyncio.Queue[dict] = asyncio.Queue()
        client_connected = True

        async def on_progress(message: str) -> None:
            """Callback for progress updates from AgentLoop."""
            if client_connected:
                await thought_queue.put({
                    "type": "thought",
                    "message": message,
                    "is_processing": _engine._is_processing,
                    "goal": _engine._current_goal,
                })

        # Register progress callback
        _engine._on_progress_callbacks.append(on_progress)

        try:
            # Send initial status
            yield f"event: status\ndata: {json.dumps({'connected': True, 'is_processing': _engine._is_processing, 'goal': _engine._current_goal})}\n\n"

            # Keep connection alive and stream thoughts
            while client_connected:
                try:
                    # Wait for thought with timeout (for keepalive)
                    thought = await asyncio.wait_for(
                        thought_queue.get(),
                        timeout=15.0  # Send keepalive every 15s
                    )

                    # Send thought event
                    yield f"event: {thought['type']}\ndata: {json.dumps(thought)}\n\n"

                except asyncio.TimeoutError:
                    # Send keepalive ping
                    yield f"event: ping\ndata: {json.dumps({'is_processing': _engine._is_processing, 'pending': len(_engine._pending_messages)})}\n\n"

        except asyncio.CancelledError:
            client_connected = False
        finally:
            # Cleanup callback
            if on_progress in _engine._on_progress_callbacks:
                _engine._on_progress_callbacks.remove(on_progress)

    return StreamingResponse(
        generate_thoughts(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/abort")
async def abort_generation():
    """
    Abort the current generation.

    Works with streaming mode - will stop generation at the next token boundary.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    director = _engine.get_actor("director")
    if not director:
        raise HTTPException(status_code=503, detail="Director not available")

    if not director.is_generating:
        return {"status": "no_generation", "message": "No generation in progress"}

    await director.mailbox.put(Message(type="abort"))
    return {"status": "aborted", "message": "Abort signal sent"}


@app.post("/interrupt")
async def interrupt_processing():
    """
    Interrupt Luna's current processing.

    This triggers the agentic interrupt handler which will:
    - Abort any running AgentLoop
    - Cancel the current task
    - Process any pending messages

    Use this when you want Luna to stop what she's doing and respond to you.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    if not _engine._is_processing:
        return {
            "status": "no_task",
            "message": "No task in progress",
            "pending_messages": len(_engine._pending_messages),
        }

    current_goal = _engine._current_goal
    await _engine.send_interrupt()

    return {
        "status": "interrupted",
        "message": "Interrupt signal sent",
        "interrupted_goal": current_goal,
        "pending_messages": len(_engine._pending_messages),
    }


# =============================================================================
# MEMORY & EXTRACTION ENDPOINTS
# =============================================================================


class NodeCreateRequest(BaseModel):
    """Request body for creating a memory node."""
    node_type: str = Field(..., description="Node type: FACT, DECISION, PROBLEM, etc.")
    content: str = Field(..., min_length=1)
    source: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


from datetime import datetime as dt_type
from typing import Union, Any
from pydantic import field_validator

class NodeResponse(BaseModel):
    """Response with memory node details."""
    id: str
    node_type: str
    content: str
    source: Optional[str] = None
    confidence: float
    importance: float
    access_count: int
    reinforcement_count: int
    lock_in: float
    lock_in_state: str
    created_at: str

    @field_validator('created_at', mode='before')
    @classmethod
    def coerce_datetime(cls, v: Any) -> str:
        """Convert datetime to ISO string if needed."""
        if v is None:
            return ""
        if isinstance(v, dt_type):
            return v.isoformat()
        return str(v)


class ExtractionRequest(BaseModel):
    """Request body for triggering extraction."""
    content: str = Field(..., min_length=1)
    role: str = Field(default="user")
    session_id: Optional[str] = None
    immediate: bool = Field(default=True, description="Process immediately without batching")


class ExtractionResponse(BaseModel):
    """Response from extraction."""
    objects_extracted: int
    edges_extracted: int
    nodes_created: list[str]


class PruneRequest(BaseModel):
    """Request body for pruning."""
    age_days: int = Field(default=30, ge=1)
    confidence_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    prune_nodes: bool = Field(default=True)
    max_prune_nodes: int = Field(default=100, ge=1)


class PruneResponse(BaseModel):
    """Response from pruning."""
    edges_pruned: int
    nodes_pruned: int


class MemoryStatsResponse(BaseModel):
    """Response with memory statistics."""
    total_nodes: int
    nodes_by_type: dict
    nodes_by_lock_in: dict
    avg_lock_in: float
    total_edges: int
    drifting_nodes: int
    fluid_nodes: int
    settled_nodes: int


@app.post("/memory/nodes", response_model=NodeResponse)
async def create_node(request: NodeCreateRequest):
    """
    Create a new memory node.

    This bypasses extraction and directly creates a node in the memory matrix.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    try:
        node_id = await matrix.add_node(
            node_type=request.node_type,
            content=request.content,
            source=request.source or "api",
            confidence=request.confidence,
            importance=request.importance,
        )

        # Fetch the created node
        node = await matrix.get_node(node_id)
        if not node:
            raise HTTPException(status_code=500, detail="Node created but not found")

        return NodeResponse(
            id=node.id,
            node_type=node.node_type,
            content=node.content,
            source=node.source,
            confidence=node.confidence,
            importance=node.importance,
            access_count=node.access_count,
            reinforcement_count=node.reinforcement_count,
            lock_in=node.lock_in,
            lock_in_state=node.lock_in_state,
            created_at=node.created_at.isoformat() if hasattr(node.created_at, 'isoformat') else str(node.created_at),
        )
    except Exception as e:
        logger.error(f"Failed to create node: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memory/nodes/{node_id}", response_model=NodeResponse)
async def get_node(node_id: str):
    """Get a memory node by ID."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    node = await matrix.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    return NodeResponse(
        id=node.id,
        node_type=node.node_type,
        content=node.content,
        source=node.source,
        confidence=node.confidence,
        importance=node.importance,
        access_count=node.access_count,
        reinforcement_count=node.reinforcement_count,
        lock_in=node.lock_in,
        lock_in_state=node.lock_in_state,
        created_at=node.created_at.isoformat() if hasattr(node.created_at, 'isoformat') else str(node.created_at),
    )


@app.get("/memory/nodes", response_model=list[NodeResponse])
async def list_nodes(
    node_type: Optional[str] = None,
    lock_in_state: Optional[str] = None,
    limit: int = 50,
):
    """
    List memory nodes with optional filtering.

    - node_type: Filter by type (FACT, DECISION, etc.)
    - lock_in_state: Filter by lock-in state (drifting, fluid, settled)
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    try:
        # Get the underlying MemoryMatrix
        # Get the underlying MemoryMatrix
        memory = getattr(matrix, "matrix", None) or getattr(matrix, "_matrix", None) or getattr(matrix, "_memory", None)
        if not memory:
            raise HTTPException(status_code=503, detail="Memory matrix not initialized")

        if lock_in_state:
            nodes = await memory.get_nodes_by_lock_in_state(lock_in_state, limit=limit)
        else:
            nodes = await memory.get_recent_nodes(limit=limit)

        # Filter by type if specified
        if node_type:
            nodes = [n for n in nodes if n.node_type == node_type]

        return [
            NodeResponse(
                id=n.id,
                node_type=n.node_type,
                content=n.content,
                source=n.source,
                confidence=n.confidence,
                importance=n.importance,
                access_count=n.access_count,
                reinforcement_count=n.reinforcement_count,
                lock_in=n.lock_in,
                lock_in_state=n.lock_in_state,
                created_at=n.created_at.isoformat() if hasattr(n.created_at, 'isoformat') else str(n.created_at),
            )
            for n in nodes
        ]
    except Exception as e:
        logger.error(f"Failed to list nodes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/nodes/{node_id}/access")
async def access_node(node_id: str):
    """
    Record an access to a memory node.

    This increases the node's access count and updates its lock-in coefficient.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    try:
        # Get the underlying MemoryMatrix
        memory = getattr(matrix, "matrix", None) or getattr(matrix, "_matrix", None) or getattr(matrix, "_memory", None)
        if not memory:
            raise HTTPException(status_code=503, detail="Memory matrix not initialized")

        await memory.record_access(node_id)
        node = await memory.get_node(node_id)

        return {
            "status": "accessed",
            "node_id": node_id,
            "new_access_count": node.access_count if node else 0,
            "new_lock_in": node.lock_in if node else 0,
            "new_lock_in_state": node.lock_in_state if node else "unknown",
        }
    except Exception as e:
        logger.error(f"Failed to record access: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/nodes/{node_id}/reinforce")
async def reinforce_node(node_id: str):
    """
    Reinforce a memory node.

    This marks the node as explicitly important, boosting its lock-in coefficient.
    Reinforced nodes are never pruned.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    try:
        # Get the underlying MemoryMatrix
        memory = getattr(matrix, "matrix", None) or getattr(matrix, "_matrix", None) or getattr(matrix, "_memory", None)
        if not memory:
            raise HTTPException(status_code=503, detail="Memory matrix not initialized")

        await memory.reinforce_node(node_id)
        node = await memory.get_node(node_id)

        return {
            "status": "reinforced",
            "node_id": node_id,
            "reinforcement_count": node.reinforcement_count if node else 0,
            "new_lock_in": node.lock_in if node else 0,
            "new_lock_in_state": node.lock_in_state if node else "unknown",
        }
    except Exception as e:
        logger.error(f"Failed to reinforce node: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memory/stats", response_model=MemoryStatsResponse)
async def get_memory_stats():
    """Get memory statistics including lock-in distribution."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    try:
        # Get the underlying MemoryMatrix
        memory = getattr(matrix, "matrix", None) or getattr(matrix, "_matrix", None) or getattr(matrix, "_memory", None)
        if not memory:
            raise HTTPException(status_code=503, detail="Memory matrix not initialized")

        # Get stats from Luna's native substrate
        stats = await memory.get_stats()

        return MemoryStatsResponse(
            total_nodes=stats.get("total_nodes", 0),
            nodes_by_type=stats.get("nodes_by_type", {}),
            nodes_by_lock_in=stats.get("nodes_by_lock_in", {}),
            avg_lock_in=stats.get("avg_lock_in", 0.15),
            total_edges=stats.get("total_edges", 0),
            drifting_nodes=stats.get("nodes_by_lock_in", {}).get("drifting", 0),
            fluid_nodes=stats.get("nodes_by_lock_in", {}).get("fluid", 0),
            settled_nodes=stats.get("nodes_by_lock_in", {}).get("settled", 0),
        )
    except Exception as e:
        logger.error(f"Failed to get memory stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/entities")
async def get_entities():
    """Get all known entities from Luna's memory.

    Used by Eclissi frontend for entity highlighting in chat messages.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    mem = getattr(matrix, "matrix", None) or getattr(matrix, "_matrix", None) or getattr(matrix, "_memory", None)
    if not mem or not mem.db:
        raise HTTPException(status_code=503, detail="Memory not initialized")

    rows = await mem.db.fetchall(
        "SELECT id, entity_type, name, aliases, full_profile FROM entities ORDER BY name"
    )
    entities = [
        {"id": r[0], "type": r[1], "name": r[2], "aliases": r[3], "profile": r[4]}
        for r in rows
    ]
    return {"entities": entities, "count": len(entities)}


# ==============================================================================
# Memory Search & Add (MCP Plugin Endpoints)
# ==============================================================================

class MemorySearchRequest(BaseModel):
    """Request for memory search."""
    query: str
    limit: int = 10
    search_type: str = "hybrid"  # keyword, semantic, hybrid


class MemorySearchResponse(BaseModel):
    """Response from memory search."""
    results: list[dict]
    count: int


class MemoryAddRequest(BaseModel):
    """Request to add a memory node."""
    node_type: str = "FACT"
    content: str
    tags: Optional[list[str]] = None
    confidence: float = 1.0
    metadata: Optional[dict] = None


class MemoryAddResponse(BaseModel):
    """Response from adding a memory node."""
    node_id: str
    success: bool


class AddEdgeRequest(BaseModel):
    """Request to add an edge between memory nodes."""
    from_node: str
    to_node: str
    relationship: str = "RELATES_TO"  # DEPENDS_ON, RELATES_TO, CAUSED_BY, etc.
    strength: float = 1.0


class AddEdgeResponse(BaseModel):
    """Response from adding an edge."""
    success: bool
    message: str


class NodeContextRequest(BaseModel):
    """Request to get context around a node."""
    node_id: str
    depth: int = 2


class NodeContextResponse(BaseModel):
    """Response with node context."""
    node_id: str
    neighbors: list[str]
    edges: list[dict]
    depth: int


class TraceRequest(BaseModel):
    """Request to trace dependencies."""
    node_id: str
    max_depth: int = 5


class TraceResponse(BaseModel):
    """Response with dependency trace."""
    node_id: str
    activations: dict[str, float]
    paths: list[dict]


class SetAppContextRequest(BaseModel):
    """Request to set app context."""
    app: str
    app_state: str


class SetAppContextResponse(BaseModel):
    """Response from setting app context."""
    success: bool
    app: str
    app_state: str


class SmartFetchRequest(BaseModel):
    """Request for smart context fetch."""
    query: str
    budget_preset: str = "balanced"  # minimal, balanced, rich


class SmartFetchResponse(BaseModel):
    """Response from smart fetch."""
    nodes: list[dict]
    budget_used: int
    provenance_summary: dict = Field(default_factory=dict)


@app.post("/memory/search", response_model=MemorySearchResponse)
async def memory_search(
    request: MemorySearchRequest,
    engine=Depends(request_engine),
):
    """
    Search memory matrix.

    This is the primary search endpoint used by the MCP plugin.
    Supports keyword, semantic, and hybrid search modes.
    """
    matrix = engine.get_actor("matrix")
    if not matrix:
        return MemorySearchResponse(results=[], count=0)

    try:
        # Get the underlying MemoryMatrix
        memory = getattr(matrix, "matrix", None) or getattr(matrix, "_matrix", None) or getattr(matrix, "_memory", None)
        if not memory:
            return MemorySearchResponse(results=[], count=0)

        # Use the appropriate search method based on search_type
        search_type = getattr(request, "search_type", "hybrid")

        if search_type == "semantic" and hasattr(memory, "semantic_search"):
            # Semantic search (vector similarity)
            search_results = await memory.semantic_search(
                query=request.query,
                limit=request.limit
            )
            results = [
                {
                    "id": n.id,
                    "node_type": n.node_type,
                    "content": n.content,
                    "confidence": n.confidence,
                    "lock_in": n.lock_in,
                    "lock_in_state": getattr(n, "lock_in_state", None),
                    "score": score,
                }
                for n, score in search_results
            ]
        elif search_type == "keyword" and hasattr(memory, "fts5_search"):
            # FTS5 keyword search
            search_results = await memory.fts5_search(
                query=request.query,
                limit=request.limit
            )
            results = [
                {
                    "id": n.id,
                    "node_type": n.node_type,
                    "content": n.content,
                    "confidence": n.confidence,
                    "lock_in": n.lock_in,
                    "lock_in_state": getattr(n, "lock_in_state", None),
                    "score": score,
                }
                for n, score in search_results
            ]
        elif search_type == "hybrid" and hasattr(memory, "hybrid_search"):
            # Hybrid search (FTS5 + semantic with RRF fusion)
            search_results = await memory.hybrid_search(
                query=request.query,
                limit=request.limit
            )
            results = [
                {
                    "id": n.id,
                    "node_type": n.node_type,
                    "content": n.content,
                    "confidence": n.confidence,
                    "lock_in": n.lock_in,
                    "lock_in_state": getattr(n, "lock_in_state", None),
                    "score": score,
                }
                for n, score in search_results
            ]
        elif hasattr(memory, "search_nodes"):
            # Fallback to basic LIKE search
            nodes = await memory.search_nodes(query=request.query, limit=request.limit)
            results = [
                {
                    "id": n.id,
                    "node_type": n.node_type,
                    "content": n.content,
                    "confidence": n.confidence,
                    "lock_in": n.lock_in,
                    "lock_in_state": getattr(n, "lock_in_state", None),
                }
                for n in nodes
            ]
        else:
            # Fallback to recent nodes filtered by content
            nodes = await memory.get_recent_nodes(limit=request.limit * 2)
            query_lower = request.query.lower()
            results = [
                {
                    "id": n.id,
                    "node_type": n.node_type,
                    "content": n.content,
                    "confidence": n.confidence,
                    "lock_in": n.lock_in,
                }
                for n in nodes
                if query_lower in n.content.lower()
            ][:request.limit]

        # Permission gate: strip DOCUMENT nodes the speaker can't see
        results = await _gate_results(results, source="api/memory/search")

        return MemorySearchResponse(results=results, count=len(results))
    except Exception as e:
        logger.error(f"Memory search error: {e}")
        return MemorySearchResponse(results=[], count=0)


_ih_cfg_cache: dict | None = None


def _load_ih_config() -> dict:
    global _ih_cfg_cache
    if _ih_cfg_cache is not None:
        return _ih_cfg_cache
    try:
        import yaml
        from pathlib import Path
        base = os.environ.get("LUNA_BASE_PATH", ".")
        cfg_path = Path(base) / "config" / "intergalactic_hub.yaml"
        if cfg_path.exists():
            with open(cfg_path) as f:
                _ih_cfg_cache = yaml.safe_load(f) or {}
        else:
            _ih_cfg_cache = {}
    except Exception:
        _ih_cfg_cache = {}
    return _ih_cfg_cache


def _ensure_ih_importable() -> bool:
    """Add project root to sys.path so intergalactic_hub is importable from the Engine."""
    import sys
    from pathlib import Path
    base = os.environ.get("LUNA_BASE_PATH", "")
    if not base:
        # Derive from this file: src/luna/api/server.py → project root is 3 levels up
        base = str(Path(__file__).resolve().parents[3])
    if base not in sys.path:
        sys.path.insert(0, base)
    try:
        import intergalactic_hub  # noqa: F401
        return True
    except ImportError:
        return False


async def _fetch_memory_nodes(memory, query: str, max_tokens: int) -> list[dict]:
    """Fetch from memory matrix + nexus, tagged with provenance."""
    nodes = await memory.get_context(query=query, max_tokens=max_tokens)
    results = [
        {
            "id": n.id,
            "node_type": n.node_type,
            "content": n.content,
            "confidence": n.confidence,
            "lock_in": n.lock_in,
            "lock_in_state": getattr(n, "lock_in_state", None),
            "provenance": "memory_matrix",
        }
        for n in nodes
    ]
    # Nexus collections
    if _engine and hasattr(_engine, 'aibrarian') and _engine.aibrarian:
        aibrarian = _engine.aibrarian
        for key, conn in aibrarian.connections.items():
            cfg = aibrarian.registry.collections.get(key)
            if not cfg or not cfg.enabled:
                continue
            try:
                from luna.substrate.aibrarian_engine import AiBrarianEngine
                fts_query = AiBrarianEngine._sanitize_fts_query(query)
                ext_rows = conn.conn.execute(
                    "SELECT e.node_type, e.content, e.confidence "
                    "FROM extractions_fts "
                    "JOIN extractions e ON extractions_fts.rowid = e.rowid "
                    "WHERE extractions_fts MATCH ? "
                    "ORDER BY e.confidence DESC "
                    "LIMIT 10",
                    (fts_query,),
                ).fetchall()
                for row in ext_rows:
                    results.append({
                        "id": f"nexus:{key}:{row[0]}:{len(results)}",
                        "node_type": row[0],
                        "content": row[1],
                        "confidence": row[2] if len(row) > 2 else 0.85,
                        "lock_in": 0.5,
                        "lock_in_state": "fluid",
                        "source": f"nexus/{key}",
                        "provenance": f"nexus/{key}",
                    })
            except Exception as e:
                logger.warning(f"Smart-fetch Nexus search for {key}: {e}")
    return results


@app.post("/memory/smart-fetch", response_model=SmartFetchResponse)
async def memory_smart_fetch(request: SmartFetchRequest):
    """
    Intelligently fetch relevant context with token budgeting.

    Dual-reads from memory matrix + Intergalactic Hub in parallel.
    Hub is additive — falls back to memory-only if unavailable.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix or not matrix.is_ready:
        return SmartFetchResponse(nodes=[], budget_used=0)

    budget_map = {"minimal": 1800, "balanced": 3800, "rich": 7200}
    max_tokens = budget_map.get(request.budget_preset, 3800)

    # ── Budget split ──────────────────────────────────────────────
    ih_cfg = _load_ih_config()
    li_cfg = ih_cfg.get("luna_integration", {})
    hub_enabled = li_cfg.get("enabled", False)
    hub_timeout_ms = li_cfg.get("hub_timeout_ms", 500)
    splits = li_cfg.get("budget_splits", {}).get(request.budget_preset, {})

    if hub_enabled and splits:
        memory_tokens = int(max_tokens * splits.get("memory_matrix", 0.67))
        hub_tokens = int(max_tokens * splits.get("hub", 0.33))
    else:
        memory_tokens = max_tokens
        hub_tokens = 0

    try:
        memory = getattr(matrix, "matrix", None) or getattr(matrix, "_matrix", None)
        if not memory:
            return SmartFetchResponse(nodes=[], budget_used=0)

        # ── Parallel fetch ────────────────────────────────────────
        if hub_enabled and hub_tokens > 0 and _ensure_ih_importable():
            from intergalactic_hub.integration.luna_bridge import hub_retrieval_for_luna
            memory_result, hub_result = await asyncio.gather(
                _fetch_memory_nodes(memory, request.query, memory_tokens),
                hub_retrieval_for_luna(request.query, hub_tokens, hub_timeout_ms),
                return_exceptions=True,
            )
        else:
            memory_result = await _fetch_memory_nodes(memory, request.query, memory_tokens)
            hub_result = []

        # ── Exception handling ────────────────────────────────────
        if isinstance(hub_result, Exception):
            logger.warning("Hub retrieval failed, memory-only fallback: %s", hub_result)
            hub_result = []
        if isinstance(memory_result, Exception):
            raise memory_result

        # ── Merge + gate ──────────────────────────────────────────
        all_results = list(memory_result) + list(hub_result)
        pre_gate = len(all_results)
        all_results = await _gate_results(all_results, source="api/memory/smart-fetch")

        if not all_results and pre_gate > 0:
            logger.warning(
                "SMART_FETCH_GATE: gate stripped all %d results for query=%r",
                pre_gate, request.query[:80],
            )
        elif not all_results:
            logger.warning(
                "SMART_FETCH_ZERO: 0 nodes for query=%r budget=%d",
                request.query[:80], max_tokens,
            )

        # Sort merged set by lock_in descending
        all_results.sort(key=lambda x: x.get("lock_in", 0), reverse=True)

        total_chars = sum(len(r.get("content", "")) for r in all_results)
        budget_used = total_chars // 4

        # ── Observability ─────────────────────────────────────────
        mem_count = sum(1 for r in all_results if r.get("provenance") == "memory_matrix")
        hub_count = sum(1 for r in all_results if r.get("provenance") == "intergalactic_hub")
        logger.info(
            "[smart_fetch] query=%r preset=%s — memory=%d hub=%d total=%d (%d tokens)",
            request.query[:60], request.budget_preset,
            mem_count, hub_count, len(all_results), budget_used,
        )

        return SmartFetchResponse(
            nodes=all_results,
            budget_used=budget_used,
            provenance_summary={
                "memory_matrix_count": mem_count,
                "hub_count": hub_count,
                "total": len(all_results),
            },
        )

    except Exception as e:
        logger.error(f"Smart fetch error: {e}", exc_info=True)
        return SmartFetchResponse(nodes=[], budget_used=0)


@app.post("/memory/add", response_model=MemoryAddResponse)
async def memory_add(
    request: MemoryAddRequest,
    engine=Depends(request_engine),
):
    """
    Add a memory node.

    This is an alias for /memory/nodes with a simpler interface
    used by the MCP plugin.
    """
    matrix = engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    try:
        node_id = await matrix.add_node(
            node_type=request.node_type,
            content=request.content,
            source="mcp",
            confidence=request.confidence,
            importance=0.5,
        )

        return MemoryAddResponse(node_id=node_id, success=True)
    except Exception as e:
        logger.error(f"Failed to add memory node: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/flush")
async def memory_flush(engine=Depends(request_engine)):
    """
    Flush pending memory operations.

    Triggers the Scribe to process any pending extractions.
    """
    scribe = engine.get_actor("scribe")
    if scribe and hasattr(scribe, "flush_pending"):
        try:
            flushed = await scribe.flush_pending()
            return {"pending": 0, "flushed": flushed}
        except Exception as e:
            logger.error(f"Memory flush error: {e}")
            return {"pending": 0, "flushed": 0, "error": str(e)}

    return {"pending": 0, "flushed": 0, "message": "No pending operations"}


# ==============================================================================
# Graph Edge Operations (for MCP compatibility)
# ==============================================================================

@app.post("/memory/add-edge", response_model=AddEdgeResponse)
async def memory_add_edge(request: AddEdgeRequest):
    """
    Add an edge (relationship) between two memory nodes.

    Relationship types: DEPENDS_ON, RELATES_TO, CAUSED_BY, FOLLOWED_BY, CONTRADICTS, SUPPORTS
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    try:
        # Access the graph from the matrix actor
        graph = getattr(matrix, "_graph", None)
        if not graph:
            raise HTTPException(status_code=503, detail="Memory graph not initialized")

        # Add the edge
        edge = await graph.add_edge(
            from_id=request.from_node,
            to_id=request.to_node,
            relationship=request.relationship,
            strength=request.strength,
        )

        logger.info(f"Added edge: {request.from_node} --{request.relationship}--> {request.to_node}")

        return AddEdgeResponse(
            success=True,
            message=f"Edge created: {request.from_node} --{request.relationship}[{request.strength}]--> {request.to_node}"
        )
    except Exception as e:
        logger.error(f"Failed to add edge: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/node-context", response_model=NodeContextResponse)
async def memory_node_context(request: NodeContextRequest):
    """
    Get context around a specific memory node.

    Returns neighbors within N hops and all connecting edges.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    try:
        graph = getattr(matrix, "_graph", None)
        if not graph:
            raise HTTPException(status_code=503, detail="Memory graph not initialized")

        # Get neighbors within depth
        neighbors = await graph.get_neighbors(request.node_id, depth=request.depth)

        # Get all edges for the node
        edges = await graph.get_edges(request.node_id)
        edge_dicts = [
            {
                "from_id": e.from_id,
                "to_id": e.to_id,
                "relationship": e.relationship,
                "strength": e.strength,
            }
            for e in edges
        ]

        return NodeContextResponse(
            node_id=request.node_id,
            neighbors=neighbors,
            edges=edge_dicts,
            depth=request.depth,
        )
    except Exception as e:
        logger.error(f"Failed to get node context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/trace", response_model=TraceResponse)
async def memory_trace(request: TraceRequest):
    """
    Trace dependency paths to a memory node using spreading activation.

    Returns activation scores for related nodes showing relevance.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    try:
        graph = getattr(matrix, "_graph", None)
        if not graph:
            raise HTTPException(status_code=503, detail="Memory graph not initialized")

        # Run spreading activation from the node
        activations = await graph.spreading_activation(
            start_nodes=[request.node_id],
            decay=0.5,
            max_depth=request.max_depth,
        )

        # Get edges to trace paths
        edges = await graph.get_edges(request.node_id)
        paths = [
            {
                "from_id": e.from_id,
                "to_id": e.to_id,
                "relationship": e.relationship,
                "strength": e.strength,
            }
            for e in edges
        ]

        return TraceResponse(
            node_id=request.node_id,
            activations=activations,
            paths=paths,
        )
    except Exception as e:
        logger.error(f"Failed to trace dependencies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================================================
# Data Room Endpoints
# ==============================================================================

@app.post("/dataroom/search")
async def dataroom_search_endpoint(
    query: str = "",
    category: str = None,
    status: str = None,
    limit: int = 10,
):
    """Search investor data room documents."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    try:
        memory = getattr(matrix, "matrix", None) or getattr(matrix, "_matrix", None) or getattr(matrix, "_memory", None)
        if not memory:
            return {"results": [], "count": 0}

        nodes = await memory.search_nodes(query=query or "document", node_type="DOCUMENT", limit=limit * 3)

        # Permission gate: strip documents the speaker can't see
        nodes = await _gate_results(nodes, source="api/dataroom/search")

        results = []
        for node in nodes:
            import json as _json
            meta = node.metadata if isinstance(node.metadata, dict) else (
                _json.loads(node.metadata) if node.metadata else {}
            )
            if category and meta.get("category") != category:
                continue
            if status and meta.get("status") != status:
                continue
            results.append({
                "id": node.id,
                "name": node.summary or node.content,
                "category": meta.get("category"),
                "subfolder": meta.get("subfolder"),
                "status": meta.get("status"),
                "url": meta.get("gdrive_url"),
                "file_type": meta.get("file_type"),
                "tags": meta.get("tags", []),
            })
            if len(results) >= limit:
                break

        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.error(f"Dataroom search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dataroom/status")
async def dataroom_status_endpoint():
    """Get data room statistics: total documents, by category, by status."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    try:
        memory = getattr(matrix, "matrix", None) or getattr(matrix, "_matrix", None) or getattr(matrix, "_memory", None)
        if not memory:
            return {"total_documents": 0, "by_category": {}, "by_status": {}}

        docs = await memory.get_nodes_by_type("DOCUMENT", limit=1000)
        category_counts = {}
        status_counts = {}

        for doc in docs:
            import json as _json
            meta = doc.metadata if isinstance(doc.metadata, dict) else (
                _json.loads(doc.metadata) if doc.metadata else {}
            )
            cat = meta.get("category", "Unknown")
            category_counts[cat] = category_counts.get(cat, 0) + 1
            st = meta.get("status", "Unknown")
            status_counts[st] = status_counts.get(st, 0) + 1

        return {
            "total_documents": len(docs),
            "by_category": category_counts,
            "by_status": status_counts,
        }
    except Exception as e:
        logger.error(f"Dataroom status failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dataroom/recent")
async def dataroom_recent_endpoint(days: int = 7):
    """Get recently synced data room documents."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix:
        raise HTTPException(status_code=503, detail="Matrix actor not available")

    try:
        memory = getattr(matrix, "matrix", None) or getattr(matrix, "_matrix", None) or getattr(matrix, "_memory", None)
        if not memory:
            return {"documents": [], "count": 0}

        from datetime import datetime, timedelta
        import json as _json

        docs = await memory.get_nodes_by_type("DOCUMENT", limit=1000)

        # Permission gate: strip documents the speaker can't see
        docs = await _gate_results(docs, source="api/dataroom/recent")

        cutoff = datetime.now() - timedelta(days=days)
        recent = []

        for doc in docs:
            meta = doc.metadata if isinstance(doc.metadata, dict) else (
                _json.loads(doc.metadata) if doc.metadata else {}
            )
            last_synced = meta.get("last_synced")
            if not last_synced:
                continue
            try:
                sync_time = datetime.fromisoformat(last_synced)
            except (ValueError, TypeError):
                continue
            if sync_time >= cutoff:
                recent.append({
                    "id": doc.id,
                    "name": doc.summary or doc.content,
                    "category": meta.get("category"),
                    "status": meta.get("status"),
                    "synced_at": last_synced,
                    "url": meta.get("gdrive_url"),
                })

        recent.sort(key=lambda x: x["synced_at"], reverse=True)
        return {"documents": recent, "count": len(recent)}
    except Exception as e:
        logger.error(f"Dataroom recent failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/state/set-app-context", response_model=SetAppContextResponse)
async def set_app_context(request: SetAppContextRequest):
    """
    Set Luna's current app context.

    This helps Luna understand what application the user is working in.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    try:
        # Store in engine's context manager if available
        context_manager = getattr(_engine, "_context_manager", None)
        if context_manager and hasattr(context_manager, "set_app_context"):
            await context_manager.set_app_context(request.app, request.app_state)
        else:
            # Fallback: store as memory node
            matrix = _engine.get_actor("matrix")
            if matrix:
                await matrix.add_node(
                    node_type="CONTEXT",
                    content=f"App context: {request.app} - {request.app_state}",
                    source="api",
                )

        logger.info(f"Set app context: {request.app} / {request.app_state}")

        return SetAppContextResponse(
            success=True,
            app=request.app,
            app_state=request.app_state,
        )
    except Exception as e:
        logger.error(f"Failed to set app context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/extraction/trigger", response_model=ExtractionResponse)
async def trigger_extraction(
    request: ExtractionRequest,
    engine=Depends(request_engine),
):
    """
    Trigger extraction on content.

    Sends content to the Scribe actor for extraction, which then
    files results via the Librarian actor.
    """
    scribe = engine.get_actor("scribe")
    if not scribe:
        raise HTTPException(status_code=503, detail="Scribe actor not available")

    librarian = engine.get_actor("librarian")

    try:
        # Send extraction request to scribe
        msg = Message(
            type="extract_text" if request.immediate else "extract_turn",
            payload={
                "text" if request.immediate else "content": request.content,
                "role": request.role,
                "session_id": request.session_id or "api",
                "source_id": "api",
                "immediate": request.immediate,
            },
        )

        await scribe.handle(msg)

        # Process librarian's mailbox if available
        nodes_created = []
        if librarian:
            while not librarian.mailbox.empty():
                lib_msg = await librarian.mailbox.get()
                await librarian.handle(lib_msg)
                nodes_created.extend(getattr(librarian, '_last_filed_node_ids', []))

        return ExtractionResponse(
            objects_extracted=scribe._objects_extracted,
            edges_extracted=scribe._edges_extracted,
            nodes_created=nodes_created,
        )
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/extraction/prune", response_model=PruneResponse)
async def prune_memory(request: PruneRequest):
    """
    Trigger synaptic pruning.

    Removes low-value edges and optionally prunes drifting nodes
    that haven't been accessed recently.

    Note: Reinforced nodes are NEVER pruned.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    librarian = _engine.get_actor("librarian")
    if not librarian:
        raise HTTPException(status_code=503, detail="Librarian actor not available")

    try:
        msg = Message(
            type="prune",
            payload={
                "age_days": request.age_days,
                "confidence_threshold": request.confidence_threshold,
                "prune_nodes": request.prune_nodes,
                "max_prune_nodes": request.max_prune_nodes,
            },
        )

        result = await librarian.handle(msg)

        return PruneResponse(
            edges_pruned=result.get("edges_pruned", 0) if result else 0,
            nodes_pruned=result.get("nodes_pruned", 0) if result else 0,
        )
    except Exception as e:
        logger.error(f"Pruning failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/extraction/stats")
async def get_extraction_stats():
    """Get extraction statistics from Scribe and Librarian."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    stats = {}

    scribe = _engine.get_actor("scribe")
    if scribe:
        stats["scribe"] = scribe.get_stats()

    librarian = _engine.get_actor("librarian")
    if librarian:
        stats["librarian"] = librarian.get_stats()

    return stats


@app.get("/extraction/history")
async def get_extraction_history(limit: int = 20):
    """
    Get recent extraction history from Scribe.

    Shows the actual extracted objects and edges from recent conversations.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    scribe = _engine.get_actor("scribe")
    if not scribe:
        raise HTTPException(status_code=503, detail="Scribe actor not available")

    history = scribe.get_extraction_history()

    # Return most recent (up to limit)
    return {
        "extractions": history[-limit:] if len(history) > limit else history,
        "total": len(history),
    }


# =============================================================================
# DEBUG ENDPOINTS - Context Visibility
# =============================================================================

class ContextItemResponse(BaseModel):
    """A single item in Luna's context window."""
    id: str
    content: str
    source: str  # IDENTITY, CONVERSATION, MEMORY, etc.
    ring: str  # CORE, INNER, MIDDLE, OUTER
    relevance: float
    tokens: int
    age_turns: int
    ttl_turns: int
    is_expired: bool


class ContextDebugResponse(BaseModel):
    """Debug view of Luna's current context window."""
    current_turn: int
    token_budget: int
    total_tokens: int
    items: list[ContextItemResponse]
    keywords: list[str]  # Keywords Luna is currently aware of
    ring_stats: dict


class ConversationCacheItem(BaseModel):
    """A single item in Luna's conversation cache."""
    role: str  # user or assistant
    content: str
    turn: int
    relevance: float
    age_turns: int


class ConversationCacheResponse(BaseModel):
    """Luna's conversation cache - what she remembers of the conversation."""
    current_turn: int
    max_turns: int  # TTL for conversation items
    items: list[ConversationCacheItem]
    total_tokens: int


@app.get("/debug/conversation-cache", response_model=ConversationCacheResponse)
async def get_conversation_cache():
    """
    Get Luna's conversation cache - the conversation history she's aware of.

    This shows the CONVERSATION items in Luna's RevolvingContext, which is
    what she actually "remembers" of the conversation when generating responses.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    context = _engine.context
    from luna.core.context import ContextSource

    # Collect conversation items from all rings
    items = []
    total_tokens = 0

    for ring in context.rings:
        for item in context.rings[ring]:
            if item.source == ContextSource.CONVERSATION:
                # Parse role from content
                content = item.content
                if content.startswith("User:") or content.startswith("User (desktop):"):
                    role = "user"
                    # Strip the prefix
                    if content.startswith("User (desktop):"):
                        content = content[15:].strip()
                    else:
                        content = content[5:].strip()
                elif content.startswith("Luna:"):
                    role = "assistant"
                    content = content[5:].strip()
                else:
                    role = "unknown"

                items.append(ConversationCacheItem(
                    role=role,
                    content=content,
                    turn=item.created_at_turn,
                    relevance=round(item.relevance, 3),
                    age_turns=item.age_turns,
                ))
                total_tokens += item.tokens

    # Sort by turn (oldest first)
    items.sort(key=lambda x: x.turn)

    return ConversationCacheResponse(
        current_turn=context.current_turn,
        max_turns=20,  # Default TTL for conversation
        items=items,
        total_tokens=total_tokens,
    )


# =============================================================================
# PERSONALITY MONITOR ENDPOINT
# =============================================================================


class PersonalityPatchResponse(BaseModel):
    """A single personality patch."""
    patch_id: str
    topic: str
    subtopic: str
    content: str
    before_state: Optional[str] = None
    after_state: str
    trigger: str
    confidence: float
    lock_in: float
    lock_in_state: str
    reinforcement_count: int
    active: bool
    created_at: str
    last_reinforced: str


class PersonalityStatsResponse(BaseModel):
    """Statistics about personality patches."""
    total_patches: int
    active_patches: int
    average_lock_in: float
    patches_by_topic: dict
    patches_by_lock_in_state: dict


class MaintenanceStatsResponse(BaseModel):
    """Lifecycle maintenance statistics."""
    last_maintenance_run: Optional[str]
    total_decay_operations: int
    total_patches_decayed: int
    total_consolidation_operations: int
    total_patches_consolidated: int
    total_cleanup_operations: int
    total_patches_cleaned: int


class SessionStatsResponse(BaseModel):
    """Session reflection statistics."""
    messages_tracked: int
    last_reflection: Optional[str] = None
    patches_created_this_session: int


class PersonalityDebugResponse(BaseModel):
    """Full personality debug response."""
    stats: PersonalityStatsResponse
    patches: list[PersonalityPatchResponse]
    maintenance: MaintenanceStatsResponse
    session: SessionStatsResponse
    mood_state: str
    bootstrap_status: str


@app.get("/debug/personality", response_model=PersonalityDebugResponse)
async def get_personality_debug():
    """
    Get Luna's personality system state for debugging.

    Shows all personality patches, statistics, maintenance status,
    and session reflection data.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    director = _engine.get_actor("director")
    if not director:
        raise HTTPException(status_code=503, detail="Director actor not available")

    # Get entity context and patch manager
    entity_context = getattr(director, "_entity_context", None)
    patch_manager = getattr(director, "_patch_manager", None)
    lifecycle_manager = getattr(director, "_lifecycle_manager", None)
    reflection_loop = getattr(director, "_reflection_loop", None)

    # Default values
    patches = []
    stats = {
        "total_patches": 0,
        "active_patches": 0,
        "average_lock_in": 0.0,
        "patches_by_topic": {},
    }
    patches_by_lock_in_state = {"drifting": 0, "fluid": 0, "settled": 0}
    maintenance_stats = {
        "last_maintenance_run": None,
        "total_decay_operations": 0,
        "total_patches_decayed": 0,
        "total_consolidation_operations": 0,
        "total_patches_consolidated": 0,
        "total_cleanup_operations": 0,
        "total_patches_cleaned": 0,
    }
    session_stats = {
        "messages_tracked": 0,
        "last_reflection": None,
        "patches_created_this_session": 0,
    }
    mood_state = "neutral"
    bootstrap_status = "unknown"

    # Get patches if patch_manager available
    if patch_manager:
        try:
            stats = await patch_manager.get_stats()
            all_patches = await patch_manager.get_all_active_patches(limit=100)

            for p in all_patches:
                patches.append(PersonalityPatchResponse(
                    patch_id=p.patch_id,
                    topic=p.topic.value if hasattr(p.topic, 'value') else str(p.topic),
                    subtopic=p.subtopic,
                    content=p.content,
                    before_state=p.before_state,
                    after_state=p.after_state,
                    trigger=p.trigger.value if hasattr(p.trigger, 'value') else str(p.trigger),
                    confidence=p.confidence,
                    lock_in=p.lock_in,
                    lock_in_state="settled" if p.lock_in >= 0.7 else "fluid" if p.lock_in >= 0.4 else "drifting",
                    reinforcement_count=p.reinforcement_count,
                    active=p.active,
                    created_at=p.created_at.isoformat() if p.created_at else "",
                    last_reinforced=p.last_reinforced.isoformat() if p.last_reinforced else "",
                ))

                # Count by lock_in state
                if p.lock_in >= 0.7:
                    patches_by_lock_in_state["settled"] += 1
                elif p.lock_in >= 0.4:
                    patches_by_lock_in_state["fluid"] += 1
                else:
                    patches_by_lock_in_state["drifting"] += 1

            # Check bootstrap status
            if stats.get("total_patches", 0) > 0:
                bootstrap_status = "bootstrapped"
            else:
                bootstrap_status = "needs_bootstrap"

        except Exception as e:
            logger.error(f"Failed to get personality patches: {e}")

    # Get lifecycle stats
    if lifecycle_manager:
        try:
            lm_stats = lifecycle_manager.get_maintenance_stats()
            maintenance_stats = {
                "last_maintenance_run": lm_stats.get("last_maintenance_run"),
                "total_decay_operations": lm_stats.get("total_decay_operations", 0),
                "total_patches_decayed": lm_stats.get("total_patches_decayed", 0),
                "total_consolidation_operations": lm_stats.get("total_consolidation_operations", 0),
                "total_patches_consolidated": lm_stats.get("total_patches_consolidated", 0),
                "total_cleanup_operations": lm_stats.get("total_cleanup_operations", 0),
                "total_patches_cleaned": lm_stats.get("total_patches_cleaned", 0),
            }
        except Exception as e:
            logger.debug(f"Could not get lifecycle stats: {e}")

    # Get session stats
    if director:
        try:
            director_session_stats = director.get_session_stats() if hasattr(director, 'get_session_stats') else {}
            session_stats = {
                "messages_tracked": director_session_stats.get("messages_tracked", 0),
                "last_reflection": director_session_stats.get("last_reflection"),
                "patches_created_this_session": director_session_stats.get("patches_created_this_session", 0),
            }
        except Exception as e:
            logger.debug(f"Could not get session stats: {e}")

    # Get mood from consciousness
    if _engine.consciousness:
        mood_state = _engine.consciousness.get_summary().get("mood", "neutral")

    return PersonalityDebugResponse(
        stats=PersonalityStatsResponse(
            total_patches=stats.get("total_patches", 0),
            active_patches=stats.get("active_patches", 0),
            average_lock_in=stats.get("average_lock_in", 0.0),
            patches_by_topic=stats.get("patches_by_topic", {}),
            patches_by_lock_in_state=patches_by_lock_in_state,
        ),
        patches=patches,
        maintenance=MaintenanceStatsResponse(**maintenance_stats),
        session=SessionStatsResponse(**session_stats),
        mood_state=mood_state,
        bootstrap_status=bootstrap_status,
    )


@app.get("/debug/context", response_model=ContextDebugResponse)
async def get_debug_context():
    """
    Get Luna's current context window for debugging.

    Shows everything Luna is currently "aware of" - what goes into her context
    when generating a response. Items are shown with their ring placement,
    relevance scores, and expiration info.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    context = _engine.context

    # Collect all items from all rings
    items = []
    all_content = []

    for ring in context.rings:
        for item in context.rings[ring]:
            items.append(ContextItemResponse(
                id=item.id,
                content=item.content,
                source=item.source.name,
                ring=item.ring.name,
                relevance=round(item.relevance, 3),
                tokens=item.tokens,
                age_turns=item.age_turns,
                ttl_turns=item.ttl_turns,
                is_expired=item.is_expired,
            ))
            all_content.append(item.content.lower())

    # Extract keywords Luna is aware of (simple word frequency)
    from collections import Counter
    import re

    stopwords = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
                 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
                 'would', 'could', 'should', 'may', 'might', 'must', 'shall',
                 'can', 'need', 'to', 'of', 'in', 'for', 'on', 'with', 'at',
                 'by', 'from', 'as', 'into', 'through', 'during', 'before',
                 'after', 'above', 'below', 'between', 'under', 'again',
                 'further', 'then', 'once', 'here', 'there', 'when', 'where',
                 'why', 'how', 'all', 'each', 'few', 'more', 'most', 'other',
                 'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same',
                 'so', 'than', 'too', 'very', 's', 't', 'just', 'don', 'now',
                 'and', 'but', 'if', 'or', 'because', 'until', 'while', 'this',
                 'that', 'these', 'those', 'am', 'i', 'you', 'he', 'she', 'it',
                 'we', 'they', 'what', 'which', 'who', 'whom', 'my', 'your',
                 'his', 'her', 'its', 'our', 'their', 'me', 'him', 'us', 'them',
                 'user', 'luna', 'assistant', 'content', 'memory', 'type'}

    all_text = " ".join(all_content)
    words = re.findall(r'\b[a-z]{3,}\b', all_text)
    word_counts = Counter(w for w in words if w not in stopwords)
    keywords = [word for word, count in word_counts.most_common(30) if count >= 2]

    # Ring stats
    ring_stats = {}
    for ring in context.rings:
        ring_items = context.rings[ring]
        ring_stats[ring.name] = {
            "count": len(ring_items),
            "tokens": sum(item.tokens for item in ring_items),
            "avg_relevance": round(
                sum(item.relevance for item in ring_items) / len(ring_items), 3
            ) if ring_items else 0,
        }

    return ContextDebugResponse(
        current_turn=context.current_turn,
        token_budget=context.token_budget,
        total_tokens=context._total_tokens(),
        items=items,
        keywords=keywords,
        ring_stats=ring_stats,
    )


# =============================================================================
# VOICE ENDPOINTS
# =============================================================================

# Global voice backend instance
_voice_backend = None
_voice_response_task: Optional[asyncio.Task] = None


async def _cancel_voice_response_task() -> None:
    """Cancel the active voice response task, if any."""
    global _voice_response_task
    task = _voice_response_task
    if task is None:
        return
    if task.done():
        _voice_response_task = None
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except asyncio.TimeoutError:
        logger.warning("voice-respond task did not cancel within timeout")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning("voice-respond task cancellation surfaced error: %s", e)
    finally:
        if _voice_response_task is task:
            _voice_response_task = None


def _start_voice_response_task(transcription: str) -> None:
    """Start a tracked voice response task and keep a single active reference."""
    global _voice_response_task
    task = _track_task(_voice_backend.process_and_respond(transcription), name="voice-respond")
    _voice_response_task = task

    def _clear_task_ref(done_task: asyncio.Task) -> None:
        global _voice_response_task
        if _voice_response_task is done_task:
            _voice_response_task = None

    task.add_done_callback(_clear_task_ref)


class VoiceStatusResponse(BaseModel):
    """Response from /voice/status endpoint."""
    running: bool
    recording: bool
    hands_free: bool
    stt_provider: str
    tts_provider: str
    persona_connected: bool
    turn_count: int


class VoiceStartRequest(BaseModel):
    """Request body for /voice/start endpoint."""
    hands_free: bool = Field(default=False, description="Enable hands-free mode")


@app.post("/voice/start")
async def start_voice(request: VoiceStartRequest):
    """
    Start the voice system.

    Initializes voice backend with STT, TTS, and connects to Luna Engine.
    """
    global _voice_backend, _voice_response_task

    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    if _voice_backend and _voice_backend.is_active:
        return {"status": "already_running", "message": "Voice system already active"}

    try:
        # Import voice components
        from voice.backend import VoiceBackend
        from voice.stt.manager import STTProviderType
        from voice.tts.manager import TTSProviderType

        # Create voice backend connected to engine
        _voice_backend = VoiceBackend(
            engine=_engine,
            stt_provider=STTProviderType.MLX_WHISPER,
            tts_provider=TTSProviderType.PIPER,
            hands_free=request.hands_free,
        )
        _voice_response_task = None

        await _voice_backend.start()

        return {
            "status": "started",
            "message": "Voice system started",
            "hands_free": request.hands_free,
        }
    except ImportError as e:
        logger.error(f"Voice components not available: {e}")
        raise HTTPException(status_code=503, detail=f"Voice components not available: {e}")
    except Exception as e:
        logger.error(f"Failed to start voice: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/voice/stop")
async def stop_voice():
    """Stop the voice system."""
    global _voice_backend, _voice_response_task

    if _voice_backend is None or not _voice_backend.is_active:
        return {"status": "not_running", "message": "Voice system not active"}

    try:
        await _cancel_voice_response_task()
        await _voice_backend.stop()
        _voice_backend = None
        _voice_response_task = None

        return {"status": "stopped", "message": "Voice system stopped"}
    except Exception as e:
        logger.error(f"Failed to stop voice: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/voice/status", response_model=VoiceStatusResponse)
async def get_voice_status():
    """Get voice system status."""
    global _voice_backend

    if _voice_backend is None:
        return VoiceStatusResponse(
            running=False,
            recording=False,
            hands_free=False,
            stt_provider="none",
            tts_provider="none",
            persona_connected=False,
            turn_count=0,
        )

    state = _voice_backend.get_state()

    return VoiceStatusResponse(
        running=state["running"],
        recording=state["recording"],
        hands_free=state["hands_free"],
        stt_provider=state["components"]["stt"],
        tts_provider=state["components"]["tts"],
        persona_connected=state["components"]["persona"] == "connected",
        turn_count=state["turn_count"],
    )


@app.post("/voice/listen/start")
async def start_listening():
    """
    Start recording user speech (push-to-talk press).

    Call this when user presses the mic button.
    """
    global _voice_backend

    if _voice_backend is None or not _voice_backend.is_active:
        raise HTTPException(status_code=400, detail="Voice system not active")

    if _voice_backend.hands_free:
        return {"status": "hands_free", "message": "Using hands-free mode - recording is automatic"}

    await _voice_backend.start_listening()

    return {"status": "listening", "message": "Recording started"}


@app.post("/voice/listen/stop")
async def stop_listening():
    """
    Stop recording and process speech (push-to-talk release).

    Call this when user releases the mic button.
    Returns transcription and triggers response generation.
    """
    global _voice_backend

    if _voice_backend is None or not _voice_backend.is_active:
        raise HTTPException(status_code=400, detail="Voice system not active")

    if _voice_backend.hands_free:
        return {"status": "hands_free", "message": "Using hands-free mode - recording stops automatically"}

    # Stop listening and get transcription
    transcription = await _voice_backend.stop_listening()

    if not transcription:
        # Provide more specific feedback
        return {
            "status": "no_speech",
            "message": "No speech detected. Try speaking louder or longer.",
            "transcription": None,
            "hint": "Hold the mic button for at least 1 second while speaking clearly"
        }

    # Trigger response generation (non-blocking). Keep a single active
    # voice-respond task so rapid PTT releases cancel stale turns cleanly.
    await _cancel_voice_response_task()
    _start_voice_response_task(transcription)

    return {
        "status": "processing",
        "message": "Speech captured, generating response",
        "transcription": transcription,
    }


class SpeakRequest(BaseModel):
    """Request body for speak endpoint."""
    text: str


class TTSRequest(BaseModel):
    """Request body for /api/tts endpoint."""
    text: str = Field(..., min_length=1, max_length=5000)
    voice: str = Field(default="en_US-amy-medium", description="Piper voice name")


_tts_manager = None


async def _get_tts_manager():
    global _tts_manager
    if _tts_manager is None:
        from voice.tts.manager import TTSManager, TTSProviderType
        _tts_manager = TTSManager(
            default_provider=TTSProviderType.PIPER,
            default_voice="en_US-amy-medium",
        )
    return _tts_manager


@app.post("/api/tts")
async def text_to_speech(request: TTSRequest):
    """
    Convert text to speech using Piper TTS.

    Returns WAV audio bytes. The frontend plays this via Audio element.
    Uses Luna's existing Piper infrastructure — same voice as desktop.
    """
    try:
        from voice.tts.preprocessing import preprocess_for_speech

        clean_text = preprocess_for_speech(request.text)
        if not clean_text.strip():
            raise HTTPException(status_code=400, detail="No speakable text after preprocessing")

        tts = await _get_tts_manager()
        audio = await tts.synthesize(clean_text)

        if not audio.data:
            raise HTTPException(status_code=500, detail="TTS synthesis produced no audio")

        return Response(
            content=audio.data,
            media_type="audio/wav",
            headers={
                "Content-Disposition": "inline",
                "Cache-Control": "no-cache",
            }
        )
    except ImportError:
        raise HTTPException(status_code=503, detail="Voice module not available")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/voice/speak")
async def speak_text(request: SpeakRequest):
    """
    Speak the given text using TTS.

    Use this to have Luna speak a text response when voice mode is on.
    """
    global _voice_backend

    if _voice_backend is None or not _voice_backend.is_active:
        return {"status": "not_running", "message": "Voice system not active"}

    try:
        # Use the TTS to speak
        await _voice_backend.speak(request.text)
        return {"status": "speaking", "text": request.text}
    except Exception as e:
        logger.error(f"Failed to speak: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/voice/stream")
async def voice_stream():
    """
    Stream voice status updates via SSE.

    Returns Server-Sent Events with:
    - event: status - Voice status updates (idle, listening, thinking, speaking)
    - event: partial_transcription - Live in-progress user transcript
    - event: transcription - User speech transcribed
    - event: response - Luna's response text
    - event: validation - Step-2 validation telemetry (segmenter/interrupt)
    - event: ping - Keepalive
    """
    global _voice_backend

    async def generate_voice_events() -> AsyncGenerator[str, None]:
        """Generate SSE events for voice status."""
        event_queue: asyncio.Queue[dict] = asyncio.Queue()
        connected = True
        last_status = None

        def on_status_change(status: str) -> None:
            """Callback for status changes."""
            if connected:
                event_queue.put_nowait({
                    "type": "status",
                    "status": status,
                    "running": _voice_backend is not None and _voice_backend.is_active,
                })

        def on_speech_end(transcription: str) -> None:
            """Callback when speech is transcribed."""
            if connected:
                event_queue.put_nowait({
                    "type": "transcription",
                    "text": transcription,
                })

        def on_partial_transcript(text: str) -> None:
            """Callback for live partial transcription while recording."""
            if connected:
                event_queue.put_nowait({
                    "type": "partial_transcription",
                    "text": text,
                })

        def on_response(text: str, metadata: Optional[dict] = None) -> None:
            """Callback when Luna responds."""
            if connected:
                event_queue.put_nowait({
                    "type": "response",
                    "text": text,
                    "metadata": metadata or {},
                    "event_id": f"voice-response-{time.time_ns()}",
                })

        def on_audio_level(level: float) -> None:
            """Callback for real-time mic audio level."""
            if connected:
                event_queue.put_nowait({
                    "type": "audio_level",
                    "level": round(level, 4),
                })

        def on_validation_event(event: dict) -> None:
            """Callback for bounded segmenter / interrupt validation events."""
            if connected:
                payload = {
                    "type": "validation",
                    **event,
                }
                event_queue.put_nowait(payload)

        # Register callbacks if voice backend exists
        registered_backend = None

        def _register_callbacks():
            nonlocal registered_backend
            if _voice_backend and _voice_backend is not registered_backend:
                _voice_backend.on_status_change(on_status_change)
                _voice_backend.on_speech_end(on_speech_end)
                _voice_backend.on_partial_transcript(on_partial_transcript)
                _voice_backend.on_response(on_response)
                _voice_backend.on_audio_level(on_audio_level)
                _voice_backend.on_validation_event(on_validation_event)
                registered_backend = _voice_backend

        _register_callbacks()

        try:
            # Send initial status
            initial_status = "idle"
            if _voice_backend:
                if _voice_backend.is_recording:
                    initial_status = "listening"
                elif _voice_backend.is_active:
                    initial_status = "idle"
                else:
                    initial_status = "inactive"
            else:
                initial_status = "inactive"

            yield f"event: status\ndata: {json.dumps({'connected': True, 'status': initial_status, 'running': _voice_backend is not None and _voice_backend.is_active})}\n\n"

            while connected:
                try:
                    event = await asyncio.wait_for(
                        event_queue.get(),
                        timeout=10.0  # Keepalive every 10s
                    )

                    yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"

                except asyncio.TimeoutError:
                    # Re-register callbacks if voice backend was created after SSE connected
                    _register_callbacks()
                    # Send keepalive ping
                    running = _voice_backend is not None and _voice_backend.is_active
                    yield f"event: ping\ndata: {json.dumps({'running': running})}\n\n"

        except asyncio.CancelledError:
            connected = False

        finally:
            connected = False

    return StreamingResponse(
        generate_voice_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================================
# VOICE SYSTEM API: Blend Engine + Corpus observability
# ============================================================================

# Lazy-loaded voice system orchestrator
_voice_orchestrator = None

def _get_voice_orchestrator():
    """Lazy-load the voice system orchestrator."""
    global _voice_orchestrator
    if _voice_orchestrator is not None:
        return _voice_orchestrator
    try:
        from luna.voice.orchestrator import VoiceSystemOrchestrator
        from luna.voice.models import VoiceSystemConfig
        voice_config_path = project_root() / "src" / "luna" / "voice" / "data" / "voice_config.yaml"
        if voice_config_path.exists():
            config = VoiceSystemConfig.from_yaml(str(voice_config_path))
        else:
            config = VoiceSystemConfig()
        _voice_orchestrator = VoiceSystemOrchestrator(config)
        return _voice_orchestrator
    except Exception as e:
        logger.warning(f"Voice system not available: {e}")
        return None


@app.get("/voice/system/status")
async def voice_system_status():
    """Get voice system status — engine modes, alpha history, config state."""
    orch = _get_voice_orchestrator()
    if not orch:
        return {"active": False, "error": "Voice system not loaded"}

    engine_info = None
    if orch.engine:
        engine_info = {
            "mode": orch.config.blend_engine_mode.value,
            "alpha_history": list(orch.engine._alpha_history[-20:]),
            "turn_history": [t.value for t in orch.engine._turn_history[-20:]],
            "line_bank_size": len(orch.engine.bank.lines),
            "bypasses": {
                "confidence_router": orch.config.bypass_confidence_router,
                "fade_controller": orch.config.bypass_fade_controller,
                "segment_planner": orch.config.bypass_segment_planner,
                "line_sampler": orch.config.bypass_line_sampler,
            },
        }

    corpus_info = None
    if orch.corpus:
        corpus_info = {
            "mode": orch.config.voice_corpus_mode.value,
            "corpus_size": len(orch.corpus.bank.lines),
            "anti_pattern_count": len(orch.corpus.bank.anti_patterns),
            "critical_anti_patterns": [a.phrase for a in orch.corpus.bank.critical_anti_patterns()],
        }

    response = {
        "active": True,
        "engine": engine_info,
        "corpus": corpus_info,
        "config": {
            "blend_engine_mode": orch.config.blend_engine_mode.value,
            "voice_corpus_mode": orch.config.voice_corpus_mode.value,
            "alpha_override": orch.config.alpha_override,
            "corpus_tier_override": orch.config.corpus_tier_override,
            "log_alpha": orch.config.log_alpha,
            "log_line_selection": orch.config.log_line_selection,
            "log_injection": orch.config.log_injection,
            "log_shadow_diff": orch.config.log_shadow_diff,
        },
    }

    # Voice v2.0 Phase 1 Step 2 — surface current segmenter knob values so the
    # HUD can populate sliders on mount. Only available while the backend is
    # active; sliders render disabled (or hidden) before /voice/start runs.
    if _voice_backend is not None and _voice_backend.is_active:
        response["segmenter_config"] = _voice_backend.get_segmenter_config()

    return response


class VoiceSystemConfigUpdate(BaseModel):
    """Partial config update for voice system."""
    blend_engine_mode: Optional[str] = None
    voice_corpus_mode: Optional[str] = None
    alpha_override: Optional[float] = None
    corpus_tier_override: Optional[str] = None
    bypass_confidence_router: Optional[bool] = None
    bypass_fade_controller: Optional[bool] = None
    bypass_segment_planner: Optional[bool] = None
    bypass_line_sampler: Optional[bool] = None
    # Voice v2.0 Phase 1 Step 2 — runtime segmenter tuning knobs.
    # Routed to the VoiceBackend singleton (separate from the orchestrator).
    segmenter_max_words: Optional[int] = None
    segmenter_min_words: Optional[int] = None
    segmenter_lookahead_depth: Optional[int] = None


@app.post("/voice/system/config")
async def update_voice_system_config(update: VoiceSystemConfigUpdate):
    """Hot-reload voice system configuration."""
    global _voice_orchestrator
    orch = _get_voice_orchestrator()
    if not orch:
        raise HTTPException(status_code=503, detail="Voice system not loaded")

    segmenter_keys = ("segmenter_max_words", "segmenter_min_words", "segmenter_lookahead_depth")
    update_dict = update.model_dump(exclude_none=True)
    segmenter_update = {k: update_dict.pop(k) for k in list(update_dict.keys()) if k in segmenter_keys}

    try:
        from luna.voice.models import VoiceSystemConfig, EngineMode
        # Build new config from current + updates (segmenter knobs already pulled out)
        current = orch.config.model_dump()
        # Convert string modes to EngineMode
        for key in ("blend_engine_mode", "voice_corpus_mode"):
            if key in update_dict:
                update_dict[key] = EngineMode(update_dict[key])
        current.update(update_dict)
        new_config = VoiceSystemConfig(**current)
        orch.on_config_change(new_config)
        response = {"ok": True, "config": {
            "blend_engine_mode": new_config.blend_engine_mode.value,
            "voice_corpus_mode": new_config.voice_corpus_mode.value,
            "alpha_override": new_config.alpha_override,
        }}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Voice v2.0 Phase 1 Step 2 — segmenter knobs live on the VoiceBackend
    # singleton, not the orchestrator. Apply separately so a bad knob value
    # surfaces a 400 and doesn't silently skip.
    if segmenter_update:
        if _voice_backend is None or not _voice_backend.is_active:
            raise HTTPException(
                status_code=503,
                detail="Voice backend not active; segmenter knobs require /voice/start first",
            )
        try:
            applied = _voice_backend.update_segmenter_config(
                max_words=segmenter_update.get("segmenter_max_words"),
                min_words=segmenter_update.get("segmenter_min_words"),
                lookahead_depth=segmenter_update.get("segmenter_lookahead_depth"),
            )
            response["segmenter_config"] = applied
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    return response


@app.post("/voice/system/simulate")
async def voice_system_simulate(signals: dict):
    """Simulate alpha computation without affecting state. For the dashboard."""
    orch = _get_voice_orchestrator()
    if not orch or not orch.engine:
        raise HTTPException(status_code=503, detail="Blend engine not active")

    try:
        from luna.voice.models import ConfidenceSignals, ContextType
        cs = ConfidenceSignals(
            memory_retrieval_score=signals.get("memory_retrieval_score", 0.2),
            turn_number=signals.get("turn_number", 1),
            entity_resolution_depth=signals.get("entity_resolution_depth", 0),
            context_type=ContextType(signals.get("context_type", "cold_start")),
            topic_continuity=signals.get("topic_continuity", 0.0),
        )
        result = orch.engine._compute_confidence(cs)
        return {
            "alpha": result.alpha,
            "tier": result.tier.value,
            "signal_contributions": result.signal_contributions,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/voice/system/reset")
async def voice_system_reset():
    """Reset conversation state (alpha history, turn history)."""
    orch = _get_voice_orchestrator()
    if not orch:
        raise HTTPException(status_code=503, detail="Voice system not loaded")
    orch.on_conversation_start()
    return {"ok": True, "message": "Voice system conversation state reset"}


# ============================================================================
# HUB API: CONVERSATION HISTORY ENDPOINTS
# ============================================================================

class HubSessionCreateRequest(BaseModel):
    """Request to create a new session."""
    app_context: str = "terminal"


class HubSessionResponse(BaseModel):
    """Session details."""
    session_id: str
    started_at: float
    ended_at: Optional[float] = None
    app_context: str


class HubTurnAddRequest(BaseModel):
    """Request to add a turn."""
    session_id: Optional[str] = None
    role: str
    content: str
    tokens: int


class HubTurnResponse(BaseModel):
    """Response after adding a turn."""
    turn_id: int
    tier: str


class HubActiveWindowResponse(BaseModel):
    """Active window turns."""
    turns: list
    total_tokens: int
    turn_count: int


class HubTokenCountResponse(BaseModel):
    """Token count for a tier."""
    total_tokens: int
    turn_count: int


class HubTierRotateRequest(BaseModel):
    """Request to rotate a turn to a new tier."""
    turn_id: int
    new_tier: str


class HubHistorySearchRequest(BaseModel):
    """Request to search history."""
    query: str
    tier: str = "recent"
    session_id: Optional[str] = None
    limit: int = 3
    search_type: str = "hybrid"


class HubHistorySearchResponse(BaseModel):
    """Search results."""
    results: list
    total: int


# --- Session Endpoints ---

@app.post("/hub/session/create", response_model=HubSessionResponse)
async def hub_create_session(request: HubSessionCreateRequest):
    """Create a new conversation session."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    history = _engine.get_actor("history_manager")
    if not history:
        raise HTTPException(status_code=503, detail="History manager not available")

    import time
    session_id = await history.create_session(app_context=request.app_context)
    return HubSessionResponse(
        session_id=session_id,
        started_at=time.time(),
        app_context=request.app_context
    )


@app.post("/hub/session/end")
async def hub_end_session(session_id: str):
    """End a conversation session."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    history = _engine.get_actor("history_manager")
    if not history:
        raise HTTPException(status_code=503, detail="History manager not available")

    await history.end_session(session_id)
    return {"success": True, "session_id": session_id}


@app.get("/hub/session/active", response_model=Optional[HubSessionResponse])
async def hub_get_active_session():
    """Get the currently active session."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    history = _engine.get_actor("history_manager")
    if not history:
        return None

    session = await history.get_active_session()
    if not session:
        return None

    return HubSessionResponse(**session)


# --- Turn Endpoints ---

@app.post("/hub/turn/add", response_model=HubTurnResponse)
async def hub_add_turn(
    request: HubTurnAddRequest,
    engine=Depends(request_engine),
):
    """Add a turn to conversation history."""
    history = engine.get_actor("history_manager")
    if not history:
        raise HTTPException(status_code=503, detail="History manager not available")

    turn_id = await history.add_turn(
        role=request.role,
        content=request.content,
        tokens=request.tokens,
        session_id=request.session_id
    )

    # Trigger extraction pipeline (Scribe → Librarian → Matrix)
    try:
        await engine._trigger_extraction(request.role, request.content)
    except Exception as e:
        logger.error(f"Hub turn extraction error: {e}")

    return HubTurnResponse(turn_id=turn_id, tier="active")


@app.get("/hub/active_window", response_model=HubActiveWindowResponse)
async def hub_get_active_window(session_id: Optional[str] = None, limit: int = 10):
    """Get the Active Window turns."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    history = _engine.get_actor("history_manager")
    if not history:
        return HubActiveWindowResponse(turns=[], total_tokens=0, turn_count=0)

    turns = await history.get_active_window(session_id=session_id, limit=limit)
    token_count = await history.get_active_token_count(session_id=session_id)

    return HubActiveWindowResponse(
        turns=turns,
        total_tokens=token_count["total_tokens"],
        turn_count=token_count["turn_count"]
    )


@app.get("/hub/active_token_count", response_model=HubTokenCountResponse)
async def hub_get_active_token_count(session_id: Optional[str] = None):
    """Get token count for Active tier."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    history = _engine.get_actor("history_manager")
    if not history:
        return HubTokenCountResponse(total_tokens=0, turn_count=0)

    counts = await history.get_active_token_count(session_id=session_id)
    return HubTokenCountResponse(**counts)


# --- Tier Endpoints ---

@app.post("/hub/tier/rotate")
async def hub_rotate_tier(request: HubTierRotateRequest):
    """Rotate a turn to a new tier."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    history = _engine.get_actor("history_manager")
    if not history:
        raise HTTPException(status_code=503, detail="History manager not available")

    await history._rotate_turn_tier(request.turn_id, request.new_tier)
    return {"success": True}


@app.get("/hub/tier/oldest_active")
async def hub_get_oldest_active(session_id: Optional[str] = None):
    """Get the oldest turn in Active tier."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    history = _engine.get_actor("history_manager")
    if not history:
        return None

    turn = await history.get_oldest_active_turn(session_id=session_id)
    return turn


# --- Search Endpoints ---

@app.post("/hub/search", response_model=HubHistorySearchResponse)
async def hub_search_history(request: HubHistorySearchRequest):
    """Search conversation history."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    history = _engine.get_actor("history_manager")
    if not history:
        return HubHistorySearchResponse(results=[], total=0)

    results = await history.search_recent(
        query=request.query,
        limit=request.limit,
        search_type=request.search_type,
        session_id=request.session_id
    )

    return HubHistorySearchResponse(results=results, total=len(results))


# --- History Manager Stats ---

@app.get("/hub/stats")
async def hub_get_history_stats():
    """Get history manager statistics."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    history = _engine.get_actor("history_manager")
    if not history:
        return {"error": "History manager not available"}

    return history.get_stats()


# =============================================================================
# TUNING ENDPOINTS
# =============================================================================

# Global tuning instances
_param_registry = None
_evaluator = None
_session_manager = None


class TuningParamResponse(BaseModel):
    """Response with parameter details."""
    name: str
    value: float
    default: float
    bounds: tuple
    step: float
    category: str
    description: str
    is_overridden: bool


class TuningParamSetRequest(BaseModel):
    """Request to set a parameter value."""
    value: float


class TuningSessionNewRequest(BaseModel):
    """Request to start a new tuning session."""
    focus: str = Field(default="all", description="Area of focus: memory, routing, latency, context, all")
    notes: str = Field(default="")


class TuningSessionResponse(BaseModel):
    """Response with session details."""
    session_id: str
    focus: str
    started_at: str
    best_iteration: int
    best_score: float
    iteration_count: int


class TuningEvalResponse(BaseModel):
    """Response with evaluation results."""
    overall_score: float
    memory_recall_score: float
    context_retention_score: float
    routing_score: float
    avg_latency_ms: float
    p95_latency_ms: float
    total_tests: int
    passed_tests: int
    failed_tests: int


class TuningCompareResponse(BaseModel):
    """Response with iteration comparison."""
    iteration_1: int
    iteration_2: int
    score_1: float
    score_2: float
    score_diff: float
    param_diffs: dict
    metric_diffs: dict


async def _ensure_tuning_initialized():
    """Initialize tuning components if needed."""
    global _param_registry, _evaluator, _session_manager

    if _param_registry is None:
        from luna.tuning.params import ParamRegistry
        from luna.tuning.evaluator import Evaluator
        from luna.tuning.session import TuningSessionManager

        _param_registry = ParamRegistry(_engine)
        _evaluator = Evaluator(_engine)
        _session_manager = TuningSessionManager()
        await _session_manager.initialize()


@app.get("/tuning/params")
async def list_tuning_params(category: Optional[str] = None):
    """
    List all tunable parameters.

    - category: Filter by category (inference, memory, router, etc.)
    """
    await _ensure_tuning_initialized()

    params = _param_registry.list_params(category)
    categories = _param_registry.list_categories()

    return {
        "params": params,
        "categories": categories,
        "count": len(params),
    }


@app.get("/tuning/params/{name:path}", response_model=TuningParamResponse)
async def get_tuning_param(name: str):
    """Get details for a specific parameter."""
    await _ensure_tuning_initialized()

    try:
        spec = _param_registry.get_spec(name)
        value = _param_registry.get(name)
        is_overridden = name in _param_registry._overrides

        return TuningParamResponse(
            name=name,
            value=value,
            default=spec.default,
            bounds=spec.bounds,
            step=spec.step,
            category=spec.category,
            description=spec.description,
            is_overridden=is_overridden,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Parameter not found: {name}")


@app.post("/tuning/params/{name:path}")
async def set_tuning_param(name: str, request: TuningParamSetRequest):
    """
    Set a parameter value.

    Returns the previous value and runs evaluation if session active.
    """
    await _ensure_tuning_initialized()

    try:
        prev_value = _param_registry.set(name, request.value)

        result = {
            "name": name,
            "previous_value": prev_value,
            "new_value": request.value,
        }

        # Run evaluation if session active
        if _session_manager.current_session:
            eval_results = await _evaluator.run_all()
            await _session_manager.add_iteration(
                params_changed={name: request.value},
                param_snapshot=_param_registry.get_all(),
                eval_results=eval_results,
                notes=f"Set {name}={request.value}",
            )
            result["eval_score"] = eval_results.overall_score
            result["iteration"] = len(_session_manager.current_session.iterations)

        return result
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Parameter not found: {name}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/tuning/param-reset/{name:path}")
async def reset_tuning_param(name: str):
    """Reset a parameter to its default value."""
    await _ensure_tuning_initialized()

    try:
        spec = _param_registry.get_spec(name)
        prev_value = _param_registry.reset(name)

        return {
            "name": name,
            "previous_value": prev_value,
            "new_value": spec.default,
            "was_overridden": prev_value is not None,
        }
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Parameter not found: {name}")


@app.post("/tuning/session/new", response_model=TuningSessionResponse)
async def start_tuning_session(request: TuningSessionNewRequest):
    """
    Start a new tuning session.

    Creates a session, records baseline parameters, and runs initial evaluation.
    """
    await _ensure_tuning_initialized()

    # End existing session if any
    if _session_manager.current_session:
        await _session_manager.end_session()

    # Get baseline parameters
    base_params = _param_registry.get_all()

    # Create session
    session = await _session_manager.new_session(
        focus=request.focus,
        base_params=base_params,
        notes=request.notes,
    )

    # Run baseline evaluation
    results = await _evaluator.run_all()
    await _session_manager.add_iteration(
        params_changed={},
        param_snapshot=base_params,
        eval_results=results,
        notes="Baseline",
    )

    return TuningSessionResponse(
        session_id=session.session_id,
        focus=session.focus,
        started_at=session.started_at,
        best_iteration=session.best_iteration,
        best_score=session.best_score,
        iteration_count=len(session.iterations),
    )


@app.get("/tuning/session")
async def get_tuning_session():
    """Get current tuning session."""
    await _ensure_tuning_initialized()

    session = _session_manager.current_session
    if not session:
        return {"active": False}

    return {
        "active": True,
        "session_id": session.session_id,
        "focus": session.focus,
        "started_at": session.started_at,
        "best_iteration": session.best_iteration,
        "best_score": session.best_score,
        "iteration_count": len(session.iterations),
        "iterations": [
            {
                "num": i.iteration_num,
                "score": i.score,
                "params_changed": i.params_changed,
                "notes": i.notes,
                "created_at": i.created_at,
            }
            for i in session.iterations
        ],
    }


@app.post("/tuning/session/end")
async def end_tuning_session():
    """End the current tuning session."""
    await _ensure_tuning_initialized()

    session = await _session_manager.end_session()
    if not session:
        return {"ended": False, "message": "No active session"}

    return {
        "ended": True,
        "session_id": session.session_id,
        "best_iteration": session.best_iteration,
        "best_score": session.best_score,
        "total_iterations": len(session.iterations),
    }


@app.post("/tuning/eval", response_model=TuningEvalResponse)
async def run_tuning_eval(category: Optional[str] = None):
    """
    Run evaluation.

    - category: Optional category to evaluate (memory_recall, context_retention, routing, latency)
    """
    await _ensure_tuning_initialized()

    if category:
        results = await _evaluator.run_category(category)
    else:
        results = await _evaluator.run_all()

    # Record iteration if session active
    if _session_manager.current_session:
        await _session_manager.add_iteration(
            params_changed={},
            param_snapshot=_param_registry.get_all(),
            eval_results=results,
            notes=f"Eval: {category or 'all'}",
        )

    return TuningEvalResponse(
        overall_score=results.overall_score,
        memory_recall_score=results.memory_recall_score,
        context_retention_score=results.context_retention_score,
        routing_score=results.routing_score,
        avg_latency_ms=results.avg_latency_ms,
        p95_latency_ms=results.p95_latency_ms,
        total_tests=results.total_tests,
        passed_tests=results.passed_tests,
        failed_tests=results.failed_tests,
    )


@app.get("/tuning/compare", response_model=TuningCompareResponse)
async def compare_tuning_iterations(iter1: int = 1, iter2: Optional[int] = None):
    """
    Compare two iterations.

    - iter1: First iteration number (default: 1)
    - iter2: Second iteration number (default: latest)
    """
    await _ensure_tuning_initialized()

    session = _session_manager.current_session
    if not session:
        raise HTTPException(status_code=400, detail="No active session")

    if not session.iterations:
        raise HTTPException(status_code=400, detail="No iterations to compare")

    if iter2 is None:
        iter2 = len(session.iterations)

    try:
        comparison = _session_manager.compare_iterations(iter1, iter2)
        return TuningCompareResponse(**comparison)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/tuning/best")
async def get_best_params():
    """Get parameters from the best iteration."""
    await _ensure_tuning_initialized()

    session = _session_manager.current_session
    if not session:
        raise HTTPException(status_code=400, detail="No active session")

    params = _session_manager.get_best_params()
    return {
        "best_iteration": session.best_iteration,
        "best_score": session.best_score,
        "params": params,
    }


@app.post("/tuning/apply-best")
async def apply_best_params():
    """Apply parameters from the best iteration."""
    await _ensure_tuning_initialized()

    session = _session_manager.current_session
    if not session:
        raise HTTPException(status_code=400, detail="No active session")

    best_params = _session_manager.get_best_params()
    count = _param_registry.import_params(best_params)

    return {
        "applied": count,
        "best_iteration": session.best_iteration,
        "best_score": session.best_score,
    }


@app.get("/tuning/sessions")
async def list_tuning_sessions(limit: int = 10):
    """List recent tuning sessions."""
    await _ensure_tuning_initialized()

    sessions = await _session_manager.list_sessions(limit)
    return {
        "sessions": sessions,
        "count": len(sessions),
    }


# =============================================================================
# MEMORY ECONOMY ENDPOINTS — Cluster & Constellation Visibility
# =============================================================================


class ClusterStatsResponse(BaseModel):
    """Memory Economy cluster statistics."""
    cluster_count: int
    total_nodes: int
    state_distribution: dict  # {drifting: n, fluid: n, settled: n, crystallized: n}
    avg_lock_in: float
    nodes_per_cluster_avg: float
    nodes_per_cluster_max: int
    top_clusters: list


class ClusterListItem(BaseModel):
    """Single cluster in list response."""
    cluster_id: str
    name: str
    member_count: int
    lock_in: float
    state: str


class ClusterListResponse(BaseModel):
    """Response from cluster list."""
    clusters: list[ClusterListItem]
    total: int


class ClusterMember(BaseModel):
    """Member node in a cluster."""
    node_id: str
    content: str
    node_type: str
    lock_in: float
    membership_strength: float


class ClusterDetailResponse(BaseModel):
    """Full cluster details."""
    cluster_id: str
    name: str
    member_count: int
    lock_in: float
    state: str
    summary: Optional[str]
    members: list[ClusterMember]
    related_clusters: list


class ConstellationRequest(BaseModel):
    """Request for constellation assembly."""
    query: str
    max_tokens: int = Field(default=3000, ge=500, le=8000)
    max_clusters: int = Field(default=5, ge=1, le=10)


class ConstellationResponse(BaseModel):
    """Assembled constellation for context."""
    activated_clusters: list
    expanded_nodes: list
    total_tokens: int
    lock_in_distribution: dict
    assembly_time_ms: float


@app.get("/clusters/stats", response_model=ClusterStatsResponse)
async def get_cluster_stats():
    """
    Get Memory Economy cluster statistics.

    Shows cluster distribution, lock-in states, and top clusters.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix or not matrix.is_ready:
        raise HTTPException(status_code=503, detail="Matrix not ready")

    try:
        from luna.memory.cluster_manager import ClusterManager

        # Get DB path from matrix
        db_path = str(matrix._matrix.db.db_path) if matrix._matrix and matrix._matrix.db else None
        if not db_path:
            raise HTTPException(status_code=503, detail="Database not available")

        cluster_mgr = ClusterManager(db_path)

        # Get all clusters
        all_clusters = cluster_mgr.list_clusters()

        # Calculate state distribution
        state_dist = {"drifting": 0, "fluid": 0, "settled": 0, "crystallized": 0}
        total_lock_in = 0.0
        sizes = []

        for c in all_clusters:
            state_dist[c.state] = state_dist.get(c.state, 0) + 1
            total_lock_in += c.lock_in
            sizes.append(c.member_count)

        cluster_count = len(all_clusters)
        avg_lock_in = total_lock_in / cluster_count if cluster_count > 0 else 0.0

        # Top clusters by size
        sorted_clusters = sorted(all_clusters, key=lambda c: c.member_count, reverse=True)[:5]
        top_clusters = [
            {
                "cluster_id": c.cluster_id,
                "name": c.name,
                "member_count": c.member_count,
                "lock_in": round(c.lock_in, 3),
                "state": c.state,
            }
            for c in sorted_clusters
        ]

        return ClusterStatsResponse(
            cluster_count=cluster_count,
            total_nodes=sum(sizes),
            state_distribution=state_dist,
            avg_lock_in=round(avg_lock_in, 3),
            nodes_per_cluster_avg=round(sum(sizes) / cluster_count, 1) if cluster_count > 0 else 0,
            nodes_per_cluster_max=max(sizes) if sizes else 0,
            top_clusters=top_clusters,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cluster stats failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/clusters/list", response_model=ClusterListResponse)
async def list_clusters(
    state: Optional[str] = None,
    min_lock_in: Optional[float] = None,
    limit: int = 50,
):
    """
    List clusters with optional filtering.

    Args:
        state: Filter by state (drifting, fluid, settled, crystallized)
        min_lock_in: Minimum lock-in threshold
        limit: Maximum clusters to return
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix or not matrix.is_ready:
        raise HTTPException(status_code=503, detail="Matrix not ready")

    try:
        from luna.memory.cluster_manager import ClusterManager

        db_path = str(matrix._matrix.db.db_path)
        cluster_mgr = ClusterManager(db_path)

        all_clusters = cluster_mgr.list_clusters()

        # Apply filters
        if state:
            all_clusters = [c for c in all_clusters if c.state == state]
        if min_lock_in is not None:
            all_clusters = [c for c in all_clusters if c.lock_in >= min_lock_in]

        # Sort by lock-in descending
        all_clusters.sort(key=lambda c: c.lock_in, reverse=True)

        # Apply limit
        clusters = all_clusters[:limit]

        return ClusterListResponse(
            clusters=[
                ClusterListItem(
                    cluster_id=c.cluster_id,
                    name=c.name,
                    member_count=c.member_count,
                    lock_in=round(c.lock_in, 3),
                    state=c.state,
                )
                for c in clusters
            ],
            total=len(all_clusters),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cluster list failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/clusters/{cluster_id}", response_model=ClusterDetailResponse)
async def get_cluster_detail(cluster_id: str):
    """
    Get detailed information about a specific cluster.

    Includes member nodes and related clusters.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix or not matrix.is_ready:
        raise HTTPException(status_code=503, detail="Matrix not ready")

    try:
        from luna.memory.cluster_manager import ClusterManager

        db_path = str(matrix._matrix.db.db_path)
        cluster_mgr = ClusterManager(db_path)

        # Get cluster
        cluster = cluster_mgr.get_cluster(cluster_id)
        if not cluster:
            raise HTTPException(status_code=404, detail=f"Cluster not found: {cluster_id}")

        # Get members with their node details
        members = cluster_mgr.get_cluster_members(cluster_id)
        member_details = []
        for m in members[:20]:  # Limit to 20 members for response size
            # Get node info from database
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA busy_timeout=15000")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, node_type, content, lock_in FROM memory_nodes
                WHERE id = ?
            """, (m["node_id"],))
            node_row = cursor.fetchone()
            conn.close()

            if node_row:
                member_details.append(ClusterMember(
                    node_id=m["node_id"],
                    content=node_row["content"][:200] if node_row["content"] else "",
                    node_type=node_row["node_type"] or "memory",
                    lock_in=round(node_row["lock_in"] or 0.5, 3),
                    membership_strength=round(m["membership_strength"], 3),
                ))

        # Get related clusters via edges
        related = cluster_mgr.get_connected_clusters(cluster_id, min_lock_in=0.3)
        related_clusters = []
        for neighbor_id, edge_lock_in in related[:5]:
            neighbor = cluster_mgr.get_cluster(neighbor_id)
            if neighbor:
                related_clusters.append({
                    "cluster_id": neighbor.cluster_id,
                    "name": neighbor.name,
                    "lock_in": round(neighbor.lock_in, 3),
                    "edge_strength": round(edge_lock_in, 3),
                })

        return ClusterDetailResponse(
            cluster_id=cluster.cluster_id,
            name=cluster.name,
            member_count=cluster.member_count,
            lock_in=round(cluster.lock_in, 3),
            state=cluster.state,
            summary=cluster.summary,
            members=member_details,
            related_clusters=related_clusters,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cluster detail failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/constellation/assemble", response_model=ConstellationResponse)
async def assemble_constellation(request: ConstellationRequest):
    """
    Assemble a constellation for a query.

    This is the primary Memory Economy retrieval endpoint.
    Returns clusters + nodes formatted for context injection.
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    matrix = _engine.get_actor("matrix")
    if not matrix or not matrix.is_ready:
        raise HTTPException(status_code=503, detail="Matrix not ready")

    import time
    start = time.time()

    try:
        from luna.librarian.cluster_retrieval import ClusterRetrieval
        from luna.memory.constellation import ConstellationAssembler

        db_path = str(matrix._matrix.db.db_path)

        # Step 1: Retrieve relevant nodes via matrix search
        nodes = await matrix._matrix.search_nodes(query=request.query, limit=20)
        node_ids = [n.id for n in nodes] if nodes else []

        # Step 2: Find relevant clusters
        retrieval = ClusterRetrieval(db_path)
        cluster_results = retrieval.find_relevant_clusters(
            node_ids=node_ids,
            top_k=request.max_clusters
        )

        # Also include auto-activated clusters
        auto_clusters = retrieval.get_auto_activated_clusters()
        seen_ids = {c.cluster_id for c, _ in cluster_results}
        for cluster in auto_clusters:
            if cluster.cluster_id not in seen_ids:
                cluster_results.append((cluster, cluster.lock_in))

        # Step 3: Assemble constellation
        assembler = ConstellationAssembler(max_tokens=request.max_tokens)

        cluster_dicts = [
            {"cluster": c, "score": score}
            for c, score in cluster_results
        ]
        node_dicts = [
            {"node_id": n.id, "content": n.content, "node_type": n.node_type, "lock_in": getattr(n, 'lock_in', 0.5)}
            for n in (nodes or [])
        ]

        constellation = assembler.assemble(
            clusters=cluster_dicts,
            nodes=node_dicts,
            prioritize_clusters=True
        )

        elapsed_ms = (time.time() - start) * 1000

        # Format response
        return ConstellationResponse(
            activated_clusters=[
                {
                    "cluster_id": getattr(c.get("cluster"), "cluster_id", None) or c.get("cluster_id"),
                    "name": getattr(c.get("cluster"), "name", None) or c.get("name", "Unknown"),
                    "lock_in": getattr(c.get("cluster"), "lock_in", None) or c.get("lock_in", 0),
                    "member_count": getattr(c.get("cluster"), "member_count", None) or c.get("member_count", 0),
                }
                for c in constellation.clusters
            ],
            expanded_nodes=[
                {
                    "node_id": n.get("node_id"),
                    "content": n.get("content", "")[:200],
                    "node_type": n.get("node_type"),
                    "lock_in": n.get("lock_in"),
                }
                for n in constellation.nodes
            ],
            total_tokens=constellation.total_tokens,
            lock_in_distribution=constellation.lock_in_distribution,
            assembly_time_ms=round(elapsed_ms, 1),
        )
    except ImportError as e:
        logger.error(f"Memory Economy modules not available: {e}")
        raise HTTPException(status_code=503, detail="Memory Economy not available")
    except Exception as e:
        logger.error(f"Constellation assembly failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# SLASH COMMAND DEBUG ENDPOINTS
# =============================================================================
# These endpoints expose luna-debug functionality for the chat UI
# Usage: /health, /find-person X, /stats, /search X, /recent, /extraction


class SlashCommandResponse(BaseModel):
    """Response from slash command endpoints."""
    command: str
    success: bool
    data: dict
    formatted: str  # Pre-formatted text for display


@app.get("/slash/health", response_model=SlashCommandResponse)
async def slash_health():
    """
    /health - Check all 6 system components.
    """
    try:
        from luna.diagnostics.health import HealthChecker, HealthStatus
        import asyncio

        checker = HealthChecker()  # Uses default db path

        # Run sync method in thread pool
        checks = await asyncio.to_thread(checker.check_all)

        # Format output
        lines = ["**System Health Check**", ""]
        for check in checks:
            status_icon = {
                HealthStatus.HEALTHY: "✓",
                HealthStatus.DEGRADED: "⚠",
                HealthStatus.BROKEN: "✗",
                HealthStatus.UNKNOWN: "?",
            }.get(check.status, "?")

            lines.append(f"{status_icon} **{check.component}**: {check.status.value}")
            if check.message:
                lines.append(f"   {check.message}")

        formatted = "\n".join(lines)

        return SlashCommandResponse(
            command="/health",
            success=True,
            data={
                "checks": [
                    {
                        "component": c.component,
                        "status": c.status.value,
                        "message": c.message,
                        "metrics": c.metrics,
                    }
                    for c in checks
                ]
            },
            formatted=formatted,
        )

    except Exception as e:
        logger.error(f"Slash health failed: {e}")
        return SlashCommandResponse(
            command="/health",
            success=False,
            data={"error": str(e)},
            formatted=f"✗ Health check failed: {e}",
        )


@app.get("/slash/find-person/{name}", response_model=SlashCommandResponse)
async def slash_find_person(name: str):
    """
    /find-person <name> - Find a person and their linked memories.
    """
    try:
        from luna.diagnostics.health import HealthChecker
        import asyncio

        checker = HealthChecker()  # Uses default db path

        # Run sync method in thread pool
        result = await asyncio.to_thread(checker.find_person, name)

        if result["found"]:
            lines = [f"**Search results for '{name}':**", ""]

            # Show entities found
            search_results = result.get("search_results", {})
            if "entities" in search_results:
                for entity in search_results["entities"][:3]:
                    lines.append(f"✓ **{entity['name']}** ({entity['type']})")
                    lines.append(f"   ID: `{entity['id']}`")
                    if entity.get("core_facts"):
                        lines.append(f"   Facts: {entity['core_facts'][:100]}...")

            # Show memory nodes found
            if "memory_nodes" in search_results:
                lines.append("")
                lines.append(f"**{len(search_results['memory_nodes'])} memory nodes:**")
                for node in search_results["memory_nodes"][:3]:
                    lines.append(f"- [{node['type']}] {node['content_preview'][:80]}...")

            # Show diagnosis
            if result.get("diagnosis"):
                lines.append("")
                for d in result["diagnosis"]:
                    lines.append(f"• {d}")

            formatted = "\n".join(lines)
        else:
            lines = [f"✗ Person '{name}' not found in memory", ""]
            if result.get("suggestions"):
                lines.append("**Suggestions:**")
                for s in result["suggestions"]:
                    lines.append(f"- {s}")
            formatted = "\n".join(lines)

        return SlashCommandResponse(
            command=f"/find-person {name}",
            success=result["found"],
            data=result,
            formatted=formatted,
        )

    except Exception as e:
        logger.error(f"Slash find-person failed: {e}")
        return SlashCommandResponse(
            command=f"/find-person {name}",
            success=False,
            data={"error": str(e)},
            formatted=f"✗ Find person failed: {e}",
        )


@app.get("/slash/stats", response_model=SlashCommandResponse)
async def slash_stats():
    """
    /stats - Database statistics.
    """
    try:
        from luna.substrate.database import MemoryDatabase

        db_path = memory_matrix_path()
        db = MemoryDatabase(db_path)
        await db.connect()

        try:
            # Gather stats
            nodes = await db.fetchone("SELECT COUNT(*) FROM memory_nodes")
            entities = await db.fetchone("SELECT COUNT(*) FROM entities")
            mentions = await db.fetchone("SELECT COUNT(*) FROM entity_mentions")
            sessions = await db.fetchone("SELECT COUNT(*) FROM sessions")

            # Check if clusters table exists
            clusters_exist = await db.fetchone("SELECT name FROM sqlite_master WHERE type='table' AND name='clusters'")
            clusters = await db.fetchone("SELECT COUNT(*) FROM clusters") if clusters_exist else (0,)

            stats = {
                "memory_nodes": nodes[0] if nodes else 0,
                "entities": entities[0] if entities else 0,
                "entity_mentions": mentions[0] if mentions else 0,
                "sessions": sessions[0] if sessions else 0,
                "clusters": clusters[0] if clusters else 0,
            }

            lines = [
                "**Database Statistics**",
                "",
                f"Memory nodes: **{stats['memory_nodes']:,}**",
                f"Entities: **{stats['entities']:,}**",
                f"Entity mentions: **{stats['entity_mentions']:,}**",
                f"Sessions: **{stats['sessions']:,}**",
                f"Clusters: **{stats['clusters']:,}**",
            ]

            return SlashCommandResponse(
                command="/stats",
                success=True,
                data=stats,
                formatted="\n".join(lines),
            )
        finally:
            await db.close()

    except Exception as e:
        logger.error(f"Slash stats failed: {e}")
        return SlashCommandResponse(
            command="/stats",
            success=False,
            data={"error": str(e)},
            formatted=f"✗ Stats failed: {e}",
        )


@app.get("/slash/search/{query}", response_model=SlashCommandResponse)
async def slash_search(query: str, limit: int = 5):
    """
    /search <query> - Search memory nodes.
    """
    if _engine is None:
        return SlashCommandResponse(
            command=f"/search {query}",
            success=False,
            data={"error": "Engine not ready"},
            formatted="✗ Engine not ready",
        )

    try:
        matrix = _engine.get_actor("matrix")
        if not matrix or not matrix.is_ready:
            return SlashCommandResponse(
                command=f"/search {query}",
                success=False,
                data={"error": "Matrix not ready"},
                formatted="✗ Memory matrix not ready",
            )

        nodes = await matrix._matrix.search_nodes(query=query, limit=limit)

        # Permission gate: strip DOCUMENT nodes
        nodes = await _gate_results(nodes, source="api/slash/search")

        if not nodes:
            return SlashCommandResponse(
                command=f"/search {query}",
                success=True,
                data={"results": []},
                formatted=f"No results for '{query}'",
            )

        lines = [f"**Search results for '{query}':**", ""]
        results = []
        for node in nodes:
            content_preview = (node.content[:100] + "...") if len(node.content) > 100 else node.content
            lines.append(f"- [{node.node_type}] {content_preview}")
            results.append({
                "id": node.id,
                "type": node.node_type,
                "content": node.content[:200],
                "lock_in": getattr(node, 'lock_in', 0.5),
            })

        return SlashCommandResponse(
            command=f"/search {query}",
            success=True,
            data={"results": results, "count": len(results)},
            formatted="\n".join(lines),
        )

    except Exception as e:
        logger.error(f"Slash search failed: {e}")
        return SlashCommandResponse(
            command=f"/search {query}",
            success=False,
            data={"error": str(e)},
            formatted=f"✗ Search failed: {e}",
        )


@app.get("/slash/recent", response_model=SlashCommandResponse)
async def slash_recent(hours: int = 24):
    """
    /recent - Recent activity summary.
    """
    try:
        from luna.diagnostics.health import HealthChecker
        import asyncio

        checker = HealthChecker()  # Uses default db path

        # Run sync method in thread pool
        activity = await asyncio.to_thread(checker.get_recent_activity, hours)

        lines = [
            f"**Activity (last {hours}h)**",
            "",
            f"Memory nodes created: **{activity['nodes_created']}**",
            f"Sessions active: **{activity['sessions_active']}**",
            f"Entities mentioned: **{activity['entities_mentioned']}**",
        ]

        if activity.get("top_node_types"):
            lines.append("")
            lines.append("**Top node types:**")
            for node_type, count in activity["top_node_types"][:5]:
                lines.append(f"- {node_type}: {count}")

        return SlashCommandResponse(
            command="/recent",
            success=True,
            data=activity,
            formatted="\n".join(lines),
        )

    except Exception as e:
        logger.error(f"Slash recent failed: {e}")
        return SlashCommandResponse(
            command="/recent",
            success=False,
            data={"error": str(e)},
            formatted=f"✗ Recent activity failed: {e}",
        )


@app.get("/slash/extraction", response_model=SlashCommandResponse)
async def slash_extraction():
    """
    /extraction - Extraction pipeline status.
    """
    try:
        from luna.substrate.database import MemoryDatabase
        from datetime import datetime, timedelta

        db_path = memory_matrix_path()
        db = MemoryDatabase(db_path)
        await db.connect()

        try:
            # Get extraction stats
            now = datetime.now()
            hour_ago = (now - timedelta(hours=1)).isoformat()
            day_ago = (now - timedelta(days=1)).isoformat()

            last_hour = await db.fetchone(
                "SELECT COUNT(*) FROM memory_nodes WHERE created_at > ?",
                (hour_ago,)
            )
            last_day = await db.fetchone(
                "SELECT COUNT(*) FROM memory_nodes WHERE created_at > ?",
                (day_ago,)
            )

            # Get latest node
            latest = await db.fetchone(
                "SELECT created_at, node_type FROM memory_nodes ORDER BY created_at DESC LIMIT 1"
            )

            stats = {
                "nodes_last_hour": last_hour[0] if last_hour else 0,
                "nodes_last_day": last_day[0] if last_day else 0,
                "latest_node_at": latest[0] if latest else None,
                "latest_node_type": latest[1] if latest else None,
            }

            status = "active" if stats["nodes_last_hour"] > 0 else "idle"

            lines = [
                f"**Extraction Pipeline: {status.upper()}**",
                "",
                f"Nodes (last hour): **{stats['nodes_last_hour']}**",
                f"Nodes (last day): **{stats['nodes_last_day']}**",
            ]

            if stats["latest_node_at"]:
                lines.append(f"Latest: {stats['latest_node_type']} at {stats['latest_node_at']}")

            return SlashCommandResponse(
                command="/extraction",
                success=True,
                data=stats,
                formatted="\n".join(lines),
            )
        finally:
            await db.close()

    except Exception as e:
        logger.error(f"Slash extraction failed: {e}")
        return SlashCommandResponse(
            command="/extraction",
            success=False,
            data={"error": str(e)},
            formatted=f"✗ Extraction check failed: {e}",
        )


@app.get("/slash/help", response_model=SlashCommandResponse)
async def slash_help():
    """
    /help - List available slash commands.
    """
    commands = [
        ("/health", "Check all 6 system components"),
        ("/find-person <name>", "Find a person and their linked memories"),
        ("/stats", "Database statistics"),
        ("/search <query>", "Search memory nodes"),
        ("/recent", "Recent activity (last 24h)"),
        ("/extraction", "Extraction pipeline status"),
        ("/voice-tuning", "Open voice tuning panel"),
        ("/orb-settings", "Open orb visual settings"),
        ("/performance", "Show current performance state"),
        ("/emotion <name>", "Set emotion preset (excited, warm, thoughtful...)"),
        ("/reset-performance", "Reset to auto-detect mode"),
        ("/llm", "Show LLM provider status"),
        ("/llm-switch <provider>", "Switch LLM provider (groq, gemini, claude)"),
        ("/restart-backend", "Restart Luna backend server"),
        ("/restart-frontend", "Reload frontend UI"),
        ("/faceid", "FaceID status, set-pin, or reset"),
        ("/help", "Show this help"),
    ]

    lines = ["**Available Commands:**", ""]
    for cmd, desc in commands:
        lines.append(f"`{cmd}` - {desc}")

    return SlashCommandResponse(
        command="/help",
        success=True,
        data={"commands": [{"command": c, "description": d} for c, d in commands]},
        formatted="\n".join(lines),
    )


# =============================================================================
# FACEID SLASH COMMANDS
# =============================================================================

def _get_face_db():
    """Get FaceDatabase instance (lazy import from Tools/FaceID).

    Uses importlib to load database.py directly, bypassing __init__.py
    which imports cv2/torch dependencies not available in the main venv.
    """
    import importlib.util
    from pathlib import Path
    faceid_root = tools_dir() / "FaceID"
    db_module_path = faceid_root / "src" / "database.py"
    spec = importlib.util.spec_from_file_location("faceid_database", str(db_module_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    db_path = faceid_root / "data" / "faces.db"
    return mod.FaceDatabase(db_path)


@app.get("/slash/faceid", response_model=SlashCommandResponse)
async def slash_faceid_status():
    """
    /faceid — Show FaceID status (enrolled entities, embeddings, PIN state).
    """
    try:
        with _get_face_db() as db:
            entities = db.list_entities()
            total = db.count_embeddings()
            has_pin = db.has_pin()

        lines = ["**FaceID Status**", ""]
        lines.append(f"Entities: **{len(entities)}**")
        lines.append(f"Total embeddings: **{total}**")
        lines.append(f"Admin PIN: **{'set' if has_pin else 'not set'}**")

        if entities:
            lines.append("")
            lines.append("| Name | Tier | DR Tier | Faces |")
            lines.append("|------|------|---------|-------|")
            for e in entities:
                lines.append(f"| {e['entity_name']} | {e['luna_tier']} | {e['dataroom_tier']} | {e['face_count']} |")

        lines.append("")
        lines.append("Commands: `/faceid set-pin <4digits>` | `/faceid reset <pin>`")

        return SlashCommandResponse(
            command="/faceid",
            success=True,
            data={"entities": entities, "total_embeddings": total, "has_pin": has_pin},
            formatted="\n".join(lines),
        )
    except Exception as e:
        return SlashCommandResponse(
            command="/faceid",
            success=False,
            data={"error": str(e)},
            formatted=f"**FaceID Error:** {e}",
        )


@app.post("/slash/faceid/{action}")
async def slash_faceid_action(action: str):
    """
    /faceid set-pin <pin> — Set or change the admin PIN.
    /faceid reset <pin>   — Wipe face embeddings (requires PIN).
    """
    parts = action.split(maxsplit=1)
    sub = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    try:
        with _get_face_db() as db:
            # ── SET PIN ──
            if sub == "set-pin":
                pin = arg.strip()
                if len(pin) != 4 or not pin.isdigit():
                    return SlashCommandResponse(
                        command="/faceid set-pin",
                        success=False,
                        data={},
                        formatted="PIN must be exactly 4 digits. Usage: `/faceid set-pin 1234`",
                    )

                if db.has_pin():
                    return SlashCommandResponse(
                        command="/faceid set-pin",
                        success=False,
                        data={},
                        formatted="PIN already set. To change it, use the CLI: `python cli/reset.py --name <your-name> --set-pin`",
                    )

                db.set_pin(pin)
                return SlashCommandResponse(
                    command="/faceid set-pin",
                    success=True,
                    data={},
                    formatted="Admin PIN has been set. Use `/faceid reset <pin>` to reset face data.",
                )

            # ── RESET ──
            if sub == "reset":
                pin = arg.strip()
                if not pin:
                    return SlashCommandResponse(
                        command="/faceid reset",
                        success=False,
                        data={},
                        formatted="Usage: `/faceid reset <4-digit-pin>`",
                    )

                if not db.has_pin():
                    return SlashCommandResponse(
                        command="/faceid reset",
                        success=False,
                        data={},
                        formatted="No PIN set yet. Run `/faceid set-pin <pin>` first.",
                    )

                if not db.verify_pin(pin):
                    db._log("reset_denied", details="Failed PIN attempt via /faceid")
                    return SlashCommandResponse(
                        command="/faceid reset",
                        success=False,
                        data={},
                        formatted="Incorrect PIN. Reset denied.",
                    )

                # Wipe all face embeddings for all entities
                entities = db.list_entities()
                total_deleted = 0
                for e in entities:
                    total_deleted += db.reset_entity(e["entity_id"])

                return SlashCommandResponse(
                    command="/faceid reset",
                    success=True,
                    data={"deleted": total_deleted},
                    formatted=f"FaceID reset complete. Deleted **{total_deleted}** embeddings.\n\nRe-enroll via CLI: `python cli/enroll.py --name <your-name> --captures 10`",
                )

            return SlashCommandResponse(
                command="/faceid",
                success=False,
                data={},
                formatted=f"Unknown subcommand: `{sub}`\n\nUsage: `/faceid` | `/faceid set-pin <pin>` | `/faceid reset <pin>`",
            )

    except Exception as e:
        return SlashCommandResponse(
            command="/faceid",
            success=False,
            data={"error": str(e)},
            formatted=f"**FaceID Error:** {e}",
        )


# =============================================================================
# RESTART COMMANDS
# =============================================================================

@app.post("/slash/restart-backend", response_model=SlashCommandResponse)
async def slash_restart_backend(background_tasks: BackgroundTasks):
    """
    /restart-backend - Restart the Luna backend server.

    This triggers a graceful shutdown and restart of the server process.
    The server will be unavailable for a few seconds during restart.
    """
    import sys
    import os

    def restart_server():
        """Background task to restart the server."""
        import time
        import subprocess

        # Give time for the response to be sent
        time.sleep(1)

        # Get the current script and arguments
        script_path = str(scripts_dir() / "run.py")
        python_exe = sys.executable

        # Log the restart
        logger.info("🔃 Backend restart initiated via /restart-backend command")

        # Start new server process
        env = os.environ.copy()
        subprocess.Popen(
            [python_exe, script_path, "--server"],
            env=env,
            start_new_session=True,
            stdout=open("/tmp/luna_server.log", "a"),
            stderr=subprocess.STDOUT,
        )

        # Exit current process
        logger.info("🛑 Shutting down current server instance...")
        os._exit(0)

    # Schedule restart in background
    background_tasks.add_task(restart_server)

    return SlashCommandResponse(
        command="/restart-backend",
        success=True,
        data={"status": "restarting", "message": "Backend restart initiated"},
        formatted="🔃 **Backend Restart Initiated**\n\nThe server will restart in ~2 seconds.\nRefresh the page after a few seconds to reconnect.",
    )


# =============================================================================
# PERFORMANCE LAYER SLASH COMMANDS
# =============================================================================
# Voice tuning, orb settings, emotion presets
# See: Docs/HANDOFF_PERFORMANCE_LAYER_UNIFIED.md


def _get_performance_orchestrator() -> PerformanceOrchestrator:
    """Get or create performance orchestrator."""
    global _performance_orchestrator, _orb_state_manager
    if _performance_orchestrator is None:
        _performance_orchestrator = PerformanceOrchestrator(_orb_state_manager)
    return _performance_orchestrator


@app.get("/slash/voice-tuning", response_model=SlashCommandResponse)
async def slash_voice_tuning():
    """
    /voice-tuning - Open voice tuning panel.
    """
    orchestrator = _get_performance_orchestrator()
    state = orchestrator.current_state

    return SlashCommandResponse(
        command="/voice-tuning",
        success=True,
        data={
            "current": {
                "length_scale": state.voice.length_scale,
                "noise_scale": state.voice.noise_scale,
                "noise_w": state.voice.noise_w,
                "sentence_silence": state.voice.sentence_silence,
                "pitch_shift": state.voice.pitch_shift,
            },
            "ranges": {
                "length_scale": {"min": 0.5, "max": 2.0, "step": 0.05, "label": "Speed", "inverted": True},
                "noise_scale": {"min": 0.0, "max": 1.0, "step": 0.05, "label": "Expressiveness"},
                "noise_w": {"min": 0.0, "max": 1.0, "step": 0.05, "label": "Rhythm Variation"},
                "sentence_silence": {"min": 0.0, "max": 1.0, "step": 0.05, "label": "Sentence Pause"},
                "pitch_shift": {"min": -12, "max": 12, "step": 0.5, "label": "Pitch (semitones)"},
            },
            "presets": ["neutral", "excited", "thoughtful", "warm", "playful"],
            "ui_type": "voice-tuning-panel",
        },
        formatted="**Voice Tuning**\nUse sliders to adjust Luna's voice characteristics.",
    )


class VoiceTuningUpdate(BaseModel):
    """Request body for voice tuning updates."""
    length_scale: float = 1.0
    noise_scale: float = 0.667
    noise_w: float = 0.8
    sentence_silence: float = 0.2
    pitch_shift: float = 0.0


@app.post("/slash/voice-tuning")
async def update_voice_tuning(request: VoiceTuningUpdate):
    """Update voice settings from UI."""
    orchestrator = _get_performance_orchestrator()

    knobs = VoiceKnobs(
        length_scale=request.length_scale,
        noise_scale=request.noise_scale,
        noise_w=request.noise_w,
        sentence_silence=request.sentence_silence,
        pitch_shift=request.pitch_shift,
    )
    orchestrator.set_voice_override(knobs)

    return {"success": True, "message": "Voice settings updated"}


@app.get("/slash/orb-settings", response_model=SlashCommandResponse)
async def slash_orb_settings():
    """
    /orb-settings - Open orb settings panel.
    """
    orchestrator = _get_performance_orchestrator()
    state = orchestrator.current_state

    return SlashCommandResponse(
        command="/orb-settings",
        success=True,
        data={
            "current": {
                "animation": state.orb.animation,
                "color": state.orb.color or "#a78bfa",
                "brightness": state.orb.brightness,
                "size_scale": state.orb.size_scale,
                "float_amplitude_x": state.orb.float_amplitude_x,
                "float_amplitude_y": state.orb.float_amplitude_y,
                "float_speed_x": state.orb.float_speed_x,
                "float_speed_y": state.orb.float_speed_y,
            },
            "ranges": {
                "brightness": {"min": 0.2, "max": 2.0, "step": 0.1, "label": "Brightness"},
                "size_scale": {"min": 0.5, "max": 2.0, "step": 0.1, "label": "Size"},
                "float_amplitude_x": {"min": 0, "max": 30, "step": 1, "label": "Drift X"},
                "float_amplitude_y": {"min": 0, "max": 30, "step": 1, "label": "Drift Y"},
                "float_speed_x": {"min": 0.0005, "max": 0.005, "step": 0.0005, "label": "Float Speed X"},
                "float_speed_y": {"min": 0.0005, "max": 0.005, "step": 0.0005, "label": "Float Speed Y"},
            },
            "animations": ["idle", "pulse", "pulse_fast", "spin", "spin_fast",
                          "glow", "wobble", "drift", "orbit", "flicker"],
            "color_presets": {
                "violet": "#a78bfa",
                "gold": "#FFD700",
                "coral": "#FFB7B2",
                "cyan": "#06B6D4",
                "teal": "#A8DADC",
                "orange": "#F4A261",
            },
            "ui_type": "orb-settings-panel",
        },
        formatted="**Orb Settings**\nCustomize Luna's visual appearance.",
    )


class OrbSettingsUpdate(BaseModel):
    """Request body for orb settings updates."""
    animation: str = "idle"
    color: Optional[str] = None
    brightness: float = 1.0
    size_scale: float = 1.0
    float_amplitude_x: float = 8.0
    float_amplitude_y: float = 12.0
    float_speed_x: float = 0.0015
    float_speed_y: float = 0.0023


@app.post("/slash/orb-settings")
async def update_orb_settings(request: OrbSettingsUpdate):
    """Update orb settings from UI."""
    orchestrator = _get_performance_orchestrator()

    knobs = OrbKnobs(
        animation=request.animation,
        color=request.color,
        brightness=request.brightness,
        size_scale=request.size_scale,
        float_amplitude_x=request.float_amplitude_x,
        float_amplitude_y=request.float_amplitude_y,
        float_speed_x=request.float_speed_x,
        float_speed_y=request.float_speed_y,
    )
    orchestrator.set_orb_override(knobs)

    return {"success": True, "message": "Orb settings updated"}


class DimensionOverride(BaseModel):
    """Manual dimension overrides from the diagnostic tool."""
    valence: Optional[float] = None
    arousal: Optional[float] = None
    certainty: Optional[float] = None
    engagement: Optional[float] = None
    warmth: Optional[float] = None


@app.post("/api/orb/dimensions/override")
async def override_orb_dimensions(request: DimensionOverride):
    """Override dimensional values from the expression pipeline diagnostic tool."""
    overrides = {k: v for k, v in request.model_dump().items() if v is not None}
    if _orb_state_manager:
        _orb_state_manager.apply_dimension_override(overrides)
    return {"success": True, "overrides": overrides}


@app.delete("/api/orb/dimensions/override")
async def clear_orb_dimension_overrides():
    """Clear all dimension overrides, returning to engine-driven values."""
    if _orb_state_manager:
        _orb_state_manager.apply_dimension_override({})
    return {"success": True, "message": "Overrides cleared"}


@app.get("/slash/performance", response_model=SlashCommandResponse)
async def slash_performance():
    """
    /performance - Show current performance state.
    """
    orchestrator = _get_performance_orchestrator()
    feedback = orchestrator.get_feedback()
    state = feedback["state"]

    formatted = f"""**Performance State**

**Emotion:** {state.get('emotion', 'neutral')}
**Gesture:** {state.get('gesture_source', 'none')}

**Voice:**
  Speed: {state['voice']['length_scale']}x
  Expressiveness: {state['voice']['noise_scale']}
  Rhythm: {state['voice']['noise_w']}

**Orb:**
  Animation: {state['orb']['animation']}
  Brightness: {state['orb']['brightness']}
  Color: {state['orb']['color'] or 'default'}

**Overrides:** {'Voice ' if feedback['has_voice_override'] else ''}{'Orb ' if feedback['has_orb_override'] else ''}{'(none)' if not feedback['has_voice_override'] and not feedback['has_orb_override'] else ''}
"""

    return SlashCommandResponse(
        command="/performance",
        success=True,
        data=feedback,
        formatted=formatted,
    )


@app.get("/slash/emotion/{emotion_name}", response_model=SlashCommandResponse)
async def slash_emotion(emotion_name: str):
    """
    /emotion <name> - Set emotion preset.
    """
    orchestrator = _get_performance_orchestrator()

    if orchestrator.set_emotion(emotion_name):
        return SlashCommandResponse(
            command=f"/emotion {emotion_name}",
            success=True,
            data={"emotion": emotion_name},
            formatted=f"**Emotion set to {emotion_name}**\nVoice and orb adjusted.",
        )

    valid = ", ".join([e.value for e in EmotionPreset])
    return SlashCommandResponse(
        command=f"/emotion {emotion_name}",
        success=False,
        data={"error": "unknown_emotion", "valid": valid},
        formatted=f"**Unknown emotion:** {emotion_name}\nValid: {valid}",
    )


@app.post("/slash/reset-performance")
async def slash_reset_performance():
    """
    /reset-performance - Clear all overrides.
    """
    orchestrator = _get_performance_orchestrator()
    orchestrator.clear_overrides()

    return SlashCommandResponse(
        command="/reset-performance",
        success=True,
        data={},
        formatted="**Performance reset**\nReturned to auto-detect mode.",
    )


# =============================================================================
# LLM PROVIDER ENDPOINTS
# =============================================================================
# Multi-provider support: Groq, Gemini, Claude
# See: HANDOFF_Multi_LLM_Provider_System.md

_llm_registry = None


def _get_llm_registry():
    """Get or create the LLM provider registry."""
    global _llm_registry
    if _llm_registry is None:
        from luna.llm import init_providers
        _llm_registry = init_providers()
    return _llm_registry


@app.get("/llm/providers")
async def get_llm_providers():
    """Get all LLM providers and their status."""
    registry = _get_llm_registry()
    return {
        "success": True,
        "providers": registry.get_all_status(),
    }


@app.get("/llm/current")
async def get_current_provider():
    """Get the currently selected LLM provider."""
    registry = _get_llm_registry()
    from luna.llm import get_config
    config = get_config()

    current = registry.get_current()
    if not current:
        return {"success": False, "error": "No provider available"}

    return {
        "success": True,
        "provider": config.current_provider,
        "model": config.get_provider_config(config.current_provider).default_model,
        "is_available": current.is_available,
    }


class SetProviderRequest(BaseModel):
    """Request to set the current provider."""
    provider: str
    model: Optional[str] = None


@app.post("/llm/provider")
async def set_current_provider(request: SetProviderRequest):
    """Set the current LLM provider."""
    registry = _get_llm_registry()

    if registry.set_current(request.provider):
        return {
            "success": True,
            "message": f"Switched to {request.provider}",
            "provider": request.provider,
        }

    return {
        "success": False,
        "error": f"Failed to switch to {request.provider}. Check if API key is configured.",
    }


# ==============================================================================
# Fallback Chain Endpoints
# ==============================================================================


@app.get("/llm/fallback-chain")
async def get_fallback_chain():
    """Get current fallback chain configuration and status."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    director = _engine.get_actor("director")
    if not director:
        raise HTTPException(status_code=503, detail="Director not available")

    fallback_chain = getattr(director, '_fallback_chain', None)
    if not fallback_chain:
        return {
            "success": False,
            "error": "Fallback chain not initialized",
            "chain": [],
            "providers": {},
        }

    registry = _get_llm_registry()

    # Build provider status
    providers = {}
    for name in fallback_chain.get_chain():
        if name == "local":
            local = getattr(director, '_local', None)
            providers[name] = {
                "available": local is not None and local.is_loaded if local else False,
                "in_chain": True,
                "type": "local",
            }
        else:
            provider = registry.get(name) if registry else None
            providers[name] = {
                "available": provider.is_available if provider else False,
                "in_chain": True,
                "type": "registry",
            }

    return {
        "success": True,
        "chain": fallback_chain.get_chain(),
        "providers": providers,
    }


class SetFallbackChainRequest(BaseModel):
    """Request to set fallback chain order."""
    chain: list[str]


@app.post("/llm/fallback-chain")
async def set_fallback_chain(request: SetFallbackChainRequest):
    """Set fallback chain order."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    director = _engine.get_actor("director")
    if not director:
        raise HTTPException(status_code=503, detail="Director not available")

    fallback_chain = getattr(director, '_fallback_chain', None)
    if not fallback_chain:
        raise HTTPException(status_code=503, detail="Fallback chain not initialized")

    if not request.chain:
        raise HTTPException(status_code=400, detail="Chain cannot be empty")

    # Update chain
    warnings = fallback_chain.set_chain(request.chain)

    # Persist to config file
    try:
        from luna.llm.fallback_config import FallbackConfig
        config = FallbackConfig(chain=request.chain)
        config.save()
    except Exception as e:
        warnings.append(f"Failed to persist config: {e}")

    return {
        "success": True,
        "chain": fallback_chain.get_chain(),
        "warnings": warnings,
    }


@app.get("/llm/fallback-chain/stats")
async def get_fallback_stats():
    """Get fallback chain statistics."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    director = _engine.get_actor("director")
    if not director:
        return {"total_requests": 0, "by_provider": {}, "fallback_events": 0}

    fallback_chain = getattr(director, '_fallback_chain', None)
    if not fallback_chain:
        return {"total_requests": 0, "by_provider": {}, "fallback_events": 0}

    return {
        "success": True,
        **fallback_chain.get_stats(),
    }


@app.get("/slash/llm", response_model=SlashCommandResponse)
async def slash_llm():
    """
    /llm - Show LLM provider status.
    """
    registry = _get_llm_registry()
    from luna.llm import get_config
    config = get_config()

    status = registry.get_all_status()

    lines = ["**LLM Providers**", ""]
    for name, info in status.items():
        icon = "✓" if info["is_available"] else "✗"
        current = " ← current" if info["is_current"] else ""
        model = info.get("default_model", "")
        lines.append(f"{icon} **{name}** ({model}){current}")

    lines.append("")
    lines.append("Use `/llm-switch <provider>` to change providers.")

    return SlashCommandResponse(
        command="/llm",
        success=True,
        data=status,
        formatted="\n".join(lines),
    )


@app.get("/slash/llm-switch/{provider_name}", response_model=SlashCommandResponse)
async def slash_llm_switch(provider_name: str):
    """
    /llm-switch <provider> - Switch LLM provider.
    """
    registry = _get_llm_registry()

    if registry.set_current(provider_name):
        return SlashCommandResponse(
            command=f"/llm-switch {provider_name}",
            success=True,
            data={"provider": provider_name},
            formatted=f"**Switched to {provider_name}**\nLuna will now use {provider_name} for responses.",
        )

    return SlashCommandResponse(
        command=f"/llm-switch {provider_name}",
        success=False,
        data={"error": "Provider not available"},
        formatted=f"**Failed to switch to {provider_name}**\nCheck if API key is configured.",
    )


@app.get("/slash/prompt", response_model=SlashCommandResponse)
async def slash_prompt():
    """
    /prompt - Show the last system prompt sent to the LLM.

    Useful for debugging:
    - What context is reaching the model?
    - Is the LoRA being used or is it a prompt issue?
    - Was this routed to local (Qwen) or delegated (Claude)?
    """
    global _engine

    if not _engine:
        return SlashCommandResponse(
            command="/prompt",
            success=False,
            data={"error": "Engine not initialized"},
            formatted="**Error:** Luna engine not running.",
        )

    director = _engine.get_actor("director")
    if not director:
        return SlashCommandResponse(
            command="/prompt",
            success=False,
            data={"error": "Director not found"},
            formatted="**Error:** Director actor not available.",
        )

    prompt_info = director.get_last_system_prompt()

    if not prompt_info.get("available"):
        return SlashCommandResponse(
            command="/prompt",
            success=False,
            data=prompt_info,
            formatted=f"**No prompt available**\n{prompt_info.get('message', 'Send a message first.')}",
        )

    route = prompt_info.get("route_decision", "unknown")
    length = prompt_info.get("length", 0)
    preview = prompt_info.get("preview", "")
    meta = prompt_info.get("assembler")

    # Format for display
    route_emoji = "🏠" if route == "local" else "☁️"

    # Build assembler metadata summary if available
    meta_block = ""
    if meta:
        check = lambda v: "✓" if v else "–"
        identity = meta.get("identity_source", "unknown")
        memory = meta.get("memory_source") or "none"
        gap = meta.get("gap_category") or "unknown"
        tokens = meta.get("prompt_tokens", 0)
        threads = meta.get("parked_thread_count", 0)
        register = meta.get("register_active") or "–"
        reg_on = check(meta.get("register_injected"))
        meta_block = f"""
**Assembler:**
  Identity: **{identity}** | Memory: **{memory}** | Gap: **{gap}**
  Temporal: {check(meta.get('temporal_injected'))} | Voice: {check(meta.get('voice_injected'))} | Register: {reg_on} ({register}) | Tokens: ~{tokens}
  Threads parked: {threads}
"""

    formatted = f"""**{route_emoji} Last System Prompt** ({route})

**Length:** {length} chars
{meta_block}
**Preview:**
```
{preview}
```

*Use browser console or logs for full prompt.*"""

    return SlashCommandResponse(
        command="/prompt",
        success=True,
        data=prompt_info,
        formatted=formatted,
    )


@app.get("/slash/register", response_model=SlashCommandResponse)
async def slash_register_get():
    """
    /register - Show context register state + sovereignty debug info.

    Displays: active register, confidence, weights, fired signals,
    bridge/sovereignty info, and denied document count.
    """
    global _engine

    if not _engine:
        return SlashCommandResponse(
            command="/register",
            success=False,
            data={"error": "Engine not initialized"},
            formatted="**Error:** Luna engine not running.",
        )

    director = _engine.get_actor("director")
    if not director:
        return SlashCommandResponse(
            command="/register",
            success=False,
            data={"error": "Director not found"},
            formatted="**Error:** Director actor not available.",
        )

    state = director.get_register_state()
    reg = state.get("register", {})
    bridge = state.get("bridge", {})
    intent_info = state.get("intent", {})
    enabled = state.get("enabled", False)

    active = reg.get("active", "ambient")
    confidence = reg.get("confidence", 0.0)
    weights = reg.get("weights", {})
    signals = reg.get("fired_signals", [])
    denied = state.get("denied_docs", 0)

    status_icon = "🟢" if enabled else "🔴"

    # Build weight bars
    weight_lines = []
    for name, weight in sorted(weights.items(), key=lambda x: -x[1]):
        bar_len = int(weight * 10)
        bar = "█" * bar_len + "░" * (10 - bar_len)
        marker = " ◄ active" if name == active else ""
        weight_lines.append(f"  `{name:<22}` {bar} {weight:.3f}{marker}")

    weights_block = "\n".join(weight_lines) if weight_lines else "  No weights (no generation yet)"

    # Bridge info
    entity = bridge.get("entity_id")
    if entity:
        tier = bridge.get("luna_tier", "?")
        dt = bridge.get("dataroom_tier", "?")
        sov = "sovereign" if bridge.get("is_sovereign") else f"tier {dt}"
        bridge_line = f"**{entity}** ({tier}, {sov})"
    else:
        bridge_line = "No entity recognized"

    signals_str = ", ".join(signals) if signals else "none"

    formatted = f"""{status_icon} **Register: {active}** (confidence: {confidence:.2f})

**Weights:**
{weights_block}

**Fired signals:** {signals_str}

**Sovereignty:**
  Bridge: {bridge_line}
  Denied docs: {denied}
  Intent: {intent_info.get('mode', '?')}"""

    return SlashCommandResponse(
        command="/register",
        success=True,
        data=state,
        formatted=formatted,
    )


@app.post("/slash/register", response_model=SlashCommandResponse)
async def slash_register_toggle(enabled: bool):
    """
    Toggle context register on/off.

    POST /slash/register?enabled=true  → enable
    POST /slash/register?enabled=false → disable
    """
    global _engine

    if not _engine:
        return SlashCommandResponse(
            command="/register",
            success=False,
            data={"error": "Engine not initialized"},
            formatted="**Error:** Luna engine not running.",
        )

    director = _engine.get_actor("director")
    if not director:
        return SlashCommandResponse(
            command="/register",
            success=False,
            data={"error": "Director not found"},
            formatted="**Error:** Director actor not available.",
        )

    director.set_register_enabled(enabled)
    status = "enabled" if enabled else "disabled"

    return SlashCommandResponse(
        command="/register",
        success=True,
        data={"enabled": enabled},
        formatted=f"Context register **{status}**.",
    )


@app.get("/slash/vk", response_model=SlashCommandResponse)
@app.get("/slash/voight-kampff", response_model=SlashCommandResponse)
async def slash_voight_kampff(layer: int = None):
    """
    /vk or /voight-kampff - Run Luna identity verification test.

    Tests 4 layers of Luna's identity chain:
    1. LoRA Loading - Is personality adapter active?
    2. Memory Retrieval - Can memories be found?
    3. Context Injection - Are memories reaching prompts?
    4. Output Quality - Does output reflect Luna?

    Args:
        layer: Optional layer number (1-4) to run only that layer
    """
    import asyncio
    from pathlib import Path
    import sys

    # Add scripts/utils path for import
    scripts_path = scripts_dir() / "utils"
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))

    try:
        from voight_kampff import VoightKampff

        vk = VoightKampff(project_root())

        if layer:
            # Single layer
            result = await vk.run_layer(layer)
            await vk.cleanup()

            status = "✅" if result.passed else "❌"
            lines = [
                f"**Layer {result.layer}: {result.name}**",
                f"Status: {status}",
                f"Score: {result.score}/{result.max_score}",
            ]
            if result.error:
                lines.append(f"Error: {result.error}")
            lines.append(f"Duration: {result.duration_ms:.0f}ms")

            return SlashCommandResponse(
                command=f"/vk --layer {layer}",
                success=result.passed,
                data={
                    "layer": result.layer,
                    "name": result.name,
                    "passed": result.passed,
                    "score": result.score,
                    "max_score": result.max_score,
                    "error": result.error,
                },
                formatted="\n".join(lines),
            )
        else:
            # Full test
            report = await vk.run_all()
            await vk.cleanup()

            # Build summary
            lines = [
                "**🧠 VOIGHT-KAMPFF RESULTS**",
                "",
            ]

            for lr in report.layers:
                status = "✅" if lr.passed else "❌"
                lines.append(f"  {status} Layer {lr.layer}: {lr.name} ({lr.score}/{lr.max_score})")

            lines.append("")
            verdict_icon = "✅" if report.overall_passed else "❌"
            lines.append(f"**VERDICT: {verdict_icon} {report.verdict}**")

            if report.first_failure:
                lines.append(f"\nFirst failure: {report.first_failure}")

            if report.recommendations:
                lines.append("\n**Recommendations:**")
                for rec in report.recommendations[:3]:
                    lines.append(f"  • {rec}")

            lines.append(f"\nDuration: {report.total_duration_ms:.0f}ms")
            lines.append("Full results: `Docs/Handoffs/VoightKampffResults/`")

            return SlashCommandResponse(
                command="/vk",
                success=report.overall_passed,
                data={
                    "verdict": report.verdict,
                    "passed": report.overall_passed,
                    "first_failure": report.first_failure,
                    "layers": [
                        {"layer": l.layer, "name": l.name, "passed": l.passed, "score": l.score}
                        for l in report.layers
                    ],
                },
                formatted="\n".join(lines),
            )

    except Exception as e:
        logger.error(f"Voight-Kampff test failed: {e}")
        import traceback
        traceback.print_exc()

        return SlashCommandResponse(
            command="/vk",
            success=False,
            data={"error": str(e)},
            formatted=f"**❌ Voight-Kampff Test Failed**\n\n{e}\n\nTry running manually:\n`.venv/bin/python scripts/utils/voight_kampff.py`",
        )


# ═══════════════════════════════════════════════════════════════════════════
# QA SYSTEM ENDPOINTS
# Luna QA v2 — Live validation system for inference quality
# ═══════════════════════════════════════════════════════════════════════════

class QAHealthResponse(BaseModel):
    """Response from /qa/health endpoint."""
    pass_rate: float
    total_24h: int
    failed_24h: int
    failing_bugs: int
    recent_failures: list[str]
    top_failures: list[dict] = []
    system_events_24h: int = 0


class QAReportResponse(BaseModel):
    """Response from /qa/last endpoint."""
    inference_id: str
    timestamp: str
    query: str
    route: str
    provider_used: str
    latency_ms: float
    passed: bool
    failed_count: int
    diagnosis: Optional[str]
    assertions: list[dict]
    context: dict


class QAStatsResponse(BaseModel):
    """Response from /qa/stats endpoint."""
    total: int
    passed: int
    failed: int
    pass_rate: float
    time_range: str
    top_failures: list[dict] = []


class QAAssertionResponse(BaseModel):
    """Response from /qa/assertions endpoint."""
    id: str
    name: str
    description: str = ""
    category: str
    severity: str
    enabled: bool
    check_type: str


class QABugResponse(BaseModel):
    """Response from /qa/bugs endpoint."""
    id: str
    name: str
    query: str
    expected_behavior: str
    actual_behavior: str
    status: str
    severity: str


@app.get("/qa/health", response_model=QAHealthResponse)
async def qa_get_health():
    """
    Get QA system health summary.

    Returns pass rate, failure counts, and recent issues.
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    validator = get_qa_validator()
    health = validator.get_health()
    health["system_events_24h"] = validator._db.count_events(hours=24)
    return QAHealthResponse(**health)


@app.post("/qa/recalibrate")
async def qa_recalibrate():
    """Mark a recalibration point. Stats will show 'since recalibration' context."""
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")
    validator = get_qa_validator()
    ts = validator.mark_recalibration()
    return {"status": "recalibrated", "timestamp": ts.isoformat()}


@app.get("/qa/last")
async def qa_get_last_report():
    """
    Get the most recent QA report.

    Returns full details of the last inference validation.
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    validator = get_qa_validator()
    report = validator.get_last_report()

    if not report:
        return {"error": "No reports yet"}

    return report.to_dict()


@app.get("/qa/stats", response_model=QAStatsResponse)
async def qa_get_stats(time_range: str = "24h"):
    """
    Get QA statistics for a time range.

    Args:
        time_range: "1h", "24h", "7d", "30d"
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    validator = get_qa_validator()
    stats = validator.get_stats(time_range)
    return QAStatsResponse(**stats)


@app.get("/qa/stats/detailed")
async def qa_get_stats_detailed(time_range: str = "7d"):
    """
    Get detailed QA statistics with breakdowns.

    Returns:
        - Basic stats (total, passed, failed, pass_rate)
        - Trend data (daily breakdown)
        - By route breakdown
        - By provider breakdown
        - By assertion breakdown
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    from datetime import datetime, timedelta
    from collections import defaultdict

    validator = get_qa_validator()
    db = validator._db

    # Get time range
    hours = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}.get(time_range, 168)
    since = datetime.now() - timedelta(hours=hours)

    # Get all reports in range
    reports = db.get_recent_reports(500)  # Get enough for analysis
    reports_in_range = [
        r for r in reports
        if datetime.fromisoformat(r["timestamp"]) > since
    ]

    # Basic stats
    total = len(reports_in_range)
    passed = sum(1 for r in reports_in_range if r["passed"])
    failed = total - passed

    # Trend data (group by day)
    trend_data = defaultdict(lambda: {"passed": 0, "failed": 0})
    for r in reports_in_range:
        date = datetime.fromisoformat(r["timestamp"]).strftime("%b %d")
        if r["passed"]:
            trend_data[date]["passed"] += 1
        else:
            trend_data[date]["failed"] += 1

    trend = [
        {"date": date, "passed": data["passed"], "failed": data["failed"]}
        for date, data in sorted(trend_data.items())
    ][-7:]  # Last 7 days

    # By route breakdown
    by_route = defaultdict(lambda: {"passed": 0, "failed": 0})
    for r in reports_in_range:
        route = r.get("route", "unknown")
        if r["passed"]:
            by_route[route]["passed"] += 1
        else:
            by_route[route]["failed"] += 1

    # By provider breakdown
    by_provider = defaultdict(lambda: {"passed": 0, "failed": 0})
    for r in reports_in_range:
        provider = r.get("provider_used", "unknown")
        if r["passed"]:
            by_provider[provider]["passed"] += 1
        else:
            by_provider[provider]["failed"] += 1

    # By assertion breakdown
    by_assertion = defaultdict(lambda: {"name": "", "passed": 0, "failed": 0})
    for r in reports_in_range:
        for a in r.get("assertions", []):
            aid = a.get("id", "unknown")
            by_assertion[aid]["name"] = a.get("name", aid)
            if a.get("passed"):
                by_assertion[aid]["passed"] += 1
            else:
                by_assertion[aid]["failed"] += 1

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / total if total > 0 else 0,
        "time_range": time_range,
        "trend": trend,
        "by_route": dict(by_route),
        "by_provider": dict(by_provider),
        "by_assertion": dict(by_assertion),
    }


@app.get("/qa/history")
async def qa_get_history(limit: int = 100):
    """
    Get QA report history.

    Args:
        limit: Maximum number of reports to return
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    validator = get_qa_validator()
    return validator.get_history(limit)


@app.get("/qa/assertions")
async def qa_list_assertions():
    """
    List all configured assertions.

    Returns both built-in and custom assertions.
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    validator = get_qa_validator()
    return [
        {
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "category": a.category,
            "severity": a.severity,
            "enabled": a.enabled,
            "check_type": a.check_type,
        }
        for a in validator.get_assertions()
    ]


class AddAssertionRequest(BaseModel):
    """Request body for adding a custom assertion."""
    name: str
    category: str  # structural, voice, personality, flow
    severity: str  # critical, high, medium, low
    target: str  # response, raw_response, system_prompt, query
    condition: str  # contains, not_contains, regex, length_gt, length_lt
    pattern: str
    case_sensitive: bool = False


@app.post("/qa/assertions")
async def qa_add_assertion(req: AddAssertionRequest):
    """
    Add a custom pattern-based assertion.

    Example:
        POST /qa/assertions
        {
            "name": "No French",
            "category": "voice",
            "severity": "medium",
            "target": "response",
            "condition": "not_contains",
            "pattern": "bonjour"
        }
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    import uuid
    validator = get_qa_validator()

    assertion = Assertion(
        id=f"CUSTOM-{uuid.uuid4().hex[:6].upper()}",
        name=req.name,
        description=f"Custom: {req.condition} '{req.pattern}' in {req.target}",
        category=req.category,
        severity=req.severity,
        check_type="pattern",
        pattern_config=PatternConfig(
            target=req.target,
            match_type=req.condition,
            pattern=req.pattern,
            case_sensitive=req.case_sensitive,
        ),
    )

    assertion_id = validator.add_assertion(assertion)
    return {"assertion_id": assertion_id, "name": req.name}


@app.put("/qa/assertions/{assertion_id}")
async def qa_toggle_assertion(assertion_id: str, enabled: bool):
    """
    Enable or disable an assertion.

    Args:
        assertion_id: The assertion ID (e.g., "P1", "CUSTOM-ABC123")
        enabled: Whether to enable (true) or disable (false)
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    validator = get_qa_validator()
    success = validator.toggle_assertion(assertion_id, enabled)
    return {"success": success, "assertion_id": assertion_id, "enabled": enabled}


@app.delete("/qa/assertions/{assertion_id}")
async def qa_delete_assertion(assertion_id: str):
    """
    Delete a custom assertion.

    Note: Built-in assertions (P1, S1, V1, F1, etc.) cannot be deleted.
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    validator = get_qa_validator()
    success = validator.delete_assertion(assertion_id)

    if not success:
        raise HTTPException(status_code=400, detail="Cannot delete built-in assertions")

    return {"success": success}


# ═══════════════════════════════════════════════════════════
# QA BUG ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.get("/qa/bugs")
async def qa_list_bugs(status: str = None):
    """
    List known bugs.

    Args:
        status: Filter by status (open, failing, fixed, wontfix)
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    validator = get_qa_validator()
    if status:
        return validator._db.get_bugs_by_status(status)
    return validator._db.get_all_bugs()


class AddBugRequest(BaseModel):
    """Request body for adding a bug."""
    name: str
    query: str
    expected_behavior: str
    actual_behavior: str
    severity: str = "high"


@app.post("/qa/bugs")
async def qa_add_bug(req: AddBugRequest):
    """
    Add a known bug to the regression database.

    This bug will be tested in regression runs.
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    validator = get_qa_validator()
    bug_id = validator._db.generate_bug_id()
    validator._db.store_bug({
        "id": bug_id,
        "name": req.name,
        "query": req.query,
        "expected_behavior": req.expected_behavior,
        "actual_behavior": req.actual_behavior,
        "severity": req.severity,
    })
    return {"bug_id": bug_id, "name": req.name}


@app.post("/qa/bugs/from-last")
async def qa_add_bug_from_last(name: str, expected_behavior: str):
    """
    Create a bug from the last failed QA report.

    Automatically captures query and actual behavior.
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    validator = get_qa_validator()
    report = validator.get_last_report()

    if not report:
        raise HTTPException(status_code=404, detail="No reports available")

    bug_id = validator._db.generate_bug_id()
    validator._db.store_bug({
        "id": bug_id,
        "name": name,
        "query": report.query,
        "expected_behavior": expected_behavior,
        "actual_behavior": report.context.final_response[:500],
        "affected_assertions": [a.id for a in report.failed_assertions],
    })

    return {"bug_id": bug_id, "query": report.query}


@app.get("/qa/bugs/{bug_id}")
async def qa_get_bug(bug_id: str):
    """Get details for a specific bug."""
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    validator = get_qa_validator()
    bug = validator._db.get_bug_by_id(bug_id)

    if not bug:
        raise HTTPException(status_code=404, detail=f"Bug {bug_id} not found")

    return bug


@app.put("/qa/bugs/{bug_id}")
async def qa_update_bug_status(bug_id: str, status: str):
    """
    Update a bug's status.

    Args:
        status: open, failing, fixed, wontfix
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    validator = get_qa_validator()
    validator._db.update_bug_status(bug_id, status)
    return {"bug_id": bug_id, "status": status}


# ═══════════════════════════════════════════════════════════
# QA SIMULATION ENDPOINT
# ═══════════════════════════════════════════════════════════


class SimulateRequest(BaseModel):
    """Request body for /qa/simulate endpoint."""
    query: str = Field(..., description="The query to simulate")
    bug_id: Optional[str] = Field(None, description="Associated bug ID if testing a known bug")
    route_override: Optional[str] = Field(
        None,
        description='Force routing for this run: "local"/"LOCAL_ONLY" or "delegated"/"FULL_DELEGATION".',
    )


@app.post("/qa/simulate")
async def qa_simulate(request: SimulateRequest):
    """
    Run a simulation against a specific query.

    This sends the query through the full inference pipeline and validates
    the response against QA assertions. Used for testing bug regressions.
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    if not _engine:
        raise HTTPException(status_code=503, detail="Engine not started")

    import time
    import uuid
    from datetime import datetime

    start_time = time.time()
    inference_id = f"SIM-{uuid.uuid4().hex[:8]}"

    try:
        # Run message through engine via callback pattern (same as /message)
        response_future: asyncio.Future = asyncio.Future()

        async def on_sim_response(text: str, data: dict) -> None:
            if not response_future.done():
                response_future.set_result((text, data))

        _engine.on_response(on_sim_response)

        try:
            if request.route_override:
                _director = _engine.get_actor("director")
                if _director:
                    _director.set_next_overrides({"force_route": request.route_override})
            await _engine.send_message(request.query, source="qa-simulate")
            response_text, response_data = await asyncio.wait_for(response_future, timeout=30.0)
        finally:
            if on_sim_response in _engine._on_response_callbacks:
                _engine._on_response_callbacks.remove(on_sim_response)

        latency_ms = (time.time() - start_time) * 1000

        # Fire background QA validation (same as /message endpoint)
        _track_task(
            _run_qa_validation_background(request.query, response_text, response_data),
            name="qa-validation-sim",
        )
        # Give the background validator time to read engine state and write report
        await asyncio.sleep(0.5)

        validator = get_qa_validator()

        # Use last report if it matches the query, otherwise build minimal context
        report = None
        if validator._last_report and validator._last_report.query == request.query:
            report = validator._last_report
        else:
            from luna.qa import InferenceContext

            # Read live engine state for accurate fallback
            _sys_prompt = ""
            _personality_len = 0
            _personality_injected = False
            _memory_stats = {}
            _voice_injected = False
            _narration_applied = response_data.get("narration_applied", False)

            if _engine:
                _dir = _engine.get_actor("director")
                if _dir:
                    _pinfo = _dir.get_last_system_prompt()
                    if _pinfo.get("available"):
                        _sys_prompt = _pinfo.get("full_prompt", "")
                        _personality_len = _pinfo.get("length", 0)
                        _personality_injected = _personality_len > 1000
                    # Check assembler metadata for voice
                    _last_meta = getattr(_dir, "_last_prompt_meta", None)
                    if _last_meta:
                        _voice_injected = _last_meta.get("voice_injected", False)

                _mat = _engine.get_actor("matrix")
                if _mat:
                    # Same cold-start race as the main /message path — poll for warmth.
                    _mem = None
                    for _ in range(5):
                        _mem = getattr(_mat, "matrix", None) or getattr(_mat, "_matrix", None)
                        if _mem is not None:
                            break
                        await asyncio.sleep(0.2)

                    if _mem is None:
                        logger.warning(
                            "QA_STATS_SIM: matrix actor present but .matrix/_matrix is None after 1s wait"
                        )
                    else:
                        try:
                            _memory_stats = await _mem.get_stats()
                        except Exception as e:
                            logger.warning(
                                f"QA_STATS_SIM: get_stats() raised — error_type={type(e).__name__} error={e}"
                            )

            ctx = InferenceContext(
                inference_id=inference_id,
                session_id="simulation",
                timestamp=datetime.now(),
                query=request.query,
                route=response_data.get("route_decision", "SIMULATION"),
                provider_used=response_data.get("provider_used", "unknown"),
                latency_ms=latency_ms,
                personality_injected=_personality_injected,
                personality_length=_personality_len,
                system_prompt=_sys_prompt,
                voice_injected=_voice_injected,
                virtues_loaded=_personality_injected,
                narration_applied=_narration_applied,
                memory_stats=_memory_stats,
                raw_response=response_text,
                final_response=response_text,
            )
            report = validator.validate(ctx)

        final_text = response_text

        return {
            "inference_id": inference_id,
            "bug_id": request.bug_id,
            "query": request.query,
            "passed": report.passed,
            "failed_count": report.failed_count,
            "latency_ms": latency_ms,
            "response": final_text,
            "final_response": final_text,
            "failed_assertions": [a.id for a in report.failed_assertions],
            "diagnosis": report.diagnosis,
            "assertions": [
                {
                    "id": a.id,
                    "name": a.name,
                    "passed": a.passed,
                    "severity": a.severity,
                    "expected": a.expected,
                    "actual": a.actual,
                }
                for a in report.assertions
            ],
        }

    except asyncio.TimeoutError:
        return {
            "inference_id": inference_id,
            "bug_id": request.bug_id,
            "query": request.query,
            "passed": False,
            "failed_count": 1,
            "latency_ms": 30000,
            "response": "Timeout",
            "final_response": "Timeout",
            "failed_assertions": ["TIMEOUT"],
            "diagnosis": "Simulation timed out after 30 seconds",
            "assertions": [],
        }
    except Exception as e:
        logger.error(f"Simulation failed: {e}")
        return {
            "inference_id": inference_id,
            "bug_id": request.bug_id,
            "query": request.query,
            "passed": False,
            "failed_count": 1,
            "latency_ms": (time.time() - start_time) * 1000,
            "response": str(e),
            "final_response": str(e),
            "failed_assertions": ["EXCEPTION"],
            "diagnosis": f"Simulation failed with exception: {e}",
            "assertions": [],
        }


# ═══════════════════════════════════════════════════════════
# QA DIAGNOSTIC EVENTS
# ═══════════════════════════════════════════════════════════


@app.get("/qa/events")
async def qa_get_events(
    source: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50,
):
    """Get diagnostic events (watchdog alerts, health failures, API errors)."""
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")
    validator = get_qa_validator()
    return validator._db.get_events(source=source, severity=severity, limit=limit)


@app.get("/qa/events/summary")
async def qa_get_event_summary(hours: int = 24):
    """Get event counts grouped by source and severity."""
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")
    validator = get_qa_validator()
    return validator._db.get_event_summary(hours=hours)


# ═══════════════════════════════════════════════════════════
# INTERGALACTIC HUB DASHBOARD
# ═══════════════════════════════════════════════════════════

_IH_RUNNERS = ("drive", "calendar", "trello", "meetings", "discord")
_IH_MCP_PORT = 8883


def _ih_pid_alive(pid: int) -> bool:
    import os as _os
    try:
        _os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _ih_pid_dir():
    from luna.core.paths import project_root
    return project_root() / "intergalactic_hub" / "storage" / ".pids"


def _ih_db_path():
    return memory_matrix_path()


def _ih_runner_status():
    import os as _os
    import time as _time
    pid_dir = _ih_pid_dir()
    out = {}
    for name in _IH_RUNNERS:
        info = {"alive": False, "pid": None, "uptime_s": None, "pidfile_mtime": None}
        pf = pid_dir / f"{name}.pid"
        if pf.exists():
            try:
                pid = int(pf.read_text().strip())
                if _ih_pid_alive(pid):
                    info["alive"] = True
                    info["pid"] = pid
                    info["uptime_s"] = int(_time.time() - pf.stat().st_mtime)
                    info["pidfile_mtime"] = int(pf.stat().st_mtime)
            except (OSError, ValueError):
                pass
        out[name] = info
    return out


def _ih_open_ro_conn():
    import sqlite3
    db = _ih_db_path()
    if not db.exists():
        raise HTTPException(status_code=503, detail="memory_matrix.lun missing")
    # mode=ro so a stuck writer cannot block dashboard reads
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


def _ih_today_midnight_utc() -> str:
    from datetime import datetime, time as dtime, timezone
    return (
        datetime.combine(datetime.now(timezone.utc).date(), dtime.min, tzinfo=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


@app.get("/api/ih/health")
async def ih_health():
    import sqlite3
    import socket as _socket

    runners = _ih_runner_status()
    midnight = _ih_today_midnight_utc()

    try:
        conn = _ih_open_ro_conn()
    except HTTPException:
        raise
    try:
        try:
            ev_today = conn.execute(
                "SELECT COUNT(*) FROM ih_events WHERE created_at >= ?", (midnight,)
            ).fetchone()[0]
            ac_today = conn.execute(
                "SELECT COUNT(*) FROM ih_action_log WHERE created_at >= ?", (midnight,)
            ).fetchone()[0]
            stale = conn.execute(
                "SELECT source, updated_at FROM ih_ingestion_state ORDER BY updated_at ASC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail="db_busy")
    finally:
        conn.close()

    mcp_up = False
    try:
        with _socket.create_connection(("127.0.0.1", _IH_MCP_PORT), timeout=0.25):
            mcp_up = True
    except OSError:
        pass

    return {
        "runners": runners,
        "alive_count": sum(1 for r in runners.values() if r["alive"]),
        "total_count": len(runners),
        "events_today": ev_today,
        "actions_today": ac_today,
        "mcp_listening": mcp_up,
        "db_freshness": {
            "most_stale_source": stale["source"] if stale else None,
            "most_stale_updated_at": stale["updated_at"] if stale else None,
        },
    }


@app.get("/api/ih/runners")
async def ih_runners():
    import sqlite3

    runners = _ih_runner_status()

    try:
        conn = _ih_open_ro_conn()
    except HTTPException:
        raise
    try:
        try:
            rows = conn.execute(
                "SELECT source, MAX(updated_at) AS last_at, "
                "       (SELECT last_error FROM ih_ingestion_state s2 "
                "        WHERE s2.source = ih_ingestion_state.source "
                "        ORDER BY updated_at DESC LIMIT 1) AS last_error "
                "FROM ih_ingestion_state GROUP BY source"
            ).fetchall()
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail="db_busy")
    finally:
        conn.close()

    last_by_prefix = {}
    err_by_prefix = {}
    for row in rows:
        prefix = (row["source"] or "").split(":")[0]
        last_at = row["last_at"]
        if last_at and (prefix not in last_by_prefix or last_at > last_by_prefix[prefix]):
            last_by_prefix[prefix] = last_at
            err_by_prefix[prefix] = row["last_error"]

    out = []
    for name in _IH_RUNNERS:
        r = runners[name]
        out.append({
            "name": name,
            "alive": r["alive"],
            "pid": r["pid"],
            "uptime_s": r["uptime_s"],
            "pidfile_mtime": r["pidfile_mtime"],
            "last_cycle_at": last_by_prefix.get(name),
            "last_error": err_by_prefix.get(name),
        })
    return {"runners": out}


@app.get("/api/ih/ingestion")
async def ih_ingestion():
    import sqlite3
    try:
        conn = _ih_open_ro_conn()
    except HTTPException:
        raise
    try:
        try:
            rows = conn.execute(
                "SELECT source, status, last_cursor, updated_at, last_error "
                "FROM ih_ingestion_state ORDER BY updated_at DESC"
            ).fetchall()
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail="db_busy")
    finally:
        conn.close()
    return {"sources": [dict(r) for r in rows]}


@app.get("/api/ih/events")
async def ih_events(limit: int = 50, source: str | None = None):
    import sqlite3
    limit = max(1, min(int(limit), 500))
    try:
        conn = _ih_open_ro_conn()
    except HTTPException:
        raise
    try:
        try:
            if source:
                rows = conn.execute(
                    "SELECT id, source, source_id, kind, timestamp, content, created_at "
                    "FROM ih_events WHERE source LIKE ? ORDER BY id DESC LIMIT ?",
                    (f"{source}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, source, source_id, kind, timestamp, content, created_at "
                    "FROM ih_events ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail="db_busy")
    finally:
        conn.close()

    events = []
    for r in rows:
        c = r["content"] or ""
        events.append({
            "id": r["id"],
            "source": r["source"],
            "source_id": r["source_id"],
            "kind": r["kind"],
            "timestamp": r["timestamp"],
            "content_preview": c[:240],
            "created_at": r["created_at"],
        })
    return {"events": events}


@app.get("/api/ih/actions")
async def ih_actions(limit: int = 50):
    import sqlite3
    limit = max(1, min(int(limit), 500))
    try:
        conn = _ih_open_ro_conn()
    except HTTPException:
        raise
    try:
        try:
            rows = conn.execute(
                "SELECT id, tool_name, caller, dry_run, status, error, result, created_at "
                "FROM ih_action_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail="db_busy")
    finally:
        conn.close()

    actions = []
    for r in rows:
        result = r["result"] or ""
        actions.append({
            "id": r["id"],
            "tool_name": r["tool_name"],
            "caller": r["caller"],
            "dry_run": bool(r["dry_run"]),
            "status": r["status"],
            "error": r["error"],
            "detail": result[:200],
            "created_at": r["created_at"],
        })
    return {"actions": actions}


# ═══════════════════════════════════════════════════════════
# QA SLASH COMMAND
# ═══════════════════════════════════════════════════════════

@app.get("/slash/qa", response_model=SlashCommandResponse)
async def slash_qa():
    """
    /qa - Quick QA health check.

    Shows pass rate, recent failures, and known bugs.
    """
    if not QA_AVAILABLE:
        return SlashCommandResponse(
            command="/qa",
            success=False,
            data={"error": "QA system not available"},
            formatted="**❌ QA System Not Available**\n\nThe QA module failed to load.",
        )

    validator = get_qa_validator()
    health = validator.get_health()

    # Build formatted output
    pass_rate = health.get("pass_rate", 0) * 100
    status_icon = "✅" if pass_rate >= 90 else "⚠️" if pass_rate >= 70 else "❌"

    lines = [
        f"**{status_icon} QA Health: {pass_rate:.1f}% pass rate**",
        "",
        f"• Total (24h): {health.get('total_24h', 0)}",
        f"• Failed (24h): {health.get('failed_24h', 0)}",
        f"• Open bugs: {health.get('failing_bugs', 0)}",
    ]

    if health.get("recent_failures"):
        lines.append("")
        lines.append("**Recent failures:**")
        for name in health.get("recent_failures", [])[:5]:
            lines.append(f"  • {name}")

    if health.get("top_failures"):
        lines.append("")
        lines.append("**Top failing assertions:**")
        for f in health.get("top_failures", [])[:3]:
            lines.append(f"  • {f['name']} ({f['count']}x)")

    return SlashCommandResponse(
        command="/qa",
        success=pass_rate >= 70,
        data=health,
        formatted="\n".join(lines),
    )


@app.get("/slash/qa-last", response_model=SlashCommandResponse)
async def slash_qa_last():
    """
    /qa-last - Show last QA report details.
    """
    if not QA_AVAILABLE:
        return SlashCommandResponse(
            command="/qa-last",
            success=False,
            data={"error": "QA system not available"},
            formatted="**❌ QA System Not Available**",
        )

    validator = get_qa_validator()
    report = validator.get_last_report()

    if not report:
        return SlashCommandResponse(
            command="/qa-last",
            success=True,
            data={"message": "No reports yet"},
            formatted="No QA reports yet. Send a message to generate one.",
        )

    # Build formatted output
    status_icon = "✅" if report.passed else "❌"
    lines = [
        f"**{status_icon} Last QA Report**",
        "",
        f"• Query: \"{report.query[:50]}{'...' if len(report.query) > 50 else ''}\"",
        f"• Route: {report.route}",
        f"• Provider: {report.provider_used}",
        f"• Latency: {report.latency_ms:.0f}ms",
        "",
    ]

    if report.passed:
        lines.append(f"All {len(report.assertions)} assertions passed.")
    else:
        lines.append(f"**{report.failed_count} assertion(s) failed:**")
        for a in report.failed_assertions:
            lines.append(f"  • [{a.severity}] {a.name}: {a.actual}")

        if report.diagnosis:
            lines.append("")
            lines.append(f"**Diagnosis:** {report.diagnosis[:200]}{'...' if len(report.diagnosis) > 200 else ''}")

    return SlashCommandResponse(
        command="/qa-last",
        success=report.passed,
        data=report.to_dict(),
        formatted="\n".join(lines),
    )


# ═══════════════════════════════════════════════════════════
# ARCADE ENDPOINTS
# ═══════════════════════════════════════════════════════════


@app.get("/api/arcade/games")
async def arcade_list_games():
    """List available arcade games."""
    _ensure_arcade_enabled()
    try:
        from luna.skills.arcade.game_registry import list_available, game_info_dict
        games = list_available()
        return {"games": [game_info_dict(g) for g in games]}
    except ImportError:
        return {"games": [], "error": "Arcade module not available"}


@app.get("/api/arcade/status")
async def arcade_status():
    """Get current arcade game status."""
    _ensure_arcade_enabled()
    try:
        from luna.skills.arcade.process_manager import ProcessManager
        pm = ProcessManager.get()
        status = pm.status()
        return {"running": status is not None, "game": status}
    except ImportError:
        return {"running": False, "game": None, "error": "Arcade module not available"}


@app.post("/api/arcade/launch")
async def arcade_launch(body: dict = None):
    """Launch an arcade game by ID."""
    _ensure_arcade_enabled()
    body = body or {}
    game_id = body.get("game_id", "steve_j_savage")
    try:
        from luna.skills.arcade.game_registry import get_game, get_games_dir
        from luna.skills.arcade.process_manager import ProcessManager
        game = get_game(game_id)
        if not game:
            return {"success": False, "error": f"Unknown game: {game_id}"}
        game_path = get_games_dir() / game.file
        if not game_path.exists():
            return {"success": False, "error": f"Game file not found: {game.file}"}
        pm = ProcessManager.get()
        gp = await pm.launch(game.id, game_path, game.title)
        return {"success": True, "game": game.title, "pid": gp.pid}
    except ImportError:
        return {"success": False, "error": "Arcade module not available"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/arcade/stop")
async def arcade_stop():
    """Stop the running arcade game."""
    _ensure_arcade_enabled()
    try:
        from luna.skills.arcade.process_manager import ProcessManager
        pm = ProcessManager.get()
        stopped = await pm.stop()
        return {"success": True, "stopped": stopped}
    except ImportError:
        return {"success": False, "error": "Arcade module not available"}


@app.get("/api/arcade/tune")
async def arcade_get_tune():
    """Read current tuning parameters."""
    _ensure_arcade_enabled()
    try:
        from luna.skills.arcade.game_registry import get_games_dir
        import json
        tune_path = get_games_dir() / "tune.json"
        if tune_path.exists():
            return json.loads(tune_path.read_text())
        return {}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/arcade/tune")
async def arcade_set_tune(body: dict):
    """Write tuning parameters (hot-reloaded by running game)."""
    _ensure_arcade_enabled()
    try:
        from luna.skills.arcade.game_registry import get_games_dir
        import json
        tune_path = get_games_dir() / "tune.json"
        # Merge with existing
        existing = {}
        if tune_path.exists():
            try:
                existing = json.loads(tune_path.read_text())
            except Exception:
                pass
        existing.update(body)
        tune_path.write_text(json.dumps(existing, indent=2))
        return {"success": True, "tune": existing}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/arcade/presets")
async def arcade_list_presets():
    """List saved tuning presets."""
    _ensure_arcade_enabled()
    try:
        from luna.skills.arcade.game_registry import get_games_dir
        import json
        presets_dir = get_games_dir() / "presets"
        if not presets_dir.exists():
            return {"presets": []}
        presets = []
        for f in sorted(presets_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                presets.append({"name": f.stem, "tune": data})
            except Exception:
                pass
        return {"presets": presets}
    except Exception as e:
        return {"presets": [], "error": str(e)}


@app.post("/api/arcade/presets/save")
async def arcade_save_preset(body: dict):
    """Save current tune as a named preset. Body: {"name": "..."}"""
    _ensure_arcade_enabled()
    try:
        from luna.skills.arcade.game_registry import get_games_dir
        import json, re
        name = body.get("name", "").strip()
        if not name:
            return {"success": False, "error": "Preset name required"}
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name)[:40]
        presets_dir = get_games_dir() / "presets"
        presets_dir.mkdir(exist_ok=True)
        tune_path = get_games_dir() / "tune.json"
        current = {}
        if tune_path.exists():
            current = json.loads(tune_path.read_text())
        preset_path = presets_dir / f"{safe_name}.json"
        preset_path.write_text(json.dumps(current, indent=2))
        return {"success": True, "name": safe_name}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/arcade/presets/load")
async def arcade_load_preset(body: dict):
    """Load a named preset into tune.json. Body: {"name": "..."}"""
    _ensure_arcade_enabled()
    try:
        from luna.skills.arcade.game_registry import get_games_dir
        import json
        name = body.get("name", "")
        presets_dir = get_games_dir() / "presets"
        preset_path = presets_dir / f"{name}.json"
        if not preset_path.exists():
            return {"success": False, "error": f"Preset '{name}' not found"}
        data = json.loads(preset_path.read_text())
        tune_path = get_games_dir() / "tune.json"
        tune_path.write_text(json.dumps(data, indent=2))
        return {"success": True, "tune": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/arcade/presets/delete")
async def arcade_delete_preset(body: dict):
    """Delete a named preset. Body: {"name": "..."}"""
    _ensure_arcade_enabled()
    try:
        from luna.skills.arcade.game_registry import get_games_dir
        name = body.get("name", "")
        presets_dir = get_games_dir() / "presets"
        preset_path = presets_dir / f"{name}.json"
        if preset_path.exists():
            preset_path.unlink()
            return {"success": True}
        return {"success": False, "error": "Not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/arcade/all-games")
async def arcade_all_games():
    """List all games (including unavailable) with an 'available' flag."""
    _ensure_arcade_enabled()
    try:
        from luna.skills.arcade.game_registry import list_all
        return {"games": list_all()}
    except ImportError:
        return {"games": [], "error": "Arcade module not available"}


@app.get("/api/arcade/last-score")
async def arcade_last_score():
    """Read the last game score written by a completed game."""
    _ensure_arcade_enabled()
    try:
        from luna.skills.arcade.game_registry import get_games_dir
        import json
        score_path = get_games_dir() / "last_score.json"
        if score_path.exists():
            return json.loads(score_path.read_text())
        return None
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# SKILL SLASH ENDPOINTS
# ═══════════════════════════════════════════════════════════


@app.post("/slash/skill/{name}", response_model=SlashCommandResponse)
async def slash_skill(name: str, body: dict = None):
    """Directly invoke a skill by name. Body: {"query": "..."}"""
    if not _engine:
        return SlashCommandResponse(
            command=f"/skill/{name}", success=False, data={},
            formatted="Engine not running",
        )
    director = _engine.get_actor("director")
    registry = getattr(director, "_skill_registry", None) if director else None
    if not registry or not registry.get(name):
        return SlashCommandResponse(
            command=f"/skill/{name}", success=False, data={},
            formatted=f"Skill '{name}' not found or not registered",
        )
    query = (body or {}).get("query", "")
    result = await registry.execute(name, query, context={})
    formatted = ""
    if result.data:
        formatted = result.data.get("formatted", str(result.data))
    elif result.error:
        formatted = result.error
    return SlashCommandResponse(
        command=f"/skill/{name}",
        success=result.success,
        data=result.data or {},
        formatted=formatted,
    )


# ═══════════════════════════════════════════════════════════
# VOIGHT-KAMPFF ENDPOINTS
# ═══════════════════════════════════════════════════════════


@app.get("/vk/results/voice-memory")
async def get_vk_voice_memory_results():
    """
    Get the latest Voice Memory VK test results.

    Returns the results from the last test run, loaded from the JSON file.
    """
    import json
    from pathlib import Path

    results_path = project_root() / "Docs" / "Handoffs" / "VoightKampffResults" / "voice_memory_results.json"

    if not results_path.exists():
        raise HTTPException(status_code=404, detail="No test results found. Run the test suite first.")

    try:
        with open(results_path) as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load results: {e}")


@app.get("/vk/results/latest")
async def get_vk_latest_results():
    """
    Get the latest VK test results from any suite.
    """
    import json
    from pathlib import Path

    results_dir = project_root() / "Docs" / "Handoffs" / "VoightKampffResults"

    if not results_dir.exists():
        raise HTTPException(status_code=404, detail="No test results directory found")

    # Find most recent results file
    result_files = list(results_dir.glob("*_results.json"))
    if not result_files:
        raise HTTPException(status_code=404, detail="No test results found")

    # Sort by modification time, get most recent
    latest = max(result_files, key=lambda p: p.stat().st_mtime)

    try:
        with open(latest) as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load results: {e}")



# Observatory routes are now mounted directly via observatory_router (see top of file).
# The old httpx reverse proxy to :8100 has been removed.


# ==============================================================================
# AiBrarian Engine API (HTTP surface for Google Sheets, Guardian, etc.)
# ==============================================================================

_aibrarian_engine = None

async def _get_aibrarian_engine():
    """Get the Engine-owned AiBrarianEngine instance.

    Prefers the Engine-owned instance (Phase 1 ownership). Falls back to
    standalone initialization only when the Engine hasn't booted yet.
    """
    global _aibrarian_engine
    if _aibrarian_engine is not None:
        return _aibrarian_engine

    # Phase 1: use Engine-owned instance
    if _engine is not None and getattr(_engine, "aibrarian", None) is not None:
        _aibrarian_engine = _engine.aibrarian
        return _aibrarian_engine

    # Fallback: standalone init (server started without full Engine boot)
    from luna.substrate.aibrarian_engine import AiBrarianEngine
    _aibrarian_engine = AiBrarianEngine(
        config_dir() / "aibrarian_registry.yaml",
        project_root=project_root(),
    )
    await _aibrarian_engine.initialize()
    logger.warning("AiBrarianEngine fallback: standalone init in server.py")
    return _aibrarian_engine


class AiBrarianSearchRequest(BaseModel):
    collection: str = "dataroom"
    query: str
    search_type: str = "hybrid"
    limit: int = 10


class AiBrarianIngestRequest(BaseModel):
    collection: str = "dataroom"
    file_path: str
    metadata: dict = Field(default_factory=dict)


@app.get("/api/nexus/list")
async def api_aibrarian_list():
    engine = await _get_aibrarian_engine()
    return {"collections": engine.list_collections()}


@app.post("/api/nexus/search")
async def api_aibrarian_search(req: AiBrarianSearchRequest):
    engine = await _get_aibrarian_engine()
    results = await engine.search(req.collection, req.query, req.search_type, req.limit)
    return {"results": results, "count": len(results)}


@app.get("/api/nexus/{collection_key}/stats")
async def api_aibrarian_stats(collection_key: str):
    engine = await _get_aibrarian_engine()
    return await engine.stats(collection_key)


@app.post("/api/nexus/ingest")
async def api_aibrarian_ingest(req: AiBrarianIngestRequest):
    engine = await _get_aibrarian_engine()
    doc_id = await engine.ingest(req.collection, Path(req.file_path), req.metadata)
    return {"doc_id": doc_id, "status": "ingested" if doc_id else "skipped"}


# --- Co-occurrence (POST — matches existing pattern) ---

class AiBrarianCoOccurrenceRequest(BaseModel):
    collection: str = "dataroom"
    terms: str
    limit: int = 50

@app.post("/api/nexus/co-occurrence")
async def api_aibrarian_co_occurrence(req: AiBrarianCoOccurrenceRequest):
    engine = await _get_aibrarian_engine()
    term_list = [t.strip() for t in req.terms.split(",") if t.strip()]
    results = await engine.co_occurrence(req.collection, term_list, req.limit)
    return {"terms": term_list, "count": len(results), "documents": results}


# --- Document retrieval ---

@app.get("/api/nexus/{c}/documents")
async def api_aibrarian_list_documents(c: str, skip: int = 0, limit: int = 50):
    engine = await _get_aibrarian_engine()
    return await engine.list_documents(c, skip, limit)

@app.get("/api/nexus/{c}/documents/{doc_id}")
async def api_aibrarian_get_document(c: str, doc_id: str):
    engine = await _get_aibrarian_engine()
    doc = await engine.get_document(c, doc_id)
    if not doc:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    return doc


# --- Count & term stats ---

@app.get("/api/nexus/{c}/count")
async def api_aibrarian_count(c: str, q: str = "", search_type: str = "keyword"):
    engine = await _get_aibrarian_engine()
    n = await engine.count(c, q, search_type)
    return {"query": q, "search_type": search_type, "count": n}

@app.get("/api/nexus/{c}/terms")
async def api_aibrarian_term_stats(c: str, terms: str = ""):
    engine = await _get_aibrarian_engine()
    term_list = [t.strip() for t in terms.split(",") if t.strip()]
    counts = await engine.term_stats(c, term_list)
    return {"counts": counts}


# --- Entities ---

@app.get("/api/nexus/{c}/entities/top")
async def api_aibrarian_top_entities(c: str, limit: int = 50, sample_size: int = 100):
    engine = await _get_aibrarian_engine()
    return await engine.top_entities(c, limit, sample_size)

@app.get("/api/nexus/{c}/entities/search")
async def api_aibrarian_search_entity(c: str, name: str = "", limit: int = 50):
    engine = await _get_aibrarian_engine()
    return await engine.search_entity(c, name, limit)

@app.get("/api/nexus/{c}/entities/{doc_id}")
async def api_aibrarian_document_entities(c: str, doc_id: str):
    engine = await _get_aibrarian_engine()
    return await engine.document_entities(c, doc_id)


# --- Timeline ---

@app.get("/api/nexus/{c}/timeline")
async def api_aibrarian_timeline(c: str, q: str = "", limit: int = 100, confidence: str = ""):
    engine = await _get_aibrarian_engine()
    conf = confidence if confidence else None
    return await engine.timeline(c, q, limit, conf)

@app.get("/api/nexus/{c}/timeline/range")
async def api_aibrarian_timeline_range(c: str, start: str = "", end: str = "", limit: int = 100, confidence: str = ""):
    engine = await _get_aibrarian_engine()
    conf = confidence if confidence else None
    return await engine.timeline_range(c, start, end, limit, conf)

@app.get("/api/nexus/{c}/timeline/{doc_id}")
async def api_aibrarian_document_timeline(c: str, doc_id: str, confidence: str = ""):
    engine = await _get_aibrarian_engine()
    conf = confidence if confidence else None
    return await engine.document_timeline(c, doc_id, conf)


# --- Analytics ---

@app.get("/api/nexus/{c}/analytics/frequency")
async def api_aibrarian_word_frequency(c: str, q: str = "", top: int = 50):
    engine = await _get_aibrarian_engine()
    return await engine.word_frequency(c, q, top)

@app.get("/api/nexus/{c}/analytics/ngrams")
async def api_aibrarian_ngrams(c: str, q: str = "", n: int = 2, top: int = 30):
    engine = await _get_aibrarian_engine()
    return await engine.ngrams(c, q, n, top)

@app.get("/api/nexus/{c}/analytics/wordcloud")
async def api_aibrarian_wordcloud(c: str, q: str = "", top: int = 100):
    engine = await _get_aibrarian_engine()
    return await engine.wordcloud(c, q, top)

@app.get("/api/nexus/{c}/analytics/compare")
async def api_aibrarian_compare_terms(c: str, terms: str = "", context_window: int = 5, top: int = 20):
    engine = await _get_aibrarian_engine()
    term_list = [t.strip() for t in terms.split(",") if t.strip()]
    return await engine.compare_terms(c, term_list, context_window, top)


# --- Similarity ---

@app.get("/api/nexus/{c}/similar/{doc_id}")
async def api_aibrarian_similar(c: str, doc_id: str, limit: int = 10):
    engine = await _get_aibrarian_engine()
    results = await engine.similar(c, doc_id, limit)
    return {"source_doc": doc_id, "similar_documents": results, "count": len(results)}

@app.get("/api/nexus/{c}/similarity/batch")
async def api_aibrarian_batch_similarity(c: str, doc_ids: str = ""):
    engine = await _get_aibrarian_engine()
    id_list = [d.strip() for d in doc_ids.split(",") if d.strip()]
    return await engine.batch_similarity(c, id_list)


# --- Export ---

@app.get("/api/nexus/{c}/export/csv")
async def api_aibrarian_export_csv(c: str, q: str = "", limit: int = 1000):
    engine = await _get_aibrarian_engine()
    import csv, io
    from fastapi.responses import StreamingResponse
    results = await engine.search(c, q, "hybrid", limit)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["doc_id", "title", "filename", "category", "score", "snippet"])
    for r in results:
        writer.writerow([r.get("doc_id"), r.get("title"), r.get("filename"),
                         r.get("category"), r.get("score"), r.get("snippet", "")[:300]])
    output.seek(0)
    safe_q = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in q)[:50]
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="nexus_{safe_q}.csv"'},
    )

@app.get("/api/nexus/{c}/export/json")
async def api_aibrarian_export_json(c: str, q: str = "", limit: int = 1000):
    engine = await _get_aibrarian_engine()
    return await engine.export_search(c, q, limit, "json")

@app.get("/api/nexus/{c}/export/document/{doc_id}")
async def api_aibrarian_export_document(c: str, doc_id: str, fmt: str = "json"):
    engine = await _get_aibrarian_engine()
    doc = await engine.get_document(c, doc_id)
    if not doc:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    if fmt == "txt":
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            iter([doc.get("full_text", "")]),
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{doc_id}.txt"'},
        )
    return doc


# --- Read-only SQL ---

@app.get("/api/nexus/{c}/sql")
async def api_aibrarian_sql(c: str, q: str = "", limit: int = 100):
    engine = await _get_aibrarian_engine()
    try:
        return await engine.execute_sql(c, q, limit)
    except PermissionError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))


# =====================================================================
# APERTURE & LIBRARY COGNITION ENDPOINTS
# =====================================================================

# --- Shared state (Phase 1: delegates to Engine-owned instances) ---
_aperture_manager = None
_collection_lock_in_engine = None
_annotation_engine = None


def _get_aperture_manager():
    global _aperture_manager
    if _aperture_manager is not None:
        return _aperture_manager
    # Phase 1: use Engine-owned instance
    if _engine is not None and getattr(_engine, "aperture", None) is not None:
        _aperture_manager = _engine.aperture
        return _aperture_manager
    # Fallback: standalone
    from luna.context.aperture import ApertureManager
    _aperture_manager = ApertureManager()
    return _aperture_manager


async def _get_lock_in_engine():
    global _collection_lock_in_engine
    if _collection_lock_in_engine is not None:
        return _collection_lock_in_engine
    # Phase 1: use Engine-owned instance
    if _engine is not None and getattr(_engine, "collection_lock_in", None) is not None:
        _collection_lock_in_engine = _engine.collection_lock_in
        return _collection_lock_in_engine

    # Fallback: standalone init
    from luna.substrate.collection_lock_in import CollectionLockInEngine

    if _engine is None:
        return None

    matrix = _engine.get_actor("matrix")
    db = getattr(matrix, "_matrix", None)
    if db is None:
        return None
    mem_db = getattr(db, "db", None)
    if mem_db is None:
        return None

    _collection_lock_in_engine = CollectionLockInEngine(mem_db)
    await _collection_lock_in_engine.ensure_table()

    if _aibrarian_engine is not None:
        _aibrarian_engine.set_lock_in_engine(_collection_lock_in_engine)

    return _collection_lock_in_engine


async def _get_annotation_engine():
    global _annotation_engine
    if _annotation_engine is not None:
        return _annotation_engine
    # Phase 1: use Engine-owned instance
    if _engine is not None and getattr(_engine, "annotations", None) is not None:
        _annotation_engine = _engine.annotations
        return _annotation_engine

    # Fallback: standalone init
    from luna.substrate.collection_annotations import AnnotationEngine

    if _engine is None:
        return None

    matrix_actor = _engine.get_actor("matrix")
    matrix = getattr(matrix_actor, "_matrix", None)
    if matrix is None:
        return None
    mem_db = getattr(matrix, "db", None)
    if mem_db is None:
        return None

    lock_in = await _get_lock_in_engine()
    _annotation_engine = AnnotationEngine(mem_db, memory_matrix=matrix, lock_in_engine=lock_in)
    await _annotation_engine.ensure_table()
    return _annotation_engine


# --- Aperture Endpoints ---

@app.get("/api/aperture")
async def api_aperture_get():
    """Get current aperture state."""
    mgr = _get_aperture_manager()
    return mgr.state.to_dict(mode=mgr.mode.value)


class ApertureSetRequest(BaseModel):
    mode: Optional[str] = None  # 'off', 'auto', or 'manual'
    preset: Optional[str] = None
    angle: Optional[int] = None
    focus_tags: Optional[list[str]] = None
    active_project: Optional[str] = None
    active_collection_keys: Optional[list[str]] = None


@app.post("/api/aperture")
async def api_aperture_set(req: ApertureSetRequest):
    """Set aperture mode and/or preset. Preset overrides angle."""
    from luna.context.aperture import AperturePreset
    mgr = _get_aperture_manager()

    if req.mode:
        mgr.set_mode(req.mode)

    if req.preset:
        try:
            mgr.set_preset(AperturePreset(req.preset))
        except ValueError:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Unknown preset: {req.preset}")
    elif req.angle is not None:
        mgr.set_angle(req.angle)

    if req.focus_tags is not None:
        mgr.set_focus_tags(req.focus_tags)
    if req.active_project is not None:
        mgr.set_active_project(req.active_project)
    if req.active_collection_keys is not None:
        mgr.set_active_collections(req.active_collection_keys)

    return mgr.state.to_dict(mode=mgr.mode.value)


@app.post("/api/aperture/reset")
async def api_aperture_reset():
    """Clear user override, revert to app default."""
    mgr = _get_aperture_manager()
    mgr.clear_override()
    return mgr.state.to_dict(mode=mgr.mode.value)


# --- Collection Lock-In Endpoints ---

@app.get("/api/collections/lock-in")
async def api_collections_lock_in():
    """Get lock-in scores for all tracked collections."""
    engine = await _get_lock_in_engine()
    if engine is None:
        return {"collections": [], "error": "Engine not ready"}

    from luna.substrate.collection_lock_in import PATTERN_FLOORS, COLLECTION_LOCK_IN_MIN
    try:
        import yaml
        from pathlib import Path as _Path
        _reg_path = config_dir() / "aibrarian_registry.yaml"
        _registry = yaml.safe_load(_reg_path.read_text()) if _reg_path.exists() else {}
        _col_configs = _registry.get("collections", {})
    except Exception:
        _col_configs = {}

    records = await engine.get_all()
    result = []
    for r in records:
        pattern = _col_configs.get(r.collection_key, {}).get("ingestion_pattern", "utilitarian")
        floor = PATTERN_FLOORS.get(pattern, COLLECTION_LOCK_IN_MIN)
        near_floor = r.lock_in < (floor + 0.05)
        result.append({
            "collection_key": r.collection_key,
            "lock_in": r.lock_in,
            "state": r.state,
            "access_count": r.access_count,
            "annotation_count": r.annotation_count,
            "connected_collections": r.connected_collections,
            "entity_overlap_count": r.entity_overlap_count,
            "last_accessed_at": r.last_accessed_at,
            "pattern": pattern,
            "floor": floor,
            "near_floor": near_floor,
        })
    return {"collections": result}


@app.get("/api/collections/{key}/lock-in")
async def api_collection_lock_in_detail(key: str):
    """Get lock-in detail for a single collection."""
    engine = await _get_lock_in_engine()
    if engine is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Engine not ready")
    record = await engine.get_lock_in(key)
    if record is None:
        return {"collection_key": key, "tracked": False}
    return {
        "collection_key": record.collection_key,
        "tracked": True,
        "lock_in": record.lock_in,
        "state": record.state,
        "access_count": record.access_count,
        "annotation_count": record.annotation_count,
        "connected_collections": record.connected_collections,
        "entity_overlap_count": record.entity_overlap_count,
        "last_accessed_at": record.last_accessed_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


# --- Annotation Endpoints ---

class AnnotationCreateRequest(BaseModel):
    collection_key: str
    doc_id: str
    annotation_type: str  # bookmark, note, flag
    content: Optional[str] = None
    chunk_index: Optional[int] = None
    original_text_preview: str = ""


@app.post("/api/annotations")
async def api_annotation_create(req: AnnotationCreateRequest):
    """Create an annotation on a collection document."""
    from luna.substrate.collection_annotations import AnnotationType
    engine = await _get_annotation_engine()
    if engine is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Engine not ready")

    try:
        ann_type = AnnotationType(req.annotation_type)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Unknown type: {req.annotation_type}")

    annotation_id = await engine.create(
        collection_key=req.collection_key,
        doc_id=req.doc_id,
        annotation_type=ann_type,
        content=req.content,
        chunk_index=req.chunk_index,
        original_text_preview=req.original_text_preview,
    )
    annotation = await engine.get(annotation_id)
    return {
        "annotation_id": annotation_id,
        "matrix_node_id": annotation.matrix_node_id if annotation else None,
    }


@app.get("/api/annotations")
async def api_annotations_list(collection: str, limit: int = 100):
    """List annotations for a collection."""
    engine = await _get_annotation_engine()
    if engine is None:
        return {"annotations": []}
    annotations = await engine.list_by_collection(collection, limit=limit)
    return {
        "annotations": [
            {
                "id": a.id,
                "collection_key": a.collection_key,
                "doc_id": a.doc_id,
                "chunk_index": a.chunk_index,
                "annotation_type": a.annotation_type,
                "content": a.content,
                "matrix_node_id": a.matrix_node_id,
                "created_at": a.created_at,
            }
            for a in annotations
        ]
    }


@app.get("/api/annotations/bridged")
async def api_annotations_bridged():
    """Get collections that have annotations creating Matrix nodes."""
    engine = await _get_annotation_engine()
    if engine is None:
        return {"bridged": []}
    return {"bridged": await engine.get_bridged_collections()}


@app.delete("/api/annotations/{annotation_id}")
async def api_annotation_delete(annotation_id: str):
    """Delete an annotation (Matrix node is preserved)."""
    engine = await _get_annotation_engine()
    if engine is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Engine not ready")
    deleted = await engine.delete(annotation_id)
    return {"deleted": deleted}


# ═══════════════════════════════════════════════════════════
# DEBUG: LAST INFERENCE SNAPSHOT
# ═══════════════════════════════════════════════════════════


@app.get("/debug/last-inference")
async def debug_last_inference():
    """
    Consolidated snapshot of the last inference for pipeline I/O diff.

    Pulls from director.get_last_system_prompt(), QA last report,
    and debug context — all in one call.
    """
    if not _engine:
        raise HTTPException(status_code=503, detail="Engine not started")

    result = {
        "query": None,
        "route": None,
        "provider": None,
        "latency_ms": None,
        "system_prompt_length": 0,
        "system_prompt_preview": "",
        "assembler": None,
        "context_items": [],
        "raw_response": None,
        "final_response": None,
        "narration_applied": False,
        "qa_passed": None,
        "qa_failures": [],
    }

    # Director prompt info
    director = _engine.get_actor("director")
    if director:
        prompt_info = director.get_last_system_prompt()
        if prompt_info.get("available"):
            result["system_prompt_length"] = prompt_info.get("length", 0)
            result["system_prompt_preview"] = prompt_info.get("preview", "")
            result["assembler"] = prompt_info.get("assembler")
            result["route"] = prompt_info.get("route_decision")

        # Get last response from director stats
        stats = director.get_stats() if hasattr(director, "get_stats") else {}
        agentic = stats.get("agentic", {})
        result["provider"] = agentic.get("last_provider") or stats.get("last_provider")

    # Context items
    try:
        context = _engine.context
        items = []
        for ring in context.rings:
            for item in context.rings[ring]:
                items.append({
                    "source": item.source.name,
                    "ring": item.ring.name,
                    "tokens": item.tokens,
                    "relevance": round(item.relevance, 3),
                })
        result["context_items"] = items
    except Exception:
        pass

    # QA last report
    if QA_AVAILABLE:
        try:
            validator = get_qa_validator()
            report = validator._last_report
            if report:
                result["query"] = report.query
                result["route"] = result["route"] or report.route
                result["provider"] = result["provider"] or report.provider_used
                result["latency_ms"] = report.latency_ms
                result["raw_response"] = getattr(report.context, "raw_response", None) if report.context else None
                result["final_response"] = getattr(report.context, "final_response", None) if report.context else None
                result["narration_applied"] = getattr(report.context, "narration_applied", False) if report.context else False
                result["qa_passed"] = report.passed
                result["qa_failures"] = [a.id for a in report.failed_assertions]
        except Exception:
            pass

    return result


# ═══════════════════════════════════════════════════════════
# QA: CHECK SINGLE ASSERTION
# ═══════════════════════════════════════════════════════════


class CheckAssertionRequest(BaseModel):
    """Request body for /qa/check-assertion."""
    assertion_id: str = Field(..., description="ID of the assertion to check")
    response_text: str = Field(..., description="Text to check against the assertion")


@app.post("/qa/check-assertion")
async def qa_check_assertion(request: CheckAssertionRequest):
    """
    Run a single assertion against provided text.

    Lightweight check — no LLM call, no pipeline execution.
    Builds a minimal InferenceContext and runs the assertion's check() method.
    """
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA system not available")

    validator = get_qa_validator()

    # Find the assertion
    target = None
    for a in validator._assertions:
        if a.id == request.assertion_id:
            target = a
            break

    if not target:
        raise HTTPException(status_code=404, detail=f"Assertion '{request.assertion_id}' not found")

    # Build minimal InferenceContext
    from datetime import datetime

    ctx = InferenceContext(
        inference_id="PLAYGROUND",
        session_id="playground",
        timestamp=datetime.now(),
        query="",
        route="PLAYGROUND",
        provider_used="playground",
        providers_tried=["playground"],
        provider_errors={},
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        personality_injected=True,
        personality_length=0,
        system_prompt="",
        virtues_loaded=True,
        narration_applied=False,
        raw_response=request.response_text,
        narrated_response=None,
        final_response=request.response_text,
    )

    result = target.check(ctx)

    return {
        "assertion_id": result.id,
        "name": result.name,
        "passed": result.passed,
        "severity": result.severity,
        "expected": result.expected,
        "actual": result.actual,
        "details": result.details,
    }


# ═══════════════════════════════════════════════════════════
# /api/diagnostics/* — MCP diagnostic control surface
# Thin routes that delegate to existing logic so MCP tools
# get live API data instead of DB fallback.
# ═══════════════════════════════════════════════════════════


@app.get("/api/diagnostics/last-inference")
async def api_diagnostics_last_inference():
    """Full diagnostic snapshot of the last inference (for MCP tools)."""
    return await debug_last_inference()


@app.get("/api/diagnostics/pipeline")
async def api_diagnostics_pipeline():
    """Pipeline state with QA overlay (for MCP tools)."""
    result = {"nodes": {}, "source": "api"}

    if _engine:
        for actor_name in ["director", "scout", "scribe", "librarian", "matrix", "reconcile"]:
            actor = _engine.get_actor(actor_name)
            if actor:
                result["nodes"][actor_name] = {
                    "active": True,
                    "type": type(actor).__name__,
                }

    if QA_AVAILABLE:
        validator = get_qa_validator()
        result["qa_health"] = validator.get_health()
        report = validator._last_report
        if report:
            result["last_qa"] = {
                "passed": report.passed,
                "failed_count": report.failed_count,
                "top_failures": [a.name for a in report.failed_assertions][:5],
                "timestamp": report.timestamp.isoformat(),
            }
        # Build node_status map from last QA report assertions
        node_status = {}
        if report:
            assertion_node_map = {
                "P1": "director", "P2": "director", "P3": "director",
                "V1": "director", "F1": "director", "F2": "director",
                "S1": "director", "S2": "director", "S3": "director", "S4": "director", "S5": "director",
                "I1": "matrix", "I2": "matrix", "I3": "matrix",
                "I4": "scribe", "E1": "scribe", "E2": "scribe",
            }
            for a in report.assertions:
                node = assertion_node_map.get(a.id, "director")
                if node not in node_status:
                    node_status[node] = "pass"
                if not a.passed:
                    node_status[node] = "fail" if a.severity in ("critical", "high") else "warn"
        result["node_status"] = node_status

    return result


@app.get("/api/director/lexicon")
async def api_director_lexicon():
    """Real-time Lexicon role status for the Director Console widget.

    Returns each role's current adapter name and implementation kind so
    the LEXICON tab can drop the hardcoded mock data.
    """
    try:
        from luna.lexicon.registry import get_registry
        from luna.lexicon import api as lexicon_api
        from luna.lexicon.pivot_config import PivotConfig, is_pivot_enabled

        registry = get_registry()
        cfg = PivotConfig.load()

        def _registry_note(role: str) -> str:
            entry = registry.get_role(role)
            if not entry:
                return "registry: missing"
            bits = [f"impl={entry.implementation or 'unset'}"]
            model = entry.metadata.get("model") or entry.metadata.get("target_model")
            if model:
                bits.append(f"model={model}")
            return ", ".join(bits)

        roles = []
        local_roles = [
            ("embed", lexicon_api._get_embed_adapter),
            ("rerank", lexicon_api._get_rerank_adapter),
            ("detect_language", lexicon_api._get_detect_language_adapter),
            ("classify_intent", lexicon_api._get_classify_intent_adapter),
            ("classify_safety", lexicon_api._get_classify_safety_adapter),
            ("ner", lexicon_api._get_ner_adapter),
        ]

        for role_name, getter in local_roles:
            try:
                adapter = getter()
                adapter_name = type(adapter).__name__
                state = "live"
                note = _registry_note(role_name)

                if role_name == "embed":
                    embedder = getattr(adapter, "_embedder", None)
                    if embedder is None:
                        state = "idle"
                    elif hasattr(embedder, "load_status"):
                        load_status = embedder.load_status()
                        if load_status.get("loaded"):
                            state = "ready"
                        elif load_status.get("cooldown_remaining_s", 0) > 0:
                            state = "cooldown"
                        else:
                            state = "idle"
                        if load_status.get("cooldown_remaining_s", 0) > 0:
                            note = f"{note}, retry_in={load_status['cooldown_remaining_s']}s"
                        if load_status.get("last_error"):
                            note = f"{note}, last_error={load_status['last_error']}"

                roles.append({
                    "role": role_name,
                    "family": "local",
                    "state": state,
                    "adapter": adapter_name,
                    "note": note,
                })
            except Exception as exc:
                roles.append({
                    "role": role_name,
                    "family": "local",
                    "state": "error",
                    "adapter": "unavailable",
                    "note": f"{type(exc).__name__}: {exc}",
                })

        try:
            remote_adapters = lexicon_api._get_remote_adapters()
            chain = " -> ".join(type(a).__name__ for a in remote_adapters) or "none"
            roles.append({
                "role": "generate",
                "family": "remote",
                "state": "ok",
                "adapter": chain,
                "note": _registry_note("generate"),
            })
            roles.append({
                "role": "curate",
                "family": "remote",
                "state": "ok",
                "adapter": chain,
                "note": _registry_note("curate"),
            })
        except Exception as exc:
            roles.append({
                "role": "generate",
                "family": "remote",
                "state": "error",
                "adapter": "unavailable",
                "note": f"{type(exc).__name__}: {exc}",
            })
            roles.append({
                "role": "curate",
                "family": "remote",
                "state": "error",
                "adapter": "unavailable",
                "note": f"{type(exc).__name__}: {exc}",
            })

        ops = {
            "mode": cfg.mode,
            "endpoints": list(cfg.endpoints),
            "message_enabled_in_config_mode": is_pivot_enabled("/message", mode=cfg.mode, config=cfg),
            "message_shadow_enabled": is_pivot_enabled("/message", mode="shadow", config=cfg),
            "message_active_enabled": is_pivot_enabled("/message", mode="active", config=cfg),
        }

        return {"roles": roles, "ops": ops, "ok": True}
    except Exception as exc:
        logger.warning("[director/lexicon] failed: %s", exc)
        return {"roles": [], "ops": None, "ok": False, "error": str(exc)}


@app.get("/api/director/traces")
async def api_director_traces(n: int = 20):
    """Recent shadow-pass traces for the Director Console live trace view.

    Each trace covers one ``run_shadow_pass_loop`` call and includes per-role
    step timing (classify_intent → classify_safety → ner → embed → generate).
    Returns at most ``n`` traces, newest first.
    """
    try:
        from luna.lexicon.shadow_loop import get_recent_traces
        traces = get_recent_traces(n=min(n, 50))
        return {"traces": traces, "ok": True}
    except Exception as exc:
        logger.warning("[director/traces] failed: %s", exc)
        return {"traces": [], "ok": False, "error": str(exc)}


class DirectorShadowTestRequest(BaseModel):
    """Request body for the Director Console local shadow probe."""
    query: str = Field(
        default="Director console live probe. Reply with one short sentence.",
        min_length=1,
        max_length=512,
    )


@app.post("/api/director/shadow-test")
async def api_director_shadow_test(request: DirectorShadowTestRequest):
    """Run one local Director/Lexicon shadow pass and return the newest trace.

    This is an operator-visible mechanics probe. It does not call the runtime
    generator and does not mutate the live response path.
    """
    try:
        from luna.lexicon.shadow_loop import get_recent_traces, run_shadow_pass_loop

        packet = run_shadow_pass_loop(request.query)
        traces = get_recent_traces(n=1)
        return {
            "packet": packet.to_dict(),
            "trace": traces[0] if traces else None,
            "ok": True,
        }
    except Exception as exc:
        logger.warning("[director/shadow-test] failed: %s", exc)
        return {"packet": None, "trace": None, "ok": False, "error": str(exc)}


@app.post("/api/director/quality-probe")
async def api_director_quality_probe():
    """Run the Director/Lexicon quality matrix and return scored diagnostics."""
    try:
        from luna.lexicon.quality_probe import run_quality_probe

        return run_quality_probe()
    except Exception as exc:
        logger.warning("[director/quality-probe] failed: %s", exc)
        return {"summary": None, "cases": [], "ok": False, "error": str(exc)}


@app.get("/api/diagnostics/health")
async def api_diagnostics_health():
    """Combined health check for MCP diagnostic tools."""
    result = {"engine": _engine is not None, "source": "api"}
    if _engine:
        status = _engine.status()
        result["uptime"] = status.get("uptime_seconds", 0)
        result["state"] = status.get("state", "unknown")
    if QA_AVAILABLE:
        validator = get_qa_validator()
        result["qa"] = validator.get_health()
    return result


class PromptPreviewRequest(BaseModel):
    """Request body for /api/diagnostics/trigger/prompt-preview."""
    message: str = "Hello"
    route_override: Optional[str] = None


@app.post("/api/diagnostics/trigger/prompt-preview")
async def api_diagnostics_prompt_preview(request: PromptPreviewRequest):
    """Build assembled prompt info without calling the LLM."""
    if not _engine:
        raise HTTPException(status_code=503, detail="Engine not started")

    director = _engine.get_actor("director")
    if not director:
        raise HTTPException(status_code=503, detail="Director not available")

    prompt_info = director.get_last_system_prompt()
    return {
        "message": request.message,
        "route_override": request.route_override,
        "available": prompt_info.get("available", False),
        "length": prompt_info.get("length", 0),
        "preview": prompt_info.get("preview", ""),
        "full_prompt": prompt_info.get("full_prompt"),
        "route_decision": prompt_info.get("route_decision"),
        "assembler": prompt_info.get("assembler"),
        "source": "api",
    }


class TestInferenceRequest(BaseModel):
    """Request body for /api/diagnostics/trigger/test-inference."""
    message: str
    route_override: Optional[str] = None
    narration_enabled: Optional[bool] = None
    extra_context: Optional[str] = None


@app.post("/api/diagnostics/trigger/test-inference")
async def api_diagnostics_test_inference(request: TestInferenceRequest):
    """Run a test message through the full pipeline with optional overrides."""
    import time as _time_mod

    if not _engine:
        raise HTTPException(status_code=503, detail="Engine not started")

    start = _time_mod.time()

    # Create a future to capture the response
    response_future: asyncio.Future = asyncio.Future()

    async def on_response(text: str, data: dict) -> None:
        if not response_future.done():
            response_future.set_result((text, data))

    _engine.on_response(on_response)

    try:
        # Apply one-shot overrides to director before queuing the message
        _director = _engine.get_actor("director")
        if _director and (request.route_override or request.narration_enabled is not None or request.extra_context):
            overrides = {}
            if request.route_override:
                overrides["force_route"] = request.route_override
            if request.narration_enabled is not None:
                overrides["narration_enabled"] = request.narration_enabled
            if request.extra_context:
                overrides["extra_context"] = request.extra_context
            _director.set_next_overrides(overrides)

        await _engine.send_message(request.message, source="diagnostic")

        text, data = await asyncio.wait_for(response_future, timeout=30.0)
        elapsed = (_time_mod.time() - start) * 1000

        director = _engine.get_actor("director")
        prompt_info = director.get_last_system_prompt() if director else {}

        # QA on the latest report (fire-and-forget already ran)
        qa_passed = None
        qa_failures = 0
        if QA_AVAILABLE:
            validator = get_qa_validator()
            report = validator._last_report
            if report and report.query == request.message:
                qa_passed = report.passed
                qa_failures = report.failed_count

        return {
            "message": request.message,
            "response": text,
            "route": data.get("route_decision", "unknown"),
            "route_reason": data.get("route_reason", ""),
            "latency_ms": elapsed,
            "qa_passed": qa_passed,
            "qa_failures": qa_failures,
            "prompt_length": prompt_info.get("length", 0) if prompt_info.get("available") else 0,
            "assembler": prompt_info.get("assembler") if prompt_info.get("available") else None,
            "overrides_applied": {
                "route": request.route_override,
                "narration": request.narration_enabled,
                "extra_context": request.extra_context is not None,
            },
            "source": "api",
        }
    except asyncio.TimeoutError:
        return {
            "message": request.message,
            "response": "Timeout",
            "latency_ms": 30000,
            "qa_passed": False,
            "qa_failures": 1,
            "source": "api",
            "error": "Timed out after 30s",
        }
    finally:
        if on_response in _engine._on_response_callbacks:
            _engine._on_response_callbacks.remove(on_response)


@app.post("/api/diagnostics/trigger/qa-sweep")
async def api_diagnostics_qa_sweep():
    """Re-validate the last inference against all assertions."""
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA not available")

    validator = get_qa_validator()
    report = validator.revalidate_last()
    if report is None:
        return {"error": "No previous inference to revalidate", "source": "api"}
    return {
        "passed": report.passed,
        "failed_count": report.failed_count,
        "diagnosis": report.diagnosis,
        "assertions": [
            {"id": a.id, "name": a.name, "passed": a.passed, "actual": a.actual}
            for a in report.assertions
        ],
        "source": "api",
    }


class RevalidateRequest(BaseModel):
    """Request body for /api/diagnostics/trigger/revalidate."""
    assertion_ids: list[str]


@app.post("/api/diagnostics/trigger/revalidate")
async def api_diagnostics_revalidate(request: RevalidateRequest):
    """Re-validate specific assertions against the last inference."""
    if not QA_AVAILABLE:
        raise HTTPException(status_code=503, detail="QA not available")

    validator = get_qa_validator()
    results = validator.revalidate_assertions(request.assertion_ids)
    return {"results": results, "source": "api"}


# ═══════════════════════════════════════════════════════════
# LunaScript diagnostic endpoints
# ═══════════════════════════════════════════════════════════


@app.get("/api/lunascript/state")
async def lunascript_state():
    """LunaScript cognitive signature diagnostics."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    director = _engine.get_actor("director")
    ls = getattr(director, '_lunascript', None) if director else None
    if not ls or not ls._initialized:
        return {"enabled": False}

    trait_values = {}
    if ls._last_measurement:
        trait_values = {
            name: round(score.value, 3)
            for name, score in ls._last_measurement.traits.items()
        }

    try:
        from luna.lunascript.signature import derive_glyph, DEFAULT_TRAIT_WEIGHTS
        glyph = derive_glyph({
            "position": ls._prev_position,
            "trait_vector": trait_values,
        }) if trait_values else "○"
    except Exception:
        glyph = "○"

    trends = ls._evolution.get_trait_trends()
    correlations = ls._evolution.get_trait_correlations()

    try:
        patterns = await ls.list_patterns()
    except Exception:
        patterns = []

    return {
        "enabled": True,
        "position": ls._prev_position,
        "glyph": glyph,
        "traits": trait_values,
        "trait_weights": {k: round(v, 3) for k, v in ls._trait_weights.items()},
        "default_weights": {k: round(v, 3) for k, v in DEFAULT_TRAIT_WEIGHTS.items()},
        "epsilon": round(ls._evolution.epsilon, 4),
        "iteration": ls._evolution._iteration,
        "drift_baseline": {
            "mean": round(ls._drift_baseline_mean, 3),
            "stddev": round(ls._drift_baseline_stddev, 3),
        },
        "trait_trends": {k: round(v, 4) for k, v in trends.items()},
        "trait_correlations": {k: round(v, 4) for k, v in correlations.items()},
        "patterns": patterns,
        "last_classification": ls._last_classification,
    }


@app.post("/api/lunascript/feedback")
async def lunascript_feedback(request: Request):
    """Record user feedback on response quality for LunaScript evolution."""
    import json, time
    body = await request.json()
    message_id = body.get("message_id", "")
    score = float(body.get("score", 0.5))

    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    director = _engine.get_actor("director")
    ls = getattr(director, '_lunascript', None) if director else None
    if not ls or not ls._initialized:
        return {"ok": False, "reason": "lunascript not initialized"}

    # Record feedback to dedicated table
    trait_vector_str = ""
    if ls._last_measurement:
        trait_vector_str = json.dumps({
            n: round(s.value, 3) for n, s in ls._last_measurement.traits.items()
        })

    try:
        await ls.db.execute(
            "INSERT INTO lunascript_feedback "
            "(message_id, score, trait_vector_at_time, classification_at_time, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, score, trait_vector_str, ls._last_classification, time.time()),
        )
    except Exception:
        pass

    # Feed into evolution if we have cached delegation data
    if ls._last_outbound_sig and ls._last_delta:
        try:
            ls._evolution.record_delegation(
                outbound=ls._last_outbound_sig,
                delta=ls._last_delta,
                classification=ls._last_classification,
                quality_score=score,
            )
            ls._trait_weights = ls._evolution.iterate_weights(ls._trait_weights)
            await ls._evolution.save_state(ls.db)
        except Exception:
            pass

    # Update last delegation_log entry's success_score
    try:
        await ls.db.execute(
            "UPDATE lunascript_delegation_log SET success_score = ? "
            "WHERE id = (SELECT MAX(id) FROM lunascript_delegation_log)",
            (score,),
        )
    except Exception:
        pass

    return {"ok": True, "epsilon": round(ls._evolution.epsilon, 4)}


# ============================================
# TOPOLOGY — manual attach of active thread
# ============================================

class TopologyAttachActiveThreadRequest(BaseModel):
    relationship: str = "member"


class TopologyAttachActiveThreadResponse(BaseModel):
    status: str
    cluster_id: Optional[str]
    thread_id: str
    active_thread_topic: Optional[str]
    existing_cluster_id: Optional[str] = None
    relationship: str = "member"


@app.post("/api/topology/clusters/{cluster_id}/attach-active-thread")
async def attach_active_thread_to_topology_cluster(
    cluster_id: str,
    request: TopologyAttachActiveThreadRequest = TopologyAttachActiveThreadRequest(),
):
    """Attach Librarian's current active thread to an existing topology cluster.

    Owner-only. Active-thread-only. Preserves the TopologyAttachmentService
    contract — ATTACHED / ALREADY_ATTACHED_SAME map to 200,
    ALREADY_ATTACHED_OTHER to 409, MISSING_CLUSTER to 404.
    MISSING_THREAD is treated as an invariant breach (500) because the
    thread_id came from Librarian's live active_thread.
    """
    await _require_admin()

    if not _engine:
        raise HTTPException(status_code=503, detail="Engine not ready")

    librarian = _engine.get_actor("librarian")
    if librarian is None:
        raise HTTPException(status_code=503, detail="Librarian not available")

    matrix = _engine.get_actor("matrix")
    if not matrix or not getattr(matrix, "_db", None):
        raise HTTPException(status_code=503, detail="Memory not available")

    active_thread = librarian.get_active_thread()
    if active_thread is None:
        logger.info(
            "[TOPOLOGY] attach-active-thread status=no_active_thread "
            f"cluster={cluster_id} thread=none existing=none"
        )
        return JSONResponse(
            status_code=409,
            content={
                "status": "no_active_thread",
                "cluster_id": cluster_id,
                "thread_id": "",
                "active_thread_topic": None,
                "existing_cluster_id": None,
                "relationship": request.relationship,
            },
        )

    svc = TopologyAttachmentService(matrix._db)
    result = await svc.attach_thread_manual(
        cluster_id, active_thread.id, request.relationship
    )

    body = {
        "status": result.status.value,
        "cluster_id": result.cluster_id,
        "thread_id": result.thread_id,
        "active_thread_topic": getattr(active_thread, "topic", None),
        "existing_cluster_id": result.existing_cluster_id,
        "relationship": result.relationship,
    }

    logger.info(
        f"[TOPOLOGY] attach-active-thread status={result.status.value} "
        f"cluster={cluster_id} thread={result.thread_id} "
        f"existing={result.existing_cluster_id or 'none'}"
    )

    if result.status == ThreadAttachmentStatus.ATTACHED:
        return body
    if result.status == ThreadAttachmentStatus.ALREADY_ATTACHED_SAME:
        return body
    if result.status == ThreadAttachmentStatus.ALREADY_ATTACHED_OTHER:
        return JSONResponse(status_code=409, content=body)
    if result.status == ThreadAttachmentStatus.MISSING_CLUSTER:
        return JSONResponse(status_code=404, content=body)
    if result.status == ThreadAttachmentStatus.MISSING_THREAD:
        logger.error(
            "[TOPOLOGY] invariant breach: librarian active thread "
            f"{result.thread_id!r} not present in canonical threads table"
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "invariant breach: active thread not present in "
                "canonical threads table"
            ),
        )

    raise HTTPException(
        status_code=500,
        detail=f"unhandled attachment status: {result.status.value}",
    )


# ═══════════════════════════════════════════════════════════
# Serve main Eclissi frontend at / (MUST be last — catch-all mount)
try:
    _eclissi_frontend = Path("frontend/dist")
    if _eclissi_frontend.exists():
        app.mount("/", StaticFiles(directory=str(_eclissi_frontend), html=True), name="eclissi-frontend")
        logger.info(f"Eclissi frontend mounted at / from {_eclissi_frontend}")
except Exception as e:
    logger.warning(f"Eclissi frontend mount failed: {e}")


def create_app() -> FastAPI:
    """Factory function for creating the app."""
    return app
