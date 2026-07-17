"""Lexicon facade — the cheap-models registry for the Director loop.

Phase E.1 wires remote adapters for `generate` + `curate`.
Phase E.2/E.3 wire local adapters for `embed` + `rerank`.
The remaining local roles stay stubbed and return None plus structured telemetry.
"""

from .api import (
    classify_intent,
    classify_safety,
    curate,
    detect_language,
    embed,
    generate,
    ner,
    rerank,
)

__all__ = [
    "embed",
    "rerank",
    "ner",
    "classify_safety",
    "classify_intent",
    "detect_language",
    "curate",
    "generate",
]
