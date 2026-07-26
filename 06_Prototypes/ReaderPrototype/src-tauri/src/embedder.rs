//! Query-time embedding for semantic search.
//!
//! Vendors a fp32 ONNX export of `sentence-transformers/all-MiniLM-L6-v2`
//! (from `Xenova/all-MiniLM-L6-v2`) directly into the binary via
//! `include_bytes!`, so the model is available offline with no download and
//! no separate runtime asset. ONNX Runtime itself links statically into the
//! compiled binary (via fastembed's default `ort-download-binaries`
//! feature) — there is no dylib to bundle or bootstrap.
//!
//! Parity with the cartridge builder's Python `SentenceTransformer` pipeline
//! (mean pooling + L2 normalize) is verified in `queries::tests::
//! embedding_space_matches_stored_vectors`.

use crate::error::ReaderError;
use fastembed::{InitOptionsUserDefined, Pooling, TextEmbedding, TokenizerFiles, UserDefinedEmbeddingModel};
use std::sync::{Mutex, OnceLock};

pub const EXPECTED_MODEL_NAME: &str = "all-MiniLM-L6-v2";
pub const EXPECTED_DIM: i64 = 384;

const ONNX_FILE: &[u8] = include_bytes!("../models/all-MiniLM-L6-v2/model.onnx");
const TOKENIZER_FILE: &[u8] = include_bytes!("../models/all-MiniLM-L6-v2/tokenizer.json");
const CONFIG_FILE: &[u8] = include_bytes!("../models/all-MiniLM-L6-v2/config.json");
const SPECIAL_TOKENS_MAP_FILE: &[u8] =
    include_bytes!("../models/all-MiniLM-L6-v2/special_tokens_map.json");
const TOKENIZER_CONFIG_FILE: &[u8] =
    include_bytes!("../models/all-MiniLM-L6-v2/tokenizer_config.json");

static EMBEDDER: OnceLock<Mutex<TextEmbedding>> = OnceLock::new();

fn init_embedder() -> Result<Mutex<TextEmbedding>, ReaderError> {
    let tokenizer_files = TokenizerFiles {
        tokenizer_file: TOKENIZER_FILE.to_vec(),
        config_file: CONFIG_FILE.to_vec(),
        special_tokens_map_file: SPECIAL_TOKENS_MAP_FILE.to_vec(),
        tokenizer_config_file: TOKENIZER_CONFIG_FILE.to_vec(),
    };
    let model = UserDefinedEmbeddingModel::new(ONNX_FILE.to_vec(), tokenizer_files)
        .with_pooling(Pooling::Mean);
    let embedding = TextEmbedding::try_new_from_user_defined(model, InitOptionsUserDefined::new())
        .map_err(|e| ReaderError::EmbeddingError {
            message: e.to_string(),
        })?;
    Ok(Mutex::new(embedding))
}

fn get_embedder() -> Result<&'static Mutex<TextEmbedding>, ReaderError> {
    if let Some(m) = EMBEDDER.get() {
        return Ok(m);
    }
    let m = init_embedder()?;
    Ok(EMBEDDER.get_or_init(|| m))
}

/// Embeds `text` with the bundled MiniLM model. Returns an L2-normalized
/// 384-dim vector in the same space as the cartridge's stored embeddings.
pub fn embed_query(text: &str) -> Result<Vec<f32>, ReaderError> {
    let embedder = get_embedder()?;
    let mut guard = embedder
        .lock()
        .map_err(|_| ReaderError::EmbeddingError {
            message: "embedder lock poisoned".to_string(),
        })?;
    let mut out = guard
        .embed(vec![text.to_string()], None)
        .map_err(|e| ReaderError::EmbeddingError {
            message: e.to_string(),
        })?;
    out.pop().ok_or_else(|| ReaderError::EmbeddingError {
        message: "embedder returned no vector".to_string(),
    })
}
