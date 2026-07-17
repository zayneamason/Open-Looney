//! SPEC-007 cartridge sketches consumer (`SketchedShelf`).
//!
//! Mirrors the engine's `luna.cartridge.sketches` Python module on the read
//! side. Holds a small set of cartridges open in read-only mode, lazily
//! loads their `sketches` tables, and answers "could cartridge X contain
//! item Y of kind Z?" without scanning the cartridge.
//!
//! Per SPEC-007 § 7.3.1: a positive answer is *probable*, requiring
//! verify-by-opening. A negative answer is *definite*. This module only
//! returns the probable/unknown set; verification is the caller's job.
//!
//! Hash family: murmur3_x64_128 with Kirsch-Mitzenmacher double hashing,
//! h1 = low 64 bits, h2 = high 64 bits (§ 7.1.2). Bitset is LSB-first
//! within each byte (§ 7.1.3). Defensive OOB bit-index guard per Q7.

use crate::cartridge::open_and_validate;
use crate::error::ReaderError;
use crate::queries;
use rusqlite::{Connection, OptionalExtension};
use std::collections::HashMap;
use std::io::Cursor;
use std::path::{Path, PathBuf};
use unicode_normalization::UnicodeNormalization;

// --- Constants (mirror engine SPEC-007 contract) ----------------------------

pub const HASH_FAMILY: &str = "murmur3_x64_128";
pub const MAX_NUM_HASHES: i64 = 32;
pub const KNOWN_KINDS: &[&str] = &[
    "extraction_ulid",
    "node_ulid",
    "entity_surface",
    "fts_term",
];

// --- Sketch kind (typed Tauri surface) -------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SketchKind {
    ExtractionUlid,
    NodeUlid,
    EntitySurface,
    FtsTerm,
}

impl SketchKind {
    pub fn as_str(self) -> &'static str {
        match self {
            SketchKind::ExtractionUlid => "extraction_ulid",
            SketchKind::NodeUlid => "node_ulid",
            SketchKind::EntitySurface => "entity_surface",
            SketchKind::FtsTerm => "fts_term",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "extraction_ulid" => Some(SketchKind::ExtractionUlid),
            "node_ulid" => Some(SketchKind::NodeUlid),
            "entity_surface" => Some(SketchKind::EntitySurface),
            "fts_term" => Some(SketchKind::FtsTerm),
            _ => None,
        }
    }
}

// --- Sketch row + Tauri DTOs ------------------------------------------------

#[derive(Debug, Clone)]
pub struct SketchRow {
    pub sketch_kind: String,
    pub sketch_version: i64,
    pub hash_family: String,
    pub num_hashes: i64,
    pub num_bits: i64,
    #[allow(dead_code)] // diagnostic-only; not required for membership query
    pub num_inserted: i64,
    pub seed: u32,
    pub bitset: Vec<u8>,
}

#[derive(Debug, serde::Serialize)]
pub struct ShelfSummary {
    pub count: usize,
    pub paths: Vec<String>,
    /// Per-cartridge populated sketch kinds (alphabetized, from
    /// meta.sketches_present). Empty inner vec ⇒ cartridge has no sketches.
    pub sketches_per_cartridge: Vec<Vec<String>>,
}

#[derive(Debug, Clone, serde::Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum CandidateStatus {
    /// All k bits set; verify-by-opening required to confirm.
    Probable,
    /// Cartridge has no sketch of this kind; consumer must fall back.
    Unknown,
    /// Probable + verify-by-opening confirmed the item exists in the cartridge.
    Confirmed,
    /// Probable per sketch, but verify-by-opening did not find the item — the
    /// bloom-filter false-positive case. Surfaced as a data-quality signal
    /// rather than dropped silently.
    FalsePositive,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct CandidateResult {
    pub path: String,
    #[serde(flatten)]
    pub status: CandidateStatus,
}

// --- Hashing (SPEC-007 § 7.1.2) --------------------------------------------

/// Split MurmurHash3_x64_128 into (h1, h2) where h1 is the low 64 bits and
/// h2 is the high 64 bits. All consumers MUST agree on this split.
fn split_hash(item: &[u8], seed: u32) -> (u64, u64) {
    let digest = murmur3::murmur3_x64_128(&mut Cursor::new(item), seed)
        .expect("murmur3 over in-memory cursor never fails");
    let h1 = digest as u64;
    let h2 = (digest >> 64) as u64;
    (h1, h2)
}

/// Kirsch–Mitzenmacher: bit_index_i = (h1 + i * h2) mod num_bits.
/// Promoted to u128 because `i * h2` can overflow u64 even for small i
/// (h2 is a full 64-bit hash word); u64 wrapping produces different bit
/// indices than Python's arbitrary-precision `(h1 + i*h2) % m`, causing
/// false-negative membership reads against engine-built sketches.
fn bit_indices(item: &[u8], seed: u32, num_hashes: i64, num_bits: i64) -> Vec<u64> {
    let (h1, h2) = split_hash(item, seed);
    let h1_128 = h1 as u128;
    let h2_128 = h2 as u128;
    let m_128 = num_bits as u128;
    (0..num_hashes as u128)
        .map(|i| ((h1_128 + i * h2_128) % m_128) as u64)
        .collect()
}

// --- Bitset (SPEC-007 § 7.1.3 — LSB-first) ---------------------------------

fn bit_is_set(bitset: &[u8], bit_index: u64) -> bool {
    let byte_idx = (bit_index >> 3) as usize;
    let bit_off = (bit_index & 7) as u8;
    bitset[byte_idx] & (1u8 << bit_off) != 0
}

// --- Normalization (SPEC-007 § 7.1.1) --------------------------------------

/// Raw 26-char ULID — already canonical per SPEC-002.
fn normalize_ulid(s: &str) -> Vec<u8> {
    s.as_bytes().to_vec()
}

/// NFKC + Unicode default casefold + strip leading/trailing whitespace.
fn normalize_entity_surface(s: &str) -> Vec<u8> {
    let nfkc: String = s.nfkc().collect();
    nfkc.to_lowercase().trim().as_bytes().to_vec()
}

/// Lowercase + UTF-8 encode. Matches the engine's `normalize_fts_term`
/// shortcut; the canonical FTS5 unicode61 tokenization is more involved
/// but for membership queries over short ASCII terms the shortcut is
/// equivalent.
fn normalize_fts_term(s: &str) -> Vec<u8> {
    s.to_lowercase().as_bytes().to_vec()
}

fn normalize_for_kind(kind: SketchKind, raw: &str) -> Vec<u8> {
    match kind {
        SketchKind::ExtractionUlid | SketchKind::NodeUlid => normalize_ulid(raw),
        SketchKind::EntitySurface => normalize_entity_surface(raw),
        SketchKind::FtsTerm => normalize_fts_term(raw),
    }
}

// --- Membership query (SPEC-007 § 7.3.1) -----------------------------------

/// Returns true iff every Kirsch–Mitzenmacher bit is set. False means
/// "definitely not in cartridge"; true means "probable, verify by opening."
/// Includes the Q7 OOB guard: if any computed index falls outside the
/// bitset (e.g. corrupted row with num_bits = 0), treat the sketch as
/// malformed and return false.
pub fn sketch_contains(row: &SketchRow, item: &[u8]) -> bool {
    if row.num_bits <= 0 || row.num_hashes <= 0 {
        return false;
    }
    for bit in bit_indices(item, row.seed, row.num_hashes, row.num_bits) {
        let total_bits = (row.bitset.len() as u64) * 8;
        if bit >= row.num_bits as u64 || bit >= total_bits {
            return false;
        }
        if !bit_is_set(&row.bitset, bit) {
            return false;
        }
    }
    true
}

// --- ShelfCartridge ---------------------------------------------------------

pub struct ShelfCartridge {
    pub path: PathBuf,
    pub conn: Connection,
    pub sketches: HashMap<String, SketchRow>,
    pub sketches_present: Vec<String>,
    #[allow(dead_code)] // surfaced via ShelfSummary diagnostics only
    pub fts_tokenizer_config: Option<String>,
}

/// Open a cartridge into the shelf: run the standard 7-step open contract,
/// then read meta.sketches_present + meta.fts_tokenizer_config + the
/// sketches table. Cartridges with no sketches open cleanly with an empty
/// sketches map (graceful absence per SPEC-007 § 7.5).
pub fn open_shelf_cartridge(path: &Path) -> Result<ShelfCartridge, ReaderError> {
    let handle = open_and_validate(path)?;
    let conn = handle.conn;

    let sketches_present_csv: Option<String> = conn
        .query_row(
            "SELECT value FROM meta WHERE key = 'sketches_present'",
            [],
            |r| r.get(0),
        )
        .optional()?;
    let sketches_present: Vec<String> = sketches_present_csv
        .as_deref()
        .map(|s| s.split(',').map(|x| x.to_string()).collect())
        .unwrap_or_default();
    let fts_tokenizer_config: Option<String> = conn
        .query_row(
            "SELECT value FROM meta WHERE key = 'fts_tokenizer_config'",
            [],
            |r| r.get(0),
        )
        .optional()?;

    let mut sketches: HashMap<String, SketchRow> = HashMap::new();
    if !sketches_present.is_empty() {
        let mut stmt = conn.prepare(
            "SELECT sketch_kind, sketch_version, hash_family, num_hashes, \
                    num_bits, num_inserted, seed, bitset \
             FROM sketches",
        )?;
        let rows = stmt.query_map([], |r| {
            let seed_i64: i64 = r.get(6)?;
            Ok(SketchRow {
                sketch_kind: r.get(0)?,
                sketch_version: r.get(1)?,
                hash_family: r.get(2)?,
                num_hashes: r.get(3)?,
                num_bits: r.get(4)?,
                num_inserted: r.get(5)?,
                seed: seed_i64 as u32,
                bitset: r.get(7)?,
            })
        })?;
        for row in rows {
            let row = row?;
            // Graceful skip on unknown hash family; cartridge open does not fail.
            if row.hash_family != HASH_FAMILY {
                continue;
            }
            if !(1..=MAX_NUM_HASHES).contains(&row.num_hashes) {
                continue;
            }
            if row.num_bits <= 0 || row.num_bits % 8 != 0 {
                continue;
            }
            let expected_bytes = (row.num_bits / 8) as usize;
            if row.bitset.len() != expected_bytes {
                continue;
            }
            sketches.insert(row.sketch_kind.clone(), row);
        }
    }

    Ok(ShelfCartridge {
        path: path.to_path_buf(),
        conn,
        sketches,
        sketches_present,
        fts_tokenizer_config,
    })
}

// --- Shelf-level filter -----------------------------------------------------

pub fn filter_candidates(
    shelf: &[ShelfCartridge],
    item: &str,
    kind: SketchKind,
) -> Vec<CandidateResult> {
    let kind_str = kind.as_str();
    let needle = normalize_for_kind(kind, item);
    let mut out = Vec::with_capacity(shelf.len());
    for cart in shelf {
        let path_str = cart.path.to_string_lossy().into_owned();
        match cart.sketches.get(kind_str) {
            None => out.push(CandidateResult {
                path: path_str,
                status: CandidateStatus::Unknown,
            }),
            Some(row) => {
                if sketch_contains(row, &needle) {
                    out.push(CandidateResult {
                        path: path_str,
                        status: CandidateStatus::Probable,
                    });
                }
                // definitely-not ⇒ omitted from result list
            }
        }
    }
    out
}

// --- Verify-by-opening (SPEC-007 § 7.3.3) ----------------------------------

/// Open the cartridge at `path` and run the precise query for the given
/// `kind` to either confirm the candidate (item exists) or downgrade it to
/// a bloom-filter false positive (item absent). Per SPEC-007 § 7.3.3 the
/// sketch is a probabilistic pre-filter; this is the verification pass.
///
/// Returns `Confirmed` or `FalsePositive`. `Probable` and `Unknown` are
/// outputs of `filter_candidates`, never of this function. The caller is
/// expected to skip verification entirely for `Unknown` candidates (a
/// cartridge with no sketch of this kind cannot be pre-filtered, so the
/// sketch decision and the verify decision are the same lookup).
///
/// Opens the cartridge with the standard 7-step open contract, runs ONE
/// precise query, then drops the handle. ~50 ms per cartridge in the
/// Meditations-scale baseline.
pub fn verify_candidate(
    path: &Path,
    item: &str,
    kind: SketchKind,
) -> Result<CandidateStatus, ReaderError> {
    let handle = open_and_validate(path)?;
    let confirmed = match kind {
        SketchKind::ExtractionUlid => queries::get_extraction(&handle.conn, item)?.is_some(),
        SketchKind::NodeUlid => {
            // Cheap existence probe; avoids the parent-chain walk that
            // queries::get_node would do.
            handle
                .conn
                .query_row(
                    "SELECT 1 FROM doc_nodes WHERE ulid = ?1 LIMIT 1",
                    [item],
                    |r| r.get::<_, i64>(0),
                )
                .optional()?
                .is_some()
        }
        SketchKind::FtsTerm => !queries::search(&handle.conn, item, 1)?.is_empty(),
        SketchKind::EntitySurface => {
            queries::find_extraction_by_content(&handle.conn, item)?.is_some()
        }
    };
    Ok(if confirmed {
        CandidateStatus::Confirmed
    } else {
        CandidateStatus::FalsePositive
    })
}

// ===========================================================================
// Tests
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn meditations_path() -> Option<PathBuf> {
        let p = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .join("..")
            .join("07_Sample_Cartridges")
            .join("Marcus-Aurelius-Meditations.v03.lun");
        if p.exists() {
            Some(p)
        } else {
            eprintln!("skip: {} not found", p.display());
            None
        }
    }

    #[test]
    fn split_hash_low_high_split() {
        let (h1, h2) = split_hash(b"hello", 0);
        let full = murmur3::murmur3_x64_128(&mut Cursor::new(b"hello".as_ref()), 0).unwrap();
        assert_eq!(h1 as u128 | ((h2 as u128) << 64), full);
    }

    #[test]
    fn split_hash_deterministic() {
        assert_eq!(split_hash(b"item-x", 0), split_hash(b"item-x", 0));
    }

    #[test]
    fn bit_indices_in_range() {
        let idxs = bit_indices(b"x", 0, 7, 1024);
        assert_eq!(idxs.len(), 7);
        assert!(idxs.iter().all(|&i| i < 1024));
    }

    /// Python↔Rust parity for the Kirsch–Mitzenmacher math. The values
    /// below were computed by the engine's `luna.cartridge.sketches`
    /// module against the canonical Meditations cartridge. The previous
    /// u64-wrapping implementation diverged from these values for i ≥ 3
    /// (because `i * h2` overflows u64 well before `(i * h2) % m` would),
    /// producing false-negative membership reads against engine-built
    /// sketches. This regression test pins the math to a known-good
    /// reference vector so the bug can't sneak back.
    #[test]
    fn bit_indices_python_parity_virtue_seed0() {
        let (h1, h2) = split_hash(b"virtue", 0);
        assert_eq!(h1, 2065860924000221294);
        assert_eq!(h2, 6582964320913773025);
        let idxs = bit_indices(b"virtue", 0, 7, 65592);
        assert_eq!(idxs, vec![54278, 48879, 43480, 38081, 32682, 27283, 21884]);
    }

    #[test]
    fn bit_is_set_lsb_layout() {
        // Bit 0 = LSB of byte 0; bit 7 = MSB of byte 0; bit 8 = LSB of byte 1.
        let bs = vec![0x81u8, 0x01u8]; // bits 0, 7, 8 set
        assert!(bit_is_set(&bs, 0));
        assert!(!bit_is_set(&bs, 1));
        assert!(bit_is_set(&bs, 7));
        assert!(bit_is_set(&bs, 8));
        assert!(!bit_is_set(&bs, 9));
    }

    #[test]
    fn normalize_entity_surface_nfkc_casefold_strip() {
        assert_eq!(
            normalize_entity_surface("  Marcus Aurelius  "),
            b"marcus aurelius".to_vec()
        );
        // Full-width Latin ⇒ NFKC normalizes to ASCII.
        assert_eq!(
            normalize_entity_surface("Ｓｔｏｉｃｉｓｍ"),
            b"stoicism".to_vec()
        );
    }

    #[test]
    fn normalize_fts_term_lowercases() {
        assert_eq!(normalize_fts_term("Virtue"), b"virtue".to_vec());
    }

    #[test]
    fn sketch_contains_no_false_negatives() {
        // Hand-build a tiny sketch: insert one known item, then test it.
        let item = b"01HQXY0000000000000000000A";
        let m: i64 = 256;
        let k: i64 = 7;
        let seed: u32 = 0;
        let mut bs = vec![0u8; (m / 8) as usize];
        for bit in bit_indices(item, seed, k, m) {
            bs[(bit >> 3) as usize] |= 1u8 << (bit & 7);
        }
        let row = SketchRow {
            sketch_kind: "extraction_ulid".into(),
            sketch_version: 1,
            hash_family: HASH_FAMILY.into(),
            num_hashes: k,
            num_bits: m,
            num_inserted: 1,
            seed,
            bitset: bs,
        };
        assert!(sketch_contains(&row, item));
    }

    #[test]
    fn sketch_contains_rejects_definite_miss() {
        // Empty bitset ⇒ every membership check returns false (unless k=0,
        // which is not allowed). Verifies the basic "definitely not" path.
        let row = SketchRow {
            sketch_kind: "extraction_ulid".into(),
            sketch_version: 1,
            hash_family: HASH_FAMILY.into(),
            num_hashes: 7,
            num_bits: 256,
            num_inserted: 0,
            seed: 0,
            bitset: vec![0u8; 32],
        };
        assert!(!sketch_contains(&row, b"anything"));
    }

    #[test]
    fn sketch_contains_oob_guard_returns_false() {
        let row = SketchRow {
            sketch_kind: "extraction_ulid".into(),
            sketch_version: 1,
            hash_family: HASH_FAMILY.into(),
            num_hashes: 7,
            num_bits: 0, // malformed
            num_inserted: 0,
            seed: 0,
            bitset: vec![],
        };
        assert!(!sketch_contains(&row, b"anything"));
    }

    #[test]
    fn open_shelf_cartridge_meditations() {
        let Some(p) = meditations_path() else { return };
        // Meditations v0.3 has no sketches table (built before SPEC-007 landed),
        // so open succeeds with empty sketches map + empty sketches_present.
        let sc = open_shelf_cartridge(&p).expect("open Meditations v0.3");
        assert_eq!(sc.path, p);
        // Note: if the reference cartridge is ever rebuilt against c20673d+,
        // sketches_present will be non-empty. Both states are valid.
    }

    /// Build a fresh v0.3 cartridge in tmp, populate sketches manually, then
    /// open it via open_shelf_cartridge and confirm sketches roundtrip.
    #[test]
    fn open_shelf_cartridge_with_sketches() {
        use rusqlite::Connection as RConn;
        let tmp = std::env::temp_dir().join(format!(
            "shelf_test_{}.lun",
            std::process::id()
        ));
        let _ = std::fs::remove_file(&tmp);
        // Build a minimal v0.3 cartridge with the canonical schema.
        let conn = RConn::open(&tmp).unwrap();
        let schema = std::fs::read_to_string(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("..")
                .join("..")
                .join("..")
                .join("..")
                .join("_LunaEngine_BetaProject_V2.0_Root")
                .join("src/luna/cartridge/schema.py"),
        )
        .ok();
        let Some(schema_py) = schema else {
            eprintln!("skip: engine schema.py not found; cannot build fixture");
            return;
        };
        // Extract LUN_SCHEMA = """\...""" block from schema.py.
        let lun_schema = schema_py
            .split("LUN_SCHEMA = \"\"\"\\\n")
            .nth(1)
            .and_then(|after| after.split("\"\"\"").next())
            .unwrap_or("");
        conn.execute_batch(lun_schema).unwrap();
        conn.execute("PRAGMA application_id = 1280659011", []).unwrap();
        conn.execute("PRAGMA user_version = 3", []).unwrap();
        // Minimal meta + ledger genesis row (synthetic; reader only needs the
        // shape, not the cryptographic chain — open_and_validate will check
        // the ledger head pointer though).
        for (k, v) in [
            ("format_version", "0.3"),
            ("cartridge_kind", "knowledge"),
            ("source_hash", &"a".repeat(64)[..]),
            ("source_filename", "fixture.md"),
            ("source_format", "markdown"),
            ("logprob_base", "e"),
            ("logprob_attribution", "response_level"),
            ("ledger_hash_algorithm", "sha256"),
            ("sketches_present", "extraction_ulid"),
        ] {
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?1, ?2)",
                rusqlite::params![k, v],
            )
            .unwrap();
        }
        // Insert one extraction_ulid sketch.
        let m: i64 = 256;
        let k: i64 = 7;
        let mut bs = vec![0u8; (m / 8) as usize];
        for bit in bit_indices(b"01HQXY0000000000000000000A", 0, k, m) {
            bs[(bit >> 3) as usize] |= 1u8 << (bit & 7);
        }
        conn.execute(
            "INSERT INTO sketches (sketch_kind, sketch_version, hash_family, \
             num_hashes, num_bits, num_inserted, seed, bitset, builder_version, notes) \
             VALUES ('extraction_ulid', 1, 'murmur3_x64_128', 7, 256, 1, 0, ?1, NULL, NULL)",
            rusqlite::params![bs],
        )
        .unwrap();
        // Genesis ledger row + head meta. Use sha256 hex placeholder; reader
        // validates head pointer matches MAX(seq) entry_hash, so insert
        // consistently.
        let entry_hash = "f".repeat(64);
        conn.execute(
            "INSERT INTO annotation_ledger (seq, ulid, entry_ts, event_type, \
             actor_id, actor_role, target_kind, target_ulid, target_cartridge_ulid, \
             payload, prev_hash, entry_hash) \
             VALUES (1, '01HQXY00000000000000000000', 0, 'meta', \
             '01HQ000000000000000000SYSTEM', 'system', NULL, NULL, NULL, '{}', NULL, ?1)",
            rusqlite::params![entry_hash],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('ledger_head_seq', '1'), \
             ('ledger_head_hash', ?1), ('ledger_genesis_ulid', '01HQXY00000000000000000000')",
            rusqlite::params![entry_hash],
        )
        .unwrap();
        conn.close().unwrap();

        // Now exercise the shelf open path.
        let sc = open_shelf_cartridge(&tmp).expect("open fixture");
        assert_eq!(sc.sketches_present, vec!["extraction_ulid".to_string()]);
        assert!(sc.sketches.contains_key("extraction_ulid"));
        let row = sc.sketches.get("extraction_ulid").unwrap();
        assert!(sketch_contains(row, b"01HQXY0000000000000000000A"));
        assert!(!sketch_contains(row, b"01ZZZZ00000000000000000000"));

        // filter_candidates over a 1-cartridge shelf.
        let cands =
            filter_candidates(&[sc], "01HQXY0000000000000000000A", SketchKind::ExtractionUlid);
        assert_eq!(cands.len(), 1);
        match cands[0].status {
            CandidateStatus::Probable => {}
            _ => panic!("expected Probable for inserted ULID"),
        }
        let cands_miss = filter_candidates(
            // re-open since filter_candidates moves the shelf vec? no — borrows.
            &[open_shelf_cartridge(&tmp).expect("reopen fixture")],
            "01ZZZZ00000000000000000000",
            SketchKind::ExtractionUlid,
        );
        assert!(cands_miss.is_empty(), "definite miss should be omitted");

        let _ = std::fs::remove_file(&tmp);
    }

    // --- Verify-by-opening fixture + tests (SPEC-007 § 7.3.3) --------------

    const KNOWN_EXTRACTION_ULID: &str = "01HQXY0000000000000000VRFY";
    const ABSENT_EXTRACTION_ULID: &str = "01ZZZZZZZZZZZZZZZZZZZZZZZZ";
    const KNOWN_NODE_ULID: &str = "01HQXY00000000000000NODEAA";
    const ABSENT_NODE_ULID: &str = "01ZZZZ00000000000000NODENO";
    const KNOWN_ENTITY_CONTENT: &str = "Plato [person]";
    const KNOWN_FTS_TERM: &str = "virtue";
    const ABSENT_FTS_TERM: &str = "zzzzqqqq";

    /// Build a v0.3 fixture cartridge populated with: one claim extraction
    /// (KNOWN_EXTRACTION_ULID), one entity extraction (KNOWN_ENTITY_CONTENT),
    /// one doc_node (KNOWN_NODE_ULID, content containing KNOWN_FTS_TERM), and
    /// sketches for all four kinds populated against these items. Returns
    /// `Some(path)` if the engine schema is available; `None` (skip) otherwise.
    /// Caller is responsible for deleting the file.
    fn build_verify_fixture(name: &str) -> Option<PathBuf> {
        use rusqlite::Connection as RConn;
        let tmp = std::env::temp_dir().join(format!(
            "shelf_verify_{}_{}.lun",
            name,
            std::process::id()
        ));
        let _ = std::fs::remove_file(&tmp);

        let schema_py = std::fs::read_to_string(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("..")
                .join("..")
                .join("..")
                .join("..")
                .join("_LunaEngine_BetaProject_V2.0_Root")
                .join("src/luna/cartridge/schema.py"),
        )
        .ok()?;
        let lun_schema = schema_py
            .split("LUN_SCHEMA = \"\"\"\\\n")
            .nth(1)
            .and_then(|after| after.split("\"\"\"").next())?;

        let conn = RConn::open(&tmp).ok()?;
        conn.execute_batch(lun_schema).ok()?;
        conn.execute("PRAGMA application_id = 1280659011", []).ok()?;
        conn.execute("PRAGMA user_version = 3", []).ok()?;

        for (k, v) in [
            ("format_version", "0.3"),
            ("cartridge_kind", "knowledge"),
            ("source_hash", &"a".repeat(64)[..]),
            ("source_filename", "verify_fixture.md"),
            ("source_format", "markdown"),
            ("logprob_base", "e"),
            ("logprob_attribution", "response_level"),
            ("ledger_hash_algorithm", "sha256"),
            (
                "sketches_present",
                "entity_surface,extraction_ulid,fts_term,node_ulid",
            ),
            ("fts_tokenizer_config", "unicode61"),
        ] {
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?1, ?2)",
                rusqlite::params![k, v],
            )
            .ok()?;
        }

        // Insert sample doc_node (FTS5 triggers populate nodes_fts from this).
        conn.execute(
            "INSERT INTO doc_nodes (id, ulid, parent_ulid, type, position, content, meta_json) \
             VALUES (1, ?1, NULL, 'paragraph', 0, ?2, NULL)",
            rusqlite::params![
                KNOWN_NODE_ULID,
                "the highest virtue is justice and reason"
            ],
        )
        .ok()?;

        // Insert sample extractions.
        conn.execute(
            "INSERT INTO extractions (ulid, type, content, anchor_status, anchor_reason, \
             extraction_method, llm_logprob_sum, llm_token_count) \
             VALUES (?1, 'claim', 'sample claim text', 'anchored', NULL, 'auto', NULL, NULL)",
            rusqlite::params![KNOWN_EXTRACTION_ULID],
        )
        .ok()?;
        conn.execute(
            "INSERT INTO extractions (ulid, type, content, anchor_status, anchor_reason, \
             extraction_method, llm_logprob_sum, llm_token_count) \
             VALUES (?1, 'entity', ?2, 'unknown', NULL, 'auto', NULL, NULL)",
            rusqlite::params!["01HQXY0000000000000000ENTI", KNOWN_ENTITY_CONTENT],
        )
        .ok()?;

        // Build + insert sketches for all four kinds, populated against the
        // known items above (using the normalization rules from § 7.1.1).
        let m: i64 = 2048;
        let k_h: i64 = 7;
        let make_sketch = |items: &[&[u8]]| -> Vec<u8> {
            let mut bs = vec![0u8; (m / 8) as usize];
            for item in items {
                for bit in bit_indices(item, 0, k_h, m) {
                    bs[(bit >> 3) as usize] |= 1u8 << (bit & 7);
                }
            }
            bs
        };
        let extraction_bs = make_sketch(&[KNOWN_EXTRACTION_ULID.as_bytes()]);
        let node_bs = make_sketch(&[KNOWN_NODE_ULID.as_bytes()]);
        let entity_normalized = normalize_entity_surface(KNOWN_ENTITY_CONTENT);
        let entity_bs = make_sketch(&[&entity_normalized[..]]);
        let fts_bs = make_sketch(&[KNOWN_FTS_TERM.as_bytes()]);

        for (kind_name, bitset) in [
            ("extraction_ulid", extraction_bs),
            ("node_ulid", node_bs),
            ("entity_surface", entity_bs),
            ("fts_term", fts_bs),
        ] {
            conn.execute(
                "INSERT INTO sketches (sketch_kind, sketch_version, hash_family, \
                 num_hashes, num_bits, num_inserted, seed, bitset, builder_version, notes) \
                 VALUES (?1, 1, 'murmur3_x64_128', 7, 2048, 1, 0, ?2, NULL, NULL)",
                rusqlite::params![kind_name, bitset],
            )
            .ok()?;
        }

        // Ledger genesis + head pointer.
        let entry_hash = "f".repeat(64);
        conn.execute(
            "INSERT INTO annotation_ledger (seq, ulid, entry_ts, event_type, \
             actor_id, actor_role, target_kind, target_ulid, target_cartridge_ulid, \
             payload, prev_hash, entry_hash) \
             VALUES (1, '01HQXY00000000000000GENESIS', 0, 'meta', \
             '01HQ000000000000000000SYSTEM', 'system', NULL, NULL, NULL, '{}', NULL, ?1)",
            rusqlite::params![entry_hash],
        )
        .ok()?;
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('ledger_head_seq', '1'), \
             ('ledger_head_hash', ?1), ('ledger_genesis_ulid', '01HQXY00000000000000GENESIS')",
            rusqlite::params![entry_hash],
        )
        .ok()?;
        conn.close().ok()?;
        Some(tmp)
    }

    #[test]
    fn verify_extraction_ulid_confirmed() {
        let Some(path) = build_verify_fixture("ext_confirmed") else {
            return;
        };
        let status =
            verify_candidate(&path, KNOWN_EXTRACTION_ULID, SketchKind::ExtractionUlid).unwrap();
        assert!(matches!(status, CandidateStatus::Confirmed));
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn verify_extraction_ulid_false_positive() {
        let Some(path) = build_verify_fixture("ext_fp") else {
            return;
        };
        let status =
            verify_candidate(&path, ABSENT_EXTRACTION_ULID, SketchKind::ExtractionUlid).unwrap();
        assert!(matches!(status, CandidateStatus::FalsePositive));
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn verify_node_ulid_confirmed() {
        let Some(path) = build_verify_fixture("node_confirmed") else {
            return;
        };
        let status = verify_candidate(&path, KNOWN_NODE_ULID, SketchKind::NodeUlid).unwrap();
        assert!(matches!(status, CandidateStatus::Confirmed));
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn verify_node_ulid_false_positive() {
        let Some(path) = build_verify_fixture("node_fp") else {
            return;
        };
        let status = verify_candidate(&path, ABSENT_NODE_ULID, SketchKind::NodeUlid).unwrap();
        assert!(matches!(status, CandidateStatus::FalsePositive));
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn verify_fts_term_confirmed() {
        let Some(path) = build_verify_fixture("fts_confirmed") else {
            return;
        };
        let status = verify_candidate(&path, KNOWN_FTS_TERM, SketchKind::FtsTerm).unwrap();
        assert!(matches!(status, CandidateStatus::Confirmed));
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn verify_fts_term_false_positive() {
        let Some(path) = build_verify_fixture("fts_fp") else {
            return;
        };
        let status = verify_candidate(&path, ABSENT_FTS_TERM, SketchKind::FtsTerm).unwrap();
        assert!(matches!(status, CandidateStatus::FalsePositive));
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn verify_entity_surface_confirmed_full_form() {
        let Some(path) = build_verify_fixture("entity_full") else {
            return;
        };
        let status =
            verify_candidate(&path, KNOWN_ENTITY_CONTENT, SketchKind::EntitySurface).unwrap();
        assert!(matches!(status, CandidateStatus::Confirmed));
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn verify_entity_surface_false_positive() {
        let Some(path) = build_verify_fixture("entity_fp") else {
            return;
        };
        let status =
            verify_candidate(&path, "DefinitelyNotAnEntity [person]", SketchKind::EntitySurface)
                .unwrap();
        assert!(matches!(status, CandidateStatus::FalsePositive));
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn filter_candidates_unknown_kind_yields_unknown() {
        // Build a shelf whose cartridge has only an extraction_ulid sketch;
        // query for a different kind ⇒ Unknown.
        // Reuse the same fixture builder pattern via direct manipulation.
        use rusqlite::Connection as RConn;
        let tmp = std::env::temp_dir().join(format!(
            "shelf_unknown_test_{}.lun",
            std::process::id()
        ));
        let _ = std::fs::remove_file(&tmp);
        let conn = RConn::open(&tmp).unwrap();
        let schema_py = std::fs::read_to_string(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("..")
                .join("..")
                .join("..")
                .join("..")
                .join("_LunaEngine_BetaProject_V2.0_Root")
                .join("src/luna/cartridge/schema.py"),
        )
        .ok();
        let Some(schema_py) = schema_py else {
            eprintln!("skip: engine schema.py not found; cannot build fixture");
            return;
        };
        let lun_schema = schema_py
            .split("LUN_SCHEMA = \"\"\"\\\n")
            .nth(1)
            .and_then(|after| after.split("\"\"\"").next())
            .unwrap_or("");
        conn.execute_batch(lun_schema).unwrap();
        conn.execute("PRAGMA application_id = 1280659011", []).unwrap();
        conn.execute("PRAGMA user_version = 3", []).unwrap();
        for (k, v) in [
            ("format_version", "0.3"),
            ("cartridge_kind", "knowledge"),
            ("source_hash", &"a".repeat(64)[..]),
            ("source_filename", "fixture.md"),
            ("source_format", "markdown"),
            ("logprob_base", "e"),
            ("logprob_attribution", "response_level"),
            ("ledger_hash_algorithm", "sha256"),
            ("sketches_present", "extraction_ulid"),
        ] {
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?1, ?2)",
                rusqlite::params![k, v],
            )
            .unwrap();
        }
        let m: i64 = 256;
        let k: i64 = 7;
        let mut bs = vec![0u8; (m / 8) as usize];
        for bit in bit_indices(b"01HQXY0000000000000000000A", 0, k, m) {
            bs[(bit >> 3) as usize] |= 1u8 << (bit & 7);
        }
        conn.execute(
            "INSERT INTO sketches (sketch_kind, sketch_version, hash_family, \
             num_hashes, num_bits, num_inserted, seed, bitset, builder_version, notes) \
             VALUES ('extraction_ulid', 1, 'murmur3_x64_128', 7, 256, 1, 0, ?1, NULL, NULL)",
            rusqlite::params![bs],
        )
        .unwrap();
        let entry_hash = "f".repeat(64);
        conn.execute(
            "INSERT INTO annotation_ledger (seq, ulid, entry_ts, event_type, \
             actor_id, actor_role, target_kind, target_ulid, target_cartridge_ulid, \
             payload, prev_hash, entry_hash) \
             VALUES (1, '01HQXY00000000000000000000', 0, 'meta', \
             '01HQ000000000000000000SYSTEM', 'system', NULL, NULL, NULL, '{}', NULL, ?1)",
            rusqlite::params![entry_hash],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('ledger_head_seq', '1'), \
             ('ledger_head_hash', ?1), ('ledger_genesis_ulid', '01HQXY00000000000000000000')",
            rusqlite::params![entry_hash],
        )
        .unwrap();
        conn.close().unwrap();

        let sc = open_shelf_cartridge(&tmp).expect("open fixture");
        let cands = filter_candidates(&[sc], "Marcus Aurelius", SketchKind::EntitySurface);
        assert_eq!(cands.len(), 1);
        match cands[0].status {
            CandidateStatus::Unknown => {}
            _ => panic!("expected Unknown for absent kind"),
        }

        let _ = std::fs::remove_file(&tmp);
    }
}
