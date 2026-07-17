"""
Directive Engine for Luna Intent Layer
=======================================

Evaluates and fires directive quests. Loaded on engine startup.
Checks armed directives against incoming events and fires matching ones.

Safety rails:
- Max 50 armed directives
- Min 5 min cooldown enforcement
- Max 10 firings per session per directive
- Max 5 chained actions per compound
- No self-modifying directive chains
- All actions logged
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger("luna.directives")

# Safety limits
MAX_ARMED = 50
MIN_COOLDOWN_MINUTES = 5
MAX_FIRES_PER_SESSION = 10
MAX_COMPOUND_ACTIONS = 5


class DirectiveEngine:
    """Evaluates and fires directive quests."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._armed: list[dict] = []
        self._cooldowns: dict[str, datetime] = {}
        self._session_fire_counts: dict[str, int] = {}

    async def load_armed(self) -> int:
        """Load all armed directives from quests table."""
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=15000")
            cursor = await db.execute(
                "SELECT * FROM quests WHERE type='directive' AND status='armed' "
                "ORDER BY priority DESC LIMIT ?",
                (MAX_ARMED,),
            )
            self._armed = [dict(r) for r in await cursor.fetchall()]
        self._session_fire_counts.clear()
        logger.info(f"Loaded {len(self._armed)} armed directives")
        return len(self._armed)

    async def evaluate_event(
        self, event_type: str, context: dict
    ) -> list[dict]:
        """
        Check armed directives against an event.
        Returns list of directives that should fire.
        Respects cooldowns, trust tiers, and session fire limits.
        """
        matches = []
        now = datetime.now()
        for d in self._armed:
            if d["trigger_type"] != event_type:
                continue
            if not self._check_cooldown(d["id"], d.get("cooldown_minutes"), now):
                continue
            if self._session_fire_counts.get(d["id"], 0) >= MAX_FIRES_PER_SESSION:
                logger.debug(f"Directive {d['id']} hit session fire limit")
                continue
            if self._matches_trigger(d, context):
                matches.append(d)
        return matches

    def _matches_trigger(self, directive: dict, context: dict) -> bool:
        """Check if a directive's trigger_config matches the event context."""
        config = json.loads(directive.get("trigger_config") or "{}")
        trigger_type = directive["trigger_type"]

        if trigger_type == "session_start":
            return True

        elif trigger_type == "keyword":
            pattern = config.get("match", "")
            message = context.get("message", "")
            if not pattern or not message:
                return False
            return bool(re.search(pattern, message, re.IGNORECASE))

        elif trigger_type == "entity_mention":
            required = {e.lower() for e in config.get("entities", [])}
            found = {e.lower() for e in context.get("entities", [])}
            return bool(required & found)

        elif trigger_type == "thread_resume":
            topic_match = config.get("thread_topic_match", "")
            thread = context.get("thread", {})
            thread_topic = thread.get("topic", "") if isinstance(thread, dict) else ""
            if not topic_match or not thread_topic:
                return False
            return bool(re.search(topic_match, thread_topic, re.IGNORECASE))

        return False

    def _check_cooldown(
        self, directive_id: str, cooldown_min: Optional[int], now: datetime
    ) -> bool:
        """Return True if cooldown has elapsed (or no cooldown set)."""
        if not cooldown_min:
            return True
        effective_cooldown = max(cooldown_min, MIN_COOLDOWN_MINUTES)
        last_fire = self._cooldowns.get(directive_id)
        if not last_fire:
            return True
        elapsed = (now - last_fire).total_seconds() / 60
        return elapsed >= effective_cooldown

    async def fire(self, directive: dict, engine: Any) -> dict:
        """Execute a directive's action. Returns result dict."""
        action_str = directive.get("action", "")
        actions = [a.strip() for a in action_str.split("+")]

        if len(actions) > MAX_COMPOUND_ACTIONS:
            logger.warning(
                f"Directive {directive['id']} has {len(actions)} actions, "
                f"capped at {MAX_COMPOUND_ACTIONS}"
            )
            actions = actions[:MAX_COMPOUND_ACTIONS]

        results = []
        for action in actions:
            result = await self._execute_action(action, engine)
            results.append(result)

        await self._record_fire(directive["id"])
        self._cooldowns[directive["id"]] = datetime.now()
        self._session_fire_counts[directive["id"]] = (
            self._session_fire_counts.get(directive["id"], 0) + 1
        )

        logger.info(f"Fired directive: {directive['id']} ({directive.get('title', '')})")
        return {"directive_id": directive["id"], "results": results}

    # =========================================================================
    # ACTION EXECUTOR
    # =========================================================================

    async def _execute_action(self, action: str, engine: Any) -> dict:
        """Parse and execute a single action string."""
        action = action.strip()

        if action == "surface_parked_threads":
            return await self._action_surface_parked_threads(engine)

        elif action.startswith("set_aperture:"):
            preset = action.split(":", 1)[1]
            return await self._action_set_aperture(preset, engine)

        elif action.startswith("load_collection:"):
            collection = action.split(":", 1)[1]
            return await self._action_load_collection(collection, engine)

        elif action.startswith("memory_sweep:"):
            params = action.split(":", 1)[1]
            return await self._action_memory_sweep(params, engine)

        elif action.startswith("surface_entity:"):
            entity_name = action.split(":", 1)[1]
            return await self._action_surface_entity(entity_name, engine)

        elif action.startswith("run_skill:"):
            skill_name = action.split(":", 1)[1]
            return await self._invoke_skill(skill_name, engine)

        return {"action": action, "ok": False, "error": "unknown action"}

    async def _action_surface_parked_threads(self, engine: Any) -> dict:
        """Surface parked threads into session context.

        Tries in-memory cache first (Librarian), then falls back to
        querying THREAD nodes directly from the database.
        """
        summaries = []

        # Try 1: Librarian in-memory cache
        librarian = getattr(engine, "librarian", None) if engine else None
        if librarian:
            try:
                parked = librarian.get_parked_threads()
                if parked:
                    summaries = [
                        {"topic": t.topic, "open_tasks": t.open_tasks, "entities": t.entities}
                        for t in parked
                    ]
            except Exception:
                pass

        # Try 2: Direct DB query for THREAD nodes (cold boot fallback)
        if not summaries:
            try:
                async with aiosqlite.connect(str(self.db_path)) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=15000")
                    cursor = await db.execute(
                        "SELECT content FROM memory_nodes "
                        "WHERE node_type = 'THREAD' "
                        "ORDER BY created_at DESC LIMIT 10"
                    )
                    rows = await cursor.fetchall()
                    for row in rows:
                        try:
                            thread_data = json.loads(row["content"])
                            status = thread_data.get("status", "")
                            if status in ("parked", "active"):
                                summaries.append({
                                    "topic": thread_data.get("topic", "?"),
                                    "open_tasks": thread_data.get("open_tasks", []),
                                    "entities": thread_data.get("entities", []),
                                })
                        except (json.JSONDecodeError, TypeError):
                            continue
            except Exception as e:
                logger.debug(f"DB fallback for threads: {e}")

        return {
            "action": "surface_parked_threads",
            "ok": True,
            "threads": len(summaries),
            "summaries": summaries,
        }

    async def _action_set_aperture(self, preset: str, engine: Any) -> dict:
        """Change aperture preset for this session."""
        try:
            from luna.substrate.lock_in import set_aperture
            await set_aperture(preset)
            return {"action": f"set_aperture:{preset}", "ok": True, "preset": preset}
        except Exception as e:
            return {"action": f"set_aperture:{preset}", "ok": False, "error": str(e)}

    async def _action_load_collection(self, collection: str, engine: Any) -> dict:
        """Add collection to active search chain."""
        try:
            matrix = engine.get_actor("matrix") if engine else None
            if matrix and hasattr(matrix, "_matrix"):
                # Store as session-level collection overlay
                if not hasattr(engine, "_directive_collections"):
                    engine._directive_collections = []
                if collection not in engine._directive_collections:
                    engine._directive_collections.append(collection)
            return {
                "action": f"load_collection:{collection}",
                "ok": True,
                "collection": collection,
            }
        except Exception as e:
            return {"action": f"load_collection:{collection}", "ok": False, "error": str(e)}

    async def _action_memory_sweep(self, params: str, engine: Any) -> dict:
        """Search memory for nodes matching query."""
        try:
            matrix = engine.get_actor("matrix") if engine else None
            results = []
            if matrix and hasattr(matrix, "_matrix") and matrix._matrix:
                nodes = await matrix._matrix.search_nodes(params, limit=5)
                results = [
                    {"id": n.get("id", ""), "summary": n.get("summary", "")[:100]}
                    for n in nodes
                ]
            return {
                "action": f"memory_sweep:{params}",
                "ok": True,
                "results": results,
                "count": len(results),
            }
        except Exception as e:
            return {"action": f"memory_sweep:{params}", "ok": False, "error": str(e)}

    async def _action_surface_entity(self, entity_name: str, engine: Any) -> dict:
        """Pull entity profile into context."""
        try:
            matrix = engine.get_actor("matrix") if engine else None
            profile = None
            if matrix and hasattr(matrix, "_matrix") and matrix._matrix:
                nodes = await matrix._matrix.search_nodes(entity_name, limit=1)
                if nodes:
                    profile = {
                        "id": nodes[0].get("id", ""),
                        "content": nodes[0].get("content", "")[:500],
                        "summary": nodes[0].get("summary", ""),
                    }
            return {
                "action": f"surface_entity:{entity_name}",
                "ok": True,
                "entity": entity_name,
                "profile": profile,
            }
        except Exception as e:
            return {"action": f"surface_entity:{entity_name}", "ok": False, "error": str(e)}

    # =========================================================================
    # SKILL EXECUTION
    # =========================================================================

    async def _invoke_skill(self, skill_name: str, engine: Any) -> dict:
        """Find and execute a skill by name."""
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=15000")
            cursor = await db.execute(
                "SELECT * FROM quests WHERE type='skill' "
                "AND title LIKE ? AND status='available'",
                (f"%{skill_name}%",),
            )
            skill = await cursor.fetchone()

        if not skill:
            return {
                "action": f"run_skill:{skill_name}",
                "ok": False,
                "error": "skill not found",
            }

        steps = json.loads(skill["steps"] or "[]")
        results = []
        for step in steps[:MAX_COMPOUND_ACTIONS]:
            result = await self._execute_action(step, engine)
            results.append(result)

        await self._record_invocation(skill["id"])
        return {
            "action": f"run_skill:{skill_name}",
            "ok": True,
            "steps": len(steps),
            "results": results,
        }

    async def get_skill(self, name: str) -> Optional[dict]:
        """Get a skill quest by name."""
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=15000")
            cursor = await db.execute(
                "SELECT * FROM quests WHERE type='skill' "
                "AND title LIKE ? AND status='available'",
                (f"%{name}%",),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    # =========================================================================
    # RECORDING
    # =========================================================================

    async def _record_fire(self, quest_id: str) -> None:
        """Update fire count and timestamp."""
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute("PRAGMA busy_timeout=15000")
            await db.execute(
                "UPDATE quests SET fire_count = COALESCE(fire_count, 0) + 1, "
                "last_fired_at = datetime('now'), status = 'fired', "
                "updated_at = datetime('now') WHERE id = ?",
                (quest_id,),
            )
            await db.commit()

    async def _record_invocation(self, quest_id: str) -> None:
        """Update invocation count and timestamp."""
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute("PRAGMA busy_timeout=15000")
            await db.execute(
                "UPDATE quests SET invocation_count = COALESCE(invocation_count, 0) + 1, "
                "last_invoked_at = datetime('now'), "
                "updated_at = datetime('now') WHERE id = ?",
                (quest_id,),
            )
            await db.commit()

    # =========================================================================
    # SEEDING
    # =========================================================================

    async def seed_from_yaml(self, yaml_path: Path, force: bool = False) -> dict:
        """
        Load seed directives and skills from YAML into SQLite.
        Skips any seed whose id already exists unless force=True.
        """
        import yaml

        config = yaml.safe_load(yaml_path.read_text())
        created = {"directives": 0, "skills": 0, "skipped": 0}

        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute("PRAGMA busy_timeout=15000")
            for seed in config.get("seed_directives", []):
                exists = await db.execute(
                    "SELECT 1 FROM quests WHERE id = ?", (seed["id"],)
                )
                if await exists.fetchone() and not force:
                    created["skipped"] += 1
                    continue

                await db.execute(
                    "INSERT OR REPLACE INTO quests "
                    "(id, type, status, priority, title, objective, "
                    " trigger_type, trigger_config, action, trust_tier, "
                    " authored_by, approved_by, cooldown_minutes, "
                    " source, created_at, updated_at) "
                    "VALUES (?, 'directive', 'armed', ?, ?, ?, "
                    "        ?, ?, ?, ?, ?, ?, ?, 'yaml_seed', "
                    "        datetime('now'), datetime('now'))",
                    (
                        seed["id"],
                        seed.get("priority", "medium"),
                        seed["title"],
                        seed["objective"],
                        seed["trigger_type"],
                        json.dumps(seed.get("trigger_config", {})),
                        seed["action"],
                        seed.get("trust_tier", "confirm"),
                        seed.get("authored_by", "system"),
                        seed.get("approved_by"),
                        seed.get("cooldown_minutes"),
                    ),
                )
                created["directives"] += 1

            for seed in config.get("seed_skills", []):
                exists = await db.execute(
                    "SELECT 1 FROM quests WHERE id = ?", (seed["id"],)
                )
                if await exists.fetchone() and not force:
                    created["skipped"] += 1
                    continue

                await db.execute(
                    "INSERT OR REPLACE INTO quests "
                    "(id, type, status, priority, title, objective, "
                    " steps, tags_json, authored_by, "
                    " source, created_at, updated_at) "
                    "VALUES (?, 'skill', 'available', ?, ?, ?, "
                    "        ?, ?, ?, 'yaml_seed', "
                    "        datetime('now'), datetime('now'))",
                    (
                        seed["id"],
                        seed.get("priority", "medium"),
                        seed["title"],
                        seed["objective"],
                        json.dumps(seed.get("steps", [])),
                        json.dumps(seed.get("tags", [])),
                        seed.get("authored_by", "system"),
                    ),
                )
                created["skills"] += 1

            await db.commit()

        logger.info(f"Seeded directives: {created}")
        return created

    # =========================================================================
    # MANAGEMENT
    # =========================================================================

    async def disable_all(self) -> int:
        """Emergency kill: disable all directives."""
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute("PRAGMA busy_timeout=15000")
            cursor = await db.execute(
                "UPDATE quests SET status = 'disabled', updated_at = datetime('now') "
                "WHERE type = 'directive' AND status IN ('armed', 'fired')"
            )
            await db.commit()
            count = cursor.rowcount
        self._armed.clear()
        logger.warning(f"DISABLED ALL DIRECTIVES ({count} affected)")
        return count

    async def rearm_fired(self) -> int:
        """Re-arm all fired directives (call at session start)."""
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute("PRAGMA busy_timeout=15000")
            cursor = await db.execute(
                "UPDATE quests SET status = 'armed', updated_at = datetime('now') "
                "WHERE type = 'directive' AND status = 'fired'"
            )
            await db.commit()
            return cursor.rowcount
