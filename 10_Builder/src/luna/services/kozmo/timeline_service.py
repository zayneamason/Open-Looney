"""
KOZMO Timeline Service — Container Operations
===============================================
Implements the six core operations on the Container System.
Each operation mutates Timeline state and emits events for view sync.

File: src/luna/services/kozmo/timeline_service.py
Version: 2.0.0

Operation Contracts:
  CREATE  -> Drop asset -> Container with one Clip on appropriate track
  RAZOR   -> Cut container at time -> two containers, both get effect chain copy
  SPLIT   -> Extract clip from container -> new container with that clip
  MERGE   -> Combine source into target -> target chain wins, source deleted
  GROUP   -> Link containers -> move together, keep independent chains
  UNGROUP -> Remove group link

Event Bus:
  Operations emit typed events for Viewport/Compositor/Studio sync.
  Wire these to WebSocket broadcasts on the KOZMO routes.

Error Handling:
  Operations return Result objects. No exceptions for business logic.
  Caller (route handler) decides how to surface errors to UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Protocol, Union

from .timeline_types import (
    Clip,
    Container,
    ContainerGroup,
    MediaAssetRef,
    Timeline,
    Track,
    TrackType,
    _new_id,
)


# ============================================================================
# Events — emitted after each operation for view sync
# ============================================================================

class EventType(str, Enum):
    CONTAINER_CREATED = "container.created"
    CONTAINER_DELETED = "container.deleted"
    CONTAINER_UPDATED = "container.updated"
    CONTAINER_SPLIT = "container.split"       # Razor or Split Clip
    CONTAINER_MERGED = "container.merged"
    GROUP_CREATED = "group.created"
    GROUP_DELETED = "group.deleted"
    TIMELINE_RECALCULATED = "timeline.recalculated"


@dataclass
class TimelineEvent:
    type: EventType
    container_ids: List[str] = field(default_factory=list)
    group_id: Optional[str] = None
    detail: str = ""


class EventSink(Protocol):
    """Interface for event consumers."""
    def emit(self, event: TimelineEvent) -> None: ...


class NullSink:
    """Default no-op sink for testing."""
    def emit(self, event: TimelineEvent) -> None:
        pass


# ============================================================================
# Result type — operations return this, no exceptions for business logic
# ============================================================================

@dataclass
class Result:
    ok: bool
    events: List[TimelineEvent] = field(default_factory=list)
    error: str = ""
    # Payloads for callers that need the created objects
    container_ids: List[str] = field(default_factory=list)
    group_id: Optional[str] = None

    @staticmethod
    def success(
        events: List[TimelineEvent] = None,
        container_ids: List[str] = None,
        group_id: str = None,
    ) -> "Result":
        return Result(
            ok=True,
            events=events or [],
            container_ids=container_ids or [],
            group_id=group_id,
        )

    @staticmethod
    def fail(error: str) -> "Result":
        return Result(ok=False, error=error)


# ============================================================================
# Service
# ============================================================================

class TimelineService:
    """
    Stateless operations on a Timeline object.
    Takes timeline by reference, mutates in place, returns Result.

    Wire this into the KOZMO API routes. Each route handler:
      1. Loads timeline from project
      2. Calls the operation
      3. Persists if result.ok
      4. Broadcasts result.events over WebSocket
    """

    def __init__(self, sink: EventSink = None):
        self.sink = sink or NullSink()

    def _emit(self, events: List[TimelineEvent]) -> None:
        for e in events:
            self.sink.emit(e)

    # ────────────────────────────────────────────────────
    # CREATE — Drop asset onto timeline
    # ────────────────────────────────────────────────────

    def create_container(
        self,
        timeline: Timeline,
        asset_ref: MediaAssetRef,
        track_id: str,
        position: float,
        label: str = "",
    ) -> Result:
        """
        Create a new container with a single clip from the given asset.

        Args:
            track_id: Which track to place on. Must exist.
            position: Timeline position in seconds.
            label: Optional human label. Defaults to asset filename.

        Returns:
            Result with new container_id.
        """
        # Validate track exists
        track = self._find_track(timeline, track_id)
        if not track:
            return Result.fail(f"Track {track_id} not found")

        # Build clip
        clip = Clip(
            asset_ref=asset_ref,
            in_point=0.0,
            out_point=asset_ref.duration,
        )

        # Build container
        ct = Container(
            label=label or asset_ref.path.split("/")[-1],
            position=position,
            duration=asset_ref.duration,
            clips=[clip],
        )

        # Check for overlaps on this track
        if self._would_overlap(timeline, track, ct):
            return Result.fail(
                f"Container would overlap existing content on track {track_id}"
            )

        # Commit
        timeline.containers[ct.id] = ct
        track.container_ids.append(ct.id)
        self._sort_track(timeline, track)
        timeline.recalculate_duration()

        events = [
            TimelineEvent(
                type=EventType.CONTAINER_CREATED,
                container_ids=[ct.id],
                detail=f"Created from {asset_ref.path}",
            ),
            TimelineEvent(type=EventType.TIMELINE_RECALCULATED),
        ]
        self._emit(events)
        return Result.success(events=events, container_ids=[ct.id])

    # ────────────────────────────────────────────────────
    # RAZOR — Cut container at time point
    # ────────────────────────────────────────────────────

    def razor(
        self,
        timeline: Timeline,
        container_id: str,
        cut_time: float,
    ) -> Result:
        """
        Split a container at an absolute timeline time.
        Produces two new containers. Original is removed.

        Both halves inherit:
          - Deep copy of effect chain
          - Camera metadata (shared, not copied — it's immutable per-shot)
          - Group membership (if any)

        Clips are split at the relative cut point within the source asset.

        Args:
            cut_time: Absolute timeline time to cut at.

        Returns:
            Result with two new container_ids [left, right].
        """
        ct = timeline.containers.get(container_id)
        if not ct:
            return Result.fail(f"Container {container_id} not found")
        if ct.locked:
            return Result.fail(f"Container {container_id} is locked")
        if not (ct.position < cut_time < ct.end):
            return Result.fail(
                f"Cut time {cut_time:.3f} outside container range "
                f"[{ct.position:.3f}, {ct.end:.3f}]"
            )

        # Relative cut point within the container
        rel_cut = cut_time - ct.position

        # Split each clip at the relative point
        left_clips = []
        right_clips = []
        for clip in ct.clips:
            # Map container-relative cut to source-asset time
            source_cut = clip.in_point + rel_cut

            if source_cut <= clip.in_point:
                # Entire clip goes to right
                right_clips.append(clip.model_copy(deep=True))
                right_clips[-1].id = _new_id()
            elif source_cut >= clip.out_point:
                # Entire clip goes to left
                left_clips.append(clip.model_copy(deep=True))
                left_clips[-1].id = _new_id()
            else:
                # Clip straddles the cut
                lc, rc = clip.split_at(source_cut)
                left_clips.append(lc)
                right_clips.append(rc)

        # Build new containers
        ct_left = Container(
            label=f"{ct.label} (L)",
            position=ct.position,
            duration=rel_cut,
            locked=False,
            clips=left_clips,
            effect_chain=ct.deep_copy_effects(),
            camera=ct.camera,  # Shared ref — camera is read-only
            group_id=ct.group_id,
            brief_id=ct.brief_id,
            scene_slug=ct.scene_slug,
            metadata={**ct.metadata, "_razor_source": ct.id},
        )
        ct_right = Container(
            label=f"{ct.label} (R)",
            position=cut_time,
            duration=ct.duration - rel_cut,
            locked=False,
            clips=right_clips,
            effect_chain=ct.deep_copy_effects(),
            camera=ct.camera,
            group_id=ct.group_id,
            brief_id=ct.brief_id,
            scene_slug=ct.scene_slug,
            metadata={**ct.metadata, "_razor_source": ct.id},
        )

        # Replace in timeline
        timeline.containers[ct_left.id] = ct_left
        timeline.containers[ct_right.id] = ct_right

        # Replace in tracks
        for track in timeline.tracks:
            if container_id in track.container_ids:
                idx = track.container_ids.index(container_id)
                track.container_ids[idx:idx + 1] = [ct_left.id, ct_right.id]

        # Replace in groups
        if ct.group_id and ct.group_id in timeline.groups:
            grp = timeline.groups[ct.group_id]
            if container_id in grp.container_ids:
                idx = grp.container_ids.index(container_id)
                grp.container_ids[idx:idx + 1] = [ct_left.id, ct_right.id]

        # Remove original
        del timeline.containers[container_id]
        timeline.recalculate_duration()

        events = [
            TimelineEvent(
                type=EventType.CONTAINER_SPLIT,
                container_ids=[ct_left.id, ct_right.id],
                detail=f"Razor at {cut_time:.3f}s (from {container_id})",
            ),
            TimelineEvent(
                type=EventType.CONTAINER_DELETED,
                container_ids=[container_id],
            ),
            TimelineEvent(type=EventType.TIMELINE_RECALCULATED),
        ]
        self._emit(events)
        return Result.success(
            events=events,
            container_ids=[ct_left.id, ct_right.id],
        )

    # ────────────────────────────────────────────────────
    # SPLIT CLIP — Extract one clip into its own container
    # ────────────────────────────────────────────────────

    def split_clip_out(
        self,
        timeline: Timeline,
        container_id: str,
        clip_id: str,
    ) -> Result:
        """
        Extract a clip from a multi-clip container into a new container.
        The new container gets a copy of the effect chain.

        The original container keeps its remaining clips.
        Its duration may shrink if the extracted clip defined the extent.

        Only works if container has 2+ clips.

        Returns:
            Result with [original_id, new_container_id].
        """
        ct = timeline.containers.get(container_id)
        if not ct:
            return Result.fail(f"Container {container_id} not found")
        if ct.locked:
            return Result.fail(f"Container {container_id} is locked")
        if len(ct.clips) < 2:
            return Result.fail("Need 2+ clips to split. Use Razor for single-clip containers.")

        clip = next((c for c in ct.clips if c.id == clip_id), None)
        if not clip:
            return Result.fail(f"Clip {clip_id} not found in container {container_id}")

        # Remove clip from original
        ct.clips = [c for c in ct.clips if c.id != clip_id]

        # Recalculate original duration to max remaining clip
        if ct.clips:
            ct.duration = max(c.duration for c in ct.clips)

        # Build new container with extracted clip
        new_ct = Container(
            label=f"{ct.label} — {clip.asset_ref.path.split('/')[-1]}",
            position=ct.position,  # Same position — user can move it
            duration=clip.duration,
            clips=[clip],
            effect_chain=ct.deep_copy_effects(),
            camera=ct.camera,
            scene_slug=ct.scene_slug,
        )

        timeline.containers[new_ct.id] = new_ct

        # Add to appropriate track based on media type
        target_track = self._find_track_for_media(
            timeline, clip.asset_ref.media_type.value
        )
        if target_track:
            target_track.container_ids.append(new_ct.id)
            self._sort_track(timeline, target_track)

        timeline.recalculate_duration()

        events = [
            TimelineEvent(
                type=EventType.CONTAINER_SPLIT,
                container_ids=[container_id, new_ct.id],
                detail=f"Split clip {clip_id} from {container_id}",
            ),
            TimelineEvent(
                type=EventType.CONTAINER_UPDATED,
                container_ids=[container_id],
            ),
            TimelineEvent(type=EventType.TIMELINE_RECALCULATED),
        ]
        self._emit(events)
        return Result.success(
            events=events,
            container_ids=[container_id, new_ct.id],
        )

    # ────────────────────────────────────────────────────
    # MERGE — Combine source into target
    # ────────────────────────────────────────────────────

    def merge(
        self,
        timeline: Timeline,
        target_id: str,
        source_id: str,
        confirmed: bool = False,
    ) -> Result:
        """
        Merge source container INTO target container.
        Source clips are appended to target. Source is then deleted.

        IMPORTANT: Target's effect chain WINS. Source's chain is DROPPED.
        This is destructive — the `confirmed` flag must be True.

        If source has a different effect chain, this is data loss.
        The UI should show a confirmation dialog before calling with confirmed=True.

        Returns:
            Result with [target_id].
        """
        if not confirmed:
            return Result.fail(
                "Merge drops source effect chain. "
                "Call with confirmed=True after user confirmation."
            )

        target = timeline.containers.get(target_id)
        source = timeline.containers.get(source_id)
        if not target:
            return Result.fail(f"Target {target_id} not found")
        if not source:
            return Result.fail(f"Source {source_id} not found")
        if target.locked:
            return Result.fail(f"Target {target_id} is locked")

        # Append source clips to target
        target.clips.extend(source.clips)

        # Recalculate target duration to span both
        target.duration = max(
            target.duration,
            (source.position + source.duration) - target.position,
        )

        # Remove source from tracks
        for track in timeline.tracks:
            if source_id in track.container_ids:
                track.container_ids.remove(source_id)

        # Remove source from groups
        if source.group_id and source.group_id in timeline.groups:
            grp = timeline.groups[source.group_id]
            if source_id in grp.container_ids:
                grp.container_ids.remove(source_id)
            # Clean up empty groups
            if not grp.container_ids:
                del timeline.groups[source.group_id]

        # Delete source
        del timeline.containers[source_id]
        timeline.recalculate_duration()

        events = [
            TimelineEvent(
                type=EventType.CONTAINER_MERGED,
                container_ids=[target_id],
                detail=f"Merged {source_id} into {target_id}. Source chain dropped.",
            ),
            TimelineEvent(
                type=EventType.CONTAINER_DELETED,
                container_ids=[source_id],
            ),
            TimelineEvent(type=EventType.TIMELINE_RECALCULATED),
        ]
        self._emit(events)
        return Result.success(events=events, container_ids=[target_id])

    # ────────────────────────────────────────────────────
    # GROUP / UNGROUP
    # ────────────────────────────────────────────────────

    def group(
        self,
        timeline: Timeline,
        container_ids: List[str],
        label: str = "",
    ) -> Result:
        """
        Link containers into a group. They move together but keep
        independent effect chains. This is NOT a merge.

        Containers already in a group will be removed from their old group.

        Returns:
            Result with group_id.
        """
        if len(container_ids) < 2:
            return Result.fail("Need 2+ containers to group")

        # Validate all exist
        for cid in container_ids:
            if cid not in timeline.containers:
                return Result.fail(f"Container {cid} not found")

        # Remove from any existing groups
        for cid in container_ids:
            ct = timeline.containers[cid]
            if ct.group_id and ct.group_id in timeline.groups:
                old_grp = timeline.groups[ct.group_id]
                if cid in old_grp.container_ids:
                    old_grp.container_ids.remove(cid)
                if not old_grp.container_ids:
                    del timeline.groups[ct.group_id]

        # Create new group
        grp = ContainerGroup(
            label=label or f"Group ({len(container_ids)})",
            container_ids=list(container_ids),
        )
        timeline.groups[grp.id] = grp

        # Tag containers
        for cid in container_ids:
            timeline.containers[cid].group_id = grp.id

        events = [
            TimelineEvent(
                type=EventType.GROUP_CREATED,
                container_ids=container_ids,
                group_id=grp.id,
                detail=f"Grouped {len(container_ids)} containers",
            ),
        ]
        self._emit(events)
        return Result.success(events=events, group_id=grp.id)

    def ungroup(
        self,
        timeline: Timeline,
        group_id: str,
    ) -> Result:
        """
        Dissolve a group. Containers keep their positions and effect chains.
        """
        grp = timeline.groups.get(group_id)
        if not grp:
            return Result.fail(f"Group {group_id} not found")

        freed = []
        for cid in grp.container_ids:
            if cid in timeline.containers:
                timeline.containers[cid].group_id = None
                freed.append(cid)

        del timeline.groups[group_id]

        events = [
            TimelineEvent(
                type=EventType.GROUP_DELETED,
                container_ids=freed,
                group_id=group_id,
                detail=f"Ungrouped {len(freed)} containers",
            ),
        ]
        self._emit(events)
        return Result.success(events=events)

    # ────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────

    @staticmethod
    def _find_track(timeline: Timeline, track_id: str) -> Optional[Track]:
        return next((t for t in timeline.tracks if t.id == track_id), None)

    @staticmethod
    def _find_track_for_media(
        timeline: Timeline, media_type: str
    ) -> Optional[Track]:
        """Find first track matching media type hint."""
        type_map = {"video": TrackType.VIDEO, "audio": TrackType.AUDIO, "image": TrackType.VIDEO}
        target_type = type_map.get(media_type, TrackType.MIXED)
        return next(
            (t for t in timeline.tracks if t.track_type == target_type),
            timeline.tracks[0] if timeline.tracks else None,
        )

    @staticmethod
    def _would_overlap(
        timeline: Timeline, track: Track, new_ct: Container
    ) -> bool:
        """Check if new container would overlap existing ones on track."""
        for cid in track.container_ids:
            existing = timeline.containers.get(cid)
            if not existing:
                continue
            # Overlap if one starts before the other ends
            if (
                new_ct.position < existing.end
                and existing.position < new_ct.end
            ):
                return True
        return False

    @staticmethod
    def _sort_track(timeline: Timeline, track: Track) -> None:
        """Re-sort track container_ids by position (INV-3)."""
        track.container_ids.sort(
            key=lambda cid: timeline.containers[cid].position
            if cid in timeline.containers
            else 0
        )
