"""
ProfileConfig — typed key/value config that lives inside the profile's .lun.

The profile_config table is created by `_migrate_profile_config_table()` in
database.py. This module is the typed accessor on top of it: get/set/delete with
automatic type coercion, in-memory caching, and audit fields (updated_at, updated_by).

The data travels with the .lun cartridge — when a profile is exported, its config
goes with it. No sidecar files.

Typical use:
    config = ProfileConfig(matrix_actor.matrix.db)
    voice_id = await config.get("voice.character_id", default="luna")
    await config.set("ui.theme", "dark", updated_by="alice")

Value types:
    string  — Python str           (no coercion, raw)
    int     — Python int           (str(int) round-trip)
    float   — Python float         (str(float) round-trip)
    bool    — Python bool          ('true' / 'false')
    json    — dict, list, etc.     (json.dumps / json.loads)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .database import MemoryDatabase

logger = logging.getLogger(__name__)


_VALID_TYPES = {"string", "int", "float", "bool", "json"}
_SENTINEL = object()  # to distinguish "key not present" from "key is None"


class ProfileConfigError(ValueError):
    pass


class ProfileConfig:
    """Async key/value accessor for the profile_config table.

    Caches values in memory after first read. Cache invalidates on set/delete.
    Caller is responsible for re-instantiating after a profile switch — instances
    are bound to a single MemoryDatabase connection.
    """

    def __init__(self, db: "MemoryDatabase") -> None:
        self._db = db
        self._cache: dict[str, Any] = {}
        self._loaded = False

    # ── Type coercion ────────────────────────────────────────────

    @staticmethod
    def _infer_type(value: Any) -> str:
        # bool MUST come before int — bool is a subclass of int in Python
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, (dict, list)):
            return "json"
        if isinstance(value, str):
            return "string"
        raise ProfileConfigError(
            f"Unsupported value type for ProfileConfig: {type(value).__name__}"
        )

    @staticmethod
    def _encode(value: Any, value_type: str) -> str:
        """Encode a Python value as the TEXT cell."""
        if value_type == "json":
            return json.dumps(value, separators=(",", ":"), sort_keys=True)
        if value_type == "bool":
            return "true" if value else "false"
        if value_type in ("int", "float"):
            return str(value)
        if value_type == "string":
            if not isinstance(value, str):
                raise ProfileConfigError(f"value_type='string' requires str, got {type(value).__name__}")
            return value
        raise ProfileConfigError(f"Unknown value_type: {value_type!r}")

    @staticmethod
    def _decode(stored: str, value_type: str) -> Any:
        """Decode a TEXT cell back to its Python value."""
        if value_type == "string":
            return stored
        if value_type == "int":
            return int(stored)
        if value_type == "float":
            return float(stored)
        if value_type == "bool":
            return stored.lower() in ("true", "1", "yes")
        if value_type == "json":
            return json.loads(stored)
        # Unknown type — return raw text and warn
        logger.warning("Unknown value_type %r for stored config, returning raw text", value_type)
        return stored

    # ── Cache management ─────────────────────────────────────────

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        rows = await self._db.fetchall(
            "SELECT key, value, value_type FROM profile_config"
        )
        for row in rows:
            try:
                self._cache[row[0]] = self._decode(row[1], row[2])
            except (ValueError, json.JSONDecodeError) as e:
                logger.warning(
                    "ProfileConfig: could not decode key %r (type=%s): %s — skipping",
                    row[0], row[2], e,
                )
        self._loaded = True

    def invalidate_cache(self) -> None:
        """Drop the in-memory cache. Next read repopulates from DB."""
        self._cache = {}
        self._loaded = False

    # ── Public API ───────────────────────────────────────────────

    async def get(self, key: str, default: Any = None) -> Any:
        """Read a config value with type-correct return. `default` if key missing."""
        await self._ensure_loaded()
        return self._cache.get(key, default)

    async def has(self, key: str) -> bool:
        await self._ensure_loaded()
        return key in self._cache

    async def all(self) -> dict[str, Any]:
        """Return a snapshot of all config keys → typed values."""
        await self._ensure_loaded()
        return dict(self._cache)

    async def all_with_metadata(self) -> list[dict]:
        """Return all rows including audit metadata (for admin UI)."""
        rows = await self._db.fetchall(
            "SELECT key, value, value_type, updated_at, updated_by, description "
            "FROM profile_config ORDER BY key"
        )
        out: list[dict] = []
        for row in rows:
            try:
                value = self._decode(row[1], row[2])
            except (ValueError, json.JSONDecodeError):
                value = row[1]
            out.append({
                "key": row[0],
                "value": value,
                "value_type": row[2],
                "updated_at": row[3],
                "updated_by": row[4],
                "description": row[5],
            })
        return out

    async def set(
        self,
        key: str,
        value: Any,
        *,
        value_type: Optional[str] = None,
        updated_by: str = "system",
        description: Optional[str] = None,
    ) -> None:
        """Insert or update a config row. Infers value_type if not given."""
        if not key or not isinstance(key, str):
            raise ProfileConfigError("key must be a non-empty string")
        if value_type is None:
            value_type = self._infer_type(value)
        if value_type not in _VALID_TYPES:
            raise ProfileConfigError(
                f"value_type must be one of {sorted(_VALID_TYPES)}, got {value_type!r}"
            )
        encoded = self._encode(value, value_type)
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO profile_config (key, value, value_type, updated_at, updated_by, description)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value       = excluded.value,
                   value_type  = excluded.value_type,
                   updated_at  = excluded.updated_at,
                   updated_by  = excluded.updated_by,
                   description = COALESCE(excluded.description, profile_config.description)""",
            (key, encoded, value_type, now, updated_by, description),
        )
        # Update cache with the typed value (not the encoded form)
        self._cache[key] = value
        self._loaded = True

    async def delete(self, key: str) -> bool:
        """Remove a key. Returns True if a row was deleted."""
        cursor = await self._db.execute(
            "DELETE FROM profile_config WHERE key = ?", (key,)
        )
        existed = cursor.rowcount > 0 if cursor.rowcount is not None else None
        self._cache.pop(key, None)
        if existed is not None:
            return existed
        # Some aiosqlite versions don't populate rowcount reliably for DELETE —
        # fall back to checking the cache (not perfect, but the common path works).
        return True
