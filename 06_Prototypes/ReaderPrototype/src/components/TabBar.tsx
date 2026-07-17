import { useReader } from "../store";
import type { View } from "../store";

const TABS: { value: View; label: string }[] = [
  { value: "document", label: "Document" },
  { value: "tree", label: "Tree" },
  { value: "extractions", label: "Extractions" },
  { value: "search", label: "Search" },
];

export function TabBar() {
  const view = useReader((s) => s.view);
  const setView = useReader((s) => s.setView);
  return (
    <nav className="border-b border-gray-200 px-2 flex items-center bg-white">
      {TABS.map((t) => (
        <button
          key={t.value}
          onClick={() => setView(t.value)}
          className={`text-xs px-4 py-2 border-b-2 transition ${
            view === t.value
              ? "border-gray-900 text-gray-900 font-medium"
              : "border-transparent text-gray-500 hover:text-gray-900"
          }`}
        >
          {t.label}
        </button>
      ))}
    </nav>
  );
}
