"""
Forge Watcher — live file-system monitor for Knowledge Forge collections.

Uses ``watchdog`` to observe mapped source folders.  File events are queued
and drained on the engine's reflective tick (minutes interval) to avoid
SQLite lock contention during active conversations.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from luna.substrate.aibrarian_engine import AiBrarianEngine

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".md", ".txt", ".csv", ".pdf", ".docx", ".json", ".yaml", ".yml"}

# Directories to ignore inside watched trees
_EXCLUDED_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".env", ".tox"}


def _is_excluded(path: str) -> bool:
    parts = Path(path).parts
    return any(p in _EXCLUDED_DIRS or p.startswith(".") for p in parts)


# ---------------------------------------------------------------------------
# watchdog event handler
# ---------------------------------------------------------------------------

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    class ForgeEventHandler(FileSystemEventHandler):
        """Translates FS events into queue entries."""

        def __init__(self, queue: asyncio.Queue, collection_key: str):
            self.queue = queue
            self.collection_key = collection_key

        def _should_process(self, path: str) -> bool:
            if _is_excluded(path):
                return False
            return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS

        def on_created(self, event):
            if not event.is_directory and self._should_process(event.src_path):
                self.queue.put_nowait(("created", self.collection_key, event.src_path))

        def on_modified(self, event):
            if not event.is_directory and self._should_process(event.src_path):
                self.queue.put_nowait(("modified", self.collection_key, event.src_path))

        def on_deleted(self, event):
            if not event.is_directory and self._should_process(event.src_path):
                self.queue.put_nowait(("deleted", self.collection_key, event.src_path))

    _WATCHDOG_AVAILABLE = True

except ImportError:
    _WATCHDOG_AVAILABLE = False
    Observer = None  # type: ignore[misc,assignment]
    ForgeEventHandler = None  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# ForgeWatcher
# ---------------------------------------------------------------------------


class ForgeWatcher:
    """Manages file watchers for all mapped source folders."""

    def __init__(self, aibrarian: AiBrarianEngine):
        self.aibrarian = aibrarian
        self.queue: asyncio.Queue = asyncio.Queue()
        self._observer: Observer | None = None  # type: ignore[assignment]
        self._watches: dict[str, object] = {}  # collection_key -> watch handle
        self._running = False

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Start watching all collections that have source_dir + watch=True."""
        if not _WATCHDOG_AVAILABLE:
            logger.warning("[FORGE-WATCHER] watchdog not installed — watcher disabled")
            return

        self._observer = Observer()

        for key, cfg in self.aibrarian.registry.collections.items():
            if cfg.source_dir and cfg.watch and cfg.enabled:
                self.watch(key, cfg.source_dir)

        self._observer.start()
        self._running = True
        logger.info("[FORGE-WATCHER] Started with %d watched folders", len(self._watches))

    def stop(self) -> None:
        """Stop all watchers."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
        self._running = False
        logger.info("[FORGE-WATCHER] Stopped")

    # -- folder management ---------------------------------------------------

    def watch(self, collection_key: str, source_dir: str) -> bool:
        """Add a folder to the watch list."""
        if not _WATCHDOG_AVAILABLE or self._observer is None:
            logger.warning("[FORGE-WATCHER] watchdog not available")
            return False

        path = Path(source_dir).expanduser().resolve()
        if not path.exists():
            logger.warning("[FORGE-WATCHER] Source dir does not exist: %s", path)
            return False

        if collection_key in self._watches:
            return True  # already watching

        handler = ForgeEventHandler(self.queue, collection_key)
        watch_handle = self._observer.schedule(handler, str(path), recursive=True)
        self._watches[collection_key] = watch_handle
        logger.info("[FORGE-WATCHER] Watching %s → %s", collection_key, path)
        return True

    def unwatch(self, collection_key: str) -> None:
        """Remove a folder from the watch list."""
        watch_handle = self._watches.pop(collection_key, None)
        if watch_handle and self._observer is not None:
            self._observer.unschedule(watch_handle)
            logger.info("[FORGE-WATCHER] Unwatched %s", collection_key)

    @property
    def watched_collections(self) -> list[str]:
        return list(self._watches.keys())

    # -- event processing (called from engine reflective tick) ---------------

    async def drain_queue(self) -> list[tuple]:
        """Drain the event queue. Non-blocking."""
        events = []
        while not self.queue.empty():
            try:
                events.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    async def process_events(self, events: list[tuple]) -> dict:
        """Process queued file events via the ingest pipeline.

        Returns a summary dict: {ingested: int, deleted: int, skipped: int, errors: int}
        """
        summary = {"ingested": 0, "deleted": 0, "skipped": 0, "errors": 0}

        for event_type, collection_key, file_path_str in events:
            file_path = Path(file_path_str)
            try:
                if event_type in ("created", "modified"):
                    status = await self.aibrarian._check_sync_status(collection_key, file_path)
                    if status == "unchanged":
                        summary["skipped"] += 1
                        continue

                    if status == "modified":
                        # Delete old doc before re-ingest
                        conn = self.aibrarian._get_conn(collection_key)
                        row = conn.conn.execute(
                            "SELECT doc_id FROM forge_sync WHERE file_path = ?",
                            (str(file_path),),
                        ).fetchone()
                        if row and row["doc_id"]:
                            await self.aibrarian._delete_document(collection_key, row["doc_id"])

                    doc_id = await self.aibrarian.ingest(
                        collection_key, file_path,
                        metadata={"title": file_path.stem},
                    )
                    if doc_id:
                        await self.aibrarian._update_sync(collection_key, file_path, doc_id)
                        summary["ingested"] += 1
                        logger.info("[FORGE-WATCHER] Ingested %s → %s", file_path.name, collection_key)
                    else:
                        summary["skipped"] += 1

                elif event_type == "deleted":
                    conn = self.aibrarian._get_conn(collection_key)
                    conn.conn.execute(
                        "UPDATE forge_sync SET status = 'deleted' WHERE file_path = ?",
                        (str(file_path),),
                    )
                    conn.conn.commit()
                    summary["deleted"] += 1
                    logger.info("[FORGE-WATCHER] Marked deleted: %s", file_path.name)

            except Exception as e:
                summary["errors"] += 1
                logger.warning("[FORGE-WATCHER] Failed to process %s: %s", file_path.name, e)

        return summary
