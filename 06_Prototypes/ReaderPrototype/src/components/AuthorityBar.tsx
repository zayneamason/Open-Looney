interface Props {
  value: number | null;
  className?: string;
}

/**
 * Compact single-axis Authority bar for inline row display in the
 * ExtractionsPanel. Saturation rises with Authority value; gray when null
 * (per SPEC-004 §4.4: never substitute a default).
 */
export function AuthorityBar({ value, className = "" }: Props) {
  if (value === null) {
    return (
      <span
        className={`inline-flex items-center justify-center w-8 h-1.5 rounded-sm bg-gray-100 text-[8px] text-gray-400 ${className}`}
        title="Authority: — (no signal)"
        aria-label="Authority: no signal"
      >
        —
      </span>
    );
  }
  const pct = Math.max(0, Math.min(1, value)) * 100;
  // Use a green saturation ramp; lower values shade toward yellow/amber.
  const color =
    value >= 0.70
      ? "bg-green-600"
      : value >= 0.50
      ? "bg-lime-500"
      : value >= 0.30
      ? "bg-amber-400"
      : "bg-red-400";
  return (
    <span
      className={`inline-block w-8 h-1.5 rounded-sm bg-gray-100 overflow-hidden ${className}`}
      title={`Authority: ${value.toFixed(2)}`}
      aria-label={`Authority: ${value.toFixed(2)}`}
    >
      <span className={`block h-full ${color}`} style={{ width: `${pct}%` }} />
    </span>
  );
}
