"""
InferenceContext — Full telemetry for a single inference.
=========================================================

Captures everything about an inference for QA validation:
- Input (query, session)
- Routing (route decision, complexity)
- Providers (tried, used, errors)
- Personality (injected, virtues)
- Processing (narration)
- Output (raw, narrated, final)
- Timing (latency, TTFT)
- Tokens (input, output)
- Request chain (step-by-step trace)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid


@dataclass
class RequestStep:
    """Single step in the request chain."""
    step: str  # receive, route, provider, generate, narrate, output
    time_ms: float
    detail: str
    metadata: Optional[dict] = None


@dataclass
class InferenceContext:
    """Full telemetry for a single inference."""

    # Identity
    inference_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: datetime = field(default_factory=datetime.now)
    session_id: str = ""

    # Input
    query: str = ""

    # Routing
    route: str = ""  # LOCAL_ONLY, DELEGATION_DETECTION, FULL_DELEGATION
    complexity_score: float = 0.0
    delegation_signals: list[str] = field(default_factory=list)

    # Providers
    providers_tried: list[str] = field(default_factory=list)
    provider_used: str = ""
    provider_errors: dict[str, str] = field(default_factory=dict)

    # Personality
    personality_injected: bool = False
    personality_length: int = 0
    system_prompt: str = ""
    voice_injected: bool = False
    virtues_loaded: bool = False

    # Processing
    narration_applied: bool = False
    narration_prompt: Optional[str] = None

    # Output
    raw_response: str = ""
    narrated_response: Optional[str] = None
    final_response: str = ""

    # Timing
    latency_ms: float = 0.0
    time_to_first_token_ms: Optional[float] = None

    # Tokens
    input_tokens: int = 0
    output_tokens: int = 0

    # Request chain
    request_chain: list[RequestStep] = field(default_factory=list)

    # Memory integration stats (populated by engine)
    memory_stats: dict = field(default_factory=dict)
    extraction_stats: dict = field(default_factory=dict)

    # Memory confidence level (NONE, LOW, MEDIUM, HIGH)
    memory_confidence_level: str = ""

    def add_step(self, step: str, time_ms: float, detail: str, **metadata):
        """Add a step to the request chain."""
        self.request_chain.append(RequestStep(
            step=step,
            time_ms=time_ms,
            detail=detail,
            metadata=metadata if metadata else None
        ))

    def to_dict(self) -> dict:
        """Serialize for storage/API."""
        return {
            "inference_id": self.inference_id,
            "timestamp": self.timestamp.isoformat(),
            "session_id": self.session_id,
            "query": self.query,
            "route": self.route,
            "complexity_score": self.complexity_score,
            "delegation_signals": self.delegation_signals,
            "providers_tried": self.providers_tried,
            "provider_used": self.provider_used,
            "provider_errors": self.provider_errors,
            "personality_injected": self.personality_injected,
            "personality_length": self.personality_length,
            "system_prompt": self.system_prompt,
            "voice_injected": self.voice_injected,
            "virtues_loaded": self.virtues_loaded,
            "narration_applied": self.narration_applied,
            "raw_response": self.raw_response,
            "narrated_response": self.narrated_response,
            "final_response": self.final_response,
            "latency_ms": self.latency_ms,
            "time_to_first_token_ms": self.time_to_first_token_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "request_chain": [
                {
                    "step": s.step,
                    "time_ms": s.time_ms,
                    "detail": s.detail,
                    "metadata": s.metadata
                }
                for s in self.request_chain
            ],
            "memory_stats": self.memory_stats,
            "extraction_stats": self.extraction_stats,
            "memory_confidence_level": self.memory_confidence_level,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InferenceContext":
        """Deserialize from dict."""
        ctx = cls(
            inference_id=data.get("inference_id", str(uuid.uuid4())[:8]),
            session_id=data.get("session_id", ""),
            query=data.get("query", ""),
            route=data.get("route", ""),
            complexity_score=data.get("complexity_score", 0.0),
            delegation_signals=data.get("delegation_signals", []),
            providers_tried=data.get("providers_tried", []),
            provider_used=data.get("provider_used", ""),
            provider_errors=data.get("provider_errors", {}),
            personality_injected=data.get("personality_injected", False),
            personality_length=data.get("personality_length", 0),
            system_prompt=data.get("system_prompt", ""),
            voice_injected=data.get("voice_injected", False),
            virtues_loaded=data.get("virtues_loaded", False),
            narration_applied=data.get("narration_applied", False),
            narration_prompt=data.get("narration_prompt"),
            raw_response=data.get("raw_response", ""),
            narrated_response=data.get("narrated_response"),
            final_response=data.get("final_response", ""),
            latency_ms=data.get("latency_ms", 0.0),
            time_to_first_token_ms=data.get("time_to_first_token_ms"),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
        )
        ctx.memory_stats = data.get("memory_stats", {})
        ctx.extraction_stats = data.get("extraction_stats", {})

        # Parse timestamp
        ts = data.get("timestamp")
        if ts:
            if isinstance(ts, str):
                ctx.timestamp = datetime.fromisoformat(ts)
            elif isinstance(ts, datetime):
                ctx.timestamp = ts

        # Parse request chain
        for step_data in data.get("request_chain", []):
            ctx.request_chain.append(RequestStep(
                step=step_data.get("step", ""),
                time_ms=step_data.get("time_ms", 0.0),
                detail=step_data.get("detail", ""),
                metadata=step_data.get("metadata"),
            ))

        return ctx
