"""
KOZMO Prompt Builder

Converts KOZMO shot configs into Eden-compatible generation prompts.

Takes camera config, entity data, scene context, and post-processing
settings and composes a structured prompt string that Eden understands.

Responsibilities:
- Shot config → Eden prompt string
- Camera/lens metadata embedding
- Character reference injection
- Scene context assembly
- Post-processing directive encoding
"""

from typing import Optional, List, Dict, Any

from .types import (
    ShotConfig,
    CameraConfig,
    PostConfig,
    Entity,
)


# =============================================================================
# Camera Metadata
# =============================================================================

# Camera body → look characteristics
CAMERA_LOOKS = {
    "arri_alexa35": "cinematic, rich color science, natural skin tones",
    "red_v_raptor": "sharp, high contrast, vivid color",
    "sony_venice2": "smooth, filmic, wide dynamic range",
    "blackmagic_ursa": "organic, slightly warm, documentary feel",
}

# Lens → optical characteristics
LENS_LOOKS = {
    "cooke_s7i": "gentle bokeh, warm flares, classic cinema look",
    "zeiss_supreme": "crisp, clinical, modern look",
    "panavision_c": "vintage anamorphic, oval bokeh, streak flares",
    "atlas_mercury": "anamorphic, blue streak flares, cinematic",
}

# Film stock → color/grain characteristics
FILM_STOCK_LOOKS = {
    "kodak_5219": "500T tungsten, warm shadows, fine grain",
    "kodak_5207": "250D daylight, natural color, subtle grain",
    "fuji_eterna": "cool tones, smooth gradients, muted palette",
    "kodak_5222": "Double-X black and white, high contrast, rich grain",
}

# Camera movement → motion description
MOVEMENT_DESCRIPTIONS = {
    "static": "locked off, still frame",
    "dolly_in": "slowly pushing in",
    "dolly_out": "slowly pulling back",
    "pan_left": "panning left",
    "pan_right": "panning right",
    "tilt_up": "tilting upward",
    "tilt_down": "tilting downward",
    "crane_up": "crane rising",
    "crane_down": "crane descending",
    "handheld": "handheld, subtle movement",
    "steadicam": "smooth tracking shot",
}


# =============================================================================
# Prompt Building
# =============================================================================


def build_shot_prompt(
    shot: ShotConfig,
    entities: Optional[Dict[str, Entity]] = None,
    scene_description: Optional[str] = None,
) -> str:
    """
    Build Eden-compatible prompt from shot config.

    Assembles prompt from:
    1. Base shot prompt (user-written)
    2. Camera/lens look descriptors
    3. Character descriptions (from entity data)
    4. Scene context
    5. Post-processing directives

    Args:
        shot: Shot configuration
        entities: Optional dict of slug → Entity for character descriptions
        scene_description: Optional scene context to include

    Returns:
        Complete prompt string for Eden generation
    """
    parts: List[str] = []

    # 1. Base prompt
    parts.append(shot.prompt)

    # 2. Camera look
    camera_desc = build_camera_description(shot.camera)
    if camera_desc:
        parts.append(camera_desc)

    # 3. Character descriptions
    if entities and shot.characters_present:
        char_desc = build_character_descriptions(
            shot.characters_present, entities
        )
        if char_desc:
            parts.append(char_desc)

    # 4. Scene context
    if scene_description:
        parts.append(f"Scene context: {scene_description}")

    # 5. Post-processing
    post_desc = build_post_description(shot.post)
    if post_desc:
        parts.append(post_desc)

    return ". ".join(parts)


def build_camera_description(camera: CameraConfig) -> str:
    """
    Build camera look description from config.

    Args:
        camera: Camera configuration

    Returns:
        Camera description string
    """
    parts = []

    # Camera body look
    body_look = CAMERA_LOOKS.get(camera.body)
    if body_look:
        parts.append(f"Shot on {camera.body.replace('_', ' ').upper()}, {body_look}")

    # Lens look
    lens_look = LENS_LOOKS.get(camera.lens)
    if lens_look:
        parts.append(f"{camera.lens.replace('_', ' ').title()} lens, {lens_look}")

    # Focal length
    parts.append(f"{camera.focal_mm}mm")

    # Aperture
    parts.append(f"f/{camera.aperture}")

    # Movement
    movements = []
    for m in camera.movement:
        desc = MOVEMENT_DESCRIPTIONS.get(m, m)
        movements.append(desc)
    if movements:
        parts.append(", ".join(movements))

    return ", ".join(parts)


def build_character_descriptions(
    character_slugs: List[str],
    entities: Dict[str, Entity],
) -> str:
    """
    Build character description text from entity data.

    Args:
        character_slugs: List of character slugs present in shot
        entities: Dict of slug → Entity

    Returns:
        Character description string
    """
    descriptions = []

    for slug in character_slugs:
        entity = entities.get(slug)
        if not entity:
            continue

        desc_parts = [entity.name]

        # Pull physical descriptors from entity data
        if "build" in entity.data:
            desc_parts.append(entity.data["build"])
        if "age" in entity.data:
            desc_parts.append(f"age {entity.data['age']}")
        if "appearance" in entity.data:
            desc_parts.append(entity.data["appearance"])

        descriptions.append(", ".join(desc_parts))

    if not descriptions:
        return ""

    return "Characters: " + "; ".join(descriptions)


def build_post_description(post: PostConfig) -> str:
    """
    Build post-processing description from config.

    Args:
        post: Post-processing configuration

    Returns:
        Post-processing description string
    """
    parts = []

    # Film stock
    stock_look = FILM_STOCK_LOOKS.get(post.film_stock)
    if stock_look:
        parts.append(stock_look)

    # Color temperature
    if post.color_temp_k != 5600:  # Non-default
        if post.color_temp_k < 4000:
            parts.append("warm tungsten lighting")
        elif post.color_temp_k > 7000:
            parts.append("cool daylight")

    # Grain
    if post.grain_pct > 0:
        if post.grain_pct < 20:
            parts.append("subtle film grain")
        elif post.grain_pct < 50:
            parts.append("moderate film grain")
        else:
            parts.append("heavy film grain")

    # Bloom
    if post.bloom_pct > 0:
        parts.append("soft bloom on highlights")

    # Halation
    if post.halation_pct > 0:
        parts.append("halation on bright edges")

    if not parts:
        return ""

    return "Post: " + ", ".join(parts)


# =============================================================================
# LoRA Reference
# =============================================================================


def get_lora_references(
    character_slugs: List[str],
    entities: Dict[str, Entity],
) -> List[str]:
    """
    Collect LoRA references for characters in a shot.

    Args:
        character_slugs: List of character slugs
        entities: Dict of slug → Entity

    Returns:
        List of LoRA identifiers
    """
    loras = []
    for slug in character_slugs:
        entity = entities.get(slug)
        if entity and entity.references.lora:
            loras.append(entity.references.lora)
    return loras


def get_reference_images(
    character_slugs: List[str],
    entities: Dict[str, Entity],
) -> List[str]:
    """
    Collect reference image paths for characters in a shot.

    Args:
        character_slugs: List of character slugs
        entities: Dict of slug → Entity

    Returns:
        List of reference image paths
    """
    images = []
    for slug in character_slugs:
        entity = entities.get(slug)
        if entity and entity.references.images:
            images.extend(entity.references.images)
    return images
