"""
Lightweight readers for voice_config.yaml feature flags.

Engine and VoiceBackend share this so the two components cannot disagree
on flag state (a cause of nasty regressions). Values are cached per
process; tests can override via environment variables or by resetting the
cache.

Env overrides (take precedence over YAML):
  LUNA_INTERRUPT_CLASSIFIER_ENABLED=1   → Step 3 classifier branch
  LUNA_RESUMPTION_ENABLED=1             → Step 4 resumption dispatch
  LUNA_CONSOLIDATOR_ENABLED=1           → Step 5 consolidator fan-out
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


_TRUTHY = {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def _load_voice_system_block() -> dict:
    """Load `voice_system:` block from voice_config.yaml once per process."""
    try:
        import yaml  # type: ignore

        cfg_path = Path(__file__).resolve().parent / "data" / "voice_config.yaml"
        with cfg_path.open() as f:
            doc = yaml.safe_load(f) or {}
        block = doc.get("voice_system", {})
        return block if isinstance(block, dict) else {}
    except Exception:
        return {}


def reset_cache() -> None:
    """Clear the cached YAML read. Called by tests that mutate the file."""
    _load_voice_system_block.cache_clear()


def _env_override(var: str) -> bool | None:
    """Return True/False if env var set, else None."""
    raw = os.environ.get(var)
    if raw is None:
        return None
    return raw.strip().lower() in _TRUTHY


def interrupt_classifier_enabled() -> bool:
    """Voice Step 3 feature gate."""
    override = _env_override("LUNA_INTERRUPT_CLASSIFIER_ENABLED")
    if override is not None:
        return override
    return bool(_load_voice_system_block().get("interrupt_classifier_enabled", False))


def resumption_enabled() -> bool:
    """Voice Step 4 feature gate."""
    override = _env_override("LUNA_RESUMPTION_ENABLED")
    if override is not None:
        return override
    return bool(_load_voice_system_block().get("resumption_enabled", False))


def consolidator_enabled() -> bool:
    """Voice Step 5 feature gate."""
    override = _env_override("LUNA_CONSOLIDATOR_ENABLED")
    if override is not None:
        return override
    return bool(_load_voice_system_block().get("consolidator_enabled", False))
