"""LogicSkill — propositional logic via sympy.logic."""

import re
import logging
from ..base import Skill, SkillResult

logger = logging.getLogger(__name__)

# Map natural language variable names to sympy symbols
_VAR_PATTERN = re.compile(r"\b([A-Z])\b")


def _parse_logic_expr(query: str):
    """Parse a boolean expression from natural language query."""
    import sympy
    from sympy.logic.boolalg import And, Or, Not, Xor

    # Extract the expression part (after "truth table for", "prove", etc.)
    cleaned = re.sub(
        r"^(can you |please )?(truth table for|truth table of|prove|disprove|check|is|show)\s+",
        "", query, flags=re.IGNORECASE,
    ).strip()

    # Normalize operators
    cleaned = re.sub(r"\bAND\b", "&", cleaned)
    cleaned = re.sub(r"\bOR\b", "|", cleaned)
    cleaned = re.sub(r"\bNOT\b", "~", cleaned)
    cleaned = re.sub(r"\bXOR\b", "^", cleaned)
    cleaned = re.sub(r"\bNAND\b", "~&", cleaned)  # will need special handling
    cleaned = re.sub(r"\bNOR\b", "~|", cleaned)
    cleaned = re.sub(r"\bIMPLIES\b", ">>", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(a |an |the )\b", "", cleaned, flags=re.IGNORECASE)

    # Find all single uppercase letter variables
    var_names = sorted(set(_VAR_PATTERN.findall(cleaned)))
    if not var_names:
        return None, []

    symbols = {name: sympy.Symbol(name) for name in var_names}

    # Try to parse with sympy
    try:
        expr = sympy.sympify(cleaned, locals=symbols)
        return expr, [symbols[n] for n in var_names]
    except Exception:
        pass

    return None, []


def _extract_verb(query: str) -> str:
    """Extract the logic operation from the query."""
    q = query.lower()
    if "truth table" in q:
        return "truth_table"
    if "tautology" in q or "always true" in q:
        return "tautology"
    if "contradiction" in q or "always false" in q:
        return "contradiction"
    if "satisfiable" in q or "satisfi" in q:
        return "satisfiable"
    if "simplify" in q:
        return "simplify"
    if "prove" in q or "disprove" in q:
        return "prove"
    return "truth_table"  # default


def _build_truth_table(expr, variables):
    """Build a truth table for the expression."""
    import itertools

    headers = [str(v) for v in variables] + [str(expr)]
    rows = []

    for values in itertools.product([True, False], repeat=len(variables)):
        subs = dict(zip(variables, values))
        try:
            result = bool(expr.subs(subs))
        except Exception:
            result = None
        rows.append(list(values) + [result])

    return headers, rows


class LogicSkill(Skill):
    name = "logic"
    description = "Propositional logic via sympy.logic"
    triggers = [
        r"\b(truth table|tautology|contradiction|satisfiable|entails)\b",
        r"\b(prove|disprove).{0,30}\b(implies|therefore|follows)\b",
        r"\b(AND|OR|NOT|XOR)\b.{0,30}\b(expression|formula)\b",
    ]

    def __init__(self, config: dict = None):
        self._config = config or {}
        self._max_variables = self._config.get("max_variables", 8)

    def is_available(self) -> bool:
        try:
            import sympy  # noqa: F401
            return True
        except ImportError:
            return False

    async def execute(self, query: str, context: dict) -> SkillResult:
        try:
            import sympy
            from sympy import satisfiable
        except ImportError:
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error="sympy not installed",
            )

        expr, variables = _parse_logic_expr(query)
        if expr is None:
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error="Could not parse logic expression",
            )

        if len(variables) > self._max_variables:
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error=f"Too many variables ({len(variables)} > {self._max_variables})",
            )

        verb = _extract_verb(query)

        try:
            if verb == "truth_table":
                headers, rows = _build_truth_table(expr, variables)
                # Check if tautology or contradiction
                result_col = [r[-1] for r in rows]
                if all(result_col):
                    verdict = "tautology"
                elif not any(result_col):
                    verdict = "contradiction"
                else:
                    verdict = "satisfiable"

                result_str = f"Truth table for {expr} ({len(rows)} rows) — {verdict}"
                return SkillResult(
                    success=True, skill_name=self.name,
                    result=rows, result_str=result_str,
                    data={
                        "headers": headers,
                        "rows": rows,
                        "verdict": verdict,
                        "expression": str(expr),
                    },
                )

            elif verb == "tautology":
                is_taut = satisfiable(~expr) == False  # noqa: E712
                verdict = "tautology" if is_taut else "not a tautology"
                return SkillResult(
                    success=True, skill_name=self.name,
                    result=is_taut, result_str=f"{expr} is {verdict}",
                    data={
                        "headers": [str(v) for v in variables] + [str(expr)],
                        "rows": [],
                        "verdict": verdict,
                        "expression": str(expr),
                    },
                )

            elif verb == "contradiction":
                is_contra = satisfiable(expr) == False  # noqa: E712
                verdict = "contradiction" if is_contra else "not a contradiction"
                return SkillResult(
                    success=True, skill_name=self.name,
                    result=is_contra, result_str=f"{expr} is {verdict}",
                    data={
                        "headers": [str(v) for v in variables] + [str(expr)],
                        "rows": [],
                        "verdict": verdict,
                        "expression": str(expr),
                    },
                )

            elif verb == "satisfiable":
                sat = satisfiable(expr)
                is_sat = sat != False  # noqa: E712
                verdict = "satisfiable" if is_sat else "unsatisfiable"
                result_str = f"{expr} is {verdict}"
                if is_sat and isinstance(sat, dict):
                    result_str += f" (e.g. {sat})"
                return SkillResult(
                    success=True, skill_name=self.name,
                    result=sat, result_str=result_str,
                    data={
                        "headers": [str(v) for v in variables] + [str(expr)],
                        "rows": [],
                        "verdict": verdict,
                        "expression": str(expr),
                    },
                )

            elif verb == "simplify":
                simplified = sympy.simplify_logic(expr)
                return SkillResult(
                    success=True, skill_name=self.name,
                    result=simplified,
                    result_str=f"Simplified: {simplified}",
                    data={
                        "headers": [],
                        "rows": [],
                        "verdict": None,
                        "expression": str(simplified),
                    },
                )

            else:
                # Default: truth table
                headers, rows = _build_truth_table(expr, variables)
                result_col = [r[-1] for r in rows]
                verdict = "tautology" if all(result_col) else "contradiction" if not any(result_col) else "satisfiable"
                return SkillResult(
                    success=True, skill_name=self.name,
                    result=rows, result_str=f"Truth table for {expr} — {verdict}",
                    data={"headers": headers, "rows": rows, "verdict": verdict, "expression": str(expr)},
                )

        except Exception as e:
            logger.warning("[LOGIC] Execution failed for query %r: %s", query[:200], e)
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error=str(e),
            )

    def narration_hint(self, result: SkillResult) -> str:
        return (
            "A truth table or logic result is shown below in a widget. "
            "Narrate the verdict briefly. Don't list the rows."
        )
