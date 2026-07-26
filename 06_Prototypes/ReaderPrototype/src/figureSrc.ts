import { convertFileSrc } from "@tauri-apps/api/core";
import type { FigurePayload } from "./types";

/** Prefer embedded data URL; else asset URL for external sidecars. */
export function figureSrc(payload: FigurePayload | null | undefined): string | null {
  if (!payload) return null;
  if (payload.bytes_base64 && payload.mime_type) {
    return `data:${payload.mime_type};base64,${payload.bytes_base64}`;
  }
  if (payload.external_path_resolved) {
    try {
      return convertFileSrc(payload.external_path_resolved);
    } catch {
      return null;
    }
  }
  return null;
}
