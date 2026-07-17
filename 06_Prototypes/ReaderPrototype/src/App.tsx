import { useReader } from "./store";
import { useMode } from "./modeStore";
import { FilePicker } from "./components/FilePicker";
import { Header } from "./components/Header";
import { Toaster } from "./components/Toaster";
import { DocTree } from "./components/DocTree";
import { NodeView } from "./components/NodeView";
import { ExtractionsPanel } from "./components/ExtractionsPanel";
import { ProvenanceDrawer } from "./components/ProvenanceDrawer";
import { TabBar } from "./components/TabBar";
import { SearchPanel } from "./components/SearchPanel";
import { DocumentView } from "./components/DocumentView";
import { ShelfPanel } from "./components/ShelfPanel";

export default function App() {
  const mode = useMode((s) => s.mode);
  const setMode = useMode((s) => s.setMode);
  const cartridge = useReader((s) => s.cartridge);
  const view = useReader((s) => s.view);
  const selectedClaim = useReader((s) => s.selectedClaim);

  return (
    <div className="h-screen flex flex-col bg-gray-50 text-gray-900 font-sans">
      <nav className="flex border-b border-gray-200 bg-white shrink-0">
        <button
          onClick={() => setMode("reader")}
          className={`px-4 py-2 text-sm border-r border-gray-200 ${
            mode === "reader"
              ? "bg-gray-900 text-white"
              : "text-gray-700 hover:bg-gray-50"
          }`}
        >
          Reader
        </button>
        <button
          onClick={() => setMode("shelf")}
          className={`px-4 py-2 text-sm ${
            mode === "shelf"
              ? "bg-gray-900 text-white"
              : "text-gray-700 hover:bg-gray-50"
          }`}
        >
          Shelf
        </button>
      </nav>
      {mode === "reader" ? (
        cartridge ? (
          <>
            <Header />
            <div className="flex-1 flex min-h-0">
              <aside className="w-72 border-r border-gray-200 bg-white shrink-0 overflow-hidden flex flex-col">
                <DocTree />
              </aside>
              <div className="flex-1 min-w-0 flex flex-col bg-white">
                <TabBar />
                <div className="flex-1 min-h-0">
                  {view === "document" && <DocumentView />}
                  {view === "tree" && <NodeView />}
                  {view === "extractions" && <ExtractionsPanel />}
                  {view === "search" && <SearchPanel />}
                </div>
              </div>
              {selectedClaim && <ProvenanceDrawer />}
            </div>
          </>
        ) : (
          <FilePicker />
        )
      ) : (
        <ShelfPanel />
      )}
      <Toaster />
    </div>
  );
}
