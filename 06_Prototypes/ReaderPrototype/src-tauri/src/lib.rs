mod cartridge;
mod embedder;
mod error;
mod queries;
mod shelf;
mod trust;
mod types;

use cartridge::{open_and_validate, CartridgeHandle};
use error::ReaderError;
use shelf::{open_shelf_cartridge, CandidateResult, ShelfCartridge, ShelfSummary, SketchKind};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use tauri::State;
use types::{
    AnchorStatus, DocNode, Extraction, ExtractionCount, ExtractionSourcesResult, ExtractionType,
    FigurePayload, HandleId, LedgerEvent, Meta, NodeType, SearchHit, TrustVector,
};

pub struct AppState {
    handles: Mutex<HashMap<HandleId, CartridgeHandle>>,
    next_id: AtomicU64,
    // SPEC-007: at most one open shelf at a time (replaces on re-open).
    shelf: Mutex<Vec<ShelfCartridge>>,
}

#[tauri::command]
fn ping() -> &'static str {
    "pong"
}

#[tauri::command]
fn open_cartridge(state: State<'_, AppState>, path: String) -> Result<HandleId, ReaderError> {
    let pb = PathBuf::from(path);
    let handle = open_and_validate(&pb)?;
    let id = state.next_id.fetch_add(1, Ordering::SeqCst);
    state.handles.lock().unwrap().insert(id, handle);
    Ok(id)
}

#[tauri::command]
fn close_cartridge(state: State<'_, AppState>, handle: HandleId) -> Result<(), ReaderError> {
    let mut guard = state.handles.lock().unwrap();
    guard
        .remove(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    Ok(())
}

#[tauri::command]
fn get_meta(state: State<'_, AppState>, handle: HandleId) -> Result<Meta, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    queries::get_meta(&h.conn)
}

#[tauri::command]
fn list_nodes(
    state: State<'_, AppState>,
    handle: HandleId,
    parent_ulid: Option<String>,
    type_filter: Option<NodeType>,
    limit: i64,
    offset: i64,
) -> Result<Vec<DocNode>, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    queries::list_nodes(&h.conn, parent_ulid, type_filter, limit, offset)
}

#[tauri::command]
fn list_all_nodes(
    state: State<'_, AppState>,
    handle: HandleId,
) -> Result<Vec<DocNode>, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    queries::list_all_nodes(&h.conn)
}

#[tauri::command]
fn get_node(
    state: State<'_, AppState>,
    handle: HandleId,
    node_ulid: String,
) -> Result<DocNode, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    queries::get_node(&h.conn, &node_ulid)
}

#[tauri::command]
fn get_figure_payload(
    state: State<'_, AppState>,
    handle: HandleId,
    figure_ulid: String,
) -> Result<FigurePayload, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    queries::get_figure_payload(&h.conn, &h.path, &figure_ulid)
}

#[tauri::command]
fn list_extractions(
    state: State<'_, AppState>,
    handle: HandleId,
    type_filter: Option<ExtractionType>,
    anchor_status_filter: Option<AnchorStatus>,
    limit: i64,
    offset: i64,
) -> Result<Vec<Extraction>, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    queries::list_extractions(&h.conn, type_filter, anchor_status_filter, limit, offset)
}

#[tauri::command]
fn get_extraction(
    state: State<'_, AppState>,
    handle: HandleId,
    extraction_ulid: String,
) -> Result<Option<Extraction>, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    queries::get_extraction(&h.conn, &extraction_ulid)
}

#[tauri::command]
fn find_extraction_by_content(
    state: State<'_, AppState>,
    handle: HandleId,
    content: String,
) -> Result<Option<Extraction>, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    queries::find_extraction_by_content(&h.conn, &content)
}

#[tauri::command]
fn get_extraction_counts(
    state: State<'_, AppState>,
    handle: HandleId,
) -> Result<Vec<ExtractionCount>, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    queries::get_extraction_counts(&h.conn)
}

#[tauri::command]
fn get_extraction_sources(
    state: State<'_, AppState>,
    handle: HandleId,
    extraction_ulid: String,
) -> Result<ExtractionSourcesResult, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    queries::get_extraction_sources(&h.conn, &extraction_ulid)
}

#[tauri::command]
fn get_ledger_events(
    state: State<'_, AppState>,
    handle: HandleId,
    target_ulid: String,
) -> Result<Vec<LedgerEvent>, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    queries::get_ledger_events(&h.conn, &target_ulid)
}

#[tauri::command]
fn get_latest_event_ts(
    state: State<'_, AppState>,
    handle: HandleId,
    target_ulid: String,
) -> Result<Option<i64>, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    queries::get_latest_event_ts(&h.conn, &target_ulid)
}

#[tauri::command]
fn compose_trust_vector(
    state: State<'_, AppState>,
    handle: HandleId,
    target_ulid: String,
) -> Result<TrustVector, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    trust::compose(&h.conn, &target_ulid)
}

#[tauri::command]
fn compose_trust_vectors_batch(
    state: State<'_, AppState>,
    handle: HandleId,
    target_ulids: Vec<String>,
) -> Result<Vec<TrustVector>, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    trust::compose_batch(&h.conn, &target_ulids)
}

#[tauri::command]
fn search(
    state: State<'_, AppState>,
    handle: HandleId,
    query: String,
    limit: i64,
) -> Result<Vec<SearchHit>, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    queries::search(&h.conn, &query, limit)
}

#[tauri::command]
fn semantic_search(
    state: State<'_, AppState>,
    handle: HandleId,
    query: String,
    limit: i64,
) -> Result<Vec<SearchHit>, ReaderError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(ReaderError::InvalidHandle { handle })?;
    let meta = queries::get_meta(&h.conn)?;
    if meta.embedding_model.as_deref() != Some(embedder::EXPECTED_MODEL_NAME)
        || meta.embedding_dim != Some(embedder::EXPECTED_DIM)
    {
        return Err(ReaderError::UnsupportedEmbeddingModel {
            actual_model: meta.embedding_model,
            actual_dim: meta.embedding_dim,
        });
    }
    let query_vector = embedder::embed_query(&query)?;
    queries::semantic_search(&h.conn, &query_vector, limit)
}

// --- SPEC-007 shelf commands -----------------------------------------------

#[tauri::command]
fn open_shelf(
    state: State<'_, AppState>,
    paths: Vec<String>,
) -> Result<ShelfSummary, ReaderError> {
    // Atomic open: build the new shelf first, only swap if every cartridge opens.
    let mut new_shelf: Vec<ShelfCartridge> = Vec::with_capacity(paths.len());
    for p in &paths {
        let pb = PathBuf::from(p);
        new_shelf.push(open_shelf_cartridge(&pb)?);
    }
    let summary = ShelfSummary {
        count: new_shelf.len(),
        paths: new_shelf
            .iter()
            .map(|c| c.path.to_string_lossy().into_owned())
            .collect(),
        sketches_per_cartridge: new_shelf
            .iter()
            .map(|c| c.sketches_present.clone())
            .collect(),
    };
    let mut guard = state.shelf.lock().unwrap();
    *guard = new_shelf;
    Ok(summary)
}

#[tauri::command]
fn close_shelf(state: State<'_, AppState>) -> Result<(), ReaderError> {
    state.shelf.lock().unwrap().clear();
    Ok(())
}

#[tauri::command]
fn shelf_filter_candidates(
    state: State<'_, AppState>,
    item: String,
    kind: SketchKind,
) -> Result<Vec<CandidateResult>, ReaderError> {
    let guard = state.shelf.lock().unwrap();
    Ok(shelf::filter_candidates(&guard, &item, kind))
}

/// SPEC-007 § 7.3.3 verify-by-opening pass. Opens the cartridge at `path`,
/// runs the precise query for the kind, returns `Confirmed` or
/// `FalsePositive`. Does NOT consult the open shelf — each verify call is
/// an independent open-query-close cycle so the call is safe to make
/// against any path on disk, not just shelf members.
#[tauri::command]
fn shelf_verify_candidate(
    path: String,
    item: String,
    kind: SketchKind,
) -> Result<shelf::CandidateStatus, ReaderError> {
    let pb = PathBuf::from(path);
    shelf::verify_candidate(&pb, &item, kind)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let state = AppState {
        handles: Mutex::new(HashMap::new()),
        next_id: AtomicU64::new(0),
        shelf: Mutex::new(Vec::new()),
    };
    tauri::Builder::default()
        .manage(state)
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            ping,
            open_cartridge,
            close_cartridge,
            get_meta,
            list_nodes,
            list_all_nodes,
            get_node,
            get_figure_payload,
            list_extractions,
            get_extraction,
            find_extraction_by_content,
            get_extraction_counts,
            get_extraction_sources,
            get_ledger_events,
            get_latest_event_ts,
            compose_trust_vector,
            compose_trust_vectors_batch,
            search,
            semantic_search,
            open_shelf,
            close_shelf,
            shelf_filter_candidates,
            shelf_verify_candidate
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
