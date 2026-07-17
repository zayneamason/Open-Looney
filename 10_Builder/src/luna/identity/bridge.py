"""
Access Bridge — Dual-Tier Permission Lookup
============================================

Single async lookup that returns both Luna relational tier
and data room structural tier for a given entity.

Used by the permission filter to decide what Luna can share.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from luna.substrate.database import MemoryDatabase

logger = logging.getLogger(__name__)


@dataclass
class BridgeResult:
    """Combined tier lookup for an entity."""
    entity_id: str
    luna_tier: str                          # admin, trusted, friend, guest, unknown
    dataroom_tier: int                      # 1-5
    dataroom_categories: list[int] = field(default_factory=list)

    @property
    def is_sovereign(self) -> bool:
        return self.dataroom_tier == 1

    @property
    def can_see_all(self) -> bool:
        return self.dataroom_tier <= 2      # Sovereign and Strategist see everything

    def can_access_category(self, category: int) -> bool:
        """Check if this entity can access a specific data room category."""
        if self.dataroom_tier <= 2:
            return True                     # Tier 1-2 see everything
        return category in self.dataroom_categories


class AccessBridge:
    """
    Async interface to the access_bridge table in Luna's engine database.
    """

    def __init__(self, db: MemoryDatabase):
        self.db = db

    async def lookup(self, entity_id: str) -> Optional[BridgeResult]:
        """
        Look up both tiers for an entity. Returns None if entity
        has no bridge entry (treat as unknown/external).
        """
        row = await self.db.fetchone(
            "SELECT entity_id, luna_tier, dataroom_tier, dataroom_categories "
            "FROM access_bridge WHERE entity_id = ?",
            (entity_id,)
        )
        if not row:
            await self._log("check", entity_id, "no_bridge_entry")
            return None

        # aiosqlite returns tuples (no row_factory set)
        categories_raw = row[3] or "[]"
        try:
            categories = json.loads(categories_raw)
        except (json.JSONDecodeError, TypeError):
            categories = []

        result = BridgeResult(
            entity_id=row[0],
            luna_tier=row[1],
            dataroom_tier=row[2],
            dataroom_categories=categories,
        )
        logger.debug(
            "Bridge lookup: %s → luna_tier=%s, dr_tier=%d, categories=%s",
            entity_id, result.luna_tier, result.dataroom_tier, categories,
        )
        return result

    async def set_dataroom_tier(
        self, entity_id: str, tier: int,
        categories: list[int], set_by: str
    ):
        """Admin operation: set data room tier for an entity."""
        from datetime import datetime
        now = datetime.now().isoformat()
        await self.db.execute(
            """UPDATE access_bridge
               SET dataroom_tier = ?, dataroom_categories = ?,
                   dataroom_tier_updated_at = ?, dataroom_tier_set_by = ?
               WHERE entity_id = ?""",
            (tier, json.dumps(categories), now, set_by, entity_id)
        )
        await self._log("tier_change", entity_id,
                        f"dr_tier={tier}, categories={categories}, by={set_by}")

    async def set_luna_tier(self, entity_id: str, tier: str):
        """Update Luna's relational tier for an entity."""
        from datetime import datetime
        now = datetime.now().isoformat()
        await self.db.execute(
            """UPDATE access_bridge
               SET luna_tier = ?, luna_tier_updated_at = ?
               WHERE entity_id = ?""",
            (tier, now, entity_id)
        )
        await self._log("tier_change", entity_id, f"luna_tier={tier}")

    async def ensure_entry(self, entity_id: str, luna_tier: str = "unknown",
                           dataroom_tier: int = 5, categories: list[int] = None,
                           set_by: str = "system"):
        """Create bridge entry if it doesn't exist."""
        existing = await self.lookup(entity_id)
        if existing:
            return existing
        await self.db.execute(
            """INSERT INTO access_bridge
               (entity_id, luna_tier, dataroom_tier, dataroom_categories, dataroom_tier_set_by)
               VALUES (?, ?, ?, ?, ?)""",
            (entity_id, luna_tier, dataroom_tier,
             json.dumps(categories or []), set_by)
        )
        return await self.lookup(entity_id)

    async def _log(self, event_type: str, entity_id: str, details: str):
        await self.db.execute(
            """INSERT INTO permission_log
               (event_type, entity_id, details)
               VALUES (?, ?, ?)""",
            (event_type, entity_id, details)
        )
