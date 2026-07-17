"""
LLM Provider System for Luna Engine.

Provides a unified interface for multiple LLM providers (Groq, Gemini, Claude)
with hot-swappable provider switching.

Usage:
    from luna.llm import get_provider, get_registry, Message

    # Get current provider
    provider = get_provider()
    result = await provider.complete([Message("user", "Hello!")])

    # Switch providers
    registry = get_registry()
    registry.set_current("gemini")

    # Get specific provider
    groq = get_provider("groq")
"""
from .base import (
    LLMProvider,
    Message,
    CompletionResult,
    ModelInfo,
    ProviderLimits,
    ProviderStatus,
)
from .registry import get_registry, get_provider, ProviderRegistry
from .config import get_config, reload_config, set_current_provider, LLMConfig
from .providers import GroqProvider, GeminiProvider, ClaudeProvider, OllamaProvider


def init_providers():
    """Initialize and register all providers."""
    registry = get_registry()

    # Register all providers
    registry.register("groq", GroqProvider())
    registry.register("gemini", GeminiProvider())
    registry.register("claude", ClaudeProvider())
    registry.register("ollama", OllamaProvider())

    return registry


__all__ = [
    # Base types
    'LLMProvider',
    'Message',
    'CompletionResult',
    'ModelInfo',
    'ProviderLimits',
    'ProviderStatus',
    # Registry
    'ProviderRegistry',
    'get_registry',
    'get_provider',
    # Config
    'LLMConfig',
    'get_config',
    'reload_config',
    'set_current_provider',
    # Providers
    'GroqProvider',
    'GeminiProvider',
    'ClaudeProvider',
    'OllamaProvider',
    # Init
    'init_providers',
]
