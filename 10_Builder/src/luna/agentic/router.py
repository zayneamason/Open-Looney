"""
Query Router for Luna Engine
============================

Routes incoming queries to the appropriate execution path based on
estimated complexity. This is the critical optimization that keeps
simple queries fast while enabling complex agentic capabilities.

From Part XIV:
- 90% of queries should hit DIRECT or SIMPLE_PLAN
- Complex queries get full treatment when needed
- Background tasks run async with notification

Performance targets:
- DIRECT: <500ms (simple chat, greetings)
- SIMPLE_PLAN: 500ms-2s (memory query, simple tool)
- FULL_PLAN: 5-30s (complex task, multiple tools)
- BACKGROUND: Minutes (deep research, file processing)
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List
import logging
import re

logger = logging.getLogger(__name__)


class ExecutionPath(Enum):
    """
    Execution paths based on query complexity.

    Each path has different latency characteristics and capabilities.
    The router decides which path to use based on complexity estimation.
    """

    DIRECT = auto()
    """
    Skip planning entirely. Direct LLM call.
    Latency: <500ms
    Use case: Simple chat, greetings, quick questions
    """

    SIMPLE_PLAN = auto()
    """
    Single-step plan. One tool call or memory retrieval.
    Latency: 500ms-2s
    Use case: Memory query, simple tool execution
    """

    FULL_PLAN = auto()
    """
    Multi-step planning with observe/think/act loop.
    Latency: 5-30s
    Use case: Complex task, multiple tools, research
    """

    BACKGROUND = auto()
    """
    Async execution with notification when complete.
    Latency: Minutes
    Use case: Deep research, file processing, long-running tasks
    """


@dataclass
class RoutingDecision:
    """
    The router's decision about how to execute a query.

    Includes the chosen path and reasoning for transparency.
    """

    path: ExecutionPath
    """The execution path to use."""

    complexity: float
    """Estimated complexity (0.0-1.0)."""

    reason: str
    """Human-readable reason for the decision."""

    signals: List[str] = field(default_factory=list)
    """Signals that influenced the decision."""

    suggested_tools: List[str] = field(default_factory=list)
    """Tools that might be needed for this query."""


class QueryRouter:
    """
    Routes queries to the appropriate execution path.

    Uses a combination of:
    - Keyword/pattern detection (explicit signals)
    - Length and structure heuristics
    - Historical patterns (future: learned from outcomes)

    The goal is to minimize latency for simple queries while
    enabling full agentic capabilities when needed.

    Example:
        router = QueryRouter()
        path = router.route("Hello Luna!")  # DIRECT
        path = router.route("Research AI chips and summarize")  # FULL_PLAN
    """

    # Complexity thresholds for path selection
    DIRECT_THRESHOLD = 0.2
    SIMPLE_THRESHOLD = 0.5
    FULL_THRESHOLD = 0.8

    # Patterns that indicate specific paths
    GREETING_PATTERNS = [
        r"^(hi|hey|hello|good (morning|afternoon|evening))[\s,!.]*",
        r"^how are you",
        r"^what'?s up",
        r"^yo\b",
    ]

    SIMPLE_QUERY_PATTERNS = [
        r"^(what|who|when|where|why|how) (is|are|was|were|did|do|does)\b",
        r"^(tell me|explain|describe)\b",
        r"^(can you|could you) (tell|explain|describe)\b",
    ]

    RESEARCH_PATTERNS = [
        r"\b(research|investigate|find out|look up|search for)\b",
        r"\b(analyze|analyse|examine|study)\b",
        r"\b(compare|contrast|evaluate)\b",
        r"\b(latest|current|recent|today'?s?|this week)\b",
    ]

    CREATIVE_PATTERNS = [
        r"\b(generate|create|make|draw|paint|render)\b.*\b(image|picture|photo|art|illustration|portrait|video|animation)\b",
        r"\b(image|picture|video|portrait)\b.*\bof\b",
        r"\beden\b",
        r"\b(visualize|illustrate|depict)\b",
        r"\b(paint|draw)\b.*\b(a|an|the|me)\b",
    ]

    MULTI_STEP_PATTERNS = [
        r"\b(and then|after that|next|also|additionally)\b",
        r"\b(first|second|third|finally)\b",
        r"\b(step by step|one by one)\b",
        r"\b(multiple|several|various)\b",
    ]

    DATAROOM_PATTERNS = [
        r"\bdata\s*room\b",
        r"\baibrarian\b",
        r"\binvestor\s*(docs?|documents?|materials?|packet)\b",
        r"\bdue\s*diligence\b",
        r"\b(what|which)\b.*\b(documents?|files?)\b.*\b(do we|have we|are in)\b",
        r"\b(financials?|legal|team|product|partnerships?)\b.*\b(folder|category|section)\b",
        r"\bwhat'?s?\s+missing\b.*\b(data\s*room|docs?|documents?)\b",
        r"\bdata\s*room\s+(status|overview|summary)\b",
    ]

    TOOL_PATTERNS = {
        "read_file": [r"\bread\b.*\b(file|document)", r"\bopen\b.*\bfile\b"],
        "write_file": [r"\b(write|save|create)\b.*\bfile\b", r"\bwrite to\b"],
        "bash": [r"\brun\b.*\b(command|script)\b", r"\bexecute\b", r"\bbash\b"],
        "web_search": [r"\bsearch\b.*\bweb\b", r"\bgoogle\b", r"\blook up online\b"],
        "memory_query": [r"\bremember\b", r"\brecall\b", r"\bwhat did (I|we|you)\b"],
        "calendar": [r"\bcalendar\b", r"\bschedule\b", r"\bappointment\b", r"\bevent\b"],
        "dataroom_search": [
            r"\bdata\s*room\b",
            r"\baibrarian\b",
            r"\binvestor\s*(docs?|documents?|materials?)\b",
            r"\bdue\s*diligence\b",
        ],
        "eden_create_image": [
            r"\b(generate|create|make|draw|paint)\b.*\b(image|picture|photo|art|illustration)\b",
            r"\b(image|picture|photo|art|illustration)\b.*\b(of|for|about|showing|depicting)\b",
            r"\beden\b.*\b(image|create)\b",
        ],
        "eden_create_video": [
            r"\b(generate|create|make)\b.*\b(video|animation|clip|timelapse)\b",
            r"\b(video|animation|clip)\b.*\b(of|for|about|showing)\b",
            r"\beden\b.*\bvideo\b",
        ],
        "eden_chat": [
            r"\b(talk|chat|speak|converse)\b.*\b(eden|agent)\b",
            r"\beden agent\b",
            r"\bcreative agent\b",
        ],
    }

    SKILL_PATTERNS = {
        "skill:math": [
            r"\b(solve|integrate|differentiate|factor|simplify|expand)\b",
            r"\b(derivative|integral|equation|polynomial)\b",
        ],
        "skill:logic": [
            r"\b(truth table|tautology|contradiction|satisfiable)\b",
            r"\b(propositional|boolean)\b.*\b(logic|expression)\b",
        ],
        "skill:diagnostic": [
            r"\b(system health|diagnostics?|health check|status check)\b",
            r"\bhow.{0,15}(doing|feeling|running)\b",
        ],
        "skill:formatting": [
            r"\b(bullet points?|numbered list|outline for|table of)\b",
            r"\b(format|organize|structure)\b.{0,20}\b(as|into)\b.{0,20}\b(list|bullets|table)\b",
        ],
        "skill:reading": [
            r"\b(read|open|load|parse)\b.{0,30}\.(pdf|docx?|xlsx|pptx|txt|md)\b",
        ],
        "skill:eden": [
            r"\b(generate|create|make|draw|paint|render)\b.{0,30}\b(image|picture|art|illustration)\b",
            r"\b(generate|create|make)\b.{0,30}\b(video|animation)\b",
        ],
        "skill:analytics": [
            r"\b(memory stats?|memory (analytics?|summary))\b",
            r"\b(session stats?|entity count)\b",
        ],
    }

    BACKGROUND_PATTERNS = [
        r"\bin the background\b",
        r"\b(notify|tell) me when\b",
        r"\btake your time\b",
        r"\bno rush\b",
        r"\b(process|analyze|research) (all|everything|the whole)\b",
    ]

    # Memory query patterns - triggers RETRIEVE action
    MEMORY_QUERY_PATTERNS = [
        r"\b(remember|recall|recollect)\b",
        r"\bwhat do (you|I) know about\b",
        r"\bdo you (remember|know)\b",
        r"\btry (to|and) remember\b",
        r"\bwho (is|was)\b",
        r"\btell me about\b",
        r"\byour memor(y|ies)\b",
    ]

    # Known project entity names — any mention forces SIMPLE_PLAN → RETRIEVE
    # This prevents "tell me about Kozmo" from routing DIRECT and skipping memory
    # Luna architecture entity names — any mention forces SIMPLE_PLAN → RETRIEVE
    ENTITY_PATTERNS = [
        r"\bkozmo\b", r"\bguardian\b", r"\beclissi\b", r"\btapestry\b",
        r"\beden\b", r"\bmemory.?matrix\b",
        r"\baibrarian\b", r"\bobservatory\b", r"\bnexus\b",
        r"\bthe forger\b", r"\bben.?franklin\b", r"\bthe scribe\b",
        r"\blibrarian\b", r"\bdataroom\b",
    ]

    def __init__(self):
        """Initialize the query router."""
        # Compile patterns for efficiency
        self._greeting_re = [re.compile(p, re.IGNORECASE) for p in self.GREETING_PATTERNS]
        self._simple_re = [re.compile(p, re.IGNORECASE) for p in self.SIMPLE_QUERY_PATTERNS]
        self._research_re = [re.compile(p, re.IGNORECASE) for p in self.RESEARCH_PATTERNS]
        self._creative_re = [re.compile(p, re.IGNORECASE) for p in self.CREATIVE_PATTERNS]
        self._multi_step_re = [re.compile(p, re.IGNORECASE) for p in self.MULTI_STEP_PATTERNS]
        self._background_re = [re.compile(p, re.IGNORECASE) for p in self.BACKGROUND_PATTERNS]
        self._memory_query_re = [re.compile(p, re.IGNORECASE) for p in self.MEMORY_QUERY_PATTERNS]
        self._entity_re = [re.compile(p, re.IGNORECASE) for p in self.ENTITY_PATTERNS]
        self._dataroom_re = [re.compile(p, re.IGNORECASE) for p in self.DATAROOM_PATTERNS]
        self._skill_re = {
            skill: [re.compile(p, re.IGNORECASE) for p in patterns]
            for skill, patterns in self.SKILL_PATTERNS.items()
        }
        self._tool_re = {
            tool: [re.compile(p, re.IGNORECASE) for p in patterns]
            for tool, patterns in self.TOOL_PATTERNS.items()
        }

        # Path forcing configuration (can be updated via tuning system)
        # When enabled, certain query types bypass complexity-based routing
        self._force_memory_to_plan: bool = True
        self._memory_min_complexity: float = 0.3
        self._force_research_to_full: bool = True

    def route(self, query: str) -> ExecutionPath:
        """
        Route a query to the appropriate execution path.

        Args:
            query: The user's input query.

        Returns:
            The execution path to use.

        Example:
            >>> router = QueryRouter()
            >>> router.route("Hi!")
            ExecutionPath.DIRECT
            >>> router.route("Research the latest AI news")
            ExecutionPath.FULL_PLAN
        """
        decision = self.analyze(query)
        return decision.path

    def needs_memory_access(self, query: str) -> bool:
        """
        Check if a query requires memory access (should delegate).

        Memory queries ALWAYS delegate to the cloud model because:
        - Local model doesn't have access to memory matrix
        - Memory queries need retrieval + reasoning
        - User is asking about something they expect Luna to remember

        Args:
            query: The user's input query.

        Returns:
            True if query needs memory access.
        """
        return self._matches_any(query, self._memory_query_re)

    def analyze(self, query: str) -> RoutingDecision:
        """
        Analyze a query and return a detailed routing decision.

        This provides more information than route() for debugging
        and transparency about why a path was chosen.

        Args:
            query: The user's input query.

        Returns:
            A RoutingDecision with path, complexity, and reasoning.
        """
        complexity = self.estimate_complexity(query)
        signals = self._detect_signals(query)
        suggested_tools = self._detect_tools(query)

        # Check for explicit background request
        if self._matches_any(query, self._background_re):
            return RoutingDecision(
                path=ExecutionPath.BACKGROUND,
                complexity=complexity,
                reason="Explicit background processing requested",
                signals=signals,
                suggested_tools=suggested_tools,
            )

        # =========================================================================
        # PATH FORCING - Override complexity-based routing for specific query types
        # =========================================================================

        # Memory queries MUST use SIMPLE_PLAN to trigger the RETRIEVE action
        # Without this, "do you remember X?" goes DIRECT and skips memory search
        if "memory_query" in signals and self._force_memory_to_plan:
            forced_complexity = max(complexity, self._memory_min_complexity)
            logger.debug(
                f"Forcing memory query to SIMPLE_PLAN: "
                f"original_complexity={complexity:.2f}, forced={forced_complexity:.2f}"
            )
            return RoutingDecision(
                path=ExecutionPath.SIMPLE_PLAN,
                complexity=forced_complexity,
                reason="Memory query requires retrieval step",
                signals=signals,
                suggested_tools=["memory_query"],
            )

        # Research queries should use FULL_PLAN for multi-step reasoning
        if "research_request" in signals and self._force_research_to_full:
            forced_complexity = max(complexity, self.FULL_THRESHOLD)
            logger.debug(
                f"Forcing research query to FULL_PLAN: "
                f"original_complexity={complexity:.2f}, forced={forced_complexity:.2f}"
            )
            return RoutingDecision(
                path=ExecutionPath.FULL_PLAN,
                complexity=forced_complexity,
                reason="Research query requires multi-step planning",
                signals=signals,
                suggested_tools=suggested_tools,
            )

        # Creative requests should use SIMPLE_PLAN to route through Eden tools
        if "creative_request" in signals:
            forced_complexity = max(complexity, self._memory_min_complexity)
            logger.debug(
                f"Forcing creative query to SIMPLE_PLAN: "
                f"original_complexity={complexity:.2f}, forced={forced_complexity:.2f}"
            )
            return RoutingDecision(
                path=ExecutionPath.SIMPLE_PLAN,
                complexity=forced_complexity,
                reason="Creative request routes through Eden tools",
                signals=signals,
                suggested_tools=suggested_tools,
            )

        # Data room queries should use SIMPLE_PLAN to trigger dataroom tools
        if "dataroom_query" in signals:
            forced_complexity = max(complexity, self._memory_min_complexity)
            logger.debug(
                f"Forcing dataroom query to SIMPLE_PLAN: "
                f"original_complexity={complexity:.2f}, forced={forced_complexity:.2f}"
            )
            return RoutingDecision(
                path=ExecutionPath.SIMPLE_PLAN,
                complexity=forced_complexity,
                reason="Data room query routes through dataroom tools",
                signals=signals,
                suggested_tools=["dataroom_search"],
            )

        # =========================================================================
        # COMPLEXITY-BASED ROUTING (default path)
        # =========================================================================

        # Route based on complexity
        if complexity < self.DIRECT_THRESHOLD:
            path = ExecutionPath.DIRECT
            reason = "Simple query, no planning needed"
        elif complexity < self.SIMPLE_THRESHOLD:
            path = ExecutionPath.SIMPLE_PLAN
            reason = "Moderate complexity, single-step plan"
        elif complexity < self.FULL_THRESHOLD:
            path = ExecutionPath.FULL_PLAN
            reason = "Complex query, multi-step planning required"
        else:
            path = ExecutionPath.BACKGROUND
            reason = "Very complex query, background processing recommended"

        logger.debug(
            f"Routed query to {path.name}: complexity={complexity:.2f}, "
            f"signals={signals}, tools={suggested_tools}"
        )

        return RoutingDecision(
            path=path,
            complexity=complexity,
            reason=reason,
            signals=signals,
            suggested_tools=suggested_tools,
        )

    # ── Semantic routing (Qwen 3B subtask) ────────────────────────────────

    # Maps Qwen intent classification → ExecutionPath
    _INTENT_TO_PATH = {
        "greeting": ExecutionPath.DIRECT,
        "simple_question": ExecutionPath.DIRECT,
        "emotional": ExecutionPath.DIRECT,
        "meta": ExecutionPath.DIRECT,
        "memory_query": ExecutionPath.SIMPLE_PLAN,
        "dataroom": ExecutionPath.SIMPLE_PLAN,
        "creative": ExecutionPath.SIMPLE_PLAN,
        "task": ExecutionPath.FULL_PLAN,
        "research": ExecutionPath.FULL_PLAN,
    }

    _COMPLEXITY_TO_SCORE = {
        "trivial": 0.1,
        "simple": 0.25,
        "moderate": 0.55,
        "complex": 0.85,
    }

    def from_intent(self, intent_result: dict, query: str = "") -> RoutingDecision:
        """
        Convert a Qwen intent classification to a RoutingDecision.

        Falls back to regex-based analyze() if the intent is invalid.

        Args:
            intent_result: {"intent": "...", "complexity": "...", "tools": [...]}
            query: Original query (for fallback and logging)
        """
        intent = intent_result.get("intent", "")
        complexity_label = intent_result.get("complexity", "simple")
        tools = intent_result.get("tools", [])

        path = self._INTENT_TO_PATH.get(intent)
        if path is None:
            logger.warning(f"[ROUTE-SEMANTIC] Unknown intent '{intent}', falling back to regex")
            return self.analyze(query) if query else RoutingDecision(
                path=ExecutionPath.DIRECT, complexity=0.1,
                reason="Unknown intent, defaulting to DIRECT",
            )

        complexity = self._COMPLEXITY_TO_SCORE.get(complexity_label, 0.25)

        logger.info(
            f"[ROUTE-SEMANTIC] intent={intent} complexity={complexity_label}({complexity:.2f}) "
            f"path={path.name} tools={tools}"
        )

        return RoutingDecision(
            path=path,
            complexity=complexity,
            reason=f"Semantic classification: {intent} ({complexity_label})",
            signals=[f"semantic:{intent}"],
            suggested_tools=tools if isinstance(tools, list) else [],
        )

    def estimate_complexity(self, query: str) -> float:
        """
        Estimate query complexity on a 0.0-1.0 scale.

        Factors:
        - Query length (longer = more complex)
        - Presence of research/analysis keywords
        - Multi-step indicators
        - Tool requirements
        - Greeting/simple patterns (reduce complexity)

        Args:
            query: The user's input query.

        Returns:
            Complexity score between 0.0 (trivial) and 1.0 (very complex).
        """
        # Start with base complexity from length
        # Short queries (< 20 chars) start very low
        # Long queries (> 200 chars) start higher
        length = len(query)
        if length < 20:
            complexity = 0.05
        elif length < 50:
            complexity = 0.15
        elif length < 100:
            complexity = 0.25
        elif length < 200:
            complexity = 0.35
        else:
            complexity = 0.45

        # Reduce for greetings
        if self._matches_any(query, self._greeting_re):
            complexity *= 0.3

        # Reduce for simple questions
        if self._matches_any(query, self._simple_re):
            complexity *= 0.7

        # Increase for research indicators
        if self._matches_any(query, self._research_re):
            complexity += 0.25

        # Increase for multi-step indicators
        if self._matches_any(query, self._multi_step_re):
            complexity += 0.2

        # Increase for tool requirements
        tools_needed = len(self._detect_tools(query))
        complexity += tools_needed * 0.1

        # Increase for question marks (multiple questions)
        question_count = query.count("?")
        if question_count > 1:
            complexity += (question_count - 1) * 0.1

        # Increase for explicit complexity words
        complexity_words = ["complex", "complicated", "detailed", "comprehensive", "thorough"]
        for word in complexity_words:
            if word in query.lower():
                complexity += 0.15
                break

        # Clamp to [0.0, 1.0]
        return max(0.0, min(1.0, complexity))

    def _detect_signals(self, query: str) -> List[str]:
        """Detect signals that influence routing."""
        signals = []

        if self._matches_any(query, self._greeting_re):
            signals.append("greeting")
        if self._matches_any(query, self._simple_re):
            signals.append("simple_question")
        if self._matches_any(query, self._research_re):
            signals.append("research_request")
        if self._matches_any(query, self._multi_step_re):
            signals.append("multi_step")
        if self._matches_any(query, self._background_re):
            signals.append("background_request")

        # Memory query detection - triggers RETRIEVE action
        if self._matches_any(query, self._memory_query_re):
            signals.append("memory_query")

        # Entity name detection — known project entities force memory retrieval
        if self._matches_any(query, self._entity_re):
            signals.append("entity_mention")
            if "memory_query" not in signals:
                signals.append("memory_query")

        # Creative request
        if self._matches_any(query, self._creative_re):
            signals.append("creative_request")

        # Data room query
        if self._matches_any(query, self._dataroom_re):
            signals.append("dataroom_query")

        # Skill-eligible query detection (observability — skills fire from Director)
        for skill_name, patterns in self._skill_re.items():
            if self._matches_any(query, patterns):
                signals.append(skill_name)

        return signals

    def _detect_tools(self, query: str) -> List[str]:
        """Detect which tools might be needed for this query."""
        tools = []

        for tool_name, patterns in self._tool_re.items():
            if self._matches_any(query, patterns):
                tools.append(tool_name)

        return tools

    def _matches_any(self, text: str, patterns: List[re.Pattern]) -> bool:
        """Check if text matches any of the compiled patterns."""
        for pattern in patterns:
            if pattern.search(text):
                return True
        return False
