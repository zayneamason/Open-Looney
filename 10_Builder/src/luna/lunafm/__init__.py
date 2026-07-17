"""
LunaFM — background cognitive broadcast system.

Luna's inner life modeled as a radio station. Channels are async
coroutines that run between conversations, producing tagged artifacts
(FLAG / CONSOLIDATION / SYNTHESIS) that flow into the normal memory
pipeline with low lock_in.

MVP: station + news + history channels.
"""
from luna.lunafm.station import LunaFMActor, Station

__all__ = ["LunaFMActor", "Station"]
