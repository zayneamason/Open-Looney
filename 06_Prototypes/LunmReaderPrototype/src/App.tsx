import { useEffect, useMemo, useState } from "react";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { useInspector } from "./store";
import type { HealthLevel, ProfileConfigRow, Tab, TableRow } from "./types";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "health", label: "Health" },
  { id: "memory", label: "Memory" },
  { id: "graph", label: "Graph" },
  { id: "conversations", label: "Conversations" },
  { id: "nexus", label: "Nexus" },
  { id: "config", label: "Config" },
];

function shortPath(path: string): string {
  const parts = path.split("/");
  return parts.slice(-3).join("/");
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "--";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function cell(row: TableRow, key: string): string {
  return formatValue(row.values[key]);
}

function roleClass(role: string): string {
  const normalized = role.toLowerCase();
  if (normalized === "user") return "bg-blue-50 text-blue-700 border-blue-200";
  if (normalized === "assistant") return "bg-emerald-50 text-emerald-700 border-emerald-200";
  if (normalized === "system") return "bg-amber-50 text-amber-700 border-amber-200";
  return "bg-gray-50 text-gray-700 border-gray-200";
}

function healthClass(level: HealthLevel): string {
  if (level === "error") return "bg-red-50 text-red-800 border-red-200";
  if (level === "warning") return "bg-amber-50 text-amber-800 border-amber-200";
  return "bg-emerald-50 text-emerald-800 border-emerald-200";
}

function ShellHeader() {
  const matrix = useInspector((s) => s.matrix);
  const overview = useInspector((s) => s.overview);
  const loading = useInspector((s) => s.loading);
  const pickAndOpen = useInspector((s) => s.pickAndOpen);
  const openPath = useInspector((s) => s.openPath);
  const close = useInspector((s) => s.close);
  const [pathInput, setPathInput] = useState("");

  async function submitPath() {
    const path = pathInput.trim();
    if (!path || loading) return;
    await openPath(path);
  }

  return (
    <header className="border-b border-gray-200 bg-white px-5 py-3">
      <div className="flex items-center gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="text-base font-semibold text-gray-950">LUNM Inspector</h1>
            <span className="rounded bg-purple-100 px-1.5 py-0.5 text-[10px] font-mono uppercase text-purple-700">
              read-only
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs text-gray-500">
            {matrix ? shortPath(matrix.path) : "Open a LUNM runtime matrix .lun file"}
          </p>
        </div>
        {overview && (
          <div className="hidden lg:flex items-center gap-3 text-[11px] font-mono text-gray-500">
            <span>app_id 0x{overview.application_id.toString(16).toUpperCase()}</span>
            <span>user_version {overview.user_version}</span>
            <span>format {overview.format_version ?? "missing"}</span>
          </div>
        )}
        <button
          type="button"
          onClick={() => void pickAndOpen()}
          disabled={loading}
          className="rounded bg-gray-900 px-3 py-2 text-xs font-medium text-white hover:bg-gray-700 disabled:opacity-50"
        >
          Open
        </button>
        {matrix && (
          <button
            type="button"
            onClick={() => void close()}
            className="rounded border border-gray-200 px-3 py-2 text-xs text-gray-700 hover:bg-gray-50"
          >
            Close
          </button>
        )}
      </div>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void submitPath();
        }}
      >
        <input
          value={pathInput}
          onChange={(e) => setPathInput(e.target.value)}
          placeholder="/private/tmp/lunm-reader-smoke/data/user/memory_matrix.lun"
          className="min-w-0 flex-1 rounded border border-gray-200 px-3 py-2 font-mono text-xs focus:border-purple-500 focus:outline-none disabled:opacity-50"
          disabled={loading}
        />
        <button
          type="submit"
          disabled={loading || !pathInput.trim()}
          className="rounded border border-gray-200 px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >
          Open Path
        </button>
      </form>
    </header>
  );
}

function TabBar() {
  const matrix = useInspector((s) => s.matrix);
  const activeTab = useInspector((s) => s.activeTab);
  const setTab = useInspector((s) => s.setTab);
  return (
    <nav className="border-b border-gray-200 bg-white px-5 flex gap-1 overflow-x-auto">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          disabled={!matrix}
          onClick={() => void setTab(tab.id)}
          className={`border-b-2 px-3 py-2 text-xs font-medium transition ${
            activeTab === tab.id
              ? "border-purple-600 text-purple-700"
              : "border-transparent text-gray-500 hover:text-gray-900"
          } disabled:opacity-40`}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}

function EmptyState() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="max-w-sm text-center">
        <h2 className="text-sm font-semibold text-gray-950">No matrix open</h2>
        <p className="mt-2 text-sm leading-6 text-gray-500">
          Open a `memory_matrix.lun` file to inspect its LUNM identity,
          format-invariant tables, conversations, and Nexus pointers.
        </p>
      </div>
    </div>
  );
}

function Overview() {
  const overview = useInspector((s) => s.overview);
  if (!overview) return null;

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
      <section>
        <h2 className="mb-3 text-sm font-semibold text-gray-950">Identity</h2>
        <div className="overflow-hidden rounded border border-gray-200 bg-white">
          {[
            ["Path", overview.path],
            ["Application ID", `0x${overview.application_id.toString(16).toUpperCase()}`],
            ["User version", overview.user_version],
            ["LUNM format", overview.format_version ?? "missing"],
            ["Matrix ULID", overview.matrix_ulid ?? "missing"],
            ["Created at", overview.created_at ?? "missing"],
            ["Engine version", overview.engine_version ?? "missing"],
          ].map(([label, value]) => (
            <div key={label} className="grid grid-cols-[150px_minmax(0,1fr)] border-b border-gray-100 last:border-b-0">
              <dt className="bg-gray-50 px-3 py-2 text-xs font-medium text-gray-500">{label}</dt>
              <dd className="break-all px-3 py-2 text-xs font-mono text-gray-900">{formatValue(value)}</dd>
            </div>
          ))}
        </div>
      </section>
      <section>
        <h2 className="mb-3 text-sm font-semibold text-gray-950">Format-Invariant Tables</h2>
        <div className="overflow-hidden rounded border border-gray-200 bg-white">
          {overview.table_counts.map((row) => (
            <div key={row.table} className="flex items-center justify-between border-b border-gray-100 px-3 py-2 last:border-b-0">
              <span className="font-mono text-xs text-gray-800">{row.table}</span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] font-mono uppercase ${
                row.present ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"
              }`}>
                {row.present ? `${row.count ?? 0} rows` : "missing"}
              </span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function Health() {
  const health = useInspector((s) => s.health);
  if (!health) return null;
  return (
    <section>
      <div className="mb-3 flex items-center gap-3">
        <h2 className="text-sm font-semibold text-gray-950">Health</h2>
        <span className="text-xs text-gray-500">
          {health.error_count} errors, {health.warning_count} warnings
        </span>
      </div>
      <div className="space-y-2">
        {health.checks.map((check, idx) => (
          <div key={`${check.code}-${idx}`} className={`rounded border px-3 py-2 ${healthClass(check.level)}`}>
            <div className="font-mono text-[10px] uppercase">{check.level} · {check.code}</div>
            <div className="mt-1 text-sm">{check.message}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

function TableView({ rows }: { rows: TableRow[] }) {
  const columns = useMemo(() => {
    const seen = new Set<string>();
    for (const row of rows) {
      for (const key of Object.keys(row.values)) seen.add(key);
    }
    return Array.from(seen);
  }, [rows]);

  if (rows.length === 0) {
    return <div className="rounded border border-dashed border-gray-200 bg-white p-8 text-center text-sm text-gray-400">No rows.</div>;
  }

  return (
    <div className="overflow-auto rounded border border-gray-200 bg-white">
      <table className="min-w-full divide-y divide-gray-200 text-left text-xs">
        <thead className="sticky top-0 bg-gray-50">
          <tr>
            {columns.map((col) => (
              <th key={col} className="whitespace-nowrap px-3 py-2 font-mono font-semibold text-gray-500">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((row, idx) => (
            <tr key={idx} className="hover:bg-gray-50">
              {columns.map((col) => (
                <td key={col} className="max-w-[360px] whitespace-pre-wrap break-words px-3 py-2 font-mono text-gray-800">
                  {formatValue(row.values[col])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ConfigTable({ rows }: { rows: ProfileConfigRow[] }) {
  if (rows.length === 0) {
    return <div className="rounded border border-dashed border-gray-200 bg-white p-8 text-center text-sm text-gray-400">No config rows.</div>;
  }
  return (
    <div className="overflow-auto rounded border border-gray-200 bg-white">
      <table className="min-w-full divide-y divide-gray-200 text-left text-xs">
        <thead className="bg-gray-50">
          <tr>
            {["key", "value", "type", "updated", "description"].map((col) => (
              <th key={col} className="px-3 py-2 font-mono font-semibold text-gray-500">{col}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((row) => (
            <tr key={row.key} className={row.key.startsWith("lunm.") ? "bg-purple-50/50" : "hover:bg-gray-50"}>
              <td className="px-3 py-2 font-mono text-gray-900">{row.key}</td>
              <td className="max-w-[420px] break-words px-3 py-2 font-mono text-gray-800">{row.value}</td>
              <td className="px-3 py-2 font-mono text-gray-500">{row.value_type}</td>
              <td className="px-3 py-2 font-mono text-gray-500">{row.updated_at ?? "--"}</td>
              <td className="px-3 py-2 text-gray-500">{row.description ?? "--"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ConversationPanel() {
  const rows = useInspector((s) => s.rows);
  const sessionId = useInspector((s) => s.sessionId);
  const setSessionId = useInspector((s) => s.setSessionId);
  const loading = useInspector((s) => s.loading);
  const selectedSession = sessionId.trim();

  if (!selectedSession) {
    return (
      <section>
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-gray-950">Sessions</h2>
            <p className="mt-1 text-xs text-gray-500">
              Click a row to read its conversation turns. `actual_turns` is computed from `conversation_turns`.
            </p>
          </div>
          {loading && <span className="text-xs text-gray-400">loading...</span>}
        </div>
        <input
          value={sessionId}
          onChange={(e) => void setSessionId(e.target.value)}
          placeholder="session_id; blank shows sessions"
          className="mb-3 w-full max-w-lg rounded border border-gray-200 px-3 py-2 text-xs focus:border-purple-500 focus:outline-none"
        />
        <div className="overflow-auto rounded border border-gray-200 bg-white">
          <table className="min-w-full divide-y divide-gray-200 text-left text-xs">
            <thead className="sticky top-0 bg-gray-50">
              <tr>
                {["session_id", "actual_turns", "stored_turns_count", "started_at", "app_context", "ended_at", "metadata"].map((col) => (
                  <th key={col} className="whitespace-nowrap px-3 py-2 font-mono font-semibold text-gray-500">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((row) => {
                const id = cell(row, "session_id");
                return (
                  <tr
                    key={id}
                    onClick={() => void setSessionId(id)}
                    className="cursor-pointer hover:bg-purple-50"
                  >
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-purple-700">{id}</td>
                    <td className="px-3 py-2 font-mono text-gray-900">{cell(row, "actual_turns")}</td>
                    <td className="px-3 py-2 font-mono text-gray-500">{cell(row, "stored_turns_count")}</td>
                    <td className="px-3 py-2 font-mono text-gray-800">{cell(row, "started_at")}</td>
                    <td className="px-3 py-2 font-mono text-gray-800">{cell(row, "app_context")}</td>
                    <td className="px-3 py-2 font-mono text-gray-500">{cell(row, "ended_at")}</td>
                    <td className="max-w-[320px] truncate px-3 py-2 font-mono text-gray-500">{cell(row, "metadata")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    );
  }

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-gray-950">Conversation</h2>
          <p className="mt-1 break-all font-mono text-xs text-gray-500">{selectedSession}</p>
        </div>
        <button
          type="button"
          onClick={() => void setSessionId("")}
          className="rounded border border-gray-200 px-3 py-2 text-xs text-gray-700 hover:bg-gray-50"
        >
          Sessions
        </button>
      </div>
      <input
        value={sessionId}
        onChange={(e) => void setSessionId(e.target.value)}
        placeholder="session_id"
        className="mb-3 w-full rounded border border-gray-200 px-3 py-2 font-mono text-xs focus:border-purple-500 focus:outline-none"
      />
      {loading && <div className="mb-3 text-xs text-gray-400">loading...</div>}
      {rows.length === 0 ? (
        <div className="rounded border border-dashed border-gray-200 bg-white p-8 text-center text-sm text-gray-400">
          No turns for this session.
        </div>
      ) : (
        <div className="space-y-3">
          {rows.map((row) => {
            const role = cell(row, "role");
            return (
              <article key={cell(row, "id")} className="rounded border border-gray-200 bg-white p-3">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className={`rounded border px-2 py-0.5 text-[10px] font-mono uppercase ${roleClass(role)}`}>
                    {role}
                  </span>
                  <span className="font-mono text-[11px] text-gray-500">{cell(row, "created_at")}</span>
                  <span className="font-mono text-[11px] text-gray-400">{cell(row, "turn_type")}</span>
                  {cell(row, "tier") !== "--" && (
                    <span className="font-mono text-[11px] text-gray-400">tier {cell(row, "tier")}</span>
                  )}
                </div>
                <div className="whitespace-pre-wrap break-words text-sm leading-6 text-gray-900">
                  {cell(row, "content")}
                </div>
                {cell(row, "thread_id") !== "--" && (
                  <div className="mt-2 break-all font-mono text-[11px] text-gray-400">
                    thread {cell(row, "thread_id")}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function FilterBar() {
  const activeTab = useInspector((s) => s.activeTab);
  const memoryFilters = useInspector((s) => s.memoryFilters);
  const graphNodeId = useInspector((s) => s.graphNodeId);
  const configPrefix = useInspector((s) => s.configPrefix);
  const setMemoryFilter = useInspector((s) => s.setMemoryFilter);
  const setGraphNodeId = useInspector((s) => s.setGraphNodeId);
  const setConfigPrefix = useInspector((s) => s.setConfigPrefix);

  if (activeTab === "memory") {
    return (
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          value={memoryFilters.nodeType ?? ""}
          onChange={(e) => void setMemoryFilter("nodeType", e.target.value)}
          placeholder="node_type"
          className="rounded border border-gray-200 px-3 py-2 text-xs focus:border-purple-500 focus:outline-none"
        />
        <input
          value={memoryFilters.classification ?? ""}
          onChange={(e) => void setMemoryFilter("classification", e.target.value)}
          placeholder="classification"
          className="rounded border border-gray-200 px-3 py-2 text-xs focus:border-purple-500 focus:outline-none"
        />
      </div>
    );
  }
  if (activeTab === "graph") {
    return (
      <input
        value={graphNodeId}
        onChange={(e) => void setGraphNodeId(e.target.value)}
        placeholder="filter by memory node id"
        className="mb-3 w-full max-w-lg rounded border border-gray-200 px-3 py-2 text-xs focus:border-purple-500 focus:outline-none"
      />
    );
  }
  if (activeTab === "conversations") {
    return null;
  }
  if (activeTab === "config") {
    return (
      <input
        value={configPrefix}
        onChange={(e) => void setConfigPrefix(e.target.value)}
        placeholder="prefix; try lunm."
        className="mb-3 w-full max-w-lg rounded border border-gray-200 px-3 py-2 text-xs focus:border-purple-500 focus:outline-none"
      />
    );
  }
  return null;
}

function ActivePanel() {
  const matrix = useInspector((s) => s.matrix);
  const activeTab = useInspector((s) => s.activeTab);
  const rows = useInspector((s) => s.rows);
  const configRows = useInspector((s) => s.configRows);
  const loading = useInspector((s) => s.loading);

  if (!matrix) return <EmptyState />;
  if (activeTab === "overview") return <Overview />;
  if (activeTab === "health") return <Health />;
  if (activeTab === "conversations") return <ConversationPanel />;
  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold capitalize text-gray-950">{activeTab}</h2>
        {loading && <span className="text-xs text-gray-400">loading...</span>}
      </div>
      <FilterBar />
      {activeTab === "config" ? <ConfigTable rows={configRows} /> : <TableView rows={rows} />}
    </section>
  );
}

export default function App() {
  const error = useInspector((s) => s.error);
  const openPath = useInspector((s) => s.openPath);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void getCurrentWebview().onDragDropEvent((event) => {
      if (event.payload.type === "over") {
        setDragging(true);
      } else if (event.payload.type === "drop") {
        setDragging(false);
        const [path] = event.payload.paths;
        if (path) void openPath(path);
      } else {
        setDragging(false);
      }
    }).then((fn) => {
      unlisten = fn;
    });
    return () => unlisten?.();
  }, [openPath]);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-gray-100 text-gray-950">
      <ShellHeader />
      <TabBar />
      {dragging && (
        <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center bg-purple-950/20 p-8">
          <div className="rounded border border-purple-300 bg-white px-5 py-3 text-sm font-medium text-purple-900 shadow-lg">
            Drop .lun matrix
          </div>
        </div>
      )}
      {error && (
        <div className="border-b border-red-200 bg-red-50 px-5 py-2 text-sm text-red-900">
          {error}
        </div>
      )}
      <main className="min-h-0 flex-1 overflow-auto p-5">
        <ActivePanel />
      </main>
    </div>
  );
}
