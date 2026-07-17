"""
KOZMO Timeline — Pydantic Models
=================================
Container-as-Authority model for NLE timeline editing.

File: src/luna/services/kozmo/timeline_types.py
Version: 2.0.0

Design Principles:
  - Containers own effects. Clips are lightweight pointers.
  - Timeline holds flat container dict. Tracks hold IDs only.
  - All IDs are strings (uuid4 hex). No mixed ID types.
  - Serializes to timeline.yaml in project root.

Invariants:
  INV-1: container.duration >= max(clip.duration for clip in container.clips)
         Container masks clips. Short clips = dead air (black/silence).
  INV-2: No two containers on the same track may overlap in time.
  INV-3: Track.container_ids ordering matches temporal position.
  INV-4: Every container_id in a Track must exist in Timeline.containers.
  INV-5: Every container_id in a Group must exist in Timeline.containers.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field


# ============================================================================
# Enums
# ============================================================================

class MediaType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    GENERATIVE = "generative"  # AI-generated, may not have a file yet


class TrackType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    MIXED = "mixed"


# ============================================================================
# Sub-components
# ============================================================================

def _new_id() -> str:
    return uuid4().hex[:12]


class MediaAssetRef(BaseModel):
    """
    Pointer to a file in the project's asset library.
    Non-destructive: timeline ops never touch the source file.

    Path is relative to project root: assets/video/crooked_nail.mp4
    """
    asset_id: str
    path: str
    duration: float  # Source file duration in seconds
    media_type: MediaType


class CameraMetadata(BaseModel):
    """
    Cinematographic data for Viewport HUD and LAB Pipeline.
    Optional — audio-only containers won't have this.
    """
    body: Optional[str] = None       # e.g. "ARRI Alexa 35"
    lens: Optional[str] = None       # e.g. "Panavision C-Series"
    focal_length: Optional[int] = None
    aperture: Optional[float] = None
    movement: Optional[str] = None   # e.g. "dolly_in", "pan_right + dolly_in"


class EffectNode(BaseModel):
    """
    A single effect in a Container's chain.
    Applied to ALL clips in the container (that's the rule).
    """
    effect_id: str = Field(default_factory=_new_id)
    plugin_type: str       # e.g. "color_grade", "film_grain", "eq", "reverb"
    name: str = ""         # Human label: "Kodak 5219 LUT"
    params: Dict[str, Union[float, str, bool, int, List[float]]] = Field(
        default_factory=dict
    )
    enabled: bool = True


# ============================================================================
# Core Models
# ============================================================================

class Clip(BaseModel):
    """
    Time-trimmed reference to an asset. Lightweight.
    No effects — those live on the Container.

    in_point/out_point are positions WITHIN the source asset.
    The container's position/duration determine where this sits on the timeline.
    """
    id: str = Field(default_factory=_new_id)
    asset_ref: MediaAssetRef
    in_point: float = 0.0
    out_point: float

    @property
    def duration(self) -> float:
        return self.out_point - self.in_point

    def split_at(self, source_time: float) -> tuple["Clip", "Clip"]:
        """
        Split this clip at a point in source-asset time.
        Returns (left_clip, right_clip).
        Caller is responsible for placing these in containers.
        """
        assert self.in_point < source_time < self.out_point, (
            f"Split point {source_time} outside clip range "
            f"[{self.in_point}, {self.out_point}]"
        )
        left = Clip(
            id=_new_id(),
            asset_ref=self.asset_ref,
            in_point=self.in_point,
            out_point=source_time,
        )
        right = Clip(
            id=_new_id(),
            asset_ref=self.asset_ref,
            in_point=source_time,
            out_point=self.out_point,
        )
        return left, right


class Container(BaseModel):
    """
    The unit of editing. This is the authority object.

    Holds 1+ clips and an effect chain.
    Position/duration define where it sits on the timeline.
    All clips share the effect chain — want per-clip effects? Split first.

    Links to:
      - Studio: via brief_id (ProductionBrief)
      - CODEX: via scene_slug (scene in story tree)
      - Groups: via group_id (ContainerGroup)
    """
    id: str = Field(default_factory=_new_id)
    label: str = ""
    position: float = 0.0     # Start time on timeline (seconds)
    duration: float = 0.0     # Duration on timeline (seconds)
    locked: bool = False

    clips: List[Clip] = Field(default_factory=list)
    effect_chain: List[EffectNode] = Field(default_factory=list)
    camera: Optional[CameraMetadata] = None

    # Cross-references
    group_id: Optional[str] = None
    brief_id: Optional[str] = None   # Link to ProductionBrief
    scene_slug: Optional[str] = None  # Link to SCRIBO scene

    metadata: Dict[str, str] = Field(default_factory=dict)

    @property
    def end(self) -> float:
        return self.position + self.duration

    @property
    def fx_count(self) -> int:
        return len([e for e in self.effect_chain if e.enabled])

    def deep_copy_effects(self) -> List[EffectNode]:
        """Deep copy effect chain for split/razor operations."""
        return [e.model_copy(deep=True) for e in self.effect_chain]


class Track(BaseModel):
    """
    A horizontal lane. Purely organizational.
    Holds ordered container IDs — no container data.
    """
    id: str = Field(default_factory=_new_id)
    label: str = ""
    track_type: TrackType = TrackType.VIDEO
    container_ids: List[str] = Field(default_factory=list)
    muted: bool = False
    solo: bool = False


class ContainerGroup(BaseModel):
    """
    Logical link between containers.
    Grouped containers move together but keep independent effect chains.
    This is NOT a merge — no data is combined.
    """
    id: str = Field(default_factory=_new_id)
    label: str = ""
    container_ids: List[str] = Field(default_factory=list)
    lock_positions: bool = True  # If true, moving one moves all


# ============================================================================
# Root Object
# ============================================================================

class Timeline(BaseModel):
    """
    The authority object. Single source of truth.
    Serializes to {project_root}/timeline.yaml.

    Views (Viewport, Compositor, Studio, Timeline UI) are
    read-only projections of this data. They don't hold local copies.
    """
    version: str = "2.0.0"
    fps: int = 24
    total_duration: float = 0.0

    tracks: List[Track] = Field(default_factory=list)
    containers: Dict[str, Container] = Field(default_factory=dict)
    groups: Dict[str, ContainerGroup] = Field(default_factory=dict)

    def recalculate_duration(self) -> None:
        """Recompute total_duration from container extents."""
        if not self.containers:
            self.total_duration = 0.0
            return
        self.total_duration = max(c.end for c in self.containers.values())

    def validate_invariants(self) -> List[str]:
        """
        Check all invariants. Returns list of violation messages.
        Empty list = clean.
        """
        violations = []

        for cid, ct in self.containers.items():
            # INV-1: container duration >= max clip duration
            if ct.clips:
                max_clip = max(cl.duration for cl in ct.clips)
                if ct.duration < max_clip - 0.001:  # float tolerance
                    violations.append(
                        f"INV-1: Container {cid} duration {ct.duration:.3f} "
                        f"< max clip duration {max_clip:.3f}"
                    )

        for track in self.tracks:
            # INV-4: all track container_ids exist
            for cid in track.container_ids:
                if cid not in self.containers:
                    violations.append(
                        f"INV-4: Track {track.id} references "
                        f"missing container {cid}"
                    )

            # INV-2: no overlaps on same track
            cts = [
                self.containers[cid]
                for cid in track.container_ids
                if cid in self.containers
            ]
            cts_sorted = sorted(cts, key=lambda c: c.position)
            for i in range(len(cts_sorted) - 1):
                a, b = cts_sorted[i], cts_sorted[i + 1]
                if a.end > b.position + 0.001:
                    violations.append(
                        f"INV-2: Overlap on track {track.id}: "
                        f"{a.id} ends at {a.end:.3f}, "
                        f"{b.id} starts at {b.position:.3f}"
                    )

        for gid, grp in self.groups.items():
            # INV-5: all group container_ids exist
            for cid in grp.container_ids:
                if cid not in self.containers:
                    violations.append(
                        f"INV-5: Group {gid} references "
                        f"missing container {cid}"
                    )

        return violations
