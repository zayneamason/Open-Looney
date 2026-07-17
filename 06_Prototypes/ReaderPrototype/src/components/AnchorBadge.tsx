import type { AnchorStatus, ExtractionType } from "../types";

interface Props {
  status: AnchorStatus;
  extractionType?: ExtractionType;
  sourceCount?: number;
  reason?: string | null;
  className?: string;
}

const BASE = "inline-flex items-center text-[10px] font-medium px-1.5 py-0.5 rounded uppercase tracking-wide";

export function AnchorBadge({ status, extractionType, sourceCount, reason, className = "" }: Props) {
  // Entities legitimately carry anchor_status=unknown (SPEC-001 scopes them out of anchor classification).
  // For claims/summaries, unknown is a data-quality flag (should never appear in v0.3).
  if (status === "unknown") {
    if (extractionType === "entity") {
      return (
        <span
          className={`${BASE} bg-gray-100 text-gray-600 ${className}`}
          title="Entities are scoped out of v0.3 anchor classification (SPEC-001)"
        >
          unclassified
        </span>
      );
    }
    return (
      <span
        className={`${BASE} bg-red-100 text-red-800 ${className}`}
        title="No v0.3 claim should ship with anchor_status=unknown (SPEC-001 hard gate)"
      >
        ? unknown
      </span>
    );
  }

  switch (status) {
    case "anchored":
      return <span className={`${BASE} bg-green-100 text-green-800 ${className}`}>✓ anchored</span>;
    case "synthesized":
      return (
        <span className={`${BASE} bg-purple-100 text-purple-800 ${className}`}>
          synthesized{sourceCount ? ` × ${sourceCount}` : ""}
        </span>
      );
    case "match_failed":
      return (
        <span
          className={`${BASE} bg-yellow-100 text-yellow-800 ${className}`}
          title={reason ?? undefined}
        >
          ⚠ unanchored
        </span>
      );
    case "filtered":
      return (
        <span
          className={`${BASE} bg-gray-100 text-gray-500 ${className}`}
          title={reason ?? undefined}
        >
          filtered{reason ? `: ${reason}` : ""}
        </span>
      );
  }
}
