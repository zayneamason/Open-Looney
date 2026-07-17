"""LunaScript feature extractors — 21 linguistic features, zero LLM calls.

Each feat_* function takes (text: str, words: list[str]) -> float.
ALL_FEATURES maps feature name -> function for the registry.
"""

import re
import math


# ── Helpers ──────────────────────────────────────────────────────────

def _split_sentences(text: str) -> list[str]:
    """Split text into sentences. Handles abbreviations and ellipses."""
    cleaned = re.sub(r"\.{2,}", "…", text)
    cleaned = re.sub(r"([.!?])\s+", r"\1\n", cleaned)
    cleaned = re.sub(r"([.!?])$", r"\1\n", cleaned)
    sentences = [s.strip() for s in cleaned.split("\n") if s.strip()]
    return sentences if sentences else [text.strip()] if text.strip() else []


_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"
    "\U0001F900-\U0001F9FF"  # supplemental
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D"
    "\U00002B50"
    "]+",
    flags=re.UNICODE,
)

_CONTRACTION_PAIRS = [
    (r"\bi am\b", "i'm"), (r"\bi will\b", "i'll"), (r"\bi would\b", "i'd"),
    (r"\bi have\b", "i've"), (r"\bwe are\b", "we're"), (r"\bwe will\b", "we'll"),
    (r"\bwe have\b", "we've"), (r"\bthey are\b", "they're"),
    (r"\bthey will\b", "they'll"), (r"\bthey have\b", "they've"),
    (r"\byou are\b", "you're"), (r"\byou will\b", "you'll"),
    (r"\byou have\b", "you've"), (r"\byou would\b", "you'd"),
    (r"\bhe is\b", "he's"), (r"\bshe is\b", "she's"), (r"\bit is\b", "it's"),
    (r"\bthat is\b", "that's"), (r"\bwhat is\b", "what's"),
    (r"\bwho is\b", "who's"), (r"\bthere is\b", "there's"),
    (r"\bdo not\b", "don't"), (r"\bdoes not\b", "doesn't"),
    (r"\bdid not\b", "didn't"), (r"\bwill not\b", "won't"),
    (r"\bwould not\b", "wouldn't"), (r"\bcould not\b", "couldn't"),
    (r"\bshould not\b", "shouldn't"), (r"\bcannot\b", "can't"),
    (r"\bcan not\b", "can't"), (r"\bis not\b", "isn't"),
    (r"\bare not\b", "aren't"), (r"\bwas not\b", "wasn't"),
    (r"\bwere not\b", "weren't"), (r"\bhas not\b", "hasn't"),
    (r"\bhave not\b", "haven't"), (r"\bhad not\b", "hadn't"),
    (r"\blet us\b", "let's"),
]

_CONTRACTION_RE = re.compile(
    r"\b\w+(?:'(?:t|s|re|ve|ll|d|m))\b", re.IGNORECASE
)


# ── Feature Functions ────────────────────────────────────────────────

def feat_question_density(text: str, words: list[str]) -> float:
    sentences = _split_sentences(text)
    if not sentences:
        return 0.0
    q_count = sum(1 for s in sentences if s.rstrip().endswith("?"))
    return q_count / len(sentences)


def feat_avg_word_length(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    return sum(len(w) for w in words) / len(words)


def feat_closing_question(text: str, words: list[str]) -> float:
    sentences = _split_sentences(text)
    if not sentences:
        return 0.0
    return 1.0 if sentences[-1].rstrip().endswith("?") else 0.0


def feat_exploratory_ratio(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    markers = {"wonder", "wondering", "interesting", "curious", "fascinated",
               "hmm", "huh"}
    lower_text = text.lower()
    count = sum(1 for w in words if w.lower() in markers)
    count += len(re.findall(r"\bwhat if\b", lower_text, re.IGNORECASE))
    return count / len(words)


def feat_opening_reaction(text: str, words: list[str]) -> float:
    stripped = text.lstrip("*_ ").lower()
    openers = ["oh ", "oh,", "ohh", "hmm", "hm,", "hm ", "okay so",
               "ok so", "wait", "ooh", "oooh", "ahh", "well,", "well "]
    return 1.0 if any(stripped.startswith(o) for o in openers) else 0.0


def feat_emoji_density(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    emoji_count = len(_EMOJI_RE.findall(text))
    return emoji_count / len(words)


def feat_list_usage(text: str, words: list[str]) -> float:
    sentences = _split_sentences(text)
    if not sentences:
        return 0.0
    list_markers = len(re.findall(r"^[\s]*[-•*]\s", text, re.MULTILINE))
    list_markers += len(re.findall(r"^[\s]*\d+[.)]\s", text, re.MULTILINE))
    return list_markers / len(sentences)


def feat_slang_ratio(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    slang = {"yo", "yeah", "yep", "nah", "kinda", "gonna", "wanna",
             "gotta", "lemme", "dunno", "nope", "hella", "dope", "cuz",
             "tbh", "idk", "imo", "lol", "omg", "bruh"}
    count = sum(1 for w in words if w.lower() in slang)
    return count / len(words)


def feat_filler_ratio(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    fillers = {"basically", "essentially", "just", "like", "literally",
               "honestly", "actually", "really", "pretty", "sort", "kind"}
    count = sum(1 for w in words if w.lower() in fillers)
    return count / len(words)


def feat_we_ratio(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    we_words = {"we", "us", "our", "ours", "ourselves"}
    lower_text = text.lower()
    count = sum(1 for w in words if w.lower() in we_words)
    count += len(re.findall(r"\blet's\b", lower_text))
    return count / len(words)


def feat_first_person_ratio(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    fp = {"i", "me", "my", "mine", "myself"}
    count = sum(1 for w in words if w.lower() in fp)
    return count / len(words)


def feat_you_ratio(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    you_words = {"you", "your", "yours", "yourself", "yourselves"}
    count = sum(1 for w in words if w.lower() in you_words)
    return count / len(words)


def feat_conditional_ratio(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    conds = {"would", "could", "should", "might"}
    count = sum(1 for w in words if w.lower() in conds)
    return count / len(words)


def feat_contraction_rate(text: str, words: list[str]) -> float:
    lower = text.lower()
    opportunities = 0
    for pattern, _ in _CONTRACTION_PAIRS:
        opportunities += len(re.findall(pattern, lower))
    contractions_found = len(_CONTRACTION_RE.findall(text))
    total = opportunities + contractions_found
    if total == 0:
        return 1.0  # no opportunities = vacuously contracted
    return contractions_found / total


def feat_tangent_markers(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    markers = {"actually", "wait", "sidebar", "tangent", "incidentally"}
    count = sum(1 for w in words if w.lower() in markers)
    count += text.count("—")
    count += text.count(" -- ")
    return count / len(words)


def feat_hedge_ratio(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    hedges = {"perhaps", "maybe", "possibly", "arguably", "potentially",
              "conceivably", "supposedly"}
    count = sum(1 for w in words if w.lower() in hedges)
    return count / len(words)


def feat_sentence_length_variance(text: str, words: list[str]) -> float:
    sentences = _split_sentences(text)
    if len(sentences) < 2:
        return 0.0
    lengths = [len(s.split()) for s in sentences]
    mean = sum(lengths) / len(lengths)
    variance = sum((l - mean) ** 2 for l in lengths) / len(lengths)
    return variance


def feat_emphasis_density(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    emphasis_words = {"honestly", "genuinely", "truly", "absolutely",
                      "definitely", "seriously", "totally"}
    count = sum(1 for w in words if w.lower() in emphasis_words)
    count += text.count("!")
    count += text.count("...")
    count += text.count("…")
    return count / len(words)


def feat_passive_ratio(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    passive_patterns = re.findall(
        r"\b(?:was|were|been|being|is|are|get|got|gets)\s+\w+(?:ed|en|t)\b",
        text, re.IGNORECASE,
    )
    sentences = _split_sentences(text)
    if not sentences:
        return 0.0
    return len(passive_patterns) / len(sentences)


def feat_formal_vocab_ratio(text: str, words: list[str]) -> float:
    if not words:
        return 0.0
    formal = {"therefore", "however", "furthermore", "moreover", "consequently",
              "nevertheless", "notwithstanding", "henceforth", "whereby",
              "therein", "herein", "accordingly", "thus", "hence"}
    count = sum(1 for w in words if w.lower() in formal)
    return count / len(words)


def feat_avg_sentence_length(text: str, words: list[str]) -> float:
    sentences = _split_sentences(text)
    if not sentences:
        return 0.0
    lengths = [len(s.split()) for s in sentences]
    return sum(lengths) / len(lengths)


# ── Registry ─────────────────────────────────────────────────────────

ALL_FEATURES: dict[str, callable] = {
    "question_density": feat_question_density,
    "avg_word_length": feat_avg_word_length,
    "closing_question": feat_closing_question,
    "exploratory_ratio": feat_exploratory_ratio,
    "opening_reaction": feat_opening_reaction,
    "emoji_density": feat_emoji_density,
    "list_usage": feat_list_usage,
    "slang_ratio": feat_slang_ratio,
    "filler_ratio": feat_filler_ratio,
    "we_ratio": feat_we_ratio,
    "first_person_ratio": feat_first_person_ratio,
    "you_ratio": feat_you_ratio,
    "conditional_ratio": feat_conditional_ratio,
    "contraction_rate": feat_contraction_rate,
    "tangent_markers": feat_tangent_markers,
    "hedge_ratio": feat_hedge_ratio,
    "sentence_length_variance": feat_sentence_length_variance,
    "emphasis_density": feat_emphasis_density,
    "passive_ratio": feat_passive_ratio,
    "formal_vocab_ratio": feat_formal_vocab_ratio,
    "avg_sentence_length": feat_avg_sentence_length,
}
