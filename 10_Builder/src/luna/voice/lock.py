"""
Voice query classification heuristics.

Exposes classify_query_type(query: str) -> str, consumed by
Director._detect_context_type() to feed context_type into ConfidenceSignals
(see VOICE_SYSTEM_ARCHITECTURE.md A3.6, A3.9, B7).

The former VoiceLock class is removed per B7 role resolution (v2.0.0).
The classification heuristics now live exclusively in classify_query_type();
the standalone prompt-injection path is deprecated.
"""

import logging
import re

logger = logging.getLogger(__name__)


def classify_query_type(query: str) -> str:
    """
    Simple query type classification for logging/debugging.

    Returns: greeting | technical | emotional | creative | task | question | general
    """
    query_lower = query.lower().strip()

    # === GREETING DETECTION ===
    greeting_markers = ["hey", "hi ", "hello", "yo ", "sup", "what's up", "howdy"]
    if any(query_lower.startswith(g) or query_lower == g.strip() for g in greeting_markers):
        return "greeting"

    # === TECHNICAL/EXPLANATION DETECTION ===
    technical_markers = [
        "explain", "how does", "how do", "what is", "what's the difference",
        "why does", "can you describe", "walk me through",
        "code", "function", "error", "bug", "debug", "implement",
        "api", "database", "async", "await", "class", "method"
    ]
    if any(t in query_lower for t in technical_markers):
        return "technical"

    # === EMOTIONAL SUPPORT DETECTION ===
    emotional_markers = [
        "feel", "feeling", "stressed", "sad", "anxious", "worried",
        "overwhelmed", "frustrated", "happy", "excited", "scared",
        "lonely", "tired", "exhausted", "burned out", "burnout"
    ]
    if any(e in query_lower for e in emotional_markers):
        return "emotional"

    # === CREATIVE REQUEST DETECTION ===
    creative_markers = [
        "write me", "write a", "imagine", "story", "poem", "haiku",
        "brainstorm", "ideas for", "come up with", "make up"
    ]
    has_create = re.search(r'\bcreate\b', query_lower) is not None
    if has_create or any(c in query_lower for c in creative_markers):
        return "creative"

    # === TASK/COMMAND DETECTION ===
    task_markers = [
        "list", "show me", "find", "search", "get", "fetch",
        "run", "execute", "do", "make", "set", "update", "delete"
    ]
    if any(t in query_lower for t in task_markers):
        return "task"

    # === QUESTION DETECTION (general) ===
    question_starters = (
        "what", "who", "where", "when", "why", "how",
        "is ", "are ", "can ", "do ", "does "
    )
    if query_lower.endswith("?") or query_lower.startswith(question_starters):
        return "question"

    return "general"
