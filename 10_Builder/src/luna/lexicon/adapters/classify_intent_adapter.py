"""Local heuristic intent classifier for the Lexicon ``classify_intent`` role.

Deterministic and dependency-free. Maps an input string to one of the nine
labels in ``INTENT_LABELS`` via a fixed-precedence keyword/pattern ladder.
Vocabulary is locked to ``luna.inference.subtasks.VALID_INTENTS`` so the
Director-side consumers see one consistent intent surface.

Returns the label on a confident or fallback match, ``None`` only on hard
failure (non-string input or internal exception). Empty/whitespace input
falls through the ladder and returns the deterministic fallback
``"simple_question"``.
"""

from __future__ import annotations

import re
import string
from typing import Optional

from .local_adapter import LocalAdapter

INTENT_LABELS: tuple[str, ...] = (
    "greeting",
    "simple_question",
    "memory_query",
    "research",
    "creative",
    "dataroom",
    "task",
    "emotional",
    "meta",
)

_FALLBACK_LABEL = "simple_question"

_GREETING_OPENERS: tuple[str, ...] = (
    "hi", "hello", "hey", "howdy", "greetings",
    "yo", "hiya", "sup",
)

_GREETING_PHRASE_OPENERS: tuple[str, ...] = (
    "good morning", "good afternoon", "good evening", "good night",
)

_META_PHRASES: tuple[str, ...] = (
    "who are you", "what are you", "what can you do",
    "what do you do", "what is luna", "what's luna",
    "tell me about yourself", "your name", "your purpose",
    "your capabilities", "your abilities", "what can you help",
    "introduce yourself", "about yourself",
)

_DATAROOM_TERMS: tuple[str, ...] = (
    "dataroom", "data room", "document", "documents", "file", "files",
    "paper", "papers", "pdf", "archive", "archives", "folder", "folders",
    "report", "reports", "spreadsheet", "transcript", "transcripts",
)

_MEMORY_PHRASES: tuple[str, ...] = (
    "remember", "recall", "do you know", "did i tell you", "you said",
    "we discussed", "we talked about", "earlier you", "last time",
    "what did i say", "what i told you", "i mentioned",
)

_RESEARCH_TERMS: tuple[str, ...] = (
    "research", "find out", "look up", "look into", "investigate",
    "compare", "analyze", "analyse", "survey", "explore",
    "deep dive", "summarize the", "summarise the",
)

_CREATIVE_TERMS: tuple[str, ...] = (
    "write", "draft", "compose", "imagine", "story", "poem", "poetry",
    "essay", "brainstorm", "lyrics", "screenplay", "song",
    "make up a", "invent a",
)

_TASK_PREFIXES: tuple[str, ...] = (
    "please ", "can you ", "could you ", "would you ", "will you ",
)
_TASK_VERBS: tuple[str, ...] = (
    "build", "make", "create", "set up", "setup", "configure",
    "run", "execute", "deploy", "install", "fix", "update", "delete",
    "remove", "rename", "schedule", "send", "open", "close",
    "start", "stop", "restart", "generate",
)

_EMOTIONAL_PHRASES: tuple[str, ...] = (
    "i feel", "i'm feeling", "im feeling", "i am feeling",
    "i'm sad", "im sad", "i am sad",
    "i'm happy", "im happy", "i am happy",
    "i'm angry", "im angry", "i am angry",
    "i'm frustrated", "i'm anxious", "i'm worried", "i'm scared",
    "i'm lonely", "i'm excited", "i'm tired", "i'm exhausted",
    "i miss", "i love", "i hate",
)

_QUESTION_OPENERS: tuple[str, ...] = (
    "what", "why", "how", "when", "where", "who", "which",
    "is", "are", "do", "does", "did", "can", "could", "would", "should",
)

_PUNCT_STRIP = string.punctuation

_TOKEN_RE = re.compile(r"[A-Za-z']+(?:[-'][A-Za-z']+)*")


def _normalize(text: str) -> str:
    return text.strip().lower()


def _has_phrase(haystack: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in haystack for phrase in phrases)


def _starts_with_greeting(haystack: str) -> bool:
    if not haystack:
        return False
    for phrase in _GREETING_PHRASE_OPENERS:
        if haystack.startswith(phrase):
            return True
    first_token_match = _TOKEN_RE.match(haystack)
    if first_token_match is None:
        return False
    first_token = first_token_match.group(0)
    return first_token in _GREETING_OPENERS


def _has_word(haystack: str, words: tuple[str, ...]) -> bool:
    """Whole-word presence test using token boundaries."""
    tokens = [tok.strip(_PUNCT_STRIP).lower() for tok in haystack.split()]
    token_set = {tok for tok in tokens if tok}
    for word in words:
        if " " in word:
            if word in haystack:
                return True
        elif word in token_set:
            return True
    return False


def _is_task_form(haystack: str) -> bool:
    if not any(haystack.startswith(prefix) for prefix in _TASK_PREFIXES):
        # Bare imperative: starts with a task verb.
        first_token_match = _TOKEN_RE.match(haystack)
        if first_token_match is None:
            return False
        return first_token_match.group(0) in _TASK_VERBS
    # Has a task prefix; require an action verb somewhere after it.
    return any(verb in haystack for verb in _TASK_VERBS)


def _is_question_form(haystack: str) -> bool:
    if "?" in haystack:
        return True
    first_token_match = _TOKEN_RE.match(haystack)
    if first_token_match is None:
        return False
    return first_token_match.group(0) in _QUESTION_OPENERS


def _classify(text: str) -> str:
    norm = _normalize(text)

    if _starts_with_greeting(norm):
        return "greeting"
    if _has_phrase(norm, _META_PHRASES):
        return "meta"
    if _has_word(norm, _DATAROOM_TERMS):
        return "dataroom"
    if _has_phrase(norm, _MEMORY_PHRASES) or _has_word(norm, ("remember", "recall")):
        return "memory_query"
    if _has_phrase(norm, _RESEARCH_TERMS) or _has_word(norm, ("research", "investigate", "compare", "analyze", "analyse", "explore")):
        return "research"
    if _has_phrase(norm, _CREATIVE_TERMS) or _has_word(norm, ("write", "draft", "compose", "imagine", "story", "poem", "essay")):
        return "creative"
    if _is_task_form(norm):
        return "task"
    if _has_phrase(norm, _EMOTIONAL_PHRASES):
        return "emotional"
    if _is_question_form(norm):
        return "simple_question"
    return _FALLBACK_LABEL


class ClassifyIntentAdapter(LocalAdapter):
    """Heuristic intent classifier — fixed-precedence keyword ladder."""

    adapter_name = "classify_intent"

    def infer(self, text: str) -> Optional[list[float]]:
        """Satisfy LocalAdapter ABC; returns a one-element flag vector."""
        result = self.classify_intent(text)
        return [1.0] if isinstance(result, str) else None

    def classify_intent(self, text) -> Optional[str]:
        if not isinstance(text, str):
            self._emit_failure(
                "classify_intent",
                "adapter_error",
                exception_type="TypeError",
            )
            return None
        try:
            return _classify(text)
        except Exception as exc:
            self._emit_failure(
                "classify_intent",
                "adapter_error",
                exception_type=type(exc).__name__,
                text_len=len(text),
            )
            return None
