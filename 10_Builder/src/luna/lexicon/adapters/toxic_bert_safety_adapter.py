"""Real-model safety classifier for the Lexicon ``classify_safety`` role.

Selected at api boundary when the registry declares
``classify_safety.implementation: model``. Co-exists with the heuristic
:class:`ClassifySafetyAdapter`; the selector in
:func:`luna.lexicon.api._get_classify_safety_adapter` binds one or the other.

Failure discipline (mirrors Phase E.9 lock):
- Non-string input → ``adapter_error`` with ``TypeError``; ``None`` returned.
- Lazy-load failure (transformers import or model download) RAISES.
  The api-layer try/except catches and emits ``adapter_internal_error``.
  There is **no silent fallback to heuristic**.
- Pipeline invocation exception → ``adapter_error``; ``None`` returned.

Output shape is identical to :class:`ClassifySafetyAdapter` so downstream
consumers see one schema regardless of which adapter is bound:
``{"label": "safe"|"review"|"block", "score": float, "reasons": list[str]}``

Label-mapping table (Phase F.3 lock):
  unitary/toxic-bert label → Lexicon reason bin
  -----------------------------------------------
  toxic           → harassment
  severe_toxic    → harassment
  insult          → harassment
  identity_hate   → harassment
  threat          → violence
  obscene         → sexual

``self_harm`` and ``illegal`` have no toxic-bert equivalent and are omitted
from ``reasons`` — the shape contract only requires ``list[str]``.

Score derivation: ``max(score for each triggered label)``.
Threshold mapping (calibrated against unitary/toxic-bert output range):
  score >= 0.5   → block
  score >= 0.05  → review   (noise floor; benign text scores ~0.001–0.005)
  else           → safe
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .classify_safety_adapter import SAFETY_LABELS
from .local_adapter import LocalAdapter

# Phase F.3 lock — bert label → Lexicon reason bin.
_BERT_TO_REASON: dict[str, str] = {
    "toxic": "harassment",
    "severe_toxic": "harassment",
    "insult": "harassment",
    "identity_hate": "harassment",
    "threat": "violence",
    "obscene": "sexual",
}

_BLOCK_THRESHOLD = 0.5
_REVIEW_THRESHOLD = 0.05  # noise floor for unitary/toxic-bert; benign text ~0.001–0.005


def _map_pipeline_output(pipeline_result: list[dict]) -> tuple[str, float, list[str]]:
    """Convert toxic-bert pipeline output to the locked Lexicon shape.

    ``pipeline_result`` is a list of ``{"label": str, "score": float}`` dicts,
    one per bert label. Triggered labels (score > 0) are mapped to reason bins;
    the aggregate score is the max triggered score.
    """
    triggered: dict[str, float] = {}
    for item in pipeline_result:
        raw_label = (item.get("label") or "").lower().replace("-", "_")
        score = float(item.get("score") or 0.0)
        if score > _REVIEW_THRESHOLD and raw_label in _BERT_TO_REASON:
            reason_bin = _BERT_TO_REASON[raw_label]
            triggered[reason_bin] = max(triggered.get(reason_bin, 0.0), score)

    if not triggered:
        return "safe", 0.0, []

    aggregate_score = max(triggered.values())
    reasons = sorted(triggered.keys())

    if aggregate_score >= _BLOCK_THRESHOLD:
        label = "block"
    else:
        label = "review"

    return label, aggregate_score, reasons


class ToxicBertSafetyAdapter(LocalAdapter):
    """unitary/toxic-bert-backed safety classifier."""

    adapter_name = "classify_safety"

    def __init__(
        self,
        model_name: str = "unitary/toxic-bert",
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

        # Lazy-load is allowed to raise — api boundary normalises to
        # adapter_internal_error. No silent fallback.
        if self._pipeline is None:
            self._pipeline = self._build_pipeline()

        if text.strip() == "":
            return {"label": "safe", "score": 0.0, "reasons": []}

        try:
            raw = self._pipeline(text)
            # HuggingFace text-classification pipeline returns list[list[dict]]
            # for multi-label or list[dict] for single-label. Normalise.
            if raw and isinstance(raw[0], list):
                items = raw[0]
            else:
                items = raw
            label, score, reasons = _map_pipeline_output(items)
            return {"label": label, "score": score, "reasons": reasons}
        except Exception as exc:
            self._emit_failure(
                "classify_safety",
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

        return pipeline(
            "text-classification",
            model=self._model_name,
            top_k=None,  # return scores for all labels
        )
