"""MenuRegistry — loads and validates menu.yaml.

See `Docs/Design/LunaAssistantMapping/DESIGN_Agentic_Menu_Framework.md` §1.
Cycle detection: `Docs/Design/LunaAssistantMapping/ADR_Menu_Framework_Decisions.md` §ADR-002.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from luna.menu.dsl import ExitConditionError, ExitConditionEvaluator

logger = logging.getLogger(__name__)


class MenuLoadError(Exception):
    """Raised when menu.yaml is malformed, contains cycles, or has no usable tasks."""
    pass


@dataclass
class TriggerChain:
    """Declarative DM chain — see ADR-002 (orchestrator over choreography)."""
    task_id: str
    condition: Optional[str] = None
    pass_context: list[str] = field(default_factory=list)


@dataclass
class MenuTask:
    id: str
    name: str
    description: str
    latency_tier: str           # near_zero | low | medium | high | background
    dm: Optional[str]           # None = native
    trigger_signals: list[str]
    tools: list[str]
    exit_condition: str
    context_slots: list[str]
    returns: str
    timeout_s: Optional[int] = None
    deprecated: bool = False
    deprecated_reason: Optional[str] = None
    on_complete_trigger: Optional[TriggerChain] = None


@dataclass
class CapabilityEntry:
    task_id: str
    name: str
    description: str
    latency_feel: str           # "instant" | "fast" | "takes a minute" | "takes a few minutes" | "background"
    dm: Optional[str]


_LATENCY_FEEL = {
    "near_zero": "instant",
    "low": "fast",
    "medium": "takes a minute",
    "high": "takes a few minutes",
    "background": "background",
}


class MenuRegistry:
    def __init__(self):
        self._tasks: dict[str, MenuTask] = {}
        self._evaluator = ExitConditionEvaluator()

    def load(self, path: str) -> None:
        """Load menu.yaml from disk. Validates exit conditions and cycle-detects chains."""
        p = Path(path)
        if not p.exists():
            raise MenuLoadError(f"menu.yaml not found at {path}")

        try:
            raw = yaml.safe_load(p.read_text())
        except yaml.YAMLError as e:
            raise MenuLoadError(f"YAML parse error in {path}: {e}") from e

        if not isinstance(raw, dict) or "tasks" not in raw:
            raise MenuLoadError(f"{path}: top-level 'tasks' key missing")

        tasks: list[MenuTask] = []
        for entry in raw["tasks"]:
            tasks.append(self._build_task(entry, path))

        for t in tasks:
            try:
                self._evaluator.validate_at_load(t.exit_condition)
            except ExitConditionError as e:
                raise MenuLoadError(
                    f"Task '{t.id}' has invalid exit_condition: {e}"
                ) from e
            if t.dm is not None and t.timeout_s is None:
                raise MenuLoadError(
                    f"Task '{t.id}' has dm='{t.dm}' but no timeout_s — required for DM tasks"
                )

        self._detect_cycles(tasks)

        active = [t for t in tasks if not t.deprecated]
        if not active:
            raise MenuLoadError(
                f"{path}: no non-deprecated tasks remain after filtering — "
                "registry would be empty"
            )

        self._tasks = {t.id: t for t in active}
        logger.info(
            "[menu-registry] loaded %d tasks from %s (%d deprecated filtered)",
            len(active), path, len(tasks) - len(active),
        )

    def get_task(self, task_id: str) -> Optional[MenuTask]:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[MenuTask]:
        return list(self._tasks.values())

    def _generate_manifest(self) -> list[CapabilityEntry]:
        """Project tasks → CapabilityEntry list for Luna's awareness (ADR-005)."""
        out: list[CapabilityEntry] = []
        for t in self._tasks.values():
            feel = _LATENCY_FEEL.get(t.latency_tier, t.latency_tier)
            out.append(CapabilityEntry(
                task_id=t.id,
                name=t.name,
                description=t.description,
                latency_feel=feel,
                dm=t.dm,
            ))
        return out

    @staticmethod
    def _build_task(entry: dict, path: str) -> MenuTask:
        required = ("id", "name", "latency_tier", "exit_condition", "returns")
        for k in required:
            if k not in entry:
                raise MenuLoadError(f"{path}: task missing required key '{k}': {entry!r}")

        trig = entry.get("on_complete_trigger")
        chain: Optional[TriggerChain] = None
        if trig is not None:
            if "task_id" not in trig:
                raise MenuLoadError(
                    f"{path}: task '{entry['id']}' on_complete_trigger missing 'task_id'"
                )
            chain = TriggerChain(
                task_id=trig["task_id"],
                condition=trig.get("condition"),
                pass_context=trig.get("pass_context", []),
            )

        return MenuTask(
            id=entry["id"],
            name=entry["name"],
            description=entry.get("description", ""),
            latency_tier=entry["latency_tier"],
            dm=entry.get("dm"),
            trigger_signals=entry.get("trigger_signals", []),
            tools=entry.get("tools", []),
            exit_condition=entry["exit_condition"],
            context_slots=entry.get("context_slots", []),
            returns=entry["returns"],
            timeout_s=entry.get("timeout_s"),
            deprecated=entry.get("deprecated", False),
            deprecated_reason=entry.get("deprecated_reason"),
            on_complete_trigger=chain,
        )

    @staticmethod
    def _detect_cycles(tasks: list[MenuTask]) -> None:
        """DFS over on_complete_trigger graph. Raises MenuLoadError on cycle.

        Algorithm copied verbatim from ADR-002.
        """
        graph: dict[str, Optional[str]] = {
            t.id: t.on_complete_trigger.task_id if t.on_complete_trigger else None
            for t in tasks
        }

        def dfs(node: str, visited: set, path: set) -> bool:
            if node in path:
                return True
            if node in visited:
                return False
            visited.add(node)
            path.add(node)
            nxt = graph.get(node)
            if nxt and dfs(nxt, visited, path):
                return True
            path.discard(node)
            return False

        visited: set = set()
        for task_id in graph:
            if dfs(task_id, visited, set()):
                raise MenuLoadError(
                    f"Cycle detected in on_complete_trigger chain involving task '{task_id}'. "
                    "Review menu.yaml and remove the cycle before loading."
                )
