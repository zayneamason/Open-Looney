"""
Owner Identity — Centralised config for who owns this engine instance.
======================================================================

Every callsite that needs the owner's name, ID, or aliases reads from
here instead of hardcoding.  The backing file is config/owner.yaml.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

from .paths import config_dir

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OwnerConfig:
    entity_id: str = ""
    display_name: str = ""
    aliases: tuple[str, ...] = ()
    admin_contacts: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def get_owner() -> OwnerConfig:
    """Load owner config from config/owner.yaml (cached after first call)."""
    path = config_dir() / "owner.yaml"
    if not path.exists():
        logger.debug("No owner.yaml found — running as unconfigured instance")
        return OwnerConfig()
    try:
        raw = yaml.safe_load(path.read_text()) or {}
        owner = raw.get("owner", {}) or {}
        return OwnerConfig(
            entity_id=owner.get("entity_id", "") or "",
            display_name=owner.get("display_name", "") or "",
            aliases=tuple(owner.get("aliases", []) or []),
            admin_contacts=tuple(owner.get("admin_contacts", []) or []),
        )
    except Exception as e:
        logger.warning("Failed to load owner.yaml: %s", e)
        return OwnerConfig()


def owner_configured() -> bool:
    """True if an owner entity_id is set."""
    return bool(get_owner().entity_id)


def owner_names() -> set[str]:
    """Lowercase set of display_name + aliases, for identity matching."""
    o = get_owner()
    names: set[str] = set()
    if o.display_name:
        names.add(o.display_name.lower())
    for alias in o.aliases:
        if alias:
            names.add(alias.lower())
    return names


def admin_contacts_str() -> str:
    """Human-readable string joining configured admin contacts, or a generic fallback."""
    contacts = get_owner().admin_contacts
    if not contacts:
        return "your administrator"
    if len(contacts) == 1:
        return contacts[0]
    return f"{contacts[0]} or {contacts[1]}"


def owner_entity_id() -> Optional[str]:
    """Return the owner entity_id, or None if unconfigured."""
    eid = get_owner().entity_id
    return eid if eid else None
