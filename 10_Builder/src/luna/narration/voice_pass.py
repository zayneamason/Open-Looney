"""Voice-pass QA check on narration output.

Spec: `Docs/Design/LunaAssistantMapping/SPEC_Narration_Voice_Pass.md`.

`voice_pass = True` requires: no forbidden phrase AND at least one DNA signal.
A failed pass is logged for QA — output is NOT retried or blocked.
"""
import re
from dataclasses import dataclass
from typing import Optional

FORBIDDEN_OPENER_PATTERNS = [
    "certainly!", "certainly,", "of course!", "of course,",
    "great question", "that's a great", "i'd be happy to", "i'm happy to",
    "absolutely!", "absolutely,", "sure thing",
]

FORBIDDEN_FULL_PATTERNS = [
    "as an ai", "as a language model", "as an llm",
    "i'm just an ai", "i don't have feelings", "i don't have personal opinions",
    "i cannot form opinions", "i should note that i am",
    "it's important to note that i",
    "please note that", "it's worth noting that",
    "i want to be clear that", "i must emphasize",
    "as always, please", "feel free to ask",
    "let me know if you need", "hope this helps",
    "i hope that answers",
    "thank you for sharing", "thank you for asking",
    "that's an interesting", "that's a fascinating",
    "i understand your concern", "i understand why you",
]

DNA_ANNOTATION_MARKERS = [
    "↳ from:", "↳ absent", "[· ", "[·retrieved", "[·inferred",
    "[·absent", "▶ new thread", "· cached ·", "↩ resumed",
    "◎ resolved", "◎ done", "· last time:", "before i go further —",
    "draft ·",
]

DNA_PROVENANCE_MARKERS = [
    "from memory ·", "from: node_", "from: session",
    "this session ·", "session −",
]

DNA_INFERENCE_MARKERS = [
    "[·inferred]", "· inferred", "synthesized from", "not a single source",
]

DNA_ABSENCE_MARKERS = [
    "[·absent]", "· absent", "nothing in the graph",
    "searched: embedding", "0 results", "no match",
]

POSITION_DECLARATION_PATTERN = re.compile(
    r"(the right call is|the issue is|the problem is|the answer is)"
    r"(?!.{0,40}\b(but|however|although|while|that said)\b)",
    re.IGNORECASE,
)

# `*action descriptor*` block on its own line — Luna's stage-direction opener.
# Bounded body length avoids long emphasized prose blocks; trailing `[^\n]*\n`
# allows the model to follow the closing `*` with decorations like a 🔒 emoji
# before the newline (observed in 3/25 live re-bench probes; the labeled-50
# set had zero such cases). Required closing newline avoids inline `*emphasis*`
# in mid-sentence positions.
ACTION_OPENER_PATTERN = re.compile(
    r"^\*[A-Za-z][^*\n]{1,80}\*[^\n]*\n",
)

# Em-dash / en-dash chain: a dash with a short fragment (≤ 8 words) on EACH
# side, where each side is bounded by the nearest clause separator.
# Calibrated against the D.5 hand-labeled set (91% recall, 1.9% FP across 54
# samples). A single em-dash inside a long clause is rejected — that is what
# distinguishes "chain cadence" from incidental dash usage.
_EM_DASH_RE = re.compile(r"[—–]")
_EM_DASH_CHAIN_PRE_RE = re.compile(r"[.,?!\n—–]([^.,?!\n—–]*)$")
_EM_DASH_CHAIN_POST_RE = re.compile(r"^([^.,?!\n—–]*)[.,?!\n—–]")


def _has_em_dash_chain(text: str, max_words: int = 8) -> bool:
    for match in _EM_DASH_RE.finditer(text):
        idx = match.start()
        pre = text[:idx]
        post = text[idx + 1 :]
        pre_match = _EM_DASH_CHAIN_PRE_RE.search(pre)
        pre_frag = pre_match.group(1) if pre_match else pre
        post_match = _EM_DASH_CHAIN_POST_RE.search(post)
        post_frag = post_match.group(1) if post_match else post
        pre_words = len(pre_frag.split())
        post_words = len(post_frag.split())
        if 0 < pre_words <= max_words and 0 < post_words <= max_words:
            return True
    return False


# Article-dropped domain-noun openers — `director is clean.`, `detector validates.`
# The set is the engine's own vocabulary plus `detector` (added 2026-05-01 from
# D.5 sample d5_008). `prompt` is intentionally excluded — d5_012 opens with
# "prompt first." but read as off-cadence in hand-labeling.
DOMAIN_NOUNS_NO_ARTICLE = frozenset({
    "director", "detector", "signal", "layer", "system", "pipeline",
    "graph", "matrix", "engine", "actor", "scribe", "librarian",
    "router", "tick", "model", "node",
})


def _strip_action_opener(text: str) -> str:
    return ACTION_OPENER_PATTERN.sub("", text.strip(), count=1).strip()


def _opens_with_domain_noun_no_article(text: str) -> bool:
    body = _strip_action_opener(text)
    if not body:
        return False
    first_sentence = re.split(r"(?<=[.!?])\s+", body)[0]
    words = first_sentence.split()
    if not words:
        return False
    first = words[0].lower().rstrip(",.!?:;\"'")
    return first in DOMAIN_NOUNS_NO_ARTICLE


@dataclass
class VoicePassResult:
    passed: bool
    forbidden_matches: list[str]
    dna_signals_found: list[str]
    failure_reason: Optional[str]


def check_voice_pass(text: str) -> VoicePassResult:
    """
    Evaluate whether a narration layer output passes the voice QA check.

    Returns VoicePassResult. The `passed` field is what NarrationResult.voice_pass
    should be set to.

    Empty or whitespace-only text always fails — narration produced nothing usable.
    """
    if not text or not text.strip():
        return VoicePassResult(
            passed=False,
            forbidden_matches=[],
            dna_signals_found=[],
            failure_reason="Empty or whitespace-only narration output",
        )

    text_lower = text.lower()
    opener = text_lower[:120]

    forbidden_matches = []

    for pattern in FORBIDDEN_OPENER_PATTERNS:
        if pattern in opener:
            forbidden_matches.append(f"opener:{pattern}")

    for pattern in FORBIDDEN_FULL_PATTERNS:
        if pattern in text_lower:
            forbidden_matches.append(f"full:{pattern}")

    dna_signals_found = []

    for marker in DNA_ANNOTATION_MARKERS:
        if marker in text:  # case-sensitive — these are symbols
            dna_signals_found.append(f"annotation:{marker.strip()}")
            break

    for marker in DNA_PROVENANCE_MARKERS:
        if marker in text_lower:
            dna_signals_found.append(f"provenance:{marker.strip()}")
            break

    if POSITION_DECLARATION_PATTERN.search(text):
        dna_signals_found.append("position_declaration")

    if ACTION_OPENER_PATTERN.match(text.strip()):
        dna_signals_found.append("structural:action_opener")

    if _has_em_dash_chain(text):
        dna_signals_found.append("structural:em_dash_chain")

    if _opens_with_domain_noun_no_article(text):
        dna_signals_found.append("structural:domain_noun_no_article")

    for marker in DNA_INFERENCE_MARKERS:
        if marker in text_lower:
            dna_signals_found.append(f"inference:{marker.strip()}")
            break

    for marker in DNA_ABSENCE_MARKERS:
        if marker in text_lower:
            dna_signals_found.append(f"absence:{marker.strip()}")
            break

    if not dna_signals_found:
        first_sentences = re.split(r"(?<=[.!?])\s+", text.strip())[:3]
        for sentence in first_sentences:
            words = sentence.strip().split()
            if (words and words[0].lower() == "you" and
                    len(words) > 2 and
                    words[1].lower() not in ("might", "could", "may", "should", "would")):
                dna_signals_found.append("structural:direct_address")
                break
            if len(words) <= 12 and sentence.strip().endswith("."):
                dna_signals_found.append("structural:fragment_assertion")
                break
            if (words and words[0].lower() in ("the",) and len(words) > 1 and
                    words[1].lower() in (
                        "system", "class", "signal", "latency", "engine",
                        "router", "layer", "tick", "actor", "matrix",
                        "pipeline", "node", "graph", "model",
                    )):
                dna_signals_found.append("structural:domain_noun_opener")
                break

    has_forbidden = len(forbidden_matches) > 0
    has_dna = len(dna_signals_found) > 0

    if has_forbidden:
        failure_reason = f"Forbidden patterns: {forbidden_matches}"
    elif not has_dna:
        failure_reason = "No DNA signals detected — output reads as generic assistant"
    else:
        failure_reason = None

    return VoicePassResult(
        passed=not has_forbidden and has_dna,
        forbidden_matches=forbidden_matches,
        dna_signals_found=dna_signals_found,
        failure_reason=failure_reason,
    )
