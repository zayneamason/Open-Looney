"""
LLM Provider Protocol and base types.

Defines the abstract interface that all LLM providers must implement,
plus shared dataclasses for messages, results, and limits.
"""
from typing import Protocol, AsyncIterator, runtime_checkable
from dataclasses import dataclass, field
from enum import Enum


@dataclass
class Message:
    """A single message in a conversation."""
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class ModelInfo:
    """Information about a model."""
    name: str
    context_window: int
    supports_streaming: bool = True


@dataclass
class ProviderLimits:
    """Rate limits and constraints for a provider."""
    requests_per_minute: int
    tokens_per_day: int | None = None
    requires_payment: bool = False


@dataclass
class CompletionResult:
    """Result from a completion request."""
    content: str
    model: str
    usage: dict = field(default_factory=dict)  # {"prompt_tokens": int, "completion_tokens": int}
    provider: str = ""


class ProviderStatus(Enum):
    """Provider availability status."""
    AVAILABLE = "available"        # Configured and ready
    NOT_CONFIGURED = "not_configured"  # Missing API key
    RATE_LIMITED = "rate_limited"  # Hit rate limit
    ERROR = "error"                # Other error


@runtime_checkable
class LLMProvider(Protocol):
    """
    Abstract interface for LLM providers.

    All providers (Groq, Gemini, Claude) must implement this protocol.
    """

    name: str

    @property
    def is_available(self) -> bool:
        """True if API key is configured and provider is ready."""
        ...

    async def complete(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        model: str | None = None
    ) -> CompletionResult:
        """Single completion request."""
        ...

    async def stream(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        model: str | None = None
    ) -> AsyncIterator[str]:
        """Streaming completion."""
        ...

    def get_model_info(self, model: str | None = None) -> ModelInfo:
        """Get info about the model."""
        ...

    def get_limits(self) -> ProviderLimits:
        """Get rate limits and constraints."""
        ...

    def get_status(self) -> ProviderStatus:
        """Get current provider status."""
        ...

    def list_models(self) -> list[str]:
        """List available models for this provider."""
        ...
