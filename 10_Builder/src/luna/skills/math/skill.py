"""MathSkill — symbolic math via sympy."""

import re
import logging
from ..base import Skill, SkillResult

logger = logging.getLogger(__name__)

# Verb → sympy operation mapping
_VERB_PATTERNS = [
    (r"\b(solve)\b", "solve"),
    (r"\b(integrate|integral)\b", "integrate"),
    (r"\b(differentiate|derivative|diff)\b", "diff"),
    (r"\b(factor)\b", "factor"),
    (r"\b(simplify)\b", "simplify"),
    (r"\b(expand)\b", "expand"),
]


def _extract_verb(query: str) -> str:
    """Extract the math operation verb from the query."""
    q = query.lower()
    for pattern, verb in _VERB_PATTERNS:
        if re.search(pattern, q):
            return verb
    return "simplify"  # default


def _extract_expression(query: str) -> str:
    """Extract the math expression from a natural language query."""
    # Try to find expression after the verb
    # e.g. "solve x^2 - 5x + 6 = 0" → "x^2 - 5x + 6"
    # e.g. "integrate sin(x) dx" → "sin(x)"

    # Remove common preamble words
    cleaned = re.sub(
        r"^(can you |please |could you )?(solve|factor|simplify|expand|integrate|differentiate|compute|calculate)\s+",
        "", query, flags=re.IGNORECASE,
    ).strip()

    # Handle "= 0" equations (convert to expression = 0 form for solve)
    cleaned = re.sub(r"\s*=\s*0\s*$", "", cleaned)

    # Remove trailing "dx", "dy" etc for integration
    cleaned = re.sub(r"\s+d[a-z]\s*$", "", cleaned, flags=re.IGNORECASE)

    # Remove quotes
    cleaned = cleaned.strip("\"'`")

    return cleaned if cleaned else query


class MathSkill(Skill):
    name = "math"
    description = "Symbolic math computation via sympy"
    triggers = [
        r"\b(solve|factor|simplify|expand|integrate|differentiate|derivative|integral)\b",
        r"\b(equation|polynomial|eigenvalue|matrix determinant)\b",
        r"\b(calculate|compute)\b.{0,30}\b(exact|symbolic|algebraic)\b",
    ]

    def __init__(self, config: dict = None):
        self._config = config or {}
        self._max_expr_len = self._config.get("max_expression_length", 500)

    def is_available(self) -> bool:
        try:
            import sympy  # noqa: F401
            return True
        except ImportError:
            return False

    async def execute(self, query: str, context: dict) -> SkillResult:
        try:
            import sympy
            from sympy.parsing.sympy_parser import (
                parse_expr, standard_transformations,
                implicit_multiplication_application, convert_xor,
            )
        except ImportError:
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error="sympy not installed",
            )

        expr_str = _extract_expression(query)
        if len(expr_str) > self._max_expr_len:
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error="Expression too long",
            )

        verb = _extract_verb(query)

        try:
            transformations = standard_transformations + (
                implicit_multiplication_application, convert_xor,
            )
            expr = parse_expr(expr_str, transformations=transformations)

            ops = {
                "solve": lambda e: sympy.solve(e),
                "integrate": lambda e: sympy.integrate(e),
                "diff": lambda e: sympy.diff(e),
                "factor": lambda e: sympy.factor(e),
                "simplify": lambda e: sympy.simplify(e),
                "expand": lambda e: sympy.expand(e),
            }
            result = ops.get(verb, ops["simplify"])(expr)
            latex_str = sympy.latex(result)
            result_str = str(result)

            return SkillResult(
                success=True,
                skill_name=self.name,
                result=result,
                result_str=result_str,
                latex=latex_str,
                data={
                    "latex": latex_str,
                    "result_str": result_str,
                    "operation": verb,
                    "input": expr_str,
                },
            )
        except Exception as e:
            logger.warning("[MATH] Parse/compute failed for query %r: %s", query[:200], e)
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error=str(e),
            )

    def narration_hint(self, result: SkillResult) -> str:
        return (
            "The exact symbolic result is shown below in a widget. "
            "Narrate it conversationally. Mention what operation was performed. "
            "Don't repeat the formula in words."
        )
