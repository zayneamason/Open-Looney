import { useReader } from "../store";

export function Toaster() {
  const toasts = useReader((s) => s.toasts);
  const dismissToast = useReader((s) => s.dismissToast);
  if (!toasts.length) return null;
  return (
    <div className="fixed bottom-4 right-4 flex flex-col gap-2 max-w-md z-50">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`px-4 py-3 rounded-md shadow-md text-sm flex items-start gap-3 ${
            t.level === "error"
              ? "bg-red-50 text-red-900 border border-red-200"
              : "bg-gray-900 text-white"
          }`}
        >
          <span className="flex-1 whitespace-pre-wrap">{t.text}</span>
          <button
            onClick={() => dismissToast(t.id)}
            className="opacity-60 hover:opacity-100 shrink-0"
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
