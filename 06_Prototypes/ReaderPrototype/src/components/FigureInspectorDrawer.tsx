import { useReader } from "../store";
import { figureSrc } from "../figureSrc";
import type { FigureEnrichment } from "../types";

const ENRICHMENT_LABEL: Record<string, string> = {
  media_classification: "Media kind",
  visual_description: "Visual description",
  figure_discourse: "Discourse",
};

function EnrichmentBlock({ row }: { row: FigureEnrichment }) {
  const label = ENRICHMENT_LABEL[row.extraction_type] ?? row.extraction_type;
  return (
    <div className="border border-gray-200 rounded p-3">
      <div className="text-[10px] font-mono uppercase tracking-wide text-gray-500 mb-1.5">
        {label}
      </div>
      <p className="text-sm text-gray-900 leading-snug whitespace-pre-wrap">{row.content}</p>
    </div>
  );
}

function formatBytes(n: number | null): string {
  if (n === null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MiB`;
}

export function FigureInspectorDrawer() {
  const selectedFigureUlid = useReader((s) => s.selectedFigureUlid);
  const payload = useReader((s) => s.figurePayload);
  const loading = useReader((s) => s.figureLoading);
  const closeFigureDrawer = useReader((s) => s.closeFigureDrawer);

  if (!selectedFigureUlid) return null;

  const imgSrc = figureSrc(payload);

  return (
    <aside className="w-96 border-l border-gray-200 bg-white shrink-0 flex flex-col overflow-hidden">
      <header className="border-b border-gray-200 px-4 py-3 flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 text-[10px] font-mono uppercase">
              figure
            </span>
            <span className="text-[10px] font-mono text-gray-400 truncate">
              {selectedFigureUlid}
            </span>
          </div>
          <p className="text-sm text-gray-900 leading-snug">
            {payload?.caption || (
              <span className="italic text-gray-400">(no caption)</span>
            )}
          </p>
        </div>
        <button
          onClick={closeFigureDrawer}
          className="text-gray-400 hover:text-gray-900 shrink-0"
          aria-label="Close figure inspector"
        >
          ✕
        </button>
      </header>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {loading && <p className="text-xs text-gray-400">loading figure…</p>}

        {!loading && imgSrc && (
          <section>
            <img
              src={imgSrc}
              alt={payload?.caption || "Figure"}
              className="max-w-full rounded border border-gray-200 bg-gray-50"
            />
          </section>
        )}

        {!loading && !imgSrc && (
          <p className="text-xs text-gray-400">
            No raster payload available for this figure.
          </p>
        )}

        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
            Media
          </h3>
          <dl className="text-xs text-gray-700 space-y-1.5 font-mono">
            <div className="flex gap-2">
              <dt className="text-gray-400 w-20 shrink-0">storage</dt>
              <dd>{payload?.storage ?? "—"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-gray-400 w-20 shrink-0">mime</dt>
              <dd>{payload?.mime_type ?? "—"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-gray-400 w-20 shrink-0">bytes</dt>
              <dd>{formatBytes(payload?.byte_len ?? null)}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-gray-400 w-20 shrink-0">sha256</dt>
              <dd className="break-all">{payload?.sha256?.slice(0, 16) ?? "—"}
                {payload?.sha256 ? "…" : ""}
              </dd>
            </div>
            {payload?.external_path_resolved && (
              <div className="flex gap-2">
                <dt className="text-gray-400 w-20 shrink-0">path</dt>
                <dd className="break-all text-[10px] leading-relaxed">
                  {payload.external_path_resolved}
                </dd>
              </div>
            )}
          </dl>
        </section>

        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
            Enrichments
            {payload ? ` (${payload.enrichments.length})` : ""}
          </h3>
          {!payload && !loading && (
            <p className="text-xs text-gray-400">No enrichments loaded.</p>
          )}
          {payload && payload.enrichments.length === 0 && (
            <p className="text-xs text-gray-400">
              No media_classification / visual_description / figure_discourse rows.
            </p>
          )}
          <div className="space-y-2">
            {payload?.enrichments.map((row) => (
              <EnrichmentBlock key={row.ulid} row={row} />
            ))}
          </div>
        </section>
      </div>
    </aside>
  );
}
