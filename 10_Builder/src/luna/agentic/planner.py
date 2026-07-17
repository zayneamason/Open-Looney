"""
Planning Layer for Luna Engine
==============================

Decomposes complex goals into executable task sequences.
Supports ReACT-style reasoning and Chain-of-Thought planning.

From Part XIV:
- Planning Layer: Decompose complex requests into steps
- Tool Protocol: Standardized way to define/call capabilities
- Reasoning Traces: Luna explains her thinking (debuggable)

The planner creates Plans with PlanSteps that the AgentLoop executes.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional
import logging
import re

logger = logging.getLogger(__name__)


class PlanStepType(Enum):
    """
    Types of plan steps the agent can execute.

    Each type has different execution characteristics and
    may involve different actors/tools.
    """

    THINK = auto()
    """
    Internal reasoning step. No external action.
    Used for analysis, synthesis, decision-making.
    """

    OBSERVE = auto()
    """
    Gather information from environment.
    Read files, check state, query memory.
    """

    RETRIEVE = auto()
    """
    Memory retrieval from the Matrix.
    Uses embedding similarity and graph traversal.
    """

    TOOL = auto()
    """
    Execute an external tool.
    File operations, bash commands, API calls.
    """

    DELEGATE = auto()
    """
    Delegate to cloud LLM (Claude) for complex reasoning.
    Used when local model can't handle the task.
    """

    RESPOND = auto()
    """
    Generate response to user.
    Final step that produces output.
    """

    PARALLEL = auto()
    """
    Execute multiple sub-steps in parallel.
    Fan-out for concurrent operations.
    """

    WAIT = auto()
    """
    Wait for external event or condition.
    Used for async operations.
    """


@dataclass
class PlanStep:
    """
    A single step in an execution plan.

    Each step has:
    - Type: What kind of operation (think, observe, tool, etc.)
    - Description: Human-readable explanation
    - Tool: Which tool to use (if applicable)
    - Params: Parameters for the tool
    - Dependencies: Steps that must complete first

    Example:
        step = PlanStep(
            type=PlanStepType.TOOL,
            description="Read the project README",
            tool="read_file",
            params={"path": "README.md"},
        )
    """

    type: PlanStepType
    """The type of step to execute."""

    description: str
    """Human-readable description of what this step does."""

    tool: Optional[str] = None
    """Tool to execute (for TOOL steps)."""

    params: Dict[str, Any] = field(default_factory=dict)
    """Parameters for the tool or operation."""

    dependencies: List[int] = field(default_factory=list)
    """Indices of steps that must complete first."""

    expected_output: Optional[str] = None
    """Description of what this step should produce."""

    timeout_seconds: float = 30.0
    """Maximum time to wait for this step."""

    retries: int = 0
    """Number of retry attempts on failure."""

    def __repr__(self) -> str:
        tool_str = f", tool={self.tool}" if self.tool else ""
        return f"PlanStep({self.type.name}{tool_str}, '{self.description[:30]}...')"


@dataclass
class Plan:
    """
    A sequence of steps to achieve a goal.

    Plans are created by the Planner and executed by the AgentLoop.
    They support:
    - Sequential execution (steps in order)
    - Parallel execution (steps with same dependencies)
    - Conditional branching (future)

    Example:
        plan = Plan(
            goal="Research AI chips and add to notes",
            steps=[
                PlanStep(type=PlanStepType.DELEGATE, description="Research AI chip news"),
                PlanStep(type=PlanStepType.THINK, description="Extract key points"),
                PlanStep(type=PlanStepType.TOOL, description="Write to notes", tool="write_file"),
                PlanStep(type=PlanStepType.RESPOND, description="Confirm to user"),
            ],
        )
    """

    goal: str
    """The original goal this plan achieves."""

    steps: List[PlanStep] = field(default_factory=list)
    """Ordered list of steps to execute."""

    reasoning: str = ""
    """Explanation of the planning decisions."""

    estimated_duration_seconds: float = 0.0
    """Estimated total execution time."""

    complexity: float = 0.0
    """Complexity score (0.0-1.0) from the router."""

    @property
    def step_count(self) -> int:
        """Number of steps in the plan."""
        return len(self.steps)

    @property
    def tool_steps(self) -> List[PlanStep]:
        """Get all steps that use tools."""
        return [s for s in self.steps if s.type == PlanStepType.TOOL]

    @property
    def required_tools(self) -> List[str]:
        """Get list of tools required by this plan."""
        return [s.tool for s in self.steps if s.tool is not None]

    def __repr__(self) -> str:
        return f"Plan(goal='{self.goal[:30]}...', steps={self.step_count})"


class Planner:
    """
    Decomposes goals into executable plans.

    The Planner analyzes user goals and creates step-by-step
    plans that the AgentLoop can execute. It uses:

    - Pattern matching for common task types
    - Heuristics for step ordering
    - Tool requirement detection
    - Complexity estimation from QueryRouter

    Future enhancements:
    - LLM-based planning for novel tasks
    - Learning from execution outcomes
    - Dynamic replanning on failures

    Example:
        planner = Planner()
        plan = await planner.decompose("Research AI chips and summarize")
    """

    # Common task patterns and their step templates
    TASK_PATTERNS = {
        "research": {
            "patterns": [r"\bresearch\b", r"\bfind out\b", r"\blook up\b"],
            "steps": [
                (PlanStepType.DELEGATE, "Research the topic using Claude"),
                (PlanStepType.THINK, "Analyze and extract key points"),
                (PlanStepType.RESPOND, "Present findings to user"),
            ],
        },
        "memory_recall": {
            "patterns": [r"\bremember\b", r"\brecall\b", r"\bwhat did\b"],
            "steps": [
                (PlanStepType.RETRIEVE, "Search memory for relevant information"),
                (PlanStepType.RESPOND, "Share what was found"),
            ],
        },
        "file_read": {
            "patterns": [r"\bread\b.*\bfile\b", r"\bshow me\b.*\bfile\b", r"\bopen\b"],
            "steps": [
                (PlanStepType.TOOL, "Read the file", "read_file"),
                (PlanStepType.RESPOND, "Present file contents"),
            ],
        },
        "file_write": {
            "patterns": [r"\bwrite\b.*\bfile\b", r"\bsave\b", r"\bcreate\b.*\bfile\b"],
            "steps": [
                (PlanStepType.THINK, "Determine what to write"),
                (PlanStepType.TOOL, "Write the file", "write_file"),
                (PlanStepType.RESPOND, "Confirm file was written"),
            ],
        },
        "summarize": {
            "patterns": [r"\bsummarize\b", r"\bsummary\b", r"\bbrief\b"],
            "steps": [
                (PlanStepType.OBSERVE, "Gather content to summarize"),
                (PlanStepType.THINK, "Create concise summary"),
                (PlanStepType.RESPOND, "Present summary"),
            ],
        },
        "analyze": {
            "patterns": [r"\banalyze\b", r"\banalyse\b", r"\bevaluate\b"],
            "steps": [
                (PlanStepType.OBSERVE, "Gather data for analysis"),
                (PlanStepType.THINK, "Perform analysis"),
                (PlanStepType.RESPOND, "Present analysis results"),
            ],
        },
    }

    # Step type duration estimates (seconds)
    STEP_DURATIONS = {
        PlanStepType.THINK: 0.5,
        PlanStepType.OBSERVE: 1.0,
        PlanStepType.RETRIEVE: 0.5,
        PlanStepType.TOOL: 2.0,
        PlanStepType.DELEGATE: 5.0,
        PlanStepType.RESPOND: 0.5,
        PlanStepType.PARALLEL: 3.0,
        PlanStepType.WAIT: 5.0,
    }

    def __init__(self):
        """Initialize the planner."""
        # Compile patterns for efficiency
        self._compiled_patterns = {}
        for task_type, config in self.TASK_PATTERNS.items():
            self._compiled_patterns[task_type] = [
                re.compile(p, re.IGNORECASE) for p in config["patterns"]
            ]

    async def decompose(self, goal: str) -> Plan:
        """
        Decompose a goal into an executable plan.

        Uses pattern matching to identify task types and
        creates appropriate step sequences.

        Args:
            goal: The user's goal or request.

        Returns:
            A Plan with steps to achieve the goal.

        Example:
            >>> planner = Planner()
            >>> plan = await planner.decompose("Research AI chips")
            >>> print(plan.step_count)
            3
        """
        # Detect task types
        detected_types = self._detect_task_types(goal)

        if not detected_types:
            # Default plan for unrecognized tasks
            return self._create_default_plan(goal)

        # Build plan from detected types
        steps = []
        reasoning_parts = []

        for task_type in detected_types:
            config = self.TASK_PATTERNS[task_type]
            reasoning_parts.append(f"Detected '{task_type}' task pattern")

            for step_def in config["steps"]:
                if len(step_def) == 2:
                    step_type, description = step_def
                    tool = None
                elif len(step_def) == 3:
                    step_type, description, tool = step_def
                else:
                    continue

                # Avoid duplicate steps
                if not any(s.description == description for s in steps):
                    steps.append(PlanStep(
                        type=step_type,
                        description=description,
                        tool=tool,
                    ))

        # Calculate estimated duration
        estimated_duration = sum(
            self.STEP_DURATIONS.get(s.type, 1.0) for s in steps
        )

        # Calculate complexity
        complexity = self.estimate_complexity(goal)

        plan = Plan(
            goal=goal,
            steps=steps,
            reasoning="; ".join(reasoning_parts),
            estimated_duration_seconds=estimated_duration,
            complexity=complexity,
        )

        logger.info(f"Created plan: {plan}")
        return plan

    def estimate_complexity(self, query: str) -> float:
        """
        Estimate query complexity on a 0.0-1.0 scale.

        This is a simplified version of QueryRouter.estimate_complexity
        for use within the planner.

        Args:
            query: The user's input query.

        Returns:
            Complexity score between 0.0 (trivial) and 1.0 (very complex).
        """
        # Base complexity from length
        length = len(query)
        if length < 20:
            complexity = 0.1
        elif length < 50:
            complexity = 0.2
        elif length < 100:
            complexity = 0.3
        elif length < 200:
            complexity = 0.4
        else:
            complexity = 0.5

        # Detect task types
        task_types = self._detect_task_types(query)

        # More task types = more complex
        complexity += len(task_types) * 0.15

        # Research and analysis are inherently complex
        if "research" in task_types or "analyze" in task_types:
            complexity += 0.2

        # Multiple questions increase complexity
        question_count = query.count("?")
        if question_count > 1:
            complexity += (question_count - 1) * 0.1

        return max(0.0, min(1.0, complexity))

    def _detect_task_types(self, goal: str) -> List[str]:
        """Detect which task types match the goal."""
        detected = []

        for task_type, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                if pattern.search(goal):
                    detected.append(task_type)
                    break  # Only add each type once

        return detected

    def _create_default_plan(self, goal: str) -> Plan:
        """Create a default plan for unrecognized tasks."""
        logger.debug(f"Creating default plan for: {goal[:50]}...")

        # Simple think-respond pattern
        steps = [
            PlanStep(
                type=PlanStepType.THINK,
                description="Analyze the request and formulate response",
            ),
            PlanStep(
                type=PlanStepType.RESPOND,
                description="Respond to the user",
            ),
        ]

        return Plan(
            goal=goal,
            steps=steps,
            reasoning="No specific task pattern detected, using default think-respond",
            estimated_duration_seconds=1.0,
            complexity=self.estimate_complexity(goal),
        )

    def create_single_step_plan(
        self,
        goal: str,
        step_type: PlanStepType,
        description: str,
        tool: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Plan:
        """
        Create a plan with a single step.

        Useful for SIMPLE_PLAN execution path where only
        one action is needed.

        Args:
            goal: The goal this plan achieves.
            step_type: Type of the single step.
            description: Description of the step.
            tool: Tool to use (if applicable).
            params: Tool parameters (if applicable).

        Returns:
            A Plan with one step plus a RESPOND step.
        """
        steps = [
            PlanStep(
                type=step_type,
                description=description,
                tool=tool,
                params=params or {},
            ),
            PlanStep(
                type=PlanStepType.RESPOND,
                description="Present result to user",
            ),
        ]

        estimated_duration = sum(
            self.STEP_DURATIONS.get(s.type, 1.0) for s in steps
        )

        return Plan(
            goal=goal,
            steps=steps,
            reasoning="Single-step plan for simple task",
            estimated_duration_seconds=estimated_duration,
            complexity=0.3,
        )
