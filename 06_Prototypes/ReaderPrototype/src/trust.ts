// SPEC-004 composer adapter. The UI consumes this module, not api.ts directly,
// so a future application can swap the canonical Rust composer for a TS
// implementation without touching React components.

import { api } from "./api";
import type { HandleId, TrustVector } from "./types";

export function composeTrust(handle: HandleId, targetUlid: string): Promise<TrustVector> {
  return api.composeTrustVector(handle, targetUlid);
}

export function composeTrustBatch(
  handle: HandleId,
  targetUlids: string[],
): Promise<TrustVector[]> {
  return api.composeTrustVectorsBatch(handle, targetUlids);
}

// --- Display helpers -------------------------------------------------------

export function formatAxisDisplay(value: number | null): string {
  if (value === null) return "—";
  return value.toFixed(2);
}

/**
 * Approximate freshness label derived from the Temporal axis value. Inverts
 * the reference composer's exponential decay (180-day half-life) just well
 * enough for a human-readable label; not exact and intentionally fuzzy.
 */
export function freshnessLabel(temporal: number | null): string {
  if (temporal === null) return "—";
  if (temporal >= 0.98) return "today";
  if (temporal >= 0.90) return "this month";
  if (temporal >= 0.71) return "<3 months";
  if (temporal >= 0.50) return "~6 months";
  if (temporal >= 0.25) return "~1 year";
  if (temporal >= 0.10) return "~2 years";
  return "older";
}

export function composerLabel(vector: TrustVector): string {
  return `${vector.composer_id}@${vector.composer_version}`;
}
