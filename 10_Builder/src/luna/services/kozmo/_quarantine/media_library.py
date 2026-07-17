"""
KOZMO Media Library — Asset Index Service

Maintains a YAML-backed index of all generated/imported media assets
for a project. Called by LabPipelineService when Eden downloads complete.
"""

import uuid
import yaml
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from ..types import MediaAsset


class MediaLibraryService:
    """Index and query media assets for a KOZMO project."""

    def __init__(self, project_root: Path):
        self.root = project_root
        self.index_path = project_root / "media_library.yaml"

    def _load_index(self) -> List[MediaAsset]:
        if not self.index_path.exists():
            return []
        try:
            raw = yaml.safe_load(self.index_path.read_text(encoding="utf-8"))
            if not raw or not isinstance(raw, list):
                return []
            assets = []
            for item in raw:
                if isinstance(item, dict):
                    for key in ("created_at",):
                        if key in item and isinstance(item[key], str):
                            try:
                                item[key] = datetime.fromisoformat(item[key])
                            except (ValueError, TypeError):
                                item[key] = None
                    assets.append(MediaAsset(**item))
            return assets
        except (yaml.YAMLError, Exception):
            return []

    def _save_index(self, assets: List[MediaAsset]) -> None:
        data = []
        for a in assets:
            d = a.model_dump(exclude_none=True)
            if "created_at" in d and d["created_at"] is not None:
                d["created_at"] = d["created_at"].isoformat()
            data.append(d)
        self.index_path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def register_asset(
        self,
        filename: str,
        path: str,
        source: str = "eden",
        asset_type: str = "image",
        brief_id: Optional[str] = None,
        scene_slug: Optional[str] = None,
        audio_track_id: Optional[str] = None,
        audio_start: Optional[float] = None,
        audio_end: Optional[float] = None,
        eden_task_id: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> MediaAsset:
        """Register a new asset in the library index."""
        assets = self._load_index()
        asset = MediaAsset(
            id=f"asset_{uuid.uuid4().hex[:8]}",
            type=asset_type,
            filename=filename,
            path=path,
            source=source,
            brief_id=brief_id,
            scene_slug=scene_slug,
            audio_track_id=audio_track_id,
            audio_start=audio_start,
            audio_end=audio_end,
            eden_task_id=eden_task_id,
            prompt=prompt,
            created_at=datetime.now(),
        )
        assets.append(asset)
        self._save_index(assets)
        return asset

    def list_assets(
        self,
        asset_type: Optional[str] = None,
        scene_slug: Optional[str] = None,
        brief_id: Optional[str] = None,
        status: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> List[MediaAsset]:
        """List assets with optional filters."""
        assets = self._load_index()
        if asset_type:
            assets = [a for a in assets if a.type == asset_type]
        if scene_slug:
            assets = [a for a in assets if a.scene_slug == scene_slug]
        if brief_id:
            assets = [a for a in assets if a.brief_id == brief_id]
        if status:
            assets = [a for a in assets if a.status == status]
        if tag:
            assets = [a for a in assets if tag in a.tags]
        return assets

    def get_asset(self, asset_id: str) -> Optional[MediaAsset]:
        """Get a single asset by ID."""
        assets = self._load_index()
        return next((a for a in assets if a.id == asset_id), None)

    def update_asset(self, asset_id: str, updates: dict) -> Optional[MediaAsset]:
        """Update tags, status, etc. on an asset."""
        assets = self._load_index()
        for i, a in enumerate(assets):
            if a.id == asset_id:
                d = a.model_dump()
                d.update(updates)
                assets[i] = MediaAsset(**d)
                self._save_index(assets)
                return assets[i]
        return None
