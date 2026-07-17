import { useEffect, useMemo, useState } from "react";
import { useReader } from "../store";
import { AnchorBadge } from "./AnchorBadge";
import { AuthorityBar } from "./AuthorityBar";
import type { AnchorStatus, Extraction, ExtractionType } from "../types";

const TYPES: ExtractionType[] = ["claim", "entity", "summary"];

const TYPE_LABELS: Record<ExtractionType, string> = {
  claim: "Claims",
  entity: "Entities",
  summary: "Summaries",
};

const STATUSES: { value: AnchorStatus | null; label: string }[] = [
  { value: null, label: "all" },
  { value: "anchored", label: "anchored" },
  { value: "synthesized", label: "synthesized" },
  { value: "match_failed", label: "match_failed" },
  { value: "filtered", label: "filtered" },
  { value: "unknown", label: "unknown" },
];

function ExtractionRow({ extraction }: { extraction: Extraction }) {
  const [expanded, setExpanded] = useState(false);
  const selectClaim = useReader((s) => s.selectClaim);
  const selectedClaim = useReader((s) => s.selectedClaim);
  const trustByUlid = useReader((s) => s.trustByExtractionUlid);
  const isSelected = selectedClaim?.ulid === extraction.ulid;
  const isClaim = extraction.extraction_type === "claim";
  const isEntity = extraction.extraction_type === "entity";
  const authority = isEntity
    ? null
    : trustByUlid[extraction.ulid]?.axes.authority ?? null;

  const preview = extraction.content.length > 200 && !expanded
    ? extraction.content.slice(0, 200) + "…"
    : extraction.content;

  return (
    <div
      className={`border-b border-gray-100 px-4 py-3 ${
        isClaim ? "cursor-pointer hover:bg-gray-50" : ""
      } ${isSelected ? "bg-blue-50" : ""}`}
      onClick={() => {
        if (isClaim) void selectClaim(extraction);
      }}
    >
      <div className="flex items-start gap-3">
        <div className="flex flex-col items-center gap-1 mt-0.5 shrink-0">
          <AnchorBadge
            status={extraction.anchor_status}
            extractionType={extraction.extraction_type}
            reason={extraction.anchor_reason}
          />
          {!isEntity && <AuthorityBar value={authority} />}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm leading-snug text-gray-900 whitespace-pre-wrap">{preview}</p>
          {extraction.content.length > 200 && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                setExpanded((v) => !v);
              }}
              className="text-[11px] text-blue-600 hover:text-blue-800 mt-1"
            >
              {expanded ? "show less" : "show more"}
            </button>
          )}
          <div className="flex items-center gap-3 mt-1.5 text-[10px] text-gray-400 font-mono">
            <span>{extraction.extraction_method}</span>
            <button
              onClick={(e) => {
                e.stopPropagation();
                void navigator.clipboard.writeText(extraction.ulid);
              }}
              className="hover:text-gray-700"
              title="Copy ULID"
            >
              {extraction.ulid}
            </button>
            {extraction.llm_logprob_sum !== null && (
              <span title="LLM log-probability sum (response-level)">
                logprob {extraction.llm_logprob_sum.toFixed(2)}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function ExtractionsPanel() {
  const counts = useReader((s) => s.extractionCounts);
  const filters = useReader((s) => s.extractionFilters);
  const extractions = useReader((s) => s.extractions);
  const loading = useReader((s) => s.extractionsLoading);
  const setExtractionFilters = useReader((s) => s.setExtractionFilters);
  const reloadExtractions = useReader((s) => s.reloadExtractions);

  useEffect(() => {
    if (extractions.length === 0 && !loading) {
      void reloadExtractions();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const totalsByType = useMemo(() => {
    const t: Record<string, number> = { claim: 0, entity: 0, summary: 0 };
    for (const c of counts ?? []) {
      t[c.extraction_type] = (t[c.extraction_type] ?? 0) + c.count;
    }
    return t;
  }, [counts]);

  return (
    <div className="h-full flex flex-col">
      <div className="border-b border-gray-200 px-4 pt-3 flex items-center gap-2">
        {TYPES.map((t) => (
          <button
            key={t}
            onClick={() => void setExtractionFilters({ type: t, anchorStatus: null })}
            className={`text-xs px-3 py-1.5 rounded-t border-b-2 transition ${
              filters.type === t
                ? "border-blue-600 text-blue-700 font-medium"
                : "border-transparent text-gray-500 hover:text-gray-900"
            }`}
          >
            {TYPE_LABELS[t]}{" "}
            <span className="text-gray-400">({totalsByType[t]?.toLocaleString() ?? "—"})</span>
          </button>
        ))}
      </div>

      <div className="px-4 py-2 border-b border-gray-200 flex items-center gap-2 text-xs text-gray-500">
        <span>anchor_status:</span>
        {STATUSES.map((s) => (
          <button
            key={s.label}
            onClick={() => void setExtractionFilters({ anchorStatus: s.value })}
            className={`px-2 py-0.5 rounded ${
              filters.anchorStatus === s.value
                ? "bg-gray-900 text-white"
                : "hover:bg-gray-100"
            }`}
          >
            {s.label}
          </button>
        ))}
        <span className="ml-auto text-gray-400">
          {loading ? "loading…" : `${extractions.length.toLocaleString()} shown`}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto">
        {extractions.length === 0 && !loading && (
          <div className="p-8 text-center text-sm text-gray-400">No extractions match these filters.</div>
        )}
        {extractions.map((e) => (
          <ExtractionRow key={e.ulid} extraction={e} />
        ))}
      </div>
    </div>
  );
}
