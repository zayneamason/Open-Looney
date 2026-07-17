"""
Shared Turn Cache Reader
========================

Reads the rotating YAML snapshot written by the Scribe on every turn.
Both Engine-side (Eclissi/voice) and Claude-side (MCP) consume this cache.

The cache is a single file overwritten each turn — not an append-only log.
Consumers should handle staleness via the TTL field.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import logging
import time

import yaml

from luna.core.paths import user_dir

logger = logging.getLogger(__name__)


def shared_turn_cache_path() -> Path:
    """Path to the rotating shared-turn cache YAML.

    Resolved on each call so it tracks the current profile / user_dir().
    """
    return user_dir() / "cache" / "shared_turn.yaml"


@dataclass
class SharedTurnSnapshot:
    """Parsed snapshot from the Shared Turn Cache."""

    schema_version: int = 1
    turn_id: str = ""
    timestamp: float = 0.0
    source: str = "unknown"
    session_id: str = ""

    # Scribed extractions by category
    facts: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)

    # Flow state
    flow_mode: str = "FLOW"
    topic: str = ""
    continuity_score: float = 1.0
    open_threads: list[str] = field(default_factory=list)

    # Expression hints (for orb / voice)
    emotional_tone: str = "neutral"
    expression_hint: str = "idle_soft"
    intensity: float = 0.0

    raw_summary: str = ""
    ttl: int = 30

    @property
    def is_stale(self) -> bool:
        """Check if the cache has exceeded its TTL."""
        if self.timestamp == 0:
            return True
        return (time.time() - self.timestamp) > self.ttl

    @property
    def age_seconds(self) -> float:
        """How old the cache is in seconds."""
        if self.timestamp == 0:
            return float("inf")
        return time.time() - self.timestamp

    @property
    def total_extractions(self) -> int:
        """Total scribed items across all categories."""
        return (
            len(self.facts) + len(self.decisions) + len(self.actions)
            + len(self.problems) + len(self.observations)
        )

    def to_context_summary(self) -> str:
        """Format as a brief context string for MCP enrichment."""
        lines = []
        if self.topic:
            lines.append(f"Current topic: {self.topic}")
        lines.append(f"Flow: {self.flow_mode} (continuity={self.continuity_score:.2f})")
        lines.append(f"Emotional tone: {self.emotional_tone}")

        if self.raw_summary:
            lines.append(f"Summary: {self.raw_summary}")

        if self.open_threads:
            lines.append(f"Open threads: {', '.join(self.open_threads[:3])}")

        if self.total_extractions > 0:
            lines.append(f"Scribed: {self.total_extractions} items this turn")
            for fact in self.facts[:2]:
                lines.append(f"  - [FACT] {fact.get('content', '')[:80]}")
            for dec in self.decisions[:2]:
                lines.append(f"  - [DECISION] {dec.get('content', '')[:80]}")

        return "\n".join(lines)


def read_shared_turn(
    path: Optional[Path] = None,
) -> Optional[SharedTurnSnapshot]:
    """Read and parse the Shared Turn Cache.

    Returns None if the file doesn't exist or is unparseable.
    Returns a SharedTurnSnapshot even if stale — caller should check .is_stale.
    """
    cache_path = path or shared_turn_cache_path()

    if not cache_path.exists():
        return None

    try:
        with open(cache_path) as f:
            data = yaml.safe_load(f)

        if not data or not isinstance(data, dict):
            return None

        scribed = data.get("scribed", {})
        flow = data.get("flow", {})
        expression = data.get("expression", {})

        return SharedTurnSnapshot(
            schema_version=data.get("schema_version", 1),
            turn_id=data.get("turn_id", ""),
            timestamp=data.get("timestamp", 0.0),
            source=data.get("source", "unknown"),
            session_id=data.get("session_id", ""),
            facts=scribed.get("facts", []),
            decisions=scribed.get("decisions", []),
            actions=scribed.get("actions", []),
            problems=scribed.get("problems", []),
            observations=scribed.get("observations", []),
            flow_mode=flow.get("mode", "FLOW"),
            topic=flow.get("topic", ""),
            continuity_score=flow.get("continuity_score", 1.0),
            open_threads=flow.get("open_threads", []),
            emotional_tone=expression.get("emotional_tone", "neutral"),
            expression_hint=expression.get("expression_hint", "idle_soft"),
            intensity=expression.get("intensity", 0.0),
            raw_summary=data.get("raw_summary", ""),
            ttl=data.get("ttl", 30),
        )
    except Exception as e:
        logger.error(f"Failed to read shared turn cache: {e}")
        return None
