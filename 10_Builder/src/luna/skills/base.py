"""Skill base class and result type."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SkillResult:
    """Unified return type for all skills."""
    success: bool
    skill_name: str
    result: Any = None
    result_str: str = ""
    latex: Optional[str] = None
    data: Optional[dict] = None
    error: Optional[str] = None
    execution_ms: float = 0.0
    fallthrough: bool = False


class Skill(ABC):
    """Base class for deterministic skills."""
    name: str = ""
    description: str = ""
    triggers: list[str] = []

    @abstractmethod
    async def execute(self, query: str, context: dict) -> SkillResult:
        ...

    def is_available(self) -> bool:
        return True

    def narration_hint(self, result: SkillResult) -> str:
        return ""
