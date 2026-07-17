"""
LunaScript — Cognitive Signature System for Sovereign AI Delegation.

Mechanical measurement, signing, and evolution of Luna's cognitive state
across delegation round-trips. Zero LLM calls. All cogs.

See: Docs/LunaScript/HANDOFF_LUNASCRIPT_COGNITIVE_SIGNATURE.md

Phase 1: features.py, measurement.py, baselines.py, position.py
Phase 2: signature.py, veto.py
Phase 3: evolution.py
Phase 4: Scribe/Memory feed + pattern library (cog_runner.py)
"""

# Phase 1
from .measurement import measure_signature
from .cog_runner import LunaScriptCogRunner

# Phase 2
from .signature import sign_outbound, sign_return, compare_signatures, classify_delta
from .veto import veto_check

# Phase 3
from .evolution import TraitEvolution, RunningStatsDecayed, RunningCovarianceDecayed
