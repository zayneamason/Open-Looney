//! SPEC-004 reference composer (`composer_id = "lun.format/reference-v1"`).
//!
//! Implements the §4.3 reference algorithm exactly:
//!   - Authority: anchor_status base + anchor_method bonus + clamped logprob bonus.
//!   - Temporal:  exponential decay over max(anchored_at, latest_event_ts,
//!                meta.created_at) with 180-day half-life.
//!   - Contestation: clamped (1 - 0.30*disputes - 0.50*filtered + 0.15*reconciled).
//!   - Resonance: 1 - 0.5^distinct_actors over claim_anchored /
//!                cartridge_reviewed / cartridge_imported events.
//!
//! Returns None for any axis whose inputs are absent (never fabricate values).
//! Contestation and Resonance return None when the cartridge lacks an
//! annotation_ledger table.

use crate::error::ReaderError;
use crate::types::{TrustAxes, TrustVector};
use chrono::{DateTime, SecondsFormat, Utc};
use rusqlite::{Connection, OptionalExtension};

pub const COMPOSER_ID: &str = "lun.format/reference-v1";
pub const COMPOSER_VERSION: &str = "1.0.0";
pub const SPEC_VERSION: &str = "0.4";

const HALF_LIFE_DAYS: f64 = 180.0;

struct ExtractionRow {
    anchor_status: String,
    anchor_method: Option<String>,
    llm_logprob_sum: Option<f64>,
    llm_token_count: Option<i64>,
}

fn load_extraction(conn: &Connection, target_ulid: &str) -> Result<Option<ExtractionRow>, ReaderError> {
    let row = conn
        .query_row(
            "SELECT e.anchor_status,
                    (SELECT es.anchor_method
                     FROM extraction_sources es
                     WHERE es.extraction_ulid = e.ulid
                     ORDER BY es.anchored_at DESC NULLS LAST
                     LIMIT 1) AS anchor_method,
                    e.llm_logprob_sum,
                    e.llm_token_count
             FROM extractions e
             WHERE e.ulid = ?1",
            [target_ulid],
            |r| {
                Ok(ExtractionRow {
                    anchor_status: r.get(0)?,
                    anchor_method: r.get(1)?,
                    llm_logprob_sum: r.get(2)?,
                    llm_token_count: r.get(3)?,
                })
            },
        )
        .optional()?;
    Ok(row)
}

fn authority(row: &ExtractionRow) -> Option<f64> {
    if row.anchor_status == "unknown" {
        return None;
    }
    let base = match row.anchor_status.as_str() {
        "anchored" => 0.75,
        "synthesized" => 0.55,
        "match_failed" => 0.20,
        "filtered" => 0.10,
        _ => return None,
    };
    let method_bonus = match row.anchor_method.as_deref().unwrap_or("auto") {
        "auto" => 0.00,
        "migrated" => 0.05,
        "manual" => 0.15,
        _ => 0.00,
    };
    let logprob_bonus = match (row.llm_logprob_sum, row.llm_token_count) {
        (Some(sum), Some(n)) if n > 0 => {
            let mean = sum / n as f64;
            // mean in (-inf, 0]; clamp to [0, 0.10]
            (0.10 + mean * 0.05).clamp(0.0, 0.10)
        }
        _ => 0.0,
    };
    Some((base + method_bonus + logprob_bonus).clamp(0.0, 1.0))
}

fn has_ledger(conn: &Connection) -> Result<bool, ReaderError> {
    let count: i64 = conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'annotation_ledger'",
        [],
        |r| r.get(0),
    )?;
    Ok(count > 0)
}

fn parse_iso8601_to_ms(s: &str) -> Option<i64> {
    // Try RFC3339 first; then a few common SQLite TEXT timestamp shapes.
    if let Ok(dt) = DateTime::parse_from_rfc3339(s) {
        return Some(dt.timestamp_millis());
    }
    // Common SQLite format: "YYYY-MM-DD HH:MM:SS"
    if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S") {
        return Some(dt.and_utc().timestamp_millis());
    }
    if let Ok(d) = chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d") {
        return d
            .and_hms_opt(0, 0, 0)
            .map(|nd| nd.and_utc().timestamp_millis());
    }
    None
}

fn meta_created_at_ms(conn: &Connection) -> Result<Option<i64>, ReaderError> {
    let raw: Option<String> = conn
        .query_row(
            "SELECT value FROM meta WHERE key = 'created_at'",
            [],
            |r| r.get(0),
        )
        .optional()?;
    Ok(raw.as_deref().and_then(parse_iso8601_to_ms))
}

fn anchored_at_ms(conn: &Connection, target_ulid: &str) -> Result<Option<i64>, ReaderError> {
    let row: Option<Option<i64>> = conn
        .query_row(
            "SELECT MAX(anchored_at) FROM extraction_sources WHERE extraction_ulid = ?1",
            [target_ulid],
            |r| r.get::<_, Option<i64>>(0),
        )
        .optional()?;
    Ok(row.flatten())
}

fn latest_event_ts_ms(conn: &Connection, target_ulid: &str) -> Result<Option<i64>, ReaderError> {
    if !has_ledger(conn)? {
        return Ok(None);
    }
    let row: Option<Option<i64>> = conn
        .query_row(
            "SELECT MAX(entry_ts) FROM annotation_ledger WHERE target_ulid = ?1",
            [target_ulid],
            |r| r.get::<_, Option<i64>>(0),
        )
        .optional()?;
    Ok(row.flatten())
}

fn temporal(
    conn: &Connection,
    target_ulid: &str,
    now_ms: i64,
) -> Result<Option<f64>, ReaderError> {
    let anchored = anchored_at_ms(conn, target_ulid)?;
    let latest = latest_event_ts_ms(conn, target_ulid)?;
    let created = meta_created_at_ms(conn)?;
    let most_recent = [anchored, latest, created]
        .into_iter()
        .flatten()
        .max();
    let Some(most_recent_ms) = most_recent else {
        return Ok(None);
    };
    let age_ms = (now_ms - most_recent_ms).max(0) as f64;
    let age_days = age_ms / (1000.0 * 60.0 * 60.0 * 24.0);
    let v = 0.5f64.powf(age_days / HALF_LIFE_DAYS);
    Ok(Some(v.clamp(0.0, 1.0)))
}

fn contestation(conn: &Connection, target_ulid: &str) -> Result<Option<f64>, ReaderError> {
    if !has_ledger(conn)? {
        return Ok(None);
    }
    let mut stmt = conn.prepare(
        "SELECT event_type, COUNT(*) FROM annotation_ledger
         WHERE target_ulid = ?1
           AND event_type IN ('claim_disputed', 'summary_overridden', 'claim_filtered', 'claim_reconciled')
         GROUP BY event_type",
    )?;
    let mut n_disputes: i64 = 0;
    let mut n_filtered: i64 = 0;
    let mut n_reconciled: i64 = 0;
    let rows = stmt.query_map([target_ulid], |r| {
        Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
    })?;
    for row in rows {
        let (event_type, count) = row?;
        match event_type.as_str() {
            "claim_disputed" | "summary_overridden" => n_disputes += count,
            "claim_filtered" => n_filtered += count,
            "claim_reconciled" => n_reconciled += count,
            _ => {}
        }
    }
    let raw = 1.0
        - 0.30 * n_disputes as f64
        - 0.50 * n_filtered as f64
        + 0.15 * n_reconciled as f64;
    Ok(Some(raw.clamp(0.0, 1.0)))
}

fn resonance(conn: &Connection, target_ulid: &str) -> Result<Option<f64>, ReaderError> {
    if !has_ledger(conn)? {
        return Ok(None);
    }
    let distinct: i64 = conn.query_row(
        "SELECT COUNT(DISTINCT actor_id) FROM annotation_ledger
         WHERE target_ulid = ?1
           AND event_type IN ('claim_anchored', 'cartridge_reviewed', 'cartridge_imported')",
        [target_ulid],
        |r| r.get(0),
    )?;
    let v = if distinct == 0 {
        0.0
    } else {
        1.0 - 0.5f64.powi(distinct as i32)
    };
    Ok(Some(v.clamp(0.0, 1.0)))
}

fn current_ms() -> i64 {
    Utc::now().timestamp_millis()
}

fn computed_at_now() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true)
}

pub fn compose(conn: &Connection, target_ulid: &str) -> Result<TrustVector, ReaderError> {
    compose_at(conn, target_ulid, current_ms(), computed_at_now())
}

fn compose_at(
    conn: &Connection,
    target_ulid: &str,
    now_ms: i64,
    computed_at: String,
) -> Result<TrustVector, ReaderError> {
    let row = load_extraction(conn, target_ulid)?;
    let authority_axis = row.as_ref().and_then(authority);
    let temporal_axis = temporal(conn, target_ulid, now_ms)?;
    let contestation_axis = contestation(conn, target_ulid)?;
    let resonance_axis = resonance(conn, target_ulid)?;
    Ok(TrustVector {
        spec_version: SPEC_VERSION.to_string(),
        composer_id: COMPOSER_ID.to_string(),
        composer_version: COMPOSER_VERSION.to_string(),
        target_ulid: target_ulid.to_string(),
        computed_at,
        axes: TrustAxes {
            authority: authority_axis,
            contestation: contestation_axis,
            temporal: temporal_axis,
            resonance: resonance_axis,
        },
        notes: None,
    })
}

pub fn compose_batch(
    conn: &Connection,
    target_ulids: &[String],
) -> Result<Vec<TrustVector>, ReaderError> {
    let now_ms = current_ms();
    let computed_at = computed_at_now();
    let mut out = Vec::with_capacity(target_ulids.len());
    for ulid in target_ulids {
        out.push(compose_at(conn, ulid, now_ms, computed_at.clone())?);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cartridge::open_and_validate;
    use std::path::PathBuf;

    fn meditations() -> Option<crate::cartridge::CartridgeHandle> {
        let p = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .join("..")
            .join("07_Sample_Cartridges")
            .join("Marcus-Aurelius-Meditations.v03.lun");
        if !p.exists() {
            eprintln!("skip: {} not found", p.display());
            return None;
        }
        Some(open_and_validate(&p).expect("open Meditations v0.3"))
    }

    fn pick_one(conn: &Connection, sql: &str) -> Option<String> {
        conn.query_row(sql, [], |r| r.get::<_, String>(0)).ok()
    }

    #[test]
    fn anchored_claim_composes_to_authority_about_0_75() {
        let Some(h) = meditations() else { return };
        let ulid = pick_one(
            &h.conn,
            "SELECT ulid FROM extractions
             WHERE type='claim' AND anchor_status='anchored'
             ORDER BY ulid LIMIT 1",
        )
        .unwrap();
        let v = compose(&h.conn, &ulid).unwrap();
        let auth = v.axes.authority.expect("authority should be Some");
        // anchor_method='auto' + llm + NULL logprob → 0.75 + 0.00 + 0.00 = 0.75
        assert!((auth - 0.75).abs() < 1e-9, "expected ~0.75, got {}", auth);
        assert_eq!(v.composer_id, "lun.format/reference-v1");
        assert_eq!(v.composer_version, "1.0.0");
        assert_eq!(v.spec_version, "0.4");
        assert_eq!(v.target_ulid, ulid);
    }

    #[test]
    fn match_failed_claim_composes_to_authority_about_0_20() {
        let Some(h) = meditations() else { return };
        let ulid = pick_one(
            &h.conn,
            "SELECT ulid FROM extractions
             WHERE type='claim' AND anchor_status='match_failed'
             ORDER BY ulid LIMIT 1",
        )
        .unwrap();
        let v = compose(&h.conn, &ulid).unwrap();
        let auth = v.axes.authority.expect("authority should be Some");
        assert!((auth - 0.20).abs() < 1e-9, "expected ~0.20, got {}", auth);
    }

    #[test]
    fn entity_composes_to_authority_none() {
        let Some(h) = meditations() else { return };
        let ulid = pick_one(
            &h.conn,
            "SELECT ulid FROM extractions
             WHERE type='entity' AND anchor_status='unknown'
             ORDER BY ulid LIMIT 1",
        )
        .unwrap();
        let v = compose(&h.conn, &ulid).unwrap();
        assert!(v.axes.authority.is_none(), "authority should be None for entity");
    }

    #[test]
    fn meditations_genesis_only_means_contestation_none_resonance_none() {
        let Some(h) = meditations() else { return };
        let ulid = pick_one(
            &h.conn,
            "SELECT ulid FROM extractions
             WHERE type='claim' AND anchor_status='anchored'
             ORDER BY ulid LIMIT 1",
        )
        .unwrap();
        let v = compose(&h.conn, &ulid).unwrap();
        // Ledger exists but no claim_anchored / etc events target this ULID.
        // Resonance should be 0.0 (or asymptotic 0 since distinct_actors=0).
        // Contestation should be Some(1.0) (no disputes, filters, or reconciliations
        // target this ULID, so the raw formula is 1.0).
        assert_eq!(v.axes.resonance, Some(0.0));
        assert_eq!(v.axes.contestation, Some(1.0));
    }

    #[test]
    fn axes_in_range_or_none() {
        let Some(h) = meditations() else { return };
        let ulids: Vec<String> = {
            let mut stmt = h
                .conn
                .prepare("SELECT ulid FROM extractions ORDER BY ulid LIMIT 50")
                .unwrap();
            stmt.query_map([], |r| r.get::<_, String>(0))
                .unwrap()
                .map(|r| r.unwrap())
                .collect()
        };
        for ulid in &ulids {
            let v = compose(&h.conn, ulid).unwrap();
            for (name, value) in [
                ("authority", v.axes.authority),
                ("contestation", v.axes.contestation),
                ("temporal", v.axes.temporal),
                ("resonance", v.axes.resonance),
            ] {
                if let Some(x) = value {
                    assert!(
                        (0.0..=1.0).contains(&x),
                        "{} = {} out of [0,1] for {}",
                        name,
                        x,
                        ulid
                    );
                }
            }
        }
    }

    #[test]
    fn determinism_two_calls_match_axes() {
        let Some(h) = meditations() else { return };
        let ulid = pick_one(
            &h.conn,
            "SELECT ulid FROM extractions WHERE type='claim' ORDER BY ulid LIMIT 1",
        )
        .unwrap();
        let v1 = compose(&h.conn, &ulid).unwrap();
        let v2 = compose(&h.conn, &ulid).unwrap();
        // computed_at can differ by seconds; axes must match exactly.
        assert_eq!(v1.axes.authority, v2.axes.authority);
        assert_eq!(v1.axes.contestation, v2.axes.contestation);
        assert_eq!(v1.axes.resonance, v2.axes.resonance);
        // Temporal can drift between calls if seconds tick over. Use a tiny epsilon.
        match (v1.axes.temporal, v2.axes.temporal) {
            (Some(a), Some(b)) => assert!((a - b).abs() < 1e-6),
            (None, None) => {}
            other => panic!("temporal mismatch: {:?}", other),
        }
    }

    #[test]
    fn batch_matches_single_call_results() {
        let Some(h) = meditations() else { return };
        let ulids: Vec<String> = {
            let mut stmt = h
                .conn
                .prepare("SELECT ulid FROM extractions ORDER BY ulid LIMIT 10")
                .unwrap();
            stmt.query_map([], |r| r.get::<_, String>(0))
                .unwrap()
                .map(|r| r.unwrap())
                .collect()
        };
        let batch = compose_batch(&h.conn, &ulids).unwrap();
        assert_eq!(batch.len(), ulids.len());
        for (i, ulid) in ulids.iter().enumerate() {
            let single = compose(&h.conn, ulid).unwrap();
            assert_eq!(single.axes.authority, batch[i].axes.authority);
            assert_eq!(single.axes.contestation, batch[i].axes.contestation);
            assert_eq!(single.axes.resonance, batch[i].axes.resonance);
            assert_eq!(single.target_ulid, batch[i].target_ulid);
        }
    }

    #[test]
    fn manual_anchored_claim_composes_to_authority_about_0_90() {
        // Slice #2 fixture: an ambassador upgrade flips anchor_method to 'manual',
        // which SPEC-004's reference composer rewards with +0.15. Test against an
        // in-memory cartridge so we don't depend on /tmp/meditations-slice2-test.lun.
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
             CREATE TABLE doc_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ulid TEXT NOT NULL UNIQUE,
                parent_ulid TEXT, type TEXT NOT NULL,
                position INTEGER NOT NULL, content TEXT, meta_json TEXT
             );
             CREATE TABLE extractions (
                ulid TEXT PRIMARY KEY, type TEXT NOT NULL, content TEXT NOT NULL,
                anchor_status TEXT NOT NULL DEFAULT 'unknown',
                anchor_reason TEXT,
                llm_logprob_sum REAL, llm_token_count INTEGER,
                extraction_method TEXT NOT NULL DEFAULT 'llm'
             ) WITHOUT ROWID;
             CREATE TABLE extraction_sources (
                extraction_ulid TEXT NOT NULL, node_ulid TEXT NOT NULL,
                anchor_method TEXT NOT NULL DEFAULT 'auto',
                anchored_by TEXT, anchored_at INTEGER, event_id TEXT,
                PRIMARY KEY (extraction_ulid, node_ulid)
             ) WITHOUT ROWID;",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO doc_nodes (ulid, type, position, content)
             VALUES ('01HQXY00000000000000000NOD', 'sentence', 0, 'text')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO extractions (ulid, type, content, anchor_status)
             VALUES ('01HQXY00000000000000000ANC', 'claim', 'c', 'anchored')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO extraction_sources (extraction_ulid, node_ulid, anchor_method, anchored_by, anchored_at, event_id)
             VALUES ('01HQXY00000000000000000ANC', '01HQXY00000000000000000NOD', 'manual', '01HQXY00000000000000ACTOR1', 0, 'deadbeef')",
            [],
        )
        .unwrap();

        let v = compose(&conn, "01HQXY00000000000000000ANC").unwrap();
        let auth = v.axes.authority.expect("authority Some");
        assert!((auth - 0.90).abs() < 1e-9, "expected 0.90 for manual-method anchor, got {}", auth);
    }
}
