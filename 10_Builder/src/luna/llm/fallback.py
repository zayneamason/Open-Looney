"""
Inference Fallback Chain.

Provides resilient LLM inference by trying providers in order
until one succeeds. Prevents Luna from hanging on API failures.

Usage:
    from luna.llm.fallback import FallbackChain, get_fallback_chain

    chain = get_fallback_chain()
    result = await chain.generate(messages, system="You are Luna...")

    # Reorder at runtime
    chain.set_chain(["groq", "local", "claude"])
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional, AsyncGenerator, TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import ProviderRegistry
    from luna.inference.local import LocalInference

logger = logging.getLogger(__name__)

# Default cooldown when a provider returns 429 without a parseable retry-after.
# 60s matches Gemini free-tier per-minute limits and is short enough that
# transient throttles don't lock the chain out for long.
_DEFAULT_429_COOLDOWN_S = 60.0
# Regex catches Gemini's "Please retry in 51.64662346s" pattern. Anthropic and
# Groq surface different shapes; this is best-effort, with the default above
# applied when no match is found.
_RETRY_DELAY_RE = re.compile(r"retry in (\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


def _parse_retry_delay_seconds(error_msg: str) -> Optional[float]:
    """Extract a retry-after duration from a provider error message.

    Returns the parsed float seconds if found, otherwise None.
    """
    if not error_msg:
        return None
    m = _RETRY_DELAY_RE.search(error_msg)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (ValueError, TypeError):
        return None


class AllProvidersFailedError(Exception):
    """Raised when all providers in the fallback chain fail."""

    def __init__(self, message: str, attempts: list["AttemptRecord"]):
        super().__init__(message)
        self.attempts = attempts


@dataclass
class AttemptRecord:
    """Record of a single provider attempt."""
    provider: str
    success: bool
    latency_ms: float
    error: Optional[str] = None
    status_code: Optional[int] = None


@dataclass
class FallbackResult:
    """Result from fallback chain inference."""
    content: str
    provider_used: str
    providers_tried: list[str]
    attempts: list[AttemptRecord]
    total_latency_ms: float


@dataclass
class ProviderStats:
    """Statistics for a single provider."""
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.attempts if self.attempts > 0 else 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts > 0 else 0.0


@dataclass
class FallbackStats:
    """Global fallback chain statistics."""
    total_requests: int = 0
    fallback_events: int = 0
    by_provider: dict[str, ProviderStats] = field(default_factory=dict)
    last_fallback: Optional[dict] = None

    def to_dict(self) -> dict:
        """Convert to dict for API response."""
        return {
            "total_requests": self.total_requests,
            "fallback_events": self.fallback_events,
            "by_provider": {
                name: {
                    "attempts": stats.attempts,
                    "successes": stats.successes,
                    "failures": stats.failures,
                    "avg_latency_ms": stats.avg_latency_ms,
                    "success_rate": stats.success_rate,
                }
                for name, stats in self.by_provider.items()
            },
            "last_fallback": self.last_fallback,
        }


class FallbackChain:
    """
    Inference fallback chain.

    Tries providers in configured order until one succeeds.
    Supports both registry providers (groq, gemini, claude) and
    local inference (Qwen via MLX).
    """

    def __init__(
        self,
        registry: Optional["ProviderRegistry"] = None,
        local_inference: Optional["LocalInference"] = None,
        chain: Optional[list[str]] = None,
        per_provider_timeout_ms: int = 30000,
        max_retries_per_provider: int = 1,
    ):
        """
        Initialize fallback chain.

        Args:
            registry: LLM provider registry (for groq, gemini, claude)
            local_inference: Local inference instance (for Qwen)
            chain: Ordered list of provider names to try
            per_provider_timeout_ms: Timeout per provider attempt
            max_retries_per_provider: Retries before moving to next
        """
        self._registry = registry
        self._local = local_inference
        self._chain = chain or ["local", "groq", "claude"]
        self._timeout_ms = per_provider_timeout_ms
        self._max_retries = max_retries_per_provider
        self._stats = FallbackStats()
        # provider_name -> unix timestamp when cooldown ends. A provider in
        # cooldown is skipped during chain iteration without an attempt.
        self._cooldowns: dict[str, float] = {}

        logger.info(f"FallbackChain initialized: chain={self._chain}")

    def _is_cooled_down(self, provider: str) -> tuple[bool, float]:
        """Return (cooled_down, seconds_remaining)."""
        until = self._cooldowns.get(provider, 0.0)
        now = time.time()
        if until > now:
            return True, until - now
        if until and until <= now:
            # Cooldown expired — clear so future skips don't keep firing
            self._cooldowns.pop(provider, None)
        return False, 0.0

    def _apply_cooldown(
        self,
        provider: str,
        error_msg: str,
        status_code: Optional[int],
    ) -> None:
        """Set a cooldown for a provider after a rate-limit-style failure.

        Triggered on status_code==429 or when the error message contains a
        parseable retry-after. Conservative: only cools down on signals that
        clearly indicate "this provider will fail for a while", not on generic
        transient errors.
        """
        delay = _parse_retry_delay_seconds(error_msg)
        if status_code == 429 and delay is None:
            delay = _DEFAULT_429_COOLDOWN_S
        if delay is None or delay <= 0:
            return
        # Cap at 5 minutes to avoid pathological provider responses locking
        # us out for hours on a single bad signal.
        delay = min(delay, 300.0)
        self._cooldowns[provider] = time.time() + delay
        logger.warning(
            f"[FALLBACK] cooldown applied: provider={provider} "
            f"duration_s={delay:.1f} reason=\"{error_msg[:120]}\""
        )

    def set_chain(self, providers: list[str]) -> list[str]:
        """
        Update chain order at runtime.

        Args:
            providers: New ordered list of provider names

        Returns:
            List of warnings (unknown providers, unavailable, etc)
        """
        warnings = []

        # Validate providers
        valid_providers = []
        for name in providers:
            if name == "local":
                if self._local is None:
                    warnings.append(f"local: LocalInference not configured")
                else:
                    valid_providers.append(name)
            elif self._registry and self._registry.get(name):
                valid_providers.append(name)
            else:
                warnings.append(f"{name}: Provider not found in registry")

        if valid_providers:
            self._chain = valid_providers
            logger.info(f"[FALLBACK] Chain updated: {self._chain}")
        else:
            warnings.append("No valid providers - keeping existing chain")

        return warnings

    def get_chain(self) -> list[str]:
        """Return current chain order."""
        return list(self._chain)

    def get_stats(self) -> dict:
        """Return attempt statistics."""
        return self._stats.to_dict()

    def _ensure_provider_stats(self, provider: str) -> ProviderStats:
        """Get or create stats for a provider."""
        if provider not in self._stats.by_provider:
            self._stats.by_provider[provider] = ProviderStats()
        return self._stats.by_provider[provider]

    def _to_llm_messages(self, messages: list[dict], system: str) -> list:
        """Convert dict messages to provider Message objects with system prompt prepended."""
        from .base import Message as LLMMessage

        llm_messages = [LLMMessage(role="system", content=system)]
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ["user", "assistant"]:
                llm_messages.append(LLMMessage(role=role, content=content))
        return llm_messages

    async def _try_local(
        self,
        messages: list[dict],
        system: str,
        max_tokens: int,
    ) -> tuple[str, Optional[str]]:
        """
        Try local inference.

        Returns:
            (response_text, error) - error is None on success
        """
        if self._local is None:
            return "", "LocalInference not configured"

        if not self._local.is_available:
            return "", "MLX not available on this system"

        try:
            # Ensure model is loaded
            if not self._local.is_loaded:
                await self._local.load_model()

            # Local inference expects user message, not messages array
            # Extract the last user message
            user_message = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    user_message = msg.get("content", "")
                    break

            if not user_message:
                return "", "No user message found"

            result = await asyncio.wait_for(
                self._local.generate(
                    user_message,
                    system_prompt=system,
                    max_tokens=max_tokens,
                ),
                timeout=self._timeout_ms / 1000,
            )

            return result.text, None

        except asyncio.TimeoutError:
            return "", "timeout"
        except Exception as e:
            return "", str(e)

    async def _try_provider(
        self,
        provider_name: str,
        messages: list[dict],
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, Optional[str], Optional[int]]:
        """
        Try a registry provider.

        Returns:
            (response_text, error, status_code)
        """
        if self._registry is None:
            return "", "Registry not configured", None

        provider = self._registry.get(provider_name)
        if provider is None:
            return "", f"Provider {provider_name} not found", None

        if not provider.is_available:
            return "", f"Provider {provider_name} not available", None

        try:
            llm_messages = self._to_llm_messages(messages, system)

            result = await asyncio.wait_for(
                provider.complete(
                    llm_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
                timeout=self._timeout_ms / 1000,
            )

            return result.content, None, None

        except asyncio.TimeoutError:
            return "", "timeout", None
        except Exception as e:
            # Try to extract status code from common API errors
            status_code = None
            error_msg = str(e)

            # Common patterns
            if "credit" in error_msg.lower():
                status_code = 402  # Payment required
            elif "rate" in error_msg.lower():
                status_code = 429
            elif hasattr(e, "status_code"):
                status_code = getattr(e, "status_code")

            return "", error_msg, status_code

    async def _try_provider_stream(
        self,
        provider_name: str,
        messages: list[dict],
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> AsyncGenerator[str, None]:
        """
        Stream from a registry provider. Raises on misconfiguration so the
        outer chain can record a pre-first-token failure and fall back.
        """
        if self._registry is None:
            raise RuntimeError("Registry not configured")

        provider = self._registry.get(provider_name)
        if provider is None:
            raise RuntimeError(f"Provider {provider_name} not found")

        if not provider.is_available:
            raise RuntimeError(f"Provider {provider_name} not available")

        llm_messages = self._to_llm_messages(messages, system)

        async for chunk in provider.stream(
            llm_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk

    async def _try_local_stream(
        self,
        messages: list[dict],
        system: str,
        max_tokens: int,
    ) -> AsyncGenerator[str, None]:
        """
        Stream from local inference. Raises on misconfiguration so the outer
        chain can record a pre-first-token failure and fall back.
        """
        if self._local is None:
            raise RuntimeError("LocalInference not configured")

        if not self._local.is_available:
            raise RuntimeError("MLX not available on this system")

        if not self._local.is_loaded:
            await self._local.load_model()

        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        if not user_message:
            raise RuntimeError("No user message found")

        async for token in self._local.generate_stream(
            user_message,
            system_prompt=system,
            max_tokens=max_tokens,
        ):
            yield token

    async def generate(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> FallbackResult:
        """
        Generate response using fallback chain.

        Tries providers in order until one succeeds.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system: System prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature

        Returns:
            FallbackResult with response and telemetry

        Raises:
            AllProvidersFailedError: If all providers fail
        """
        self._stats.total_requests += 1
        start_time = time.perf_counter()
        attempts: list[AttemptRecord] = []
        providers_tried: list[str] = []

        for idx, provider_name in enumerate(self._chain):
            attempt_start = time.perf_counter()
            providers_tried.append(provider_name)

            # Skip cooled-down providers without spending a real attempt. The
            # attempt is still recorded so telemetry shows why the chain
            # advanced.
            cooled, remaining = self._is_cooled_down(provider_name)
            if cooled:
                logger.info(
                    f"[FALLBACK] skipping {provider_name}: cooldown "
                    f"{remaining:.1f}s remaining"
                )
                attempts.append(AttemptRecord(
                    provider=provider_name,
                    success=False,
                    latency_ms=0.0,
                    error=f"cooldown ({remaining:.1f}s remaining)",
                    status_code=429,
                ))
                continue

            # Attempt generation
            if provider_name == "local":
                content, error = await self._try_local(
                    messages, system, max_tokens
                )
                status_code = None
            else:
                content, error, status_code = await self._try_provider(
                    provider_name, messages, system, max_tokens, temperature
                )

            latency_ms = (time.perf_counter() - attempt_start) * 1000
            success = error is None and content

            # If the provider failed with a rate-limit signal, register a
            # cooldown so subsequent requests skip it until the window clears.
            if not success and error:
                self._apply_cooldown(provider_name, error, status_code)

            # Record attempt
            attempt = AttemptRecord(
                provider=provider_name,
                success=success,
                latency_ms=latency_ms,
                error=error,
                status_code=status_code,
            )
            attempts.append(attempt)

            # Update stats
            stats = self._ensure_provider_stats(provider_name)
            stats.attempts += 1
            stats.total_latency_ms += latency_ms
            if success:
                stats.successes += 1
            else:
                stats.failures += 1

            # Log attempt
            if success:
                logger.info(
                    f"[INFERENCE] provider={provider_name} status=success "
                    f"latency_ms={latency_ms:.0f}"
                )
            else:
                logger.warning(
                    f"[INFERENCE] provider={provider_name} status=failed "
                    f"error=\"{error}\" latency_ms={latency_ms:.0f}"
                )

            if success:
                # Log fallback if not first provider
                if idx > 0:
                    self._stats.fallback_events += 1
                    prev_provider = self._chain[idx - 1]
                    prev_error = attempts[-2].error if len(attempts) > 1 else "unknown"
                    self._stats.last_fallback = {
                        "from": prev_provider,
                        "to": provider_name,
                        "reason": prev_error,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                    logger.info(
                        f"[FALLBACK] from={prev_provider} to={provider_name} "
                        f"reason=\"{prev_error}\""
                    )

                total_latency = (time.perf_counter() - start_time) * 1000
                return FallbackResult(
                    content=content,
                    provider_used=provider_name,
                    providers_tried=providers_tried,
                    attempts=attempts,
                    total_latency_ms=total_latency,
                )

        # All providers failed
        total_latency = (time.perf_counter() - start_time) * 1000
        error_summary = "; ".join(
            f"{a.provider}: {a.error}" for a in attempts
        )
        logger.error(f"[FALLBACK] All providers failed: {error_summary}")

        raise AllProvidersFailedError(
            f"All {len(attempts)} providers failed: {error_summary}",
            attempts=attempts,
        )

    async def stream(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """
        Stream response using fallback chain with real provider streaming.

        Iterates providers in chain order, calling each provider's real
        streaming path. Fallback is permitted ONLY before the first token
        has been emitted to the caller. After the first token is yielded,
        a mid-stream provider failure surfaces as an exception — no
        provider swap occurs and partial output is not replayed.

        The per-provider timeout is applied to time-to-first-token, not to
        full completion, so large prompts don't block fallback decisions.

        Args:
            messages: List of message dicts
            system: System prompt
            max_tokens: Maximum tokens
            temperature: Sampling temperature

        Yields:
            Response token chunks as they arrive from the provider

        Raises:
            AllProvidersFailedError: Every provider failed before first token
            Exception: A provider failed AFTER emitting at least one token
        """
        self._stats.total_requests += 1
        attempts: list[AttemptRecord] = []
        timeout_s = self._timeout_ms / 1000

        for idx, provider_name in enumerate(self._chain):
            attempt_start = time.perf_counter()

            # Skip cooled-down providers without instantiating their stream.
            cooled, remaining = self._is_cooled_down(provider_name)
            if cooled:
                logger.info(
                    f"[FALLBACK] skipping {provider_name}: cooldown "
                    f"{remaining:.1f}s remaining"
                )
                attempts.append(AttemptRecord(
                    provider=provider_name,
                    success=False,
                    latency_ms=0.0,
                    error=f"cooldown ({remaining:.1f}s remaining)",
                    status_code=429,
                ))
                continue

            if provider_name == "local":
                it = self._try_local_stream(messages, system, max_tokens)
            else:
                it = self._try_provider_stream(
                    provider_name, messages, system, max_tokens, temperature
                )

            try:
                first_chunk = await asyncio.wait_for(
                    it.__anext__(),
                    timeout=timeout_s,
                )
            except StopAsyncIteration:
                # Empty stream — count as success, no fallback needed
                latency_ms = (time.perf_counter() - attempt_start) * 1000
                attempts.append(AttemptRecord(
                    provider=provider_name,
                    success=True,
                    latency_ms=latency_ms,
                ))
                stats = self._ensure_provider_stats(provider_name)
                stats.attempts += 1
                stats.successes += 1
                stats.total_latency_ms += latency_ms
                logger.info(
                    f"[INFERENCE] provider={provider_name} status=success "
                    f"latency_ms={latency_ms:.0f} (streamed, empty)"
                )
                if idx > 0:
                    self._record_fallback_success(idx, provider_name, attempts)
                return
            except Exception as e:
                # Pre-first-token failure: release iterator, record, fall back
                try:
                    await it.aclose()
                except Exception:
                    pass

                latency_ms = (time.perf_counter() - attempt_start) * 1000
                error_msg = "timeout" if isinstance(e, asyncio.TimeoutError) else str(e)
                # Best-effort 429 detection on streamed errors. Mirrors the
                # pattern used in _try_provider so cooldown behavior is
                # consistent between generate() and stream().
                inferred_status: Optional[int] = None
                lowered = error_msg.lower()
                if "429" in error_msg or "rate" in lowered or "quota" in lowered:
                    inferred_status = 429
                self._apply_cooldown(provider_name, error_msg, inferred_status)
                attempts.append(AttemptRecord(
                    provider=provider_name,
                    success=False,
                    latency_ms=latency_ms,
                    error=error_msg,
                    status_code=inferred_status,
                ))
                stats = self._ensure_provider_stats(provider_name)
                stats.attempts += 1
                stats.failures += 1
                stats.total_latency_ms += latency_ms
                logger.warning(
                    f"[INFERENCE] provider={provider_name} status=failed "
                    f"error=\"{error_msg}\" latency_ms={latency_ms:.0f}"
                )
                if idx < len(self._chain) - 1:
                    next_provider = self._chain[idx + 1]
                    logger.info(
                        f"[FALLBACK] from={provider_name} to={next_provider} "
                        f"reason=\"{error_msg}\""
                    )
                continue

            # First chunk obtained — emit it, then enter post-first-token mode.
            # Reaching this point means fallback is no longer permitted; the
            # control-flow position itself encodes "first token already emitted".
            yield first_chunk

            try:
                async for chunk in it:
                    yield chunk
            except Exception as e:
                latency_ms = (time.perf_counter() - attempt_start) * 1000
                error_msg = f"post-first-token: {e}"
                attempts.append(AttemptRecord(
                    provider=provider_name,
                    success=False,
                    latency_ms=latency_ms,
                    error=error_msg,
                ))
                stats = self._ensure_provider_stats(provider_name)
                stats.attempts += 1
                stats.failures += 1
                stats.total_latency_ms += latency_ms
                logger.warning(
                    f"[STREAM] provider={provider_name} status=partial_failure "
                    f"error=\"{e}\""
                )
                raise

            # Successful drain
            latency_ms = (time.perf_counter() - attempt_start) * 1000
            attempts.append(AttemptRecord(
                provider=provider_name,
                success=True,
                latency_ms=latency_ms,
            ))
            stats = self._ensure_provider_stats(provider_name)
            stats.attempts += 1
            stats.successes += 1
            stats.total_latency_ms += latency_ms
            logger.info(
                f"[INFERENCE] provider={provider_name} status=success "
                f"latency_ms={latency_ms:.0f} (streamed)"
            )
            if idx > 0:
                self._record_fallback_success(idx, provider_name, attempts)
            return

        # All providers failed pre-first-token
        error_summary = "; ".join(f"{a.provider}: {a.error}" for a in attempts)
        logger.error(f"[FALLBACK] All providers failed: {error_summary}")

        raise AllProvidersFailedError(
            f"All {len(attempts)} providers failed: {error_summary}",
            attempts=attempts,
        )

    def _record_fallback_success(
        self,
        idx: int,
        provider_name: str,
        attempts: list[AttemptRecord],
    ) -> None:
        """Record fallback telemetry when a non-first provider succeeded."""
        self._stats.fallback_events += 1
        prev_provider = self._chain[idx - 1]
        prev_error = attempts[-2].error if len(attempts) > 1 else "unknown"
        self._stats.last_fallback = {
            "from": prev_provider,
            "to": provider_name,
            "reason": prev_error,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        logger.info(
            f"[FALLBACK] from={prev_provider} to={provider_name} "
            f"reason=\"{prev_error}\""
        )


# Global fallback chain instance
_fallback_chain: Optional[FallbackChain] = None


def get_fallback_chain() -> Optional[FallbackChain]:
    """Get the global fallback chain instance."""
    return _fallback_chain


def init_fallback_chain(
    registry: Optional["ProviderRegistry"] = None,
    local_inference: Optional["LocalInference"] = None,
    chain: Optional[list[str]] = None,
) -> FallbackChain:
    """
    Initialize the global fallback chain.

    Args:
        registry: LLM provider registry
        local_inference: Local inference instance
        chain: Initial chain order

    Returns:
        The initialized FallbackChain
    """
    global _fallback_chain
    _fallback_chain = FallbackChain(
        registry=registry,
        local_inference=local_inference,
        chain=chain,
    )
    return _fallback_chain
