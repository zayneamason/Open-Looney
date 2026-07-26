import { create } from "zustand";
import { api } from "./api";
import { composeTrust, composeTrustBatch } from "./trust";
import type {
  AnchorStatus,
  DocNode,
  Extraction,
  ExtractionCount,
  ExtractionSourcesResult,
  ExtractionType,
  FigurePayload,
  HandleId,
  LedgerEvent,
  Meta,
  ReaderError,
  SearchHit,
  SketchKind,
  TrustVector,
} from "./types";

export function errorToText(e: unknown): string {
  if (typeof e === "object" && e !== null && "kind" in (e as Record<string, unknown>)) {
    const err = e as ReaderError;
    switch (err.kind) {
      case "not_a_sqlite_file":
        return "Not a SQLite file. Pick a .lun cartridge.";
      case "wrong_family":
        if (err.family_hint === "LUNM") {
          return "This is a runtime-matrix file (LUNM), not a knowledge cartridge. The reader only opens LUNC cartridges.";
        }
        return `Unknown application_id: 0x${err.actual_id.toString(16).padStart(8, "0").toUpperCase()}. Expected 0x4C554E43.`;
      case "unsupported_version":
        if (err.actual === 1 || err.actual === 2) {
          return `v0.${err.actual} cartridge. Migrate first: python -m luna.cartridge.migrate <path> — then reopen.`;
        }
        return `Unsupported user_version: ${err.actual}. This reader supports v0.3 only.`;
      case "unsupported_cartridge_kind":
        return `cartridge_kind = '${err.actual}'. v0.3 supports 'knowledge' only.`;
      case "unsupported_attribution":
        return `logprob_attribution='${err.attr ?? "—"}', logprob_base='${err.base ?? "—"}'. v0.3 reader only supports response_level / e.`;
      case "missing_required_meta":
        return `Cartridge missing required meta.${err.key}. Build may be incomplete or the SPEC-005 ledger genesis row was not inserted.`;
      case "unsupported_hash_algorithm":
        return `meta.ledger_hash_algorithm='${err.actual}'. v0.3 reader only supports sha256.`;
      case "ledger_integrity":
        return `Ledger integrity check failed: ${err.detail}. Run \`lun fsck --ledger\` against this cartridge for diagnostics.`;
      case "sqlite_error":
        return `SQLite error: ${err.message}. Try opening in the sqlite3 CLI for diagnostics.`;
      case "invalid_handle":
        return `Invalid cartridge handle ${err.handle}. Reopen the file.`;
      case "io_error":
        return `I/O error: ${err.message}.`;
    }
  }
  return String(e);
}

export type Toast = { id: string; level: "error" | "info"; text: string };
export type View = "document" | "tree" | "extractions" | "search";

export interface ExtractionFilters {
  type: ExtractionType;
  anchorStatus: AnchorStatus | null;
}

export interface ReaderState {
  cartridge: { handle: HandleId; path: string; meta: Meta } | null;
  toasts: Toast[];
  view: View;

  // Tree
  rootNodes: DocNode[] | null;
  childrenByParent: Record<string, DocNode[]>;
  treeExpansion: Set<string>;
  selectedNode: DocNode | null;

  // Extractions
  extractionCounts: ExtractionCount[] | null;
  extractionFilters: ExtractionFilters;
  extractions: Extraction[];
  extractionsLoading: boolean;

  // Provenance
  selectedClaim: Extraction | null;
  claimSources: ExtractionSourcesResult | null;
  ledgerEvents: LedgerEvent[] | null;
  currentClaimTrust: TrustVector | null;

  // SPEC-013 figure inspector
  selectedFigureUlid: string | null;
  figurePayload: FigurePayload | null;
  figureLoading: boolean;

  // Per-row trust (SPEC-004 AuthorityBar in ExtractionsPanel)
  trustByExtractionUlid: Record<string, TrustVector>;

  // Search
  searchQuery: string;
  searchResults: SearchHit[];
  searchLoading: boolean;
  searchError: string | null;

  openCartridge(path: string): Promise<void>;
  closeCartridge(): Promise<void>;
  setView(v: View): void;
  pushToast(level: Toast["level"], text: string): void;
  dismissToast(id: string): void;

  loadRootNodes(): Promise<void>;
  loadChildren(parentUlid: string): Promise<void>;
  toggleExpand(ulid: string): Promise<void>;
  selectNode(ulid: string): Promise<void>;

  setExtractionFilters(f: Partial<ExtractionFilters>): Promise<void>;
  reloadExtractions(): Promise<void>;
  selectClaim(claim: Extraction): Promise<void>;
  selectFigure(figureUlid: string): Promise<void>;
  closeDrawer(): void;
  closeFigureDrawer(): void;

  setSearchQuery(q: string): void;
  runSearch(): Promise<void>;

  /** SPEC-007 v0.3.3: open `path` and per-kind navigate to `item`. Used by
   * SketchedShelf click-through to land the user on the actual hit inside
   * the cartridge rather than at the default Document view. */
  openCartridgeAndNavigate(
    path: string,
    kind: SketchKind,
    item: string,
  ): Promise<void>;
}

const DEFAULT_FILTERS: ExtractionFilters = { type: "claim", anchorStatus: null };

const EMPTY_STATE = {
  rootNodes: null as DocNode[] | null,
  childrenByParent: {} as Record<string, DocNode[]>,
  treeExpansion: new Set<string>(),
  selectedNode: null as DocNode | null,
  extractionCounts: null as ExtractionCount[] | null,
  extractionFilters: DEFAULT_FILTERS,
  extractions: [] as Extraction[],
  extractionsLoading: false,
  selectedClaim: null as Extraction | null,
  claimSources: null as ExtractionSourcesResult | null,
  ledgerEvents: null as LedgerEvent[] | null,
  currentClaimTrust: null as TrustVector | null,
  selectedFigureUlid: null as string | null,
  figurePayload: null as FigurePayload | null,
  figureLoading: false,
  trustByExtractionUlid: {} as Record<string, TrustVector>,
  searchQuery: "",
  searchResults: [] as SearchHit[],
  searchLoading: false,
  searchError: null as string | null,
};

export const useReader = create<ReaderState>((set, get) => ({
  cartridge: null,
  toasts: [],
  view: "document",
  ...EMPTY_STATE,

  async openCartridge(path: string) {
    try {
      const handle = await api.openCartridge(path);
      const [meta, counts] = await Promise.all([
        api.getMeta(handle),
        api.getExtractionCounts(handle),
      ]);
      set({
        cartridge: { handle, path, meta },
        ...EMPTY_STATE,
        extractionCounts: counts,
        view: "document",
      });
    } catch (e) {
      get().pushToast("error", errorToText(e));
    }
  },

  async closeCartridge() {
    const { cartridge } = get();
    if (!cartridge) return;
    try {
      await api.closeCartridge(cartridge.handle);
    } catch {
      // best effort
    }
    set({ cartridge: null, ...EMPTY_STATE, view: "document" });
  },

  setView(v) {
    set({ view: v });
    if (v === "extractions" && get().extractions.length === 0) {
      void get().reloadExtractions();
    }
  },

  pushToast(level, text) {
    const id = Math.random().toString(36).slice(2);
    set((s) => ({ toasts: [...s.toasts, { id, level, text }] }));
    setTimeout(() => get().dismissToast(id), 8000);
  },

  dismissToast(id) {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },

  async loadRootNodes() {
    const { cartridge } = get();
    if (!cartridge) return;
    try {
      const nodes = await api.listNodes(cartridge.handle, null, null, 100, 0);
      set({ rootNodes: nodes });
    } catch (e) {
      get().pushToast("error", errorToText(e));
    }
  },

  async loadChildren(parentUlid: string) {
    const { cartridge, childrenByParent } = get();
    if (!cartridge) return;
    if (childrenByParent[parentUlid]) return;
    try {
      const nodes = await api.listNodes(cartridge.handle, parentUlid, null, 1000, 0);
      set((s) => ({ childrenByParent: { ...s.childrenByParent, [parentUlid]: nodes } }));
    } catch (e) {
      get().pushToast("error", errorToText(e));
    }
  },

  async toggleExpand(ulid: string) {
    const { treeExpansion } = get();
    const next = new Set(treeExpansion);
    if (next.has(ulid)) {
      next.delete(ulid);
    } else {
      next.add(ulid);
      await get().loadChildren(ulid);
    }
    set({ treeExpansion: next });
  },

  async selectNode(ulid: string) {
    const { cartridge } = get();
    if (!cartridge) return;
    try {
      const node = await api.getNode(cartridge.handle, ulid);
      set({ selectedNode: node });
      const chain = node.parent_chain ?? [];
      const next = new Set(get().treeExpansion);
      for (const ancestor of chain) {
        if (!next.has(ancestor.ulid)) {
          next.add(ancestor.ulid);
          await get().loadChildren(ancestor.ulid);
        }
      }
      set({ treeExpansion: next });
    } catch (e) {
      get().pushToast("error", errorToText(e));
    }
  },

  async setExtractionFilters(f) {
    set((s) => ({ extractionFilters: { ...s.extractionFilters, ...f } }));
    await get().reloadExtractions();
  },

  async reloadExtractions() {
    const { cartridge, extractionFilters } = get();
    if (!cartridge) return;
    set({ extractionsLoading: true });
    try {
      const rows = await api.listExtractions(
        cartridge.handle,
        extractionFilters.type,
        extractionFilters.anchorStatus,
        500,
        0,
      );
      set({ extractions: rows, extractionsLoading: false });
      // SPEC-004: batch-compose trust vectors for claim/summary rows.
      // Entities get authority=null per the composer, so skip them.
      const trustTargets = rows
        .filter(
          (r) =>
            r.extraction_type === "claim" || r.extraction_type === "summary",
        )
        .map((r) => r.ulid);
      if (trustTargets.length > 0) {
        try {
          const vectors = await composeTrustBatch(cartridge.handle, trustTargets);
          set((s) => {
            const next = { ...s.trustByExtractionUlid };
            for (const v of vectors) next[v.target_ulid] = v;
            return { trustByExtractionUlid: next };
          });
        } catch (e) {
          // Trust composition failure is non-fatal; rows just show "—".
          get().pushToast("error", errorToText(e));
        }
      }
    } catch (e) {
      set({ extractionsLoading: false });
      get().pushToast("error", errorToText(e));
    }
  },

  async selectClaim(claim: Extraction) {
    const { cartridge } = get();
    if (!cartridge) return;
    set({
      selectedClaim: claim,
      claimSources: null,
      ledgerEvents: null,
      currentClaimTrust: null,
      selectedFigureUlid: null,
      figurePayload: null,
      figureLoading: false,
    });
    try {
      const [sources, events, trust] = await Promise.all([
        api.getExtractionSources(cartridge.handle, claim.ulid),
        api.getLedgerEvents(cartridge.handle, claim.ulid),
        composeTrust(cartridge.handle, claim.ulid),
      ]);
      set({ claimSources: sources, ledgerEvents: events, currentClaimTrust: trust });
    } catch (e) {
      get().pushToast("error", errorToText(e));
    }
  },

  async selectFigure(figureUlid: string) {
    const { cartridge } = get();
    if (!cartridge) return;
    set({
      selectedFigureUlid: figureUlid,
      figurePayload: null,
      figureLoading: true,
      selectedClaim: null,
      claimSources: null,
      ledgerEvents: null,
      currentClaimTrust: null,
    });
    try {
      const payload = await api.getFigurePayload(cartridge.handle, figureUlid);
      set({ figurePayload: payload, figureLoading: false });
    } catch (e) {
      set({ figureLoading: false });
      get().pushToast("error", errorToText(e));
    }
  },

  closeDrawer() {
    set({
      selectedClaim: null,
      claimSources: null,
      ledgerEvents: null,
      currentClaimTrust: null,
    });
  },

  closeFigureDrawer() {
    set({
      selectedFigureUlid: null,
      figurePayload: null,
      figureLoading: false,
    });
  },

  setSearchQuery(q: string) {
    set({ searchQuery: q });
  },

  async runSearch() {
    const { cartridge, searchQuery } = get();
    if (!cartridge) return;
    const query = searchQuery.trim();
    if (!query) {
      set({ searchResults: [], searchError: null });
      return;
    }
    set({ searchLoading: true, searchError: null });
    try {
      // v2 hook: prefer semantic if configured; v1 always falls back to FTS5.
      const fn = api.semanticSearch ?? api.search;
      const results = await fn(cartridge.handle, query, 50);
      set({ searchResults: results, searchLoading: false });
    } catch (e) {
      set({
        searchLoading: false,
        searchError: errorToText(e),
        searchResults: [],
      });
    }
  },

  async openCartridgeAndNavigate(path, kind, item) {
    // Open (or re-open if the user clicked the currently-open cartridge);
    // openCartridge resets EMPTY_STATE so per-kind navigation always starts
    // from a clean slate.
    await get().openCartridge(path);
    const { cartridge } = get();
    if (!cartridge) return; // openCartridge surfaced an error toast already

    switch (kind) {
      case "fts_term": {
        set({ searchQuery: item, view: "search" });
        await get().runSearch();
        return;
      }
      case "node_ulid": {
        set({ view: "tree" });
        await get().selectNode(item);
        return;
      }
      case "extraction_ulid": {
        try {
          const ext = await api.getExtraction(cartridge.handle, item);
          if (!ext) {
            get().pushToast(
              "error",
              `Extraction ${item} not found after open (sketch false positive).`,
            );
            set({ view: "extractions" });
            await get().reloadExtractions();
            return;
          }
          set({
            view: "extractions",
            extractionFilters: { type: ext.extraction_type, anchorStatus: null },
          });
          await get().reloadExtractions();
          await get().selectClaim(ext);
        } catch (e) {
          get().pushToast("error", errorToText(e));
        }
        return;
      }
      case "entity_surface": {
        try {
          const ext = await api.findExtractionByContent(cartridge.handle, item);
          if (!ext) {
            get().pushToast(
              "error",
              `Entity "${item}" not found after open (sketch false positive).`,
            );
            set({
              view: "extractions",
              extractionFilters: { type: "entity", anchorStatus: null },
            });
            await get().reloadExtractions();
            return;
          }
          set({
            view: "extractions",
            extractionFilters: { type: "entity", anchorStatus: null },
          });
          await get().reloadExtractions();
          await get().selectClaim(ext);
        } catch (e) {
          get().pushToast("error", errorToText(e));
        }
        return;
      }
    }
  },
}));
