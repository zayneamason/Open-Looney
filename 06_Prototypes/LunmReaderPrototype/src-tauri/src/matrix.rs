use crate::error::LunmError;
use crate::types::MatrixHandle;
use rusqlite::{Connection, OpenFlags};
use std::fs::File;
use std::io::Read;
use std::path::Path;

pub const LUNC_APPLICATION_ID: u32 = 0x4C554E43;
pub const LUNM_APPLICATION_ID: u32 = 0x4C554E4D;
pub const MIN_LUNM_USER_VERSION: i64 = 2;

pub fn open_and_validate(path: &Path) -> Result<MatrixHandle, LunmError> {
    check_sqlite_header(path)?;
    let conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    check_application_id(&conn)?;
    check_user_version(&conn)?;
    conn.execute_batch("PRAGMA query_only = 1;")?;
    Ok(MatrixHandle {
        path: path.to_string_lossy().into_owned(),
        conn,
    })
}

fn check_sqlite_header(path: &Path) -> Result<(), LunmError> {
    let mut f = File::open(path)?;
    let mut header = [0_u8; 16];
    f.read_exact(&mut header)
        .map_err(|_| LunmError::NotSqlite)?;
    if &header == b"SQLite format 3\0" {
        Ok(())
    } else {
        Err(LunmError::NotSqlite)
    }
}

fn check_application_id(conn: &Connection) -> Result<(), LunmError> {
    let id: i64 = conn.query_row("PRAGMA application_id", [], |r| r.get(0))?;
    let actual_id = id as u32;
    if actual_id == LUNM_APPLICATION_ID {
        return Ok(());
    }
    let family_hint = if actual_id == LUNC_APPLICATION_ID {
        "LUNC".to_string()
    } else {
        "unknown".to_string()
    };
    Err(LunmError::WrongFamily {
        actual_id,
        family_hint,
    })
}

fn check_user_version(conn: &Connection) -> Result<(), LunmError> {
    let actual: i64 = conn.query_row("PRAGMA user_version", [], |r| r.get(0))?;
    if actual >= MIN_LUNM_USER_VERSION {
        Ok(())
    } else {
        Err(LunmError::UnsupportedUserVersion { actual })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::queries::{
        get_lunm_health, list_conversation_turns, list_graph_edges, list_memory_nodes,
        list_sessions,
    };
    use crate::types::MemoryNodeFilters;
    use rusqlite::Connection;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_path(name: &str) -> std::path::PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "lunm-reader-{name}-{}-{stamp}.lun",
            std::process::id()
        ))
    }

    fn create_fixture(path: &Path, app_id: u32, user_version: i64, include_profile: bool) {
        let conn = Connection::open(path).unwrap();
        conn.execute_batch(&format!(
            "PRAGMA application_id = {app_id};
             PRAGMA user_version = {user_version};"
        ))
        .unwrap();
        conn.execute_batch(FI_SCHEMA).unwrap();
        conn.execute_batch(
            "INSERT INTO memory_nodes(id, node_type, content, classification)
             VALUES ('n1', 'NOTE', 'remember this', 'private');
             INSERT INTO graph_edges(from_id, to_id, relationship)
             VALUES ('n1', 'n1', 'self');
             INSERT INTO sessions(session_id, started_at, turns_count)
             VALUES ('s1', 1.0, 1);
             INSERT INTO conversation_turns(session_id, role, content, turn_type)
             VALUES ('s1', 'user', 'hello', 'NORMAL_USER_TURN');
             INSERT INTO nexus_nodes(nexus_node_id, collection_key, satellite_node_id, node_type)
             VALUES ('nx1', 'collection', 'n1', 'NOTE');
             INSERT INTO nexus_edges(src_node_id, dst_node_id, edge_type)
             VALUES ('nx1', 'nx1', 'self');
             INSERT INTO nexus_registry(collection_key, lun_path, ingestion_pattern, mounted, family, user_version)
             VALUES ('collection', '/tmp/example.lun', 'manual', 1, 'lunc_v03', 3);",
        )
        .unwrap();
        if include_profile {
            conn.execute_batch(
                "INSERT INTO profile_config(key, value, value_type)
                 VALUES
                 ('lunm.format_version', '0.1', 'string'),
                 ('lunm.matrix_ulid', '01KY8KK2A4VVQ8VB2NS0NQP5CD', 'string'),
                 ('lunm.created_at', '2026-07-24T00:00:00+00:00', 'string'),
                 ('lunm.engine_version', '2.0.0', 'string');",
            )
            .unwrap();
        }
    }

    const FI_SCHEMA: &str = "
        CREATE TABLE memory_nodes (
            id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            content TEXT NOT NULL,
            classification TEXT DEFAULT 'public',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE graph_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id TEXT NOT NULL,
            to_id TEXT NOT NULL,
            relationship TEXT NOT NULL,
            strength REAL DEFAULT 1.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE conversation_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            turn_type TEXT NOT NULL DEFAULT 'NORMAL_USER_TURN',
            tier TEXT DEFAULT 'active',
            thread_id TEXT
        );
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            started_at REAL NOT NULL,
            ended_at REAL,
            app_context TEXT,
            turns_count INTEGER DEFAULT 0,
            metadata TEXT
        );
        CREATE TABLE profile_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            value_type TEXT NOT NULL DEFAULT 'string',
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_by TEXT,
            description TEXT
        );
        CREATE TABLE nexus_nodes (
            nexus_node_id TEXT PRIMARY KEY,
            collection_key TEXT NOT NULL,
            satellite_node_id TEXT NOT NULL,
            node_type TEXT NOT NULL,
            promoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE nexus_edges (
            src_node_id TEXT NOT NULL,
            dst_node_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            weight REAL DEFAULT 1.0
        );
        CREATE TABLE nexus_registry (
            collection_key TEXT PRIMARY KEY,
            lun_path TEXT NOT NULL,
            ingestion_pattern TEXT NOT NULL,
            mounted INTEGER NOT NULL DEFAULT 0,
            family TEXT,
            user_version INTEGER,
            validation_status TEXT,
            validation_reason TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source TEXT NOT NULL DEFAULT 'yaml'
        );
    ";

    #[test]
    fn rejects_non_sqlite() {
        let path = temp_path("nonsqlite");
        fs::write(&path, b"not sqlite").unwrap();
        assert!(matches!(open_and_validate(&path), Err(LunmError::NotSqlite)));
        let _ = fs::remove_file(path);
    }

    #[test]
    fn rejects_lunc_family() {
        let path = temp_path("lunc");
        create_fixture(&path, LUNC_APPLICATION_ID, 3, true);
        match open_and_validate(&path) {
            Err(LunmError::WrongFamily { family_hint, .. }) => assert_eq!(family_hint, "LUNC"),
            other => panic!("expected WrongFamily(LUNC), got {other:?}"),
        }
        let _ = fs::remove_file(path);
    }

    #[test]
    fn rejects_unknown_family() {
        let path = temp_path("unknown");
        create_fixture(&path, 1234, 2, true);
        match open_and_validate(&path) {
            Err(LunmError::WrongFamily { family_hint, .. }) => assert_eq!(family_hint, "unknown"),
            other => panic!("expected WrongFamily(unknown), got {other:?}"),
        }
        let _ = fs::remove_file(path);
    }

    #[test]
    fn accepts_lunm_user_version_2() {
        let path = temp_path("ok");
        create_fixture(&path, LUNM_APPLICATION_ID, 2, true);
        let handle = open_and_validate(&path).unwrap();
        assert_eq!(handle.path, path.to_string_lossy());
        let _ = fs::remove_file(path);
    }

    #[test]
    fn rejects_lunm_user_version_below_2() {
        let path = temp_path("old");
        create_fixture(&path, LUNM_APPLICATION_ID, 1, true);
        assert!(matches!(
            open_and_validate(&path),
            Err(LunmError::UnsupportedUserVersion { actual: 1 })
        ));
        let _ = fs::remove_file(path);
    }

    #[test]
    fn enforces_query_only() {
        let path = temp_path("readonly");
        create_fixture(&path, LUNM_APPLICATION_ID, 2, true);
        let handle = open_and_validate(&path).unwrap();
        assert!(handle
            .conn
            .execute("CREATE TABLE should_fail(id INTEGER)", [])
            .is_err());
        let _ = fs::remove_file(path);
    }

    #[test]
    fn health_reports_missing_fi_table() {
        let path = temp_path("missing-table");
        create_fixture(&path, LUNM_APPLICATION_ID, 2, true);
        let conn = Connection::open(&path).unwrap();
        conn.execute("DROP TABLE profile_config", []).unwrap();
        drop(conn);
        let handle = open_and_validate(&path).unwrap();
        let report = get_lunm_health(&handle.conn).unwrap();
        assert!(report.error_count > 0);
        assert!(report
            .checks
            .iter()
            .any(|c| c.code == "missing_fi_table" && c.message.contains("profile_config")));
        let _ = fs::remove_file(path);
    }

    #[test]
    fn health_warns_when_matrix_ulid_missing() {
        let path = temp_path("missing-ulid");
        create_fixture(&path, LUNM_APPLICATION_ID, 2, false);
        let handle = open_and_validate(&path).unwrap();
        let report = get_lunm_health(&handle.conn).unwrap();
        assert!(report.warning_count > 0);
        assert!(report
            .checks
            .iter()
            .any(|c| c.code == "missing_matrix_ulid"));
        let _ = fs::remove_file(path);
    }

    #[test]
    fn lists_rows_from_fixture_tables() {
        let path = temp_path("rows");
        create_fixture(&path, LUNM_APPLICATION_ID, 2, true);
        let handle = open_and_validate(&path).unwrap();
        let nodes = list_memory_nodes(
            &handle.conn,
            MemoryNodeFilters {
                node_type: Some("NOTE".into()),
                classification: Some("private".into()),
            },
            10,
            0,
        )
        .unwrap();
        assert_eq!(nodes.len(), 1);
        let edges = list_graph_edges(&handle.conn, Some("n1".into()), 10, 0).unwrap();
        assert_eq!(edges.len(), 1);
        let sessions = list_sessions(&handle.conn, 10, 0).unwrap();
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0].values.get("actual_turns").unwrap(), &serde_json::json!(1));
        assert_eq!(
            sessions[0].values.get("stored_turns_count").unwrap(),
            &serde_json::json!(1)
        );
        let turns = list_conversation_turns(&handle.conn, Some("s1".into()), 10, 0).unwrap();
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].values.get("content").unwrap(), &serde_json::json!("hello"));
        let _ = fs::remove_file(path);
    }
}
