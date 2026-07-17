"""
KOZMO LAB Pipeline Service — Phase 8

Manages the production queue: briefs arrive from SCRIBO overlay or CODEX board,
get rigged with camera settings, and dispatched to Eden for generation.

Storage: YAML files in {project}/lab/briefs/
Assets: Generated images in {project}/lab/assets/
"""

import uuid
import yaml
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from .types import (
    ProductionBrief,
    CameraRig,
    BriefPostConfig,
    SequenceShot,
)
from .camera_registry import build_enriched_prompt


# =============================================================================
# LAB Pipeline Service
# =============================================================================


class LabPipelineService:
    """Manages the LAB production queue and dispatch."""

    def __init__(self, project_root: Path):
        self.root = project_root
        self.briefs_dir = project_root / "lab" / "briefs"
        self.assets_dir = project_root / "lab" / "assets"

    def _ensure_dirs(self) -> None:
        """Create lab directories if they don't exist."""
        self.briefs_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)

    def _brief_path(self, brief_id: str) -> Path:
        """YAML file path for a brief."""
        return self.briefs_dir / f"{brief_id}.yaml"

    def _brief_to_dict(self, brief: ProductionBrief) -> dict:
        """Serialize brief to YAML-friendly dict."""
        d = brief.model_dump(exclude_none=True)
        for key in ("created_at", "updated_at"):
            if key in d and d[key] is not None:
                d[key] = d[key].isoformat()
        return d

    def _dict_to_brief(self, d: dict) -> ProductionBrief:
        """Deserialize dict from YAML into ProductionBrief."""
        for key in ("created_at", "updated_at"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = datetime.fromisoformat(d[key])
                except (ValueError, TypeError):
                    d[key] = None
        return ProductionBrief(**d)

    def _load_brief_from_file(self, path: Path) -> Optional[ProductionBrief]:
        """Load a single brief from a YAML file."""
        if not path.exists():
            return None
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not raw or not isinstance(raw, dict):
                return None
            return self._dict_to_brief(raw)
        except (yaml.YAMLError, Exception):
            return None

    def _sync_to_media_dir(self, local_path: Path, brief, brief_id: str) -> None:
        """Copy generated asset to external media_sync_path if configured."""
        import shutil
        manifest_path = self.root / "manifest.yaml"
        if not manifest_path.exists():
            return
        try:
            manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            sync_path = (manifest_data.get("settings") or {}).get("media_sync_path")
            if not sync_path:
                return
            sync_dir = Path(sync_path)
            sync_dir.mkdir(parents=True, exist_ok=True)
            scene_name = brief.source_scene or "unknown"
            sync_name = f"{scene_name}_{brief_id}.png"
            shutil.copy2(local_path, sync_dir / sync_name)
        except Exception:
            pass  # Non-critical — don't fail the pipeline

    # --- Queue Management ---

    def list_briefs(
        self, status: Optional[str] = None, assignee: Optional[str] = None
    ) -> List[ProductionBrief]:
        """List briefs with optional filters."""
        self._ensure_dirs()
        briefs = []
        for f in sorted(self.briefs_dir.glob("*.yaml")):
            brief = self._load_brief_from_file(f)
            if brief is None:
                continue
            if status and brief.status != status:
                continue
            if assignee and brief.assignee != assignee:
                continue
            briefs.append(brief)
        return briefs

    def get_brief(self, brief_id: str) -> Optional[ProductionBrief]:
        """Get single brief."""
        return self._load_brief_from_file(self._brief_path(brief_id))

    def create_brief(self, brief: ProductionBrief) -> ProductionBrief:
        """Create new brief. Called by overlay push-to-lab or board."""
        self._ensure_dirs()
        if not brief.id:
            brief.id = f"pb_{uuid.uuid4().hex[:8]}"
        if brief.created_at is None:
            brief.created_at = datetime.now()
        brief.updated_at = datetime.now()

        path = self._brief_path(brief.id)
        data = self._brief_to_dict(brief)
        path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return brief

    def update_brief(self, brief_id: str, updates: dict) -> Optional[ProductionBrief]:
        """Update brief fields."""
        brief = self.get_brief(brief_id)
        if brief is None:
            return None

        brief_dict = brief.model_dump()
        brief_dict.update(updates)
        brief_dict["updated_at"] = datetime.now()
        updated = ProductionBrief(**brief_dict)

        path = self._brief_path(brief_id)
        data = self._brief_to_dict(updated)
        path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return updated

    def delete_brief(self, brief_id: str) -> bool:
        """Remove brief from queue."""
        path = self._brief_path(brief_id)
        if path.exists():
            path.unlink()
            return True
        return False

    # --- Camera Rigging ---

    def apply_camera_rig(
        self, brief_id: str, camera: CameraRig, post: BriefPostConfig
    ) -> Optional[ProductionBrief]:
        """Apply camera + post settings to a brief."""
        return self.update_brief(brief_id, {
            "camera": camera.model_dump(),
            "post": post.model_dump(),
        })

    def apply_rig_to_shot(
        self, brief_id: str, shot_id: str, camera: CameraRig, post: BriefPostConfig
    ) -> Optional[SequenceShot]:
        """Apply camera rig to individual shot in a sequence."""
        brief = self.get_brief(brief_id)
        if brief is None or brief.shots is None:
            return None

        for i, shot in enumerate(brief.shots):
            if shot.id == shot_id:
                shot.camera = camera
                shot.post = post
                brief.shots[i] = shot
                self.update_brief(brief_id, {"shots": [s.model_dump() for s in brief.shots]})
                return shot
        return None

    # --- Prompt Building ---

    def build_brief_prompt(self, brief_id: str, shot_id: Optional[str] = None) -> Optional[str]:
        """Build enriched prompt combining base + camera + lens + stock + movement."""
        brief = self.get_brief(brief_id)
        if brief is None:
            return None

        if shot_id and brief.shots:
            shot = next((s for s in brief.shots if s.id == shot_id), None)
            if shot:
                return build_enriched_prompt(
                    base_prompt=shot.prompt,
                    body_id=shot.camera.body,
                    lens_id=shot.camera.lens,
                    focal=shot.camera.focal,
                    aperture=shot.camera.aperture,
                    stock_id=shot.post.stock,
                    movements=shot.camera.movement,
                )

        if brief.camera and brief.post:
            return build_enriched_prompt(
                base_prompt=brief.prompt,
                body_id=brief.camera.body,
                lens_id=brief.camera.lens,
                focal=brief.camera.focal,
                aperture=brief.camera.aperture,
                stock_id=brief.post.stock,
                movements=brief.camera.movement,
            )

        # Reference type or no camera rig — return base prompt
        return brief.prompt

    # --- Sequence Management ---

    def add_shot(self, brief_id: str, shot: SequenceShot) -> Optional[ProductionBrief]:
        """Add shot to sequence."""
        brief = self.get_brief(brief_id)
        if brief is None:
            return None
        if brief.shots is None:
            brief.shots = []
        brief.shots.append(shot)
        return self.update_brief(brief_id, {"shots": [s.model_dump() for s in brief.shots]})

    def remove_shot(self, brief_id: str, shot_id: str) -> Optional[ProductionBrief]:
        """Remove shot from sequence."""
        brief = self.get_brief(brief_id)
        if brief is None or brief.shots is None:
            return None
        brief.shots = [s for s in brief.shots if s.id != shot_id]
        return self.update_brief(brief_id, {"shots": [s.model_dump() for s in brief.shots]})

    def reorder_shots(self, brief_id: str, shot_ids: List[str]) -> Optional[ProductionBrief]:
        """Reorder shots in sequence."""
        brief = self.get_brief(brief_id)
        if brief is None or brief.shots is None:
            return None

        shots_by_id = {s.id: s for s in brief.shots}
        reordered = [shots_by_id[sid] for sid in shot_ids if sid in shots_by_id]
        brief.shots = reordered
        return self.update_brief(brief_id, {"shots": [s.model_dump() for s in brief.shots]})

    # --- Eden Dispatch ---

    async def dispatch_to_eden(self, brief_id: str, eden_adapter) -> Optional[str]:
        """
        Dispatch brief to Eden for image generation.

        1. Build enriched prompt (camera + lens + stock metadata)
        2. Call eden_adapter.create_image(prompt, wait=False)
        3. Store eden_task_id on brief
        4. Update brief status to "generating"
        5. Return task_id for polling

        Args:
            brief_id: The brief to generate
            eden_adapter: An initialized EdenAdapter instance

        Returns:
            Eden task_id string, or None if brief not found
        """
        enriched = self.build_brief_prompt(brief_id)
        if enriched is None:
            return None

        task = await eden_adapter.create_image(enriched, wait=False)

        self.update_brief(brief_id, {
            "eden_task_id": task.id,
            "status": "generating",
        })

        return task.id

    async def poll_eden_status(self, brief_id: str, eden_adapter) -> Optional[dict]:
        """
        Poll Eden for generation status and update brief when complete.

        Returns dict with status info, or None if brief not found.
        """
        import httpx

        brief = self.get_brief(brief_id)
        if brief is None or not brief.eden_task_id:
            return None

        task = await eden_adapter.poll_task(brief.eden_task_id)

        result = {
            "brief_id": brief_id,
            "eden_task_id": brief.eden_task_id,
            "status": task.status.value if hasattr(task.status, 'value') else str(task.status),
            "is_complete": task.is_terminal,
        }

        if task.is_terminal and not task.is_failed:
            image_url = task.first_output_url
            result["image_url"] = image_url

            if image_url:
                # Download and save hero frame
                self._ensure_dirs()
                filename = f"{brief_id}.png"
                local_path = self.assets_dir / filename

                try:
                    async with httpx.AsyncClient(timeout=60.0) as http:
                        resp = await http.get(image_url)
                        resp.raise_for_status()
                        local_path.write_bytes(resp.content)

                    self.update_brief(brief_id, {
                        "hero_frame": f"lab/assets/{filename}",
                        "status": "review",
                    })
                    result["saved_path"] = f"lab/assets/{filename}"

                    # Auto-create timeline container if audio_start is set
                    container_result = self.auto_create_container(
                        brief_id,
                        asset_path=f"lab/assets/{filename}",
                        duration=brief.camera.duration if brief.camera else 3.0,
                    )
                    if container_result:
                        result["container_id"] = container_result

                    # Sync to external media directory if configured
                    self._sync_to_media_dir(local_path, brief, brief_id)

                    # Register in media library index
                    from ._quarantine.media_library import MediaLibraryService
                    media_lib = MediaLibraryService(self.root)
                    media_lib.register_asset(
                        filename=filename,
                        path=f"lab/assets/{filename}",
                        source="eden",
                        brief_id=brief_id,
                        scene_slug=brief.source_scene,
                        audio_track_id=brief.audio_track_id,
                        audio_start=brief.audio_start,
                        audio_end=brief.audio_end,
                        eden_task_id=brief.eden_task_id,
                        prompt=brief.prompt,
                    )
                except Exception as e:
                    result["download_error"] = str(e)

        elif task.is_failed:
            result["error"] = task.error
            self.update_brief(brief_id, {"status": "rigging"})

        return result

    # --- Timeline Integration (Phase 3) ---

    def auto_create_container(
        self,
        brief_id: str,
        asset_path: str,
        duration: float = 3.0,
    ) -> Optional[str]:
        """
        Auto-create a timeline container when a brief's generation completes.

        Position at brief.audio_start if set, otherwise at end of timeline.
        Binds the container back to the brief via container_id.

        Returns container_id if created, None if skipped.
        """
        from .timeline_types import MediaAssetRef, MediaType
        from .timeline_service import TimelineService
        from .timeline_store import load_timeline, save_timeline

        brief = self.get_brief(brief_id)
        if brief is None:
            return None

        # Skip if container already bound
        if brief.container_id:
            return brief.container_id

        tl = load_timeline(self.root)

        # Need at least one video track
        if not tl.tracks:
            return None

        # Find video track
        video_track = next(
            (t for t in tl.tracks if t.track_type.value == "video"),
            tl.tracks[0],
        )

        # Position: use audio_start if set, otherwise end of timeline
        position = brief.audio_start if brief.audio_start is not None else tl.total_duration

        asset_ref = MediaAssetRef(
            asset_id=f"brief_{brief_id}",
            path=asset_path,
            duration=duration,
            media_type=MediaType.IMAGE,
        )

        svc = TimelineService()
        result = svc.create_container(
            tl,
            asset_ref=asset_ref,
            track_id=video_track.id,
            position=position,
            label=brief.title,
        )

        if not result.ok:
            return None

        container_id = result.container_ids[0]

        # Bind brief_id on the container
        tl.containers[container_id].brief_id = brief_id

        save_timeline(self.root, tl)

        # Bind container_id on the brief
        self.update_brief(brief_id, {"container_id": container_id})

        return container_id
