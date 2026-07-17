"""Local heuristic language detector for the Lexicon ``detect_language`` role.

Deterministic and dependency-free: script-range checks for non-Latin
families plus stopword scoring for the six Latin-script languages.
Returns one of ``LANG_CODES`` on a confident match, ``"und"`` for
ambiguous-but-valid text, and ``None`` only on hard failure (bad input
type or internal exception).
"""

from __future__ import annotations

import string
from typing import Optional

from .local_adapter import LocalAdapter

LANG_CODES: tuple[str, ...] = (
    "en", "es", "fr", "de", "pt", "it",
    "ja", "ko", "zh", "ru", "ar", "hi",
)

_LATIN_TIEBREAK: tuple[str, ...] = ("en", "es", "fr", "pt", "it", "de")
_DOMINANT_SCRIPT_THRESHOLD = 0.5
_MIN_LATIN_LEN = 2

_HIRAGANA = (0x3040, 0x309F)
_KATAKANA = (0x30A0, 0x30FF)
_HANGUL_SYL = (0xAC00, 0xD7AF)
_HANGUL_JAMO = (0x1100, 0x11FF)
_CJK = (0x4E00, 0x9FFF)
_CYRILLIC = (0x0400, 0x04FF)
_ARABIC = (0x0600, 0x06FF)
_DEVANAGARI = (0x0900, 0x097F)

_PUNCT_STRIP = string.punctuation + "¡¿«»“”‘’—…"

_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset({
        "the", "is", "are", "of", "to", "in", "and", "you", "that", "it",
        "was", "for", "with", "on", "as",
    }),
    "es": frozenset({
        "el", "la", "de", "que", "y", "en", "los", "una", "para", "con",
        "es", "no", "su", "por", "un",
    }),
    "fr": frozenset({
        "le", "la", "de", "et", "à", "les", "un", "des", "est", "pour",
        "dans", "que", "qui", "sur", "avec",
    }),
    "de": frozenset({
        "der", "die", "das", "und", "ist", "ein", "mit", "von", "den", "zu",
        "im", "sich", "auf", "nicht", "ich",
    }),
    "pt": frozenset({
        "o", "a", "de", "que", "e", "para", "com", "não", "em", "do",
        "da", "os", "as", "um", "uma",
    }),
    "it": frozenset({
        "il", "la", "di", "e", "è", "che", "un", "in", "per", "con",
        "non", "una", "sono", "ma", "ho",
    }),
}


def _in_range(codepoint: int, low_high: tuple[int, int]) -> bool:
    return low_high[0] <= codepoint <= low_high[1]


def _classify_char(ch: str) -> Optional[str]:
    """Return a script tag for a non-Latin alphabetic char, else None.

    Returns one of: ``hiragana``, ``katakana``, ``hangul``, ``cjk``,
    ``cyrillic``, ``arabic``, ``devanagari``. Latin / digit / whitespace
    / punctuation chars return ``None`` so they don't weight any script.
    """
    cp = ord(ch)
    if _in_range(cp, _HIRAGANA):
        return "hiragana"
    if _in_range(cp, _KATAKANA):
        return "katakana"
    if _in_range(cp, _HANGUL_SYL) or _in_range(cp, _HANGUL_JAMO):
        return "hangul"
    if _in_range(cp, _CJK):
        return "cjk"
    if _in_range(cp, _CYRILLIC):
        return "cyrillic"
    if _in_range(cp, _ARABIC):
        return "arabic"
    if _in_range(cp, _DEVANAGARI):
        return "devanagari"
    return None


def _is_alphabetic(ch: str) -> bool:
    """A char counts as alphabetic for script-density purposes if it's a
    letter (``isalpha`` covers Latin + every non-Latin script we care
    about). Digits, whitespace, and punctuation are excluded so they
    don't dilute the dominant-script ratio."""
    return ch.isalpha()


def _score(text: str) -> str:
    if text.strip() == "":
        return "und"

    script_counts: dict[str, int] = {}
    alphabetic_total = 0
    for ch in text:
        if not _is_alphabetic(ch):
            continue
        alphabetic_total += 1
        tag = _classify_char(ch)
        if tag is not None:
            script_counts[tag] = script_counts.get(tag, 0) + 1

    if alphabetic_total == 0:
        return "und"

    has_hiragana = script_counts.get("hiragana", 0) > 0
    has_katakana = script_counts.get("katakana", 0) > 0
    has_hangul = script_counts.get("hangul", 0) > 0

    if has_hiragana or has_katakana:
        return "ja"
    if has_hangul:
        return "ko"

    def _dominant(tag: str) -> bool:
        return script_counts.get(tag, 0) / alphabetic_total >= _DOMINANT_SCRIPT_THRESHOLD

    if _dominant("cjk"):
        return "zh"
    if _dominant("cyrillic"):
        return "ru"
    if _dominant("arabic"):
        return "ar"
    if _dominant("devanagari"):
        return "hi"

    if any(script_counts.get(tag, 0) > 0
           for tag in ("cjk", "cyrillic", "arabic", "devanagari")):
        return "und"

    if len(text.strip()) < _MIN_LATIN_LEN:
        return "und"

    tokens = [tok.strip(_PUNCT_STRIP).lower() for tok in text.split()]
    tokens = [tok for tok in tokens if tok]

    scores: dict[str, int] = {lang: 0 for lang in _STOPWORDS}
    for token in tokens:
        for lang, words in _STOPWORDS.items():
            if token in words:
                scores[lang] += 1

    max_score = max(scores.values())
    if max_score == 0:
        return "und"

    for lang in _LATIN_TIEBREAK:
        if scores[lang] == max_score:
            return lang

    return "und"


class DetectLanguageAdapter(LocalAdapter):
    """Heuristic language identifier — script ranges + Latin stopwords."""

    adapter_name = "detect_language"

    def infer(self, text: str) -> Optional[list[float]]:
        """Satisfy LocalAdapter ABC; returns a one-element flag vector."""
        result = self.detect_language(text)
        return [1.0] if isinstance(result, str) else None

    def detect_language(self, text) -> Optional[str]:
        if not isinstance(text, str):
            self._emit_failure(
                "detect_language",
                "adapter_error",
                exception_type="TypeError",
            )
            return None
        try:
            return _score(text)
        except Exception as exc:
            self._emit_failure(
                "detect_language",
                "adapter_error",
                exception_type=type(exc).__name__,
                text_len=len(text),
            )
            return None
