"""Local heuristic NER for the Lexicon ``ner`` role.

Deterministic and dependency-free. Extracts entities by precedence:
``date`` > ``place`` > ``project`` > ``person`` > ``concept``. Returns a
``list[dict]`` of ``{"name", "type"}`` entries in first-appearance order,
deduplicated by ``(name.lower(), type)``.

Returns ``None`` only on hard failure (non-string input or internal
exception). Empty / whitespace-only input returns ``[]``.
"""

from __future__ import annotations

import re
from typing import Optional

from .local_adapter import LocalAdapter

ENTITY_TYPES: tuple[str, ...] = ("person", "project", "place", "concept", "date")

_PLACE_ALLOWLIST: frozenset[str] = frozenset({
    "Paris", "London", "Tokyo", "Berlin", "Madrid", "Rome", "Moscow",
    "Beijing", "Seoul", "Mumbai", "Delhi", "Cairo", "Sydney", "Toronto",
    "Vancouver", "Chicago", "Boston", "Washington", "New York",
    "Los Angeles", "San Francisco",
    "USA", "UK", "France", "Germany", "Japan", "China", "Korea",
    "India", "Russia", "Italy", "Spain", "Portugal", "Brazil",
    "Mexico", "Canada", "Australia", "Africa", "Europe", "Asia",
})

_PERSON_VERBS: tuple[str, ...] = (
    "said", "met", "asked", "told", "replied", "wrote", "called",
    "emailed", "saw", "greeted", "thanked",
)
_SALUTATIONS_WITH_DOT: tuple[str, ...] = (
    "Mr.", "Mrs.", "Ms.", "Dr.", "Prof.",
)
_SALUTATIONS_NO_DOT: tuple[str, ...] = ("Professor",)
_PLACE_PREPOSITIONS: tuple[str, ...] = (
    "in", "at", "from", "to", "visiting", "near", "into",
    "toward", "towards",
)

_PUNCT_TRIM = ".,;:!?\"'()[]{}"

_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
    re.compile(r"\b(?:today|tomorrow|yesterday)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:next|last)\s+(?:week|month|year|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
        r"\s+\d{1,2}(?:,?\s+\d{4})?\b"
    ),
)

_TITLE_CASE_RUN = re.compile(r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\b")
_PROJECT_PHRASE = re.compile(
    r"\b(?:[Pp]roject|[Cc]odename|[Ii]nitiative)"
    r"\s+([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)*)\b"
)
_PERSON_SALUTATION_PHRASE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Professor)\.?\s+"
    r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\b"
)

_RANK_DATE = 1
_RANK_PLACE = 2
_RANK_PROJECT = 3
_RANK_PERSON = 4
_RANK_CONCEPT = 5


def _normalize_span(span: str) -> str:
    return span.strip().strip(_PUNCT_TRIM).strip()


def _left_token(text: str, position: int) -> Optional[str]:
    left = text[:position].rstrip()
    if not left:
        return None
    tokens = left.split()
    return tokens[-1] if tokens else None


def _right_token(text: str, position: int) -> Optional[str]:
    right = text[position:].lstrip()
    if not right:
        return None
    tokens = right.split()
    if not tokens:
        return None
    return tokens[0].rstrip(_PUNCT_TRIM)


def _is_salutation(token: str) -> bool:
    return token in _SALUTATIONS_WITH_DOT or token in _SALUTATIONS_NO_DOT


def _classify_title_run(text: str, start: int, end: int) -> tuple[str, int]:
    run = text[start:end]
    normalized = _normalize_span(run)
    left = _left_token(text, start)

    if left and _is_salutation(left):
        return "person", _RANK_PERSON
    if normalized in _PLACE_ALLOWLIST:
        return "place", _RANK_PLACE
    if left and left.lower() in _PLACE_PREPOSITIONS:
        return "place", _RANK_PLACE
    if left and left.lower() in _PERSON_VERBS:
        return "person", _RANK_PERSON
    right = _right_token(text, end)
    if right and right.lower() in _PERSON_VERBS:
        return "person", _RANK_PERSON
    return "concept", _RANK_CONCEPT


def _extract(text: str) -> list[dict]:
    if not text or text.strip() == "":
        return []

    candidates: list[tuple[int, int, str, str, int]] = []

    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            name = _normalize_span(match.group(0))
            if name:
                candidates.append(
                    (match.start(), match.end(), name, "date", _RANK_DATE)
                )

    for match in _PERSON_SALUTATION_PHRASE.finditer(text):
        name = _normalize_span(match.group(1))
        if name:
            candidates.append(
                (match.start(), match.end(), name, "person", _RANK_PERSON)
            )

    for match in _PROJECT_PHRASE.finditer(text):
        name = _normalize_span(match.group(1))
        if name:
            candidates.append(
                (match.start(), match.end(), name, "project", _RANK_PROJECT)
            )

    for match in _TITLE_CASE_RUN.finditer(text):
        etype, rank = _classify_title_run(text, match.start(), match.end())
        name = _normalize_span(match.group(0))
        if name:
            candidates.append(
                (match.start(), match.end(), name, etype, rank)
            )

    candidates.sort(key=lambda c: (c[0], c[4]))

    accepted: list[tuple[int, int, str, str]] = []
    for start, end, name, etype, _rank in candidates:
        overlaps = any(
            not (end <= a_start or start >= a_end)
            for (a_start, a_end, _n, _t) in accepted
        )
        if overlaps:
            continue
        accepted.append((start, end, name, etype))

    seen: set[tuple[str, str]] = set()
    result: list[dict] = []
    for _start, _end, name, etype in accepted:
        key = (name.lower(), etype)
        if key in seen:
            continue
        seen.add(key)
        result.append({"name": name, "type": etype})
    return result


class NERAdapter(LocalAdapter):
    """Heuristic NER — title-case + date regex + context-based typing."""

    adapter_name = "ner"

    def infer(self, text: str) -> Optional[list[float]]:
        """Satisfy LocalAdapter ABC; returns entity count as a 1-element vector."""
        result = self.ner(text)
        return [float(len(result))] if isinstance(result, list) else None

    def ner(self, text) -> Optional[list[dict]]:
        if not isinstance(text, str):
            self._emit_failure(
                "ner",
                "adapter_error",
                exception_type="TypeError",
            )
            return None
        try:
            return _extract(text)
        except Exception as exc:
            self._emit_failure(
                "ner",
                "adapter_error",
                exception_type=type(exc).__name__,
                text_len=len(text),
            )
            return None
