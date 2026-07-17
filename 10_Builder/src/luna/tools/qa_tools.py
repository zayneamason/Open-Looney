"""
QA Tools for Luna Engine
=========================

Self-diagnosis tools that allow Luna to check her own QA status,
search past reports, manage assertions, and track bugs.

These tools wrap the QA MCP functions for use in the ToolRegistry.

Based on Part XIV (Agentic Architecture) of the Luna Engine Bible.
"""

import asyncio
import logging
from typing import Optional
from functools import partial

from .registry import Tool, ToolRegistry

logger = logging.getLogger(__name__)


# =============================================================================
# QA MODULE IMPORTS
# =============================================================================

try:
    from luna.qa.mcp_tools import (
        qa_get_last_report,
        qa_get_health,
        qa_search_reports,
        qa_get_stats,
        qa_get_assertion_list,
        qa_add_assertion,
        qa_toggle_assertion,
        qa_delete_assertion,
        qa_add_bug,
        qa_add_bug_from_last,
        qa_list_bugs,
        qa_update_bug_status,
        qa_get_bug,
        qa_diagnose_last,
        qa_check_personality,
    )
    QA_AVAILABLE = True
except ImportError:
    QA_AVAILABLE = False
    logger.warning("QA module not available - QA tools will not be registered")


# =============================================================================
# ASYNC WRAPPERS
# =============================================================================

def _run_sync(func, *args, **kwargs):
    """Run a synchronous function."""
    return func(*args, **kwargs)


async def _async_wrap(func, *args, **kwargs):
    """Wrap a sync function to run in executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, partial(_run_sync, func, *args, **kwargs)
    )


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================

async def get_last_report() -> dict:
    """
    Get the QA report for Luna's most recent inference.

    Returns assertion results, diagnosis, and debugging info.
    Use this to understand why your last response may have failed QA.
    """
    return await _async_wrap(qa_get_last_report)


async def get_health() -> dict:
    """
    Get Luna's current QA health status.

    Returns:
        pass_rate: float (0-1)
        recent_failures: list of failed assertion names
        failing_bugs: count of known bugs still failing
    """
    return await _async_wrap(qa_get_health)


async def search_reports(
    query: Optional[str] = None,
    route: Optional[str] = None,
    passed: Optional[bool] = None,
    limit: int = 10
) -> list:
    """
    Search QA report history.

    Args:
        query: Filter by query text
        route: Filter by route (LOCAL_ONLY, DELEGATION_DETECTION, FULL_DELEGATION)
        passed: Filter by pass/fail status
        limit: Max results
    """
    return await _async_wrap(qa_search_reports, query, route, passed, limit)


async def get_stats(time_range: str = "24h") -> dict:
    """
    Get QA statistics.

    Args:
        time_range: "1h", "24h", "7d", "30d"
    """
    return await _async_wrap(qa_get_stats, time_range)


async def get_assertion_list() -> list:
    """Get all configured assertions."""
    return await _async_wrap(qa_get_assertion_list)


async def add_assertion(
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
    """
    return await _async_wrap(
        qa_add_assertion, name, category, severity, target, condition, pattern, case_sensitive
    )


async def toggle_assertion(assertion_id: str, enabled: bool) -> dict:
    """Enable or disable an assertion."""
    return await _async_wrap(qa_toggle_assertion, assertion_id, enabled)


async def delete_assertion(assertion_id: str) -> dict:
    """Delete a custom assertion (cannot delete built-in)."""
    return await _async_wrap(qa_delete_assertion, assertion_id)


async def add_bug(
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
    return await _async_wrap(qa_add_bug, name, query, expected_behavior, actual_behavior, severity)


async def add_bug_from_last(name: str, expected_behavior: str) -> dict:
    """
    Create a bug from the last failed QA report.
    Automatically populates query, actual behavior, and affected assertions.
    """
    return await _async_wrap(qa_add_bug_from_last, name, expected_behavior)


async def list_bugs(status: Optional[str] = None) -> list:
    """
    List known bugs.

    Args:
        status: Filter by status (open, failing, fixed, wontfix)
    """
    return await _async_wrap(qa_list_bugs, status)


async def update_bug_status(bug_id: str, status: str) -> dict:
    """Update a bug's status."""
    return await _async_wrap(qa_update_bug_status, bug_id, status)


async def get_bug(bug_id: str) -> dict:
    """Get details for a specific bug."""
    return await _async_wrap(qa_get_bug, bug_id)


async def diagnose_last() -> dict:
    """
    Get a detailed diagnosis of the last QA failure.
    Returns structured information about what went wrong and how to fix it.
    """
    return await _async_wrap(qa_diagnose_last)


async def check_personality() -> dict:
    """
    Check if Luna's personality is properly configured.
    Returns status of personality-related assertions from last report.
    """
    return await _async_wrap(qa_check_personality)


# =============================================================================
# TOOL DEFINITIONS
# =============================================================================

qa_get_last_report_tool = Tool(
    name="qa_get_last_report",
    description="Get the QA report for Luna's most recent inference. Shows assertion results, diagnosis, and debugging info.",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    },
    execute=get_last_report,
    requires_confirmation=False,
    timeout_seconds=10
)

qa_get_health_tool = Tool(
    name="qa_get_health",
    description="Get Luna's current QA health status including pass rate, recent failures, and failing bug count.",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    },
    execute=get_health,
    requires_confirmation=False,
    timeout_seconds=10
)

qa_search_reports_tool = Tool(
    name="qa_search_reports",
    description="Search QA report history by query text, route, or pass/fail status.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Filter by query text"},
            "route": {"type": "string", "description": "Filter by route (LOCAL_ONLY, DELEGATION_DETECTION, FULL_DELEGATION)"},
            "passed": {"type": "boolean", "description": "Filter by pass/fail status"},
            "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10}
        },
        "required": []
    },
    execute=search_reports,
    requires_confirmation=False,
    timeout_seconds=10
)

qa_get_stats_tool = Tool(
    name="qa_get_stats",
    description="Get QA statistics for a time range.",
    parameters={
        "type": "object",
        "properties": {
            "time_range": {"type": "string", "description": "Time range: 1h, 24h, 7d, 30d", "default": "24h"}
        },
        "required": []
    },
    execute=get_stats,
    requires_confirmation=False,
    timeout_seconds=10
)

qa_get_assertions_tool = Tool(
    name="qa_get_assertions",
    description="List all configured QA assertions.",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    },
    execute=get_assertion_list,
    requires_confirmation=False,
    timeout_seconds=10
)

qa_add_assertion_tool = Tool(
    name="qa_add_assertion",
    description="Add a new pattern-based assertion to the QA system.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Human-readable name"},
            "category": {"type": "string", "description": "Category: structural, voice, personality, flow"},
            "severity": {"type": "string", "description": "Severity: critical, high, medium, low"},
            "target": {"type": "string", "description": "Target: response, raw_response, system_prompt, query"},
            "condition": {"type": "string", "description": "Condition: contains, not_contains, regex, length_gt, length_lt"},
            "pattern": {"type": "string", "description": "The pattern to match"},
            "case_sensitive": {"type": "boolean", "description": "Case-sensitive match", "default": False}
        },
        "required": ["name", "category", "severity", "target", "condition", "pattern"]
    },
    execute=add_assertion,
    requires_confirmation=True,
    timeout_seconds=10
)

qa_toggle_assertion_tool = Tool(
    name="qa_toggle_assertion",
    description="Enable or disable a QA assertion.",
    parameters={
        "type": "object",
        "properties": {
            "assertion_id": {"type": "string", "description": "The assertion ID"},
            "enabled": {"type": "boolean", "description": "Enable (true) or disable (false)"}
        },
        "required": ["assertion_id", "enabled"]
    },
    execute=toggle_assertion,
    requires_confirmation=False,
    timeout_seconds=10
)

qa_delete_assertion_tool = Tool(
    name="qa_delete_assertion",
    description="Delete a custom QA assertion (built-in assertions cannot be deleted).",
    parameters={
        "type": "object",
        "properties": {
            "assertion_id": {"type": "string", "description": "The assertion ID to delete"}
        },
        "required": ["assertion_id"]
    },
    execute=delete_assertion,
    requires_confirmation=True,
    timeout_seconds=10
)

qa_add_bug_tool = Tool(
    name="qa_add_bug",
    description="Add a known bug to the regression database for tracking.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Bug name/title"},
            "query": {"type": "string", "description": "The query that triggers the bug"},
            "expected_behavior": {"type": "string", "description": "What should happen"},
            "actual_behavior": {"type": "string", "description": "What actually happens"},
            "severity": {"type": "string", "description": "Severity: critical, high, medium, low", "default": "high"}
        },
        "required": ["name", "query", "expected_behavior", "actual_behavior"]
    },
    execute=add_bug,
    requires_confirmation=True,
    timeout_seconds=10
)

qa_add_bug_from_last_tool = Tool(
    name="qa_add_bug_from_last",
    description="Create a bug from the last failed QA report. Auto-populates query and actual behavior.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Bug name/title"},
            "expected_behavior": {"type": "string", "description": "What should happen"}
        },
        "required": ["name", "expected_behavior"]
    },
    execute=add_bug_from_last,
    requires_confirmation=True,
    timeout_seconds=10
)

qa_list_bugs_tool = Tool(
    name="qa_list_bugs",
    description="List known bugs, optionally filtered by status.",
    parameters={
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "Filter by status: open, failing, fixed, wontfix"}
        },
        "required": []
    },
    execute=list_bugs,
    requires_confirmation=False,
    timeout_seconds=10
)

qa_update_bug_status_tool = Tool(
    name="qa_update_bug_status",
    description="Update a bug's status.",
    parameters={
        "type": "object",
        "properties": {
            "bug_id": {"type": "string", "description": "The bug ID"},
            "status": {"type": "string", "description": "New status: open, failing, fixed, wontfix"}
        },
        "required": ["bug_id", "status"]
    },
    execute=update_bug_status,
    requires_confirmation=False,
    timeout_seconds=10
)

qa_get_bug_tool = Tool(
    name="qa_get_bug",
    description="Get details for a specific bug by ID.",
    parameters={
        "type": "object",
        "properties": {
            "bug_id": {"type": "string", "description": "The bug ID"}
        },
        "required": ["bug_id"]
    },
    execute=get_bug,
    requires_confirmation=False,
    timeout_seconds=10
)

qa_diagnose_last_tool = Tool(
    name="qa_diagnose_last",
    description="Get a detailed diagnosis of the last QA failure with structured fix suggestions.",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    },
    execute=diagnose_last,
    requires_confirmation=False,
    timeout_seconds=10
)

qa_check_personality_tool = Tool(
    name="qa_check_personality",
    description="Check if Luna's personality is properly configured based on recent QA results.",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    },
    execute=check_personality,
    requires_confirmation=False,
    timeout_seconds=10
)


# =============================================================================
# ALL QA TOOLS
# =============================================================================

ALL_QA_TOOLS = [
    qa_get_last_report_tool,
    qa_get_health_tool,
    qa_search_reports_tool,
    qa_get_stats_tool,
    qa_get_assertions_tool,
    qa_add_assertion_tool,
    qa_toggle_assertion_tool,
    qa_delete_assertion_tool,
    qa_add_bug_tool,
    qa_add_bug_from_last_tool,
    qa_list_bugs_tool,
    qa_update_bug_status_tool,
    qa_get_bug_tool,
    qa_diagnose_last_tool,
    qa_check_personality_tool,
]


def register_qa_tools(registry: ToolRegistry) -> None:
    """
    Register all QA tools with a ToolRegistry.

    Args:
        registry: The ToolRegistry to register tools with
    """
    if not QA_AVAILABLE:
        logger.warning("QA module not available - skipping QA tool registration")
        return

    for tool in ALL_QA_TOOLS:
        try:
            registry.register(tool)
            logger.debug(f"Registered QA tool: {tool.name}")
        except ValueError as e:
            logger.warning(f"Could not register QA tool {tool.name}: {e}")

    logger.info(f"Registered {len(ALL_QA_TOOLS)} QA tools")
