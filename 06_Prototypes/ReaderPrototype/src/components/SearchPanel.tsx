import { useReader } from "../store";
import { isSemanticSearchAvailable } from "../semanticSearch";
import type { SearchHit } from "../types";

function ResultRow({ hit }: { hit: SearchHit }) {
  const selectNode = useReader((s) => s.selectNode);
  const setView = useReader((s) => s.setView);
  return (
    <button
      onClick={async () => {
        await selectNode(hit.node_ulid);
        setView("document");
      }}
      className="block w-full text-left border-b border-gray-100 px-4 py-3 hover:bg-gray-50"
    >
      <div className="flex items-center gap-2 text-[10px] text-gray-400 font-mono mb-1">
        <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 uppercase">
          {hit.source}
        </span>
        {hit.level && (
          <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 uppercase">
            {hit.level}
          </span>
        )}
        <span>rank {hit.rank.toFixed(3)}</span>
        <span className="truncate" title={hit.node_ulid}>node {hit.node_ulid.slice(-8)}</span>
      </div>
      <p
        className="text-sm text-gray-900 leading-snug [&_mark]:bg-yellow-200 [&_mark]:text-gray-900 [&_mark]:rounded [&_mark]:px-0.5"
        dangerouslySetInnerHTML={{ __html: hit.snippet_html }}
      />
    </button>
  );
}

export function SearchPanel() {
  const cartridge = useReader((s) => s.cartridge);
  const query = useReader((s) => s.searchQuery);
  const searchMode = useReader((s) => s.searchMode);
  const results = useReader((s) => s.searchResults);
  const loading = useReader((s) => s.searchLoading);
  const error = useReader((s) => s.searchError);
  const setSearchQuery = useReader((s) => s.setSearchQuery);
  const setSearchMode = useReader((s) => s.setSearchMode);
  const runSearch = useReader((s) => s.runSearch);

  const semanticAvailable = cartridge ? isSemanticSearchAvailable(cartridge.meta) : false;

  return (
    <div className="h-full flex flex-col">
      <div className="border-b border-gray-200 px-4 py-3 flex items-center gap-2">
        <div className="flex rounded border border-gray-200 overflow-hidden text-xs">
          <button
            onClick={() => setSearchMode("keyword")}
            className={`px-3 py-2 ${
              searchMode === "keyword" ? "bg-gray-900 text-white" : "bg-white text-gray-600"
            }`}
          >
            Keyword
          </button>
          <button
            onClick={() => setSearchMode("semantic")}
            disabled={!semanticAvailable}
            title={
              semanticAvailable
                ? undefined
                : "This cartridge's embeddings were built with a different model — semantic search unavailable."
            }
            className={`px-3 py-2 border-l border-gray-200 ${
              searchMode === "semantic" ? "bg-gray-900 text-white" : "bg-white text-gray-600"
            } disabled:opacity-40 disabled:cursor-not-allowed`}
          >
            Semantic
          </button>
        </div>
        <input
          type="text"
          value={query}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void runSearch();
          }}
          placeholder={
            searchMode === "semantic"
              ? "Describe a concept — try a paraphrase, not exact words"
              : "FTS5 query — try 'virtue' or 'death OR mortal'"
          }
          className="flex-1 text-sm px-3 py-2 border border-gray-200 rounded focus:outline-none focus:border-gray-500"
          autoFocus
        />
        <button
          onClick={() => void runSearch()}
          disabled={!query.trim() || loading}
          className="text-xs px-4 py-2 bg-gray-900 text-white rounded hover:bg-gray-700 disabled:opacity-50"
        >
          {loading ? "searching…" : "Search"}
        </button>
      </div>

      {error && (
        <div className="px-4 py-3 bg-red-50 border-b border-red-200 text-xs text-red-900">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {!loading && results.length === 0 && query.trim() && !error && (
          <div className="p-8 text-center text-sm text-gray-400">
            No matches.{" "}
            {searchMode === "semantic"
              ? "Try a different phrasing or concept."
              : 'Try different terms or use FTS5 operators (AND, OR, NEAR, "quoted phrase").'}
          </div>
        )}
        {!loading && results.length === 0 && !query.trim() && (
          <div className="p-8 text-center text-sm text-gray-400">
            {searchMode === "semantic" ? (
              <p>
                Natural-language search over the cartridge's stored MiniLM embeddings — try a
                paraphrase or concept instead of exact words.
              </p>
            ) : (
              <p>Full-text search over <code className="font-mono">doc_nodes.content</code> via FTS5.</p>
            )}
          </div>
        )}
        {results.map((hit) => (
          <ResultRow key={hit.node_ulid} hit={hit} />
        ))}
      </div>

      {results.length > 0 && (
        <div className="border-t border-gray-200 px-4 py-2 text-[11px] text-gray-500">
          {results.length} hit{results.length === 1 ? "" : "s"} · click to navigate to node
        </div>
      )}
    </div>
  );
}
