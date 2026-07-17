"""
LLM-Based Intent Router.

Uses qwen3:8b via Ollama for fast intent classification (~50-200ms).
Falls back to keyword-based classification on timeout or error.

Configurable thinking mode:
  - thinking_enabled=False (default): /nothink appended, temperature=0.1 → ~50-100ms
  - thinking_enabled=True: normal Qwen 3 thinking, temperature=0.6 → ~200-400ms

Config source: config/local_inference.json → routing section
"""
import json
import logging
import re
import time
from typing import Optional

import yaml

from luna.context.modes import IntentClassification, ResponseMode
from luna.core.paths import config_dir

logger = logging.getLogger(__name__)

# Classification prompt — YAML format, natural for LLMs
CLASSIFY_PROMPT = """Classify this message. Respond in YAML format:

mode: CHAT or RECALL or REFLECT or ASSIST
role: general or code or vision or reasoning or simple
confidence: 0.0 to 1.0

CHAT: casual conversation, greetings, small talk
RECALL: asking about past events, memories, things discussed before
REFLECT: asking for opinions, feelings, personal perspectives
ASSIST: task requests, factual questions, help with something

Message: {message}"""

# Map string modes to ResponseMode enum
MODE_MAP = {
    "CHAT": ResponseMode.CHAT,
    "RECALL": ResponseMode.RECALL,
    "REFLECT": ResponseMode.REFLECT,
    "ASSIST": ResponseMode.ASSIST,
}

# Valid roles
VALID_ROLES = {"general", "code", "vision", "reasoning", "simple"}


class IntentRouter:
    """
    Fast LLM-based intent classification via qwen3:8b.

    Falls back to keyword-based classification on any failure.
    """

    def __init__(
        self,
        provider=None,
        model: str = "qwen3:8b",
        thinking_enabled: bool = False,
        timeout_ms: float = 500,
    ):
        self._provider = provider
        self._model = model
        self._thinking_enabled = thinking_enabled
        self._timeout_ms = timeout_ms

        # Stats
        self._total_calls = 0
        self._llm_successes = 0
        self._fallback_count = 0
        self._total_latency_ms = 0.0

    @classmethod
    def from_config(cls, provider=None) -> Optional["IntentRouter"]:
        """
        Create IntentRouter from config/local_inference.json.

        Returns None if intent routing is disabled or config is missing.
        """
        try:
            config_path = config_dir() / "local_inference.json"
            if not config_path.exists():
                logger.info("IntentRouter: no local_inference.json, disabled")
                return None

            config = json.loads(config_path.read_text())
            routing = config.get("routing", {})

            if not routing.get("intent_routing_enabled", False):
                logger.info("IntentRouter: disabled in config")
                return None

            model = routing.get("intent_model", "qwen3:8b")
            thinking = routing.get("intent_thinking_enabled", False)
            timeout = config.get("performance", {}).get("hot_path_timeout_ms", 500)

            router = cls(
                provider=provider,
                model=model,
                thinking_enabled=thinking,
                timeout_ms=timeout,
            )
            logger.info(
                f"IntentRouter initialized: model={model} "
                f"thinking={'on' if thinking else 'off'} "
                f"timeout={timeout}ms"
            )
            return router

        except Exception as e:
            logger.warning(f"IntentRouter config load failed: {e}")
            return None

    @property
    def thinking_enabled(self) -> bool:
        return self._thinking_enabled

    @thinking_enabled.setter
    def thinking_enabled(self, value: bool):
        self._thinking_enabled = value
        logger.info(f"IntentRouter thinking mode: {'on' if value else 'off'}")

    def get_stats(self) -> dict:
        return {
            "total_calls": self._total_calls,
            "llm_successes": self._llm_successes,
            "fallback_count": self._fallback_count,
            "avg_latency_ms": (
                self._total_latency_ms / self._total_calls
                if self._total_calls > 0
                else 0.0
            ),
        }

    async def classify(self, message: str) -> IntentClassification:
        """
        Classify user message intent via LLM.

        Returns IntentClassification with mode, confidence, signals, and
        an extra `role` attribute for model selection.
        """
        self._total_calls += 1
        start = time.perf_counter()

        try:
            result = await self._classify_llm(message)
            latency_ms = (time.perf_counter() - start) * 1000
            self._total_latency_ms += latency_ms
            self._llm_successes += 1

            logger.info(
                f"[INTENT-ROUTER] mode={result.mode.value} role={getattr(result, 'role', '?')} "
                f"conf={result.confidence:.2f} latency={latency_ms:.0f}ms "
                f"thinking={'on' if self._thinking_enabled else 'off'}"
            )
            return result

        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            self._total_latency_ms += latency_ms
            self._fallback_count += 1
            logger.warning(
                f"[INTENT-ROUTER] LLM classification failed ({latency_ms:.0f}ms): {e}, "
                f"using keyword fallback"
            )
            return self._classify_keyword(message)

    async def _classify_llm(self, message: str) -> IntentClassification:
        """Call qwen3:8b for classification."""
        if self._provider is None:
            raise RuntimeError("No provider configured")

        from .base import Message as LLMMessage

        prompt = CLASSIFY_PROMPT.format(message=message[:500])  # Truncate long messages

        # Append /nothink when thinking is disabled for speed
        if not self._thinking_enabled:
            prompt += "\n/nothink"

        temperature = 0.6 if self._thinking_enabled else 0.1

        import asyncio
        result = await asyncio.wait_for(
            self._provider.complete(
                messages=[
                    LLMMessage(role="system", content="You are a message classifier. Respond with three lines in YAML format."),
                    LLMMessage(role="user", content=prompt),
                ],
                temperature=temperature,
                max_tokens=150,  # Classification is short
                model=self._model,
            ),
            timeout=self._timeout_ms / 1000,
        )

        return self._parse_classification(result.content, message)

    def _parse_classification(self, raw: str, original_message: str) -> IntentClassification:
        """Parse LLM response into IntentClassification.

        Three-layer degradation:
        1. YAML parse (handles clean output)
        2. Regex fallback (handles preamble/conversational wrapping)
        3. One-word scan (last resort — scan for mode word anywhere)
        """
        text = raw.strip()

        # Strip Qwen thinking blocks
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(
                l for l in lines if not l.strip().startswith("```")
            ).strip()

        # --- Layer 1: YAML parse ---
        data = None
        try:
            parsed = yaml.safe_load(text)
            if isinstance(parsed, dict) and "mode" in parsed:
                data = parsed
        except Exception:
            pass

        if data is not None:
            mode_str = str(data.get("mode", "CHAT")).upper()
            mode = MODE_MAP.get(mode_str, ResponseMode.CHAT)
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.7))))
            role = str(data.get("role", "general")).lower()
            if role not in VALID_ROLES:
                role = "general"
        else:
            # --- Layer 2: Regex fallback ---
            mode = ResponseMode.CHAT
            role = "general"
            confidence = 0.7

            mode_match = re.search(r'mode:\s*(\w+)', text, re.IGNORECASE)
            if mode_match:
                mode_str_found = mode_match.group(1).upper()
                mode = MODE_MAP.get(mode_str_found, ResponseMode.CHAT)
            else:
                # --- Layer 2b: One-word scan ---
                text_upper = text.upper()
                for word, val in [
                    ("RECALL", ResponseMode.RECALL),
                    ("REFLECT", ResponseMode.REFLECT),
                    ("ASSIST", ResponseMode.ASSIST),
                    ("CHAT", ResponseMode.CHAT),
                ]:
                    if word in text_upper:
                        mode = val
                        break

            role_match = re.search(r'role:\s*(\w+)', text, re.IGNORECASE)
            if role_match:
                role = role_match.group(1).lower()
                if role not in VALID_ROLES:
                    role = "general"

            conf_match = re.search(r'confidence:\s*([\d.]+)', text, re.IGNORECASE)
            if conf_match:
                confidence = max(0.0, min(1.0, float(conf_match.group(1))))

        # Code keyword upgrade: if role is still 'general', check for code signals
        if role == "general":
            msg_lower = original_message.lower()
            code_keywords = [
                "write a script", "implement", "build a", "create a program",
                "debug this", "fix this code", "python", "javascript",
                "function", "class ", "def ", "import ",
            ]
            for kw in code_keywords:
                if kw in msg_lower:
                    role = "code"
                    break

        mode_str = mode.value.upper()
        intent = IntentClassification(
            mode=mode,
            confidence=confidence,
            signals=[f"llm_router:{mode_str}", f"role:{role}"],
        )
        intent.role = role
        return intent

    def _classify_keyword(self, message: str) -> IntentClassification:
        """
        Keyword-based fallback classification.

        Mirrors the Director's _classify_intent() logic for safety.
        """
        msg_lower = message.lower()

        # Code signals
        code_keywords = [
            "write a script", "implement", "build a", "create a program",
            "debug this", "fix this code", "python", "javascript",
            "function", "class ", "def ", "import ",
        ]
        for kw in code_keywords:
            if kw in msg_lower:
                intent = IntentClassification(
                    mode=ResponseMode.ASSIST,
                    confidence=0.7,
                    signals=["keyword_fallback", "code_signal"],
                )
                intent.role = "code"
                return intent

        # Memory/recall signals
        recall_keywords = [
            "do you remember", "what do you remember",
            "you mentioned", "you said", "we talked",
            "last time", "earlier",
        ]
        for kw in recall_keywords:
            if kw in msg_lower:
                intent = IntentClassification(
                    mode=ResponseMode.RECALL,
                    confidence=0.7,
                    signals=["keyword_fallback"],
                )
                intent.role = "general"
                return intent

        # Reflect signals
        reflect_keywords = [
            "how do you feel", "what do you think",
            "your opinion", "do you like",
        ]
        for kw in reflect_keywords:
            if kw in msg_lower:
                intent = IntentClassification(
                    mode=ResponseMode.REFLECT,
                    confidence=0.7,
                    signals=["keyword_fallback"],
                )
                intent.role = "general"
                return intent

        # Assist signals (non-code)
        assist_keywords = [
            "help me", "can you", "how do i", "explain",
            "what is", "tell me about",
        ]
        for kw in assist_keywords:
            if kw in msg_lower:
                intent = IntentClassification(
                    mode=ResponseMode.ASSIST,
                    confidence=0.6,
                    signals=["keyword_fallback"],
                )
                intent.role = "general"
                return intent

        # Default: CHAT
        intent = IntentClassification(
            mode=ResponseMode.CHAT,
            confidence=0.5,
            signals=["keyword_fallback", "default"],
        )
        intent.role = "general"
        return intent
