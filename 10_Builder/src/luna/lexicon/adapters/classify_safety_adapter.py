"""Local heuristic safety classifier for the Lexicon ``classify_safety`` role.

Deterministic and dependency-free. Maps an input string to a structured
record:

```
{"label": "safe" | "review" | "block",
 "score": float in [0.0, 1.0],
 "reasons": list[str]}
```

Reasons come from a fixed taxonomy of bins (``harassment``, ``illegal``,
``self_harm``, ``sexual``, ``violence``). The score is a deterministic
function of bin breadth and hit count; thresholds map score to label.

Returns the dict on the happy path, ``None`` only on hard failure
(non-string input or internal exception). Empty/whitespace input
returns ``{"label":"safe","score":0.0,"reasons":[]}``.
"""

from __future__ import annotations

from typing import Optional

from .local_adapter import LocalAdapter

SAFETY_LABELS: tuple[str, ...] = ("safe", "review", "block")
SAFETY_BINS: tuple[str, ...] = (
    "harassment", "illegal", "self_harm", "sexual", "violence",
)

_REVIEW_THRESHOLD = 0.0
_BLOCK_THRESHOLD = 0.5
_BIN_WEIGHT = 0.25
_HIT_WEIGHT = 0.05

_BIN_TRIGGERS: dict[str, tuple[str, ...]] = {
    "harassment": (
        "you're an idiot", "you are an idiot",
        "you're stupid", "you are stupid",
        "you suck", "shut up",
        "you're worthless", "you are worthless",
        "you're pathetic", "you are pathetic",
        "go to hell",
    ),
    "self_harm": (
        "kill myself", "end my life", "want to die", "end it all",
        "self harm", "self-harm", "cut myself", "hurt myself",
        "suicide", "overdose on",
    ),
    "violence": (
        "kill you", "shoot you", "hurt you", "stab you",
        "beat you up", "attack you", "punch you",
        "murder you", "i'll murder",
    ),
    "sexual": (
        "porn", "pornography", "nudes", "nsfw",
        "explicit content", "sexting",
    ),
    "illegal": (
        "make a bomb", "build a bomb",
        "make meth", "synthesize meth",
        "make cocaine", "buy cocaine", "buy heroin", "buy meth",
        "pirate software", "crack the password",
        "how to hack", "ddos attack", "sql injection",
    ),
}


def _score_text(text: str) -> tuple[str, float, list[str]]:
    if text.strip() == "":
        return "safe", 0.0, []

    lowered = text.lower()
    hit_total = 0
    bins_hit: list[str] = []
    for bin_name in SAFETY_BINS:
        triggers = _BIN_TRIGGERS[bin_name]
        bin_hits = sum(1 for trigger in triggers if trigger in lowered)
        if bin_hits > 0:
            bins_hit.append(bin_name)
            hit_total += bin_hits

    if not bins_hit:
        return "safe", 0.0, []

    raw = _BIN_WEIGHT * len(bins_hit) + _HIT_WEIGHT * hit_total
    score = max(0.0, min(1.0, raw))

    if score >= _BLOCK_THRESHOLD:
        label = "block"
    elif score > _REVIEW_THRESHOLD:
        label = "review"
    else:  # pragma: no cover — guarded above by ``not bins_hit`` early return.
        label = "safe"

    reasons = sorted(bins_hit)
    return label, score, reasons


class ClassifySafetyAdapter(LocalAdapter):
    """Heuristic safety classifier — fixed bins + deterministic scoring."""

    adapter_name = "classify_safety"

    def infer(self, text: str) -> Optional[list[float]]:
        """Satisfy LocalAdapter ABC; returns the score as a 1-element vector."""
        result = self.classify_safety(text)
        if result is None:
            return None
        return [float(result["score"])]

    def classify_safety(self, text) -> Optional[dict]:
        if not isinstance(text, str):
            self._emit_failure(
                "classify_safety",
                "adapter_error",
                exception_type="TypeError",
            )
            return None
        try:
            label, score, reasons = _score_text(text)
            return {"label": label, "score": score, "reasons": reasons}
        except Exception as exc:
            self._emit_failure(
                "classify_safety",
                "adapter_error",
                exception_type=type(exc).__name__,
                text_len=len(text),
            )
            return None
