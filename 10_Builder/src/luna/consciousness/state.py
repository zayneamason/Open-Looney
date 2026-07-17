"""
Consciousness State for Luna Engine
====================================

Luna's continuous consciousness. The LLM is stateless.
This IS Luna's ongoing experience — persisted across restarts.

> "Consciousness is not a thing but a process." — Daniel Dennett
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
import logging

from .attention import AttentionManager
from .curiosity import CuriosityBuffer
from .personality import PersonalityWeights

logger = logging.getLogger(__name__)


# Persistence path
SNAPSHOT_PATH = Path.home() / ".luna" / "snapshot.yaml"


@dataclass
class ConsciousnessState:
    """
    Luna's continuous consciousness.

    The LLM is stateless inference. This class maintains:
    - What Luna is paying attention to (topics with decay)
    - Luna's personality (trait weights)
    - Current coherence level
    - Mood state
    - Tick counter (lifetime consciousness cycles)
    """

    attention: AttentionManager = field(default_factory=AttentionManager)
    personality: PersonalityWeights = field(default_factory=PersonalityWeights)
    curiosity: CuriosityBuffer = field(default_factory=CuriosityBuffer)
    coherence: float = 1.0  # How "together" Luna feels (0-1)
    mood: str = "neutral"   # Current emotional state
    last_updated: datetime = field(default_factory=datetime.now)
    tick_count: int = 0

    # Thread awareness (Layer 6)
    active_thread_topic: Optional[str] = None
    active_thread_turn_count: int = 0
    open_task_count: int = 0
    parked_thread_count: int = 0

    # Valid mood states
    VALID_MOODS = {
        "neutral", "curious", "focused", "playful",
        "thoughtful", "energetic", "calm", "helpful"
    }

    async def tick(self) -> dict:
        """
        Single consciousness tick (called every cognitive cycle).

        Returns dict of what changed.
        """
        changes = {}

        # 1. Decay attention topics
        pruned = self.attention.decay_all()
        if pruned > 0:
            changes["attention_pruned"] = pruned

        # 1.5 Age curiosity buffer (auto-suppress stale low-priority entries)
        self.curiosity.tick(self.tick_count)

        # 2. Update coherence
        if self.active_thread_topic:
            # Thread-aware coherence (Layer 6)
            thread_depth = min(1.0, self.active_thread_turn_count / 10.0)
            task_tension = self.open_task_count
            self.coherence = max(0.1, min(1.0,
                0.6 + (thread_depth * 0.35) - (task_tension * 0.03)
            ))
        else:
            # Fallback: attention-based coherence
            focused_topics = self.attention.get_focused(threshold=0.3)
            if focused_topics:
                weights = [t.weight for t in focused_topics]
                max_weight = max(weights) if weights else 0.5
                spread = len(focused_topics)
                self.coherence = min(1.0, max_weight * (1.0 - spread * 0.05))
            else:
                self.coherence = 0.7

        # 3. Update timestamp and tick count
        self.last_updated = datetime.now()
        self.tick_count += 1

        return changes

    def update_from_thread(self, active_thread=None, parked_threads=None) -> None:
        """Update consciousness from thread state (Layer 6)."""
        parked_threads = parked_threads or []

        if active_thread:
            self.active_thread_topic = active_thread.topic
            self.active_thread_turn_count = active_thread.turn_count
            # Boost attention for thread topic and entities
            self.attention.track(active_thread.topic, weight=0.8)
            for entity in active_thread.entities[:5]:
                self.attention.track(entity, weight=0.5)
        else:
            self.active_thread_topic = None
            self.active_thread_turn_count = 0

        self.parked_thread_count = len(parked_threads)

        # Count total open tasks
        total_tasks = 0
        if active_thread:
            total_tasks += len(active_thread.open_tasks)
        for t in parked_threads:
            total_tasks += len(t.open_tasks)
        self.open_task_count = total_tasks

    def set_mood(self, mood: str) -> bool:
        """
        Set current mood.

        Returns True if mood was valid and set.
        """
        mood_lower = mood.lower()
        if mood_lower in self.VALID_MOODS:
            self.mood = mood_lower
            logger.debug(f"Consciousness: Mood set to '{mood_lower}'")
            return True
        logger.warning(f"Consciousness: Invalid mood '{mood}'")
        return False

    def focus_on(self, topic: str, weight: float = 1.0) -> None:
        """Convenience method to track attention topic."""
        self.attention.track(topic, weight)

    def get_context_hint(self) -> str:
        """
        Generate context hint for prompt injection.

        Combines personality and attention into guidance.
        """
        hints = []

        # Personality hint
        personality_hint = self.personality.to_prompt_hint()
        if personality_hint:
            hints.append(personality_hint)

        # Attention hint (what Luna is focused on)
        focused = self.attention.get_focused(threshold=0.3, limit=3)
        if focused:
            topics = [t.name for t in focused]
            hints.append(f"Currently focused on: {', '.join(topics)}.")

        # Thread engagement hint (Layer 6)
        if self.active_thread_topic:
            hints.append(f"Deeply engaged in: {self.active_thread_topic}.")
        if self.open_task_count > 0:
            hints.append(f"Tracking {self.open_task_count} unresolved item(s).")

        # Mood hint
        if self.mood != "neutral":
            hints.append(f"Current mood: {self.mood}.")

        # Curiosity synthesis (injected only when ripe)
        curiosity_block = self.curiosity.to_prompt_block()
        if curiosity_block:
            hints.append("\n\n" + curiosity_block)

        return " ".join(hints)

    def get_summary(self) -> dict:
        """Get summary of consciousness state."""
        focused = self.attention.get_focused(threshold=0.1, limit=5)
        return {
            "mood": self.mood,
            "coherence": round(self.coherence, 2),
            "attention_topics": len(self.attention),
            "focused_topics": [
                {"name": t.name, "weight": round(t.weight, 2)}
                for t in focused
            ],
            "top_traits": self.personality.get_top_traits(3),
            "tick_count": self.tick_count,
            "last_updated": self.last_updated.isoformat(),
            "active_thread": self.active_thread_topic,
            "active_thread_turns": self.active_thread_turn_count,
            "open_tasks": self.open_task_count,
            "parked_threads": self.parked_thread_count,
        }

    def to_dict(self) -> dict:
        """Serialize for persistence."""
        return {
            "attention": self.attention.to_dict(),
            "personality": self.personality.to_dict(),
            "coherence": self.coherence,
            "mood": self.mood,
            "last_updated": self.last_updated.isoformat(),
            "tick_count": self.tick_count,
            "active_thread_topic": self.active_thread_topic,
            "active_thread_turn_count": self.active_thread_turn_count,
            "open_task_count": self.open_task_count,
            "parked_thread_count": self.parked_thread_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConsciousnessState":
        """Restore from persistence."""
        state = cls()

        # Restore attention
        if "attention" in data:
            state.attention = AttentionManager.from_dict(data["attention"])

        # Restore personality
        if "personality" in data:
            state.personality = PersonalityWeights.from_dict(data["personality"])

        # Restore other fields
        state.coherence = data.get("coherence", 1.0)
        state.mood = data.get("mood", "neutral")
        state.tick_count = data.get("tick_count", 0)

        # Restore thread awareness (Layer 6)
        state.active_thread_topic = data.get("active_thread_topic")
        state.active_thread_turn_count = data.get("active_thread_turn_count", 0)
        state.open_task_count = data.get("open_task_count", 0)
        state.parked_thread_count = data.get("parked_thread_count", 0)

        if "last_updated" in data:
            try:
                state.last_updated = datetime.fromisoformat(data["last_updated"])
            except (ValueError, TypeError):
                state.last_updated = datetime.now()

        return state

    async def save(self, path: Optional[Path] = None) -> bool:
        """
        Save consciousness state to disk.

        Returns True if successful.
        """
        save_path = path or SNAPSHOT_PATH

        try:
            import yaml

            save_path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "version": "2.0",
                "consciousness": self.to_dict(),
            }

            with open(save_path, "w") as f:
                yaml.safe_dump(data, f, default_flow_style=False)

            logger.info(f"Consciousness: Saved to {save_path}")
            return True

        except Exception as e:
            logger.error(f"Consciousness: Failed to save: {e}")
            return False

    @classmethod
    async def load(cls, path: Optional[Path] = None) -> "ConsciousnessState":
        """
        Load consciousness state from disk.

        Returns fresh state if file doesn't exist or is invalid.
        """
        load_path = path or SNAPSHOT_PATH

        if not load_path.exists():
            logger.info("Consciousness: No snapshot found, starting fresh")
            return cls()

        try:
            import yaml

            with open(load_path) as f:
                data = yaml.safe_load(f)

            if data and "consciousness" in data:
                state = cls.from_dict(data["consciousness"])
                logger.info(
                    f"Consciousness: Restored from snapshot "
                    f"(tick {state.tick_count}, {len(state.attention)} topics)"
                )
                return state

            logger.warning("Consciousness: Invalid snapshot format, starting fresh")
            return cls()

        except Exception as e:
            logger.error(f"Consciousness: Failed to load: {e}")
            return cls()

    def __repr__(self) -> str:
        return (
            f"ConsciousnessState(mood={self.mood}, "
            f"coherence={self.coherence:.2f}, "
            f"topics={len(self.attention)}, "
            f"tick={self.tick_count})"
        )
