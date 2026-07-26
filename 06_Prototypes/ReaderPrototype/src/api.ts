import { invoke } from "@tauri-apps/api/core";
import type {
  AnchorStatus,
  CandidateResult,
  CandidateStatus,
  DocNode,
  Extraction,
  ExtractionCount,
  ExtractionSourcesResult,
  ExtractionType,
  FigurePayload,
  HandleId,
  LedgerEvent,
  Meta,
  NodeType,
  SearchHit,
  ShelfSummary,
  SketchKind,
  TrustVector,
} from "./types";

async function call<T>(cmd: string, args: Record<string, unknown> = {}): Promise<T> {
  return await invoke<T>(cmd, args);
}

export const api = {
  ping: () => call<string>("ping"),

  openCartridge: (path: string) => call<HandleId>("open_cartridge", { path }),
  closeCartridge: (handle: HandleId) => call<void>("close_cartridge", { handle }),
  getMeta: (handle: HandleId) => call<Meta>("get_meta", { handle }),

  listNodes: (
    handle: HandleId,
    parentUlid: string | null,
    typeFilter: NodeType | null,
    limit: number,
    offset: number,
  ) =>
    call<DocNode[]>("list_nodes", {
      handle,
      parentUlid,
      typeFilter,
      limit,
      offset,
    }),

  listAllNodes: (handle: HandleId) =>
    call<DocNode[]>("list_all_nodes", { handle }),

  getNode: (handle: HandleId, nodeUlid: string) =>
    call<DocNode>("get_node", { handle, nodeUlid }),

  getFigurePayload: (handle: HandleId, figureUlid: string) =>
    call<FigurePayload>("get_figure_payload", { handle, figureUlid }),

  listExtractions: (
    handle: HandleId,
    typeFilter: ExtractionType | null,
    anchorStatusFilter: AnchorStatus | null,
    limit: number,
    offset: number,
  ) =>
    call<Extraction[]>("list_extractions", {
      handle,
      typeFilter,
      anchorStatusFilter,
      limit,
      offset,
    }),

  getExtraction: (handle: HandleId, extractionUlid: string) =>
    call<Extraction | null>("get_extraction", { handle, extractionUlid }),

  findExtractionByContent: (handle: HandleId, content: string) =>
    call<Extraction | null>("find_extraction_by_content", { handle, content }),

  getExtractionCounts: (handle: HandleId) =>
    call<ExtractionCount[]>("get_extraction_counts", { handle }),

  getExtractionSources: (handle: HandleId, extractionUlid: string) =>
    call<ExtractionSourcesResult>("get_extraction_sources", {
      handle,
      extractionUlid,
    }),

  getLedgerEvents: (handle: HandleId, targetUlid: string) =>
    call<LedgerEvent[]>("get_ledger_events", { handle, targetUlid }),

  getLatestEventTs: (handle: HandleId, targetUlid: string) =>
    call<number | null>("get_latest_event_ts", { handle, targetUlid }),

  composeTrustVector: (handle: HandleId, targetUlid: string) =>
    call<TrustVector>("compose_trust_vector", { handle, targetUlid }),

  composeTrustVectorsBatch: (handle: HandleId, targetUlids: string[]) =>
    call<TrustVector[]>("compose_trust_vectors_batch", { handle, targetUlids }),

  search: (handle: HandleId, query: string, limit: number) =>
    call<SearchHit[]>("search", { handle, query, limit }),

  semanticSearch: (handle: HandleId, query: string, limit: number) =>
    call<SearchHit[]>("semantic_search", { handle, query, limit }),

  // --- SPEC-007 SketchedShelf ---------------------------------------------

  openShelf: (paths: string[]) =>
    call<ShelfSummary>("open_shelf", { paths }),

  closeShelf: () => call<void>("close_shelf"),

  shelfFilterCandidates: (item: string, kind: SketchKind) =>
    call<CandidateResult[]>("shelf_filter_candidates", { item, kind }),

  shelfVerifyCandidate: (path: string, item: string, kind: SketchKind) =>
    call<{ status: CandidateStatus }>("shelf_verify_candidate", {
      path,
      item,
      kind,
    }),
};
