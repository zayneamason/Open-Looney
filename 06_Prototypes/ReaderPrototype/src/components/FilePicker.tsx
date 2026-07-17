import { open } from "@tauri-apps/plugin-dialog";
import { useReader } from "../store";

export function FilePicker() {
  const openCartridge = useReader((s) => s.openCartridge);

  async function pick() {
    const selected = await open({
      multiple: false,
      directory: false,
      filters: [{ name: ".lun cartridge", extensions: ["lun"] }],
    });
    if (typeof selected === "string") {
      await openCartridge(selected);
    }
  }

  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center max-w-md px-6">
        <h1 className="text-3xl font-semibold mb-2">.lun Reader</h1>
        <p className="text-sm text-gray-600 mb-8">
          Open a v0.2 cartridge to browse its document tree, extractions, and full-text search.
        </p>
        <button
          onClick={pick}
          className="px-6 py-3 bg-gray-900 text-white rounded-md hover:bg-gray-700 transition"
        >
          Open .lun cartridge…
        </button>
      </div>
    </div>
  );
}
