"""
Frequency coupling — bidirectional bridge between LunaScript traits
and LunaFM channel behavior.

Read side: `trait_to_aperture(curiosity) → sigma` maps curiosity to a
spectral search radius via the V3 APERTURE_MAP. Entertainment channel
calls this before sampling if the spectral engine is online.

Write side: `nudge_trait(db, trait, delta)` mutates the trait_vector
JSON in `lunascript_state`. Called from Station.write_artifact on
successful emissions so the conversation pipeline responds to the
channels' cognitive output.

Degraded mode: if lunascript_state is empty or missing, reads return
defaults and writes are no-ops. Never blocks a channel tick.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# V3 aperture presets — spectral search radius σ
APERTURE_MAP = {
    "TUNNEL":   0.01,
    "NARROW":   0.02,
    "BALANCED": 0.05,
    "WIDE":     0.10,
    "OPEN":     0.20,
}

# Per-emission nudge budget — prevents runaway feedback
MAX_NUDGE_PER_EMISSION = 0.10
MIN_NUDGE_DELTA = 0.01


def trait_to_aperture(curiosity: float) -> tuple[str, float]:
    """Map curiosity ∈ [0,1] to (preset_name, sigma)."""
    c = max(0.0, min(1.0, float(curiosity)))
    if c >= 0.9:
        return "OPEN", APERTURE_MAP["OPEN"]
    if c >= 0.7:
        return "WIDE", APERTURE_MAP["WIDE"]
    if c >= 0.5:
        return "BALANCED", APERTURE_MAP["BALANCED"]
    if c >= 0.3:
        return "NARROW", APERTURE_MAP["NARROW"]
    return "TUNNEL", APERTURE_MAP["TUNNEL"]


async def read_traits(db) -> dict[str, float]:
    """Read current trait vector. Returns {} if unavailable."""
    if db is None:
        return {}
    try:
        row = await db.fetchone(
            "SELECT trait_vector FROM lunascript_state WHERE id = 1"
        )
        if not row or not row[0]:
            return {}
        vec = json.loads(row[0])
        if isinstance(vec, dict):
            return {k: float(v) for k, v in vec.items() if isinstance(v, (int, float))}
    except Exception as e:
        logger.debug(f"[LUNAFM:coupling] read_traits failed: {e}")
    return {}


async def nudge_trait(db, trait: str, delta: float) -> Optional[float]:
    """
    Nudge one trait. Clamps to [0,1], caps per-emission to
    MAX_NUDGE_PER_EMISSION, ignores noise below MIN_NUDGE_DELTA.

    Returns the new trait value on success, None on no-op / failure.
    """
    if db is None:
        return None
    delta = float(delta)
    if abs(delta) < MIN_NUDGE_DELTA:
        return None
    delta = max(-MAX_NUDGE_PER_EMISSION, min(MAX_NUDGE_PER_EMISSION, delta))

    try:
        row = await db.fetchone(
            "SELECT trait_vector FROM lunascript_state WHERE id = 1"
        )
        if not row or not row[0]:
            # No lunascript state yet — skip silently
            return None
        vec = json.loads(row[0])
        if not isinstance(vec, dict):
            return None
        old = float(vec.get(trait, 0.5))
        new = max(0.0, min(1.0, old + delta))
        vec[trait] = new

        await db.execute(
            "UPDATE lunascript_state SET trait_vector = ?, updated_at = ? WHERE id = 1",
            (json.dumps(vec), time.time()),
        )
        logger.debug(f"[LUNAFM:coupling] nudge {trait}: {old:.2f} → {new:.2f} (Δ{delta:+.2f})")
        return new
    except Exception as e:
        logger.debug(f"[LUNAFM:coupling] nudge_trait failed: {e}")
        return None


# Mapping from emission kind to the trait it nudges
EMISSION_NUDGES = {
    ("entertainment", "SYNTHESIS"):  ("curiosity", 0.05),
    ("history", "CONSOLIDATION"):    ("patience", 0.03),
    ("news", "FLAG"):                ("directness", 0.02),
}


async def nudge_for_emission(db, channel_id: str, node_type: str) -> None:
    """Apply the default trait nudge for a given emission kind."""
    key = (channel_id, node_type)
    spec = EMISSION_NUDGES.get(key)
    if not spec:
        return
    trait, delta = spec
    await nudge_trait(db, trait, delta)
