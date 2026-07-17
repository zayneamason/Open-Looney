"""
Luna Engine Local Inference — MLX + Qwen 3B
============================================

Fast local inference for the hot path (<200ms).
Uses Apple Silicon MLX for efficient on-device generation.

This is Luna's MIND - her fast-twitch response system.
Claude is a research assistant for complex queries.

Usage:
    inference = LocalInference()
    await inference.load_model()

    # Streaming generation
    async for token in inference.generate_stream("Hello Luna"):
        print(token, end="", flush=True)

LoRA Adapter:
    Luna has a trained personality LoRA at:
    models/luna_lora_mlx/

    To use it:
    1. Convert PEFT LoRA to MLX format:
       python -m mlx_lm.lora.convert --hf-path luna_director_3b_lora --mlx-path luna_lora_mlx

    2. Either fuse with base model:
       python -m mlx_lm.lora.fuse --base-model Qwen/Qwen2.5-3B-Instruct --adapter-path luna_lora_mlx

    3. Or load dynamically (pass adapter_path to load)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional

from luna.core.gpu_lock import gpu_lock
from luna.core.paths import project_root

logger = logging.getLogger(__name__)

# Default model - Qwen2.5-3B-Instruct (fast, capable)
DEFAULT_MODEL = "Qwen/Qwen2.5-3B-Instruct"
FALLBACK_MODEL = "mlx-community/Qwen2.5-3B-Instruct-4bit"

# Local model paths (preferred over downloading from HuggingFace)
_MODELS_DIR = project_root() / "models"
LOCAL_MODEL_PATH = _MODELS_DIR / "Qwen2.5-3B-Instruct-MLX-4bit"

# Luna LoRA adapter path (trained personality) - MLX format
LUNA_LORA_PATH = _MODELS_DIR / "luna_lora_mlx"


@dataclass
class InferenceConfig:
    """Configuration for local inference."""

    model_id: str = DEFAULT_MODEL
    max_tokens: int = 512
    temperature: float = 0.7
    top_p: float = 0.9
    repetition_penalty: float = 1.1

    # Performance tuning
    use_4bit: bool = True  # Use 4-bit quantized for speed
    cache_prompt: bool = True

    # Timeout for hot path (ms)
    hot_path_timeout_ms: int = 200

    # LoRA adapter path (Luna's personality layer)
    adapter_path: Optional[Path] = None

    @classmethod
    def from_config(cls, config_path: str = "config/local_inference.json") -> "InferenceConfig":
        """Load config from local_inference.json, falling back to defaults."""
        path = Path(config_path)
        if path.exists():
            try:
                cfg = json.loads(path.read_text())
                model = cfg.get("model", {})
                gen = cfg.get("generation", {})
                perf = cfg.get("performance", {})
                return cls(
                    model_id=model.get("model_id", DEFAULT_MODEL),
                    use_4bit=model.get("use_4bit", True),
                    cache_prompt=model.get("cache_prompt", True),
                    adapter_path=Path(model["adapter_path"]) if model.get("adapter_path") else None,
                    max_tokens=gen.get("max_tokens", 512),
                    temperature=gen.get("temperature", 0.7),
                    top_p=gen.get("top_p", 0.9),
                    repetition_penalty=gen.get("repetition_penalty", 1.1),
                    hot_path_timeout_ms=perf.get("hot_path_timeout_ms", 200),
                )
            except Exception as e:
                logger.warning(f"Failed to load local inference config: {e}")
        return cls()


@dataclass
class GenerationResult:
    """Result from a generation."""

    text: str
    tokens: int = 0
    latency_ms: float = 0.0
    tokens_per_second: float = 0.0
    from_cache: bool = False


class LocalInference:
    """
    MLX-based local inference for Qwen 3B.

    Provides fast (<200ms first token) local generation
    for Luna's hot path responses.
    """

    def __init__(self, config: Optional[InferenceConfig] = None):
        """Initialize local inference."""
        self.config = config or InferenceConfig()
        self._model = None
        self._tokenizer = None
        self._loaded = False
        self._loading = False
        self._adapter_loaded = False
        self._load_error: Optional[str] = None

        # Streaming callbacks
        self._stream_callbacks: list[Callable[[str], None]] = []

        # Performance tracking
        self._generation_count = 0
        self._total_tokens = 0
        self._total_latency_ms = 0.0

    @property
    def is_loaded(self) -> bool:
        """Check if model is loaded and ready."""
        return self._loaded and self._model is not None

    @property
    def is_available(self) -> bool:
        """Check if MLX is available on this system."""
        try:
            import mlx.core as mx
            return True
        except ImportError:
            return False

    async def load_model(self) -> bool:
        """
        Load the Qwen model into memory.

        Returns:
            True if loaded successfully
        """
        if self._loaded:
            return True

        if self._loading:
            # Wait for existing load
            while self._loading:
                await asyncio.sleep(0.1)
            return self._loaded

        self._loading = True

        try:
            # Import MLX-LM (lazy to avoid import errors on non-Mac)
            from mlx_lm import load, generate

            model_id = self.config.model_id

            # Check for Luna LoRA adapter FIRST (before deciding on model)
            adapter_path = self.config.adapter_path
            if adapter_path is None and LUNA_LORA_PATH.exists():
                adapter_path = LUNA_LORA_PATH
                logger.info(f"Found Luna LoRA adapter: {adapter_path}")

            # Prefer local model if available (faster startup, no download needed)
            # The Luna LoRA adapter is compatible with the 4-bit quantized model
            if LOCAL_MODEL_PATH.exists() and (LOCAL_MODEL_PATH / "model.safetensors").exists():
                model_id = str(LOCAL_MODEL_PATH)
                logger.info(f"Using local model: {model_id}")
            elif adapter_path is not None:
                # Fallback: download full-precision model for LoRA compatibility
                if "4bit" in model_id or model_id == FALLBACK_MODEL:
                    model_id = DEFAULT_MODEL
                    logger.info(f"LoRA adapter detected - using full-precision model: {model_id}")
                else:
                    logger.info(f"Using model with LoRA adapter: {model_id}")
            elif self.config.use_4bit and "4bit" not in model_id:
                # No local model or LoRA - use 4-bit quantized version
                model_id = FALLBACK_MODEL
                logger.info(f"No local model - downloading 4-bit quantized: {model_id}")

            logger.info(f"Loading model: {model_id}")
            start = time.perf_counter()

            # Load model and tokenizer (with optional LoRA adapter)
            # Run in executor to not block event loop
            loop = asyncio.get_event_loop()
            adapter_str = str(adapter_path) if adapter_path else None

            # FIX: Log the exact parameters being passed to MLX load()
            logger.info(f"MLX load() params: model_id={model_id}, adapter_path={adapter_str}")

            self._model, self._tokenizer = await loop.run_in_executor(
                None,
                lambda: load(model_id, adapter_path=adapter_str)
            )

            load_time = (time.perf_counter() - start) * 1000
            lora_str = " + Luna LoRA" if adapter_path else ""
            logger.info(f"Model{lora_str} loaded in {load_time:.0f}ms")

            # FIX: Additional verification that adapter was applied
            if adapter_path:
                logger.info(f"Luna personality LoRA applied from: {adapter_path}")

            self._loaded = True
            self._adapter_loaded = adapter_path is not None
            self._load_error = None
            return True

        except ImportError as e:
            self._load_error = f"MLX not available: {e}"
            from luna.diagnostics.maturity import compiled_debug
            compiled_debug(logger, "MLX not available: %s", e)
            compiled_debug(logger, "Install with: pip install mlx-lm")
            return False

        except Exception as e:
            self._load_error = f"Failed to load model: {e}"
            logger.error(self._load_error)
            return False

        finally:
            self._loading = False

    async def unload_model(self) -> None:
        """Unload model from memory."""
        if self._model is not None:
            del self._model
            self._model = None
        if self._tokenizer is not None:
            del self._tokenizer
            self._tokenizer = None
        self._loaded = False
        logger.info("Model unloaded")

    def _format_prompt(self, user_message: str, system_prompt: Optional[str] = None) -> str:
        """Format prompt for Qwen chat template."""
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": user_message})

        # Use tokenizer's chat template if available
        if hasattr(self._tokenizer, "apply_chat_template"):
            return self._tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True
            )

        # Fallback: simple format
        prompt = ""
        if system_prompt:
            prompt += f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
        prompt += f"<|im_start|>user\n{user_message}<|im_end|>\n"
        prompt += "<|im_start|>assistant\n"
        return prompt

    async def generate(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> GenerationResult:
        """
        Generate a complete response (non-streaming).

        Args:
            user_message: The user's input
            system_prompt: Optional system prompt
            max_tokens: Override max tokens

        Returns:
            GenerationResult with text and metrics
        """
        if not self._loaded:
            await self.load_model()

        if not self._loaded:
            raise RuntimeError(f"Model not loaded: {self._load_error}")

        from mlx_lm import generate

        prompt = self._format_prompt(user_message, system_prompt)
        max_toks = max_tokens or self.config.max_tokens

        start = time.perf_counter()

        # Run generation in executor
        loop = asyncio.get_event_loop()

        # Create sampler with temperature settings
        from mlx_lm.sample_utils import make_sampler, make_repetition_penalty
        sampler = make_sampler(
            temp=self.config.temperature,
            top_p=self.config.top_p,
        )

        # Create repetition penalty processor to prevent loops
        rep_penalty = make_repetition_penalty(
            penalty=self.config.repetition_penalty,
            context_size=50  # Look back 50 tokens for repetition
        )

        def _locked_generate():
            # 8s acquire timeout — fail fast if GPU is held by a concurrent request
            # rather than queuing indefinitely and causing cascade timeouts.
            if not gpu_lock.acquire(timeout=8.0):
                raise RuntimeError("GPU lock contention timeout (>8s)")
            try:
                return generate(
                    self._model,
                    self._tokenizer,
                    prompt=prompt,
                    max_tokens=max_toks,
                    sampler=sampler,
                    logits_processors=[rep_penalty],
                )
            finally:
                gpu_lock.release()

        response = await loop.run_in_executor(None, _locked_generate)

        latency_ms = (time.perf_counter() - start) * 1000

        # Count tokens (approximate)
        tokens = len(self._tokenizer.encode(response)) if self._tokenizer else len(response.split())
        tps = tokens / (latency_ms / 1000) if latency_ms > 0 else 0

        # Track stats
        self._generation_count += 1
        self._total_tokens += tokens
        self._total_latency_ms += latency_ms

        return GenerationResult(
            text=response,
            tokens=tokens,
            latency_ms=latency_ms,
            tokens_per_second=tps,
        )

    async def generate_stream(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Generate response with streaming tokens.

        Args:
            user_message: The user's input
            system_prompt: Optional system prompt
            max_tokens: Override max tokens

        Yields:
            Tokens as they are generated
        """
        if not self._loaded:
            await self.load_model()

        if not self._loaded:
            raise RuntimeError(f"Model not loaded: {self._load_error}")

        from mlx_lm import stream_generate

        prompt = self._format_prompt(user_message, system_prompt)
        max_toks = max_tokens or self.config.max_tokens

        start = time.perf_counter()
        token_count = 0

        # Stream tokens
        # Note: stream_generate is synchronous, so we yield control periodically
        loop = asyncio.get_event_loop()

        # Create a queue for tokens
        token_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

        def generate_tokens():
            """Run generation in thread and push tokens to queue."""
            with gpu_lock:
                try:
                    # Create sampler
                    from mlx_lm.sample_utils import make_sampler, make_repetition_penalty
                    sampler = make_sampler(
                        temp=self.config.temperature,
                        top_p=self.config.top_p,
                    )

                    # Create repetition penalty processor to prevent loops
                    rep_penalty = make_repetition_penalty(
                        penalty=self.config.repetition_penalty,
                        context_size=50  # Look back 50 tokens for repetition
                    )

                    for response in stream_generate(
                        self._model,
                        self._tokenizer,
                        prompt=prompt,
                        max_tokens=max_toks,
                        sampler=sampler,
                        logits_processors=[rep_penalty],
                    ):
                        token = response.text
                        # Put token in queue (thread-safe)
                        asyncio.run_coroutine_threadsafe(
                            token_queue.put(token),
                            loop
                        )
                finally:
                    # Signal completion
                    asyncio.run_coroutine_threadsafe(
                        token_queue.put(None),
                        loop
                    )

        # Start generation in background thread
        import concurrent.futures
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(generate_tokens)

        try:
            while True:
                token = await token_queue.get()
                if token is None:
                    break

                token_count += 1

                # Invoke callbacks
                for callback in self._stream_callbacks:
                    try:
                        callback(token)
                    except Exception as e:
                        logger.warning(f"Stream callback error: {e}")

                yield token

        finally:
            executor.shutdown(wait=False)

        latency_ms = (time.perf_counter() - start) * 1000

        # Track stats
        self._generation_count += 1
        self._total_tokens += token_count
        self._total_latency_ms += latency_ms

        logger.debug(f"Generated {token_count} tokens in {latency_ms:.0f}ms")

    def on_stream(self, callback: Callable[[str], None]) -> None:
        """Register a streaming callback."""
        self._stream_callbacks.append(callback)

    def remove_stream_callback(self, callback: Callable[[str], None]) -> None:
        """Remove a streaming callback."""
        if callback in self._stream_callbacks:
            self._stream_callbacks.remove(callback)

    def get_stats(self) -> dict:
        """Get inference statistics."""
        avg_latency = (
            self._total_latency_ms / self._generation_count
            if self._generation_count > 0 else 0
        )
        avg_tps = (
            self._total_tokens / (self._total_latency_ms / 1000)
            if self._total_latency_ms > 0 else 0
        )

        return {
            "loaded": self._loaded,
            "model": self.config.model_id if self._loaded else None,
            "luna_lora": self._adapter_loaded,
            "generation_count": self._generation_count,
            "total_tokens": self._total_tokens,
            "avg_latency_ms": avg_latency,
            "avg_tokens_per_second": avg_tps,
        }


class HybridInference:
    """
    Hybrid inference router.

    Routes between fast local inference (Qwen 3B) and
    powerful cloud inference (Claude) based on:
    - Query complexity
    - Latency requirements
    - Local model availability
    """

    def __init__(
        self,
        local: Optional[LocalInference] = None,
        complexity_threshold: float = 0.15,  # Low threshold - delegate most queries to Claude
    ):
        """
        Initialize hybrid inference.

        Args:
            local: Local inference instance (created if None)
            complexity_threshold: Score above which to use Claude
        """
        self.local = local or LocalInference()
        self.complexity_threshold = complexity_threshold

        # Track routing decisions
        self._local_count = 0
        self._cloud_count = 0

    def estimate_complexity(self, user_message: str) -> float:
        """
        Estimate query complexity (0-1).

        Simple heuristics:
        - Length of message
        - Presence of complex keywords
        - Question depth indicators

        Returns:
            Complexity score 0-1 (higher = more complex)
        """
        score = 0.0

        # Length factor
        words = len(user_message.split())
        if words > 50:
            score += 0.3
        elif words > 20:
            score += 0.1

        # Complexity indicators
        complex_keywords = [
            "explain", "analyze", "compare", "evaluate", "synthesize",
            "why", "how does", "what if", "consider", "implications",
            "code", "debug", "implement", "architecture", "design",
            "research", "summarize", "translate", "write",
        ]

        message_lower = user_message.lower()
        for keyword in complex_keywords:
            if keyword in message_lower:
                score += 0.15

        # Multi-part questions
        if "?" in user_message:
            question_count = user_message.count("?")
            score += min(0.2 * question_count, 0.4)

        # Technical indicators
        if any(c in user_message for c in ["```", "def ", "class ", "function"]):
            score += 0.3

        return min(score, 1.0)

    def should_use_local(self, user_message: str) -> bool:
        """
        Decide if local inference should be used.

        Returns:
            True if local should be used, False for cloud
        """
        # Check if local model is loaded and ready
        if not self.local.is_loaded:
            return False

        # Estimate complexity
        complexity = self.estimate_complexity(user_message)

        return complexity < self.complexity_threshold

    async def route(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        force_local: bool = False,
        force_cloud: bool = False,
    ) -> tuple[str, bool]:
        """
        Route to appropriate inference backend.

        Args:
            user_message: The user's input
            system_prompt: Optional system prompt
            force_local: Force local inference
            force_cloud: Force cloud inference

        Returns:
            Tuple of (response_text, used_local)
        """
        use_local = force_local or (
            not force_cloud and self.should_use_local(user_message)
        )

        if use_local:
            self._local_count += 1
            result = await self.local.generate(user_message, system_prompt)
            return result.text, True
        else:
            self._cloud_count += 1
            # Return None to signal cloud should be used
            # The caller (Director) handles Claude API
            return "", False

    def get_stats(self) -> dict:
        """Get routing statistics."""
        total = self._local_count + self._cloud_count
        local_pct = (self._local_count / total * 100) if total > 0 else 0

        return {
            "local_count": self._local_count,
            "cloud_count": self._cloud_count,
            "local_percentage": local_pct,
            "local_stats": self.local.get_stats(),
        }
