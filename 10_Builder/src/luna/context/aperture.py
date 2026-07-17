"""
Aperture Context Layer — Cognitive Focus Control
=================================================

The aperture controls how wide Luna looks during recall.
It determines what knowledge surfaces based on:
- App context (each app has a default)
- User override (manual adjustment)
- Collection lock-in (usage-driven)

Aperture shapes context, not personality. Luna's kernel, virtues,
voice — all untouched. Only the Memory layer (4.0) content changes.

Presets:
    TUNNEL  (15°) — Project focus only
    NARROW  (35°) — Project + related collections
    BALANCED (55°) — Focus with peripheral awareness (DEFAULT)
    WIDE    (75°) — Broad recall, light filtering
    OPEN    (95°) — Full memory access, no filtering

Agency:
    Luna retains the ability to break through aperture for urgent/important
    info. The breakthrough threshold is inversely proportional to aperture
    width — tighter focus = harder to interrupt.

Usage:
    from luna.context.aperture import ApertureState, AperturePreset, ApertureManager

    manager = ApertureManager()
    manager.set_app_context("kozmo")  # Sets default to NARROW
    state = manager.state             # Current ApertureState

    # User override
    manager.set_preset(AperturePreset.TUNNEL)
    manager.state.user_override  # True

    # Check thresholds
    manager.state.breakthrough_threshold  # 0.72 at TUNNEL
    manager.state.inner_ring_threshold    # 0.75 at TUNNEL
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class AperturePreset(str, Enum):
    TUNNEL = "tunnel"       # 15° — project focus only
    NARROW = "narrow"       # 35° — project + related
    BALANCED = "balanced"   # 55° — focus with peripheral awareness
    WIDE = "wide"           # 75° — broad recall, light filtering (DEFAULT)
    OPEN = "open"           # 95° — full memory access, no filtering


class ApertureMode(str, Enum):
    OFF = "off"        # No aperture filtering — everything is searched
    AUTO = "auto"      # Intent-driven — subtask runner sets aperture per query
    MANUAL = "manual"  # User sets preset — stays until changed


APERTURE_ANGLES = {
    AperturePreset.TUNNEL: 15,
    AperturePreset.NARROW: 35,
    AperturePreset.BALANCED: 55,
    AperturePreset.WIDE: 75,
    AperturePreset.OPEN: 95,
}


# Default aperture per app context
APP_DEFAULTS = {
    "kozmo": AperturePreset.NARROW,
    "guardian": AperturePreset.BALANCED,
    "eclissi": AperturePreset.WIDE,
    "companion": AperturePreset.WIDE,
    "dataroom": AperturePreset.TUNNEL,
}


@dataclass
class ApertureState:
    """Current cognitive focus state."""
    preset: AperturePreset = AperturePreset.WIDE
    angle: int = 75
    focus_tags: list[str] = field(default_factory=list)
    active_project: Optional[str] = None
    active_collection_keys: list[str] = field(default_factory=list)
    app_context: str = "companion"
    user_override: bool = False

    @property
    def breakthrough_threshold(self) -> float:
        """
        Relevance threshold for outer ring items to break through focus.

        Higher threshold = harder to break through.
        At TUNNEL (15°): 0.72 — very hard to interrupt
        At BALANCED (55°): 0.51
        At OPEN (95°): 0.30 — easy to surface anything
        """
        return round(0.30 + ((95 - self.angle) / 95) * 0.50, 2)

    @property
    def inner_ring_threshold(self) -> float:
        """
        Lock-in threshold for a collection to be in the inner ring.

        Collections with lock-in >= this threshold AND tag overlap
        are auto-searched at full depth.
        """
        thresholds = {
            AperturePreset.TUNNEL: 0.75,
            AperturePreset.NARROW: 0.60,
            AperturePreset.BALANCED: 0.40,
            AperturePreset.WIDE: 0.25,
            AperturePreset.OPEN: 0.0,
        }
        return thresholds.get(self.preset, 0.40)

    def to_dict(self, mode: str = "auto") -> dict:
        return {
            "mode": mode,
            "preset": self.preset.value,
            "angle": self.angle,
            "focus_tags": self.focus_tags,
            "active_project": self.active_project,
            "active_collection_keys": self.active_collection_keys,
            "app_context": self.app_context,
            "user_override": self.user_override,
            "breakthrough_threshold": self.breakthrough_threshold,
            "inner_ring_threshold": self.inner_ring_threshold,
        }


class ApertureManager:
    """
    Manages aperture state transitions within a session.

    Handles:
    - App context changes (resets to app default unless user_override)
    - User overrides (preset or raw angle)
    - Focus tag management
    - State serialization for API/MCP
    """

    # Intent → aperture mapping for auto mode
    AUTO_APERTURE_MAP = {
        ('greeting', 'trivial'): AperturePreset.WIDE,
        ('greeting', 'simple'): AperturePreset.WIDE,
        ('greeting', 'moderate'): AperturePreset.WIDE,
        ('greeting', 'complex'): AperturePreset.WIDE,
        ('simple_question', 'trivial'): AperturePreset.WIDE,
        ('simple_question', 'simple'): AperturePreset.WIDE,
        ('simple_question', 'moderate'): AperturePreset.BALANCED,
        ('simple_question', 'complex'): AperturePreset.BALANCED,
        ('memory_query', None): AperturePreset.BALANCED,
        ('research', 'trivial'): AperturePreset.WIDE,
        ('research', 'simple'): AperturePreset.WIDE,
        ('research', 'moderate'): AperturePreset.WIDE,
        ('research', 'complex'): AperturePreset.OPEN,
        ('creative', None): AperturePreset.NARROW,
        ('dataroom', None): AperturePreset.TUNNEL,
        ('task', None): AperturePreset.BALANCED,
        ('emotional', None): AperturePreset.WIDE,
        ('meta', None): AperturePreset.OPEN,
    }

    def __init__(self) -> None:
        self._state = ApertureState()
        self._mode = ApertureMode.AUTO

    @property
    def state(self) -> ApertureState:
        return self._state

    @property
    def mode(self) -> ApertureMode:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Set aperture mode: off, auto, or manual."""
        try:
            self._mode = ApertureMode(mode.lower())
        except ValueError:
            self._mode = ApertureMode.AUTO

        if self._mode == ApertureMode.OFF:
            self._state.preset = AperturePreset.OPEN
            self._state.angle = 95
            self._state.user_override = False
            logger.info("[APERTURE] Mode: OFF — all filtering disabled")
        elif self._mode == ApertureMode.MANUAL:
            self._state.user_override = True
            logger.info("[APERTURE] Mode: MANUAL — preset=%s", self._state.preset.value)
        else:  # AUTO
            self._state.user_override = False
            logger.info("[APERTURE] Mode: AUTO — intent-driven")

    def auto_aperture(self, intent: str, complexity: str) -> AperturePreset:
        """
        Automatically set aperture based on query intent + complexity.
        Only acts when mode is 'auto'. Does NOT set user_override.
        """
        if self._mode != ApertureMode.AUTO:
            return self._state.preset

        # Try exact match first, then intent-only match
        preset = self.AUTO_APERTURE_MAP.get((intent, complexity))
        if not preset:
            preset = self.AUTO_APERTURE_MAP.get((intent, None))
        if not preset:
            preset = AperturePreset.WIDE  # default fallback

        self._state.preset = preset
        self._state.angle = APERTURE_ANGLES[preset]
        self._state.user_override = False
        logger.info("[AUTO-APERTURE] intent=%s complexity=%s -> %s (%d°)",
            intent, complexity, preset.value, self._state.angle)
        return preset

    def set_app_context(self, app: str) -> None:
        """
        Set app context. Resets aperture to app default unless user has overridden.

        Args:
            app: App identifier (kozmo, guardian, eclissi, companion, dataroom)
        """
        self._state.app_context = app

        if not self._state.user_override:
            default_preset = APP_DEFAULTS.get(app, AperturePreset.WIDE)
            self._state.preset = default_preset
            self._state.angle = APERTURE_ANGLES[default_preset]
            logger.debug(f"Aperture reset to {default_preset.value} for app={app}")

    def set_preset(self, preset: AperturePreset) -> None:
        """Set aperture to a named preset. Switches to MANUAL mode."""
        self._state.preset = preset
        self._state.angle = APERTURE_ANGLES[preset]
        self._state.user_override = True
        self._mode = ApertureMode.MANUAL
        logger.info("[APERTURE] Preset: %s (%d°) — mode switched to MANUAL",
            preset.value, self._state.angle)

    def set_angle(self, angle: int) -> None:
        """
        Set aperture to a raw angle. Snaps to nearest preset.

        Args:
            angle: Degree value 15-95
        """
        angle = max(15, min(95, angle))
        self._state.angle = angle
        self._state.user_override = True

        # Snap to nearest preset
        closest = min(
            AperturePreset,
            key=lambda p: abs(APERTURE_ANGLES[p] - angle),
        )
        self._state.preset = closest
        logger.info(f"Aperture set to {angle}° (snapped to {closest.value})")

    def set_focus_tags(self, tags: list[str]) -> None:
        """Set focus tags that shape which collections/nodes are prioritized."""
        self._state.focus_tags = tags

    def set_active_project(self, project: Optional[str]) -> None:
        """Set the active project context."""
        self._state.active_project = project

    def set_active_collections(self, keys: list[str]) -> None:
        """Explicitly set which collection keys are in the inner ring."""
        self._state.active_collection_keys = keys

    def clear_override(self) -> None:
        """Clear user override, allowing app context to control aperture."""
        self._state.user_override = False
        # Re-apply app default
        self.set_app_context(self._state.app_context)

    def reset(self) -> None:
        """Full reset to default state."""
        self._state = ApertureState()
        self._mode = ApertureMode.AUTO

    def from_dict(self, data: dict) -> None:
        """Restore state from a serialized dict."""
        if "preset" in data:
            try:
                self._state.preset = AperturePreset(data["preset"])
            except ValueError:
                self._state.preset = AperturePreset.WIDE

        if "angle" in data:
            self._state.angle = max(15, min(95, int(data["angle"])))

        self._state.focus_tags = data.get("focus_tags", [])
        self._state.active_project = data.get("active_project")
        self._state.active_collection_keys = data.get("active_collection_keys", [])
        self._state.app_context = data.get("app_context", "companion")
        self._state.user_override = data.get("user_override", False)
