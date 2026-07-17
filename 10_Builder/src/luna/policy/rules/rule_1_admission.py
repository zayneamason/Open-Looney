"""Rule 1 admission contract.

Phase 1 cutover: every intake item is routed through this module using an
explicit Door. The rule returns an admission decision and mutates only
item-local initialization fields (lock_in/permanent) based on policy tables.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from luna.core.types import Door, ItemKind
from luna.policy.policy import Policy

_VALID_RING_NAMES = {"CORE", "INNER", "MIDDLE", "OUTER"}


@dataclass(frozen=True)
class AdmitDecision:
    """Result of Rule 1 intake routing."""

    admit: bool
    ring: str | None
    reason: str


class Rule_1_Admission:
    """Implements Spec v0.2 Rule 1 using policy.yaml tables."""

    @staticmethod
    def admit(item: Any, door: Door, policy: Policy) -> AdmitDecision:
        rule_1 = policy.rule_1_admission
        if not isinstance(rule_1, Mapping):
            return AdmitDecision(admit=False, ring=None, reason="rule_1_missing")

        doors = rule_1.get("doors") or {}
        if not isinstance(doors, Mapping):
            return AdmitDecision(admit=False, ring=None, reason="rule_1_doors_missing")

        door_cfg = doors.get(door.value)
        if not isinstance(door_cfg, Mapping):
            return AdmitDecision(admit=False, ring=None, reason=f"door_not_configured:{door.value}")

        kind = getattr(item, "kind", ItemKind.MEMORY)
        kind_key = kind.value if isinstance(kind, ItemKind) else str(kind)

        # Rule 1.4: NEXUS relevance gate.
        gate = door_cfg.get("relevance_gate")
        if gate is not None and float(getattr(item, "relevance", 0.0)) < float(gate):
            return AdmitDecision(admit=False, ring=None, reason="nexus_relevance_gate")

        # Rule 1.2: initialize lock_in by kind when configured for this door.
        lock_in_by_kind = door_cfg.get("lock_in_by_kind") or {}
        if isinstance(lock_in_by_kind, Mapping) and kind_key in lock_in_by_kind:
            item.lock_in = float(lock_in_by_kind[kind_key])

        # Identity items are permanently protected when present.
        if kind_key == ItemKind.IDENTITY.value:
            item.permanent = True

        # Ring selection: per-kind override, then door default.
        ring_name: str | None = None
        default_ring_by_kind = door_cfg.get("default_ring_by_kind") or {}
        if isinstance(default_ring_by_kind, Mapping):
            ring_name = default_ring_by_kind.get(kind_key)
        if not ring_name:
            ring_name = door_cfg.get("default_ring")

        if ring_name not in _VALID_RING_NAMES:
            return AdmitDecision(
                admit=False,
                ring=None,
                reason=f"invalid_target_ring:{ring_name!r}",
            )

        # CORE is reserved for identity.
        if ring_name == "CORE" and kind_key != ItemKind.IDENTITY.value:
            return AdmitDecision(admit=True, ring="INNER", reason="core_reserved_for_identity")

        return AdmitDecision(admit=True, ring=ring_name, reason=f"door:{door.value}")
