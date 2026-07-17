// Top-level mode toggle between the single-cartridge Reader and the
// multi-cartridge SketchedShelf. Promoted out of App.tsx's local useState
// so non-App components (e.g. the Shelf's row-click handler) can flip the
// mode without prop-drilling — keeps useReader/useShelf isolated, since
// they neither own nor know about the mode.

import { create } from "zustand";

export type Mode = "reader" | "shelf";

interface ModeState {
  mode: Mode;
  setMode(m: Mode): void;
}

export const useMode = create<ModeState>((set) => ({
  mode: "reader",
  setMode: (mode) => set({ mode }),
}));
