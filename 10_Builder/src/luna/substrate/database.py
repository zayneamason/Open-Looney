"""
Luna Engine Memory Database

SQLite connection manager for Luna Engine's Memory Matrix.
Uses WAL mode for concurrent access and aiosqlite for async operations.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional, Sequence

import aiosqlite

from luna.core.paths import project_root, memory_matrix_path

logger = logging.getLogger(__name__)


class WrongFamilyError(Exception):
    """Local to substrate; do not import from cartridge in this phase."""


class MemoryDatabase:
    """
    Async SQLite database manager for Luna's memory substrate.

    Manages SQLite connection with WAL mode for concurrent read/write access.
    Automatically loads schema on first run and handles graceful shutdown
    with WAL checkpoint.

    Usage:
        async with MemoryDatabase() as db:
            await db.execute("INSERT INTO memory_nodes ...")
            row = await db.fetchone("SELECT * FROM memory_nodes WHERE id = ?", (id,))

    Or manually:
        db = MemoryDatabase()
        await db.connect()
        try:
            ...
        finally:
            await db.close()
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        """
        Initialize the database manager.

        Args:
            db_path: Path to the SQLite database file.
                     Defaults to ~/.luna/luna.db
        """
        if db_path is None:
            db_path = memory_matrix_path()

        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None
        self._wal_checkpoint_counter: int = 0
        # Resolve schema.sql — check multiple locations for dev vs compiled mode.
        _candidates = [
            project_root() / "data" / "schema.sql",                        # compiled binary
            project_root() / "data" / "user" / "schema.sql",              # Forge build layout
            project_root() / "src" / "luna" / "substrate" / "schema.sql",  # dev mode
            Path(__file__).parent / "schema.sql",                          # fallback
        ]
        self._schema_path = next((c for c in _candidates if c.exists()), _candidates[-1])

    @property
    def is_connected(self) -> bool:
        """Check if database connection is active."""
        return self._connection is not None

    def _maybe_rename_legacy_db(self) -> None:
        """One-shot rename of luna_engine.db → memory_matrix.lun.

        Runs before aiosqlite opens the connection. If the legacy file exists
        and the new name doesn't, atomically rename db/wal/shm siblings.
        On any partial-state ambiguity (both names present) raise loudly —
        never silently fall back to either path.
        """
        legacy_path = self.db_path.parent / "luna_engine.db"
        if not legacy_path.exists():
            return
        if self.db_path.exists():
            raise RuntimeError(
                f"Ambiguous DB state: both {legacy_path.name} and "
                f"{self.db_path.name} exist in {self.db_path.parent}. "
                f"Move/delete the legacy file before continuing."
            )
        logger.warning(
            "Renaming legacy DB %s → %s (one-shot Step-2 migration)",
            legacy_path.name, self.db_path.name,
        )
        legacy_path.rename(self.db_path)
        for suffix in ("-wal", "-shm"):
            legacy_sibling = legacy_path.with_name(legacy_path.name + suffix)
            if legacy_sibling.exists():
                new_sibling = self.db_path.with_name(self.db_path.name + suffix)
                legacy_sibling.rename(new_sibling)

    async def connect(self) -> None:
        """
        Open database connection and initialize schema.

        Creates the database directory if it doesn't exist.
        Enables WAL mode for better concurrent access.
        Loads schema from schema.sql on first run.
        """
        if self._connection is not None:
            logger.warning("Database already connected")
            return

        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # One-shot legacy rename (must run before aiosqlite opens the file)
        self._maybe_rename_legacy_db()

        logger.info(f"Connecting to database: {self.db_path}")

        # Open connection
        self._connection = await aiosqlite.connect(self.db_path)

        # Enable WAL mode for concurrent access
        await self._connection.execute("PRAGMA journal_mode=WAL")

        # Set busy timeout (5 seconds) to handle concurrent writes
        await self._connection.execute("PRAGMA busy_timeout=15000")

        # Enable foreign keys
        await self._connection.execute("PRAGMA foreign_keys=ON")

        # Set reasonable cache size (negative = KB)
        await self._connection.execute("PRAGMA cache_size=-64000")  # 64MB

        # SPEC-006: matrix family identity (LUNM = Luna Matrix). Idempotent —
        # sets pragmas on first open after migration, no-ops on every open after.
        cursor = await self._connection.execute("PRAGMA application_id")
        row = await cursor.fetchone()
        current_app_id = row[0]
        await cursor.close()

        if current_app_id == 0:
            await self._connection.execute("PRAGMA application_id = 0x4C554E4D")
            await self._connection.execute("PRAGMA user_version = 2")
            await self._connection.commit()
            logger.info(
                "Matrix application_id set to LUNM (0x4C554E4D); user_version=2 at %s",
                self.db_path,
            )
        elif current_app_id != 0x4C554E4D:
            raise WrongFamilyError(
                f"Matrix DB has unexpected application_id=0x{current_app_id:08X}, "
                f"expected 0x4C554E4D at {self.db_path}"
            )
        # else: application_id already set correctly; no-op.

        # Load schema
        await self._load_schema()

        logger.info("Database connected and schema loaded")

    async def _load_schema(self) -> None:
        """Load and execute schema.sql to create tables."""
        if not self._schema_path.exists():
            raise FileNotFoundError(f"Schema file not found: {self._schema_path}")

        schema_sql = self._schema_path.read_text()

        # Pre-schema migration: add turn_type column on existing DBs before
        # executescript runs — schema.sql declares an index on conversation_turns(turn_type),
        # which fails on an existing DB where CREATE TABLE IF NOT EXISTS is a no-op and
        # the column hasn't been added yet.
        await self._migrate_turn_type_column()

        # Execute schema (executescript equivalent for aiosqlite)
        await self._connection.executescript(schema_sql)
        await self._connection.commit()

        # Run migrations for existing databases
        await self._migrate_scope_columns()
        await self._migrate_origin_columns()
        await self._migrate_namespace_columns()
        await self._migrate_ambassador_tables()
        await self._migrate_aperture_tables()
        await self._migrate_lunascript_tables()
        await self._migrate_governance_columns()
        await self._migrate_task_tables()
        await self._migrate_topology_cluster_tables()
        await self._migrate_profile_config_table()

        logger.debug("Schema loaded successfully")

    async def _migrate_turn_type_column(self) -> None:
        """Add turn_type column to conversation_turns if missing (Voice v2.0).

        Mirrors `migrations/006_turn_type.sql` as an idempotent in-code
        migration so existing databases (where `CREATE TABLE IF NOT EXISTS`
        short-circuited) pick up the column on next boot. Steps:

          1. PRAGMA table_info(conversation_turns) — if `turn_type` is
             already present, no-op.
          2. ALTER TABLE ADD COLUMN ... NOT NULL DEFAULT 'NORMAL_USER_TURN'.
          3. Backfill existing assistant rows to NORMAL_ASSISTANT_TURN.
          4. CREATE INDEX IF NOT EXISTS idx_turns_turn_type.

        See src/luna/core/turn_types.py for enum + taxonomy.
        """
        try:
            cursor = await self._connection.execute(
                "PRAGMA table_info(conversation_turns)"
            )
            cols = [row[1] for row in await cursor.fetchall()]
            if "turn_type" not in cols:
                await self._connection.execute(
                    "ALTER TABLE conversation_turns "
                    "ADD COLUMN turn_type TEXT NOT NULL DEFAULT 'NORMAL_USER_TURN'"
                )
                await self._connection.execute(
                    "UPDATE conversation_turns "
                    "SET turn_type = 'NORMAL_ASSISTANT_TURN' WHERE role = 'assistant'"
                )
                logger.info(
                    "Migration: added 'turn_type' column to conversation_turns "
                    "and backfilled assistant rows"
                )
        except Exception as e:
            logger.debug(f"turn_type column migration skip: {e}")

        # Index creation is always safe (IF NOT EXISTS).
        try:
            await self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_turns_turn_type "
                "ON conversation_turns(turn_type)"
            )
        except Exception as e:
            logger.debug(f"turn_type index skip: {e}")

        await self._connection.commit()
        logger.debug("turn_type migration ready")

    async def _migrate_task_tables(self) -> None:
        """Create canonical task/thread tables if missing (Slice 1).

        Idempotent — uses CREATE TABLE IF NOT EXISTS. Mirrors the DDL in
        schema.sql so existing databases pick up the new tables on next
        boot without requiring a schema rebuild.
        """
        statements = [
            """CREATE TABLE IF NOT EXISTS tasks (
                id              TEXT PRIMARY KEY,
                title           TEXT NOT NULL,
                description     TEXT,
                kind            TEXT NOT NULL DEFAULT 'conversation_born_task',
                status          TEXT NOT NULL DEFAULT 'inbox',
                priority        INTEGER NOT NULL DEFAULT 5,
                owner           TEXT,
                source          TEXT,
                blocked_reason  TEXT,
                result_json     TEXT,
                error           TEXT,
                metadata_json   TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                started_at      TEXT,
                completed_at    TEXT,
                due_at          TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_kind     ON tasks(kind)",
            """CREATE TABLE IF NOT EXISTS task_dependencies (
                task_id             TEXT NOT NULL,
                depends_on_task_id  TEXT NOT NULL,
                PRIMARY KEY (task_id, depends_on_task_id),
                FOREIGN KEY (task_id)            REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (depends_on_task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS task_runs (
                id            TEXT PRIMARY KEY,
                task_id       TEXT NOT NULL,
                run_type      TEXT NOT NULL,
                actor         TEXT,
                started_at    TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at  TEXT,
                status        TEXT NOT NULL,
                result_json   TEXT,
                error         TEXT,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )""",
            "CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs(task_id)",
            """CREATE TABLE IF NOT EXISTS threads (
                id             TEXT PRIMARY KEY,
                topic          TEXT NOT NULL DEFAULT '',
                status         TEXT NOT NULL DEFAULT 'active',
                project_slug   TEXT,
                started_at     TEXT NOT NULL DEFAULT (datetime('now')),
                parked_at      TEXT,
                resumed_at     TEXT,
                closed_at      TEXT,
                resume_count   INTEGER NOT NULL DEFAULT 0,
                metadata_json  TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status)",
            """CREATE TABLE IF NOT EXISTS thread_tasks (
                thread_id     TEXT NOT NULL,
                task_id       TEXT NOT NULL,
                relationship  TEXT NOT NULL DEFAULT 'open_task',
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                resolved_at   TEXT,
                PRIMARY KEY (thread_id, task_id),
                FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE,
                FOREIGN KEY (task_id)   REFERENCES tasks(id) ON DELETE CASCADE
            )""",
            "CREATE INDEX IF NOT EXISTS idx_thread_tasks_task ON thread_tasks(task_id)",
            # ── Mapping ontology (nine-bucket canonical joins) ─────────────────
            # See Docs handoff for the design; replaces metadata_json as the
            # authoritative source for Observatory's `mappings` payload.
            # task_entities: entity_id and entity_text use NOT NULL with ''
            # sentinels because SQLite disallows expressions inside PRIMARY KEY
            # declarations. Application code treats '' as "unresolved / absent"
            # on both columns. FK on entity_id is enforced only when non-empty
            # (empty strings don't match any real entities.id), so no SET NULL
            # trigger is needed — deletes from entities leave stale empty refs
            # that the join layer gracefully renders as text-only entries.
            """CREATE TABLE IF NOT EXISTS task_entities (
                task_id       TEXT NOT NULL,
                entity_id     TEXT NOT NULL DEFAULT '',
                entity_text   TEXT NOT NULL DEFAULT '',
                relationship  TEXT NOT NULL DEFAULT 'about',
                confidence    REAL NOT NULL DEFAULT 1.0,
                source        TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (task_id, entity_id, entity_text, relationship),
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                CHECK (entity_id <> '' OR entity_text <> '')
            )""",
            "CREATE INDEX IF NOT EXISTS idx_task_entities_entity_id ON task_entities(entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_task_entities_task_id   ON task_entities(task_id)",
            """CREATE TABLE IF NOT EXISTS task_subjects (
                task_id     TEXT NOT NULL,
                subject     TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT 'manual',
                weight      REAL NOT NULL DEFAULT 1.0,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (task_id, subject),
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )""",
            "CREATE INDEX IF NOT EXISTS idx_task_subjects_task_id ON task_subjects(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_task_subjects_subject ON task_subjects(subject)",
            """CREATE TABLE IF NOT EXISTS task_keywords (
                task_id     TEXT NOT NULL,
                keyword     TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT 'manual',
                weight      REAL NOT NULL DEFAULT 1.0,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (task_id, keyword),
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )""",
            "CREATE INDEX IF NOT EXISTS idx_task_keywords_task_id ON task_keywords(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_task_keywords_keyword ON task_keywords(keyword)",
            """CREATE TABLE IF NOT EXISTS task_files (
                task_id      TEXT NOT NULL,
                path         TEXT NOT NULL,
                relationship TEXT NOT NULL DEFAULT 'touches',
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (task_id, path, relationship),
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )""",
            "CREATE INDEX IF NOT EXISTS idx_task_files_task_id ON task_files(task_id)",
            """CREATE TABLE IF NOT EXISTS task_memory_links (
                task_id         TEXT NOT NULL,
                memory_node_id  TEXT NOT NULL,
                relationship    TEXT NOT NULL DEFAULT 'related',
                score           REAL NOT NULL DEFAULT 0.0,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (task_id, memory_node_id, relationship),
                FOREIGN KEY (task_id)        REFERENCES tasks(id)        ON DELETE CASCADE,
                FOREIGN KEY (memory_node_id) REFERENCES memory_nodes(id) ON DELETE CASCADE
            )""",
            "CREATE INDEX IF NOT EXISTS idx_task_memory_task_id ON task_memory_links(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_task_memory_node_id ON task_memory_links(memory_node_id)",
        ]
        for stmt in statements:
            try:
                await self._connection.execute(stmt)
            except Exception as e:
                logger.debug(f"Task table migration skip: {e}")

        # ALTER tasks table to add mappings_backfilled_at if missing (sentinel
        # for the one-shot metadata → mapping tables backfill).
        try:
            cursor = await self._connection.execute("PRAGMA table_info(tasks)")
            cols = [row[1] for row in await cursor.fetchall()]
            if "mappings_backfilled_at" not in cols:
                await self._connection.execute(
                    "ALTER TABLE tasks ADD COLUMN mappings_backfilled_at TEXT"
                )
                logger.info("Migration: added 'mappings_backfilled_at' column to tasks")
        except Exception as e:
            logger.debug(f"tasks.mappings_backfilled_at migration skip: {e}")

        await self._connection.commit()
        logger.debug("Task tables ready")

    async def _migrate_profile_config_table(self) -> None:
        """Create profile_config table if missing (Profile cartridge config, step 3c).

        Per-profile key/value config that travels inside the .lun. Each row is one
        tunable knob (voice id, lock-in threshold, preferred LLM, theme, etc.).
        See ProfileConfig in src/luna/substrate/profile_config.py for the typed
        accessor. Idempotent — uses CREATE TABLE IF NOT EXISTS.
        """
        statements = [
            """CREATE TABLE IF NOT EXISTS profile_config (
                key          TEXT PRIMARY KEY,
                value        TEXT NOT NULL,
                value_type   TEXT NOT NULL DEFAULT 'string'
                                CHECK (value_type IN ('string','int','float','bool','json')),
                updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
                updated_by   TEXT,
                description  TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_profile_config_updated_at ON profile_config(updated_at DESC)",
        ]
        for stmt in statements:
            try:
                await self._connection.execute(stmt)
            except Exception as e:
                logger.debug(f"profile_config migration skip: {e}")

        await self._connection.commit()
        logger.debug("profile_config table ready")

    async def _migrate_topology_cluster_tables(self) -> None:
        """Create topology cluster substrate tables if missing.

        Separate namespace from memory-economy ``clusters`` (owned by
        ``ClusterManager``). Attaches to canonical ``threads.id``. Idempotent —
        uses CREATE TABLE IF NOT EXISTS so existing DBs pick up the tables on
        next boot without a schema rebuild.
        """
        statements = [
            """CREATE TABLE IF NOT EXISTS topology_clusters (
                cluster_id    TEXT PRIMARY KEY,
                label         TEXT NOT NULL,
                shape_class   TEXT,
                lock_state    TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
                metadata_json TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_topology_clusters_shape_class ON topology_clusters(shape_class)",
            "CREATE INDEX IF NOT EXISTS idx_topology_clusters_lock_state  ON topology_clusters(lock_state)",
            """CREATE TABLE IF NOT EXISTS topology_cluster_threads (
                cluster_id    TEXT NOT NULL,
                thread_id     TEXT NOT NULL,
                relationship  TEXT NOT NULL DEFAULT 'member',
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (cluster_id, thread_id),
                FOREIGN KEY (cluster_id) REFERENCES topology_clusters(cluster_id) ON DELETE CASCADE,
                FOREIGN KEY (thread_id)  REFERENCES threads(id) ON DELETE CASCADE
            )""",
            "CREATE INDEX IF NOT EXISTS idx_topology_cluster_threads_thread ON topology_cluster_threads(thread_id)",
        ]
        for stmt in statements:
            try:
                await self._connection.execute(stmt)
            except Exception as e:
                logger.debug(f"Topology cluster migration skip: {e}")

        await self._connection.commit()
        logger.debug("Topology cluster tables ready")

    async def _migrate_lunascript_tables(self) -> None:
        """Create LunaScript cognitive signature tables if missing (v2.4 migration)."""
        try:
            from luna.lunascript.schema import apply_lunascript_schema
            await apply_lunascript_schema(self)
            await self._connection.commit()
            logger.debug("LunaScript tables ready")
        except ImportError:
            logger.debug("LunaScript module not available, skipping migration")
        except Exception as e:
            logger.debug(f"LunaScript migration skip: {e}")

    async def _migrate_scope_columns(self) -> None:
        """Add scope columns to existing tables if missing (v2.1 migration)."""
        migrations = [
            ("memory_nodes", "scope", "ALTER TABLE memory_nodes ADD COLUMN scope TEXT NOT NULL DEFAULT 'global'"),
            ("graph_edges", "scope", "ALTER TABLE graph_edges ADD COLUMN scope TEXT NOT NULL DEFAULT 'global'"),
        ]

        for table, column, alter_sql in migrations:
            try:
                cursor = await self._connection.execute(f"PRAGMA table_info({table})")
                columns = await cursor.fetchall()
                col_names = [col[1] for col in columns]

                if column not in col_names:
                    await self._connection.execute(alter_sql)
                    logger.info(f"Migration: added '{column}' column to {table}")
            except Exception as e:
                # Column may already exist (race condition) — safe to ignore
                logger.debug(f"Migration skip for {table}.{column}: {e}")

        # Create scope indexes (must be after ALTER TABLE, not in schema.sql)
        scope_indexes = [
            "CREATE INDEX IF NOT EXISTS idx_nodes_scope ON memory_nodes(scope)",
            "CREATE INDEX IF NOT EXISTS idx_nodes_scope_type ON memory_nodes(scope, node_type)",
            "CREATE INDEX IF NOT EXISTS idx_edges_scope ON graph_edges(scope)",
        ]
        for idx_sql in scope_indexes:
            try:
                await self._connection.execute(idx_sql)
            except Exception as e:
                logger.debug(f"Scope index skip: {e}")

        await self._connection.commit()

    async def _migrate_governance_columns(self) -> None:
        """Add governance columns to memory_nodes + protocols/roles tables (v2.6 migration)."""
        migrations = [
            ("memory_nodes", "classification",
             "ALTER TABLE memory_nodes ADD COLUMN classification TEXT DEFAULT 'public'"),
            ("memory_nodes", "custodian",
             "ALTER TABLE memory_nodes ADD COLUMN custodian TEXT"),
            ("memory_nodes", "access_roles",
             "ALTER TABLE memory_nodes ADD COLUMN access_roles TEXT"),
            ("memory_nodes", "consent_status",
             "ALTER TABLE memory_nodes ADD COLUMN consent_status TEXT DEFAULT 'granted'"),
            ("memory_nodes", "consent_date",
             "ALTER TABLE memory_nodes ADD COLUMN consent_date TEXT"),
            ("memory_nodes", "review_status",
             "ALTER TABLE memory_nodes ADD COLUMN review_status TEXT DEFAULT 'current'"),
            ("memory_nodes", "date_shared",
             "ALTER TABLE memory_nodes ADD COLUMN date_shared TEXT"),
        ]

        for table, column, alter_sql in migrations:
            try:
                cursor = await self._connection.execute(f"PRAGMA table_info({table})")
                columns = await cursor.fetchall()
                col_names = [col[1] for col in columns]

                if column not in col_names:
                    await self._connection.execute(alter_sql)
                    logger.info(f"Migration: added '{column}' column to {table}")
            except Exception as e:
                logger.debug(f"Migration skip for {table}.{column}: {e}")

        # Governance indexes
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_nodes_classification ON memory_nodes(classification)",
            "CREATE INDEX IF NOT EXISTS idx_nodes_custodian ON memory_nodes(custodian)",
        ]:
            try:
                await self._connection.execute(idx_sql)
            except Exception as e:
                logger.debug(f"Governance index skip: {e}")

        # Protocol + role tables (idempotent)
        for table_sql in [
            """CREATE TABLE IF NOT EXISTS protocols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                layer INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                rules TEXT,
                authored_by TEXT,
                community TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                access_level INTEGER DEFAULT 0,
                description TEXT,
                community TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_protocols_layer ON protocols(layer)",
            "CREATE INDEX IF NOT EXISTS idx_protocols_community ON protocols(community)",
            "CREATE INDEX IF NOT EXISTS idx_roles_name ON roles(name)",
            "CREATE INDEX IF NOT EXISTS idx_roles_access ON roles(access_level)",
        ]:
            try:
                await self._connection.execute(table_sql)
            except Exception as e:
                logger.debug(f"Governance table skip: {e}")

        await self._connection.commit()

    async def _migrate_namespace_columns(self) -> None:
        """Add namespace columns to memory_nodes and entities if missing.

        Production DBs were ALTER'd to include
        `namespace TEXT NOT NULL DEFAULT 'active'` at some point but the
        migration never made it into schema.sql or this file. Fresh installs
        crash on `WHERE namespace = 'active'` queries in memory.py.

        Idempotent — checks PRAGMA table_info before each ALTER.
        """
        migrations = [
            ("memory_nodes", "namespace", "ALTER TABLE memory_nodes ADD COLUMN namespace TEXT NOT NULL DEFAULT 'active'"),
            ("entities",     "namespace", "ALTER TABLE entities ADD COLUMN namespace TEXT NOT NULL DEFAULT 'active'"),
        ]

        for table, column, alter_sql in migrations:
            try:
                cursor = await self._connection.execute(f"PRAGMA table_info({table})")
                columns = await cursor.fetchall()
                col_names = [col[1] for col in columns]

                if column not in col_names:
                    await self._connection.execute(alter_sql)
                    logger.info(f"Migration: added '{column}' column to {table}")
            except Exception as e:
                logger.debug(f"Migration skip for {table}.{column}: {e}")

        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_memory_nodes_namespace ON memory_nodes(namespace)",
            "CREATE INDEX IF NOT EXISTS idx_entities_namespace ON entities(namespace)",
        ]:
            try:
                await self._connection.execute(idx_sql)
            except Exception as e:
                logger.debug(f"Namespace index skip: {e}")

        await self._connection.commit()

    async def _migrate_origin_columns(self) -> None:
        """Add origin columns to entities and graph_edges if missing (v2.5 migration)."""
        migrations = [
            ("entities", "origin", "ALTER TABLE entities ADD COLUMN origin TEXT NOT NULL DEFAULT 'user'"),
            ("graph_edges", "origin", "ALTER TABLE graph_edges ADD COLUMN origin TEXT NOT NULL DEFAULT 'user'"),
        ]

        for table, column, alter_sql in migrations:
            try:
                cursor = await self._connection.execute(f"PRAGMA table_info({table})")
                columns = await cursor.fetchall()
                col_names = [col[1] for col in columns]

                if column not in col_names:
                    await self._connection.execute(alter_sql)
                    logger.info(f"Migration: added '{column}' column to {table}")
            except Exception as e:
                logger.debug(f"Migration skip for {table}.{column}: {e}")

        # Backfill entities: personas are system, seed_loader entries are seed
        try:
            await self._connection.execute(
                "UPDATE entities SET origin = 'system' WHERE entity_type = 'persona' AND origin = 'user'"
            )
            await self._connection.execute(
                "UPDATE entities SET origin = 'seed' "
                "WHERE id IN (SELECT DISTINCT entity_id FROM entity_versions WHERE changed_by = 'seed_loader') "
                "AND origin = 'user'"
            )
        except Exception as e:
            logger.debug(f"Origin backfill skip: {e}")

        # Create indexes
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_entities_origin ON entities(origin)",
            "CREATE INDEX IF NOT EXISTS idx_edges_origin ON graph_edges(origin)",
        ]:
            try:
                await self._connection.execute(idx_sql)
            except Exception as e:
                logger.debug(f"Origin index skip: {e}")

        await self._connection.commit()

    async def _migrate_ambassador_tables(self) -> None:
        """Create ambassador protocol tables if missing (v2.2 migration)."""
        migration_path = project_root() / "migrations" / "004_ambassador_protocol.sql"
        if not migration_path.exists():
            logger.debug("Ambassador migration file not found, skipping")
            return

        try:
            # Check if table already exists
            cursor = await self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ambassador_protocol'"
            )
            if await cursor.fetchone():
                logger.debug("Ambassador tables already exist, skipping migration")
                return

            migration_sql = migration_path.read_text()
            await self._connection.executescript(migration_sql)
            await self._connection.commit()
            logger.info("Migration: created ambassador_protocol and ambassador_audit_log tables")
        except Exception as e:
            logger.debug(f"Ambassador migration skip: {e}")

    async def _migrate_aperture_tables(self) -> None:
        """Create aperture & library cognition tables if missing (v2.3 migration)."""
        tables_to_check = [
            ("collection_lock_in", """
                CREATE TABLE IF NOT EXISTS collection_lock_in (
                    collection_key TEXT PRIMARY KEY,
                    lock_in REAL DEFAULT 0.15,
                    state TEXT DEFAULT 'drifting',
                    access_count INTEGER DEFAULT 0,
                    annotation_count INTEGER DEFAULT 0,
                    connected_collections INTEGER DEFAULT 0,
                    entity_overlap_count INTEGER DEFAULT 0,
                    last_accessed_at TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """),
            ("collection_annotations", """
                CREATE TABLE IF NOT EXISTS collection_annotations (
                    id TEXT PRIMARY KEY,
                    collection_key TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    chunk_index INTEGER,
                    annotation_type TEXT NOT NULL,
                    content TEXT,
                    matrix_node_id TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """),
        ]

        for table_name, create_sql in tables_to_check:
            try:
                cursor = await self._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                )
                if not await cursor.fetchone():
                    await self._connection.execute(create_sql)
                    logger.info(f"Migration: created {table_name} table")
            except Exception as e:
                logger.debug(f"Migration skip for {table_name}: {e}")

        # Create indexes
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_coll_lock_in_state ON collection_lock_in(state)",
            "CREATE INDEX IF NOT EXISTS idx_coll_lock_in_score ON collection_lock_in(lock_in DESC)",
            "CREATE INDEX IF NOT EXISTS idx_annotations_collection ON collection_annotations(collection_key)",
            "CREATE INDEX IF NOT EXISTS idx_annotations_type ON collection_annotations(annotation_type)",
            "CREATE INDEX IF NOT EXISTS idx_annotations_doc ON collection_annotations(collection_key, doc_id)",
        ]
        for idx_sql in indexes:
            try:
                await self._connection.execute(idx_sql)
            except Exception as e:
                logger.debug(f"Aperture index skip: {e}")

        await self._connection.commit()

    async def close(self) -> None:
        """
        Close database connection gracefully.

        Performs WAL checkpoint to merge WAL file back into main database,
        then closes the connection.
        """
        if self._connection is None:
            logger.warning("Database not connected")
            return

        logger.info("Closing database connection")

        try:
            # Checkpoint WAL to merge changes into main database
            await self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await self._connection.commit()
            logger.debug("WAL checkpoint completed")
        except Exception as e:
            logger.warning(f"WAL checkpoint failed: {e}")

        await self._connection.close()
        self._connection = None

        logger.info("Database connection closed")

    async def checkpoint_wal(self) -> None:
        """Periodic WAL checkpoint to prevent unbounded WAL growth.

        Uses PASSIVE mode so it never blocks writers — it only checkpoints
        pages that don't require blocking. Call this periodically from the
        engine tick loop.
        """
        if self._connection is None:
            return
        try:
            result = await self._connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
            row = await result.fetchone()
            if row:
                # row = (busy, log_pages, checkpointed_pages)
                logger.debug(f"WAL checkpoint: busy={row[0]}, log={row[1]}, checkpointed={row[2]}")
        except Exception as e:
            logger.debug(f"WAL checkpoint skipped: {e}")

    async def execute_with_retry(
        self,
        sql: str,
        params: Optional[Sequence[Any]] = None,
        max_retries: int = 3,
    ) -> aiosqlite.Cursor:
        """Execute a write statement with exponential backoff on database lock.

        Backoff schedule: 0.5s, 1s, 2s. Use for hot-path writes where
        contention with background tasks is likely (e.g. record_conversation_turn).
        """
        conn = self._ensure_connected()
        delays = [0.5, 1.0, 2.0]
        for attempt in range(max_retries + 1):
            try:
                t0 = time.monotonic()
                cursor = await conn.execute(sql, params or ())
                await conn.commit()
                elapsed = time.monotonic() - t0
                if elapsed > 1.0:
                    logger.warning(f"Slow DB write ({elapsed:.1f}s): {sql[:80]}")
                return cursor
            except sqlite3.OperationalError as e:
                if "database is locked" not in str(e) or attempt >= max_retries:
                    raise
                delay = delays[min(attempt, len(delays) - 1)]
                sql_stripped = sql.strip()
                sql_op = sql_stripped.split()[0].upper() if sql_stripped else "UNKNOWN"
                logger.warning(
                    f"[DB-CONTENTION] op={sql_op} "
                    f"retry={attempt + 1}/{max_retries} "
                    f"reason=database_is_locked"
                )
                await asyncio.sleep(delay)
        raise RuntimeError("execute_with_retry: unreachable")

    def _ensure_connected(self) -> aiosqlite.Connection:
        """Ensure we have an active connection."""
        if self._connection is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._connection

    async def execute(
        self,
        sql: str,
        params: Optional[Sequence[Any]] = None
    ) -> aiosqlite.Cursor:
        """
        Execute a single SQL statement.

        Args:
            sql: SQL statement to execute
            params: Optional parameters for the statement

        Returns:
            The cursor from the execution
        """
        conn = self._ensure_connected()
        cursor = await conn.execute(sql, params or ())
        await conn.commit()
        return cursor

    async def executemany(
        self,
        sql: str,
        params_list: Sequence[Sequence[Any]]
    ) -> aiosqlite.Cursor:
        """
        Execute a SQL statement with multiple parameter sets.

        Useful for batch inserts.

        Args:
            sql: SQL statement to execute
            params_list: List of parameter tuples

        Returns:
            The cursor from the execution
        """
        conn = self._ensure_connected()
        cursor = await conn.executemany(sql, params_list)
        await conn.commit()
        return cursor

    async def fetchone(
        self,
        sql: str,
        params: Optional[Sequence[Any]] = None
    ) -> Optional[aiosqlite.Row]:
        """
        Execute query and fetch a single row.

        Args:
            sql: SQL query to execute
            params: Optional parameters for the query

        Returns:
            The first row of results, or None if no results
        """
        conn = self._ensure_connected()
        async with conn.execute(sql, params or ()) as cursor:
            return await cursor.fetchone()

    async def fetchall(
        self,
        sql: str,
        params: Optional[Sequence[Any]] = None
    ) -> list[aiosqlite.Row]:
        """
        Execute query and fetch all rows.

        Args:
            sql: SQL query to execute
            params: Optional parameters for the query

        Returns:
            List of all result rows
        """
        conn = self._ensure_connected()
        async with conn.execute(sql, params or ()) as cursor:
            return await cursor.fetchall()

    async def __aenter__(self) -> MemoryDatabase:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()
