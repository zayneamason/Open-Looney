"""
Luna Engine Tuning Session
==========================

Track tuning iterations, compare results, and persist to database.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .evaluator import EvalResults
    from .params import ParamRegistry

logger = logging.getLogger(__name__)


@dataclass
class TuningIteration:
    """A single tuning iteration."""
    iteration_num: int
    params_changed: dict[str, Any]  # Which params were tweaked this iteration
    param_snapshot: dict[str, Any]  # Full param state at this iteration
    eval_results: dict  # Serialized EvalResults
    score: float
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TuningIteration":
        return cls(**data)


@dataclass
class TuningSession:
    """
    A tuning session that tracks multiple iterations.

    Persists to SQLite for history and comparison.
    """
    session_id: str
    focus: str  # Area of focus: "memory", "routing", "latency", "all"
    started_at: str
    ended_at: Optional[str] = None
    notes: str = ""
    iterations: list[TuningIteration] = field(default_factory=list)
    best_iteration: int = 0
    best_score: float = 0.0
    base_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "focus": self.focus,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "notes": self.notes,
            "iterations": [i.to_dict() for i in self.iterations],
            "best_iteration": self.best_iteration,
            "best_score": self.best_score,
            "base_params": self.base_params,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TuningSession":
        iterations = [TuningIteration.from_dict(i) for i in data.get("iterations", [])]
        return cls(
            session_id=data["session_id"],
            focus=data["focus"],
            started_at=data["started_at"],
            ended_at=data.get("ended_at"),
            notes=data.get("notes", ""),
            iterations=iterations,
            best_iteration=data.get("best_iteration", 0),
            best_score=data.get("best_score", 0.0),
            base_params=data.get("base_params", {}),
        )


class TuningSessionManager:
    """
    Manages tuning sessions with persistence.

    Stores sessions and iterations in SQLite for history tracking.
    """

    def __init__(self, db_path: str = None):
        from luna.core.paths import memory_matrix_path
        if db_path is None:
            db_path = str(memory_matrix_path())
        """
        Initialize the session manager.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self._current_session: Optional[TuningSession] = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the database tables."""
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout=15000")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tuning_sessions (
                    session_id TEXT PRIMARY KEY,
                    focus TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    notes TEXT,
                    best_iteration INTEGER DEFAULT 0,
                    best_score REAL DEFAULT 0.0,
                    base_params TEXT
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS tuning_iterations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    iteration_num INTEGER NOT NULL,
                    params_changed TEXT NOT NULL,
                    param_snapshot TEXT NOT NULL,
                    eval_results TEXT NOT NULL,
                    score REAL NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES tuning_sessions(session_id)
                )
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_iterations_session
                ON tuning_iterations(session_id)
            """)

            await db.commit()

        self._initialized = True
        logger.info("TuningSessionManager initialized")

    async def new_session(
        self,
        focus: str = "all",
        base_params: Optional[dict] = None,
        notes: str = "",
    ) -> TuningSession:
        """
        Create a new tuning session.

        Args:
            focus: Area of focus ("memory", "routing", "latency", "all")
            base_params: Starting parameter values
            notes: Session notes

        Returns:
            New TuningSession
        """
        if not self._initialized:
            await self.initialize()

        session = TuningSession(
            session_id=str(uuid.uuid4()),
            focus=focus,
            started_at=datetime.now().isoformat(),
            notes=notes,
            base_params=base_params or {},
        )

        # Persist to database
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout=15000")
            await db.execute("""
                INSERT INTO tuning_sessions
                (session_id, focus, started_at, notes, base_params)
                VALUES (?, ?, ?, ?, ?)
            """, (
                session.session_id,
                session.focus,
                session.started_at,
                session.notes,
                json.dumps(session.base_params),
            ))
            await db.commit()

        self._current_session = session
        logger.info(f"Created tuning session {session.session_id} with focus={focus}")
        return session

    async def add_iteration(
        self,
        params_changed: dict[str, Any],
        param_snapshot: dict[str, Any],
        eval_results: "EvalResults",
        notes: str = "",
    ) -> TuningIteration:
        """
        Add an iteration to the current session.

        Args:
            params_changed: Parameters that were changed
            param_snapshot: Full parameter state
            eval_results: Evaluation results
            notes: Iteration notes

        Returns:
            New TuningIteration
        """
        if not self._current_session:
            raise RuntimeError("No active tuning session")

        iteration_num = len(self._current_session.iterations) + 1

        iteration = TuningIteration(
            iteration_num=iteration_num,
            params_changed=params_changed,
            param_snapshot=param_snapshot,
            eval_results=eval_results.to_dict(),
            score=eval_results.overall_score,
            notes=notes,
        )

        self._current_session.iterations.append(iteration)

        # Update best if this is better
        if iteration.score > self._current_session.best_score:
            self._current_session.best_score = iteration.score
            self._current_session.best_iteration = iteration_num

        # Persist to database
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout=15000")
            await db.execute("""
                INSERT INTO tuning_iterations
                (session_id, iteration_num, params_changed, param_snapshot, eval_results, score, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self._current_session.session_id,
                iteration.iteration_num,
                json.dumps(iteration.params_changed),
                json.dumps(iteration.param_snapshot),
                json.dumps(iteration.eval_results),
                iteration.score,
                iteration.notes,
                iteration.created_at,
            ))

            # Update session best
            await db.execute("""
                UPDATE tuning_sessions
                SET best_iteration = ?, best_score = ?
                WHERE session_id = ?
            """, (
                self._current_session.best_iteration,
                self._current_session.best_score,
                self._current_session.session_id,
            ))

            await db.commit()

        logger.info(f"Iteration {iteration_num}: score={iteration.score:.3f}")
        return iteration

    async def end_session(self) -> Optional[TuningSession]:
        """End the current session."""
        if not self._current_session:
            return None

        self._current_session.ended_at = datetime.now().isoformat()

        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout=15000")
            await db.execute("""
                UPDATE tuning_sessions
                SET ended_at = ?
                WHERE session_id = ?
            """, (
                self._current_session.ended_at,
                self._current_session.session_id,
            ))
            await db.commit()

        session = self._current_session
        self._current_session = None
        logger.info(f"Ended session {session.session_id}")
        return session

    @property
    def current_session(self) -> Optional[TuningSession]:
        """Get the current active session."""
        return self._current_session

    async def get_session(self, session_id: str) -> Optional[TuningSession]:
        """Load a session by ID."""
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=15000")

            # Get session
            cursor = await db.execute("""
                SELECT * FROM tuning_sessions WHERE session_id = ?
            """, (session_id,))
            row = await cursor.fetchone()

            if not row:
                return None

            # Get iterations
            cursor = await db.execute("""
                SELECT * FROM tuning_iterations
                WHERE session_id = ?
                ORDER BY iteration_num
            """, (session_id,))
            iter_rows = await cursor.fetchall()

        iterations = []
        for ir in iter_rows:
            iterations.append(TuningIteration(
                iteration_num=ir["iteration_num"],
                params_changed=json.loads(ir["params_changed"]),
                param_snapshot=json.loads(ir["param_snapshot"]),
                eval_results=json.loads(ir["eval_results"]),
                score=ir["score"],
                notes=ir["notes"] or "",
                created_at=ir["created_at"],
            ))

        return TuningSession(
            session_id=row["session_id"],
            focus=row["focus"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            notes=row["notes"] or "",
            iterations=iterations,
            best_iteration=row["best_iteration"],
            best_score=row["best_score"],
            base_params=json.loads(row["base_params"]) if row["base_params"] else {},
        )

    async def list_sessions(self, limit: int = 20) -> list[dict]:
        """List recent sessions."""
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=15000")
            cursor = await db.execute("""
                SELECT session_id, focus, started_at, ended_at, best_score,
                       (SELECT COUNT(*) FROM tuning_iterations WHERE session_id = s.session_id) as iterations
                FROM tuning_sessions s
                ORDER BY started_at DESC
                LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()

        return [dict(row) for row in rows]

    def compare_iterations(
        self,
        iter1: int,
        iter2: int,
    ) -> dict:
        """Compare two iterations from the current session."""
        if not self._current_session:
            raise RuntimeError("No active session")

        if iter1 < 1 or iter1 > len(self._current_session.iterations):
            raise ValueError(f"Invalid iteration: {iter1}")
        if iter2 < 1 or iter2 > len(self._current_session.iterations):
            raise ValueError(f"Invalid iteration: {iter2}")

        i1 = self._current_session.iterations[iter1 - 1]
        i2 = self._current_session.iterations[iter2 - 1]

        # Find param differences
        param_diffs = {}
        all_keys = set(i1.param_snapshot.keys()) | set(i2.param_snapshot.keys())
        for key in all_keys:
            v1 = i1.param_snapshot.get(key)
            v2 = i2.param_snapshot.get(key)
            if v1 != v2:
                param_diffs[key] = {"from": v1, "to": v2}

        # Score comparison
        r1 = i1.eval_results
        r2 = i2.eval_results

        return {
            "iteration_1": iter1,
            "iteration_2": iter2,
            "score_diff": i2.score - i1.score,
            "score_1": i1.score,
            "score_2": i2.score,
            "param_diffs": param_diffs,
            "metric_diffs": {
                "memory_recall": r2.get("memory_recall_score", 0) - r1.get("memory_recall_score", 0),
                "context_retention": r2.get("context_retention_score", 0) - r1.get("context_retention_score", 0),
                "routing": r2.get("routing_score", 0) - r1.get("routing_score", 0),
                "latency": r2.get("avg_latency_ms", 0) - r1.get("avg_latency_ms", 0),
            },
        }

    def get_best_params(self) -> dict[str, Any]:
        """Get parameters from the best iteration."""
        if not self._current_session:
            raise RuntimeError("No active session")

        if self._current_session.best_iteration == 0:
            return self._current_session.base_params

        best = self._current_session.iterations[self._current_session.best_iteration - 1]
        return best.param_snapshot
