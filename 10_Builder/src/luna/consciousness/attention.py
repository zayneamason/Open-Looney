"""
Attention Manager for Luna Engine
=================================

Tracks what Luna is actively thinking about. Topics decay over time
with exponential half-life.

> "The mind is like a muscle - what you focus on grows stronger,
>  what you ignore fades away."
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import math
import logging

logger = logging.getLogger(__name__)


@dataclass
class AttentionTopic:
    """A topic Luna is paying attention to."""

    name: str
    weight: float  # 0.0-1.0, how much attention
    last_active: datetime = field(default_factory=datetime.now)
    access_count: int = 0

    def to_dict(self) -> dict:
        """Serialize for persistence."""
        return {
            "name": self.name,
            "weight": self.weight,
            "last_active": self.last_active.isoformat(),
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AttentionTopic":
        """Restore from persistence."""
        return cls(
            name=data["name"],
            weight=data["weight"],
            last_active=datetime.fromisoformat(data["last_active"]),
            access_count=data.get("access_count", 0),
        )


class AttentionManager:
    """
    Manages Luna's attention with exponential decay.

    Topics fade over time with a configurable half-life.
    60 days = topic at 50% strength after 60 days.
    """

    def __init__(self, half_life_days: float = 60.0):
        self.topics: dict[str, AttentionTopic] = {}
        self.half_life_days = half_life_days
        self._decay_constant = math.log(2) / half_life_days

    def track(self, topic: str, weight: float = 1.0) -> None:
        """
        Track or boost a topic.

        If topic exists: boost weight and update timestamp.
        If new: create with given weight.
        """
        topic_lower = topic.lower().strip()

        if topic_lower in self.topics:
            # Boost existing topic (diminishing returns)
            existing = self.topics[topic_lower]
            boost = weight * 0.2 * (1.0 - existing.weight)  # Smaller boost at higher weights
            existing.weight = min(1.0, existing.weight + boost)
            existing.last_active = datetime.now()
            existing.access_count += 1
            logger.debug(f"Attention: Boosted '{topic}' to {existing.weight:.2f}")
        else:
            # New topic
            self.topics[topic_lower] = AttentionTopic(
                name=topic,
                weight=min(1.0, max(0.0, weight)),
                last_active=datetime.now(),
                access_count=1,
            )
            logger.debug(f"Attention: Tracking new topic '{topic}' at {weight:.2f}")

    def decay_all(self) -> int:
        """
        Apply exponential decay to all topics.

        Returns number of topics pruned (fell below threshold).
        """
        now = datetime.now()
        pruned = 0
        prune_threshold = 0.01  # Remove topics below 1%

        topics_to_remove = []

        for key, topic in self.topics.items():
            age_days = (now - topic.last_active).total_seconds() / 86400

            # Exponential decay: weight * e^(-λt)
            decay_factor = math.exp(-self._decay_constant * age_days)
            topic.weight *= decay_factor

            # Mark for pruning if below threshold
            if topic.weight < prune_threshold:
                topics_to_remove.append(key)
                pruned += 1

        # Remove pruned topics
        for key in topics_to_remove:
            del self.topics[key]

        if pruned > 0:
            logger.debug(f"Attention: Pruned {pruned} decayed topics")

        return pruned

    def get_focused(self, threshold: float = 0.1, limit: int = 10) -> list[AttentionTopic]:
        """
        Get topics above threshold, sorted by weight.

        Args:
            threshold: Minimum weight to include
            limit: Maximum topics to return

        Returns:
            List of AttentionTopic sorted by weight descending
        """
        focused = [
            t for t in self.topics.values()
            if t.weight >= threshold
        ]
        focused.sort(key=lambda t: t.weight, reverse=True)
        return focused[:limit]

    def compute_freshness(self, created_at: datetime) -> float:
        """
        Compute freshness score for a memory node.

        Uses same decay curve as attention.
        Fresh = 1.0, decays towards 0.0.
        """
        now = datetime.now()
        age_days = (now - created_at).total_seconds() / 86400
        return math.exp(-self._decay_constant * age_days)

    def get_topic(self, name: str) -> Optional[AttentionTopic]:
        """Get a specific topic by name."""
        return self.topics.get(name.lower().strip())

    def clear(self) -> None:
        """Clear all topics."""
        self.topics.clear()

    def to_dict(self) -> dict:
        """Serialize for persistence."""
        return {
            "topics": {
                name: topic.to_dict()
                for name, topic in self.topics.items()
            },
            "half_life_days": self.half_life_days,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AttentionManager":
        """Restore from persistence."""
        manager = cls(half_life_days=data.get("half_life_days", 60.0))

        for name, topic_data in data.get("topics", {}).items():
            manager.topics[name] = AttentionTopic.from_dict(topic_data)

        return manager

    def __len__(self) -> int:
        return len(self.topics)
