PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1');

CREATE TABLE IF NOT EXISTS traces (
    id              TEXT PRIMARY KEY,
    ts              REAL NOT NULL,
    session_id      TEXT,
    turn_num        INTEGER,
    query           TEXT NOT NULL,
    route           TEXT,
    door            TEXT,
    aperture        TEXT,
    aperture_deg    INTEGER,
    backend         TEXT,
    prepare_only    INTEGER NOT NULL,
    latency_ms      INTEGER,
    token_count     INTEGER,
    token_budget    INTEGER,
    final_prompt    TEXT,
    notes           TEXT,
    schema_version  INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_traces_ts ON traces(ts DESC);
CREATE INDEX IF NOT EXISTS idx_traces_route ON traces(route);
CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id);

CREATE TABLE IF NOT EXISTS trace_stages (
    trace_id        TEXT NOT NULL,
    stage_idx       INTEGER NOT NULL,
    stage_name      TEXT NOT NULL,
    start_ms        REAL NOT NULL,
    end_ms          REAL NOT NULL,
    budget_ms       REAL,
    candidates_in   INTEGER,
    candidates_out  INTEGER,
    status          TEXT,
    error           TEXT,
    PRIMARY KEY (trace_id, stage_idx),
    FOREIGN KEY (trace_id) REFERENCES traces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trace_candidates (
    trace_id         TEXT NOT NULL,
    stage_name       TEXT NOT NULL,
    ord              INTEGER NOT NULL,
    node_id          TEXT,
    node_kind        TEXT,
    node_label       TEXT,
    raw_score        REAL,
    fused_score      REAL,
    lock_in          REAL,
    relevance        REAL,
    age_days         REAL,
    accepted         INTEGER NOT NULL,
    rejection_reason TEXT,
    ring_assigned    TEXT,
    FOREIGN KEY (trace_id) REFERENCES traces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_candidates_trace ON trace_candidates(trace_id);
CREATE INDEX IF NOT EXISTS idx_candidates_rejection ON trace_candidates(rejection_reason)
    WHERE rejection_reason IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_candidates_kind ON trace_candidates(node_kind);

CREATE TABLE IF NOT EXISTS trace_rings (
    trace_id     TEXT NOT NULL,
    ring         TEXT NOT NULL,
    item_count   INTEGER,
    token_count  INTEGER,
    items_json   TEXT,
    PRIMARY KEY (trace_id, ring),
    FOREIGN KEY (trace_id) REFERENCES traces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trace_prompt_layers (
    trace_id    TEXT NOT NULL,
    layer_idx   INTEGER NOT NULL,
    layer_id    TEXT NOT NULL,
    layer_name  TEXT NOT NULL,
    content     TEXT,
    token_count INTEGER,
    PRIMARY KEY (trace_id, layer_idx),
    FOREIGN KEY (trace_id) REFERENCES traces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trace_keywords (
    trace_id  TEXT NOT NULL,
    keyword   TEXT NOT NULL,
    source    TEXT NOT NULL,
    PRIMARY KEY (trace_id, keyword, source),
    FOREIGN KEY (trace_id) REFERENCES traces(id) ON DELETE CASCADE
);
