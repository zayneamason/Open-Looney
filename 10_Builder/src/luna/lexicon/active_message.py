"""Active /message StagePacket candidate builder — System Spec v1.2 Phase C.1.

Phase C.1: a conservative active-path StagePacket source that mirrors the
legacy route decision. The first valid active candidate proves the apply
path can produce a non-empty `READY` packet end-to-end without expanding
to real Lexicon roles, embeddings, or rerankers.

The builder is pure: no I/O, no Director/engine imports, never raises.
Empty `minted_prompt` and local-unavailable conditions are surfaced as
StagePackets that the existing `_validate_active_stage_packet` rejects
authoritatively — the validator stays the single source of truth for
gating.

Phase C.5 also exposes `build_active_agentic_error_payload` — a synthetic
director_pivot_active debug payload for the case where active mode was
requested but the engine's agentic processor raised before (or instead of)
the Director's pivot helper running. Without it, the response carries no
directorPivotActive field, making upstream failures invisible to soak
harnesses (see C.4 evidence report).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from luna.lexicon.shadow_loop import PlanSignal
from luna.lexicon.stager import Route, StagePacket, Trace


def build_active_message_stage_packet(
    *,
    query: str,
    minted_prompt: str,
    legacy_should_delegate: bool,
    local_available: bool,
) -> StagePacket:
    """Build the first active-path StagePacket candidate for `/message`.

    Mirrors the legacy route decision: delegated legacy → agentic/delegate,
    local legacy → direct/local. The minted prompt comes from the canonical
    PromptAssembler output passed in as `minted_prompt`. The validator
    rejects empty prompts and local-unavailable cases downstream.
    """
    # Reserved for future query-aware minting/refinement. Phase C.1 mirrors
    # legacy route only, but keeping the argument preserves the target API.
    _ = query

    if legacy_should_delegate:
        route_path = "agentic"
        route_backend = "delegate"
    else:
        route_path = "direct"
        route_backend = "local"

    signal = PlanSignal(
        status="READY",
        reason="active_candidate_mirrors_legacy",
        route=route_path,
        backend=route_backend,
        confidence=1.0,
        refinement={"query_delta": None, "retrieval_hint": None},
    )

    return StagePacket(
        minted_prompt=minted_prompt,
        route=Route(path=route_path, backend=route_backend),
        trace=Trace(
            pass_count=1,
            exit_reason="ready",
            plan_signals=[signal.to_dict()],
            lexicon_calls=0,
            total_latency_ms=0,
        ),
        warning=None,
    )


def build_active_agentic_error_payload(
    *,
    endpoint: str,
    exception: BaseException,
    legacy_should_delegate: Optional[bool] = None,
) -> Dict[str, Any]:
    """Build a synthetic director_pivot_active debug payload for an agentic error.

    Used by the engine's `_process_message_agentic` exception handler when
    active mode was requested for the endpoint but the agentic pipeline
    raised before the Director could apply a pivot (e.g. SQLite write-lock
    cascade in matrix get_context). Mirrors the fallback shape produced by
    `Director._maybe_run_director_active` so downstream consumers see one
    consistent contract regardless of which code path produced the
    fallback.

    `legacy_should_delegate` is optional — the engine error path does not
    always know what legacy would have decided (Director may not have run
    at all). When unknown, `legacy_route` is emitted as None to signal
    "decision never made" rather than fabricating a default.
    """
    return {
        "mode": "active",
        "endpoint": endpoint,
        "applied": False,
        "fallback_triggered": True,
        "fallback_reason": f"agentic_error:{type(exception).__name__}",
        "stage_packet": None,
        "legacy_route": (
            {"should_delegate": bool(legacy_should_delegate)}
            if legacy_should_delegate is not None
            else None
        ),
        "active_route": None,
        "mutated_runtime": False,
    }
