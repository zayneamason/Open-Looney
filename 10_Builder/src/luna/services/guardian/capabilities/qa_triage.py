"""
qa_triage — Guardian's first capability.

Inspects the last inference, summarizes health / trend / known bugs / node
status, and derives deterministic next-step recommendations. Pure reads from
the existing QA surface — no state mutation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .spec import CapabilityResult, CapabilitySpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Capability card
# ---------------------------------------------------------------------------

QA_TRIAGE_SPEC = CapabilitySpec(
    name="qa_triage",
    description=(
        "Triage the most recent inference: failures by category, health trend, "
        "related bugs, pipeline node status, and deterministic next-step "
        "recommendations. Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "include_bugs": {"type": "boolean", "default": True},
            "include_node_status": {"type": "boolean", "default": True},
            "time_range": {
                "type": "string",
                "enum": ["1h", "24h", "7d", "30d"],
                "default": "24h",
            },
        },
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "last_inference": {"type": ["object", "null"]},
            "health": {"type": ["object", "null"]},
            "trend": {"type": ["object", "null"]},
            "related_bugs": {"type": ["array", "null"]},
            "node_status": {"type": ["object", "null"]},
            "recommendations": {"type": "array", "items": {"type": "string"}},
        },
    },
    read_only=True,
    latency_class="fast",
    fallback_behavior=(
        "Sub-sources that raise are set to null with a source_notes entry; "
        "overall status stays 'ok' unless every sub-source fails."
    ),
)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

_ASSERTION_NODE_MAP = {
    "P1": "director", "P2": "director", "P3": "director",
    "V1": "director", "F1": "director", "F2": "director",
    "S1": "director", "S2": "director", "S3": "director",
    "S4": "director", "S5": "director",
    "I1": "matrix", "I2": "matrix", "I3": "matrix",
    "I4": "scribe", "E1": "scribe", "E2": "scribe",
}


async def handle_qa_triage(inputs: dict, engine: Any) -> CapabilityResult:
    include_bugs = inputs.get("include_bugs", True)
    include_node_status = inputs.get("include_node_status", True)
    time_range = inputs.get("time_range", "24h")

    notes: list[str] = []
    data: dict[str, Any] = {
        "last_inference": None,
        "health": None,
        "trend": None,
        "related_bugs": None,
        "node_status": None,
        "recommendations": [],
    }

    data["last_inference"] = await _safe(
        _collect_last_inference, notes, "last_inference"
    )
    data["health"] = await _safe(_collect_health, notes, "health")
    data["trend"] = await _safe(_collect_trend, notes, "trend", time_range)

    if include_bugs:
        data["related_bugs"] = await _safe(
            _collect_related_bugs, notes, "related_bugs", data["last_inference"]
        )

    if include_node_status:
        data["node_status"] = await _safe(
            _collect_node_status, notes, "node_status", engine
        )

    data["recommendations"] = _derive_recommendations(data)

    # Open a dedupe-keyed Guardian task on critical failure so the board
    # shows live QA work owned by Guardian rather than stopping at narration.
    guardian_task_id = await _open_guardian_task_if_critical(engine, data)
    if guardian_task_id:
        data["guardian_task_id"] = guardian_task_id

    populated = sum(
        1 for k in ("last_inference", "health", "trend") if data.get(k) is not None
    )
    status = "ok" if populated > 0 else "no_data"

    return CapabilityResult(
        capability="qa_triage",
        status=status,
        data=data,
        source_notes=notes,
    )


async def _open_guardian_task_if_critical(engine: Any, data: dict) -> str | None:
    """Upsert a Guardian-owned task when the last inference had critical fails.

    Dedup by deterministic task_id (``guardian_triage_<inference_id>``) via a
    pre-insert `tm.get` check — no schema change needed. Silent on any failure
    so triage itself never breaks because task creation did.
    """
    last = data.get("last_inference") or {}
    if last.get("status") != "failed":
        return None
    if int(last.get("critical_count") or 0) <= 0:
        return None
    if engine is None or getattr(engine, "task_manager", None) is None:
        return None
    inference_id = last.get("inference_id")
    if not inference_id:
        return None

    from luna.core.tasks import TaskStatus

    task_id = f"guardian_triage_{inference_id}"
    tm = engine.task_manager
    try:
        existing = await tm.get(task_id)
        if existing is not None:
            return task_id

        critical_ids: list[str] = []
        for cat_rows in (last.get("failures_by_category") or {}).values():
            for a in cat_rows or []:
                if a.get("severity") == "critical" and a.get("id"):
                    critical_ids.append(a["id"])

        diagnosis = last.get("diagnosis") or "critical assertion failure"
        title = f"QA triage: {diagnosis}"[:160]
        description = (
            f"Inference {inference_id} failed with {last.get('total_failures')} "
            f"assertion failure(s) ({last.get('critical_count')} critical). "
            f"Route: {last.get('route')}. Diagnosis: {diagnosis}"
        )

        await tm.create(
            task_id=task_id,
            title=title,
            description=description,
            kind="qa_triage",
            priority=2,  # high
            status=TaskStatus.READY.value,
            owner="guardian",
            source="qa_triage_capability",
            metadata={
                "inference_id": inference_id,
                "route": last.get("route"),
                "critical_assertions": critical_ids[:10],
                "capability_invocation": True,
            },
            keywords=[k for k in critical_ids[:5] if k] or None,
        )
        return task_id
    except Exception as e:
        logger.warning(f"qa_triage: failed to open Guardian task: {e}")
        return None


# ---------------------------------------------------------------------------
# Sub-source collectors (each wrapped with _safe for fault isolation)
# ---------------------------------------------------------------------------

async def _collect_last_inference() -> dict:
    from luna.qa.mcp_tools import qa_diagnose_last

    return await asyncio.to_thread(qa_diagnose_last)


async def _collect_health() -> dict:
    from luna.qa.mcp_tools import qa_get_health

    return await asyncio.to_thread(qa_get_health)


async def _collect_trend(time_range: str) -> dict:
    from luna.qa.mcp_tools import qa_get_stats

    stats = await asyncio.to_thread(qa_get_stats, time_range)
    return {"time_range": time_range, **(stats or {})}


async def _collect_related_bugs(last_inference: dict | None) -> list[dict]:
    from luna.qa.mcp_tools import qa_list_bugs

    bugs = await asyncio.to_thread(qa_list_bugs, "open")
    if not last_inference or last_inference.get("status") != "failed":
        return bugs[:5] if bugs else []

    failed_ids = {
        a.get("id")
        for cat in (last_inference.get("failures_by_category") or {}).values()
        for a in cat
    }
    if not failed_ids:
        return bugs[:5] if bugs else []

    return [
        b for b in (bugs or [])
        if set(b.get("affected_assertions") or []) & failed_ids
    ][:5]


async def _collect_node_status(engine: Any) -> dict:
    """Reproduce /api/diagnostics/pipeline's node_status computation in-process."""
    if engine is None:
        raise RuntimeError("engine reference unavailable")

    from luna.qa.mcp_tools import _get_validator

    validator = await asyncio.to_thread(_get_validator)
    report = validator._last_report
    if not report:
        return {}

    node_status: dict[str, str] = {}
    for a in report.assertions:
        node = _ASSERTION_NODE_MAP.get(a.id, "director")
        if node not in node_status:
            node_status[node] = "pass"
        if not a.passed:
            node_status[node] = (
                "fail" if a.severity in ("critical", "high") else "warn"
            )
    return node_status


# ---------------------------------------------------------------------------
# Safety wrapper — keeps one sub-source's failure from tanking the capability
# ---------------------------------------------------------------------------

async def _safe(fn, notes: list[str], label: str, *args):
    try:
        return await fn(*args)
    except Exception as e:
        notes.append(f"{label} unavailable: {type(e).__name__}: {e}")
        logger.warning(f"qa_triage: {label} collector failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Recommendations (deterministic, no LLM)
# ---------------------------------------------------------------------------

def _derive_recommendations(data: dict) -> list[str]:
    recs: list[str] = []
    last = data.get("last_inference") or {}
    status = last.get("status")

    if status == "no_reports":
        return ["no inferences recorded yet"]

    if status == "passed":
        return ["last inference clean; no action"]

    if status == "failed":
        critical_count = last.get("critical_count", 0)
        failures_by_category = last.get("failures_by_category") or {}

        if critical_count > 0:
            crit_ids: list[str] = []
            for cat in failures_by_category.values():
                crit_ids.extend(
                    a.get("id") for a in cat if a.get("severity") == "critical"
                )
            ids_str = ",".join(crit_ids[:3]) or "<top critical>"
            recs.append(
                f"Run qa_force_revalidate on critical assertions: {ids_str}"
            )

        if "personality" in failures_by_category:
            recs.append("Run qa_check_personality — P1/P2/P3 failed")

    node_status = data.get("node_status") or {}
    failing_nodes = [n for n, s in node_status.items() if s == "fail"]
    if failing_nodes:
        recs.append(
            f"Inspect failing actor(s) via luna_pipeline_state: {', '.join(failing_nodes)}"
        )

    bugs = data.get("related_bugs") or []
    if bugs:
        names = [b.get("name", b.get("id", "?")) for b in bugs[:3]]
        recs.append(f"Known related bugs: {', '.join(names)}")

    if not recs:
        recs.append("No specific action — review last_inference details")

    return recs


# ---------------------------------------------------------------------------
# Prompt formatting — render the triage result as a compact system-prompt block
# ---------------------------------------------------------------------------

def format_triage_for_prompt(result: CapabilityResult) -> str:
    if result.status == "error":
        return f"[qa_triage error: {result.data.get('error', 'unknown')}]"
    if result.status == "no_data":
        return "[qa_triage: no data available]"

    data = result.data
    lines: list[str] = []
    last = data.get("last_inference") or {}

    if last.get("status") == "failed":
        lines.append(
            f"Last inference FAILED: route={last.get('route')}, "
            f"total_failures={last.get('total_failures')}, "
            f"critical={last.get('critical_count')}"
        )
        if last.get("diagnosis"):
            lines.append(f"Diagnosis: {last['diagnosis']}")
        cats = last.get("failures_by_category") or {}
        if cats:
            lines.append("Failures by category:")
            for cat_name, items in cats.items():
                ids = ", ".join(a.get("id", "?") for a in items)
                lines.append(f"  • {cat_name}: {ids}")
    elif last.get("status") == "passed":
        lines.append(
            f"Last inference PASSED: route={last.get('route')}, "
            f"latency_ms={last.get('latency_ms')}"
        )
    elif last.get("status") == "no_reports":
        lines.append("No QA reports yet.")

    health = data.get("health")
    if health:
        lines.append(
            f"Health: pass_rate={health.get('pass_rate')}, "
            f"recent_failures={health.get('recent_failures')}, "
            f"failing_bugs={health.get('failing_bugs')}"
        )

    trend = data.get("trend")
    if trend and trend.get("report_count"):
        lines.append(
            f"Trend ({trend.get('time_range')}): "
            f"{trend.get('report_count')} reports, "
            f"pass_rate={trend.get('pass_rate')}"
        )

    node_status = data.get("node_status")
    if node_status:
        parts = [f"{n}={s}" for n, s in node_status.items()]
        lines.append("Node status: " + ", ".join(parts))

    bugs = data.get("related_bugs")
    if bugs:
        lines.append("Related bugs: " + ", ".join(b.get("name", "?") for b in bugs))

    recs = data.get("recommendations") or []
    if recs:
        lines.append("Recommendations:")
        for r in recs:
            lines.append(f"  → {r}")

    if result.source_notes:
        lines.append(
            "Degraded sources: " + "; ".join(result.source_notes)
        )

    return "\n".join(lines)
