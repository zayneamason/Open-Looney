"""
Identity Actor — FaceID Integration
=====================================

Runs face recognition in a background loop and emits identity events
into the engine. When a face is recognized, Luna knows who she's
talking to (name, tier, permissions). When the face disappears,
she knows she's alone.

The heavy lifting (FaceNet, MTCNN, camera) is imported lazily from
Tools/FaceID so the engine doesn't require torch just to boot.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any, TYPE_CHECKING

from luna.actors.base import Actor, Message
from luna.core.events import InputEvent, EventType, EventPriority
from luna.core.paths import tools_dir

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Path to FaceID tool relative to project root
FACEID_ROOT = tools_dir() / "FaceID"
FACEID_DB = FACEID_ROOT / "data" / "faces.db"


@dataclass
class CurrentIdentity:
    """Who Luna currently sees."""
    entity_id: Optional[str] = None
    entity_name: Optional[str] = None
    confidence: float = 0.0
    luna_tier: str = "unknown"
    dataroom_tier: int = 5
    dataroom_categories: list = field(default_factory=list)
    last_seen: float = 0.0

    @property
    def is_present(self) -> bool:
        return self.entity_id is not None and (time.time() - self.last_seen) < 10.0

    def clear(self):
        self.entity_id = None
        self.entity_name = None
        self.confidence = 0.0
        self.luna_tier = "unknown"
        self.dataroom_tier = 5
        self.dataroom_categories = []
        self.last_seen = 0.0


class IdentityActor(Actor):
    """
    Face recognition actor.

    Runs a background loop that:
    1. Captures camera frames (non-blocking, in executor)
    2. Detects + encodes faces via FaceNet
    3. Matches against enrolled embeddings
    4. Emits IDENTITY_RECOGNIZED / IDENTITY_LOST events

    The camera + torch pipeline runs in a thread executor so it
    never blocks the engine's async event loop.
    """

    # How often to run recognition (seconds)
    RECOGNITION_INTERVAL = 2.0

    # How long without a face before emitting IDENTITY_LOST
    PRESENCE_TIMEOUT = 4.0

    def __init__(self, enabled: bool = True):
        super().__init__(name="identity")
        self.enabled = enabled
        self.current = CurrentIdentity()
        self._recognition_task: Optional[asyncio.Task] = None

        # Lazily loaded FaceID components
        self._encoder: Any = None
        self._matcher: Any = None
        self._camera: Any = None
        self._db: Any = None
        self._initialized = False

        # Subscribers notified on identity state changes
        self._on_change_callbacks: list = []

    @property
    def is_ready(self) -> bool:
        return self._initialized and self.enabled

    def on_change(self, callback) -> None:
        """Subscribe to identity state changes. Callback receives (current: CurrentIdentity)."""
        self._on_change_callbacks.append(callback)

    async def _notify_change(self) -> None:
        """Fire all on_change callbacks."""
        for cb in self._on_change_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(self.current)
                else:
                    cb(self.current)
            except Exception as e:
                logger.error(f"Identity change callback error: {e}")

    async def on_start(self) -> None:
        """Start the background recognition loop."""
        if not self.enabled:
            logger.info("IdentityActor disabled, skipping")
            return

        # Initialize FaceID components in executor (heavy imports)
        loop = asyncio.get_event_loop()
        try:
            self._initialized = await loop.run_in_executor(None, self._init_faceid)
        except Exception as e:
            logger.warning(f"FaceID initialization failed (non-fatal): {e}")
            self._initialized = False
            return

        if self._initialized:
            self._recognition_task = asyncio.create_task(
                self._recognition_loop(),
                name="identity-recognition",
            )
            # Sync bridge entries from faces.db → engine DB at startup
            asyncio.create_task(self._sync_all_bridges())
            logger.info("IdentityActor started — recognition loop active")

    def _init_faceid(self) -> bool:
        """Initialize FaceID components (runs in thread executor)."""
        import sys

        # Add FaceID to path
        faceid_src = str(tools_dir() / "FaceID")
        if faceid_src not in sys.path:
            sys.path.insert(0, faceid_src)

        try:
            from src.encoder import FaceEncoder
            from src.database import FaceDatabase
            from src.matcher import IdentityMatcher
            from src.camera import Camera

            self._encoder = FaceEncoder()
            self._db = FaceDatabase(FACEID_DB)
            self._db.connect()
            self._matcher = IdentityMatcher(self._db)
            self._camera = Camera()

            if not self._camera.open():
                logger.warning("FaceID: Camera could not be opened")
                self._db.close()
                return False

            logger.info(
                f"FaceID initialized: model={self._encoder.model_name}, "
                f"db={FACEID_DB}, "
                f"entities={len(self._db.list_entities())}"
            )
            return True

        except ImportError as e:
            logger.warning(f"FaceID dependencies not available: {e}")
            return False
        except Exception as e:
            logger.warning(f"FaceID init error: {e}")
            return False

    def _init_faceid_headless(self) -> bool:
        """Initialize encoder + matcher without camera (for browser-sourced frames).

        Uses importlib to load modules directly, bypassing __init__.py
        which imports Camera → cv2 (not available in main venv).
        """
        import importlib.util

        def _load_module(name, path):
            spec = importlib.util.spec_from_file_location(name, str(path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        try:
            src_dir = FACEID_ROOT / "src"

            # Load modules in dependency order (no relative imports)
            db_mod = _load_module("faceid_database", src_dir / "database.py")
            enc_mod = _load_module("faceid_encoder", src_dir / "encoder.py")

            # Patch matcher's expected imports before loading it
            import sys
            sys.modules["faceid_database"] = db_mod
            sys.modules["faceid_encoder"] = enc_mod

            # matcher.py uses relative imports — load it with src as a fake package
            # Instead, read and exec with patched globals
            matcher_path = src_dir / "matcher.py"
            matcher_source = matcher_path.read_text()
            # Replace relative imports with our loaded modules
            matcher_source = matcher_source.replace(
                "from .database import FaceDatabase, StoredFace, AccessBridge",
                ""
            ).replace(
                "from .encoder import FaceDetection",
                ""
            )
            matcher_ns = {
                "__builtins__": __builtins__,
                "FaceDatabase": db_mod.FaceDatabase,
                "StoredFace": db_mod.StoredFace,
                "AccessBridge": db_mod.AccessBridge,
                "FaceDetection": enc_mod.FaceDetection,
            }
            exec(compile(matcher_source, str(matcher_path), "exec"), matcher_ns)

            FaceEncoder = enc_mod.FaceEncoder
            FaceDatabase = db_mod.FaceDatabase
            IdentityMatcher = matcher_ns["IdentityMatcher"]

            self._encoder = FaceEncoder()
            self._db = FaceDatabase(FACEID_DB)
            self._db.connect()
            self._matcher = IdentityMatcher(self._db)

            logger.info(
                f"FaceID initialized (headless): model={self._encoder.model_name}, "
                f"db={FACEID_DB}, "
                f"entities={len(self._db.list_entities())}"
            )
            return True

        except ImportError as e:
            logger.warning(f"FaceID dependencies not available: {e}")
            return False
        except Exception as e:
            logger.warning(f"FaceID headless init error: {e}")
            return False

    async def _recognition_loop(self) -> None:
        """Background loop: capture → detect → match → emit events."""
        loop = asyncio.get_event_loop()
        logger.debug("Recognition loop started")

        while self._running and self._initialized:
            try:
                # Run the heavy CV pipeline in a thread
                result = await loop.run_in_executor(None, self._recognize_once)

                if result is not None:
                    await self._handle_recognition(result)
                else:
                    await self._handle_no_face()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Recognition loop error: {e}")

            await asyncio.sleep(self.RECOGNITION_INTERVAL)

        logger.debug("Recognition loop stopped")

    def _recognize_once(self) -> Optional[dict]:
        """
        Single recognition pass (runs in thread executor).

        Returns dict with match info, or None if no face detected.
        """
        if not self._camera or not self._encoder or not self._matcher:
            return None

        frame = self._camera.capture()
        if frame is None:
            return None

        detections = self._encoder.detect_faces(frame.image)
        if not detections:
            return None

        # Match the best (highest confidence) detection
        result = self._matcher.match_best_of_n(detections)

        if result.is_known:
            return {
                "entity_id": result.entity_id,
                "entity_name": result.entity_name,
                "confidence": result.confidence,
                "luna_tier": result.luna_tier,
                "dataroom_tier": result.dataroom_tier,
                "dataroom_categories": result.dataroom_categories,
            }

        return None

    async def recognize_from_frame(self, frame) -> Optional[dict]:
        """
        Run recognition on an externally-provided frame (e.g., browser camera).
        Initializes encoder/matcher headlessly if not already done.
        Returns match dict or None.
        """
        # Lazy headless init if the camera-based init wasn't run
        if not self._encoder or not self._matcher:
            loop = asyncio.get_event_loop()
            ok = await loop.run_in_executor(None, self._init_faceid_headless)
            if not ok:
                return None
            self._initialized = True

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, self._recognize_frame_sync, frame
        )

        if result is not None:
            await self._handle_recognition(result)

        return result

    def _recognize_frame_sync(self, frame) -> Optional[dict]:
        """Synchronous face detection + matching on a raw frame (numpy array)."""
        detections = self._encoder.detect_faces(frame)
        if not detections:
            return None

        result = self._matcher.match_best_of_n(detections)

        # Build bbox list from all detections for frontend overlay
        bboxes = []
        for det in detections:
            x, y, w, h = det.bbox
            if w > 0 and h > 0:
                bboxes.append({
                    "x": x, "y": y, "w": w, "h": h,
                    "confidence": round(det.confidence, 3),
                })

        if result.is_known:
            return {
                "entity_id": result.entity_id,
                "entity_name": result.entity_name,
                "confidence": result.confidence,
                "luna_tier": result.luna_tier,
                "dataroom_tier": result.dataroom_tier,
                "dataroom_categories": result.dataroom_categories,
                "bboxes": bboxes,
            }
        # Return bboxes even when no match so frontend can draw them
        return {"bboxes": bboxes, "_no_match": True}

    async def enroll_from_frame(self, frame, entity_name: str, entity_id: str = None,
                                luna_tier: str = "guest", dataroom_tier: int = 5,
                                dataroom_categories: list = None) -> dict:
        """
        Enroll a face from a browser-provided frame.
        Detects face, extracts embedding, stores in DB.
        Returns {"enrolled": True, "confidence": ..., "count": ...} or error dict.
        """
        # Lazy headless init
        if not self._encoder or not self._db:
            loop = asyncio.get_event_loop()
            ok = await loop.run_in_executor(None, self._init_faceid_headless)
            if not ok:
                return {"enrolled": False, "error": "FaceID initialization failed"}

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, self._enroll_frame_sync, frame, entity_name, entity_id,
            luna_tier, dataroom_tier, dataroom_categories or []
        )

        # Sync bridge entry to engine DB so permission filter works
        if result.get("enrolled"):
            await self._sync_bridge_to_engine(
                result.get("entity_id", entity_id or ""),
                entity_name, luna_tier, dataroom_tier,
                dataroom_categories or [],
            )

        return result

    def _enroll_frame_sync(self, frame, entity_name: str, entity_id: str,
                           luna_tier: str, dataroom_tier: int,
                           dataroom_categories: list) -> dict:
        """Synchronous enrollment from a single frame."""
        detections = self._encoder.detect_faces(frame)
        if not detections:
            return {"enrolled": False, "error": "No face detected", "bboxes": []}

        # Use the best detection
        best = max(detections, key=lambda d: d.confidence)
        if best.confidence < 0.5:
            return {"enrolled": False, "error": "Face confidence too low",
                    "confidence": round(best.confidence, 3)}

        # Generate entity_id if not provided
        if not entity_id:
            import hashlib
            hash_val = hashlib.md5(entity_name.encode()).hexdigest()[:8]
            entity_id = f"entity_{hash_val}"

        # Store embedding
        self._db.store_embedding(
            entity_id=entity_id,
            entity_name=entity_name,
            embedding=best.embedding,
            model_name=self._encoder.model_name,
            quality=best.confidence,
            context="browser_enrollment",
        )

        # Ensure access bridge exists
        self._db.set_access(
            entity_id=entity_id,
            entity_name=entity_name,
            luna_tier=luna_tier,
            dataroom_tier=dataroom_tier,
            dataroom_categories=dataroom_categories,
            set_by="browser",
        )

        # Invalidate matcher cache so it picks up new embeddings
        self._matcher.invalidate_cache()

        count = self._db.count_embeddings(entity_id)

        # Build bboxes for frontend
        bboxes = []
        for det in detections:
            x, y, w, h = det.bbox
            if w > 0 and h > 0:
                bboxes.append({"x": x, "y": y, "w": w, "h": h,
                               "confidence": round(det.confidence, 3)})

        return {
            "enrolled": True,
            "entity_id": entity_id,
            "entity_name": entity_name,
            "confidence": round(best.confidence, 3),
            "count": count,
            "bboxes": bboxes,
        }

    # ── Bridge Sync (faces.db → engine DB) ─────────────────────────────

    async def _sync_bridge_to_engine(self, entity_id: str, entity_name: str,
                                      luna_tier: str, dataroom_tier: int,
                                      categories: list) -> None:
        """Sync a single bridge entry from faces.db to the engine DB."""
        if not self.engine:
            return
        matrix_actor = self.engine.get_actor("matrix")
        if not matrix_actor or not matrix_actor.is_ready:
            return
        try:
            from luna.identity.bridge import AccessBridge
            bridge = AccessBridge(matrix_actor._matrix.db)
            await bridge.ensure_entry(
                entity_id=entity_id,
                luna_tier=luna_tier,
                dataroom_tier=dataroom_tier,
                categories=categories,
                set_by="faceid_sync",
            )
            logger.info("Bridge synced to engine DB: %s (%s/T%d)", entity_id, luna_tier, dataroom_tier)
        except Exception as e:
            logger.error("Bridge sync failed for %s: %s", entity_id, e)

    async def _sync_all_bridges(self) -> None:
        """Sync all bridge entries from faces.db to engine DB. Called at startup."""
        if not self._db:
            return
        import json as _json
        entities = self._db.list_entities()
        for entity in entities:
            cats = entity.get("dataroom_categories", "[]")
            if isinstance(cats, str):
                try:
                    cats = _json.loads(cats)
                except (ValueError, TypeError):
                    cats = []
            await self._sync_bridge_to_engine(
                entity["entity_id"],
                entity.get("entity_name", ""),
                entity.get("luna_tier", "unknown"),
                entity.get("dataroom_tier", 5),
                cats,
            )
        if entities:
            logger.info("Startup bridge sync: %d entities synced", len(entities))

    async def _handle_recognition(self, match: dict) -> None:
        """Handle a successful face match."""
        entity_id = match["entity_id"]
        was_present = self.current.is_present
        was_same = self.current.entity_id == entity_id

        # Update current identity
        self.current.entity_id = entity_id
        self.current.entity_name = match["entity_name"]
        self.current.confidence = match["confidence"]
        self.current.luna_tier = match["luna_tier"]
        self.current.dataroom_tier = match["dataroom_tier"]
        self.current.dataroom_categories = match["dataroom_categories"]
        self.current.last_seen = time.time()

        # Only emit event on new recognition (not every frame)
        if not was_present or not was_same:
            logger.info(
                f"Identity recognized: {match['entity_name']} "
                f"(confidence={match['confidence']:.3f}, tier={match['luna_tier']})"
            )
            await self._emit_identity_event(EventType.IDENTITY_RECOGNIZED, match)
            await self._notify_change()

    async def _handle_no_face(self) -> None:
        """Handle no face detected — check for presence timeout."""
        if self.current.is_present:
            elapsed = time.time() - self.current.last_seen
            if elapsed > self.PRESENCE_TIMEOUT:
                name = self.current.entity_name
                logger.info(f"Identity lost: {name} (absent for {elapsed:.0f}s)")
                lost_data = {
                    "entity_id": self.current.entity_id,
                    "entity_name": name,
                }
                self.current.clear()
                await self._emit_identity_event(EventType.IDENTITY_LOST, lost_data)
                await self._notify_change()

    async def _emit_identity_event(self, event_type: EventType, payload: dict) -> None:
        """Push an identity event into the engine's input buffer."""
        if not self.engine:
            return

        event = InputEvent(
            type=event_type,
            payload=payload,
            priority=EventPriority.INTERNAL,
            source="identity",
        )
        await self.engine.input_buffer.put(event)

    async def handle(self, msg: Message) -> None:
        """Handle mailbox messages."""
        match msg.type:
            case "start_recognition":
                if not self._initialized:
                    loop = asyncio.get_event_loop()
                    self._initialized = await loop.run_in_executor(
                        None, self._init_faceid
                    )
                if self._initialized and not self._recognition_task:
                    self._recognition_task = asyncio.create_task(
                        self._recognition_loop(),
                        name="identity-recognition",
                    )

            case "stop_recognition":
                if self._recognition_task:
                    self._recognition_task.cancel()
                    self._recognition_task = None
                self.current.clear()

    async def on_stop(self) -> None:
        """Clean up camera and database on shutdown."""
        if self._recognition_task:
            self._recognition_task.cancel()
            try:
                await self._recognition_task
            except asyncio.CancelledError:
                pass

        # Close in executor (camera release can block)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._cleanup)

    def _cleanup(self) -> None:
        """Release resources (runs in thread executor)."""
        if self._camera:
            self._camera.close()
            self._camera = None
        if self._db:
            self._db.close()
            self._db = None
        logger.info("IdentityActor: resources released")

    def get_identity_context(self) -> str:
        """
        Build identity context string for system prompt injection.

        Called by engine._build_system_prompt() to tell Luna who she's
        talking to.
        """
        if not self.current.is_present:
            return ""

        return (
            f"\n## Current Speaker (FaceID)\n"
            f"You are currently speaking with **{self.current.entity_name}**.\n"
            f"- Luna tier: {self.current.luna_tier}\n"
            f"- Data room tier: {self.current.dataroom_tier}\n"
            f"- Confidence: {self.current.confidence:.2f}\n"
            f"- Categories: {self.current.dataroom_categories}\n"
            f"\nAddress them by name naturally. Adjust your behavior based on their tier.\n"
        )
