mod error;
mod matrix;
mod queries;
mod types;

use crate::error::LunmError;
use crate::matrix::open_and_validate;
use crate::types::{
    LunmHealthReport, LunmOverview, MatrixHandle, MatrixHandleId, MemoryNodeFilters,
    ProfileConfigRow, TableRow,
};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use tauri::State;

pub struct AppState {
    handles: Mutex<HashMap<MatrixHandleId, MatrixHandle>>,
    next_id: AtomicU64,
}

#[tauri::command]
fn ping() -> &'static str {
    "pong"
}

#[tauri::command]
fn open_lunm_matrix(state: State<'_, AppState>, path: String) -> Result<MatrixHandleId, LunmError> {
    let handle = open_and_validate(&PathBuf::from(path))?;
    let id = state.next_id.fetch_add(1, Ordering::SeqCst);
    state.handles.lock().unwrap().insert(id, handle);
    Ok(id)
}

#[tauri::command]
fn close_lunm_matrix(state: State<'_, AppState>, handle: MatrixHandleId) -> Result<(), LunmError> {
    state
        .handles
        .lock()
        .unwrap()
        .remove(&handle)
        .ok_or(LunmError::InvalidHandle { handle })?;
    Ok(())
}

#[tauri::command]
fn get_lunm_overview(
    state: State<'_, AppState>,
    handle: MatrixHandleId,
) -> Result<LunmOverview, LunmError> {
    with_handle(&state, handle, |h| queries::get_lunm_overview(&h.conn, &h.path))
}

#[tauri::command]
fn get_lunm_health(
    state: State<'_, AppState>,
    handle: MatrixHandleId,
) -> Result<LunmHealthReport, LunmError> {
    with_handle(&state, handle, |h| queries::get_lunm_health(&h.conn))
}

#[tauri::command]
fn list_memory_nodes(
    state: State<'_, AppState>,
    handle: MatrixHandleId,
    filters: MemoryNodeFilters,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    with_handle(&state, handle, |h| {
        queries::list_memory_nodes(&h.conn, filters, limit, offset)
    })
}

#[tauri::command]
fn list_graph_edges(
    state: State<'_, AppState>,
    handle: MatrixHandleId,
    node_id: Option<String>,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    with_handle(&state, handle, |h| {
        queries::list_graph_edges(&h.conn, node_id, limit, offset)
    })
}

#[tauri::command]
fn list_sessions(
    state: State<'_, AppState>,
    handle: MatrixHandleId,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    with_handle(&state, handle, |h| queries::list_sessions(&h.conn, limit, offset))
}

#[tauri::command]
fn list_conversation_turns(
    state: State<'_, AppState>,
    handle: MatrixHandleId,
    session_id: Option<String>,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    with_handle(&state, handle, |h| {
        queries::list_conversation_turns(&h.conn, session_id, limit, offset)
    })
}

#[tauri::command]
fn list_nexus_registry(
    state: State<'_, AppState>,
    handle: MatrixHandleId,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    with_handle(&state, handle, |h| {
        queries::list_nexus_registry(&h.conn, limit, offset)
    })
}

#[tauri::command]
fn list_nexus_nodes(
    state: State<'_, AppState>,
    handle: MatrixHandleId,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    with_handle(&state, handle, |h| {
        queries::list_nexus_nodes(&h.conn, limit, offset)
    })
}

#[tauri::command]
fn list_nexus_edges(
    state: State<'_, AppState>,
    handle: MatrixHandleId,
    limit: i64,
    offset: i64,
) -> Result<Vec<TableRow>, LunmError> {
    with_handle(&state, handle, |h| {
        queries::list_nexus_edges(&h.conn, limit, offset)
    })
}

#[tauri::command]
fn list_profile_config(
    state: State<'_, AppState>,
    handle: MatrixHandleId,
    prefix: Option<String>,
    limit: i64,
    offset: i64,
) -> Result<Vec<ProfileConfigRow>, LunmError> {
    with_handle(&state, handle, |h| {
        queries::list_profile_config(&h.conn, prefix, limit, offset)
    })
}

fn with_handle<T>(
    state: &State<'_, AppState>,
    handle: MatrixHandleId,
    f: impl FnOnce(&MatrixHandle) -> Result<T, LunmError>,
) -> Result<T, LunmError> {
    let guard = state.handles.lock().unwrap();
    let h = guard
        .get(&handle)
        .ok_or(LunmError::InvalidHandle { handle })?;
    f(h)
}

pub fn run() {
    let state = AppState {
        handles: Mutex::new(HashMap::new()),
        next_id: AtomicU64::new(1),
    };
    tauri::Builder::default()
        .manage(state)
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            ping,
            open_lunm_matrix,
            close_lunm_matrix,
            get_lunm_overview,
            get_lunm_health,
            list_memory_nodes,
            list_graph_edges,
            list_sessions,
            list_conversation_turns,
            list_nexus_registry,
            list_nexus_nodes,
            list_nexus_edges,
            list_profile_config
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
