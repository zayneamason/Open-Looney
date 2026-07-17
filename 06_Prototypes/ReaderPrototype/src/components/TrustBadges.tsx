import type { TrustVector } from "../types";
import { composerLabel, formatAxisDisplay, freshnessLabel } from "../trust";

interface Props {
  vector: TrustVector;
}

function AuthorityAxisBar({ value }: { value: number | null }) {
  if (value === null) {
    return <span className="text-xs text-gray-400">—</span>;
  }
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const color =
    value >= 0.70
      ? "bg-green-600"
      : value >= 0.50
      ? "bg-lime-500"
      : value >= 0.30
      ? "bg-amber-400"
      : "bg-red-400";
  return (
    <span className="inline-flex items-center gap-2">
      <span className="inline-block w-20 h-1.5 rounded-sm bg-gray-100 overflow-hidden">
        <span className={`block h-full ${color}`} style={{ width: `${pct}%` }} />
      </span>
      <span className="text-xs font-mono text-gray-700">{value.toFixed(2)}</span>
    </span>
  );
}

/**
 * SPEC-004 four-axis TrustVector display, per §4.4 rendering guidance:
 *   - Authority: always shown, saturation bar + numeric.
 *   - Contestation: warning chip when value < 0.5; quiet otherwise.
 *   - Temporal: freshness label.
 *   - Resonance: count chip when value > 0.4; quiet otherwise.
 *   - Composer ID + version in a tooltip on the badge group
 *     (cross-composer comparison rule).
 */
export function TrustBadges({ vector }: Props) {
  const { authority, contestation, temporal, resonance } = vector.axes;
  const tooltip = `Composed by ${composerLabel(vector)} at ${vector.computed_at}`;
  return (
    <div className="space-y-2" title={tooltip}>
      <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs items-center">
        <span className="text-gray-500 uppercase tracking-wide text-[10px]">Authority</span>
        <AuthorityAxisBar value={authority} />

        <span className="text-gray-500 uppercase tracking-wide text-[10px]">Contestation</span>
        <span>
          {contestation === null ? (
            <span className="text-gray-400">—</span>
          ) : contestation < 0.5 ? (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 text-[10px] font-medium">
              ⚠ disputed ({formatAxisDisplay(contestation)})
            </span>
          ) : (
            <span className="font-mono text-gray-700">{formatAxisDisplay(contestation)}</span>
          )}
        </span>

        <span className="text-gray-500 uppercase tracking-wide text-[10px]">Temporal</span>
        <span className="flex items-center gap-2">
          <span className="text-gray-700">{freshnessLabel(temporal)}</span>
          {temporal !== null && (
            <span className="font-mono text-[10px] text-gray-400">{formatAxisDisplay(temporal)}</span>
          )}
        </span>

        <span className="text-gray-500 uppercase tracking-wide text-[10px]">Resonance</span>
        <span>
          {resonance === null ? (
            <span className="text-gray-400">—</span>
          ) : resonance > 0.4 ? (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 text-[10px] font-medium">
              ✓ affirmed ({formatAxisDisplay(resonance)})
            </span>
          ) : (
            <span className="font-mono text-gray-700">{formatAxisDisplay(resonance)}</span>
          )}
        </span>
      </div>
      <div className="text-[10px] text-gray-400 font-mono truncate" title={composerLabel(vector)}>
        {composerLabel(vector)}
      </div>
    </div>
  );
}
