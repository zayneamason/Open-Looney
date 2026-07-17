export type HandleId = number;

export type ReaderError =
  | { kind: "not_a_sqlite_file" }
  | { kind: "wrong_family"; actual_id: number; family_hint: string }
  | { kind: "unsupported_version"; actual: number }
  | { kind: "unsupported_cartridge_kind"; actual: string }
  | { kind: "unsupported_attribution"; base: string | null; attr: string | null }
  | { kind: "missing_required_meta"; key: string }
  | { kind: "unsupported_hash_algorithm"; actual: string }
  | { kind: "ledger_integrity"; detail: string }
  | { kind: "sqlite_error"; message: string }
  | { kind: "invalid_handle"; handle: number }
  | { kind: "io_error"; message: string };

export interface Meta {
  title: string | null;
  source_filename: string | null;
  source_format: string | null;
  source_hash: string | null;
  word_count: number | null;
  node_count: number | null;
  created_at: string | null;
  format_version: string | null;
  cartridge_kind: string | null;
  embedding_model: string | null;
  embedding_dim: number | null;
  logprob_base: string | null;
  logprob_attribution: string | null;
  ledger_hash_algorithm: string | null;
  ledger_genesis_ulid: string | null;
  ledger_head_seq: number | null;
  ledger_head_hash: string | null;
  extra: Record<string, string>;
}

export type NodeType =
  | "document"
  | "section"
  | "paragraph"
  | "sentence"
  | "list"
  | "list_item"
  | "figure"
  | "table"
  | "row"
  | "cell";

export interface DocNode {
  ulid: string;
  parent_ulid: string | null;
  node_type: NodeType;
  position: number;
  content: string;
  meta_json: Record<string, unknown> | null;
  children_count?: number;
  parent_chain?: DocNodeBrief[];
}

export interface DocNodeBrief {
  ulid: string;
  node_type: NodeType;
  position: number;
  content_preview: string;
}

export type ExtractionType = "claim" | "entity" | "summary";
export type AnchorStatus =
  | "anchored"
  | "synthesized"
  | "match_failed"
  | "filtered"
  | "unknown";

export interface Extraction {
  ulid: string;
  extraction_type: ExtractionType;
  content: string;
  anchor_status: AnchorStatus;
  anchor_reason: string | null;
  extraction_method: string;
  llm_logprob_sum: number | null;
  llm_token_count: number | null;
}

export interface ExtractionSource {
  node: DocNode;
  anchor_method: string | null;
  anchored_by: string | null;
  anchored_at: number | null;
  event_id: string | null;
}

export interface ContextNode {
  node: DocNode;
  relevance: number;
}

export interface ExtractionSourcesResult {
  sources: ExtractionSource[];
  context: ContextNode[];
}

export interface ExtractionCount {
  extraction_type: string;
  anchor_status: string;
  count: number;
}

export interface SearchHit {
  node_ulid: string;
  snippet_html: string;
  rank: number;
  /** Source discriminator. Always "fts" in v1; v2 may add "semantic" / "hybrid". */
  source: string;
}

export interface TrustAxes {
  authority: number | null;
  contestation: number | null;
  temporal: number | null;
  resonance: number | null;
}

export interface TrustVector {
  spec_version: string;
  composer_id: string;
  composer_version: string;
  target_ulid: string;
  computed_at: string;
  axes: TrustAxes;
  notes?: string;
}

export type LedgerEventType =
  | "claim_anchored"
  | "claim_disputed"
  | "claim_filtered"
  | "claim_reconciled"
  | "summary_overridden"
  | "cartridge_reviewed"
  | "cartridge_imported"
  | "meta";

export type LedgerActorRole = "owner" | "ambassador" | "elder" | "oracle" | "system";

export interface LedgerEvent {
  seq: number;
  ulid: string;
  entry_ts: number;
  event_type: LedgerEventType;
  actor_id: string;
  actor_role: LedgerActorRole;
  actor_display_name: string | null;
  target_kind: string | null;
  target_ulid: string | null;
  target_cartridge_ulid: string | null;
  payload: unknown;
  prev_hash: string | null;
  entry_hash: string;
}

// --- SPEC-007 SketchedShelf ------------------------------------------------

export type SketchKind =
  | "extraction_ulid"
  | "node_ulid"
  | "entity_surface"
  | "fts_term";

export interface ShelfSummary {
  count: number;
  paths: string[];
  /** Per-cartridge populated kinds, alphabetized. Empty inner array ⇒
   * cartridge has no sketches. */
  sketches_per_cartridge: string[][];
}

/**
 * SPEC-007 candidate verdict, post-sketch and post-verify-by-opening.
 *
 *  - `probable`       — sketch said yes; verify not run yet (in-flight).
 *  - `unknown`        — cartridge has no sketch of this kind; consumer falls back.
 *  - `confirmed`      — sketch said yes AND verify found the item.
 *  - `false_positive` — sketch said yes BUT verify did not find it.
 *
 * Cartridges where the sketch says "definitely not" are omitted from the
 * result list entirely. The four statuses above are the only candidates
 * the consumer sees.
 */
export type CandidateStatus =
  | "probable"
  | "unknown"
  | "confirmed"
  | "false_positive";

export interface CandidateResult {
  path: string;
  status: CandidateStatus;
}
