"""
Study Context Renderer — Pre-session project knowledge injection.

Reads the study_context section from a project's YAML config and renders
it into natural prose for Layer 2.0 of the system prompt. Luna starts
every conversation with the complete briefing loaded.

Retrieval (Layer 4.0) still runs on top — this is the foundation.

Generic renderer: walks arbitrary YAML sections. Known sections (project,
people, active_work, narrative) get dedicated renderers for richer output.
Unknown sections are rendered generically — no code changes needed for
new project domains.
"""

import logging
from pathlib import Path
from typing import Optional

from luna.core.paths import config_dir

logger = logging.getLogger(__name__)

# Same config directory used by search_chain.py
_CONFIG_DIR = config_dir() / "projects"


class StudyContextRenderer:
    """Render a project's study_context YAML into natural prose."""

    # Keys that are metadata, not content sections
    METADATA_KEYS = {"version", "token_budget", "enabled", "changelog", "auto_quest"}

    # Sections with dedicated renderers (known structure → richer output)
    KNOWN_RENDERERS = {
        "project": "_render_project",
        "people": "_render_people",
        "active_work": "_render_active_work",
        "narrative": "_render_narrative",
    }

    def __init__(self, project_config: dict):
        self.config = project_config.get("study_context", {})

    def is_enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    @property
    def token_budget(self) -> int:
        return int(self.config.get("token_budget", 4000))

    def render(self) -> str:
        """Render study context YAML into natural prose for the system prompt."""
        if not self.is_enabled():
            return ""

        parts = []
        for section_name, section_data in self.config.items():
            if section_name in self.METADATA_KEYS:
                continue
            if section_name in self.KNOWN_RENDERERS:
                method = getattr(self, self.KNOWN_RENDERERS[section_name])
                parts.append(method())
            else:
                parts.append(self._render_generic(section_name, section_data))

        text = "\n\n".join(p for p in parts if p)
        return self._truncate_to_budget(text)

    # ── Known section renderers ──────────────────────────────────────────

    def _render_project(self) -> str:
        proj = self.config.get("project")
        if not proj:
            return ""
        lines = [f"## {proj.get('name', 'Project')}"]
        for key in ("location", "partner", "scope", "status", "demo"):
            val = proj.get(key)
            if val:
                label = key.replace("_", " ").title()
                lines.append(f"**{label}:** {val}")
        return "\n".join(lines)

    def _render_people(self) -> str:
        people = self.config.get("people")
        if not people:
            return ""
        sections = []
        for _key, person in people.items():
            display_name = _key.replace("_", " ").title()
            lines = [f"## {display_name}"]
            if person.get("role"):
                age_str = f", Age {person['age']}" if person.get("age") else ""
                clan_str = f", {person['clan']}" if person.get("clan") else ""
                lines.append(f"{person['role']}{age_str}{clan_str}.")
            if person.get("summary"):
                lines.append(person["summary"].strip())
            if person.get("household"):
                lines.append("**Household:** " + "; ".join(person["household"]))
            if person.get("key_relationships"):
                lines.append("**Key relationships:** " + "; ".join(person["key_relationships"]))
            if person.get("key_knowledge"):
                lines.append("**Key knowledge:** " + "; ".join(person["key_knowledge"]))
            if person.get("arc"):
                lines.append(f"**Arc:** {person['arc'].strip()}")
            sections.append("\n".join(lines))
        return "\n\n".join(sections)

    def _render_active_work(self) -> str:
        work = self.config.get("active_work")
        if not work:
            return ""
        lines = ["## Active Work"]
        for category, items in work.items():
            lines.append(f"**{category.replace('_', ' ').title()}:**")
            for item in items:
                lines.append(f"- {item}")
        return "\n".join(lines)

    def _render_narrative(self) -> str:
        narrative = self.config.get("narrative")
        if not narrative:
            return ""
        return f"## Narrative\n{narrative.strip()}"

    # ── Generic renderer (handles arbitrary YAML sections) ────────────

    def _render_generic(self, name: str, data) -> str:
        """Render any YAML section into prose. No code changes needed per project."""
        header = name.replace("_", " ").title()

        if isinstance(data, str):
            return f"## {header}\n{data.strip()}"

        if isinstance(data, list):
            items = []
            for item in data:
                if isinstance(item, dict):
                    items.append(self._dict_to_prose(item))
                else:
                    items.append(f"- {item}")
            return f"## {header}\n" + "\n".join(items)

        if isinstance(data, dict):
            parts = [f"## {header}"]
            for key, val in data.items():
                sub_header = key.replace("_", " ").title()
                if isinstance(val, str):
                    parts.append(f"**{sub_header}:** {val}")
                elif isinstance(val, list):
                    parts.append(f"**{sub_header}:**")
                    for v in val:
                        if isinstance(v, dict):
                            parts.append(self._dict_to_prose(v, indent=2))
                        else:
                            parts.append(f"  - {v}")
                elif isinstance(val, dict):
                    parts.append(f"### {sub_header}")
                    parts.append(self._dict_to_prose(val))
                else:
                    parts.append(f"**{sub_header}:** {val}")
            return "\n".join(parts)

        return f"## {header}\n{data}"

    def _dict_to_prose(self, d: dict, indent: int = 0) -> str:
        """Convert a dict into readable key-value prose."""
        prefix = " " * indent
        lines = []
        for k, v in d.items():
            label = k.replace("_", " ").title()
            if isinstance(v, str) and "\n" in v:
                lines.append(f"{prefix}**{label}:** {v.strip()}")
            elif isinstance(v, list):
                items = ", ".join(str(i) for i in v)
                lines.append(f"{prefix}**{label}:** {items}")
            elif isinstance(v, dict):
                lines.append(f"{prefix}**{label}:**")
                lines.append(self._dict_to_prose(v, indent + 2))
            else:
                lines.append(f"{prefix}**{label}:** {v}")
        return "\n".join(lines)

    # ── Token budgeting ────────────────────────────────────────────────

    def _truncate_to_budget(self, text: str) -> str:
        """Truncate to token budget (~4 chars per token)."""
        max_chars = self.token_budget * 4
        if len(text) <= max_chars:
            return text
        truncated = text[:max_chars].rsplit("\n", 1)[0]
        logger.warning(
            f"[STUDY-CONTEXT] Truncated from {len(text)} to {len(truncated)} chars "
            f"(budget={self.token_budget} tokens)"
        )
        return truncated


def load_study_context(slug: str, config_dir: Optional[Path] = None) -> Optional[str]:
    """
    Load and render study context for a project slug.

    Returns rendered prose string, or None if no study context / disabled.
    """
    base_dir = config_dir or _CONFIG_DIR
    config_path = base_dir / f"{slug}.yaml"

    if not config_path.exists():
        return None

    try:
        import yaml
        with open(config_path, "r") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"[STUDY-CONTEXT] Failed to load config for '{slug}': {e}")
        return None

    renderer = StudyContextRenderer(data)
    if not renderer.is_enabled():
        return None

    text = renderer.render()
    if text:
        token_est = len(text) // 4
        logger.info(
            f"[STUDY-CONTEXT] Loaded for '{slug}': ~{token_est} tokens, "
            f"version={data.get('study_context', {}).get('version', 'unknown')}"
        )
    return text or None


def load_raw_config(slug: str, config_dir: Optional[Path] = None) -> Optional[dict]:
    """Load raw project YAML config as a dict (not rendered)."""
    base_dir = config_dir or _CONFIG_DIR
    config_path = base_dir / f"{slug}.yaml"
    if not config_path.exists():
        return None
    try:
        import yaml
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"[STUDY-CONTEXT] Failed to load raw config for '{slug}': {e}")
        return None


def flatten_to_text(data) -> str:
    """Recursively flatten YAML data to a single string for keyword matching."""
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        return " ".join(flatten_to_text(item) for item in data)
    if isinstance(data, dict):
        return " ".join(f"{k} {flatten_to_text(v)}" for k, v in data.items())
    return str(data) if data is not None else ""


def get_matching_sections(
    config: dict,
    entity_names: list[str],
) -> list[str]:
    """
    Find study context section paths that mention any of the given entity names.

    Returns paths like: ["people.ada_001", "governance", "springs"]
    """
    study = config.get("study_context", {})
    if not study:
        return []

    metadata_keys = StudyContextRenderer.METADATA_KEYS
    matched = []
    normalized = {e.lower().replace(" ", "_") for e in entity_names}
    names_lower = {e.lower() for e in entity_names}

    for section_name, section_data in study.items():
        if section_name in metadata_keys:
            continue

        if section_name == "people" and isinstance(section_data, dict):
            for person_key, person_data in section_data.items():
                if person_key in normalized:
                    matched.append(f"people.{person_key}")
                    continue
                person_text = flatten_to_text(person_data).lower()
                for ename in names_lower:
                    if ename in person_text:
                        matched.append(f"people.{person_key}")
                        break
        else:
            section_text = flatten_to_text(section_data).lower()
            for ename in names_lower:
                if ename in section_text:
                    matched.append(section_name)
                    break

    return matched
