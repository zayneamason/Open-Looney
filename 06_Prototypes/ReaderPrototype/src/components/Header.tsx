import { useReader } from "../store";

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  } catch {
    return iso;
  }
}

export function Header() {
  const cartridge = useReader((s) => s.cartridge);
  const closeCartridge = useReader((s) => s.closeCartridge);
  if (!cartridge) return null;

  const { meta } = cartridge;
  const titleSuspicious = (meta.title?.length ?? 100) < 20;
  const versionBadge = meta.format_version === "0.3"
    ? "v0.3"
    : meta.format_version
    ? `v${meta.format_version}`
    : "v?";
  const versionClass = meta.format_version === "0.3"
    ? "bg-green-100 text-green-800"
    : "bg-yellow-100 text-yellow-800";

  return (
    <header className="border-b border-gray-200 bg-white px-4 py-3 flex items-center justify-between gap-4">
      <div className="flex items-center gap-3 min-w-0 flex-1">
        <h1 className="text-sm font-semibold truncate" title={meta.title ?? undefined}>
          {meta.title || "(untitled)"}
        </h1>
        {titleSuspicious && (
          <span
            className="text-xs text-amber-600 shrink-0"
            title="Title is suspiciously short — parser may have truncated"
          >
            ⚠ short title
          </span>
        )}
        <span className="text-xs text-gray-500 truncate">
          {meta.source_filename} · {meta.source_format} · {meta.word_count?.toLocaleString() ?? "—"} words
        </span>
        <span className={`text-xs px-2 py-0.5 rounded shrink-0 ${versionClass}`}>{versionBadge}</span>
        <span className="text-xs text-gray-500 shrink-0">built {formatDate(meta.created_at)}</span>
      </div>
      <button
        onClick={closeCartridge}
        className="text-xs text-gray-600 hover:text-gray-900 px-2 py-1 rounded hover:bg-gray-100 shrink-0"
      >
        Close
      </button>
    </header>
  );
}
