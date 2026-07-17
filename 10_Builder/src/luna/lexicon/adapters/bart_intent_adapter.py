"""Real-model intent classifier for the Lexicon ``classify_intent`` role.

Selected at api boundary when the registry declares
``classify_intent.implementation: model``. Co-exists with the heuristic
:class:`ClassifyIntentAdapter`; the selector in
:func:`luna.lexicon.api._get_classify_intent_adapter` binds one or the other.

Uses ``MoritzLaurer/deberta-v3-base-zeroshot-v2.0`` via HuggingFace zero-shot
classification. DeBERTa-v3-base-zeroshot achieves 0.619 macro-F1 across 28
datasets vs 0.497 for bart-large-mnli (Laurer 2023, arxiv:2312.17543).
Descriptive hypothesis strings are used as candidate labels (not the short
INTENT_LABELS keys) because NLI entailment scores are sensitive to label
wording. The top-scoring description is mapped back to its INTENT_LABELS key.

Named follow-up: rename class/file from Bart* → ZeroShotIntent* to reflect the
model-agnostic adapter pattern (same API works for any zero-shot NLI model).

Failure discipline (mirrors Phase E.9 / F.2 / F.3 lock):
- Non-string input → ``adapter_error`` with ``TypeError``; ``None`` returned.
- Lazy-load failure (transformers import or model download) RAISES.
  The api-layer try/except catches and emits ``adapter_internal_error``.
  There is **no silent fallback to heuristic**.
- Pipeline invocation exception → ``adapter_error``; ``None`` returned.

Output contract matches the heuristic baseline exactly: one of the strings in
``INTENT_LABELS``. Downstream consumers see one output schema regardless of
which adapter is bound.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .classify_intent_adapter import INTENT_LABELS
from .local_adapter import LocalAdapter

# Descriptive hypothesis strings improve BART zero-shot accuracy over bare
# single-word labels. Ordered to match INTENT_LABELS for index alignment.
_LABEL_DESCRIPTIONS: dict[str, str] = {
    "greeting":        "a greeting, casual hello, or friendly introduction",
    "simple_question": "a factual question, how-to question, or request for information",
    "memory_query":    "asking what you remember or recall from previous conversations",
    "research":        "a request to research, investigate, or analyse a topic in depth",
    "creative":        "a request to write or compose original text such as updates, stories, or drafts",
    "dataroom":        "a request to access, search, or retrieve documents or files",
    "task":            "a directive to execute a specific technical action such as run, configure, deploy, or install",
    "emotional":       "sharing personal feelings or seeking emotional understanding and support",
    "meta":            "asking what capabilities this AI assistant has and what it can do for the user",
}

# Explicit hypothesis template improves NLI entailment scoring for intent
# classification over the BART default "This example is {}."
_HYPOTHESIS_TEMPLATE = "The main intent of this message is {}."
_DESCRIPTION_TO_LABEL: dict[str, str] = {v: k for k, v in _LABEL_DESCRIPTIONS.items()}
_CANDIDATE_LABELS: list[str] = list(_LABEL_DESCRIPTIONS.values())


class BartIntentAdapter(LocalAdapter):
    """facebook/bart-large-mnli zero-shot intent classifier."""

    adapter_name = "classify_intent"

    def __init__(
        self,
        model_name: str = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0",
        *,
        _pipeline_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        super().__init__()
        self._model_name = model_name
        self._pipeline: Optional[Any] = None
        # Test seam: inject a fake pipeline factory to avoid live model load.
        self._pipeline_factory = _pipeline_factory

    def load(self) -> None:
        super().load()
        if self._pipeline is None:
            self._pipeline = self._build_pipeline()

    def unload(self) -> None:
        super().unload()
        if self._refcount == 0:
            self._pipeline = None

    def infer(self, text: str) -> Optional[list[float]]:
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

        # Lazy-load is allowed to raise — api boundary normalises to
        # adapter_internal_error. No silent fallback.
        if self._pipeline is None:
            self._pipeline = self._build_pipeline()

        try:
            result = self._pipeline(
                text,
                candidate_labels=_CANDIDATE_LABELS,
                hypothesis_template=_HYPOTHESIS_TEMPLATE,
                multi_label=False,
            )
            descriptions = result.get("labels") if isinstance(result, dict) else None
            if not descriptions:
                return INTENT_LABELS[1]  # safe fallback: simple_question
            top_description = descriptions[0]
            return _DESCRIPTION_TO_LABEL.get(top_description, INTENT_LABELS[1])
        except Exception as exc:
            self._emit_failure(
                "classify_intent",
                "adapter_error",
                exception_type=type(exc).__name__,
                text_len=len(text),
            )
            return None

    def _build_pipeline(self) -> Any:
        """Acquire the HuggingFace pipeline — raises on import or load failure."""
        if self._pipeline_factory is not None:
            return self._pipeline_factory()
        from transformers import pipeline  # lazy — optional dep

        return pipeline("zero-shot-classification", model=self._model_name)
