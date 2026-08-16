use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

pub type MatrixHandleId = u64;

#[derive(Debug)]
pub struct MatrixHandle {
    pub path: String,
    pub conn: rusqlite::Connection,
}

#[derive(Debug, Clone, Serialize)]
pub struct ProfileConfigRow {
    pub key: String,
    pub value: String,
    pub value_type: String,
    pub updated_at: Option<String>,
    pub updated_by: Option<String>,
    pub description: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TableCount {
    pub table: String,
    pub count: Option<i64>,
    pub present: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct LunmOverview {
    pub path: String,
    pub application_id: u32,
    pub user_version: i64,
    pub format_version: Option<String>,
    pub matrix_ulid: Option<String>,
    pub created_at: Option<String>,
    pub engine_version: Option<String>,
    pub header_rows: Vec<ProfileConfigRow>,
    pub table_counts: Vec<TableCount>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HealthLevel {
    Ok,
    Warning,
    Error,
}

#[derive(Debug, Clone, Serialize)]
pub struct HealthCheck {
    pub level: HealthLevel,
    pub code: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct LunmHealthReport {
    pub checks: Vec<HealthCheck>,
    pub error_count: usize,
    pub warning_count: usize,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MemoryNodeFilters {
    pub node_type: Option<String>,
    pub classification: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TableRow {
    pub values: BTreeMap<String, Value>,
}
