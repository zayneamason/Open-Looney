import { useReader } from "../store";
import { AnchorBadge } from "./AnchorBadge";
import { TrustBadges } from "./TrustBadges";
import type {
  ContextNode,
  ExtractionSource,
  LedgerEvent,
  LedgerEventType,
} from "../types";

function formatTimestamp(ms: number | null): string {
  if (ms === null) return "—";
  try {
    return new Date(ms).toISOString().replace("T", " ").slice(0, 19) + " UTC";
  } catch {
    return String(ms);
  }
}

const EVENT_TYPE_STYLE: Record<LedgerEventType, string> = {
  claim_anchored: "bg-green-100 text-green-800",
  claim_disputed: "bg-amber-100 text-amber-800",
  claim_filtered: "bg-gray-100 text-gray-600",
  claim_reconciled: "bg-blue-100 text-blue-800",
  summary_overridden: "bg-purple-100 text-purple-800",
  cartridge_reviewed: "bg-indigo-100 text-indigo-800",
  cartridge_imported: "bg-gray-100 text-gray-700",
  meta: "bg-gray-100 text-gray-600",
};

function SourceRow({ source }: { source: ExtractionSource }) {
  const selectNode = useReader((s) => s.selectNode);
  const setView = useReader((s) => s.setView);
  const pageNum = (source.node.meta_json as { page_num?: number } | null)?.page_num;
  return (
    <div
      className="border border-gray-200 rounded p-3 cursor-pointer hover:bg-gray-50"
      onClick={() => {
        void selectNode(source.node.ulid);
        setView("tree");
      }}
    >
      <div className="text-xs text-gray-500 flex items-center gap-2 mb-1.5">
        <span className="font-mono uppercase text-[10px]">{source.node.node_type}</span>
        {typeof pageNum === "number" && <span>page {pageNum}</span>}
        {source.anchor_method && (
          <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
            {source.anchor_method}
          </span>
        )}
        {source.anchored_at !== null && (
          <span title="anchored_at">{formatTimestamp(source.anchored_at)}</span>
        )}
      </div>
      <p className="text-sm text-gray-900 leading-snug whitespace-pre-wrap">
        {source.node.content || <span className="italic text-gray-400">(no content)</span>}
      </p>
    </div>
  );
}

function ContextRow({ ctx }: { ctx: ContextNode }) {
  const selectNode = useReader((s) => s.selectNode);
  const setView = useReader((s) => s.setView);
  const pageNum = (ctx.node.meta_json as { page_num?: number } | null)?.page_num;
  const pct = Math.round(ctx.relevance * 100);
  return (
    <div
      className="border border-gray-200 rounded p-3 cursor-pointer hover:bg-gray-50"
      onClick={() => {
        void selectNode(ctx.node.ulid);
        setView("tree");
      }}
    >
      <div className="text-xs text-gray-500 flex items-center gap-2 mb-1.5">
        <span className="font-mono uppercase text-[10px]">{ctx.node.node_type}</span>
        {typeof pageNum === "number" && <span>page {pageNum}</span>}
        <span className="ml-auto flex items-center gap-1.5">
          <span className="w-16 h-1 bg-gray-200 rounded-full overflow-hidden">
            <span
              className="block h-full bg-purple-500"
              style={{ width: `${pct}%` }}
            />
          </span>
          <span className="text-[10px] font-mono">{ctx.relevance.toFixed(2)}</span>
        </span>
      </div>
      <p className="text-sm text-gray-900 leading-snug whitespace-pre-wrap">
        {ctx.node.content || <span className="italic text-gray-400">(no content)</span>}
      </p>
    </div>
  );
}

function LedgerEventRow({ event }: { event: LedgerEvent }) {
  const actorLabel = event.actor_display_name ?? `${event.actor_id.slice(-8)}`;
  const style = EVENT_TYPE_STYLE[event.event_type] ?? "bg-gray-100 text-gray-700";
  return (
    <div className="border border-gray-200 rounded p-3">
      <div className="text-xs text-gray-500 flex items-center gap-2 mb-1.5 flex-wrap">
        <span className={`px-1.5 py-0.5 rounded font-mono uppercase text-[10px] ${style}`}>
          {event.event_type}
        </span>
        <span className="font-mono text-[10px] text-gray-600">{event.actor_role}</span>
        <span title={event.actor_id}>{actorLabel}</span>
        <span className="ml-auto" title={`seq=${event.seq}`}>{formatTimestamp(event.entry_ts)}</span>
      </div>
      <details className="text-[11px] text-gray-500">
        <summary className="cursor-pointer hover:text-gray-900 font-mono">
          payload · hash {event.entry_hash.slice(0, 12)}…
        </summary>
        <pre className="mt-2 p-2 bg-gray-50 rounded font-mono leading-relaxed overflow-x-auto">
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      </details>
    </div>
  );
}

export function ProvenanceDrawer() {
  const claim = useReader((s) => s.selectedClaim);
  const result = useReader((s) => s.claimSources);
  const ledgerEvents = useReader((s) => s.ledgerEvents);
  const trust = useReader((s) => s.currentClaimTrust);
  const closeDrawer = useReader((s) => s.closeDrawer);
  if (!claim) return null;

  return (
    <aside className="w-96 border-l border-gray-200 bg-white shrink-0 flex flex-col overflow-hidden">
      <header className="border-b border-gray-200 px-4 py-3 flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <AnchorBadge
              status={claim.anchor_status}
              extractionType={claim.extraction_type}
              reason={claim.anchor_reason}
              sourceCount={result?.sources.length}
            />
            <span className="text-[10px] font-mono text-gray-400 truncate">{claim.ulid}</span>
          </div>
          <p className="text-sm text-gray-900 leading-snug">{claim.content}</p>
        </div>
        <button
          onClick={closeDrawer}
          className="text-gray-400 hover:text-gray-900 shrink-0"
          aria-label="Close drawer"
        >
          ✕
        </button>
      </header>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
            Trust (SPEC-004)
          </h3>
          {trust ? (
            <TrustBadges vector={trust} />
          ) : (
            <p className="text-xs text-gray-400">composing…</p>
          )}
        </section>

        {claim.anchor_status === "match_failed" && claim.anchor_reason && (
          <div className="rounded bg-yellow-50 border border-yellow-200 p-3 text-xs text-yellow-900">
            <div className="font-semibold mb-1">match_failed reason</div>
            <div className="font-mono leading-relaxed">{claim.anchor_reason}</div>
          </div>
        )}

        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
            Sources {result ? `(${result.sources.length})` : ""}
          </h3>
          {!result && <p className="text-xs text-gray-400">loading…</p>}
          {result && result.sources.length === 0 && (
            <p className="text-xs text-gray-400">No source nodes recorded for this claim.</p>
          )}
          <div className="space-y-2">
            {result?.sources.map((s) => (
              <SourceRow key={s.node.ulid} source={s} />
            ))}
          </div>
        </section>

        {result && result.context.length > 0 && (
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
              Context nodes ({result.context.length})
            </h3>
            <div className="space-y-2">
              {result.context.map((c) => (
                <ContextRow key={c.node.ulid} ctx={c} />
              ))}
            </div>
          </section>
        )}

        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
            Annotations {ledgerEvents ? `(${ledgerEvents.length})` : ""}
          </h3>
          {!ledgerEvents && <p className="text-xs text-gray-400">loading…</p>}
          {ledgerEvents && ledgerEvents.length === 0 && (
            <p className="text-[11px] text-gray-400 italic">
              No SPEC-005 annotation events targeting this extraction. Ambassador
              upgrades will appear here once the engine writes them.
            </p>
          )}
          <div className="space-y-2">
            {ledgerEvents?.map((e) => (
              <LedgerEventRow key={e.entry_hash} event={e} />
            ))}
          </div>
        </section>
      </div>
    </aside>
  );
}
