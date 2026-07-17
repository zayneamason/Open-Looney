"""
Local Embeddings using MiniLM
=============================

Free, local semantic embeddings using sentence-transformers.
No API costs, ~50ms per embedding on CPU.

Model: all-MiniLM-L6-v2 (384 dimensions)

Usage:
    embeddings = LocalEmbeddings()
    vector = embeddings.encode("search query")
    vectors = embeddings.encode_batch(["text1", "text2"])
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional
import threading

import contextlib
from luna.core.gpu_lock import gpu_lock

logger = logging.getLogger(__name__)

# Model configuration
MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

# Device selection: default CPU to avoid gpu_lock contention with MLX/Qwen.
# Set LUNA_EMBED_DEVICE=mps to opt back into shared-Metal mode (legacy).
EMBED_DEVICE = os.getenv("LUNA_EMBED_DEVICE", "cpu").lower()
# Cooldown after a failed model load to avoid repeated heavyweight retries
# on every subsequent embed call in the same process.
_LOAD_RETRY_COOLDOWN_SECONDS = float(
    os.getenv("LUNA_EMBED_LOAD_RETRY_COOLDOWN_SECONDS", "300")
)
# gpu_lock is only meaningful when both Qwen and the embedder touch Metal.
# On CPU, the lock would just preserve the contention we're trying to fix.
_USE_GPU_LOCK = EMBED_DEVICE == "mps"


def _lock_ctx():
    """Return gpu_lock when on Metal, no-op contextmanager otherwise."""
    return gpu_lock if _USE_GPU_LOCK else contextlib.nullcontext()

# Singleton instance and lock
_instance: Optional["LocalEmbeddings"] = None
_lock = threading.Lock()


def get_embeddings() -> "LocalEmbeddings":
    """
    Get the singleton LocalEmbeddings instance.

    Thread-safe lazy initialization.
    """
    global _instance
    with _lock:
        if _instance is None:
            logger.warning(f"[EMBED-SINGLETON] CREATING NEW INSTANCE pid={os.getpid()}")
            _instance = LocalEmbeddings()
        else:
            logger.info(f"[EMBED-SINGLETON] REUSING instance pid={os.getpid()}")
        return _instance


class LocalEmbeddings:
    """
    Local embedding generator using sentence-transformers MiniLM.

    Features:
    - 384-dimensional embeddings
    - ~50ms per embedding on CPU
    - No API costs
    - Thread-safe singleton pattern

    The model is loaded lazily on first use to avoid startup overhead.
    """

    def __init__(self):
        """Initialize the embeddings wrapper (model loaded lazily)."""
        self._model = None
        self._load_lock = threading.Lock()
        self._last_load_failure_ts: float = 0.0
        self._last_load_failure_error: Optional[str] = None
        self.dim = EMBEDDING_DIM
        self.model_name = MODEL_NAME
        logger.debug(f"LocalEmbeddings initialized (model will load on first use)")

    def _load_failure_cooldown_remaining(self) -> float:
        if self._last_load_failure_ts <= 0.0:
            return 0.0
        elapsed = time.time() - self._last_load_failure_ts
        return max(0.0, _LOAD_RETRY_COOLDOWN_SECONDS - elapsed)

    def load_status(self) -> dict:
        """Expose lightweight embed-model load state for diagnostics surfaces."""
        return {
            "loaded": self._model is not None,
            "cooldown_remaining_s": int(self._load_failure_cooldown_remaining()),
            "last_error": self._last_load_failure_error,
        }

    def _load_model(self) -> None:
        """Load the sentence-transformers model (lazy, thread-safe)."""
        if self._model is not None:
            logger.info(f"[EMBED-MODEL] MODEL ALREADY LOADED pid={os.getpid()}")
            return

        cooldown_remaining = self._load_failure_cooldown_remaining()
        if cooldown_remaining > 0.0:
            raise RuntimeError(
                "Embedding model load cooldown active "
                f"({int(cooldown_remaining)}s remaining): "
                f"{self._last_load_failure_error or 'previous_load_failure'}"
            )

        with self._load_lock:
            if self._model is not None:
                logger.info(f"[EMBED-MODEL] MODEL ALREADY LOADED pid={os.getpid()}")
                return

            cooldown_remaining = self._load_failure_cooldown_remaining()
            if cooldown_remaining > 0.0:
                raise RuntimeError(
                    "Embedding model load cooldown active "
                    f"({int(cooldown_remaining)}s remaining): "
                    f"{self._last_load_failure_error or 'previous_load_failure'}"
                )

            try:
                from sentence_transformers import SentenceTransformer
                logger.warning(f"[EMBED-MODEL] LOADING MODEL pid={os.getpid()} instance_id={id(self)} requested_device={EMBED_DEVICE}")
                self._model = SentenceTransformer(MODEL_NAME, device=EMBED_DEVICE)
                # Warmup probe — paying the first-encode cost here, at init,
                # tells us (a) device placement and (b) actual cold-start cost.
                # If warmup_ms is high (>30s), the 75s-on-first-real-query bug
                # we saw is just deferred cold-load, not a length issue.
                _t0 = time.time()
                _ = self._model.encode(["warmup probe"], show_progress_bar=False)
                _warmup_ms = (time.time() - _t0) * 1000
                logger.warning(
                    f"[EMBED-DIAG] model={MODEL_NAME} "
                    f"device={self._model.device} "
                    f"max_seq_length={self._model.max_seq_length} "
                    f"warmup_ms={_warmup_ms:.0f} "
                    f"pid={os.getpid()}"
                )
                logger.info(f"Embedding model loaded successfully ({EMBEDDING_DIM} dimensions)")
                self._last_load_failure_ts = 0.0
                self._last_load_failure_error = None
            except ImportError:
                self._last_load_failure_ts = time.time()
                self._last_load_failure_error = "sentence-transformers not installed"
                raise RuntimeError(
                    "sentence-transformers not installed. "
                    "Run: pip install sentence-transformers"
                )
            except Exception as e:
                self._last_load_failure_ts = time.time()
                self._last_load_failure_error = str(e)
                raise RuntimeError(f"Failed to load embedding model: {e}")

    @property
    def model(self):
        """Get the model, loading it if necessary."""
        if self._model is None:
            self._load_model()
        return self._model

    def encode(self, text: str, normalize: bool = True) -> list[float]:
        """
        Generate embedding for a single text.

        Args:
            text: The text to embed
            normalize: Whether to L2-normalize the embedding (default True)

        Returns:
            384-dimensional embedding vector
        """
        if not text or not text.strip():
            # Return zero vector for empty text
            return [0.0] * self.dim

        # Split timing: gpu_lock is shared with MLX inference (Qwen).
        # Slow embeds can be either lock-wait (Qwen mid-generation) or
        # actual encode time. Logging both disambiguates.
        import time as _t
        t0 = _t.time()
        with _lock_ctx():
            t1 = _t.time()
            embedding = self.model.encode(
                text,
                normalize_embeddings=normalize,
                show_progress_bar=False,
            )
            t2 = _t.time()
        wait_ms = (t1 - t0) * 1000
        encode_ms = (t2 - t1) * 1000
        total_ms = (t2 - t0) * 1000
        if total_ms > 500:
            logger.warning(
                f"[EMBED-PROFILE] slow_single wait_ms={wait_ms:.0f} "
                f"encode_ms={encode_ms:.0f} total_ms={total_ms:.0f} "
                f"chars={len(text)} words={len(text.split())}"
            )
        return embedding.tolist()

    def encode_batch(
        self,
        texts: list[str],
        normalize: bool = True,
        batch_size: int = 32,
        show_progress: bool = False,
    ) -> list[list[float]]:
        """
        Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed
            normalize: Whether to L2-normalize embeddings (default True)
            batch_size: Batch size for encoding (default 32)
            show_progress: Whether to show progress bar (default False)

        Returns:
            List of 384-dimensional embedding vectors
        """
        if not texts:
            return []

        # Handle empty strings
        non_empty_indices = []
        non_empty_texts = []
        for i, text in enumerate(texts):
            if text and text.strip():
                non_empty_indices.append(i)
                non_empty_texts.append(text)

        # Initialize results with zero vectors
        results = [[0.0] * self.dim for _ in texts]

        if non_empty_texts:
            import time as _t
            t0 = _t.time()
            with _lock_ctx():
                t1 = _t.time()
                embeddings = self.model.encode(
                    non_empty_texts,
                    normalize_embeddings=normalize,
                    batch_size=batch_size,
                    show_progress_bar=show_progress,
                )
                t2 = _t.time()
            wait_ms = (t1 - t0) * 1000
            encode_ms = (t2 - t1) * 1000
            total_ms = (t2 - t0) * 1000
            if total_ms > 500:
                total_chars = sum(len(t) for t in non_empty_texts)
                logger.warning(
                    f"[EMBED-PROFILE] slow_batch wait_ms={wait_ms:.0f} "
                    f"encode_ms={encode_ms:.0f} total_ms={total_ms:.0f} "
                    f"n={len(non_empty_texts)} total_chars={total_chars} "
                    f"batch_size={batch_size}"
                )

            # Place embeddings back in correct positions
            for idx, embedding in zip(non_empty_indices, embeddings):
                results[idx] = embedding.tolist()

        return results

    def similarity(self, text1: str, text2: str) -> float:
        """
        Compute cosine similarity between two texts.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Cosine similarity score (0-1 for normalized vectors)
        """
        emb1 = self.encode(text1)
        emb2 = self.encode(text2)

        # Cosine similarity (vectors are already normalized)
        dot_product = sum(a * b for a, b in zip(emb1, emb2))
        return dot_product

    def is_loaded(self) -> bool:
        """Check if the model is currently loaded."""
        return self._model is not None

    def preload(self) -> None:
        """
        Explicitly load the model.

        Call this during startup to avoid first-query latency.
        """
        self._load_model()
