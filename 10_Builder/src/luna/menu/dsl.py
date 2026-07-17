"""Exit-condition DSL for menu task DM runtimes.

See `Docs/Design/LunaAssistantMapping/SPEC_Exit_Condition_DSL.md`.
"""
import ast

from simpleeval import InvalidExpression, NameNotDefined, SimpleEval


class ExitConditionError(Exception):
    """Raised when an exit condition cannot be evaluated."""
    pass


class ExitConditionEvaluator:
    """
    Evaluates exit condition DSL expressions against a DM runtime context.

    Usage:
        evaluator = ExitConditionEvaluator()
        satisfied = evaluator.check("sources_cited >= 3 and summary_written", ctx)
    """

    ALLOWED_NAMES = {
        # Integer counters
        "sources_cited",
        "all_unclassified_turns",
        "orphan_entity_count",
        "nodes_written",
        "turns_processed",
        "hops_completed",
        # Boolean flags
        "summary_written",
        "max_hops_satisfied",
        "positions_retrieved",
        "supersession_chain_resolved",
        "quest_created",
        "quest_id_returned",
        "answered",
    }

    def check(self, expression: str, context: dict) -> bool:
        """
        Evaluate an exit condition expression against a context dict.

        Args:
            expression: DSL string from menu.yaml exit_condition field
            context: Dict of variable name → current value, populated by DM runtime

        Returns:
            True if exit condition is satisfied, False otherwise

        Raises:
            ExitConditionError: If expression contains unknown variables,
                                invalid syntax, or non-boolean result
        """
        unknown = set(context.keys()) - self.ALLOWED_NAMES
        if unknown:
            raise ExitConditionError(
                f"Unregistered context variables: {unknown}. "
                f"Register them in ExitConditionEvaluator.ALLOWED_NAMES."
            )

        evaluator = SimpleEval()
        evaluator.names = context

        try:
            result = evaluator.eval(expression)
        except NameNotDefined as e:
            raise ExitConditionError(
                f"Unknown variable in exit condition '{expression}': {e}. "
                f"Is it registered in ALLOWED_NAMES and populated by the DM?"
            ) from e
        except InvalidExpression as e:
            raise ExitConditionError(
                f"Invalid exit condition expression '{expression}': {e}"
            ) from e

        if not isinstance(result, bool):
            raise ExitConditionError(
                f"Exit condition '{expression}' evaluated to non-boolean: "
                f"{result!r} ({type(result).__name__}). "
                f"Exit conditions must resolve to True or False."
            )

        return result

    def validate_at_load(self, expression: str) -> list[str]:
        """
        Validate an exit condition expression at menu load time (no context needed).
        Returns list of variable names referenced in the expression.
        Used by MenuRegistry to catch authoring errors before runtime.

        Returns:
            List of variable names found in expression

        Raises:
            ExitConditionError: If expression syntax is invalid or references
                                unknown variables
        """
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as e:
            raise ExitConditionError(
                f"Syntax error in exit condition '{expression}': {e}"
            ) from e

        names_found = [
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        ]

        unknown = set(names_found) - self.ALLOWED_NAMES
        if unknown:
            raise ExitConditionError(
                f"Unknown variables in exit condition '{expression}': {unknown}. "
                f"Register them in ExitConditionEvaluator.ALLOWED_NAMES before use."
            )

        return names_found
