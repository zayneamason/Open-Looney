use serde::Serialize;
use thiserror::Error;

#[allow(dead_code)]
#[derive(Debug, Error, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum LunmError {
    #[error("not a SQLite file")]
    NotSqlite,
    #[error("wrong family: {actual_id}")]
    WrongFamily {
        actual_id: u32,
        family_hint: String,
    },
    #[error("unsupported user_version: {actual}")]
    UnsupportedUserVersion { actual: i64 },
    #[error("missing format-invariant table: {table}")]
    MissingFormatInvariantTable { table: String },
    #[error("missing format-invariant column: {table}.{column}")]
    MissingFormatInvariantColumn { table: String, column: String },
    #[error("invalid matrix handle: {handle}")]
    InvalidHandle { handle: u64 },
    #[error("SQLite error: {message}")]
    SqliteError { message: String },
    #[error("I/O error: {message}")]
    IoError { message: String },
}

impl From<rusqlite::Error> for LunmError {
    fn from(value: rusqlite::Error) -> Self {
        LunmError::SqliteError {
            message: value.to_string(),
        }
    }
}

impl From<std::io::Error> for LunmError {
    fn from(value: std::io::Error) -> Self {
        LunmError::IoError {
            message: value.to_string(),
        }
    }
}
