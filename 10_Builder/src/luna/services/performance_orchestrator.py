"""
Performance Orchestrator — Coordinates voice + orb from gestures.

This wraps OrbStateManager and adds voice modulation,
keeping the systems unified rather than duplicated.
"""
import json
import os
import re
import logging
from pathlib import Path
from typing import Optional, Callable, List, Tuple, Set
from dataclasses import replace

from .performance_state import (
    PerformanceState, VoiceKnobs, OrbKnobs,
    EMOTION_PRESETS, GESTURE_TO_EMOTION, EMOJI_TO_EMOTION, EmotionPreset
)
from .orb_state import OrbStateManager, OrbAnimation
from luna.core.paths import config_dir

logger = logging.getLogger(__name__)

# Map each EmotionPreset to the gesture_contexts it belongs to.
# When gesture_contexts is configured, only emotions whose context
# appears in the allowed list will fire.
_EMOTION_CONTEXT: dict[EmotionPreset, str] = {
    EmotionPreset.EXCITED: "emotional_responses",
    EmotionPreset.THOUGHTFUL: "processing_complex_thoughts",
    EmotionPreset.WARM: "connection_moments",
    EmotionPreset.PLAYFUL: "emotional_responses",
    EmotionPreset.CONCERNED: "emotional_responses",
    EmotionPreset.CURIOUS: "processing_complex_thoughts",
}


class PerformanceOrchestrator:
    """
    Central coordinator for Luna's audiovisual performance.

    Responsibilities:
    - Parse gestures from text
    - Map to emotion presets
    - Emit coordinated voice + orb parameters
    - Handle manual overrides from UI
    - Manage pre-roll timing
    """

    def __init__(self, orb_manager: Optional[OrbStateManager] = None):
        self._orb_manager = orb_manager or OrbStateManager()
        self._current_state = PerformanceState()
        self._subscribers: List[Callable[[PerformanceState], None]] = []

        # Manual overrides (from UI sliders)
        self._voice_override: Optional[VoiceKnobs] = None
        self._orb_override: Optional[OrbKnobs] = None
        self._override_locked = False  # Lock overrides during speech

        # Compile gesture patterns
        self._compiled_patterns = {
            re.compile(pattern, re.IGNORECASE): emotion
            for pattern, emotion in GESTURE_TO_EMOTION.items()
        }

        # Load allowed gesture contexts from config
        self._allowed_contexts: Optional[Set[str]] = None
        self._config_mtime: float = 0
        self._config_path = config_dir() / "personality.json"
        self._load_gesture_contexts()

    @property
    def current_state(self) -> PerformanceState:
        return self._current_state

    @property
    def orb_manager(self) -> OrbStateManager:
        return self._orb_manager

    def _load_gesture_contexts(self):
        """Load allowed gesture contexts from personality.json, with mtime caching."""
        try:
            if not self._config_path.exists():
                self._allowed_contexts = None
                return
            mtime = self._config_path.stat().st_mtime
            if mtime == self._config_mtime:
                return  # No change
            self._config_mtime = mtime
            with open(self._config_path, "r") as f:
                config = json.load(f)
            contexts = config.get("expression", {}).get("gesture_contexts", [])
            self._allowed_contexts = set(contexts) if contexts else None
            logger.debug("Loaded gesture_contexts: %s", self._allowed_contexts)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Failed to load gesture_contexts: %s", e)
            self._allowed_contexts = None

    def subscribe(self, callback: Callable[[PerformanceState], None]) -> Callable:
        """Subscribe to state changes. Returns unsubscribe function."""
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback)

    def _notify(self):
        """Notify all subscribers of state change."""
        for callback in self._subscribers:
            try:
                callback(self._current_state)
            except Exception as e:
                logger.error(f"Subscriber error: {e}")

    def process_text(self, text: str) -> Tuple[str, PerformanceState]:
        """
        Process Luna's response text.

        Returns:
            (clean_text, performance_state) — Text for TTS and state for orb+voice
        """
        # 1. Detect emotion from gestures
        emotion = self._detect_emotion(text)

        # 2. Get base preset (or neutral)
        if emotion:
            base_state = EMOTION_PRESETS.get(emotion, PerformanceState())
            base_state = replace(base_state, emotion=emotion)
        else:
            base_state = PerformanceState(emotion=EmotionPreset.NEUTRAL)

        # 3. Apply manual overrides if set
        if self._voice_override and not self._override_locked:
            base_state = replace(base_state, voice=self._voice_override)
        if self._orb_override and not self._override_locked:
            base_state = replace(base_state, orb=self._orb_override)

        # 4. Clean text for TTS
        clean_text = self._clean_for_tts(text)

        # 5. Find source gesture for metadata
        gesture_match = self._find_gesture(text)
        if gesture_match:
            base_state = replace(base_state, gesture_source=gesture_match)

        # 6. Update current state and notify
        self._current_state = base_state
        self._notify()

        # 7. Also update orb manager for WebSocket broadcast
        self._update_orb_manager(base_state)

        return clean_text, base_state

    def _detect_emotion(self, text: str) -> Optional[EmotionPreset]:
        """Match text against gesture patterns, then emoji, to find emotion."""
        # Refresh allowed contexts (mtime-cached, cheap)
        self._load_gesture_contexts()

        # 1. Gesture patterns first
        for pattern, emotion in self._compiled_patterns.items():
            if pattern.search(text):
                if self._allowed_contexts is not None:
                    ctx = _EMOTION_CONTEXT.get(emotion)
                    if ctx and ctx not in self._allowed_contexts:
                        logger.debug("Filtered emotion %s (context %s not allowed)", emotion, ctx)
                        continue
                logger.debug(f"Detected emotion {emotion} from pattern {pattern.pattern}")
                return emotion

        # 2. Emoji fallback — scan first 500 chars
        scan_text = text[:500]
        for emoji, emotion in EMOJI_TO_EMOTION.items():
            if emoji in scan_text:
                if self._allowed_contexts is not None:
                    ctx = _EMOTION_CONTEXT.get(emotion)
                    if ctx and ctx not in self._allowed_contexts:
                        continue
                logger.debug(f"Detected emotion {emotion} from emoji {emoji}")
                return emotion

        return None

    def _find_gesture(self, text: str) -> Optional[str]:
        """Extract the actual gesture marker from text."""
        match = re.search(r'\*[^*]+\*', text)
        return match.group(0) if match else None

    def _clean_for_tts(self, text: str) -> str:
        """Remove gesture markers for TTS."""
        # Remove *gesture* markers
        clean = re.sub(r'\*[^*]+\*', '', text)
        # Normalize whitespace
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean

    def _update_orb_manager(self, state: PerformanceState):
        """Sync orb manager with performance state."""
        if self._orb_manager:
            # Map to OrbAnimation enum
            try:
                anim = OrbAnimation(state.orb.animation.upper())
            except ValueError:
                # Try lowercase if uppercase fails
                try:
                    anim = OrbAnimation(state.orb.animation.lower())
                except ValueError:
                    anim = OrbAnimation.IDLE

            # Create OrbState and set it
            from .orb_state import OrbState, StatePriority
            orb_state = OrbState(
                animation=anim,
                color=state.orb.color,
                brightness=state.orb.brightness,
                priority=StatePriority.GESTURE,
                source="performance_orchestrator"
            )
            self._orb_manager.set_state(orb_state)

    # === MANUAL OVERRIDE API (for UI controls) ===

    def set_voice_override(self, knobs: VoiceKnobs):
        """Set manual voice override from UI."""
        self._voice_override = knobs
        self._current_state = replace(self._current_state, voice=knobs)
        self._notify()
        logger.info(f"Voice override set: {knobs}")

    def set_orb_override(self, knobs: OrbKnobs):
        """Set manual orb override from UI."""
        self._orb_override = knobs
        self._current_state = replace(self._current_state, orb=knobs)
        self._notify()
        self._update_orb_manager(self._current_state)
        logger.info(f"Orb override set: {knobs}")

    def clear_overrides(self):
        """Clear all manual overrides."""
        self._voice_override = None
        self._orb_override = None
        logger.info("Overrides cleared")

    def lock_overrides(self, locked: bool = True):
        """Lock overrides during speech to prevent jank."""
        self._override_locked = locked

    def get_feedback(self) -> dict:
        """Get current state for feedback UI."""
        return {
            "state": self._current_state.to_dict(),
            "has_voice_override": self._voice_override is not None,
            "has_orb_override": self._orb_override is not None,
            "override_locked": self._override_locked,
        }

    def set_emotion(self, emotion_name: str) -> bool:
        """
        Set emotion preset by name.

        Args:
            emotion_name: Emotion name (e.g., "excited", "warm")

        Returns:
            True if emotion was set, False if unknown
        """
        try:
            emotion = EmotionPreset(emotion_name.lower())
            preset = EMOTION_PRESETS.get(emotion)
            if preset:
                self.set_voice_override(preset.voice)
                self.set_orb_override(preset.orb)
                return True
        except ValueError:
            pass
        return False
