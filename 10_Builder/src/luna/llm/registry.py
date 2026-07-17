"""
LLM Provider Registry.

Singleton registry for managing and switching between LLM providers.
"""
import logging
from typing import Optional

from .base import LLMProvider, ProviderStatus
from .config import get_config, set_current_provider as save_current_provider

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """
    Registry for LLM providers.

    Manages registration, switching, and availability of providers.
    Hot-swap capable - no restart required to switch providers.
    """

    _instance: Optional["ProviderRegistry"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._providers: dict[str, LLMProvider] = {}
        self._initialized = True
        logger.info("ProviderRegistry initialized")

    def register(self, name: str, provider: LLMProvider):
        """Register a provider."""
        self._providers[name] = provider
        logger.info(f"Registered provider: {name} (available: {provider.is_available})")

    def unregister(self, name: str):
        """Unregister a provider."""
        if name in self._providers:
            del self._providers[name]
            logger.info(f"Unregistered provider: {name}")

    def get(self, name: str) -> Optional[LLMProvider]:
        """Get a provider by name."""
        return self._providers.get(name)

    def get_by_name(self, name: str) -> Optional[LLMProvider]:
        """Get provider by name (alias for get())."""
        return self._providers.get(name)

    def is_available(self, name: str) -> bool:
        """Check if provider exists and is available."""
        provider = self._providers.get(name)
        return provider is not None and provider.is_available

    def get_current(self) -> Optional[LLMProvider]:
        """Get the currently selected provider."""
        config = get_config()
        return self._providers.get(config.current_provider)

    def set_current(self, name: str) -> bool:
        """
        Set the current provider.

        Returns:
            True if successful, False if provider not found or unavailable
        """
        if name not in self._providers:
            logger.warning(f"Provider not found: {name}")
            return False

        provider = self._providers[name]
        if not provider.is_available:
            logger.warning(f"Provider not available: {name}")
            return False

        save_current_provider(name)
        logger.info(f"Switched to provider: {name}")
        return True

    def list_available(self) -> list[str]:
        """List all registered provider names."""
        return list(self._providers.keys())

    def list_configured(self) -> list[str]:
        """List providers with API keys configured."""
        return [
            name for name, provider in self._providers.items()
            if provider.is_available
        ]

    def get_all_status(self) -> dict[str, dict]:
        """
        Get status of all providers for UI display.

        Returns:
            Dict mapping provider name to status info
        """
        config = get_config()
        result = {}

        for name, provider in self._providers.items():
            pconfig = config.get_provider_config(name)
            result[name] = {
                "name": name,
                "is_available": provider.is_available,
                "is_current": config.current_provider == name,
                "status": provider.get_status().value,
                "models": provider.list_models() if provider.is_available else [],
                "default_model": pconfig.default_model if pconfig else "",
                "limits": {
                    "rpm": provider.get_limits().requests_per_minute,
                    "requires_payment": provider.get_limits().requires_payment,
                } if provider.is_available else {},
            }

        return result


# Global registry instance
_registry: Optional[ProviderRegistry] = None


def get_registry() -> ProviderRegistry:
    """Get the global registry instance."""
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


def get_provider(name: str = None) -> Optional[LLMProvider]:
    """
    Get a provider by name, or the current provider if no name given.

    Convenience function for common usage.
    """
    registry = get_registry()
    if name:
        return registry.get(name)
    return registry.get_current()
