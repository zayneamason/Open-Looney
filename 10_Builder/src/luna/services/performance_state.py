"""
Unified Performance State for coordinated voice + orb behavior.

This extends OrbState to include voice modulation parameters,
enabling synchronized audiovisual expression.
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum

from .orb_state import OrbAnimation


class EmotionPreset(Enum):
    """Pre-configured emotion → performance mappings."""
    NEUTRAL = "neutral"
    EXCITED = "excited"
    THOUGHTFUL = "thoughtful"
    WARM = "warm"
    PLAYFUL = "playful"
    CONCERNED = "concerned"
    CURIOUS = "curious"


@dataclass
class VoiceKnobs:
    """Voice modulation parameters for TTS."""
    # Piper native controls
    length_scale: float = 1.0      # Speed: 0.5 (2x fast) → 2.0 (half speed)
    noise_scale: float = 0.667     # Expressiveness: 0.0 (monotone) → 1.0 (expressive)
    noise_w: float = 0.8           # Rhythm variation: 0.0 (robotic) → 1.0 (natural)
    sentence_silence: float = 0.2  # Pause between sentences (seconds)

    # Post-processing (Phase 2)
    pitch_shift: float = 0.0       # Semitones (+/- 12)

    def to_piper_args(self) -> List[str]:
        """Convert to Piper CLI arguments."""
        args = [
            "--length_scale", str(self.length_scale),
            "--noise_scale", str(self.noise_scale),
            "--noise_w", str(self.noise_w),
        ]
        if self.sentence_silence > 0:
            args.extend(["--sentence_silence", str(self.sentence_silence)])
        return args


@dataclass
class OrbKnobs:
    """Orb visual parameters."""
    animation: str = "idle"
    color: Optional[str] = None      # Hex color override
    brightness: float = 1.0          # 0.0 → 2.0
    size_scale: float = 1.0          # 0.5 → 2.0
    # Fairy float parameters
    float_amplitude_x: float = 8.0   # Pixels
    float_amplitude_y: float = 12.0
    float_speed_x: float = 0.0015    # Radians per ms
    float_speed_y: float = 0.0023


@dataclass
class PerformanceState:
    """
    Complete performance state combining voice and visual.

    Used by:
    - PerformanceOrchestrator to generate from gestures
    - TTSManager to modulate voice
    - OrbStateManager to control visuals
    - Frontend UIs to display/adjust
    """
    voice: VoiceKnobs = field(default_factory=VoiceKnobs)
    orb: OrbKnobs = field(default_factory=OrbKnobs)

    # Timing
    pre_roll_ms: int = 300           # Orb starts this many ms before voice
    post_roll_ms: int = 800          # Orb settles this many ms after voice ends

    # Metadata
    emotion: Optional[EmotionPreset] = None
    gesture_source: Optional[str] = None  # The marker that triggered this

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for API/WebSocket."""
        return {
            "voice": {
                "length_scale": self.voice.length_scale,
                "noise_scale": self.voice.noise_scale,
                "noise_w": self.voice.noise_w,
                "sentence_silence": self.voice.sentence_silence,
                "pitch_shift": self.voice.pitch_shift,
            },
            "orb": {
                "animation": self.orb.animation,
                "color": self.orb.color,
                "brightness": self.orb.brightness,
                "size_scale": self.orb.size_scale,
                "float_amplitude_x": self.orb.float_amplitude_x,
                "float_amplitude_y": self.orb.float_amplitude_y,
                "float_speed_x": self.orb.float_speed_x,
                "float_speed_y": self.orb.float_speed_y,
            },
            "timing": {
                "pre_roll_ms": self.pre_roll_ms,
                "post_roll_ms": self.post_roll_ms,
            },
            "emotion": self.emotion.value if self.emotion else None,
            "gesture_source": self.gesture_source,
        }


# === EMOTION PRESETS ===
# These map emotions to coordinated voice+orb settings

EMOTION_PRESETS: Dict[EmotionPreset, PerformanceState] = {
    EmotionPreset.NEUTRAL: PerformanceState(
        voice=VoiceKnobs(length_scale=1.0, noise_scale=0.667, noise_w=0.8),
        orb=OrbKnobs(animation="idle", brightness=1.0),
    ),
    EmotionPreset.EXCITED: PerformanceState(
        voice=VoiceKnobs(length_scale=0.85, noise_scale=0.9, noise_w=0.95),
        orb=OrbKnobs(animation="pulse_fast", color="#FFD700", brightness=1.3,
                     float_amplitude_x=15, float_amplitude_y=20, float_speed_x=0.003),
        pre_roll_ms=200,
    ),
    EmotionPreset.THOUGHTFUL: PerformanceState(
        voice=VoiceKnobs(length_scale=1.2, noise_scale=0.4, noise_w=0.6, sentence_silence=0.4),
        orb=OrbKnobs(animation="glow", color="#A8DADC", brightness=0.8,
                     float_amplitude_x=5, float_amplitude_y=8, float_speed_x=0.001),
        pre_roll_ms=500,
    ),
    EmotionPreset.WARM: PerformanceState(
        voice=VoiceKnobs(length_scale=0.95, noise_scale=0.7, noise_w=0.85),
        orb=OrbKnobs(animation="pulse", color="#FFB7B2", brightness=1.1,
                     float_amplitude_x=10, float_amplitude_y=14),
        pre_roll_ms=300,
    ),
    EmotionPreset.PLAYFUL: PerformanceState(
        voice=VoiceKnobs(length_scale=0.9, noise_scale=0.85, noise_w=0.9),
        orb=OrbKnobs(animation="spin", color="#C4B5FD", brightness=1.2,
                     float_amplitude_x=12, float_amplitude_y=18, float_speed_x=0.0025),
        pre_roll_ms=250,
    ),
    EmotionPreset.CONCERNED: PerformanceState(
        voice=VoiceKnobs(length_scale=1.1, noise_scale=0.5, noise_w=0.7),
        orb=OrbKnobs(animation="wobble", color="#F4A261", brightness=0.9),
        pre_roll_ms=400,
    ),
    EmotionPreset.CURIOUS: PerformanceState(
        voice=VoiceKnobs(length_scale=1.05, noise_scale=0.75, noise_w=0.8),
        orb=OrbKnobs(animation="orbit", color="#06B6D4", brightness=1.1),
        pre_roll_ms=350,
    ),
}


# === GESTURE → EMOTION MAPPING ===
# Regex patterns that map to emotions (extends existing OrbStateManager patterns)

GESTURE_TO_EMOTION: Dict[str, EmotionPreset] = {
    r"\*excitedly\*|\*excited\*|\*enthusiastically\*": EmotionPreset.EXCITED,
    r"\*thinks?\*|\*contemplat(es?|ive)\*|\*ponders?\*": EmotionPreset.THOUGHTFUL,
    r"\*warmly\*|\*gently\*|\*softly\*": EmotionPreset.WARM,
    r"\*playfully\*|\*mischievously\*|\*giggles?\*": EmotionPreset.PLAYFUL,
    r"\*concerned\*|\*worriedly\*|\*carefully\*": EmotionPreset.CONCERNED,
    r"\*curiously\*|\*tilts?\*|\*wonders?\*": EmotionPreset.CURIOUS,
    r"\*pulses? warmly\*": EmotionPreset.WARM,
    r"\*spins? playfully\*": EmotionPreset.PLAYFUL,
    r"\*pulses? excitedly\*": EmotionPreset.EXCITED,
}


# === EMOJI → EMOTION MAPPING ===
# Fallback when no gesture pattern matches — maps emoji to voice modulation

EMOJI_TO_EMOTION: Dict[str, EmotionPreset] = {
    "✨": EmotionPreset.EXCITED,
    "💜": EmotionPreset.WARM,
    "🫶": EmotionPreset.WARM,
    "😊": EmotionPreset.WARM,
    "🤔": EmotionPreset.THOUGHTFUL,
    "💭": EmotionPreset.THOUGHTFUL,
    "😏": EmotionPreset.PLAYFUL,
    "😈": EmotionPreset.PLAYFUL,
    "🎉": EmotionPreset.EXCITED,
    "😔": EmotionPreset.CONCERNED,
    "🥺": EmotionPreset.CONCERNED,
    "🧐": EmotionPreset.CURIOUS,
    "👀": EmotionPreset.CURIOUS,
    "💡": EmotionPreset.EXCITED,
    "⚡": EmotionPreset.EXCITED,
    "🌙": EmotionPreset.THOUGHTFUL,
}
