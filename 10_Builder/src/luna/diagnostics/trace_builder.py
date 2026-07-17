"""Per-request pipeline trace builder (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
import time
import uuid


@dataclass(frozen=True)
class StageRecord:
    stage_idx: int
    stage_name: str
    start_ms: float
    end_ms: float
    budget_ms: Optional[float] = None
    candidates_in: Optional[int] = None
    candidates_out: Optional[int] = None
    status: str = "ok"
    error: Optional[str] = None


@dataclass(frozen=True)
class CandidateRecord:
    stage_name: str
    ord: int
    node_id: Optional[str] = None
    node_kind: Optional[str] = None
    node_label: Optional[str] = None
    raw_score: Optional[float] = None
    fused_score: Optional[float] = None
    lock_in: Optional[float] = None
    relevance: Optional[float] = None
    age_days: Optional[float] = None
    accepted: bool = False
    rejection_reason: Optional[str] = None
    ring_assigned: Optional[str] = None


@dataclass(frozen=True)
class RingRecord:
    ring: str
    item_count: int
    token_count: int
    items: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class PromptLayerRecord:
    layer_idx: int
    layer_id: str
    layer_name: str
    content: Optional[str] = None
    token_count: Optional[int] = None


@dataclass(frozen=True)
class TraceRecord:
    id: str
    ts: float
    query: str
    session_id: Optional[str]
    turn_num: Optional[int]
    route: Optional[str]
    door: Optional[str]
    aperture: Optional[str]
    aperture_deg: Optional[int]
    backend: str
    prepare_only: bool
    latency_ms: int
    token_count: Optional[int]
    token_budget: Optional[int]
    final_prompt: Optional[str]
    notes: Dict[str, Any]
    stages: List[StageRecord]
    candidates: List[CandidateRecord]
    rings: List[RingRecord]
    prompt_layers: List[PromptLayerRecord]
    keywords: List[Dict[str, str]]

    def to_dict(self) -> dict:
        """Serialize for API responses."""
        return {
            "id": self.id,
            "ts": self.ts,
            "query": self.query,
            "session_id": self.session_id,
            "turn_num": self.turn_num,
            "route": self.route,
            "door": self.door,
            "aperture": self.aperture,
            "aperture_deg": self.aperture_deg,
            "backend": self.backend,
            "prepare_only": self.prepare_only,
            "latency_ms": self.latency_ms,
            "token_count": self.token_count,
            "token_budget": self.token_budget,
            "final_prompt": self.final_prompt,
            "notes": self.notes,
            "stages": [asdict(s) for s in self.stages],
            "candidates": [asdict(c) for c in self.candidates],
            "rings": [asdict(r) for r in self.rings],
            "prompt_layers": [asdict(l) for l in self.prompt_layers],
            "keywords": self.keywords,
        }


class TraceBuilder:
    """Mutable collector for a single trace run."""

    def __init__(
        self,
        query: str,
        session_id: Optional[str] = None,
        turn_num: Optional[int] = None,
    ) -> None:
        self.id = f"trace_{uuid.uuid4().hex[:12]}"
        self._ts = time.time()
        self._t0 = time.perf_counter()

        self.query = query
        self.session_id = session_id
        self.turn_num = turn_num

        self.route: Optional[str] = None
        self.door: Optional[str] = None
        self.aperture: Optional[str] = None
        self.aperture_deg: Optional[int] = None

        self.backend: str = "skipped"
        self.prepare_only: bool = True
        self.token_count: Optional[int] = None
        self.token_budget: Optional[int] = None
        self.final_prompt: Optional[str] = None

        self.notes: Dict[str, Any] = {}

        self._stage_started: Dict[str, Dict[str, Any]] = {}
        self._stage_order: List[str] = []
        self._stages: Dict[str, StageRecord] = {}

        self._candidates: List[CandidateRecord] = []
        self._rings: Dict[str, RingRecord] = {}
        self._prompt_layers: List[PromptLayerRecord] = []
        self._keywords: List[Dict[str, str]] = []

        self._finalized = False
        self._final_record: Optional[TraceRecord] = None

    def _elapsed_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    def _safe_call(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as exc:
            self.warn(f"trace_record_error:{type(exc).__name__}")

    def record_route(
        self,
        route: str,
        door: Optional[str] = None,
        aperture: Optional[str] = None,
        aperture_deg: Optional[int] = None,
    ) -> None:
        self._safe_call(self._record_route, route, door, aperture, aperture_deg)

    def _record_route(
        self,
        route: str,
        door: Optional[str],
        aperture: Optional[str],
        aperture_deg: Optional[int],
    ) -> None:
        self.route = route
        self.door = door
        self.aperture = aperture
        self.aperture_deg = aperture_deg

    def stage_start(self, name: str, budget_ms: Optional[float] = None) -> None:
        self._safe_call(self._stage_start, name, budget_ms)

    def _stage_start(self, name: str, budget_ms: Optional[float]) -> None:
        if name in self._stage_started:
            return
        self._stage_started[name] = {
            "start_ms": self._elapsed_ms(),
            "budget_ms": budget_ms,
        }
        self._stage_order.append(name)

    def stage_end(
        self,
        name: str,
        candidates_in: Optional[int] = None,
        candidates_out: Optional[int] = None,
        status: str = "ok",
        error: Optional[str] = None,
    ) -> None:
        self._safe_call(
            self._stage_end,
            name,
            candidates_in,
            candidates_out,
            status,
            error,
        )

    def _stage_end(
        self,
        name: str,
        candidates_in: Optional[int],
        candidates_out: Optional[int],
        status: str,
        error: Optional[str],
    ) -> None:
        if name in self._stages:
            return
        started = self._stage_started.get(name)
        if started is None:
            started = {"start_ms": self._elapsed_ms(), "budget_ms": None}
            self._stage_order.append(name)

        self._stages[name] = StageRecord(
            stage_idx=max(0, self._stage_order.index(name)),
            stage_name=name,
            start_ms=float(started["start_ms"]),
            end_ms=float(self._elapsed_ms()),
            budget_ms=started.get("budget_ms"),
            candidates_in=candidates_in,
            candidates_out=candidates_out,
            status=status,
            error=error,
        )

    def record_candidate(self, stage: str, **kwargs) -> None:
        self._safe_call(self._record_candidate, stage, kwargs)

    def _record_candidate(self, stage: str, kwargs: Dict[str, Any]) -> None:
        self._candidates.append(CandidateRecord(stage_name=stage, **kwargs))

    def record_ring(
        self,
        ring: str,
        item_count: int,
        token_count: int,
        items: List[Dict[str, Any]],
    ) -> None:
        self._safe_call(self._record_ring, ring, item_count, token_count, items)

    def _record_ring(
        self,
        ring: str,
        item_count: int,
        token_count: int,
        items: List[Dict[str, Any]],
    ) -> None:
        self._rings[ring] = RingRecord(
            ring=ring,
            item_count=item_count,
            token_count=token_count,
            items=items,
        )

    def record_prompt_layer(
        self,
        layer_id: str,
        layer_name: str,
        content: Optional[str],
        token_count: Optional[int],
    ) -> None:
        self._safe_call(self._record_prompt_layer, layer_id, layer_name, content, token_count)

    def _record_prompt_layer(
        self,
        layer_id: str,
        layer_name: str,
        content: Optional[str],
        token_count: Optional[int],
    ) -> None:
        self._prompt_layers.append(
            PromptLayerRecord(
                layer_idx=len(self._prompt_layers),
                layer_id=layer_id,
                layer_name=layer_name,
                content=content,
                token_count=token_count,
            )
        )

    def record_keyword(self, keyword: str, source: str) -> None:
        self._safe_call(self._record_keyword, keyword, source)

    def _record_keyword(self, keyword: str, source: str) -> None:
        self._keywords.append({"keyword": keyword, "source": source})

    def set_prompt(
        self,
        prompt: str,
        *,
        token_count: Optional[int] = None,
        token_budget: Optional[int] = None,
    ) -> None:
        self._safe_call(self._set_prompt, prompt, token_count, token_budget)

    def _set_prompt(
        self,
        prompt: str,
        token_count: Optional[int],
        token_budget: Optional[int],
    ) -> None:
        self.final_prompt = prompt
        if token_count is not None:
            self.token_count = int(token_count)
        if token_budget is not None:
            self.token_budget = int(token_budget)

    def warn(self, msg: str) -> None:
        warnings = self.notes.setdefault("warnings", [])
        if msg not in warnings:
            warnings.append(msg)

    def finalize(
        self,
        *,
        latency_ms: Optional[int] = None,
        timeout_ms: int = 50,
    ) -> TraceRecord:
        """Freeze and return immutable trace record.

        If finalization takes longer than ``timeout_ms``, the record is marked
        as truncated in notes. Finalization never raises.
        """
        if self._finalized and self._final_record is not None:
            return self._final_record

        t0 = time.perf_counter()
        timed_out = False

        def _check_timeout() -> None:
            nonlocal timed_out
            if timeout_ms <= 0:
                return
            if (time.perf_counter() - t0) * 1000.0 > timeout_ms:
                timed_out = True

        stages: List[StageRecord] = []
        for name in self._stage_order:
            _check_timeout()
            if timed_out:
                break
            stage = self._stages.get(name)
            if stage is None:
                started = self._stage_started.get(name)
                if started is not None:
                    stage = StageRecord(
                        stage_idx=max(0, self._stage_order.index(name)),
                        stage_name=name,
                        start_ms=float(started["start_ms"]),
                        end_ms=float(self._elapsed_ms()),
                        budget_ms=started.get("budget_ms"),
                        status="incomplete",
                    )
            if stage is not None:
                stages.append(stage)

        if timed_out:
            self.warn("trace_truncated:finalize_timeout")

        latency = int(latency_ms if latency_ms is not None else self._elapsed_ms())

        self._final_record = TraceRecord(
            id=self.id,
            ts=self._ts,
            query=self.query,
            session_id=self.session_id,
            turn_num=self.turn_num,
            route=self.route,
            door=self.door,
            aperture=self.aperture,
            aperture_deg=self.aperture_deg,
            backend=self.backend,
            prepare_only=self.prepare_only,
            latency_ms=latency,
            token_count=self.token_count,
            token_budget=self.token_budget,
            final_prompt=self.final_prompt,
            notes=self.notes,
            stages=stages,
            candidates=list(self._candidates),
            rings=[self._rings[k] for k in sorted(self._rings.keys())],
            prompt_layers=list(self._prompt_layers),
            keywords=list(self._keywords),
        )
        self._finalized = True
        return self._final_record
