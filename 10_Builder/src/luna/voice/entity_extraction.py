"""
Shared entity-extraction helper used by Luna's voice interrupt classifier
(B3.3) and Scribe's CLARIFYING-confirmation heuristic (B6.6).

Co-locating the two consumers' signal extraction keeps their heuristics
from drifting apart.

Phase 1: word-boundary substring match against entity names present in a
provided `turn_context` dict. Not exhaustive; optimized to avoid false
positives (would mis-classify CLARIFYING as REDIRECTING in the classifier,
or mis-tag unconfirmed nodes in Scribe).

Phase 2+ horizon: replace with proper NER if heuristic precision is
insufficient on production traffic.
"""
from __future__ import annotations

from typing import Iterable


def extract_referenced_entities(
    utterance: str,
    turn_context: dict,
) -> list[str]:
    """Return entity names in `turn_context` that also appear in `utterance`.

    Used by Scribe's CLARIFYING-confirmation check (B6.6).
    """
    if not turn_context:
        return []
    known = _entity_names_from_context(turn_context)
    utterance_lower = utterance.lower()
    hits: list[str] = []
    for entity in known:
        if _word_boundary_contains(utterance_lower, entity.lower()):
            hits.append(entity)
    return hits


def has_new_entity(
    utterance: str,
    turn_context: dict,
) -> bool:
    """True if `utterance` contains a capitalized token not in turn_context.

    Used by the classifier (B3.3 rules 4 and 5). Conservative: looks only
    for capitalized tokens longer than 3 characters. Accepts false
    negatives in preference to false positives.
    """
    known = {e.lower() for e in _entity_names_from_context(turn_context)}
    for token in utterance.split():
        stripped = token.strip(".,!?;:\"'—–-()[]{}")
        if (
            len(stripped) > 3
            and stripped[0].isupper()
            and stripped.lower() not in known
        ):
            return True
    return False


def _entity_names_from_context(turn_context: dict) -> Iterable[str]:
    """Extract entity names from the turn_context dict.

    Tolerates the two shapes we see in practice: a list of strings or a
    list of dicts with `name`/`id` keys.
    """
    entities = turn_context.get("entities", [])
    names: list[str] = []
    for e in entities:
        if isinstance(e, dict):
            name = e.get("name") or e.get("id")
            if name:
                names.append(str(name))
        elif isinstance(e, str):
            names.append(e)
    return names


def _word_boundary_contains(haystack: str, needle: str) -> bool:
    """True if needle appears in haystack with word boundaries on both sides."""
    if not needle or needle not in haystack:
        return False
    idx = haystack.find(needle)
    before_ok = idx == 0 or not haystack[idx - 1].isalnum()
    after_idx = idx + len(needle)
    after_ok = after_idx >= len(haystack) or not haystack[after_idx].isalnum()
    return before_ok and after_ok
