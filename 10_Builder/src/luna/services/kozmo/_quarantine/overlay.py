"""
KOZMO Overlay Service — Phase 7

Manages annotation overlays for SCRIBO documents.
Annotations stored as sidecar .overlay.yaml files alongside .scribo documents.

Data flow:
  SCRIBO document (.scribo) ← has → Overlay sidecar (.overlay.yaml)
  Overlay annotation ← push-to-lab → ProductionBrief (Phase 8)
"""

import uuid
import yaml
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from ..scribo_parser import extract_visual_annotations
from ..types import (
    Annotation,
    OverlayState,
    LabAction,
    AgentTask,
    TextHighlight,
)


# =============================================================================
# Helpers
# =============================================================================


def _overlay_path_for_doc(story_dir: Path, doc_slug: str) -> Optional[Path]:
    """
    Find the .overlay.yaml sidecar for a .scribo document.
    Walks the story directory to find the matching .scribo file,
    then returns its sibling .overlay.yaml path.
    """
    for scribo_file in story_dir.rglob(f"{doc_slug}.scribo"):
        return scribo_file.with_suffix(".overlay.yaml")
    # If no .scribo file found, return a path in story root
    return story_dir / f"{doc_slug}.overlay.yaml"


def _annotation_to_dict(ann: Annotation) -> dict:
    """Serialize annotation to YAML-friendly dict."""
    d = ann.model_dump(exclude_none=True)
    # Convert datetime objects to ISO strings for YAML
    for key in ("created_at", "resolved_at"):
        if key in d and d[key] is not None:
            d[key] = d[key].isoformat()
    return d


def _dict_to_annotation(d: dict) -> Annotation:
    """Deserialize dict from YAML into Annotation model."""
    # Parse datetime strings back
    for key in ("created_at", "resolved_at"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = datetime.fromisoformat(d[key])
            except (ValueError, TypeError):
                d[key] = None
    return Annotation(**d)


# =============================================================================
# Overlay Service
# =============================================================================


class OverlayService:
    """Manages annotation overlays for SCRIBO documents."""

    def __init__(self, project_root: Path):
        self.root = project_root
        self.story_dir = project_root / "story"

    def get_overlay(self, doc_slug: str) -> OverlayState:
        """Read .overlay.yaml sidecar for a document."""
        path = _overlay_path_for_doc(self.story_dir, doc_slug)
        if path is None or not path.exists():
            return OverlayState(document_slug=doc_slug)

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError):
            return OverlayState(document_slug=doc_slug)

        if not raw or not isinstance(raw, dict):
            return OverlayState(document_slug=doc_slug)

        annotations = []
        for ann_dict in raw.get("annotations", []):
            try:
                annotations.append(_dict_to_annotation(ann_dict))
            except Exception:
                continue  # Skip malformed annotations

        return OverlayState(
            document_slug=doc_slug,
            annotations=annotations,
        )

    def save_overlay(self, state: OverlayState) -> OverlayState:
        """Write .overlay.yaml sidecar."""
        path = _overlay_path_for_doc(self.story_dir, state.document_slug)
        if path is None:
            raise FileNotFoundError(
                f"Cannot find location for overlay: {state.document_slug}"
            )

        data = {
            "document_slug": state.document_slug,
            "annotations": [_annotation_to_dict(a) for a in state.annotations],
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return state

    def add_annotation(self, doc_slug: str, annotation: Annotation) -> Annotation:
        """Add annotation to overlay. Generates ID if not provided."""
        if not annotation.id:
            annotation.id = f"ann_{uuid.uuid4().hex[:8]}"
        if annotation.created_at is None:
            annotation.created_at = datetime.now()

        state = self.get_overlay(doc_slug)
        state.annotations.append(annotation)
        self.save_overlay(state)
        return annotation

    def update_annotation(
        self, doc_slug: str, annotation_id: str, updates: dict
    ) -> Optional[Annotation]:
        """Update annotation fields."""
        state = self.get_overlay(doc_slug)
        for i, ann in enumerate(state.annotations):
            if ann.id == annotation_id:
                ann_dict = ann.model_dump()
                ann_dict.update(updates)
                updated = Annotation(**ann_dict)
                state.annotations[i] = updated
                self.save_overlay(state)
                return updated
        return None

    def delete_annotation(self, doc_slug: str, annotation_id: str) -> bool:
        """Remove annotation from overlay."""
        state = self.get_overlay(doc_slug)
        before = len(state.annotations)
        state.annotations = [a for a in state.annotations if a.id != annotation_id]
        if len(state.annotations) == before:
            return False
        self.save_overlay(state)
        return True

    def resolve_annotation(self, doc_slug: str, annotation_id: str) -> Optional[Annotation]:
        """Toggle resolved status."""
        state = self.get_overlay(doc_slug)
        for i, ann in enumerate(state.annotations):
            if ann.id == annotation_id:
                ann.resolved = not ann.resolved
                ann.resolved_at = datetime.now() if ann.resolved else None
                state.annotations[i] = ann
                self.save_overlay(state)
                return ann
        return None

    def get_annotations_by_type(self, doc_slug: str, ann_type: str) -> List[Annotation]:
        """Filter annotations by type."""
        state = self.get_overlay(doc_slug)
        return [a for a in state.annotations if a.type == ann_type]

    def get_all_actions(self) -> List[Annotation]:
        """
        Aggregate all LAB action annotations across all documents.
        Returns annotations with lab_action or agent_task set.
        """
        actions = []
        for overlay_file in self.story_dir.rglob("*.overlay.yaml"):
            doc_slug = overlay_file.stem.replace(".overlay", "")
            state = self.get_overlay(doc_slug)
            for ann in state.annotations:
                if ann.lab_action is not None or ann.agent_task is not None:
                    actions.append(ann)
        return actions

    def push_to_lab(self, doc_slug: str, annotation_id: str) -> Optional[dict]:
        """
        Convert an action annotation into data suitable for creating a ProductionBrief.
        Returns dict with fields needed by LabPipelineService.create_brief().
        """
        state = self.get_overlay(doc_slug)
        ann = next((a for a in state.annotations if a.id == annotation_id), None)
        if ann is None:
            return None

        brief_data = {
            "source_scene": doc_slug,
            "source_annotation_id": annotation_id,
            "source_paragraph": ann.paragraph_id,
            "title": ann.text[:80] if ann.text else "Untitled Brief",
            "prompt": "",
            "characters": [],
            "assignee": None,
        }

        if ann.lab_action:
            brief_data["prompt"] = ann.lab_action.prompt or ann.text
            brief_data["type"] = (
                "sequence" if ann.lab_action.type == "shot_sequence" else "single"
            )
            brief_data["assignee"] = ann.lab_action.assignee
            if ann.lab_action.entity:
                brief_data["characters"] = [ann.lab_action.entity]
        elif ann.agent_task:
            brief_data["prompt"] = ann.text
            brief_data["type"] = "reference"
            brief_data["assignee"] = ann.agent_task.agent
            if ann.agent_task.entity:
                brief_data["characters"] = [ann.agent_task.entity]
        else:
            brief_data["type"] = "single"
            brief_data["prompt"] = ann.text

        # Parse [[VISUAL timecode — prompt]] if present in the prompt text
        raw_prompt = brief_data["prompt"]
        visuals = extract_visual_annotations(raw_prompt)
        if visuals:
            v = visuals[0]
            brief_data["prompt"] = v["prompt"]
            brief_data["title"] = v["prompt"][:80]
            brief_data["audio_start"] = v["start_seconds"]
            brief_data["audio_end"] = v["end_seconds"]

        return brief_data

    def push_all_actions(self, doc_slug: str) -> List[dict]:
        """Batch push all action annotations from a document."""
        state = self.get_overlay(doc_slug)
        results = []
        for ann in state.annotations:
            if ann.lab_action is not None or ann.agent_task is not None:
                result = self.push_to_lab(doc_slug, ann.id)
                if result:
                    results.append(result)
        return results
