"""Lexicon role registry loader — backed by config/lexicon.yaml.

Phase A: read-only metadata accessor. Adapters are not constructed yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from luna.core.paths import config_dir

logger = logging.getLogger("luna.lexicon.registry")

DEFAULT_CONFIG_PATH = config_dir() / "lexicon.yaml"

IMPLEMENTATION_KINDS = ("model", "heuristic", "remote", "")

ROLE_NAMES = (
    "embed",
    "rerank",
    "ner",
    "classify_safety",
    "classify_intent",
    "detect_language",
    "curate",
    "generate",
)


@dataclass(frozen=True)
class RoleEntry:
    name: str
    adapter: str
    implementation: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LexiconRegistry:
    roles: Dict[str, RoleEntry] = field(default_factory=dict)
    backends: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "LexiconRegistry":
        config_path = path or DEFAULT_CONFIG_PATH
        if not config_path.exists():
            logger.info("[LEXICON] Registry not found at %s; using empty registry", config_path)
            return cls()
        try:
            import yaml

            with open(config_path, "r") as f:
                data = yaml.safe_load(f) or {}
        except ImportError:
            logger.warning("[LEXICON] PyYAML not installed; registry empty")
            return cls()
        except Exception as exc:
            logger.error("[LEXICON] Failed to load registry from %s: %s", config_path, exc)
            return cls()

        backends = data.pop("backends", {}) or {}
        roles: Dict[str, RoleEntry] = {}
        for name in ROLE_NAMES:
            entry = data.get(name)
            if not isinstance(entry, dict):
                continue
            adapter = str(entry.get("adapter", ""))
            implementation = str(entry.get("implementation", ""))
            metadata = {k: v for k, v in entry.items() if k not in ("adapter", "implementation")}
            roles[name] = RoleEntry(
                name=name,
                adapter=adapter,
                implementation=implementation,
                metadata=metadata,
            )
        return cls(roles=roles, backends=backends)

    def get_role(self, name: str) -> Optional[RoleEntry]:
        return self.roles.get(name)


_registry: Optional[LexiconRegistry] = None


def get_registry() -> LexiconRegistry:
    global _registry
    if _registry is None:
        _registry = LexiconRegistry.load()
    return _registry
