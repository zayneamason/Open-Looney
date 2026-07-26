use serde::{Deserialize, Serialize};
use std::collections::HashMap;

pub type HandleId = u64;

#[derive(Serialize)]
pub struct Meta {
    pub title: Option<String>,
    pub source_filename: Option<String>,
    pub source_format: Option<String>,
    pub source_hash: Option<String>,
    pub word_count: Option<i64>,
    pub node_count: Option<i64>,
    pub created_at: Option<String>,
    pub format_version: Option<String>,
    pub cartridge_kind: Option<String>,
    pub embedding_model: Option<String>,
    pub embedding_dim: Option<i64>,
    pub logprob_base: Option<String>,
    pub logprob_attribution: Option<String>,
    pub ledger_hash_algorithm: Option<String>,
    pub ledger_genesis_ulid: Option<String>,
    pub ledger_head_seq: Option<i64>,
    pub ledger_head_hash: Option<String>,
    pub extra: HashMap<String, String>,
}

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum NodeType {
    Document,
    Section,
    Paragraph,
    Sentence,
    List,
    ListItem,
    Figure,
    Image,
    Table,
    Row,
    Cell,
}

impl NodeType {
    pub fn as_sql_str(&self) -> &'static str {
        match self {
            NodeType::Document => "document",
            NodeType::Section => "section",
            NodeType::Paragraph => "paragraph",
            NodeType::Sentence => "sentence",
            NodeType::List => "list",
            NodeType::ListItem => "list_item",
            NodeType::Figure => "figure",
            NodeType::Image => "image",
            NodeType::Table => "table",
            NodeType::Row => "row",
            NodeType::Cell => "cell",
        }
    }

    pub fn from_sql_str(s: &str) -> Option<Self> {
        match s {
            "document" => Some(NodeType::Document),
            "section" => Some(NodeType::Section),
            "paragraph" => Some(NodeType::Paragraph),
            "sentence" => Some(NodeType::Sentence),
            "list" => Some(NodeType::List),
            "list_item" => Some(NodeType::ListItem),
            "figure" => Some(NodeType::Figure),
            "image" => Some(NodeType::Image),
            "table" => Some(NodeType::Table),
            "row" => Some(NodeType::Row),
            "cell" => Some(NodeType::Cell),
            _ => None,
        }
    }
}

#[derive(Serialize)]
pub struct DocNode {
    pub ulid: String,
    pub parent_ulid: Option<String>,
    pub node_type: NodeType,
    pub position: i64,
    pub content: String,
    pub meta_json: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub children_count: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_chain: Option<Vec<DocNodeBrief>>,
}

#[derive(Serialize)]
pub struct DocNodeBrief {
    pub ulid: String,
    pub node_type: NodeType,
    pub position: i64,
    pub content_preview: String,
}

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ExtractionType {
    Claim,
    Entity,
    Summary,
    MediaClassification,
    VisualDescription,
    FigureDiscourse,
}

impl ExtractionType {
    pub fn as_sql_str(&self) -> &'static str {
        match self {
            ExtractionType::Claim => "claim",
            ExtractionType::Entity => "entity",
            ExtractionType::Summary => "summary",
            ExtractionType::MediaClassification => "media_classification",
            ExtractionType::VisualDescription => "visual_description",
            ExtractionType::FigureDiscourse => "figure_discourse",
        }
    }

    pub fn from_sql_str(s: &str) -> Option<Self> {
        match s {
            "claim" => Some(ExtractionType::Claim),
            "entity" => Some(ExtractionType::Entity),
            "summary" => Some(ExtractionType::Summary),
            "media_classification" => Some(ExtractionType::MediaClassification),
            "visual_description" => Some(ExtractionType::VisualDescription),
            "figure_discourse" => Some(ExtractionType::FigureDiscourse),
            _ => None,
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AnchorStatus {
    Anchored,
    Synthesized,
    MatchFailed,
    Filtered,
    Unknown,
}

impl AnchorStatus {
    pub fn as_sql_str(&self) -> &'static str {
        match self {
            AnchorStatus::Anchored => "anchored",
            AnchorStatus::Synthesized => "synthesized",
            AnchorStatus::MatchFailed => "match_failed",
            AnchorStatus::Filtered => "filtered",
            AnchorStatus::Unknown => "unknown",
        }
    }

    pub fn from_sql_str(s: &str) -> Option<Self> {
        match s {
            "anchored" => Some(AnchorStatus::Anchored),
            "synthesized" => Some(AnchorStatus::Synthesized),
            "match_failed" => Some(AnchorStatus::MatchFailed),
            "filtered" => Some(AnchorStatus::Filtered),
            "unknown" => Some(AnchorStatus::Unknown),
            _ => None,
        }
    }
}

#[derive(Serialize)]
pub struct Extraction {
    pub ulid: String,
    pub extraction_type: ExtractionType,
    pub content: String,
    pub anchor_status: AnchorStatus,
    pub anchor_reason: Option<String>,
    pub extraction_method: String,
    pub llm_logprob_sum: Option<f64>,
    pub llm_token_count: Option<i64>,
}

#[derive(Serialize)]
pub struct ExtractionSource {
    pub node: DocNode,
    pub anchor_method: Option<String>,
    pub anchored_by: Option<String>,
    pub anchored_at: Option<i64>,
    pub event_id: Option<String>,
}

#[derive(Serialize)]
pub struct ContextNode {
    pub node: DocNode,
    pub relevance: f64,
}

#[derive(Serialize)]
pub struct ExtractionSourcesResult {
    pub sources: Vec<ExtractionSource>,
    pub context: Vec<ContextNode>,
}

#[derive(Serialize)]
pub struct ExtractionCount {
    pub extraction_type: String,
    pub anchor_status: String,
    pub count: i64,
}

#[derive(Serialize)]
pub struct SearchHit {
    pub node_ulid: String,
    pub snippet_html: String,
    pub rank: f64,
    /// Source discriminator: `"fts"` (keyword) or `"semantic"`.
    pub source: String,
    /// Embedding granularity that matched ("paragraph" / "section"). Only
    /// set for `source == "semantic"`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub level: Option<String>,
}

#[derive(Serialize)]
pub struct TrustAxes {
    pub authority: Option<f64>,
    pub contestation: Option<f64>,
    pub temporal: Option<f64>,
    pub resonance: Option<f64>,
}

#[derive(Serialize)]
pub struct TrustVector {
    pub spec_version: String,
    pub composer_id: String,
    pub composer_version: String,
    pub target_ulid: String,
    pub computed_at: String,
    pub axes: TrustAxes,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub notes: Option<String>,
}

#[derive(Serialize)]
pub struct LedgerEvent {
    pub seq: i64,
    pub ulid: String,
    pub entry_ts: i64,
    pub event_type: String,
    pub actor_id: String,
    pub actor_role: String,
    pub actor_display_name: Option<String>,
    pub target_kind: Option<String>,
    pub target_ulid: Option<String>,
    pub target_cartridge_ulid: Option<String>,
    pub payload: serde_json::Value,
    pub prev_hash: Option<String>,
    pub entry_hash: String,
}

/// One figure enrichment row (media_classification, visual_description, figure_discourse, …).
#[derive(Serialize, Clone, Debug)]
pub struct FigureEnrichment {
    pub ulid: String,
    pub extraction_type: String,
    pub content: String,
    pub anchored_at: Option<i64>,
}

/// Resolved figure media + enrichments for Reader display / inspector.
#[derive(Serialize, Clone, Debug)]
pub struct FigurePayload {
    pub figure_ulid: String,
    pub caption: String,
    pub image_ulid: Option<String>,
    pub mime_type: Option<String>,
    pub sha256: Option<String>,
    pub byte_len: Option<i64>,
    pub storage: Option<String>,
    /// Absolute path when storage is external (resolved beside the .lun).
    pub external_path_resolved: Option<String>,
    /// Raw bytes as base64 (embedded blob, or file read for external).
    pub bytes_base64: Option<String>,
    pub enrichments: Vec<FigureEnrichment>,
}
