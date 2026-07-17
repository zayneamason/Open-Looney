"""Director pivot mode config — System Spec v1.2 Section 5.

Loads `DIRECTOR_PIVOT_MODE` and `DIRECTOR_PIVOT_ENDPOINTS` from YAML
with environment-variable override. Phase A: helper is tested but no
runtime caller gates on it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from luna.core.paths import config_dir

logger = logging.getLogger("luna.lexicon.pivot")

VALID_MODES = ("legacy", "shadow", "active")
DEFAULT_MODE = "legacy"

DEFAULT_CONFIG_PATH = config_dir() / "pivot_mode.yaml"


@dataclass(frozen=True)
class PivotConfig:
    mode: str = DEFAULT_MODE
    endpoints: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "PivotConfig":
        config_path = path or DEFAULT_CONFIG_PATH

        yaml_mode: Optional[str] = None
        yaml_endpoints: Optional[Tuple[str, ...]] = None

        if config_path.exists():
            try:
                import yaml

                with open(config_path, "r") as f:
                    data = yaml.safe_load(f) or {}
                if "mode" in data and data["mode"] is not None:
                    yaml_mode = str(data["mode"])
                if "endpoints" in data and data["endpoints"] is not None:
                    yaml_endpoints = tuple(str(e) for e in data["endpoints"])
            except ImportError:
                logger.warning("[LEXICON] PyYAML not installed; pivot config falls back to defaults")
            except Exception as exc:
                logger.error("[LEXICON] Failed to load pivot config from %s: %s", config_path, exc)

        env_mode = os.environ.get("DIRECTOR_PIVOT_MODE")
        env_endpoints_raw = os.environ.get("DIRECTOR_PIVOT_ENDPOINTS")

        mode = env_mode if env_mode is not None else (yaml_mode if yaml_mode is not None else DEFAULT_MODE)
        if mode not in VALID_MODES:
            logger.warning(
                "[LEXICON] Invalid DIRECTOR_PIVOT_MODE=%r; falling back to %r", mode, DEFAULT_MODE
            )
            mode = DEFAULT_MODE

        if env_endpoints_raw is not None:
            endpoints = tuple(
                e.strip() for e in env_endpoints_raw.split(",") if e.strip()
            )
        elif yaml_endpoints is not None:
            endpoints = yaml_endpoints
        else:
            endpoints = ()

        return cls(mode=mode, endpoints=endpoints)


def is_pivot_enabled(
    endpoint: str,
    *,
    mode: Optional[str] = None,
    config: Optional[PivotConfig] = None,
) -> bool:
    """Return True iff pivot mode applies to this endpoint.

    `legacy` always returns False, regardless of endpoint allowlist
    (Spec Section 5 endpoint scope rule 1). `shadow` and `active` apply
    only when the endpoint is allowlisted (rule 2).
    """
    cfg = config or PivotConfig.load()
    effective_mode = mode if mode is not None else cfg.mode
    if effective_mode not in VALID_MODES:
        logger.warning(
            "[LEXICON] is_pivot_enabled called with invalid mode=%r; treating as %r",
            effective_mode,
            DEFAULT_MODE,
        )
        effective_mode = DEFAULT_MODE
    if effective_mode == "legacy":
        return False
    return endpoint in cfg.endpoints
