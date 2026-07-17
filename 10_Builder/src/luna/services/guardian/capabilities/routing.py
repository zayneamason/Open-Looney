"""
Keyword/regex routing detector for Guardian capabilities.

Pure function, deterministic, no LLM. Adding new capabilities means adding
a pattern set here. Returns the capability name to invoke, or None.
"""

from __future__ import annotations

import re

_QA_TRIAGE_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"fail(ed)?\s+qa",
        r"what\s+(broke|failed)",
        r"last\s+inference",
        r"diagnose",
        r"qa\s+(summary|report|status|health)",
        r"what'?s\s+wrong.*inference",
        r"why.*(response|answer).*(fail|wrong|broke)",
        r"(show|inspect)\s+(diagnostic|qa)\s+state",
    )
)


def detect_capability_intent(message: str) -> str | None:
    """Return the capability name to invoke for this message, or None."""
    if not message:
        return None

    for pat in _QA_TRIAGE_PATTERNS:
        if pat.search(message):
            return "qa_triage"

    return None
