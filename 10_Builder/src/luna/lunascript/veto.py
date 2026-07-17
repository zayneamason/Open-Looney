"""LunaScript veto — structural validation of delegated responses.

Checks if a delegated response matches Luna's response geometry.
Rejects if not. No LLM calls. Pure structural checks.
"""

import re
from dataclasses import dataclass, field

from .features import _split_sentences, feat_contraction_rate, feat_list_usage
from .features import feat_sentence_length_variance
from .baselines import BaselineStats


@dataclass
class VetoResult:
    passed: bool
    violations: list[str]
    metrics: dict[str, float]
    quality_score: float  # 0-1, feeds back into learning


# Default forbidden phrases — generic LLM filler that Luna never uses
DEFAULT_FORBIDDEN = [
    "I'd be happy to",
    "Certainly!",
    "As an AI",
    "I don't have personal",
    "Let me help you with",
    "I appreciate you",
    "That's a great question",
]


def veto_check(
    response_text: str,
    geometry: dict,
    baselines: dict[str, BaselineStats],
    forbidden_phrases: list[str] = None,
) -> VetoResult:
    """Check if response matches Luna's structural geometry.

    Returns VetoResult with pass/fail, violations list, and quality score.
    """
    violations = []
    metrics = {}
    forbidden = forbidden_phrases if forbidden_phrases is not None else DEFAULT_FORBIDDEN

    sentences = _split_sentences(response_text)
    words = re.findall(r"\b\w+(?:['']\w+)?\b", response_text)
    sentence_count = len(sentences)

    metrics["sentence_count"] = sentence_count
    metrics["word_count"] = len(words)

    # 1. Sentence count within geometry bounds
    max_sent = geometry.get("max_sent", 20)
    if sentence_count > max_sent:
        violations.append(f"too_many_sentences: {sentence_count} > {max_sent}")

    # 2. Question required check
    if geometry.get("question_req", False):
        has_question = any(s.rstrip().endswith("?") for s in sentences)
        if not has_question:
            violations.append("missing_required_question")
        metrics["has_question"] = 1.0 if has_question else 0.0

    # 2b. Trailing question forbidden (default: True)
    if geometry.get("question_forbid", True) and not geometry.get("question_req", False):
        if sentences and sentences[-1].rstrip().endswith("?"):
            violations.append("trailing_question_forbidden")
            metrics["trailing_question"] = 1.0

    # 3. List usage check — if geometry prohibits tangents, lists should be minimal
    list_count = feat_list_usage(response_text, words)
    metrics["list_usage"] = list_count
    if not geometry.get("tangent", True) and list_count > 0.5:
        violations.append(f"excessive_lists: {list_count:.2f} markers/sentence")

    # 4. Contraction rate floor — Luna almost always uses contractions
    contraction_rate = feat_contraction_rate(response_text, words)
    metrics["contraction_rate"] = contraction_rate
    bl = baselines.get("contraction_rate")
    if bl and contraction_rate < bl.mean - 2.0 * bl.stddev:
        violations.append(f"low_contraction_rate: {contraction_rate:.2f} (floor: {bl.mean - 2.0 * bl.stddev:.2f})")

    # 5. Forbidden phrases
    lower_text = response_text.lower()
    for phrase in forbidden:
        if phrase.lower() in lower_text:
            violations.append(f"forbidden_phrase: '{phrase}'")

    # 6. Sentence length variance — catches Claude's uniform length
    if len(sentences) >= 3:
        slv = feat_sentence_length_variance(response_text, words)
        metrics["sentence_length_variance"] = slv
        bl_slv = baselines.get("sentence_length_variance")
        if bl_slv and bl_slv.stddev > 0:
            z = (slv - bl_slv.mean) / bl_slv.stddev
            if z < -2.0:
                violations.append(f"uniform_sentence_length: variance {slv:.1f} (z={z:.1f})")

    # Compute quality score (1.0 = perfect, degrades per violation)
    quality = 1.0
    quality -= len(violations) * 0.15
    quality = max(0.0, quality)
    metrics["violation_count"] = len(violations)

    return VetoResult(
        passed=len(violations) == 0,
        violations=violations,
        metrics=metrics,
        quality_score=quality,
    )


def build_retry_prompt(violations: list[str], geometry: dict) -> str:
    """Build a tighter constraint prompt after a veto failure."""
    lines = ["## STRICT VOICE CONSTRAINTS (retry — previous response was rejected)"]
    lines.append("The previous response did not match Luna's voice. Fix these:")

    for v in violations:
        if "too_many_sentences" in v:
            lines.append(f"- Keep response under {geometry.get('max_sent', 8)} sentences.")
        elif "missing_required_question" in v:
            lines.append("- End your response with a question.")
        elif "excessive_lists" in v:
            lines.append("- Do NOT use bullet points or numbered lists.")
        elif "low_contraction_rate" in v:
            lines.append("- Use contractions (don't, can't, won't — not 'do not', 'cannot').")
        elif "trailing_question_forbidden" in v:
            lines.append("- Do NOT end your response with a question. Just share and stop.")
        elif "forbidden_phrase" in v:
            phrase = v.split("'")[1] if "'" in v else ""
            lines.append(f"- Do NOT use the phrase '{phrase}'.")
        elif "uniform_sentence_length" in v:
            lines.append("- Vary your sentence lengths. Mix short and long sentences.")

    lines.append("- Be natural and conversational, not formal or robotic.")
    return "\n".join(lines)
