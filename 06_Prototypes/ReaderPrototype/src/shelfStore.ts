// SPEC-007 SketchedShelf — Zustand store separate from useReader.
// The shelf is a sibling surface to the single-cartridge reader; keeping
// it in its own store avoids bloating ReaderState and makes the multi-
// cartridge boundary explicit.

import { create } from "zustand";
import {
  closeShelf,
  filterCandidates,
  openShelf,
  verifyCandidate,
} from "./shelf";
import type {
  CandidateResult,
  CandidateStatus,
  ShelfSummary,
  SketchKind,
} from "./types";
import { errorToText, useReader } from "./store";
import { useMode } from "./modeStore";

export interface ShelfState {
  shelf: ShelfSummary | null;
  query: string;
  kind: SketchKind;
  candidates: CandidateResult[] | null;
  /** Paths whose verify-by-opening call is in-flight. ShelfPanel renders a
   * "verifying…" indicator for these rows. */
  verifying: Set<string>;
  /** Path → upgraded status after verify-by-opening resolved. ShelfPanel
   * prefers this over the sketch-pass `candidates[i].status` when set. */
  verifyResults: Record<string, CandidateStatus>;
  loading: boolean;
  error: string | null;

  openShelf(paths: string[]): Promise<void>;
  closeShelf(): Promise<void>;
  setQuery(q: string): void;
  setKind(k: SketchKind): void;
  runQuery(): Promise<void>;
  /** Fire verify-by-opening for every non-`unknown` candidate in parallel.
   * Updates verifyResults per-resolution rather than waiting for the batch. */
  verifyAll(): Promise<void>;
  /** Cross-mode click-through. Drives useReader.openCartridgeAndNavigate
   * and flips useMode to 'reader'. Stores reference each other directly
   * via getState() — App.tsx doesn't have to plumb the action. */
  clickedCandidate(path: string): Promise<void>;
  reset(): void;
}

export const useShelf = create<ShelfState>((set, get) => ({
  shelf: null,
  query: "",
  kind: "fts_term",
  candidates: null,
  verifying: new Set(),
  verifyResults: {},
  loading: false,
  error: null,

  async openShelf(paths) {
    if (paths.length === 0) {
      set({ error: "Select one or more .lun cartridges to open." });
      return;
    }
    set({ loading: true, error: null });
    try {
      const summary = await openShelf(paths);
      set({
        shelf: summary,
        candidates: null,
        verifying: new Set(),
        verifyResults: {},
        loading: false,
        error: null,
      });
    } catch (e) {
      set({ loading: false, error: errorToText(e) });
    }
  },

  async closeShelf() {
    try {
      await closeShelf();
    } finally {
      set({
        shelf: null,
        candidates: null,
        verifying: new Set(),
        verifyResults: {},
        query: "",
        error: null,
      });
    }
  },

  setQuery(q) {
    set({ query: q });
  },

  setKind(k) {
    // Clear candidates + verify state on kind change; old results are for a
    // different kind and would be misleading.
    set({
      kind: k,
      candidates: null,
      verifying: new Set(),
      verifyResults: {},
    });
  },

  async runQuery() {
    const { query, kind, shelf } = get();
    if (!shelf || shelf.count === 0) {
      set({ error: "Open a shelf first." });
      return;
    }
    const item = query.trim();
    if (item.length === 0) {
      set({ error: "Enter a query term." });
      return;
    }
    set({
      loading: true,
      error: null,
      verifying: new Set(),
      verifyResults: {},
    });
    try {
      const candidates = await filterCandidates(item, kind);
      set({ candidates, loading: false, error: null });
      // SPEC-007 § 7.3.3: probable ≠ confirmed. Auto-fire verify-by-opening
      // for every non-`unknown` candidate. UI updates per-row as each
      // verify call resolves.
      void get().verifyAll();
    } catch (e) {
      set({ loading: false, error: errorToText(e) });
    }
  },

  async verifyAll() {
    const { candidates, kind, query } = get();
    if (!candidates) return;
    const item = query.trim();
    if (item.length === 0) return;
    const targets = candidates.filter((c) => c.status !== "unknown");
    if (targets.length === 0) return;

    // Stamp every target as "verifying" before firing the requests; the UI
    // re-renders the badge as "verifying…" until each resolves.
    set((s) => {
      const next = new Set(s.verifying);
      for (const t of targets) next.add(t.path);
      return { verifying: next };
    });

    await Promise.allSettled(
      targets.map(async (t) => {
        try {
          const upgraded = await verifyCandidate(t.path, item, kind);
          set((s) => {
            const verifying = new Set(s.verifying);
            verifying.delete(t.path);
            return {
              verifying,
              verifyResults: { ...s.verifyResults, [t.path]: upgraded },
            };
          });
        } catch (e) {
          // Verify failure (e.g. cartridge moved between filter and verify)
          // leaves the row in `probable` state with an error stamp. Surface
          // as a toast-via-error rather than blocking the whole batch.
          set((s) => {
            const verifying = new Set(s.verifying);
            verifying.delete(t.path);
            return {
              verifying,
              error: `Verify failed for ${t.path}: ${errorToText(e)}`,
            };
          });
        }
      }),
    );
  },

  async clickedCandidate(path) {
    const { kind, query } = get();
    const item = query.trim();
    if (item.length === 0) {
      set({ error: "No query active; cannot navigate inside the cartridge." });
      return;
    }
    // Switch to the Reader tab first so the user sees the transition,
    // then drive the open + per-kind navigate (async). useReader's
    // openCartridge will reset state and surface its own error toast if
    // the cartridge fails the 7-step open contract.
    useMode.getState().setMode("reader");
    await useReader.getState().openCartridgeAndNavigate(path, kind, item);
  },

  reset() {
    set({
      shelf: null,
      query: "",
      kind: "fts_term",
      candidates: null,
      verifying: new Set(),
      verifyResults: {},
      loading: false,
      error: null,
    });
  },
}));
