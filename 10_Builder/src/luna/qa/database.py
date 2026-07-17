"""
QADatabase — SQLite storage for QA data.
=========================================

Tables:
- qa_reports: Full inference reports
- qa_assertion_results: Individual assertion results per report
- qa_assertions: Custom user-defined assertions
- qa_bugs: Known bugs for regression testing
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta
from typing import Optional
from contextlib import contextmanager
from pathlib import Path

from .assertions import Assertion, PatternConfig


class QADatabase:
    """SQLite storage for QA data."""

    def __init__(self, db_path: str = "data/qa.db"):
        self.db_path = db_path

        # Ensure directory exists
        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA busy_timeout=15000")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        """Create tables if they don't exist."""
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS qa_reports (
                    id TEXT PRIMARY KEY,
                    timestamp DATETIME,
                    session_id TEXT,
                    query TEXT,
                    route TEXT,
                    provider_used TEXT,
                    latency_ms REAL,
                    passed BOOLEAN,
                    failed_count INTEGER,
                    diagnosis TEXT,
                    context_json TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_reports_timestamp ON qa_reports(timestamp);
                CREATE INDEX IF NOT EXISTS idx_reports_passed ON qa_reports(passed);

                CREATE TABLE IF NOT EXISTS qa_assertion_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id TEXT REFERENCES qa_reports(id),
                    assertion_id TEXT,
                    assertion_name TEXT,
                    passed BOOLEAN,
                    severity TEXT,
                    expected TEXT,
                    actual TEXT,
                    details TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_results_report ON qa_assertion_results(report_id);
                CREATE INDEX IF NOT EXISTS idx_results_assertion ON qa_assertion_results(assertion_id);

                CREATE TABLE IF NOT EXISTS qa_assertions (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    category TEXT,
                    severity TEXT,
                    enabled BOOLEAN DEFAULT TRUE,
                    check_type TEXT,
                    pattern_config_json TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS qa_bugs (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    query TEXT,
                    context TEXT,
                    expected_behavior TEXT,
                    actual_behavior TEXT,
                    root_cause TEXT,
                    affected_assertions TEXT,
                    status TEXT DEFAULT 'open',
                    severity TEXT,
                    date_found DATETIME,
                    date_fixed DATETIME,
                    fixed_by TEXT,
                    last_test_passed BOOLEAN,
                    last_test_time DATETIME,
                    last_test_response TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_bugs_status ON qa_bugs(status);

                CREATE TABLE IF NOT EXISTS qa_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    source TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    component TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT,
                    resolved BOOLEAN DEFAULT FALSE
                );

                CREATE INDEX IF NOT EXISTS idx_events_timestamp ON qa_events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_events_source ON qa_events(source);
            """)

            # Migration: fix CUSTOM-CB2C01 inverted logic (regex → regex_not_match)
            self._migrate_surrender_assertion(conn)

    def _migrate_surrender_assertion(self, conn):
        """Fix CUSTOM-CB2C01 Knowledge Surrender assertion — regex should be regex_not_match."""
        row = conn.execute(
            "SELECT pattern_config_json FROM qa_assertions WHERE id = 'CUSTOM-CB2C01'"
        ).fetchone()
        if row and row["pattern_config_json"]:
            pc = json.loads(row["pattern_config_json"])
            if pc.get("match_type") == "regex":
                pc["match_type"] = "regex_not_match"
                conn.execute(
                    "UPDATE qa_assertions SET pattern_config_json = ? WHERE id = 'CUSTOM-CB2C01'",
                    (json.dumps(pc),)
                )

    # ═══════════════════════════════════════════════════════════
    # REPORTS
    # ═══════════════════════════════════════════════════════════

    def store_report(self, report) -> None:
        """Store a QA report."""
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO qa_reports
                (id, timestamp, session_id, query, route, provider_used,
                 latency_ms, passed, failed_count, diagnosis, context_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                report.inference_id,
                report.timestamp.isoformat(),
                report.context.session_id,
                report.query,
                report.route,
                report.provider_used,
                report.latency_ms,
                report.passed,
                report.failed_count,
                report.diagnosis,
                json.dumps(report.context.to_dict()),
            ))

            # Store assertion results
            for a in report.assertions:
                conn.execute("""
                    INSERT INTO qa_assertion_results
                    (report_id, assertion_id, assertion_name, passed, severity, expected, actual, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    report.inference_id,
                    a.id,
                    a.name,
                    a.passed,
                    a.severity,
                    a.expected,
                    a.actual,
                    a.details,
                ))

    def get_last_report(self) -> Optional[dict]:
        """Get most recent report."""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM qa_reports ORDER BY timestamp DESC LIMIT 1
            """).fetchone()

            if not row:
                return None

            return self._row_to_report(conn, row)

    def get_recent_reports(self, limit: int = 100) -> list:
        """Get recent reports."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM qa_reports ORDER BY timestamp DESC LIMIT ?
            """, (limit,)).fetchall()

            return [self._row_to_report(conn, row) for row in rows]

    def get_report_by_id(self, report_id: str) -> Optional[dict]:
        """Get a specific report by ID."""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM qa_reports WHERE id = ?
            """, (report_id,)).fetchone()

            if not row:
                return None

            return self._row_to_report(conn, row)

    def _row_to_report(self, conn, row) -> dict:
        """Convert DB row to report dict."""
        # Get assertion results
        assertions = conn.execute("""
            SELECT * FROM qa_assertion_results WHERE report_id = ?
        """, (row["id"],)).fetchall()

        return {
            "inference_id": row["id"],
            "timestamp": row["timestamp"],
            "session_id": row["session_id"],
            "query": row["query"],
            "route": row["route"],
            "provider_used": row["provider_used"],
            "latency_ms": row["latency_ms"],
            "passed": bool(row["passed"]),
            "failed_count": row["failed_count"],
            "diagnosis": row["diagnosis"],
            "context": json.loads(row["context_json"]) if row["context_json"] else {},
            "assertions": [
                {
                    "id": a["assertion_id"],
                    "name": a["assertion_name"],
                    "passed": bool(a["passed"]),
                    "severity": a["severity"],
                    "expected": a["expected"],
                    "actual": a["actual"],
                    "details": a["details"],
                }
                for a in assertions
            ],
        }

    # ═══════════════════════════════════════════════════════════
    # STATS
    # ═══════════════════════════════════════════════════════════

    def get_stats(self, time_range: str = "24h") -> dict:
        """Get aggregate statistics."""
        hours = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}.get(time_range, 24)
        since = datetime.now() - timedelta(hours=hours)

        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN passed THEN 1 ELSE 0 END) as passed,
                    SUM(CASE WHEN NOT passed THEN 1 ELSE 0 END) as failed
                FROM qa_reports
                WHERE timestamp > ?
            """, (since.isoformat(),)).fetchone()

            total = row["total"] or 0
            passed = row["passed"] or 0
            failed = row["failed"] or 0

            # Get most common failures
            failures = conn.execute("""
                SELECT assertion_id, assertion_name, COUNT(*) as count
                FROM qa_assertion_results ar
                JOIN qa_reports r ON ar.report_id = r.id
                WHERE r.timestamp > ? AND NOT ar.passed
                GROUP BY assertion_id, assertion_name
                ORDER BY count DESC
                LIMIT 5
            """, (since.isoformat(),)).fetchall()

            return {
                "total": total,
                "passed": passed,
                "failed": failed,
                "pass_rate": passed / total if total > 0 else 0,
                "time_range": time_range,
                "top_failures": [
                    {"id": f["assertion_id"], "name": f["assertion_name"], "count": f["count"]}
                    for f in failures
                ],
            }

    # ═══════════════════════════════════════════════════════════
    # ASSERTIONS
    # ═══════════════════════════════════════════════════════════

    def store_assertion(self, assertion: Assertion) -> None:
        """Store a custom assertion."""
        pattern_json = None
        if assertion.pattern_config:
            pattern_json = json.dumps({
                "target": assertion.pattern_config.target,
                "match_type": assertion.pattern_config.match_type,
                "pattern": assertion.pattern_config.pattern,
                "case_sensitive": assertion.pattern_config.case_sensitive,
            })

        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO qa_assertions
                (id, name, description, category, severity, enabled, check_type, pattern_config_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                assertion.id,
                assertion.name,
                assertion.description,
                assertion.category,
                assertion.severity,
                assertion.enabled,
                assertion.check_type,
                pattern_json,
            ))

    def get_assertions(self) -> list[Assertion]:
        """Get all custom assertions."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM qa_assertions").fetchall()

            assertions = []
            for row in rows:
                pattern_config = None
                if row["pattern_config_json"]:
                    pc = json.loads(row["pattern_config_json"])
                    pattern_config = PatternConfig(
                        target=pc["target"],
                        match_type=pc["match_type"],
                        pattern=pc["pattern"],
                        case_sensitive=pc.get("case_sensitive", False),
                    )

                assertions.append(Assertion(
                    id=row["id"],
                    name=row["name"],
                    description=row["description"] or "",
                    category=row["category"],
                    severity=row["severity"],
                    enabled=bool(row["enabled"]),
                    check_type=row["check_type"],
                    pattern_config=pattern_config,
                ))

            return assertions

    def update_assertion(self, assertion: Assertion) -> None:
        """Update an assertion."""
        self.store_assertion(assertion)

    def delete_assertion(self, assertion_id: str) -> None:
        """Delete an assertion."""
        with self._conn() as conn:
            conn.execute("DELETE FROM qa_assertions WHERE id = ?", (assertion_id,))

    # ═══════════════════════════════════════════════════════════
    # BUGS
    # ═══════════════════════════════════════════════════════════

    def store_bug(self, bug: dict) -> str:
        """Store a bug."""
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO qa_bugs
                (id, name, description, query, expected_behavior, actual_behavior,
                 root_cause, affected_assertions, status, severity, date_found)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bug["id"],
                bug["name"],
                bug.get("description", ""),
                bug["query"],
                bug["expected_behavior"],
                bug["actual_behavior"],
                bug.get("root_cause", ""),
                json.dumps(bug.get("affected_assertions", [])),
                bug.get("status", "open"),
                bug.get("severity", "high"),
                datetime.now().isoformat(),
            ))
            return bug["id"]

    def get_all_bugs(self) -> list[dict]:
        """Get all bugs."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM qa_bugs ORDER BY date_found DESC").fetchall()
            return [self._row_to_bug(row) for row in rows]

    def get_bugs_by_status(self, status: str) -> list[dict]:
        """Get bugs by status."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM qa_bugs WHERE status = ? ORDER BY date_found DESC",
                (status,)
            ).fetchall()
            return [self._row_to_bug(row) for row in rows]

    def get_bug_by_id(self, bug_id: str) -> Optional[dict]:
        """Get a specific bug by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM qa_bugs WHERE id = ?",
                (bug_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_bug(row)

    def _row_to_bug(self, row) -> dict:
        """Convert DB row to bug dict."""
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "query": row["query"],
            "expected_behavior": row["expected_behavior"],
            "actual_behavior": row["actual_behavior"],
            "root_cause": row["root_cause"],
            "affected_assertions": json.loads(row["affected_assertions"]) if row["affected_assertions"] else [],
            "status": row["status"],
            "severity": row["severity"],
            "date_found": row["date_found"],
            "date_fixed": row["date_fixed"],
            "fixed_by": row["fixed_by"],
            "last_test_passed": row["last_test_passed"],
            "last_test_time": row["last_test_time"],
            "last_test_response": row["last_test_response"],
        }

    def update_bug_status(self, bug_id: str, status: str) -> None:
        """Update bug status."""
        with self._conn() as conn:
            if status == "fixed":
                conn.execute(
                    "UPDATE qa_bugs SET status = ?, date_fixed = ? WHERE id = ?",
                    (status, datetime.now().isoformat(), bug_id)
                )
            else:
                conn.execute(
                    "UPDATE qa_bugs SET status = ? WHERE id = ?",
                    (status, bug_id)
                )

    def update_bug_test_result(self, bug_id: str, passed: bool, response: str) -> None:
        """Update last test result for a bug."""
        with self._conn() as conn:
            conn.execute("""
                UPDATE qa_bugs
                SET last_test_passed = ?, last_test_time = ?, last_test_response = ?
                WHERE id = ?
            """, (passed, datetime.now().isoformat(), response[:1000], bug_id))

    def count_failing_bugs(self) -> int:
        """Count bugs that are still failing."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as count FROM qa_bugs WHERE status IN ('open', 'failing')"
            ).fetchone()
            return row["count"]

    def generate_bug_id(self) -> str:
        """Generate next bug ID."""
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) as count FROM qa_bugs").fetchone()
            return f"BUG-{row['count'] + 1:03d}"

    # ═══════════════════════════════════════════════════════════
    # DIAGNOSTIC EVENTS
    # ═══════════════════════════════════════════════════════════

    def store_event(self, source: str, severity: str, component: str,
                    message: str, details: dict = None) -> int:
        """Store a diagnostic event. Returns the event ID."""
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO qa_events (source, severity, component, message, details_json)
                VALUES (?, ?, ?, ?, ?)
            """, (source, severity, component, message,
                  json.dumps(details) if details else None))
            return cursor.lastrowid

    def get_events(self, source: str = None, severity: str = None,
                   limit: int = 100) -> list[dict]:
        """Get diagnostic events, optionally filtered."""
        clauses, params = [], []
        if source:
            clauses.append("source = ?")
            params.append(source)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM qa_events {where} ORDER BY timestamp DESC LIMIT ?",
                params,
            ).fetchall()
            return [self._row_to_event(row) for row in rows]

    def get_event_summary(self, hours: int = 24) -> dict:
        """Get event counts grouped by source and severity."""
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT source, severity, COUNT(*) as count
                FROM qa_events WHERE timestamp > ?
                GROUP BY source, severity ORDER BY count DESC
            """, (since,)).fetchall()

            by_source: dict[str, int] = {}
            by_severity: dict[str, int] = {}
            total = 0
            for r in rows:
                by_source[r["source"]] = by_source.get(r["source"], 0) + r["count"]
                by_severity[r["severity"]] = by_severity.get(r["severity"], 0) + r["count"]
                total += r["count"]

            return {
                "total": total,
                "hours": hours,
                "by_source": by_source,
                "by_severity": by_severity,
            }

    def count_events(self, hours: int = 24) -> int:
        """Count events in the last N hours."""
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as count FROM qa_events WHERE timestamp > ?",
                (since,),
            ).fetchone()
            return row["count"]

    def _row_to_event(self, row) -> dict:
        """Convert DB row to event dict."""
        return {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "source": row["source"],
            "severity": row["severity"],
            "component": row["component"],
            "message": row["message"],
            "details": json.loads(row["details_json"]) if row["details_json"] else None,
            "resolved": bool(row["resolved"]),
        }
