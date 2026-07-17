"""Read-only trace access for traces.db."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import sqlite3


def _json_loads_safe(raw: Optional[str], fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except Exception:
        return fallback


class TraceReader:
    """Read-only helper for listing, fetching, and diffing traces."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _db_exists(self) -> bool:
        return self.db_path.exists()

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        route: Optional[str] = None,
        since: Optional[float] = None,
        session_id: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Return trace summaries with total count for pagination."""
        if not self._db_exists():
            return [], 0

        limit = max(1, min(int(limit or 50), 500))
        offset = max(0, int(offset or 0))

        where: List[str] = []
        args: List[Any] = []
        if route:
            where.append("t.route = ?")
            args.append(route)
        if since is not None:
            where.append("t.ts >= ?")
            args.append(float(since))
        if session_id:
            where.append("t.session_id = ?")
            args.append(session_id)

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        with self._connect() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(1) FROM traces t {where_sql}",
                    tuple(args),
                ).fetchone()[0]
            )

            rows = conn.execute(
                f"""
                SELECT
                    t.id,
                    t.ts,
                    t.session_id,
                    t.turn_num,
                    t.query,
                    t.route,
                    t.backend,
                    t.prepare_only,
                    t.latency_ms,
                    t.token_count,
                    t.token_budget,
                    (
                        SELECT COUNT(1)
                        FROM trace_stages s
                        WHERE s.trace_id = t.id
                    ) AS stage_count,
                    (
                        SELECT COUNT(1)
                        FROM trace_candidates c
                        WHERE c.trace_id = t.id
                    ) AS candidate_count
                FROM traces t
                {where_sql}
                ORDER BY t.ts DESC
                LIMIT ? OFFSET ?
                """,
                tuple(args + [limit, offset]),
            ).fetchall()

        items: List[Dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "ts": row["ts"],
                    "session_id": row["session_id"],
                    "turn_num": row["turn_num"],
                    "query": row["query"],
                    "route": row["route"],
                    "backend": row["backend"],
                    "prepare_only": bool(row["prepare_only"]),
                    "latency_ms": row["latency_ms"],
                    "token_count": row["token_count"],
                    "token_budget": row["token_budget"],
                    "stage_count": int(row["stage_count"] or 0),
                    "candidate_count": int(row["candidate_count"] or 0),
                }
            )
        return items, total

    def get(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """Fetch one full trace record by id."""
        if not trace_id or not self._db_exists():
            return None

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id, ts, session_id, turn_num, query, route, door,
                    aperture, aperture_deg, backend, prepare_only,
                    latency_ms, token_count, token_budget,
                    final_prompt, notes
                FROM traces
                WHERE id = ?
                """,
                (trace_id,),
            ).fetchone()
            if row is None:
                return None

            stages = conn.execute(
                """
                SELECT
                    stage_idx, stage_name, start_ms, end_ms, budget_ms,
                    candidates_in, candidates_out, status, error
                FROM trace_stages
                WHERE trace_id = ?
                ORDER BY stage_idx ASC
                """,
                (trace_id,),
            ).fetchall()

            candidates = conn.execute(
                """
                SELECT
                    stage_name, ord, node_id, node_kind, node_label,
                    raw_score, fused_score, lock_in, relevance, age_days,
                    accepted, rejection_reason, ring_assigned
                FROM trace_candidates
                WHERE trace_id = ?
                ORDER BY stage_name ASC, ord ASC
                """,
                (trace_id,),
            ).fetchall()

            rings = conn.execute(
                """
                SELECT ring, item_count, token_count, items_json
                FROM trace_rings
                WHERE trace_id = ?
                ORDER BY
                    CASE ring
                        WHEN 'CORE' THEN 0
                        WHEN 'INNER' THEN 1
                        WHEN 'MIDDLE' THEN 2
                        WHEN 'OUTER' THEN 3
                        ELSE 9
                    END ASC
                """,
                (trace_id,),
            ).fetchall()

            prompt_layers = conn.execute(
                """
                SELECT layer_idx, layer_id, layer_name, content, token_count
                FROM trace_prompt_layers
                WHERE trace_id = ?
                ORDER BY layer_idx ASC
                """,
                (trace_id,),
            ).fetchall()

            keywords = conn.execute(
                """
                SELECT keyword, source
                FROM trace_keywords
                WHERE trace_id = ?
                ORDER BY keyword ASC, source ASC
                """,
                (trace_id,),
            ).fetchall()

        return {
            "id": row["id"],
            "ts": row["ts"],
            "query": row["query"],
            "session_id": row["session_id"],
            "turn_num": row["turn_num"],
            "route": row["route"],
            "door": row["door"],
            "aperture": row["aperture"],
            "aperture_deg": row["aperture_deg"],
            "backend": row["backend"],
            "prepare_only": bool(row["prepare_only"]),
            "latency_ms": row["latency_ms"],
            "token_count": row["token_count"],
            "token_budget": row["token_budget"],
            "final_prompt": row["final_prompt"],
            "notes": _json_loads_safe(row["notes"], {}),
            "stages": [
                {
                    "stage_idx": s["stage_idx"],
                    "stage_name": s["stage_name"],
                    "start_ms": s["start_ms"],
                    "end_ms": s["end_ms"],
                    "budget_ms": s["budget_ms"],
                    "candidates_in": s["candidates_in"],
                    "candidates_out": s["candidates_out"],
                    "status": s["status"],
                    "error": s["error"],
                }
                for s in stages
            ],
            "candidates": [
                {
                    "stage_name": c["stage_name"],
                    "ord": c["ord"],
                    "node_id": c["node_id"],
                    "node_kind": c["node_kind"],
                    "node_label": c["node_label"],
                    "raw_score": c["raw_score"],
                    "fused_score": c["fused_score"],
                    "lock_in": c["lock_in"],
                    "relevance": c["relevance"],
                    "age_days": c["age_days"],
                    "accepted": bool(c["accepted"]),
                    "rejection_reason": c["rejection_reason"],
                    "ring_assigned": c["ring_assigned"],
                }
                for c in candidates
            ],
            "rings": [
                {
                    "ring": r["ring"],
                    "item_count": r["item_count"],
                    "token_count": r["token_count"],
                    "items": _json_loads_safe(r["items_json"], []),
                }
                for r in rings
            ],
            "prompt_layers": [
                {
                    "layer_idx": l["layer_idx"],
                    "layer_id": l["layer_id"],
                    "layer_name": l["layer_name"],
                    "content": l["content"],
                    "token_count": l["token_count"],
                }
                for l in prompt_layers
            ],
            "keywords": [{"keyword": k["keyword"], "source": k["source"]} for k in keywords],
        }

    def diff(self, id_a: str, id_b: str) -> Optional[Dict[str, Any]]:
        """Return a structural diff between two traces."""
        a = self.get(id_a)
        b = self.get(id_b)
        if a is None or b is None:
            return None

        def _stage_latency(stage: Dict[str, Any]) -> Optional[float]:
            start = stage.get("start_ms")
            end = stage.get("end_ms")
            if start is None or end is None:
                return None
            return float(end) - float(start)

        a_stages = {s["stage_name"]: s for s in a.get("stages", [])}
        b_stages = {s["stage_name"]: s for s in b.get("stages", [])}
        stage_names = list(dict.fromkeys(list(a_stages.keys()) + list(b_stages.keys())))

        stage_diffs: List[Dict[str, Any]] = []
        for name in stage_names:
            sa = a_stages.get(name) or {}
            sb = b_stages.get(name) or {}
            la = _stage_latency(sa) if sa else None
            lb = _stage_latency(sb) if sb else None
            delta = None
            if la is not None and lb is not None:
                delta = lb - la
            stage_diffs.append(
                {
                    "stage_name": name,
                    "status_a": sa.get("status"),
                    "status_b": sb.get("status"),
                    "latency_a_ms": la,
                    "latency_b_ms": lb,
                    "latency_delta_ms": delta,
                    "candidates_out_a": sa.get("candidates_out"),
                    "candidates_out_b": sb.get("candidates_out"),
                }
            )

        def _cand_key(c: Dict[str, Any]) -> tuple:
            return (
                c.get("stage_name"),
                c.get("node_id"),
                c.get("node_kind"),
                c.get("node_label"),
                c.get("ord"),
            )

        a_cands = {_cand_key(c): c for c in a.get("candidates", [])}
        b_cands = {_cand_key(c): c for c in b.get("candidates", [])}
        a_keys = set(a_cands.keys())
        b_keys = set(b_cands.keys())

        added = [b_cands[k] for k in sorted(b_keys - a_keys)]
        removed = [a_cands[k] for k in sorted(a_keys - b_keys)]

        changed: List[Dict[str, Any]] = []
        for k in sorted(a_keys & b_keys):
            ca = a_cands[k]
            cb = b_cands[k]
            if (
                ca.get("fused_score") != cb.get("fused_score")
                or ca.get("accepted") != cb.get("accepted")
                or ca.get("rejection_reason") != cb.get("rejection_reason")
                or ca.get("ring_assigned") != cb.get("ring_assigned")
            ):
                changed.append(
                    {
                        "key": {
                            "stage_name": cb.get("stage_name"),
                            "node_id": cb.get("node_id"),
                            "node_kind": cb.get("node_kind"),
                            "node_label": cb.get("node_label"),
                            "ord": cb.get("ord"),
                        },
                        "a": {
                            "fused_score": ca.get("fused_score"),
                            "accepted": ca.get("accepted"),
                            "rejection_reason": ca.get("rejection_reason"),
                            "ring_assigned": ca.get("ring_assigned"),
                        },
                        "b": {
                            "fused_score": cb.get("fused_score"),
                            "accepted": cb.get("accepted"),
                            "rejection_reason": cb.get("rejection_reason"),
                            "ring_assigned": cb.get("ring_assigned"),
                        },
                    }
                )

        a_rings = {r["ring"]: r for r in a.get("rings", [])}
        b_rings = {r["ring"]: r for r in b.get("rings", [])}
        ring_names = list(dict.fromkeys(list(a_rings.keys()) + list(b_rings.keys())))
        ring_diffs: List[Dict[str, Any]] = []
        for ring in ring_names:
            ra = a_rings.get(ring) or {}
            rb = b_rings.get(ring) or {}
            item_a = int(ra.get("item_count") or 0)
            item_b = int(rb.get("item_count") or 0)
            tok_a = int(ra.get("token_count") or 0)
            tok_b = int(rb.get("token_count") or 0)
            ring_diffs.append(
                {
                    "ring": ring,
                    "item_count_a": item_a,
                    "item_count_b": item_b,
                    "item_delta": item_b - item_a,
                    "token_count_a": tok_a,
                    "token_count_b": tok_b,
                    "token_delta": tok_b - tok_a,
                }
            )

        a_layers = {l["layer_id"]: l for l in a.get("prompt_layers", [])}
        b_layers = {l["layer_id"]: l for l in b.get("prompt_layers", [])}
        layer_ids = list(dict.fromkeys(list(a_layers.keys()) + list(b_layers.keys())))
        layer_diffs: List[Dict[str, Any]] = []
        for layer_id in layer_ids:
            la = a_layers.get(layer_id) or {}
            lb = b_layers.get(layer_id) or {}
            tok_a = la.get("token_count")
            tok_b = lb.get("token_count")
            tok_delta = None
            if tok_a is not None and tok_b is not None:
                tok_delta = int(tok_b) - int(tok_a)
            layer_diffs.append(
                {
                    "layer_id": layer_id,
                    "layer_name_a": la.get("layer_name"),
                    "layer_name_b": lb.get("layer_name"),
                    "token_count_a": tok_a,
                    "token_count_b": tok_b,
                    "token_delta": tok_delta,
                    "content_changed": (la.get("content") != lb.get("content")),
                }
            )

        latency_a = int(a.get("latency_ms") or 0)
        latency_b = int(b.get("latency_ms") or 0)
        summary = (
            f"latency {latency_a}ms -> {latency_b}ms (delta {latency_b - latency_a}ms), "
            f"stages {len(a.get('stages', []))} -> {len(b.get('stages', []))}, "
            f"candidates {len(a.get('candidates', []))} -> {len(b.get('candidates', []))}"
        )

        return {
            "a_id": a["id"],
            "b_id": b["id"],
            "stage_diffs": stage_diffs,
            "candidate_diffs": {
                "added_count": len(added),
                "removed_count": len(removed),
                "changed_count": len(changed),
                "added": added,
                "removed": removed,
                "changed": changed,
            },
            "ring_diffs": ring_diffs,
            "layer_diffs": layer_diffs,
            "summary": summary,
        }
