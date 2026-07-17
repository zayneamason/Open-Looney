"""
KOZMO Timeline Store — YAML Persistence
========================================
Load/save Timeline objects to {project_root}/timeline.yaml.

Uses Pydantic's model_dump/model_validate for serialization.
PyYAML handles the YAML ↔ dict layer.

File: src/luna/services/kozmo/timeline_store.py
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .timeline_types import Timeline


TIMELINE_FILENAME = "timeline.yaml"


def timeline_path(project_root: Path) -> Path:
    """Get the timeline.yaml path for a project."""
    return project_root / TIMELINE_FILENAME


def load_timeline(project_root: Path) -> Timeline:
    """
    Load Timeline from {project_root}/timeline.yaml.
    Returns empty Timeline if file doesn't exist or is empty.
    """
    path = timeline_path(project_root)
    if not path.exists():
        return Timeline()

    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return Timeline()

    data = yaml.safe_load(raw)
    if not data:
        return Timeline()

    return Timeline.model_validate(data)


def save_timeline(project_root: Path, timeline: Timeline) -> Path:
    """
    Save Timeline to {project_root}/timeline.yaml.
    Returns the path written to.
    """
    path = timeline_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = timeline.model_dump(mode="json")
    content = yaml.dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )

    path.write_text(content, encoding="utf-8")
    return path
