use serde::Serialize;

#[derive(Debug, thiserror::Error, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ReaderError {
    #[error("not a SQLite file")]
    NotASqliteFile,

    #[error("wrong family: 0x{actual_id:08X}")]
    WrongFamily { actual_id: u32, family_hint: String },

    #[error("unsupported user_version: {actual}")]
    UnsupportedVersion { actual: i32 },

    #[error("unsupported cartridge_kind: {actual}")]
    UnsupportedCartridgeKind { actual: String },

    #[error("unsupported attribution")]
    UnsupportedAttribution {
        base: Option<String>,
        attr: Option<String>,
    },

    #[error("missing required meta: {key}")]
    MissingRequiredMeta { key: String },

    #[error("unsupported ledger hash algorithm: {actual}")]
    UnsupportedHashAlgorithm { actual: String },

    #[error("ledger integrity: {detail}")]
    LedgerIntegrity { detail: String },

    #[error("sqlite: {message}")]
    SqliteError { message: String },

    #[error("invalid handle: {handle}")]
    InvalidHandle { handle: u64 },

    #[error("io: {message}")]
    IoError { message: String },
}

impl From<rusqlite::Error> for ReaderError {
    fn from(e: rusqlite::Error) -> Self {
        ReaderError::SqliteError {
            message: e.to_string(),
        }
    }
}

impl From<std::io::Error> for ReaderError {
    fn from(e: std::io::Error) -> Self {
        ReaderError::IoError {
            message: e.to_string(),
        }
    }
}
