"""
Fallback Chain Configuration.

Loads and persists the fallback chain configuration from YAML.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import ProviderRegistry

from luna.core.paths import config_dir

logger = logging.getLogger(__name__)

# Default config location
_CONFIG_DIR = config_dir()
DEFAULT_CONFIG_PATH = _CONFIG_DIR / "fallback_chain.yaml"


@dataclass
class FallbackConfig:
    """
    Fallback chain configuration.

    Loaded from config/fallback_chain.yaml or defaults.
    """

    chain: list[str] = field(default_factory=lambda: ["local", "groq", "claude"])
    per_provider_timeout_ms: int = 30000
    max_retries_per_provider: int = 1

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "FallbackConfig":
        """
        Load configuration from YAML file.

        Args:
            path: Config file path (uses default if None)

        Returns:
            FallbackConfig instance
        """
        config_path = path or DEFAULT_CONFIG_PATH

        if not config_path.exists():
            logger.info(f"Fallback config not found at {config_path}, using defaults")
            return cls()

        try:
            import yaml

            with open(config_path, "r") as f:
                data = yaml.safe_load(f) or {}

            # Use explicit defaults (dataclass fields aren't class attributes)
            default_chain = ["local", "groq", "claude"]
            default_timeout = 30000
            default_retries = 1

            config = cls(
                chain=data.get("chain", default_chain),
                per_provider_timeout_ms=data.get(
                    "per_provider_timeout_ms", default_timeout
                ),
                max_retries_per_provider=data.get(
                    "max_retries_per_provider", default_retries
                ),
            )

            logger.info(f"Loaded fallback config from {config_path}: chain={config.chain}")
            return config

        except ImportError:
            logger.warning("PyYAML not installed, using default config")
            return cls()
        except Exception as e:
            logger.error(f"Failed to load fallback config: {e}")
            return cls()

    def save(self, path: Optional[Path] = None) -> bool:
        """
        Save configuration to YAML file.

        Args:
            path: Config file path (uses default if None)

        Returns:
            True if saved successfully
        """
        config_path = path or DEFAULT_CONFIG_PATH

        try:
            import yaml

            # Ensure directory exists
            config_path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "chain": self.chain,
                "per_provider_timeout_ms": self.per_provider_timeout_ms,
                "max_retries_per_provider": self.max_retries_per_provider,
            }

            # Add comments to the YAML
            yaml_content = f"""# Inference Fallback Chain Configuration
# First provider that succeeds wins
# Edit this file or use API/UI to reorder

chain:
{chr(10).join(f'  - {p}' for p in self.chain)}

per_provider_timeout_ms: {self.per_provider_timeout_ms}
max_retries_per_provider: {self.max_retries_per_provider}
"""

            with open(config_path, "w") as f:
                f.write(yaml_content)

            logger.info(f"Saved fallback config to {config_path}")
            return True

        except ImportError:
            logger.error("PyYAML not installed, cannot save config")
            return False
        except Exception as e:
            logger.error(f"Failed to save fallback config: {e}")
            return False

    def validate(self, registry: Optional["ProviderRegistry"] = None) -> list[str]:
        """
        Validate configuration against available providers.

        Args:
            registry: Provider registry to validate against

        Returns:
            List of warning messages
        """
        warnings = []

        if not self.chain:
            warnings.append("Chain is empty - no providers configured")
            return warnings

        for provider in self.chain:
            if provider == "local":
                # Local is always valid (availability checked at runtime)
                continue

            if registry is None:
                warnings.append(f"{provider}: Cannot validate - no registry")
                continue

            provider_obj = registry.get(provider)
            if provider_obj is None:
                warnings.append(f"{provider}: Not found in registry")
            elif not provider_obj.is_available:
                warnings.append(f"{provider}: Not available (missing API key?)")

        return warnings


def load_fallback_config(path: Optional[Path] = None) -> FallbackConfig:
    """
    Convenience function to load fallback config.

    Args:
        path: Optional config file path

    Returns:
        FallbackConfig instance
    """
    return FallbackConfig.load(path)
