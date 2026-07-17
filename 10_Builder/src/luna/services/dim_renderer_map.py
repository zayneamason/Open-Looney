"""
Dimension → Renderer Mapping

Maps the 5 continuous emotional dimensions from DimensionalEngine
to OrbRenderer parameters (ring hue/saturation/lightness/opacity,
breathe speed, drift, glow, phase offset, flicker).

This replaces the discrete apply_state() switch for P1 DIMENSION cases.
The frontend Canvas 2D renderer consumes these parameters unchanged.
"""

from luna.services.dimensional_engine import DimensionalState
from luna.services.orb_renderer import OrbRenderer
import logging

logger = logging.getLogger(__name__)


def map_dimensions_to_renderer(dims: DimensionalState, renderer: OrbRenderer):
    """
    Continuous mapping from 5 emotional dimensions to ring rendering params.

    Modifies renderer in-place. Call renderer.reset() before this
    to start from base ring configuration.

    Args:
        dims: Current DimensionalState (5 floats)
        renderer: OrbRenderer to mutate
    """
    v = dims.valence       # -1 to 1
    a = dims.arousal       #  0 to 1
    c = dims.certainty     #  0 to 1
    e = dims.engagement    #  0 to 1
    w = dims.warmth        #  0 to 1

    ring_count = len(renderer.rings)

    # ── VALENCE → hue, saturation, lightness ──
    # Cool (240) when negative → neutral (262) → warm (280+) when positive
    hue = 240 + ((v + 1) / 2) * 40 + w * 15
    saturation = 50 + a * 30 + w * 12
    lightness = 38 + ((v + 1) / 2) * 18

    for ring in renderer.rings:
        t = ring.id / max(1, ring_count - 1)
        ring.hue = hue + t * 8                # slight per-ring shift
        ring.saturation = saturation + t * 15
        ring.lightness = lightness + t * 10

    # ── AROUSAL → breathe speed, drift speed, breathe amplitude ──
    renderer.animation.breathe_speed = 0.008 + a * 0.027
    renderer.animation.breathe_amplitude = 1.5 + a * 2.5
    renderer.animation.drift.speed = 0.0003 + a * 0.0012

    # ── CERTAINTY → ring phase offset, opacity multiplier, flicker ──
    renderer.animation.ring_phase_offset = 1.2 - c * 0.5  # uncertain = more desync
    opacity_mul = 0.6 + c * 0.4
    for ring in renderer.rings:
        t = ring.id / max(1, ring_count - 1)
        ring.base_opacity = (0.08 + t * 0.35) * opacity_mul
    renderer.animation.flicker = c < 0.25  # extreme uncertainty triggers flicker

    # ── ENGAGEMENT → corona glow, drift radius ──
    renderer.animation.glow.ambient_max = 0.04 + e * 0.11
    renderer.animation.glow.corona_max = 0.06 + e * 0.16
    renderer.animation.drift.radius_x = 12 + (1 - e) * 10   # less drift when engaged
    renderer.animation.drift.radius_y = 8 + (1 - e) * 7

    # ── WARMTH → additional hue/saturation/corona (already folded above) ──
    # Warm → slightly larger core glow
    renderer.animation.glow.corona_max += w * 0.04

    logger.debug(
        f"Dim→Renderer: hue={hue:.0f} breathe={renderer.animation.breathe_speed:.3f} "
        f"phaseOff={renderer.animation.ring_phase_offset:.2f} "
        f"glow={renderer.animation.glow.corona_max:.3f}"
    )
