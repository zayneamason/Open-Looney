use crate::error::ReaderError;
use crate::types::{
    AnchorStatus, ContextNode, DocNode, DocNodeBrief, Extraction, ExtractionCount,
    ExtractionSource, ExtractionSourcesResult, ExtractionType, FigureEnrichment, FigurePayload,
    LedgerEvent, Meta, NodeType, SearchHit,
};
use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use rusqlite::{params, Connection, OptionalExtension, Row};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

/// Escapes HTML metacharacters in `raw`, EXCEPT for the literal sequences
/// `<mark>` and `</mark>` which are preserved verbatim. The frontend can then
/// inject the result via `dangerouslySetInnerHTML` without XSS risk, because
/// FTS5's `snippet()` never emits those tags on its own — we pass them in.
pub fn safe_snippet(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len() + 16);
    let mut rest = raw;
    while !rest.is_empty() {
        if let Some(suffix) = rest.strip_prefix("<mark>") {
            out.push_str("<mark>");
            rest = suffix;
        } else if let Some(suffix) = rest.strip_prefix("</mark>") {
            out.push_str("</mark>");
            rest = suffix;
        } else {
            // SAFETY: rest is non-empty, so chars().next() returns Some.
            let ch = rest.chars().next().unwrap();
            match ch {
                '<' => out.push_str("&lt;"),
                '>' => out.push_str("&gt;"),
                '&' => out.push_str("&amp;"),
                '"' => out.push_str("&quot;"),
                '\'' => out.push_str("&#39;"),
                _ => out.push(ch),
            }
            rest = &rest[ch.len_utf8()..];
        }
    }
    out
}

// Note: the `embeddings` table exists in v0.3 cartridges and stores raw
// little-endian f32 BLOBs (see `unpack_f32_le` / `semantic_search` below).
// Do not SELECT * from any table — always enumerate columns.

pub fn get_meta(conn: &Connection) -> Result<Meta, ReaderError> {
    let mut stmt = conn.prepare("SELECT key, value FROM meta")?;
    let rows = stmt.query_map([], |r| {
        let k: String = r.get(0)?;
        let v: String = r.get(1)?;
        Ok((k, v))
    })?;

    let mut all: HashMap<String, String> = HashMap::new();
    for row in rows {
        let (k, v) = row?;
        all.insert(k, v);
    }

    let word_count = all.remove("word_count").and_then(|s| s.parse().ok());
    let node_count = all.remove("node_count").and_then(|s| s.parse().ok());
    let embedding_dim = all.remove("embedding_dim").and_then(|s| s.parse().ok());
    let ledger_head_seq = all.remove("ledger_head_seq").and_then(|s| s.parse().ok());

    Ok(Meta {
        title: all.remove("title"),
        source_filename: all.remove("source_filename"),
        source_format: all.remove("source_format"),
        source_hash: all.remove("source_hash"),
        word_count,
        node_count,
        created_at: all.remove("created_at"),
        format_version: all.remove("format_version"),
        cartridge_kind: all.remove("cartridge_kind"),
        embedding_model: all.remove("embedding_model"),
        embedding_dim,
        logprob_base: all.remove("logprob_base"),
        logprob_attribution: all.remove("logprob_attribution"),
        ledger_hash_algorithm: all.remove("ledger_hash_algorithm"),
        ledger_genesis_ulid: all.remove("ledger_genesis_ulid"),
        ledger_head_seq,
        ledger_head_hash: all.remove("ledger_head_hash"),
        extra: all,
    })
}

fn parse_node_type(s: &str) -> Result<NodeType, ReaderError> {
    NodeType::from_sql_str(s).ok_or_else(|| ReaderError::SqliteError {
        message: format!("unknown doc_nodes.type value: {:?}", s),
    })
}

fn parse_extraction_type(s: &str) -> Result<ExtractionType, ReaderError> {
    ExtractionType::from_sql_str(s).ok_or_else(|| ReaderError::SqliteError {
        message: format!("unknown extractions.type value: {:?}", s),
    })
}

fn parse_anchor_status(s: &str) -> Result<AnchorStatus, ReaderError> {
    AnchorStatus::from_sql_str(s).ok_or_else(|| ReaderError::SqliteError {
        message: format!("unknown anchor_status value: {:?}", s),
    })
}

fn parse_meta_json(raw: Option<String>) -> Option<serde_json::Value> {
    raw.and_then(|s| serde_json::from_str(&s).ok())
}

/// Reads 6 consecutive columns starting at `offset` as DocNode raw data
/// (without children_count or parent_chain): ulid, parent_ulid, type, position,
/// content, meta_json.
fn doc_node_at(
    r: &Row<'_>,
    offset: usize,
) -> rusqlite::Result<(
    String,
    Option<String>,
    String,
    i64,
    Option<String>,
    Option<String>,
)> {
    Ok((
        r.get(offset)?,
        r.get(offset + 1)?,
        r.get(offset + 2)?,
        r.get(offset + 3)?,
        r.get(offset + 4)?,
        r.get(offset + 5)?,
    ))
}

fn build_doc_node(
    raw: (
        String,
        Option<String>,
        String,
        i64,
        Option<String>,
        Option<String>,
    ),
) -> Result<DocNode, ReaderError> {
    let (ulid, parent_ulid, type_str, position, content, meta_json_raw) = raw;
    Ok(DocNode {
        ulid,
        parent_ulid,
        node_type: parse_node_type(&type_str)?,
        position,
        content: content.unwrap_or_default(),
        meta_json: parse_meta_json(meta_json_raw),
        children_count: None,
        parent_chain: None,
    })
}

pub fn list_nodes(
    conn: &Connection,
    parent_ulid: Option<String>,
    type_filter: Option<NodeType>,
    limit: i64,
    offset: i64,
) -> Result<Vec<DocNode>, ReaderError> {
    let type_str = type_filter.as_ref().map(|t| t.as_sql_str());
    let mut stmt = conn.prepare(
        "SELECT ulid, parent_ulid, type, position, content, meta_json
         FROM doc_nodes
         WHERE parent_ulid IS ?1
           AND (?2 IS NULL OR type = ?2)
         ORDER BY position ASC, ulid ASC
         LIMIT ?3 OFFSET ?4",
    )?;
    let rows = stmt.query_map(params![parent_ulid, type_str, limit, offset], |r| {
        doc_node_at(r, 0)
    })?;

    let mut out = Vec::new();
    for row in rows {
        out.push(build_doc_node(row?)?);
    }
    Ok(out)
}

pub fn list_all_nodes(conn: &Connection) -> Result<Vec<DocNode>, ReaderError> {
    // ORDER BY position then ulid gives a stable tree-walk under Strategy A
    // without exposing the FTS-only integer id.
    let mut stmt = conn.prepare(
        "SELECT ulid, parent_ulid, type, position, content, meta_json
         FROM doc_nodes
         ORDER BY parent_ulid IS NULL DESC, parent_ulid ASC, position ASC, ulid ASC",
    )?;
    let rows = stmt.query_map([], |r| doc_node_at(r, 0))?;

    let mut out = Vec::new();
    for row in rows {
        out.push(build_doc_node(row?)?);
    }
    Ok(out)
}

pub fn get_node(conn: &Connection, node_ulid: &str) -> Result<DocNode, ReaderError> {
    let (parent_ulid, type_str, position, content, meta_json_raw) = conn.query_row(
        "SELECT parent_ulid, type, position, content, meta_json
         FROM doc_nodes WHERE ulid = ?1",
        [node_ulid],
        |r| {
            Ok((
                r.get::<_, Option<String>>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, i64>(2)?,
                r.get::<_, Option<String>>(3)?,
                r.get::<_, Option<String>>(4)?,
            ))
        },
    )?;

    let children_count: i64 = conn.query_row(
        "SELECT COUNT(*) FROM doc_nodes WHERE parent_ulid = ?1",
        [node_ulid],
        |r| r.get(0),
    )?;

    // Parent chain: iterative walk via parent_ulid.
    let mut chain: Vec<DocNodeBrief> = Vec::new();
    let mut cur = parent_ulid.clone();
    while let Some(pulid) = cur {
        let (parent_of_parent, brief_type, brief_pos, brief_preview) = conn.query_row(
            "SELECT parent_ulid, type, position, substr(content, 1, 80)
             FROM doc_nodes WHERE ulid = ?1",
            [&pulid],
            |r| {
                Ok((
                    r.get::<_, Option<String>>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, i64>(2)?,
                    r.get::<_, Option<String>>(3)?,
                ))
            },
        )?;
        chain.push(DocNodeBrief {
            ulid: pulid,
            node_type: parse_node_type(&brief_type)?,
            position: brief_pos,
            content_preview: brief_preview.unwrap_or_default(),
        });
        cur = parent_of_parent;
    }
    chain.reverse(); // root first → leaf

    Ok(DocNode {
        ulid: node_ulid.to_string(),
        parent_ulid,
        node_type: parse_node_type(&type_str)?,
        position,
        content: content.unwrap_or_default(),
        meta_json: parse_meta_json(meta_json_raw),
        children_count: Some(children_count),
        parent_chain: Some(chain),
    })
}

pub fn list_extractions(
    conn: &Connection,
    type_filter: Option<ExtractionType>,
    anchor_status_filter: Option<AnchorStatus>,
    limit: i64,
    offset: i64,
) -> Result<Vec<Extraction>, ReaderError> {
    let t = type_filter.as_ref().map(|t| t.as_sql_str());
    let s = anchor_status_filter.as_ref().map(|s| s.as_sql_str());
    let mut stmt = conn.prepare(
        "SELECT ulid, type, content, anchor_status, anchor_reason,
                extraction_method, llm_logprob_sum, llm_token_count
         FROM extractions
         WHERE (?1 IS NULL OR type = ?1)
           AND (?2 IS NULL OR anchor_status = ?2)
         ORDER BY ulid ASC
         LIMIT ?3 OFFSET ?4",
    )?;
    let rows = stmt.query_map(params![t, s, limit, offset], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, Option<String>>(2)?,
            r.get::<_, String>(3)?,
            r.get::<_, Option<String>>(4)?,
            r.get::<_, String>(5)?,
            r.get::<_, Option<f64>>(6)?,
            r.get::<_, Option<i64>>(7)?,
        ))
    })?;

    let mut out = Vec::new();
    for row in rows {
        let (ulid, type_str, content, status_str, reason, method, logprob, tokens) = row?;
        out.push(Extraction {
            ulid,
            extraction_type: parse_extraction_type(&type_str)?,
            content: content.unwrap_or_default(),
            anchor_status: parse_anchor_status(&status_str)?,
            anchor_reason: reason,
            extraction_method: method,
            llm_logprob_sum: logprob,
            llm_token_count: tokens,
        });
    }
    Ok(out)
}

/// Single-row extraction lookup by ULID. Returns `Ok(None)` when the ULID
/// is not present — that is the SPEC-007 verify-by-opening false-positive
/// case for the `extraction_ulid` sketch kind. Distinct from `Err(...)`,
/// which is reserved for SQL / corruption failures.
pub fn get_extraction(
    conn: &Connection,
    extraction_ulid: &str,
) -> Result<Option<Extraction>, ReaderError> {
    let row = conn
        .query_row(
            "SELECT ulid, type, content, anchor_status, anchor_reason,
                    extraction_method, llm_logprob_sum, llm_token_count
             FROM extractions WHERE ulid = ?1",
            [extraction_ulid],
            |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, Option<String>>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, Option<String>>(4)?,
                    r.get::<_, String>(5)?,
                    r.get::<_, Option<f64>>(6)?,
                    r.get::<_, Option<i64>>(7)?,
                ))
            },
        )
        .optional()?;
    let Some((ulid, type_str, content, status_str, reason, method, logprob, tokens)) = row else {
        return Ok(None);
    };
    Ok(Some(Extraction {
        ulid,
        extraction_type: parse_extraction_type(&type_str)?,
        content: content.unwrap_or_default(),
        anchor_status: parse_anchor_status(&status_str)?,
        anchor_reason: reason,
        extraction_method: method,
        llm_logprob_sum: logprob,
        llm_token_count: tokens,
    }))
}

/// Find the first entity extraction whose `content` matches the given string
/// after NFKC + lowercase + trim normalization (mirrors the SPEC-007 § 7.1.1
/// `entity_surface` sketch normalization). The verify-by-opening backend for
/// the `entity_surface` sketch kind; `Ok(None)` is the false-positive case.
///
/// Bare-name fix (engine commit `24c19c2`): the engine populates the sketch
/// with both the raw `"name [type]"` form AND the bare name, so the caller
/// may pass either form. The exact-match SQL compares the normalized stored
/// content to the normalized needle, so both forms hit when present.
pub fn find_extraction_by_content(
    conn: &Connection,
    content: &str,
) -> Result<Option<Extraction>, ReaderError> {
    use unicode_normalization::UnicodeNormalization;
    let needle: String = content.nfkc().collect::<String>().to_lowercase();
    let needle = needle.trim();
    let row = conn
        .query_row(
            "SELECT ulid, type, content, anchor_status, anchor_reason,
                    extraction_method, llm_logprob_sum, llm_token_count
             FROM extractions
             WHERE type = 'entity'
               AND TRIM(LOWER(content)) = ?1
             LIMIT 1",
            [needle],
            |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, Option<String>>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, Option<String>>(4)?,
                    r.get::<_, String>(5)?,
                    r.get::<_, Option<f64>>(6)?,
                    r.get::<_, Option<i64>>(7)?,
                ))
            },
        )
        .optional()?;
    let Some((ulid, type_str, content, status_str, reason, method, logprob, tokens)) = row else {
        return Ok(None);
    };
    Ok(Some(Extraction {
        ulid,
        extraction_type: parse_extraction_type(&type_str)?,
        content: content.unwrap_or_default(),
        anchor_status: parse_anchor_status(&status_str)?,
        anchor_reason: reason,
        extraction_method: method,
        llm_logprob_sum: logprob,
        llm_token_count: tokens,
    }))
}

pub fn get_extraction_counts(conn: &Connection) -> Result<Vec<ExtractionCount>, ReaderError> {
    let mut stmt = conn.prepare(
        "SELECT type, anchor_status, COUNT(*)
         FROM extractions
         GROUP BY type, anchor_status
         ORDER BY type, anchor_status",
    )?;
    let rows = stmt.query_map([], |r| {
        Ok(ExtractionCount {
            extraction_type: r.get(0)?,
            anchor_status: r.get(1)?,
            count: r.get(2)?,
        })
    })?;
    let mut out = Vec::new();
    for r in rows {
        out.push(r?);
    }
    Ok(out)
}

pub fn get_extraction_sources(
    conn: &Connection,
    extraction_ulid: &str,
) -> Result<ExtractionSourcesResult, ReaderError> {
    // Sources: extraction_sources JOIN doc_nodes via ULID.
    let mut sources_stmt = conn.prepare(
        "SELECT n.ulid, n.parent_ulid, n.type, n.position, n.content, n.meta_json,
                es.anchor_method, es.anchored_by, es.anchored_at, es.event_id
         FROM extraction_sources es JOIN doc_nodes n ON n.ulid = es.node_ulid
         WHERE es.extraction_ulid = ?1
         ORDER BY n.position",
    )?;
    let source_rows = sources_stmt.query_map([extraction_ulid], |r| {
        let node_raw = doc_node_at(r, 0)?;
        let method: Option<String> = r.get(6)?;
        let by: Option<String> = r.get(7)?;
        let at: Option<i64> = r.get(8)?;
        let event: Option<String> = r.get(9)?;
        Ok((node_raw, method, by, at, event))
    })?;

    let mut sources = Vec::new();
    for row in source_rows {
        let (node_raw, method, by, at, event) = row?;
        sources.push(ExtractionSource {
            node: build_doc_node(node_raw)?,
            anchor_method: method,
            anchored_by: by,
            anchored_at: at,
            event_id: event,
        });
    }

    // Context (for synthesized extractions).
    let mut context_stmt = conn.prepare(
        "SELECT n.ulid, n.parent_ulid, n.type, n.position, n.content, n.meta_json,
                ecn.relevance
         FROM extraction_context_nodes ecn JOIN doc_nodes n ON n.ulid = ecn.node_ulid
         WHERE ecn.extraction_ulid = ?1
         ORDER BY ecn.relevance DESC",
    )?;
    let context_rows = context_stmt.query_map([extraction_ulid], |r| {
        let node_raw = doc_node_at(r, 0)?;
        let relevance: f64 = r.get(6)?;
        Ok((node_raw, relevance))
    })?;

    let mut context = Vec::new();
    for row in context_rows {
        let (node_raw, relevance) = row?;
        context.push(ContextNode {
            node: build_doc_node(node_raw)?,
            relevance,
        });
    }

    Ok(ExtractionSourcesResult { sources, context })
}

fn media_blobs_table_exists(conn: &Connection) -> Result<bool, ReaderError> {
    let n: i64 = conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'media_blobs'",
        [],
        |r| r.get(0),
    )?;
    Ok(n > 0)
}

fn resolve_external_media_path(
    cartridge_path: &Path,
    relative: &str,
) -> Result<PathBuf, ReaderError> {
    let base = cartridge_path.parent().ok_or_else(|| ReaderError::IoError {
        message: "cartridge path has no parent directory".into(),
    })?;
    let joined = base.join(relative);
    let base_canon = base.canonicalize().map_err(|e| ReaderError::IoError {
        message: format!("cannot resolve cartridge directory: {e}"),
    })?;
    let resolved = if joined.exists() {
        joined.canonicalize().map_err(|e| ReaderError::IoError {
            message: format!("cannot resolve external media path: {e}"),
        })?
    } else {
        // Keep a normalized absolute path for error reporting / UI.
        if !joined.is_absolute() {
            return Ok(base_canon.join(relative));
        }
        joined
    };
    if !resolved.starts_with(&base_canon) {
        return Err(ReaderError::IoError {
            message: format!(
                "external media path escapes cartridge directory: {}",
                relative
            ),
        });
    }
    Ok(resolved)
}

/// Resolve figure caption, raster bytes (embedded or sidecar), and SPEC-013 enrichments.
pub fn get_figure_payload(
    conn: &Connection,
    cartridge_path: &Path,
    figure_ulid: &str,
) -> Result<FigurePayload, ReaderError> {
    let figure = get_node(conn, figure_ulid)?;
    if figure.node_type != NodeType::Figure {
        return Err(ReaderError::SqliteError {
            message: format!("node {figure_ulid} is not a figure"),
        });
    }

    let image_ulid: Option<String> = conn
        .query_row(
            "SELECT ulid FROM doc_nodes
             WHERE parent_ulid = ?1 AND type = 'image'
             ORDER BY position ASC, ulid ASC
             LIMIT 1",
            [figure_ulid],
            |r| r.get(0),
        )
        .optional()?;

    let mut mime_type: Option<String> = None;
    let mut sha256: Option<String> = None;
    let mut byte_len: Option<i64> = None;
    let mut storage: Option<String> = None;
    let mut external_path_resolved: Option<String> = None;
    let mut bytes_base64: Option<String> = None;

    if let Some(ref img_ulid) = image_ulid {
        if media_blobs_table_exists(conn)? {
            let row: Option<(
                String,
                String,
                String,
                Option<Vec<u8>>,
                Option<String>,
                Option<i64>,
            )> = conn
                .query_row(
                    "SELECT media_type, sha256, storage, bytes, external_path,
                            CASE WHEN bytes IS NULL THEN NULL ELSE length(bytes) END
                     FROM media_blobs WHERE node_ulid = ?1",
                    [img_ulid],
                    |r| {
                        Ok((
                            r.get(0)?,
                            r.get(1)?,
                            r.get(2)?,
                            r.get(3)?,
                            r.get(4)?,
                            r.get(5)?,
                        ))
                    },
                )
                .optional()?;

            if let Some((mt, sha, stor, bytes, ext, len)) = row {
                mime_type = Some(mt);
                sha256 = Some(sha);
                storage = Some(stor.clone());
                byte_len = len;
                match stor.as_str() {
                    "embedded" => {
                        if let Some(b) = bytes {
                            byte_len = Some(b.len() as i64);
                            bytes_base64 = Some(B64.encode(&b));
                        }
                    }
                    "external" => {
                        if let Some(rel) = ext {
                            let abs = resolve_external_media_path(cartridge_path, &rel)?;
                            external_path_resolved =
                                Some(abs.to_string_lossy().into_owned());
                            if abs.is_file() {
                                if let Ok(meta) = std::fs::metadata(&abs) {
                                    byte_len = Some(meta.len() as i64);
                                }
                                // Do not base64 large sidecars — frontend uses convertFileSrc.
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    let mut enrichments = Vec::new();
    let mut stmt = conn.prepare(
        "SELECT e.ulid, e.type, e.content, es.anchored_at
         FROM extractions e
         JOIN extraction_sources es ON es.extraction_ulid = e.ulid
         WHERE es.node_ulid = ?1
           AND e.type IN (
             'media_classification',
             'visual_description',
             'figure_discourse'
           )
         ORDER BY e.type ASC, e.ulid ASC",
    )?;
    let rows = stmt.query_map([figure_ulid], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, String>(2)?,
            r.get::<_, Option<i64>>(3)?,
        ))
    })?;
    for row in rows {
        let (ulid, extraction_type, content, anchored_at) = row?;
        enrichments.push(FigureEnrichment {
            ulid,
            extraction_type,
            content,
            anchored_at,
        });
    }

    Ok(FigurePayload {
        figure_ulid: figure_ulid.to_string(),
        caption: figure.content,
        image_ulid,
        mime_type,
        sha256,
        byte_len,
        storage,
        external_path_resolved,
        bytes_base64,
        enrichments,
    })
}

pub fn search(conn: &Connection, query: &str, limit: i64) -> Result<Vec<SearchHit>, ReaderError> {
    // FTS5 external-content table over doc_nodes(content), with content_rowid='id',
    // so the FTS rowid maps directly to doc_nodes.id. v0.3 keeps doc_nodes.id as
    // an FTS-only INTEGER (Strategy A); the join surfaces the user-facing ULID.
    let mut stmt = conn.prepare(
        "SELECT n.ulid,
                snippet(nodes_fts, 0, '<mark>', '</mark>', '…', 32) AS snippet_html,
                rank
         FROM nodes_fts
         JOIN doc_nodes n ON n.id = nodes_fts.rowid
         WHERE nodes_fts MATCH ?1
         ORDER BY rank
         LIMIT ?2",
    )?;
    let rows = stmt.query_map(params![query, limit], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, f64>(2)?,
        ))
    })?;
    let mut out = Vec::new();
    for row in rows {
        let (node_ulid, raw_snippet, rank) = row?;
        out.push(SearchHit {
            node_ulid,
            snippet_html: safe_snippet(&raw_snippet),
            rank,
            source: "fts".to_string(),
            level: None,
        });
    }
    Ok(out)
}

/// Unpacks a raw little-endian f32 array blob (as written by the cartridge
/// builder's `struct.pack(f"{len(vector)}f", *vector)` — no header).
fn unpack_f32_le(blob: &[u8]) -> Vec<f32> {
    blob.chunks_exact(4)
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect()
}

fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let dot: f32 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    let norm_a: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let norm_b: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm_a == 0.0 || norm_b == 0.0 {
        return 0.0;
    }
    dot / (norm_a * norm_b)
}

/// Reconstructs display text for a paragraph/section node, whose own
/// `doc_nodes.content` column is NULL by schema design (text lives on
/// descendant `sentence`/`list_item`/`cell` nodes). Mirrors the same text
/// the cartridge builder's `embedder.py` concatenated when generating the
/// stored vector, so the snippet reflects what was actually embedded.
fn reconstruct_node_text(conn: &Connection, node_ulid: &str, level: &str) -> Result<String, ReaderError> {
    let sql = if level == "paragraph" {
        "SELECT COALESCE(GROUP_CONCAT(content, ' '), '') FROM (
            SELECT content FROM doc_nodes
            WHERE parent_ulid = ?1 AND type = 'sentence'
            ORDER BY id
        )"
    } else {
        "WITH RECURSIVE subtree AS (
            SELECT ulid FROM doc_nodes WHERE ulid = ?1
            UNION ALL
            SELECT d.ulid FROM doc_nodes d JOIN subtree s ON d.parent_ulid = s.ulid
        )
        SELECT COALESCE(GROUP_CONCAT(content, ' '), '') FROM (
            SELECT content FROM doc_nodes
            WHERE ulid IN (SELECT ulid FROM subtree)
              AND type IN ('sentence', 'list_item', 'cell')
              AND content IS NOT NULL AND content != ''
            ORDER BY id
        )"
    };
    Ok(conn.query_row(sql, params![node_ulid], |r| r.get(0))?)
}

/// Semantic search: brute-force cosine scan over the cartridge's stored
/// `embeddings` (paragraph + section level, merged into one ranked list).
/// Vector counts per cartridge are small (dozens to low hundreds), so no
/// ANN index is needed. `rank = 1.0 - cosine_similarity` (lower is better),
/// matching the FTS bm25 "lower is better" convention `search()` uses.
pub fn semantic_search(
    conn: &Connection,
    query_vector: &[f32],
    limit: i64,
) -> Result<Vec<SearchHit>, ReaderError> {
    let mut stmt = conn.prepare("SELECT node_ulid, level, vector FROM embeddings")?;
    let rows = stmt.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, Vec<u8>>(2)?,
        ))
    })?;

    let mut scored: Vec<(f32, String, String)> = Vec::new();
    for row in rows {
        let (node_ulid, level, blob) = row?;
        let vector = unpack_f32_le(&blob);
        if vector.len() != query_vector.len() {
            // Defends against a stray malformed/legacy-dim row rather than
            // failing the whole search.
            continue;
        }
        let sim = cosine_similarity(query_vector, &vector);
        scored.push((sim, node_ulid, level));
    }
    scored.sort_by(|a, b| b.0.total_cmp(&a.0));
    scored.truncate(limit.max(0) as usize);

    scored
        .into_iter()
        .map(|(sim, node_ulid, level)| {
            let content = reconstruct_node_text(conn, &node_ulid, &level)?;
            let mut snippet: String = content.chars().take(220).collect();
            if content.chars().count() > 220 {
                snippet.push('…');
            }
            Ok(SearchHit {
                node_ulid,
                snippet_html: safe_snippet(&snippet),
                rank: 1.0 - sim as f64,
                source: "semantic".to_string(),
                level: Some(level),
            })
        })
        .collect()
}

/// SPEC-005 read-path helper: full event history for a single target ULID.
/// Joins to annotation_actors for actor display_name when available.
pub fn get_ledger_events(
    conn: &Connection,
    target_ulid: &str,
) -> Result<Vec<LedgerEvent>, ReaderError> {
    let mut stmt = conn.prepare(
        "SELECT l.seq, l.ulid, l.entry_ts, l.event_type, l.actor_id, l.actor_role,
                a.display_name,
                l.target_kind, l.target_ulid, l.target_cartridge_ulid,
                l.payload, l.prev_hash, l.entry_hash
         FROM annotation_ledger l
         LEFT JOIN annotation_actors a ON a.actor_id = l.actor_id
         WHERE l.target_ulid = ?1
         ORDER BY l.seq ASC",
    )?;
    let rows = stmt.query_map([target_ulid], |r| {
        Ok((
            r.get::<_, i64>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, i64>(2)?,
            r.get::<_, String>(3)?,
            r.get::<_, String>(4)?,
            r.get::<_, String>(5)?,
            r.get::<_, Option<String>>(6)?,
            r.get::<_, Option<String>>(7)?,
            r.get::<_, Option<String>>(8)?,
            r.get::<_, Option<String>>(9)?,
            r.get::<_, String>(10)?,
            r.get::<_, Option<String>>(11)?,
            r.get::<_, String>(12)?,
        ))
    })?;
    let mut out = Vec::new();
    for row in rows {
        let (
            seq,
            ulid,
            entry_ts,
            event_type,
            actor_id,
            actor_role,
            actor_display_name,
            target_kind,
            target_ulid_col,
            target_cartridge_ulid,
            payload_raw,
            prev_hash,
            entry_hash,
        ) = row?;
        let payload = serde_json::from_str(&payload_raw).unwrap_or(serde_json::Value::Null);
        out.push(LedgerEvent {
            seq,
            ulid,
            entry_ts,
            event_type,
            actor_id,
            actor_role,
            actor_display_name,
            target_kind,
            target_ulid: target_ulid_col,
            target_cartridge_ulid,
            payload,
            prev_hash,
            entry_hash,
        });
    }
    Ok(out)
}

/// SPEC-004 Temporal axis input: most recent ledger event timestamp for a
/// target ULID. Returns None when the target has no ledger events.
pub fn get_latest_event_ts(
    conn: &Connection,
    target_ulid: &str,
) -> Result<Option<i64>, ReaderError> {
    let row: Option<i64> = conn
        .query_row(
            "SELECT MAX(entry_ts) FROM annotation_ledger WHERE target_ulid = ?1",
            [target_ulid],
            |r| r.get(0),
        )
        .ok()
        .flatten();
    Ok(row)
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

    #[test]
    fn list_root_returns_single_document() {
        let Some(h) = meditations() else { return };
        let roots = list_nodes(&h.conn, None, None, 10, 0).unwrap();
        assert_eq!(roots.len(), 1);
        assert_eq!(roots[0].node_type, NodeType::Document);
    }

    #[test]
    fn document_has_128_direct_section_children() {
        // 128 sections directly under the document; 38 nested sections; 166 total.
        // Post-M-01 (engine cd7be64): the multi-line-heading merge collapsed
        // ~10 previously-fragmented nested section pairs into single sections.
        // The 128 direct children stayed the same — M-01 only affected nested
        // headings, not the top-level page sections.
        let Some(h) = meditations() else { return };
        let roots = list_nodes(&h.conn, None, None, 10, 0).unwrap();
        let doc_ulid = roots[0].ulid.clone();
        let sections =
            list_nodes(&h.conn, Some(doc_ulid), Some(NodeType::Section), 500, 0).unwrap();
        assert_eq!(sections.len(), 128);
    }

    #[test]
    fn total_node_counts_by_type() {
        let Some(h) = meditations() else { return };
        let counts: Vec<(String, i64)> = {
            let mut stmt = h
                .conn
                .prepare("SELECT type, COUNT(*) FROM doc_nodes GROUP BY type ORDER BY type")
                .unwrap();
            stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
                .unwrap()
                .map(|r| r.unwrap())
                .collect()
        };
        // Post-M-01 (engine cd7be64): section count dropped 176 → 166 because
        // the parser's multi-line-heading merge collapsed ~10 fragmented
        // section pairs across the document. document/paragraph/sentence
        // counts are structural and unchanged from v0.2.
        assert_eq!(
            counts,
            vec![
                ("document".to_string(), 1),
                ("paragraph".to_string(), 310),
                ("section".to_string(), 166),
                ("sentence".to_string(), 3326),
            ]
        );
    }

    #[test]
    fn node_type_accepts_all_builder_shapes() {
        for t in [
            "document",
            "section",
            "paragraph",
            "sentence",
            "list",
            "list_item",
            "figure",
            "image",
            "table",
            "row",
            "cell",
        ] {
            assert!(NodeType::from_sql_str(t).is_some(), "missing node type {t}");
        }
    }

    #[test]
    fn list_all_nodes_returns_full_document_tree() {
        let Some(h) = meditations() else { return };
        let nodes = list_all_nodes(&h.conn).unwrap();
        assert_eq!(nodes.len(), 3803);
        assert_eq!(nodes.first().map(|n| n.node_type), Some(NodeType::Document));
    }

    #[test]
    fn list_all_nodes_handles_rich_markdown_node_types() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE doc_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ulid TEXT NOT NULL UNIQUE,
                parent_ulid TEXT,
                type TEXT NOT NULL,
                position INTEGER NOT NULL,
                content TEXT,
                meta_json TEXT,
                FOREIGN KEY (parent_ulid) REFERENCES doc_nodes(ulid)
            );",
        )
        .unwrap();
        for (ulid, parent_ulid, node_type, position, content) in [
            ("01ABCDEFGHIJKLMNOPQRSTUVW1", None, "document", 0, None),
            ("01ABCDEFGHIJKLMNOPQRSTUVW2", Some("01ABCDEFGHIJKLMNOPQRSTUVW1"), "list", 0, None),
            ("01ABCDEFGHIJKLMNOPQRSTUVW3", Some("01ABCDEFGHIJKLMNOPQRSTUVW2"), "list_item", 0, Some("alpha")),
            ("01ABCDEFGHIJKLMNOPQRSTUVW4", Some("01ABCDEFGHIJKLMNOPQRSTUVW1"), "figure", 1, Some("diagram")),
            ("01ABCDEFGHIJKLMNOPQRSTUVW5", Some("01ABCDEFGHIJKLMNOPQRSTUVW1"), "table", 2, None),
            ("01ABCDEFGHIJKLMNOPQRSTUVW6", Some("01ABCDEFGHIJKLMNOPQRSTUVW5"), "row", 0, None),
            ("01ABCDEFGHIJKLMNOPQRSTUVW7", Some("01ABCDEFGHIJKLMNOPQRSTUVW6"), "cell", 0, Some("header")),
        ] {
            conn.execute(
                "INSERT INTO doc_nodes (ulid, parent_ulid, type, position, content, meta_json)
                 VALUES (?1, ?2, ?3, ?4, ?5, NULL)",
                params![ulid, parent_ulid, node_type, position, content],
            )
            .unwrap();
        }

        let nodes = list_all_nodes(&conn).unwrap();
        let types: Vec<_> = nodes.iter().map(|n| n.node_type).collect();
        // Document first (parent_ulid IS NULL), then descendants ordered by parent_ulid + position.
        assert_eq!(types[0], NodeType::Document);
        assert!(types.contains(&NodeType::List));
        assert!(types.contains(&NodeType::ListItem));
        assert!(types.contains(&NodeType::Figure));
        assert!(types.contains(&NodeType::Table));
        assert!(types.contains(&NodeType::Row));
        assert!(types.contains(&NodeType::Cell));
        assert_eq!(nodes_len_via_count(&conn), 7);
    }

    #[test]
    fn get_figure_payload_reads_embedded_bytes_and_enrichments() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE doc_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ulid TEXT NOT NULL UNIQUE,
                parent_ulid TEXT,
                type TEXT NOT NULL,
                position INTEGER NOT NULL,
                content TEXT,
                meta_json TEXT
            );
            CREATE TABLE media_blobs (
                node_ulid TEXT PRIMARY KEY,
                media_type TEXT NOT NULL,
                width INTEGER,
                height INTEGER,
                sha256 TEXT NOT NULL,
                storage TEXT NOT NULL,
                bytes BLOB,
                external_path TEXT
            );
            CREATE TABLE extractions (
                ulid TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                anchor_status TEXT NOT NULL DEFAULT 'anchored',
                anchor_reason TEXT,
                llm_logprob_sum REAL,
                llm_token_count INTEGER,
                extraction_method TEXT NOT NULL DEFAULT 'rule'
            );
            CREATE TABLE extraction_sources (
                extraction_ulid TEXT NOT NULL,
                node_ulid TEXT NOT NULL,
                anchor_method TEXT NOT NULL DEFAULT 'auto',
                anchored_by TEXT,
                anchored_at INTEGER,
                event_id TEXT,
                PRIMARY KEY (extraction_ulid, node_ulid)
            );",
        )
        .unwrap();

        let fig = "01FIG000000000000000000001";
        let img = "01IMG000000000000000000001";
        let ext = "01EXT000000000000000000001";
        conn.execute(
            "INSERT INTO doc_nodes (ulid, parent_ulid, type, position, content)
             VALUES ('01DOC000000000000000000001', NULL, 'document', 0, NULL)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO doc_nodes (ulid, parent_ulid, type, position, content)
             VALUES (?1, '01DOC000000000000000000001', 'figure', 0, 'A diagram')",
            [fig],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO doc_nodes (ulid, parent_ulid, type, position, content)
             VALUES (?1, ?2, 'image', 0, NULL)",
            [img, fig],
        )
        .unwrap();
        let png = b"\x89PNG\r\n\x1a\nfake";
        conn.execute(
            "INSERT INTO media_blobs (node_ulid, media_type, width, height, sha256, storage, bytes, external_path)
             VALUES (?1, 'image/png', 1, 1, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                     'embedded', ?2, NULL)",
            params![img, png.as_slice()],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO extractions (ulid, type, content, extraction_method)
             VALUES (?1, 'media_classification', 'diagram', 'rule')",
            [ext],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO extraction_sources (extraction_ulid, node_ulid, anchored_at)
             VALUES (?1, ?2, 123)",
            [ext, fig],
        )
        .unwrap();

        let cart = PathBuf::from("/tmp/fake.lun");
        let payload = get_figure_payload(&conn, &cart, fig).unwrap();
        assert_eq!(payload.caption, "A diagram");
        assert_eq!(payload.image_ulid.as_deref(), Some(img));
        assert_eq!(payload.storage.as_deref(), Some("embedded"));
        assert_eq!(payload.mime_type.as_deref(), Some("image/png"));
        assert!(payload.bytes_base64.is_some());
        assert_eq!(payload.enrichments.len(), 1);
        assert_eq!(payload.enrichments[0].extraction_type, "media_classification");
        assert_eq!(payload.enrichments[0].content, "diagram");
    }

    fn nodes_len_via_count(conn: &Connection) -> i64 {
        conn.query_row("SELECT COUNT(*) FROM doc_nodes", [], |r| r.get(0))
            .unwrap()
    }

    #[test]
    fn get_node_returns_parent_chain_root_first() {
        let Some(h) = meditations() else { return };
        // Pick the first sentence node by ULID order.
        let first_sentence_ulid: String = h
            .conn
            .query_row(
                "SELECT ulid FROM doc_nodes WHERE type='sentence' ORDER BY ulid ASC LIMIT 1",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let node = get_node(&h.conn, &first_sentence_ulid).unwrap();
        assert_eq!(node.node_type, NodeType::Sentence);
        let chain = node.parent_chain.as_ref().unwrap();
        assert!(
            !chain.is_empty(),
            "sentence should have at least one ancestor"
        );
        // Chain is root-first.
        assert_eq!(chain[0].node_type, NodeType::Document);
        // Children count for a sentence should be 0.
        assert_eq!(node.children_count, Some(0));
    }

    // Baselines below are against `07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun`
    // rebuilt 2026-05-24 from PDF against engine `cd7be64` (Haiku 4.5;
    // M-01 PDF parser fix triggered the rebuild). Haiku is non-deterministic
    // so counts drift across rebuilds — the v0.2 audit baseline was
    // 512/532/62 claims/entities/summaries; Haiku 4.5 produces richer
    // extractions, hence the bump. The "anchor_status=unknown" SPEC-001
    // invariant is unchanged.
    #[test]
    fn list_extractions_claim_counts() {
        let Some(h) = meditations() else { return };
        let all_claims =
            list_extractions(&h.conn, Some(ExtractionType::Claim), None, 2000, 0).unwrap();
        assert_eq!(all_claims.len(), 1204);
        let anchored = list_extractions(
            &h.conn,
            Some(ExtractionType::Claim),
            Some(AnchorStatus::Anchored),
            2000,
            0,
        )
        .unwrap();
        assert_eq!(anchored.len(), 1056);
        let match_failed = list_extractions(
            &h.conn,
            Some(ExtractionType::Claim),
            Some(AnchorStatus::MatchFailed),
            2000,
            0,
        )
        .unwrap();
        assert_eq!(match_failed.len(), 148);
        let unknown_claims = list_extractions(
            &h.conn,
            Some(ExtractionType::Claim),
            Some(AnchorStatus::Unknown),
            2000,
            0,
        )
        .unwrap();
        assert_eq!(
            unknown_claims.len(),
            0,
            "no claim should have anchor_status=unknown per SPEC-001"
        );
    }

    #[test]
    fn list_extractions_entity_and_summary_counts() {
        let Some(h) = meditations() else { return };
        let entities =
            list_extractions(&h.conn, Some(ExtractionType::Entity), None, 2000, 0).unwrap();
        assert_eq!(entities.len(), 1418);
        assert!(entities
            .iter()
            .all(|e| e.anchor_status == AnchorStatus::Unknown));
        let summaries =
            list_extractions(&h.conn, Some(ExtractionType::Summary), None, 2000, 0).unwrap();
        assert_eq!(summaries.len(), 145);
    }

    #[test]
    fn extraction_counts_groups() {
        let Some(h) = meditations() else { return };
        let counts = get_extraction_counts(&h.conn).unwrap();
        let lookup: HashMap<(String, String), i64> = counts
            .into_iter()
            .map(|c| ((c.extraction_type, c.anchor_status), c.count))
            .collect();
        assert_eq!(lookup.get(&("claim".into(), "anchored".into())), Some(&1056));
        assert_eq!(
            lookup.get(&("claim".into(), "match_failed".into())),
            Some(&148)
        );
        assert_eq!(lookup.get(&("entity".into(), "unknown".into())), Some(&1418));
        assert_eq!(
            lookup.get(&("summary".into(), "anchored".into())),
            Some(&145)
        );
    }

    #[test]
    fn safe_snippet_preserves_mark_and_escapes_html() {
        // No special chars
        assert_eq!(safe_snippet("plain text"), "plain text");
        // Mark tags preserved
        assert_eq!(safe_snippet("<mark>foo</mark>"), "<mark>foo</mark>");
        // < and > outside marks get escaped
        assert_eq!(safe_snippet("a < b > c"), "a &lt; b &gt; c");
        // Ampersand escaped
        assert_eq!(safe_snippet("a & b"), "a &amp; b");
        // Mixed
        assert_eq!(
            safe_snippet("<mark>a</mark> & <mark>b</mark>"),
            "<mark>a</mark> &amp; <mark>b</mark>"
        );
        // Adversarial: looks-like-mark but isn't (lowercase only is required, so MARK lowercased)
        assert_eq!(safe_snippet("<MARK>x</MARK>"), "&lt;MARK&gt;x&lt;/MARK&gt;");
        // Script tag fully escaped
        assert_eq!(
            safe_snippet("<script>alert(1)</script>"),
            "&lt;script&gt;alert(1)&lt;/script&gt;"
        );
        // UTF-8 multibyte preserved
        assert_eq!(safe_snippet("café — naïve"), "café — naïve");
        // The ellipsis we pass to FTS5 ('…') passes through cleanly
        assert_eq!(safe_snippet("…<mark>x</mark>…"), "…<mark>x</mark>…");
    }

    #[test]
    fn search_finds_virtue_and_surfaces_ulid() {
        let Some(h) = meditations() else { return };
        let hits = search(&h.conn, "virtue", 10).unwrap();
        assert!(
            !hits.is_empty(),
            "should find at least one match for 'virtue'"
        );
        for hit in &hits {
            assert!(
                hit.snippet_html.contains("<mark>"),
                "snippet missing <mark>: {:?}",
                hit.snippet_html
            );
            assert_eq!(hit.source, "fts");
            assert_eq!(
                hit.node_ulid.len(),
                26,
                "search hit must surface a 26-char ULID, got: {:?}",
                hit.node_ulid
            );
        }
        for w in hits.windows(2) {
            assert!(w[0].rank <= w[1].rank, "results not in rank order");
        }
    }

    #[test]
    fn unpack_f32_le_roundtrip() {
        let original: Vec<f32> = vec![0.5, -1.25, 3.0, 0.0, -0.000123];
        let mut blob = Vec::new();
        for v in &original {
            blob.extend_from_slice(&v.to_le_bytes());
        }
        assert_eq!(unpack_f32_le(&blob), original);
    }

    #[test]
    fn cosine_similarity_identical_is_one() {
        let a = [1.0f32, 2.0, 3.0];
        assert!((cosine_similarity(&a, &a) - 1.0).abs() < 1e-6);
    }

    #[test]
    fn cosine_similarity_orthogonal_is_zero() {
        let a = [1.0f32, 0.0];
        let b = [0.0f32, 1.0];
        assert!(cosine_similarity(&a, &b).abs() < 1e-6);
    }

    #[test]
    fn semantic_search_returns_ranked_hits_with_level() {
        let Some(h) = meditations() else { return };
        let query_vector = crate::embedder::embed_query("virtue").expect("embed_query");
        let hits = semantic_search(&h.conn, &query_vector, 10).unwrap();
        assert!(!hits.is_empty(), "should find semantic matches for 'virtue'");
        for hit in &hits {
            assert_eq!(hit.source, "semantic");
            assert!(matches!(hit.level.as_deref(), Some("paragraph") | Some("section")));
            assert_eq!(hit.node_ulid.len(), 26);
        }
        for w in hits.windows(2) {
            assert!(w[0].rank <= w[1].rank, "results not in rank order");
        }
    }

    /// Correctness check: the bundled Rust/ONNX pipeline must reproduce the
    /// same vector space the Python `SentenceTransformer` pipeline used to
    /// build the cartridge. Without this, semantic search could compile and
    /// run while silently returning meaningless rankings.
    #[test]
    fn embedding_space_matches_stored_vectors() {
        let Some(h) = meditations() else { return };
        // A known paragraph node + its exact source text (reconstructed the
        // same way the builder's embedder.py does: sentence children joined
        // by ' ', in id order).
        let node_ulid = "01KSCJC7Z3NW52390CZG8V3WPS";
        let text = "Marcus Aurelius' Meditations - tr. Casaubon v. 8.16, uploaded to www.philaletheians.co.uk, 14 July 2013 Page 1 of 128";

        let blob: Vec<u8> = h
            .conn
            .query_row(
                "SELECT vector FROM embeddings WHERE node_ulid = ?1 AND level = 'paragraph'",
                params![node_ulid],
                |r| r.get(0),
            )
            .unwrap();
        let stored = unpack_f32_le(&blob);

        let query_vector = crate::embedder::embed_query(text).expect("embed_query");
        let sim = cosine_similarity(&query_vector, &stored);
        assert!(
            sim > 0.95,
            "expected near-identical vectors for the same text, got cosine similarity {sim}"
        );
    }

    #[test]
    fn extraction_sources_for_anchored_claim() {
        let Some(h) = meditations() else { return };
        let claim_ulid: String = h
            .conn
            .query_row(
                "SELECT e.ulid FROM extractions e
             WHERE e.type='claim' AND e.anchor_status='anchored'
               AND EXISTS (SELECT 1 FROM extraction_sources es WHERE es.extraction_ulid = e.ulid)
             ORDER BY e.ulid ASC LIMIT 1",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let result = get_extraction_sources(&h.conn, &claim_ulid).unwrap();
        assert!(
            !result.sources.is_empty(),
            "anchored claim {} should have ≥1 source",
            claim_ulid
        );
        assert!(
            result.context.is_empty(),
            "Meditations has 0 extraction_context_nodes"
        );
    }

    #[test]
    fn get_node_includes_meta_json_page_num() {
        let Some(h) = meditations() else { return };
        let first_sentence_ulid: String = h
            .conn
            .query_row(
                "SELECT ulid FROM doc_nodes WHERE type='sentence' ORDER BY ulid ASC LIMIT 1",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let node = get_node(&h.conn, &first_sentence_ulid).unwrap();
        // PDF parser populates `{"page_num": N}`.
        let meta = node.meta_json.expect("meta_json should be present");
        let page = meta
            .get("page_num")
            .and_then(|v| v.as_i64())
            .expect("page_num should be a number");
        assert!(page >= 1);
    }

    #[test]
    fn ledger_genesis_visible() {
        let Some(h) = meditations() else { return };
        let (seq, event_type, actor_role): (i64, String, String) = h
            .conn
            .query_row(
                "SELECT seq, event_type, actor_role FROM annotation_ledger WHERE seq = 1",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .unwrap();
        assert_eq!(seq, 1);
        assert_eq!(event_type, "meta");
        assert_eq!(actor_role, "system");
    }

    #[test]
    fn ledger_events_empty_for_unanchored_claim() {
        let Some(h) = meditations() else { return };
        let claim_ulid: String = h
            .conn
            .query_row(
                "SELECT ulid FROM extractions WHERE type='claim' ORDER BY ulid ASC LIMIT 1",
                [],
                |r| r.get(0),
            )
            .unwrap();
        // No ambassador upgrades have been written yet (engine 407122f only
        // landed core ledger + genesis); every claim ULID returns an empty
        // event list until slice #2 wires the upgrade flow.
        let events = get_ledger_events(&h.conn, &claim_ulid).unwrap();
        assert!(events.is_empty(), "expected no events, got {}", events.len());
        let ts = get_latest_event_ts(&h.conn, &claim_ulid).unwrap();
        assert!(ts.is_none());
    }
}
