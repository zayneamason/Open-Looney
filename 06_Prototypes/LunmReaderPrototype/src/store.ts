import { create } from "zustand";
import { open } from "@tauri-apps/plugin-dialog";
import { api } from "./api";
import type {
  LunmError,
  LunmHealthReport,
  LunmOverview,
  MatrixHandle,
  MemoryNodeFilters,
  ProfileConfigRow,
  Tab,
  TableRow,
} from "./types";

export function errorToText(e: unknown): string {
  if (typeof e === "object" && e !== null && "kind" in (e as Record<string, unknown>)) {
    const err = e as LunmError;
    switch (err.kind) {
      case "not_sqlite":
        return "Not a SQLite file. Pick a LUNM .lun runtime matrix.";
      case "wrong_family":
        if (err.family_hint === "LUNC") {
          return "This is a LUNC cartridge, not a LUNM runtime matrix.";
        }
        return `Unknown application_id: 0x${err.actual_id.toString(16).padStart(8, "0").toUpperCase()}. Expected LUNM 0x4C554E4D.`;
      case "unsupported_user_version":
        return `Unsupported LUNM user_version ${err.actual}. This inspector requires user_version >= 2.`;
      case "missing_format_invariant_table":
        return `Missing format-invariant table: ${err.table}.`;
      case "missing_format_invariant_column":
        return `Missing format-invariant column: ${err.table}.${err.column}.`;
      case "invalid_handle":
        return `Invalid matrix handle ${err.handle}. Reopen the file.`;
      case "sqlite_error":
        return `SQLite error: ${err.message}.`;
      case "io_error":
        return `I/O error: ${err.message}.`;
    }
  }
  return String(e);
}

export interface InspectorState {
  matrix: { handle: MatrixHandle; path: string } | null;
  overview: LunmOverview | null;
  health: LunmHealthReport | null;
  activeTab: Tab;
  rows: TableRow[];
  configRows: ProfileConfigRow[];
  loading: boolean;
  error: string | null;
  memoryFilters: MemoryNodeFilters;
  graphNodeId: string;
  sessionId: string;
  configPrefix: string;

  pickAndOpen(): Promise<void>;
  openPath(path: string): Promise<void>;
  close(): Promise<void>;
  setTab(tab: Tab): Promise<void>;
  reloadCurrentTab(): Promise<void>;
  setMemoryFilter(key: keyof MemoryNodeFilters, value: string): Promise<void>;
  setGraphNodeId(value: string): Promise<void>;
  setSessionId(value: string): Promise<void>;
  setConfigPrefix(value: string): Promise<void>;
}

const DEFAULT_FILTERS: MemoryNodeFilters = { nodeType: null, classification: null };

export const useInspector = create<InspectorState>((set, get) => ({
  matrix: null,
  overview: null,
  health: null,
  activeTab: "overview",
  rows: [],
  configRows: [],
  loading: false,
  error: null,
  memoryFilters: DEFAULT_FILTERS,
  graphNodeId: "",
  sessionId: "",
  configPrefix: "",

  async pickAndOpen() {
    const selected = await open({
      multiple: false,
      filters: [{ name: "LUNM matrix", extensions: ["lun"] }],
    });
    if (typeof selected === "string") {
      await get().openPath(selected);
    }
  },

  async openPath(path: string) {
    set({ loading: true, error: null });
    try {
      const handle = await api.openLunmMatrix(path);
      const [overview, health] = await Promise.all([
        api.getLunmOverview(handle),
        api.getLunmHealth(handle),
      ]);
      set({
        matrix: { handle, path },
        overview,
        health,
        activeTab: "overview",
        rows: [],
        configRows: [],
        loading: false,
        error: null,
      });
    } catch (e) {
      set({ loading: false, error: errorToText(e) });
    }
  },

  async close() {
    const matrix = get().matrix;
    if (matrix) {
      try {
        await api.closeLunmMatrix(matrix.handle);
      } catch {
        // Best effort.
      }
    }
    set({
      matrix: null,
      overview: null,
      health: null,
      activeTab: "overview",
      rows: [],
      configRows: [],
      error: null,
    });
  },

  async setTab(tab: Tab) {
    set({ activeTab: tab, rows: [], configRows: [], error: null });
    await get().reloadCurrentTab();
  },

  async reloadCurrentTab() {
    const { matrix, activeTab } = get();
    if (!matrix || activeTab === "overview" || activeTab === "health") return;
    set({ loading: true, error: null });
    try {
      if (activeTab === "memory") {
        const rows = await api.listMemoryNodes(matrix.handle, get().memoryFilters);
        set({ rows, configRows: [], loading: false });
      } else if (activeTab === "graph") {
        const needle = get().graphNodeId.trim() || null;
        const rows = await api.listGraphEdges(matrix.handle, needle);
        set({ rows, configRows: [], loading: false });
      } else if (activeTab === "conversations") {
        const sessionId = get().sessionId.trim() || null;
        const rows = sessionId
          ? await api.listConversationTurns(matrix.handle, sessionId)
          : await api.listSessions(matrix.handle);
        set({ rows, configRows: [], loading: false });
      } else if (activeTab === "nexus") {
        const [registry, nodes, edges] = await Promise.all([
          api.listNexusRegistry(matrix.handle, 50),
          api.listNexusNodes(matrix.handle, 50),
          api.listNexusEdges(matrix.handle, 50),
        ]);
        set({
          rows: [
            ...registry.map((r) => sectionRow("registry", r)),
            ...nodes.map((r) => sectionRow("nodes", r)),
            ...edges.map((r) => sectionRow("edges", r)),
          ],
          configRows: [],
          loading: false,
        });
      } else if (activeTab === "config") {
        const prefix = get().configPrefix.trim() || null;
        const configRows = await api.listProfileConfig(matrix.handle, prefix);
        set({ rows: [], configRows, loading: false });
      }
    } catch (e) {
      set({ loading: false, error: errorToText(e), rows: [], configRows: [] });
    }
  },

  async setMemoryFilter(key, value) {
    const normalized = value.trim() || null;
    set((s) => ({
      memoryFilters: { ...s.memoryFilters, [key]: normalized },
    }));
    await get().reloadCurrentTab();
  },

  async setGraphNodeId(value: string) {
    set({ graphNodeId: value });
    await get().reloadCurrentTab();
  },

  async setSessionId(value: string) {
    set({ sessionId: value });
    await get().reloadCurrentTab();
  },

  async setConfigPrefix(value: string) {
    set({ configPrefix: value });
    await get().reloadCurrentTab();
  },
}));

function sectionRow(section: string, row: TableRow): TableRow {
  return { values: { section, ...row.values } };
}
