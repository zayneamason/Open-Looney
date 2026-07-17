"""Shared engine types used across core, policy, and actors.

Phase 0 introduces two enums that the policy layer (Rules 1–5) and the core
context need to share without forming an import cycle:

- ItemKind — what a memory IS (FACT, REFLECTION, OBSERVATION, ...). Drives
  Rule 1 admission table and Rule 2 decay constants.
- Door — how an item entered the system (USER_TURN, MEMORY_MATRIX, NEXUS).
  Drives Rule 1 admission. Phase 0 does not wire doors at runtime; the enum
  exists so Phase 1 can land without backfilling type definitions.

Reference: SPEC_Luna_Memory_Policy_v0.2.md Sections 4 and 6.
"""
from __future__ import annotations
from enum import Enum


class ItemKind(str, Enum):
    """Classification of a memory item by content type.

    Values match the YAML keys in ``rule_1_admission.MEMORY_MATRIX``.
    """
    IDENTITY = "IDENTITY"
    FACT = "FACT"
    CAUSAL = "CAUSAL"
    PATTERN = "PATTERN"
    REFLECTION = "REFLECTION"
    MEMORY = "MEMORY"
    CONVERSATION = "CONVERSATION"
    OBSERVATION = "OBSERVATION"


class Door(str, Enum):
    """Sanctioned inflow paths into the rings.

    IDENTITY items are *set*, not admitted — they do not pass through a Door.
    """
    USER_TURN = "USER_TURN"
    MEMORY_MATRIX = "MEMORY_MATRIX"
    NEXUS = "NEXUS"
