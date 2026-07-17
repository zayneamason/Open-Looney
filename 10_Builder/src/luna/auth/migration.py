"""
One-shot migration: data/user/ → data/profiles/<slug>/.

Triggers: profiles.json doesn't exist + data/user/memory_matrix.lun exists.
Action:
    1. Read config/owner.yaml → derive slug + display_name (default 'admin' / 'Owner').
    2. Move data/user/* → data/profiles/<slug>/ via os.rename (atomic on same FS).
    3. Move config/owner.yaml + config/identity_bypass.json → data/profiles/<slug>/config/.
    4. Seed data/profiles.json with the new admin profile + provided password.

Idempotent: returns NoOpResult if profiles.json already exists.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from luna.core.paths import config_dir, data_dir, project_root

from .registry import ProfileRegistry, validate_slug

logger = logging.getLogger(__name__)


@dataclass
class MigrationResult:
    performed: bool                  # True if migration ran, False if no-op
    slug: Optional[str] = None       # slug of the migrated admin profile
    profile_dir: Optional[Path] = None
    moved_user_dir: bool = False
    moved_owner_yaml: bool = False
    moved_identity_bypass: bool = False
    reason: str = ""                 # Human-readable summary


def _legacy_user_dir() -> Path:
    """The pre-profile-system data/user/ directory."""
    return data_dir() / "user"


def _profile_dir(slug: str) -> Path:
    return data_dir() / "profiles" / slug


def _read_owner_yaml() -> dict:
    """Read config/owner.yaml. Returns {} if missing or malformed."""
    path = config_dir() / "owner.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
        return data.get("owner", {}) or {}
    except (yaml.YAMLError, OSError) as e:
        logger.warning("Could not read owner.yaml during migration: %s", e)
        return {}


def auto_migrate_to_profile_system(
    admin_password: str,
    *,
    slug_override: Optional[str] = None,
    display_name_override: Optional[str] = None,
) -> MigrationResult:
    """One-shot migration of data/user/ → data/profiles/<slug>/ + registry seed.

    Args:
        admin_password: Initial password for the admin profile (min 8 chars).
        slug_override: Force a specific slug (otherwise derived from owner.yaml).
        display_name_override: Force a specific display name.

    Returns:
        MigrationResult — `performed=False` if registry already exists (idempotent).
    """
    registry = ProfileRegistry()

    # Idempotent: if registry already exists, do nothing.
    if registry.exists():
        return MigrationResult(
            performed=False,
            reason=f"Registry already exists at {registry.path}",
        )

    legacy_user = _legacy_user_dir()
    if not (legacy_user / "memory_matrix.lun").exists():
        # Fresh install — no legacy data to migrate. Just bootstrap an admin.
        # Default slug is 'owner' (not 'admin') because 'admin' is reserved
        # in the registry to avoid tier/slug confusion.
        slug = slug_override or "owner"
        validate_slug(slug)
        display_name = display_name_override or "Owner"
        target = _profile_dir(slug)
        target.mkdir(parents=True, exist_ok=True)
        registry.create_profile(
            slug=slug,
            display_name=display_name,
            password=admin_password,
            tier="admin",
            metadata={"bootstrap": "fresh_install"},
        )
        logger.info("Fresh install — bootstrapped admin profile %r at %s", slug, target)
        return MigrationResult(
            performed=True,
            slug=slug,
            profile_dir=target,
            reason="Fresh install — bootstrapped admin profile (no legacy data)",
        )

    # Derive slug + display name from owner.yaml.
    owner = _read_owner_yaml()
    slug = slug_override or (owner.get("entity_id") or "owner").lower().replace(" ", "-")
    validate_slug(slug)
    display_name = display_name_override or owner.get("display_name") or "Owner"

    target = _profile_dir(slug)
    if target.exists():
        raise RuntimeError(
            f"Migration target {target} already exists but registry doesn't — "
            "ambiguous state. Inspect manually before retrying."
        )

    target.parent.mkdir(parents=True, exist_ok=True)

    # Move data/user/ → data/profiles/<slug>/ atomically.
    # os.rename is atomic on the same filesystem. If the destination's parent is
    # on a different mount, this raises OSError(EXDEV) and we'd need a copy+remove
    # fallback — for Railway with a single volume mount that won't happen.
    logger.info("Migrating %s → %s", legacy_user, target)
    os.rename(legacy_user, target)

    result = MigrationResult(
        performed=True,
        slug=slug,
        profile_dir=target,
        moved_user_dir=True,
    )

    # Move identity files into the profile's config dir.
    target_config = target / "config"
    target_config.mkdir(parents=True, exist_ok=True)

    src_owner = config_dir() / "owner.yaml"
    if src_owner.exists():
        dst_owner = target_config / "owner.yaml"
        os.rename(src_owner, dst_owner)
        result.moved_owner_yaml = True
        logger.info("Moved owner.yaml → %s", dst_owner)

    src_bypass = config_dir() / "identity_bypass.json"
    if src_bypass.exists():
        dst_bypass = target_config / "identity_bypass.json"
        os.rename(src_bypass, dst_bypass)
        result.moved_identity_bypass = True
        logger.info("Moved identity_bypass.json → %s", dst_bypass)

    # Seed the registry with the admin profile.
    registry.create_profile(
        slug=slug,
        display_name=display_name,
        password=admin_password,
        tier="admin",
        metadata={
            "bootstrap": "legacy_migration",
            "migrated_from": str(legacy_user),
        },
    )

    result.reason = (
        f"Migrated legacy data/user/ → {target} and seeded admin profile {slug!r}"
    )
    logger.info(result.reason)
    return result


def is_migrated() -> bool:
    """True if the profile system has been bootstrapped (registry exists)."""
    return ProfileRegistry().exists()


def needs_migration() -> bool:
    """True if a legacy data/user/memory_matrix.lun exists but no registry yet."""
    if is_migrated():
        return False
    return (_legacy_user_dir() / "memory_matrix.lun").exists()
