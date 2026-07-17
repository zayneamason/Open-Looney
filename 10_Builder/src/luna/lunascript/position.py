"""LunaScript conversation position detector — heuristic, zero LLM calls.

Detects where in the conversation arc we are and returns response geometry.
"""

import re
from typing import Optional


POSITIONS = ["OPENING", "EXPLORING", "BUILDING", "DEEPENING", "PIVOTING", "CLOSING"]

RESPONSE_GEOMETRY = {
    "OPENING": {
        "max_sent": 3,
        "question_req": True,
        "tangent": False,
        "pattern": "acknowledge -> question",
    },
    "EXPLORING": {
        "max_sent": 8,
        "question_req": True,
        "tangent": True,
        "pattern": "react -> think -> tangent? -> question",
    },
    "BUILDING": {
        "max_sent": 15,
        "question_req": False,
        "tangent": False,
        "pattern": "build_on -> add_layer -> validate?",
    },
    "DEEPENING": {
        "max_sent": 20,
        "question_req": False,
        "tangent": True,
        "pattern": "connect -> go_deeper -> surface_insight",
    },
    "PIVOTING": {
        "max_sent": 8,
        "question_req": True,
        "tangent": False,
        "pattern": "acknowledge_shift -> bridge -> new_question",
    },
    "CLOSING": {
        "max_sent": 4,
        "question_req": False,
        "tangent": False,
        "pattern": "summarize -> next_step? -> warm_close",
    },
}

_CLOSING_RE = re.compile(
    r"\b(?:thanks?|bye|goodbye|see you|later|night|gotta go|talk (?:soon|later)|take care)\b",
    re.IGNORECASE,
)
_PIVOT_RE = re.compile(
    r"\b(?:what about|actually|wait|different|switch|change|instead|never ?mind|hold on)\b",
    re.IGNORECASE,
)
_BUILDING_RE = re.compile(
    r"\b(?:let's|going with|decided|okay let|we should|do it|build|implement|go ahead)\b",
    re.IGNORECASE,
)
_DEEPENING_RE = re.compile(
    r"\b(?:how does|explain|specifically|elaborate|tell me more|go deeper|why does|in detail)\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"\?")


def detect_position(
    message: str,
    history: list[str],
    prev_position: Optional[str] = None,
) -> tuple[str, float]:
    """Detect conversation position from message + history. Returns (position, confidence)."""
    turn_count = len(history)
    msg_lower = message.lower().strip()
    msg_len = len(message)

    # Turn 0-1: OPENING
    if turn_count <= 1:
        return ("OPENING", 0.95)

    # Short message + closing language -> CLOSING
    if msg_len < 80 and _CLOSING_RE.search(message):
        return ("CLOSING", 0.85)

    # Pivot language
    if _PIVOT_RE.search(message):
        return ("PIVOTING", 0.75)

    # Building/decision language
    if _BUILDING_RE.search(message):
        return ("BUILDING", 0.70)

    # Deep language + longer messages
    if _DEEPENING_RE.search(message) and msg_len > 60:
        return ("DEEPENING", 0.70)

    # Long messages with history depth suggest deepening
    if turn_count > 6 and msg_len > 120:
        return ("DEEPENING", 0.55)

    # High question density -> EXPLORING
    q_count = len(_QUESTION_RE.findall(message))
    if q_count >= 2:
        return ("EXPLORING", 0.70)

    # Default: EXPLORING
    return ("EXPLORING", 0.50)


def get_geometry(position: str) -> dict:
    """Get response geometry for a position."""
    return RESPONSE_GEOMETRY.get(position, RESPONSE_GEOMETRY["EXPLORING"]).copy()


def merge_geometry_with_mode(geometry: dict, response_mode) -> dict:
    """Cross position geometry with ResponseMode constraints."""
    merged = geometry.copy()

    mode_name = response_mode.value if hasattr(response_mode, "value") else str(response_mode)

    if mode_name == "CHAT":
        merged["max_sent"] = min(merged["max_sent"], 8)
    elif mode_name == "RECALL":
        merged["max_sent"] = min(merged["max_sent"], 10)
        merged["tangent"] = False
    elif mode_name == "ASSIST":
        merged["question_req"] = False
        merged["tangent"] = False
    elif mode_name == "REFLECT":
        merged["max_sent"] = max(merged["max_sent"], 10)
        merged["tangent"] = True

    return merged
