"""Skill detection via regex pattern matching, slash commands, and optional LLM fallback."""

import asyncio
import re
from collections import namedtuple
from typing import Optional

from .config import SkillsConfig

# Detection result with confidence level ("high" or "low")
DetectionResult = namedtuple("DetectionResult", ["skill", "confidence"])


SKILL_PATTERNS: dict[str, list[str]] = {
    "math": [
        r"\b(solve|factor|simplify|expand|integrate|differentiate|derivative|integral)\b",
        r"\b(equation|polynomial|eigenvalue|matrix determinant)\b",
        r"\b(calculate|compute)\b.{0,30}\b(exact|symbolic|algebraic)\b",
    ],
    "logic": [
        r"\b(truth table|tautology|contradiction|satisfiable|entails)\b",
        r"\b(prove|disprove).{0,30}\b(implies|therefore|follows)\b",
        r"\b(AND|OR|NOT|XOR)\b.{0,30}\b(expression|formula)\b",
    ],
    "formatting": [
        r"\b(bullet points?|numbered list|outline for|table of)\b",
        r"\b(format|organize|structure)\b.{0,20}\b(as|into)\b.{0,20}\b(list|bullets|table)\b",
    ],
    "reading": [
        r"\b(read|open|parse|extract)\b.{0,30}\.(pdf|docx|doc|xlsx|pptx)\b",
        r"\b(what('?s| is) in|summarize|load)\b.{0,20}\b.{0,10}\.(pdf|doc)\b",
    ],
    "diagnostic": [
        r"\b(health check|system status|diagnostics?)\b",
        r"\b(how('?s| is).{0,10}(memory|database|system|engine))\b",
        r"\b(is.{0,10}(everything|system|database).{0,10}(okay|working|broken))\b",
    ],
    "eden": [
        r"\b(generate|create|make|draw|paint|render)\b.{0,30}\b(image|picture|art|illustration)\b",
        r"\b(generate|create|make)\b.{0,30}\b(video|animation)\b",
        r"\beden\b",
    ],
    "analytics": [
        r"\b(how many|count).{0,30}\b(memories|nodes|sessions|entities)\b",
        r"\b(memory|session|delegation).{0,10}(stats|statistics|summary|overview)\b",
        r"\b(analyze|analyse)\b.{0,20}\bdata\b",
    ],
    "arcade": [
        r"\b(play|launch|start|run)\b.{0,20}\b(game|arcade)\b",
        r"\bsteve j savage\b",
        r"\barcade\b",
        r"\blet'?s play\b",
    ],
    "library_research": [
        r"\b(data\s?room|dataroom)\b",
        r"\bnexus\b",
        r"\b(the\s+)?library\b",
        r"\b(investor|partnership|partner)\s+docs?\b",
        r"\b(search|look\s+(?:in|through))\s+(the\s+)?(?:documents?|docs|collection|library)\b",
        r"\bwhat\s+do\s+(?:the|our)\s+(?:documents?|docs)\s+say\b",
        r"\bwhat\s+do\s+we\s+have\s+(?:in\s+the\s+)?(?:library|docs|documents?|collection)\b",
    ],
    "memory_recall": [
        r"\bwhat\s+do\s+you\s+(?:know|remember)\s+about\b",
        r"\bdo\s+you\s+remember\b",
        r"\bhave\s+we\s+(?:talked|discussed)\s+about\b",
        r"\brecall\s+(?:what|anything|our|the|any)\b",
        r"\bwhat\s+have\s+we\s+said\s+about\b",
        r"\b(?:remember|remind\s+me)\s+(?:what|about)\b",
    ],
}

SKILL_PRIORITY = [
    "diagnostic", "math", "logic", "reading",
    "library_research", "memory_recall",
    "eden", "arcade", "formatting", "analytics",
]

# All known skill names for LLM validation
_SKILL_NAMES = set(SKILL_PATTERNS.keys())

# LLM classification prompt
_CLASSIFY_PROMPT = (
    "Classify the following user message into exactly one skill category, "
    "or 'none' if it doesn't match any.\n"
    "Categories: math, logic, formatting, reading, diagnostic, eden, analytics, arcade, library_research, memory_recall\n"
    "Reply with ONLY the category name (one word).\n\n"
    "Message: {message}\n\nCategory:"
)


class SkillDetector:
    """Detect which skill (if any) matches a user message."""

    def __init__(self, config: SkillsConfig = None):
        self._config = config or SkillsConfig()
        self._patterns, self._low_confidence = self._build_patterns()
        self._slash_map = self._build_slash_map()

    def _is_enabled(self, skill_name: str) -> bool:
        skill_conf = getattr(self._config, skill_name, {})
        if isinstance(skill_conf, dict):
            return skill_conf.get("enabled", True)
        return True

    def _build_patterns(self) -> tuple[dict[str, list[re.Pattern]], set[re.Pattern]]:
        """Compile built-in + extra trigger patterns with sensitivity.

        Returns (compiled_patterns, low_confidence_patterns).
        Single-word extra triggers are tagged as low-confidence so the
        director can ask the user to confirm before firing the skill.
        """
        compiled = {}
        low_confidence: set[re.Pattern] = set()

        for skill_name, base_patterns in SKILL_PATTERNS.items():
            if not self._is_enabled(skill_name):
                compiled[skill_name] = []
                continue

            # Get per-skill config
            skill_conf = getattr(self._config, skill_name, {})
            if isinstance(skill_conf, dict):
                sensitivity = skill_conf.get("sensitivity", "strict")
                extra_triggers = skill_conf.get("extra_triggers", [])
            else:
                sensitivity = "strict"
                extra_triggers = []

            # Start with built-in patterns (always word-boundary, high confidence)
            patterns = [re.compile(p, re.IGNORECASE) for p in base_patterns]

            # Add extra triggers with sensitivity-aware wrapping
            for trigger in extra_triggers:
                if not trigger or not isinstance(trigger, str):
                    continue
                cleaned = trigger.strip()
                if not cleaned:
                    continue
                escaped = re.escape(cleaned)
                if sensitivity == "strict":
                    pat = rf"\b{escaped}\b"
                elif sensitivity == "loose":
                    pat = rf"(?<!\w){escaped}(?!\w)"
                else:  # aggressive
                    pat = escaped
                compiled_pat = re.compile(pat, re.IGNORECASE)
                patterns.append(compiled_pat)

                # Single-word triggers → low confidence
                if len(cleaned.split()) == 1:
                    low_confidence.add(compiled_pat)

            compiled[skill_name] = patterns
        return compiled, low_confidence

    def _build_slash_map(self) -> dict[str, str]:
        """Build mapping from slash command to skill name."""
        if not self._config.detection.slash_commands:
            return {}
        slash_map = {}
        for skill_name in SKILL_PATTERNS:
            if not self._is_enabled(skill_name):
                continue
            skill_conf = getattr(self._config, skill_name, {})
            if isinstance(skill_conf, dict):
                cmd = skill_conf.get("slash_command", "")
                if cmd and isinstance(cmd, str):
                    slash_map[cmd.lower()] = skill_name
        return slash_map

    def detect(self, message: str) -> Optional[DetectionResult]:
        """Return DetectionResult (skill, confidence) or None.

        Confidence is "high" for slash commands and built-in patterns,
        "low" for single-word extra triggers (director should confirm).
        """
        trimmed = message.strip()

        # 1. Slash command check — always high confidence
        if trimmed.startswith("/") and self._slash_map:
            parts = trimmed.split(None, 1)
            cmd = parts[0].lower()
            if cmd in self._slash_map:
                return DetectionResult(self._slash_map[cmd], "high")

        # 2. Regex pattern check
        for skill_name in SKILL_PRIORITY:
            patterns = self._patterns.get(skill_name, [])
            for pattern in patterns:
                if pattern.search(message):
                    confidence = "low" if pattern in self._low_confidence else "high"
                    return DetectionResult(skill_name, confidence)
        return None

    def detect_all(self, message: str) -> list[DetectionResult]:
        """Return all matching DetectionResults (debug)."""
        matches = []
        for skill_name in SKILL_PRIORITY:
            patterns = self._patterns.get(skill_name, [])
            for pattern in patterns:
                if pattern.search(message):
                    confidence = "low" if pattern in self._low_confidence else "high"
                    matches.append(DetectionResult(skill_name, confidence))
                    break
        return matches

    async def detect_with_llm(
        self, message: str, inference_fn
    ) -> Optional[DetectionResult]:
        """Try regex first, then LLM classification as fallback."""
        # 1. Regex first
        result = self.detect(message)
        if result:
            return result

        # 2. LLM fallback — high confidence (LLM explicitly classified it)
        if not self._config.detection.llm_enabled:
            return None

        try:
            prompt = _CLASSIFY_PROMPT.format(message=message[:500])
            timeout_s = self._config.detection.llm_timeout_ms / 1000.0
            raw = await asyncio.wait_for(inference_fn(prompt), timeout=timeout_s)
            if not raw:
                return None
            candidate = raw.strip().lower().split()[0] if raw.strip() else ""
            if candidate in _SKILL_NAMES and self._is_enabled(candidate):
                return DetectionResult(candidate, "high")
        except (asyncio.TimeoutError, Exception):
            pass
        return None
