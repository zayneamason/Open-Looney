"""
ProfileRegistry — top-level registry of profiles, mapping slug → metadata + password hash.

The registry lives at data/profiles.json (profile-invariant — read BEFORE any profile
is selected). Stdlib-only password hashing via PBKDF2-SHA256, 200k iterations
(OWASP-recommended floor as of 2024). Constant-time comparison via hmac.compare_digest.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from luna.core.paths import data_dir

logger = logging.getLogger(__name__)


# ── Errors ────────────────────────────────────────────────────────────────

class ProfileNotFoundError(KeyError):
    pass


class ProfileAlreadyExistsError(ValueError):
    pass


class InvalidSlugError(ValueError):
    pass


class InvalidPasswordError(ValueError):
    pass


# ── Slug validation ──────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_RESERVED_SLUGS = {"system", "user", "admin", "test", "default", "profiles"}


def validate_slug(slug: str) -> None:
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise InvalidSlugError(
            f"Invalid slug {slug!r}. Must be lowercase ASCII alphanumeric + hyphen, "
            "1-32 chars, starting with alphanumeric."
        )
    if slug in _RESERVED_SLUGS:
        raise InvalidSlugError(f"Slug {slug!r} is reserved.")


# ── Password hashing (PBKDF2-SHA256) ────────────────────────────────────

_PBKDF2_ITERATIONS = 200_000
_PBKDF2_SALT_BYTES = 16
_MIN_PASSWORD_LENGTH = 8


def hash_password(password: str, *, iterations: int = _PBKDF2_ITERATIONS) -> str:
    """Hash a password using PBKDF2-SHA256. Returns Django-style format string.

    Format: pbkdf2_sha256$<iterations>$<salt_hex>$<digest_hex>
    """
    if not isinstance(password, str) or len(password) < _MIN_PASSWORD_LENGTH:
        raise InvalidPasswordError(
            f"Password must be a string of at least {_MIN_PASSWORD_LENGTH} characters."
        )
    salt = secrets.token_bytes(_PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, hash_str: str) -> bool:
    """Constant-time verify a password against a stored hash. False on any parse error."""
    if not isinstance(password, str) or not isinstance(hash_str, str):
        return False
    try:
        algo, iters_str, salt_hex, expected_hex = hash_str.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iters_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, KeyError):
        return False


# ── Registry path ────────────────────────────────────────────────────────

def profile_registry_path() -> Path:
    """Path to the top-level profile registry. Profile-invariant."""
    return data_dir() / "profiles.json"


# ── Registry data classes ────────────────────────────────────────────────

@dataclass
class ProfileRecord:
    slug: str
    display_name: str
    tier: str  # "admin" | "tester"
    password_hash: str
    created_at: str
    last_login_at: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_public_dict(self) -> dict:
        """Serialize without the password hash (for API responses)."""
        return {
            "slug": self.slug,
            "display_name": self.display_name,
            "tier": self.tier,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
        }


# ── Registry ─────────────────────────────────────────────────────────────

class ProfileRegistry:
    """Reads/writes data/profiles.json. Concurrent-safe via fcntl file locks."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or profile_registry_path()

    # ── I/O ────────────────────────────────────────────────────────

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": self.SCHEMA_VERSION, "profiles": {}}
        try:
            with open(self.path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except (json.JSONDecodeError, OSError) as e:
            raise RuntimeError(f"Could not read profile registry at {self.path}: {e}")
        if not isinstance(data, dict) or data.get("version") != self.SCHEMA_VERSION:
            raise RuntimeError(
                f"Profile registry schema mismatch at {self.path}: "
                f"expected version {self.SCHEMA_VERSION}, got {data.get('version')!r}"
            )
        if "profiles" not in data or not isinstance(data["profiles"], dict):
            raise RuntimeError(f"Profile registry malformed at {self.path}: missing profiles dict")
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically via temp + rename, holding exclusive lock on the final file
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # ── Public API ─────────────────────────────────────────────────

    def exists(self) -> bool:
        return self.path.exists()

    def list_profiles(self) -> list[ProfileRecord]:
        data = self._read()
        return [self._row_to_record(slug, row) for slug, row in data["profiles"].items()]

    def get_profile(self, slug: str) -> ProfileRecord:
        data = self._read()
        row = data["profiles"].get(slug)
        if row is None:
            raise ProfileNotFoundError(f"No profile with slug {slug!r}")
        return self._row_to_record(slug, row)

    def has_profile(self, slug: str) -> bool:
        try:
            self.get_profile(slug)
            return True
        except ProfileNotFoundError:
            return False

    def verify_password(self, slug: str, password: str) -> bool:
        """Return True if password matches the stored hash for this slug.

        Returns False (rather than raising) for unknown slugs, to prevent
        username enumeration via timing.
        """
        try:
            record = self.get_profile(slug)
        except ProfileNotFoundError:
            # Run a dummy verify to keep timing constant
            verify_password(password, "pbkdf2_sha256$1$00$00")
            return False
        return verify_password(password, record.password_hash)

    def create_profile(
        self,
        slug: str,
        display_name: str,
        password: str,
        tier: str = "tester",
        metadata: Optional[dict] = None,
    ) -> ProfileRecord:
        validate_slug(slug)
        if tier not in ("admin", "tester"):
            raise ValueError(f"tier must be 'admin' or 'tester', got {tier!r}")
        if not display_name or not isinstance(display_name, str):
            raise ValueError("display_name must be a non-empty string")

        data = self._read()
        if slug in data["profiles"]:
            raise ProfileAlreadyExistsError(f"Profile {slug!r} already exists")

        record = ProfileRecord(
            slug=slug,
            display_name=display_name,
            tier=tier,
            password_hash=hash_password(password),
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata=metadata or {},
        )
        data["profiles"][slug] = self._record_to_row(record)
        self._write(data)
        logger.info("Profile created: slug=%s tier=%s", slug, tier)
        return record

    def set_password(self, slug: str, password: str) -> None:
        data = self._read()
        if slug not in data["profiles"]:
            raise ProfileNotFoundError(f"No profile with slug {slug!r}")
        data["profiles"][slug]["password_hash"] = hash_password(password)
        self._write(data)
        logger.info("Password updated for profile %s", slug)

    def touch_last_login(self, slug: str) -> None:
        """Stamp last_login_at — call after successful login."""
        data = self._read()
        if slug not in data["profiles"]:
            raise ProfileNotFoundError(f"No profile with slug {slug!r}")
        data["profiles"][slug]["last_login_at"] = datetime.now(timezone.utc).isoformat()
        self._write(data)

    def delete_profile(self, slug: str) -> None:
        """Remove a profile from the registry. Does NOT touch the profile's data dir.

        Caller is responsible for archiving / removing data/profiles/<slug>/ if needed.
        """
        data = self._read()
        if slug not in data["profiles"]:
            raise ProfileNotFoundError(f"No profile with slug {slug!r}")
        del data["profiles"][slug]
        self._write(data)
        logger.info("Profile %s removed from registry (data dir untouched)", slug)

    # ── Internal serialization ────────────────────────────────────

    @staticmethod
    def _row_to_record(slug: str, row: dict) -> ProfileRecord:
        return ProfileRecord(
            slug=slug,
            display_name=row.get("display_name", ""),
            tier=row.get("tier", "tester"),
            password_hash=row.get("password_hash", ""),
            created_at=row.get("created_at", ""),
            last_login_at=row.get("last_login_at"),
            metadata=row.get("metadata", {}) or {},
        )

    @staticmethod
    def _record_to_row(record: ProfileRecord) -> dict:
        return {
            "display_name": record.display_name,
            "tier": record.tier,
            "password_hash": record.password_hash,
            "created_at": record.created_at,
            "last_login_at": record.last_login_at,
            "metadata": record.metadata,
        }
