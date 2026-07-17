"""
Path B advisory adapter for packager-contract mapping.

This module is intentionally deterministic and side-effect free.
It maps live Luna context objects into packager-shaped contracts and produces
an advisory payload that is safe to consume as telemetry.

Important: advisory output never mutates runtime route or prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Literal, Sequence


RouteHint = Literal["LOCAL", "DELEGATE"]

_DELEGATE_HINT_KEYWORDS = {
    "compare",
    "analyze",
    "analysis",
    "design",
    "debug",
    "reason",
    "why",
}


@dataclass(frozen=True)
class PackagerRingItem:
    id: str
    kind: str
    content: str
    lock_in: float
    tokens: int
    last_seen_turn: int
    ring: str
    source: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
            "lock_in": self.lock_in,
            "tokens": self.tokens,
            "last_seen_turn": self.last_seen_turn,
            "ring": self.ring,
            "source": self.source,
        }


@dataclass(frozen=True)
class PackagerContractPreview:
    query: Dict[str, Any]
    rings: Dict[str, Any]
    candidates: list[Dict[str, Any]]
    history: list[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": dict(self.query),
            "rings": dict(self.rings),
            "candidates": [dict(c) for c in self.candidates],
            "history": [dict(h) for h in self.history],
        }


@dataclass(frozen=True)
class PackagerAdvisory:
    prompt_candidate: str
    route_hint: RouteHint
    ring_delta: Dict[str, Any]
    used_fallback: bool
    fallback_reason: str
    refinement_count: int
    invariant_violations: list[str] = field(default_factory=list)
    contract_preview: Dict[str, Any] = field(default_factory=dict)
    trace: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt_candidate": self.prompt_candidate,
            "route_hint": self.route_hint,
            "ring_delta": dict(self.ring_delta),
            "used_fallback": self.used_fallback,
            "fallback_reason": self.fallback_reason,
            "refinement_count": self.refinement_count,
            "invariant_violations": list(self.invariant_violations),
            "contract_preview": dict(self.contract_preview),
            "trace": dict(self.trace),
        }


def normalize_context_item(item: Any, *, default_turn: int = 0) -> PackagerRingItem:
    """Map live ContextItem-like objects to packager RingItem shape."""
    content = str(getattr(item, "content", "") or "")
    last_seen_turn = (
        getattr(item, "last_accessed_turn", None)
        or getattr(item, "created_at_turn", None)
        or default_turn
    )
    tokens = int(getattr(item, "tokens", 0) or max(1, len(content) // 4))
    lock_in = float(getattr(item, "lock_in", 0.0) or 0.0)
    ring = _enum_name(getattr(item, "ring", None), "OUTER")
    source = _enum_name(getattr(item, "source", None), "MEMORY")
    kind = _map_kind(item)

    return PackagerRingItem(
        id=str(getattr(item, "id", "") or ""),
        kind=kind,
        content=content,
        lock_in=lock_in,
        tokens=tokens,
        last_seen_turn=int(last_seen_turn),
        ring=ring,
        source=source,
    )


def build_contract_preview(
    *,
    query: str,
    turn_id: int,
    context_items: Sequence[Any] | None,
    history: Sequence[Dict[str, Any]] | None,
    memory_context: str = "",
    token_budget: int = 8000,
) -> PackagerContractPreview:
    """Build a packager-shaped preview from live inputs."""
    normalized = [normalize_context_item(i, default_turn=turn_id) for i in (context_items or [])]
    ring_state = _build_ring_state(normalized, token_budget=token_budget)
    candidates = _build_candidates(normalized, memory_context=memory_context)
    hist = [dict(h) for h in (history or [])]
    return PackagerContractPreview(
        query={"text": query, "turn_id": turn_id},
        rings=ring_state,
        candidates=candidates,
        history=hist,
    )


def validate_ring_delta(core_item_ids: Iterable[str], ring_delta: Dict[str, Any]) -> list[str]:
    """
    Validate proposal-only delta invariants.

    CORE is read-only: no demotes/evicts of CORE ids are allowed.
    """
    core_ids = {str(i) for i in core_item_ids}
    violations: list[str] = []
    if not core_ids:
        return violations

    evicts = ring_delta.get("evicts", []) or []
    demotes = ring_delta.get("demotes", []) or []

    for item_id in evicts:
        if str(item_id) in core_ids:
            violations.append(f"core_evict_blocked:{item_id}")

    for entry in demotes:
        if not isinstance(entry, (tuple, list)) or not entry:
            continue
        item_id = str(entry[0])
        if item_id in core_ids:
            violations.append(f"core_demote_blocked:{item_id}")

    return violations


def compute_route_hint(query: str) -> RouteHint:
    q = (query or "").lower()
    if len(q) > 200:
        return "DELEGATE"
    if any(k in q for k in _DELEGATE_HINT_KEYWORDS):
        return "DELEGATE"
    return "LOCAL"


def apply_route_advisory(runtime_route: str, advisory: Dict[str, Any] | None) -> str:
    """
    Advisory-only mode: runtime route always wins.

    This protects existing delegated/local behavior while Path B is telemetry-only.
    """
    _ = advisory
    return runtime_route


def apply_prompt_advisory(runtime_prompt: str, advisory: Dict[str, Any] | None) -> str:
    """
    Advisory-only mode: runtime prompt always wins.

    Path B first seam logs candidate prompts for comparison only.
    """
    _ = advisory
    return runtime_prompt


def build_packager_advisory(
    *,
    query: str,
    turn_id: int,
    runtime_route: str,
    runtime_prompt: str,
    context_items: Sequence[Any] | None,
    history: Sequence[Dict[str, Any]] | None,
    memory_context: str = "",
    token_budget: int = 8000,
) -> PackagerAdvisory:
    """
    Produce deterministic advisory output.

    This intentionally does not invoke Tools/packager_proto code yet.
    Path B consumes this as a contract bridge + telemetry scaffold.
    """
    preview = build_contract_preview(
        query=query,
        turn_id=turn_id,
        context_items=context_items,
        history=history,
        memory_context=memory_context,
        token_budget=token_budget,
    )
    core_ids = [i["id"] for i in preview.rings.get("core", [])]
    ring_delta = {
        "admits": [],
        "promotes": [],
        "demotes": [],
        "evicts": [],
    }
    violations = validate_ring_delta(core_ids, ring_delta)

    route_hint = compute_route_hint(query)
    advisory = PackagerAdvisory(
        prompt_candidate=runtime_prompt,
        route_hint=route_hint,
        ring_delta=ring_delta,
        used_fallback=True,
        fallback_reason="path_b_advisory_mode",
        refinement_count=0,
        invariant_violations=violations,
        contract_preview=preview.to_dict(),
        trace={
            "mode": "advisory_only",
            "runtime_route": runtime_route,
            "advisory_route_applied": False,
            "steps": [
                "inspect",
                "route_hint",
                "mint_fallback",
            ],
        },
    )
    return advisory


def _build_ring_state(items: Sequence[PackagerRingItem], *, token_budget: int) -> Dict[str, Any]:
    grouped: Dict[str, list[Dict[str, Any]]] = {
        "core": [],
        "inner": [],
        "middle": [],
        "outer": [],
    }
    for item in items:
        ring_key = item.ring.lower()
        if ring_key not in grouped:
            ring_key = "outer"
        grouped[ring_key].append(item.to_dict())

    budget_used = sum(int(i["tokens"]) for values in grouped.values() for i in values)
    return {
        "core": grouped["core"],
        "inner": grouped["inner"],
        "middle": grouped["middle"],
        "outer": grouped["outer"],
        "budget_total": int(token_budget),
        "budget_used": int(budget_used),
    }


def _build_candidates(items: Sequence[PackagerRingItem], *, memory_context: str) -> list[Dict[str, Any]]:
    candidates: list[Dict[str, Any]] = []
    for item in items:
        candidates.append(
            {
                "id": item.id,
                "kind": item.kind,
                "content": item.content,
                "score": item.lock_in,
                "source": item.source,
                "tokens": item.tokens,
            }
        )
    if memory_context.strip():
        candidates.append(
            {
                "id": "prefetched_memory_context",
                "kind": "FACT",
                "content": memory_context.strip(),
                "score": 0.3,
                "source": "Matrix",
                "tokens": max(1, len(memory_context.strip()) // 4),
            }
        )
    return candidates


def _enum_name(value: Any, default: str) -> str:
    if value is None:
        return default
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(value, str):
        return value
    return default


def _map_kind(item: Any) -> str:
    raw = _enum_name(getattr(item, "kind", None), "").upper()
    if raw == "IDENTITY":
        return "IDENTITY"
    if raw == "CONVERSATION":
        return "CONV"
    if raw == "REFLECTION":
        return "REFLECT"
    if raw == "OBSERVATION":
        return "OBS"
    if raw == "MEMORY":
        return "FACT"
    return "FACT"
