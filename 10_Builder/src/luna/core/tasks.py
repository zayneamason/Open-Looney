"""Canonical TaskManager persistence.

Slice 1 of the TaskManager / Board / Guardian architecture. See
Docs/bible/Handoffs/HANDOFF_TASKMANAGER_BOARD_THREADS_AND_GUARDIAN_ARCHITECTURE.md.

Backed by the canonical `tasks`, `task_dependencies`, `task_runs`,
`threads`, and `thread_tasks` tables in `data/user/luna_engine.db`. Threads
persistence is owned by Librarian (Slice 2); this module only reads
existing thread rows when linking tasks.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from luna.substrate.database import MemoryDatabase

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Task lifecycle states. See handoff §Status Vocabularies."""

    INBOX = "inbox"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


_TERMINAL_STATES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.ARCHIVED}

_ACTIVE_RUN_STATUS = "running"


@dataclass
class Task:
    """A canonical task row, hydrated from the `tasks` table."""

    id: str
    title: str
    description: Optional[str] = None
    kind: str = "conversation_born_task"
    status: str = TaskStatus.INBOX.value
    priority: int = 5
    owner: Optional[str] = None
    source: Optional[str] = None
    blocked_reason: Optional[str] = None
    result: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    due_at: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _row_to_task(row: Sequence[Any], deps: Optional[List[str]] = None) -> Task:
    """Map a row from the tasks table into a Task dataclass.

    Expected column order matches `_TASK_COLUMNS`.
    """
    result = None
    if row[9]:
        try:
            result = json.loads(row[9])
        except json.JSONDecodeError:
            result = row[9]
    metadata: Dict[str, Any] = {}
    if row[11]:
        try:
            metadata = json.loads(row[11]) or {}
        except json.JSONDecodeError:
            metadata = {}
    return Task(
        id=row[0],
        title=row[1],
        description=row[2],
        kind=row[3],
        status=row[4],
        priority=row[5],
        owner=row[6],
        source=row[7],
        blocked_reason=row[8],
        result=result,
        error=row[10],
        metadata=metadata,
        created_at=row[12],
        updated_at=row[13],
        started_at=row[14],
        completed_at=row[15],
        due_at=row[16],
        dependencies=list(deps or []),
    )


_TASK_COLUMNS = (
    "id, title, description, kind, status, priority, owner, source, "
    "blocked_reason, result_json, error, metadata_json, "
    "created_at, updated_at, started_at, completed_at, due_at"
)


def _generate_task_id() -> str:
    """task_YYYYMMDD_HHMMSS_xxxx — matches the prior YAML bridge format."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = secrets.token_hex(2)
    return f"task_{ts}_{suffix}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class TaskManager:
    """Canonical work layer, persisted in SQLite.

    Construct with a live `MemoryDatabase` handle. All methods are async
    and issue individual statements — no long-running transactions are
    held so the shared WAL connection stays available to other actors.
    """

    def __init__(self, db: MemoryDatabase, max_concurrent: int = 5) -> None:
        self.db = db
        self.max_concurrent = max_concurrent

    # ──────────────────────────────────────────────────────────────── create

    async def create(
        self,
        title: str,
        description: Optional[str] = None,
        *,
        kind: str = "conversation_born_task",
        priority: int = 5,
        status: str = TaskStatus.INBOX.value,
        owner: Optional[str] = None,
        source: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        dependencies: Optional[List[str]] = None,
        thread_id: Optional[str] = None,
        due_at: Optional[str] = None,
        task_id: Optional[str] = None,
        entities: Optional[List[str]] = None,
        subjects: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        files: Optional[List[str]] = None,
        memory_links: Optional[List[str]] = None,
    ) -> Task:
        """Insert a new task. Optionally links to a thread and registers deps.

        Thread linkage writes to `thread_tasks`. If the referenced thread row
        does not exist yet, the linkage row is skipped (not an error) — the
        thread table is populated by Librarian in Slice 2.

        Mapping kwargs (`entities`, `subjects`, `keywords`, `files`,
        `memory_links`) write rows into the canonical mapping tables. Entity
        strings go into `task_entities.entity_text` (resolution is deferred).
        """
        tid = task_id or _generate_task_id()
        priority = max(1, min(10, int(priority)))
        meta_json = json.dumps(metadata) if metadata else None

        await self.db.execute(
            """
            INSERT INTO tasks (
                id, title, description, kind, status, priority, owner, source,
                metadata_json, due_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tid, title, description, kind, status, priority, owner, source, meta_json, due_at),
        )

        if dependencies:
            await self.db.executemany(
                "INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id) VALUES (?, ?)",
                [(tid, dep) for dep in dependencies],
            )

        if thread_id:
            # Only link if the thread row exists. Missing rows are normal in
            # Slice 1 (threads table is empty until Slice 2 backfills).
            existing = await self.db.fetchone(
                "SELECT id FROM threads WHERE id = ?", (thread_id,)
            )
            if existing is not None:
                await self.db.execute(
                    "INSERT OR IGNORE INTO thread_tasks (thread_id, task_id) VALUES (?, ?)",
                    (thread_id, tid),
                )

        if entities or subjects or keywords or files or memory_links:
            await self.set_mappings(
                tid,
                entities=entities,
                subjects=subjects,
                keywords=keywords,
                files=files,
                memory_links=memory_links,
                source="create",
            )

        task = await self.get(tid)
        assert task is not None  # just inserted
        return task

    # ─────────────────────────────────────────────────────────────── reads

    async def get(self, task_id: str) -> Optional[Task]:
        row = await self.db.fetchone(
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = ?", (task_id,)
        )
        if row is None:
            return None
        deps = await self._load_dependencies(task_id)
        return _row_to_task(row, deps)

    async def list(
        self,
        *,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        owner: Optional[str] = None,
        thread_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Task]:
        clauses: List[str] = []
        params: List[Any] = []
        if status:
            clauses.append("t.status = ?")
            params.append(status)
        if kind:
            clauses.append("t.kind = ?")
            params.append(kind)
        if owner:
            clauses.append("t.owner = ?")
            params.append(owner)

        if thread_id:
            sql = (
                f"SELECT {', '.join('t.' + c.strip() for c in _TASK_COLUMNS.split(','))} "
                "FROM tasks t INNER JOIN thread_tasks tt ON tt.task_id = t.id "
                "WHERE tt.thread_id = ?"
            )
            params = [thread_id] + params
            if clauses:
                sql += " AND " + " AND ".join(clauses)
        else:
            sql = f"SELECT {_TASK_COLUMNS} FROM tasks t"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)

        sql += " ORDER BY t.priority ASC, t.created_at ASC LIMIT ?" if thread_id else \
               " ORDER BY priority ASC, created_at ASC LIMIT ?"
        params.append(int(limit))

        rows = await self.db.fetchall(sql, params)
        tasks = []
        for row in rows:
            deps = await self._load_dependencies(row[0])
            tasks.append(_row_to_task(row, deps))
        return tasks

    async def next_ready(self) -> Optional[Task]:
        """Highest-priority ready task with all deps completed."""
        in_progress_count = await self._count_status(TaskStatus.IN_PROGRESS.value)
        if in_progress_count >= self.max_concurrent:
            return None

        rows = await self.db.fetchall(
            f"""
            SELECT {_TASK_COLUMNS} FROM tasks
            WHERE status = ?
            ORDER BY priority ASC, created_at ASC
            """,
            (TaskStatus.READY.value,),
        )
        for row in rows:
            task_id = row[0]
            deps = await self._load_dependencies(task_id)
            if await self._dependencies_satisfied(deps):
                return _row_to_task(row, deps)
        return None

    async def stats(self) -> Dict[str, Any]:
        rows = await self.db.fetchall("SELECT status, COUNT(*) FROM tasks GROUP BY status")
        by_status = {row[0]: row[1] for row in rows}
        total = sum(by_status.values())
        return {
            "total": total,
            "by_status": by_status,
            "pending": by_status.get(TaskStatus.READY.value, 0) + by_status.get(TaskStatus.INBOX.value, 0),
            "in_progress": by_status.get(TaskStatus.IN_PROGRESS.value, 0),
            "completed": by_status.get(TaskStatus.COMPLETED.value, 0),
            "failed": by_status.get(TaskStatus.FAILED.value, 0),
            "max_concurrent": self.max_concurrent,
            "capacity_available": self.max_concurrent - by_status.get(TaskStatus.IN_PROGRESS.value, 0),
        }

    # ───────────────────────────────────────────────────────── transitions

    async def promote_to_ready(self, task_id: str) -> Optional[Task]:
        """Move inbox → ready. No-op if already ready."""
        return await self._transition(
            task_id,
            new_status=TaskStatus.READY.value,
            allowed_from={TaskStatus.INBOX.value, TaskStatus.READY.value},
        )

    async def start(self, task_id: str, *, actor: Optional[str] = None) -> Optional[Task]:
        """ready → in_progress. Opens a task_runs row."""
        task = await self._transition(
            task_id,
            new_status=TaskStatus.IN_PROGRESS.value,
            allowed_from={TaskStatus.READY.value, TaskStatus.INBOX.value},
            set_started_at=True,
        )
        if task is not None:
            await self._open_run(task_id, run_type="exec", actor=actor)
        return task

    async def complete(self, task_id: str, result: Any = None) -> Optional[Task]:
        """in_progress → completed. Closes the open run."""
        result_json = json.dumps(result) if result is not None else None
        task = await self._transition(
            task_id,
            new_status=TaskStatus.COMPLETED.value,
            allowed_from={TaskStatus.IN_PROGRESS.value},
            set_completed_at=True,
            result_json=result_json,
        )
        if task is not None:
            await self._close_run(task_id, status=TaskStatus.COMPLETED.value, result_json=result_json)
        return task

    async def fail(self, task_id: str, error: str) -> Optional[Task]:
        """in_progress → failed. Closes the open run."""
        task = await self._transition(
            task_id,
            new_status=TaskStatus.FAILED.value,
            allowed_from={TaskStatus.IN_PROGRESS.value},
            set_completed_at=True,
            error=error,
        )
        if task is not None:
            await self._close_run(task_id, status=TaskStatus.FAILED.value, error=error)
        return task

    async def block(self, task_id: str, reason: str) -> Optional[Task]:
        return await self._transition(
            task_id,
            new_status=TaskStatus.BLOCKED.value,
            allowed_from={TaskStatus.READY.value, TaskStatus.IN_PROGRESS.value, TaskStatus.INBOX.value},
            blocked_reason=reason,
        )

    async def unblock(self, task_id: str) -> Optional[Task]:
        return await self._transition(
            task_id,
            new_status=TaskStatus.READY.value,
            allowed_from={TaskStatus.BLOCKED.value, TaskStatus.WAITING.value},
            clear_blocked_reason=True,
        )

    async def cancel(self, task_id: str) -> Optional[Task]:
        task = await self._transition(
            task_id,
            new_status=TaskStatus.CANCELLED.value,
            allowed_from=None,  # cancel from anywhere except terminal
            set_completed_at=True,
            forbid_terminal=True,
        )
        if task is not None:
            await self._close_open_run(task_id, status=TaskStatus.CANCELLED.value)
        return task

    async def archive(self, task_id: str) -> Optional[Task]:
        task = await self._transition(
            task_id,
            new_status=TaskStatus.ARCHIVED.value,
            allowed_from=None,
            forbid_terminal=False,
        )
        if task is not None:
            await self._close_open_run(task_id, status=TaskStatus.ARCHIVED.value)
        return task

    # ─────────────────────────────────────────────────────────── YAML migration

    async def migrate_yaml_queue(self, yaml_path: Path) -> Dict[str, Any]:
        """One-shot migration of the legacy bridge YAML queue into `tasks`.

        Idempotent by sentinel: the file is renamed to
        `task_queue.yaml.migrated-<ts>` on success. If that file or any
        `.migrated-*` sibling already exists, migration is skipped.
        Returns a summary dict — safe to log at info level.
        """
        summary: Dict[str, Any] = {"migrated": 0, "skipped": 0, "errors": 0, "status": "noop"}
        if not yaml_path.exists():
            return summary

        # Skip if a prior migration artifact is already present.
        for sibling in yaml_path.parent.glob(f"{yaml_path.name}.migrated-*"):
            if sibling.exists():
                summary["status"] = "already_migrated"
                return summary

        try:
            import yaml as _yaml
        except ImportError:
            logger.warning("PyYAML not available, skipping YAML queue migration")
            summary["status"] = "no_yaml"
            return summary

        try:
            data = _yaml.safe_load(yaml_path.read_text()) or {}
        except Exception as e:
            logger.warning(f"Failed to parse YAML queue at {yaml_path}: {e}")
            summary["status"] = "parse_error"
            return summary

        entries = data.get("tasks") or []
        if not entries:
            # Empty file — still rename so we don't keep polling it.
            _rename_with_timestamp(yaml_path)
            summary["status"] = "empty_migrated"
            return summary

        status_map = {
            "pending": TaskStatus.READY.value,
            "claimed": TaskStatus.IN_PROGRESS.value,
            "in_progress": TaskStatus.IN_PROGRESS.value,
            "completed": TaskStatus.COMPLETED.value,
            "failed": TaskStatus.FAILED.value,
        }
        priority_map = {"critical": 1, "high": 3, "medium": 5, "low": 8}

        for entry in entries:
            try:
                raw_id = entry.get("id") or _generate_task_id()
                existing = await self.db.fetchone("SELECT id FROM tasks WHERE id = ?", (raw_id,))
                if existing:
                    summary["skipped"] += 1
                    continue

                title = entry.get("title") or "(migrated task)"
                desc = entry.get("description")
                raw_status = entry.get("status") or "pending"
                status = status_map.get(raw_status, TaskStatus.READY.value)
                raw_priority = entry.get("priority")
                if isinstance(raw_priority, int):
                    priority = max(1, min(10, raw_priority))
                else:
                    priority = priority_map.get(str(raw_priority or "medium").lower(), 5)

                metadata = {
                    "tags": entry.get("tags") or [],
                    "related_files": (entry.get("context") or {}).get("related_files", []),
                    "legacy_created_by": entry.get("created_by"),
                    "legacy_claimed_by": entry.get("claimed_by"),
                    "legacy_context": entry.get("context") or {},
                    "legacy_result": entry.get("result"),
                    "legacy_result_summary": entry.get("result_summary"),
                }

                await self.create(
                    title=title,
                    description=desc,
                    kind="conversation_born_task",
                    priority=priority,
                    status=status,
                    source="yaml_bridge_migration",
                    metadata={k: v for k, v in metadata.items() if v not in (None, [], {})},
                    task_id=raw_id,
                )
                summary["migrated"] += 1
            except Exception as e:
                logger.warning(f"YAML task migration error on entry {entry.get('id')}: {e}")
                summary["errors"] += 1

        _rename_with_timestamp(yaml_path)
        summary["status"] = "migrated"
        logger.info(
            "YAML queue migration: %d migrated, %d skipped, %d errors",
            summary["migrated"], summary["skipped"], summary["errors"],
        )
        return summary

    # ─────────────────────────────────────────────────── mapping ontology

    async def add_entity(
        self,
        task_id: str,
        *,
        entity_id: Optional[str] = None,
        entity_text: Optional[str] = None,
        relationship: str = "about",
        confidence: float = 1.0,
        source: Optional[str] = None,
    ) -> bool:
        # Schema stores '' for absent columns (SQLite PK disallows expressions).
        eid = (entity_id or "").strip()
        etext = (entity_text or "").strip()
        if not eid and not etext:
            return False
        await self.db.execute(
            """
            INSERT OR IGNORE INTO task_entities
                (task_id, entity_id, entity_text, relationship, confidence, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, eid, etext, relationship, float(confidence), source),
        )
        return True

    async def add_subject(
        self, task_id: str, subject: str,
        *, source: str = "manual", weight: float = 1.0,
    ) -> bool:
        if not subject:
            return False
        await self.db.execute(
            "INSERT OR IGNORE INTO task_subjects (task_id, subject, source, weight) VALUES (?, ?, ?, ?)",
            (task_id, subject, source, float(weight)),
        )
        return True

    async def add_keyword(
        self, task_id: str, keyword: str,
        *, source: str = "manual", weight: float = 1.0,
    ) -> bool:
        if not keyword:
            return False
        await self.db.execute(
            "INSERT OR IGNORE INTO task_keywords (task_id, keyword, source, weight) VALUES (?, ?, ?, ?)",
            (task_id, keyword, source, float(weight)),
        )
        return True

    async def add_file(
        self, task_id: str, path: str,
        *, relationship: str = "touches",
    ) -> bool:
        if not path:
            return False
        await self.db.execute(
            "INSERT OR IGNORE INTO task_files (task_id, path, relationship) VALUES (?, ?, ?)",
            (task_id, path, relationship),
        )
        return True

    async def add_memory_link(
        self, task_id: str, memory_node_id: str,
        *, relationship: str = "related", score: float = 0.0,
    ) -> bool:
        if not memory_node_id:
            return False
        await self.db.execute(
            "INSERT OR IGNORE INTO task_memory_links (task_id, memory_node_id, relationship, score) VALUES (?, ?, ?, ?)",
            (task_id, memory_node_id, relationship, float(score)),
        )
        return True

    async def set_mappings(
        self,
        task_id: str,
        *,
        entities: Optional[List[str]] = None,
        subjects: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        files: Optional[List[str]] = None,
        memory_links: Optional[List[str]] = None,
        source: str = "manual",
    ) -> Dict[str, int]:
        """Bulk mapping writer. Returns counts by bucket. Idempotent (INSERT OR IGNORE)."""
        counts: Dict[str, int] = {}
        for name in (entities or []):
            if await self.add_entity(task_id, entity_text=name, source=source):
                counts["entities"] = counts.get("entities", 0) + 1
        for s in (subjects or []):
            if await self.add_subject(task_id, s, source=source):
                counts["subjects"] = counts.get("subjects", 0) + 1
        for k in (keywords or []):
            if await self.add_keyword(task_id, k, source=source):
                counts["keywords"] = counts.get("keywords", 0) + 1
        for f in (files or []):
            if await self.add_file(task_id, f):
                counts["files"] = counts.get("files", 0) + 1
        for m in (memory_links or []):
            if await self.add_memory_link(task_id, m):
                counts["memory_links"] = counts.get("memory_links", 0) + 1
        return counts

    async def load_mappings(self, task_id: str) -> Dict[str, List[Any]]:
        """Return the mapping buckets for a single task from canonical tables.

        Nine-bucket shape for parity with Observatory's response:
        `people`, `entities`, `subjects`, `keywords`, `threads`, `projects`,
        `quests`, `files`, `memory`. `people` is a typed filter over
        task_entities (entity_type='person'). `projects` / `quests` are left
        to the caller (Observatory joins threads + metadata for those).
        """
        entities: List[str] = []
        people: List[str] = []
        rows = await self.db.fetchall(
            """
            SELECT te.entity_id, te.entity_text, te.relationship,
                   e.entity_type, e.name
            FROM task_entities te
            LEFT JOIN entities e ON e.id = te.entity_id AND te.entity_id <> ''
            WHERE te.task_id = ?
            """,
            (task_id,),
        )
        for r in rows:
            entity_id, entity_text, relationship, etype, ename = r[0], r[1], r[2], r[3], r[4]
            # '' is the absence sentinel for both entity_id and entity_text.
            label = ename or (entity_text or None) or (entity_id or None)
            if not label:
                continue
            if etype == "person" or relationship == "owned_by":
                people.append(label)
            else:
                entities.append(label)

        subj_rows = await self.db.fetchall(
            "SELECT subject FROM task_subjects WHERE task_id = ? ORDER BY weight DESC, subject", (task_id,)
        )
        kw_rows = await self.db.fetchall(
            "SELECT keyword FROM task_keywords WHERE task_id = ? ORDER BY weight DESC, keyword", (task_id,)
        )
        file_rows = await self.db.fetchall(
            "SELECT path FROM task_files WHERE task_id = ? ORDER BY created_at", (task_id,)
        )
        mem_rows = await self.db.fetchall(
            "SELECT memory_node_id, relationship FROM task_memory_links WHERE task_id = ? ORDER BY score DESC",
            (task_id,),
        )

        thread_rows = await self.db.fetchall(
            "SELECT thread_id FROM thread_tasks WHERE task_id = ?", (task_id,)
        )

        return {
            "people":   people,
            "entities": entities,
            "subjects": [r[0] for r in subj_rows],
            "keywords": [r[0] for r in kw_rows],
            "threads":  [r[0] for r in thread_rows],
            "projects": [],   # filled by caller (Observatory) from threads/metadata
            "quests":   [],   # deferred until quest narrowing slice
            "files":    [r[0] for r in file_rows],
            "memory":   [{"id": r[0], "relationship": r[1]} for r in mem_rows],
        }

    async def backfill_mappings_from_metadata(self) -> Dict[str, Any]:
        """One-shot idempotent migration: tasks.metadata_json → mapping tables.

        Tasks already backfilled (mappings_backfilled_at IS NOT NULL) are
        skipped. On success, the sentinel is stamped so re-runs are no-ops.
        Returns a summary safe to log at info level.
        """
        summary: Dict[str, Any] = {
            "tasks_scanned": 0,
            "tasks_backfilled": 0,
            "entities": 0, "subjects": 0, "keywords": 0, "files": 0,
            "errors": 0,
        }
        rows = await self.db.fetchall(
            "SELECT id, metadata_json FROM tasks WHERE mappings_backfilled_at IS NULL"
        )
        summary["tasks_scanned"] = len(rows)

        for row in rows:
            tid = row[0]
            raw = row[1]
            try:
                meta = json.loads(raw) if raw else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}

            def _str_list(value) -> List[str]:
                return [x for x in (value or []) if isinstance(x, str) and x]

            ent_list = _str_list(meta.get("entities"))
            # Metadata "people" flow into task_entities with a hint relationship
            # so load_mappings can split them back out before entity resolution lands.
            people_list = _str_list(meta.get("people"))
            subj_list = _str_list(meta.get("subjects"))
            kw_list = _str_list(meta.get("keywords") or meta.get("tags"))
            file_list = _str_list(meta.get("related_files") or meta.get("files"))

            try:
                counts = await self.set_mappings(
                    tid,
                    entities=ent_list,
                    subjects=subj_list,
                    keywords=kw_list,
                    files=file_list,
                    source="metadata_backfill",
                )
                for person in people_list:
                    if await self.add_entity(
                        tid, entity_text=person,
                        relationship="owned_by",
                        source="metadata_backfill",
                    ):
                        counts["entities"] = counts.get("entities", 0) + 1
                for bucket in ("entities", "subjects", "keywords", "files"):
                    summary[bucket] += counts.get(bucket, 0)
                await self.db.execute(
                    "UPDATE tasks SET mappings_backfilled_at = ? WHERE id = ?",
                    (_now(), tid),
                )
                summary["tasks_backfilled"] += 1
            except Exception as e:
                logger.warning(f"Mapping backfill error on task {tid}: {e}")
                summary["errors"] += 1

        if summary["tasks_backfilled"]:
            logger.info(
                "TaskManager: backfilled mappings for %d tasks (entities=%d, subjects=%d, keywords=%d, files=%d)",
                summary["tasks_backfilled"],
                summary["entities"], summary["subjects"], summary["keywords"], summary["files"],
            )
        return summary

    # ────────────────────────────────────────────────────────────── internals

    async def _transition(
        self,
        task_id: str,
        *,
        new_status: str,
        allowed_from: Optional[set],
        set_started_at: bool = False,
        set_completed_at: bool = False,
        result_json: Optional[str] = None,
        error: Optional[str] = None,
        blocked_reason: Optional[str] = None,
        clear_blocked_reason: bool = False,
        forbid_terminal: bool = True,
    ) -> Optional[Task]:
        row = await self.db.fetchone("SELECT status FROM tasks WHERE id = ?", (task_id,))
        if row is None:
            return None
        current = row[0]
        if allowed_from is not None and current not in allowed_from:
            logger.debug("Task %s: transition %s → %s not permitted", task_id, current, new_status)
            return None
        if forbid_terminal and current in {s.value for s in _TERMINAL_STATES}:
            logger.debug("Task %s: refusing to transition out of terminal %s", task_id, current)
            return None

        sets = ["status = ?", "updated_at = ?"]
        params: List[Any] = [new_status, _now()]
        if set_started_at:
            sets.append("started_at = ?")
            params.append(_now())
        if set_completed_at:
            sets.append("completed_at = ?")
            params.append(_now())
        if result_json is not None:
            sets.append("result_json = ?")
            params.append(result_json)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if blocked_reason is not None:
            sets.append("blocked_reason = ?")
            params.append(blocked_reason)
        if clear_blocked_reason:
            sets.append("blocked_reason = NULL")
        params.append(task_id)

        await self.db.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params
        )
        return await self.get(task_id)

    async def _load_dependencies(self, task_id: str) -> List[str]:
        rows = await self.db.fetchall(
            "SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?",
            (task_id,),
        )
        return [r[0] for r in rows]

    async def _dependencies_satisfied(self, dep_ids: List[str]) -> bool:
        if not dep_ids:
            return True
        placeholders = ",".join("?" * len(dep_ids))
        rows = await self.db.fetchall(
            f"SELECT id, status FROM tasks WHERE id IN ({placeholders})",
            dep_ids,
        )
        found = {r[0]: r[1] for r in rows}
        for dep in dep_ids:
            if found.get(dep) != TaskStatus.COMPLETED.value:
                return False
        return True

    async def _count_status(self, status: str) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) FROM tasks WHERE status = ?", (status,)
        )
        return int(row[0]) if row else 0

    async def _open_run(self, task_id: str, *, run_type: str, actor: Optional[str]) -> str:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        await self.db.execute(
            """
            INSERT INTO task_runs (id, task_id, run_type, actor, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, task_id, run_type, actor, _ACTIVE_RUN_STATUS),
        )
        return run_id

    async def _close_open_run(
        self,
        task_id: str,
        *,
        status: str,
        error: Optional[str] = None,
    ) -> bool:
        """Close the most recent running run, if one exists. No-op otherwise.

        Used by cancel/archive — we want the audit trail to reflect that the
        active run ended, but we do NOT want to fabricate a run row for a
        task that was never started.
        """
        row = await self.db.fetchone(
            "SELECT id FROM task_runs WHERE task_id = ? AND status = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (task_id, _ACTIVE_RUN_STATUS),
        )
        if row is None:
            return False
        await self.db.execute(
            "UPDATE task_runs SET status = ?, completed_at = ?, error = ? WHERE id = ?",
            (status, _now(), error, row[0]),
        )
        return True

    async def _close_run(
        self,
        task_id: str,
        *,
        status: str,
        result_json: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        row = await self.db.fetchone(
            "SELECT id FROM task_runs WHERE task_id = ? AND status = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (task_id, _ACTIVE_RUN_STATUS),
        )
        if row is None:
            # No open run — still record a closed one so audit trail is complete.
            run_id = f"run_{uuid.uuid4().hex[:12]}"
            await self.db.execute(
                """
                INSERT INTO task_runs (id, task_id, run_type, actor, completed_at, status, result_json, error)
                VALUES (?, ?, 'exec', NULL, ?, ?, ?, ?)
                """,
                (run_id, task_id, _now(), status, result_json, error),
            )
            return
        await self.db.execute(
            "UPDATE task_runs SET status = ?, completed_at = ?, result_json = ?, error = ? WHERE id = ?",
            (status, _now(), result_json, error, row[0]),
        )


def _rename_with_timestamp(path: Path) -> Path:
    """Rename path → path.migrated-YYYYMMDDHHMMSS. Returns the new path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    target = path.with_name(f"{path.name}.migrated-{ts}")
    path.rename(target)
    return target
