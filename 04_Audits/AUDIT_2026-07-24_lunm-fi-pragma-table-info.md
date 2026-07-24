# AUDIT: LUNM format-invariant PRAGMA table_info

**Date:** 2026-07-24
**Engine:** `c5c451fa` (main, post SPEC-010 PR #158)
**Matrix:** `data/user/memory_matrix.lun` (read-only)
**Purpose:** Evidence for SPEC-011 draft — live columns vs schema.sql parity.

---

# Live PRAGMA table_info (memory_matrix.lun, read-only)
## memory_nodes (24 cols)
| cid | name | type | notnull | dflt | pk |
| --- | --- | --- | --- | --- | --- |
| 0 | `id` | TEXT | 0 | None | 1 |
| 1 | `node_type` | TEXT | 1 | None | 0 |
| 2 | `content` | TEXT | 1 | None | 0 |
| 3 | `summary` | TEXT | 0 | None | 0 |
| 4 | `source` | TEXT | 0 | None | 0 |
| 5 | `confidence` | REAL | 0 | '1.0' | 0 |
| 6 | `importance` | REAL | 0 | '0.5' | 0 |
| 7 | `access_count` | INTEGER | 0 | '0' | 0 |
| 8 | `reinforcement_count` | INTEGER | 0 | '0' | 0 |
| 9 | `lock_in` | REAL | 0 | '0.15' | 0 |
| 10 | `lock_in_state` | TEXT | 0 | "'drifting'" | 0 |
| 11 | `last_accessed` | TEXT | 0 | None | 0 |
| 12 | `created_at` | TEXT | 1 | "datetime('now')" | 0 |
| 13 | `updated_at` | TEXT | 1 | "datetime('now')" | 0 |
| 14 | `metadata` | TEXT | 0 | None | 0 |
| 15 | `scope` | TEXT | 1 | "'global'" | 0 |
| 16 | `classification` | TEXT | 0 | "'public'" | 0 |
| 17 | `custodian` | TEXT | 0 | None | 0 |
| 18 | `access_roles` | TEXT | 0 | None | 0 |
| 19 | `consent_status` | TEXT | 0 | "'granted'" | 0 |
| 20 | `consent_date` | TEXT | 0 | None | 0 |
| 21 | `review_status` | TEXT | 0 | "'current'" | 0 |
| 22 | `date_shared` | TEXT | 0 | None | 0 |
| 23 | `namespace` | TEXT | 1 | "'active'" | 0 |

## graph_edges (9 cols)
| cid | name | type | notnull | dflt | pk |
| --- | --- | --- | --- | --- | --- |
| 0 | `id` | INTEGER | 0 | None | 1 |
| 1 | `from_id` | TEXT | 1 | None | 0 |
| 2 | `to_id` | TEXT | 1 | None | 0 |
| 3 | `relationship` | TEXT | 1 | None | 0 |
| 4 | `strength` | REAL | 0 | '1.0' | 0 |
| 5 | `created_at` | TEXT | 1 | "datetime('now')" | 0 |
| 6 | `metadata` | TEXT | 0 | None | 0 |
| 7 | `scope` | TEXT | 1 | "'global'" | 0 |
| 8 | `origin` | TEXT | 1 | "'user'" | 0 |

## conversation_turns (14 cols)
| cid | name | type | notnull | dflt | pk |
| --- | --- | --- | --- | --- | --- |
| 0 | `id` | INTEGER | 0 | None | 1 |
| 1 | `session_id` | TEXT | 1 | None | 0 |
| 2 | `role` | TEXT | 1 | None | 0 |
| 3 | `content` | TEXT | 1 | None | 0 |
| 4 | `tokens` | INTEGER | 0 | None | 0 |
| 5 | `created_at` | TEXT | 1 | "datetime('now')" | 0 |
| 6 | `metadata` | TEXT | 0 | None | 0 |
| 7 | `turn_type` | TEXT | 1 | "'NORMAL_USER_TURN'" | 0 |
| 8 | `tier` | TEXT | 0 | "'active'" | 0 |
| 9 | `compressed` | TEXT | 0 | None | 0 |
| 10 | `compressed_at` | REAL | 0 | None | 0 |
| 11 | `archived_at` | REAL | 0 | None | 0 |
| 12 | `context_refs` | TEXT | 0 | None | 0 |
| 13 | `thread_id` | TEXT | 0 | None | 0 |

## sessions (6 cols)
| cid | name | type | notnull | dflt | pk |
| --- | --- | --- | --- | --- | --- |
| 0 | `session_id` | TEXT | 0 | None | 1 |
| 1 | `started_at` | REAL | 1 | None | 0 |
| 2 | `ended_at` | REAL | 0 | None | 0 |
| 3 | `app_context` | TEXT | 0 | None | 0 |
| 4 | `turns_count` | INTEGER | 0 | '0' | 0 |
| 5 | `metadata` | TEXT | 0 | None | 0 |

## nexus_nodes (5 cols)
| cid | name | type | notnull | dflt | pk |
| --- | --- | --- | --- | --- | --- |
| 0 | `nexus_node_id` | TEXT | 0 | None | 1 |
| 1 | `collection_key` | TEXT | 1 | None | 0 |
| 2 | `satellite_node_id` | TEXT | 1 | None | 0 |
| 3 | `node_type` | TEXT | 1 | None | 0 |
| 4 | `promoted_at` | TIMESTAMP | 0 | 'CURRENT_TIMESTAMP' | 0 |

## nexus_edges (4 cols)
| cid | name | type | notnull | dflt | pk |
| --- | --- | --- | --- | --- | --- |
| 0 | `src_node_id` | TEXT | 1 | None | 0 |
| 1 | `dst_node_id` | TEXT | 1 | None | 0 |
| 2 | `edge_type` | TEXT | 1 | None | 0 |
| 3 | `weight` | REAL | 0 | '1.0' | 0 |

## nexus_registry (16 cols)
| cid | name | type | notnull | dflt | pk |
| --- | --- | --- | --- | --- | --- |
| 0 | `collection_key` | TEXT | 0 | None | 1 |
| 1 | `lun_path` | TEXT | 1 | None | 0 |
| 2 | `ingestion_pattern` | TEXT | 1 | None | 0 |
| 3 | `lock_in` | REAL | 0 | '0.15' | 0 |
| 4 | `access_count` | INTEGER | 0 | '0' | 0 |
| 5 | `annotation_count` | INTEGER | 0 | '0' | 0 |
| 6 | `enabled` | BOOLEAN | 0 | 'TRUE' | 0 |
| 7 | `created_at` | TIMESTAMP | 0 | 'CURRENT_TIMESTAMP' | 0 |
| 8 | `updated_at` | TIMESTAMP | 0 | 'CURRENT_TIMESTAMP' | 0 |
| 9 | `mounted` | INTEGER | 1 | '0' | 0 |
| 10 | `discovered_at` | TEXT | 0 | None | 0 |
| 11 | `validation_status` | TEXT | 0 | None | 0 |
| 12 | `validation_reason` | TEXT | 0 | None | 0 |
| 13 | `family` | TEXT | 0 | None | 0 |
| 14 | `user_version` | INTEGER | 0 | None | 0 |
| 15 | `source` | TEXT | 1 | "'yaml'" | 0 |

## profile_config (6 cols)
| cid | name | type | notnull | dflt | pk |
| --- | --- | --- | --- | --- | --- |
| 0 | `key` | TEXT | 0 | None | 1 |
| 1 | `value` | TEXT | 1 | None | 0 |
| 2 | `value_type` | TEXT | 1 | "'string'" | 0 |
| 3 | `updated_at` | TEXT | 1 | "datetime('now')" | 0 |
| 4 | `updated_by` | TEXT | 0 | None | 0 |
| 5 | `description` | TEXT | 0 | None | 0 |

# Indexes (sqlite_master)
## memory_nodes
- `idx_memory_nodes_namespace`: `CREATE INDEX idx_memory_nodes_namespace ON memory_nodes(namespace)`
- `idx_nodes_accessed`: `CREATE INDEX idx_nodes_accessed ON memory_nodes(last_accessed DESC)`
- `idx_nodes_classification`: `CREATE INDEX idx_nodes_classification ON memory_nodes(classification)`
- `idx_nodes_created`: `CREATE INDEX idx_nodes_created ON memory_nodes(created_at)`
- `idx_nodes_custodian`: `CREATE INDEX idx_nodes_custodian ON memory_nodes(custodian)`
- `idx_nodes_importance`: `CREATE INDEX idx_nodes_importance ON memory_nodes(importance DESC)`
- `idx_nodes_lock_in`: `CREATE INDEX idx_nodes_lock_in ON memory_nodes(lock_in DESC)`
- `idx_nodes_lock_in_state`: `CREATE INDEX idx_nodes_lock_in_state ON memory_nodes(lock_in_state)`
- `idx_nodes_scope`: `CREATE INDEX idx_nodes_scope ON memory_nodes(scope)`
- `idx_nodes_scope_type`: `CREATE INDEX idx_nodes_scope_type ON memory_nodes(scope, node_type)`
- `idx_nodes_type`: `CREATE INDEX idx_nodes_type ON memory_nodes(node_type)`

## graph_edges
- `idx_edges_from`: `CREATE INDEX idx_edges_from ON graph_edges(from_id)`
- `idx_edges_origin`: `CREATE INDEX idx_edges_origin ON graph_edges(origin)`
- `idx_edges_relationship`: `CREATE INDEX idx_edges_relationship ON graph_edges(relationship)`
- `idx_edges_scope`: `CREATE INDEX idx_edges_scope ON graph_edges(scope)`
- `idx_edges_to`: `CREATE INDEX idx_edges_to ON graph_edges(to_id)`

## conversation_turns
- `idx_turns_created`: `CREATE INDEX idx_turns_created ON conversation_turns(created_at)`
- `idx_turns_session`: `CREATE INDEX idx_turns_session ON conversation_turns(session_id)`
- `idx_turns_session_tier`: `CREATE INDEX idx_turns_session_tier ON conversation_turns(session_id, tier)`
- `idx_turns_thread_id`: `CREATE INDEX idx_turns_thread_id ON conversation_turns(thread_id)`
- `idx_turns_tier_timestamp`: `CREATE INDEX idx_turns_tier_timestamp ON conversation_turns(tier, created_at DESC)`
- `idx_turns_turn_type`: `CREATE INDEX idx_turns_turn_type ON conversation_turns(turn_type)`

## sessions
- `idx_sessions_started`: `CREATE INDEX idx_sessions_started ON sessions(started_at DESC)`

## nexus_nodes
- `idx_nexus_nodes_collection`: `CREATE INDEX idx_nexus_nodes_collection ON nexus_nodes(collection_key)`

## nexus_edges
- `idx_nexus_edges_dst`: `CREATE INDEX idx_nexus_edges_dst ON nexus_edges(dst_node_id)`
- `idx_nexus_edges_src`: `CREATE INDEX idx_nexus_edges_src ON nexus_edges(src_node_id)`

## nexus_registry

## profile_config
- `idx_profile_config_updated_at`: `CREATE INDEX idx_profile_config_updated_at ON profile_config(updated_at DESC)`

