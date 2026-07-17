"""Trace-mode configuration loader.

Reads trace settings from ``config/frontend_config.json`` under:

``diagnostics.trace``

Defaults are intentionally safe for local usage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from luna.core.paths import config_dir, project_root, user_dir


@dataclass(frozen=True)
class TraceConfig:
    enabled: bool = True
    persist: bool = True
    db_path: Path = user_dir() / "traces.db"
    retention_days: int = 30
    max_size_mb: int = 500
    capture_full_prompt: bool = False
    capture_candidate_content: bool = False
    finalize_timeout_ms: int = 50


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _resolve_db_path(raw) -> Path:
    default_path = user_dir() / "traces.db"
    if not raw:
        return default_path
    try:
        path = Path(str(raw))
    except Exception:
        return default_path
    if path.is_absolute():
        return path
    return (project_root() / path).resolve()


def load_trace_config() -> TraceConfig:
    """Load trace config from frontend config JSON, falling back to defaults."""
    defaults = TraceConfig()
    cfg_path = config_dir() / "frontend_config.json"
    payload = {}
    if cfg_path.exists():
        try:
            payload = json.loads(cfg_path.read_text())
        except Exception:
            payload = {}

    trace_cfg = ((payload.get("diagnostics") or {}).get("trace") or {})

    return TraceConfig(
        enabled=_as_bool(trace_cfg.get("enabled"), defaults.enabled),
        persist=_as_bool(trace_cfg.get("persist"), defaults.persist),
        db_path=_resolve_db_path(trace_cfg.get("db_path")),
        retention_days=max(0, _as_int(trace_cfg.get("retention_days"), defaults.retention_days)),
        max_size_mb=max(1, _as_int(trace_cfg.get("max_size_mb"), defaults.max_size_mb)),
        capture_full_prompt=_as_bool(
            trace_cfg.get("capture_full_prompt"),
            defaults.capture_full_prompt,
        ),
        capture_candidate_content=_as_bool(
            trace_cfg.get("capture_candidate_content"),
            defaults.capture_candidate_content,
        ),
        finalize_timeout_ms=max(
            1,
            _as_int(trace_cfg.get("finalize_timeout_ms"), defaults.finalize_timeout_ms),
        ),
    )
