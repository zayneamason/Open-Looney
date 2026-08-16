export type MatrixHandle = number;

export type LunmError =
  | { kind: "not_sqlite" }
  | { kind: "wrong_family"; actual_id: number; family_hint: string }
  | { kind: "unsupported_user_version"; actual: number }
  | { kind: "missing_format_invariant_table"; table: string }
  | { kind: "missing_format_invariant_column"; table: string; column: string }
  | { kind: "invalid_handle"; handle: number }
  | { kind: "sqlite_error"; message: string }
  | { kind: "io_error"; message: string };

export interface ProfileConfigRow {
  key: string;
  value: string;
  value_type: string;
  updated_at: string | null;
  updated_by: string | null;
  description: string | null;
}

export interface TableCount {
  table: string;
  count: number | null;
  present: boolean;
}

export interface LunmOverview {
  path: string;
  application_id: number;
  user_version: number;
  format_version: string | null;
  matrix_ulid: string | null;
  created_at: string | null;
  engine_version: string | null;
  header_rows: ProfileConfigRow[];
  table_counts: TableCount[];
}

export type HealthLevel = "ok" | "warning" | "error";

export interface HealthCheck {
  level: HealthLevel;
  code: string;
  message: string;
}

export interface LunmHealthReport {
  checks: HealthCheck[];
  error_count: number;
  warning_count: number;
}

export interface MemoryNodeFilters {
  nodeType: string | null;
  classification: string | null;
}

export interface TableRow {
  values: Record<string, unknown>;
}

export type Tab =
  | "overview"
  | "health"
  | "memory"
  | "graph"
  | "conversations"
  | "nexus"
  | "config";
