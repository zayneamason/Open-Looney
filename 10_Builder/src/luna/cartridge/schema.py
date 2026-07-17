"""
.lun Knowledge Cartridge Schema
================================

SQL constants for the standalone `.lun` SQLite format.
A .lun file stores a document's complete node tree, comprehension
artifacts anchored to source nodes, and embeddings for search.
"""

LUN_SCHEMA = """\
-- Key-value metadata (title, source_hash, created_at, ...)
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Document node tree — every element is a node with parent pointers
CREATE TABLE IF NOT EXISTS doc_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER,
    type TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    content TEXT,
    meta_json TEXT,
    -- SPEC-002 portable identity (Phase 3). NOT NULL enforced by builder writes;
    -- Python regex ^[0-9A-HJKMNP-TV-Z]{26}$ is the authoritative format validator.
    ulid TEXT NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES doc_nodes(id)
);

-- LLM-generated extractions (claims, summaries, entities)
CREATE TABLE IF NOT EXISTS extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    -- SPEC-001 anchor classification (Phase 2)
    anchor_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (anchor_status IN ('anchored', 'synthesized', 'match_failed', 'filtered', 'unknown')),
    anchor_reason TEXT,
    -- SPEC-002 portable identity (Phase 3) + Phase 3.5 canonical first char.
    -- CHECK is a write gate; Python regex ^[0-7][0-9A-HJKMNP-TV-Z]{25}$ is authoritative.
    ulid TEXT NOT NULL,
    -- SPEC-003 raw signals (Phase 4). Replaces hardcoded 'confidence' constant.
    -- llm_logprob_sum and llm_token_count are paired-NULL (both NULL or both populated);
    -- invariant enforced by validate_extractions().
    llm_logprob_sum REAL,
    llm_token_count INTEGER,
    extraction_method TEXT NOT NULL DEFAULT 'llm'
        CHECK (extraction_method IN ('llm', 'rule', 'ner', 'manual')),
    CHECK (length(ulid) = 26 AND ulid GLOB '[0-7][0-9A-HJKMNP-TV-Z]*')
);

-- Anchors extractions to specific source nodes
CREATE TABLE IF NOT EXISTS claim_sources (
    claim_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    -- SPEC-001 provenance (Phase 2). anchored_at is unix milliseconds INTEGER.
    anchor_method TEXT NOT NULL DEFAULT 'auto'
        CHECK (anchor_method IN ('auto', 'manual', 'migrated')),
    anchored_by TEXT,
    anchored_at INTEGER,
    event_id TEXT,
    -- SPEC-002 shadow ULIDs (Phase 3). Nullable in v0.2; become composite PK in v0.3.
    claim_ulid TEXT,
    node_ulid TEXT,
    PRIMARY KEY (claim_id, node_id),
    FOREIGN KEY (claim_id) REFERENCES extractions(id),
    FOREIGN KEY (node_id) REFERENCES doc_nodes(id)
);

-- SPEC-001 synthesis soft-anchoring (Phase 2)
CREATE TABLE IF NOT EXISTS claim_context_nodes (
    claim_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    relevance REAL NOT NULL,
    -- SPEC-002 shadow ULIDs (Phase 3). Nullable in v0.2; become composite PK in v0.3.
    claim_ulid TEXT,
    node_ulid TEXT,
    PRIMARY KEY (claim_id, node_id),
    FOREIGN KEY (claim_id) REFERENCES extractions(id),
    FOREIGN KEY (node_id) REFERENCES doc_nodes(id),
    CHECK (relevance >= 0.0 AND relevance <= 1.0)
);

-- Embeddings stored as raw BLOBs (no vec0 — portability)
CREATE TABLE IF NOT EXISTS embeddings (
    node_id INTEGER NOT NULL,
    level TEXT NOT NULL,
    vector BLOB NOT NULL,
    -- SPEC-002 shadow ULID (Phase 3). Nullable in v0.2.
    node_ulid TEXT,
    PRIMARY KEY (node_id, level),
    FOREIGN KEY (node_id) REFERENCES doc_nodes(id)
);

-- FTS5 full-text index on node content
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    content,
    content='doc_nodes',
    content_rowid='id'
);

-- FTS5 sync triggers (same pattern as aibrarian_schema.py)
CREATE TRIGGER IF NOT EXISTS nodes_fts_ai AFTER INSERT ON doc_nodes BEGIN
    INSERT INTO nodes_fts(rowid, content)
    VALUES (new.id, COALESCE(new.content, ''));
END;

CREATE TRIGGER IF NOT EXISTS nodes_fts_ad AFTER DELETE ON doc_nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, content)
    VALUES ('delete', old.id, COALESCE(old.content, ''));
END;

CREATE TRIGGER IF NOT EXISTS nodes_fts_au AFTER UPDATE ON doc_nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, content)
    VALUES ('delete', old.id, COALESCE(old.content, ''));
    INSERT INTO nodes_fts(rowid, content)
    VALUES (new.id, COALESCE(new.content, ''));
END;

-- Tracks which local nodes have been promoted to Nexus (Move 3).
-- local_node_id is TEXT for cross-schema compatibility — cartridge .lun uses
-- INTEGER autoincrement IDs, aibrarian .db uses TEXT UUIDs; both are stored
-- here as TEXT so the same promote_to_nexus() handles either substrate.
CREATE TABLE IF NOT EXISTS nexus_refs (
    local_node_id TEXT NOT NULL,
    nexus_node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    promoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (local_node_id, node_type)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_doc_nodes_parent ON doc_nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_doc_nodes_type ON doc_nodes(type);
CREATE INDEX IF NOT EXISTS idx_extractions_type ON extractions(type);
CREATE INDEX IF NOT EXISTS idx_claim_sources_node ON claim_sources(node_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_level ON embeddings(level);
CREATE INDEX IF NOT EXISTS idx_nexus_refs_nexus ON nexus_refs(nexus_node_id);
-- SPEC-001 indexes (Phase 2)
CREATE INDEX IF NOT EXISTS idx_extractions_anchor_status ON extractions(anchor_status);
CREATE INDEX IF NOT EXISTS idx_claim_context_claim ON claim_context_nodes(claim_id);
CREATE INDEX IF NOT EXISTS idx_claim_context_node ON claim_context_nodes(node_id);
-- SPEC-002 indexes (Phase 3). UNIQUE on primary ULID columns only — Q1 resolution
-- defers shadow-column indexes to v0.3 (when they become the composite PK).
CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_nodes_ulid ON doc_nodes(ulid);
CREATE UNIQUE INDEX IF NOT EXISTS uq_extractions_ulid ON extractions(ulid);
"""
