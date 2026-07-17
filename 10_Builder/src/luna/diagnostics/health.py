"""Health checking system for Luna Engine components."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum
from pathlib import Path
import time
import sqlite3
import json

from luna.core.paths import data_dir, user_dir, memory_matrix_path

# Suppress the db_stub alert to at most one emission per hour (per process).
# Without this, the watchdog fires an identical "stray stub" DEGRADED event
# on every health-check cycle (default: every 60 s), flooding the events log.
_STUB_WARN_COOLDOWN_SECONDS: float = 3600.0
_stub_last_warned: Dict[str, float] = {}

# Orphan filenames to watch for at data_dir() (sibling of data/user/).
# Legacy `luna_engine.db` predates the Step-2 rename — bootstrap_db.py with
# stale args could still create one. New `memory_matrix.lun` would only land
# here from a misdirected `mv` during migration.
_ORPHAN_FILENAMES = ("luna_engine.db", "memory_matrix.lun")


class HealthStatus(Enum):
    """Health status levels."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BROKEN = "broken"
    UNKNOWN = "unknown"


@dataclass
class HealthCheck:
    """Result of a health check."""
    component: str
    status: HealthStatus
    message: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class HealthChecker:
    """
    Continuous health monitoring for Luna components.

    Checks:
    - database: Connection and schema integrity
    - extraction: Node creation activity
    - retrieval: Search performance
    - memory_matrix: Node type distribution
    - sessions: Session recording status
    - entities: Entity/profile system health
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize health checker.

        Args:
            db_path: Path to SQLite database. Defaults to the active profile's
                memory_matrix_path() — resolved at instance creation, NOT at
                class definition. Each HealthChecker instance picks up the
                current profile contextvar.
        """
        if db_path is None:
            self.db_path = str(memory_matrix_path())
        else:
            self.db_path = db_path

    def check_all(self) -> List[HealthCheck]:
        """Run all health checks."""
        return [
            self.check_database(),
            self.check_db_stub(),
            self.check_extraction(),
            self.check_retrieval(),
            self.check_memory_matrix(),
            self.check_sessions(),
            self.check_entities()
        ]

    def check_db_stub(self) -> HealthCheck:
        """Detect a stray DB file at data/<name> (sibling of data/user/).

        Watches both `luna_engine.db` (legacy, pre-Step-2) and `memory_matrix.lun`
        (current canonical name). The canonical DB lives at data/user/memory_matrix.lun;
        a file at data/<either name> is never read by the engine but will fool
        `ls data/` and naive status checks into thinking Luna is empty.

        Per-path alert suppression at most one emission per hour (process-local).
        """
        global _stub_last_warned
        now = time.time()
        stub_results: List[HealthCheck] = []

        for fname in _ORPHAN_FILENAMES:
            stub_path = data_dir() / fname
            if not stub_path.exists():
                # Reset cooldown so it fires again if it reappears.
                _stub_last_warned.pop(fname, None)
                continue

            size_bytes = stub_path.stat().st_size
            table_count = 0
            try:
                conn = sqlite3.connect(str(stub_path))
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
                table_count = cursor.fetchone()[0]
                conn.close()
            except sqlite3.Error:
                pass

            is_empty_stub = size_bytes < 100 * 1024 and table_count == 0

            if now - _stub_last_warned.get(fname, 0.0) < _STUB_WARN_COOLDOWN_SECONDS:
                stub_results.append(HealthCheck(
                    component="db_stub",
                    status=HealthStatus.HEALTHY,
                    message=f"DB stub at {fname} present (alert suppressed — already reported this hour)",
                    metrics={
                        'stub_path': str(stub_path),
                        'size_bytes': size_bytes,
                        'table_count': table_count,
                        'suppressed': True,
                    }
                ))
                continue

            _stub_last_warned[fname] = now

            if is_empty_stub:
                stub_results.append(HealthCheck(
                    component="db_stub",
                    status=HealthStatus.DEGRADED,
                    message=(
                        f"Stray empty DB stub detected at {stub_path} — not used by engine "
                        f"but will confuse status checks. Safe to delete."
                    ),
                    metrics={
                        'stub_path': str(stub_path),
                        'size_bytes': size_bytes,
                        'table_count': table_count,
                    }
                ))
            else:
                stub_results.append(HealthCheck(
                    component="db_stub",
                    status=HealthStatus.DEGRADED,
                    message=(
                        f"Unexpected DB file at {stub_path} ({size_bytes} bytes, "
                        f"{table_count} tables) — canonical DB is data/user/memory_matrix.lun. "
                        f"Investigate before deleting."
                    ),
                    metrics={
                        'stub_path': str(stub_path),
                        'size_bytes': size_bytes,
                        'table_count': table_count,
                    }
                ))

        if not stub_results:
            return HealthCheck(
                component="db_stub",
                status=HealthStatus.HEALTHY,
                message="No stray DB stubs present",
                metrics={'watched': list(_ORPHAN_FILENAMES), 'exists': False}
            )

        # Prefer DEGRADED over HEALTHY when consolidating multiple results.
        degraded = [r for r in stub_results if r.status != HealthStatus.HEALTHY]
        return degraded[0] if degraded else stub_results[0]

    def check_database(self) -> HealthCheck:
        """Check database connectivity and integrity."""
        try:
            if not Path(self.db_path).exists():
                return HealthCheck(
                    component="database",
                    status=HealthStatus.BROKEN,
                    message=f"Database not found: {self.db_path}",
                    metrics={'path': self.db_path, 'exists': False}
                )

            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA busy_timeout=15000")
            cursor = conn.cursor()

            # Check tables exist
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            required_tables = ['memory_nodes', 'graph_edges', 'entities', 'conversation_turns']
            missing = [t for t in required_tables if t not in tables]

            # Check row counts
            cursor.execute("SELECT COUNT(*) FROM memory_nodes")
            node_count = cursor.fetchone()[0]

            # Check database size
            db_size = Path(self.db_path).stat().st_size

            conn.close()

            if missing:
                return HealthCheck(
                    component="database",
                    status=HealthStatus.BROKEN,
                    message=f"Missing tables: {missing}",
                    metrics={
                        'node_count': node_count,
                        'missing_tables': missing,
                        'tables_found': len(tables),
                        'db_size_mb': db_size / 1024 / 1024
                    }
                )

            return HealthCheck(
                component="database",
                status=HealthStatus.HEALTHY,
                message=f"Database operational: {node_count:,} nodes, {len(tables)} tables",
                metrics={
                    'node_count': node_count,
                    'tables': len(tables),
                    'db_size_mb': round(db_size / 1024 / 1024, 2)
                }
            )

        except Exception as e:
            return HealthCheck(
                component="database",
                status=HealthStatus.BROKEN,
                message=f"Database error: {e}",
                metrics={'error': str(e)}
            )

    def check_extraction(self) -> HealthCheck:
        """Check if extraction is creating nodes."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA busy_timeout=15000")
            cursor = conn.cursor()

            # Check nodes created in last hour
            cursor.execute("""
                SELECT COUNT(*) FROM memory_nodes
                WHERE datetime(created_at) > datetime('now', '-1 hour')
            """)
            recent_nodes = cursor.fetchone()[0]

            # Check nodes created today
            cursor.execute("""
                SELECT COUNT(*) FROM memory_nodes
                WHERE date(created_at) = date('now')
            """)
            today_nodes = cursor.fetchone()[0]

            # Check total nodes
            cursor.execute("SELECT COUNT(*) FROM memory_nodes")
            total_nodes = cursor.fetchone()[0]

            # Check extraction queue
            cursor.execute("""
                SELECT COUNT(*) FROM extraction_queue
                WHERE status = 'pending'
            """)
            pending_extractions = cursor.fetchone()[0]

            conn.close()

            # Determine status
            if total_nodes == 0:
                status = HealthStatus.BROKEN
                message = "No nodes in database - extraction may not be running"
            elif today_nodes > 0:
                status = HealthStatus.HEALTHY
                message = f"Extraction active: {today_nodes} nodes created today"
            elif recent_nodes == 0 and pending_extractions > 0:
                status = HealthStatus.DEGRADED
                message = f"Pending extractions ({pending_extractions}) but no recent nodes"
            else:
                status = HealthStatus.UNKNOWN
                message = f"No recent activity. Total nodes: {total_nodes:,}"

            return HealthCheck(
                component="extraction",
                status=status,
                message=message,
                metrics={
                    'recent_nodes_1h': recent_nodes,
                    'today_nodes': today_nodes,
                    'total_nodes': total_nodes,
                    'pending_extractions': pending_extractions
                }
            )

        except Exception as e:
            return HealthCheck(
                component="extraction",
                status=HealthStatus.UNKNOWN,
                message=f"Check failed: {e}",
                metrics={'error': str(e)}
            )

    def check_retrieval(self) -> HealthCheck:
        """Check if retrieval is working."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA busy_timeout=15000")
            cursor = conn.cursor()

            # Test basic query performance
            start = time.time()
            cursor.execute("""
                SELECT id, content FROM memory_nodes
                WHERE content LIKE '%test%'
                LIMIT 5
            """)
            _ = cursor.fetchall()
            basic_query_time = (time.time() - start) * 1000

            # Check FTS tables exist
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name LIKE '%fts%'
            """)
            fts_tables = [row[0] for row in cursor.fetchall()]

            # Check if vector embeddings table exists
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name LIKE '%embedding%'
            """)
            embedding_tables = [row[0] for row in cursor.fetchall()]

            # Check index usage
            cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'")
            index_count = cursor.fetchone()[0]

            conn.close()

            if basic_query_time > 500:
                status = HealthStatus.DEGRADED
                message = f"Slow retrieval: {basic_query_time:.0f}ms for basic query"
            elif not embedding_tables:
                status = HealthStatus.DEGRADED
                message = "Vector embeddings table not found - semantic search unavailable"
            else:
                status = HealthStatus.HEALTHY
                message = f"Retrieval operational: {basic_query_time:.1f}ms query time"

            return HealthCheck(
                component="retrieval",
                status=status,
                message=message,
                metrics={
                    'basic_query_ms': round(basic_query_time, 2),
                    'fts_tables': len(fts_tables),
                    'embedding_tables': len(embedding_tables),
                    'index_count': index_count
                }
            )

        except Exception as e:
            return HealthCheck(
                component="retrieval",
                status=HealthStatus.BROKEN,
                message=f"Retrieval check failed: {e}",
                metrics={'error': str(e)}
            )

    def check_memory_matrix(self) -> HealthCheck:
        """Check Memory Matrix state and node distribution."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA busy_timeout=15000")
            cursor = conn.cursor()

            # Node type distribution
            cursor.execute("""
                SELECT node_type, COUNT(*)
                FROM memory_nodes
                GROUP BY node_type
            """)
            type_dist = dict(cursor.fetchall())

            # Total nodes
            cursor.execute("SELECT COUNT(*) FROM memory_nodes")
            total_nodes = cursor.fetchone()[0]

            # Lock-in state distribution
            cursor.execute("""
                SELECT lock_in_state, COUNT(*)
                FROM memory_nodes
                GROUP BY lock_in_state
            """)
            lock_in_dist = dict(cursor.fetchall())

            # Average lock-in
            cursor.execute("SELECT AVG(lock_in) FROM memory_nodes")
            avg_lock_in = cursor.fetchone()[0] or 0

            # Edge count
            cursor.execute("SELECT COUNT(*) FROM graph_edges")
            edge_count = cursor.fetchone()[0]

            conn.close()

            if total_nodes == 0:
                status = HealthStatus.UNKNOWN
                message = "Memory Matrix is empty"
            elif len(type_dist) < 2:
                status = HealthStatus.DEGRADED
                message = f"Low diversity: only {len(type_dist)} node type(s)"
            else:
                status = HealthStatus.HEALTHY
                message = f"Memory Matrix healthy: {total_nodes:,} nodes, {len(type_dist)} types"

            return HealthCheck(
                component="memory_matrix",
                status=status,
                message=message,
                metrics={
                    'total_nodes': total_nodes,
                    'node_types': type_dist,
                    'lock_in_states': lock_in_dist,
                    'avg_lock_in': round(avg_lock_in, 3),
                    'edge_count': edge_count
                }
            )

        except Exception as e:
            return HealthCheck(
                component="memory_matrix",
                status=HealthStatus.UNKNOWN,
                message=f"Check failed: {e}",
                metrics={'error': str(e)}
            )

    def check_sessions(self) -> HealthCheck:
        """Check session recording."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA busy_timeout=15000")
            cursor = conn.cursor()

            # Total sessions
            cursor.execute("SELECT COUNT(*) FROM sessions")
            total_sessions = cursor.fetchone()[0]

            # Recent sessions (last 24h)
            cursor.execute("""
                SELECT COUNT(*) FROM sessions
                WHERE started_at > strftime('%s', 'now', '-1 day')
            """)
            recent_sessions = cursor.fetchone()[0]

            # Total conversation turns
            cursor.execute("SELECT COUNT(*) FROM conversation_turns")
            total_turns = cursor.fetchone()[0]

            # Recent turns
            cursor.execute("""
                SELECT COUNT(*) FROM conversation_turns
                WHERE datetime(created_at) > datetime('now', '-1 hour')
            """)
            recent_turns = cursor.fetchone()[0]

            # Compression queue status
            cursor.execute("""
                SELECT status, COUNT(*) FROM compression_queue GROUP BY status
            """)
            compression_status = dict(cursor.fetchall())

            conn.close()

            if total_sessions == 0:
                status = HealthStatus.UNKNOWN
                message = "No sessions recorded yet"
            elif total_turns == 0:
                status = HealthStatus.DEGRADED
                message = f"{total_sessions} sessions but no conversation turns"
            else:
                status = HealthStatus.HEALTHY
                message = f"Sessions active: {total_sessions} sessions, {total_turns:,} turns"

            return HealthCheck(
                component="sessions",
                status=status,
                message=message,
                metrics={
                    'total_sessions': total_sessions,
                    'recent_sessions_24h': recent_sessions,
                    'total_turns': total_turns,
                    'recent_turns_1h': recent_turns,
                    'compression_queue': compression_status
                }
            )

        except Exception as e:
            return HealthCheck(
                component="sessions",
                status=HealthStatus.UNKNOWN,
                message=f"Check failed: {e}",
                metrics={'error': str(e)}
            )

    def check_entities(self) -> HealthCheck:
        """Check entity/profile system health."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA busy_timeout=15000")
            cursor = conn.cursor()

            # Count entities by type
            cursor.execute("""
                SELECT entity_type, COUNT(*)
                FROM entities
                GROUP BY entity_type
            """)
            entity_dist = dict(cursor.fetchall())

            total_entities = sum(entity_dist.values())
            person_count = entity_dist.get('person', 0)

            # Check entity mentions
            cursor.execute("SELECT COUNT(*) FROM entity_mentions")
            mention_count = cursor.fetchone()[0]

            # Check entity relationships
            cursor.execute("SELECT COUNT(*) FROM entity_relationships")
            relationship_count = cursor.fetchone()[0]

            # Recent entity updates
            cursor.execute("""
                SELECT COUNT(*) FROM entities
                WHERE datetime(updated_at) > datetime('now', '-24 hours')
            """)
            recent_updates = cursor.fetchone()[0]

            conn.close()

            if total_entities == 0:
                status = HealthStatus.BROKEN
                message = "No entities in database - profile system not working"
            elif person_count == 0:
                status = HealthStatus.DEGRADED
                message = f"{total_entities} entities but no 'person' type - people not being extracted"
            elif mention_count == 0:
                status = HealthStatus.DEGRADED
                message = f"{total_entities} entities but no mentions - entities not linked to memories"
            else:
                status = HealthStatus.HEALTHY
                message = f"Entity system healthy: {total_entities} entities, {person_count} people"

            return HealthCheck(
                component="entities",
                status=status,
                message=message,
                metrics={
                    'total_entities': total_entities,
                    'entity_types': entity_dist,
                    'person_count': person_count,
                    'mention_count': mention_count,
                    'relationship_count': relationship_count,
                    'recent_updates_24h': recent_updates
                }
            )

        except Exception as e:
            return HealthCheck(
                component="entities",
                status=HealthStatus.UNKNOWN,
                message=f"Check failed: {e}",
                metrics={'error': str(e)}
            )

    def find_person(self, name: str) -> Dict[str, Any]:
        """
        Diagnose why a person might not be found.

        Returns detailed search results and suggestions.
        """
        results = {
            'name': name,
            'found': False,
            'search_results': {},
            'diagnosis': [],
            'suggestions': []
        }

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA busy_timeout=15000")
            cursor = conn.cursor()

            # 1. Direct entity lookup
            cursor.execute("""
                SELECT id, entity_type, name, aliases, core_facts
                FROM entities
                WHERE LOWER(name) LIKE ?
                   OR LOWER(id) LIKE ?
                   OR aliases LIKE ?
            """, (f'%{name.lower()}%', f'%{name.lower()}%', f'%{name}%'))

            entities = cursor.fetchall()
            if entities:
                results['found'] = True
                results['search_results']['entities'] = [
                    {
                        'id': e[0],
                        'type': e[1],
                        'name': e[2],
                        'aliases': json.loads(e[3]) if e[3] else [],
                        'core_facts': e[4][:200] + '...' if e[4] and len(e[4]) > 200 else e[4]
                    }
                    for e in entities
                ]

            # 2. Memory nodes content search
            cursor.execute("""
                SELECT id, node_type, content, lock_in
                FROM memory_nodes
                WHERE LOWER(content) LIKE ?
                ORDER BY lock_in DESC
                LIMIT 10
            """, (f'%{name.lower()}%',))

            nodes = cursor.fetchall()
            if nodes:
                results['search_results']['memory_nodes'] = [
                    {
                        'id': n[0],
                        'type': n[1],
                        'content_preview': n[2][:150] + '...' if len(n[2]) > 150 else n[2],
                        'lock_in': n[3]
                    }
                    for n in nodes
                ]
                if not results['found']:
                    results['found'] = True

            # 3. Entity mentions search
            cursor.execute("""
                SELECT em.entity_id, e.name, em.mention_type, em.context_snippet
                FROM entity_mentions em
                JOIN entities e ON em.entity_id = e.id
                WHERE em.context_snippet LIKE ?
                LIMIT 5
            """, (f'%{name}%',))

            mentions = cursor.fetchall()
            if mentions:
                results['search_results']['mentions'] = [
                    {
                        'entity_id': m[0],
                        'entity_name': m[1],
                        'mention_type': m[2],
                        'context': m[3]
                    }
                    for m in mentions
                ]

            conn.close()

            # Generate diagnosis
            if not results['found']:
                results['diagnosis'].append(f"'{name}' not found in any table")

                # Check overall entity count
                entity_check = self.check_entities()
                if entity_check.metrics.get('person_count', 0) == 0:
                    results['diagnosis'].append("No 'person' entities exist - extraction may not be creating people")
                    results['suggestions'].append("Check if Scribe actor is running and extracting entities")

                results['suggestions'].append(f"Try searching with variations of '{name}'")
                results['suggestions'].append("Check conversation history for original mention")
            else:
                if 'entities' in results['search_results']:
                    results['diagnosis'].append(f"Found in entities table")
                if 'memory_nodes' in results['search_results']:
                    results['diagnosis'].append(f"Found in {len(results['search_results']['memory_nodes'])} memory nodes")
                if 'mentions' not in results['search_results']:
                    results['diagnosis'].append("No entity mentions found - entity may not be linked to memories")
                    results['suggestions'].append("Entity exists but may not be connected to conversation history")

        except Exception as e:
            results['error'] = str(e)
            results['diagnosis'].append(f"Search failed: {e}")

        return results

    def get_recent_activity(self, hours: int = 24) -> Dict[str, Any]:
        """Get summary of recent database activity."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA busy_timeout=15000")
            cursor = conn.cursor()

            activity = {
                'period_hours': hours,
                'nodes_created': 0,
                'turns_added': 0,
                'entities_updated': 0,
                'recent_node_types': {},
                'recent_sessions': []
            }

            # Nodes created
            cursor.execute(f"""
                SELECT COUNT(*) FROM memory_nodes
                WHERE datetime(created_at) > datetime('now', '-{hours} hours')
            """)
            activity['nodes_created'] = cursor.fetchone()[0]

            # Node types created
            cursor.execute(f"""
                SELECT node_type, COUNT(*) FROM memory_nodes
                WHERE datetime(created_at) > datetime('now', '-{hours} hours')
                GROUP BY node_type
            """)
            activity['recent_node_types'] = dict(cursor.fetchall())

            # Turns added
            cursor.execute(f"""
                SELECT COUNT(*) FROM conversation_turns
                WHERE datetime(created_at) > datetime('now', '-{hours} hours')
            """)
            activity['turns_added'] = cursor.fetchone()[0]

            # Entities updated
            cursor.execute(f"""
                SELECT COUNT(*) FROM entities
                WHERE datetime(updated_at) > datetime('now', '-{hours} hours')
            """)
            activity['entities_updated'] = cursor.fetchone()[0]

            # Recent sessions
            cursor.execute(f"""
                SELECT session_id, turns_count, started_at
                FROM sessions
                ORDER BY started_at DESC
                LIMIT 5
            """)
            activity['recent_sessions'] = [
                {'session_id': r[0], 'turns': r[1], 'started': r[2]}
                for r in cursor.fetchall()
            ]

            conn.close()
            return activity

        except Exception as e:
            return {'error': str(e)}
