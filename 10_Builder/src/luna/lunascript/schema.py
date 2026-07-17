"""LunaScript SQLite table definitions — all IF NOT EXISTS, safe on existing DBs."""

LUNASCRIPT_TABLES = """
-- Luna's current cognitive signature state (one row, updated in place)
CREATE TABLE IF NOT EXISTS lunascript_state (
    id INTEGER PRIMARY KEY DEFAULT 1,
    trait_vector TEXT NOT NULL,
    trait_weights TEXT NOT NULL,
    trait_trends TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'idle',
    glyph_string TEXT DEFAULT '○',
    constraints TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    epsilon REAL NOT NULL DEFAULT 0.15,
    updated_at REAL NOT NULL
);

-- Every delegation round-trip
CREATE TABLE IF NOT EXISTS lunascript_delegation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outbound_sig TEXT NOT NULL,
    outbound_glyph TEXT,
    return_sig TEXT,
    return_glyph TEXT,
    delta_vector TEXT,
    delta_class TEXT,
    drift_score REAL,
    task_type TEXT,
    provider_used TEXT,
    success_score REAL,
    veto_violations TEXT,
    iteration_applied TEXT,
    created_at REAL NOT NULL
);

-- Named cognitive patterns Luna can snap into
CREATE TABLE IF NOT EXISTS lunascript_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    trait_vector TEXT NOT NULL,
    glyph_string TEXT,
    usage_count INTEGER DEFAULT 0,
    avg_success REAL DEFAULT 0.0,
    created_at REAL NOT NULL,
    last_used REAL
);

-- Feature baselines from corpus calibration
CREATE TABLE IF NOT EXISTS lunascript_baselines (
    feature_name TEXT PRIMARY KEY,
    mean REAL NOT NULL,
    stddev REAL NOT NULL,
    min_val REAL NOT NULL,
    max_val REAL NOT NULL,
    p25 REAL NOT NULL,
    p50 REAL NOT NULL,
    p75 REAL NOT NULL,
    n INTEGER NOT NULL,
    calibrated_at REAL NOT NULL
);

-- Running trait-outcome correlations (serialized state)
CREATE TABLE IF NOT EXISTS lunascript_correlations (
    trait_name TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT 'all',
    correlation REAL,
    n_observations INTEGER DEFAULT 0,
    serialized_state TEXT,
    last_updated REAL,
    PRIMARY KEY (trait_name, task_type)
);

-- User feedback on delegation quality (thumbs up/down)
CREATE TABLE IF NOT EXISTS lunascript_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT,
    score REAL NOT NULL,
    trait_vector_at_time TEXT,
    classification_at_time TEXT,
    created_at REAL NOT NULL
);
"""


async def apply_lunascript_schema(db) -> None:
    # Strip SQL comments before splitting on semicolons
    lines = [l for l in LUNASCRIPT_TABLES.splitlines() if not l.strip().startswith("--")]
    cleaned = "\n".join(lines)
    for statement in cleaned.strip().split(";"):
        statement = statement.strip()
        if statement:
            await db.execute(statement)
