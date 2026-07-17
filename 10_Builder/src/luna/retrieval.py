"""
Unified Retrieval Module
========================

Single retrieval entry point used by both the direct path (engine.py)
and the agentic path (agentic/loop.py).

Phase 1: Extract and unify — no new functionality.
Phase 2 (future): Graph walk via topological retrieval.
Phase 3 (future): LLM-based routing via Haiku manifest selection.
"""

import asyncio
import logging
import re as _re
import time as _time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Matrix `_retrieval_score` values are RRF/composite ranking signals in a tiny
# numeric band. Normalize only when Matrix results cross into the shared
# `RetrievalCandidate.score` domain used by fallback selection and engine ingest.
_MATRIX_SCORE_NORMALIZATION_CEILING = 0.01


# ─── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class RetrievalRequest:
    query: str
    budget_matrix: int = 1500
    budget_nexus_structure: int = 3000
    budget_nexus_extractions: int = 8000
    budget_nexus_chunks: int = 5000
    scopes: Optional[list] = None
    subtask_phase: Optional[object] = None
    include_reflections: bool = True
    include_relational: bool = True
    aperture_preset: str = 'wide'
    aperture_angle: int = 75
    aperture_inner_threshold: float = 0.25
    aperture_breakthrough: float = 0.40
    # Phase 1A.3: hard wall-clock ceiling. UnifiedRetrieval.retrieve() checks
    # the deadline between stages and returns partial results rather than
    # waiting for late stages. 6 s matches observed warm-engine completion
    # plus headroom; tunable per-call.
    budget_ms: float = 6000.0
    # Phase 1A.4: per-stage caps prevent a single in-flight stage from
    # eating the global budget. Each stage runs under
    # `asyncio.wait_for(min(stage_cap, remaining_budget))`; on timeout the
    # stage returns empty and `truncated` flips True. Defaults are sized
    # against observed live timings (matrix tail 5.6 s, collection 3.1 s,
    # llm_router 1.2 s) — generous enough not to starve healthy queries,
    # tight enough to bound the runaway tail.
    stage_budget_matrix_ms: float = 2500.0
    stage_budget_collection_ms: float = 2500.0
    stage_budget_llm_router_ms: float = 2000.0
    # When False (default), retrieve() skips Stage 2 (collection/dataroom). Direct
    # callers (engine._get_collection_context, search_chain) bypass retrieve() and
    # are unaffected. Set True only on opt-in paths that explicitly want dataroom
    # content stitched into memory_context.
    include_dataroom: bool = False
    trace: Optional[object] = None


@dataclass
class RetrievalCandidate:
    content: str
    # "matrix" = direct hybrid/FTS/semantic hit.
    # "graph"  = graph-derived neighbor; `provenance["method"]` distinguishes
    #            typed_traversal (1-hop allowlist) from spreading_activation
    #            (legacy, retired from main retrieve()). See
    #            Docs/bible/Handoffs/RETRIEVAL_CONTRACTS_AND_INVARIANTS.md.
    # "nexus/{collection}" = collection context.
    # "reflection" = personality reflection pass.
    # "relational" = conversation_turns side channel turned first-class.
    source: str
    node_type: str          # FACT, DECISION, CLAIM, CHUNK, etc.
    score: float            # Composite or RRF score
    confidence: float       # Lock-in or extraction confidence
    provenance: dict = field(default_factory=dict)


@dataclass
class RetrievalResult:
    candidates: list[RetrievalCandidate] = field(default_factory=list)
    context_string: str = ""
    nexus_nodes: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    collections_searched: list[str] = field(default_factory=list)
    reflection_mode: str = "standard"
    # Phase 1A.3: stages that finished within budget_ms. Truncation is
    # explicit so callers can never confuse "no context" with "skipped".
    completed_stages: list[str] = field(default_factory=list)
    truncated: bool = False
    # Phase 1C: per-source composition. Populated by UnifiedRetrieval.retrieve()
    # so callers can answer "which source is pulling its weight" without adding
    # bespoke logging to every stage. Shape:
    #   { source_name: {"produced": N, "selected": M, "chars": C} }
    # where `produced` = candidates the stage added pre-router, `selected` =
    # candidates that survived the router/fallback cut, `chars` = total content
    # chars from surviving candidates of that source. Source provenance is lost
    # at ring ingest (all become ContextSource.MEMORY), so this is the last
    # honest picture of cross-source balance.
    source_stats: dict = field(default_factory=dict)
    router_ran: bool = False


# ─── Manifest builder ──────────────────────────────────────────────────────


def _build_source_balanced_manifest(
    candidates: list[RetrievalCandidate],
    limit: int = 20,
) -> list[RetrievalCandidate]:
    """Round-robin per-source selection for the Haiku router's manifest.

    Phase 1C: Matrix candidates carry RRF composite scores (~0.01–0.2)
    while nexus candidates carry confidence scores (0.5–1.0). A raw
    cross-source score sort pushes matrix off the top-K manifest even
    when matrix produces the most candidates — measured across 10 queries,
    matrix was 49 % of the candidate pool and 0 % of final selections.

    This picks each source's best candidates in rounds, so every source
    that produced anything gets a fair shot at the manifest. Cross-source
    competition happens in the router (Haiku sees all of them), not here.
    """
    if not candidates:
        return []
    by_source: dict[str, list[RetrievalCandidate]] = {}
    for c in candidates:
        by_source.setdefault(c.source, []).append(c)
    for group in by_source.values():
        group.sort(key=lambda c: c.score, reverse=True)
    manifest: list[RetrievalCandidate] = []
    while len(manifest) < limit:
        added = False
        for group in by_source.values():
            if len(manifest) >= limit:
                break
            if group:
                manifest.append(group.pop(0))
                added = True
        if not added:
            break
    return manifest


# ─── Query expansion helpers (moved from engine.py) ────────────────────────

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
    """
    from luna.substrate.aibrarian_engine import AiBrarianEngine

    words = _re.findall(r"[a-zA-Z]{3,}", query.lower())
    content_words = [w for w in words if w not in _EXPANSION_STOPWORDS]

    if not content_words:
        return []

    results = []
    seen_ids: set = set()

    # Strategy A: OR-joined query
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


# ─── Reflection mode detection (moved from engine.py) ──────────────────────

def _get_active_reflection_mode(aibrarian, collections_searched: list) -> str:
    """
    Determine reflection mode from the collections that were searched.
    Most permissive mode wins: relational > reflective > precision.
    Default: "reflective".
    """
    if not aibrarian or not collections_searched:
        return "standard"

    MODE_RANK = {"precision": 0, "reflective": 1, "relational": 2}
    RANK_MODE = {v: k for k, v in MODE_RANK.items()}

    max_rank = 0
    for key in collections_searched:
        cfg = aibrarian.registry.collections.get(key)
        if cfg:
            mode = getattr(cfg, 'reflection_mode', None)
            if mode is None and hasattr(cfg, '__getitem__'):
                try:
                    mode = cfg['reflection_mode']
                except (KeyError, TypeError):
                    pass
            rank = MODE_RANK.get(mode or "reflective", 1)
            max_rank = max(max_rank, rank)

    return RANK_MODE.get(max_rank, "reflective")


def _format_reflection_context(reflection_nodes: list) -> str:
    """Format PERSONALITY_REFLECTION nodes as labeled context."""
    if not reflection_nodes:
        return ""
    lines = ["## Luna's reflections on this material\n"]
    for node in reflection_nodes:
        content = node.content if hasattr(node, 'content') else str(node)
        summary = getattr(node, 'summary', "") or ""
        lines.append(f"[REFLECTION] {content}")
        if summary:
            lines.append(f"  — re: {summary}")
        lines.append("")
    return "\n".join(lines)


# ─── Relational context (moved from engine.py) ─────────────────────────────

async def _get_relational_context(matrix_actor, query: str) -> Optional[str]:
    """
    For relational mode: search raw turns in `conversation_turns` by LIKE match.
    """
    if not matrix_actor or not matrix_actor.is_ready:
        return None

    try:
        db = getattr(matrix_actor, "_db", None)
        if not db:
            return None

        pattern = f"%{query}%"
        rows = await db.fetchall(
            """
            SELECT role, content, created_at
            FROM conversation_turns
            WHERE content LIKE ?
            ORDER BY created_at DESC
            LIMIT 3
            """,
            (pattern,),
        )

        if not rows:
            return None

        lines = ["## Connected conversations\n"]
        for row in rows:
            role = row[0]
            content = row[1]
            lines.append(f"- [{role}] {content[:300]}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"[RELATIONAL] Conversation search failed: {e}")
        return None


# ─── Unified Retrieval ─────────────────────────────────────────────────────

class UnifiedRetrieval:
    """Single retrieval module used by both direct and agentic paths."""

    def __init__(
        self,
        matrix_actor,
        aibrarian,
        aperture,
        collection_lock_in,
        active_scopes,
        active_project=None,
    ):
        self.matrix_actor = matrix_actor
        self.aibrarian = aibrarian
        self.aperture = aperture
        self.collection_lock_in = collection_lock_in
        self.active_scopes = active_scopes
        self.active_project = active_project
        # Manifest-based retrieval routing (Handoff #48)
        self._manifest_cache: str = ''
        self._manifest_cache_time: float = 0.0
        self._MANIFEST_TTL: float = 300.0  # 5 minutes

    @staticmethod
    def _normalize_matrix_candidate_score(raw_score: float) -> float:
        """Map raw Matrix composite scores into the shared candidate domain.

        Keep this transform monotonic so within-Matrix ordering is preserved,
        while lifting Matrix candidates into a score band that is comparable to
        the confidence-like scales already used by Nexus sources.
        """
        raw = max(0.0, float(raw_score))
        if raw == 0.0:
            return 0.0
        return min(1.0, raw / _MATRIX_SCORE_NORMALIZATION_CEILING)

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Main entry point. Runs all retrieval stages, returns unified result.

        Phase 1A.3: a hard wall-clock budget (`request.budget_ms`) bounds
        total retrieval time. The deadline is checked between stages — if it
        has passed, remaining stages are skipped and partial results are
        returned with `truncated=True`. A single in-flight stage can still
        overshoot because we never preempt mid-stage; that is intentional for
        this slice (per-stage budgets are a later optimization).
        """
        rt: dict = {}
        rt0 = _time.time()
        candidates: list[RetrievalCandidate] = []
        nexus_nodes: list = []
        collections_searched: list[str] = []
        parts: list[str] = []
        completed_stages: list[str] = []
        truncated = False
        # Monotonic clock so deadline math is unaffected by wall-clock jumps.
        deadline = _time.monotonic() + (max(0.0, request.budget_ms) / 1000.0)

        def time_left() -> bool:
            return _time.monotonic() < deadline

        def stage_budget_s(cap_ms: float) -> float:
            """Per-stage cap, never exceeding remaining global budget."""
            remaining = max(0.0, deadline - _time.monotonic())
            return min(max(0.0, cap_ms) / 1000.0, remaining)

        trace = getattr(request, "trace", None)

        def _trace_stage_start(name: str, budget_ms: Optional[float] = None) -> None:
            if trace is None:
                return
            try:
                trace.stage_start(name, budget_ms=budget_ms)
            except Exception:
                pass

        def _trace_stage_end(
            name: str,
            *,
            candidates_in: Optional[int] = None,
            candidates_out: Optional[int] = None,
            status: str = "ok",
            error: Optional[str] = None,
        ) -> None:
            if trace is None:
                return
            try:
                trace.stage_end(
                    name,
                    candidates_in=candidates_in,
                    candidates_out=candidates_out,
                    status=status,
                    error=error,
                )
            except Exception:
                pass

        def _trace_record_candidate(stage_name: str, ord_idx: int, cand: RetrievalCandidate) -> None:
            if trace is None:
                return
            try:
                prov = cand.provenance if isinstance(cand.provenance, dict) else {}
                trace.record_candidate(
                    stage_name,
                    ord=ord_idx,
                    node_id=prov.get("node_id"),
                    node_kind=getattr(cand, "node_type", None),
                    node_label=(cand.content or "")[:120],
                    raw_score=float(getattr(cand, "score", 0.0)),
                    fused_score=float(getattr(cand, "score", 0.0)),
                    lock_in=float(getattr(cand, "confidence", 0.0)) if getattr(cand, "confidence", None) is not None else None,
                    relevance=float(getattr(cand, "score", 0.0)),
                    accepted=True,
                )
            except Exception:
                pass

        # ── Stage 1: Matrix (conversation memory, personal knowledge) ──
        rt["matrix_get_context"] = 0.0
        _matrix_in = len(candidates)
        _matrix_status = "ok"
        _matrix_error = None
        _trace_stage_start("matrix", budget_ms=request.stage_budget_matrix_ms)
        if not time_left():
            truncated = True
            _matrix_status = "skipped"
        elif self.matrix_actor and self.matrix_actor.is_ready:
            inner_matrix = getattr(self.matrix_actor, '_matrix', None)
            if inner_matrix:
                t = _time.time()
                # Aperture controls scope breadth
                if request.aperture_angle <= 20:  # TUNNEL
                    effective_scopes = [s for s in (request.scopes or ['global'])
                                        if s != 'global']
                    if not effective_scopes:
                        effective_scopes = ['global']
                elif request.aperture_angle <= 60:  # NARROW / BALANCED
                    effective_scopes = request.scopes or ['global']
                else:  # WIDE / OPEN
                    effective_scopes = None  # search everything
                # Phase 1A.4: per-stage cap. Matrix tail observed at 2.7-5.6s
                # in the live engine — cap at request.stage_budget_matrix_ms
                # (default 2.5s) so a slow vector search never starves the
                # rest of the pipeline.
                stage_s = stage_budget_s(request.stage_budget_matrix_ms)
                try:
                    memory_nodes = await asyncio.wait_for(
                        inner_matrix.get_context(
                            request.query, max_tokens=request.budget_matrix,
                            scopes=effective_scopes,
                        ),
                        timeout=stage_s if stage_s > 0 else 0.001,
                    )
                except asyncio.TimeoutError:
                    truncated = True
                    memory_nodes = []
                    _matrix_status = "timeout"
                    _matrix_error = "stage_timeout"
                    logger.warning(
                        "[STAGE-TIMEOUT] matrix_get_context exceeded %.2fs cap", stage_s,
                    )
                else:
                    completed_stages.append("matrix")
                    if memory_nodes:
                        for node in memory_nodes:
                            text = node.summary or node.content
                            # Typed traversal tags graph-derived neighbors in
                            # MemoryMatrix.get_context() with `_graph_expansion`.
                            # Surface those as first-class `source="graph"`
                            # candidates with full provenance so source
                            # accounting reflects the split honestly and direct
                            # matrix hits stay distinguishable from graph hits.
                            expansion = getattr(node, '_graph_expansion', None)
                            if expansion is not None:
                                raw_score = float(expansion.get("rank_score", 0.0))
                                # Traversal rank = strength * max(lock_in, 0.01),
                                # already bounded in the shared [0.0, 1.0]
                                # ingest domain. No re-normalization needed.
                                candidates.append(RetrievalCandidate(
                                    content=text,
                                    source="graph",
                                    node_type=getattr(node, 'node_type', 'MEMORY'),
                                    score=raw_score,
                                    confidence=getattr(node, 'lock_in', getattr(node, 'confidence', 0.5)),
                                    provenance={
                                        'node_id': getattr(node, 'id', None),
                                        'method': 'typed_traversal',
                                        'seed_node_id': expansion.get("seed_id"),
                                        'edge_type': expansion.get("edge_type"),
                                        'edge_strength': expansion.get("strength"),
                                        'rank_score': raw_score,
                                    },
                                ))
                            else:
                                raw_score = getattr(node, '_retrieval_score', 1.0)
                                score = self._normalize_matrix_candidate_score(raw_score)
                                candidates.append(RetrievalCandidate(
                                    content=text,
                                    source="matrix",
                                    node_type=getattr(node, 'node_type', 'MEMORY'),
                                    score=score,
                                    confidence=getattr(node, 'confidence', 1.0),
                                    provenance={
                                        'node_id': getattr(node, 'id', None),
                                        'raw_score': raw_score,
                                        'normalized_score': score,
                                    },
                                ))
                        # Format for context string
                        matrix_context = self.matrix_actor._format_context(memory_nodes)
                        if matrix_context:
                            parts.append(matrix_context)
                rt["matrix_get_context"] = _time.time() - t
            else:
                completed_stages.append("matrix")  # nothing to do, but stage attempted
        else:
            completed_stages.append("matrix")  # no actor; stage attempted

        for _i, _cand in enumerate(candidates[_matrix_in:]):
            _trace_record_candidate("matrix", _i, _cand)
        _trace_stage_end(
            "matrix",
            candidates_in=_matrix_in,
            candidates_out=len(candidates),
            status=_matrix_status,
            error=_matrix_error,
        )

        # ── Stage 1.5: Graph enrichment ──
        # Typed 1-hop traversal is now the canonical graph enrichment path
        # and runs inside MemoryMatrix.get_context(). Those neighbors are
        # split out as `source="graph"` / `method="typed_traversal"` in the
        # matrix stage above. The legacy spreading_activation walk
        # (`_graph_walk`) is retired from the main retrieve() path to avoid
        # two competing graph-enrichment semantics. The helper is kept for
        # potential specialty use (e.g. server.py graph endpoints). The
        # stage is still recorded in `completed_stages` (when the budget
        # allows) so runbooks/monitors observing the stage list do not
        # regress.
        rt["graph_walk"] = 0.0
        _trace_stage_start("graph_walk", budget_ms=0.0)
        _graph_status = "ok"
        if not time_left():
            truncated = True
            _graph_status = "skipped"
        else:
            completed_stages.append("graph_walk")
        _trace_stage_end(
            "graph_walk",
            candidates_in=len(candidates),
            candidates_out=len(candidates),
            status=_graph_status,
        )

        # ── Stage 2: Collection context (Nexus 4-tier cascade) ──
        _collection_in = len(candidates)
        _collection_status = "ok"
        _collection_error = None
        _trace_stage_start("collection_context", budget_ms=request.stage_budget_collection_ms)
        if not time_left():
            truncated = True
            _collection_status = "skipped"
        elif not request.include_dataroom:
            rt["collection_context"] = 0.0
            completed_stages.append("collection_context_skipped")
            _collection_status = "skipped"
            # WARNING level matches the [STAGE-TIMEOUT] / [RETRIEVE-TIMING]
            # / [COLLECTION-TIMING] pattern in this module; INFO is filtered
            # out by luna.retrieval's logger config.
            logger.warning(
                "[RETRIEVAL] dataroom skipped: include_dataroom=False"
            )
        else:
            t = _time.time()
            # Phase 1A.4: per-stage cap. Collection context tail observed at
            # 1.7-3.1s; cap at request.stage_budget_collection_ms.
            stage_s = stage_budget_s(request.stage_budget_collection_ms)
            try:
                if request.subtask_phase and getattr(request.subtask_phase, 'decomposed_queries', None):
                    coro = self._get_collection_context_multi(
                        request.subtask_phase.decomposed_queries, request,
                    )
                else:
                    coro = self._get_collection_context(request.query, request)
                collection_context, col_nexus_nodes, col_collections = await asyncio.wait_for(
                    coro, timeout=stage_s if stage_s > 0 else 0.001,
                )
            except asyncio.TimeoutError:
                truncated = True
                collection_context = ""
                col_nexus_nodes = []
                col_collections = []
                _collection_status = "timeout"
                _collection_error = "stage_timeout"
                logger.warning(
                    "[STAGE-TIMEOUT] collection_context exceeded %.2fs cap", stage_s,
                )
            else:
                completed_stages.append("collection_context")
            rt["collection_context"] = _time.time() - t

            nexus_nodes.extend(col_nexus_nodes)
            collections_searched = col_collections

            if collection_context:
                parts.append(collection_context)
                for node in col_nexus_nodes:
                    candidates.append(RetrievalCandidate(
                        content=node.get("content", ""),
                        source=node.get("source", "nexus"),
                        node_type=node.get("node_type", "CHUNK"),
                        score=node.get("confidence", 0.5),
                        confidence=node.get("confidence", 0.5),
                        provenance=node,
                    ))

        for _i, _cand in enumerate(candidates[_collection_in:]):
            _trace_record_candidate("collection_context", _i, _cand)
        _trace_stage_end(
            "collection_context",
            candidates_in=_collection_in,
            candidates_out=len(candidates),
            status=_collection_status,
            error=_collection_error,
        )

        # ── Stage 2.5: Reflection mode detection + retrieval ──
        # Only activate if nexus nodes were actually retrieved — searching a
        # collection with 0 results doesn't mean Luna is "reading" a document.
        active_mode = (
            _get_active_reflection_mode(self.aibrarian, collections_searched)
            if nexus_nodes
            else "standard"
        )

        rt["reflection_search"] = 0.0
        _reflection_in = len(candidates)
        _reflection_status = "ok"
        _trace_stage_start("reflection_search", budget_ms=0.0)
        if not time_left():
            truncated = True
            _reflection_status = "skipped"
        else:
            completed_stages.append("reflection_search")
            if (
                request.include_reflections
                and active_mode in ("reflective", "relational")
                and self.matrix_actor and self.matrix_actor.is_ready
            ):
                t = _time.time()
                try:
                    matrix_ops = self.matrix_actor._matrix if hasattr(self.matrix_actor, '_matrix') else None
                    if matrix_ops:
                        reflection_results = await matrix_ops.fts5_search(
                            request.query, limit=5
                        )
                        reflection_nodes = [
                            node for node, _score in reflection_results
                            if getattr(node, 'node_type', '') == 'PERSONALITY_REFLECTION'
                        ]
                        if reflection_nodes:
                            reflection_context = _format_reflection_context(reflection_nodes)
                            parts.append(reflection_context)
                            for node in reflection_nodes:
                                candidates.append(RetrievalCandidate(
                                    content=node.content if hasattr(node, 'content') else str(node),
                                    source="reflection",
                                    node_type="PERSONALITY_REFLECTION",
                                    score=getattr(node, '_retrieval_score', 0.7),
                                    confidence=getattr(node, 'confidence', 0.7),
                                ))
                            logger.info(f"[REFLECTION-RETRIEVAL] Injected {len(reflection_nodes)} reflections (mode={active_mode})")
                except Exception as e:
                    logger.warning(f"[REFLECTION-RETRIEVAL] Failed: {e}")
                rt["reflection_search"] = _time.time() - t

        for _i, _cand in enumerate(candidates[_reflection_in:]):
            _trace_record_candidate("reflection_search", _i, _cand)
        _trace_stage_end(
            "reflection_search",
            candidates_in=_reflection_in,
            candidates_out=len(candidates),
            status=_reflection_status,
        )

        # ── Stage 2.75: Relational retrieval ──
        rt["relational_context"] = 0.0
        _relational_in = len(candidates)
        _relational_status = "ok"
        _trace_stage_start("relational_context", budget_ms=0.0)
        if not time_left():
            truncated = True
            _relational_status = "skipped"
        else:
            completed_stages.append("relational_context")
            if request.include_relational and active_mode == "relational":
                t = _time.time()
                relational_context = await _get_relational_context(self.matrix_actor, request.query)
                if relational_context:
                    parts.append(relational_context)
                    candidates.append(RetrievalCandidate(
                        content=relational_context,
                        source="relational",
                        node_type="CONVERSATION_LINK",
                        score=0.75,
                        confidence=0.7,
                        provenance={
                            "path": "conversation_turns",
                            "match": "LIKE",
                            "block_chars": len(relational_context),
                        },
                    ))
                    logger.info("[RELATIONAL] Injected conversation context")
                rt["relational_context"] = _time.time() - t

        for _i, _cand in enumerate(candidates[_relational_in:]):
            _trace_record_candidate("relational_context", _i, _cand)
        _trace_stage_end(
            "relational_context",
            candidates_in=_relational_in,
            candidates_out=len(candidates),
            status=_relational_status,
        )

        # Phase 1C: snapshot pre-router per-source candidate counts. `candidates`
        # at this point is the full pool that every later stage gets to see.
        produced_by_source: dict[str, int] = {}
        for _c in candidates:
            produced_by_source[_c.source] = produced_by_source.get(_c.source, 0) + 1

        # ── Stage 3: LLM Router (manifest-based selection) ──
        # Skipped when over budget; small-pool fallback below is pure Python
        # so it always runs to keep selection sane.
        MIN_CANDIDATES_FOR_ROUTING = 8
        rt["llm_router"] = 0.0
        ran_llm_router = False
        _router_in = len(candidates)
        _router_status = "ok"
        _router_error = None
        _trace_stage_start("llm_router", budget_ms=request.stage_budget_llm_router_ms)
        if not time_left():
            truncated = True
            _router_status = "skipped"
        elif len(candidates) >= MIN_CANDIDATES_FOR_ROUTING:
            t = _time.time()
            total_budget = (
                request.budget_matrix + request.budget_nexus_structure
                + request.budget_nexus_extractions + request.budget_nexus_chunks
            )
            # Phase 1A.4: per-stage cap. LLM router can stall on Claude API;
            # cap at request.stage_budget_llm_router_ms. On timeout we fall
            # through to the small-pool fallback below — never hard fail.
            stage_s = stage_budget_s(request.stage_budget_llm_router_ms)
            try:
                selected = await asyncio.wait_for(
                    self._route_via_manifest(
                        query=request.query,
                        candidates=candidates,
                        token_budget=total_budget,
                        aperture_preset=request.aperture_preset,
                    ),
                    timeout=stage_s if stage_s > 0 else 0.001,
                )
            except asyncio.TimeoutError:
                truncated = True
                selected = None
                _router_status = "timeout"
                _router_error = "stage_timeout"
                logger.warning(
                    "[STAGE-TIMEOUT] llm_router exceeded %.2fs cap", stage_s,
                )

            if selected is not None:
                completed_stages.append("llm_router")
                ran_llm_router = True
                # Rebuild parts from selected candidates
                parts = []
                for c in selected:
                    if c.source == "matrix":
                        parts.append(f"[Matrix {c.node_type}] {c.content}")
                    elif c.source == "graph":
                        parts.append(f"[Graph {c.node_type}] {c.content}")
                    elif c.source == "reflection":
                        parts.append(f"[Reflection] {c.content}")
                    elif c.source == "relational":
                        parts.append(c.content)
                    else:
                        parts.append(f"[{c.source} {c.node_type}] {c.content}")
                candidates = selected
            rt["llm_router"] = _time.time() - t
        else:
            _router_status = "skipped"

        if not ran_llm_router and candidates:
            # Small pool or LLM router dropped — score-based truncation. This
            # is in-memory only; runs even when the budget is exhausted so
            # truncated responses still get coherent candidate selection.
            total_budget = (
                request.budget_matrix + request.budget_nexus_structure
                + request.budget_nexus_extractions + request.budget_nexus_chunks
            )
            candidates = self._fallback_selection(candidates, total_budget)

        # ── Lexicon rerank pass ───────────────────────────────────────────
        # Re-sort candidates by MiniLM cosine similarity against the query.
        # Runs after fallback selection so we rerank only the final pool.
        # Fire-and-forget: any failure leaves candidates in their current order.
        if candidates:
            try:
                from luna.lexicon.api import rerank as _lex_rerank
                _rerank_texts = [c.content for c in candidates]
                _loop = asyncio.get_running_loop()
                _reranked = await asyncio.wait_for(
                    _loop.run_in_executor(None, _lex_rerank, request.query, _rerank_texts),
                    timeout=5.0,
                )
                if _reranked and len(_reranked) == len(candidates):
                    # _reranked is [{value: text, score: float}] sorted desc by score
                    _score_map = {
                        r["value"]: r["score"]
                        for r in _reranked
                        if isinstance(r, dict) and "value" in r and "score" in r
                    }
                    candidates.sort(
                        key=lambda c: _score_map.get(c.content, 0.0),
                        reverse=True,
                    )
                    logger.info("[RETRIEVAL] Lexicon rerank: %d candidates reordered", len(candidates))
            except Exception as _rerank_err:
                logger.debug("[RETRIEVAL] Rerank skipped: %s", _rerank_err)

        if ran_llm_router:
            for _i, _cand in enumerate(candidates):
                _trace_record_candidate("llm_router", _i, _cand)
        _trace_stage_end(
            "llm_router",
            candidates_in=_router_in,
            candidates_out=len(candidates),
            status=_router_status,
            error=_router_error,
        )

        # ── Assemble ──
        rt["retrieve_total"] = _time.time() - rt0
        try:
            logger.warning(
                "[RETRIEVE-TIMING] total=%.3fs | truncated=%s | completed=%s | %s",
                rt["retrieve_total"],
                truncated,
                ",".join(completed_stages) or "none",
                " | ".join(
                    f"{k}={v:.3f}s"
                    for k, v in sorted(rt.items(), key=lambda x: -x[1])
                    if k != "retrieve_total"
                ),
            )
        except Exception:
            pass

        context_string = "\n\n".join(parts) if parts else ""

        # Phase 1C: final per-source composition. Compares survivors against the
        # pre-router pool so we can see stages producing candidates that never
        # make it into final context. `chars` counts only survivors — it's the
        # honest budget footprint.
        source_stats: dict[str, dict[str, int]] = {}
        for src, produced in produced_by_source.items():
            source_stats[src] = {"produced": produced, "selected": 0, "chars": 0}
        for _c in candidates:
            s = source_stats.setdefault(_c.source, {"produced": 0, "selected": 0, "chars": 0})
            s["selected"] += 1
            s["chars"] += len(_c.content or "")
        try:
            # WARNING level so it sits alongside [RETRIEVE-TIMING] — makes
            # per-retrieval source balance inspectable in any engine log
            # without needing a bespoke harness.
            logger.warning(
                "[SOURCE-COMPOSITION] router_ran=%s truncated=%s | %s",
                ran_llm_router, truncated,
                " | ".join(
                    f"{src}: {st['selected']}/{st['produced']} ({st['chars']}ch)"
                    for src, st in sorted(source_stats.items())
                ) or "none",
            )
        except Exception:
            pass

        return RetrievalResult(
            candidates=candidates,
            context_string=context_string,
            nexus_nodes=nexus_nodes,
            timings=rt,
            collections_searched=collections_searched,
            reflection_mode=active_mode,
            completed_stages=completed_stages,
            truncated=truncated,
            source_stats=source_stats,
            router_ran=ran_llm_router,
        )

    # ── Graph walk (Phase 2: Topological retrieval) ──────────────────────

    async def _graph_walk(
        self,
        seed_candidates: list[RetrievalCandidate],
        max_depth: int = 2,
        decay: float = 0.5,
        max_results: int = 10,
        scope: Optional[str] = None,
    ) -> list[RetrievalCandidate]:
        """
        Layer 2: Topological retrieval via spreading activation.

        Takes top candidates from Matrix search as seeds. Spreads activation
        through graph edges. Returns connected nodes that share no vocabulary
        with the original query but are structurally related.
        """
        inner_matrix = getattr(self.matrix_actor, '_matrix', None)
        if not inner_matrix:
            return []
        graph = getattr(inner_matrix, 'graph', None)
        if not graph:
            return []

        # Extract seed node IDs from Matrix candidates
        seed_ids = []
        for c in seed_candidates:
            node_id = c.provenance.get('node_id')
            if node_id:
                seed_ids.append(node_id)

        if not seed_ids:
            return []

        # Run spreading activation
        try:
            activations = await graph.spreading_activation(
                start_nodes=seed_ids,
                decay=decay,
                max_depth=max_depth,
                scope=scope,
            )
        except Exception as e:
            logger.warning(f"[GRAPH-WALK] spreading_activation failed: {e}")
            return []

        # Filter out seed nodes and sort by activation score
        seed_set = set(seed_ids)
        walked = [
            (nid, score) for nid, score in activations.items()
            if nid not in seed_set and score > 0.05
        ]
        walked.sort(key=lambda x: x[1], reverse=True)
        walked = walked[:max_results]

        # Resolve node IDs back to content
        results = []
        for node_id, activation_score in walked:
            node = await self._resolve_node(inner_matrix, node_id)
            if not node:
                continue
            results.append(RetrievalCandidate(
                content=node.summary or node.content,
                source='graph',
                node_type=getattr(node, 'node_type', 'MEMORY'),
                score=activation_score,
                confidence=getattr(node, 'lock_in', 0.5),
                provenance={
                    'node_id': node_id,
                    'activation_score': activation_score,
                    'seed_nodes': seed_ids[:3],
                    'method': 'spreading_activation',
                },
            ))

        logger.info(
            "[GRAPH-WALK] Seeds: %d, Activated: %d, Returned: %d, Top: %s (%.3f)",
            len(seed_ids), len(activations), len(results),
            results[0].provenance.get('node_id', '?')[:20] if results else 'none',
            results[0].score if results else 0,
        )

        return results

    async def _resolve_node(self, inner_matrix, node_id: str):
        """Resolve a node ID to a MemoryNode object."""
        # Use get_node if available (MemoryMatrix method)
        if hasattr(inner_matrix, 'get_node'):
            try:
                node = await inner_matrix.get_node(node_id)
                if node:
                    return node
            except Exception:
                pass

        # Fallback: direct database lookup
        try:
            db = getattr(inner_matrix, 'db', None)
            if db and hasattr(db, 'fetchone'):
                row = await db.fetchone(
                    "SELECT * FROM memory_nodes WHERE id = ? AND namespace = 'active'",
                    (node_id,),
                )
                if row:
                    from luna.substrate.memory import MemoryNode
                    return MemoryNode(**dict(row))
        except Exception:
            pass

        return None

    def _format_graph_context(self, candidates: list[RetrievalCandidate]) -> str:
        """Format graph-walked candidates as labeled context."""
        lines = ["## Connected knowledge (graph)\n"]
        for c in candidates:
            lines.append(f"[{c.node_type}] {c.content[:500]}")
        return "\n".join(lines)

    # ── LLM Router (Phase 3: Manifest-based selection) ──────────────────

    async def _route_via_manifest(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        token_budget: int = 10000,
        max_selections: int = 12,
        aperture_preset: str = 'wide',
    ) -> list[RetrievalCandidate]:
        """
        Layer 3: LLM-based manifest routing.

        Builds a metadata manifest of all candidates, asks Haiku
        which ones best answer the query within the token budget.
        Falls back to score-based truncation if Haiku is unavailable.
        """
        # Try LLM routing first
        try:
            from luna.inference.haiku_subtask_backend import HaikuSubtaskBackend
            backend = HaikuSubtaskBackend()
            if not backend.is_loaded:
                return self._fallback_selection(candidates, token_budget)
        except ImportError:
            return self._fallback_selection(candidates, token_budget)

        # Build manifest (metadata only, ~80 chars content preview per candidate).
        # Cap at 20 to keep the routing call fast.
        manifest_candidates = _build_source_balanced_manifest(candidates, limit=20)
        # Build index mapping back to original list
        candidate_map = {i: c for i, c in enumerate(manifest_candidates)}

        manifest_lines = []
        for i, c in enumerate(manifest_candidates):
            preview = (c.content or '')[:80].replace('\n', ' ')
            manifest_lines.append(
                f'[{i}] {c.source} | {c.node_type} | '
                f'score={c.score:.3f} | conf={c.confidence:.2f} | '
                f'{preview}'
            )
        manifest = '\n'.join(manifest_lines)

        system_prompt = (
            'You are a retrieval router for a knowledge system. '
            'Given a user query and a manifest of candidate knowledge items, '
            'select the items that best answer the query. '
            'Return ONLY a comma-separated list of item numbers (e.g. 0,3,7,11). '
            f'Select up to {max_selections} items. Prioritize:\n'
            '- Direct relevance to the query\n'
            '- Cross-domain connections (items from different sources that illuminate each other)\n'
            '- High confidence items over low confidence\n'
            '- Contradictions and evidence (CONTRADICTS/SUPPORTS) are high value\n'
            'Return ONLY the numbers, nothing else.'
        )

        # Aperture-aware guidance
        _aperture_guidance = {
            'tunnel': '\nFocus mode: DEEP FOCUS. Strongly prefer candidates from the active project. Only include outside items if they directly contradict or critically support the query.',
            'narrow': '\nFocus mode: FOCUSED. Prefer project-relevant candidates. Allow closely related items from other sources.',
            'balanced': '\nFocus mode: BALANCED. Mix project-relevant and cross-domain candidates.',
            'wide': '\nFocus mode: BROAD. Actively seek diverse candidates. Cross-domain connections are valuable.',
            'open': '\nFocus mode: EXPLORATION. Maximize diversity. Every source is equally weighted. Seek unexpected connections.',
        }
        guidance = _aperture_guidance.get(aperture_preset, '')
        if guidance:
            system_prompt += guidance

        user_msg = f'Query: {query}\n\nManifest ({len(manifest_candidates)} items):\n{manifest}'

        import re
        t0 = _time.time()
        try:
            result = await backend.generate(
                user_message=user_msg,
                system_prompt=system_prompt,
                max_tokens=100,
            )
            elapsed = _time.time() - t0
            logger.info("[LLM-ROUTER] Haiku responded in %.2fs: %s", elapsed, result.text[:80])
        except Exception as e:
            logger.warning("[LLM-ROUTER] Haiku call failed (%.2fs): %s", _time.time() - t0, e)
            return self._fallback_selection(candidates, token_budget)

        # Parse response: extract integers from comma-separated list
        indices = []
        for token in re.findall(r'\d+', result.text):
            idx = int(token)
            if idx in candidate_map:
                indices.append(idx)

        if not indices:
            logger.warning("[LLM-ROUTER] No valid indices parsed from: %s", result.text[:80])
            return self._fallback_selection(candidates, token_budget)

        # Deduplicate while preserving order
        seen: set = set()
        selected: list[RetrievalCandidate] = []
        for idx in indices:
            if idx not in seen:
                seen.add(idx)
                selected.append(candidate_map[idx])

        logger.info(
            "[LLM-ROUTER] Selected %d/%d candidates: sources=%s",
            len(selected), len(candidates),
            ', '.join(sorted(set(c.source for c in selected))),
        )

        return selected

    def _fallback_selection(
        self,
        candidates: list[RetrievalCandidate],
        token_budget: int = 10000,
    ) -> list[RetrievalCandidate]:
        """Score-based truncation fallback when LLM router is unavailable."""
        sorted_candidates = sorted(candidates, key=lambda c: c.score, reverse=True)

        selected = []
        chars_used = 0
        chars_budget = token_budget * 4  # ~4 chars per token

        for c in sorted_candidates:
            c_chars = len(c.content)
            if chars_used + c_chars > chars_budget:
                continue
            selected.append(c)
            chars_used += c_chars

        return selected

    # ── Manifest-based source selection (Handoff #48) ──────────────────────

    async def _select_collections_via_manifest(
        self,
        query: str,
        lock_in_map: dict[str, float],
        inner_thresh: float,
    ) -> list[str]:
        """Use Haiku to select relevant collections based on a source manifest.

        Falls back to lock-in-based selection on any error.
        """
        try:
            from luna.retrieval_manifest import build_source_manifest, select_sources

            # Build or refresh manifest (5-min TTL)
            now = _time.time()
            if not self._manifest_cache or (now - self._manifest_cache_time) > self._MANIFEST_TTL:
                self._manifest_cache = build_source_manifest(self.aibrarian)
                self._manifest_cache_time = now

            if not self._manifest_cache:
                return self._fallback_collection_selection(lock_in_map, inner_thresh)

            # Get Haiku backend
            from luna.inference.haiku_subtask_backend import HaikuSubtaskBackend
            backend = HaikuSubtaskBackend()
            if not backend.is_loaded:
                logger.warning("[MANIFEST] Haiku backend not available, falling back")
                return self._fallback_collection_selection(lock_in_map, inner_thresh)

            selected = await select_sources(query, self._manifest_cache, backend)
            logger.info("[MANIFEST] Selected: %s", selected)

            if not selected:
                return self._fallback_collection_selection(lock_in_map, inner_thresh)

            # Filter to only connected collections, preserve selection order
            collections_to_search = [
                s.replace("collection:", "")
                for s in selected
                if s.startswith("collection:")
                and s.replace("collection:", "") in self.aibrarian.connections
            ]

            # Always include primary collections even if Haiku didn't select them
            for key, cfg in self.aibrarian.registry.collections.items():
                if not cfg.enabled:
                    continue
                if getattr(cfg, 'grounding_priority', 'supplemental') == 'primary':
                    if key not in collections_to_search and key in self.aibrarian.connections:
                        collections_to_search.append(key)

            return collections_to_search

        except Exception as e:
            logger.warning("[MANIFEST] Selector failed, falling back: %s", e)
            return self._fallback_collection_selection(lock_in_map, inner_thresh)

    def _fallback_collection_selection(
        self,
        lock_in_map: dict[str, float],
        inner_thresh: float,
    ) -> list[str]:
        """Original lock-in + grounding_priority collection selection."""
        collections_to_search: list[str] = []
        for key, cfg in self.aibrarian.registry.collections.items():
            if not cfg.enabled:
                continue
            is_primary = getattr(cfg, 'grounding_priority', 'supplemental') == 'primary'
            if is_primary:
                # Primary collections always searched regardless of lock-in
                collections_to_search.append(key)
            elif lock_in_map:
                # Only search supplemental/background collections if they have lock-in history
                li = lock_in_map.get(key, 0.0)
                if li >= inner_thresh or li > 0:
                    collections_to_search.append(key)
            # Empty lock_in_map = fresh start → skip non-primary collections entirely
            # (Luna can query them on demand via the AiBrarian)

        def _sort_key(k):
            cfg = self.aibrarian.registry.collections.get(k)
            if cfg and getattr(cfg, 'grounding_priority', 'supplemental') == 'primary':
                return 0
            return 1
        collections_to_search.sort(key=_sort_key)
        return collections_to_search

    # ── Collection context (moved from engine.py _get_collection_context) ──

    async def _get_collection_context(
        self, query: str, request: RetrievalRequest,
    ) -> tuple[str, list, list[str]]:
        """
        Aperture-driven 4-tier cascade over Nexus collections.
        Returns (context_string, nexus_nodes, collections_searched).
        """
        import asyncio

        voice_parts: list[str] = []
        if self.active_project:
            search_cfg = None
            try:
                from luna.tools.search_chain import SearchChainConfig
                search_cfg = SearchChainConfig.default()
            except Exception:
                pass
            if search_cfg:
                try:
                    from luna.tools.search_chain import run_search_chain
                    # search_chain needs an engine-like object — pass self for now
                    # but it only uses .aibrarian, so we build a thin proxy
                    results = await run_search_chain(search_cfg, query, self)
                except Exception as e:
                    logger.warning(f"[PHASE2] search_chain failed: {e}")
                    results = []
                for r in (results or []):
                    content = r.get("content", "")
                    source = r.get("source", "collection")
                    if content:
                        voice_parts.append(f"[{source}]\n{content}")

        if not self.aibrarian:
            return "", [], []

        aperture = self.aperture.state
        inner_thresh = aperture.inner_ring_threshold

        # Gather lock-in records
        lock_in_map: dict[str, float] = {}
        if self.collection_lock_in:
            try:
                records = await self.collection_lock_in.get_all()
                lock_in_map = {r.collection_key: r.lock_in for r in records}
            except Exception:
                pass

        # Manifest-based source selection (Handoff #48)
        collections_to_search = await self._select_collections_via_manifest(
            query, lock_in_map, inner_thresh,
        )
        logger.info(f"[PHASE2] Collections to search: {collections_to_search}")

        if not collections_to_search:
            return "", [], collections_to_search

        # ── Dynamic budget scaling ──────────────────────────────────
        # Fewer collections = deeper per-collection retrieval.
        # Depth signals or narrow aperture also boost budgets.
        struct_budget = request.budget_nexus_structure
        content_budget = request.budget_nexus_extractions
        chunk_budget = request.budget_nexus_chunks

        n_cols = len(collections_to_search)
        if n_cols <= 1:
            _focus_mult = 2.0
        elif n_cols <= 2:
            _focus_mult = 1.5
        elif n_cols <= 3:
            _focus_mult = 1.2
        else:
            _focus_mult = 1.0

        # Aperture boost: narrow/tunnel = deeper retrieval
        if request.aperture_angle <= 30:
            _focus_mult *= 1.3
        elif request.aperture_angle <= 50:
            _focus_mult *= 1.1

        if _focus_mult > 1.0:
            content_budget = int(content_budget * _focus_mult)
            chunk_budget = int(chunk_budget * _focus_mult)
            logger.info(
                "[PHASE2] Budget scaled: %.1fx (collections=%d, aperture=%d) → "
                "extractions=%d, chunks=%d",
                _focus_mult, n_cols, request.aperture_angle,
                content_budget, chunk_budget,
            )
        parts: list[str] = []
        nexus_nodes: list = []

        _PRIORITY_FLOOR = {"primary": 0.75, "supplemental": 0.5, "background": 0.3}

        import time as _ctime
        _ct: dict = {}
        _ct0 = _ctime.time()

        subtask_phase = request.subtask_phase

        for key in collections_to_search:
            if content_budget <= 0 and chunk_budget <= 0:
                break

            conn = self.aibrarian.connections.get(key)
            if not conn:
                continue

            _ck = _ctime.time()
            _t_ct = _ctime.time()

            from luna.substrate.aibrarian_engine import AiBrarianEngine
            fts_query = AiBrarianEngine._sanitize_fts_query(query)

            cfg = self.aibrarian.registry.collections.get(key)
            priority = getattr(cfg, 'grounding_priority', 'supplemental') if cfg else 'supplemental'
            conf_floor = _PRIORITY_FLOOR.get(priority, 0.5)

            # ── STRUCTURE PASS ──
            for node_type in ('DOCUMENT_SUMMARY', 'TABLE_OF_CONTENTS'):
                if struct_budget <= 0:
                    break
                try:
                    row = conn.conn.execute(
                        "SELECT node_type, content, confidence FROM extractions "
                        "WHERE node_type = ? LIMIT 1",
                        (node_type,),
                    ).fetchone()
                    if row:
                        content = row[1][:struct_budget]
                        parts.append(f"[Nexus/{key} {node_type}]\n{content}")
                        struct_budget -= len(content)
                        nexus_nodes.append({
                            "id": f"nexus:{key}:{node_type}:{len(nexus_nodes)}",
                            "content": content,
                            "node_type": node_type,
                            "source": f"nexus/{key}",
                            "doc_title": cfg.name if cfg else key,
                            "confidence": max(row[2] if len(row) > 2 else 0.85, conf_floor),
                            "grounding_priority": priority,
                        })
                except Exception:
                    pass

            _ct[f"{key}_t0_structure"] = _ctime.time() - _t_ct
            _t_ct = _ctime.time()

            # ── TIER 1: Content extractions FTS5 ──
            ext_rows = []
            try:
                ext_rows = conn.conn.execute(
                    "SELECT e.node_type, e.content, e.confidence "
                    "FROM extractions_fts "
                    "JOIN extractions e ON extractions_fts.rowid = e.rowid "
                    "WHERE extractions_fts MATCH ? "
                    "AND e.node_type NOT IN ('DOCUMENT_SUMMARY', 'TABLE_OF_CONTENTS') "
                    "ORDER BY rank "
                    "LIMIT 15",
                    (fts_query,),
                ).fetchall()
            except Exception:
                pass
            ext_rows = [dict(r) if not isinstance(r, dict) else r for r in ext_rows]
            logger.info("[PHASE2] Tier 1 FTS5 for %s: query='%s', results=%d", key, fts_query[:60], len(ext_rows))

            _ct[f"{key}_t1_fts5_ext"] = _ctime.time() - _t_ct
            _t_ct = _ctime.time()

            # ── TIER 2: Query expansion ──
            if len(ext_rows) < 8:
                expanded_rows = _expand_and_search_extractions(conn, query)
                expanded_rows = [dict(r) if not isinstance(r, dict) else r for r in expanded_rows]
                expanded_rows = [r for r in expanded_rows
                                 if r.get("node_type", "")
                                 not in ('DOCUMENT_SUMMARY', 'TABLE_OF_CONTENTS')]
                ext_rows = list(ext_rows) + expanded_rows

            # Merge (deduplicate) + collect structured nodes
            seen_content: set = set()
            for row in ext_rows:
                if not isinstance(row, dict):
                    try:
                        row = dict(row)
                    except (TypeError, ValueError):
                        continue
                content = row["content"]
                node_type = row["node_type"]
                confidence = row.get("confidence", 0.85)
                if content not in seen_content and content_budget > 0:
                    seen_content.add(content)
                    chunk = content[:content_budget]
                    parts.append(f"[Nexus/{key} {node_type}]\n{chunk}")
                    content_budget -= len(chunk)
                    nexus_nodes.append({
                        "id": f"nexus:{key}:{node_type}:{len(nexus_nodes)}",
                        "content": content,
                        "node_type": node_type,
                        "source": f"nexus/{key}",
                        "doc_title": cfg.name if cfg else key,
                        "confidence": max(confidence, conf_floor),
                        "grounding_priority": priority,
                    })

            _ct[f"{key}_t2_fts5_full"] = _ctime.time() - _t_ct
            _t_ct = _ctime.time()

            # ── TIER 3: Semantic fallback ──
            _tier3_threshold = 5
            if (subtask_phase and getattr(subtask_phase, 'intent', None)
                    and isinstance(subtask_phase.intent, dict)
                    and subtask_phase.intent.get("complexity") == "complex"):
                _tier3_threshold = 15
            if len(seen_content) < _tier3_threshold and content_budget > 300:
                try:
                    sem_results = await self.aibrarian.search(
                        key, query, "semantic", limit=5
                    )
                    if not sem_results:
                        sem_results = await self.aibrarian.search(
                            key, query, "keyword", limit=5
                        )
                    for r in sem_results:
                        content = r.get("snippet") or r.get("content", "")
                        title = r.get("title") or r.get("filename", "")
                        if content and content not in seen_content and content_budget > 0:
                            seen_content.add(content)
                            chunk = content[:content_budget]
                            parts.append(f"[Nexus/{key} chunk: {title}]\n{chunk}")
                            content_budget -= len(chunk)
                            nexus_nodes.append({
                                "id": f"nexus:{key}:chunk:{len(nexus_nodes)}",
                                "content": content,
                                "node_type": "CHUNK",
                                "source": f"nexus/{key}",
                                "doc_title": cfg.name if cfg else key,
                                "confidence": conf_floor,
                                "grounding_priority": priority,
                            })
                except Exception as e:
                    logger.warning(f"[PHASE2] Semantic fallback for {key}: {e}")

            _ct[f"{key}_t3_semantic"] = _ctime.time() - _t_ct
            _t_ct = _ctime.time()

            # ── TIER 4: Raw text chunks ──
            _DEPTH_SIGNALS = {
                'evidence', 'specific', 'detail', 'passage', 'quote',
                'section', 'argue', 'methodology', 'data', 'example',
                'describe', 'text says', 'what does', 'how does', 'explain how',
                'according to', 'what role', 'what did', 'what was', 'tell me about',
                'impact', 'relationship', 'documented', 'find', 'found',
                'system', 'how did', 'why did', 'who', 'when did',
            }
            wants_depth = any(sig in query.lower() for sig in _DEPTH_SIGNALS)
            logger.info(f"[PHASE2] Tier 4 check for {key}: wants_depth={wants_depth}, chunk_budget={chunk_budget}")
            if wants_depth and chunk_budget > 0:
                try:
                    _claim_text = ' '.join(
                        n.get('content', '')[:200] for n in nexus_nodes
                        if n.get('node_type') in ('CLAIM', 'SECTION_SUMMARY')
                        and n.get('source', '').endswith(key)
                    )
                    _META_WORDS = _DEPTH_SIGNALS | {
                        'what', 'how', 'does', 'the', 'about', 'that', 'this', 'was', 'were',
                        'proving', 'show', 'present', 'discuss', 'explain', 'book', 'section',
                        'chapter', 'author', 'argues', 'analysis', 'describes', 'examines',
                        'from', 'with', 'into', 'also', 'both', 'their', 'have', 'been',
                        'which', 'than', 'more', 'most', 'only', 'between', 'other',
                    }
                    _combined = _re.sub(r'[?.,!"\'\-\(\)]', '', (query + ' ' + _claim_text).lower())
                    _all_terms = [w for w in _combined.split() if w not in _META_WORDS and len(w) > 3]
                    from collections import Counter
                    _term_counts = Counter(_all_terms)
                    _top_terms = [t for t, _ in _term_counts.most_common(6)]
                    chunk_fts = ' AND '.join(_top_terms[:4]) if _top_terms else fts_query
                    chunk_rows = conn.conn.execute(
                        "SELECT c.chunk_text, c.section_label "
                        "FROM chunks_fts "
                        "JOIN chunks c ON chunks_fts.rowid = c.rowid "
                        "WHERE chunks_fts MATCH ? "
                        "ORDER BY rank LIMIT 5",
                        (chunk_fts,),
                    ).fetchall()
                    if not chunk_rows:
                        # AND too restrictive — fall back to OR
                        _content_terms = [w for w in _re.findall(r"[a-zA-Z]{3,}", query.lower())
                                          if w not in _META_WORDS]
                        chunk_fts_or = ' OR '.join(_content_terms[:5]) if _content_terms else fts_query
                        chunk_rows = conn.conn.execute(
                            "SELECT c.chunk_text, c.section_label "
                            "FROM chunks_fts "
                            "JOIN chunks c ON chunks_fts.rowid = c.rowid "
                            "WHERE chunks_fts MATCH ? "
                            "ORDER BY rank LIMIT 5",
                            (chunk_fts_or,),
                        ).fetchall()
                    logger.info(f"[PHASE2] Tier 4 chunk query for {key}: '{chunk_fts}'")
                    for row in chunk_rows:
                        text = row[0][:chunk_budget]
                        section = row[1] or ""
                        if text and text not in seen_content and chunk_budget > 0:
                            seen_content.add(text)
                            label = f" ({section})" if section else ""
                            parts.append(f"[Nexus/{key} SOURCE_TEXT{label}]\n{text}")
                            chunk_budget -= len(text)
                            nexus_nodes.append({
                                "id": f"nexus:{key}:chunk:{len(nexus_nodes)}",
                                "content": text,
                                "node_type": "SOURCE_TEXT",
                                "source": f"nexus/{key}",
                                "doc_title": cfg.name if cfg else key,
                                "confidence": 0.95,
                                "grounding_priority": priority,
                            })
                    if chunk_rows:
                        logger.info(f"[PHASE2] Tier 4 chunks for {key}: {len(chunk_rows)} raw passages")
                except Exception as e:
                    logger.debug(f"[PHASE2] Tier 4 chunk search for {key}: {e}")

                _ct[f"{key}_t4_chunks"] = _ctime.time() - _t_ct
                _t_ct = _ctime.time()

                # ── TIER 5: Luna's prior reflections on this material ──
                try:
                    refl_rows = conn.conn.execute(
                        "SELECT r.content, r.reflection_type, r.created_at "
                        "FROM reflections_fts "
                        "JOIN reflections r ON reflections_fts.rowid = r.rowid "
                        "WHERE reflections_fts MATCH ? "
                        "LIMIT 3",
                        (fts_query,),
                    ).fetchall()
                    for row in refl_rows:
                        text = row[0]
                        if text and text not in seen_content and content_budget > 0:
                            seen_content.add(text)
                            chunk = text[:content_budget]
                            parts.append(f"[Nexus/{key} LUNA_REFLECTION]\n{chunk}")
                            content_budget -= len(chunk)
                            nexus_nodes.append({
                                "id": f"nexus:{key}:reflection:{len(nexus_nodes)}",
                                "content": text,
                                "node_type": "LUNA_REFLECTION",
                                "source": f"nexus/{key}",
                                "doc_title": cfg.name if cfg else key,
                                "confidence": 0.8,
                                "grounding_priority": priority,
                            })
                    if refl_rows:
                        logger.info(f"[PHASE2] Tier 5: {len(refl_rows)} prior reflections for {key}")
                except Exception:
                    pass

                _ct[f"{key}_t5_reflections"] = _ctime.time() - _t_ct

            _ct[f"{key}_total"] = _ctime.time() - _ck

        _ct["all_collections_total"] = _ctime.time() - _ct0
        try:
            logger.warning(
                "[COLLECTION-TIMING] total=%.3fs | %s",
                _ct["all_collections_total"],
                " | ".join(
                    f"{k}={v:.3f}s"
                    for k, v in sorted(_ct.items(), key=lambda x: -x[1])
                    if k != "all_collections_total" and v > 0.01
                ),
            )
        except Exception:
            pass

        # Write-back: Log access
        for key in collections_to_search:
            conn = self.aibrarian.connections.get(key)
            if conn:
                try:
                    conn.conn.execute(
                        "INSERT INTO access_log (event_type, query, results_count, luna_instance) "
                        "VALUES (?, ?, ?, ?)",
                        ("query", query[:500], len(parts), "luna-default"),
                    )
                    conn.conn.commit()
                except Exception:
                    pass

        # Merge voice-path results
        all_parts = voice_parts + parts

        if not all_parts:
            assembled = ""
        else:
            assembled = "\n\n".join(all_parts)
            logger.info(
                f"[PHASE2] Collection recall: {len(all_parts)} fragments "
                f"(voice={len(voice_parts)}, nexus={len(parts)}), "
                f"{len(assembled)} chars"
            )

        return assembled, nexus_nodes, collections_to_search

    async def _get_collection_context_multi(
        self, queries: list, request: RetrievalRequest,
    ) -> tuple[str, list, list[str]]:
        """Run multiple retrieval queries and merge results."""
        import asyncio

        if not queries:
            return "", [], []

        queries = queries[:4]

        tasks = [self._get_collection_context(q, request) for q in queries]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Merge nexus nodes and collections
        all_nexus_nodes: list = []
        all_collections: list[str] = []
        seen_node_ids: set = set()
        seen_collections: set = set()

        parts: list = []
        seen_content: set = set()

        for query_text, result in zip(queries, results):
            if isinstance(result, Exception):
                logger.warning(f"[MULTI-QUERY] Failed for '{query_text}': {result}")
                continue
            ctx_str, ctx_nodes, ctx_cols = result
            if not ctx_str:
                continue

            # Merge nexus nodes
            for node in ctx_nodes:
                nid = node.get("id")
                if nid not in seen_node_ids:
                    seen_node_ids.add(nid)
                    all_nexus_nodes.append(node)

            # Merge collections
            for col in ctx_cols:
                if col not in seen_collections:
                    seen_collections.add(col)
                    all_collections.append(col)

            # Deduplicate content
            new_fragments = []
            for line in ctx_str.split("\n\n"):
                frag_key = line.strip()[:100]
                if frag_key and frag_key not in seen_content:
                    seen_content.add(frag_key)
                    new_fragments.append(line)

            if new_fragments:
                section = "\n\n".join(new_fragments)
                parts.append(f"[Query: {query_text}]\n{section}")

        if not parts:
            return "", all_nexus_nodes, all_collections

        assembled = "\n\n---\n\n".join(parts)
        logger.info(
            f"[MULTI-QUERY] {len(queries)} queries → {len(parts)} result sections, "
            f"{len(assembled)} chars"
        )
        return assembled, all_nexus_nodes, all_collections
