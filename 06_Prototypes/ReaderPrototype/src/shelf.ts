// SPEC-007 SketchedShelf adapter — thin wrapper around the Rust commands
// + display helpers. Swap point for a future JS-side shelf implementation
// (mirrors the trust.ts adapter pattern from slice #3).

import { api } from "./api";
import type {
  CandidateResult,
  CandidateStatus,
  ShelfSummary,
  SketchKind,
} from "./types";

export async function openShelf(paths: string[]): Promise<ShelfSummary> {
  return api.openShelf(paths);
}

export async function closeShelf(): Promise<void> {
  return api.closeShelf();
}

export async function filterCandidates(
  item: string,
  kind: SketchKind,
): Promise<CandidateResult[]> {
  return api.shelfFilterCandidates(item, kind);
}

/** SPEC-007 § 7.3.3 verify-by-opening: open the cartridge at `path` and
 * run the precise query for `kind`. Upgrades `probable` → `confirmed` or
 * downgrades to `false_positive`. */
export async function verifyCandidate(
  path: string,
  item: string,
  kind: SketchKind,
): Promise<CandidateStatus> {
  const result = await api.shelfVerifyCandidate(path, item, kind);
  return result.status;
}

export function kindLabel(kind: SketchKind): string {
  switch (kind) {
    case "extraction_ulid":
      return "Extraction ULID";
    case "node_ulid":
      return "Node ULID";
    case "entity_surface":
      return "Entity surface";
    case "fts_term":
      return "FTS term";
  }
}

export function statusLabel(status: CandidateStatus): string {
  switch (status) {
    case "probable":
      return "probable";
    case "unknown":
      return "no sketch (verify)";
    case "confirmed":
      return "confirmed";
    case "false_positive":
      return "false positive";
  }
}

/** Centralized badge styling — tailwind classes per status. Keeps the
 * ShelfPanel JSX clean and ensures consistent colors across surfaces. */
export function statusBadgeClasses(status: CandidateStatus): string {
  switch (status) {
    case "confirmed":
      return "bg-green-100 text-green-800";
    case "probable":
      return "bg-amber-100 text-amber-800";
    case "unknown":
      return "bg-amber-50 text-amber-700";
    case "false_positive":
      return "bg-gray-200 text-gray-600";
  }
}

export const ALL_KINDS: SketchKind[] = [
  "fts_term",
  "extraction_ulid",
  "node_ulid",
  "entity_surface",
];
