"""
LLM Provider configuration loader.

Loads provider settings from config/llm_providers.json and environment variables.
"""
import json
import os
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from luna.core.paths import config_dir

logger = logging.getLogger(__name__)

# Default config path
CONFIG_PATH = config_dir() / "llm_providers.json"


@dataclass
class ProviderConfig:
    """Configuration for a single provider."""
    name: str
    enabled: bool = True
    api_key_env: str = ""
    default_model: str = ""
    models: list[str] = field(default_factory=list)

    @property
    def api_key(self) -> Optional[str]:
        """Get API key from environment."""
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None

    @property
    def is_configured(self) -> bool:
        """Check if API key is set."""
        return self.api_key is not None and len(self.api_key) > 0


@dataclass
class LLMConfig:
    """Full LLM configuration."""
    current_provider: str = "groq"
    default_provider: str = "groq"
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "LLMConfig":
        """Load configuration from JSON file."""
        if not path.exists():
            logger.warning(f"Config not found at {path}, using defaults")
            return cls._create_default()

        try:
            with open(path) as f:
                data = json.load(f)

            providers = {}
            for name, pconfig in data.get("providers", {}).items():
                providers[name] = ProviderConfig(
                    name=name,
                    enabled=pconfig.get("enabled", True),
                    api_key_env=pconfig.get("api_key_env", ""),
                    default_model=pconfig.get("default_model", ""),
                    models=pconfig.get("models", []),
                )

            return cls(
                current_provider=data.get("current_provider", "groq"),
                default_provider=data.get("default_provider", "groq"),
                providers=providers,
            )
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return cls._create_default()

    @classmethod
    def _create_default(cls) -> "LLMConfig":
        """Create default configuration."""
        return cls(
            current_provider="groq",
            default_provider="groq",
            providers={
                "groq": ProviderConfig(
                    name="groq",
                    enabled=True,
                    api_key_env="GROQ_API_KEY",
                    default_model="llama-3.1-70b-versatile",
                    models=[
                        "llama-3.1-70b-versatile",
                        "llama-3.1-8b-instant",
                        "mixtral-8x7b-32768",
                    ],
                ),
                "gemini": ProviderConfig(
                    name="gemini",
                    enabled=True,
                    api_key_env="GOOGLE_API_KEY",
                    default_model="gemini-2.0-flash",
                    models=[
                        "gemini-2.0-flash",
                        "gemini-2.0-flash-lite",
                        "gemini-1.5-pro",
                    ],
                ),
                "claude": ProviderConfig(
                    name="claude",
                    enabled=True,
                    api_key_env="ANTHROPIC_API_KEY",
                    default_model="claude-haiku-4-5-20251001",
                    models=[
                        "claude-haiku-4-5-20251001",
                        "claude-sonnet-4-6",
                        "claude-opus-4-6",
                    ],
                ),
            },
        )

    def save(self, path: Path = CONFIG_PATH):
        """Save configuration to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "current_provider": self.current_provider,
            "default_provider": self.default_provider,
            "providers": {
                name: {
                    "enabled": p.enabled,
                    "api_key_env": p.api_key_env,
                    "default_model": p.default_model,
                    "models": p.models,
                }
                for name, p in self.providers.items()
            },
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved LLM config to {path}")

    def get_provider_config(self, name: str) -> Optional[ProviderConfig]:
        """Get configuration for a specific provider."""
        return self.providers.get(name)


# Global config instance
_config: Optional[LLMConfig] = None


def get_config() -> LLMConfig:
    """Get or load the global config."""
    global _config
    if _config is None:
        _config = LLMConfig.load()
    return _config


def reload_config():
    """Force reload of configuration."""
    global _config
    _config = LLMConfig.load()
    return _config


def set_current_provider(name: str) -> bool:
    """Set the current provider and save config."""
    config = get_config()
    if name not in config.providers:
        return False
    config.current_provider = name
    config.save()
    return True
