"""Real-model language-detection adapter for the Lexicon ``detect_language`` role.

Selected at api boundary when the registry declares
``detect_language.implementation: model``. Co-exists with the heuristic
:class:`DetectLanguageAdapter`; the selector in
:func:`luna.lexicon.api._get_detect_language_adapter` binds one or the other.

Failure discipline (mirrors Phase E.9 lock):
- Non-string input → ``adapter_error`` with ``TypeError``; ``None`` returned.
- Lazy-load failure (fasttext import or model-file download) RAISES.
  The api-layer try/except catches and emits ``adapter_internal_error``.
  There is **no silent fallback to heuristic**.
- ``model.predict(text)`` exception → ``adapter_error``; ``None`` returned.

Output contract matches the heuristic baseline exactly: one of the strings in
``luna.lexicon.adapters.detect_language_adapter.LANG_CODES`` or ``"und"`` for
undetermined. Downstream consumers see one output schema regardless of which
adapter is bound.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .detect_language_adapter import LANG_CODES
from .local_adapter import LocalAdapter

# fasttext language codes that map directly to Lexicon LANG_CODES.
# fasttext prefixes predictions with "__label__"; we strip that.
_FASTTEXT_TO_LEXICON: dict[str, str] = {code: code for code in LANG_CODES}

_UNDETERMINED = "und"


def _parse_prediction(raw_label: str) -> str:
    """Strip fasttext ``__label__`` prefix and normalise to a Lexicon code."""
    label = raw_label.replace("__label__", "").strip().lower()
    return _FASTTEXT_TO_LEXICON.get(label, _UNDETERMINED)


class FasttextDetectLanguageAdapter(LocalAdapter):
    """fasttext lid.176-backed language detector."""

    adapter_name = "detect_language"

    def __init__(
        self,
        model_name: str = "fasttext/lid.176",
        *,
        _lid_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        super().__init__()
        self._model_name = model_name
        self._model: Optional[Any] = None
        # Test seam: inject a fake model factory to avoid live fasttext install.
        self._lid_factory = _lid_factory

    def load(self) -> None:
        super().load()
        if self._model is None:
            self._model = self._build_model()

    def unload(self) -> None:
        super().unload()
        if self._refcount == 0:
            self._model = None

    def infer(self, text: str) -> Optional[list[float]]:
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

        # Lazy-load is allowed to raise — api boundary normalises to
        # adapter_internal_error. Loud failure is the lock (no silent fallback).
        if self._model is None:
            self._model = self._build_model()

        try:
            labels, _scores = self._model.predict(text.replace("\n", " "), k=1)
            if not labels:
                return _UNDETERMINED
            return _parse_prediction(labels[0])
        except Exception as exc:
            self._emit_failure(
                "detect_language",
                "adapter_error",
                exception_type=type(exc).__name__,
                text_len=len(text),
            )
            return None

    def _build_model(self) -> Any:
        """Acquire the fasttext model — raises on import or load failure."""
        if self._lid_factory is not None:
            return self._lid_factory()
        import fasttext  # imported lazily so the dependency stays optional
        import fasttext.util

        fasttext.util.download_model("lid.176", if_exists="ignore")
        return fasttext.load_model("lid.176.bin")
