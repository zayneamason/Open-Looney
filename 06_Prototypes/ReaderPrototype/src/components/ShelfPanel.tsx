// SPEC-007 SketchedShelf — demo surface (v0.3.3 slice scope).
// Opens N cartridges in read-only mode, lets the user pick a sketch kind
// and a query term, and shows the candidate cartridges. After the sketch
// filter returns, verify-by-opening (§ 7.3.3) runs automatically against
// every non-`unknown` candidate; badges upgrade to `confirmed` or
// downgrade to `false_positive` as each verify call resolves. Clicking a
// row opens that cartridge in the Reader tab with per-kind navigation.

import { open } from "@tauri-apps/plugin-dialog";
import { useShelf } from "../shelfStore";
import {
  ALL_KINDS,
  kindLabel,
  statusBadgeClasses,
  statusLabel,
} from "../shelf";
import type { CandidateStatus, SketchKind } from "../types";

function basename(p: string): string {
  const i = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  return i >= 0 ? p.slice(i + 1) : p;
}

/** Per-row effective status: the verify-pass result if available, otherwise
 * the sketch-pass status. Encapsulates the "verifying overrides sketch"
 * rule so callers don't have to remember the priority. */
function effectiveStatus(
  rowStatus: CandidateStatus,
  upgraded: CandidateStatus | undefined,
  isVerifying: boolean,
): { status: CandidateStatus; isVerifying: boolean } {
  if (upgraded !== undefined) return { status: upgraded, isVerifying: false };
  return { status: rowStatus, isVerifying };
}

export function ShelfPanel() {
  const shelf = useShelf((s) => s.shelf);
  const query = useShelf((s) => s.query);
  const kind = useShelf((s) => s.kind);
  const candidates = useShelf((s) => s.candidates);
  const verifying = useShelf((s) => s.verifying);
  const verifyResults = useShelf((s) => s.verifyResults);
  const loading = useShelf((s) => s.loading);
  const error = useShelf((s) => s.error);
  const setQuery = useShelf((s) => s.setQuery);
  const setKind = useShelf((s) => s.setKind);
  const runQuery = useShelf((s) => s.runQuery);
  const openShelfFn = useShelf((s) => s.openShelf);
  const closeShelfFn = useShelf((s) => s.closeShelf);
  const clickedCandidate = useShelf((s) => s.clickedCandidate);

  async function pick() {
    const selected = await open({
      multiple: true,
      directory: false,
      filters: [{ name: ".lun cartridges", extensions: ["lun"] }],
    });
    if (!selected) return;
    const paths = Array.isArray(selected) ? selected : [selected];
    await openShelfFn(paths);
  }

  // Aggregate counts use the effective per-row status (verify-pass result
  // when available; otherwise the sketch-pass status). Mirrors what the
  // user sees in the badge column.
  const counts = candidates
    ? candidates.reduce(
        (acc, c) => {
          const eff =
            verifyResults[c.path] !== undefined
              ? verifyResults[c.path]
              : c.status;
          acc[eff] = (acc[eff] ?? 0) + 1;
          return acc;
        },
        {} as Record<CandidateStatus, number>,
      )
    : null;

  return (
    <div className="flex flex-col h-full p-6 max-w-3xl mx-auto w-full overflow-auto">
      <header className="mb-4">
        <h2 className="text-xl font-semibold mb-1">Shelf</h2>
        <p className="text-sm text-gray-600">
          SPEC-007 bloom-filter pre-filter across multiple cartridges. After
          the sketch returns probable matches, each candidate is verified by
          opening the cartridge and running the precise query — false
          positives are surfaced as a data-quality signal. Click any row to
          open that cartridge in the Reader.
        </p>
      </header>

      <div className="flex gap-2 mb-4">
        <button
          onClick={pick}
          className="px-4 py-2 bg-gray-900 text-white rounded-md hover:bg-gray-700 transition text-sm"
        >
          {shelf ? "Replace shelf…" : "Open shelf…"}
        </button>
        {shelf && (
          <button
            onClick={closeShelfFn}
            className="px-4 py-2 bg-gray-100 text-gray-900 rounded-md hover:bg-gray-200 transition text-sm"
          >
            Close shelf
          </button>
        )}
      </div>

      {shelf ? (
        <>
          <section className="mb-6">
            <h3 className="text-sm font-medium text-gray-700 mb-2">
              Shelf ({shelf.count} {shelf.count === 1 ? "cartridge" : "cartridges"})
            </h3>
            <ul className="text-sm space-y-1">
              {shelf.paths.map((path, i) => {
                const kinds = shelf.sketches_per_cartridge[i] ?? [];
                return (
                  <li key={path} className="flex justify-between gap-4 py-1 border-b border-gray-100">
                    <span className="font-mono text-xs truncate" title={path}>
                      {basename(path)}
                    </span>
                    <span className="text-xs text-gray-500 shrink-0">
                      {kinds.length === 0
                        ? "no sketches"
                        : kinds.map((k) => k).join(", ")}
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>

          <section className="mb-4">
            <h3 className="text-sm font-medium text-gray-700 mb-2">Filter</h3>
            <div className="flex gap-2 items-stretch">
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value as SketchKind)}
                className="px-3 py-2 border border-gray-300 rounded-md text-sm bg-white"
              >
                {ALL_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {kindLabel(k)}
                  </option>
                ))}
              </select>
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") runQuery();
                }}
                placeholder={
                  kind === "fts_term"
                    ? "term (e.g. virtue)"
                    : kind === "entity_surface"
                      ? "entity (e.g. Marcus Aurelius)"
                      : "ULID (26 chars)"
                }
                className="flex-1 px-3 py-2 border border-gray-300 rounded-md text-sm"
              />
              <button
                onClick={runQuery}
                disabled={loading}
                className="px-4 py-2 bg-gray-900 text-white rounded-md hover:bg-gray-700 transition text-sm disabled:opacity-50"
              >
                {loading ? "…" : "Filter"}
              </button>
            </div>
          </section>

          {candidates && counts && (
            <section>
              <div className="text-xs text-gray-600 mb-2 flex flex-wrap gap-x-3 gap-y-1">
                {counts.confirmed ? <span>{counts.confirmed} confirmed</span> : null}
                {counts.probable ? <span>{counts.probable} probable</span> : null}
                {counts.unknown ? <span>{counts.unknown} unknown</span> : null}
                {counts.false_positive ? (
                  <span>{counts.false_positive} false positive</span>
                ) : null}
                {verifying.size > 0 ? (
                  <span className="text-amber-700">{verifying.size} verifying…</span>
                ) : null}
              </div>
              {candidates.length === 0 ? (
                <p className="text-sm text-gray-500 italic">
                  No candidates. Every cartridge's sketch says "definitely not."
                </p>
              ) : (
                <ul className="space-y-1">
                  {candidates.map((c) => {
                    const eff = effectiveStatus(
                      c.status,
                      verifyResults[c.path],
                      verifying.has(c.path),
                    );
                    return (
                      <li
                        key={c.path}
                        onClick={() => clickedCandidate(c.path)}
                        className="flex justify-between items-center py-2 px-3 bg-white border border-gray-200 rounded-md cursor-pointer hover:bg-gray-50 hover:border-gray-300 transition"
                        title={`Open ${basename(c.path)} in Reader`}
                      >
                        <span className="font-mono text-xs truncate" title={c.path}>
                          {basename(c.path)}
                        </span>
                        <span className="flex items-center gap-2 shrink-0">
                          {eff.isVerifying && (
                            <span className="text-xs text-amber-700 italic">
                              verifying…
                            </span>
                          )}
                          <span
                            className={`text-xs px-2 py-0.5 rounded-full ${statusBadgeClasses(eff.status)}`}
                          >
                            {statusLabel(eff.status)}
                          </span>
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          )}
        </>
      ) : (
        <p className="text-sm text-gray-500 italic">
          Open one or more <code>.lun</code> cartridges to filter by sketch.
        </p>
      )}

      {error && (
        <div className="mt-4 p-3 bg-red-50 text-red-800 text-sm rounded-md">{error}</div>
      )}
    </div>
  );
}
