"""
KOZMO Camera Registry — Phase 8

Static registries for camera bodies, lens profiles, film stocks, and movements.
Referenced by ID in CameraRig/BriefPostConfig models.
Used by prompt builder to enrich Eden prompts.
"""

from typing import Optional
from pydantic import BaseModel


# =============================================================================
# Registry Data Classes
# =============================================================================


class CameraBodyInfo(BaseModel):
    id: str
    name: str
    badge: str  # CINEMA, INDIE, FILM, LO-FI
    sensor_size: str = "full_frame"
    prompt_phrase: str = ""


class LensInfo(BaseModel):
    id: str
    name: str
    type: str  # spherical, anamorphic
    character: str
    focal_range: tuple[int, int] = (18, 200)
    prompt_phrase: str = ""


class StockInfo(BaseModel):
    id: str
    name: str
    character: str
    prompt_phrase: str = ""


class MovementInfo(BaseModel):
    id: str
    name: str
    icon: str
    prompt_phrase: str


# =============================================================================
# Camera Bodies
# =============================================================================

CAMERA_BODIES: dict[str, CameraBodyInfo] = {
    "arri_alexa35": CameraBodyInfo(
        id="arri_alexa35", name="ARRI Alexa 35", badge="CINEMA",
        prompt_phrase="Shot on ARRI Alexa 35, cinematic, rich color science, natural skin tones",
    ),
    "red_v_raptor": CameraBodyInfo(
        id="red_v_raptor", name="RED V-Raptor", badge="CINEMA",
        prompt_phrase="Shot on RED V-Raptor, sharp, high contrast, vivid color",
    ),
    "sony_venice2": CameraBodyInfo(
        id="sony_venice2", name="Sony Venice 2", badge="CINEMA",
        prompt_phrase="Shot on Sony Venice 2, smooth, filmic, wide dynamic range",
    ),
    "bmpcc_6k": CameraBodyInfo(
        id="bmpcc_6k", name="Blackmagic 6K", badge="INDIE",
        prompt_phrase="Shot on Blackmagic 6K, organic, warm, documentary feel",
    ),
    "16mm_bolex": CameraBodyInfo(
        id="16mm_bolex", name="16mm Bolex", badge="FILM",
        prompt_phrase="Shot on 16mm Bolex, handheld film texture, vintage grain",
    ),
    "vhs_camcorder": CameraBodyInfo(
        id="vhs_camcorder", name="VHS Camcorder", badge="LO-FI",
        prompt_phrase="Shot on VHS camcorder, lo-fi, scan lines, analog warmth",
    ),
}

# =============================================================================
# Lens Profiles
# =============================================================================

LENS_PROFILES: dict[str, LensInfo] = {
    "cooke_s7i": LensInfo(
        id="cooke_s7i", name="Cooke S7/i", type="spherical",
        character="Warm, organic flares", focal_range=(18, 135),
        prompt_phrase="Cooke S7/i spherical lens, warm organic flares, gentle bokeh",
    ),
    "panavision_c": LensInfo(
        id="panavision_c", name="Panavision C-Series", type="anamorphic",
        character="Oval bokeh, blue streaks", focal_range=(35, 100),
        prompt_phrase="Panavision C-Series anamorphic, oval bokeh, blue streak flares",
    ),
    "zeiss_supreme": LensInfo(
        id="zeiss_supreme", name="Zeiss Supreme", type="spherical",
        character="Clinical precision", focal_range=(15, 200),
        prompt_phrase="Zeiss Supreme lens, crisp clinical precision",
    ),
    "atlas_mercury": LensInfo(
        id="atlas_mercury", name="Atlas Mercury", type="anamorphic",
        character="Modern ana, warm", focal_range=(28, 100),
        prompt_phrase="Atlas Mercury anamorphic, modern warm anamorphic look",
    ),
    "canon_k35": LensInfo(
        id="canon_k35", name="Canon K35", type="spherical",
        character="70s softness, vintage", focal_range=(18, 85),
        prompt_phrase="Canon K35 vintage lens, 70s softness, warm halation",
    ),
    "helios_44": LensInfo(
        id="helios_44", name="Helios 44-2", type="spherical",
        character="Swirly bokeh, Soviet", focal_range=(58, 58),
        prompt_phrase="Helios 44-2 Soviet lens, swirly bokeh, dreamy",
    ),
}

# =============================================================================
# Film Stocks
# =============================================================================

FILM_STOCKS: dict[str, StockInfo] = {
    "none": StockInfo(
        id="none", name="Digital Clean", character="No grain",
        prompt_phrase="",
    ),
    "kodak_5219": StockInfo(
        id="kodak_5219", name="Kodak 5219 (500T)", character="Warm tungsten, cinema",
        prompt_phrase="Kodak 5219 500T film stock, warm tungsten tones, cinema grain",
    ),
    "kodak_5207": StockInfo(
        id="kodak_5207", name="Kodak 5207 (250D)", character="Daylight, neutral",
        prompt_phrase="Kodak 5207 250D film stock, daylight balanced, neutral color",
    ),
    "fuji_eterna": StockInfo(
        id="fuji_eterna", name="Fuji Eterna Vivid", character="Rich greens, cool",
        prompt_phrase="Fuji Eterna Vivid film stock, rich greens, cool tones",
    ),
    "cinestill_800": StockInfo(
        id="cinestill_800", name="CineStill 800T", character="Halation, neon warmth",
        prompt_phrase="CineStill 800T, red halation, neon warmth, night photography",
    ),
    "ilford_hp5": StockInfo(
        id="ilford_hp5", name="Ilford HP5+ (B&W)", character="Punchy contrast",
        prompt_phrase="Ilford HP5+ black and white, punchy contrast, classic grain",
    ),
}

# =============================================================================
# Camera Movements
# =============================================================================

MOVEMENTS: dict[str, MovementInfo] = {
    "static": MovementInfo(id="static", name="Static", icon="◻", prompt_phrase="static camera, locked off"),
    "dolly_in": MovementInfo(id="dolly_in", name="Dolly In", icon="→◎", prompt_phrase="dolly in, slowly pushing forward"),
    "dolly_out": MovementInfo(id="dolly_out", name="Dolly Out", icon="◎→", prompt_phrase="dolly out, slowly pulling back"),
    "pan_left": MovementInfo(id="pan_left", name="Pan L", icon="←", prompt_phrase="panning left"),
    "pan_right": MovementInfo(id="pan_right", name="Pan R", icon="→", prompt_phrase="panning right"),
    "tilt_up": MovementInfo(id="tilt_up", name="Tilt Up", icon="↑", prompt_phrase="tilting up"),
    "tilt_down": MovementInfo(id="tilt_down", name="Tilt Down", icon="↓", prompt_phrase="tilting down"),
    "crane_up": MovementInfo(id="crane_up", name="Crane Up", icon="⤴", prompt_phrase="crane up, rising"),
    "crane_down": MovementInfo(id="crane_down", name="Crane Down", icon="⤵", prompt_phrase="crane down, descending"),
    "orbit_cw": MovementInfo(id="orbit_cw", name="Orbit CW", icon="↻", prompt_phrase="orbiting clockwise around subject"),
    "handheld": MovementInfo(id="handheld", name="Handheld", icon="〰", prompt_phrase="handheld, subtle movement, organic"),
    "steadicam": MovementInfo(id="steadicam", name="Steadicam", icon="≋", prompt_phrase="steadicam, smooth gliding movement"),
}


# =============================================================================
# Lookup Helpers
# =============================================================================


def get_camera_body(body_id: str) -> Optional[CameraBodyInfo]:
    return CAMERA_BODIES.get(body_id)


def get_lens(lens_id: str) -> Optional[LensInfo]:
    return LENS_PROFILES.get(lens_id)


def get_stock(stock_id: str) -> Optional[StockInfo]:
    return FILM_STOCKS.get(stock_id)


def get_movement(movement_id: str) -> Optional[MovementInfo]:
    return MOVEMENTS.get(movement_id)


def build_enriched_prompt(
    base_prompt: str,
    body_id: str = "arri_alexa35",
    lens_id: str = "cooke_s7i",
    focal: int = 50,
    aperture: float = 2.8,
    stock_id: str = "none",
    movements: list[str] | None = None,
) -> str:
    """
    Combine base prompt with camera metadata as natural language.
    This is the text bridge to Eden until native camera controls exist.
    """
    parts = [base_prompt.rstrip(".")]

    body = get_camera_body(body_id)
    if body:
        parts.append(f"Shot on {body.name}.")

    lens = get_lens(lens_id)
    if lens:
        parts.append(f"{lens.name} {lens.type} lens, {focal}mm, f/{aperture}.")

    stock = get_stock(stock_id)
    if stock and stock.id != "none":
        parts.append(f"{stock.name} film stock.")

    if movements:
        non_static = [m for m in movements if m != "static"]
        if non_static:
            move_names = []
            for m_id in non_static:
                mv = get_movement(m_id)
                if mv:
                    move_names.append(mv.name)
            if move_names:
                parts.append(f"Camera movement: {' + '.join(move_names)}.")

    return " ".join(parts)
