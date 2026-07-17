"""EdenSkill — image/video generation via Eden.art (wraps eden_tools.py)."""

import re
import logging
from ..base import Skill, SkillResult

logger = logging.getLogger(__name__)


def _detect_media_type(query: str) -> str:
    """Detect whether user wants image or video."""
    q = query.lower()
    if re.search(r"\b(video|animation|animate|clip|motion)\b", q):
        return "video"
    return "image"


def _extract_prompt(query: str) -> str:
    """Extract the generation prompt from natural language."""
    # Remove preamble verbs
    cleaned = re.sub(
        r"^(can you |please )?(generate|create|make|draw|paint|render|show me|visualize|illustrate)\s+"
        r"(an? |the |me )?(image|picture|photo|art|illustration|portrait|video|animation|clip)\s+"
        r"(of |showing |depicting |with )?",
        "", query, flags=re.IGNORECASE,
    ).strip()
    return cleaned if cleaned else query


class EdenSkill(Skill):
    name = "eden"
    description = "Image/video generation via Eden.art"
    triggers = [
        r"\b(generate|create|make|draw|paint|render)\b.{0,30}\b(image|picture|art|illustration)\b",
        r"\b(generate|create|make)\b.{0,30}\b(video|animation)\b",
        r"\beden\b",
    ]

    def __init__(self, config: dict = None):
        self._config = config or {}

    def is_available(self) -> bool:
        try:
            from luna.tools.eden_tools import get_eden_adapter
            return get_eden_adapter() is not None
        except ImportError:
            return False

    async def execute(self, query: str, context: dict) -> SkillResult:
        try:
            from luna.tools.eden_tools import (
                eden_create_image, eden_create_video, get_eden_adapter,
            )
        except ImportError:
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error="eden_tools not available",
            )

        if get_eden_adapter() is None:
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error="Eden adapter not initialized (EDEN_API_KEY?)",
            )

        media_type = _detect_media_type(query)
        prompt = _extract_prompt(query)

        try:
            if media_type == "video":
                result = await eden_create_video(prompt=prompt, wait=True)
            else:
                result = await eden_create_image(prompt=prompt, wait=True)

            if result.get("error"):
                return SkillResult(
                    success=False, skill_name=self.name,
                    fallthrough=True, error=result["error"],
                )

            url = result.get("url", "")
            task_id = result.get("task_id", "")

            return SkillResult(
                success=True,
                skill_name=self.name,
                result=result,
                result_str=f"Generated {media_type}: {url}",
                data={
                    "url": url,
                    "task_id": task_id,
                    "type": media_type,
                    "prompt": prompt,
                },
            )
        except Exception as e:
            logger.warning(f"[EDEN] Generation failed: {e}")
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error=str(e),
            )

    def narration_hint(self, result: SkillResult) -> str:
        return (
            "Media was generated — show URL naturally. "
            "Express curiosity about whether it matched the vision."
        )
