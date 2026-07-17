"""Trace persistence writer for traces.db."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Optional
import json
import sqlite3
import time

from .trace_builder import TraceRecord
from .trace_config import TraceConfig


class TraceWriter:
    """Persists ``TraceRecord`` into SQLite with best-effort semantics."""

    def __init__(self, db_path: Path, config: TraceConfig):
        self.db_path = Path(db_path)
        self.config = config
        self.last_error: Optional[str] = None
        self._schema_ready = False

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path = Path(__file__).with_name("trace_schema.sql")
        schema_sql = schema_path.read_text()

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(schema_sql)
            conn.commit()

        self._schema_ready = True

    def persist(self, record: TraceRecord) -> bool:
        """Persist one trace. Returns True on success, False on failure."""
        self.last_error = None
        if not self.config.persist:
            return False

        try:
            self._ensure_schema()

            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                cur = conn.cursor()

                cur.execute(
                    """
                    INSERT OR REPLACE INTO traces (
                        id, ts, session_id, turn_num, query, route, door,
                        aperture, aperture_deg, backend, prepare_only,
                        latency_ms, token_count, token_budget,
                        final_prompt, notes, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        record.id,
                        record.ts,
                        record.session_id,
                        record.turn_num,
                        record.query,
                        record.route,
                        record.door,
                        record.aperture,
                        record.aperture_deg,
                        record.backend,
                        1 if record.prepare_only else 0,
                        record.latency_ms,
                        record.token_count,
                        record.token_budget,
                        record.final_prompt if self.config.capture_full_prompt else None,
                        json.dumps(record.notes or {}, ensure_ascii=True),
                    ),
                )

                cur.execute("DELETE FROM trace_stages WHERE trace_id = ?", (record.id,))
                cur.execute("DELETE FROM trace_candidates WHERE trace_id = ?", (record.id,))
                cur.execute("DELETE FROM trace_rings WHERE trace_id = ?", (record.id,))
                cur.execute("DELETE FROM trace_prompt_layers WHERE trace_id = ?", (record.id,))
                cur.execute("DELETE FROM trace_keywords WHERE trace_id = ?", (record.id,))

                for s in record.stages:
                    cur.execute(
                        """
                        INSERT INTO trace_stages (
                            trace_id, stage_idx, stage_name, start_ms, end_ms,
                            budget_ms, candidates_in, candidates_out, status, error
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.id,
                            s.stage_idx,
                            s.stage_name,
                            s.start_ms,
                            s.end_ms,
                            s.budget_ms,
                            s.candidates_in,
                            s.candidates_out,
                            s.status,
                            s.error,
                        ),
                    )

                for c in record.candidates:
                    node_label = c.node_label if self.config.capture_candidate_content else None
                    cur.execute(
                        """
                        INSERT INTO trace_candidates (
                            trace_id, stage_name, ord, node_id, node_kind, node_label,
                            raw_score, fused_score, lock_in, relevance, age_days,
                            accepted, rejection_reason, ring_assigned
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.id,
                            c.stage_name,
                            c.ord,
                            c.node_id,
                            c.node_kind,
                            node_label,
                            c.raw_score,
                            c.fused_score,
                            c.lock_in,
                            c.relevance,
                            c.age_days,
                            1 if c.accepted else 0,
                            c.rejection_reason,
                            c.ring_assigned,
                        ),
                    )

                for r in record.rings:
                    ring_items = r.items
                    if not self.config.capture_candidate_content:
                        sanitized_items = []
                        for item in ring_items:
                            if isinstance(item, dict):
                                cleaned = dict(item)
                                cleaned.pop("label", None)
                                sanitized_items.append(cleaned)
                            else:
                                sanitized_items.append(item)
                        ring_items = sanitized_items
                    cur.execute(
                        """
                        INSERT INTO trace_rings (
                            trace_id, ring, item_count, token_count, items_json
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            record.id,
                            r.ring,
                            r.item_count,
                            r.token_count,
                            json.dumps(ring_items, ensure_ascii=True),
                        ),
                    )

                for layer in record.prompt_layers:
                    cur.execute(
                        """
                        INSERT INTO trace_prompt_layers (
                            trace_id, layer_idx, layer_id, layer_name, content, token_count
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.id,
                            layer.layer_idx,
                            layer.layer_id,
                            layer.layer_name,
                            layer.content if self.config.capture_full_prompt else None,
                            layer.token_count,
                        ),
                    )

                for kw in record.keywords:
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO trace_keywords (
                            trace_id, keyword, source
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            record.id,
                            kw.get("keyword"),
                            kw.get("source"),
                        ),
                    )

                conn.commit()

            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def cleanup(self) -> int:
        """Apply retention cleanup and return deleted row count."""
        self.last_error = None
        try:
            self._ensure_schema()
            if self.config.retention_days <= 0:
                return 0
            cutoff = time.time() - (self.config.retention_days * 86400)
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                cur = conn.cursor()
                cur.execute("DELETE FROM traces WHERE ts < ?", (cutoff,))
                deleted = int(cur.rowcount or 0)
                conn.commit()
            return deleted
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return 0
