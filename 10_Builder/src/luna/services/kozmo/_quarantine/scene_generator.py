"""
KOZMO Scene Generator (Phase 6)

AI-powered scene generation using entity profiles.
Uses Claude API to generate Fountain-formatted scenes with:
- Character introductions based on profiles
- Location atmosphere
- Suggested dialogue starters
- Proper Fountain formatting

Uses filesystem-based entity storage (YAML).
"""
from typing import List, Dict, Optional
from pathlib import Path
import os
import logging

from .models import (
    SceneGenerateRequest,
    SceneGenerateResponse,
    DocumentFrontmatter,
)

logger = logging.getLogger(__name__)


class SceneGenerator:
    """Generates scene stubs using Claude AI and filesystem-based entities."""

    def __init__(self, project_root: Path):
        from .project import ProjectPaths
        self.project_root = project_root
        self.paths = ProjectPaths(project_root)
        self.client = None

        # Initialize Anthropic client if API key available
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            try:
                from anthropic import Anthropic
                self.client = Anthropic(api_key=api_key)
            except ImportError:
                logger.warning("anthropic package not installed - scene generation unavailable")
        else:
            logger.warning("ANTHROPIC_API_KEY not set - scene generation unavailable")

    def _load_entity(self, entity_slug: str) -> Optional[dict]:
        """Load entity from filesystem by slug, searching all type directories."""
        from .entity import parse_entity_safe

        if not self.paths.entities.exists():
            return None

        for type_dir in self.paths.entities.iterdir():
            if type_dir.is_dir():
                yaml_file = type_dir / f"{entity_slug}.yaml"
                if yaml_file.exists():
                    result = parse_entity_safe(yaml_file)
                    if result.entity:
                        e = result.entity
                        return {
                            "slug": e.slug,
                            "name": e.name,
                            "type": e.type,
                            "data": e.data,
                            "tags": e.tags,
                            "status": e.status,
                            "luna_notes": e.luna_notes,
                        }
        return None

    def generate_scene(self, request: SceneGenerateRequest) -> SceneGenerateResponse:
        """Generate a scene stub using entity profiles."""
        if not self.client:
            raise ValueError("Claude API not configured (missing ANTHROPIC_API_KEY or anthropic package)")

        # Load character entities
        characters = []
        for slug in request.character_slugs:
            char = self._load_entity(slug)
            if char:
                characters.append(char)

        # Load location entity
        location = self._load_entity(request.location_slug)
        if not location:
            raise ValueError(f"Location {request.location_slug} not found")

        # Build generation prompt
        prompt = self._build_generation_prompt(
            characters,
            location,
            request.goal,
            request.style,
        )

        # Call Claude API
        message = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[
                {"role": "user", "content": prompt}
            ],
        )

        generated_text = message.content[0].text

        # Parse generated text into frontmatter + body
        scene_data = self._parse_generated_scene(
            generated_text,
            request.character_slugs,
            request.location_slug,
        )

        return SceneGenerateResponse(
            frontmatter=scene_data["frontmatter"],
            body=scene_data["body"],
            meta={
                "tokens_used": message.usage.input_tokens + message.usage.output_tokens,
                "model": "claude-sonnet-4-20250514",
                "style": request.style,
            },
        )

    def _build_generation_prompt(
        self,
        characters: List[Dict],
        location: Dict,
        goal: str,
        style: str,
    ) -> str:
        """Build the Claude prompt for scene generation."""
        loc_data = location.get("data", {})
        atmosphere = loc_data.get("atmosphere", "")
        typical_props = loc_data.get("scene_template", {}).get("props_always_present", [])
        lighting = loc_data.get("lighting", "DAY")

        # Build character descriptions
        char_descriptions = []
        for char in characters:
            data = char.get("data", {})
            traits = ", ".join(data.get("traits", []))
            dialogue_style = data.get("dialogue_style", "natural")

            desc = f"- {char['name']}"
            if traits:
                desc += f" ({traits})"
            if dialogue_style:
                desc += f" - speaks in a {dialogue_style} manner"
            char_descriptions.append(desc)

        char_text = "\n".join(char_descriptions) if char_descriptions else "- (no character details available)"

        if style == "fountain":
            format_instructions = """
Format the scene in Fountain screenplay format:
- Scene heading: INT./EXT. LOCATION - DAY/NIGHT
- Action lines (description, no dialogue)
- Character names in ALL CAPS before dialogue
- Dialogue indented below character names
- Minimal parentheticals
"""
        else:
            format_instructions = """
Format the scene as prose narrative:
- Start with atmospheric description
- Introduce characters naturally
- Include suggested dialogue as quoted speech
- Keep it concise (1-2 paragraphs)
"""

        prompt = f"""You are a screenplay writing assistant for KOZMO, an AI filmmaking platform.

Generate a scene stub (NOT a complete scene - just a starting point for the writer to build on).

**Location:** {location['name']}
**Atmosphere:** {atmosphere}
**Time:** {lighting}
**Typical Props:** {', '.join(typical_props) if typical_props else 'None specified'}

**Characters:**
{char_text}

**Scene Goal:** {goal}

{format_instructions}

**Important:**
- This is a STUB to give the writer a starting point
- Include atmospheric description based on location
- Introduce characters consistent with their traits
- Suggest 1-2 lines of placeholder dialogue
- Keep it SHORT (under 200 words)
- Do NOT write a complete scene

Generate the scene stub now:
"""
        return prompt

    def _parse_generated_scene(
        self,
        generated_text: str,
        character_slugs: List[str],
        location_slug: str,
    ) -> Dict:
        """Parse generated text into frontmatter + body."""
        body = generated_text.strip()

        frontmatter = DocumentFrontmatter(
            characters_present=character_slugs,
            location=location_slug,
            props=[],
            time_of_day=self._extract_time_of_day(body),
        )

        return {
            "frontmatter": frontmatter,
            "body": body,
        }

    def _extract_time_of_day(self, scene_text: str) -> Optional[str]:
        """Extract time of day from scene heading."""
        import re
        match = re.search(r'(DAY|NIGHT|DAWN|DUSK)', scene_text, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        return None

    def estimate_cost(self, request: SceneGenerateRequest) -> Dict:
        """Estimate token usage and cost for generation."""
        estimated_input_tokens = 400
        estimated_output_tokens = 200

        # Claude Sonnet pricing
        input_cost_per_token = 0.003 / 1000
        output_cost_per_token = 0.015 / 1000

        estimated_cost = (
            estimated_input_tokens * input_cost_per_token +
            estimated_output_tokens * output_cost_per_token
        )

        return {
            "estimated_input_tokens": estimated_input_tokens,
            "estimated_output_tokens": estimated_output_tokens,
            "estimated_cost_usd": round(estimated_cost, 4),
            "model": "claude-sonnet-4-20250514",
        }
