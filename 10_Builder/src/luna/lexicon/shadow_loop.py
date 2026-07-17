"""Director shadow pass loop — System Spec v1.2 Section 9.

Phase B: deterministic shadow loop that exercises the verb cycle as
observable telemetry only. The loop now treats complete local-role output
as READY and derives a deterministic route/backend decision from role
results, while remaining non-authoritative for runtime generation.

Its job in Phase B is to:

- prove the gating + plumbing paths are correct,
- emit PlanSignal + StagePacket telemetry,
- never raise into the runtime caller,
- never influence runtime route/prompt/backend.

The pure helper run_shadow_pass_loop() is callable from any context
(tests, Director). It does not import Director or engine state.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Dict, List, Literal, Optional

from luna.lexicon import api as _default_lexicon
from luna.lexicon.registry import get_registry
from luna.lexicon.stager import Route, StagePacket, Trace

logger = logging.getLogger("luna.lexicon.shadow")

PlanStatus = Literal["READY", "INSUFFICIENT", "UNCERTAIN"]
VALID_STATUSES = ("READY", "INSUFFICIENT", "UNCERTAIN")

# Ring buffer of recent shadow-pass traces. Exposed via get_recent_traces()
# for the Director Console real-time trace view.
_TRACE_BUFFER: deque = deque(maxlen=50)


@dataclass
class _RoleStep:
    role: str
    latency_ms: int
    ok: bool
    result_summary: str


@dataclass
class _LexiconTrace:
    ts: float
    query_preview: str
    pass_count: int
    exit_reason: str
    total_latency_ms: int
    status: str
    steps: list  # list[dict] — first-pass role steps


def _result_summary(role: str, result: Any) -> str:
    if result is None:
        return "None"
    if role == "classify_intent":
        return str(result)
    if role == "classify_safety":
        if isinstance(result, dict):
            return f"{result.get('label','?')}→{result.get('score', 0):.2f}"
        return str(result)
    if role == "ner":
        if isinstance(result, list):
            return f"{len(result)} {'entity' if len(result) == 1 else 'entities'}"
        return str(result)
    if role == "embed":
        if isinstance(result, list):
            return f"vector[{len(result)}]"
        return str(result)
    return str(result)[:40]


def _estimate_tokens(value: Any) -> int:
    """Cheap display-only token estimate for non-LLM Lexicon roles."""
    if value is None:
        return 0
    text = value if isinstance(value, str) else str(value)
    return max(1, (len(text) + 3) // 4)


def _role_meta(role: str, lexicon: ModuleType) -> dict:
    registry = get_registry()
    entry = registry.get_role(role)
    metadata = dict(entry.metadata) if entry else {}
    model = (
        metadata.get("model")
        or metadata.get("target_model")
        or metadata.get("primary")
        or metadata.get("fallback")
        or "none"
    )
    getter_name = {
        "classify_intent": "_get_classify_intent_adapter",
        "classify_safety": "_get_classify_safety_adapter",
        "ner": "_get_ner_adapter",
        "embed": "_get_embed_adapter",
        "rerank": "_get_rerank_adapter",
        "detect_language": "_get_detect_language_adapter",
    }.get(role)
    adapter = entry.adapter if entry else "unknown"
    if getter_name:
        getter = getattr(lexicon, getter_name, None)
        if callable(getter):
            try:
                adapter = type(getter()).__name__
            except Exception:
                adapter = "unavailable"
    implementation = entry.implementation if entry else "unknown"
    return {
        "adapter": adapter,
        "implementation": implementation,
        "model": model,
        "model_fired": implementation == "model",
        "token_accounting": "estimated:chars/4",
    }


def _trace_step(
    sequence: int,
    role: str,
    latency_ms: int,
    ok: bool,
    result: Any,
    query: str,
    lexicon: ModuleType,
) -> dict:
    step = {
        "sequence": sequence,
        "role": role,
        "latency_ms": latency_ms,
        "ok": ok,
        "result_summary": _result_summary(role, result),
        "input_tokens_est": _estimate_tokens(query),
        "output_tokens_est": _estimate_tokens(result),
    }
    step.update(_role_meta(role, lexicon))
    if role == "embed" and isinstance(result, list):
        step["vector_dim"] = len(result)
        step["output_tokens_est"] = 0
    return step


def _ready_route_from_roles(intent: Any, safety: Any) -> tuple[str, str, str, float]:
    """Derive a deterministic READY route/backend from lexicon role outputs."""
    intent_label = str(intent).strip().lower() if isinstance(intent, str) else ""
    safety_label = str((safety or {}).get("label", "")).strip().lower() if isinstance(safety, dict) else ""

    if safety_label in ("review", "block"):
        return "agentic", "delegate", f"safety_{safety_label}", 0.90

    if intent_label in {"research", "memory_query", "dataroom", "task", "creative"}:
        return "agentic", "delegate", f"intent_{intent_label}", 0.80

    return "direct", "local", (f"intent_{intent_label}" if intent_label else "intent_default"), 0.75


def get_recent_traces(n: int = 20) -> list:
    """Return up to *n* most-recent shadow-pass traces, newest first."""
    buf = list(_TRACE_BUFFER)
    buf.reverse()
    out = []
    for t in buf[:n]:
        out.append({
            "ts": t.ts,
            "query_preview": t.query_preview,
            "pass_count": t.pass_count,
            "exit_reason": t.exit_reason,
            "total_latency_ms": t.total_latency_ms,
            "status": t.status,
            "steps": t.steps,
        })
    return out

MAX_PASSES = 3
DEGRADED_DEFAULT_ROUTE = "direct"
DEGRADED_DEFAULT_BACKEND = "local"


@dataclass(frozen=True)
class PlanSignal:
    status: PlanStatus
    reason: str
    route: Literal["direct", "agentic"]
    backend: Literal["local", "delegate"]
    confidence: float
    refinement: Dict[str, Any] = field(
        default_factory=lambda: {"query_delta": None, "retrieval_hint": None}
    )

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"PlanSignal.status must be one of {VALID_STATUSES}; got {self.status!r}"
            )
        if self.route not in ("direct", "agentic"):
            raise ValueError(f"PlanSignal.route invalid: {self.route!r}")
        if self.backend not in ("local", "delegate"):
            raise ValueError(f"PlanSignal.backend invalid: {self.backend!r}")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(
                f"PlanSignal.confidence must be in [0,1]; got {self.confidence!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "route": self.route,
            "backend": self.backend,
            "confidence": float(self.confidence),
            "refinement": dict(self.refinement),
        }


def _degraded_signal(reason: str) -> PlanSignal:
    return PlanSignal(
        status="INSUFFICIENT",
        reason=reason,
        route=DEGRADED_DEFAULT_ROUTE,
        backend=DEGRADED_DEFAULT_BACKEND,
        confidence=0.0,
        refinement={"query_delta": None, "retrieval_hint": None},
    )


def _safe_call(fn, *args, **kwargs) -> tuple[Any, Optional[str]]:
    """Invoke a Lexicon role; return (result, error_reason). Never raise."""
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:
        return None, f"role_exception:{type(exc).__name__}"


def _timed_safe_call(fn, *args, **kwargs) -> tuple[Any, Optional[str], int]:
    """Like _safe_call but also returns wall-clock latency in milliseconds."""
    t0 = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        return result, None, int((time.perf_counter() - t0) * 1000)
    except Exception as exc:
        return None, f"role_exception:{type(exc).__name__}", int((time.perf_counter() - t0) * 1000)


def _run_one_pass(
    query: str, lexicon: ModuleType
) -> tuple[PlanSignal, int, list]:
    """Run one verb-cycle pass.

    Returns (signal, lexicon_call_count, role_steps) where role_steps is a
    list of _RoleStep dicts for the Director Console real-time trace view.
    """
    calls = 0
    error_reasons: List[str] = []
    steps: list = []

    intent, err, lat = _timed_safe_call(lexicon.classify_intent, query)
    calls += 1
    if err:
        error_reasons.append(err)
    steps.append(_trace_step(1, "classify_intent", lat, err is None, intent, query, lexicon))

    safety, err, lat = _timed_safe_call(lexicon.classify_safety, query)
    calls += 1
    if err:
        error_reasons.append(err)
    steps.append(_trace_step(2, "classify_safety", lat, err is None, safety, query, lexicon))

    entities, err, lat = _timed_safe_call(lexicon.ner, query)
    calls += 1
    if err:
        error_reasons.append(err)
    steps.append(_trace_step(3, "ner", lat, err is None, entities, query, lexicon))

    embedding, err, lat = _timed_safe_call(lexicon.embed, query)
    calls += 1
    if err:
        error_reasons.append(err)
    steps.append(_trace_step(4, "embed", lat, err is None, embedding, query, lexicon))

    missing = [
        role
        for role, value in (
            ("classify_intent", intent),
            ("classify_safety", safety),
            ("ner", entities),
            ("embed", embedding),
        )
        if value is None
    ]

    if not missing:
        route, backend, reason, confidence = _ready_route_from_roles(intent, safety)
        signal = PlanSignal(
            status="READY",
            reason=reason,
            route=route,
            backend=backend,
            confidence=confidence,
            refinement={"query_delta": None, "retrieval_hint": None},
        )
        return signal, calls, steps

    if error_reasons:
        missing_reason = f"missing:{','.join(missing)}" if missing else ""
        reason = "|".join(r for r in [*error_reasons, missing_reason] if r)
        return _degraded_signal(reason), calls, steps

    if len(missing) < 4:
        return (
            PlanSignal(
                status="INSUFFICIENT",
                reason=f"partial_output_missing:{','.join(missing)}",
                route=DEGRADED_DEFAULT_ROUTE,
                backend=DEGRADED_DEFAULT_BACKEND,
                confidence=0.10,
                refinement={"query_delta": None, "retrieval_hint": None},
            ),
            calls,
            steps,
        )

    return _degraded_signal("lexicon_roles_returned_none"), calls, steps


def run_shadow_pass_loop(
    query: str,
    *,
    lexicon: Optional[ModuleType] = None,
) -> StagePacket:
    """Run the Director shadow pass loop.

    Returns a StagePacket suitable for telemetry. Never raises — any
    internal exception is converted to a degraded signal and the loop
    continues. Caller must treat the result as observation only.
    """
    lex = lexicon or _default_lexicon
    started = time.perf_counter()

    signals: List[PlanSignal] = []
    total_calls = 0
    pass_count = 0
    exit_reason = "pass_ceiling"
    warning: Optional[str] = None
    first_pass_steps: list = []

    for i in range(MAX_PASSES):
        pass_count = i + 1
        try:
            signal, calls, steps = _run_one_pass(query, lex)
        except Exception as exc:
            logger.debug("[SHADOW] pass %d hit unexpected exception: %s", pass_count, exc)
            signal = _degraded_signal(f"loop_exception:{type(exc).__name__}")
            calls = 0
            steps = []

        if i == 0:
            first_pass_steps = steps

        signals.append(signal)
        total_calls += calls

        if signal.status == "READY":
            exit_reason = "ready"
            break
        # INSUFFICIENT or UNCERTAIN — loop until budget exhausted

    if pass_count >= MAX_PASSES and (not signals or signals[-1].status != "READY"):
        warning = "pass_ceiling"
        exit_reason = "pass_ceiling"

    final = signals[-1] if signals else _degraded_signal("no_signal_emitted")
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    # Synthesize a "generate" step from the final PlanSignal so the trace
    # covers the full classify_intent → … → generate pipeline.
    generate_step = {
        "sequence": len(first_pass_steps) + 1,
        "role": "generate",
        "latency_ms": 0,
        "ok": final.status == "READY",
        "result_summary": f"route={final.route} backend={final.backend} [{final.status}]",
        "adapter": "shadow_route_planner",
        "implementation": "synthetic",
        "model": "none",
        "model_fired": False,
        "input_tokens_est": _estimate_tokens(query),
        "output_tokens_est": 0,
        "token_accounting": "estimated:chars/4",
    }
    trace_steps = first_pass_steps + [generate_step]

    _TRACE_BUFFER.append(_LexiconTrace(
        ts=time.time(),
        query_preview=(query[:80] + "…") if len(query) > 80 else query,
        pass_count=pass_count,
        exit_reason=exit_reason,
        total_latency_ms=elapsed_ms,
        status=final.status,
        steps=trace_steps,
    ))

    return StagePacket(
        minted_prompt="",  # telemetry-only; never used for generation
        route=Route(path=final.route, backend=final.backend),
        trace=Trace(
            pass_count=pass_count,
            exit_reason=exit_reason,
            plan_signals=[s.to_dict() for s in signals],
            lexicon_calls=total_calls,
            total_latency_ms=elapsed_ms,
        ),
        warning=warning,
    )
