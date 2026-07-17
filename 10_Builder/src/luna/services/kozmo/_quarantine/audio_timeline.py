"""
KOZMO Audio Timeline Service

Manages audio-driven timelines for projects where audio leads
and visuals follow (narration pieces, music videos, podcast visualizers).

Storage: {project}/audio_timeline.yaml
Audio assets: {project}/assets/audio/
"""

import re
import yaml
from pathlib import Path
from typing import Optional

from ..types import AudioTimeline, AudioTrack
from ..scribo_parser import parse_scribo

_VISUAL_RE = re.compile(r"\[\[VISUAL\s+[^\]]*?\s*—\s*(.*?)\]\]", re.DOTALL)


# =============================================================================
# Audio Timeline Service
# =============================================================================


class AudioTimelineService:
    """Load, save, and build audio timelines for a project."""

    def __init__(self, project_root: Path):
        self.root = project_root
        self.timeline_path = project_root / "audio_timeline.yaml"
        self.audio_dir = project_root / "assets" / "audio"

    def load_timeline(self) -> Optional[AudioTimeline]:
        """Read audio_timeline.yaml from project root."""
        if not self.timeline_path.exists():
            return None

        try:
            raw = yaml.safe_load(
                self.timeline_path.read_text(encoding="utf-8")
            )
        except (yaml.YAMLError, UnicodeDecodeError):
            return None

        if not raw or not isinstance(raw, dict):
            return None

        tracks = []
        for t in raw.get("tracks", []):
            try:
                tracks.append(AudioTrack(**t))
            except Exception:
                continue  # Skip malformed tracks

        return AudioTimeline(
            total_duration=raw.get("total_duration", 0.0),
            tracks=tracks,
        )

    def save_timeline(self, timeline: AudioTimeline) -> AudioTimeline:
        """Write audio_timeline.yaml to project root."""
        data = {
            "total_duration": timeline.total_duration,
            "tracks": [t.model_dump(exclude_none=True) for t in timeline.tracks],
        }
        self.timeline_path.write_text(
            yaml.dump(
                data,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return timeline

    def get_track(self, track_id: str) -> Optional[AudioTrack]:
        """Get a single track by ID."""
        timeline = self.load_timeline()
        if timeline is None:
            return None
        return next((t for t in timeline.tracks if t.id == track_id), None)

    def get_track_at_time(self, seconds: float) -> Optional[AudioTrack]:
        """Find which audio track is playing at a given timecode."""
        timeline = self.load_timeline()
        if timeline is None:
            return None
        return next(
            (t for t in timeline.tracks if t.start_time <= seconds < t.end_time),
            None,
        )

    def get_tracks_for_scene(self, doc_slug: str) -> list:
        """Get all audio tracks linked to a .scribo scene."""
        timeline = self.load_timeline()
        if timeline is None:
            return []
        return [t for t in timeline.tracks if t.document_slug == doc_slug]

    def _build_audio_to_doc_index(self) -> dict:
        """
        Build a mapping from audio filename → (document_slug, container_slug)
        by scanning .scribo files for audio_file frontmatter.
        """
        story_dir = self.root / "story"
        index = {}  # filename → { document_slug, container_slug }

        if not story_dir.exists():
            return index

        for sf in story_dir.rglob("*.scribo"):
            try:
                text = sf.read_text(encoding="utf-8")
                fm, body = parse_scribo(text)
            except Exception:
                continue

            if fm.audio_file:
                visual_prompt = None
                vm = _VISUAL_RE.search(body)
                if vm:
                    visual_prompt = vm.group(1).strip()
                index[fm.audio_file] = {
                    "document_slug": sf.stem,
                    "container_slug": fm.container,
                    "visual_prompt": visual_prompt,
                }

        return index

    def enrich_with_documents(self, timeline: AudioTimeline) -> AudioTimeline:
        """
        Cross-reference audio tracks with SCRIBO documents.

        Matches each track's filename against .scribo audio_file frontmatter
        and populates document_slug, container_slug, and visual_prompt on the track.
        """
        index = self._build_audio_to_doc_index()

        for track in timeline.tracks:
            ref = index.get(track.filename)
            if ref:
                track.document_slug = ref["document_slug"]
                track.container_slug = ref["container_slug"]
                if ref.get("visual_prompt"):
                    track.visual_prompt = ref["visual_prompt"]

        return timeline

    def build_from_scribo(self) -> AudioTimeline:
        """
        Auto-build audio timeline from .scribo frontmatter.

        Scans story/ directory for .scribo files with audio_file and
        audio_duration frontmatter fields, then assembles them in order.

        Falls back to scanning assets/audio/ directory if no scribo
        frontmatter is found.
        """
        story_dir = self.root / "story"
        tracks = []
        cursor = 0.0
        idx = 1

        if story_dir.exists():
            scribo_files = sorted(story_dir.rglob("*.scribo"))
            for sf in scribo_files:
                try:
                    text = sf.read_text(encoding="utf-8")
                    fm, body = parse_scribo(text)
                except Exception:
                    continue

                if fm.audio_file:
                    dur = 0.0
                    if fm.audio_duration:
                        try:
                            dur = float(fm.audio_duration.rstrip("s"))
                        except (ValueError, AttributeError):
                            pass

                    # Extract [[VISUAL ... — description]] from body
                    visual_prompt = None
                    vm = _VISUAL_RE.search(body)
                    if vm:
                        visual_prompt = vm.group(1).strip()

                    track_id = f"at_{idx:02d}"
                    tracks.append(AudioTrack(
                        id=track_id,
                        filename=fm.audio_file,
                        path=f"assets/audio/{fm.audio_file}",
                        voice=(fm.characters_present[0] if fm.characters_present else None),
                        start_time=cursor,
                        end_time=cursor + dur,
                        duration=dur,
                        document_slug=sf.stem,
                        container_slug=fm.container,
                        visual_prompt=visual_prompt,
                    ))
                    cursor += dur
                    idx += 1

        # If no scribo-based tracks, scan the audio directory
        if not tracks and self.audio_dir.exists():
            audio_files = sorted(
                f for f in self.audio_dir.iterdir()
                if f.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac")
            )
            for af in audio_files:
                track_id = f"at_{idx:02d}"
                tracks.append(AudioTrack(
                    id=track_id,
                    filename=af.name,
                    path=f"assets/audio/{af.name}",
                    start_time=cursor,
                    end_time=cursor,  # Duration unknown without ffprobe
                    duration=0.0,
                ))
                idx += 1

        total = max((t.end_time for t in tracks), default=0.0)
        timeline = AudioTimeline(total_duration=total, tracks=tracks)
        self.save_timeline(timeline)
        return timeline
