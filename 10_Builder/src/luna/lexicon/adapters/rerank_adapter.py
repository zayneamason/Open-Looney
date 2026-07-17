"""Local MiniLM-backed reranker for the Lexicon ``rerank`` role."""

from __future__ import annotations

from collections import OrderedDict
from math import sqrt
from typing import Any, Optional, Sequence

from luna.substrate.local_embeddings import LocalEmbeddings, get_embeddings

from .local_adapter import LocalAdapter

_CACHE_MAX = 128
_EMPTY_CANDIDATE_SCORE = -1.0


class RerankAdapter(LocalAdapter):
    """Adapter that reranks candidates via MiniLM cosine similarity."""

    adapter_name = "rerank"

    def __init__(self) -> None:
        super().__init__()
        self._embedder: Optional[LocalEmbeddings] = None
        self._cache: "OrderedDict[str, list[float]]" = OrderedDict()
        self._cache_max: int = _CACHE_MAX

    def load(self) -> None:
        super().load()
        if self._embedder is None:
            self._embedder = get_embeddings()

    def unload(self) -> None:
        super().unload()
        if self._refcount == 0:
            self._cache.clear()
            self._embedder = None

    def infer(self, text: str) -> Optional[list[float]]:
        """Expose raw vector inference to satisfy LocalAdapter contract."""
        return self._embed_text(text)

    def rerank(self, query: str, candidates: Sequence[Any]) -> Optional[list[Any]]:
        if not isinstance(query, str):
            self._emit_failure(
                "rerank",
                "adapter_error",
                exception_type="TypeError",
            )
            return None

        if not hasattr(candidates, "__len__"):
            self._emit_failure(
                "rerank",
                "adapter_error",
                exception_type="TypeError",
            )
            return None

        query_vec = self._embed_text(query)
        if query_vec is None:
            return None

        ranked: list[tuple[float, int, Any]] = []
        for idx, original in enumerate(candidates):
            candidate_text = self._candidate_text(original)
            if candidate_text.strip() == "":
                score = _EMPTY_CANDIDATE_SCORE
            else:
                candidate_vec = self._embed_text(candidate_text)
                if candidate_vec is None:
                    return None
                score = self._cosine(query_vec, candidate_vec)

            ranked.append((score, idx, self._render_candidate(original, score)))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ranked]

    def _embed_text(self, text: str) -> Optional[list[float]]:
        if not isinstance(text, str):
            self._emit_failure(
                "rerank",
                "adapter_error",
                exception_type="TypeError",
            )
            return None

        key = text.strip()
        if key != "":
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached

        if self._embedder is None:
            try:
                self._embedder = get_embeddings()
            except Exception as exc:
                self._emit_failure(
                    "rerank",
                    "adapter_error",
                    exception_type=type(exc).__name__,
                    text_len=len(text),
                )
                return None

        try:
            vector = self._embedder.encode(text)
        except Exception as exc:
            self._emit_failure(
                "rerank",
                "adapter_error",
                exception_type=type(exc).__name__,
                text_len=len(text),
            )
            return None

        if key != "":
            self._cache[key] = vector
            if len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)

        return vector

    @staticmethod
    def _candidate_text(candidate: Any) -> str:
        if isinstance(candidate, str):
            return candidate
        if isinstance(candidate, dict):
            for key in ("content", "text", "value"):
                value = candidate.get(key)
                if value is not None:
                    return str(value)
            return ""
        return str(candidate)

    @staticmethod
    def _render_candidate(candidate: Any, score: float) -> Any:
        if isinstance(candidate, dict):
            out = dict(candidate)
            out["score"] = float(score)
            return out
        return {"value": candidate, "score": float(score)}

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = sqrt(sum(x * x for x in a))
        mag_b = sqrt(sum(y * y for y in b))
        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return dot / (mag_a * mag_b)
