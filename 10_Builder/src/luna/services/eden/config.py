"""
Eden service configuration.

Loaded from config/eden.json with env var overrides.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from luna.core.paths import config_dir as _config_dir


class EdenConfig(BaseModel):
    """Eden API connection configuration."""
    api_base: str = "https://api.eden.art"
    api_key: Optional[str] = None
    default_agent_id: Optional[str] = None

    # Task polling
    poll_interval_seconds: float = 3.0
    poll_max_attempts: int = 60  # 3 min max wait
    poll_backoff_factor: float = 1.2

    # Session defaults
    default_manna_budget: float = 100.0
    default_turn_budget: int = 50

    # HTTP
    timeout_seconds: float = 30.0
    max_retries: int = 3

    @classmethod
    def load(cls, config_dir: Optional[Path] = None) -> "EdenConfig":
        """Load config from eden.json with env var overrides."""
        config_dir = config_dir or _config_dir()
        config_path = config_dir / "eden.json"

        data = {}
        if config_path.exists():
            with open(config_path) as f:
                data = json.load(f)

        # Env var overrides (highest priority)
        if os.getenv("EDEN_API_BASE"):
            data["api_base"] = os.getenv("EDEN_API_BASE")
        if os.getenv("EDEN_API_KEY"):
            data["api_key"] = os.getenv("EDEN_API_KEY")
        if os.getenv("EDEN_AGENT_ID"):
            data["default_agent_id"] = os.getenv("EDEN_AGENT_ID")

        return cls(**data)

    @property
    def is_configured(self) -> bool:
        return self.api_key is not None
