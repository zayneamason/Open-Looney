"""KOZMO Fountain + Prompt Routes — Screenplay parsing and Eden prompt building."""

from typing import Optional, List
from fastapi import APIRouter
from pydantic import BaseModel

from ..types import ShotConfig
from ..fountain import parse_fountain, dialogue_count
from ..prompt_builder import build_shot_prompt, get_lora_references, get_reference_images
from . import _get_project_paths, _load_project_entities

router = APIRouter(tags=["kozmo-fountain"])


class PromptBuildRequest(BaseModel):
    shot: ShotConfig
    scene_description: Optional[str] = None


class PromptBuildResponse(BaseModel):
    prompt: str
    lora_references: List[str] = []
    reference_images: List[str] = []


@router.post("/projects/{project_slug}/fountain/parse")
def api_fountain_parse(project_slug: str, text: str):
    """Parse Fountain screenplay text."""
    _get_project_paths(project_slug)  # Validate project exists

    doc = parse_fountain(text)

    return {
        "scenes": len(doc.scenes),
        "characters": doc.characters,
        "title_page": doc.title_page,
        "scene_list": [
            {
                "header": s.header,
                "location": s.location,
                "time_of_day": s.time_of_day,
                "characters": s.characters_present,
                "dialogue_count": s.has_dialogue,
            }
            for s in doc.scenes
        ],
    }


@router.get("/projects/{project_slug}/fountain/dialogue")
def api_fountain_dialogue(project_slug: str, text: str):
    """Get dialogue counts from Fountain text."""
    _get_project_paths(project_slug)

    doc = parse_fountain(text)
    counts = dialogue_count(doc)

    return {"dialogue_counts": counts}


@router.post("/projects/{project_slug}/prompt/build")
def api_build_prompt(project_slug: str, req: PromptBuildRequest):
    """Build Eden prompt from shot config."""
    paths = _get_project_paths(project_slug)
    entities = _load_project_entities(paths)

    prompt = build_shot_prompt(
        shot=req.shot,
        entities=entities,
        scene_description=req.scene_description,
    )

    loras = get_lora_references(req.shot.characters_present, entities)
    images = get_reference_images(req.shot.characters_present, entities)

    return PromptBuildResponse(
        prompt=prompt,
        lora_references=loras,
        reference_images=images,
    )
