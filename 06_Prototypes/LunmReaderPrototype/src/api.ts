import { invoke } from "@tauri-apps/api/core";
import type {
  LunmHealthReport,
  LunmOverview,
  MatrixHandle,
  MemoryNodeFilters,
  ProfileConfigRow,
  TableRow,
} from "./types";

const call = <T>(cmd: string, args?: Record<string, unknown>) => invoke<T>(cmd, args);

export const api = {
  openLunmMatrix: (path: string) =>
    call<MatrixHandle>("open_lunm_matrix", { path }),
  closeLunmMatrix: (handle: MatrixHandle) =>
    call<void>("close_lunm_matrix", { handle }),
  getLunmOverview: (handle: MatrixHandle) =>
    call<LunmOverview>("get_lunm_overview", { handle }),
  getLunmHealth: (handle: MatrixHandle) =>
    call<LunmHealthReport>("get_lunm_health", { handle }),
  listMemoryNodes: (
    handle: MatrixHandle,
    filters: MemoryNodeFilters,
    limit = 100,
    offset = 0,
  ) => call<TableRow[]>("list_memory_nodes", { handle, filters, limit, offset }),
  listGraphEdges: (
    handle: MatrixHandle,
    nodeId: string | null,
    limit = 100,
    offset = 0,
  ) => call<TableRow[]>("list_graph_edges", { handle, nodeId, limit, offset }),
  listSessions: (handle: MatrixHandle, limit = 100, offset = 0) =>
    call<TableRow[]>("list_sessions", { handle, limit, offset }),
  listConversationTurns: (
    handle: MatrixHandle,
    sessionId: string | null,
    limit = 100,
    offset = 0,
  ) =>
    call<TableRow[]>("list_conversation_turns", {
      handle,
      sessionId,
      limit,
      offset,
    }),
  listNexusRegistry: (handle: MatrixHandle, limit = 100, offset = 0) =>
    call<TableRow[]>("list_nexus_registry", { handle, limit, offset }),
  listNexusNodes: (handle: MatrixHandle, limit = 100, offset = 0) =>
    call<TableRow[]>("list_nexus_nodes", { handle, limit, offset }),
  listNexusEdges: (handle: MatrixHandle, limit = 100, offset = 0) =>
    call<TableRow[]>("list_nexus_edges", { handle, limit, offset }),
  listProfileConfig: (
    handle: MatrixHandle,
    prefix: string | null,
    limit = 100,
    offset = 0,
  ) => call<ProfileConfigRow[]>("list_profile_config", { handle, prefix, limit, offset }),
};
