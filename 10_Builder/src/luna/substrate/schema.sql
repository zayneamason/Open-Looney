-- Luna Engine Memory Matrix Schema
-- SQLite database with sqlite-vec for embeddings

-- ============================================================================
-- CORE TABLES
-- ============================================================================

-- Memory nodes - facts, decisions, problems, actions
CREATE TABLE IF NOT EXISTS memory_nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,  -- FACT, DECISION, PROBLEM, ACTION, CONTEXT, OPINION
    content TEXT NOT NULL,
    summary TEXT,  -- Short summary for display
    source TEXT,  -- Where this came from (conversation, file, etc.)
    confidence REAL DEFAULT 1.0,  -- 0-1 confidence score
    importance REAL DEFAULT 0.5,  -- 0-1 importance score
    access_count INTEGER DEFAULT 0,  -- Times retrieved (for lock-in)
    reinforcement_count INTEGER DEFAULT 0,  -- Times explicitly reinforced (for lock-in)
    lock_in REAL DEFAULT 0.15,  -- Lock-in coefficient (0.15-0.85)
    lock_in_state TEXT DEFAULT 'drifting',  -- drifting, fluid, settled
    last_accessed TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT,  -- JSON for extra data
    scope TEXT NOT NULL DEFAULT 'global',  -- Memory scope: 'global' or 'project:{slug}'
    -- Governance metadata (sovereign knowledge protection)
    classification TEXT DEFAULT 'public',      -- public, community, ceremonial, sacred
    custodian TEXT,                             -- who holds/authored this knowledge
    access_roles TEXT,                          -- JSON array: ["elder", "initiated_weaver"]
    consent_status TEXT DEFAULT 'granted',      -- granted, pending, revoked
    consent_date TEXT,                          -- when consent was given
    review_status TEXT DEFAULT 'current',       -- current, due, retracted
    date_shared TEXT,                           -- when the knowledge was originally shared
    -- Active/archived partitioning (see database.py::_migrate_namespace_columns()).
    -- Currently every row is 'active'; column reserved for future archive flow.
    namespace TEXT NOT NULL DEFAULT 'active'
);

-- Conversation turns - raw conversation history
-- Three-tier system: active (recent), recent (compressed), archive (Memory Matrix)
CREATE TABLE IF NOT EXISTS conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,  -- user, assistant, system
    content TEXT NOT NULL,
    tokens INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT,  -- JSON for extra data
    -- Voice v2.0 turn-type taxonomy (see src/luna/core/turn_types.py).
    -- Fresh DBs get the column from this CREATE TABLE; existing DBs pick it
    -- up via database.py::_migrate_turn_type_column().
    turn_type TEXT NOT NULL DEFAULT 'NORMAL_USER_TURN',
    -- History tier columns
    tier TEXT DEFAULT 'active',  -- 'active', 'recent', 'archive'
    compressed TEXT,  -- Compressed summary (for recent tier)
    compressed_at REAL,  -- Timestamp of compression
    archived_at REAL,  -- Timestamp of archival
    context_refs TEXT  -- JSON array of referenced context IDs
);

-- Graph edges - relationships between nodes
CREATE TABLE IF NOT EXISTS graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relationship TEXT NOT NULL,  -- DEPENDS_ON, RELATES_TO, CAUSED_BY, etc.
    strength REAL DEFAULT 1.0,  -- 0-1 edge weight
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT,
    scope TEXT NOT NULL DEFAULT 'global',  -- Edge scope: 'global' or 'project:{slug}'
    origin TEXT NOT NULL DEFAULT 'user',  -- 'system' | 'user' | 'seed'
    FOREIGN KEY (from_id) REFERENCES memory_nodes(id),
    FOREIGN KEY (to_id) REFERENCES memory_nodes(id),
    UNIQUE(from_id, to_id, relationship)
);

-- Consciousness snapshots - periodic state saves
CREATE TABLE IF NOT EXISTS consciousness_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tick_count INTEGER NOT NULL,
    attention_state TEXT,  -- JSON
    personality_state TEXT,  -- JSON
    active_topics TEXT,  -- JSON array
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sessions - track conversation sessions
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    ended_at REAL,
    app_context TEXT,
    turns_count INTEGER DEFAULT 0,
    metadata TEXT  -- JSON
);

-- ============================================================================
-- VECTOR EMBEDDINGS TABLE
-- Created by sqlite-vec extension
-- ============================================================================

-- This will be created dynamically when sqlite-vec is loaded:
-- CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0(
--     id TEXT PRIMARY KEY,
--     embedding FLOAT[1536]  -- OpenAI/Anthropic embedding dimension
-- );

-- ============================================================================
-- HISTORY SYSTEM TABLES
-- Three-tier conversation history: Active -> Recent -> Archive
-- ============================================================================

-- Compression queue for background processing
CREATE TABLE IF NOT EXISTS compression_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    queued_at REAL NOT NULL,
    processed_at REAL,
    FOREIGN KEY (turn_id) REFERENCES conversation_turns(id)
);

-- Extraction queue for archival to Memory Matrix
CREATE TABLE IF NOT EXISTS extraction_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    queued_at REAL NOT NULL,
    processed_at REAL,
    FOREIGN KEY (turn_id) REFERENCES conversation_turns(id)
);

-- History embeddings for semantic search on Recent tier
CREATE TABLE IF NOT EXISTS history_embeddings (
    turn_id INTEGER PRIMARY KEY,
    embedding BLOB NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (turn_id) REFERENCES conversation_turns(id)
);

-- ============================================================================
-- FULL-TEXT SEARCH (FTS5)
-- ============================================================================

-- FTS5 virtual table for fast text search with stemming
-- Porter stemmer: "collaborate" matches "collaborator", "collaboration"
CREATE VIRTUAL TABLE IF NOT EXISTS memory_nodes_fts USING fts5(
    content,
    summary,
    content='memory_nodes',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- Trigger: Keep FTS5 in sync on INSERT
CREATE TRIGGER IF NOT EXISTS memory_nodes_fts_insert
AFTER INSERT ON memory_nodes
BEGIN
    INSERT INTO memory_nodes_fts(rowid, content, summary)
    VALUES (NEW.rowid, NEW.content, NEW.summary);
END;

-- Trigger: Keep FTS5 in sync on UPDATE
CREATE TRIGGER IF NOT EXISTS memory_nodes_fts_update
AFTER UPDATE OF content, summary ON memory_nodes
BEGIN
    UPDATE memory_nodes_fts
    SET content = NEW.content, summary = NEW.summary
    WHERE rowid = NEW.rowid;
END;

-- Trigger: Keep FTS5 in sync on DELETE
CREATE TRIGGER IF NOT EXISTS memory_nodes_fts_delete
AFTER DELETE ON memory_nodes
BEGIN
    DELETE FROM memory_nodes_fts WHERE rowid = OLD.rowid;
END;

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_nodes_type ON memory_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_created ON memory_nodes(created_at);
CREATE INDEX IF NOT EXISTS idx_nodes_importance ON memory_nodes(importance DESC);
CREATE INDEX IF NOT EXISTS idx_nodes_accessed ON memory_nodes(last_accessed DESC);
CREATE INDEX IF NOT EXISTS idx_nodes_lock_in ON memory_nodes(lock_in DESC);
CREATE INDEX IF NOT EXISTS idx_nodes_lock_in_state ON memory_nodes(lock_in_state);
CREATE INDEX IF NOT EXISTS idx_nodes_classification ON memory_nodes(classification);
CREATE INDEX IF NOT EXISTS idx_nodes_custodian ON memory_nodes(custodian);
-- NOTE: idx_nodes_scope and idx_nodes_scope_type created in database.py migration
-- (scope column may not exist on pre-v2.1 databases until ALTER TABLE runs)

CREATE INDEX IF NOT EXISTS idx_turns_session ON conversation_turns(session_id);
CREATE INDEX IF NOT EXISTS idx_turns_created ON conversation_turns(created_at);
CREATE INDEX IF NOT EXISTS idx_turns_tier_timestamp ON conversation_turns(tier, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_turns_session_tier ON conversation_turns(session_id, tier);
CREATE INDEX IF NOT EXISTS idx_turns_turn_type ON conversation_turns(turn_type);

CREATE INDEX IF NOT EXISTS idx_compression_pending ON compression_queue(status, queued_at);
CREATE INDEX IF NOT EXISTS idx_extraction_pending ON extraction_queue(status, queued_at);

CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);

CREATE INDEX IF NOT EXISTS idx_edges_from ON graph_edges(from_id);
CREATE INDEX IF NOT EXISTS idx_edges_to ON graph_edges(to_id);
CREATE INDEX IF NOT EXISTS idx_edges_relationship ON graph_edges(relationship);
-- NOTE: idx_edges_scope created in database.py migration

CREATE INDEX IF NOT EXISTS idx_snapshots_tick ON consciousness_snapshots(tick_count);

-- ============================================================================
-- TRIGGERS
-- ============================================================================

-- Update timestamp on node modification
CREATE TRIGGER IF NOT EXISTS update_node_timestamp
AFTER UPDATE ON memory_nodes
BEGIN
    UPDATE memory_nodes SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- Increment access count and update last_accessed
CREATE TRIGGER IF NOT EXISTS track_node_access
AFTER UPDATE OF access_count ON memory_nodes
BEGIN
    UPDATE memory_nodes SET last_accessed = datetime('now') WHERE id = NEW.id;
END;

-- ============================================================================
-- ENTITY SYSTEM TABLES
-- First-class entities: people, personas, places, projects
-- ============================================================================

-- Entities: First-class objects Luna knows about
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,              -- Slug: 'user_001', 'marzipan', 'ben-franklin'
    entity_type TEXT NOT NULL,        -- 'person' | 'persona' | 'place' | 'project'
    name TEXT NOT NULL,
    aliases TEXT,                     -- JSON array: ["User", "Admin"]
    core_facts TEXT,                  -- JSON blob (~500 tokens max)
    full_profile TEXT,                -- Markdown, can be lengthy
    voice_config TEXT,                -- JSON: tone, patterns, constraints (for personas)
    current_version INTEGER DEFAULT 1,
    metadata TEXT,                    -- Flexible JSON blob
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    origin TEXT NOT NULL DEFAULT 'user',  -- 'system' | 'user' | 'seed'
    -- Active/archived partitioning (see database.py::_migrate_namespace_columns()).
    namespace TEXT NOT NULL DEFAULT 'active'
);

-- idx_entities_origin and idx_entities_namespace created by their respective
-- migration helpers in database.py for existing DBs (idempotent on fresh installs).

-- Entity relationships: Graph of connections between entities
CREATE TABLE IF NOT EXISTS entity_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_entity TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    to_entity TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relationship TEXT NOT NULL,       -- 'creator', 'friend', 'collaborator', 'embodies'
    strength REAL DEFAULT 0.5,        -- 0-1, for relevance weighting
    bidirectional INTEGER DEFAULT 0,  -- If true, relationship goes both ways
    context TEXT,                     -- "Met at Mars College 2025"
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(from_entity, to_entity, relationship)
);

-- Entity mentions: Links entities to Memory Matrix nodes
CREATE TABLE IF NOT EXISTS entity_mentions (
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL REFERENCES memory_nodes(id) ON DELETE CASCADE,
    mention_type TEXT NOT NULL,       -- 'subject', 'author', 'reference'
    confidence REAL DEFAULT 1.0,
    context_snippet TEXT,             -- Brief excerpt showing mention
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (entity_id, node_id)
);

-- Entity versions: Full history of profile changes (append-only)
CREATE TABLE IF NOT EXISTS entity_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    core_facts TEXT,
    full_profile TEXT,
    voice_config TEXT,
    change_type TEXT NOT NULL,        -- 'create' | 'update' | 'synthesize' | 'rollback'
    change_summary TEXT,              -- Human-readable: "Added Mars College location"
    changed_by TEXT NOT NULL,         -- 'scribe' | 'librarian' | 'manual'
    change_source TEXT,               -- node_id or conversation_id that triggered
    created_at TEXT DEFAULT (datetime('now')),
    valid_from TEXT DEFAULT (datetime('now')),
    valid_until TEXT,                 -- NULL = current version
    UNIQUE(entity_id, version)
);

-- Entity indexes
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_ent_relationships_from ON entity_relationships(from_entity);
CREATE INDEX IF NOT EXISTS idx_ent_relationships_to ON entity_relationships(to_entity);
CREATE INDEX IF NOT EXISTS idx_ent_relationships_type ON entity_relationships(relationship);
CREATE INDEX IF NOT EXISTS idx_mentions_entity ON entity_mentions(entity_id);
CREATE INDEX IF NOT EXISTS idx_mentions_node ON entity_mentions(node_id);
CREATE INDEX IF NOT EXISTS idx_versions_current ON entity_versions(entity_id, valid_until);
CREATE INDEX IF NOT EXISTS idx_versions_temporal ON entity_versions(entity_id, valid_from, valid_until);

-- Entity triggers
CREATE TRIGGER IF NOT EXISTS update_entity_timestamp
AFTER UPDATE ON entities
BEGIN
    UPDATE entities SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_ent_relationship_timestamp
AFTER UPDATE ON entity_relationships
BEGIN
    UPDATE entity_relationships SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- ============================================================================
-- TUNING SYSTEM TABLES
-- Automated parameter tuning with iteration tracking
-- ============================================================================

-- Tuning sessions: Track tuning runs
CREATE TABLE IF NOT EXISTS tuning_sessions (
    session_id TEXT PRIMARY KEY,
    focus TEXT NOT NULL,              -- 'memory', 'routing', 'latency', 'all'
    started_at TEXT NOT NULL,
    ended_at TEXT,
    notes TEXT,
    best_iteration INTEGER DEFAULT 0,
    best_score REAL DEFAULT 0.0,
    base_params TEXT                  -- JSON: starting parameter snapshot
);

-- Tuning iterations: Individual parameter experiments
CREATE TABLE IF NOT EXISTS tuning_iterations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    iteration_num INTEGER NOT NULL,
    params_changed TEXT NOT NULL,     -- JSON: parameters changed this iteration
    param_snapshot TEXT NOT NULL,     -- JSON: full parameter state
    eval_results TEXT NOT NULL,       -- JSON: evaluation results
    score REAL NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES tuning_sessions(session_id)
);

-- Tuning indexes
CREATE INDEX IF NOT EXISTS idx_tuning_sessions_started ON tuning_sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_tuning_iterations_session ON tuning_iterations(session_id);
CREATE INDEX IF NOT EXISTS idx_tuning_iterations_score ON tuning_iterations(score DESC);

-- ============================================================================
-- APERTURE & LIBRARY COGNITION TABLES
-- Collection-level lock-in and annotation bridge system
-- ============================================================================

-- Collection lock-in: Luna's internal state about external Aibrarian collections
-- Lives in engine DB, NOT in individual collection databases
CREATE TABLE IF NOT EXISTS collection_lock_in (
    collection_key TEXT PRIMARY KEY,
    lock_in REAL DEFAULT 0.15,
    state TEXT DEFAULT 'drifting',         -- drifting, fluid, settled
    access_count INTEGER DEFAULT 0,        -- Searches + document opens
    annotation_count INTEGER DEFAULT 0,    -- Bookmarks + notes + flags
    connected_collections INTEGER DEFAULT 0,  -- Cross-references
    entity_overlap_count INTEGER DEFAULT 0,   -- Entities shared with Matrix
    last_accessed_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Collection annotations: Bridge from collections into Memory Matrix
-- Each annotation creates a Matrix node with source=aibrarian provenance
CREATE TABLE IF NOT EXISTS collection_annotations (
    id TEXT PRIMARY KEY,
    collection_key TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    chunk_index INTEGER,
    annotation_type TEXT NOT NULL,          -- bookmark, note, flag
    content TEXT,                            -- Luna's note text
    matrix_node_id TEXT,                    -- ID of Matrix node created
    created_at TEXT DEFAULT (datetime('now'))
);

-- Collection lock-in indexes
CREATE INDEX IF NOT EXISTS idx_coll_lock_in_state ON collection_lock_in(state);
CREATE INDEX IF NOT EXISTS idx_coll_lock_in_score ON collection_lock_in(lock_in DESC);

-- Collection annotation indexes
CREATE INDEX IF NOT EXISTS idx_annotations_collection ON collection_annotations(collection_key);
CREATE INDEX IF NOT EXISTS idx_annotations_type ON collection_annotations(annotation_type);
CREATE INDEX IF NOT EXISTS idx_annotations_doc ON collection_annotations(collection_key, doc_id);

-- ============================================================================
-- NEXUS TABLES (Step 2 of Memory Matrix → Nexus migration)
-- See: ClaudeCo-Projects/Project Eclipse/NEXUS_CORTEX_ARCHITECTURE_BRIEF.md
-- Empty after Step 2; Move 2 lands the YAML→nexus_registry seed import.
-- ============================================================================

CREATE TABLE IF NOT EXISTS nexus_nodes (
    nexus_node_id TEXT PRIMARY KEY,
    collection_key TEXT NOT NULL,
    satellite_node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    promoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nexus_edges (
    src_node_id TEXT NOT NULL,
    dst_node_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    FOREIGN KEY (src_node_id) REFERENCES nexus_nodes(nexus_node_id),
    FOREIGN KEY (dst_node_id) REFERENCES nexus_nodes(nexus_node_id)
);

CREATE TABLE IF NOT EXISTS nexus_registry (
    collection_key TEXT PRIMARY KEY,
    lun_path TEXT NOT NULL,
    ingestion_pattern TEXT NOT NULL,
    lock_in REAL DEFAULT 0.15,
    access_count INTEGER DEFAULT 0,
    annotation_count INTEGER DEFAULT 0,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_nexus_nodes_collection ON nexus_nodes(collection_key);
CREATE INDEX IF NOT EXISTS idx_nexus_edges_src ON nexus_edges(src_node_id);
CREATE INDEX IF NOT EXISTS idx_nexus_edges_dst ON nexus_edges(dst_node_id);

-- ============================================================================
-- DIRECTIVE / QUEST SYSTEM
-- Intent layer: directives, skills, and automated quest execution
-- ============================================================================

CREATE TABLE IF NOT EXISTS quests (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,                    -- 'directive' | 'skill'
    status TEXT NOT NULL DEFAULT 'armed',  -- 'armed' | 'fired' | 'disabled' | 'available'
    priority TEXT DEFAULT 'medium',        -- 'low' | 'medium' | 'high'
    title TEXT NOT NULL,
    objective TEXT,
    trigger_type TEXT,                     -- 'session_start' | 'keyword' | 'entity_mention'
    trigger_config TEXT,                   -- JSON: match patterns, entity lists, etc.
    action TEXT,                           -- Action string(s)
    trust_tier TEXT DEFAULT 'confirm',     -- 'auto' | 'confirm' | 'manual'
    cooldown_minutes INTEGER,
    fire_count INTEGER DEFAULT 0,
    invocation_count INTEGER DEFAULT 0,
    last_fired_at TEXT,
    last_invoked_at TEXT,
    steps TEXT,                            -- JSON array of step strings (for skills)
    tags_json TEXT,                        -- JSON array of tags
    authored_by TEXT DEFAULT 'system',
    approved_by TEXT,
    source TEXT,                           -- 'yaml_seed' | 'user' | 'manual'
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_quests_type_status ON quests(type, status);
CREATE INDEX IF NOT EXISTS idx_quests_trigger ON quests(trigger_type);
CREATE INDEX IF NOT EXISTS idx_quests_priority ON quests(priority DESC);

CREATE TABLE IF NOT EXISTS quest_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quest_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK(target_type IN ('entity', 'node', 'cluster')),
    target_id TEXT NOT NULL,
    FOREIGN KEY (quest_id) REFERENCES quests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quest_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quest_id TEXT NOT NULL,
    content TEXT NOT NULL,
    themes TEXT DEFAULT '[]',
    lock_in_delta REAL DEFAULT 0.0,
    edges_created INTEGER DEFAULT 0,
    node_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (quest_id) REFERENCES quests(id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_quest_targets_quest ON quest_targets(quest_id);
CREATE INDEX IF NOT EXISTS idx_quest_journal_quest ON quest_journal(quest_id);

-- ============================================================================
-- GOVERNANCE PROTOCOL TABLES
-- Community-authored governance rules stored alongside the knowledge
-- ============================================================================

-- Protocols: Governance rules authored by the community
CREATE TABLE IF NOT EXISTS protocols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layer INTEGER NOT NULL,               -- 1-10 (Sovereign Knowledge Framework)
    name TEXT NOT NULL,                    -- "Sovereignty & Governance"
    description TEXT,                      -- What this layer governs
    rules TEXT,                            -- JSON: specific rules for this layer
    authored_by TEXT,                      -- Who wrote these rules
    community TEXT,                        -- Which community these apply to
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_protocols_layer ON protocols(layer);
CREATE INDEX IF NOT EXISTS idx_protocols_community ON protocols(community);

-- Roles: Community-defined roles with access levels
CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,             -- "elder", "youth", "intermediary", "guest"
    access_level INTEGER DEFAULT 0,        -- 0=public, 1=community, 2=ceremonial, 3=sacred
    description TEXT,                       -- What this role means in the community
    community TEXT,                         -- Which community defines this role
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_roles_name ON roles(name);
CREATE INDEX IF NOT EXISTS idx_roles_access ON roles(access_level);

-- ============================================================================
-- CANONICAL TASK / THREAD TABLES (Slice 1 — TaskManager persistence)
-- See: Docs/bible/Handoffs/HANDOFF_TASKMANAGER_BOARD_THREADS_AND_GUARDIAN_ARCHITECTURE.md
-- ============================================================================

-- Tasks: canonical units of work. Replaces the in-memory TaskManager and the
-- YAML bridge queue. Slice 2 migrates Thread.open_tasks into this table.
CREATE TABLE IF NOT EXISTS tasks (
    id                         TEXT PRIMARY KEY,
    title                      TEXT NOT NULL,
    description                TEXT,
    kind                       TEXT NOT NULL DEFAULT 'conversation_born_task',
    status                     TEXT NOT NULL DEFAULT 'inbox',  -- inbox, ready, in_progress, blocked, waiting, completed, failed, cancelled, archived
    priority                   INTEGER NOT NULL DEFAULT 5,      -- 1-10, lower = higher priority
    owner                      TEXT,                             -- e.g. 'companion', 'guardian', actor name
    source                     TEXT,                             -- how the task was created
    blocked_reason             TEXT,
    result_json                TEXT,
    error                      TEXT,
    metadata_json              TEXT,
    created_at                 TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                 TEXT NOT NULL DEFAULT (datetime('now')),
    started_at                 TEXT,
    completed_at               TEXT,
    due_at                     TEXT,
    mappings_backfilled_at     TEXT  -- sentinel: one-shot metadata → mapping tables migration
);
CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_kind     ON tasks(kind);

-- Task dependencies: a task becomes eligible for execution only after all
-- of its deps are in status='completed'.
CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id             TEXT NOT NULL,
    depends_on_task_id  TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on_task_id),
    FOREIGN KEY (task_id)            REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- Task runs: append-only history of attempts/executions. Opened on start,
-- closed on complete/fail. Enables retry and audit.
CREATE TABLE IF NOT EXISTS task_runs (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    run_type      TEXT NOT NULL,               -- exec, retry, diagnose, etc.
    actor         TEXT,
    started_at    TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at  TEXT,
    status        TEXT NOT NULL,               -- running, completed, failed
    result_json   TEXT,
    error         TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs(task_id);

-- Threads: structured thread record. Coexists with THREAD memory_nodes
-- until Slice 2 backfills. Slice 1 creates the table but leaves it empty.
CREATE TABLE IF NOT EXISTS threads (
    id             TEXT PRIMARY KEY,
    topic          TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',  -- active, parked, closed
    project_slug   TEXT,
    started_at     TEXT NOT NULL DEFAULT (datetime('now')),
    parked_at      TEXT,
    resumed_at     TEXT,
    closed_at      TEXT,
    resume_count   INTEGER NOT NULL DEFAULT 0,
    metadata_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);

-- Thread ↔ task linkage. Slice 2 populates this; Slice 1 creates only.
CREATE TABLE IF NOT EXISTS thread_tasks (
    thread_id     TEXT NOT NULL,
    task_id       TEXT NOT NULL,
    relationship  TEXT NOT NULL DEFAULT 'open_task',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT,
    PRIMARY KEY (thread_id, task_id),
    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id)   REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_thread_tasks_task ON thread_tasks(task_id);

-- ── Task Mapping Ontology ────────────────────────────────────────────────
-- Canonical nine-bucket joins replacing tasks.metadata_json as the
-- authoritative source for Observatory's mappings payload. See
-- Docs/bible/Handoffs for the design.

-- entity_id + entity_text use NOT NULL '' sentinels because SQLite disallows
-- expressions inside PRIMARY KEY declarations. '' on either column means
-- "absent". CHECK guarantees at least one is populated.
CREATE TABLE IF NOT EXISTS task_entities (
    task_id       TEXT NOT NULL,
    entity_id     TEXT NOT NULL DEFAULT '',
    entity_text   TEXT NOT NULL DEFAULT '',
    relationship  TEXT NOT NULL DEFAULT 'about', -- about | mentions | affects | owned_by
    confidence    REAL NOT NULL DEFAULT 1.0,
    source        TEXT,                           -- metadata_backfill | manual | extractor
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (task_id, entity_id, entity_text, relationship),
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    CHECK (entity_id <> '' OR entity_text <> '')
);
CREATE INDEX IF NOT EXISTS idx_task_entities_entity_id ON task_entities(entity_id);
CREATE INDEX IF NOT EXISTS idx_task_entities_task_id   ON task_entities(task_id);

CREATE TABLE IF NOT EXISTS task_subjects (
    task_id     TEXT NOT NULL,
    subject     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual', -- manual | derived | thread | quest | metadata_backfill
    weight      REAL NOT NULL DEFAULT 1.0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (task_id, subject),
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_task_subjects_task_id ON task_subjects(task_id);
CREATE INDEX IF NOT EXISTS idx_task_subjects_subject ON task_subjects(subject);

CREATE TABLE IF NOT EXISTS task_keywords (
    task_id     TEXT NOT NULL,
    keyword     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    weight      REAL NOT NULL DEFAULT 1.0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (task_id, keyword),
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_task_keywords_task_id ON task_keywords(task_id);
CREATE INDEX IF NOT EXISTS idx_task_keywords_keyword ON task_keywords(keyword);

CREATE TABLE IF NOT EXISTS task_files (
    task_id      TEXT NOT NULL,
    path         TEXT NOT NULL,
    relationship TEXT NOT NULL DEFAULT 'touches', -- touches | evidence | blocked_by | output
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (task_id, path, relationship),
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_task_files_task_id ON task_files(task_id);

CREATE TABLE IF NOT EXISTS task_memory_links (
    task_id         TEXT NOT NULL,
    memory_node_id  TEXT NOT NULL,
    relationship    TEXT NOT NULL DEFAULT 'related', -- evidence | decision | origin | related
    score           REAL NOT NULL DEFAULT 0.0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (task_id, memory_node_id, relationship),
    FOREIGN KEY (task_id)        REFERENCES tasks(id)         ON DELETE CASCADE,
    FOREIGN KEY (memory_node_id) REFERENCES memory_nodes(id)  ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_task_memory_task_id ON task_memory_links(task_id);
CREATE INDEX IF NOT EXISTS idx_task_memory_node_id ON task_memory_links(memory_node_id);

-- ============================================================================
-- TOPOLOGY CLUSTER SUBSTRATE (Slice 1: storage-only, thread-owned)
-- Separate namespace from memory-economy `clusters`. Attaches to canonical
-- threads.id — not memory_nodes. shape_class / lock_state intentionally
-- free-form (no CHECK) while topology ontology stabilizes.
-- ============================================================================

CREATE TABLE IF NOT EXISTS topology_clusters (
    cluster_id    TEXT PRIMARY KEY,
    label         TEXT NOT NULL,
    shape_class   TEXT,
    lock_state    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_topology_clusters_shape_class
    ON topology_clusters(shape_class);
CREATE INDEX IF NOT EXISTS idx_topology_clusters_lock_state
    ON topology_clusters(lock_state);

CREATE TABLE IF NOT EXISTS topology_cluster_threads (
    cluster_id    TEXT NOT NULL,
    thread_id     TEXT NOT NULL,
    relationship  TEXT NOT NULL DEFAULT 'member',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (cluster_id, thread_id),
    FOREIGN KEY (cluster_id) REFERENCES topology_clusters(cluster_id) ON DELETE CASCADE,
    FOREIGN KEY (thread_id)  REFERENCES threads(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_topology_cluster_threads_thread
    ON topology_cluster_threads(thread_id);

-- ============================================================================
-- AGENTIC MENU FRAMEWORK — game_states (bridges to future THREAD nodes)
-- ============================================================================

CREATE TABLE IF NOT EXISTS game_states (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    obstacle_description TEXT NOT NULL,
    primary_class INTEGER NOT NULL,
    secondary_classes TEXT DEFAULT '[]',
    tool_in_use TEXT,
    partial_findings TEXT DEFAULT '[]',
    open_questions TEXT DEFAULT '[]',
    aperture_at_cache TEXT,
    sigma_at_cache REAL,
    status TEXT DEFAULT 'active',
    created_at REAL NOT NULL,
    last_active REAL NOT NULL,
    thread_node_id TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_game_states_session
    ON game_states(session_id, status);
CREATE INDEX IF NOT EXISTS idx_game_states_last_active
    ON game_states(last_active);
