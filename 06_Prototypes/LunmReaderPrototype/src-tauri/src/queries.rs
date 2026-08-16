use crate::matrix::{LUNM_APPLICATION_ID, MIN_LUNM_USER_VERSION};
use crate::types::{
    HealthCheck, HealthLevel, LunmHealthReport, LunmOverview, MemoryNodeFilters,
    ProfileConfigRow, TableCount, TableRow,
};
use crate::error::LunmError;
use rusqlite::types::ValueRef;
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{Number, Value};
use std::collections::{BTreeMap, BTreeSet};

const FI_TABLES: &[&str] = &[
    "memory_nodes",
    "graph_edges",
    "conversation_turns",
    "sessions",
    "nexus_nodes",
    "nexus_edges",
    "nexus_registry",
    "profile_config",
];

const REQUIRED_COLUMNS: &[(&str, &[&str])] = &[
    (
        "profile_config",
        &["key", "value", "value_type", "updated_at"],
    ),
    (
        "nexus_registry",
        &[
            "collection_key",
            "lun_path",
            "ingestion_pattern",
            "mounted",
            "family",
            "user_version",
            "source",
        ],
    ),
    (
        "nexus_nodes",
        &["nexus_node_id", "collection_key", "satellite_node_id", "node_type"],
    ),
    ("nexus_edges", &["src_node_id", "dst_node_id", "edge_type"]),
    (
        "memory_nodes",
        &["id", "node_type", "content", "created_at", "updated_at"],
    ),
    ("graph_edges", &["from_id", "to_id", "relationship"]),
    (
        "conversation_turns",
        &["id", "session_id", "role", "content", "created_at", "turn_type"],
    ),
    ("sessions", &["session_id", "started_at"]),
];

pub fn get_lunm_overview(conn: &Connection, path: &str) -> Result<LunmOverview, LunmError> {
    let application_id = pragma_i64(conn, "application_id")? as u32;
    let user_version = pragma_i64(conn, "user_version")?;
    let header_rows = list_profile_config(conn, Some("lunm.".to_string()), 100, 0)?;
    let lookup = |key: &str| {
        header_rows
            .iter()
            .find(|r| r.key == key)
            .map(|r| r.value.clone())
    };
    let mut table_counts = Vec::new();
    for table in FI_TABLES {
        let present = table_exists(conn, table)?;
        let count = if present {
            Some(conn.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| r.get(0))?)
        } else {
            None
        };
        table_counts.push(TableCount {
            table: (*table).to_string(),
            count,
            present,
        });
    }
    Ok(LunmOverview {
        path: path.to_string(),
        application_id,
        user_version,
        format_version: lookup("lunm.format_version"),
        matrix_ulid: lookup("lunm.matrix_ulid"),
        created_at: lookup("lunm.created_at"),
        engine_version: lookup("lunm.engine_version"),
        header_rows,
        table_counts,
    })
}

pub fn get_lunm_health(conn: &Connection) -> Result<LunmHealthReport, LunmError> {
    let mut checks = Vec::new();
    let application_id = pragma_i64(conn, "application_id")? as u32;
    push_check(
        &mut checks,
        if application_id == LUNM_APPLICATION_ID {
            HealthLevel::Ok
        } else {
            HealthLevel::Error
        },
        "application_id",
        format!("application_id = 0x{application_id:08X}"),
    );

    let user_version = pragma_i64(conn, "user_version")?;
    push_check(
        &mut checks,
        if user_version >= MIN_LUNM_USER_VERSION {
            HealthLevel::Ok
        } else {
            HealthLevel::Error
        },
        "user_version",
        format!("user_version = {user_version}"),
    );

    for table in FI_TABLES {
        if !table_exists(conn, table)? {
            push_check(
                &mut checks,
                HealthLevel::Error,
                "missing_fi_table",
                format!("Missing format-invariant table `{table}`"),
            );
        }
    }

    for (table, cols) in REQUIRED_COLUMNS {
        if !table_exists(conn, table)? {
            continue;
        }
        let existing = column_names(conn, table)?;
        for col in *cols {
            if !existing.contains(*col) {
                push_check(
                    &mut checks,
                    HealthLevel::Error,
                    "missing_fi_column",
                    format!("Missing format-invariant column `{table}.{col}`"),
                );
            }
        }
    }

    let format_version = config_value(conn, "lunm.format_version")?;
    if format_version.is_none() {
        push_check(
            &mut checks,
            HealthLevel::Warning,
            "missing_format_version",
            "Missing `lunm.format_version` header key",
        );
    } else if user_version == 2 && format_version.as_deref() != Some("0.1") {
        push_check(
            &mut checks,
            HealthLevel::Warning,
            "version_drift",
            format!(
                "user_version=2 expects lunm.format_version=0.1, found {}",
                format_version.unwrap()
            ),
        );
    } else {
        push_check(
            &mut checks,
            HealthLevel::Ok,
            "format_version",
            format!(
                "lunm.format_version = {}",
                format_version.unwrap_or_else(|| "missing".to_string())
            ),
        );
    }

    let matrix_ulid = config_value(conn, "lunm.matrix_ulid")?;
    match matrix_ulid {
        None => push_check(
            &mut checks,
            HealthLevel::Warning,
            "missing_matrix_ulid",
            "Missing `lunm.matrix_ulid` header key",
        ),
        Some(v) if !is_canonical_ulid(&v) => push_check(
            &mut checks,
            HealthLevel::Warning,
            "malformed_matrix_ulid",
            format!("`lunm.matrix_ulid` is not a canonical ULID: {v}"),
        ),
        Some(v) => push_check(
            &mut checks,
            HealthLevel::Ok,
            "matrix_ulid",
            format!("lunm.matrix_ulid = {v}"),
        ),
    }

    let warning_count = checks
        .iter()
        .filter(|c| matches!(c.level, HealthLevel::Warning))
        .count();
    let error_count = checks
        .iter()
        .filter(|c| matches!(c.level, HealthLevel::Error))
        .count();
    Ok(LunmHealthReport {
        checks,
        error_count,
        warning_count,
    })
}

pub fn list_memory_nodes(
    conn: &Connection,
    filters: MemoryNodeFilters,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    let limit = clamp_limit(limit);
    let offset = offset.max(0);
    let sql = "SELECT id, node_type, content, classification, created_at, updated_at
               FROM memory_nodes
               WHERE (?1 IS NULL OR node_type = ?1)
                 AND (?2 IS NULL OR classification = ?2)
               ORDER BY created_at DESC, id ASC
               LIMIT ?3 OFFSET ?4";
    rows_for_query(
        conn,
        sql,
        params![filters.node_type, filters.classification, limit, offset],
    )
}

pub fn list_graph_edges(
    conn: &Connection,
    node_id: Option<String>,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    rows_for_query(
        conn,
        "SELECT e.id, e.from_id, e.to_id, e.relationship, e.strength, e.created_at,
                src.content AS from_content, dst.content AS to_content
         FROM graph_edges e
         LEFT JOIN memory_nodes src ON src.id = e.from_id
         LEFT JOIN memory_nodes dst ON dst.id = e.to_id
         WHERE (?1 IS NULL OR e.from_id = ?1 OR e.to_id = ?1)
         ORDER BY e.created_at DESC, e.id DESC
         LIMIT ?2 OFFSET ?3",
        params![node_id, clamp_limit(limit), offset.max(0)],
    )
}

pub fn list_sessions(
    conn: &Connection,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    rows_for_query(
        conn,
        "SELECT s.session_id, s.started_at, s.ended_at, s.app_context,
                COUNT(t.id) AS actual_turns, s.turns_count AS stored_turns_count,
                s.metadata
         FROM sessions s
         LEFT JOIN conversation_turns t ON t.session_id = s.session_id
         GROUP BY s.session_id, s.started_at, s.ended_at, s.app_context,
                  s.turns_count, s.metadata
         ORDER BY s.started_at DESC, s.session_id ASC
         LIMIT ?1 OFFSET ?2",
        params![clamp_limit(limit), offset.max(0)],
    )
}

pub fn list_conversation_turns(
    conn: &Connection,
    session_id: Option<String>,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    rows_for_query(
        conn,
        "SELECT id, session_id, role, content, created_at, turn_type, tier, thread_id
         FROM conversation_turns
         WHERE (?1 IS NULL OR session_id = ?1)
         ORDER BY created_at ASC, id ASC
         LIMIT ?2 OFFSET ?3",
        params![session_id, clamp_limit(limit), offset.max(0)],
    )
}

pub fn list_nexus_registry(
    conn: &Connection,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    rows_for_query(
        conn,
        "SELECT collection_key, lun_path, ingestion_pattern, mounted, family,
                user_version, source, validation_status, validation_reason, updated_at
         FROM nexus_registry
         ORDER BY updated_at DESC, collection_key ASC
         LIMIT ?1 OFFSET ?2",
        params![clamp_limit(limit), offset.max(0)],
    )
}

pub fn list_nexus_nodes(
    conn: &Connection,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    rows_for_query(
        conn,
        "SELECT nexus_node_id, collection_key, satellite_node_id, node_type, promoted_at
         FROM nexus_nodes
         ORDER BY promoted_at DESC, nexus_node_id ASC
         LIMIT ?1 OFFSET ?2",
        params![clamp_limit(limit), offset.max(0)],
    )
}

pub fn list_nexus_edges(
    conn: &Connection,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    rows_for_query(
        conn,
        "SELECT src_node_id, dst_node_id, edge_type, weight
         FROM nexus_edges
         ORDER BY src_node_id ASC, dst_node_id ASC
         LIMIT ?1 OFFSET ?2",
        params![clamp_limit(limit), offset.max(0)],
    )
}

pub fn list_profile_config(
    conn: &Connection,
    prefix: Option<String>,
    limit: i64,
    offset: i64,
) -> Result<Vec<ProfileConfigRow>, LunmError> {
    if !table_exists(conn, "profile_config")? {
        return Ok(Vec::new());
    }
    let prefix_like = prefix.map(|p| format!("{p}%"));
    let mut stmt = conn.prepare(
        "SELECT key, value, value_type, updated_at, updated_by, description
         FROM profile_config
         WHERE (?1 IS NULL OR key LIKE ?1)
         ORDER BY key ASC
         LIMIT ?2 OFFSET ?3",
    )?;
    let rows = stmt.query_map(params![prefix_like, clamp_limit(limit), offset.max(0)], |r| {
        Ok(ProfileConfigRow {
            key: r.get(0)?,
            value: r.get(1)?,
            value_type: r.get(2)?,
            updated_at: r.get(3)?,
            updated_by: r.get(4)?,
            description: r.get(5)?,
        })
    })?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row?);
    }
    Ok(out)
}

fn pragma_i64(conn: &Connection, name: &str) -> Result<i64, LunmError> {
    Ok(conn.query_row(&format!("PRAGMA {name}"), [], |r| r.get(0))?)
}

fn push_check(
    checks: &mut Vec<HealthCheck>,
    level: HealthLevel,
    code: impl Into<String>,
    message: impl Into<String>,
) {
    checks.push(HealthCheck {
        level,
        code: code.into(),
        message: message.into(),
    });
}

fn clamp_limit(limit: i64) -> i64 {
    limit.clamp(1, 500)
}

fn table_exists(conn: &Connection, table: &str) -> Result<bool, LunmError> {
    let exists: Option<i64> = conn
        .query_row(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'virtual table') AND name = ?1",
            [table],
            |r| r.get(0),
        )
        .optional()?;
    Ok(exists.is_some())
}

fn column_names(conn: &Connection, table: &str) -> Result<BTreeSet<String>, LunmError> {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let rows = stmt.query_map([], |r| r.get::<_, String>(1))?;
    let mut names = BTreeSet::new();
    for row in rows {
        names.insert(row?);
    }
    Ok(names)
}

fn config_value(conn: &Connection, key: &str) -> Result<Option<String>, LunmError> {
    if !table_exists(conn, "profile_config")? {
        return Ok(None);
    }
    Ok(conn
        .query_row("SELECT value FROM profile_config WHERE key = ?1", [key], |r| {
            r.get(0)
        })
        .optional()?)
}

fn is_canonical_ulid(value: &str) -> bool {
    if value.len() != 26 {
        return false;
    }
    let mut chars = value.chars();
    match chars.next() {
        Some('0'..='7') => {}
        _ => return false,
    }
    chars.all(|c| matches!(c, '0'..='9' | 'A'..='H' | 'J'..='K' | 'M'..='N' | 'P'..='T' | 'V'..='Z'))
}

fn rows_for_query<P>(conn: &Connection, sql: &str, params: P) -> Result<Vec<TableRow>, LunmError>
where
    P: rusqlite::Params,
{
    let mut stmt = conn.prepare(sql)?;
    let names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
    let rows = stmt.query_map(params, |r| row_to_table_row(r, &names))?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row?);
    }
    Ok(out)
}

fn row_to_table_row(row: &Row<'_>, names: &[String]) -> rusqlite::Result<TableRow> {
    let mut values = BTreeMap::new();
    for (idx, name) in names.iter().enumerate() {
        values.insert(name.clone(), value_ref_to_json(row.get_ref(idx)?));
    }
    Ok(TableRow { values })
}

fn value_ref_to_json(value: ValueRef<'_>) -> Value {
    match value {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(v) => Value::Number(v.into()),
        ValueRef::Real(v) => Number::from_f64(v).map(Value::Number).unwrap_or(Value::Null),
        ValueRef::Text(v) => Value::String(String::from_utf8_lossy(v).into_owned()),
        ValueRef::Blob(v) => Value::String(format!("<{} bytes>", v.len())),
    }
}
