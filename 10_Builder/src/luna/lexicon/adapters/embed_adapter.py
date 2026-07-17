"""Local MiniLM-backed embedding adapter for the Lexicon ``embed`` role."""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional

from luna.substrate.local_embeddings import LocalEmbeddings, get_embeddings

from .local_adapter import LocalAdapter

_CACHE_MAX = 128


class EmbedAdapter(LocalAdapter):
    """Adapter wrapping the substrate :class:`LocalEmbeddings` singleton.

    Lifecycle is logical only: the underlying singleton in
    :mod:`luna.substrate.local_embeddings` does not support physical model
    teardown, so :meth:`unload` simply releases adapter-local state when the
    refcount hits zero. The handle returned by :func:`get_embeddings` is
    process-wide and remains alive after teardown here.
    """

    adapter_name = "embed"

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
        if not isinstance(text, str):
            self._emit_failure(
                "embed",
                "adapter_error",
                exception_type="TypeError",
            )
            return None

        key = text.strip()

        if self._embedder is None:
            try:
                self._embedder = get_embeddings()
            except Exception as exc:
                self._emit_failure(
                    "embed",
                    "adapter_error",
                    exception_type=type(exc).__name__,
                    text_len=len(text),
                )
                return None

        if key == "":
            try:
                return self._embedder.encode(text)
            except Exception as exc:
                self._emit_failure(
                    "embed",
                    "adapter_error",
                    exception_type=type(exc).__name__,
                    text_len=len(text),
                )
                return None

        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached

        try:
            vector = self._embedder.encode(text)
        except Exception as exc:
            self._emit_failure(
                "embed",
                "adapter_error",
                exception_type=type(exc).__name__,
                text_len=len(text),
            )
            return None

        self._cache[key] = vector
        if len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)
        return vector
