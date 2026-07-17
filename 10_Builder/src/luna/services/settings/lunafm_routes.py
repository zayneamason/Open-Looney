"""
Settings routes for LunaFM — station on/off, per-channel enabled,
interval, max_nodes_per_hour. Reads/writes the YAML files under
config/lunafm/ and hot-reloads the running Station.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from luna.core.paths import config_dir

logger = logging.getLogger("luna.settings.lunafm")

router = APIRouter(prefix="/api/settings/lunafm", tags=["settings", "lunafm"])

# Import late — api.server sets _engine after startup
def _get_engine():
    from luna.api import server as _srv
    return _srv._engine


def _station_yaml_path() -> Path:
    return config_dir() / "lunafm" / "station.yaml"


def _channels_dir() -> Path:
    return config_dir() / "lunafm" / "channels"


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Missing: {path.name}")
    return yaml.safe_load(path.read_text()) or {}


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False))


# ── Payloads ────────────────────────────────────────────────────────────────

class ChannelUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_s: Optional[float] = None


class StationUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_nodes_per_hour: Optional[int] = None
    channels: Optional[dict[str, ChannelUpdate]] = None  # {id: {enabled, interval_s}}


# ── GET current config ──────────────────────────────────────────────────────

@router.get("")
async def get_lunafm_settings():
    """Return station config + all channel configs + runtime status."""
    station_yaml = _read_yaml(_station_yaml_path())
    station_block = station_yaml.get("station", {}) or {}

    channels = []
    ch_dir = _channels_dir()
    if ch_dir.exists():
        for yaml_file in sorted(ch_dir.glob("*.yaml")):
            try:
                cfg = _read_yaml(yaml_file).get("channel", {}) or {}
                channels.append({
                    "id": cfg.get("id"),
                    "name": cfg.get("name"),
                    "enabled": bool(cfg.get("enabled", True)),
                    "interval_s": float(cfg.get("frequency", {}).get("interval_s", 60)),
                    "yaml_file": yaml_file.name,
                })
            except Exception as e:
                logger.warning(f"Failed to read {yaml_file}: {e}")

    engine = _get_engine()
    station = getattr(engine, "lunafm", None) if engine else None
    runtime = station.status() if station else None

    return {
        "station": {
            "enabled": bool(station_block.get("enabled", True)),
            "max_nodes_per_hour": int(
                station_block.get("targets", {}).get("memory_matrix", {}).get("max_nodes_per_hour", 20)
            ),
        },
        "channels": channels,
        "runtime": runtime,
    }


# ── POST partial update ─────────────────────────────────────────────────────

@router.post("")
async def update_lunafm_settings(update: StationUpdate):
    """
    Apply a partial update. Writes to YAML and triggers station.reload()
    so the running daemon picks up the changes without a restart.
    """
    station_path = _station_yaml_path()
    station_yaml = _read_yaml(station_path)
    station_block = station_yaml.setdefault("station", {})

    if update.enabled is not None:
        station_block["enabled"] = bool(update.enabled)

    if update.max_nodes_per_hour is not None:
        targets = station_block.setdefault("targets", {})
        mem = targets.setdefault("memory_matrix", {})
        mem["max_nodes_per_hour"] = int(update.max_nodes_per_hour)

    _write_yaml(station_path, station_yaml)

    if update.channels:
        ch_dir = _channels_dir()
        for ch_id, ch_update in update.channels.items():
            # Find the YAML file for this channel
            target_file = None
            for yaml_file in ch_dir.glob("*.yaml"):
                cfg = _read_yaml(yaml_file)
                if (cfg.get("channel") or {}).get("id") == ch_id:
                    target_file = yaml_file
                    break
            if target_file is None:
                logger.warning(f"No YAML for channel {ch_id}")
                continue
            cfg = _read_yaml(target_file)
            ch_cfg = cfg.setdefault("channel", {})
            if ch_update.enabled is not None:
                ch_cfg["enabled"] = bool(ch_update.enabled)
            if ch_update.interval_s is not None:
                freq = ch_cfg.setdefault("frequency", {})
                freq["interval_s"] = float(ch_update.interval_s)
            _write_yaml(target_file, cfg)

    # Hot reload the running station
    engine = _get_engine()
    station = getattr(engine, "lunafm", None) if engine else None
    if station is not None:
        try:
            await station.reload()
        except Exception as e:
            logger.warning(f"Station reload failed: {e}")

    return await get_lunafm_settings()


@router.post("/reload")
async def reload_lunafm():
    """Force the station to re-read its YAML files."""
    engine = _get_engine()
    station = getattr(engine, "lunafm", None) if engine else None
    if station is None:
        raise HTTPException(status_code=503, detail="LunaFM not running")
    return await station.reload()
