"""
Orb Ring Renderer

Translates OrbAnimation states into ring-level visual parameters.
The frontend Canvas 2D renderer consumes these parameters to draw
breathing, drifting, glowing concentric rings.

The backend sends ring *configuration* (base radii, opacities, animation
mode flags). The frontend runs breathing/drift/glow math locally at 60fps.
"""

from dataclasses import dataclass, field
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


@dataclass
class OrbRing:
    """A single concentric ring in the orb."""
    id: int
    base_radius: float       # resting radius in px
    base_opacity: float      # resting opacity (0-1)
    hue: float = 262.0       # HSL hue (purple)
    saturation: float = 60.0
    lightness: float = 45.0
    stroke_width: float = 1.0
    has_fill: bool = False    # innermost ring gets radial gradient fill


@dataclass
class DriftParams:
    """Lissajous fairy-float parameters."""
    radius_x: float = 12.0
    radius_y: float = 8.0
    speed: float = 0.0006


@dataclass
class GlowParams:
    """Glow layer intensity parameters."""
    ambient_max: float = 0.08
    corona_max: float = 0.12


@dataclass
class AnimationParams:
    """Current animation mode flags consumed by the frontend renderer."""
    breathe_speed: float = 0.015
    breathe_amplitude: float = 2.5
    drift: DriftParams = field(default_factory=DriftParams)
    glow: GlowParams = field(default_factory=GlowParams)
    ring_phase_offset: float = 0.7
    sync_breathing: bool = False      # PULSE: all rings breathe in phase
    sequential_pulse: bool = False    # PROCESSING: ripple outward
    flicker: bool = False             # FLICKER/ERROR: random per-ring jitter
    color_override: Optional[str] = None  # hex color override
    expanded_drift: bool = False      # DRIFT: wider float radius
    contracted: bool = False          # LISTENING: rings pull tight
    speech_rhythm: bool = False       # SPEAKING: pulse with rhythm
    split_groups: bool = False        # SPLIT: rings separate into groups


@dataclass
class TransitionEnvelope:
    """Attack/sustain/release envelope for orb state transitions."""
    pre_attack_ms: int = 200        # "intake of breath" — contraction before reaction
    pre_attack_contraction: float = 0.15  # how much rings contract (fraction of radius)
    attack_ms: int = 300            # ramp up to new state
    sustain_ms: int = -1            # hold duration (-1 = until next state)
    release_ms: int = 600           # ramp back to idle
    ease: str = "ease_out"          # ease_in, ease_out, ease_in_out, linear


TRANSITION_ENVELOPES: dict = {
    # Quick reactions — she noticed something
    "pulse":      TransitionEnvelope(pre_attack_ms=150, attack_ms=150, sustain_ms=800, release_ms=400),
    "pulse_fast": TransitionEnvelope(pre_attack_ms=80, attack_ms=80, sustain_ms=600, release_ms=300),
    "flicker":    TransitionEnvelope(pre_attack_ms=50, attack_ms=50, sustain_ms=500, release_ms=200),

    # Sustained presence — she's holding this feeling
    "glow":       TransitionEnvelope(pre_attack_ms=300, attack_ms=400, sustain_ms=2000, release_ms=800),
    "drift":      TransitionEnvelope(pre_attack_ms=400, attack_ms=600, sustain_ms=3000, release_ms=1000),
    "orbit":      TransitionEnvelope(pre_attack_ms=300, attack_ms=400, sustain_ms=2000, release_ms=700),

    # System states — she's working
    "processing":    TransitionEnvelope(pre_attack_ms=200, attack_ms=200, sustain_ms=-1, release_ms=500),
    "speaking":      TransitionEnvelope(pre_attack_ms=150, attack_ms=150, sustain_ms=-1, release_ms=400),
    "listening":     TransitionEnvelope(pre_attack_ms=250, attack_ms=250, sustain_ms=-1, release_ms=600),
    "memory_search": TransitionEnvelope(pre_attack_ms=200, attack_ms=300, sustain_ms=-1, release_ms=500),

    # Movement — she's physically responding
    "spin":       TransitionEnvelope(pre_attack_ms=200, attack_ms=300, sustain_ms=1500, release_ms=500),
    "spin_fast":  TransitionEnvelope(pre_attack_ms=100, attack_ms=200, sustain_ms=1000, release_ms=400),
    "wobble":     TransitionEnvelope(pre_attack_ms=200, attack_ms=300, sustain_ms=1500, release_ms=600),
    "split":      TransitionEnvelope(pre_attack_ms=300, attack_ms=400, sustain_ms=2000, release_ms=800),

    # Error — something's wrong
    "error":        TransitionEnvelope(pre_attack_ms=50, attack_ms=100, sustain_ms=8000, release_ms=1000),
    "disconnected": TransitionEnvelope(pre_attack_ms=0, attack_ms=500, sustain_ms=-1, release_ms=2000),

    # Return home
    "idle":       TransitionEnvelope(pre_attack_ms=0, attack_ms=500, sustain_ms=-1, release_ms=0),
}


class OrbRenderer:
    """
    Translates OrbState into ring-level visual parameters.

    The renderer manages:
    - Ring model (count, radii, opacities, colors)
    - Animation mode flags (sync, sequential, flicker, etc.)
    - Ring mutations (subdivide, scale, fade, color, reset)

    The frontend Canvas 2D component consumes to_dict() output
    and runs the actual animation loop at 60fps.
    """

    DEFAULT_RING_COUNT = 5
    DEFAULT_BASE_RADIUS = 44.0

    def __init__(self, ring_count: int = DEFAULT_RING_COUNT,
                 base_radius: float = DEFAULT_BASE_RADIUS):
        self.base_radius = base_radius
        self._ring_count = ring_count
        self._ring_id_counter = 0
        self.rings: List[OrbRing] = []
        self.animation = AnimationParams()
        self._current_state_name: str = "idle"
        self._build_rings(ring_count)

    def _build_rings(self, count: int):
        """Build concentric rings with even spacing from outer to inner."""
        self.rings = []
        self._ring_id_counter = 0
        spacing = self.base_radius / (count + 0.5)
        for i in range(count):
            t = i / (count - 1) if count > 1 else 0.0
            ring = OrbRing(
                id=self._next_id(),
                base_radius=self.base_radius - i * spacing,
                base_opacity=0.08 + t * 0.35,
                hue=262.0,
                saturation=60.0 + t * 15.0,
                lightness=45.0 + t * 10.0,
                stroke_width=1.2 - t * 0.4,
                has_fill=(i == count - 1),  # innermost ring
            )
            self.rings.append(ring)
        logger.debug(f"Built {len(self.rings)} rings, base_radius={self.base_radius}")

    def _next_id(self) -> int:
        rid = self._ring_id_counter
        self._ring_id_counter += 1
        return rid

    def _reset_animation(self):
        """Reset animation params to defaults."""
        self.animation = AnimationParams()

    def apply_state(self, state) -> None:
        """
        Map an OrbState to ring configuration and animation params.

        Args:
            state: OrbState with .animation (OrbAnimation enum),
                   .color (optional hex), .brightness (float)
        """
        self._reset_animation()

        anim = state.animation.value if hasattr(state.animation, 'value') else str(state.animation)
        self._current_state_name = anim

        # Apply color override from state
        if state.color:
            self.animation.color_override = state.color

        # Apply brightness as glow intensity scaling
        brightness = getattr(state, 'brightness', 1.0)
        self.animation.glow.ambient_max = 0.08 * brightness
        self.animation.glow.corona_max = 0.12 * brightness

        match anim:
            case "idle":
                pass  # defaults are idle

            case "pulse":
                self.animation.sync_breathing = True
                self.animation.breathe_amplitude = 2.5 * 1.5
                self.animation.breathe_speed = 0.02

            case "pulse_fast":
                self.animation.sync_breathing = True
                self.animation.breathe_amplitude = 2.5 * 2.0
                self.animation.breathe_speed = 0.04

            case "spin":
                # Visual rotation: increasing phase offset between rings
                self.animation.ring_phase_offset = 1.4
                self.animation.breathe_speed = 0.02

            case "spin_fast":
                self.animation.ring_phase_offset = 2.0
                self.animation.breathe_speed = 0.035

            case "flicker":
                self.animation.flicker = True

            case "wobble":
                self.animation.expanded_drift = True
                self.animation.drift.radius_x = 20.0
                self.animation.drift.radius_y = 16.0
                self.animation.drift.speed = 0.0015

            case "drift":
                self.animation.expanded_drift = True
                self.animation.drift.radius_x = 24.0
                self.animation.drift.radius_y = 16.0
                self.animation.drift.speed = 0.001

            case "orbit":
                self.animation.ring_phase_offset = 1.2
                self.animation.breathe_speed = 0.018

            case "glow":
                self.animation.glow.ambient_max = 0.15 * brightness
                self.animation.glow.corona_max = 0.22 * brightness
                # Fade rings slightly to let glow dominate
                for ring in self.rings:
                    ring.base_opacity *= 0.7

            case "split":
                self.animation.split_groups = True
                # Spread ring radii apart — add gaps between groups
                mid = len(self.rings) // 2
                for i, ring in enumerate(self.rings):
                    if i < mid:
                        ring.base_radius += 8
                    else:
                        ring.base_radius -= 4

            case "processing":
                self.animation.sequential_pulse = True
                self.animation.breathe_speed = 0.025

            case "listening":
                self.animation.contracted = True
                # Rings contract tight, high opacity
                for ring in self.rings:
                    ring.base_radius *= 0.7
                    ring.base_opacity = min(1.0, ring.base_opacity * 1.5)

            case "speaking":
                self.animation.speech_rhythm = True
                self.animation.breathe_speed = 0.03

            case "memory_search":
                self.animation.color_override = "#06b6d4"
                self.animation.ring_phase_offset = 1.2
                # Rings expand outward slightly
                for ring in self.rings:
                    ring.base_radius *= 1.15

            case "error":
                self.animation.color_override = "#ef4444"
                self.animation.flicker = True
                self.animation.glow.ambient_max = 0.04
                self.animation.glow.corona_max = 0.06

            case "disconnected":
                self.animation.color_override = "#6b7280"
                self.animation.breathe_speed = 0.008
                self.animation.breathe_amplitude = 1.0
                self.animation.glow.ambient_max = 0.03
                self.animation.glow.corona_max = 0.05

        logger.debug(f"Applied state: {anim}")

    # ── Ring mutations ──

    def subdivide_ring(self, idx: int) -> bool:
        """Split one ring into two thinner rings at ±half-gap from original."""
        if idx < 0 or idx >= len(self.rings):
            return False
        ring = self.rings[idx]
        if ring.base_radius < 8:
            return False

        gap = ring.base_radius * 0.15
        outer = OrbRing(
            id=self._next_id(),
            base_radius=ring.base_radius,
            base_opacity=ring.base_opacity * 0.85,
            hue=ring.hue,
            saturation=ring.saturation,
            lightness=ring.lightness,
            stroke_width=ring.stroke_width,
            has_fill=False,
        )
        inner = OrbRing(
            id=self._next_id(),
            base_radius=ring.base_radius - gap,
            base_opacity=ring.base_opacity,
            hue=ring.hue,
            saturation=ring.saturation,
            lightness=ring.lightness,
            stroke_width=ring.stroke_width,
            has_fill=ring.has_fill,
        )
        self.rings[idx:idx + 1] = [outer, inner]
        logger.debug(f"Subdivided ring {ring.id} → {outer.id} + {inner.id}")
        return True

    def subdivide_all(self) -> int:
        """Split every ring that's large enough. Returns number of splits."""
        count = 0
        i = len(self.rings) - 1
        while i >= 0:
            if self.rings[i].base_radius >= 8:
                if self.subdivide_ring(i):
                    count += 1
            i -= 1
        return count

    def scale_ring(self, idx: int, factor: float):
        """Scale a ring's base radius by a factor."""
        if 0 <= idx < len(self.rings):
            self.rings[idx].base_radius = max(3.0, min(200.0,
                self.rings[idx].base_radius * factor))

    def fade_ring(self, idx: int, opacity: float):
        """Set a ring's base opacity."""
        if 0 <= idx < len(self.rings):
            self.rings[idx].base_opacity = max(0.0, min(1.0, opacity))

    def color_ring(self, idx: int, hue: float, saturation: float, lightness: float):
        """Set a ring's HSL color."""
        if 0 <= idx < len(self.rings):
            self.rings[idx].hue = hue
            self.rings[idx].saturation = saturation
            self.rings[idx].lightness = lightness

    def stroke_ring(self, idx: int, width: float):
        """Set a ring's stroke width."""
        if 0 <= idx < len(self.rings):
            self.rings[idx].stroke_width = max(0.3, min(4.0, width))

    def reset(self):
        """Return to base ring configuration."""
        self._build_rings(self._ring_count)
        self._reset_animation()

    def to_dict(self) -> dict:
        """Serialize ring config + animation params + transition envelope for WebSocket."""
        envelope = TRANSITION_ENVELOPES.get(self._current_state_name, TransitionEnvelope())
        return {
            "rings": [
                {
                    "id": r.id,
                    "baseRadius": r.base_radius,
                    "baseOpacity": r.base_opacity,
                    "hue": r.hue,
                    "saturation": r.saturation,
                    "lightness": r.lightness,
                    "strokeWidth": r.stroke_width,
                    "hasFill": r.has_fill,
                }
                for r in self.rings
            ],
            "animation": {
                "breatheSpeed": self.animation.breathe_speed,
                "breatheAmplitude": self.animation.breathe_amplitude,
                "driftRadiusX": self.animation.drift.radius_x,
                "driftRadiusY": self.animation.drift.radius_y,
                "driftSpeed": self.animation.drift.speed,
                "ringPhaseOffset": self.animation.ring_phase_offset,
                "syncBreathing": self.animation.sync_breathing,
                "sequentialPulse": self.animation.sequential_pulse,
                "flicker": self.animation.flicker,
                "colorOverride": self.animation.color_override,
                "expandedDrift": self.animation.expanded_drift,
                "contracted": self.animation.contracted,
                "speechRhythm": self.animation.speech_rhythm,
                "splitGroups": self.animation.split_groups,
                "glowAmbientMax": self.animation.glow.ambient_max,
                "glowCoronaMax": self.animation.glow.corona_max,
            },
            "transition": {
                "preAttackMs": envelope.pre_attack_ms,
                "preAttackContraction": envelope.pre_attack_contraction,
                "attackMs": envelope.attack_ms,
                "sustainMs": envelope.sustain_ms,
                "releaseMs": envelope.release_ms,
                "ease": envelope.ease,
            },
        }
