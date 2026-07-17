import { useReader } from "../store";
import type { DocNode } from "../types";

function ParentChain({ node }: { node: DocNode }) {
  const chain = node.parent_chain ?? [];
  if (chain.length === 0) return null;
  return (
    <nav className="text-xs text-gray-500 flex flex-wrap gap-1 items-center mb-3">
      {chain.map((p, i) => (
        <span key={p.ulid} className="flex items-center gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wide text-gray-400">
            {p.node_type}
          </span>
          <span className="truncate max-w-xs">{p.content_preview}</span>
          {i < chain.length - 1 && <span className="text-gray-300">›</span>}
        </span>
      ))}
    </nav>
  );
}

export function NodeView() {
  const selectedNode = useReader((s) => s.selectedNode);

  if (!selectedNode) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-gray-400">
        Select a node from the tree to view its content.
      </div>
    );
  }

  const meta = selectedNode.meta_json;
  const hasMeta = meta && Object.keys(meta).length > 0;
  const pageNum = (meta as { page_num?: number } | null)?.page_num;

  return (
    <div className="h-full overflow-y-auto p-6 max-w-3xl mx-auto">
      <ParentChain node={selectedNode} />
      <header className="mb-4 flex items-center gap-3 flex-wrap text-xs text-gray-500">
        <span className="font-mono uppercase tracking-wide text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-700">
          {selectedNode.node_type}
        </span>
        <span>position {selectedNode.position}</span>
        {typeof pageNum === "number" && <span>page {pageNum}</span>}
        {typeof selectedNode.children_count === "number" && selectedNode.children_count > 0 && (
          <span>{selectedNode.children_count} children</span>
        )}
        <span
          className="font-mono text-[10px] text-gray-400 cursor-pointer hover:text-gray-700"
          title="Click to copy ULID"
          onClick={() => void navigator.clipboard.writeText(selectedNode.ulid)}
        >
          {selectedNode.ulid}
        </span>
      </header>
      <article className="prose prose-sm max-w-none whitespace-pre-wrap text-sm leading-relaxed text-gray-900">
        {selectedNode.content || <span className="text-gray-400 italic">(no content)</span>}
      </article>
      {hasMeta && (
        <details className="mt-6 text-xs text-gray-500">
          <summary className="cursor-pointer hover:text-gray-900">meta_json</summary>
          <pre className="mt-2 p-2 bg-gray-100 rounded text-[11px] font-mono overflow-x-auto">
            {JSON.stringify(meta, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
