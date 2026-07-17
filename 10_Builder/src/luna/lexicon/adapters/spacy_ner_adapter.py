"""Real-model NER adapter for the Lexicon ``ner`` role — spaCy backend.

Selected at api boundary when the registry declares
``ner.implementation: model``. Co-exists with the heuristic
:class:`NERAdapter`; the selector in :func:`luna.lexicon.api._get_ner_adapter`
binds one or the other.

Failure discipline (Phase E.9 lock):
- Non-string input → ``adapter_error`` with ``TypeError``; ``None`` returned.
- Lazy-load failure (spaCy import or ``spacy.load(model_name)``) RAISES.
  The api-layer try/except in :func:`luna.lexicon.api.ner` catches and emits
  ``adapter_internal_error``. There is **no silent fallback to heuristic**.
- ``nlp(text)`` invocation exception → ``adapter_error`` with the actual
  exception type; ``None`` returned.

Output schema matches the heuristic baseline exactly so downstream
consumers see one schema regardless of which adapter is bound:
``list[dict]`` of ``{"name": str, "type": str}`` deduped by
``(name.lower(), type)`` in first-appearance order.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .local_adapter import LocalAdapter

# spaCy entity label → Lexicon entity type (locked Phase E.9 Section 5.2).
_LABEL_MAP: dict[str, str] = {
    "PERSON": "person",
    "ORG": "project",
    "PRODUCT": "project",
    "WORK_OF_ART": "project",
    "GPE": "place",
    "LOC": "place",
    "FAC": "place",
    "DATE": "date",
    "TIME": "date",
    "EVENT": "concept",
    "LAW": "concept",
    "LANGUAGE": "concept",
    "NORP": "concept",
}
_FALLBACK_TYPE = "concept"


def _map_label(label: str) -> str:
    """Map a spaCy entity label to the Lexicon ``type`` lock; fall back to concept."""
    if not label:
        return _FALLBACK_TYPE
    return _LABEL_MAP.get(label, _FALLBACK_TYPE)


class SpacyNERAdapter(LocalAdapter):
    """spaCy-backed NER adapter — real-model alternative to :class:`NERAdapter`."""

    adapter_name = "ner"

    def __init__(
        self,
        model_name: str = "en_core_web_sm",
        *,
        _nlp_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        super().__init__()
        self._model_name = model_name
        self._nlp: Optional[Any] = None
        # Test seam: inject a fake nlp factory to avoid live spaCy install.
        self._nlp_factory = _nlp_factory

    def load(self) -> None:
        super().load()
        if self._nlp is None:
            self._nlp = self._build_nlp()

    def unload(self) -> None:
        super().unload()
        if self._refcount == 0:
            self._nlp = None

    def infer(self, text: str) -> Optional[list[dict]]:
        return self.ner(text)

    def ner(self, text) -> Optional[list[dict]]:
        if not isinstance(text, str):
            self._emit_failure(
                "ner",
                "adapter_error",
                exception_type="TypeError",
            )
            return None

        # Lazy-load is allowed to raise — the api boundary normalizes to
        # adapter_internal_error. Loud failure is the lock (no silent fallback).
        if self._nlp is None:
            self._nlp = self._build_nlp()

        try:
            doc = self._nlp(text)
        except Exception as exc:
            self._emit_failure(
                "ner",
                "adapter_error",
                exception_type=type(exc).__name__,
                text_len=len(text),
            )
            return None

        seen: set[tuple[str, str]] = set()
        result: list[dict] = []
        for ent in getattr(doc, "ents", ()):
            name = (getattr(ent, "text", "") or "").strip()
            if not name:
                continue
            etype = _map_label(getattr(ent, "label_", "") or "")
            key = (name.lower(), etype)
            if key in seen:
                continue
            seen.add(key)
            result.append({"name": name, "type": etype})
        return result

    def _build_nlp(self) -> Any:
        """Acquire the spaCy nlp pipeline — raises on import or model-load failure."""
        if self._nlp_factory is not None:
            return self._nlp_factory()
        import spacy  # imported lazily so the dependency stays optional

        return spacy.load(self._model_name)
