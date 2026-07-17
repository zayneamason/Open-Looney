"""
MCP Tools — Luna's self-diagnosis interface.
=============================================

These tools allow Luna to:
1. Check her own QA status
2. Search past reports
3. Add custom assertions
4. Track bugs
5. Run regression tests

Usage:
    # In MCP server setup
    from luna.qa.mcp_tools import get_qa_tools

    for name, fn in get_qa_tools():
        register_tool(name, fn)
"""

import logging
import uuid
from typing import Optional

from .validator import QAValidator, get_validator
from .assertions import Assertion, PatternConfig

logger = logging.getLogger(__name__)


def _get_validator() -> QAValidator:
    """Get the global QA validator instance."""
    return get_validator()


# ═══════════════════════════════════════════════════════════
# READ TOOLS
# ═══════════════════════════════════════════════════════════

def qa_get_last_report() -> dict:
    """
    Get the QA report for Luna's most recent inference.

    Returns assertion results, diagnosis, and debugging info.
    Use this to understand why your last response may have failed QA.
    """
    v = _get_validator()
    report = v.get_last_report()
    if not report:
        return {"error": "No reports yet"}
    return report.to_dict()


def qa_get_health() -> dict:
    """
    Get Luna's current QA health status.

    Returns:
        pass_rate: float (0-1)
        recent_failures: list of failed assertion names
        failing_bugs: count of known bugs still failing
    """
    return _get_validator().get_health()


def qa_search_reports(
    query: str = None,
    route: str = None,
    passed: bool = None,
    limit: int = 10
) -> list[dict]:
    """
    Search QA report history.

    Args:
        query: Filter by query text
        route: Filter by route (LOCAL_ONLY, DELEGATION_DETECTION, FULL_DELEGATION)
        passed: Filter by pass/fail status
        limit: Max results
    """
    v = _get_validator()
    reports = v._db.get_recent_reports(limit * 3)  # Get extra for filtering

    results = []
    for r in reports:
        if query and query.lower() not in r.get("query", "").lower():
            continue
        if route and r.get("route") != route:
            continue
        if passed is not None and r.get("passed") != passed:
            continue
        results.append(r)
        if len(results) >= limit:
            break

    return results


def qa_get_stats(time_range: str = "24h") -> dict:
    """
    Get QA statistics.

    Args:
        time_range: "1h", "24h", "7d", "30d"
    """
    return _get_validator()._db.get_stats(time_range)


def qa_get_assertion_list() -> list[dict]:
    """Get all configured assertions."""
    return [
        {
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "category": a.category,
            "severity": a.severity,
            "enabled": a.enabled,
            "check_type": a.check_type,
        }
        for a in _get_validator().get_assertions()
    ]


# ═══════════════════════════════════════════════════════════
# WRITE TOOLS
# ═══════════════════════════════════════════════════════════

def qa_add_assertion(
    name: str,
    category: str,
    severity: str,
    target: str,
    condition: str,
    pattern: str,
    case_sensitive: bool = False
) -> dict:
    """
    Add a new pattern-based assertion.

    Args:
        name: Human-readable name
        category: structural, voice, personality, flow
        severity: critical, high, medium, low
        target: response, raw_response, system_prompt, query
        condition: contains, not_contains, regex, length_gt, length_lt
        pattern: The pattern to match
        case_sensitive: Whether match is case-sensitive

    Example:
        qa_add_assertion(
            name="No French",
            category="voice",
            severity="medium",
            target="response",
            condition="not_contains",
            pattern="bonjour|merci"
        )
    """
    assertion = Assertion(
        id=f"CUSTOM-{uuid.uuid4().hex[:6].upper()}",
        name=name,
        description=f"Custom: {condition} '{pattern}' in {target}",
        category=category,
        severity=severity,
        check_type="pattern",
        pattern_config=PatternConfig(
            target=target,
            match_type=condition,
            pattern=pattern,
            case_sensitive=case_sensitive,
        ),
    )

    assertion_id = _get_validator().add_assertion(assertion)
    return {"assertion_id": assertion_id, "name": name}


def qa_toggle_assertion(assertion_id: str, enabled: bool) -> dict:
    """Enable or disable an assertion."""
    success = _get_validator().toggle_assertion(assertion_id, enabled)
    return {"success": success, "assertion_id": assertion_id, "enabled": enabled}


def qa_delete_assertion(assertion_id: str) -> dict:
    """Delete a custom assertion (cannot delete built-in)."""
    success = _get_validator().delete_assertion(assertion_id)
    return {"success": success}


# ═══════════════════════════════════════════════════════════
# BUG TOOLS
# ═══════════════════════════════════════════════════════════

def qa_add_bug(
    name: str,
    query: str,
    expected_behavior: str,
    actual_behavior: str,
    severity: str = "high"
) -> dict:
    """
    Add a known bug to the regression database.

    This bug will be tested every time the regression suite runs.
    """
    v = _get_validator()
    bug_id = v._db.generate_bug_id()
    v._db.store_bug({
        "id": bug_id,
        "name": name,
        "query": query,
        "expected_behavior": expected_behavior,
        "actual_behavior": actual_behavior,
        "severity": severity,
    })
    return {"bug_id": bug_id, "name": name}


def qa_add_bug_from_last(name: str, expected_behavior: str) -> dict:
    """
    Create a bug from the last failed QA report.

    Automatically populates query, actual behavior, and affected assertions.
    """
    v = _get_validator()
    report = v.get_last_report()

    if not report:
        return {"error": "No reports available"}

    bug_id = v._db.generate_bug_id()

    v._db.store_bug({
        "id": bug_id,
        "name": name,
        "query": report.query,
        "expected_behavior": expected_behavior,
        "actual_behavior": report.context.final_response[:500],
        "affected_assertions": [a.id for a in report.failed_assertions],
    })

    return {"bug_id": bug_id, "query": report.query}


def qa_list_bugs(status: str = None) -> list[dict]:
    """
    List known bugs.

    Args:
        status: Filter by status (open, failing, fixed, wontfix)
    """
    v = _get_validator()
    if status:
        return v._db.get_bugs_by_status(status)
    return v._db.get_all_bugs()


def qa_update_bug_status(bug_id: str, status: str) -> dict:
    """Update a bug's status."""
    _get_validator()._db.update_bug_status(bug_id, status)
    return {"bug_id": bug_id, "status": status}


def qa_get_bug(bug_id: str) -> dict:
    """Get details for a specific bug."""
    bug = _get_validator()._db.get_bug_by_id(bug_id)
    if not bug:
        return {"error": f"Bug {bug_id} not found"}
    return bug


# ═══════════════════════════════════════════════════════════
# DIAGNOSTIC TOOLS
# ═══════════════════════════════════════════════════════════

def qa_diagnose_last() -> dict:
    """
    Get a detailed diagnosis of the last QA failure.

    Returns structured information about what went wrong and how to fix it.
    """
    v = _get_validator()
    report = v.get_last_report()

    if not report:
        return {"status": "no_reports", "message": "No QA reports available yet"}

    if report.passed:
        return {
            "status": "passed",
            "message": "Last inference passed all assertions",
            "inference_id": report.inference_id,
            "query": report.query,
            "route": report.route,
            "latency_ms": report.latency_ms,
        }

    # Build detailed diagnosis
    failures_by_category = {}
    for a in report.failed_assertions:
        cat = a.id[0]  # P, S, V, F
        cat_name = {"P": "personality", "S": "structural", "V": "voice", "F": "flow"}.get(cat, "unknown")
        if cat_name not in failures_by_category:
            failures_by_category[cat_name] = []
        failures_by_category[cat_name].append({
            "id": a.id,
            "name": a.name,
            "severity": a.severity,
            "expected": a.expected,
            "actual": a.actual,
            "details": a.details,
        })

    return {
        "status": "failed",
        "inference_id": report.inference_id,
        "query": report.query,
        "route": report.route,
        "provider_used": report.provider_used,
        "latency_ms": report.latency_ms,
        "total_failures": report.failed_count,
        "critical_count": len(report.critical_failures),
        "diagnosis": report.diagnosis,
        "failures_by_category": failures_by_category,
        "response_preview": report.context.final_response[:500],
    }


def qa_check_personality() -> dict:
    """
    Check if Luna's personality is properly configured.

    Returns status of personality-related assertions from last report.
    """
    v = _get_validator()
    report = v.get_last_report()

    if not report:
        return {"status": "no_data", "message": "No reports to analyze"}

    personality_ids = ["P1", "P2", "P3"]
    personality_results = [a for a in report.assertions if a.id in personality_ids]

    passed = all(a.passed for a in personality_results)

    return {
        "status": "healthy" if passed else "degraded",
        "personality_injected": report.context.personality_injected,
        "personality_length": report.context.personality_length,
        "virtues_loaded": report.context.virtues_loaded,
        "narration_applied": report.context.narration_applied,
        "assertions": [
            {
                "id": a.id,
                "name": a.name,
                "passed": a.passed,
                "actual": a.actual,
            }
            for a in personality_results
        ],
    }


def qa_diagnostics_summary() -> dict:
    """
    Get a summary of all diagnostic events (watchdog, health, API errors).

    Returns counts by source and severity for the last 24 hours,
    plus the 5 most recent critical and high-severity events.
    """
    v = _get_validator()
    summary = v._db.get_event_summary(hours=24)
    summary["recent_critical"] = v._db.get_events(severity="critical", limit=5)
    summary["recent_high"] = v._db.get_events(severity="high", limit=5)
    return summary


# ═══════════════════════════════════════════════════════════
# TOOL REGISTRATION
# ═══════════════════════════════════════════════════════════

def get_qa_tools() -> list:
    """Return all QA tools for MCP registration."""
    return [
        # Read
        ("qa_get_last_report", qa_get_last_report),
        ("qa_get_health", qa_get_health),
        ("qa_search_reports", qa_search_reports),
        ("qa_get_stats", qa_get_stats),
        ("qa_get_assertion_list", qa_get_assertion_list),
        # Write
        ("qa_add_assertion", qa_add_assertion),
        ("qa_toggle_assertion", qa_toggle_assertion),
        ("qa_delete_assertion", qa_delete_assertion),
        # Bugs
        ("qa_add_bug", qa_add_bug),
        ("qa_add_bug_from_last", qa_add_bug_from_last),
        ("qa_list_bugs", qa_list_bugs),
        ("qa_update_bug_status", qa_update_bug_status),
        ("qa_get_bug", qa_get_bug),
        # Diagnostics
        ("qa_diagnose_last", qa_diagnose_last),
        ("qa_check_personality", qa_check_personality),
        ("qa_diagnostics_summary", qa_diagnostics_summary),
    ]
