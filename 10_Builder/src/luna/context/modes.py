"""
Response Modes — Structured behavioral injection for Luna.

Level 2 of the Prompt Control Architecture.

The Director classifies query intent into a ResponseMode BEFORE routing.
The PromptAssembler injects mode + rules as a structured block.
The model doesn't pick behavior — the system does.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class ResponseMode(Enum):
    CHAT = "CHAT"
    RECALL = "RECALL"
    REFLECT = "REFLECT"
    ASSIST = "ASSIST"
    UNCERTAIN = "UNCERTAIN"


# Mode behavioral contracts (injected into prompt)
MODE_CONTRACTS = {
    ResponseMode.CHAT: {
        "description": "Casual conversation. Be warm, be Luna.",
        "rules": [
            "Be natural and conversational",
            "No memory claims needed unless naturally relevant",
            "Don't reference memories unless the topic calls for it",
            "Match the user's energy and tone",
        ],
    },
    ResponseMode.RECALL: {
        "description": (
            "The user is asking about something specific they expect you to remember. "
            "The memory retrieval below is what was found."
        ),
        "rules": [
            "Reference only what is in the memory context below",
            "If the memory is thin or absent, name that gap directly",
            "Cite what you actually find — be specific about the source",
        ],
    },
    ResponseMode.REFLECT: {
        "description": "User asked how you feel or what you think. Draw from experience.",
        "rules": [
            "Draw from personality DNA + experience layer",
            "Be genuine, not performative",
            "You can have opinions — share them authentically",
            "Reference relevant memories if they inform your perspective",
        ],
    },
    ResponseMode.ASSIST: {
        "description": "User needs help with a task. Be precise and focused.",
        "rules": [
            "Stay on topic — don't wander",
            "Be precise and actionable",
            "If you have relevant context, lead with it rather than asking clarifying questions",
            "Use appropriate technical depth for the task",
        ],
    },
    ResponseMode.UNCERTAIN: {
        "description": "Insufficient context to determine intent.",
        "rules": [
            "If you have relevant memories, share them first — then ask for clarification only if needed",
            "Don't guess what the user wants",
            "Keep it brief",
        ],
    },
}


@dataclass
class IntentClassification:
    """Result of intent classification."""
    mode: ResponseMode
    confidence: float          # 0-1 how sure we are
    signals: list = field(default_factory=list)  # What triggered this classification
    is_continuation: bool = False  # Short follow-up inheriting previous mode
    previous_mode: Optional[ResponseMode] = None  # For continuity tracking

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "confidence": self.confidence,
            "signals": self.signals,
            "is_continuation": self.is_continuation,
            "previous_mode": self.previous_mode.value if self.previous_mode else None,
        }
