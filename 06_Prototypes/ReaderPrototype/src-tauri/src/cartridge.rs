use crate::error::ReaderError;
use rusqlite::{Connection, OpenFlags};
use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};

const EXPECTED_APPLICATION_ID: u32 = 0x4C554E43; // 'LUNC'
const LUNM_APPLICATION_ID: u32 = 0x4C554E4D; // 'LUNM'
const SQLITE_HEADER: &[u8; 16] = b"SQLite format 3\0";
const SUPPORTED_USER_VERSION: i32 = 3;

pub struct CartridgeHandle {
    pub conn: Connection,
    #[allow(dead_code)] // kept for diagnostics / future re-open
    pub path: PathBuf,
}

pub fn open_and_validate(path: &Path) -> Result<CartridgeHandle, ReaderError> {
    check_sqlite_header(path)?;
    let conn = open_readonly(path)?;
    check_application_id(&conn)?;
    check_user_version(&conn)?;
    check_cartridge_kind(&conn)?;
    check_attribution(&conn)?;
    check_ledger_meta(&conn)?;
    fast_open_ledger_check(&conn)?;
    Ok(CartridgeHandle {
        conn,
        path: path.to_path_buf(),
    })
}

fn check_sqlite_header(path: &Path) -> Result<(), ReaderError> {
    let mut f = File::open(path)?;
    let mut buf = [0u8; 16];
    match f.read_exact(&mut buf) {
        Ok(()) if &buf == SQLITE_HEADER => Ok(()),
        Ok(()) => Err(ReaderError::NotASqliteFile),
        Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => Err(ReaderError::NotASqliteFile),
        Err(e) => Err(e.into()),
    }
}

fn open_readonly(path: &Path) -> Result<Connection, ReaderError> {
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX;
    let conn = Connection::open_with_flags(path, flags)?;
    conn.execute_batch("PRAGMA query_only = 1;")?;
    Ok(conn)
}

fn check_application_id(conn: &Connection) -> Result<(), ReaderError> {
    let id: i64 = conn.query_row("PRAGMA application_id", [], |r| r.get(0))?;
    let id_u32 = id as u32;
    if id_u32 == EXPECTED_APPLICATION_ID {
        return Ok(());
    }
    let family_hint = if id_u32 == LUNM_APPLICATION_ID {
        "LUNM".to_string()
    } else {
        "unknown".to_string()
    };
    Err(ReaderError::WrongFamily {
        actual_id: id_u32,
        family_hint,
    })
}

fn check_user_version(conn: &Connection) -> Result<(), ReaderError> {
    // Reader supports v0.3 (user_version=3) only. v0.1 and v0.2 cartridges are
    // rejected with a migrate hint surfaced in the frontend error text; the
    // reader does not migrate. Per LUN-FORMAT_v0.3.md §"Open contract": v0.2
    // cartridges raise UnsupportedVersionError(2) with the migration command.
    let v: i64 = conn.query_row("PRAGMA user_version", [], |r| r.get(0))?;
    let v = v as i32;
    if v == SUPPORTED_USER_VERSION {
        Ok(())
    } else {
        Err(ReaderError::UnsupportedVersion { actual: v })
    }
}

fn check_cartridge_kind(conn: &Connection) -> Result<(), ReaderError> {
    let kind: Option<String> = conn
        .query_row(
            "SELECT value FROM meta WHERE key = 'cartridge_kind'",
            [],
            |r| r.get(0),
        )
        .ok();
    match kind.as_deref() {
        Some("knowledge") => Ok(()),
        Some(_) => Err(ReaderError::UnsupportedCartridgeKind {
            actual: kind.unwrap(),
        }),
        None => Err(ReaderError::MissingRequiredMeta {
            key: "cartridge_kind".to_string(),
        }),
    }
}

fn check_attribution(conn: &Connection) -> Result<(), ReaderError> {
    let base: Option<String> = conn
        .query_row(
            "SELECT value FROM meta WHERE key = 'logprob_base'",
            [],
            |r| r.get(0),
        )
        .ok();
    let attr: Option<String> = conn
        .query_row(
            "SELECT value FROM meta WHERE key = 'logprob_attribution'",
            [],
            |r| r.get(0),
        )
        .ok();
    match (base.as_deref(), attr.as_deref()) {
        (Some("e"), Some("response_level")) => Ok(()),
        _ => Err(ReaderError::UnsupportedAttribution { base, attr }),
    }
}

fn meta_value(conn: &Connection, key: &str) -> Result<Option<String>, ReaderError> {
    Ok(conn
        .query_row(
            "SELECT value FROM meta WHERE key = ?1",
            [key],
            |r| r.get::<_, String>(0),
        )
        .ok())
}

/// SPEC-005 read-time check. Requires the 4 ledger meta keys and locks the
/// hash algorithm to SHA-256 (per LUN-FORMAT_v0.3.md §"Open contract" step 6).
fn check_ledger_meta(conn: &Connection) -> Result<(), ReaderError> {
    let algo = meta_value(conn, "ledger_hash_algorithm")?.ok_or_else(|| {
        ReaderError::MissingRequiredMeta {
            key: "ledger_hash_algorithm".to_string(),
        }
    })?;
    if algo != "sha256" {
        return Err(ReaderError::UnsupportedHashAlgorithm { actual: algo });
    }
    for required in ["ledger_genesis_ulid", "ledger_head_seq", "ledger_head_hash"] {
        if meta_value(conn, required)?.is_none() {
            return Err(ReaderError::MissingRequiredMeta {
                key: required.to_string(),
            });
        }
    }
    Ok(())
}

/// O(1) ledger consistency check. Verifies the two append-only triggers exist
/// and that `meta.ledger_head_seq` / `meta.ledger_head_hash` agree with
/// `MAX(seq)` and the entry_hash at that row.
fn fast_open_ledger_check(conn: &Connection) -> Result<(), ReaderError> {
    for trigger in ["annotation_ledger_no_update", "annotation_ledger_no_delete"] {
        let exists: i64 = conn.query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name = ?1",
            [trigger],
            |r| r.get(0),
        )?;
        if exists == 0 {
            return Err(ReaderError::LedgerIntegrity {
                detail: format!("missing append-only trigger: {}", trigger),
            });
        }
    }

    let head_seq: i64 = meta_value(conn, "ledger_head_seq")?
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| ReaderError::LedgerIntegrity {
            detail: "ledger_head_seq is not an integer".to_string(),
        })?;
    let head_hash = meta_value(conn, "ledger_head_hash")?.ok_or_else(|| {
        ReaderError::MissingRequiredMeta {
            key: "ledger_head_hash".to_string(),
        }
    })?;

    let actual_max: Option<i64> = conn
        .query_row("SELECT MAX(seq) FROM annotation_ledger", [], |r| r.get(0))
        .ok()
        .flatten();
    let actual_max = actual_max.ok_or_else(|| ReaderError::LedgerIntegrity {
        detail: "annotation_ledger is empty (genesis row required)".to_string(),
    })?;
    if actual_max != head_seq {
        return Err(ReaderError::LedgerIntegrity {
            detail: format!(
                "ledger_head_seq={} but MAX(seq)={}",
                head_seq, actual_max
            ),
        });
    }
    let actual_hash: String = conn.query_row(
        "SELECT entry_hash FROM annotation_ledger WHERE seq = ?1",
        [actual_max],
        |r| r.get(0),
    )?;
    if actual_hash != head_hash {
        return Err(ReaderError::LedgerIntegrity {
            detail: "ledger_head_hash does not match entry_hash at MAX(seq)".to_string(),
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection as RwConn;
    use std::io::Write;

    fn meditations_path() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .join("..")
            .join("07_Sample_Cartridges")
            .join("Marcus-Aurelius-Meditations.v03.lun")
    }

    fn meditations_v02_path() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .join("..")
            .join("07_Sample_Cartridges")
            .join("Marcus-Aurelius-Meditations.lun")
    }

    fn tmp_dir() -> PathBuf {
        let p = std::env::temp_dir().join("lun_reader_tests");
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    fn expect_err(r: Result<CartridgeHandle, ReaderError>) -> ReaderError {
        match r {
            Ok(_) => panic!("expected validation error, got Ok"),
            Err(e) => e,
        }
    }

    fn fabricate_non_sqlite() -> PathBuf {
        let p = tmp_dir().join("not_sqlite.lun");
        let mut f = File::create(&p).unwrap();
        f.write_all(b"this is not a sqlite file at all").unwrap();
        p
    }

    /// Builds a minimal v0.3-shaped cartridge with optional meta overrides and
    /// a one-row genesis ledger. `meta_extras` is appended to the standard meta
    /// inserts; pass `""` for the default v0.3 shape.
    fn fabricate_v03(
        name: &str,
        app_id: u32,
        user_version: i32,
        cartridge_kind: &str,
        include_attribution: bool,
        ledger_algo: Option<&str>,
        include_ledger_keys: bool,
        include_ledger_triggers: bool,
        include_genesis_row: bool,
    ) -> PathBuf {
        let p = tmp_dir().join(name);
        let _ = std::fs::remove_file(&p);
        let conn = RwConn::open(&p).unwrap();
        conn.execute_batch(&format!(
            "PRAGMA application_id = {};\n\
             PRAGMA user_version = {};\n\
             CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);\n\
             INSERT INTO meta VALUES('cartridge_kind','{}');",
            app_id as i32, user_version, cartridge_kind
        ))
        .unwrap();
        if include_attribution {
            conn.execute_batch(
                "INSERT INTO meta VALUES('logprob_base','e');\n\
                 INSERT INTO meta VALUES('logprob_attribution','response_level');",
            )
            .unwrap();
        }
        // Ledger table + triggers
        conn.execute_batch(
            "CREATE TABLE annotation_ledger (
                seq        INTEGER PRIMARY KEY AUTOINCREMENT,
                ulid       TEXT NOT NULL UNIQUE,
                entry_ts   INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor_id   TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                target_kind TEXT,
                target_ulid TEXT,
                target_cartridge_ulid TEXT,
                payload TEXT NOT NULL,
                prev_hash TEXT,
                entry_hash TEXT NOT NULL UNIQUE
            );",
        )
        .unwrap();
        if include_ledger_triggers {
            conn.execute_batch(
                "CREATE TRIGGER annotation_ledger_no_update
                 BEFORE UPDATE ON annotation_ledger
                 BEGIN SELECT RAISE(ABORT, 'append-only'); END;
                 CREATE TRIGGER annotation_ledger_no_delete
                 BEFORE DELETE ON annotation_ledger
                 BEGIN SELECT RAISE(ABORT, 'append-only'); END;",
            )
            .unwrap();
        }
        let mut head_seq: i64 = 0;
        let mut head_hash = String::new();
        if include_genesis_row {
            let genesis_hash = "a".repeat(64);
            conn.execute(
                "INSERT INTO annotation_ledger
                 (seq, ulid, entry_ts, event_type, actor_id, actor_role, payload, prev_hash, entry_hash)
                 VALUES (1, '00000000000000000000000000', 0, 'meta',
                         '00000000000000000000000000', 'system', '{}', NULL, ?1)",
                [&genesis_hash],
            )
            .unwrap();
            head_seq = 1;
            head_hash = genesis_hash;
        }
        if include_ledger_keys {
            if let Some(algo) = ledger_algo {
                conn.execute(
                    "INSERT INTO meta VALUES('ledger_hash_algorithm', ?1)",
                    [algo],
                )
                .unwrap();
            }
            conn.execute_batch(
                "INSERT INTO meta VALUES('ledger_genesis_ulid','00000000000000000000000000');",
            )
            .unwrap();
            conn.execute(
                "INSERT INTO meta VALUES('ledger_head_seq', ?1)",
                [&head_seq.to_string()],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO meta VALUES('ledger_head_hash', ?1)",
                [&head_hash],
            )
            .unwrap();
        }
        drop(conn);
        p
    }

    fn default_v03(name: &str) -> PathBuf {
        fabricate_v03(name, 0x4C554E43, 3, "knowledge", true, Some("sha256"), true, true, true)
    }

    #[test]
    fn opens_meditations_v03_cleanly() {
        let p = meditations_path();
        if !p.exists() {
            eprintln!("skipping: {} not found", p.display());
            return;
        }
        let h = open_and_validate(&p).expect("should open Meditations v0.3 cartridge");
        let kind: String = h
            .conn
            .query_row(
                "SELECT value FROM meta WHERE key='cartridge_kind'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(kind, "knowledge");
        let fmt: String = h
            .conn
            .query_row(
                "SELECT value FROM meta WHERE key='format_version'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(fmt, "0.3");
    }

    #[test]
    fn rejects_v02_meditations_with_migrate_hint() {
        let p = meditations_v02_path();
        if !p.exists() {
            eprintln!("skipping: {} not found", p.display());
            return;
        }
        let err = expect_err(open_and_validate(&p));
        assert!(
            matches!(err, ReaderError::UnsupportedVersion { actual: 2 }),
            "got: {:?}",
            err
        );
    }

    #[test]
    fn rejects_non_sqlite() {
        let p = fabricate_non_sqlite();
        let err = expect_err(open_and_validate(&p));
        assert!(matches!(err, ReaderError::NotASqliteFile), "got: {:?}", err);
    }

    #[test]
    fn rejects_lunm_family() {
        let p = fabricate_v03(
            "fake_lunm.lun",
            0x4C554E4D,
            3,
            "knowledge",
            true,
            Some("sha256"),
            true,
            true,
            true,
        );
        let err = expect_err(open_and_validate(&p));
        match err {
            ReaderError::WrongFamily {
                actual_id,
                family_hint,
            } => {
                assert_eq!(actual_id, 0x4C554E4D);
                assert_eq!(family_hint, "LUNM");
            }
            other => panic!("expected WrongFamily(LUNM), got {:?}", other),
        }
    }

    #[test]
    fn rejects_unknown_family() {
        let p = fabricate_v03(
            "fake_unknown.lun",
            0xDEADBEEF,
            3,
            "knowledge",
            true,
            Some("sha256"),
            true,
            true,
            true,
        );
        let err = expect_err(open_and_validate(&p));
        match err {
            ReaderError::WrongFamily {
                actual_id,
                family_hint,
            } => {
                assert_eq!(actual_id, 0xDEADBEEF);
                assert_eq!(family_hint, "unknown");
            }
            other => panic!("expected WrongFamily(unknown), got {:?}", other),
        }
    }

    #[test]
    fn rejects_v01_user_version() {
        let p = fabricate_v03(
            "fake_v01.lun",
            0x4C554E43,
            1,
            "knowledge",
            false,
            None,
            false,
            false,
            false,
        );
        let err = expect_err(open_and_validate(&p));
        assert!(
            matches!(err, ReaderError::UnsupportedVersion { actual: 1 }),
            "got: {:?}",
            err
        );
    }

    #[test]
    fn rejects_v02_user_version() {
        let p = fabricate_v03(
            "fake_v02.lun",
            0x4C554E43,
            2,
            "knowledge",
            true,
            None,
            false,
            false,
            false,
        );
        let err = expect_err(open_and_validate(&p));
        assert!(
            matches!(err, ReaderError::UnsupportedVersion { actual: 2 }),
            "got: {:?}",
            err
        );
    }

    #[test]
    fn rejects_unknown_cartridge_kind() {
        let p = fabricate_v03(
            "fake_runtime_kind.lun",
            0x4C554E43,
            3,
            "runtime",
            true,
            Some("sha256"),
            true,
            true,
            true,
        );
        let err = expect_err(open_and_validate(&p));
        match err {
            ReaderError::UnsupportedCartridgeKind { actual } => assert_eq!(actual, "runtime"),
            other => panic!("expected UnsupportedCartridgeKind, got {:?}", other),
        }
    }

    #[test]
    fn rejects_missing_attribution() {
        let p = fabricate_v03(
            "fake_no_attr.lun",
            0x4C554E43,
            3,
            "knowledge",
            false,
            Some("sha256"),
            true,
            true,
            true,
        );
        let err = expect_err(open_and_validate(&p));
        match err {
            ReaderError::UnsupportedAttribution { base, attr } => {
                assert!(base.is_none());
                assert!(attr.is_none());
            }
            other => panic!("expected UnsupportedAttribution, got {:?}", other),
        }
    }

    #[test]
    fn rejects_missing_ledger_algorithm_key() {
        let p = fabricate_v03(
            "fake_no_algo.lun",
            0x4C554E43,
            3,
            "knowledge",
            true,
            None,
            true,
            true,
            true,
        );
        let err = expect_err(open_and_validate(&p));
        match err {
            ReaderError::MissingRequiredMeta { key } => assert_eq!(key, "ledger_hash_algorithm"),
            other => panic!("expected MissingRequiredMeta(ledger_hash_algorithm), got {:?}", other),
        }
    }

    #[test]
    fn rejects_unsupported_hash_algorithm() {
        let p = fabricate_v03(
            "fake_bad_algo.lun",
            0x4C554E43,
            3,
            "knowledge",
            true,
            Some("md5"),
            true,
            true,
            true,
        );
        let err = expect_err(open_and_validate(&p));
        match err {
            ReaderError::UnsupportedHashAlgorithm { actual } => assert_eq!(actual, "md5"),
            other => panic!("expected UnsupportedHashAlgorithm, got {:?}", other),
        }
    }

    #[test]
    fn rejects_missing_append_only_trigger() {
        let p = fabricate_v03(
            "fake_no_trigger.lun",
            0x4C554E43,
            3,
            "knowledge",
            true,
            Some("sha256"),
            true,
            false,
            true,
        );
        let err = expect_err(open_and_validate(&p));
        match err {
            ReaderError::LedgerIntegrity { detail } => {
                assert!(detail.contains("append-only trigger"), "got: {}", detail)
            }
            other => panic!("expected LedgerIntegrity, got {:?}", other),
        }
    }

    #[test]
    fn rejects_ledger_head_mismatch() {
        let p = default_v03("fake_head_mismatch.lun");
        // Tamper: bump ledger_head_seq beyond MAX(seq).
        let rw = RwConn::open(&p).unwrap();
        rw.execute(
            "UPDATE meta SET value = '99' WHERE key = 'ledger_head_seq'",
            [],
        )
        .unwrap();
        drop(rw);
        let err = expect_err(open_and_validate(&p));
        match err {
            ReaderError::LedgerIntegrity { detail } => {
                assert!(detail.contains("MAX(seq)"), "got: {}", detail)
            }
            other => panic!("expected LedgerIntegrity head mismatch, got {:?}", other),
        }
    }
}
