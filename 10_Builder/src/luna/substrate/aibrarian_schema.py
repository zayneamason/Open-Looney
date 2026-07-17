"""
AiBrarian Engine SQL Schemas
=============================

Schema definitions for document collections managed by the AiBrarian Engine.
Two schema types: "standard" and "investigation" (standard + entity graph).

Each collection is a standalone SQLite database with these tables.
"""

# ---------------------------------------------------------------------------
# Standard Schema — documents + chunks + FTS5 + extractions + entities
# ---------------------------------------------------------------------------

STANDARD_SCHEMA = """\
-- Documents: source files ingested into the collection
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    title TEXT,
    full_text TEXT,
    word_count INTEGER,
    source_path TEXT,
    source_type TEXT DEFAULT 'local',
    doc_type TEXT,
    category TEXT,
    status TEXT DEFAULT 'draft',
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Chunks: overlapping segments for search + embeddings
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    word_count INTEGER,
    start_char INTEGER,
    end_char INTEGER,
    section_label TEXT,
    section_level INTEGER,
    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
);

-- Extracted knowledge (populated by Scribe)
CREATE TABLE IF NOT EXISTS extractions (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    chunk_index INTEGER,
    node_type TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
);

-- Entity mentions
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_value TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
);

-- FTS5 on chunks for keyword search
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_text,
    section_label,
    content='chunks',
    content_rowid='rowid'
);

-- FTS5 sync triggers
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, chunk_text, section_label)
    VALUES (new.rowid, new.chunk_text, COALESCE(new.section_label, ''));
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text, section_label)
    VALUES ('delete', old.rowid, old.chunk_text, COALESCE(old.section_label, ''));
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text, section_label)
    VALUES ('delete', old.rowid, old.chunk_text, COALESCE(old.section_label, ''));
    INSERT INTO chunks_fts(rowid, chunk_text, section_label)
    VALUES (new.rowid, new.chunk_text, COALESCE(new.section_label, ''));
END;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_extractions_doc ON extractions(doc_id);
CREATE INDEX IF NOT EXISTS idx_extractions_type ON extractions(node_type);
CREATE INDEX IF NOT EXISTS idx_entities_doc ON entities(doc_id);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category);

-- FTS5 on extractions for searching summaries + claims
CREATE VIRTUAL TABLE IF NOT EXISTS extractions_fts USING fts5(
    content,
    content='extractions',
    content_rowid='rowid'
);

-- FTS5 sync triggers for extractions
CREATE TRIGGER IF NOT EXISTS extractions_ai AFTER INSERT ON extractions BEGIN
    INSERT INTO extractions_fts(rowid, content)
    VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS extractions_ad AFTER DELETE ON extractions BEGIN
    INSERT INTO extractions_fts(extractions_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS extractions_au AFTER UPDATE ON extractions BEGIN
    INSERT INTO extractions_fts(extractions_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
    INSERT INTO extractions_fts(rowid, content)
    VALUES (new.rowid, new.content);
END;
"""


# ---------------------------------------------------------------------------
# Migration: extractions_fts table + triggers
# Older collection DBs were created before extractions_fts was part of the
# standard schema. Auto-applied at connect() time when `extractions` exists
# but `extractions_fts` does not.
# ---------------------------------------------------------------------------

EXTRACTIONS_FTS_MIGRATION = """\
CREATE VIRTUAL TABLE IF NOT EXISTS extractions_fts USING fts5(
    content,
    content='extractions',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS extractions_ai AFTER INSERT ON extractions BEGIN
    INSERT INTO extractions_fts(rowid, content)
    VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS extractions_ad AFTER DELETE ON extractions BEGIN
    INSERT INTO extractions_fts(extractions_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS extractions_au AFTER UPDATE ON extractions BEGIN
    INSERT INTO extractions_fts(extractions_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
    INSERT INTO extractions_fts(rowid, content)
    VALUES (new.rowid, new.content);
END;
"""

# ---------------------------------------------------------------------------
# Nexus Refs Schema — Move 3: tracks which local nodes have been promoted
# into the master Nexus pointer graph. Auto-migrated on connect for existing
# collection DBs.
# ---------------------------------------------------------------------------

NEXUS_REFS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS nexus_refs (
    local_node_id TEXT NOT NULL,
    nexus_node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    promoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (local_node_id, node_type)
);
CREATE INDEX IF NOT EXISTS idx_nexus_refs_nexus ON nexus_refs(nexus_node_id);
"""

# ---------------------------------------------------------------------------
# Forge Sync Schema — tracks ingested files for watch/compile mode
# ---------------------------------------------------------------------------

FORGE_SYNC_SCHEMA = """\
CREATE TABLE IF NOT EXISTS forge_sync (
    file_path TEXT PRIMARY KEY,
    file_hash TEXT NOT NULL,
    file_size INTEGER,
    last_modified REAL,
    last_synced TEXT,
    doc_id TEXT,
    status TEXT DEFAULT 'synced',
    error TEXT
);
"""

# ---------------------------------------------------------------------------
# Investigation Schema Extension — connections, gaps, claims
# ---------------------------------------------------------------------------

INVESTIGATION_SCHEMA = """\
-- Connections between entities
CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY,
    entity_a_id TEXT NOT NULL,
    entity_b_id TEXT NOT NULL,
    relationship TEXT NOT NULL,
    direction TEXT DEFAULT 'bidirectional',
    confidence TEXT DEFAULT 'confirmed',
    start_date TEXT,
    end_date TEXT,
    source_doc_ids TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Investigation gaps / open questions
CREATE TABLE IF NOT EXISTS gaps (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    priority TEXT DEFAULT 'medium',
    status TEXT DEFAULT 'open',
    questions TEXT,
    related_entity_ids TEXT,
    resolution TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Claims requiring verification
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    claim TEXT NOT NULL,
    source_doc_id TEXT,
    verification_status TEXT DEFAULT 'unverified',
    confidence_score REAL,
    supporting_evidence TEXT,
    contradicting_evidence TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ---------------------------------------------------------------------------
# sqlite-vec embedding table (created at runtime per collection)
# ---------------------------------------------------------------------------

EMBEDDING_TABLE_SQL = """\
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(
    chunk_id TEXT PRIMARY KEY,
    embedding FLOAT[{dim}]
);
"""

# ---------------------------------------------------------------------------
# Cartridge Schema — extends any collection DB into a sovereign cartridge
# ---------------------------------------------------------------------------

CARTRIDGE_SCHEMA = """\
-- Cartridge manifest (one row per cartridge)
CREATE TABLE IF NOT EXISTS cartridge_meta (
    id TEXT PRIMARY KEY DEFAULT 'manifest',
    title TEXT NOT NULL,
    description TEXT,
    author TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    version TEXT DEFAULT '1.0.0',
    schema_version INTEGER DEFAULT 2,
    extraction_model TEXT,
    extraction_date TIMESTAMP,
    cover_image_hash TEXT,
    source_hash TEXT,
    document_type TEXT,
    language TEXT DEFAULT 'en',
    tags TEXT DEFAULT '[]',
    page_count INTEGER,
    word_count INTEGER
);

-- Sovereignty protocols
CREATE TABLE IF NOT EXISTS protocols (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    access_level TEXT NOT NULL DEFAULT 'public',
    restricted_to TEXT DEFAULT '[]',
    rationale TEXT,
    set_by TEXT,
    set_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Luna's reflections (marginalia)
CREATE TABLE IF NOT EXISTS reflections (
    id TEXT PRIMARY KEY,
    extraction_id TEXT,
    reflection_type TEXT NOT NULL DEFAULT 'connection',
    content TEXT NOT NULL,
    confidence REAL DEFAULT 0.8,
    luna_instance TEXT,
    session_id TEXT,
    superseded_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (extraction_id) REFERENCES extractions(id) ON DELETE SET NULL,
    FOREIGN KEY (superseded_by) REFERENCES reflections(id) ON DELETE SET NULL
);

-- Human annotations
CREATE TABLE IF NOT EXISTS annotations (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    annotation_type TEXT NOT NULL DEFAULT 'note',
    content TEXT NOT NULL,
    author TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Cross-cartridge references
CREATE TABLE IF NOT EXISTS cross_refs (
    id TEXT PRIMARY KEY,
    source_node_type TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    target_cartridge_key TEXT NOT NULL,
    target_node_type TEXT,
    target_node_id TEXT,
    relationship TEXT NOT NULL DEFAULT 'relates_to',
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Access log (provenance)
CREATE TABLE IF NOT EXISTS access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    query TEXT,
    results_count INTEGER,
    luna_instance TEXT,
    session_id TEXT,
    accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for cartridge tables
CREATE INDEX IF NOT EXISTS idx_protocols_scope ON protocols(scope);
CREATE INDEX IF NOT EXISTS idx_protocols_level ON protocols(access_level);
CREATE INDEX IF NOT EXISTS idx_reflections_extraction ON reflections(extraction_id);
CREATE INDEX IF NOT EXISTS idx_reflections_type ON reflections(reflection_type);
CREATE INDEX IF NOT EXISTS idx_annotations_target ON annotations(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_cross_refs_source ON cross_refs(source_node_type, source_node_id);
CREATE INDEX IF NOT EXISTS idx_cross_refs_target ON cross_refs(target_cartridge_key);
CREATE INDEX IF NOT EXISTS idx_access_log_event ON access_log(event_type);

-- FTS5 on reflections
CREATE VIRTUAL TABLE IF NOT EXISTS reflections_fts USING fts5(
    content,
    content='reflections',
    content_rowid='rowid'
);

-- FTS5 sync triggers for reflections
CREATE TRIGGER IF NOT EXISTS reflections_ai AFTER INSERT ON reflections BEGIN
    INSERT INTO reflections_fts(rowid, content)
    VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS reflections_ad AFTER DELETE ON reflections BEGIN
    INSERT INTO reflections_fts(reflections_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS reflections_au AFTER UPDATE ON reflections BEGIN
    INSERT INTO reflections_fts(reflections_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
    INSERT INTO reflections_fts(rowid, content)
    VALUES (new.rowid, new.content);
END;
"""
