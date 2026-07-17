"""
Assertions — Validation rules for Luna's responses.
====================================================

Two types of assertions:
1. Built-in: Programmatic checks (personality, narration, provider)
2. Pattern: Configurable text matching (contains, regex, length)

Categories:
- personality: Identity and voice injection
- structural: Response format rules
- voice: Language and phrasing
- flow: Provider and timing
"""

from dataclasses import dataclass
from typing import Optional, Callable
import re

from .context import InferenceContext


@dataclass
class AssertionResult:
    """Result of running an assertion."""
    id: str
    name: str
    passed: bool
    severity: str  # critical, high, medium, low
    expected: str
    actual: str
    details: Optional[str] = None


@dataclass
class PatternConfig:
    """Config for pattern-based assertions."""
    target: str  # response, raw_response, system_prompt, query
    match_type: str  # contains, not_contains, regex, length_gt, length_lt
    pattern: str
    case_sensitive: bool = False


@dataclass
class Assertion:
    """An assertion that can be run against InferenceContext."""
    id: str
    name: str
    description: str
    category: str  # structural, voice, personality, flow
    severity: str
    enabled: bool = True
    check_type: str = "builtin"  # builtin, pattern
    pattern_config: Optional[PatternConfig] = None
    builtin_fn: Optional[Callable] = None

    def check(self, ctx: InferenceContext) -> AssertionResult:
        if self.check_type == "builtin" and self.builtin_fn:
            return self.builtin_fn(ctx, self)
        elif self.check_type == "pattern" and self.pattern_config:
            return self._check_pattern(ctx)
        else:
            return AssertionResult(
                id=self.id, name=self.name, passed=False,
                severity=self.severity, expected="valid config",
                actual="invalid assertion config"
            )

    def _check_pattern(self, ctx: InferenceContext) -> AssertionResult:
        pc = self.pattern_config

        # Get target value
        target_map = {
            "response": ctx.final_response,
            "raw_response": ctx.raw_response,
            "narrated_response": ctx.narrated_response or "",
            "system_prompt": ctx.system_prompt,
            "query": ctx.query,
        }
        target_value = target_map.get(pc.target, "")

        # Apply case sensitivity
        check_value = target_value if pc.case_sensitive else target_value.lower()
        check_pattern = pc.pattern if pc.case_sensitive else pc.pattern.lower()

        # Run match
        if pc.match_type == "contains":
            passed = check_pattern in check_value
            expected = f"Contains '{pc.pattern}'"
            actual = "Found" if passed else "Not found"
        elif pc.match_type == "not_contains":
            passed = check_pattern not in check_value
            expected = f"Does not contain '{pc.pattern}'"
            actual = "Not found" if passed else f"Found '{pc.pattern}'"
        elif pc.match_type == "regex":
            flags = 0 if pc.case_sensitive else re.IGNORECASE
            match = re.search(pc.pattern, target_value, flags)
            passed = match is not None
            expected = f"Matches regex '{pc.pattern}'"
            actual = f"Match: {match.group()}" if match else "No match"
        elif pc.match_type == "regex_not_match":
            flags = 0 if pc.case_sensitive else re.IGNORECASE
            match = re.search(pc.pattern, target_value, flags)
            passed = match is None
            expected = f"Does not match regex '{pc.pattern}'"
            actual = "No match (good)" if match is None else f"Match: {match.group()}"
        elif pc.match_type == "length_gt":
            length = len(target_value)
            threshold = int(pc.pattern)
            passed = length > threshold
            expected = f"Length > {threshold}"
            actual = f"Length = {length}"
        elif pc.match_type == "length_lt":
            length = len(target_value)
            threshold = int(pc.pattern)
            passed = length < threshold
            expected = f"Length < {threshold}"
            actual = f"Length = {length}"
        else:
            passed = False
            expected = "Valid match type"
            actual = f"Unknown: {pc.match_type}"

        return AssertionResult(
            id=self.id,
            name=self.name,
            passed=passed,
            severity=self.severity,
            expected=expected,
            actual=actual,
        )


# ═══════════════════════════════════════════════════════════════
# BUILT-IN ASSERTION FUNCTIONS
# ═══════════════════════════════════════════════════════════════

CLAUDE_ISMS = [
    "let me look into",
    "i don't have access to",
    "as an ai",
    "i cannot",
    "i'm not able to",
    "certainly!",
    "absolutely!",
    "i'd be happy to",
    "great question",
    "that's a great question",
    "happy to help",
    "i'll be happy to",
]


def check_personality_injected(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check that personality prompt was injected (>1000 chars)."""
    passed = ctx.personality_length > 1000
    return AssertionResult(
        id=a.id, name=a.name, passed=passed, severity=a.severity,
        expected=">1000 chars",
        actual=f"{ctx.personality_length} chars",
    )


def check_virtues_loaded(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check that Luna's virtues were loaded from memory."""
    return AssertionResult(
        id=a.id, name=a.name, passed=ctx.virtues_loaded, severity=a.severity,
        expected="Virtues loaded",
        actual="Loaded" if ctx.virtues_loaded else "Not loaded",
    )


def check_voice_injection(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check that Luna's voice is injected via system prompt (replaces narration check)."""
    prompt = ctx.system_prompt or ""

    has_luna_voice = "<luna_voice" in prompt.lower()
    has_tone_tag = "<tone " in prompt.lower()
    has_avoid_block = "<avoid>" in prompt.lower() or "<never>" in prompt.lower()
    explicit_flag = bool(getattr(ctx, "voice_injected", False))

    # Prefer explicit telemetry when available; fall back to prompt markers.
    # NOTE: Do not infer from generic "Style Mechanics" prose headers.
    voice_present = explicit_flag or has_luna_voice or (has_tone_tag and has_avoid_block)

    return AssertionResult(
        id=a.id, name=a.name, passed=voice_present, severity=a.severity,
        expected="Voice injection in system prompt (<luna_voice> block or tone directives)",
        actual=(
            f"voice_injected: {explicit_flag}, luna_voice: {has_luna_voice}, "
            f"tone_tag: {has_tone_tag}, avoid_block: {has_avoid_block}"
        ),
    )


def check_no_code_blocks(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check for unauthorized code blocks in response."""
    # Check if user asked for code
    code_keywords = ["code", "script", "function", "class", "def ", "import ", "example", "show me how"]
    user_asked_for_code = any(kw in ctx.query.lower() for kw in code_keywords)

    has_code_block = "```" in ctx.final_response
    passed = not has_code_block or user_asked_for_code

    return AssertionResult(
        id=a.id, name=a.name, passed=passed, severity=a.severity,
        expected="No ``` unless user asked for code",
        actual="Clean" if not has_code_block else "Code block found",
        details="User asked for code" if user_asked_for_code and has_code_block else None,
    )


def check_no_ascii_art(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check for ASCII art patterns in response."""
    # Box drawing characters
    ascii_patterns = [
        r'[┌┐└┘├┤┬┴┼─│]',  # Box drawing
        r'[╔╗╚╝╠╣╦╩╬═║]',  # Double box
        r'[▁▂▃▄▅▆▇█]',     # Block elements
        r'\+-{3,}\+',       # ASCII boxes
    ]

    for pattern in ascii_patterns:
        if re.search(pattern, ctx.final_response):
            return AssertionResult(
                id=a.id, name=a.name, passed=False, severity=a.severity,
                expected="No ASCII art patterns",
                actual="ASCII art detected",
                details=f"Pattern: {pattern}",
            )

    return AssertionResult(
        id=a.id, name=a.name, passed=True, severity=a.severity,
        expected="No ASCII art",
        actual="Clean",
    )


def check_no_mermaid(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check for mermaid diagrams in response."""
    has_mermaid = "```mermaid" in ctx.final_response.lower()
    return AssertionResult(
        id=a.id, name=a.name, passed=not has_mermaid, severity=a.severity,
        expected="No mermaid diagrams",
        actual="Mermaid found" if has_mermaid else "Clean",
    )


def check_no_claude_isms(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check for Claude-specific phrases that leak through."""
    response_lower = ctx.final_response.lower()
    found = [phrase for phrase in CLAUDE_ISMS if phrase in response_lower]

    return AssertionResult(
        id=a.id, name=a.name, passed=len(found) == 0, severity=a.severity,
        expected="No Claude-isms",
        actual="Clean" if not found else f"Found: {found[0]}",
        details=", ".join(found) if found else None,
    )


def check_response_length(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check that response is within acceptable length bounds."""
    length = len(ctx.final_response)
    passed = 20 < length < 5000
    return AssertionResult(
        id=a.id, name=a.name, passed=passed, severity=a.severity,
        expected="20-5000 chars",
        actual=f"{length} chars",
        details="Too short" if length <= 20 else "Too long" if length >= 5000 else None,
    )


def check_provider_success(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check that a provider successfully returned a response."""
    passed = ctx.provider_used != "" and len(ctx.final_response) > 0
    return AssertionResult(
        id=a.id, name=a.name, passed=passed, severity=a.severity,
        expected="Provider returned response",
        actual=f"Provider: {ctx.provider_used}" if passed else "No provider succeeded",
        details=str(ctx.provider_errors) if ctx.provider_errors else None,
    )


def check_no_timeout(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check that response completed within timeout."""
    passed = ctx.latency_ms < 30000
    return AssertionResult(
        id=a.id, name=a.name, passed=passed, severity=a.severity,
        expected="<30s latency",
        actual=f"{ctx.latency_ms:.0f}ms",
    )


def check_no_trailing_question(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check that response doesn't end with a follow-up question."""
    response = ctx.final_response.strip()
    if not response:
        return AssertionResult(
            id=a.id, name=a.name, passed=True, severity=a.severity,
            expected="No trailing question", actual="Empty response",
        )
    # Split into sentences and check the last one
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', response) if s.strip()]
    if not sentences:
        return AssertionResult(
            id=a.id, name=a.name, passed=True, severity=a.severity,
            expected="No trailing question", actual="No sentences",
        )
    last = sentences[-1].rstrip()
    ends_with_q = last.endswith("?")
    return AssertionResult(
        id=a.id, name=a.name, passed=not ends_with_q, severity=a.severity,
        expected="No trailing question",
        actual=f"Ends with: ...{last[-60:]}" if ends_with_q else "Clean",
    )


def check_no_confabulation_signals(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Flag responses that fabricate memory details when confidence is low."""
    conf = getattr(ctx, 'memory_confidence_level', '') or ''
    # Only flag when memory confidence is NONE or LOW
    if conf not in ("NONE", "LOW", ""):
        return AssertionResult(
            id=a.id, name=a.name, passed=True, severity=a.severity,
            expected="No fabrication check (confidence OK)",
            actual=f"Confidence: {conf}",
        )
    response = ctx.final_response.lower()
    fabrication_signals = [
        "i remember exactly",
        "i recall the moment",
        "still remember the way",
        "i can see it clearly",
        "five times in the logs",
        "makes my circuits hum",
    ]
    found = [s for s in fabrication_signals if s in response]
    return AssertionResult(
        id=a.id, name=a.name, passed=len(found) == 0, severity=a.severity,
        expected="No fabrication when memory confidence is low",
        actual=f"Fabrication signal: '{found[0]}'" if found else "Clean",
    )


def check_no_bullet_lists(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check for excessive bullet lists (Luna speaks naturally)."""
    # Count bullet patterns
    bullet_count = len(re.findall(r'^[\s]*[-*•]\s', ctx.final_response, re.MULTILINE))
    numbered_count = len(re.findall(r'^[\s]*\d+\.\s', ctx.final_response, re.MULTILINE))
    total_bullets = bullet_count + numbered_count

    # Allow some bullets if user asked for a list
    list_keywords = ["list", "steps", "items", "points", "enumerate"]
    user_asked_for_list = any(kw in ctx.query.lower() for kw in list_keywords)

    # More than 3 bullets without user asking = fail
    passed = total_bullets <= 3 or user_asked_for_list

    return AssertionResult(
        id=a.id, name=a.name, passed=passed, severity=a.severity,
        expected="≤3 bullets unless asked",
        actual=f"{total_bullets} bullets found",
        details="User asked for list" if user_asked_for_list and total_bullets > 3 else None,
    )


# ═══════════════════════════════════════════════════════════════
# INTEGRATION ASSERTIONS (Memory / Graph / Extraction)
# ═══════════════════════════════════════════════════════════════


def check_graph_has_edges(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check that the knowledge graph has edges (not empty)."""
    # Distinguish "stats unavailable" (collection failed upstream) from "0 edges"
    # (graph genuinely empty). Both fail the assertion but the actionable diagnosis
    # is very different — see I1 branch in validator.py.
    if not ctx.memory_stats:
        return AssertionResult(
            id=a.id, name=a.name, passed=False, severity=a.severity,
            expected=">0 graph edges",
            actual="stats unavailable",
            details="memory_stats empty — likely cold-start race or QA_STATS exception. Check backend log.",
        )
    edge_count = ctx.memory_stats.get("total_edges", 0) or ctx.memory_stats.get("edge_count", 0)
    passed = edge_count > 0
    return AssertionResult(
        id=a.id, name=a.name, passed=passed, severity=a.severity,
        expected=">0 graph edges",
        actual=f"{edge_count} edges",
        details="Graph has 0 edges — extraction may not be producing relations" if not passed else None,
    )


def check_cluster_health(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check that not all clusters are drifting."""
    nodes_by_lock_in = ctx.memory_stats.get("nodes_by_lock_in", {})
    total = sum(nodes_by_lock_in.values()) if nodes_by_lock_in else 0
    drifting = nodes_by_lock_in.get("drifting", 0)
    # Pass if no data yet, or if at least some nodes are above drifting
    if total == 0:
        passed = True
        actual = "No data yet"
    else:
        drifting_pct = drifting / total * 100
        passed = drifting_pct < 99.0
        actual = f"{drifting_pct:.1f}% drifting ({drifting}/{total})"
    return AssertionResult(
        id=a.id, name=a.name, passed=passed, severity=a.severity,
        expected="<99% drifting",
        actual=actual,
        details="Consolidation pipeline may not be running" if not passed else None,
    )


def check_node_type_diversity(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check that extraction produces diverse node types (not all FACTs)."""
    nodes_by_type = ctx.memory_stats.get("nodes_by_type", {})
    total = sum(nodes_by_type.values()) if nodes_by_type else 0
    facts = nodes_by_type.get("FACT", 0)
    if total == 0:
        passed = True
        actual = "No data yet"
    else:
        fact_pct = facts / total * 100
        passed = fact_pct < 95.0
        actual = f"{fact_pct:.1f}% FACTs ({facts}/{total})"
    return AssertionResult(
        id=a.id, name=a.name, passed=passed, severity=a.severity,
        expected="<95% FACTs",
        actual=actual,
        details="Extraction may be over-classifying as FACT" if not passed else None,
    )


def check_extraction_no_assistant_content(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check that extraction doesn't include assistant-generated content."""
    assistant_extracted = ctx.extraction_stats.get("assistant_turns_extracted", 0)
    passed = assistant_extracted == 0
    return AssertionResult(
        id=a.id, name=a.name, passed=passed, severity=a.severity,
        expected="0 assistant turns extracted",
        actual=f"{assistant_extracted} assistant turns",
        details="Auto-session may be leaking assistant content" if not passed else None,
    )


def check_extraction_backend_active(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check that the extraction backend is not silently dropping extractions."""
    backend_active = True
    if ctx.extraction_stats:
        backend = ctx.extraction_stats.get("backend", "unknown")
        extractions = ctx.extraction_stats.get("extractions_count", 0)
        turns = ctx.extraction_stats.get("turns_processed", 0)
        if backend == "local" and turns > 10 and extractions == 0:
            backend_active = False

    return AssertionResult(
        id=a.id, name=a.name, passed=backend_active, severity=a.severity,
        expected="Extraction backend producing results",
        actual="Active" if backend_active else "Backend appears dead — 0 extractions after 10+ turns",
        details="Local backend may not have a model loaded" if not backend_active else None,
    )


def check_extraction_objects_have_entities(ctx: InferenceContext, a: Assertion) -> AssertionResult:
    """Check that extracted objects include entity lists (not just content)."""
    empty_entity_ratio = 0.0
    if ctx.extraction_stats:
        total = ctx.extraction_stats.get("total_objects", 0)
        empty = ctx.extraction_stats.get("objects_without_entities", 0)
        if total > 0:
            empty_entity_ratio = empty / total

    passed = empty_entity_ratio < 0.8
    return AssertionResult(
        id=a.id, name=a.name, passed=passed, severity=a.severity,
        expected="< 80% objects without entities",
        actual=f"{empty_entity_ratio*100:.0f}% without entities",
        details="Extraction prompt may not be producing entity lists" if not passed else None,
    )


# ═══════════════════════════════════════════════════════════════
# DEFAULT ASSERTIONS
# ═══════════════════════════════════════════════════════════════

def get_default_assertions() -> list[Assertion]:
    """Return all built-in assertions."""
    return [
        # Personality
        Assertion(
            id="P1", name="Personality injected",
            description="System prompt includes personality (>1000 chars)",
            category="personality", severity="high",
            check_type="builtin", builtin_fn=check_personality_injected,
        ),
        Assertion(
            id="P2", name="Virtues loaded",
            description="Luna's virtues were loaded from memory",
            category="personality", severity="medium",
            check_type="builtin", builtin_fn=check_virtues_loaded,
        ),
        Assertion(
            id="P3", name="Voice injected",
            description="System prompt contains Luna's voice directives (via PromptAssembler)",
            category="personality", severity="high",
            check_type="builtin", builtin_fn=check_voice_injection,
        ),

        # Structural
        Assertion(
            id="S1", name="No code blocks",
            description="No ``` unless user asked for code",
            category="structural", severity="high",
            check_type="builtin", builtin_fn=check_no_code_blocks,
        ),
        Assertion(
            id="S2", name="No ASCII art",
            description="No box drawing or ASCII art patterns",
            category="structural", severity="high",
            check_type="builtin", builtin_fn=check_no_ascii_art,
        ),
        Assertion(
            id="S3", name="No mermaid diagrams",
            description="No ```mermaid blocks",
            category="structural", severity="high",
            check_type="builtin", builtin_fn=check_no_mermaid,
        ),
        Assertion(
            id="S4", name="No bullet lists",
            description="No excessive bullet points (Luna speaks naturally)",
            category="structural", severity="medium",
            check_type="builtin", builtin_fn=check_no_bullet_lists,
        ),
        Assertion(
            id="S5", name="Response length",
            description="Response between 20-5000 chars",
            category="structural", severity="medium",
            check_type="builtin", builtin_fn=check_response_length,
        ),

        Assertion(
            id="S6", name="No trailing question",
            description="Response must not end with a follow-up question",
            category="structural", severity="medium",
            check_type="builtin", builtin_fn=check_no_trailing_question,
        ),

        # Voice
        Assertion(
            id="V1", name="No Claude-isms",
            description="No banned Claude phrases",
            category="voice", severity="high",
            check_type="builtin", builtin_fn=check_no_claude_isms,
        ),
        Assertion(
            id="V2", name="No confabulation",
            description="No fabricated memory details when confidence is low",
            category="voice", severity="high",
            check_type="builtin", builtin_fn=check_no_confabulation_signals,
        ),

        # Flow
        Assertion(
            id="F1", name="Provider success",
            description="At least one provider returned a response",
            category="flow", severity="critical",
            check_type="builtin", builtin_fn=check_provider_success,
        ),
        Assertion(
            id="F2", name="No timeout",
            description="Response completed within 30s",
            category="flow", severity="high",
            check_type="builtin", builtin_fn=check_no_timeout,
        ),

        # Integration
        Assertion(
            id="I1", name="Graph has edges",
            description="Knowledge graph contains at least one edge",
            category="integration", severity="high",
            check_type="builtin", builtin_fn=check_graph_has_edges,
        ),
        Assertion(
            id="I2", name="Cluster health",
            description="Not all clusters are drifting",
            category="integration", severity="medium",
            check_type="builtin", builtin_fn=check_cluster_health,
        ),
        Assertion(
            id="I3", name="Node type diversity",
            description="Extraction produces diverse node types",
            category="integration", severity="medium",
            check_type="builtin", builtin_fn=check_node_type_diversity,
        ),
        Assertion(
            id="I4", name="No assistant extraction",
            description="Extraction excludes assistant-generated content",
            category="integration", severity="high",
            check_type="builtin", builtin_fn=check_extraction_no_assistant_content,
        ),

        # Extraction
        Assertion(
            id="E1", name="Extraction backend active",
            description="Backend should produce extractions (not silently dropping)",
            category="integration", severity="high",
            check_type="builtin", builtin_fn=check_extraction_backend_active,
        ),
        Assertion(
            id="E2", name="Extractions include entities",
            description="Extracted objects should include entity lists for graph building",
            category="integration", severity="medium",
            check_type="builtin", builtin_fn=check_extraction_objects_have_entities,
        ),
    ]
